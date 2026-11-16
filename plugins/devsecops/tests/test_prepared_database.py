"""Actual pinned Grype cache preparation on a deliberately empty test database."""
from contextlib import contextmanager
import json
from pathlib import Path
import pickle
import time

import pytest

from devsecops.adapters import artifacts as scanner
from devsecops.adapters import prepared_database as prepared
from devsecops.factory import mission_security_operation as operation
from test_artifacts import database, source, tools, tree_hash
from test_mission_security import case
from test_mission_security_operation import inputs
from test_vulnerability_policy import encode

__all__ = ['source', 'tools', 'case']


@pytest.fixture
def cache(tmp_path, tools):
    selected = database(tmp_path)
    with prepared.prepare_database(database=selected['database_snapshot'],
            database_sha256=selected['database_sha256'], grype=tools['grype'],
            output=tmp_path / 'preparation') as value:
        yield value, selected


def bound(cache, tools):
    value, selected = cache
    return prepared.bind_prepared_database(value, database_sha256=selected['database_sha256'],
        grype_sha256=scanner._hash(tools['grype'].read_bytes()), max_database_age_hours=120,
        deadline=time.monotonic()+30)


def test_real_scan_reuses_readonly_cache_and_preserves_coverage_limits(cache, source, tmp_path, tools):
    value, selected = cache
    binding = bound(cache, tools)
    result = scanner.scan_local_artifact(source=source, source_kind='directory',
        expected_sha256=tree_hash(source, tmp_path), output=tmp_path / 'scan',
        prepared_database=value, **selected, **tools)
    assert result['observations']['vulnerabilities']['decision'] == 'pass'
    assert result['observations']['database']['preparation_sha256'] == binding['preparation_sha256']
    assert sum(row['operation'] == 'db' for row in result['observations']['receipts']) == 1
    assert result['decision'] == 'unknown'
    assert 'database-origin-unverified' in {row['code'] for row in result['gaps']}
    assert bound(cache, tools) == binding
    assert Path(binding['cache']).stat().st_mode & 0o777 == 0o500


@pytest.mark.parametrize('change', ['dictionary', 'unissued', 'process', 'expired', 'archive', 'tool', 'age'])
def test_context_and_exact_inputs_are_required(cache, tools, change):
    value, selected = cache
    args = dict(database_sha256=selected['database_sha256'], grype_sha256=scanner._hash(tools['grype'].read_bytes()),
                max_database_age_hours=120, deadline=time.monotonic()+30)
    if change == 'dictionary':
        value = {'cache': str(prepared._LIVE[value]['cache'])}
    elif change == 'unissued':
        value = object.__new__(prepared.PreparedDatabase)
    elif change in ('process', 'expired'):
        prepared._LIVE[value]['pid' if change == 'process' else 'expires_monotonic'] = -1
    else:
        args[{'archive': 'database_sha256', 'tool': 'grype_sha256', 'age': 'max_database_age_hours'}[change]] = 121 if change == 'age' else '0'*64
    with pytest.raises(ValueError):
        prepared.bind_prepared_database(value, **args)


@pytest.mark.parametrize('change', ['bytes', 'replacement', 'symlink', 'additional-file'])
def test_changed_cache_is_rejected(cache, tools, tmp_path, change):
    root = Path(bound(cache, tools)['cache']) / '6'
    target = root / 'vulnerability.db'
    root.chmod(0o700)
    if change == 'additional-file':
        (root / 'unexpected').write_bytes(b'not selected')
    elif change == 'symlink':
        outside = tmp_path / 'outside.db'
        outside.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(outside)
    elif change == 'replacement':
        content = target.read_bytes()
        target.unlink()
        target.write_bytes(content)
        target.chmod(0o400)
    else:
        target.chmod(0o600)
        content = bytearray(target.read_bytes())
        content[-1] ^= 1
        target.write_bytes(content)
        target.chmod(0o400)
    root.chmod(0o500)
    with pytest.raises((ValueError, OSError)):
        bound(cache, tools)


def test_cache_context_closes_and_cannot_be_serialized(tmp_path, tools):
    selected = database(tmp_path)
    with prepared.prepare_database(database=selected['database_snapshot'],
            database_sha256=selected['database_sha256'], grype=tools['grype'], output=tmp_path / 'preparation') as value:
        binding = bound((value, selected), tools)
        with pytest.raises(TypeError):
            pickle.dumps(value)
    assert not Path(binding['cache']).exists()
    with pytest.raises(ValueError):
        bound((value, selected), tools)
    report = json.loads((tmp_path / 'preparation/database-preparation.json').read_text())
    assert report['scan_performed'] is False and report['promotion_authority'] is False


@pytest.mark.parametrize('field,maximum', [('database_preparation_timeout_seconds', 1800), ('scanner_timeout_seconds', 600)])
@pytest.mark.parametrize('invalid', [True, False, None, 0, -1, 1.5, 'over-limit'])
def test_configuration_budgets_are_closed(case, tmp_path, field, maximum, invalid):
    args = inputs(case, tmp_path)
    value = json.loads(args['security_config'].read_bytes())
    value[field] = maximum+1 if invalid == 'over-limit' else invalid
    with pytest.raises(ValueError, match='bounded'):
        operation._configuration(encode(value))


def test_database_preparation_precedes_delivery_and_is_retired_after_assessment(case, tmp_path, monkeypatch):
    args = inputs(case, tmp_path)
    config = json.loads(args['security_config'].read_bytes())
    config.update(database_preparation_timeout_seconds=60, scanner_timeout_seconds=600)
    args['security_config'].write_bytes(encode(config))
    args['security_config_sha256'] = scanner._hash(args['security_config'].read_bytes())
    observed = []
    def delivery(**kwargs):
        assert (args['output'] / 'database-preparation/database-preparation.json').is_file()
        assert len(prepared._LIVE) == 1
        observed.extend(prepared._LIVE)
        return {'decision': 'pass', 'delivery_sha256': 'a'*64}, case['args']['staged']
    monkeypatch.setattr(operation, 'qualify_build_and_stage', delivery)
    result, cap = operation.qualify_stage_and_assess(**args)
    assert len(observed) == 1 and observed[0] not in prepared._LIVE
    assert cap is None and result['checks']['assessment'] == 'unknown'
    report = json.loads((args['output'] / 'security/scan/receipt.json').read_text())
    preparation = json.loads((args['output'] / 'database-preparation/database-preparation.json').read_text())
    assert report['observations']['database']['preparation_sha256'] == preparation['preparation_sha256']
    assert (args['output'] / 'security/scan/grype.json').is_file()


def test_expensive_preparation_does_not_renew_image_authority(case, tmp_path, monkeypatch):
    from devsecops.factory import mission_staging
    original = prepared.prepare_database
    @contextmanager
    def expired(**kwargs):
        with original(**kwargs) as value:
            mission_staging._STAGED[case['args']['staged']]['expires_monotonic'] = 0
            yield value
    monkeypatch.setattr(prepared, 'prepare_database', expired)
    monkeypatch.setattr(operation, 'qualify_build_and_stage', lambda **kw: (
        {'decision': 'pass', 'delivery_sha256': 'a'*64}, case['args']['staged']))
    args = inputs(case, tmp_path)
    config = json.loads(args['security_config'].read_bytes())
    config['database_preparation_timeout_seconds'] = 60
    args['security_config'].write_bytes(encode(config))
    args['security_config_sha256'] = scanner._hash(args['security_config'].read_bytes())
    with pytest.raises(ValueError, match='authority'):
        operation.qualify_stage_and_assess(**args)
    assert not prepared._LIVE


@pytest.mark.parametrize('change', ['preparation-failed', 'policy.json', 'listing.json', 'database.archive'])
def test_preparation_failure_or_changed_frozen_inputs_prevent_delivery(case, tmp_path, monkeypatch, change):
    original = prepared.prepare_database
    @contextmanager
    def modified(**kwargs):
        if change == 'preparation-failed':
            raise ValueError('deliberate preparation failure')
        with original(**kwargs) as value:
            path = kwargs['database'].parent / change
            content = bytearray(path.read_bytes())
            content[-1] ^= 1
            path.chmod(0o600)
            path.write_bytes(content)
            path.chmod(0o400)
            yield value
    def forbidden_delivery(**kwargs):
        pytest.fail('Delivery must not start after failed preparation or altered inputs')
    monkeypatch.setattr(prepared, 'prepare_database', modified)
    monkeypatch.setattr(operation, 'qualify_build_and_stage', forbidden_delivery)
    args = inputs(case, tmp_path)
    config = json.loads(args['security_config'].read_bytes())
    config['database_preparation_timeout_seconds'] = 60
    args['security_config'].write_bytes(encode(config))
    args['security_config_sha256'] = scanner._hash(args['security_config'].read_bytes())
    with pytest.raises(ValueError, match='preparation failure|Retained security evidence'):
        operation.qualify_stage_and_assess(**args)
    assert not prepared._LIVE
