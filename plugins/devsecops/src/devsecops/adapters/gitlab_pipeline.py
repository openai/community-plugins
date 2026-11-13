"""Exact-pipeline authenticated acquisition; never a factory authority issuer."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import time

from devsecops.adapters.http import APIError, HTTPSReadClient, _bearer, _json
from devsecops.adapters.signatures import _snapshot
from devsecops.factory.mission_security import _read_retained
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal

_STATUSES = {'created', 'waiting_for_resource', 'preparing', 'pending', 'running', 'success',
             'failed', 'canceled', 'skipped', 'manual', 'scheduled', 'canceling', 'waiting_for_callback'}
_SOURCES = {'push', 'web', 'trigger', 'schedule', 'api', 'external', 'pipeline', 'chat', 'webide',
            'merge_request_event', 'external_pull_request_event', 'parent_pipeline',
            'ondemand_dast_scan', 'ondemand_dast_validation', 'security_orchestration_policy',
            'container_registry_push', 'duo_workflow'}
_UNSETTLED = _STATUSES - {'success', 'failed', 'skipped', 'manual'}
_MAX_ARTIFACT = 8_000_000
_MAX_JSON = 2_000_000
_PER_PAGE = 100


def _id(value):
    if type(value) is not int or not 1 <= value < 2**63:
        raise ValueError('Explicit positive GitLab identifier required')
    return value


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})', value):
        raise APIError('gitlab-source-mismatch')
    return value


def _requests(value):
    if not isinstance(value, (tuple, list)) or len(value) > 8:
        raise ValueError('At most eight explicit artifact requests are supported')
    selected = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'job_id', 'path'}:
            raise ValueError('Artifact selection requires exact job ID and relative path')
        path = item['path']
        if (not isinstance(path, str) or not 1 <= len(path) <= 1024
                or any(not re.fullmatch(r'[A-Za-z0-9_~.-]+', part) or part in ('.', '..')
                       for part in path.split('/'))):
            raise ValueError('Artifact paths must be normalized bounded relative ASCII paths')
        pair = (_id(item['job_id']), path)
        if pair in selected:
            raise ValueError('Duplicate artifact selection')
        selected.append(pair)
    return tuple(selected)


def _pipeline(value, project, pipeline, sha):
    if (value.get('id') != pipeline or value.get('project_id') != project
            or type(value.get('id')) is not int or type(value.get('project_id')) is not int
            or _sha(value.get('sha')) != sha or value.get('status') not in _STATUSES
            or value.get('source') not in _SOURCES or not isinstance(value.get('ref'), str)
            or not 1 <= len(value['ref']) <= 1024):
        raise APIError('gitlab-pipeline-mismatch')
    return {'id': pipeline, 'project_id': project, 'sha': sha, 'status': value['status'],
            'source': value['source'], 'ref_sha256': _digest(value['ref'].encode()),
            'merge_result': bool(re.search(r'(?:^|/)merge-requests/[0-9]+/(?:merge|train)(?:$|/)', value['ref']))}


def _job(value, project, pipeline, sha):
    if not isinstance(value, dict):
        raise APIError('gitlab-job-mismatch')
    owner = value.get('pipeline')
    if (not isinstance(owner, dict) or type(owner.get('id')) is not int or owner['id'] != pipeline
            or ('project_id' in owner and _id(owner['project_id']) != project) or _sha(owner.get('sha')) != sha
            or value.get('status') not in _STATUSES or type(value.get('allow_failure')) is not bool
            or not isinstance(value.get('commit'), dict) or _sha(value['commit'].get('id')) != sha):
        raise APIError('gitlab-job-mismatch')
    result = {'id': _id(value.get('id')), 'project_id': project, 'pipeline_id': pipeline, 'sha': sha,
              'status': value['status'], 'allow_failure': value['allow_failure']}
    # The route authenticates project membership even on GitLab versions whose
    # nested pipeline omits project_id. No API-returned URL selects another route.
    for name in ('name', 'stage'):
        if not isinstance(value.get(name), str) or not 1 <= len(value[name]) <= 1024:
            raise APIError('gitlab-job-mismatch')
        result[name + '_sha256'] = _digest(value[name].encode())
    for name in ('runner', 'runner_manager'):
        item = value.get(name)
        result[name + '_id'] = _id(item.get('id')) if isinstance(item, dict) else None
    return result


def collect_gitlab_pipeline(*, origin, project_id, pipeline_id, expected_sha, token_env,
                            output, artifact_requests=(), ca_bundle=None, timeout=60, max_pages=10):
    """Read one selected pipeline and explicit artifacts over verified HTTPS.

    No trigger, retry, cancellation, logs, latest-ref lookup, imported report,
    callback, or authority reconstruction is accepted by this API.
    """
    project_id, pipeline_id = _id(project_id), _id(pipeline_id)
    _sha(expected_sha)
    requests = _requests(artifact_requests)
    if (not isinstance(token_env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', token_env)
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 120
            or type(max_pages) is not int or not 1 <= max_pages <= 10
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts
            or (ca_bundle is not None and (not isinstance(ca_bundle, Path)
                or not ca_bundle.is_absolute() or '..' in ca_bundle.parts))):
        raise ValueError('Explicit credential reference, bounded limits and new private output required')
    authorization = _bearer(os.environ.get(token_env, ''))
    deadline = time.monotonic() + timeout
    report = {'schema_version': 1, 'kind': 'gitlab-exact-pipeline-acquisition', 'decision': 'unknown',
        'execution_status': 'partial', 'factory_decision': 'unknown', 'origin_authority': 'not-issued',
        'scope': 'authenticated-api-status-and-selected-artifact-acquisition-only',
        'selection': {'project_id': project_id, 'pipeline_id': pipeline_id, 'expected_sha': expected_sha},
        'pipeline': None, 'jobs': [], 'artifacts': [], 'responses': [], 'gaps': [],
        'started_at': datetime.now(timezone.utc).isoformat()}
    client = None
    with _new_directory(output) as directory:
        retained = {}
        def checkpoint():
            if time.monotonic() >= deadline:
                raise APIError('deadline')
            _verify_output_path(output, directory)
        def store(raw):
            checkpoint()
            name = 'sha256-' + _digest(raw) + '.blob'
            ref = {'sha256': _digest(raw), 'bytes': len(raw)}
            if name not in retained:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                retained[name] = ref
            else:
                _read_retained(directory, name, ref, deadline)
            checkpoint()
            return {'file': name, **ref}
        def request(path, params=None, *, binary=False):
            checkpoint()
            response = client.get_bytes(path, params=params, accept=('application/json', 'application/octet-stream',
                'text/plain', 'application/zip') if binary else ('application/json',))
            if len(response.body) > (_MAX_ARTIFACT if binary else _MAX_JSON):
                raise APIError('gitlab-response-limit')
            ref = store(response.body)
            report['responses'].append({'request_index': len(client.receipts), **ref,
                'pagination': deepcopy(response.pagination)})
            return response, ref
        def document(path):
            response, _ = request(path)
            return _json(response.body)
        def pages(path, *, jobs=False):
            items, seen, totals = [], set(), None
            for page in range(1, max_pages + 1):
                response, _ = request(path, {'page': page, 'per_page': _PER_PAGE,
                    **({'include_retried': 'false'} if jobs else {})})
                wrapped = _json(b'{"items":' + response.body + b'}')
                if set(wrapped) != {'items'}:
                    raise APIError('invalid-json')
                values = wrapped['items']
                headers = response.pagination
                if (not isinstance(values, list) or len(values) > _PER_PAGE
                        or headers.get('x-page') != str(page) or headers.get('x-per-page') != str(_PER_PAGE)
                        or headers.get('x-next-page') not in ('', str(page + 1))
                        or ('x-prev-page' in headers and headers['x-prev-page'] != (str(page-1) if page > 1 else ''))):
                    raise APIError('gitlab-pagination-incomplete')
                current = tuple(headers.get(name) for name in ('x-total', 'x-total-pages'))
                if totals is not None and current != totals:
                    raise APIError('gitlab-pagination-changed')
                totals = current
                for item in values:
                    if not isinstance(item, dict) or _id(item.get('id')) in seen:
                        raise APIError('gitlab-pagination-duplicate')
                    seen.add(item['id'])
                    items.append(item)
                if headers['x-next-page'] == '':
                    if ((current[0] is not None and current[0] != str(len(items)))
                            or (current[1] is not None and current[1] not in
                                ({'0', '1'} if not items and page == 1 else {str(page)}))):
                        raise APIError('gitlab-pagination-incomplete')
                    return items
                if len(values) != _PER_PAGE:
                    raise APIError('gitlab-pagination-incomplete')
            raise APIError('gitlab-pagination-limit')
        def jobs():
            rows = pages(base + '/jobs', jobs=True)
            return sorted((_job(row, project_id, pipeline_id, expected_sha) for row in rows), key=lambda row: row['id'])
        def audit():
            checkpoint()
            for name, ref in retained.items():
                _read_retained(directory, name, ref, deadline)
            checkpoint()
        try:
            ca = None
            if ca_bundle is not None:
                ca = output / 'ca.pem'
                retained['ca.pem'] = _snapshot(Path(ca_bundle), ca, 2_000_000, deadline=deadline)
            checkpoint()
            client = HTTPSReadClient(origin, token_env=token_env, ca_bundle=ca,
                timeout=min(30, timeout), total_timeout=timeout, max_body_bytes=_MAX_ARTIFACT,
                max_total_bytes=64_000_000, max_requests=64)
            client._deadline = deadline
            # Freeze this exact selected credential for the collection. The
            # closure is local trusted code, never an input callback or secret receipt.
            client._authorization = lambda: authorization
            report['origin'] = client.origin
            base = f'/api/v4/projects/{project_id}/pipelines/{pipeline_id}'
            before = _pipeline(document(base), project_id, pipeline_id, expected_sha)
            report['pipeline'] = before
            initial_jobs = jobs()
            report['jobs'] = initial_jobs
            if not initial_jobs:
                raise APIError('gitlab-jobs-empty')
            if pages(base + '/bridges'):
                report['gaps'].append('gitlab-downstream-pipelines-not-collected')
            if before['source'] in ('pipeline', 'parent_pipeline') or before['merge_result']:
                report['gaps'].append('gitlab-upstream-or-merge-result-context-not-collected')
            by_id = {job['id']: job for job in initial_jobs}
            for job_id, path in requests:
                if job_id not in by_id:
                    raise APIError('gitlab-artifact-job-mismatch')
                route = f'/api/v4/projects/{project_id}/jobs/{job_id}'
                first = _job(document(route), project_id, pipeline_id, expected_sha)
                if first != by_id[job_id]:
                    raise APIError('gitlab-job-changed')
                _, ref = request(route + '/artifacts/' + path, binary=True)
                report['artifacts'].append({'job_id': job_id, 'path': path, **ref})
                if _job(document(route), project_id, pipeline_id, expected_sha) != first:
                    raise APIError('gitlab-job-changed')
            if jobs() != initial_jobs:
                raise APIError('gitlab-jobs-changed')
            if pages(base + '/bridges'):
                if 'gitlab-downstream-pipelines-not-collected' not in report['gaps']:
                    report['gaps'].append('gitlab-downstream-pipelines-not-collected')
            if _pipeline(document(base), project_id, pipeline_id, expected_sha) != before:
                raise APIError('gitlab-pipeline-changed')
            status = before['status']
            if status == 'success' and any(job['status'] in _UNSETTLED or
                    (job['status'] in ('failed', 'manual') and not job['allow_failure']) for job in initial_jobs):
                report['gaps'].append('gitlab-inconsistent-job-status')
            if status not in ('success', 'failed'):
                report['gaps'].append('gitlab-pipeline-unresolved')
            if not report['gaps']:
                report['decision'] = 'pass' if status == 'success' else 'fail'
                report['execution_status'] = 'complete'
            audit()
        except APIError as error:
            report['gaps'].append(error.code)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            report['gaps'].append('gitlab-acquisition-incomplete')
        finally:
            report['transport_receipts'] = client.receipts if client is not None else []
            if client is not None:
                client._authorization = lambda: None
            authorization = None
        if report['gaps']:
            report['decision'], report['execution_status'] = 'unknown', 'partial'
        report['retained'] = deepcopy(retained)
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report = seal(report, 'acquisition_sha256')
        _verify_output_path(output, directory)
        try:
            _write(directory, 'gitlab-pipeline.json', report)
            os.fsync(directory)
            _verify_output_path(output, directory)
            if report['execution_status'] == 'complete':
                audit()
        except BaseException:
            try:
                os.unlink('gitlab-pipeline.json', dir_fd=directory)
            except FileNotFoundError:
                pass
            os.fsync(directory)
            raise
        return report
