"""Actual local Git chain with controlled upstream issuance and GET transport.

No real build, publication, service credential, installed release or CI claim.
"""
from copy import deepcopy
import hashlib
import json
import sys
from types import ModuleType
from pathlib import Path
import time

import pytest

from devsecops.adapters.http import ReadResponse
from devsecops.factory import deployment_proposal, git_base_possession
from devsecops.factory import mission_git_operation as operation
from devsecops.factory.state import IDENTITY_KEYS
from devsecops.runtime.context import canonical_bytes, seal, verify_seal
from test_deployment_commit import case, git
from test_git_destination import advertisement, selection
from test_git_transport import inventory

__all__ = ['case']


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def revise(subject, name, update):
    path = subject['args']['configurations'][name][0]
    value = json.loads(path.read_bytes())
    update(value)
    path.write_bytes(canonical_bytes(value))
    subject['args']['configurations'][name] = (path, sha(path.read_bytes()))


@pytest.fixture
def subject(case, monkeypatch, tmp_path):
    root = tmp_path.resolve()
    source = root/'source'
    source.mkdir()
    run_scope = {name: sha(name.encode()) for name in IDENTITY_KEYS}
    bound = case['binding']
    bound['run_scope'] = run_scope
    bound['published_evidence']['signer']['authority'] = 'controlled-wiring-fixture-not-live'
    proposal = {'schema_version': 1, 'kind': 'mission-deployment-proposal-configuration',
        'deployment_repository': str(case['repo']), 'deployment_commit': bound['deployment_commit'],
        'chart_path': 'charts/mission', 'values_path': bound['values_path'],
        'image_keys': deepcopy(deployment_proposal._KEYS), 'release': 'mission', 'namespace': 'mission',
        'kubernetes_version': '1.34.0', 'helm_path': str(root/'tools/helm'), 'helm_sha256': '1'*64,
        'kubeconform_path': str(root/'tools/kubeconform'), 'kubeconform_sha256': '2'*64,
        'schemas_path': str(root/'schemas')}
    proposal_pin = sha(canonical_bytes(proposal))
    bound['configuration_sha256'] = proposal_pin
    commit = deepcopy(case['config'])
    commit['proposal_configuration_sha256'] = proposal_pin
    build = {'schema_version': 1, 'kind': 'mission-build-configuration',
        'source': {'repository': str(source), 'revision': bound['source_commit'], 'path': 'mission'},
        'publisher': {'policy': str(root/'publisher-policy'), 'policy_sha256': '3'*64,
                      'public_key': str(root/'publisher-key'), 'package': None},
        'image': {'policy': str(root/'image-policy'), 'policy_sha256': '4'*64},
        'tools': {name: str(root/name) for name in ('cosign', 'build_tools', 'wheel')},
        'owner_uid': 1000, 'run_scope': run_scope, 'registry_auth': {}}
    publication = {'schema_version': 1, 'kind': 'mission-evidence-publication-configuration',
        'origin': bound['origin'], 'repository': bound['repository'],
        'credentials': {'token_env': 'CONTROLLED_PUBLICATION'}, 'ca_bundle': None,
        'trust_policy_path': str(root/'trust'), 'trust_policy_sha256': '5'*64,
        'public_key_path': str(root/'public'), 'public_key_sha256': '6'*64,
        'cosign_path': str(root/'cosign')}
    destination = selection(bound['deployment_commit'])
    configs = {}
    for name in operation.NAMES:
        value = {'build': build, 'proposal': proposal, 'publication': publication,
                 'commit': commit, 'destination': destination}.get(name, {'controlled': name})
        path = root/(name+'.json')
        path.write_bytes(canonical_bytes(value))
        configs[name] = (path, sha(path.read_bytes()))
    args = dict(configurations=configs, expected_run_scope=deepcopy(run_scope),
        expected_source_repository=source, expected_source_revision=bound['source_commit'],
        plugin_id='devsecops@devsecops-local', plugin_version='0.2.0-alpha.7',
        output=root/'composition', runtime_root=root/'runtime')
    state = {'case': case, 'args': args, 'calls': [], 'gets': [], 'packages': [], 'hook': None}

    def installed(expected, plugin_id, plugin_version, deadline):
        state['packages'].append(expected)
        assert expected == run_scope['package_sha256']
        return expected

    def upstream(**kwargs):
        state['calls'].append(kwargs)
        kwargs['output'].mkdir()
        (kwargs['output']/'retained-intent.txt').write_text('Controlled upstream effect, preserve on failure')
        if state['hook']:
            state['hook'](kwargs)
        return seal({'schema_version': 1, 'kind': 'mission-publish-and-propose', 'decision': 'pass'},
                    'proposal_operation_sha256'), case['args']['proposal']

    monkeypatch.setattr(operation, '_installed', installed)
    monkeypatch.setattr(operation.mission_proposal_operation, 'qualify_publish_and_propose', upstream)
    monkeypatch.setenv('UNIT_API_READ', 'SYNTHETIC_API_CANARY')
    monkeypatch.setenv('UNIT_GIT_USER', 'synthetic-user')
    monkeypatch.setenv('UNIT_GIT_READ', 'SYNTHETIC_GIT_CANARY')

    class Client:
        def __init__(self, origin, **kwargs):
            self.origin, self.receipts = origin, []
            self._authorization = None
            self._bytes = 0
        def get_bytes(self, path, *, params=None, accept=()):
            state['gets'].append((path, params))
            self.receipts.append({'status': 200})
            prefix, base = '/api/v4/projects/37', bound['deployment_commit']
            if path == prefix:
                value = {'id': 37, 'http_url_to_repo': destination['origin']+'/group/deploy.git',
                         'path_with_namespace': 'group/deploy'}
            elif path == '/group/deploy.git/info/refs':
                return ReadResponse(200, advertisement(base), git_base_possession._MEDIA)
            elif path == prefix+'/repository/commits/'+base:
                parents = git(case['repo'], 'rev-list', '--parents', '-n', '1', base).decode().split()[1:]
                value = {'id': base, 'parent_ids': parents}
            elif path == prefix+'/repository/tree':
                folder = params['path']
                rows = git(case['repo'], 'ls-tree', '-z', base+(':'+folder if folder else '^{tree}')).split(b'\0')
                value = []
                for row in filter(None, rows):
                    details, name = row.split(b'\t', 1)
                    mode, kind, oid = details.decode().split()
                    name = name.decode()
                    value.append(dict(id=oid, name=name, type=kind, mode=mode,
                                      path=(folder+'/' if folder else '')+name))
                return ReadResponse(200, canonical_bytes(value), 'application/json', pagination={
                    'x-page': '1', 'x-per-page': '100', 'x-prev-page': '', 'x-next-page': ''})
            elif path.startswith(prefix+'/repository/blobs/') and path.endswith('/raw'):
                return ReadResponse(200, git(case['repo'], 'cat-file', 'blob', path.split('/')[-2]), 'application/octet-stream')
            else:
                pytest.fail('Unexpected GET: '+path)
            return ReadResponse(200, canonical_bytes(value), 'application/json')
    monkeypatch.setattr(git_base_possession, 'HTTPSReadClient', Client)
    return state


def test_actual_commit_transport_and_base_chain_keeps_direct_handles_and_durable_inputs(subject):
    before = inventory(subject['case']['repo'])
    result, transport, observed = operation.qualify_publish_and_prepare_git(**subject['args'])
    assert result['decision'] == 'pass', result
    assert set(result['checks'].values()) == {'pass'}
    assert transport is not None and observed is not None
    assert observed.describe()['new_commit'] == transport.describe()['new_commit'] == result['new_commit']
    assert result['source_commit'] != result['new_commit']
    assert result['git_write'] == 'not-performed' and not result['omission_authorized']
    assert result['factory_decision'] == 'unknown' and result['ci_execution'] == 'not-established'
    assert inventory(subject['case']['repo']) == before
    assert len(subject['calls']) == 1 and subject['gets']
    assert (subject['args']['output']/'inputs/destination.json').is_file()
    verify_seal(result, 'git_operation_sha256')
    assert json.loads((subject['args']['output']/'mission-git-operation.json').read_bytes()) == result
    assert 'CANARY' not in json.dumps(result)


@pytest.mark.parametrize('name', operation.NAMES)
def test_all_pins_rejected_before_upstream_effect(subject, name):
    path, _ = subject['args']['configurations'][name]
    subject['args']['configurations'][name] = (path, '0'*64)
    with pytest.raises(ValueError):
        operation.qualify_publish_and_prepare_git(**subject['args'])
    assert not subject['calls'] and not subject['gets']


@pytest.mark.parametrize('name,field,value', [
    ('destination', 'base_commit', '9'*40), ('commit', 'deployment_commit', '9'*40),
    ('commit', 'values_path', 'wrong.yaml'), ('commit', 'proposal_configuration_sha256', '8'*64),
    ('build', 'run_scope', {key: '7'*64 for key in IDENTITY_KEYS}),
])
def test_mismatched_destination_commit_or_run_stops_before_upstream(subject, name, field, value):
    revise(subject, name, lambda value_: value_.update({field: value}))
    with pytest.raises(ValueError):
        operation.qualify_publish_and_prepare_git(**subject['args'])
    assert not subject['calls'] and not subject['args']['output'].exists()


@pytest.mark.parametrize('field', ['expected_source_repository', 'expected_source_revision'])
def test_independently_selected_source_must_match_before_upstream(subject, field):
    subject['args'][field] = Path('/wrong/source') if field.endswith('repository') else '1'*40
    with pytest.raises(ValueError):
        operation.qualify_publish_and_prepare_git(**subject['args'])
    assert not subject['calls']


def test_wrong_package_rejected_before_any_effect(subject, monkeypatch):
    def reject(*args):
        raise ValueError('Wrong installed package')
    monkeypatch.setattr(operation, '_installed', reject)
    with pytest.raises(ValueError):
        operation.qualify_publish_and_prepare_git(**subject['args'])
    assert not subject['calls'] and not subject['args']['output'].exists()


@pytest.mark.parametrize('root', ['source', 'deployment', 'package', 'overlap'])
def test_output_and_runtime_roots_are_admitted_before_effect(subject, root):
    args = subject['args']
    if root == 'overlap':
        args['runtime_root'] = args['output']/'runtime'
    else:
        protected = {'source': args['expected_source_repository'], 'deployment': subject['case']['repo'],
                     'package': operation.ROOT}[root]
        args['output'] = protected/'forbidden-composition'
    with pytest.raises(ValueError):
        operation.qualify_publish_and_prepare_git(**args)
    assert not subject['calls']


@pytest.mark.parametrize('mutation', ['expired', 'wrong-run', 'wrong-package', 'wrong-base', 'saved-dictionary'])
def test_direct_proposal_authority_cannot_be_recreated_or_rebound(subject, monkeypatch, mutation):
    case = subject['case']
    def hook(kwargs):
        record = deployment_proposal._PROPOSALS[case['args']['proposal']]
        if mutation == 'expired':
            record['expires_monotonic'] = 0
        elif mutation == 'wrong-base':
            record['binding']['deployment_commit'] = '0'*40
        elif mutation == 'saved-dictionary':
            case['args']['proposal'] = deepcopy(record['binding'])
        else:
            record['binding']['run_scope'] = deepcopy(record['binding']['run_scope'])
            record['binding']['run_scope']['package_sha256' if mutation == 'wrong-package' else 'scenario_sha256'] = '0'*64
    subject['hook'] = hook
    result, transport, observed = operation.qualify_publish_and_prepare_git(**subject['args'])
    assert result['decision'] == 'unknown' and transport is observed is None
    assert len(subject['calls']) == 1 and not subject['gets']
    assert (subject['args']['output']/'publication-proposal/retained-intent.txt').exists()


def test_changed_frozen_selection_never_reaches_git_and_retains_upstream_evidence(subject):
    def hook(kwargs):
        selected = subject['args']['output']/'inputs/destination.json'
        selected.chmod(0o600)
        selected.write_bytes(b'changed')
    subject['hook'] = hook
    with pytest.raises(ValueError):
        operation.qualify_publish_and_prepare_git(**subject['args'])
    assert not subject['gets'] and len(subject['calls']) == 1
    assert not (subject['args']['output']/'mission-git-operation.json').exists()


def test_original_changed_input_cannot_replace_durable_selection(subject):
    def hook(kwargs):
        subject['args']['configurations']['destination'][0].write_bytes(b'changed original')
    subject['hook'] = hook
    result, _, observed = operation.qualify_publish_and_prepare_git(**subject['args'])
    assert result['decision'] == 'pass' and observed.describe()


def test_missing_remote_base_preserves_local_preparation_without_retry(subject, monkeypatch):
    original = git_base_possession.HTTPSReadClient.get_bytes
    def missing(self, path, **kwargs):
        response = original(self, path, **kwargs)
        if path.endswith('/info/refs'):
            return ReadResponse(200, advertisement('0'*40), git_base_possession._MEDIA)
        return response
    monkeypatch.setattr(git_base_possession.HTTPSReadClient, 'get_bytes', missing)
    result, transport, observed = operation.qualify_publish_and_prepare_git(**subject['args'])
    assert result['decision'] == 'unknown' and transport is observed is None
    assert result['checks']['commit'] == result['checks']['transport'] == 'pass'
    assert len(subject['calls']) == 1
    assert (subject['args']['output']/'transport/git-transport.json').exists()


def test_expiry_after_terminal_write_withdraws_only_summary(subject, monkeypatch):
    original = operation._write_terminal
    def write(directory, name, value):
        identity = original(directory, name, value)
        for record in list(git_base_possession._OBSERVATIONS.values()):
            record['expires_monotonic'] = 0
        return identity
    monkeypatch.setattr(operation, '_write_terminal', write)
    with pytest.raises(ValueError):
        operation.qualify_publish_and_prepare_git(**subject['args'])
    assert not (subject['args']['output']/'mission-git-operation.json').exists()
    assert (subject['args']['output']/'base/git-base-possession.json').exists()


def test_upstream_cannot_extend_total_deadline(subject, monkeypatch):
    original = time.monotonic
    subject['hook'] = lambda kwargs: monkeypatch.setattr(operation.time, 'monotonic', lambda: original()+5000)
    with pytest.raises((ValueError, TimeoutError)):
        operation.qualify_publish_and_prepare_git(**subject['args'])
    assert len(subject['calls']) == 1 and not subject['gets']


@pytest.mark.parametrize('mutation', ['none', 'member', 'loaded-path', 'namespace'])
def test_installed_gate_audits_loaded_engine_module_sources(monkeypatch, mutation):
    # Exercise the new gate itself. Existing native package tests cover the
    # actual cache-path/manifest verifier, stubbed here only to supply membership.
    members = {str(path.relative_to(operation.ROOT)): sha(path.read_bytes())
               for path in (operation.ROOT/'src').rglob('*.py')}
    expected = 'a'*64
    calls = []
    def package(root, digest, plugin_id, plugin_version, deadline):
        calls.append((root, digest, plugin_id, plugin_version))
        assert digest == expected
        return b'controlled-manifest', members
    monkeypatch.setattr(operation, '_package', package)
    if mutation == 'member':
        members['src/devsecops/factory/mission_git_operation.py'] = '0'*64
    elif mutation == 'loaded-path':
        monkeypatch.setattr(operation.mission_operation, '__file__', '/outside/mission_operation.py')
    elif mutation == 'namespace':
        monkeypatch.setitem(sys.modules, 'devsecops.unselected_module', ModuleType('devsecops.unselected_module'))
    if mutation == 'none':
        assert operation._installed(expected, 'devsecops@devsecops-local', '0.2.0-alpha.7', time.monotonic()+30) == sha(b'controlled-manifest')
    else:
        with pytest.raises(ValueError):
            operation._installed(expected, 'devsecops@devsecops-local', '0.2.0-alpha.7', time.monotonic()+30)
    assert calls == [(operation.ROOT, expected, 'devsecops@devsecops-local', '0.2.0-alpha.7')]


def test_interruption_after_upstream_intent_propagates_and_never_retries(subject):
    def interrupt(kwargs):
        raise KeyboardInterrupt
    subject['hook'] = interrupt
    with pytest.raises(KeyboardInterrupt):
        operation.qualify_publish_and_prepare_git(**subject['args'])
    assert len(subject['calls']) == 1 and not subject['gets']
    assert (subject['args']['output']/'publication-proposal/retained-intent.txt').exists()
    assert not (subject['args']['output']/'mission-git-operation.json').exists()


def test_installed_package_change_after_upstream_stops_before_local_git(subject, monkeypatch):
    def change(kwargs):
        def rejected(*args):
            raise ValueError('Installed package changed')
        monkeypatch.setattr(operation, '_installed', rejected)
    subject['hook'] = change
    with pytest.raises(ValueError):
        operation.qualify_publish_and_prepare_git(**subject['args'])
    assert len(subject['calls']) == 1 and not subject['gets']
    assert not (subject['args']['output']/'commit').exists()
    assert (subject['args']['output']/'publication-proposal/retained-intent.txt').exists()


@pytest.mark.parametrize('stage,module,function,kind,field', [
    ('publication_proposal', operation.mission_proposal_operation, 'qualify_publish_and_propose',
     'mission-publish-and-propose', 'proposal_operation_sha256'),
    ('commit', operation.deployment_commit, 'prepare_deployment_commit',
     'mission-local-deployment-commit', 'preparation_sha256'),
    ('transport', operation.git_transport, 'prepare_git_transport',
     'mission-local-git-transport', 'preparation_sha256'),
    ('base', operation.git_base_possession, 'verify_git_base_possession',
     'mission-git-base-possession', 'observation_sha256'),
])
@pytest.mark.parametrize('decision', ['fail', 'unknown', 'bad-seal', 'wrong-kind', 'missing'])
def test_child_decision_preserved_only_for_valid_direct_report(subject, monkeypatch, stage, module, function, kind, field, decision):
    called = []
    def child(**kwargs):
        called.append(kwargs)
        report = seal({'schema_version': 1, 'kind': kind,
                       'decision': decision if decision in ('fail', 'unknown') else 'fail'}, field)
        if decision == 'bad-seal':
            report[field] = '0'*64
        elif decision == 'wrong-kind':
            report = seal({**report, 'kind': 'another-component'}, field)
        elif decision == 'missing':
            report = None
        return report, None
    monkeypatch.setattr(module, function, child)
    result, transport, observed = operation.qualify_publish_and_prepare_git(**subject['args'])
    expected = 'fail' if decision == 'fail' else 'unknown'
    assert result['decision'] == result['checks'][stage] == expected
    assert transport is observed is None and len(called) == 1
    later = list(result['checks'])[list(result['checks']).index(stage)+1:]
    assert all(result['checks'][name] == 'not-started' for name in later)
    assert result['factory_decision'] == 'unknown' and result['delivery_authority'] == 'not-issued'
    assert not subject['gets']


def test_installed_gate_expires_during_loaded_module_read(monkeypatch):
    members = {str(path.relative_to(operation.ROOT)): sha(path.read_bytes())
               for path in (operation.ROOT/'src').rglob('*.py')}
    monkeypatch.setattr(operation, '_package', lambda *args: (b'manifest', members))
    read, clock = operation._read, time.monotonic
    deadline = clock()+30
    def slow_read(*args):
        raw = read(*args)
        monkeypatch.setattr(operation.time, 'monotonic', lambda: deadline+1)
        return raw
    monkeypatch.setattr(operation, '_read', slow_read)
    with pytest.raises(ValueError):
        operation._installed('a'*64, 'devsecops@devsecops-local', '0.2.0-alpha.7', deadline)


def test_last_installed_audit_expiry_withdraws_success_summary(subject, monkeypatch):
    original, clock = operation._installed, time.monotonic
    seen = []
    def slow_last(*args):
        result = original(*args)
        if (subject['args']['output']/'mission-git-operation.json').exists():
            seen.append(True)
            if len(seen) == 2:
                monkeypatch.setattr(operation.time, 'monotonic', lambda: clock()+5000)
        return result
    monkeypatch.setattr(operation, '_installed', slow_last)
    with pytest.raises(ValueError):
        operation.qualify_publish_and_prepare_git(**subject['args'])
    assert len(seen) == 2
    assert not (subject['args']['output']/'mission-git-operation.json').exists()
    assert (subject['args']['output']/'base/git-base-possession.json').exists()
