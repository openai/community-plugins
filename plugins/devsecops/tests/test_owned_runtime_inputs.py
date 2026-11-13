"""Controlled production composition, not live cluster/recording qualification."""
from copy import deepcopy
import json

import pytest

from devsecops.factory.owned_runtime_inputs import bind_runtime_recipe, validate_recipe_inputs, _probe_requests, _observed_target, _pointer
from devsecops.factory.network_policy_qualification import _validate_cni
from devsecops.factory.state import StateError
from devsecops.runtime.context import canonical_bytes, digest
from devsecops.runtime.probe_observations import prepare_probe_actor, validate_probe_plan
from test_runtime_recovery import fixture


def recipe():
    bundles, _, evidence = fixture()
    context = bundles['baseline']['context_snapshot']
    targets = []
    for t in context['intent']['targets']:
        targets.append({'id': t['id'], 'role': t['roles'][0], 'namespace': t['kubernetes']['namespace'],
            'deployment': t['kubernetes']['deployment'], 'service': t['kubernetes']['service'], 'container': t['id'],
            'health': {'port': 8080, 'path': '/health' if t['id'] == 'mission' else '/', 'body_sha256': 'e'*64},
            'argocd': {'namespace': t['argocd']['application_namespace'], 'application': t['argocd']['application'],
                'project': t['argocd']['project'], 'sources': [{k: s[k] for k in ('position', 'path', 'renderer')}
                                                            for s in t['argocd']['sources']]}})
    return {'schema_version': 1, 'kind': 'owned-runtime-selection', 'identities': context['intent']['identities'],
        'environment_id': context['intent']['environment'], 'targets': targets, 'delivery_source_position': 1,
        'read_transport': {'kind': 'owned-kubernetes-read-v1', 'services': {
            'argocd': {'namespace': 'argocd', 'name': 'argocd-server', 'port': 80, 'scheme': 'http'},
            'prometheus': {'namespace': 'monitoring', 'name': 'prometheus', 'port': 9090, 'scheme': 'http'}}},
        'prometheus': {'namespace': 'monitoring', 'service': 'prometheus', 'api_port': 9090,
                       'job': 'mission', 'port': 9090, 'instance': 'single-ready-pod-ip'},
        'probes': {'health': {'namespace': 'health', 'pod_labels': {'app': 'health'}},
            'metrics': {'namespace': 'monitoring', 'pod_labels': {'app': 'prometheus'}},
            'wrong_namespace': {'namespace': 'other', 'pod_labels': {'app': 'prometheus'}},
            'wrong_label': {'namespace': 'monitoring', 'pod_labels': {'app': 'wrong'}}},
        'cluster_domain': 'cluster.local',
        'policy': {'source_kind': 'helm-values', 'source_path': 'values.yaml',
            'source_pointer': '/networkPolicy/metricsPeer/podLabels/app',
            'resource_id': 'networking.k8s.io/NetworkPolicy/demo/mission-metrics'},
        'cni': {'kind': 'canal', 'namespace': 'kube-system', 'daemonset': 'rke2-canal', 'spec_sha256': 'd'*64,
                'required_containers': ['selected-calico', 'selected-flannel']},
        'timing': {'lead_seconds': 5, 'request_seconds': .5, 'gap_seconds': 1}}


def test_recipe_is_pure_and_preserves_operator_body_peer_source_and_cni_expectations():
    selected = recipe()
    result = bind_runtime_recipe(canonical_bytes(selected), identities=selected['identities'], environment_id=selected['environment_id'])
    assert result == selected
    assert result is not selected
    assert result['targets'][0]['health']['body_sha256'] == 'e'*64
    assert result['cni']['spec_sha256'] == 'd'*64


@pytest.mark.parametrize('damage', ['run', 'environment', 'token', 'command', 'overlap', 'negative-ns',
                                   'negative-label', 'source-gap', 'source-escape', 'cni-route', 'transport-port'])
def test_recipe_rejects_ambiguous_or_unselected_scope_before_any_effect(damage):
    selected = recipe()
    identities, environment = deepcopy(selected['identities']), selected['environment_id']
    if damage == 'run':
        selected['identities']['package_sha256'] = '0'*64
    elif damage == 'environment':
        selected['environment_id'] = 'other'
    elif damage in {'token', 'command'}:
        selected[damage] = 'forbidden'
    elif damage == 'overlap':
        selected['targets'][1] = deepcopy(selected['targets'][0])
    elif damage == 'negative-ns':
        selected['probes']['wrong_namespace']['namespace'] = 'monitoring'
    elif damage == 'negative-label':
        selected['probes']['wrong_label']['pod_labels'] = {'app': 'prometheus'}
    elif damage == 'source-gap':
        selected['targets'][0]['argocd']['sources'][0]['position'] = 2
    elif damage == 'source-escape':
        selected['policy']['source_path'] = '../outside.yaml'
    elif damage == 'transport-port':
        selected['read_transport']['services']['prometheus']['port'] = 80
    else:
        selected['cni']['daemonset'] = 'calico-node'
    with pytest.raises((StateError, ValueError)):
        bind_runtime_recipe(canonical_bytes(selected), identities=identities, environment_id=environment)


def test_actual_four_actor_plan_yields_exact_fourteen_case_matrix_for_both_attempts():
    selected = recipe()
    bundles, _, evidence = fixture()
    services = {s['target_id']: {'metadata': {'namespace': 'demo', 'name': s['target_id'], 'uid': s['uid']},
                'spec': {'clusterIPs': s['cluster_ips'], 'ports': [{'port': 8080}, {'port': 9090}]}}
                for s in evidence['inventory']['services']}
    for attempt in ('primary', 'replay'):
        cases = []
        for phase in ('baseline', 'fault', 'recovery'):
            frozen = deepcopy(bundles[phase]['context_snapshot'])
            frozen['intent']['attempt_id'] = attempt
            groups = _probe_requests(selected, intent=frozen['intent'], window=bundles[phase]['window'],
                services=services, image='example.test/mission@sha256:'+'a'*64, suffix='unique')
            assert len(groups) == (1 if phase == 'fault' else 4)
            for group in groups:
                plan = prepare_probe_actor(frozen, **group)
                pod, config = validate_probe_plan(plan, frozen)
                assert pod['spec']['containers'][0]['image'].endswith('@sha256:'+'a'*64)
                batch = json.loads(config['data']['batch.json'])
                cases += [r['request']['case_id'] for r in batch['requests']]
                assert all(r['request']['attempt_id'] == attempt for r in batch['requests'])
        assert len(cases) == len(set(cases)) == 14


def test_fixed_schedule_cannot_slide_or_extend_a_short_collection_window():
    selected = recipe()
    bundles, _, _ = fixture()
    window = dict(bundles['baseline']['window'], end=bundles['baseline']['window']['start']+5)
    with pytest.raises(StateError, match='immutable'):
        _probe_requests(selected, intent=bundles['baseline']['context_snapshot']['intent'], window=window,
                        services={}, image='unused', suffix='test')


def target_observation():
    selected = recipe()['targets'][0]
    def obj(kind, name, spec, namespace='demo'):
        return {'apiVersion': 'v1', 'kind': kind, 'metadata': {'namespace': namespace, 'name': name, 'uid': kind+'-uid',
            'resourceVersion': '1', 'generation': 1}, 'spec': spec}
    delivery = {'watched_revision': 'a'*40, 'repository_url': 'https://git.test/owned/project.git', 'rendered_sha256': 'd'*64}
    argo = selected['argocd']
    app = obj('Application', argo['application'], {'project': argo['project'],
        'source': {'repoURL': delivery['repository_url'], 'path': argo['sources'][0]['path'], 'targetRevision': 'main'},
        'destination': {'server': 'https://kubernetes.default.svc', 'namespace': 'demo'}}, namespace=argo['namespace'])
    app['status'] = {'sync': {'status': 'Synced', 'revision': delivery['watched_revision']}, 'health': {'status': 'Healthy'},
        'resources': [{'kind': 'Deployment', 'namespace': 'demo', 'name': selected['deployment']},
                      {'kind': 'Service', 'namespace': 'demo', 'name': selected['service']}]}
    dep = obj('Deployment', selected['deployment'], {'replicas': 1, 'selector': {'matchLabels': {'app': 'mission'}}})
    dep['status'] = {'observedGeneration': 1, 'availableReplicas': 1}
    svc = obj('Service', selected['service'], {'clusterIPs': ['10.96.0.5']})
    pod = obj('Pod', 'actual-pod', {'containers': [{'name': selected['container'], 'image': 'image@sha256:'+'c'*64}]})
    pod['metadata']['labels'] = {'app': 'mission'}
    pod['status'] = {'conditions': [{'type': 'Ready', 'status': 'True'}], 'podIP': '10.0.0.5'}
    return selected, {'application': app, 'deployment': dep, 'service': svc, 'pods': [pod], 'delivery': delivery,
                      'image_mapping': {'id': 'mapping', 'sha256': 'f'*64}}


def test_target_uses_observed_uids_generation_and_immutable_merged_revision():
    selected, inputs = target_observation()
    target, pod = _observed_target(selected, **inputs)
    assert target['kubernetes']['deployment_uid'] == 'Deployment-uid'
    assert target['kubernetes']['service_uid'] == 'Service-uid'
    assert target['argocd']['sources'][0]['revision'] == 'a'*40
    assert pod['status']['podIP'] == '10.0.0.5'


@pytest.mark.parametrize('damage', ['revision', 'repo', 'namespace', 'missing-resource', 'replicas', 'generation', 'duplicate-pod', 'sidecar'])
def test_observed_target_cannot_choose_convenient_or_unbound_live_facts(damage):
    selected, values = target_observation()
    if damage == 'revision':
        values['application']['status']['sync']['revision'] = 'b'*40
    elif damage == 'repo':
        values['application']['spec']['source']['repoURL'] = 'https://other.test/repo'
    elif damage == 'namespace':
        values['service']['metadata']['namespace'] = 'other'
    elif damage == 'missing-resource':
        values['application']['status']['resources'].pop()
    elif damage == 'replicas':
        values['deployment']['spec']['replicas'] = 2
    elif damage == 'generation':
        values['deployment']['status']['observedGeneration'] = 0
    elif damage == 'duplicate-pod':
        values['pods'].append(deepcopy(values['pods'][0]))
    else:
        values['pods'][0]['spec']['containers'].append({'name': 'istio-proxy'})
    with pytest.raises(StateError):
        _observed_target(selected, **values)


@pytest.mark.parametrize('kind,name', [('canal', 'rke2-canal'), ('calico', 'calico-node'), ('cilium', 'cilium')])
def test_cni_requires_actual_exact_selected_descriptor_and_ready_generation(kind, name):
    cni = {'metadata': {'namespace': 'kube-system', 'name': name, 'uid': 'actual-cni', 'generation': 3},
        'spec': {'template': {'spec': {'containers': [{'name': 'selected-container', 'image': 'pinned@sha256:'+'f'*64}]}}},
        'status': {'desiredNumberScheduled': 1, 'numberReady': 1, 'observedGeneration': 3}}
    selection = {'kind': kind, 'namespace': 'kube-system', 'daemonset': name, 'spec_sha256': digest(cni['spec']),
                 'required_containers': ['selected-container']}
    _validate_cni(cni, selection, locked_cni=kind)
    cni['spec']['template']['spec']['containers'][0]['image'] = 'other:latest'
    with pytest.raises(StateError):
        _validate_cni(cni, selection, locked_cni=kind)


def test_explicit_helm_source_pointer_does_not_guess_a_values_transform():
    source = {'networkPolicy': {'metricsPeer': {'podLabels': {'app.kubernetes.io/name': 'prometheus'}}}}
    assert _pointer(source, '/networkPolicy/metricsPeer/podLabels/app.kubernetes.io~1name') == 'prometheus'
    with pytest.raises(StateError):
        _pointer(source, '/networkPolicy/metricsPort')


def runtime_inputs():
    selected = recipe()
    bundles, *_ = fixture()
    frozen = deepcopy(bundles['baseline']['context_snapshot'])
    config, lock = frozen['factory_config'], frozen['factory_lock']
    for service in config['services']:
        if service['id'] in ('kubernetes', 'argocd', 'prometheus'):
            service['read_auth'] = {'mode': 'none'}
            service.pop('ca_file', None)
        if service['id'] == 'kubernetes':
            service['scope']['namespaces'] = ['demo', 'health', 'monitoring', 'other', 'kube-system']
            service['scope']['applications'] = sorted({t[k] for t in selected['targets'] for k in ('deployment', 'service')})
    lock['platform'].update(cni='canal', mesh_mode='disabled')
    return selected, config, lock


def test_pre_effect_selection_checks_scopes_and_window_without_actual_provider_calls():
    selected, config, lock = runtime_inputs()
    assert validate_recipe_inputs(selected, config, lock)


@pytest.mark.parametrize('damage', ['credentials', 'ca', 'namespace', 'workload', 'mesh', 'cni', 'schedule'])
def test_pre_effect_selection_rejects_static_auth_missing_read_scope_and_infeasible_window(damage):
    selected, config, lock = runtime_inputs()
    kube = next(s for s in config['services'] if s['id'] == 'kubernetes')
    if damage == 'credentials':
        kube['read_auth'] = {'mode': 'bearer', 'token_env': 'STATIC_TOKEN'}
    elif damage == 'ca':
        kube['ca_file'] = '/selected/static-ca.pem'
    elif damage == 'namespace':
        kube['scope']['namespaces'].remove('health')
    elif damage == 'workload':
        kube['scope']['applications'].pop()
    elif damage == 'mesh':
        lock['platform']['mesh_mode'] = 'ambient'
    elif damage == 'cni':
        lock['platform']['cni'] = 'cilium'
    else:
        lock['observation_window_seconds'] = 10
    with pytest.raises(StateError):
        validate_recipe_inputs(selected, config, lock)
