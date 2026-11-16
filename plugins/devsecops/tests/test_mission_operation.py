"""Strict configuration and a direct same-process base-to-build handoff."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest

from devsecops.factory import mission_operation as operation
from devsecops.factory.build import ROOT
from devsecops.factory.state import IDENTITY_KEYS


def configuration(tmp_path):
    document = {'schema_version': 1, 'kind': 'mission-build-configuration',
        'source': {'repository': str(tmp_path / 'source'), 'revision': '1' * 40, 'path': 'mission'},
        'publisher': {'policy': str(tmp_path / 'publisher.json'), 'policy_sha256': '2' * 64,
            'public_key': str(tmp_path / 'publisher.pem'), 'package': None},
        'image': {'policy': str(tmp_path / 'image.json'), 'policy_sha256': '3' * 64},
        'tools': {name: str(tmp_path / name) for name in ('cosign', 'build_tools', 'wheel')},
        'owner_uid': 1000, 'run_scope': {name: '4' * 64 for name in IDENTITY_KEYS},
        'registry_auth': {'token_env': 'REGISTRY1_READ_TOKEN'}}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(document))
    return document, dict(config=path, expected_config_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), output=tmp_path / 'operation')


@pytest.mark.parametrize('damage', ['unknown', 'schema-bool', 'uid-bool', 'source-extra', 'publisher-extra',
    'relative-path', 'mutable-commit', 'run-scope', 'raw-token', 'both-auth', 'policy-hash'])
def test_bad_configuration_never_acquires_or_mutates(tmp_path, monkeypatch, damage):
    document, args = configuration(tmp_path)
    if damage == 'unknown':
        document['callback'] = 'untrusted.module'
    elif damage == 'schema-bool':
        document['schema_version'] = True
    elif damage == 'uid-bool':
        document['owner_uid'] = True
    elif damage == 'source-extra':
        document['source']['command'] = 'any'
    elif damage == 'publisher-extra':
        document['publisher']['qualified'] = True
    elif damage == 'relative-path':
        document['tools']['cosign'] = 'local'
    elif damage == 'mutable-commit':
        document['source']['revision'] = 'main'
    elif damage == 'run-scope':
        document['run_scope'] = {'partial': '0' * 64}
    elif damage == 'raw-token':
        document['registry_auth'] = {'token': 'NEVER-PRINT-TOKEN'}
    elif damage == 'both-auth':
        document['registry_auth']['basic_username_env'] = 'REGISTRY_USERNAME'
    else:
        document['image']['policy_sha256'] = 'wrong'
    args['config'].write_text(json.dumps(document))
    args['expected_config_sha256'] = hashlib.sha256(args['config'].read_bytes()).hexdigest()
    monkeypatch.setattr(operation, 'qualify_mission_base', lambda **_: pytest.fail('Bad configuration reached acquisition'))
    with tempfile.TemporaryDirectory(prefix='dso-o-', dir='/tmp') as temporary:
        with pytest.raises((TypeError, ValueError)):
            operation.qualify_and_build(**args, runtime_root=Path(temporary).resolve() / 'runtime')
    assert not args['output'].exists()


def test_changed_configuration_identity_fails_before_output(tmp_path, monkeypatch):
    _, args = configuration(tmp_path)
    args['config'].write_text('{}')
    with pytest.raises(ValueError, match='differs'):
        operation.qualify_and_build(**args, runtime_root=Path('/tmp/not-created'))
    assert not args['output'].exists()


def test_unknown_base_prevents_any_build(tmp_path, monkeypatch):
    _, args = configuration(tmp_path)
    monkeypatch.setattr(operation, 'prerequisites', lambda _: {'controlled': True})
    monkeypatch.setattr(operation, 'qualify_mission_base', lambda **_: ({'decision': 'unknown', 'qualification_sha256': '5' * 64}, None))
    monkeypatch.setattr(operation, 'build_mission', lambda **_: pytest.fail('Incomplete base reached build'))
    with tempfile.TemporaryDirectory(prefix='dso-o-', dir='/tmp') as temporary:
        report, capability = operation.qualify_and_build(**args, runtime_root=Path(temporary).resolve() / 'runtime')
    assert report['decision'] == 'unknown' and capability is None
    assert report['checks']['build'] == 'unknown' and report['downstream']['push'] == 'not-started'


def test_concrete_base_object_reaches_build_in_same_process(tmp_path, monkeypatch):
    document, args = configuration(tmp_path)
    monkeypatch.setattr(operation, 'prerequisites', lambda _: {'controlled': True})
    opaque = object()
    subject = {'origin': 'https://registry1.dso.mil', 'repository': 'ironbank/example/python',
               'digest': 'sha256:' + '0' * 64, 'architecture': 'amd64'}
    def qualified(**kwargs):
        assert kwargs['run_scope'] == document['run_scope']
        assert kwargs['token_env'] == 'REGISTRY1_READ_TOKEN'
        return {'decision': 'pass', 'qualification_sha256': '5' * 64, 'subject': subject}, opaque
    def built(**kwargs):
        assert kwargs['verified_base'] is opaque
        assert kwargs['revision'] == document['source']['revision']
        assert kwargs['base_repository'] == 'registry1.dso.mil/ironbank/example/python'
        return {'decision': 'unknown', 'execution_mode': 'controlled-test', 'build_sha256': '6' * 64}, None
    monkeypatch.setattr(operation, 'qualify_mission_base', qualified)
    monkeypatch.setattr(operation, 'build_mission', built)
    with tempfile.TemporaryDirectory(prefix='dso-o-', dir='/tmp') as temporary:
        report, capability = operation.qualify_and_build(**args, runtime_root=Path(temporary).resolve() / 'runtime')
    assert report['decision'] == 'unknown' and capability is None
    assert report['execution_mode'] == 'controlled-test'


def test_cli_rejects_bad_operator_config_without_echoing_secrets(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location('mission_script', ROOT / 'scripts/build_mission.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, args = configuration(tmp_path)
    args['config'].write_text('{"private":"NEVER-PRINT-TOKEN"}')
    code = module.main(['build', '--config', str(args['config']), '--config-sha256', '0' * 64,
        '--output', str(args['output']), '--runtime-root', '/tmp/not-created'])
    captured = capsys.readouterr()
    assert code == 2 and not captured.out and 'NEVER-PRINT-TOKEN' not in captured.err


def test_unqualified_linux_owner_rejected_before_registry_acquisition(tmp_path, monkeypatch):
    _, args = configuration(tmp_path)
    def denied(_):
        raise ValueError('Controlled unsupported owner')
    monkeypatch.setattr(operation, 'prerequisites', denied)
    monkeypatch.setattr(operation, 'qualify_mission_base', lambda **_: pytest.fail('Unqualified owner reached registry'))
    with pytest.raises(ValueError, match='unsupported owner'):
        operation.qualify_and_build(**args, runtime_root=Path('/tmp/not-created'))
    assert not args['output'].exists()


@pytest.mark.parametrize('damage', ['repository', 'revision', 'missing-repository', 'missing-revision'])
def test_stale_ci_configuration_stops_before_acquisition(tmp_path, monkeypatch, damage):
    document, args = configuration(tmp_path)
    selected = {'expected_source_repository': Path(document['source']['repository']),
                'expected_source_revision': document['source']['revision']}
    if damage == 'repository':
        selected['expected_source_repository'] = tmp_path / 'different-checkout'
    elif damage == 'revision':
        selected['expected_source_revision'] = '9' * 40
    else:
        selected.pop('expected_source_' + damage.removeprefix('missing-'))
    monkeypatch.setattr(operation, 'prerequisites', lambda _: pytest.fail('Stale CI input reached platform admission'))
    with pytest.raises(ValueError, match='caller selected'):
        operation.qualify_and_build(**args, **selected, runtime_root=Path('/tmp/not-created'))
    assert not args['output'].exists()
