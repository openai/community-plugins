"""Direct current assessment to an actual locally signed, verified image bundle."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import time
from weakref import WeakKeyDictionary

from devsecops.adapters.artifacts import MAX_SOURCE, MAX_OUTPUT
from devsecops.adapters.http import HTTPSReadClient, _json
from devsecops.adapters.local_signing import sign_payload
from devsecops.adapters.signatures import _public_key, _repository_ref, _snapshot, verify_image_signature
from devsecops.adapters.vulnerability_policy import checkpoint, timestamp, text
from devsecops.factory.mission_security import _read_retained, require_assessed_mission
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import canonical_bytes, seal

_SIGNED = WeakKeyDictionary()
_TRUST_MODES = ('operator-managed-application-key', 'owned-lab-application-key', 'controlled-test')
_VERIFIER_GAPS = {'signer-authority-unverified', 'key-lifecycle-unverified', 'transparency-log-unverified',
                  'official-image-origin-unverified', 'required-attestations-unverified'}


class LocallySignedMission:
    """Local signing evidence only: no registry publication or promotion grant."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('Local signing authority requires a direct assessed image and verified signature')

    def __reduce__(self):
        raise TypeError('Local signing authority cannot be serialized')

    def describe(self):
        record = _SIGNED.get(self)
        if record is None:
            raise ValueError('Unissued local signing authority')
        return deepcopy(record['binding'])


def require_locally_signed_mission(value, *, run_scope, source_commit, architecture, origin, repository,
                                   policy_sha256, trust_policy_sha256):
    record = _SIGNED.get(value) if type(value) is LocallySignedMission else None
    if record is None or record['pid'] != os.getpid() or record['expires_monotonic'] <= time.monotonic():
        raise ValueError('Unissued, foreign-process or expired local signing authority')
    binding = record['binding']
    if (any(binding[name] != selected for name, selected in (
            ('run_scope', run_scope), ('source_commit', source_commit), ('architecture', architecture),
            ('origin', origin), ('repository', repository), ('policy_sha256', policy_sha256),
            ('trust_policy_sha256', trust_policy_sha256)))
            or datetime.now(timezone.utc) >= timestamp(binding['expires_at'])):
        raise ValueError('Local signing authority differs from selected run, image scope or trust policy')
    return deepcopy(binding)


def signing_configuration(raw):
    selected = _json(raw)
    if (set(selected) != {'schema_version', 'kind', 'private_key_path', 'password_env', 'public_key_path',
                          'trust_policy_path', 'trust_policy_sha256', 'cosign_path'}
            or type(selected['schema_version']) is not int or selected['schema_version'] != 1
            or selected['kind'] != 'mission-local-signing-configuration'
            or not isinstance(selected['trust_policy_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', selected['trust_policy_sha256'])
            or not isinstance(selected['password_env'], str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', selected['password_env'])):
        raise ValueError('An explicit independently pinned local signing configuration is required')
    for field in ('private_key_path', 'public_key_path', 'trust_policy_path', 'cosign_path'):
        if (not isinstance(selected[field], str) or len(selected[field]) > 4096
                or not Path(selected[field]).is_absolute() or '..' in Path(selected[field]).parts):
            raise ValueError('Signing paths must reference explicit local files')
    return selected


def trust_document(raw, *, deadline):
    checkpoint(deadline)
    if len(raw) > 64000:
        raise ValueError('Signing trust policy exceeds its bound')
    selected = _json(raw)
    if (set(selected) != {'schema_version', 'kind', 'id', 'version', 'issuer', 'authority', 'public_key_sha256',
                          'origin', 'repository', 'status', 'valid_from', 'expires_at', 'trust_reference'}
            or type(selected['schema_version']) is not int or selected['schema_version'] != 1
            or selected['kind'] != 'mission-application-signing-trust'
            or selected['authority'] not in _TRUST_MODES
            or not isinstance(selected['public_key_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', selected['public_key_sha256'])
            or selected['status'] not in ('active', 'revoked')):
        raise ValueError('Unsupported application signing trust policy')
    for field in ('id', 'version', 'issuer'):
        text(selected[field], 128)
    text(selected['trust_reference'], 1024)
    origin = HTTPSReadClient(selected['origin'])
    if (origin.origin != selected['origin'] or origin._host == 'registry1.dso.mil'
            or not _repository_ref(selected['origin'].removeprefix('https://') + '/' + selected['repository'])):
        raise ValueError('Application signer must be scoped to an exact staging repository')
    trust_guard(selected, deadline=deadline)
    return selected


def trust_guard(trust, *, deadline):
    checkpoint(deadline)
    now = datetime.now(timezone.utc)
    if trust['status'] != 'active' or not timestamp(trust['valid_from']) <= now < timestamp(trust['expires_at']):
        raise ValueError('Application signing key is revoked, expired or not yet valid')
    return timestamp(trust['expires_at'])


def _evidence(source):
    """Path-free signed identities; bind the complete direct description by hash."""
    selected = {name: deepcopy(source[name]) for name in ('run_scope', 'source_commit', 'architecture',
        'context_sha256', 'base', 'manifest_digest', 'config_digest', 'build_report_sha256',
        'policy_sha256', 'policy_id', 'policy_version', 'database', 'tools', 'scanner_receipt_sha256',
        'applied_exceptions', 'expires_at')}
    selected['staging'] = {name: deepcopy(source[name]) for name in ('origin', 'repository', 'configuration_sha256', 'readback_sha256')}
    selected['archive'] = {'sha256': source['archive_sha256'], 'bytes': source['archive_bytes']}
    for name in ('sbom', 'vulnerabilities'):
        selected[name] = {key: source[name][key] for key in ('sha256', 'bytes')}
    selected['assessment_binding_sha256'] = hashlib.sha256(canonical_bytes(source)).hexdigest()
    return selected


def sign_assessed_mission(*, assessed, run_scope, source_commit, architecture, origin, repository,
                          policy_sha256, config, expected_config_sha256, output, timeout=180):
    """Sign only direct current evidence, then independently verify actual bytes."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 2 <= timeout <= 300
            or not isinstance(expected_config_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_config_sha256)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Pinned signing configuration, new output and bounded deadline are required')
    deadline = time.monotonic() + timeout
    run_scope = deepcopy(run_scope)
    def admission():
        checkpoint(deadline)
        return require_assessed_mission(assessed, run_scope=run_scope, source_commit=source_commit,
            architecture=architecture, origin=origin, repository=repository, policy_sha256=policy_sha256)
    source = admission()  # Reject saved JSON before credentials or processes.
    report = {'schema_version': 1, 'kind': 'mission-local-signing', 'decision': 'unknown',
        'local_signing_authority': 'not-issued', 'factory_decision': 'unknown', 'scope': 'local-signed-artifacts-only',
        'run_scope': run_scope, 'source_commit': source_commit, 'architecture': architecture,
        'subject': {'repository': origin.removeprefix('https://') + '/' + repository, 'digest': source['manifest_digest']},
        'configuration_sha256': expected_config_sha256, 'started_at': datetime.now(timezone.utc).isoformat(),
        'checks': {'assessment': 'pass', 'signer_trust': 'unknown', 'artifact': 'unknown', 'sign': 'not-started', 'verify': 'not-started'},
        'downstream': {'publish': 'not-started', 'registry_readback': 'not-started', 'promote': 'not-started'},
        'network_mode': 'local-key-no-oidc-no-tlog-no-timestamp', 'gaps': []}
    binding = None
    with _new_directory(output) as directory:
        retained = {}
        def freeze(path, name, limit, expected=None):
            _verify_output_path(output, directory)
            ref = _snapshot(path, output / name, limit, deadline=deadline)
            if expected is not None and ref['sha256'] != expected:
                raise ValueError('Signing input differs from selected evidence')
            retained[name] = ref
            _verify_output_path(output, directory)
            return ref
        def read(name):
            return _read_retained(directory, name, retained[name], deadline, materialize=True)
        def audit():
            admission()
            trust_guard(trust, deadline=deadline)
            _verify_output_path(output, directory)
            for name, ref in retained.items():
                _read_retained(directory, name, ref, deadline)
            _verify_output_path(output, directory)
        try:
            freeze(config, 'signing-config.json', 64000, expected_config_sha256)
            selected = signing_configuration(read('signing-config.json'))
            trust_ref = freeze(Path(selected['trust_policy_path']), 'signing-trust.json', 64000, selected['trust_policy_sha256'])
            trust = trust_document(read('signing-trust.json'), deadline=deadline)
            if (trust['origin'], trust['repository']) != (origin, repository):
                raise ValueError('Signer trust differs from selected staging destination')
            key_ref = freeze(Path(selected['public_key_path']), 'public-key.pem', 16384, trust['public_key_sha256'])
            public = read('public-key.pem')
            _public_key(public)
            report['signer'] = {name: trust[name] for name in ('issuer', 'authority', 'id', 'version', 'trust_reference', 'public_key_sha256')}
            report['signer'].update(trust_policy_sha256=trust_ref['sha256'],
                lifecycle='operator-selected-active-policy', publisher_authority='not-claimed', transparency_log='not-used')
            report['checks']['signer_trust'] = 'pass' if trust['authority'] != 'controlled-test' else 'unknown'
            ref = freeze(Path(source['archive_path']), 'image.oci.tar', MAX_SOURCE, source['archive_sha256'])
            if ref['bytes'] != source['archive_bytes']:
                raise ValueError('Image archive size differs from assessment')
            for name, filename in [('sbom', 'sbom.spdx.json'), ('vulnerabilities', 'grype.json')]:
                ref = freeze(Path(source[name]['path']), filename, MAX_OUTPUT, source[name]['sha256'])
                if ref['bytes'] != source[name]['bytes']:
                    raise ValueError('Scanner artifact size differs from assessment')
            # The issuer fixes this adjacent path; no report-supplied path is followed.
            receipt_path = Path(source['archive_path']).parent / 'scanner-receipt.json'
            freeze(receipt_path, 'scanner-receipt.json', MAX_OUTPUT, source['scanner_receipt_sha256'])
            report['checks']['artifact'] = 'pass'
            expires = min(trust_guard(trust, deadline=deadline), timestamp(source['expires_at']),
                datetime.now(timezone.utc) + timedelta(minutes=15))
            signed_evidence = {'schema_version': 1, 'kind': 'devsecops-mission-security-evidence',
                'issuer': deepcopy(report['signer']), 'assessment': _evidence(source),
                'signed_at': datetime.now(timezone.utc).isoformat(), 'expires_at': expires.isoformat()}
            payload = {'critical': {'identity': {'docker-reference': report['subject']['repository']},
                'image': {'docker-manifest-digest': source['manifest_digest']}, 'type': 'cosign container image signature'},
                'optional': {'devsecops': signed_evidence}}
            raw = canonical_bytes(payload)
            if len(raw) > 1000000:
                raise ValueError('Signed image evidence exceeds its bound')
            retained['payload.json'] = _write(directory, 'payload.json', payload)
            audit()
            report['checks']['sign'] = 'unknown'
            bundle, tool, receipts = sign_payload(payload=read('payload.json'), public_key=public,
                private_key=Path(selected['private_key_path']), password_env=selected['password_env'],
                cosign=Path(selected['cosign_path']), deadline=deadline)
            admission()
            trust_guard(trust, deadline=deadline)
            retained['bundle.json'] = _write(directory, 'bundle.json', bundle.decode('utf-8'))
            report['tool'], report['receipts'] = tool, receipts
            report['checks']['sign'] = 'pass'
            remaining = deadline - time.monotonic()
            if remaining < 1:
                raise ValueError('Insufficient remaining time for signature verification')
            report['checks']['verify'] = 'unknown'
            verification = verify_image_signature(expected_repository=report['subject']['repository'],
                expected_digest=source['manifest_digest'], public_key=output / 'public-key.pem',
                payload=output / 'payload.json', bundle=output / 'bundle.json', cosign=Path(selected['cosign_path']),
                timeout=min(60, remaining))
            admission()
            retained['signature-verification.json'] = _write(directory, 'signature-verification.json', verification)
            observed = verification['observations']
            if (any(observed[name]['decision'] != 'pass' for name in ('crypto', 'subject_binding', 'verification'))
                    or {item['code'] for item in verification['gaps']} != _VERIFIER_GAPS
                    or observed['tool'] != tool or observed.get('bundle_payload_digest_matches') is not True
                    or observed['inputs'] != {'public-key.pem': key_ref, 'payload.json': retained['payload.json'], 'bundle.json': retained['bundle.json']}):
                raise ValueError('Generated image signature verification is incomplete or differs')
            report['checks']['verify'] = 'pass'
            audit()
            if trust['authority'] == 'controlled-test' or source.get('execution_mode') == 'controlled-test':
                report['gaps'].append('controlled-evidence-cannot-issue-local-signing-authority')
            else:
                binding = {**deepcopy(source), 'archive_path': str(output / 'image.oci.tar'),
                    'sbom': {'path': str(output / 'sbom.spdx.json'), **retained['sbom.spdx.json']},
                    'vulnerabilities': {'path': str(output / 'grype.json'), **retained['grype.json']},
                    'trust_policy_sha256': trust_ref['sha256'], 'signer': deepcopy(report['signer']),
                    'signature_payload': {'path': str(output / 'payload.json'), **retained['payload.json']},
                    'signature_bundle': {'path': str(output / 'bundle.json'), **retained['bundle.json']},
                    'signature_verification_sha256': retained['signature-verification.json']['sha256'],
                    'signing_tool': tool, 'scope': 'local-signed-artifacts-only', 'expires_at': expires.isoformat()}
                report['decision'], report['local_signing_authority'] = 'pass', 'issued'
                report['binding'] = deepcopy(binding)
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
            binding = None
            report['decision'], report['local_signing_authority'] = 'unknown', 'not-issued'
            report.pop('binding', None)
            report['gaps'].append('local-signing-evidence-incomplete-or-changed')
        report['artifacts'] = deepcopy(retained)
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report = seal(report, 'signing_sha256')
        _verify_output_path(output, directory)
        try:
            _write(directory, 'mission-signing.json', report)
            os.fsync(directory)
            _verify_output_path(output, directory)
            if report['checks']['verify'] == 'pass':
                audit()
                if datetime.now(timezone.utc) >= expires:
                    raise ValueError('Local signing evidence expired during publication')
        except BaseException:
            try:
                os.unlink('mission-signing.json', dir_fd=directory)
            except FileNotFoundError:
                pass
            os.fsync(directory)
            raise
        if binding is None:
            return report, None
        capability = object.__new__(LocallySignedMission)
        _SIGNED[capability] = {'pid': os.getpid(), 'binding': deepcopy(binding),
            'expires_monotonic': time.monotonic() + (expires-datetime.now(timezone.utc)).total_seconds()}
        return report, capability
