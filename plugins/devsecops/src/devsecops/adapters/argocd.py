"""Passive Application evidence using the Argo CD v3.5.6 API contract."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

from .http import APIError

API_CONTRACT = '3.5.6'
SPEC_DIGEST_FORMAT = 'devsecops-json-sha256-v1'
SYNC = {'Synced', 'OutOfSync', 'Unknown'}
HEALTH = {'Healthy', 'Degraded', 'Progressing', 'Missing', 'Suspended', 'Unknown'}
PHASES = {'Running', 'Terminating', 'Failed', 'Error', 'Succeeded'}
CONDITIONS = {'InvalidSpecError', 'ComparisonError', 'SyncError', 'UnknownError',
              'SharedResourceWarning', 'RepeatedResourceWarning', 'ExcludedResourceWarning',
              'OrphanedResourceWarning', 'DeletionError'}


def _object(value):
    return value if isinstance(value, dict) else {}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _name(value, *, namespace=False):
    return (isinstance(value, str) and len(value) <= (63 if namespace else 253)
            and re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value)
            and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                    for label in value.split('.')) and (not namespace or '.' not in value))


def _revision(value):
    return isinstance(value, str) and re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value) is not None


def _time(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except ValueError:
        return None


def _application_shape(app, *, typed_rest=False):
    """Accept omitted TypeMeta only at the typed Argo Application REST route.

    Argo CD's protobuf REST projection omits both Kubernetes TypeMeta fields.
    Present fields must still be complete and exact; no fields are synthesized.
    """
    if not isinstance(app, dict):
        return False
    if typed_rest and 'apiVersion' not in app and 'kind' not in app:
        return True
    return app.get('apiVersion') == 'argoproj.io/v1alpha1' and app.get('kind') == 'Application'


def _identity(app):
    metadata, spec = _object(app.get('metadata')), _object(app.get('spec'))
    return {key: metadata.get(key) for key in ('uid', 'generation', 'resourceVersion')} | {'spec_sha256': _digest(spec)}


def _sources(spec):
    if 'sources' in spec and not isinstance(spec['sources'], list):
        return None
    if spec.get('sources'):
        sources = spec['sources']
        if spec.get('source') or not isinstance(sources, list) or not 1 <= len(sources) <= 32:
            return None
    else:
        sources = [spec.get('source')]
    if any(not isinstance(source, dict) or not source for source in sources):
        return None
    return sources


def collect_argocd(client, *, project, application_namespace, application,
                   selected_revisions, max_age_seconds=300, now=None):
    """Collect one scoped Application twice, without refresh or manifest calls.

    selected_revisions maps every 1-based source position to a full Git object
    ID. It is trusted intent supplied by the caller, never inferred from status.
    This first contract supports Git sources, not Helm/OCI chart version pins.
    `now` is an aware datetime for repeatable tests; production callers omit it.
    """
    if (not _name(project) or not _name(application_namespace, namespace=True) or not _name(application)
            or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400
            or not isinstance(selected_revisions, dict) or not 1 <= len(selected_revisions) <= 32):
        raise APIError('invalid-configuration')
    expected = {}
    for position, revision in selected_revisions.items():
        if (type(position) not in (str, int) or not re.fullmatch(r'[1-9][0-9]?', str(position))
                or not 1 <= int(position) <= 32 or not _revision(revision)
                or int(position) in expected):
            raise APIError('invalid-configuration')
        expected[int(position)] = revision
    if now is not None and (not isinstance(now, datetime) or now.tzinfo is None):
        raise APIError('invalid-configuration')
    def clock():
        return now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)

    start = clock()
    gaps = []
    def gap(code, message, required=True):
        if not any(item['code'] == code for item in gaps):
            gaps.append({'code': code, 'message': message, 'required': required})
    result = {'schema_version': 1, 'provider': 'argocd', 'execution_status': 'partial',
              'decision': 'unknown', 'confirmed_failure': False,
              'scope': {'origin': client.origin, 'project': project,
                        'application_namespace': application_namespace, 'application': application},
              'observed_at': start.isoformat(), 'observations': {
                  'api_contract': API_CONTRACT, 'spec_digest_format': SPEC_DIGEST_FORMAT}, 'gaps': gaps}
    observations = result['observations']
    def finish():
        end = clock()
        result['observed_at'] = end.isoformat()
        observations['collection'] = {'started_at': start.isoformat(), 'finished_at': end.isoformat()}
        observations['receipts'] = client.receipts
        return result
    try:
        version = client.get_json('/api/version')
        path = '/api/v1/applications/'+application
        params = {'project': project, 'appNamespace': application_namespace}
        before = client.get_json(path, params)
        after = client.get_json(path, params)
    except APIError as error:
        gap('transport-'+error.code, 'A scoped service read did not complete; no remote diagnostic is exported.')
        return finish()
    observed_version = version.get('Version')
    if isinstance(observed_version, str) and re.fullmatch(r'v?[0-9]+\.[0-9]+\.[0-9]+(?:\+[A-Za-z0-9.-]{1,64})?', observed_version):
        observations['server_version'] = observed_version
        if observed_version.removeprefix('v').split('+', 1)[0] != API_CONTRACT:
            gap('unsupported-version', 'The observed server version differs from the pinned adapter contract.')
    else:
        observations['server_version'] = None
        gap('unknown-version', 'The server did not expose a recognized release version.')
    for app in (before, after):
        metadata, spec = _object(app.get('metadata')), _object(app.get('spec'))
        if (metadata.get('name') != application or metadata.get('namespace') != application_namespace
                or spec.get('project') != project):
            gap('scope-mismatch', 'An Application response did not match every configured scope field.')
            return finish()
        if not _application_shape(app, typed_rest=True):
            gap('application-shape', 'The scoped response is not an Application of the expected API version.')
            return finish()
    first_id, last_id = _identity(before), _identity(after)
    stable = first_id == last_id
    if not stable:
        gap('identity-race', 'Application UID, generation, resource version, or desired spec changed during collection.')
    safe_id = {}
    for key in ('uid', 'resourceVersion'):
        value = last_id[key]
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', value):
            gap('identity-incomplete', 'Application immutable identity or resource version is missing or malformed.')
            value = None
        safe_id['resource_version' if key == 'resourceVersion' else key] = value
    generation = last_id['generation']
    if type(generation) is not int or generation < 1:
        generation = None
        gap('identity-incomplete', 'Application immutable identity or generation is missing or malformed.')
    safe_id.update(generation=generation, spec_sha256=last_id['spec_sha256'])
    observations['application'] = safe_id
    spec, status = _object(after.get('spec')), _object(after.get('status'))
    observations['destination_spec_sha256'] = (
        _digest(spec['destination']) if isinstance(spec.get('destination'), dict) and spec['destination'] else None)
    sync = _object(status.get('sync'))
    compared = _object(sync.get('comparedTo'))
    sources, compared_sources = _sources(spec), _sources(compared)
    source_match = sources is not None and sources == compared_sources
    destination_match = (isinstance(spec.get('destination'), dict) and bool(spec['destination'])
                         and spec['destination'] == compared.get('destination'))
    comparison_match = (source_match and destination_match and isinstance(spec.get('ignoreDifferences', []), list)
                        and spec.get('ignoreDifferences', []) == compared.get('ignoreDifferences', []))
    observations['comparison'] = {'identity_stable': stable, 'source_spec_matches': source_match,
                                  'destination_matches': destination_match,
                                  'comparison_inputs_match': comparison_match}
    if not comparison_match:
        gap('comparison-inputs-mismatch', 'Controller comparedTo inputs do not match the current desired Application inputs.')
    if not sources:
        gap('source-mapping', 'The desired source mapping is absent, ambiguous, or exceeds the bounded contract.')
        sources = []
    if set(expected) != set(range(1, len(sources)+1)):
        gap('source-mapping', 'Trusted selected revisions must cover every source position exactly once.')
    multi_mode = bool(spec.get('sources'))
    observed_revisions = sync.get('revisions') if multi_mode else [sync.get('revision')]
    if not isinstance(observed_revisions, list) or len(observed_revisions) != len(sources):
        observed_revisions = [None]*len(sources)
        gap('revision-mapping', 'Controller resolved revisions do not map unambiguously to all source positions.')
    mappings = []
    for position, source in enumerate(sources, 1):
        # These modes can introduce another renderer/input resolution boundary.
        if (not isinstance(source.get('repoURL'), str) or not source['repoURL']
                or source.get('chart') or str(source.get('repoURL')).startswith('oci://')
                or source.get('plugin') is not None or spec.get('sourceHydrator') is not None
                or set(source)-{'repoURL', 'path', 'targetRevision', 'helm', 'kustomize', 'directory', 'ref', 'name'}
                or any(key in source and not isinstance(source[key], dict) for key in ('helm', 'kustomize', 'directory'))):
            gap('unsupported-source-mode', 'A selected source requires a separately qualified chart, plugin, or hydration resolver.')
        revision = observed_revisions[position-1]
        if not _revision(revision):
            revision = None
            gap('unresolved-revision', 'A controller revision is not a full immutable Git object ID.')
        matched = revision is not None and revision == expected.get(position)
        if not matched:
            gap('selected-revision-mismatch', 'Controller state is not bound to every trusted selected immutable revision.')
        mappings.append({'position': position, 'source_spec_sha256': _digest(source),
                         'expected_revision': expected.get(position), 'observed_revision': revision, 'matched': matched})
    observations['sources'] = mappings
    reconciled = _time(status.get('reconciledAt'))
    age = (clock()-reconciled).total_seconds() if reconciled else None
    safe_id['reconciled_at'] = reconciled.isoformat() if reconciled else None
    observations['freshness'] = {'age_seconds': round(age, 3) if age is not None else None,
                                 'max_age_seconds': max_age_seconds, 'source': 'status.reconciledAt'}
    if age is None or age < -30 or age > max_age_seconds:
        gap('stale-or-unavailable', 'Controller reconciliation time is missing, stale, or implausibly in the future.')
    observations['generation'] = {'desired': generation, 'observed': None, 'assessment': 'not-exposed-by-api-contract'}
    gap('observed-generation-unavailable', 'This API contract exposes no observedGeneration; comparedTo and stable identity provide narrower evidence.', False)
    sync_status, health_status = sync.get('status'), _object(status.get('health')).get('status')
    if not isinstance(sync_status, str) or sync_status not in SYNC:
        sync_status = 'Unknown'
    if not isinstance(health_status, str) or health_status not in HEALTH:
        health_status = 'Unknown'
    if sync_status == 'Unknown' or health_status == 'Unknown':
        gap('controller-status-unknown', 'Controller sync or health status is unavailable or unrecognized.')
    op = _object(status.get('operationState'))
    phase = op.get('phase') if op else None
    if phase is not None and (not isinstance(phase, str) or phase not in PHASES):
        phase = None
        gap('operation-unknown', 'The operation phase is not recognized by the API contract.')
    if op and phase is None:
        gap('operation-unknown', 'The operation phase is unavailable.')
    op_result = _object(op.get('syncResult'))
    op_revisions = op_result.get('revisions') if multi_mode else [op_result.get('revision')]
    valid_op_revisions = (isinstance(op_revisions, list) and len(op_revisions) == len(sources)
                          and bool(sources) and all(_revision(revision) for revision in op_revisions))
    op_matches = (valid_op_revisions and all(expected.get(i) == rev for i, rev in enumerate(op_revisions, 1)))
    op_sources = _sources(op_result)
    op_source_matches = bool(sources) and op_sources == sources
    operation = {'phase': phase, 'selected_revisions_match': op_matches if valid_op_revisions else None,
                 'selected_sources_match': op_source_matches if op_sources else None}
    for field, output in (('startedAt', 'started_at'), ('finishedAt', 'finished_at')):
        parsed = _time(op.get(field))
        operation[output] = parsed.isoformat() if parsed else None
    observations['operation'] = operation
    if phase in ('Running', 'Terminating') or after.get('operation'):
        gap('operation-in-progress', 'A requested or active controller operation prevents a settled-state assessment.')
    if phase in ('Failed', 'Error') and not valid_op_revisions:
        gap('operation-revision-unknown', 'A failed operation cannot be related to the selected source revisions.')
    if phase in ('Failed', 'Error') and op_matches and op_sources is None:
        gap('operation-context-unknown', 'A selected-revision failure has no immutable source configuration context.')
    condition_types = []
    conditions = status.get('conditions', [])
    if not isinstance(conditions, list) or len(conditions) > 64:
        gap('conditions-incomplete', 'Controller conditions exceed the bounded contract or are malformed.')
    else:
        for condition in conditions:
            kind = _object(condition).get('type')
            if not isinstance(kind, str) or kind not in CONDITIONS:
                gap('conditions-incomplete', 'An unrecognized controller condition requires investigation.')
            else:
                condition_types.append(kind)
    observations['condition_types'] = sorted(set(condition_types))
    if any(kind.endswith('Error') for kind in condition_types):
        gap('controller-error-condition', 'Controller error conditions prevent a complete current-state assessment.')
    resources = status.get('resources', [])
    normalized_resources = []
    if not isinstance(resources, list) or len(resources) > 2000:
        gap('resources-incomplete', 'Managed-resource summaries exceed the bounded contract or are malformed.')
    else:
        for resource in resources:
            resource = _object(resource)
            row = {}
            valid = True
            for key in ('group', 'version', 'kind', 'namespace', 'name'):
                value = resource.get(key, '')
                if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{0,253}', value) or (key in ('kind', 'name') and not value):
                    valid = False
                    break
                row[key] = value
            if not valid:
                gap('resources-incomplete', 'A managed-resource identity is missing or malformed.')
                continue
            row['sync_status'] = resource.get('status') if isinstance(resource.get('status'), str) and resource['status'] in SYNC else 'Unknown'
            resource_health = _object(resource.get('health')).get('status')
            row['health_status'] = resource_health if isinstance(resource_health, str) and resource_health in HEALTH else 'Unknown'
            normalized_resources.append(row)
    observations['resources'] = normalized_resources
    sync_decision = {'Synced': 'pass', 'OutOfSync': 'fail'}.get(sync_status, 'unknown')
    health_decision = {'Healthy': 'pass', 'Degraded': 'fail', 'Missing': 'fail'}.get(health_status, 'unknown')
    operation_decision = ('fail' if op_matches and op_source_matches and phase in ('Failed', 'Error') else
                          'pass' if op_matches and op_source_matches and phase == 'Succeeded' else 'unknown')
    if operation_decision == 'fail' and sync_decision == health_decision == 'pass':
        gap('operation-status-conflict', 'A selected-source operation failure conflicts with current positive controller state; current failure is not inferred.')
    observations['decisions'] = {'sync': sync_decision, 'health': health_decision,
                                  'operation': operation_decision, 'functional': 'unknown'}
    observations['sync_status'], observations['health_status'] = sync_status, health_status
    required_gap = any(item['required'] for item in gaps)
    failure = 'fail' in (sync_decision, health_decision, operation_decision)
    result['confirmed_failure'] = failure and not required_gap
    if not required_gap:
        result['execution_status'] = 'complete'
        result['decision'] = 'fail' if failure else ('pass' if sync_decision == health_decision == 'pass' else 'unknown')
    return finish()
