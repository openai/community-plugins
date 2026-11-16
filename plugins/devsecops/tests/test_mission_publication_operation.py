"""Five pinned inputs compose direct signing with real controlled TLS publication."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

from devsecops.factory import mission_publication as publication
from devsecops.factory import mission_publication_operation as operation
from devsecops.factory import mission_signing as signing
from test_mission_publication import case, registry, scanned, lab, ca, encode, sha

__all__ = ['case', 'registry', 'scanned', 'lab', 'ca']
ROOT = Path(__file__).resolve().parents[1]


def inputs(case, tmp_path):
    config = case['config']
    arguments = {'publication_config': case['args']['config'],
        'publication_config_sha256': case['args']['expected_config_sha256'],
        'output': tmp_path / 'operation', 'runtime_root': tmp_path / 'runtime',
        'expected_source_repository': tmp_path / 'repository',
        'expected_source_revision': case['binding']['source_commit']}
    selected = {'build': {'controlled': 'build'}, 'security': {'controlled': 'security'},
        'staging': {**{name: config[name] for name in ('schema_version', 'origin', 'repository', 'credentials', 'ca_bundle')},
                    'kind': 'mission-staging-configuration'},
        'signing': {'schema_version': 1, 'kind': 'mission-local-signing-configuration',
                    'private_key_path': str(Path(config['public_key_path']).parent / 'lab.key'),
                    'password_env': 'MISSION_SIGNING_PASSWORD',
                    **{name: config[name] for name in ('trust_policy_path', 'trust_policy_sha256', 'public_key_path', 'cosign_path')}}}
    for name, value in selected.items():
        path = tmp_path / (name + '-operation.json')
        path.write_bytes(encode(value))
        arguments[name + '_config'], arguments[name + '_config_sha256'] = path, sha(path.read_bytes())
    return arguments


def update(arguments, name, mutate):
    path = arguments[name + '_config']
    value = json.loads(path.read_bytes())
    mutate(value)
    path.write_bytes(encode(value))
    arguments[name + '_config_sha256'] = sha(path.read_bytes())


def signed_result(case, monkeypatch):
    calls = []
    def upstream(**kwargs):
        calls.append(kwargs)
        return {'decision': 'pass', 'signing_operation_sha256': 'a' * 64}, case['args']['signed']
    monkeypatch.setattr(operation, 'qualify_assess_and_sign', upstream)
    return calls


def test_direct_signed_object_composes_real_tls_publication_and_remains_controlled(case, tmp_path, monkeypatch):
    calls = signed_result(case, monkeypatch)
    arguments = inputs(case, tmp_path)
    result, capability = operation.qualify_sign_and_publish(**arguments)
    assert calls[0]['expected_source_repository'] == arguments['expected_source_repository']
    assert calls[0]['expected_source_revision'] == arguments['expected_source_revision']
    assert capability is None and result['decision'] == result['factory_decision'] == 'unknown'
    assert result['checks'] == {'local_signing': 'pass', 'publication': 'unknown'}
    assert result['downstream'] == {'ci_execution': 'not-established', 'promote': 'not-started'}
    assert result['gaps'] == ['mission-evidence-publication-incomplete']
    report = json.loads((arguments['output'] / 'publication/mission-publication.json').read_bytes())
    assert report['checks']['discovery'] == report['checks']['signature_verification'] == 'pass'
    assert report['gaps'] == ['controlled-evidence-cannot-issue-publication-authority']
    assert any(item['method'] == 'PUT' for item in case['registry']['requests'])


@pytest.mark.parametrize('name', ['build', 'staging', 'security', 'signing', 'publication'])
def test_each_outer_pin_is_checked_before_any_upstream_write(case, tmp_path, monkeypatch, name):
    arguments = inputs(case, tmp_path)
    arguments[name + '_config_sha256'] = '0' * 64
    monkeypatch.setattr(operation, 'qualify_assess_and_sign', lambda **kw: pytest.fail('changed pin reached upstream'))
    with pytest.raises(ValueError):
        operation.qualify_sign_and_publish(**arguments)
    assert not arguments['output'].exists() and not case['registry']['requests']


@pytest.mark.parametrize('mutation', ['staging-scope', 'publication-scope', 'trust-pin', 'public-pin',
                                     'signing-trust', 'signing-key', 'publication-trust', 'publication-key', 'expired-trust'])
def test_independent_trust_key_and_destination_preflight_before_upstream(case, tmp_path, monkeypatch, mutation):
    arguments = inputs(case, tmp_path)
    if mutation in ('staging-scope', 'publication-scope'):
        update(arguments, mutation.split('-')[0], lambda value: value.update(repository='other/mission'))
    elif mutation in ('trust-pin', 'public-pin'):
        update(arguments, 'publication', lambda value: value.update({
            'trust_policy_sha256' if mutation == 'trust-pin' else 'public_key_sha256': '0' * 64}))
    elif mutation == 'expired-trust':
        policy = deepcopy(case['trust'])
        policy['expires_at'] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        path = tmp_path / 'expired-trust.json'
        path.write_bytes(encode(policy))
        for name in ('signing', 'publication'):
            update(arguments, name, lambda value: value.update(trust_policy_path=str(path), trust_policy_sha256=sha(path.read_bytes())))
    else:
        name, part = mutation.split('-')
        path = tmp_path / (mutation + '.invalid')
        path.write_bytes(b'changed independently selected bytes')
        update(arguments, name, lambda value: value.update({
            'trust_policy_path' if part == 'trust' else 'public_key_path': str(path)}))
    monkeypatch.setattr(operation, 'qualify_assess_and_sign', lambda **kw: pytest.fail('bad trust reached upstream'))
    with pytest.raises(ValueError):
        operation.qualify_sign_and_publish(**arguments)
    assert not arguments['output'].exists() and not case['registry']['requests']


@pytest.mark.parametrize('decision', ['unknown', 'fail'])
def test_missing_or_failed_signing_never_publishes(case, tmp_path, monkeypatch, decision):
    monkeypatch.setattr(operation, 'qualify_assess_and_sign', lambda **kw: (
        {'decision': decision, 'signing_operation_sha256': 'a' * 64}, None))
    monkeypatch.setattr(operation, 'publish_signed_mission', lambda **kw: pytest.fail('no signed capability'))
    result, capability = operation.qualify_sign_and_publish(**inputs(case, tmp_path))
    assert capability is None and result['decision'] == decision
    assert result['checks']['publication'] == 'not-started'
    assert not case['registry']['requests']


@pytest.mark.parametrize('mutation', ['forged-description', 'expired', 'foreign-process'])
def test_signed_admission_is_typed_current_and_local_before_publisher(case, tmp_path, monkeypatch, mutation):
    selected = case['args']['signed']
    if mutation == 'forged-description':
        class Forged:
            def describe(self):
                return deepcopy(case['binding'])
        selected = Forged()
    else:
        signing._SIGNED[selected]['expires_monotonic' if mutation == 'expired' else 'pid'] = 0
    monkeypatch.setattr(operation, 'qualify_assess_and_sign', lambda **kw: (
        {'decision': 'pass', 'signing_operation_sha256': 'a' * 64}, selected))
    monkeypatch.setattr(operation, 'publish_signed_mission', lambda **kw: pytest.fail('invalid capability reached publisher'))
    with pytest.raises(ValueError, match='authority'):
        operation.qualify_sign_and_publish(**inputs(case, tmp_path))
    assert not case['registry']['requests']


def test_real_ambiguous_put_is_not_retried_or_promoted_to_pass(case, tmp_path, monkeypatch):
    signed_result(case, monkeypatch)
    case['registry']['fault'] = 'put-disconnect'
    arguments = inputs(case, tmp_path)
    result, capability = operation.qualify_sign_and_publish(**arguments)
    assert capability is None and result['decision'] == 'unknown'
    assert result['checks']['publication'] == 'unknown'
    report = json.loads((arguments['output'] / 'publication/mission-publication.json').read_bytes())
    assert report['mutations'] and report['mutations'][-1]['state'] == 'unknown'
    assert len([item for item in case['registry']['requests'] if item['method'] == 'PUT']) == 1


@pytest.mark.parametrize('mutation', ['expiry', 'wall-expiry', 'foreign-process', 'config-pin', 'deadline', 'route'])
def test_terminal_receipt_withdraws_on_lost_publication_admission(case, tmp_path, monkeypatch, mutation):
    signed_result(case, monkeypatch)
    arguments = inputs(case, tmp_path)
    # Only tests may install controlled guard records; this is not a production issuer.
    cap = object.__new__(publication.PublishedMission)
    binding = {**deepcopy(case['binding']), 'publication_config_sha256': arguments['publication_config_sha256']}
    publication._PUBLISHED[cap] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic() + 10000, 'binding': binding}
    monkeypatch.setattr(operation, 'publish_signed_mission', lambda **kw: (
        {'decision': 'pass', 'publication_sha256': 'b' * 64}, cap))
    original_write, original_time = operation._write, operation.time.monotonic
    root, moved = arguments['output'], tmp_path / 'moved-operation'
    def changed(directory, name, value):
        result = original_write(directory, name, value)
        if name == 'mission-publication-operation.json':
            record = publication._PUBLISHED[cap]
            if mutation == 'expiry':
                record['expires_monotonic'] = 0
            elif mutation == 'wall-expiry':
                record['binding']['expires_at'] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
            elif mutation == 'foreign-process':
                record['pid'] = 0
            elif mutation == 'config-pin':
                record['binding']['publication_config_sha256'] = 'f' * 64
            elif mutation == 'deadline':
                monkeypatch.setattr(operation.time, 'monotonic', lambda: original_time() + 4000)
            else:
                root.rename(moved)
                root.mkdir(mode=0o700)
        return result
    monkeypatch.setattr(operation, '_write', changed)
    with pytest.raises(ValueError):
        operation.qualify_sign_and_publish(**arguments)
    assert not (root / 'mission-publication-operation.json').exists()
    assert not (moved / 'mission-publication-operation.json').exists()


def load_script():
    spec = importlib.util.spec_from_file_location('publish_mission', ROOT / 'scripts/publish_mission.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('decision,code', [('pass', 0), ('fail', 1), ('unknown', 2)])
def test_cli_has_five_pins_and_no_saved_report_authority(case, tmp_path, monkeypatch, capsys, decision, code):
    script = load_script()
    monkeypatch.setattr(script, 'qualify_sign_and_publish', lambda **kw: (
        {'decision': decision, 'factory_decision': 'unknown'}, None))
    arguments = inputs(case, tmp_path)
    assert script.main([part for key, value in arguments.items() for part in ('--' + key.replace('_', '-'), str(value))]) == code
    assert decision in capsys.readouterr().out
    help_text = script.parser().format_help()
    assert '--publication-config-sha256' in help_text and '--signing-config-sha256' in help_text
    assert '--signed-report' not in help_text and '--publication-report' not in help_text
    assert 'Writes image and signed evidence' in help_text


def test_cli_failure_does_not_echo_sensitive_exception(case, tmp_path, monkeypatch, capsys):
    script = load_script()
    def fail(**kwargs):
        raise ValueError('SECRET-EXCEPTION-CANARY')
    monkeypatch.setattr(script, 'qualify_sign_and_publish', fail)
    arguments = inputs(case, tmp_path)
    assert script.main([part for key, value in arguments.items() for part in ('--' + key.replace('_', '-'), str(value))]) == 2
    captured = capsys.readouterr()
    assert 'SECRET-EXCEPTION-CANARY' not in captured.out + captured.err
    assert 'retain mutation intents' in captured.err


def test_packaged_publication_wrapper_imports_from_unrelated_directory(tmp_path):
    from scripts import build_plugin
    archive = tmp_path / 'publication.zip'
    build_plugin.build(archive)
    extracted = tmp_path / 'clean installed plugin with spaces'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    script = extracted / 'devsecops/scripts/publish_mission.py'
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    result = subprocess.run([sys.executable, '-B', str(script), '--help'], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert '--publication-config-sha256' in result.stdout
