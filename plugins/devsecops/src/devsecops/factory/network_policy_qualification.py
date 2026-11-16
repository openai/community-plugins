"""Independent, pre-incident NetworkPolicy experiment with original readbacks.

Only the explicitly disabled-mesh, plain HTTP path is supported. Other paths
remain unqualified until a separate protocol-specific implementation exists.
Plans are inert; the owning Kubernetes/Git handler performs each journaled
transition. No incident receipt can be attached as an F0 observation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
from types import MappingProxyType
from weakref import WeakKeyDictionary

from devsecops.profiles import relative_path
from devsecops.runtime.context import canonical_bytes, digest, parse_time, clock, freeze_runtime_context
from devsecops.runtime.probe_observations import _client, probe_observation_artifacts, probe_observation_transport
from .state import StateError, _read, _sha, _json

_ISSUER = object()
_PLANS, _QUALIFIED = WeakKeyDictionary(), WeakKeyDictionary()
_STATES = ('baseline', 'fault', 'recovery')


@dataclass(frozen=True, eq=False)
class PolicyExperimentPlan:
    baseline: bytes
    fault: bytes
    restored: bytes
    source_path: str
    source_commit: str
    resource_id: str
    pointer: str
    before: str
    after: str
    metrics_port: int


class QualifiedNetworkPolicyPath:
    __slots__ = ('__weakref__',)

    def __new__(cls, issuer=None):
        if issuer is not _ISSUER:
            raise StateError('NetworkPolicy qualification requires an independent completed experiment')
        return super().__new__(cls)

    def __reduce__(self):
        raise StateError('NetworkPolicy qualification cannot be restored from reports')


def _candidate(manifest, *, source_path, source_commit, metrics_port, namespace_labels, pod_labels):
    """One exact selector defect; all other resource content is preserved."""
    import re
    relative_path(source_path)
    if (not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', source_commit or '')
            or type(manifest) is not dict or manifest.get('apiVersion') != 'networking.k8s.io/v1'
            or manifest.get('kind') != 'NetworkPolicy' or set(manifest)-{'apiVersion', 'kind', 'metadata', 'spec'}
            or type(metrics_port) is not int or not 1 <= metrics_port <= 65535
            or not namespace_labels or not pod_labels):
        raise StateError('An exact NetworkPolicy source and scoped telemetry selector are required')
    metadata, spec = manifest['metadata'], manifest['spec']
    if (set(metadata)-{'namespace', 'name', 'labels', 'annotations'}
            or not metadata.get('namespace') or not metadata.get('name')
            or set(spec)-{'podSelector', 'policyTypes', 'ingress'}
            or spec.get('policyTypes') != ['Ingress']
            or not spec.get('podSelector', {}).get('matchLabels')
            or spec['podSelector'].get('matchExpressions')):
        raise StateError('Only a scoped ingress-only default-deny-preserving policy is supported')
    matched = []
    for index, rule in enumerate(spec.get('ingress', [])):
        if rule.get('ports') == [{'port': metrics_port, 'protocol': 'TCP'}] or rule.get('ports') == [{'port': metrics_port}]:
            peer = {'namespaceSelector': {'matchLabels': namespace_labels}, 'podSelector': {'matchLabels': pod_labels}}
            if set(rule) != {'ports', 'from'} or rule['from'] != [peer]:
                raise StateError('Telemetry rule must have one exact namespace-and-pod peer')
            matched.append(index)
    if len(matched) != 1:
        raise StateError('Exactly one explicit telemetry ingress rule is required')
    key = sorted(pod_labels)[0]
    before, after = pod_labels[key], 'devsecops-f0-denied'
    if before == after:
        raise StateError('The experimental selector must differ from the allowed label')
    index = matched[0]
    fault = deepcopy(manifest)
    fault['spec']['ingress'][index]['from'][0]['podSelector']['matchLabels'][key] = after
    pointer = '/spec/ingress/'+str(index)+'/from/0/podSelector/matchLabels/'+key.replace('~', '~0').replace('/', '~1')
    resource = 'networking.k8s.io/NetworkPolicy/'+metadata['namespace']+'/'+metadata['name']
    return PolicyExperimentPlan(canonical_bytes(manifest), canonical_bytes(fault), canonical_bytes(manifest),
                                source_path, source_commit, resource, pointer, before, after, metrics_port)


def _mesh_disabled(namespace, pods):
    labels = namespace.get('metadata', {}).get('labels', {})
    if labels.get('istio-injection') != 'disabled' or labels.get('istio.io/dataplane-mode') == 'ambient':
        raise StateError('Explicit disabled namespace enrollment is required for the plain HTTP experiment')
    for pod in pods:
        metadata, spec = pod.get('metadata', {}), pod.get('spec', {})
        if (any(key.startswith(('ambient.istio.io/', 'sidecar.istio.io/')) for key in metadata.get('annotations', {}))
                or spec.get('initContainers') or spec.get('ephemeralContainers')
                or any(c.get('name') in {'istio-proxy', 'ztunnel', 'linkerd-proxy'} for c in spec.get('containers', []))):
            raise StateError('Observed mesh injection is outside the plain HTTP experiment')


def _validate_cni(cni, selection, *, locked_cni):
    """The selected installed descriptor is an expectation, never a live fact."""
    names = {'calico': 'calico-node', 'cilium': 'cilium', 'canal': 'rke2-canal'}
    if (selection['kind'] != locked_cni or locked_cni not in names
            or selection['namespace'] != 'kube-system' or selection['daemonset'] != names[locked_cni]
            or cni.get('metadata', {}).get('name') != selection['daemonset']
            or cni['metadata'].get('namespace') != selection['namespace'] or not cni['metadata'].get('uid')
            or digest(cni.get('spec')) != selection['spec_sha256']
            or not set(selection['required_containers']) <= {c.get('name') for c in cni.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])}
            or cni.get('status', {}).get('desiredNumberScheduled', 0) < 1
            or cni['status'].get('numberReady') != cni['status']['desiredNumberScheduled']
            or cni['status'].get('observedGeneration') != cni['metadata'].get('generation')):
        raise StateError('Actual CNI installed spec, controller identity or readiness differs from its pinned selection')


class NetworkPolicyExperiment:
    def __init__(self, runtime, *, bootstrap):
        from .runtime_observations import RuntimeObservationSession
        from .canonical_bootstrap import OwnedBootstrapCoordinator
        if type(runtime) is not RuntimeObservationSession or type(bootstrap) is not OwnedBootstrapCoordinator:
            raise StateError('F0 requires concrete original runtime and bootstrap coordinators')
        self.runtime, self.bootstrap = runtime, bootstrap
        self._pid, self._plan, self._observed, self._qualified = os.getpid(), None, {}, None
        self._before = {}

    def _admit(self, context):
        if self._pid != os.getpid() or context.phase['operation'] not in {'build-promote', 'reset'}:
            raise StateError('F0 is limited to its explicitly granted production preparation phases')
        authority = self.runtime._admit(context)
        if authority is not self.bootstrap.require_authority(context):
            raise StateError('F0 bootstrap identity changed')
        from .canonical_bootstrap import require_bootstrap_authority
        return require_bootstrap_authority(authority, self.runtime.driver.journal)

    def prepare(self, context, *, baseline_manifest: Path, manifest_sha256, source_path,
                source_commit, metrics_port, namespace_labels, pod_labels):
        self._admit(context)
        if self._plan is not None:
            raise StateError('F0 plan is already prepared; do not repeat the experiment')
        raw = _read(baseline_manifest, 256000)
        if _sha(raw) != manifest_sha256:
            raise StateError('F0 baseline manifest identity changed')
        manifest = _json(raw)
        plan = _candidate(manifest, source_path=source_path, source_commit=source_commit,
            metrics_port=metrics_port, namespace_labels=namespace_labels, pod_labels=pod_labels)
        self._plan = plan
        _PLANS[plan] = {'owner': self, 'path': baseline_manifest, 'raw': raw, 'pid': os.getpid(),
                        'namespace_labels': deepcopy(namespace_labels), 'pod_labels': deepcopy(pod_labels)}
        return plan

    def require_transition(self, plan, context, *, state):
        self._admit(context)
        record = _PLANS.get(plan) if type(plan) is PolicyExperimentPlan else None
        if (record is None or record['owner'] is not self or record['pid'] != os.getpid() or plan is not self._plan
                or _read(record['path'], 256000) != record['raw'] or state not in _STATES
                or list(self._observed) != list(_STATES[:_STATES.index(state)])):
            raise StateError('Original F0 plan and previous direct experiment observations are required')
        return _json(getattr(plan, 'restored' if state == 'recovery' else state))

    def observe(self, context, *, state):
        """GET the exact transitioned policy/topology after original collectors."""
        facts = self._admit(context)
        expected = self.require_transition(self._plan, context, state=state)
        record = self.runtime._retained('f0-network-policy', state)
        bundle = record.get('bundle', {})
        before = self._before.get(state)
        if (before is None or before['intent_sha256'] != record['context']['input_artifacts']['intent']['sha256']
                or before['at'].timestamp() > (bundle.get('window') or {}).get('start', -1)):
            raise StateError('F0 policy was not directly observed before the exact collection window')
        if bundle.get('stage') != 'measured' or bundle.get('execution_mode') != 'direct' or record['mode'] != 'operator':
            raise StateError('F0 requires actual independent measured collections')
        if not record.get('probes') or not record.get('probe_artifacts'):
            raise StateError('F0 requires original authorized positive/negative probe observations')
        context_doc = record['context']
        lock = context_doc['factory_lock']
        if lock['platform']['mesh_mode'] != 'disabled' or lock['platform']['cni'] not in {'calico', 'cilium', 'canal'}:
            raise StateError('Selected CNI/mesh mode has no supported plain HTTP qualification route')
        transport = self.runtime._transport(context, context_doc, record['authority'])
        client, service = _client(context_doc, record['authority'], deadline=context.deadline, transport=transport)
        namespace, name = expected['metadata']['namespace'], expected['metadata']['name']
        if namespace not in service['scope'].get('namespaces', []) or 'kube-system' not in service['scope'].get('namespaces', []):
            raise StateError('F0 policy and actual CNI read scope are not authorized')
        policy = client.get_json('/apis/networking.k8s.io/v1/namespaces/'+namespace+'/networkpolicies/'+name)
        if (policy.get('spec') != expected['spec'] or policy.get('metadata', {}).get('namespace') != namespace
                or policy['metadata'].get('name') != name or not policy['metadata'].get('uid')
                or policy['metadata'].get('uid') != before['policy']['metadata']['uid']
                or policy['metadata'].get('resourceVersion') != before['policy']['metadata']['resourceVersion']):
            raise StateError('F0 policy readback differs from the one approved experimental selector')
        ns = client.get_json('/api/v1/namespaces/'+namespace)
        pods = []
        for target in bundle['targets'].values():
            images = target['after']['kubernetes']['observations']['image_snapshot']
            if images['namespace'] != namespace:
                continue
            for item in images['containers']:
                pod = client.get_json('/api/v1/namespaces/'+namespace+'/pods/'+item['pod_name'])
                if pod.get('metadata', {}).get('uid') != item['pod_uid']:
                    raise StateError('F0 target Pod changed after its runtime observation')
                pods.append(pod)
        if not pods:
            raise StateError('F0 target Pod observations are missing')
        _mesh_disabled(ns, pods)
        selection = self.runtime.cni_selection(context)
        cni_name = selection['daemonset']
        cni = client.get_json('/apis/apps/v1/namespaces/kube-system/daemonsets/'+cni_name)
        _validate_cni(cni, selection, locked_cni=lock['platform']['cni'])
        sources, receipts = [], {}
        for origin in record['probes']:
            source, values = probe_observation_artifacts(origin)
            transport = probe_observation_transport(origin)
            labels = transport['namespace_labels']
            if (labels.get('istio-injection') != 'disabled' or labels.get('istio.io/dataplane-mode') == 'ambient'
                    or any(k.startswith(('ambient.istio.io/', 'sidecar.istio.io/')) for k in transport['pod_annotations'])):
                raise StateError('F0 probe source mesh enrollment is not explicitly disabled')
            sources.append(source)
            if set(receipts) & set(values):
                raise StateError('Duplicate F0 probe control')
            receipts.update(values)
        observed = {'policy': policy, 'namespace': ns, 'pods': pods, 'cni': cni,
            'bundle': deepcopy(bundle), 'sources': sources, 'receipts': receipts,
            'bootstrap': facts, 'observed_at': clock().isoformat()}
        _phase_checks(observed, state, self._plan, _PLANS[self._plan])
        self._observed[state] = observed
        return {'state': state, 'observation_sha256': digest({k: v for k, v in observed.items() if k != 'bootstrap'})}

    def start_observation(self, context, *, state, intent: Path, intent_sha256):
        """Read the transitioned policy before collection/probes, with exact inputs."""
        self._admit(context)
        expected = self.require_transition(self._plan, context, state=state)
        if state in self._before:
            raise StateError('F0 before-observation already exists')
        raw = _read(intent, 1_000_000)
        if _sha(raw) != intent_sha256:
            raise StateError('F0 intent identity changed')
        doc = _json(raw)
        if (doc.get('attempt_id') != 'f0-network-policy' or doc.get('phase') != state
                or doc.get('run_id') != context.run_id or doc.get('identities') != dict(context.identities)):
            raise StateError('F0 observation requires its separate exact run/phase intent')
        journal = self.runtime.driver.journal
        selected = {name: journal._artifact_path(journal.state['lifecycle']['inputs'][name]['path'])
                    for name in ('config', 'lock')}
        selected['intent'] = intent
        frozen = freeze_runtime_context(selected['config'], selected['lock'], intent)
        transport = self.runtime._transport(context, frozen, selected)
        client, service = _client(frozen, selected, deadline=context.deadline, transport=transport)
        ns, name = expected['metadata']['namespace'], expected['metadata']['name']
        if ns not in service['scope'].get('namespaces', []):
            raise StateError('F0 policy namespace is outside explicit read scope')
        policy = client.get_json('/apis/networking.k8s.io/v1/namespaces/'+ns+'/networkpolicies/'+name)
        if (policy.get('spec') != expected['spec'] or policy.get('metadata', {}).get('name') != name
                or policy['metadata'].get('namespace') != ns or not policy['metadata'].get('uid')
                or not policy['metadata'].get('resourceVersion')):
            raise StateError('F0 transitioned policy readback is incomplete')
        self._before[state] = {'policy': policy, 'at': clock(), 'intent_sha256': intent_sha256}
        return {'uid': policy['metadata']['uid'], 'resourceVersion': policy['metadata']['resourceVersion'],
                'spec_sha256': digest(policy['spec'])}

    def qualify(self, context):
        facts = self._admit(context)
        self.runtime.mission_image_binding()
        if tuple(self._observed) != _STATES or self._qualified is not None:
            raise StateError('Independent baseline, fault and restored observations are required exactly once')
        baseline = self._observed['baseline']
        for state in _STATES:
            observed = self._observed[state]
            self.runtime._retained('f0-network-policy', state)
            _phase_checks(observed, state, self._plan, _PLANS[self._plan])
            if (observed['bootstrap'] != facts or observed['cni']['metadata']['uid'] != baseline['cni']['metadata']['uid']
                    or observed['cni']['spec'] != baseline['cni']['spec']
                    or observed['namespace']['metadata']['uid'] != baseline['namespace']['metadata']['uid']
                    or observed['namespace']['metadata'].get('labels') != baseline['namespace']['metadata'].get('labels')):
                raise StateError('F0 cluster, CNI or namespace enrollment changed across the experiment')
            if observed['policy']['metadata']['uid'] != baseline['policy']['metadata']['uid']:
                raise StateError('F0 policy was replaced rather than changed through its owned selector transition')
            for target_id in baseline['bundle']['targets']:
                before = baseline['bundle']['targets'][target_id]['after']['kubernetes']['observations']['image_snapshot']
                after = observed['bundle']['targets'][target_id]['after']['kubernetes']['observations']['image_snapshot']
                if (before['deployment_uid'] != after['deployment_uid'] or before['containers'] != after['containers']):
                    raise StateError('F0 workload, container image or restart identity changed')
                old_policies = baseline['bundle']['targets'][target_id]['after']['kubernetes']['observations']['network_policies']
                new_policies = observed['bundle']['targets'][target_id]['after']['kubernetes']['observations']['network_policies']
                def unrelated(rows):
                    return [{k: v for k, v in row.items() if k not in {'resource_version', 'generation'}}
                            for row in rows if ('networking.k8s.io/NetworkPolicy/'+row['namespace']+'/'+row['name']) != self._plan.resource_id]
                if unrelated(old_policies) != unrelated(new_policies):
                    raise StateError('An unrelated NetworkPolicy changed during the F0 experiment')
            def pod_specs(rows):
                return sorted([(p['metadata']['uid'], p['metadata'].get('labels', {}),
                                p['metadata'].get('annotations', {}), p['spec']) for p in rows], key=lambda row: row[0])
            if pod_specs(observed['pods']) != pod_specs(baseline['pods']):
                raise StateError('F0 target Pod configuration or enrollment changed')
        for earlier, later in zip(_STATES, _STATES[1:]):
            if self._observed[earlier]['bundle']['window']['end'] >= self._observed[later]['bundle']['window']['start']:
                raise StateError('F0 control windows are not strictly ordered')
        scope = {'run_id': context.run_id, 'identities': dict(context.identities),
            'cluster_uid': facts['kubernetes']['cluster_uid'], 'kubernetes_origin': facts['kubernetes']['origin'],
            'cni': baseline['bundle']['context_snapshot']['factory_lock']['platform']['cni'], 'mesh_mode': 'disabled',
            'probe_transport': 'plain-http-networkpolicy', 'source_path': self._plan.source_path,
            'source_commit': self._plan.source_commit, 'resource_id': self._plan.resource_id,
            'pointer': self._plan.pointer, 'before': self._plan.before, 'after': self._plan.after,
            'baseline_manifest_sha256': _sha(self._plan.baseline), 'fault_manifest_sha256': _sha(self._plan.fault),
            'baseline_spec_sha256': digest(_json(self._plan.baseline)['spec']),
            'fault_spec_sha256': digest(_json(self._plan.fault)['spec']),
            'observations': {state: digest({k: v for k, v in value.items() if k != 'bootstrap'})
                             for state, value in self._observed.items()}}
        scope['qualification_sha256'] = digest(scope)
        origin = QualifiedNetworkPolicyPath(_ISSUER)
        _QUALIFIED[origin] = {'pid': os.getpid(), 'owner': self, 'binding': scope}
        self._qualified = origin
        return origin


def _phase_checks(observed, state, plan, selection):
    """Pure semantic checks; never issues a qualification capability."""
    bundle = observed['bundle']
    intent = bundle['context_snapshot']['intent']
    if intent['attempt_id'] != 'f0-network-policy' or intent['phase'] != state:
        raise StateError('Incident or unrelated collections cannot qualify the pre-incident path')
    mission = next(t for t in intent['targets'] if 'mission' in t['roles'])
    target = bundle['targets'][mission['id']]
    metrics = target.get('prometheus') or {}
    scrape = metrics.get('observations', {}).get('scrape', {})
    samples = scrape.get('samples', [])
    required = bundle['context_snapshot']['factory_lock']['required_fresh_scrapes']
    window = bundle.get('window') or {}
    freshness = metrics.get('observations', {}).get('collection_freshness', {})
    if state == 'fault':
        # A blocked scrape legitimately removes/stales the application counter.
        # The independent up=0 source series must still be complete and fresh;
        # API denial, malformed samples, wrong labels and image gaps never pass.
        counter_absence = {'prometheus-missing-or-multiple-series', 'prometheus-missing-or-excessive-samples',
            'prometheus-insufficient-fresh-samples', 'prometheus-stale-source-sample',
            'prometheus-no-fresh-source-samples', 'prometheus-source-window-mismatch',
            'runtime-prometheus-source-time-unavailable', 'runtime-prometheus-stale-at-collection'}
        provider_complete = all(g['code'] in counter_absence for g in metrics.get('gaps', []))
        source_fresh = freshness.get('series', {}).get('up', {}).get('fresh') is True
    else:
        provider_complete = metrics.get('execution_status') == 'complete' and not metrics.get('gaps')
        source_fresh = freshness.get('decision') == 'pass'
    if (not provider_complete or not source_fresh or len(samples) < required
            or scrape.get('fresh_sample_count', 0) < required
            or any(s.get('value') != (0 if state == 'fault' else 1) for s in samples)
            or any(not window.get('start', float('inf')) <= s.get('source_timestamp', -1) <= window.get('end', -1) for s in samples)):
        raise StateError('F0 exact telemetry target/source samples are incomplete or have the wrong state')
    expected_scope = {k: mission['prometheus'][k] for k in ('namespace', 'service', 'job', 'instance')}
    if metrics.get('scope') != expected_scope:
        raise StateError('F0 telemetry observation selected a different target')
    if state != 'fault' and metrics.get('observations', {}).get('functional', {}).get('progressing') is not True:
        raise StateError('F0 mission functional counter did not progress')
    records = {}
    for receipt in observed['receipts'].values():
        request = receipt['request']
        kind = request['case_id'].removeprefix(state+'-')
        if (request['attempt_id'] != 'f0-network-policy' or request['phase'] != state
                or request['identities'] != intent['identities'] or request['run_id'] != intent['run_id']
                or request['destination']['scheme'] != 'http'
                or not window['start'] <= parse_time(receipt['started_at']).timestamp()
                    <= parse_time(receipt['finished_at']).timestamp() <= window['end']):
            raise StateError('F0 probe belongs to a different run, phase or transport')
        if kind in records:
            raise StateError('Duplicate F0 control kind')
        source = next(s for s in observed['sources'] if s['uid'] == request['source']['expected']['uid'])
        records[kind] = (receipt, source)
    wanted = {'mission-health', 'reference-health'}
    if state != 'fault':
        wanted |= {'metrics-allowed-before', 'metrics-allowed-after', 'metrics-denied-wrong-namespace', 'metrics-denied-wrong-label'}
    if set(records) != wanted:
        raise StateError('F0 probe control matrix is incomplete')
    for kind, (receipt, source) in records.items():
        observation = receipt['observation']
        if kind.startswith('metrics-denied'):
            if observation['status'] is not None or observation['outcome'] not in {'connect-timeout', 'connection-refused'}:
                raise StateError('F0 denied source reached the metrics listener')
        elif observation['http_decision'] != 'pass' or observation['status'] != 200 or receipt['execution_status'] != 'complete':
            raise StateError('F0 required health or listening control failed')
        if kind.startswith('metrics-'):
            ns = all(source['namespace_labels'].get(k) == v for k, v in selection['namespace_labels'].items())
            pod = all(source['pod_labels'].get(k) == v for k, v in selection['pod_labels'].items())
            expected = (False, True) if kind.endswith('wrong-namespace') else (True, False) if kind.endswith('wrong-label') else (True, True)
            if (ns, pod) != expected or receipt['request']['destination']['port'] != plan.metrics_port:
                raise StateError('F0 source labels or metrics port differ from the tested policy')
    if state != 'fault':
        before, after = records['metrics-allowed-before'], records['metrics-allowed-after']
        negatives = [records['metrics-denied-wrong-namespace'], records['metrics-denied-wrong-label']]
        if (before[1]['uid'] != after[1]['uid'] or len({before[1]['uid'], *(n[1]['uid'] for n in negatives)}) != 3
                or negatives[0][1]['namespace_uid'] == before[1]['namespace_uid']
                or negatives[1][1]['namespace_uid'] != before[1]['namespace_uid']
                or not all(parse_time(before[0]['finished_at']) < parse_time(n[0]['started_at'])
                           <= parse_time(n[0]['finished_at']) < parse_time(after[0]['started_at']) for n in negatives)):
            raise StateError('F0 positive listening controls do not bracket both denied sources')


def require_qualified_network_policy_path(origin, *, runtime, run_id, identities):
    record = _QUALIFIED.get(origin) if type(origin) is QualifiedNetworkPolicyPath else None
    if (record is None or record['pid'] != os.getpid() or record['owner']._qualified is not origin
            or record['owner'].runtime is not runtime or record['binding']['run_id'] != run_id
            or record['binding']['identities'] != dict(identities)):
        raise StateError('An original independent same-run F0 path qualification is required')
    for state in _STATES:
        current = runtime._retained('f0-network-policy', state)
        if clock() >= parse_time(current['context']['factory_config']['expires_at']):
            raise StateError('F0 environment authorization has expired')
    return MappingProxyType(deepcopy(record['binding']))
