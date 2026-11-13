"""Signing composition preserves independent pins and same-process authority."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

from devsecops.factory import mission_signing as signing
from devsecops.factory import mission_signing_operation as operation
from test_mission_signing import case, scanned, lab, encode, sha

__all__ = ['case', 'scanned', 'lab']
ROOT = Path(__file__).resolve().parents[1]


def inputs(case, tmp_path):
    arguments = {'signing_config': case['args']['config'], 'signing_config_sha256': case['args']['expected_config_sha256'],
        'output': tmp_path / 'operation', 'runtime_root': tmp_path / 'runtime',
        'expected_source_repository': tmp_path / 'repository', 'expected_source_revision': 'a'*40}
    for name in ('build', 'staging', 'security'):
        path = tmp_path / (name + '.json')
        value = {'controlled': name}
        if name == 'staging':
            value = {'schema_version': 1, 'kind': 'mission-staging-configuration', 'origin': case['binding']['origin'],
                'repository': case['binding']['repository'], 'credentials': {'token_env': 'NOT_USED_BY_CONTROLLED_TEST'}, 'ca_bundle': None}
        path.write_bytes(encode(value))
        arguments[name+'_config'], arguments[name+'_config_sha256'] = path, sha(path.read_bytes())
    return arguments


def test_direct_assessment_composes_actual_local_signing(case, tmp_path, monkeypatch):
    calls = []
    def assess(**kwargs):
        calls.append(kwargs)
        return {'decision': 'pass', 'security_operation_sha256': 'a'*64}, case['args']['assessed']
    monkeypatch.setattr(operation, 'qualify_stage_and_assess', assess)
    arguments = inputs(case, tmp_path)
    result, cap = operation.qualify_assess_and_sign(**arguments)
    assert result['checks'] == {'assessment': 'pass', 'local_signing': 'unknown'} and cap is None
    assert result['factory_decision'] == 'unknown' and result['downstream']['publish'] == 'not-started'
    assert calls[0]['expected_source_revision'] == arguments['expected_source_revision']
    verifier = json.loads((arguments['output'] / 'signing/signature-verification.json').read_text())
    assert verifier['observations']['verification']['decision'] == 'pass'


@pytest.mark.parametrize('name', ['build', 'staging', 'security', 'signing'])
def test_all_pins_checked_before_delivery(case, tmp_path, monkeypatch, name):
    arguments = inputs(case, tmp_path)
    arguments[name+'_config_sha256'] = '0'*64
    monkeypatch.setattr(operation, 'qualify_stage_and_assess', lambda **kw: pytest.fail('no delivery after changed pin'))
    with pytest.raises(ValueError):
        operation.qualify_assess_and_sign(**arguments)
    assert not arguments['output'].exists()


@pytest.mark.parametrize('part', ['trust', 'public', 'scope'])
def test_signing_trust_preflight_precedes_registry_mutations(case, tmp_path, monkeypatch, part):
    arguments = inputs(case, tmp_path)
    if part == 'scope':
        stage = json.loads(arguments['staging_config'].read_bytes())
        stage['repository'] = 'different/repository'
        arguments['staging_config'].write_bytes(encode(stage))
        arguments['staging_config_sha256'] = sha(arguments['staging_config'].read_bytes())
    else:
        Path(case['config']['trust_policy_path' if part == 'trust' else 'public_key_path']).write_bytes(b'changed')
    monkeypatch.setattr(operation, 'qualify_stage_and_assess', lambda **kw: pytest.fail('no delivery with changed signer trust'))
    with pytest.raises(ValueError):
        operation.qualify_assess_and_sign(**arguments)


@pytest.mark.parametrize('decision', ['unknown', 'fail'])
def test_incomplete_or_failed_assessment_never_signs(case, tmp_path, monkeypatch, decision):
    monkeypatch.setattr(operation, 'qualify_stage_and_assess', lambda **kw: ({'decision': decision, 'security_operation_sha256': 'a'*64}, None))
    monkeypatch.setattr(operation, 'sign_assessed_mission', lambda **kw: pytest.fail('no assessment authority'))
    result, cap = operation.qualify_assess_and_sign(**inputs(case, tmp_path))
    assert cap is None and result['decision'] == decision and result['checks']['local_signing'] == 'not-started'


def test_report_dictionary_cannot_authorize_signer(case, tmp_path, monkeypatch):
    class Forged:
        def describe(self):
            return case['binding']
    monkeypatch.setattr(operation, 'qualify_stage_and_assess', lambda **kw: ({'decision': 'pass', 'security_operation_sha256': 'a'*64}, Forged()))
    with pytest.raises(ValueError, match='authority'):
        operation.qualify_assess_and_sign(**inputs(case, tmp_path))


def test_terminal_expired_capability_withdraws_operation(case, tmp_path, monkeypatch):
    # Test-only guard record exercises terminal plumbing; no real authority issuer.
    cap = object.__new__(signing.LocallySignedMission)
    binding = {**case['binding'], 'trust_policy_sha256': case['config']['trust_policy_sha256']}
    signing._SIGNED[cap] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic()+100, 'binding': binding}
    monkeypatch.setattr(operation, 'qualify_stage_and_assess', lambda **kw: ({'decision': 'pass', 'security_operation_sha256': 'a'*64}, case['args']['assessed']))
    monkeypatch.setattr(operation, 'sign_assessed_mission', lambda **kw: ({'decision': 'pass', 'signing_sha256': 'b'*64}, cap))
    original = operation._write
    def expired(directory, name, value):
        result = original(directory, name, value)
        if name == 'mission-signing-operation.json':
            signing._SIGNED[cap]['expires_monotonic'] = 0
        return result
    monkeypatch.setattr(operation, '_write', expired)
    arguments = inputs(case, tmp_path)
    with pytest.raises(ValueError, match='expired'):
        operation.qualify_assess_and_sign(**arguments)
    assert not (arguments['output'] / 'mission-signing-operation.json').exists()


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('decision,code', [('pass', 0), ('fail', 1), ('unknown', 2)])
def test_signing_cli_four_pins_and_explicit_local_scope(case, tmp_path, monkeypatch, capsys, decision, code):
    script = load_script('sign_mission')
    monkeypatch.setattr(script, 'qualify_assess_and_sign', lambda **kw: ({'decision': decision, 'factory_decision': 'unknown'}, None))
    arguments = inputs(case, tmp_path)
    assert script.main([part for key, value in arguments.items() for part in ('--'+key.replace('_', '-'), str(value))]) == code
    assert decision in capsys.readouterr().out
    help_text = script.parser().format_help()
    assert '--signing-config-sha256' in help_text and '--assessment-report' not in help_text
    assert 'Signatures remain local' in help_text


def test_key_setup_cli_returns_only_retrieval_metadata(lab, tmp_path, monkeypatch, capsys):
    script = load_script('generate_lab_signing_key')
    monkeypatch.setattr(script, 'generate_lab_key', lambda **kw: {'kind': 'owned-lab-application-key', 'private_key': 'application.key', 'trust_policy': 'not-selected'})
    assert script.main(['--cosign', str(lab[1]), '--output', str(tmp_path/'credentials')]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['trust_policy'] == 'not-selected'


def test_packaged_signing_wrappers_import_in_clean_installation(tmp_path):
    from scripts import build_plugin
    archive = tmp_path / 'signing.zip'
    build_plugin.build(archive)
    extracted = tmp_path / 'clean installed plugin with spaces'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    plugin = extracted / 'devsecops'
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    for name in ('sign_mission.py', 'generate_lab_signing_key.py'):
        result = subprocess.run([sys.executable, '-B', str(plugin / 'scripts' / name), '--help'],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
    assert (plugin / 'docs/interfaces/mission-signing.md').is_file()
