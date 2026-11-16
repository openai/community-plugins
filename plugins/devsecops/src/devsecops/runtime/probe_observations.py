"""Fixed scheduled probe actors and bounded read-only Kubernetes observations.

Plan construction has no external effects. An authorized provision/inject/probe
handler must create the Pod and immutable ConfigMap and retain their UIDs.
These observers perform only exact GETs; they never create, exec or attach.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import math
import time
from weakref import WeakKeyDictionary

from devsecops.adapters.http import HTTPSReadClient
from devsecops.factory.state import StateError, _json
from .context import canonical_bytes, clock, digest, file_hash, parse_time, validate, verify_runtime_authority, verify_seal

_ISSUER = object()
_OBSERVED = WeakKeyDictionary()
_NAME = re.compile(r'[a-z][a-z0-9-]{0,62}\Z')
_IMAGE = re.compile(r'[a-z0-9][a-z0-9.:/-]+@sha256:[a-f0-9]{64}\Z')
_ENV = {'namespace': 'DEVSECOPS_PROBE_POD_NAMESPACE', 'name': 'DEVSECOPS_PROBE_POD_NAME',
        'uid': 'DEVSECOPS_PROBE_POD_UID'}
_PYTHON = '/opt/mission-status/venv/bin/python'
_RUNNER = '''"""Fixed canonical scheduled probe batch; no commands from input."""
import importlib.util
import json
import os
import time
from datetime import datetime, timezone

def epoch(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.timestamp()

with open("/actor/batch.json", "rb") as stream:
    batch = json.load(stream)
spec = importlib.util.spec_from_file_location("fixed_probe", "/opt/mission-status/probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
deadline = epoch(batch["not_after"])
monotonic_deadline = time.monotonic() + max(0, deadline - time.time())
for item in batch["requests"]:
    scheduled = epoch(item["run_at"])
    while time.time() < scheduled:
        if time.monotonic() >= monotonic_deadline or time.time() >= deadline:
            raise SystemExit(2)
        time.sleep(min(0.1, max(0, scheduled-time.time())))
    request = item["request"]
    if time.time() + request["limits"]["total_seconds"] > deadline:
        raise SystemExit(2)
    request["source"]["expected"]["uid"] = os.environ["DEVSECOPS_PROBE_POD_UID"]
    print(json.dumps(probe.probe(request), sort_keys=True, separators=(",", ":")), flush=True)
'''


@dataclass(frozen=True)
class ProbeActorPlan:
    """Inert data, not a permission or observation certificate."""
    pod: bytes
    configmap: bytes
    source_id: str
    target_services: tuple[tuple[str, str, str, str, tuple[str, ...]], ...]


class ProbeObservation:
    __slots__ = ('__weakref__',)

    def __new__(cls, issuer=None):
        if issuer is not _ISSUER:
            raise StateError('Probe observations require direct Kubernetes reads')
        return super().__new__(cls)

    def __reduce__(self):
        raise StateError('Probe observations cannot be serialized or restored')


def prepare_probe_actor(context, *, source_id, namespace, name, image, pod_labels,
                        requests, not_after, services):
    """Build one bounded actor with exact request schedules and fixed code.

    Requests use ``uid='pending'`` until the fixed runner substitutes its own
    DownwardAPI UID. All other fields are immutable. ``services`` maps target ID
    to an independently observed Service document; this data alone grants no
    destination/image/network-policy assurance.
    """
    intent = context['intent']
    if (not all(_NAME.fullmatch(v or '') for v in (source_id, namespace, name))
            or not _IMAGE.fullmatch(image or '') or type(pod_labels) is not dict
            or not 1 <= len(requests) <= 6 or len(name) > 57):
        raise StateError('Probe actor scope or image pin is invalid')
    request_schema = json.loads((Path(__file__).resolve().parents[3]/'schemas/http-probe-request.schema.json').read_bytes())
    planned, destinations, previous = [], {}, None
    end = parse_time(not_after)
    for item in requests:
        if set(item) != {'request', 'run_at'}:
            raise StateError('Probe batch accepts only exact scheduled requests')
        request = deepcopy(item['request'])
        validate(request, request_schema)
        when = parse_time(item['run_at'])
        if (request['run_id'] != intent['run_id'] or request['attempt_id'] != intent['attempt_id']
                or request['phase'] != intent['phase'] or request['identities'] != intent['identities']
                or request['source'] != {'expected': {'namespace': namespace, 'name': name, 'uid': 'pending'},
                                          'environment': _ENV}
                or request['destination']['scheme'] != 'http'
                or not 0 < (end-when).total_seconds() <= 300
                or when.timestamp()+request['limits']['total_seconds'] > end.timestamp()
                or (previous is not None and when.timestamp() < previous)):
            raise StateError('Probe request scope or immutable schedule is invalid')
        destination = request['destination']
        targets = [t for t in intent['targets'] if (t['kubernetes']['namespace'], t['kubernetes']['service'])
                   == (destination['namespace'], destination['service'])]
        if len(targets) != 1 or targets[0]['id'] not in services:
            raise StateError('Probe destination is outside the frozen intent')
        target = targets[0]
        service = services[target['id']]
        ips = service.get('spec', {}).get('clusterIPs', [])
        if (service.get('metadata', {}).get('namespace') != destination['namespace']
                or service['metadata'].get('name') != destination['service']
                or not service['metadata'].get('uid') or not 1 <= len(ips) <= 2
                or set(ips) != set(destination['expected_addresses'])
                or not any(p.get('port') == destination['port'] and p.get('protocol', 'TCP') == 'TCP'
                           for p in service['spec'].get('ports', []))):
            raise StateError('Probe destination differs from its explicit observed Service')
        for address in ips:
            ipaddress.ip_address(address)
        destinations[target['id']] = (destination['namespace'], destination['service'], service['metadata']['uid'], tuple(ips))
        planned.append({'request': request, 'run_at': when.isoformat()})
        previous = when.timestamp()+request['limits']['total_seconds']
    if len({p['request']['case_id'] for p in planned}) != len(planned):
        raise StateError('Probe case IDs must be unique')
    batch = {'schema_version': 1, 'requests': planned, 'not_after': end.isoformat()}
    labels = deepcopy(pod_labels)
    labels['devsecops.openai.com/run'] = intent['run_id']
    labels['devsecops.openai.com/attempt'] = intent['attempt_id']
    config_name = name+'-batch'
    config = {'apiVersion': 'v1', 'kind': 'ConfigMap',
              'metadata': {'name': config_name, 'namespace': namespace, 'labels': labels}, 'immutable': True,
              'data': {'runner.py': _RUNNER, 'batch.json': canonical_bytes(batch).decode()}}
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': namespace, 'labels': labels},
        'spec': {'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'activeDeadlineSeconds': 360, 'terminationGracePeriodSeconds': 1,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 10001, 'runAsGroup': 10001,
                                'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'probe', 'image': image, 'imagePullPolicy': 'IfNotPresent',
                'command': [_PYTHON, '/actor/runner.py'],
                'env': [{'name': value, 'valueFrom': {'fieldRef': {'apiVersion': 'v1', 'fieldPath': 'metadata.'+key}}}
                        for key, value in _ENV.items()],
                'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                    'capabilities': {'drop': ['ALL']}},
                'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '100m', 'memory': '128Mi'}},
                'volumeMounts': [{'name': 'actor', 'mountPath': '/actor', 'readOnly': True}]}],
            'volumes': [{'name': 'actor', 'configMap': {'name': config_name, 'defaultMode': 0o444}}]}}
    return ProbeActorPlan(canonical_bytes(pod), canonical_bytes(config), source_id,
                          tuple((key, *value) for key, value in sorted(destinations.items())))


def validate_probe_plan(plan, context):
    """Regenerate the fixed manifest; reject extra executable or resource fields."""
    pod, cm, batch = _validate_plan(plan)
    services = {}
    for target, namespace, name, uid, ips in plan.target_services:
        ports = sorted({item['request']['destination']['port'] for item in batch['requests']
                        if item['request']['destination']['namespace'] == namespace
                        and item['request']['destination']['service'] == name})
        services[target] = {'metadata': {'namespace': namespace, 'name': name, 'uid': uid},
                            'spec': {'clusterIPs': list(ips), 'ports': [{'port': p} for p in ports]}}
    labels = deepcopy(pod['metadata']['labels'])
    for key in ('devsecops.openai.com/run', 'devsecops.openai.com/attempt'):
        labels.pop(key, None)
    expected = prepare_probe_actor(context, source_id=plan.source_id,
        namespace=pod['metadata']['namespace'], name=pod['metadata']['name'],
        image=pod['spec']['containers'][0]['image'], pod_labels=labels,
        requests=batch['requests'], not_after=batch['not_after'], services=services)
    if expected != plan:
        raise StateError('Probe actor plan is not the exact fixed generated manifest')
    return pod, cm


def _validate_plan(plan):
    if type(plan) is not ProbeActorPlan:
        raise StateError('Only a fixed probe actor plan is accepted')
    pod, cm = _json(plan.pod), _json(plan.configmap)
    if (len(plan.pod)+len(plan.configmap) > 256_000 or cm.get('immutable') is not True
            or cm.get('data', {}).get('runner.py') != _RUNNER
            or pod.get('spec', {}).get('containers', [{}])[0].get('command') != [_PYTHON, '/actor/runner.py']):
        raise StateError('Probe actor code or manifest changed')
    return pod, cm, _json(cm['data']['batch.json'].encode())


def _project(actual, expected):
    """Compare selected fixed fields while allowing harmless API defaults."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _project(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(_project(a, e) for a, e in zip(actual, expected))
    return actual == expected


def _readback(plan, pod, cm, namespace, services):
    wanted, wanted_cm, batch = _validate_plan(plan)
    spec = pod.get('spec', {})
    if (not _project(pod, wanted) or not _project(cm, wanted_cm)
            or spec.get('hostNetwork', False) or spec.get('hostPID', False) or spec.get('hostIPC', False)
            or spec.get('initContainers') or spec.get('ephemeralContainers')
            or any(k in spec['containers'][0] for k in ('args', 'envFrom', 'lifecycle', 'workingDir'))
            or pod.get('metadata', {}).get('deletionTimestamp') is not None
            or cm.get('metadata', {}).get('deletionTimestamp') is not None
            or namespace.get('metadata', {}).get('name') != wanted['metadata']['namespace']):
        raise StateError('Observed probe actor differs from its fixed plan')
    statuses = pod.get('status', {}).get('containerStatuses', [])
    if (len(statuses) != 1 or statuses[0].get('name') != 'probe' or statuses[0].get('restartCount') != 0
            or not re.fullmatch(r'(?:[a-z][a-z0-9+.-]*://)?(?:[A-Za-z0-9._:/-]+@)?sha256:[a-f0-9]{64}', statuses[0].get('imageID', ''))):
        raise StateError('Probe container identity or uninterrupted execution is unavailable')
    for target, ns, name, uid, ips in plan.target_services:
        item = services[target]
        if (item['metadata'].get('namespace') != ns or item['metadata'].get('name') != name
                or item['metadata'].get('uid') != uid or tuple(item['spec'].get('clusterIPs', [])) != ips):
            raise StateError('Probe destination Service changed')
    metadata = pod['metadata']
    for obj in (pod, cm, namespace):
        if not isinstance(obj.get('metadata', {}).get('uid'), str) or not obj['metadata']['uid']:
            raise StateError('Probe readback has no stable object UID')
    return {'id': plan.source_id, 'namespace': metadata['namespace'], 'name': metadata['name'], 'uid': metadata['uid'],
        'namespace_uid': namespace['metadata']['uid'], 'pod_labels': metadata.get('labels', {}),
        'namespace_labels': namespace['metadata'].get('labels', {}), 'image_id': statuses[0]['imageID'],
        'configmap_uid': cm['metadata']['uid'], 'pod_annotations': metadata.get('annotations', {}), 'batch': batch}


def _client(context, authority, *, deadline=None, transport=None):
    verify_runtime_authority(context, authority)
    service = next(s for s in context['factory_config']['services'] if s['id'] == 'kubernetes')
    if transport is not None:
        from devsecops.factory.owned_runtime_transport import require_bound_runtime_read
        require_bound_runtime_read(transport, context)
        budget = 40 if deadline is None else min(40, deadline-time.monotonic())
        if budget <= .1:
            raise StateError('Original runtime read transport budget exhausted')
        return transport.client('kubernetes', budget=budget), service
    auth = service['read_auth']
    if auth['mode'] not in {'none', 'bearer'}:
        raise StateError('Probe observation requires scoped Kubernetes read authentication')
    if auth.get('token_env') in [v for k, v in service.get('operator_auth', {}).items() if k.endswith('_env')]:
        raise StateError('Read and operator Kubernetes identities must be separate')
    ca = context['ca_artifacts'].get('kubernetes', {})
    if ca.get('state') == 'unreadable' or (ca.get('state') == 'frozen' and file_hash(service['ca_file']) != ca['sha256']):
        raise StateError('Probe observation CA identity changed')
    if clock() >= parse_time(context['factory_config']['expires_at']):
        raise StateError('Probe observation environment authorization expired')
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise StateError('Probe observation deadline is invalid')
    budget = 40 if deadline is None else min(40, deadline-time.monotonic())
    if budget <= 0.1:
        raise StateError('Probe observation deadline exhausted')
    return HTTPSReadClient(service['origin'], token_env=auth.get('token_env'), ca_bundle=service.get('ca_file'),
        timeout=min(5, budget), total_timeout=budget, max_requests=16, max_body_bytes=2_000_000, max_total_bytes=8_000_000), service


def _read_actor(plan, client, scope, *, await_image=False):
    pod, cm, _ = _validate_plan(plan)
    ns, name = pod['metadata']['namespace'], pod['metadata']['name']
    if ns not in scope.get('namespaces', []):
        raise StateError('Probe namespace is outside the Kubernetes read scope')
    for _, target_ns, service, _, _ in plan.target_services:
        if target_ns not in scope.get('namespaces', []) or service not in scope.get('applications', []):
            raise StateError('Probe target is outside the Kubernetes read scope')
    actual_pod = client.get_json('/api/v1/namespaces/'+ns+'/pods/'+name)
    if await_image:
        first = min(parse_time(item['run_at']).timestamp() for item in _validate_plan(plan)[2]['requests'])
        # An admitted actor may still be waiting for the scheduler/image pull.
        # Only missing imageID in a genuinely pending Pod gets bounded GETs.
        # The original observed UID cannot change, and the schedule never moves.
        uid = actual_pod.get('metadata', {}).get('uid')
        for _ in range(60):
            status = actual_pod.get('status', {})
            containers = status.get('containerStatuses', [])
            if any(c.get('imageID') for c in containers) or status.get('phase') not in {'Pending', None}:
                break
            if (not uid or actual_pod.get('metadata', {}).get('uid') != uid
                    or clock().timestamp()+.25 >= first):
                raise StateError('Scheduled probe actor was not ready before its immutable first request')
            time.sleep(.25)
            actual_pod = client.get_json('/api/v1/namespaces/'+ns+'/pods/'+name)
        if actual_pod.get('metadata', {}).get('uid') != uid:
            raise StateError('Probe Pod was replaced during pending observation')
    actual_cm = client.get_json('/api/v1/namespaces/'+ns+'/configmaps/'+cm['metadata']['name'])
    actual_ns = client.get_json('/api/v1/namespaces/'+ns)
    services = {target: client.get_json('/api/v1/namespaces/'+target_ns+'/services/'+service)
                for target, target_ns, service, _, _ in plan.target_services}
    return _readback(plan, actual_pod, actual_cm, actual_ns, services)


def start_probe_observation(context, *, authority, plan, deadline=None, transport=None):
    """GET and pin already-created actors before their first scheduled probe."""
    validate_probe_plan(plan, context)
    client, service = _client(context, authority, deadline=deadline, transport=transport)
    value = _read_actor(plan, client, service['scope'], await_image=True)
    before = clock()
    if before >= min(parse_time(item['run_at']) for item in value['batch']['requests']):
        raise StateError('Probe actor was not observed before its first scheduled request')
    origin = ProbeObservation(_ISSUER)
    _OBSERVED[origin] = {'pid': os.getpid(), 'context': deepcopy(context), 'authority': dict(authority),
        'plan': plan, 'source': value, 'before': before, 'receipts': None, 'transports': [transport] if transport is not None else []}
    return origin


def finish_probe_observation(origin, *, deadline=None, transport=None):
    """Read exact Pod logs and independently recheck actors and Services."""
    if type(origin) is not ProbeObservation or origin not in _OBSERVED or _OBSERVED[origin]['pid'] != os.getpid():
        raise StateError('An original process-local probe observation is required')
    issued = _OBSERVED[origin]
    if issued['receipts'] is not None:
        raise StateError('Probe observation is already complete')
    if bool(issued['transports']) != (transport is not None):
        raise StateError('Probe finish cannot replace its original transport mode')
    client, service = _client(issued['context'], issued['authority'], deadline=deadline, transport=transport)
    source = issued['source']
    last = max(parse_time(item['run_at']).timestamp()+item['request']['limits']['total_seconds']
               for item in source['batch']['requests'])
    if clock().timestamp() < last:
        raise StateError('Probe batch is not yet eligible for final observation')
    prefix = '/api/v1/namespaces/'+source['namespace']+'/pods/'+source['name']
    response = client.get_bytes(prefix+'/log', {'container': 'probe', 'timestamps': 'false', 'limitBytes': 256000},
                                accept=('text/plain', 'application/octet-stream'))
    after = _read_actor(issued['plan'], client, service['scope'])
    if after != source:
        raise StateError('Probe identity or destination changed across its requests')
    receipts = parse_probe_logs(response.body, source=source, observed_before=issued['before'], observed_after=clock())
    issued['receipts'] = receipts
    issued['after'] = clock()
    if transport is not None:
        issued['transports'].append(transport)
    return deepcopy(receipts)


def parse_probe_logs(raw, *, source, observed_before, observed_after):
    """Pure bounded validation; this function alone issues no origin."""
    if not isinstance(raw, bytes) or not 1 <= len(raw) < 256000:
        raise StateError('Probe logs are missing or truncated at the read bound')
    lines = raw.splitlines()
    expected = source['batch']['requests']
    if len(lines) != len(expected):
        raise StateError('Probe logs are incomplete or contain unexpected output')
    receipts = {}
    schema = json.loads((Path(__file__).resolve().parents[3]/'schemas/http-probe-receipt.schema.json').read_bytes())
    for line, item in zip(lines, expected):
        receipt = _json(line)
        validate(receipt, schema)
        verify_seal(receipt, 'receipt_sha256')
        request = deepcopy(item['request'])
        request['source']['expected']['uid'] = source['uid']
        identity = {key: source[key] for key in ('namespace', 'name', 'uid')}
        start, end = parse_time(receipt['started_at']), parse_time(receipt['finished_at'])
        if (receipt['request'] != request or receipt['execution_mode'] != 'operator'
                or receipt['request_artifact']['canonical_sha256'] != digest(request)
                or receipt['source_identity']['claims'] != identity or not receipt['source_identity']['matched']
                or not observed_before <= start <= end <= observed_after
                or start < parse_time(item['run_at']) or end > parse_time(source['batch']['not_after'])
                or (end-start).total_seconds() > request['limits']['total_seconds']+1):
            raise StateError('Probe logs do not match the original actor, schedule and requests')
        receipts['http:'+request['case_id']] = receipt
    return receipts


def probe_observation_artifacts(origin):
    """Original observation only; identity/receipts cannot be restored from JSON."""
    if type(origin) is not ProbeObservation or origin not in _OBSERVED or _OBSERVED[origin]['pid'] != os.getpid():
        raise StateError('An original process-local probe observation is required')
    value = _OBSERVED[origin]
    if value['receipts'] is None:
        raise StateError('Probe observation is incomplete')
    verify_runtime_authority(value['context'], value['authority'])
    source = {k: deepcopy(v) for k, v in value['source'].items() if k not in {'batch', 'configmap_uid', 'pod_annotations'}}
    source.update(observed_before=value['before'].isoformat(), observed_after=value['after'].isoformat())
    return source, deepcopy(value['receipts'])


def probe_observation_transport(origin):
    """Original observed enrollment data, not a transport qualification claim."""
    probe_observation_artifacts(origin)
    source = _OBSERVED[origin]['source']
    return {'namespace_labels': deepcopy(source['namespace_labels']),
            'pod_annotations': deepcopy(source['pod_annotations'])}


def verify_probe_read_origins(origin, *, current_context):
    """Reaudit original acquisition history without renewing old read tokens."""
    probe_observation_artifacts(origin)
    value = _OBSERVED[origin]
    from devsecops.factory.owned_runtime_transport import require_runtime_read_history
    transports = value.get('transports', [])
    if len(transports) != 2:
        raise StateError('Original before/after owned probe read transports are unavailable')
    bindings = [require_runtime_read_history(item, value['context'], current_context=current_context) for item in transports]
    if any((b['actual_origin'], b['cluster_uid']) != (bindings[0]['actual_origin'], bindings[0]['cluster_uid']) for b in bindings):
        raise StateError('Probe read transport cluster changed during observation')
    return bindings


def probe_observation_destinations(origin):
    """Exact independently bracketed Service identities from original GETs."""
    probe_observation_artifacts(origin)
    value = _OBSERVED[origin]
    rows = []
    for target, namespace, name, uid, addresses in value['plan'].target_services:
        domains = {item['request']['destination']['cluster_domain'] for item in value['source']['batch']['requests']
            if (item['request']['destination']['namespace'], item['request']['destination']['service']) == (namespace, name)}
        if len(domains) != 1:
            raise StateError('Probe destination cluster domain is ambiguous')
        rows.append({'target_id': target, 'uid': uid, 'cluster_ips': list(addresses), 'cluster_domain': next(iter(domains)),
                     'observed_before': value['before'].isoformat(), 'observed_after': value['after'].isoformat()})
    return rows
