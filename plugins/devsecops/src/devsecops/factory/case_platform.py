"""D3 owned optional-extension fault, Git repair and direct Flux observations.

Functional health is a labeled Kubernetes service-proxy GET. It does not claim
network-policy path assurance; those separate probes remain D1/D2 requirements.
"""
from copy import deepcopy
from datetime import datetime, timezone
import os
import re
import time

from .case_platform_git import OwnedPlatformGit, require_platform_transition
from .lifecycle import AssertionResult, PhaseOutcome
from .state import StateError, _canonical, _exclusive, _sha
from devsecops.adapters.http import _json

ASSERTIONS = ('real-bigbang-flux-extension-dependency-failure', 'applied-versus-attempted-generation-bound',
              'git-repair-and-dependent-service-recovered', 'core-platform-preserved')
_COMMIT = re.compile(r'[a-f0-9]{40}\Z')


def revision_commit(revision):
    if not isinstance(revision, str) or len(revision) > 256:
        return None
    # Explicit source-controller Git artifact syntax; do not accept a substring
    # from a tag, error message or unrelated chart version as a delivered commit.
    match = re.fullmatch(r'(?:[^\s:@]{1,128}@)?sha1:([a-f0-9]{40})', revision)
    return match.group(1) if match else revision if _COMMIT.fullmatch(revision) else None


def ready_condition(document, kind='Ready'):
    metadata, status = document.get('metadata', {}), document.get('status', {})
    generation = metadata.get('generation')
    rows = [row for row in status.get('conditions', []) if row.get('type') == kind]
    if (type(generation) is not int or generation < 1 or len(rows) != 1
            or rows[0].get('observedGeneration', status.get('observedGeneration')) != generation):
        return None
    return rows[0]


def core_ready(document):
    if document['kind'] == 'Application':
        status = document.get('status', {})
        return status.get('sync', {}).get('status') == 'Synced' and status.get('health', {}).get('status') == 'Healthy'
    row = ready_condition(document, 'Available' if document['kind'] == 'Deployment' else 'Ready')
    if row is None or row.get('status') != 'True':
        return False
    if document['kind'] == 'Deployment':
        desired = document.get('spec', {}).get('replicas', 1)
        status = document.get('status', {})
        return (type(desired) is int and desired > 0 and status.get('updatedReplicas') == desired
                and status.get('availableReplicas') == desired and status.get('observedGeneration') == document['metadata']['generation'])
    return True


def bind_core_selection(selection, platform_recipe, observation):
    readiness = platform_recipe['readiness']
    required = {('apps/v1', 'Deployment', row['namespace'], row['name']) for row in readiness['flux_deployments']}
    required.update(('helm.toolkit.fluxcd.io/v2', 'HelmRelease', row['namespace'], row['name'])
                    for row in readiness['helm_releases'])
    argo = readiness['argo_application']
    required.add(('argoproj.io/v1alpha1', 'Application', argo['namespace'], argo['name']))
    selected = {(row['apiVersion'], row['kind'], row['namespace'], row['name']) for row in selection['core_resources']}
    actual_argo = observation['argo_application_identity']
    if (selected != required or len(selected) != len(selection['core_resources'])
            or any(actual_argo[key] != argo[key] for key in ('namespace', 'name'))
            or not actual_argo.get('uid')):
        raise StateError('D3 core inventory differs from actual platform bootstrap readiness roles')


def assess_observation(observation, *, stage, commit, selection, baseline=None, fault=None):
    """Deterministic interpretation, not an API-origin or runtime capability."""
    source, root, dependency, extension = (observation[key] for key in ('source', 'root', 'dependency', 'extension'))
    for document in (source, root, dependency):
        condition = ready_condition(document)
        if condition is None or condition.get('status') != 'True':
            return None
    if (source.get('spec', {}).get('ref') != {'commit': commit}
            or revision_commit(source.get('status', {}).get('artifact', {}).get('revision')) != commit
            or revision_commit(root.get('status', {}).get('lastAppliedRevision')) != commit
            or root.get('spec', {}).get('sourceRef') != {'kind': 'GitRepository', 'name': selection['source_name']}
            or extension.get('spec', {}).get('sourceRef') != {'kind': 'GitRepository', 'name': selection['source_name']}
            or any(not core_ready(row) for row in observation['core'])
            or observation['service_health'] != selection['service']['expected']):
        return None
    current = ready_condition(extension)
    if current is None:
        return None
    status, metadata = extension['status'], extension['metadata']
    service_identity = selection['namespace'] + '_' + selection['service']['name'] + '__Service'
    if sum(row.get('id') == service_identity and row.get('v') == 'v1'
           for row in status.get('inventory', {}).get('entries', [])) != 1:
        return None
    applied = revision_commit(status.get('lastAppliedRevision'))
    attempted = revision_commit(status.get('lastAttemptedRevision'))
    observed_generation = status.get('observedGeneration')
    if type(observed_generation) is not int or not 0 <= observed_generation <= metadata['generation']:
        return None
    if stage == 'fault':
        if (baseline is None or current.get('status') != 'False' or current.get('reason') != 'DependencyNotReady'
                or extension['spec'].get('dependsOn') != [{'name': selection['dependency_name'] + '-unavailable'}]
                or metadata['generation'] <= baseline['extension']['metadata']['generation']
                or applied != revision_commit(baseline['extension']['status'].get('lastAppliedRevision'))
                or applied == commit):
            return None
    elif (current.get('status') != 'True' or extension['spec'].get('dependsOn') != [{'name': selection['dependency_name']}]
            or observed_generation != metadata['generation'] or applied != commit
            or stage == 'repair' and (fault is None or metadata['generation'] <= fault['extension']['metadata']['generation'])):
        return None
    if baseline is not None:
        if observation['service']['metadata']['uid'] != baseline['service']['metadata']['uid']:
            return None
        for name in ('source', 'root', 'dependency', 'extension'):
            if observation[name]['metadata']['uid'] != baseline[name]['metadata']['uid']:
                return None
        original = {(row['apiVersion'], row['kind'], row['metadata']['namespace'], row['metadata']['name']): row
                    for row in baseline['core']}
        current_core = {(row['apiVersion'], row['kind'], row['metadata']['namespace'], row['metadata']['name']): row
                        for row in observation['core']}
        if set(original) != set(current_core):
            return None
        for key, row in current_core.items():
            before = original[key]
            if (row['metadata']['uid'] != before['metadata']['uid'] or row.get('spec') != before.get('spec')
                    or row['metadata']['generation'] != before['metadata']['generation']):
                return None
    return {'condition_observed_generation': current.get('observedGeneration', observed_generation), 'metadata_generation': metadata['generation'],
            'status_observed_generation': observed_generation, 'last_applied_commit': applied,
            'last_attempted_commit': attempted, 'selected_source_commit': commit,
            'dependency_reason': current.get('reason'), 'stage': stage,
            'service_health_route': 'kubernetes-service-proxy-no-network-policy-assurance'}


class OwnedPlatformCase:
    def __init__(self, cases, selection, output):
        from .owned_cases import OwnedCanonicalCases
        if type(cases) is not OwnedCanonicalCases:
            raise StateError('D3 requires its original canonical case caller')
        self.cases, self.selection, self.output = cases, deepcopy(selection), output
        self.journal = cases.journal
        self._pid, self._observed, self._writer = os.getpid(), {}, None

    def _admit(self, context):
        if self._pid != os.getpid() or context.phase['id'] != 'platform-dependency':
            raise StateError('D3 observer belongs to its original process and phase')
        self.cases._admit(context)
        if not {'inject', 'deliver', 'probe'} <= set(context.inputs['config']['allowed_mutations']):
            raise StateError('D3 requires inject, deliver and probe grants before any transition')
        return self.journal.current_mutation('platform-dependency', 'platform-dependency')

    def _capture(self, context):
        self._admit(context)
        bootstrap = self.cases.operations.bootstrap
        authority = bootstrap.require_authority(context)
        from .canonical_bootstrap import require_bootstrap_authority
        facts = require_bootstrap_authority(authority, self.journal)
        platform = bootstrap.platform.observation(context)
        if (platform['run_id'] != context.run_id or platform['cluster_uid'] != facts['kubernetes']['cluster_uid']
                or platform['versions'] != {'flux_version': bootstrap.platform.recipe['flux_version'],
                                             'bigbang_version': bootstrap.platform.recipe['bigbang_version']}):
            raise StateError('D3 requires actual original Big Bang/Flux bootstrap observations')
        resources = bootstrap.kubernetes(context)
        selected = self.selection
        bind_core_selection(selected, bootstrap.platform.recipe, platform)
        observed = {'observed_at': datetime.now(timezone.utc).isoformat(), 'cluster_uid': resources.cluster_uid,
                    'versions': dict(platform['versions'])}
        started = time.monotonic()
        for key, kind, api in (('source', 'GitRepository', 'source.toolkit.fluxcd.io/v1'),
                              ('root', 'Kustomization', 'kustomize.toolkit.fluxcd.io/v1'),
                              ('dependency', 'Kustomization', 'kustomize.toolkit.fluxcd.io/v1'),
                              ('extension', 'Kustomization', 'kustomize.toolkit.fluxcd.io/v1')):
            descriptor = {'apiVersion': api, 'kind': kind,
                          'metadata': {'namespace': selected['namespace'], 'name': selected[key + '_name']}}
            observed[key] = (resources.read_platform_extension(context, transition=self._writer._last, descriptor=descriptor)
                if key == 'extension' and self._writer is not None and self._writer._last is not None
                else resources.read_owned(context, descriptor))
        observed['core'] = [resources.read_owned(context, {'apiVersion': row['apiVersion'], 'kind': row['kind'],
                            'metadata': {'namespace': row['namespace'], 'name': row['name']}})
                            for row in selected['core_resources']]
        argo = next(row for row in observed['core'] if row['kind'] == 'Application')
        if argo['metadata']['uid'] != platform['argo_application_identity']['uid']:
            raise StateError('D3 actual core Argo Application was replaced')
        # Original namespace ownership is mandatory even though the Service may
        # have been created by its directly observed Flux extension controller.
        resources.read_owned(context, {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': selected['namespace']}})
        service = resources.session.request('GET', '/api/v1/namespaces/' + selected['namespace'] + '/services/' + selected['service']['name'],
                                            deadline=context.deadline)
        if (not service or service.get('metadata', {}).get('name') != selected['service']['name']
                or service['metadata'].get('namespace') != selected['namespace'] or not service['metadata'].get('uid')
                or not any(row.get('port') == selected['service']['port'] and row.get('protocol', 'TCP') == 'TCP'
                           for row in service.get('spec', {}).get('ports', []))):
            raise StateError('D3 dependent Service destination is absent or changed')
        observed['service'] = service
        health = resources.session.read_service_health(selected['namespace'], selected['service']['name'],
            selected['service']['port'], health_path=selected['service']['health_path'], deadline=context.deadline)
        if health is None or health.status != 200 or len(health.body) > 16384:
            raise StateError('D3 dependent Service health is unavailable')
        observed['service_health'] = _json(health.body)
        if time.monotonic() - started > context.inputs['lock']['freshness_seconds']:
            raise StateError('D3 observation exceeded its fixed freshness bound')
        self._admit(context)
        return observed

    def _wait(self, context, stage, commit):
        for index in range(self.selection['max_observations']):
            observed = self._capture(context)
            check = assess_observation(observed, stage=stage, commit=commit, selection=self.selection,
                                       baseline=self._observed.get('baseline'), fault=self._observed.get('fault'))
            if check is not None:
                _exclusive(self.output / (stage + '-observation.json'), _canonical({'observation': observed, 'assessment': check}))
                self._observed[stage] = observed
                return check
            _exclusive(self.output / (stage + '-pending-' + str(index) + '.json'), _canonical({
                'stage': stage, 'observation_sha256': _sha(_canonical(observed)), 'observation': observed, 'observed_at': observed['observed_at'],
                'decision': 'unknown', 'selected_source_commit': commit}))
            remaining = context.deadline - time.monotonic()
            if remaining <= self.selection['poll_seconds'] or index + 1 >= self.selection['max_observations']:
                break
            time.sleep(self.selection['poll_seconds'])
        raise StateError('D3 actual controller/functional observation did not reach the expected bounded state')

    def execute(self, context):
        self._admit(context)
        self.output.mkdir(mode=0o700)
        report = {'schema_version': 1, 'kind': 'canonical-platform-dependency-case', 'case_id': 'D3',
                  'run_id': context.run_id, 'identities': dict(context.identities), 'execution_mode': context.execution_mode,
                  'decision': 'unknown', 'stages': {}, 'mutation_effect': 'not_applied'}
        try:
            first = self._capture(context)
            initial = first['source']['spec']['ref']['commit']
            report['stages']['baseline'] = self._wait(context, 'baseline', initial)
            self._writer = OwnedPlatformGit(self.journal, operations=self.cases.operations,
                                            selection=self.selection, output=self.output / 'git')
            for stage in ('fault', 'repair'):
                transition = self._writer.transition(context, stage)
                bound = require_platform_transition(transition, journal=self.journal, context=context)
                resources = self.cases.operations.bootstrap.kubernetes(context)
                resources.select_platform_commit(context, transition=transition)
                report['stages'][stage] = self._wait(context, stage, bound['commit'])
            self._admit(context)
            if context.execution_mode == 'operator':
                report['decision'], report['mutation_effect'] = 'pass', 'applied'
        except (OSError, ValueError, KeyError, TypeError):
            report['gap'] = 'actual-platform-git-generation-health-or-preservation-incomplete'
            if self._writer is not None and self._writer._dispatched:
                report['mutation_effect'] = 'unknown'
        _exclusive(self.output / 'case-result.json', _canonical(report))
        success = report['decision'] == 'pass'
        return PhaseOutcome('completed' if success else 'incomplete',
            'actual-platform-dependency-verified' if success else 'actual-platform-dependency-incomplete',
            tuple(sorted(path for path in self.output.rglob('*') if path.is_file())),
            tuple(AssertionResult('D3', name, 'pass') for name in ASSERTIONS) if success else (), report['mutation_effect'])
