"""Verify every required DSSE envelope with pinned offline Cosign.

This verifies signed statements, not publisher authority or predicate truth.
No predicate body, credential, raw diagnostic, or local path is exported.
"""
from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import tempfile
import time
from urllib.parse import urlsplit

from devsecops.process import ToolError, run
from .http import APIError, _json, _large_attestation_json, _JSONLimitError
from .signatures import (ROOT, VERSION, MAX_BINARY, _Invalid, _base64, _digest,
                         _public_key, _repository_ref, _snapshot)
from .registry_attestation_policy import (AttestationSizePolicy, limits_for, payload_json_values_for,
    DEFAULT_PAYLOAD_JSON_VALUES, MAX_PAYLOAD_JSON_VALUES,
    MAX_ENVELOPE as SELECTED_MAX_ENVELOPE, MAX_PAYLOAD as SELECTED_MAX_PAYLOAD,
    V3_MAX_ENVELOPE, V3_MAX_PAYLOAD, V3_MAX_PAYLOAD_JSON_VALUES,
    MAX_TOTAL as MAX_TOTAL_ENVELOPES)

PAYLOAD_TYPE = 'application/vnd.in-toto+json'
STATEMENT_TYPES = {'https://in-toto.io/Statement/v0.1', 'https://in-toto.io/Statement/v1'}
MAX_ENVELOPE = 4_000_000
MAX_PAYLOAD = 2_500_000
JSON_LIMIT_CODES = frozenset({'attestation-payload-json-value-limit', 'attestation-payload-json-depth-limit'})


def _dsse_base64(value, *, minimum=1, maximum=MAX_PAYLOAD):
    # DSSE accepts both standard and URL-safe base64. Reject mixed alphabets,
    # whitespace, missing padding and noncanonical encodings before decoding.
    if isinstance(value, str) and ('-' in value or '_' in value):
        if '+' in value or '/' in value:
            raise _Invalid('signature-encoding-unsupported')
        raw = _base64(value.translate(str.maketrans('-_', '+/')), minimum=minimum, maximum=maximum)
        if base64.urlsafe_b64encode(raw).decode() != value:
            raise _Invalid('signature-encoding-unsupported')
        return raw
    return _base64(value, minimum=minimum, maximum=maximum)


def _predicate_type(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or re.search(r'[\x00-\x20\x7f]', value):
        return False
    try:
        parsed = urlsplit(value)
        # Predicate types are exact identifiers, never network destinations.
        # A fragment is part of the signed identity (for example Syft's type).
        return bool(parsed.scheme == 'https' and parsed.hostname and not parsed.username
                    and not parsed.password and not parsed.query)
    except ValueError:
        return False


def _decode(raw, *, max_payload=MAX_PAYLOAD, max_payload_json_values=DEFAULT_PAYLOAD_JSON_VALUES):
    envelope_limits = {MAX_PAYLOAD: MAX_ENVELOPE, SELECTED_MAX_PAYLOAD: SELECTED_MAX_ENVELOPE,
                       V3_MAX_PAYLOAD: V3_MAX_ENVELOPE}
    if (type(raw) is not bytes or type(max_payload) is not int
            or max_payload not in envelope_limits
            or not 0 < len(raw) <= envelope_limits[max_payload]):
        raise _Invalid('attestation-size-limit')
    json_maximum = V3_MAX_PAYLOAD_JSON_VALUES if max_payload == V3_MAX_PAYLOAD else MAX_PAYLOAD_JSON_VALUES
    if (type(max_payload_json_values) is not int
            or not DEFAULT_PAYLOAD_JSON_VALUES <= max_payload_json_values <= json_maximum
            or max_payload_json_values > DEFAULT_PAYLOAD_JSON_VALUES and max_payload == MAX_PAYLOAD):
        raise _Invalid('attestation-json-allowance-invalid')
    envelope = _json(raw)
    if set(envelope) != {'payloadType', 'payload', 'signatures'} or envelope['payloadType'] != PAYLOAD_TYPE:
        raise _Invalid('dsse-envelope-unsupported')
    payload = _dsse_base64(envelope['payload'], maximum=max_payload)
    signatures = envelope['signatures']
    # One signature per envelope is the currently supported storage shape. Do not
    # silently drop extra signers or choose a convenient signature from a quorum.
    if not isinstance(signatures, list) or len(signatures) != 1 or not isinstance(signatures[0], dict):
        raise _Invalid('dsse-signature-set-unsupported')
    signature = signatures[0]
    if set(signature) - {'keyid', 'sig'} or 'sig' not in signature:
        raise _Invalid('dsse-signature-unsupported')
    if 'keyid' in signature and (not isinstance(signature['keyid'], str) or len(signature['keyid']) > 256):
        raise _Invalid('dsse-key-hint-unsupported')
    signature_bytes = _dsse_base64(signature['sig'], minimum=64, maximum=128)
    try:
        decoder = _large_attestation_json if max_payload == V3_MAX_PAYLOAD else _json
        statement = decoder(payload, max_values=max_payload_json_values)
    except _JSONLimitError as error:
        raise APIError('attestation-payload-json-' + error.dimension + '-limit') from None
    if (set(statement) != {'_type', 'subject', 'predicateType', 'predicate'}
            or statement['_type'] not in STATEMENT_TYPES
            or not _predicate_type(statement['predicateType'])
            or not isinstance(statement['predicate'], dict) or not statement['predicate']):
        raise _Invalid('attestation-statement-unsupported')
    subjects = statement['subject']
    if (not isinstance(subjects, list) or len(subjects) != 1 or not isinstance(subjects[0], dict)
            or set(subjects[0]) != {'name', 'digest'}
            or not _repository_ref(subjects[0]['name']) or not isinstance(subjects[0]['digest'], dict)
            or set(subjects[0]['digest']) != {'sha256'}
            or not isinstance(subjects[0]['digest']['sha256'], str)
            or not _digest('sha256:' + subjects[0]['digest']['sha256'])):
        raise _Invalid('attestation-subject-unsupported')
    # DSSE v1 PAE signs the exact payload bytes and type, including their lengths.
    kind = PAYLOAD_TYPE.encode('ascii')
    pae = b'DSSEv1 ' + str(len(kind)).encode() + b' ' + kind + b' ' + str(len(payload)).encode() + b' ' + payload
    return statement, payload, pae, base64.b64encode(signature_bytes).decode()


def verify_attestations(*, expected_repository, expected_digest, required_predicates,
                        public_key, envelopes, cosign, timeout=60.0, now=None,
                        attestation_size_policy=None, envelope_descriptors=None):
    """Verify 1–16 required predicate types, each from its own signed DSSE input.

    `envelopes` is a mapping from the independently required predicate URI to an
    absolute local Path, or None for a missing artifact. Different URI inputs
    cannot fulfill one another. No supplied JSON verdict or key hint is trusted.
    """
    if (not _repository_ref(expected_repository) or not _digest(expected_digest)
            or not isinstance(required_predicates, (list, tuple)) or not 1 <= len(required_predicates) <= 16
            or any(not _predicate_type(item) for item in required_predicates)
            or len(set(required_predicates)) != len(required_predicates)
            or not isinstance(envelopes, dict) or set(envelopes) - set(required_predicates)
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 120):
        raise ValueError('Exact subject, required predicate URIs and bounded inputs are required')
    if now is not None and (not isinstance(now, datetime) or now.tzinfo is None):
        raise ValueError('An aware observation time is required')
    if attestation_size_policy is not None:
        if (type(attestation_size_policy) is not AttestationSizePolicy
                or not isinstance(envelope_descriptors, dict)
                or set(envelope_descriptors) - set(required_predicates)):
            raise ValueError('A validated size policy and exact envelope descriptors are required')
        attestation_size_policy.require_scope(origin=attestation_size_policy.origin,
            repository=attestation_size_policy.repository, subject_digest=expected_digest)
        if (attestation_size_policy.origin.removeprefix('https://') + '/'
                + attestation_size_policy.repository != expected_repository):
            raise ValueError('Attestation size policy differs from the exact signed subject')
    elif envelope_descriptors is not None:
        raise ValueError('Envelope size selection requires its pinned policy')
    def clock():
        return now or datetime.now(timezone.utc)
    started = clock().isoformat()
    observations = {'verification': {'decision': 'unknown'}, 'network_mode': 'offline-key-only',
                    'inputs': {}, 'predicates': [
                        {'predicate_type': predicate, 'crypto': 'unknown', 'subject_binding': 'unknown',
                         'type_binding': 'unknown', 'verification': 'unknown'}
                        for predicate in required_predicates], 'receipts': [],
                    'collection': {'started_at': started}}
    result = {'schema_version': 1, 'provider': 'attestations', 'decision': 'unknown',
              'execution_status': 'partial', 'confirmed_failure': False,
              'scope': {'repository': expected_repository, 'digest': expected_digest,
                        'required_predicates': list(required_predicates)},
              'observed_at': started, 'observations': observations,
              'gaps': [{'code': code, 'required': True} for code in (
                  'signer-authority-unverified', 'key-lifecycle-unverified',
                  'transparency-log-unverified', 'official-image-origin-unverified',
                  'predicate-semantics-unassessed')]}
    if attestation_size_policy is not None:
        result['scope']['attestation_size_policy'] = attestation_size_policy.identity_record()
    deadline = time.monotonic() + timeout
    def check_time():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _Invalid('attestation-time-budget-exhausted')
        return remaining
    def gap(code, predicate=None):
        result['gaps'].append({'code': code, 'required': True,
                               **({'predicate_type': predicate} if predicate else {})})
    try:
        with tempfile.TemporaryDirectory(prefix='devsecops-attestation-') as directory:
            scratch = Path(directory).resolve()
            for name in ('home', 'empty-path', 'cache', 'docker', 'tmp'):
                (scratch / name).mkdir(mode=0o700)
            env = {'PATH': str(scratch / 'empty-path'), 'HOME': str(scratch / 'home'),
                   'XDG_CACHE_HOME': str(scratch / 'cache'), 'DOCKER_CONFIG': str(scratch / 'docker'),
                   'TMPDIR': str(scratch / 'tmp')}
            lock_receipt = _snapshot(ROOT / 'cosign.lock.json', scratch / 'lock.json', 64_000, deadline=deadline)
            lock = _json((scratch / 'lock.json').read_bytes())
            arch = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine(), platform.machine())
            target = platform.system().lower() + '-' + arch
            if lock.get('schema_version') != 1 or lock.get('version') != VERSION or target not in lock.get('tools', {}):
                raise _Invalid('cosign-lock-unsupported')
            binary = _snapshot(cosign, scratch / 'cosign', MAX_BINARY, deadline=deadline)
            pinned = lock['tools'][target]
            if binary != {'sha256': pinned['sha256'], 'bytes': pinned['bytes']}:
                raise _Invalid('cosign-binary-mismatch')
            os.chmod(scratch / 'cosign', 0o500)
            observations['tool'] = {'name': 'cosign', 'version': VERSION, 'platform': target,
                                    'binary_sha256': binary['sha256'], 'lock_sha256': lock_receipt['sha256']}
            observations['inputs']['public-key'] = _snapshot(public_key, scratch / 'public-key.pem', 16_384, deadline=deadline)
            _public_key((scratch / 'public-key.pem').read_bytes())
            def command(args, operation, predicate=None):
                remaining = check_time()
                code, out, err, receipt = run([str(scratch / 'cosign'), *args], cwd=scratch,
                                              timeout=remaining, limit=64_000, env=env)
                observations['receipts'].append({'operation': operation,
                    **({'predicate_type': predicate} if predicate else {}), **receipt})
                # A child can exit between process.run's final poll and its
                # timeout check. A late success cannot complete this operation.
                check_time()
                return code, out, err
            code, out, _ = command(['version', '--json'], 'version')
            if code != 0 or _json(out).get('gitVersion') != 'v' + VERSION:
                raise _Invalid('cosign-version-mismatch')
            reserved_envelopes = 0
            for index, predicate in enumerate(required_predicates):
                check_time()
                observation = observations['predicates'][index]
                source = envelopes.get(predicate)
                if source is None:
                    gap('required-attestation-missing', predicate)
                    continue
                try:
                    stem = str(index)
                    maximum, payload_limit = MAX_ENVELOPE, MAX_PAYLOAD
                    json_limit = DEFAULT_PAYLOAD_JSON_VALUES
                    descriptor = None
                    if attestation_size_policy is not None:
                        descriptor = envelope_descriptors.get(predicate)
                        if type(descriptor) is not dict or set(descriptor) != {'digest', 'size_bytes'}:
                            raise _Invalid('attestation-envelope-descriptor-missing')
                        maximum, payload_limit = limits_for(attestation_size_policy,
                            descriptor['digest'], descriptor['size_bytes'])
                        json_limit = payload_json_values_for(attestation_size_policy,
                            descriptor['digest'], descriptor['size_bytes'])
                        maximum = min(maximum, descriptor['size_bytes'])
                    if reserved_envelopes + maximum > MAX_TOTAL_ENVELOPES:
                        raise _Invalid('attestation-aggregate-size-limit')
                    # Charge before copying, including a later invalid or changed
                    # file. Selected inputs must equal the reserved descriptor.
                    reserved_envelopes += maximum
                    receipt = _snapshot(source, scratch / (stem + '.json'), maximum, deadline=deadline)
                    observations['inputs'][predicate] = receipt
                    if descriptor is not None and (receipt['bytes'] != descriptor['size_bytes']
                            or 'sha256:' + receipt['sha256'] != descriptor['digest']):
                        raise _Invalid('attestation-envelope-descriptor-mismatch')
                    with (scratch / (stem + '.json')).open('rb') as stream:
                        raw = stream.read(maximum + 1)
                    if len(raw) != receipt['bytes'] or hashlib.sha256(raw).hexdigest() != receipt['sha256']:
                        raise _Invalid('attestation-envelope-snapshot-changed')
                    statement, payload, pae, signature = _decode(raw, max_payload=payload_limit,
                        max_payload_json_values=json_limit)
                    # These bytes are derived only from the frozen envelope. Passing
                    # raw payload without DSSE PAE would verify the wrong protocol.
                    (scratch / (stem + '.pae')).write_bytes(pae)
                    (scratch / (stem + '.sig')).write_text(signature, encoding='ascii')
                    os.chmod(scratch / (stem + '.pae'), 0o400)
                    os.chmod(scratch / (stem + '.sig'), 0o400)
                    code, _, err = command(['verify-blob', '--key', 'public-key.pem', '--offline',
                        '--insecure-ignore-tlog', '--signature', stem + '.sig', stem + '.pae'], 'verify-dsse-pae', predicate)
                    if code == 0:
                        observation['crypto'] = 'pass'
                        subject = statement['subject'][0]
                        observation['subject_binding'] = ('pass' if subject['name'] == expected_repository
                            and subject['digest']['sha256'] == expected_digest[7:] else 'fail')
                        observation['type_binding'] = 'pass' if statement['predicateType'] == predicate else 'fail'
                        observation['statement_sha256'] = hashlib.sha256(payload).hexdigest()
                        observation['predicate_sha256'] = hashlib.sha256(
                            json.dumps(statement['predicate'], sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=False, allow_nan=False).encode()).hexdigest()
                        check_time()
                        observation['verification'] = ('pass' if observation['subject_binding'] == observation['type_binding'] == 'pass' else 'fail')
                        if observation['verification'] != 'pass':
                            gap('attestation-subject-or-predicate-mismatch', predicate)
                    elif b'invalid signature when validating ASN.1 encoded signature' in err:
                        observation['crypto'] = observation['verification'] = 'fail'
                        gap('attestation-signature-invalid', predicate)
                    else:
                        gap('attestation-tool-verification-unavailable', predicate)
                except _Invalid as exc:
                    gap(exc.code, predicate)
                    if exc.code in {'attestation-time-budget-exhausted', 'input-deadline-exceeded'}:
                        break
                except ToolError:
                    gap('attestation-tool-budget-or-execution-failed', predicate)
                    break
                except APIError as error:
                    gap(error.code if error.code in JSON_LIMIT_CODES else 'attestation-input-unavailable-or-invalid', predicate)
                except (OSError, UnicodeError, ValueError, TypeError, KeyError):
                    gap('attestation-input-unavailable-or-invalid', predicate)
    except _Invalid as exc:
        gap(exc.code)
    except ToolError:
        gap('attestation-tool-budget-or-execution-failed')
    except (APIError, OSError, UnicodeError, ValueError, TypeError, KeyError):
        gap('attestation-input-unavailable-or-invalid')
    decisions = [item['verification'] for item in observations['predicates']]
    observations['verification']['decision'] = ('fail' if 'fail' in decisions else
        'pass' if all(item == 'pass' for item in decisions) else 'unknown')
    result['observed_at'] = clock().isoformat()
    observations['collection']['finished_at'] = result['observed_at']
    return result


def verify_negative_attestation_control(*, expected_repository, expected_digest, required_predicates,
        public_key, envelopes, envelope_descriptors, cosign, output, attestation_size_policy=None, timeout=60.0):
    """Verify original inputs, then test one internally derived invalid signature.

    This is a negative-control report, never production verification or image
    authority. No caller-supplied replacement bytes or skipped hash check exist.
    """
    if (not isinstance(output, Path) or not output.is_absolute()
            or any(p.is_symlink() for p in (output, *output.parents))
            or '..' in output.parts or type(timeout) not in (int, float)
            or not math.isfinite(timeout) or not 1 <= timeout <= 120
            or not isinstance(required_predicates, (list, tuple)) or not 1 <= len(required_predicates) <= 16
            or any(not _predicate_type(p) for p in required_predicates)
            or len(set(required_predicates)) != len(required_predicates)
            or type(envelopes) is not dict or set(envelopes) != set(required_predicates)
            or type(envelope_descriptors) is not dict or set(envelope_descriptors) != set(required_predicates)
            or not _repository_ref(expected_repository) or not _digest(expected_digest)):
        raise ValueError('Exact original subjects, descriptors and a new control output are required')
    deadline = time.monotonic() + timeout
    def remaining():
        left = deadline - time.monotonic()
        if left < 1:
            raise _Invalid('attestation-time-budget-exhausted')
        return min(left, 120)
    result = {'schema_version': 1, 'provider': 'attestation-negative-control', 'decision': 'unknown',
        'execution_status': 'partial', 'confirmed_failure': False,
        'scope': {'repository': expected_repository, 'digest': expected_digest,
                  'required_predicates': list(required_predicates), 'control': 'internally-derived-payload-tamper'},
        'observations': {'verification': {'decision': 'unknown'}, 'original_verification': 'unknown',
                         'receipts': [], 'inputs': {}}, 'gaps': [], 'image_authority': 'not-issued'}
    try:
        output.mkdir(mode=0o700)
        with tempfile.TemporaryDirectory(prefix='devsecops-attestation-control-') as directory:
            scratch = Path(directory).resolve()
            frozen = {}
            bounds = {}
            reserved_envelopes = 0
            for index, predicate in enumerate(required_predicates):
                remaining()
                descriptor = envelope_descriptors[predicate]
                if (type(descriptor) is not dict or set(descriptor) != {'digest', 'size_bytes'}
                        or not _digest(descriptor['digest']) or type(descriptor['size_bytes']) is not int):
                    raise _Invalid('attestation-envelope-descriptor-missing')
                maximum, payload_limit = limits_for(attestation_size_policy,
                    descriptor['digest'], descriptor['size_bytes'])
                if not 0 < descriptor['size_bytes'] <= maximum:
                    raise _Invalid('attestation-size-limit')
                reserved_envelopes += descriptor['size_bytes']
                if reserved_envelopes > MAX_TOTAL_ENVELOPES:
                    raise _Invalid('attestation-aggregate-size-limit')
                destination = scratch / ('original-' + str(index) + '.json')
                receipt = _snapshot(envelopes[predicate], destination, descriptor['size_bytes'], deadline=deadline)
                if receipt != {'sha256': descriptor['digest'][7:], 'bytes': descriptor['size_bytes']}:
                    raise _Invalid('attestation-envelope-descriptor-mismatch')
                json_limit = payload_json_values_for(attestation_size_policy,
                    descriptor['digest'], descriptor['size_bytes'])
                frozen[predicate], bounds[predicate] = destination, (maximum, payload_limit, json_limit)
                result['observations']['inputs'][predicate] = receipt
            key = scratch / 'original-public-key.pem'
            key_receipt = _snapshot(public_key, key, 16384, deadline=deadline)
            positive = verify_attestations(expected_repository=expected_repository,
                expected_digest=expected_digest, required_predicates=required_predicates,
                public_key=key, envelopes=frozen, cosign=cosign, timeout=remaining(),
                **({} if attestation_size_policy is None else {
                    'attestation_size_policy': attestation_size_policy,
                    'envelope_descriptors': envelope_descriptors}))
            remaining()
            result['observations']['original_verification'] = positive['observations']['verification']['decision']
            # Retain the exact safe report behind its digest, including a failed
            # positive re-verification. It contains receipts/hashes, not payloads.
            original_report = json.dumps(positive, sort_keys=True, separators=(',', ':')).encode()
            fd = os.open(output / 'original-verification.json',
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(original_report)
                stream.flush()
                os.fsync(stream.fileno())
            result['observations']['original_report_sha256'] = hashlib.sha256(original_report).hexdigest()
            result['observations']['original_report_artifact'] = 'original-verification.json'
            if positive['observations']['verification']['decision'] != 'pass':
                raise _Invalid('negative-control-original-not-verified')
            predicate = required_predicates[0]
            maximum, payload_limit, json_limit = bounds[predicate]
            with frozen[predicate].open('rb') as stream:
                original = stream.read(maximum + 1)
            descriptor = envelope_descriptors[predicate]
            if (len(original) != descriptor['size_bytes']
                    or 'sha256:' + hashlib.sha256(original).hexdigest() != descriptor['digest']):
                raise _Invalid('attestation-envelope-descriptor-mismatch')
            statement, original_payload, _, _ = _decode(original, max_payload=payload_limit,
                max_payload_json_values=json_limit)
            # Change one predicate-type character while retaining the original
            # DSSE signature. Parsing and exact subject checks remain supported.
            original_kind = statement['predicateType']
            statement['predicateType'] = original_kind[:-1] + ('a' if original_kind[-1] != 'a' else 'b')
            payload = json.dumps(statement, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
            if payload == original_payload or len(payload) > payload_limit:
                raise _Invalid('negative-control-derived-size-invalid')
            envelope = _json(original)
            envelope['payload'] = base64.b64encode(payload).decode('ascii')
            derived = json.dumps(envelope, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
            if len(derived) > maximum:
                raise _Invalid('negative-control-derived-size-invalid')
            _, _, pae, signature = _decode(derived, max_payload=payload_limit,
                max_payload_json_values=json_limit)
            fd = os.open(output / 'tampered-envelope.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(derived)
                stream.flush()
                os.fsync(stream.fileno())
            result['observations']['derivation'] = {'predicate_type': predicate,
                'original_sha256': descriptor['digest'][7:], 'original_bytes': len(original),
                'derived_sha256': hashlib.sha256(derived).hexdigest(), 'derived_bytes': len(derived),
                'artifact': 'tampered-envelope.json', 'signature': 'original-retained',
                'production_input': False}
            # Re-pin the actual executable/key immediately before the control's
            # native invocation; positive reports never authorize changed bytes.
            binary = _snapshot(cosign, scratch / 'cosign', MAX_BINARY, deadline=deadline)
            if binary['sha256'] != positive['observations']['tool']['binary_sha256']:
                raise _Invalid('cosign-binary-mismatch')
            if _snapshot(key, scratch / 'key.pem', 16384, deadline=deadline) != key_receipt:
                raise _Invalid('negative-control-key-changed')
            _public_key((scratch / 'key.pem').read_bytes())
            os.chmod(scratch / 'cosign', 0o500)
            for name, raw in (('control.pae', pae), ('control.sig', signature.encode())):
                (scratch / name).write_bytes(raw)
                os.chmod(scratch / name, 0o400)
            for name in ('home', 'empty-path', 'cache', 'docker', 'tmp'):
                (scratch / name).mkdir(mode=0o700)
            env = {'PATH': str(scratch / 'empty-path'), 'HOME': str(scratch / 'home'),
                   'XDG_CACHE_HOME': str(scratch / 'cache'), 'DOCKER_CONFIG': str(scratch / 'docker'),
                   'TMPDIR': str(scratch / 'tmp')}
            code, _, err, receipt = run([str(scratch / 'cosign'), 'verify-blob', '--key', 'key.pem', '--offline',
                '--insecure-ignore-tlog', '--signature', 'control.sig', 'control.pae'], cwd=scratch,
                timeout=remaining(), limit=64000, env=env)
            result['observations']['receipts'].append({'operation': 'verify-derived-negative-dsse-pae', **receipt})
            remaining()
            if code != 0 and b'invalid signature when validating ASN.1 encoded signature' in err:
                result['observations']['verification']['decision'] = 'fail'
                result['execution_status'] = 'complete'
            else:
                raise _Invalid('negative-control-signature-rejection-unestablished')
    except _Invalid as error:
        result['gaps'].append({'code': error.code, 'required': True})
    except ToolError:
        result['gaps'].append({'code': 'negative-control-tool-unavailable', 'required': True})
    except (APIError, OSError, ValueError, TypeError, KeyError):
        result['gaps'].append({'code': 'negative-control-input-unavailable-or-invalid', 'required': True})
    return result
