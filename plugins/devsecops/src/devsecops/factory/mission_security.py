"""Direct staged image -> pinned native scanners -> explicit security policy."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import time
from weakref import WeakKeyDictionary

from devsecops.adapters.artifacts import MAX_DATABASE, MAX_OUTPUT, MAX_SOURCE, scan_local_artifact
from devsecops.adapters.http import APIError, _json
from devsecops.adapters.signatures import _snapshot
from devsecops.adapters.scanner_catalog import NORMALIZATION
from devsecops.adapters.vulnerability_policy import (
    CONTROLLED, checkpoint, database_listing, evaluate_scan, policy_document, temporal_guard, timestamp,
)
from devsecops.factory.mission_staging import require_staged_mission
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal

_ASSESSED = WeakKeyDictionary()
_BASELINE_GAPS = {'artifact-origin-unverified', 'database-origin-unverified',
                  'signature-and-attestations-unverified', 'promotion-not-approved'}


class AssessedMission:
    """Current narrow scan/policy authority, never signing or promotion authority."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('AssessedMission requires direct scanner execution and explicit policy assessment')

    def __reduce__(self):
        raise TypeError('Security authority cannot be serialized')

    def describe(self):
        record = _ASSESSED.get(self)
        if record is None:
            raise ValueError('Unissued security authority')
        return deepcopy(record['binding'])


def require_assessed_mission(value, *, run_scope, source_commit, architecture, origin, repository, policy_sha256):
    record = _ASSESSED.get(value) if type(value) is AssessedMission else None
    if record is None or record['pid'] != os.getpid() or time.monotonic() >= record['expires_monotonic']:
        raise ValueError('Unissued, foreign-process or expired security authority')
    binding = record['binding']
    if (any(binding[key] != selected for key, selected in (
            ('run_scope', run_scope), ('source_commit', source_commit), ('architecture', architecture),
            ('origin', origin), ('repository', repository), ('policy_sha256', policy_sha256)))
            or datetime.now(timezone.utc) >= timestamp(binding['expires_at'])):
        raise ValueError('Security authority differs from the selected run, destination or policy')
    return deepcopy(binding)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_nlink)


def _read_retained(directory, name, expected, deadline, *, materialize=False):
    """Hash a bounded, stable, private file through the retained output directory."""
    checkpoint(deadline)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_size != expected['bytes']
                or before.st_uid != os.getuid() or before.st_nlink != 1 or before.st_mode & 0o077
                or materialize and before.st_size > MAX_OUTPUT):
            raise ValueError('Retained security evidence changed')
        digest, total, chunks = hashlib.sha256(), 0, []
        while chunk := stream.read(65536):
            checkpoint(deadline)
            total += len(chunk)
            if total > expected['bytes']:
                raise ValueError('Retained security evidence exceeds its bound')
            digest.update(chunk)
            if materialize:
                chunks.append(chunk)
        if (total != expected['bytes'] or _identity(before) != _identity(os.fstat(stream.fileno()))
                or digest.hexdigest() != expected['sha256']):
            raise ValueError('Retained security evidence differs from assessment')
    checkpoint(deadline)
    return b''.join(chunks) if materialize else None


def assess_staged_mission(*, staged, run_scope, source_commit, architecture, origin, repository,
                          policy, expected_policy_sha256, database, listing, syft, grype, output, timeout=600,
                          scanner_timeout_seconds=300, prepared_database=None):
    """Run actual local scanners once over the exact immutable staged archive.

The operator selects a separately trusted offline DB/listing package in a pinned
policy. No network, update, report import, signing or promotion occurs here.
Controlled provenance exercises mechanics but cannot issue AssessedMission.
"""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 900
            or type(scanner_timeout_seconds) is not int or not 1 <= scanner_timeout_seconds <= 600
            or not isinstance(expected_policy_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_policy_sha256)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Pinned policy, new output and bounded deadline are required')
    deadline = time.monotonic() + timeout
    run_scope = deepcopy(run_scope)
    def admission():
        checkpoint(deadline)
        return require_staged_mission(staged, run_scope=run_scope, source_commit=source_commit,
            architecture=architecture, origin=origin, repository=repository)
    source = admission()  # No imported report can reach files or tools.
    report = {'schema_version': 1, 'kind': 'mission-security-assessment', 'decision': 'unknown',
        'assessment_authority': 'not-issued', 'factory_decision': 'unknown', 'scope': 'staged-image-scan-policy-only',
        'run_scope': run_scope, 'source_commit': source_commit, 'architecture': architecture,
        'destination': {'origin': origin, 'repository': repository, 'manifest_digest': source['manifest_digest']},
        'policy_sha256': expected_policy_sha256, 'started_at': datetime.now(timezone.utc).isoformat(),
        'checks': {'staging': 'pass', 'database': 'unknown', 'scanner': 'not-started', 'policy': 'not-started'},
        'downstream': {'sign': 'not-started', 'promote': 'not-started'}, 'artifacts': {}, 'gaps': []}
    binding = None
    with _new_directory(output) as directory:
        retained = {}
        def freeze(path, name, limit, expected=None):
            _verify_output_path(output, directory)
            reference = _snapshot(path, output / name, limit, deadline=deadline)
            if expected is not None and reference['sha256'] != expected:
                raise ValueError('Security input differs from independently selected identity')
            retained[name] = reference
            _verify_output_path(output, directory)
            return reference
        def read(name):
            return _read_retained(directory, name, retained[name], deadline, materialize=True)
        def audit():
            admission()
            _verify_output_path(output, directory)
            for name, reference in retained.items():
                _read_retained(directory, name, reference, deadline)
            _verify_output_path(output, directory)
        try:
            freeze(policy, 'policy.json', 256000, expected_policy_sha256)
            selected = policy_document(read('policy.json'), deadline=deadline)
            db_policy = selected['database']
            freeze(listing, 'database-listing.json', 64000, db_policy['listing_sha256'])
            listed = database_listing(read('database-listing.json'), selected, deadline=deadline)
            db_ref = freeze(database, 'database.archive', MAX_DATABASE, db_policy['archive_sha256'])
            image_ref = freeze(Path(source['archive_path']), 'image.oci.tar', MAX_SOURCE, source['archive_sha256'])
            if image_ref['bytes'] != source['archive_bytes']:
                raise ValueError('Staged archive size differs')
            report['database'] = {'authority': db_policy['authority'], 'trust_reference': db_policy['trust_reference'],
                'source_url': db_policy['source_url'], 'acquired_at': db_policy['acquired_at'],
                'archive': db_ref, 'listing': retained['database-listing.json'],
                'built_at': listed['built'], 'schema_version': listed['schemaVersion'],
                'publisher_cryptographic_authentication': 'not-claimed'}
            report['checks']['database'] = 'pass' if db_policy['authority'] != CONTROLLED else 'unknown'
            admission()
            remaining = deadline - time.monotonic()
            if remaining < 1:
                raise ValueError('Security assessment deadline exceeded')
            report['checks']['scanner'] = 'unknown'
            scan = scan_local_artifact(source=output / 'image.oci.tar', source_kind='oci-archive',
                expected_sha256=image_ref['sha256'], expected_image_digest=source['manifest_digest'],
                architecture=architecture, syft=syft, grype=grype, output=output / 'scan',
                database_snapshot=output / 'database.archive', database_sha256=db_ref['sha256'],
                max_database_age_hours=db_policy['maximum_age_hours'], timeout=min(scanner_timeout_seconds, remaining),
                prepared_database=prepared_database)
            admission()
            freeze(output / 'scan/receipt.json', 'scanner-receipt.json', MAX_OUTPUT)
            if _json(read('scanner-receipt.json')) != scan:
                raise ValueError('Scanner receipt differs from direct execution')
            observed = scan['observations']
            report['scanner_gaps'] = [item['code'] for item in scan['gaps']
                if isinstance(item.get('code'), str) and re.fullmatch(r'[a-z0-9-]{1,128}', item['code'])]
            if ({item['code'] for item in scan['gaps']} != _BASELINE_GAPS
                    or observed['coverage'] != 'one-platform-image'
                    or observed['source'] != image_ref or observed['image']['manifest_digest'] != source['manifest_digest']
                    or observed['image']['config_digest'] != source['config_digest']
                    or observed['image']['platform'] != {'os': 'linux', 'architecture': architecture}
                    or any(observed.get(name, {}).get(key) != observed['image'][key]
                        for name in ('catalog', 'normalization') for key in ('manifest_digest', 'config_digest', 'platform'))
                    or observed['normalization'].get('method') != NORMALIZATION
                    or observed['database']['decision'] != 'pass' or observed['database']['sha256'] != db_ref['sha256']
                    or observed['database']['bytes'] != db_ref['bytes'] or observed['sbom']['decision'] != 'pass'
                    or observed['vulnerabilities']['decision'] not in ('pass', 'fail')):
                raise ValueError('Actual scanner execution is incomplete or differs from staging')
            for key, name in [('sbom', 'sbom.spdx.json'), ('vulnerabilities', 'grype.json')]:
                expected = observed['artifacts'][key]
                if expected['path'] != name:
                    raise ValueError('Scanner artifact route differs')
                actual = freeze(output / 'scan' / name, name, MAX_OUTPUT, expected['sha256'])
                if actual['bytes'] != expected['bytes']:
                    raise ValueError('Scanner artifact size differs')
            report['checks']['scanner'] = 'pass'
            assessment = evaluate_scan(read('sbom.spdx.json'), read('grype.json'), observed, source,
                selected, listed, deadline=deadline)
            report['policy'] = {'id': selected['id'], 'version': selected['version'], **assessment}
            report['checks']['policy'] = assessment['decision']
            report['gaps'].extend(assessment['gaps'])
            if db_policy['authority'] == CONTROLLED or source.get('execution_mode') == CONTROLLED:
                report['gaps'].append('controlled-evidence-cannot-issue-security-authority')
            if assessment['decision'] == 'fail':
                report['decision'] = 'fail'
            elif assessment['decision'] == 'pass' and not report['gaps']:
                expires = min(timestamp(source['expires_at']), timestamp(assessment['expires_at']),
                    datetime.now(timezone.utc) + timedelta(minutes=15))
                binding = {**deepcopy(source), 'archive_path': str(output / 'image.oci.tar'),
                    'policy_sha256': expected_policy_sha256, 'policy_id': selected['id'], 'policy_version': selected['version'],
                    'database': deepcopy(report['database']), 'tools': deepcopy(observed['tools']),
                    'sbom': {'path': str(output / 'sbom.spdx.json'), **retained['sbom.spdx.json']},
                    'vulnerabilities': {'path': str(output / 'grype.json'), **retained['grype.json']},
                    'scanner_receipt_sha256': retained['scanner-receipt.json']['sha256'],
                    'applied_exceptions': assessment['applied_exceptions'], 'expires_at': expires.isoformat()}
                report['decision'], report['assessment_authority'] = 'pass', 'issued'
                report['binding'] = deepcopy(binding)
            audit()
            temporal_guard(selected, listed, deadline=deadline, applied=assessment['applied_exceptions'])
        except (APIError, OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
            binding = None
            report['decision'], report['assessment_authority'] = 'unknown', 'not-issued'
            report.pop('binding', None)
            report['gaps'].append('security-evidence-incomplete-or-changed')
        report['artifacts'] = {name: reference for name, reference in retained.items()}
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report = seal(report, 'assessment_sha256')
        _verify_output_path(output, directory)
        try:
            _write(directory, 'mission-security.json', report)
            os.fsync(directory)
            _verify_output_path(output, directory)
            if binding is not None or report['decision'] == 'fail':
                audit()
                temporal_guard(selected, listed, deadline=deadline, applied=assessment['applied_exceptions'])
                if binding is not None and datetime.now(timezone.utc) >= expires:
                    raise ValueError('Assessment authority expired during publication')
        except BaseException:
            try:
                os.unlink('mission-security.json', dir_fd=directory)
            except FileNotFoundError:
                pass
            os.fsync(directory)
            raise
        if binding is None:
            return report, None
        capability = object.__new__(AssessedMission)
        _ASSESSED[capability] = {'pid': os.getpid(), 'binding': deepcopy(binding),
            'expires_monotonic': time.monotonic() + (expires - datetime.now(timezone.utc)).total_seconds()}
        return report, capability
