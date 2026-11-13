"""Real native tools on controlled OCI/DB inputs; no live database qualification."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import pickle
import sqlite3
import time

import pytest

from devsecops.factory import mission_security as security
from devsecops.factory import mission_staging as staging
from devsecops.runtime.context import verify_seal
from test_artifacts import database, oci_archive, tools
from test_vulnerability_policy import policy, encode

__all__ = ['tools']


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def case(tmp_path, tools):
    image, manifest = oci_archive(tmp_path)
    from devsecops.adapters.artifacts import _oci_archive
    parsed = _oci_archive(image, manifest, 'amd64')
    # Test-only guard record. The marker explicitly prevents assessment authority.
    bound = {'run_scope': {'run_id': 'controlled-scan-test', 'attempt_id': 'one'},
        'source_commit': 'c' * 40, 'architecture': 'amd64', 'origin': 'https://staging.example.invalid',
        'repository': 'demo/mission', 'manifest_digest': manifest, 'config_digest': parsed['config_digest'],
        'archive_path': str(image), 'archive_sha256': sha(image.read_bytes()), 'archive_bytes': image.stat().st_size,
        'context_sha256': 'a' * 64, 'base': {'execution_mode': 'controlled-test'}, 'build_report_sha256': 'b' * 64,
        'configuration_sha256': 'd' * 64, 'readback_sha256': 'e' * 64,
        'execution_mode': 'controlled-test', 'expires_at': (datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()}
    cap = object.__new__(staging.StagedMission)
    staging._STAGED[cap] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic()+600, 'binding': bound}
    db = database(tmp_path, age_hours=1)
    with sqlite3.connect(tmp_path / 'vulnerability.db') as connection:
        recorded = datetime.fromisoformat(connection.execute('SELECT build_timestamp FROM db_metadata').fetchone()[0])
        # The pinned CLI status/descriptor representation truncates to seconds.
        built = recorded.replace(microsecond=0).isoformat()
    selected = policy()
    selected['database']['archive_sha256'] = db['database_sha256']
    listed = {'status': 'active', 'schemaVersion': '6.1.10', 'built': built,
        'path': 'vulnerability-db_v6.1.10_controlled.tar.gz', 'checksum': 'sha256:' + db['database_sha256']}
    listing_path, policy_path = tmp_path / 'listing.json', tmp_path / 'policy.json'
    listing_path.write_bytes(encode(listed))
    selected['database']['listing_sha256'] = sha(listing_path.read_bytes())
    policy_path.write_bytes(encode(selected))
    args = {'staged': cap, **{key: bound[key] for key in ('run_scope', 'source_commit', 'architecture', 'origin', 'repository')},
        'policy': policy_path, 'expected_policy_sha256': sha(policy_path.read_bytes()), 'database': db['database_snapshot'],
        'listing': listing_path, **tools, 'output': tmp_path / 'assessment'}
    return {'args': args, 'binding': bound, 'policy': selected, 'listing': listed}


def test_native_scanners_bind_controlled_image_and_cannot_issue_authority(case):
    report, capability = security.assess_staged_mission(**case['args'])
    assert capability is None and report['decision'] == 'unknown' and report['factory_decision'] == 'unknown'
    assert report['checks'] == {'staging': 'pass', 'database': 'unknown', 'scanner': 'pass', 'policy': 'unknown'}
    assert report['policy']['packages'] == 1 and report['policy']['counts']['Critical'] == 0
    assert set(report['gaps']) == {'distribution-coverage-unavailable', 'database-provider-coverage-incomplete',
        'controlled-evidence-cannot-issue-security-authority'}
    assert report['database']['publisher_cryptographic_authentication'] == 'not-claimed'
    assert report['downstream'] == {'sign': 'not-started', 'promote': 'not-started'}
    root = case['args']['output']
    for name, reference in report['artifacts'].items():
        assert sha((root / name).read_bytes()) == reference['sha256']
    assert 'SECRET_CONFIG_CANARY' not in json.dumps(report)
    verify_seal(report, 'assessment_sha256')


@pytest.mark.parametrize('change', ['dictionary', 'scope', 'commit', 'destination', 'expired', 'process'])
def test_guard_rejects_before_io_or_tools(case, monkeypatch, change):
    arguments = case['args']
    if change == 'dictionary':
        arguments['staged'] = deepcopy(case['binding'])
    elif change == 'scope':
        arguments['run_scope'] = {'run_id': 'another'}
    elif change == 'commit':
        arguments['source_commit'] = 'f' * 40
    elif change == 'destination':
        arguments['repository'] = 'different/image'
    elif change == 'expired':
        staging._STAGED[arguments['staged']]['expires_monotonic'] = 0
    else:
        staging._STAGED[arguments['staged']]['pid'] += 1
    monkeypatch.setattr(security, 'scan_local_artifact', lambda **kwargs: pytest.fail('must not execute tools'))
    with pytest.raises(ValueError):
        security.assess_staged_mission(**arguments)
    assert not arguments['output'].exists()


@pytest.mark.parametrize('changed', ['policy', 'listing', 'database', 'archive'])
def test_changed_inputs_prevent_tool_execution(case, monkeypatch, changed):
    arguments = case['args']
    path = Path(case['binding']['archive_path']) if changed == 'archive' else arguments[changed]
    path.write_bytes(path.read_bytes() + b' ')
    monkeypatch.setattr(security, 'scan_local_artifact', lambda **kwargs: pytest.fail('must reject before tools'))
    report, capability = security.assess_staged_mission(**arguments)
    assert report['decision'] == 'unknown' and capability is None
    assert 'security-evidence-incomplete-or-changed' in report['gaps']
    assert report['checks']['scanner'] == 'not-started'


def test_stale_database_listing_rejected_before_tools(case, monkeypatch):
    case['listing']['built'] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    case['args']['listing'].write_bytes(encode(case['listing']))
    case['policy']['database']['listing_sha256'] = sha(case['args']['listing'].read_bytes())
    case['args']['policy'].write_bytes(encode(case['policy']))
    case['args']['expected_policy_sha256'] = sha(case['args']['policy'].read_bytes())
    monkeypatch.setattr(security, 'scan_local_artifact', lambda **kwargs: pytest.fail('must reject stale metadata'))
    report, _ = security.assess_staged_mission(**case['args'])
    assert report['decision'] == 'unknown' and report['checks']['scanner'] == 'not-started'


@pytest.mark.parametrize('name', ['receipt.json', 'grype.json', 'sbom.spdx.json'])
def test_changed_retained_scanner_bytes_do_not_pass(case, monkeypatch, name):
    actual = security.scan_local_artifact
    def changed(**kwargs):
        result = actual(**kwargs)
        path = kwargs['output'] / name
        path.write_bytes(path.read_bytes().replace(b'pass', b'fail') if name == 'receipt.json' else b'{}')
        return result
    monkeypatch.setattr(security, 'scan_local_artifact', changed)
    report, capability = security.assess_staged_mission(**case['args'])
    assert report['decision'] == 'unknown' and capability is None
    assert 'security-evidence-incomplete-or-changed' in report['gaps']


def test_actual_scanner_deadline_cannot_become_policy_pass(case, monkeypatch):
    actual_scan, actual_time = security.scan_local_artifact, security.time.monotonic
    def finished_after_deadline(**kwargs):
        result = actual_scan(**kwargs)
        assert result['observations']['vulnerabilities']['decision'] == 'pass'
        monkeypatch.setattr(security.time, 'monotonic', lambda: actual_time() + 1000)
        return result
    monkeypatch.setattr(security, 'scan_local_artifact', finished_after_deadline)
    report, capability = security.assess_staged_mission(**case['args'])
    assert report['decision'] == 'unknown' and capability is None
    assert report['checks']['scanner'] == 'unknown' and report['checks']['policy'] == 'not-started'
    assert 'security-evidence-incomplete-or-changed' in report['gaps']


def test_controlled_evidence_cannot_issue_even_with_simulated_passing_policy(case, monkeypatch):
    actual = security.evaluate_scan
    def simulated(*args, **kwargs):
        result = actual(*args, **kwargs)
        result['decision'], result['gaps'] = 'pass', []
        return result
    monkeypatch.setattr(security, 'evaluate_scan', simulated)
    report, capability = security.assess_staged_mission(**case['args'])
    assert report['checks']['scanner'] == 'pass' and report['checks']['policy'] == 'pass'
    assert report['decision'] == 'unknown' and capability is None
    assert report['assessment_authority'] == 'not-issued'
    assert 'controlled-evidence-cannot-issue-security-authority' in report['gaps']


@pytest.mark.parametrize('change', ['expiry', 'artifact', 'route'])
def test_terminal_failure_receipt_is_withdrawn_if_evidence_changes(case, monkeypatch, change):
    # Exercise publication using an explicitly simulated policy result after
    # real native scanner execution. This does not issue an AssessedMission.
    actual_evaluate = security.evaluate_scan
    def negative(*args, **kwargs):
        result = actual_evaluate(*args, **kwargs)
        result['decision'], result['exceeded'] = 'fail', ['High']
        return result
    monkeypatch.setattr(security, 'evaluate_scan', negative)
    actual_write = security._write
    output = case['args']['output']
    displaced = output.with_name('displaced-assessment')
    def changed(directory, name, value):
        result = actual_write(directory, name, value)
        if name == 'mission-security.json':
            if change == 'expiry':
                staging._STAGED[case['args']['staged']]['expires_monotonic'] = 0
            elif change == 'artifact':
                (output / 'grype.json').chmod(0o600)
                (output / 'grype.json').write_bytes(b'{}')
            else:
                output.rename(displaced)
                output.mkdir(mode=0o700)
        return result
    monkeypatch.setattr(security, '_write', changed)
    with pytest.raises(ValueError):
        security.assess_staged_mission(**case['args'])
    assert not (output / 'mission-security.json').exists()
    assert not (displaced / 'mission-security.json').exists()


def test_no_public_constructor_or_report_authority(case):
    with pytest.raises(TypeError):
        security.AssessedMission()
    selectors = {name: case['args'][name] for name in ('run_scope', 'source_commit', 'architecture', 'origin', 'repository')}
    with pytest.raises(ValueError):
        security.require_assessed_mission(case['binding'], policy_sha256='a'*64, **selectors)
    # Private test record exercises expiry/scope only, never the production issuer.
    cap = object.__new__(security.AssessedMission)
    binding = {**case['binding'], 'policy_sha256': 'a'*64}
    security._ASSESSED[cap] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic()+10, 'binding': binding}
    assert security.require_assessed_mission(cap, policy_sha256='a'*64, **selectors)['source_commit'] == binding['source_commit']
    with pytest.raises(ValueError):
        security.require_assessed_mission(cap, policy_sha256='b'*64, **selectors)
    with pytest.raises(TypeError):
        pickle.dumps(cap)
    security._ASSESSED[cap]['expires_monotonic'] = 0
    with pytest.raises(ValueError):
        security.require_assessed_mission(cap, policy_sha256='a'*64, **selectors)
