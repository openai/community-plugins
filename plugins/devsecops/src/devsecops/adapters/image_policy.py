"""Explicit operator policy over directly acquired, verified image evidence.

Policy satisfaction is a narrow observation. It cannot establish publisher
ownership, certificate revocation, layer integrity or runtime compatibility.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

from .attestations import _decode, _predicate_type
from .http import HTTPSReadClient, _json
from .image_verification import verify_registry_image
from .registry_download_policy import load_blob_download_policy
from .registry_attestation_policy import (load_attestation_size_policy, limits_for as attestation_limits_for,
    payload_json_values_for as attestation_json_values_for)
from .oci import _digest, _repository
from .signatures import _snapshot
from devsecops.factory.state import _read, _safe_path
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def _instant(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value):
        raise ValueError('Policy timestamps require UTC seconds')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _pointer(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 256 or not value.startswith('/')
            or re.search(r'[\x00-\x1f\x7f]|~(?![01])', value)):
        raise ValueError('Unsupported policy pointer')
    parts = [part.replace('~1', '/').replace('~0', '~') for part in value[1:].split('/')]
    if len(parts) > 16:
        raise ValueError('Policy pointer is too deep')
    return parts


def _rules(rules):
    if not isinstance(rules, list) or not 1 <= len(rules) <= 32:
        raise ValueError('Each required predicate needs bounded explicit rules')
    paths = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {'path', 'equals'}:
            raise ValueError('Only exact policy equality is supported')
        path = tuple(_pointer(rule['path']))
        if path in paths or len(_canonical(rule['equals'])) > 4096:
            raise ValueError('Duplicate or oversized policy rule')
        paths.add(path)


def _policy(raw):
    policy = _json(raw)
    if (set(policy) != {'schema_version', 'kind', 'subject', 'signer', 'predicates'}
            or type(policy['schema_version']) is not int or policy['schema_version'] != 1
            or policy['kind'] != 'registry-image-policy'):
        raise ValueError('Unsupported image policy')
    subject, signer, predicates = policy['subject'], policy['signer'], policy['predicates']
    if (not isinstance(subject, dict) or set(subject) != {'origin', 'repository', 'digest', 'architecture'}
            or not _repository(subject['repository']) or not _digest(subject['digest'])
            or subject['architecture'] not in {'amd64', 'arm64'}):
        raise ValueError('An exact immutable image subject is required')
    # Validate and require the canonical spelling before any authentication.
    if HTTPSReadClient(subject['origin']).origin != subject['origin']:
        raise ValueError('Policy origin must use canonical HTTPS spelling')
    if (not isinstance(signer, dict) or set(signer) != {'key_sha256', 'valid_from', 'valid_until'}
            or not isinstance(signer['key_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', signer['key_sha256'])):
        raise ValueError('An explicit signer identity and policy validity window are required')
    start, end = _instant(signer['valid_from']), _instant(signer['valid_until'])
    if not 0 < (end - start).total_seconds() <= 90 * 86400:
        raise ValueError('Policy validity must be positive and at most 90 days')
    if not isinstance(predicates, list) or not 1 <= len(predicates) <= 16:
        raise ValueError('Required predicate policy is missing')
    seen = set()
    for item in predicates:
        if (not isinstance(item, dict) or set(item) != {'type', 'rules'}
                or not _predicate_type(item['type']) or item['type'] in seen):
            raise ValueError('Predicate policy is unsupported or duplicated')
        seen.add(item['type'])
        _rules(item['rules'])
    return policy


def _valid_now(policy):
    now = datetime.now(timezone.utc)
    signer = policy['signer']
    return _instant(signer['valid_from']) <= now < _instant(signer['valid_until'])


def _matches(document, rule, checkpoint=lambda: None):
    value = document
    for part in _pointer(rule['path']):
        checkpoint()
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and re.fullmatch(r'0|[1-9][0-9]*', part) and int(part) < len(value):
            value = value[int(part)]
        else:
            return False
    # Compare a bounded JSON stream rather than materializing a possibly large
    # candidate value once for each rule. Preserve boolean/number distinctions.
    expected = _canonical(rule['equals'])
    offset = 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    for piece in encoder.iterencode(value):
        checkpoint()
        if len(piece) > len(expected) - offset:
            return False
        raw = piece.encode()
        if not expected.startswith(raw, offset):
            return False
        offset += len(raw)
    checkpoint()
    return offset == len(expected)


def _config(config, architecture):
    # BuildKit's Go JSON decoder matches these field names without regard to
    # case. Reject aliases/collisions before assessing the Python-decoded view.
    canonical = {'config': 'config', 'os': 'os', 'architecture': 'architecture'}
    if any(key.casefold() in canonical and key != canonical[key.casefold()] for key in config):
        return 'fail'
    settings = config.get('config')
    if (config.get('os') != 'linux' or config.get('architecture') != architecture
            or not isinstance(settings, dict)):
        return 'unknown'
    if any(key.casefold() == 'onbuild' and key != 'OnBuild' for key in settings):
        return 'fail'
    # A fixed child Dockerfile cannot override inherited ONBUILD execution.
    # Even apparently harmless triggers require a different reviewed recipe.
    if settings.get('OnBuild') not in (None, []):
        return 'fail'
    return 'pass'


def verify_registry_image_policy(*, policy_path, expected_policy_sha256, public_key,
                                 cosign, output, token_env=None, basic_username_env=None,
                                 basic_password_env=None, token_path=None, token_service=None,
                                 ca_bundle=None, total_timeout=240,
                                 blob_download_policy=None, blob_download_policy_sha256=None,
                                 attestation_size_policy=None, attestation_size_policy_sha256=None):
    """Run acquisition and cryptography here, then assess the pinned policy.

    There is no imported-report or configurable evaluator callback. The caller
    supplies policy authority separately from image metadata and credentials.
    """
    if (not isinstance(expected_policy_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_policy_sha256)
            or type(total_timeout) not in (int, float) or not math.isfinite(total_timeout)
            or not 10 <= total_timeout <= 300 or not isinstance(output, Path) or not output.is_absolute()):
        raise ValueError('Explicit policy identity, output and bounded deadline are required')
    output = _safe_path(output)
    if output.exists():
        raise ValueError('Policy assessment requires a new output directory')
    deadline = time.monotonic() + total_timeout
    def checkpoint():
        if time.monotonic() >= deadline:
            raise ValueError('Image policy assessment exceeded its deadline')
    with tempfile.TemporaryDirectory(prefix='devsecops-image-policy-') as temporary:
        scratch = Path(temporary).resolve()
        ref = _snapshot(policy_path, scratch / 'policy.json', 64_000, deadline=deadline)
        if ref['sha256'] != expected_policy_sha256:
            raise ValueError('Image policy differs from the independently selected identity')
        policy = _policy((scratch / 'policy.json').read_bytes())
        if not _valid_now(policy):
            raise ValueError('Image policy is outside its validity window')
        subject = policy['subject']
        selected_download = load_blob_download_policy(blob_download_policy, blob_download_policy_sha256,
            registry_origin=subject['origin'], allowed_repositories=[subject['repository']])
        selected_sizes = load_attestation_size_policy(attestation_size_policy, attestation_size_policy_sha256,
            origin=subject['origin'], repository=subject['repository'], subject_digest=subject['digest'], deadline=deadline)
        remaining = deadline - time.monotonic()
        if remaining < 5:
            raise ValueError('Insufficient time for image verification')
        # The output is created exclusively before any credentialed request.
        with _new_directory(output) as directory:
            verification = verify_registry_image(subject['origin'], repository=subject['repository'],
                requested_digest=subject['digest'], architecture=subject['architecture'],
                allowed_repositories=[subject['repository']],
                required_predicates=[item['type'] for item in policy['predicates']],
                public_key=public_key, expected_key_sha256=policy['signer']['key_sha256'],
                cosign=cosign, output=output / 'verification', token_env=token_env,
                basic_username_env=basic_username_env, basic_password_env=basic_password_env,
                token_path=token_path, token_service=token_service, ca_bundle=ca_bundle,
                total_timeout=min(240, remaining),
                **({} if selected_download is None else {'blob_download_policy': blob_download_policy,
                    'blob_download_policy_sha256': blob_download_policy_sha256}),
                **({} if selected_sizes is None else {'attestation_size_policy': attestation_size_policy,
                    'attestation_size_policy_sha256': attestation_size_policy_sha256}))
            result = {'schema_version': 1, 'kind': 'registry-image-policy-assessment',
                'decision': 'unknown', 'build_authority': 'not-issued', 'policy_sha256': ref['sha256'],
                'subject': subject, 'verification_sha256': verification['verification_sha256'],
                'checks': {'cryptography': verification['verification'], 'image_config': 'unknown',
                           'predicate_rules': 'unknown', 'policy_current': 'unknown'},
                'predicates': [], 'gaps': list(verification['gaps']), 'policy_satisfaction': 'unknown'}

            if selected_download is not None:
                result['blob_download_policy'] = selected_download.identity_record()

            if selected_sizes is not None:
                result['attestation_size_policy'] = selected_sizes.identity_record()

            def artifact(name, expected, *, attestation=False):
                checkpoint()
                if (not isinstance(name, str) or '/' in name or '\\' in name or name in {'.', '..'}
                        or not isinstance(expected, dict)):
                    raise ValueError('Unsupported verification artifact')
                maximum = 4_000_000
                if attestation:
                    maximum, _ = attestation_limits_for(selected_sizes, 'sha256:' + expected['sha256'], expected['bytes'])
                raw = _read(output / 'verification' / name, maximum)
                if len(raw) != expected['bytes'] or _sha(raw) != expected['sha256']:
                    raise ValueError('Verified artifact changed before policy assessment')
                checkpoint()
                return raw

            if verification['verification'] == 'pass':
                acquisition = _json(artifact('registry-acquisition.json', verification['evidence']['registry-acquisition.json']))
                attestations = _json(artifact('required-attestations.json', verification['evidence']['required-attestations.json']))
                image = acquisition['observations']['image']
                config_name = 'sha256-' + image['config_digest'][7:] + '.blob'
                config = _json(artifact(config_name, acquisition['artifacts'][config_name]))
                result['checks']['image_config'] = _config(config, subject['architecture'])
                checked = {row['predicate_type']: row for row in attestations['observations']['predicates']}
                for requirement in policy['predicates']:
                    checkpoint()
                    kind = requirement['type']
                    selected = [row for row in acquisition['observations']['attestations'] if row['predicate_type'] == kind]
                    if len(selected) != 1:
                        raise ValueError('Required verified predicate is ambiguous')
                    name = selected[0]['envelope_artifact']
                    raw = artifact(name, acquisition['artifacts'][name], attestation=True)
                    if _sha(raw) != attestations['observations']['inputs'][kind]['sha256']:
                        raise ValueError('Predicate envelope differs from the cryptographic input')
                    _, payload_limit = attestation_limits_for(selected_sizes, 'sha256:' + _sha(raw), len(raw))
                    json_limit = attestation_json_values_for(selected_sizes, 'sha256:' + _sha(raw), len(raw))
                    statement, payload, _, _ = _decode(raw, max_payload=payload_limit,
                        max_payload_json_values=json_limit)
                    checkpoint()
                    observed = checked[kind]
                    if (observed['verification'] != 'pass' or _sha(payload) != observed['statement_sha256']
                            or _sha(_canonical(statement['predicate'])) != observed['predicate_sha256']):
                        raise ValueError('Predicate content differs from the cryptographic observation')
                    checkpoint()
                    decisions = []
                    for rule in requirement['rules']:
                        checkpoint()
                        decisions.append('pass' if _matches(statement['predicate'], rule, checkpoint) else 'fail')
                        checkpoint()
                    result['predicates'].append({'type': kind, 'predicate_sha256': observed['predicate_sha256'],
                        'rules': [{'index': index, 'decision': value} for index, value in enumerate(decisions)],
                        'decision': 'pass' if all(value == 'pass' for value in decisions) else 'fail'})
                result['checks']['predicate_rules'] = ('pass' if all(row['decision'] == 'pass' for row in result['predicates']) else 'fail')
            result['checks']['policy_current'] = 'pass' if _valid_now(policy) else 'unknown'
            values = list(result['checks'].values())
            result['policy_satisfaction'] = ('fail' if 'fail' in values else 'pass' if all(value == 'pass' for value in values) else 'unknown')
            result['gaps'].extend(['policy-window-is-not-certificate-lifecycle', 'predicate-rule-match-is-not-factual-validation'])
            if time.monotonic() >= deadline:
                raise ValueError('Image policy assessment exceeded its deadline')
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            _verify_output_path(output, directory)
            _write(directory, 'policy-assessment.json', result)
            os.fsync(directory)
            try:
                _verify_output_path(output, directory)
                if time.monotonic() >= deadline or not _valid_now(policy):
                    raise ValueError('Image policy expired during publication')
            except (OSError, ValueError):
                os.unlink('policy-assessment.json', dir_fd=directory)
                os.fsync(directory)
                raise
    return result
