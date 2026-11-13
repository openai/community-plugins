"""Controlled in-process wiring only: no builds, registry calls or real issuance."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

from devsecops.factory import deployment_proposal as proposal
from devsecops.factory import mission_proposal_operation as operation
from devsecops.factory import mission_publication as publication
from devsecops.runtime.context import verify_seal

ROOT = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def case(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    selected = {'schema_version': 1, 'kind': 'mission-deployment-proposal-configuration',
        'deployment_repository': str(root / 'deployment'), 'deployment_commit': 'd' * 40,
        'chart_path': 'charts/mission', 'values_path': 'environments/lab.yaml',
        'image_keys': deepcopy(proposal._KEYS), 'release': 'mission', 'namespace': 'mission',
        'kubernetes_version': '1.34.0', 'helm_path': str(root / 'tools/helm'), 'helm_sha256': '1' * 64,
        'kubeconform_path': str(root / 'tools/kubeconform'), 'kubeconform_sha256': '2' * 64,
        'schemas_path': str(root / 'schemas')}
    publisher = {'schema_version': 1, 'kind': 'mission-evidence-publication-configuration',
        'origin': 'https://registry.example.test', 'repository': 'mission/app',
        'credentials': {'token_env': 'TEST_PUBLICATION_TOKEN'},
        'ca_bundle': None, 'trust_policy_path': str(root / 'trust.json'), 'trust_policy_sha256': '3' * 64,
        'public_key_path': str(root / 'public.pem'), 'public_key_sha256': '4' * 64,
        'cosign_path': str(root / 'tools/cosign')}
    args = {'output': root / 'operation', 'runtime_root': root / 'runtime',
        'expected_source_repository': root / 'source', 'expected_source_revision': 'a' * 40}
    for name in ('build', 'staging', 'security', 'signing', 'publication', 'proposal'):
        value = selected if name == 'proposal' else publisher if name == 'publication' else {'controlled': name}
        path = root / (name + '.json')
        path.write_text(json.dumps(value))
        args[name + '_config'], args[name + '_config_sha256'] = path, sha(path.read_bytes())
    binding = {'run_scope': 'controlled-wiring', 'source_commit': 'a' * 40, 'architecture': 'amd64',
        'origin': publisher['origin'], 'repository': publisher['repository'], 'manifest_digest': 'sha256:' + '5' * 64,
        'policy_sha256': '6' * 64, 'trust_policy_sha256': publisher['trust_policy_sha256'],
        'publication_config_sha256': args['publication_config_sha256'],
        'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()}
    # These private records are test doubles; no production issuer is invoked.
    published = object.__new__(publication.PublishedMission)
    publication._PUBLISHED[published] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic() + 600,
        'binding': deepcopy(binding)}
    local = object.__new__(proposal.DeploymentProposal)
    proposal_binding = {**deepcopy(binding), 'deployment_repository': selected['deployment_repository'],
        'deployment_commit': selected['deployment_commit'], 'configuration_sha256': args['proposal_config_sha256']}
    proposal._PROPOSALS[local] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic() + 600,
        'binding': proposal_binding}
    calls = {'publication': [], 'proposal': []}
    def upstream(**kwargs):
        calls['publication'].append(kwargs)
        kwargs['output'].mkdir()
        (kwargs['output'] / 'retained-mutation.txt').write_text('controlled upstream intent, preserve on failure')
        return {'decision': 'pass', 'publication_operation_sha256': '7' * 64}, published
    def downstream(**kwargs):
        calls['proposal'].append(kwargs)
        return {'decision': 'pass', 'proposal_sha256': '8' * 64}, local
    monkeypatch.setattr(operation, 'qualify_sign_and_publish', upstream)
    monkeypatch.setattr(operation, 'propose_deployment_change', downstream)
    return {'args': args, 'selected': selected, 'publisher': publisher, 'published': published,
        'proposal': local, 'binding': binding, 'calls': calls, 'upstream': upstream, 'downstream': downstream}


def rewrite(case, name, change):
    path = case['args'][name + '_config']
    value = json.loads(path.read_bytes())
    change(value)
    path.write_text(json.dumps(value))
    case['args'][name + '_config_sha256'] = sha(path.read_bytes())


def test_direct_handoff_preserves_capability_scope_and_local_only_status(case):
    result, capability = operation.qualify_publish_and_propose(**case['args'])
    assert capability is case['proposal']
    assert result['decision'] == 'pass' and result['factory_decision'] == 'unknown'
    assert result['checks'] == {'publication': 'pass', 'proposal': 'pass'}
    assert set(result['downstream'].values()) == {'not-performed'}
    assert result['ci_execution'] == 'not-established'
    upstream, downstream = case['calls']['publication'][0], case['calls']['proposal'][0]
    assert downstream['published'] is case['published']
    assert downstream['publication_config_sha256'] == case['args']['publication_config_sha256']
    assert downstream['expected_config_sha256'] == case['args']['proposal_config_sha256']
    assert upstream['expected_source_revision'] == case['args']['expected_source_revision']
    assert upstream['expected_source_repository'] == case['args']['expected_source_repository']
    assert upstream['publication_config'] != case['args']['publication_config']
    assert downstream['config'] != case['args']['proposal_config']
    assert upstream['timeout'] + 2 <= 3500 and 2 <= downstream['timeout'] <= 600
    retained = json.loads((case['args']['output'] / 'mission-proposal-operation.json').read_bytes())
    assert retained == result
    verify_seal(result, 'proposal_operation_sha256')


def test_explicit_1365_selection_passes_prepublication_gate_without_changing_default(case):
    rewrite(case, 'proposal', lambda selected: selected.update(kubernetes_version='1.36.5'))
    record = proposal._PROPOSALS[case['proposal']]
    record['binding']['configuration_sha256'] = case['args']['proposal_config_sha256']
    result, capability = operation.qualify_publish_and_propose(**case['args'])
    assert capability is case['proposal'] and result['checks'] == {'publication': 'pass', 'proposal': 'pass'}
    assert len(case['calls']['publication']) == len(case['calls']['proposal']) == 1


@pytest.mark.parametrize('name', ['build', 'staging', 'security', 'signing', 'publication', 'proposal'])
def test_every_config_pin_is_checked_before_upstream_mutation(case, name):
    case['args'][name + '_config_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        operation.qualify_publish_and_propose(**case['args'])
    assert not case['calls']['publication'] and not case['args']['output'].exists()


@pytest.mark.parametrize('change', [lambda c: c.update(image_keys={}),
    lambda c: c.update(kubernetes_version='1.36.0'), lambda c: c.update(deployment_commit='main')])
def test_invalid_sixth_config_is_rejected_before_upstream_mutation(case, change):
    rewrite(case, 'proposal', change)
    with pytest.raises(ValueError):
        operation.qualify_publish_and_propose(**case['args'])
    assert not case['calls']['publication'] and not case['args']['output'].exists()


@pytest.mark.parametrize('decision,with_cap,expected', [('unknown', False, 'unknown'), ('fail', False, 'fail'),
    ('pass', False, 'unknown'), ('unknown', True, 'unknown'), ('fail', True, 'fail')])
def test_incomplete_publication_never_invokes_proposal(case, monkeypatch, decision, with_cap, expected):
    monkeypatch.setattr(operation, 'qualify_sign_and_publish', lambda **kw: (
        {'decision': decision, 'publication_operation_sha256': '7' * 64}, case['published'] if with_cap else None))
    result, cap = operation.qualify_publish_and_propose(**case['args'])
    assert result['decision'] == expected and cap is None
    assert not case['calls']['proposal'] and result['checks']['proposal'] == 'not-started'


@pytest.mark.parametrize('mutation', ['forged', 'expired', 'foreign', 'source', 'publication-pin', 'destination'])
def test_publication_authority_is_current_and_matches_operator_selection(case, monkeypatch, mutation):
    published = case['published']
    if mutation == 'forged':
        class Forged:
            def describe(self):
                pytest.fail('Untrusted describe must not be invoked')
        published = Forged()
    elif mutation in ('expired', 'foreign'):
        publication._PUBLISHED[published]['expires_monotonic' if mutation == 'expired' else 'pid'] = 0
    else:
        binding = publication._PUBLISHED[published]['binding']
        binding[{'source': 'source_commit', 'publication-pin': 'publication_config_sha256', 'destination': 'repository'}[mutation]] = 'wrong'
    monkeypatch.setattr(operation, 'qualify_sign_and_publish', lambda **kw: (
        {'decision': 'pass', 'publication_operation_sha256': '7' * 64}, published))
    result, cap = operation.qualify_publish_and_propose(**case['args'])
    assert result['decision'] == 'unknown' and cap is None and not case['calls']['proposal']


def test_original_config_change_does_not_replace_frozen_selection(case, monkeypatch):
    def upstream(**kwargs):
        case['args']['proposal_config'].write_text('untrusted changed source')
        return case['upstream'](**kwargs)
    monkeypatch.setattr(operation, 'qualify_sign_and_publish', upstream)
    result, cap = operation.qualify_publish_and_propose(**case['args'])
    assert result['decision'] == 'pass' and cap is case['proposal']


def test_frozen_config_change_stops_proposal_after_one_publication(case, monkeypatch):
    def upstream(**kwargs):
        frozen = kwargs['publication_config'].parent / 'proposal.json'
        frozen.chmod(0o600)
        frozen.write_text('changed private input')
        return case['upstream'](**kwargs)
    monkeypatch.setattr(operation, 'qualify_sign_and_publish', upstream)
    result, cap = operation.qualify_publish_and_propose(**case['args'])
    assert result['decision'] == 'unknown' and cap is None
    assert len(case['calls']['publication']) == 1 and not case['calls']['proposal']
    assert (case['args']['output'] / 'publication/retained-mutation.txt').exists()


@pytest.mark.parametrize('field', ['deployment_commit', 'configuration_sha256', 'manifest_digest'])
def test_proposal_is_bound_to_independently_selected_target_and_image(case, field):
    proposal._PROPOSALS[case['proposal']]['binding'][field] = 'wrong'
    result, cap = operation.qualify_publish_and_propose(**case['args'])
    assert result['decision'] == 'unknown' and cap is None and 'binding' not in result


@pytest.mark.parametrize('decision,kind,expected', [('pass', 'missing', 'unknown'),
    ('pass', 'forged', 'unknown'), ('unknown', 'typed', 'unknown'), ('fail', 'typed', 'fail')])
def test_incomplete_or_forged_proposal_cannot_pass(case, monkeypatch, decision, kind, expected):
    returned = None if kind == 'missing' else object() if kind == 'forged' else case['proposal']
    monkeypatch.setattr(operation, 'propose_deployment_change', lambda **kw: (
        {'decision': decision, 'proposal_sha256': '8' * 64}, returned))
    result, cap = operation.qualify_publish_and_propose(**case['args'])
    assert result['decision'] == expected and cap is None and 'binding' not in result
    assert len(case['calls']['publication']) == 1


def test_local_proposal_exception_preserves_publication_and_never_retries(case, monkeypatch):
    def fail(**kwargs):
        case['calls']['proposal'].append(kwargs)
        raise ValueError('SECRET-EXCEPTION-CANARY')
    monkeypatch.setattr(operation, 'propose_deployment_change', fail)
    result, cap = operation.qualify_publish_and_propose(**case['args'])
    assert result['decision'] == 'unknown' and cap is None
    assert len(case['calls']['publication']) == len(case['calls']['proposal']) == 1
    assert (case['args']['output'] / 'publication/retained-mutation.txt').exists()
    assert 'SECRET-EXCEPTION-CANARY' not in json.dumps(result)


def test_publication_cannot_reset_overall_deadline(case, monkeypatch):
    original = time.monotonic
    def upstream(**kwargs):
        value = case['upstream'](**kwargs)
        monkeypatch.setattr(operation.time, 'monotonic', lambda: original() + 4000)
        return value
    monkeypatch.setattr(operation, 'qualify_sign_and_publish', upstream)
    result, cap = operation.qualify_publish_and_propose(**case['args'])
    assert result['decision'] == 'unknown' and cap is None and not case['calls']['proposal']


@pytest.mark.parametrize('mutation', ['publication-expiry', 'proposal-expiry', 'route', 'config'])
def test_terminal_receipt_is_withdrawn_if_final_admission_changes(case, monkeypatch, mutation):
    original = operation._write
    root = case['args']['output']
    moved = root.parent / 'moved-operation'
    def write(directory, name, value):
        ref = original(directory, name, value)
        if mutation == 'publication-expiry':
            publication._PUBLISHED[case['published']]['expires_monotonic'] = 0
        elif mutation == 'proposal-expiry':
            proposal._PROPOSALS[case['proposal']]['expires_monotonic'] = 0
        elif mutation == 'config':
            frozen = case['calls']['proposal'][0]['config']
            frozen.chmod(0o600)
            frozen.write_text('changed after receipt')
        else:
            root.rename(moved)
            root.mkdir()
        return ref
    monkeypatch.setattr(operation, '_write', write)
    with pytest.raises(ValueError):
        operation.qualify_publish_and_propose(**case['args'])
    assert not (root / 'mission-proposal-operation.json').exists()
    assert not (moved / 'mission-proposal-operation.json').exists()


def load_script():
    spec = importlib.util.spec_from_file_location('publish_and_propose_mission', ROOT / 'scripts/publish_and_propose_mission.py')
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


@pytest.mark.parametrize('decision,code', [('pass', 0), ('fail', 1), ('unknown', 2)])
def test_cli_six_pins_and_exit_semantics(case, monkeypatch, capsys, decision, code):
    script = load_script()
    monkeypatch.setattr(script, 'qualify_publish_and_propose', lambda **kw: ({'decision': decision}, None))
    args = [part for key, value in case['args'].items() for part in ('--' + key.replace('_', '-'), str(value))]
    assert script.main(args) == code and decision in capsys.readouterr().out
    help_text = script.parser().format_help()
    assert '--proposal-config-sha256' in help_text and 'Writes image/evidence' in help_text
    assert '--publication-report' not in help_text and '--proposal-report' not in help_text


def test_cli_does_not_expose_exception_content(case, monkeypatch, capsys):
    script = load_script()
    def fail(**kwargs):
        raise ValueError('SECRET-EXCEPTION-CANARY')
    monkeypatch.setattr(script, 'qualify_publish_and_propose', fail)
    args = [part for key, value in case['args'].items() for part in ('--' + key.replace('_', '-'), str(value))]
    assert script.main(args) == 2
    assert 'SECRET-EXCEPTION-CANARY' not in capsys.readouterr().err


def test_clean_extracted_command_imports_its_own_package(tmp_path):
    from scripts import build_plugin
    archive = tmp_path / 'candidate.zip'
    build_plugin.build(archive)
    extracted = tmp_path / 'clean package with spaces'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    script = extracted / 'devsecops/scripts/publish_and_propose_mission.py'
    result = subprocess.run([sys.executable, '-B', str(script), '--help'], cwd=tmp_path,
        env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and '--proposal-config-sha256' in result.stdout
