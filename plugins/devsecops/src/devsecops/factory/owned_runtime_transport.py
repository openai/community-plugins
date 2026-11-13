"""Original owned-cluster read routing without replacing canonical inputs.

The selected service origins remain logical operator scope. Actual reads use a
separate original read-only ServiceAccount, observed cluster CA/endpoint, and
closed Kubernetes routes. Portable bindings never recreate this capability.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
import json
from pathlib import Path
import re
import ssl
import time

from devsecops.adapters.http import APIError, HTTPSReadClient, _bearer, _json as http_json
from devsecops.runtime.context import canonical_bytes, verify_runtime_authority
from .contracts import NAME, object_schema
from .state import StateError, _json, _read, _sha

READ_TRANSPORT_SCHEMA = object_schema({
    'kind': {'const': 'owned-kubernetes-read-v1'},
    'services': object_schema({name: object_schema({
        'namespace': NAME, 'name': NAME,
        'port': {'type': 'integer', 'minimum': 1, 'maximum': 65535},
        'scheme': {'enum': ['http', 'https']},
    }) for name in ('argocd', 'prometheus')}),
})
_NAME = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'


def validate_read_transport(value):
    from jsonschema import Draft202012Validator
    if not Draft202012Validator(READ_TRANSPORT_SCHEMA).is_valid(value):
        raise StateError('Owned read transport requires explicit closed service selectors')
    return deepcopy(value)


class OwnedRuntimeTransport:
    def __init__(self, bootstrap, *, journal, selection):
        from .canonical_bootstrap import OwnedBootstrapCoordinator
        if type(bootstrap) is not OwnedBootstrapCoordinator or bootstrap.journal is not journal:
            raise StateError('Runtime read transport requires its original bootstrap')
        self.bootstrap, self.journal, self.pid = bootstrap, journal, os.getpid()
        self.selection = validate_read_transport(selection)
        self.identities, self.run_id = dict(journal.identities), journal.state['run_id']
        self._selected()

    def __reduce__(self):
        raise StateError('Runtime read authority cannot be restored from evidence')

    def _selected(self):
        ref = self.journal.state['lifecycle']['auxiliary_inputs'].get('owned_runtime_recipe')
        if ref is None:
            raise StateError('Runtime read selection was not frozen before execution')
        raw = _read(self.journal._artifact_path(ref['path']), 64000)
        if (_sha(raw) != ref['sha256'] or len(raw) != ref['bytes']
                or _json(raw).get('read_transport') != self.selection):
            raise StateError('Original runtime read selection changed')

    def bind(self, phase_context, frozen, *, authority):
        self._selected()
        return _BoundRuntimeRead(self, phase_context, frozen, authority)


class _BoundRuntimeRead:
    def __init__(self, owner, phase, frozen, authority):
        self.owner, self.phase = owner, phase
        self.frozen, self.authority = deepcopy(frozen), dict(authority)
        self._bootstrap = owner.bootstrap.require_authority(phase)
        self._reader = owner.bootstrap.runtime_reader(phase)
        for service in self.frozen['factory_config']['services']:
            if service['id'] in ('kubernetes', 'argocd', 'prometheus') and (
                    service['read_auth'] != {'mode': 'none'} or 'ca_file' in service):
                raise StateError('Owned logical read scope must explicitly delegate transport identity to the original bootstrap')
        self._observed_reads = 0
        self._initial_binding = self.describe()

    def __reduce__(self):
        raise StateError('Runtime read binding is process-local')

    def _admit(self, deadline=None):
        from .canonical_bootstrap import require_bootstrap_authority
        from .owned_runtime_reader import require_runtime_reader
        owner, context = self.owner, self.phase
        if deadline is not None:
            context = replace(context, deadline=min(context.deadline, deadline))
        if (owner.pid != os.getpid() or owner.journal.identities != owner.identities
                or owner.journal.state['run_id'] != owner.run_id or context.remaining_seconds() <= 0
                or self.frozen['intent']['run_id'] != owner.run_id
                or self.frozen['intent']['identities'] != owner.identities):
            raise StateError('Runtime read binding differs from the active original run')
        owner._selected()
        verify_runtime_authority(self.frozen, self.authority)
        if owner.bootstrap.require_authority(context) is not self._bootstrap:
            raise StateError('Runtime bootstrap was replaced')
        facts = require_bootstrap_authority(self._bootstrap, owner.journal)
        reader = require_runtime_reader(self._reader, journal=owner.journal, bootstrap_authority=self._bootstrap)
        if reader['role'] != 'runtime' or reader['origin'] != facts['kubernetes']['origin']:
            raise StateError('Runtime reader differs from the original owned cluster')
        return reader, facts

    def describe(self):
        reader, facts = self._admit()
        return {'kind': 'original-owned-runtime-read-binding', 'run_id': self.owner.run_id,
            'identities': dict(self.owner.identities), 'cluster_uid': facts['kubernetes']['cluster_uid'],
            'actual_origin': reader['origin'], 'ca_sha256': reader['ca_sha256'],
            'serviceaccount_uid': reader['serviceaccount_uid'], 'expires_at': reader['expires_at'],
            'selection_sha256': _sha(canonical_bytes(self.owner.selection)),
            'context_sha256': self.frozen['context_sha256'], 'authority': 'original-process-only'}

    def client(self, provider, target=None, *, budget=20):
        if provider not in ('kubernetes', 'argocd', 'prometheus'):
            raise APIError('service-not-configured')
        if target is not None and target not in self.frozen['intent']['targets']:
            raise APIError('scope-not-authorized')
        reader, _ = self._admit()
        remaining = min(float(budget), self.phase.remaining_seconds(), 120)
        if remaining <= 0:
            raise APIError('phase-budget-exhausted')
        return _OwnedReadClient(self, provider, target, reader, remaining)


def require_bound_runtime_read(value, frozen):
    if type(value) is not _BoundRuntimeRead or value.frozen != frozen:
        raise StateError('Original runtime read binding for these exact inputs is required')
    return value.describe()


def require_runtime_read_history(value, frozen, *, current_context):
    """Validate original completed read identity; never issue a fresh client."""
    from .canonical_bootstrap import require_bootstrap_authority
    from .owned_runtime_reader import require_runtime_reader_history
    if type(value) is not _BoundRuntimeRead or value.frozen != frozen or value._observed_reads <= 0:
        raise StateError('Original observed runtime read binding required')
    owner = value.owner
    if (owner.pid != os.getpid() or owner.journal.identities != owner.identities
            or owner.journal.state['run_id'] != owner.run_id):
        raise StateError('Historical runtime read belongs to another process or run')
    owner._selected()
    verify_runtime_authority(frozen, value.authority)
    if owner.bootstrap.require_authority(current_context) is not value._bootstrap:
        raise StateError('Historical runtime bootstrap identity changed')
    facts = require_bootstrap_authority(value._bootstrap, owner.journal)
    reader = require_runtime_reader_history(value._reader, journal=owner.journal, bootstrap_authority=value._bootstrap)
    expected = value._initial_binding
    if (reader['role'] != 'runtime' or reader['origin'] != expected['actual_origin']
            or reader['ca_sha256'] != expected['ca_sha256']
            or reader['serviceaccount_uid'] != expected['serviceaccount_uid']
            or reader['expires_at'] != expected['expires_at']
            or facts['kubernetes']['cluster_uid'] != expected['cluster_uid']):
        raise StateError('Historical runtime transport differs from its directly observed identity')
    return deepcopy(expected)


class _ReadHTTP(HTTPSReadClient):
    def __init__(self, bound, reader, budget):
        self._bound = bound
        raw = _read(Path(reader['ca_path']), 2_000_000)
        if _sha(raw) != reader['ca_sha256']:
            raise APIError('ca-input-changed')
        super().__init__(reader['origin'],
            timeout=min(5, budget), total_timeout=budget, max_requests=64,
            max_body_bytes=2_000_000, max_total_bytes=16_000_000)
        try:
            self._context = ssl.create_default_context(cadata=raw.decode('ascii'))
            self._context.minimum_version = ssl.TLSVersion.TLSv1_2
        except (UnicodeError, ValueError, ssl.SSLError):
            raise APIError('invalid-ca') from None

    def _authorization(self):
        from .owned_runtime_reader import private_token
        self._bound._admit(deadline=self._deadline)
        return _bearer(private_token(self._bound._reader, journal=self._bound.owner.journal,
                                    bootstrap_authority=self._bound._bootstrap))


class _OwnedReadClient:
    def __init__(self, bound, provider, target, reader, budget):
        self.bound, self.provider, self.target = bound, provider, deepcopy(target)
        self.service = next(row for row in bound.frozen['factory_config']['services'] if row['id'] == provider)
        self.origin = self.service['origin'].rstrip('/')
        self._deadline = min(time.monotonic() + budget, bound.phase.deadline)
        self._http = _ReadHTTP(bound, reader, budget)
        self._http._deadline = self._deadline
        self._route_receipts = []

    @property
    def receipts(self):
        return deepcopy(self._http.receipts)

    def binding(self):
        return self.bound.describe() | {'provider': self.provider, 'logical_origin': self.origin,
                                        'routes': deepcopy(self._route_receipts)}

    def _current(self):
        if time.monotonic() >= self._deadline:
            raise APIError('phase-budget-exhausted')
        reader, _ = self.bound._admit(deadline=self._deadline)
        if time.monotonic() >= self._deadline or self._http.origin != reader['origin'].rstrip('/'):
            raise APIError('phase-budget-exhausted')
        if _sha(_read(Path(reader['ca_path']), 2_000_000)) != reader['ca_sha256']:
            raise APIError('ca-input-changed')

    def _kubernetes_route(self, path, params, binary):
        # Read resources only: no exec, attach, portforward, refresh, secrets or
        # arbitrary subresource/proxy routes. The separate RBAC adds server scope.
        pattern = (r'/(api/v1|apis/(?:apps/v1|networking.k8s.io/v1|discovery.k8s.io/v1))/namespaces/('
                   + _NAME + r')(?:/(pods|services|configmaps|deployments|replicasets|daemonsets|networkpolicies|endpointslices|events)'
                   r'(?:/(' + _NAME + r')(?:/(log))?)?)?')
        match = re.fullmatch(pattern, path or '')
        if match is None or match[2] not in self.service['scope'].get('namespaces', []):
            raise APIError('scope-not-authorized')
        api, namespace, resource, name, subresource = match.groups()
        expected_api = {'deployments': 'apis/apps/v1', 'replicasets': 'apis/apps/v1', 'daemonsets': 'apis/apps/v1',
                        'networkpolicies': 'apis/networking.k8s.io/v1', 'endpointslices': 'apis/discovery.k8s.io/v1'}
        if api != expected_api.get(resource, 'api/v1'):
            raise APIError('scope-not-authorized')
        if subresource and (resource != 'pods' or not binary or params != {'container': 'probe', 'timestamps': 'false', 'limitBytes': 256000}):
            raise APIError('scope-not-authorized')
        if binary and subresource != 'log':
            raise APIError('scope-not-authorized')
        if resource and not name:
            target = (self.target or {}).get('kubernetes', {})
            allowed = {'pods', 'replicasets', 'networkpolicies', 'endpointslices', 'events'}
            if (resource not in allowed or namespace != target.get('namespace') or type(params) is not dict
                    or type(params.get('limit')) is not int or not 1 <= params['limit'] <= 100):
                raise APIError('scope-not-authorized')
            wanted = {'limit', 'continue'}
            if resource in ('pods', 'replicasets', 'endpointslices'):
                wanted.add('labelSelector')
                selector = ('kubernetes.io/service-name='+target['service'] if resource == 'endpointslices'
                            else getattr(self, '_deployment_selector', None))
                if not selector or params.get('labelSelector') != selector:
                    raise APIError('scope-not-authorized')
            elif resource == 'events':
                wanted.add('fieldSelector')
                if params.get('fieldSelector') != 'involvedObject.uid='+target.get('deployment_uid', ''):
                    raise APIError('scope-not-authorized')
            if not set(params) <= wanted:
                raise APIError('scope-not-authorized')
        elif params and not subresource:
            raise APIError('scope-not-authorized')
        if not resource and params:
            raise APIError('scope-not-authorized')
        return path

    def _service_route(self, path):
        selected = self.bound.owner.selection['services'][self.provider]
        descriptor = {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {
            'namespace': selected['namespace'], 'name': selected['name']}}
        bounded = replace(self.bound.phase, deadline=min(self.bound.phase.deadline, self._deadline))
        resources = self.bound.owner.bootstrap.kubernetes(bounded)
        # Service identity uses the same original read-only credential, TLS
        # connection and request budget as its subsequent proxy request. The
        # ownership ledger audits bytes only; it never falls back to admin I/O.
        observed = self._http.get_json('/api/v1/namespaces/'+selected['namespace']+'/services/'+selected['name'])
        self._current()
        observed = resources.audit_owned_read(bounded, descriptor, observed)
        spec = observed.get('spec', {})
        ports = [row for row in spec.get('ports', []) if row.get('port') == selected['port'] and row.get('protocol', 'TCP') == 'TCP']
        if (spec.get('type', 'ClusterIP') != 'ClusterIP' or spec.get('externalName') or spec.get('externalIPs')
                or not spec.get('selector') or len(ports) != 1 or not observed.get('metadata', {}).get('uid')):
            raise APIError('scope-not-authorized')
        prefix = '/api/v1/namespaces/'+selected['namespace']+'/services/'+selected['scheme']+':'+selected['name']+':'+str(selected['port'])+'/proxy'
        self._route_receipts.append({'kind': 'original-owned-service-proxy', 'namespace': selected['namespace'],
            'name': selected['name'], 'uid': observed['metadata']['uid'], 'port': selected['port'], 'route': prefix+path})
        return prefix+path

    def get_json(self, path, params=None):
        self._current()
        if self.provider == 'kubernetes':
            route = self._kubernetes_route(path, params, False)
            result = self._http.get_json(route, params)
            target = (self.target or {}).get('kubernetes', {})
            dep = '/apis/apps/v1/namespaces/'+target.get('namespace', '')+'/deployments/'+target.get('deployment', '')
            if path == dep:
                selector = result.get('spec', {}).get('selector', {})
                labels = selector.get('matchLabels', {})
                if (result.get('metadata', {}).get('uid') != target.get('deployment_uid')
                        or not labels or selector.get('matchExpressions')):
                    raise APIError('scope-not-authorized')
                self._deployment_selector = ','.join(key+'='+value for key, value in sorted(labels.items()))
        elif self.provider == 'prometheus':
            if path != '/api/v1/query_range' or type(params) is not dict or set(params) != {'query', 'start', 'end', 'step', 'timeout', 'limit'}:
                raise APIError('scope-not-authorized')
            selected = (self.target or {}).get('prometheus', {})
            names = ('namespace', 'service', 'job', 'instance')
            if not all(name in selected for name in names):
                raise APIError('scope-not-authorized')
            selector = '{'+','.join(name+'='+json.dumps(selected[name]) for name in names)+'}'
            queries = {metric+selector for metric in ('up', 'mission_events_total')}
            queries |= {'timestamp('+value+')' for value in tuple(queries)}
            lock = self.bound.frozen['factory_lock']
            if (params['query'] not in queries or params['timeout'] != '5s' or params['limit'] != 2
                    or type(params['start']) is not int or type(params['end']) is not int
                    or params['end']-params['start'] != lock['observation_window_seconds']
                    or params['step'] != lock['scrape_interval_seconds']):
                raise APIError('scope-not-authorized')
            route = self._service_route(path)
            result = http_json(self._http.get_bytes(route, params).body)
        elif path == '/api/version' and params is None:
            route = self._service_route(path)
            result = http_json(self._http.get_bytes(route).body)
        else:
            target = (self.target or {}).get('argocd', {})
            if (path != '/api/v1/applications/'+target.get('application', '') or params != {
                    'project': target.get('project'), 'appNamespace': target.get('application_namespace')}):
                raise APIError('scope-not-authorized')
            route = '/apis/argoproj.io/v1alpha1/namespaces/'+target['application_namespace']+'/applications/'+target['application']
            result = self._http.get_json(route)
            self._route_receipts.append({'kind': 'argo-controller-application-via-kubernetes', 'route': route})
        self._current()
        self.bound._observed_reads += 1
        return result

    def get_bytes(self, path, params=None, *, accept=('application/json',), allow_unauthorized=False):
        self._current()
        if self.provider != 'kubernetes' or allow_unauthorized:
            raise APIError('scope-not-authorized')
        route = self._kubernetes_route(path, params, True)
        result = self._http.get_bytes(route, params, accept=accept)
        self._current()
        self.bound._observed_reads += 1
        return result
