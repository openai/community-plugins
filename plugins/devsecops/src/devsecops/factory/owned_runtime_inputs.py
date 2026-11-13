"""Fixed canonical runtime selection, original delivery intents and probe actors.

Selection is inert and contains no credentials or fabricated live identifiers.
The coordinator obtains UIDs, addresses and revisions from the original owned
bootstrap and merged Git origins. No saved receipt can recreate this producer.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import os
from pathlib import Path
import re
import time
from uuid import uuid4

import yaml
from devsecops.resources import _StrictLoader

from devsecops.adapters.argocd import _digest as spec_digest
from devsecops.adapters.vulnerability_policy import checkpoint
from devsecops.profiles import relative_path
from devsecops.runtime.context import digest, validate, INTENT_SCHEMA
from devsecops.runtime.cli import _new_directory, _write
from .contracts import NAME, DIGEST, array, object_schema
from .state import StateError, _json, _read, _sha
from .owned_runtime_transport import READ_TRANSPORT_SCHEMA, validate_read_transport

_LABELS = {'type': 'object', 'minProperties': 1, 'maxProperties': 32,
           'propertyNames': {'type': 'string', 'pattern': '^[A-Za-z0-9./_-]{1,253}$'},
           'additionalProperties': {'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$'}}
_PATH = {'type': 'string', 'minLength': 1, 'maxLength': 1024}
_PORT = {'type': 'integer', 'minimum': 1, 'maximum': 65535}


class _NotConverged(StateError):
    """Only explicit observed convergence failures may receive bounded GETs."""

_PEER = object_schema({'namespace': NAME, 'pod_labels': _LABELS})
_SOURCE = object_schema({'position': {'type': 'integer', 'minimum': 1, 'maximum': 32},
                         'path': _PATH, 'renderer': {'enum': ['helm', 'kustomize', 'manifests', 'values']}})
_TARGET = object_schema({'id': NAME, 'role': {'enum': ['mission', 'reference-web']},
    'namespace': NAME, 'deployment': NAME, 'service': NAME, 'container': NAME,
    'health': object_schema({'port': _PORT, 'path': {'type': 'string', 'pattern': '^/[^?#\\s]*$', 'maxLength': 1024},
                            'body_sha256': DIGEST}),
    'argocd': object_schema({'namespace': NAME, 'application': NAME, 'project': NAME,
                             'sources': array(_SOURCE, minimum=1, maximum=32)})})
RUNTIME_RECIPE_SCHEMA = object_schema({'schema_version': {'const': 1},
    'kind': {'const': 'owned-runtime-selection'},
    'identities': object_schema({key: DIGEST for key in ('scenario_sha256', 'assertions_sha256',
        'package_sha256', 'lock_sha256', 'config_sha256', 'environment_sha256')}),
    'environment_id': NAME, 'targets': array(_TARGET, minimum=2, maximum=2),
    'delivery_source_position': {'type': 'integer', 'minimum': 1, 'maximum': 32},
    'read_transport': READ_TRANSPORT_SCHEMA,
    'prometheus': object_schema({'namespace': NAME, 'service': NAME, 'api_port': _PORT,
        'job': {'type': 'string', 'pattern': '^[A-Za-z0-9_.:/-]{1,128}$'},
        'port': _PORT, 'instance': {'const': 'single-ready-pod-ip'}}),
    'probes': object_schema({key: _PEER for key in ('health', 'metrics', 'wrong_namespace', 'wrong_label')}),
    'cluster_domain': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9.-]{0,252}$'},
    'policy': object_schema({'source_kind': {'enum': ['manifest', 'helm-values']}, 'source_path': _PATH,
        'source_pointer': {'type': 'string', 'pattern': '^/[^\\s]+$', 'maxLength': 1024},
        'resource_id': {'type': 'string', 'pattern': '^networking.k8s.io/NetworkPolicy/[a-z0-9-]+/[a-z0-9.-]+$'}}),
    'cni': object_schema({'kind': {'enum': ['calico', 'cilium', 'canal']},
        'namespace': {'const': 'kube-system'}, 'daemonset': NAME, 'spec_sha256': DIGEST,
        'required_containers': array(NAME, minimum=1, maximum=8)}),
    'timing': object_schema({'lead_seconds': {'type': 'integer', 'minimum': 5, 'maximum': 120},
        'request_seconds': {'type': 'number', 'minimum': .1, 'maximum': 5},
        'gap_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 10}})})


def bind_runtime_recipe(raw, *, identities, environment_id):
    """Pure closed parser used before any production effect or secret lookup."""
    if type(raw) is not bytes or len(raw) > 64000:
        raise StateError('Runtime selection must be bounded exact JSON bytes')
    value = _json(raw)
    validate(value, RUNTIME_RECIPE_SCHEMA)
    if value['identities'] != dict(identities) or value['environment_id'] != environment_id:
        raise StateError('Runtime selection differs from the frozen run identities')
    validate_read_transport(value['read_transport'])
    selected_prom = value['read_transport']['services']['prometheus']
    if (selected_prom['namespace'], selected_prom['name'], selected_prom['port']) != (
            value['prometheus']['namespace'], value['prometheus']['service'], value['prometheus']['api_port']):
        raise StateError('Prometheus read transport differs from the explicit runtime selection')
    targets = value['targets']
    if {t['role'] for t in targets} != {'mission', 'reference-web'} or len({t['id'] for t in targets}) != 2:
        raise StateError('Exactly distinct mission and reference targets are required')
    if len({(t['namespace'], t['deployment']) for t in targets}) != 2 or len({(t['namespace'], t['service']) for t in targets}) != 2:
        raise StateError('Canonical runtime targets may not overlap')
    for target in targets:
        sources = target['argocd']['sources']
        if [s['position'] for s in sources] != list(range(1, len(sources)+1)):
            raise StateError('Runtime source positions must completely cover the desired mapping')
        for source in sources:
            relative_path(source['path'])
    mission = next(t for t in targets if t['role'] == 'mission')
    if value['delivery_source_position'] not in {s['position'] for s in mission['argocd']['sources']}:
        raise StateError('Delivery source position must be explicitly selected from the mission mapping')
    relative_path(value['policy']['source_path'])
    peers = value['probes']
    if (peers['wrong_namespace']['namespace'] == peers['metrics']['namespace']
            or peers['wrong_namespace']['pod_labels'] != peers['metrics']['pod_labels']
            or peers['wrong_label']['namespace'] != peers['metrics']['namespace']
            or all(peers['wrong_label']['pod_labels'].get(k) == v for k, v in peers['metrics']['pod_labels'].items())):
        raise StateError('Negative probe controls must independently vary namespace and Pod labels')
    cni = value['cni']
    if cni['daemonset'] != {'calico': 'calico-node', 'cilium': 'cilium', 'canal': 'rke2-canal'}[cni['kind']]:
        raise StateError('CNI selection is outside the fixed controller route')
    if len(set(cni['required_containers'])) != len(cni['required_containers']):
        raise StateError('CNI container selection must be unique')
    return deepcopy(value)


def validate_recipe_inputs(recipe, config, lock):
    """Reject infeasible scope/schedule before any initial CI or cluster write."""
    services = {service['id']: service for service in config['services']}
    if not {'kubernetes', 'argocd', 'prometheus'} <= set(services):
        raise StateError('Canonical runtime requires all three explicitly scoped read providers')
    for name in ('kubernetes', 'argocd', 'prometheus'):
        service = services[name]
        if service['read_auth'] != {'mode': 'none'} or service.get('ca_file'):
            raise StateError('Derived owned reads cannot silently replace configured static credentials or CA')
    kube = services['kubernetes']['scope']
    namespaces = {t['namespace'] for t in recipe['targets']} | {p['namespace'] for p in recipe['probes'].values()} | {'kube-system'}
    applications = {t[k] for t in recipe['targets'] for k in ('service', 'deployment')}
    if not namespaces <= set(kube.get('namespaces', [])) or not applications <= set(kube.get('applications', [])):
        raise StateError('Explicit Kubernetes read scope does not cover selected target/probe/CNI resources')
    argo = services['argocd']['scope']
    if (not {t['argocd']['namespace'] for t in recipe['targets']} <= set(argo.get('namespaces', []))
            or not {t['argocd']['project'] for t in recipe['targets']} <= set(argo.get('argocd_projects', []))):
        raise StateError('Explicit Argo read scope does not cover the selected applications')
    mission = next(t for t in recipe['targets'] if t['role'] == 'mission')
    prometheus = services['prometheus']['scope']
    if mission['namespace'] not in prometheus.get('namespaces', []) or mission['service'] not in prometheus.get('applications', []):
        raise StateError('Explicit Prometheus read scope does not cover the exact mission target')
    timing = recipe['timing']
    needed = timing['lead_seconds']+5*(timing['request_seconds']+timing['gap_seconds'])+timing['request_seconds']
    if needed >= lock['observation_window_seconds']:
        raise StateError('Selected probe schedule exceeds the locked observation window')
    if lock['platform']['cni'] != recipe['cni']['kind'] or lock['platform']['mesh_mode'] != 'disabled':
        raise StateError('Selected platform lacks the explicit implemented independent F0 route')
    return True


def _pointer(value, pointer):
    current = value
    for part in pointer.split('/')[1:]:
        if re.search(r'~(?![01])', part):
            raise StateError('Policy source pointer has an invalid escape')
        key = part.replace('~1', '/').replace('~0', '~')
        if type(current) is not dict or key not in current:
            raise StateError('Selected policy scalar is absent from the actual Git source')
        current = current[key]
    if type(current) is not str:
        raise StateError('Policy source must select one explicit string scalar')
    return current


def _identity(value, *, namespace, name, kind):
    metadata = value.get('metadata', {}) if type(value) is dict else {}
    if (type(value) is not dict or value.get('kind') != kind or metadata.get('name') != name
            or namespace is not None and metadata.get('namespace') != namespace
            or not metadata.get('uid') or not metadata.get('resourceVersion')):
        raise StateError('Actual Kubernetes identity is incomplete or differs from the selection')
    return metadata


def _observed_target(selected, *, application, deployment, service, pods, delivery, image_mapping):
    """Pure shape/identity validation; no authority is issued from this helper."""
    argo, ns = selected['argocd'], selected['namespace']
    _identity(application, namespace=argo['namespace'], name=argo['application'], kind='Application')
    dep = _identity(deployment, namespace=ns, name=selected['deployment'], kind='Deployment')
    svc = _identity(service, namespace=ns, name=selected['service'], kind='Service')
    spec, status = application.get('spec', {}), application.get('status', {})
    sources = spec.get('sources') or ([spec['source']] if spec.get('source') else [])
    wanted = argo['sources']
    revisions = status.get('sync', {}).get('revisions') if spec.get('sources') else [status.get('sync', {}).get('revision')]
    if (spec.get('project', 'default') != argo['project'] or len(sources) != len(wanted)
            or spec.get('destination', {}).get('namespace') != ns
            or not spec.get('destination', {}).get('server')
            or spec.get('destination', {}).get('name')):
        raise StateError('Selected Argo source or destination differs from the explicit mapping')
    if (revisions != [delivery['watched_revision']]*len(wanted)
            or status.get('sync', {}).get('status') != 'Synced'
            or status.get('health', {}).get('status') != 'Healthy'):
        raise _NotConverged('Selected Argo actual merged revision has not converged')
    resources = status.get('resources', [])
    for kind, name in [('Deployment', selected['deployment']), ('Service', selected['service'])]:
        rows = [r for r in resources if r.get('kind') == kind and r.get('namespace') == ns and r.get('name') == name]
        if len(rows) != 1:
            raise _NotConverged('Selected workload is absent from the actual Argo resource inventory')
    intent_sources = []
    for row, expected in zip(sources, wanted, strict=True):
        if (row.get('repoURL') != delivery['repository_url'] or row.get('path', '') != expected['path']
                or row.get('chart') or row.get('plugin') or spec.get('sourceHydrator')):
            raise StateError('Desired Argo source differs from the explicit owned Git mapping')
        intent_sources.append({**expected, 'repository_url': delivery['repository_url'],
            'revision': delivery['watched_revision'], 'source_spec_sha256': spec_digest(row),
            'rendered_artifact_sha256': delivery['rendered_sha256']})
    replicas = deployment.get('spec', {}).get('replicas', 1)
    if replicas != 1 or dep.get('generation', 0) < 1:
        raise StateError('Canonical workload must explicitly select one replica')
    if (deployment.get('status', {}).get('observedGeneration') != dep['generation']
            or deployment['status'].get('availableReplicas') != replicas):
        raise _NotConverged('Canonical exact single-Pod workload is not ready at its desired generation')
    selector = deployment['spec'].get('selector', {}).get('matchLabels', {})
    matches = [p for p in pods if all(p.get('metadata', {}).get('labels', {}).get(k) == v for k, v in selector.items())]
    if not selector or len(matches) > 1:
        raise StateError('Canonical telemetry requires one unambiguous observed workload Pod')
    if not matches:
        raise _NotConverged('Selected workload Pod is not yet observed')
    pod = matches[0]
    _identity(pod, namespace=ns, name=pod['metadata']['name'], kind='Pod')
    if not any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod.get('status', {}).get('conditions', [])):
        raise _NotConverged('Observed workload Pod is not ready')
    app = [c for c in pod.get('spec', {}).get('containers', []) if c.get('name') == selected['container']]
    if len(app) != 1 or len(pod.get('spec', {}).get('containers', [])) != 1:
        raise StateError('Canonical application container selection is ambiguous')
    cluster_ips = service.get('spec', {}).get('clusterIPs', [])
    if not cluster_ips or any(not ipaddress.ip_address(ip) for ip in cluster_ips):
        raise StateError('Observed Service has no actual ClusterIP destination')
    target = {'id': selected['id'], 'roles': [selected['role']],
        'kubernetes': {'service_id': 'kubernetes', 'namespace': ns, 'deployment': selected['deployment'],
            'service': selected['service'], 'deployment_uid': dep['uid'], 'generation': dep['generation'], 'service_uid': svc['uid']},
        'argocd': {'service_id': 'argocd', 'project': argo['project'], 'application_namespace': argo['namespace'],
            'application': argo['application'], 'sources': intent_sources, 'destination': deepcopy(spec['destination']),
            'destination_spec_sha256': spec_digest(spec['destination'])},
        'image_mapping_receipt': image_mapping}
    return target, pod


def _probe_requests(recipe, *, intent, window, services, image, suffix):
    """Generate the exact fourteen-case matrix across canonical phase windows."""
    phase, attempt = intent['phase'], intent['attempt_id']
    by_role = {t['role']: t for t in recipe['targets']}
    rows = [('mission-health', 'mission', 'health'), ('reference-health', 'reference-web', 'health')]
    if phase != 'fault':
        rows += [('metrics-allowed-before', 'mission', 'metrics'),
                 ('metrics-denied-wrong-namespace', 'mission', 'wrong_namespace'),
                 ('metrics-denied-wrong-label', 'mission', 'wrong_label'),
                 ('metrics-allowed-after', 'mission', 'metrics')]
    timing = recipe['timing']
    stride = timing['request_seconds']+timing['gap_seconds']
    first = window['start']+timing['lead_seconds']
    if first+(len(rows)-1)*stride+timing['request_seconds'] >= window['end']:
        raise StateError('Fixed probe schedule cannot fit the locked immutable observation window')
    groups = {}
    for index, (kind, role, peer) in enumerate(rows):
        selected = by_role[role]
        is_health = kind.endswith('health')
        destination = selected['health'] if is_health else {'port': recipe['prometheus']['port'], 'path': '/metrics', 'body_sha256': None}
        actor = recipe['probes'][peer]
        source_id = attempt+'-'+phase+'-'+peer.replace('_', '-')+'-'+suffix
        name = 'probe-'+digest(source_id)[:32]
        service = services[selected['id']]
        req = {'schema_version': 1, 'kind': 'http-probe-request', 'run_id': intent['run_id'], 'attempt_id': attempt,
            'case_id': phase+'-'+kind, 'phase': phase, 'identities': deepcopy(intent['identities']),
            'source': {'expected': {'namespace': actor['namespace'], 'name': name, 'uid': 'pending'},
                'environment': {'namespace': 'DEVSECOPS_PROBE_POD_NAMESPACE', 'name': 'DEVSECOPS_PROBE_POD_NAME', 'uid': 'DEVSECOPS_PROBE_POD_UID'}},
            'destination': {'scheme': 'http', 'namespace': selected['namespace'], 'service': selected['service'],
                'cluster_domain': recipe['cluster_domain'], 'port': destination['port'], 'path': destination['path'],
                'expected_addresses': service['spec']['clusterIPs']},
            'expectation': {'status': 200, 'body_sha256': destination['body_sha256']},
            'limits': {'dns_seconds': timing['request_seconds'], 'connect_seconds': timing['request_seconds'],
                'header_seconds': timing['request_seconds'], 'body_seconds': timing['request_seconds'],
                'total_seconds': timing['request_seconds'], 'body_bytes': 65536}}
        group = groups.setdefault(peer, {'source_id': source_id, 'namespace': actor['namespace'], 'name': name,
            'image': image, 'pod_labels': deepcopy(actor['pod_labels']), 'requests': [],
            'not_after': datetime.fromtimestamp(window['end'], timezone.utc).isoformat(), 'services': services})
        group['requests'].append({'request': req, 'run_at': datetime.fromtimestamp(first+index*stride, timezone.utc).isoformat()})
    return list(groups.values())


class OwnedRuntimeInputCoordinator:
    """The sole concrete operator-intent producer for the canonical runtime."""
    def __init__(self, operations, *, runtime, bootstrap):
        from .owned_operations import OwnedOperationCoordinator
        from .runtime_observations import RuntimeObservationSession
        from .canonical_bootstrap import OwnedBootstrapCoordinator
        if (type(operations) is not OwnedOperationCoordinator or type(runtime) is not RuntimeObservationSession
                or type(bootstrap) is not OwnedBootstrapCoordinator or operations.journal is not runtime.driver.journal
                or operations.bootstrap is not bootstrap or operations.runtime is not runtime):
            raise StateError('Runtime preparation requires original same-journal concrete producers')
        self.operations, self.runtime, self.bootstrap, self.journal = operations, runtime, bootstrap, operations.journal
        self._pid = os.getpid()
        self._selected = operations.selected_component('runtime')
        self.recipe = bind_runtime_recipe(self._selected[2], identities=self.journal.identities,
                                          environment_id=self.journal.state['environment_id'])
        inputs = self.journal.state['lifecycle']['inputs']
        documents = {}
        for name in ('config', 'lock'):
            ref = inputs[name]
            raw = _read(self.journal._artifact_path(ref['path']), 64000)
            if _sha(raw) != ref['sha256'] or len(raw) != ref['bytes']:
                raise StateError('Canonical runtime input selection changed before preparation')
            documents[name] = _json(raw)
        validate_recipe_inputs(self.recipe, documents['config'], documents['lock'])
        from .owned_runtime_transport import OwnedRuntimeTransport
        self._read_transport = OwnedRuntimeTransport(bootstrap, journal=self.journal, selection=self.recipe['read_transport'])
        self._records, self._policy, self._experiment = {}, None, None
        self._f0_evidence = []

    def _admit(self, context):
        if self._pid != os.getpid() or self.operations.selected_component('runtime') != self._selected:
            raise StateError('Runtime producer process or selected bytes changed')
        if _read(self._selected[0], 64000) != self._selected[2]:
            raise StateError('Runtime selection file changed')
        if self.recipe != bind_runtime_recipe(self._selected[2], identities=self.journal.identities,
                                              environment_id=self.journal.state['environment_id']):
            raise StateError('Original runtime selection was changed in memory')
        self.operations._admit(context)
        return self.runtime._admit(context)

    def transport_selection(self, context):
        self._admit(context)
        return deepcopy(self.recipe['prometheus'])

    def bind_transport(self, context, frozen, *, authority):
        self._admit(context)
        return self._read_transport.bind(context, frozen, authority=authority)

    def refresh_images(self, context):
        self._admit(context)
        self.runtime.bind_runtime_image(context, self.operations.runtime_image(context))
        self.runtime.bind_locked_images(context, self.operations.locked_images(context))

    def _delivery(self, attempt, phase):
        from .owned_git_delivery import require_delivery
        origin = self.operations.delivery(attempt, phase)
        value = require_delivery(origin, journal=self.journal)
        if (value['run_id'] != self.journal.state['run_id'] or value['attempt'] != attempt
                or value['delivery_stage'] != 'merged-watched-branch'
                or value['watched_revision'] != value['deployment_commit']):
            raise StateError('Runtime intent requires original completed exact watched-branch delivery')
        return origin, value

    def _snapshot(self, context, *, attempt, phase):
        deadline = min(time.monotonic()+60, context.deadline-125)
        while True:
            try:
                return self._snapshot_once(context, attempt=attempt, phase=phase)
            except _NotConverged:
                if time.monotonic()+.25 >= deadline:
                    raise
                checkpoint(context.deadline)
                time.sleep(.25)

    def _snapshot_once(self, context, *, attempt, phase):
        from .canonical_bootstrap import require_bootstrap_authority
        authority = self._admit(context)
        facts = require_bootstrap_authority(authority, self.journal)
        origin, delivery = self._delivery(attempt, phase)
        kube = self.bootstrap.kubernetes(context)
        mission = self.runtime.mission_image_binding()
        locked = self.runtime.locked_image_binding(context)
        rendered_path = Path(delivery['rendered_path'])
        rendered_raw = _read(rendered_path, 16_000_000)
        if _sha(rendered_raw) != delivery['rendered_sha256']:
            raise StateError('Original delivered rendered artifact changed')
        targets, services, observed, mappings = [], {}, {}, {}
        for selected in self.recipe['targets']:
            ns, argo = selected['namespace'], selected['argocd']
            if ns not in facts['kubernetes']['owned_namespaces']:
                raise StateError('Selected workload namespace is not originally owned')
            namespace = kube.read_owned(context, {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns}})
            app = kube.read_owned(context, {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
                'metadata': {'namespace': argo['namespace'], 'name': argo['application']}})
            deployment = kube.session.request('GET', '/apis/apps/v1/namespaces/'+ns+'/deployments/'+selected['deployment'], deadline=context.deadline)
            service = kube.session.request('GET', '/api/v1/namespaces/'+ns+'/services/'+selected['service'], deadline=context.deadline)
            # Bounded, explicitly scoped list. The pure target validator requires
            # exactly one selected Ready Pod; it never chooses a convenient one.
            pods = kube.session.request('GET', '/api/v1/namespaces/'+ns+'/pods', deadline=context.deadline)
            mapping = mission if selected['role'] == 'mission' else locked['reference']
            mapping_path = Path(mapping['mapping_receipt_path'])
            mapping_raw = _read(mapping_path, 16_000_000)
            if _sha(mapping_raw) != mapping['mapping_receipt_sha256']:
                raise StateError('Original image mapping bytes changed')
            mapping_id = selected['id']+'-image-mapping'
            mappings[mapping_id] = mapping_path
            target, pod = _observed_target(selected, application=app, deployment=deployment, service=service,
                pods=pods.get('items', []), delivery=delivery,
                image_mapping={'id': mapping_id, 'sha256': _sha(mapping_raw)})
            if selected['role'] == 'mission':
                ip = str(ipaddress.ip_address(pod['status']['podIP']))
                instance = ('['+ip+']' if ':' in ip else ip)+':'+str(self.recipe['prometheus']['port'])
                target['prometheus'] = {'service_id': 'prometheus', 'namespace': ns, 'service': selected['service'],
                    'job': self.recipe['prometheus']['job'], 'instance': instance}
            targets.append(target)
            services[selected['id']] = service
            observed[selected['id']] = {'namespace': namespace, 'application': app, 'deployment': deployment, 'service': service, 'pod': pod}
        return {'authority': authority, 'facts': facts, 'delivery_origin': origin, 'delivery': delivery,
            'targets': targets, 'services': services, 'observed': observed, 'mappings': mappings,
            'rendered_path': rendered_path, 'rendered_raw': rendered_raw}

    def _save(self, context, prefix, documents):
        directory = context.run_dir/'runtime-inputs'/(prefix+'-'+str(uuid4()))
        with _new_directory(directory) as fd:
            for name, document in documents.items():
                _write(fd, name+'.json', document)
        return {name: directory/(name+'.json') for name in documents}

    def _intent(self, context, *, attempt, phase, snapshot):
        journal = self.journal
        intent = {'schema_version': 1, 'run_id': context.run_id, 'attempt_id': attempt, 'phase': phase,
            'environment': journal.state['environment_id'], 'profile': context.inputs['config']['profile'],
            'scenario_id': context.inputs['lock']['scenario_id'], 'identities': dict(context.identities),
            'targets': deepcopy(snapshot['targets'])}
        # This receipt is explicitly a projection of the original merged Git
        # capability. It is never the origin or an independent trust grant.
        projection = {'kind': 'original-owned-runtime-intent', 'run_id': context.run_id, 'attempt_id': attempt,
            'phase': phase, 'selection_sha256': self._selected[1], 'delivery': snapshot['delivery'],
            'targets': deepcopy(snapshot['targets']), 'cluster_uid': snapshot['facts']['kubernetes']['cluster_uid']}
        paths = self._save(context, attempt+'-'+phase, {'intent-origin': projection})
        receipt = {'id': 'runtime-intent-origin', 'sha256': _sha(_read(paths['intent-origin'], 16_000_000))}
        for target in intent['targets']:
            target['argocd']['intent_receipt'] = receipt
            for source in target['argocd']['sources']:
                source['intent_receipt'] = receipt
        validate(intent, INTENT_SCHEMA)
        intent_path = self._save(context, attempt+'-'+phase, {'intent': intent})['intent']
        receipts = dict(snapshot['mappings']) | {'runtime-intent-origin': paths['intent-origin']}
        return intent, intent_path, receipts

    def _begin(self, context, *, attempt, phase, snapshot, experiment=None):
        key = (attempt, phase)
        if key in self._records:
            raise StateError('Runtime input phase already began; reconcile instead of repeating actors')
        intent, path, receipts = self._intent(context, attempt=attempt, phase=phase, snapshot=snapshot)
        delivery_path = None
        chain = None
        if phase == 'recovery' and attempt != 'f0-network-policy':
            chain = self._recovery_chain(context, snapshot)
            delivery_path = chain['delivery']
        if experiment is not None:
            experiment.start_observation(context, state=phase, intent=path, intent_sha256=_sha(_read(path, 1_000_000)))
        selection = self.runtime.begin(context, intent=path, intent_sha256=_sha(_read(path, 1_000_000)),
                                      receipt_documents=receipts, delivery_receipt=delivery_path)
        if selection.window is None:
            raise StateError('Actual runtime collectors could not establish the immutable observation window')
        image = self.runtime.mission_image_binding()
        reference = image['origin'].removeprefix('https://')+'/'+image['repository']+'@'+image['manifest_digest']
        plans = []
        kube = self.bootstrap.kubernetes(context)
        for actor in _probe_requests(self.recipe, intent=intent, window=selection.window, services=snapshot['services'],
                                     image=reference, suffix=uuid4().hex[:8]):
            plan = self.runtime.prepare_probe_actor(context, attempt=attempt, phase=phase, **actor)
            kube.create_probe(context, runtime=self.runtime, plan=plan, publication=self.operations.runtime_image(context))
            plans.append(plan)
        self.runtime.start_probes(context, attempt=attempt, phase=phase, plans=plans)
        pins = {path: _read(path, 16_000_000) for path in [path, *receipts.values(), snapshot['rendered_path']]}
        if delivery_path is not None:
            pins.update({p: _read(p, 16_000_000) for p in chain.values()})
        self._records[key] = {'intent': intent, 'path': path, 'snapshot': snapshot, 'pins': pins,
            'delivery_path': delivery_path, 'chain': chain, 'created_at': datetime.now(timezone.utc).isoformat()}
        self.require_intent(context, attempt=attempt, phase=phase)
        return selection

    def require_intent(self, context, *, attempt, phase):
        from .owned_git_delivery import require_delivery
        self._admit(context)
        record = self._records.get((attempt, phase))
        if record is None:
            raise StateError('Original production intent does not exist')
        snapshot = record['snapshot']
        if require_delivery(snapshot['delivery_origin'], journal=self.journal) != snapshot['delivery']:
            raise StateError('Original runtime Git delivery changed')
        for path, raw in record['pins'].items():
            if _read(path, 16_000_000) != raw:
                raise StateError('Original runtime intent or source projection changed')
        if self.bootstrap.require_authority(context) is not snapshot['authority']:
            raise StateError('Original runtime cluster authority changed')
        return deepcopy(record['intent'])

    def require_cni_selection(self, context):
        self._admit(context)
        if context.inputs['lock']['platform']['cni'] != self.recipe['cni']['kind']:
            raise StateError('Selected CNI controller differs from the factory lock')
        return deepcopy(self.recipe['cni'])

    def _prepare_policy(self, context, snapshot):
        from .network_policy_qualification import NetworkPolicyExperiment
        if self._policy is not None:
            raise StateError('Original independent policy preparation already exists')
        selection = self.recipe['policy']
        rendered = _json(snapshot['rendered_raw'])
        matches = [r['body'] for r in rendered.get('resources', []) if r.get('id') == selection['resource_id']]
        if len(matches) != 1:
            raise StateError('Selected policy is absent or duplicated in the actual delivered render')
        policy = matches[0]
        source = self.operations.source_checkout('primary', 'baseline')
        if source['revision'] != snapshot['delivery']['watched_revision']:
            raise StateError('Policy source checkout differs from the observed merged revision')
        path = Path(source['repository'])/selection['source_path']
        if path.resolve() != path or not path.is_relative_to(Path(source['repository'])):
            raise StateError('Policy source cannot traverse symlinks or leave the original checkout')
        raw = _read(path, 1_000_000)
        documents = list(yaml.load_all(raw, Loader=_StrictLoader))
        if len(documents) != 1 or type(documents[0]) is not dict:
            raise StateError('Policy source requires one explicit YAML/JSON document')
        if selection['source_kind'] == 'manifest' and documents[0] != policy:
            raise StateError('Selected raw source does not equal the actual rendered policy')
        saved = self._save(context, 'f0-source', {'baseline-policy': policy})['baseline-policy']
        self._f0_evidence.append(saved)
        experiment = NetworkPolicyExperiment(self.runtime, bootstrap=self.bootstrap)
        peer = self.recipe['probes']['metrics']
        plan = experiment.prepare(context, baseline_manifest=saved, manifest_sha256=_sha(_read(saved, 1_000_000)),
            source_path=selection['source_path'], source_commit=source['revision'],
            metrics_port=self.recipe['prometheus']['port'],
            namespace_labels={'kubernetes.io/metadata.name': peer['namespace']}, pod_labels=peer['pod_labels'])
        if _pointer(documents[0], selection['source_pointer']) != plan.before:
            raise StateError('Explicit policy source pointer differs from the qualified rendered selector')
        self._policy = {'snapshot': snapshot, 'source': source, 'source_path': path,
            'source_raw': raw, 'plan': plan, 'selection': deepcopy(selection)}
        self._experiment = experiment
        self.require_policy_target(context)
        return plan

    def require_policy_target(self, context):
        from .owned_git_delivery import require_delivery
        self._admit(context)
        record = self._policy
        if record is None:
            raise StateError('Original policy preparation is missing')
        snapshot, plan = record['snapshot'], record['plan']
        if (self.operations.source_checkout('primary', 'baseline') != record['source']
                or _read(record['source_path'], 1_000_000) != record['source_raw']
                or require_delivery(snapshot['delivery_origin'], journal=self.journal) != snapshot['delivery']):
            raise StateError('Original policy Git source changed')
        selected = next(t for t in self.recipe['targets'] if t['role'] == 'mission')
        prior = snapshot['observed'][selected['id']]
        kube = self.bootstrap.kubernetes(context)
        app = kube.read_owned(context, {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
            'metadata': {'namespace': selected['argocd']['namespace'], 'name': selected['argocd']['application']}})
        if app.get('spec', {}).get('syncPolicy', {}).get('automated', {}).get('selfHeal', False) is not False:
            raise StateError('Direct independent policy experiment requires explicitly non-self-healing Argo ownership')
        if app['metadata']['uid'] != prior['application']['metadata']['uid'] or app['spec'] != prior['application']['spec']:
            raise StateError('Original Argo policy ownership changed')
        sync = app.get('status', {}).get('sync', {})
        revisions = sync.get('revisions') if app['spec'].get('sources') else [sync.get('revision')]
        if revisions != [snapshot['delivery']['watched_revision']]*len(selected['argocd']['sources']):
            raise StateError('Actual Argo resolved revisions changed during the independent policy experiment')
        namespace = kube.read_owned(context, {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': selected['namespace']}})
        if namespace['metadata']['uid'] != prior['namespace']['metadata']['uid']:
            raise StateError('Original policy namespace changed')
        _, _, ns, name = plan.resource_id.split('/')
        rows = [r for r in app.get('status', {}).get('resources', [])
                if r.get('kind') == 'NetworkPolicy' and r.get('namespace') == ns and r.get('name') == name]
        if len(rows) != 1:
            raise StateError('Policy is absent from the actual owned Argo resource inventory')
        route = '/apis/networking.k8s.io/v1/namespaces/'+ns+'/networkpolicies/'+name
        policy = kube.session.request('GET', route, deadline=context.deadline)
        _identity(policy, namespace=ns, name=name, kind='NetworkPolicy')
        baseline, fault = _json(plan.baseline), _json(plan.fault)
        if policy['spec'] not in (baseline['spec'], fault['spec']):
            raise StateError('Actual policy differs from both exact selected experiment states')
        if 'uid' not in record:
            if policy['spec'] != baseline['spec']:
                raise StateError('Independent policy experiment did not start from original baseline')
            record['uid'] = policy['metadata']['uid']
        if policy['metadata']['uid'] != record['uid']:
            raise StateError('Original experiment policy was replaced')
        return {'run_id': context.run_id, 'identities': dict(context.identities),
            'cluster_uid': snapshot['facts']['kubernetes']['cluster_uid'], 'origin': snapshot['facts']['kubernetes']['origin'],
            'namespace': ns, 'namespace_uid': namespace['metadata']['uid'],
            'application': {'namespace': app['metadata']['namespace'], 'name': app['metadata']['name'], 'uid': app['metadata']['uid'],
                'source_revisions': list(revisions),
                'destination': deepcopy(app['spec']['destination']), 'self_heal': False},
            'delivery': deepcopy(snapshot['delivery']), 'policy': policy, 'policy_path': route,
            'baseline_spec_sha256': digest(baseline['spec']), 'fault_spec_sha256': digest(fault['spec'])}

    def qualified_policy_plan(self, context):
        from .network_policy_qualification import require_qualified_network_policy_path
        self._admit(context)
        if self._policy is None:
            raise StateError('Original independent policy source is absent')
        qualification = require_qualified_network_policy_path(self.runtime._network_policy_path,
            runtime=self.runtime, run_id=context.run_id, identities=dict(context.identities))
        # Reaudit original source bytes; current live policy may legitimately
        # be in its later incident/recovery state, so this accessor performs no
        # mutation and does not require the old controller revision to persist.
        if (self.operations.source_checkout('primary', 'baseline') != self._policy['source']
                or _read(self._policy['source_path'], 1_000_000) != self._policy['source_raw']):
            raise StateError('Qualified original policy source changed')
        return dict(self._policy['selection'], plan=self._policy['plan'], qualification=qualification,
                    render_resource_id=self._policy['selection']['resource_id'])

    def prepare(self, context):
        from .lifecycle import PhaseOutcome
        self._admit(context)
        operation = context.phase['operation']
        if operation == 'probe':
            # Actors and their immutable window were admitted by delivery.
            # This fixed route only finishes reads/logs and prepares the report.
            return self.runtime.dispatch(context)
        phase = {'build-promote': 'baseline', 'reset': 'baseline', 'inject': 'fault', 'deliver': 'recovery'}.get(operation)
        if phase is None:
            raise StateError('Runtime preparation requires its fixed journaled mutation phase')
        attempt = 'primary' if operation == 'build-promote' else 'replay' if operation == 'reset' else context.phase['attempt']
        self.runtime.bind_input_producer(context, self)
        self.refresh_images(context)
        snapshot = self._snapshot(context, attempt=attempt, phase=phase)
        if operation == 'build-promote':
            # The snapshot above has independently observed the actual merged
            # revision, Synced/Healthy Application and ready workload. Up only
            # admitted that original Application; close its deferred child
            # inventory now, before F0 mutations or baseline actors can start.
            inventory = self.bootstrap.capture_controller_inventory(context)
            if inventory.get('captured') is not True or inventory.get('pending_application_inventory'):
                raise StateError('First ready delivery requires complete original controller inventory')
            self.require_cni_selection(context)
            plan = self._prepare_policy(context, snapshot)
            for state in ('baseline', 'fault', 'recovery'):
                manifest = self._experiment.require_transition(plan, context, state=state)
                if state != 'baseline':
                    target = self.require_policy_target(context)
                    self.bootstrap.kubernetes(context).replace_network_policy(context,
                        original_uid=target['policy']['metadata']['uid'],
                        expected_spec_sha256=digest(target['policy']['spec']), manifest=manifest, runtime_inputs=self)
                self._begin(context, attempt='f0-network-policy', phase=state, snapshot=snapshot, experiment=self._experiment)
                self.runtime.finish_experiment(context, phase=state)
                self.runtime.finish_experiment_probes(context, phase=state)
                self._experiment.observe(context, state=state)
                observed = {key: value for key, value in self._experiment._observed[state].items() if key != 'bootstrap'}
                self._f0_evidence.extend(self._save(context, 'f0-'+state, {'f0-observation': observed}).values())
            self.runtime.bind_network_policy_path(context, self._experiment.qualify(context))
            self._f0_evidence.extend(self._save(context, 'f0-qualified',
                {'f0-qualification': dict(self.runtime.qualified_fault(context))}).values())
            # Re-read actual objects after restoration; pre-experiment snapshots
            # never masquerade as the later canonical baseline observation.
            snapshot = self._snapshot(context, attempt=attempt, phase=phase)
        self.runtime.bind_input_producer(context, self)
        self._begin(context, attempt=attempt, phase=phase, snapshot=snapshot)
        keys = [('f0-network-policy', item) for item in ('baseline', 'fault', 'recovery')] if operation == 'build-promote' else []
        keys.append((attempt, phase))
        artifacts = self.phase_artifacts(context, keys=keys)
        return PhaseOutcome('completed', 'canonical-runtime-actors-observed',
                            artifacts, mutation_effect='applied')

    def phase_artifacts(self, context, *, keys):
        """Explicit full original file closure; never just embedded path strings."""
        self._admit(context)
        result = {self._selected[0]}
        for attempt, phase in keys:
            self.require_intent(context, attempt=attempt, phase=phase)
            result.update(self._records[(attempt, phase)]['pins'])
            result.update(self.runtime._retained(attempt, phase)['pins'])
        if any(attempt == 'f0-network-policy' for attempt, _ in keys):
            result.update(self._f0_evidence)
            result.add(self._policy['source_path'])
        return tuple(sorted(result, key=str))

    def _recovery_chain(self, context, snapshot):
        from .correction_phase import require_validated_correction
        from .native_factory_session import NativeFactorySession
        correction = require_validated_correction(self.runtime.driver, context, attempt=context.phase['attempt'])
        session = getattr(self.runtime.driver, '_native_session', None)
        if type(session) is not NativeFactorySession or session.origin(attempt=context.phase['attempt']) is not correction['origin']:
            raise StateError('Recovery requires the original successful native correction session')
        artifacts = {name: value[0] for name, value in correction['artifacts'].items()}
        validated = _json(_read(artifacts['correction'], 16_000_000))
        delivered = snapshot['delivery']
        if (delivered.get('selected_tree') != validated['patched_tree']
                or delivered['rendered_sha256'] != _sha(_read(artifacts['rendered_after'], 16_000_000))
                or not delivered.get('observed_at')):
            raise StateError('Actual merged Git tree/render/readback is not bound to the original validated correction')
        native = dict(correction['native_artifacts'])
        if set(native) != {'native_request', 'native_result', 'native_validation', 'native_execution', 'patch'}:
            raise StateError('Original successful correction native artifacts are incomplete')
        directory = context.run_dir/'runtime-inputs'/('native-chain-'+str(uuid4()))
        with _new_directory(directory) as fd:
            for name, raw in native.items():
                if name == 'patch':
                    if _read(artifacts['patch'], 16_000_000) != raw:
                        raise StateError('Original native patch differs from local validated patch')
                    continue
                filename = name+'.json'
                descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                artifacts[name] = directory/filename
        mission = next(t for t in snapshot['targets'] if 'mission' in t['roles'])
        source = next(s for s in mission['argocd']['sources'] if s['position'] == self.recipe['delivery_source_position'])
        common = {'schema_version': 1, 'run_id': context.run_id, 'attempt_id': context.phase['attempt'],
                  'environment': self.journal.state['environment_id'], 'identities': dict(context.identities)}
        delivery = {**common, 'kind': 'runtime-delivery-binding', 'target_id': mission['id'],
            'source_position': source['position'], 'delivered_revision': delivered['watched_revision'],
            'delivered_at': delivered['observed_at'], 'native_handoff_sha256': _sha(native['native_request']),
            'patch_sha256': _sha(native['patch']), 'candidate_tree_sha256': delivered['selected_tree']['sha256'],
            'validation_receipt_sha256': _sha(_read(artifacts['correction'], 16_000_000))}
        git = {**common, 'kind': 'runtime-git-delivery', 'repository_url': delivered['repository_url'],
            'revision': delivered['watched_revision'], 'source_path': source['path'],
            'selected_tree': delivered['selected_tree'], 'rendered_artifact_sha256': delivered['rendered_sha256'],
            'observed_at': delivered['observed_at']}
        artifacts.update(self._save(context, 'recovery-chain', {'delivery': delivery, 'git_delivery': git}))
        return artifacts

    def complete_recovery_inputs(self, context):
        """After read-only recovery collection, prepare exact native report input."""
        self._admit(context)
        self.refresh_images(context)
        attempt = context.phase['attempt']
        if context.phase['operation'] not in {'probe', 'native'}:
            raise StateError('Recovery input completion requires the measured recovery/report phase')
        self.require_intent(context, attempt=attempt, phase='recovery')
        record = self._records[(attempt, 'recovery')]
        chain = dict(record['chain'])
        inventory = self.runtime.prepare_inventory(context)
        chain['inventory'] = inventory['inventory'][0]
        peer = self.recipe['probes']['metrics']
        return self.runtime.prepare_recovery(context, chain_artifacts=chain,
            image_expectations=inventory['image_expectations'], application_containers=inventory['application_containers'],
            metrics_authorization={'namespace_labels': {'kubernetes.io/metadata.name': peer['namespace']},
                                   'pod_labels': deepcopy(peer['pod_labels'])})

    def verify_recovery_origins(self, context, *, attempt, artifacts, intents):
        """Closed runtime adapter entry point; original objects remain mandatory."""
        for phase in ('baseline', 'fault', 'recovery'):
            if self.require_intent(context, attempt=attempt, phase=phase) != intents[phase]:
                raise StateError('Recovery intent differs from its original operator producer')
        record = self._records[(attempt, 'recovery')]
        delivered = record['snapshot']['delivery']
        delivery, git = _json(artifacts['delivery']), _json(artifacts['git_delivery'])
        if (artifacts['delivery'] != record['pins'][record['delivery_path']]
                or git['revision'] != delivered['watched_revision']
                or git['repository_url'] != delivered['repository_url']
                or git['selected_tree'] != delivered['selected_tree']
                or git['rendered_artifact_sha256'] != delivered['rendered_sha256']
                or git['observed_at'] != delivered['observed_at']
                or delivery['delivered_at'] != delivered['observed_at']):
            raise StateError('Recovery Git delivery differs from original merged readback')
        return ('operator-intent', 'git-delivery')
