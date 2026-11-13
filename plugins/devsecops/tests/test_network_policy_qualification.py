"""Pure F0 semantic controls; no simulated test can issue live qualification."""
from copy import deepcopy

import pytest

from devsecops.factory.network_policy_qualification import (_candidate, _mesh_disabled, _phase_checks,
    QualifiedNetworkPolicyPath, require_qualified_network_policy_path)
from devsecops.factory.state import StateError, _json
from test_runtime_recovery import fixture


def selection():
    bundles, contract, evidence = fixture()
    resource = evidence['rendered_after']['resources'][0]['body']
    manifest = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
                'metadata': {k: resource['metadata'][k] for k in ('namespace', 'name')}, 'spec': deepcopy(resource['spec'])}
    auth = contract['metrics_authorization']
    plan = _candidate(manifest, source_path='deploy/policy.yaml', source_commit='a'*40, metrics_port=9090,
                      namespace_labels=auth['namespace_labels'], pod_labels=auth['pod_labels'])
    return bundles, contract, evidence, manifest, plan


def test_candidate_changes_exactly_one_existing_selector_preserving_default_deny_and_other_fields():
    _, _, _, manifest, plan = selection()
    changed = _json(plan.fault)
    assert plan.pointer == '/spec/ingress/0/from/0/podSelector/matchLabels/app'
    assert plan.before == 'prometheus' and plan.after == 'devsecops-f0-denied'
    changed['spec']['ingress'][0]['from'][0]['podSelector']['matchLabels']['app'] = plan.before
    assert changed == manifest
    assert plan.baseline == plan.restored
    assert _json(plan.fault)['spec']['policyTypes'] == ['Ingress']


@pytest.mark.parametrize('damage', ['unscoped', 'egress', 'duplicate', 'broad-peer', 'different-port', 'unknown-selector', 'status'])
def test_unqualified_policy_shapes_cannot_produce_experimental_fault(damage):
    _, contract, _, manifest, _ = selection()
    if damage == 'unscoped':
        manifest['spec']['podSelector'] = {}
    elif damage == 'egress':
        manifest['spec']['egress'] = []
    elif damage == 'duplicate':
        manifest['spec']['ingress'] *= 2
    elif damage == 'broad-peer':
        manifest['spec']['ingress'][0]['from'] = [{}]
    elif damage == 'different-port':
        manifest['spec']['ingress'][0]['ports'] = [{'port': 8080}]
    elif damage == 'unknown-selector':
        manifest['spec']['podSelector']['matchExpressions'] = [{'key': 'app', 'operator': 'Exists'}]
    else:
        manifest['status'] = {'fake': 'trusted'}
    with pytest.raises(StateError):
        _candidate(manifest, source_path='deploy/policy.yaml', source_commit='a'*40, metrics_port=9090,
                   namespace_labels=contract['metrics_authorization']['namespace_labels'],
                   pod_labels=contract['metrics_authorization']['pod_labels'])


def observations(state, *, f0=True):
    bundles, contract, evidence, _, plan = selection()
    bundle = deepcopy(bundles[state])
    receipts = {name: deepcopy(value) for name, value in evidence.items() if name.startswith('http:'+state+'-')}
    if f0:
        bundle['context_snapshot']['intent']['attempt_id'] = 'f0-network-policy'
        for receipt in receipts.values():
            receipt['request']['attempt_id'] = 'f0-network-policy'
    return {'bundle': bundle, 'receipts': receipts, 'sources': evidence['inventory']['probe_sources']}, plan, contract['metrics_authorization']


@pytest.mark.parametrize('phase', ['baseline', 'fault', 'recovery'])
def test_fixed_independent_controls_observe_expected_failure_and_recovery_without_issuing_authority(phase):
    value, plan, auth = observations(phase)
    _phase_checks(value, phase, plan, auth)
    with pytest.raises(StateError):
        QualifiedNetworkPolicyPath()
    with pytest.raises(StateError):
        require_qualified_network_policy_path(value, runtime=object(), run_id='run', identities={})


def test_expected_fault_can_remove_counter_but_never_lose_fresh_exact_up_source_samples():
    value, plan, auth = observations('fault')
    metrics = value['bundle']['targets']['mission']['prometheus']
    metrics['execution_status'] = 'partial'
    metrics['observations'].pop('functional')
    metrics['observations']['collection_freshness']['decision'] = 'unknown'
    metrics['gaps'] = [{'code': 'prometheus-missing-or-multiple-series'},
                       {'code': 'runtime-prometheus-source-time-unavailable'}]
    _phase_checks(value, 'fault', plan, auth)
    metrics['observations']['collection_freshness']['series']['up']['fresh'] = False
    with pytest.raises(StateError):
        _phase_checks(value, 'fault', plan, auth)
    metrics['observations']['collection_freshness']['series']['up']['fresh'] = True
    metrics['gaps'].append({'code': 'prometheus-denied'})
    with pytest.raises(StateError):
        _phase_checks(value, 'fault', plan, auth)


@pytest.mark.parametrize('damage', ['incident', 'wrong-target', 'old-source-samples', 'missing-source-samples',
    'evaluation-only', 'listener-control', 'negative-reachable', 'wrong-label', 'unbracketed', 'flat-counter'])
def test_f0_cannot_substitute_incident_fixtures_or_incomplete_datapath_controls(damage):
    value, plan, auth = observations('baseline', f0=damage != 'incident')
    metrics = value['bundle']['targets']['mission']['prometheus']
    if damage == 'wrong-target':
        metrics['scope']['instance'] = '10.0.0.99:9090'
    elif damage == 'old-source-samples':
        metrics['observations']['scrape']['samples'][0]['source_timestamp'] -= 3600
    elif damage == 'missing-source-samples':
        metrics['observations']['scrape']['samples'].pop()
    elif damage == 'evaluation-only':
        metrics['observations']['scrape']['samples'][0].pop('source_timestamp')
    elif damage == 'listener-control':
        value['receipts']['http:baseline-metrics-allowed-after']['observation']['status'] = 500
    elif damage == 'negative-reachable':
        value['receipts']['http:baseline-metrics-denied-wrong-label']['observation']['status'] = 200
    elif damage == 'wrong-label':
        value['sources'][2]['pod_labels']['app'] = 'prometheus'
    elif damage == 'unbracketed':
        value['receipts']['http:baseline-metrics-allowed-after']['started_at'] = value['receipts']['http:baseline-metrics-allowed-before']['started_at']
    elif damage == 'flat-counter':
        metrics['observations']['functional']['progressing'] = False
    with pytest.raises(StateError):
        _phase_checks(value, 'baseline', plan, auth)


@pytest.mark.parametrize('damage', ['ambient', 'sidecar', 'init', 'unknown-enrollment', 'redirect-annotation'])
def test_observed_mesh_path_is_never_relabelled_plain_http(damage):
    ns = {'metadata': {'labels': {'istio-injection': 'disabled'}}}
    pod = {'metadata': {}, 'spec': {'containers': [{'name': 'mission'}]}}
    if damage == 'ambient':
        ns['metadata']['labels']['istio.io/dataplane-mode'] = 'ambient'
    elif damage == 'sidecar':
        pod['spec']['containers'].append({'name': 'istio-proxy'})
    elif damage == 'init':
        pod['spec']['initContainers'] = [{'name': 'mesh-init'}]
    elif damage == 'unknown-enrollment':
        ns['metadata']['labels'] = {}
    else:
        pod['metadata']['annotations'] = {'ambient.istio.io/redirection': 'enabled'}
    with pytest.raises(StateError):
        _mesh_disabled(ns, [pod])
