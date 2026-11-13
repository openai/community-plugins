"""Same-process wiring, early pins and installed entrypoint behavior."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from devsecops.factory import mission_security_operation as operation
from test_mission_security import case, sha, tools
from test_vulnerability_policy import encode

__all__ = ['case', 'tools']
ROOT = Path(__file__).resolve().parents[1]


def inputs(case, tmp_path):
    build, stage = tmp_path / 'build.json', tmp_path / 'staging.json'
    build.write_bytes(b'{"controlled":"build-fixture"}')
    stage.write_bytes(b'{"controlled":"staging-fixture"}')
    arguments = case['args']
    selected = {'schema_version': 1, 'kind': 'mission-security-configuration',
        'policy_path': str(arguments['policy']), 'policy_sha256': arguments['expected_policy_sha256'],
        'database_path': str(arguments['database']), 'listing_path': str(arguments['listing']),
        'syft_path': str(arguments['syft']), 'grype_path': str(arguments['grype'])}
    path = tmp_path / 'security.json'
    path.write_bytes(encode(selected))
    return {'build_config': build, 'build_config_sha256': sha(build.read_bytes()),
        'staging_config': stage, 'staging_config_sha256': sha(stage.read_bytes()),
        'security_config': path, 'security_config_sha256': sha(path.read_bytes()),
        'output': tmp_path / 'operation', 'runtime_root': tmp_path / 'runtime',
        'expected_source_repository': tmp_path / 'repository', 'expected_source_revision': 'a'*40}


def test_direct_staged_result_runs_native_scanners(case, tmp_path, monkeypatch):
    calls = []
    def delivery(**kwargs):
        calls.append(kwargs)
        return {'decision': 'pass', 'delivery_sha256': 'a'*64}, case['args']['staged']
    monkeypatch.setattr(operation, 'qualify_build_and_stage', delivery)
    arguments = inputs(case, tmp_path)
    report, cap = operation.qualify_stage_and_assess(**arguments)
    assert len(calls) == 1 and calls[0]['expected_source_revision'] == arguments['expected_source_revision']
    assert report['checks'] == {'delivery': 'pass', 'assessment': 'unknown'} and cap is None
    assert report['factory_decision'] == 'unknown'
    assert (arguments['output'] / 'security/scan/grype.json').is_file()


@pytest.mark.parametrize('field', ['build_config_sha256', 'staging_config_sha256', 'security_config_sha256'])
def test_three_pins_preflight_before_build_or_upload(case, tmp_path, monkeypatch, field):
    arguments = inputs(case, tmp_path)
    arguments[field] = '0'*64
    monkeypatch.setattr(operation, 'qualify_build_and_stage', lambda **kw: pytest.fail('must not begin delivery'))
    with pytest.raises(ValueError):
        operation.qualify_stage_and_assess(**arguments)
    assert not arguments['output'].exists()


@pytest.mark.parametrize('field', ['policy', 'listing', 'database'])
def test_security_inputs_checked_before_mutating_delivery(case, tmp_path, monkeypatch, field):
    arguments = inputs(case, tmp_path)
    case['args'][field].write_bytes(b'changed')
    monkeypatch.setattr(operation, 'qualify_build_and_stage', lambda **kw: pytest.fail('must not begin delivery'))
    with pytest.raises(ValueError):
        operation.qualify_stage_and_assess(**arguments)


def test_incomplete_delivery_never_scans(case, tmp_path, monkeypatch):
    monkeypatch.setattr(operation, 'qualify_build_and_stage', lambda **kw: ({'decision': 'unknown', 'delivery_sha256': 'a'*64}, None))
    monkeypatch.setattr(operation, 'assess_staged_mission', lambda **kw: pytest.fail('no staging authority'))
    report, cap = operation.qualify_stage_and_assess(**inputs(case, tmp_path))
    assert report['checks']['assessment'] == 'not-started' and cap is None


def test_report_object_cannot_replace_staged_authority(case, tmp_path, monkeypatch):
    class Forged:
        def describe(self):
            return case['binding']
    monkeypatch.setattr(operation, 'qualify_build_and_stage', lambda **kw: ({'decision': 'pass', 'delivery_sha256': 'a'*64}, Forged()))
    with pytest.raises(ValueError, match='authority'):
        operation.qualify_stage_and_assess(**inputs(case, tmp_path))


def test_terminal_policy_failure_after_deadline_is_withdrawn(case, tmp_path, monkeypatch):
    monkeypatch.setattr(operation, 'qualify_build_and_stage', lambda **kw: (
        {'decision': 'pass', 'delivery_sha256': 'a'*64}, case['args']['staged']))
    monkeypatch.setattr(operation, 'assess_staged_mission', lambda **kw: (
        {'decision': 'fail', 'assessment_sha256': 'b'*64}, None))
    original_write = operation._write
    original_time = operation.time.monotonic
    def changed(directory, name, value):
        result = original_write(directory, name, value)
        if name == 'mission-security-operation.json':
            monkeypatch.setattr(operation.time, 'monotonic', lambda: original_time()+4000)
        return result
    monkeypatch.setattr(operation, '_write', changed)
    args = inputs(case, tmp_path)
    with pytest.raises(ValueError, match='deadline'):
        operation.qualify_stage_and_assess(**args)
    assert not (args['output'] / 'mission-security-operation.json').exists()


@pytest.mark.parametrize('decision,code', [('pass', 0), ('fail', 1), ('unknown', 2)])
def test_cli_three_configs_no_imported_report(case, tmp_path, monkeypatch, capsys, decision, code):
    spec = importlib.util.spec_from_file_location('assess_mission_script', ROOT / 'scripts/assess_mission.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    called = []
    def execute(**kwargs):
        called.append(kwargs)
        return {'decision': decision, 'factory_decision': 'unknown'}, None
    monkeypatch.setattr(module, 'qualify_stage_and_assess', execute)
    args = inputs(case, tmp_path)
    assert module.main([part for key, value in args.items() for part in ('--'+key.replace('_', '-'), str(value))]) == code
    assert called[0]['security_config_sha256'] == args['security_config_sha256']
    assert decision in capsys.readouterr().out
    assert '--staging-report' not in module.parser().format_help()


def test_configuration_rejects_relative_paths_and_extras(case, tmp_path):
    args = inputs(case, tmp_path)
    value = json.loads(args['security_config'].read_text())
    for key, replacement in [('policy_path', '../policy.json'), ('policy_sha256', True), ('execute', 'arbitrary-script')]:
        changed = {**value, key: replacement}
        with pytest.raises(ValueError):
            operation._configuration(encode(changed))


def test_packaged_wrapper_works_without_repository_imports(tmp_path):
    from scripts import build_plugin
    archive = tmp_path / 'security.zip'
    build_plugin.build(archive)
    extracted = tmp_path / 'clean installed plugin with spaces'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    plugin = extracted / 'devsecops'
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    result = subprocess.run([sys.executable, '-B', str(plugin / 'scripts/assess_mission.py'), '--help'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert '--security-config-sha256' in result.stdout
    assert (plugin / 'docs/interfaces/mission-security.md').is_file()
