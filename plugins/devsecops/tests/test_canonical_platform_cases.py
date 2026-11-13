"""Controlled interpretation and transition tests; no actual Flux execution."""
from copy import deepcopy
import os
import sys
import threading
from types import SimpleNamespace, ModuleType
from uuid import uuid4
import pytest

from devsecops.factory.case_platform import assess_observation, revision_commit, bind_core_selection
from devsecops.factory import case_platform_git as git_case
from devsecops.factory.case_platform_git import (PlatformGitTransition, extension_fault,
    require_platform_transition, unchanged_other_blobs)
from devsecops.factory.state import StateError, _canonical, _json


SELECTION = {'namespace': 'extensions', 'source_name': 'platform-source', 'root_name': 'root',
             'dependency_name': 'base', 'extension_name': 'optional',
             'service': {'name': 'dependent', 'expected': {'status': 'ok'}}}


def resource(kind, name, generation=1, revision='a' * 40):
    api = {'GitRepository': 'source.toolkit.fluxcd.io/v1', 'Kustomization': 'kustomize.toolkit.fluxcd.io/v1',
           'HelmRelease': 'helm.toolkit.fluxcd.io/v2', 'Deployment': 'apps/v1', 'Application': 'argoproj.io/v1alpha1'}[kind]
    return {'apiVersion': api, 'kind': kind, 'metadata': {'namespace': 'extensions', 'name': name,
        'uid': 'uid-' + name, 'generation': generation}, 'spec': {}, 'status': {
        'observedGeneration': generation, 'lastAppliedRevision': 'main@sha1:' + revision,
        'conditions': [{'type': 'Ready', 'status': 'True', 'observedGeneration': generation}]}}


@pytest.fixture
def observations():
    source = resource('GitRepository', 'platform-source')
    source['spec'] = {'ref': {'commit': 'a' * 40}}
    source['status']['artifact'] = {'revision': 'sha1:' + 'a' * 40}
    root, dependency, extension = [resource('Kustomization', name) for name in ('root', 'base', 'optional')]
    for item in (root, extension):
        item['spec']['sourceRef'] = {'kind': 'GitRepository', 'name': 'platform-source'}
    extension['spec']['dependsOn'] = [{'name': 'base'}]
    extension['status']['inventory'] = {'entries': [{'id': 'extensions_dependent__Service', 'v': 'v1'}]}
    core = [resource('Deployment', 'flux'), resource('HelmRelease', 'bigbang'), resource('Application', 'argo')]
    core[0]['spec']['replicas'] = 1
    core[0]['status'].update(updatedReplicas=1, availableReplicas=1)
    core[0]['status']['conditions'][0]['type'] = 'Available'
    core[2]['status'].update(sync={'status': 'Synced'}, health={'status': 'Healthy'})
    baseline = {'source': source, 'root': root, 'dependency': dependency, 'extension': extension,
                'core': core, 'service_health': {'status': 'ok'}, 'service': {'metadata': {'uid': 'svc-uid'}}}
    fault, repaired = deepcopy(baseline), deepcopy(baseline)
    for item, revision, gen in ((fault, 'b' * 40, 2), (repaired, 'c' * 40, 3)):
        item['source']['spec']['ref']['commit'] = revision
        item['source']['status']['artifact']['revision'] = 'main@sha1:' + revision
        item['root']['status']['lastAppliedRevision'] = 'main@sha1:' + revision
        item['extension']['metadata']['generation'] = gen
        item['extension']['status']['conditions'][0]['observedGeneration'] = gen
    fault['extension']['spec']['dependsOn'] = [{'name': 'base-unavailable'}]
    fault['extension']['status']['conditions'][0].update(status='False', reason='DependencyNotReady')
    fault['extension']['status']['lastAttemptedRevision'] = 'main@sha1:' + 'b' * 40
    repaired['extension']['status'].update(observedGeneration=3, lastAppliedRevision='sha1:' + 'c' * 40)
    return baseline, fault, repaired


def test_actual_status_semantics_distinguish_applied_from_attempted(observations):
    baseline, fault, repaired = observations
    assert assess_observation(baseline, stage='baseline', commit='a' * 40, selection=SELECTION)
    result = assess_observation(fault, stage='fault', commit='b' * 40, selection=SELECTION, baseline=baseline)
    assert result['last_applied_commit'] == 'a' * 40
    assert result['last_attempted_commit'] == 'b' * 40
    assert result['condition_observed_generation'] == 2
    assert result['status_observed_generation'] == 1
    assert 'no-network-policy-assurance' in result['service_health_route']
    assert assess_observation(repaired, stage='repair', commit='c' * 40, selection=SELECTION, baseline=baseline, fault=fault)


@pytest.mark.parametrize('change', [
    lambda o: o['extension']['status']['conditions'][0].update(observedGeneration=1),
    lambda o: o['extension']['status'].update(lastAppliedRevision='sha1:' + 'b' * 40),
    lambda o: o['extension']['status']['conditions'][0].update(reason='UnrelatedFailure'),
    lambda o: o['core'][0]['status'].update(availableReplicas=0),
    lambda o: o['core'][1]['spec'].update(suspend=True),
    lambda o: o['core'][2]['status']['sync'].update(status='OutOfSync'),
    lambda o: o['service']['metadata'].update(uid='replaced'),
    lambda o: o['source']['spec'].update(ref={'branch': 'main'}),
    lambda o: o['root']['status'].update(lastAppliedRevision='sha1:' + 'a' * 40),
    lambda o: o.update(service_health={'status': 'failed'}),
    lambda o: o['extension']['status'].update(inventory={'entries': []}),
])
def test_partial_or_unrelated_fault_never_passes(observations, change):
    baseline, fault, _ = observations
    change(fault)
    assert assess_observation(fault, stage='fault', commit='b' * 40, selection=SELECTION, baseline=baseline) is None


@pytest.mark.parametrize('revision', ['sha256:' + 'a' * 64, 'prefix-' + 'a' * 40, 'main@' + 'a' * 40,
                                      'sha1:' + 'a' * 40 + '-suffix', None])
def test_ambiguous_revision_does_not_become_applied_commit(revision):
    assert revision_commit(revision) is None


def test_fixed_fault_changes_only_dependency_and_preserves_baseline():
    baseline = resource('Kustomization', 'optional')
    del baseline['status']
    baseline['metadata'] = {'namespace': 'extensions', 'name': 'optional'}
    baseline['spec'] = {'dependsOn': [{'name': 'base'}], 'sourceRef': {'kind': 'GitRepository', 'name': 'platform-source'},
                        'path': './platform/optional', 'prune': True, 'interval': '10s'}
    raw = _canonical(baseline)
    changed = _json(extension_fault(raw, SELECTION))
    assert changed['spec']['dependsOn'] == [{'name': 'base-unavailable'}]
    changed['spec']['dependsOn'] = baseline['spec']['dependsOn']
    assert changed == baseline and raw == _canonical(baseline)


def test_other_git_file_changes_and_tree_membership_are_rejected():
    before = {'platform/optional.yaml': {'type': 'blob', 'id': 'a'}, 'README.md': {'type': 'blob', 'id': 'b'}}
    after = deepcopy(before)
    after['platform/optional.yaml']['id'] = 'c'
    unchanged_other_blobs(before, after, 'platform/optional.yaml')
    after['README.md']['id'] = 'd'
    with pytest.raises(StateError, match='unrelated'):
        unchanged_other_blobs(before, after, 'platform/optional.yaml')
    del after['README.md']
    with pytest.raises(StateError, match='membership'):
        unchanged_other_blobs(before, after, 'platform/optional.yaml')


def test_json_cannot_issue_platform_mutation_origin():
    with pytest.raises(StateError):
        PlatformGitTransition()
    with pytest.raises(StateError, match='same-process'):
        require_platform_transition({'commit': 'a' * 40}, journal=None, context=None)


def test_unrelated_owned_resources_cannot_substitute_for_actual_core_roles():
    recipe = {'readiness': {'flux_deployments': [{'namespace': 'flux-system', 'name': 'controller'}],
        'helm_releases': [{'namespace': 'bigbang', 'name': 'bigbang'}],
        'argo_application': {'namespace': 'argocd', 'name': 'platform'}}}
    selected = {'core_resources': [
        {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'namespace': 'flux-system', 'name': 'controller'},
        {'apiVersion': 'helm.toolkit.fluxcd.io/v2', 'kind': 'HelmRelease', 'namespace': 'bigbang', 'name': 'bigbang'},
        {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application', 'namespace': 'argocd', 'name': 'platform'}]}
    observed = {'argo_application_identity': {'namespace': 'argocd', 'name': 'platform', 'uid': 'original'}}
    bind_core_selection(selected, recipe, observed)
    selected['core_resources'][0]['name'] = 'unrelated-healthy'
    with pytest.raises(StateError, match='readiness roles'):
        bind_core_selection(selected, recipe, observed)


@pytest.fixture
def git_writer(tmp_path, monkeypatch):
    selected = deepcopy(SELECTION)
    baseline = {'apiVersion': 'kustomize.toolkit.fluxcd.io/v1', 'kind': 'Kustomization',
        'metadata': {'namespace': 'extensions', 'name': 'optional'},
        'spec': {'dependsOn': [{'name': 'base'}], 'sourceRef': {'kind': 'GitRepository', 'name': 'platform-source'},
                 'path': './platform/optional', 'prune': True, 'interval': '10s'}}
    path = tmp_path / 'baseline.yaml'
    path.write_bytes(_canonical(baseline))
    selected.update(repository_path='platform/extension.yaml', baseline_file={'path': str(path), 'sha256': git_case._sha(path.read_bytes())})
    source = {'metadata': {'uid': 'source-uid'}, 'spec': {'url': 'https://git.test/owner/project.git', 'ref': {'commit': 'a' * 40}}}
    extension = {'metadata': {'uid': 'extension-uid'}}
    class Transport:
        def __init__(self):
            self.receipts, self.writes, self.branches = [], [], {'main': 'd' * 40}
            self.fail_commit = False
            self.poisoned = False
        def stop_writes(self):
            self.poisoned = True
        def request(self, method, route, payload=None, expected=(200,)):
            assert all(200 <= status <= 299 for status in expected)
            self.receipts.append({'status': 200, 'method': method, 'request_sha256': git_case._sha(route.encode())})
            if method == 'GET' and '/branches/' in route:
                branch = route.rsplit('/', 1)[1]
                if branch not in self.branches:
                    self.receipts[-1]['status'] = 404
                    raise git_case.GitLabAPIError('http-status', 404, 'none')
                return {'name': branch, 'commit': {'id': self.branches[branch]}}
            if method == 'GET':
                return {}
            self.writes.append((route, deepcopy(payload)))
            self.receipts[-1]['status'] = 201
            if route.endswith('/branches'):
                self.branches[payload['branch']] = payload['ref']
                return {'name': payload['branch'], 'commit': {'id': payload['ref']}}
            if self.fail_commit:
                raise OSError('controlled unknown response')
            parent = self.branches[payload['branch']]
            self.branches[payload['branch']] = 'b' * 40
            return {'id': 'b' * 40, 'parent_ids': [parent]}
    transport = Transport()
    resources = SimpleNamespace(read_owned=lambda c, d: source if d['kind'] == 'GitRepository' else extension,
        session=SimpleNamespace(request=lambda *a, **k: None))
    observed = {'scope': {'origin': 'https://git.test', 'environment_id': 'demo', 'environment_fingerprint': 'e' * 64,
        'owner_user_id': 1, 'namespace_id': 2, 'namespace_path': 'owner'},
        'projects': {'deployment': {'id': 3, 'path_with_namespace': 'owner/project'}}}
    bootstrap = SimpleNamespace(gitlab=lambda c: (object(), transport), kubernetes=lambda c: resources)
    module = ModuleType('devsecops.factory.canonical_bootstrap')
    module.require_bootstrap_authority = lambda *a: observed
    monkeypatch.setitem(sys.modules, 'devsecops.factory.canonical_bootstrap', module)
    monkeypatch.setattr(git_case, '_project_identity', lambda *a: 3)
    seen_files = []
    def file_read(session, project, name, commit, expected_content):
        seen_files.append((commit, expected_content))
        return {'blob_id': ('a' if commit == 'a' * 40 else 'b') * 40, 'last_commit_id': commit}
    monkeypatch.setattr(git_case, '_file', file_read)
    monkeypatch.setattr(git_case, '_tree', lambda s, p, commit: {
        'platform/extension.yaml': {'type': 'blob', 'id': ('a' if commit == 'a' * 40 else 'b') * 40},
        'README.md': {'type': 'blob', 'id': 'e' * 40}})
    writer = object.__new__(git_case.OwnedPlatformGit)
    writer.journal, writer.operations, writer.selection, writer.output = object(), SimpleNamespace(bootstrap=bootstrap), selected, tmp_path / 'git'
    writer._pid, writer._last, writer._dispatched = os.getpid(), None, set()
    writer._files, writer._initial, writer._fault = {}, None, None
    writer._admit = lambda context: 'operation'
    context = SimpleNamespace(run_id=str(uuid4()), identities={'run': 'selected'}, deadline=1000)
    return writer, context, transport, seen_files


def test_actual_transition_uses_create_only_branch_from_selected_revision_and_preserves_main(git_writer):
    writer, context, transport, seen = git_writer
    transition = writer.transition(context, 'fault')
    binding = require_platform_transition(transition, journal=writer.journal, context=context)
    assert transport.branches['main'] == 'd' * 40
    assert transport.writes[0][0].endswith('/branches')
    assert transport.writes[0][1]['ref'] == 'a' * 40
    assert transport.writes[1][1]['branch'] == binding['branch']
    assert seen[0][0] == 'a' * 40 and binding['main_preserved'] == 'd' * 40


def test_preexisting_case_branch_cannot_be_adopted(git_writer):
    writer, context, transport, _ = git_writer
    transport.branches['codex-d3-' + context.run_id.replace('-', '')] = 'a' * 40
    with pytest.raises(StateError, match='already exists'):
        writer.transition(context, 'fault')
    assert transport.writes == []


def test_unknown_commit_after_branch_creation_retains_intent_and_blocks_repeat(git_writer):
    writer, context, transport, _ = git_writer
    transport.fail_commit = True
    with pytest.raises(OSError):
        writer.transition(context, 'fault')
    assert transport.poisoned and (writer.output / 'fault-intent.json').exists()
    with pytest.raises(StateError, match='uncertain effects'):
        writer.transition(context, 'fault')
    assert len(transport.writes) == 2


def test_missing_branch_uses_real_gitlab_request_admission_without_404_allowlist():
    from devsecops.factory.gitlab_api import GitLabSession
    session = object.__new__(GitLabSession)
    session._lock, session._receipts = threading.RLock(), []
    def exchange(method, path, payload, expected):
        assert expected == (200,)
        session._receipts.append({'method': method, 'status': 404, 'request_sha256': git_case._sha(path.encode())})
        raise git_case.GitLabAPIError('http-status', 404, 'none')
    session._exchange = exchange
    assert git_case.case_branch_head(session, 3, 'codex-d3-' + 'a' * 32, absent=True) is None
