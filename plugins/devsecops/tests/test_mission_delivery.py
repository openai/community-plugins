"""Composition keeps real capabilities in one process; reports never authorize."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from devsecops.factory import mission_delivery as delivery
from devsecops.factory import mission_staging as staging
from devsecops.runtime.context import verify_seal
from test_mission_staging import ca, registry, case, run, sha, REPOSITORY

__all__ = ['ca', 'registry', 'case']
ROOT = Path(__file__).resolve().parents[1]


def inputs(case, tmp_path):
    config = tmp_path / 'build.json'
    config.write_bytes(b'{"controlled_build_config":true}')
    return {'build_config': config, 'build_config_sha256': sha(config.read_bytes()),
        'staging_config': case['config'], 'staging_config_sha256': case['expected_config_sha256'],
        'output': tmp_path / 'delivery', 'runtime_root': tmp_path / 'runtime',
        'expected_source_repository': tmp_path / 'source', 'expected_source_revision': case['source_commit']}


def test_real_staging_composes_direct_built_object(case, registry, tmp_path, monkeypatch):
    called = []
    def build(**kwargs):
        called.append(kwargs)
        return {'decision': 'pass', 'operation_sha256': 'd' * 64}, case['built']
    monkeypatch.setattr(delivery, 'qualify_and_build', build)
    arguments = inputs(case, tmp_path)
    report, capability = delivery.qualify_build_and_stage(**arguments)
    assert len(called) == 1
    assert called[0]['expected_source_repository'] == arguments['expected_source_repository']
    assert called[0]['expected_source_revision'] == case['source_commit']
    assert called[0]['output'] == arguments['output'] / 'build'
    assert report['checks'] == {'build': 'pass', 'stage': 'unknown'}
    assert report['decision'] == 'unknown' and report['factory_decision'] == 'unknown'
    assert capability is None and registry['manifests']
    verify_seal(report, 'delivery_sha256')


def test_build_without_capability_never_stages(case, registry, tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, 'qualify_and_build', lambda **kwargs: ({'decision': 'unknown', 'operation_sha256': 'd' * 64}, None))
    report, capability = delivery.qualify_build_and_stage(**inputs(case, tmp_path))
    assert report['checks']['stage'] == 'not-started' and capability is None
    assert not registry['requests']


@pytest.mark.parametrize('part', ['build_config_sha256', 'staging_config_sha256'])
def test_both_pins_checked_before_any_build(case, registry, tmp_path, monkeypatch, part):
    def forbidden(**kwargs):
        pytest.fail('must not begin F0/build with a changed operator configuration')
    monkeypatch.setattr(delivery, 'qualify_and_build', forbidden)
    arguments = inputs(case, tmp_path)
    arguments[part] = '0' * 64
    with pytest.raises(ValueError):
        delivery.qualify_build_and_stage(**arguments)
    assert not registry['requests'] and not arguments['output'].exists()


def test_report_dictionary_is_not_a_direct_build_capability(case, registry, tmp_path, monkeypatch):
    class Forged:
        def describe(self):
            return case['_binding']
    monkeypatch.setattr(delivery, 'qualify_and_build', lambda **kwargs: ({'decision': 'pass', 'operation_sha256': 'd' * 64}, Forged()))
    with pytest.raises(ValueError, match='authority'):
        delivery.qualify_build_and_stage(**inputs(case, tmp_path))
    assert not registry['requests']


def test_combined_success_keeps_narrow_typed_result_and_terminal_guard(case, tmp_path, monkeypatch):
    # Test-injected build guard record, not a live canonical F0/build claim.
    case['_binding'].pop('execution_mode')
    _, authority = run(case)
    assert authority is not None
    monkeypatch.setattr(delivery, 'qualify_and_build', lambda **kwargs: ({'decision': 'pass', 'operation_sha256': 'd' * 64}, case['built']))
    monkeypatch.setattr(delivery, 'stage_mission', lambda **kwargs: ({'decision': 'pass', 'stage_sha256': 'e' * 64}, authority))
    report, returned = delivery.qualify_build_and_stage(**inputs(case, tmp_path))
    assert returned is authority and report['decision'] == 'pass' and report['factory_decision'] == 'unknown'
    assert report['binding']['repository'] == REPOSITORY
    assert report['downstream'] == dict.fromkeys(('scan', 'sign', 'promote'), 'not-started')


def test_combined_terminal_expiry_withdraws_success(case, tmp_path, monkeypatch):
    case['_binding'].pop('execution_mode')
    _, authority = run(case)
    monkeypatch.setattr(delivery, 'qualify_and_build', lambda **kwargs: ({'decision': 'pass', 'operation_sha256': 'd' * 64}, case['built']))
    monkeypatch.setattr(delivery, 'stage_mission', lambda **kwargs: ({'decision': 'pass', 'stage_sha256': 'e' * 64}, authority))
    original = delivery._write
    def write(directory, name, value):
        result = original(directory, name, value)
        if name == 'mission-delivery.json':
            staging._STAGED[authority]['expires_monotonic'] = 0
        return result
    monkeypatch.setattr(delivery, '_write', write)
    arguments = inputs(case, tmp_path)
    with pytest.raises(ValueError, match='expired'):
        delivery.qualify_build_and_stage(**arguments)
    assert not (arguments['output'] / 'mission-delivery.json').exists()


def test_cli_has_two_configs_no_imported_report_and_unknown_exit(case, tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location('stage_mission_script', ROOT / 'scripts/stage_mission.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    parsed = inputs(case, tmp_path)
    called = []
    def execute(**kwargs):
        called.append(kwargs)
        return {'decision': 'unknown', 'factory_decision': 'unknown'}, None
    monkeypatch.setattr(module, 'qualify_build_and_stage', execute)
    argv = [part for key, value in parsed.items() for part in ('--' + key.replace('_', '-'), str(value))]
    assert module.main(argv) == 2
    assert called[0]['build_config_sha256'] == parsed['build_config_sha256']
    assert called[0]['staging_config_sha256'] == parsed['staging_config_sha256']
    assert 'unknown' in capsys.readouterr().out
    help_text = module.parser().format_help()
    assert '--build-report' not in help_text and '--staging-report' not in help_text


def test_packaged_wrapper_imports_from_extracted_installation(tmp_path):
    from scripts import build_plugin
    archive = tmp_path / 'staging candidate.zip'
    build_plugin.build(archive)
    extracted = tmp_path / 'clean installed plugin with spaces'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    plugin = extracted / 'devsecops'
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    result = subprocess.run([sys.executable, '-B', str(plugin / 'scripts/stage_mission.py'), '--help'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert '--staging-config-sha256' in result.stdout
    assert (plugin / 'docs/interfaces/mission-staging.md').is_file()
    assert (plugin / 'demo/software-factory/ci/mission-staging.example.json').is_file()
