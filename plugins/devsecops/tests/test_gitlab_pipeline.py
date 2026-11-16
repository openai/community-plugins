"""Actual private-CA TLS and GitLab wire responses; no hosted qualification."""
from copy import deepcopy
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from devsecops.adapters import gitlab_pipeline as pipeline
from tests.test_argocd import ca, server  # noqa: F401

SHA = 'a' * 40
SECRET = 'PRIVATE-GITLAB-CANARY'


def job(identifier=99, **changes):
    return {'id': identifier, 'pipeline': {'id': 42, 'project_id': 7, 'sha': SHA},
        'commit': {'id': SHA}, 'name': SECRET, 'stage': 'publish', 'status': 'success',
        'allow_failure': False, 'runner': {'id': 8}, 'runner_manager': {'id': 9},
        'web_url': 'https://outside.invalid/' + SECRET, **changes}


def info(**changes):
    return {'id': 42, 'project_id': 7, 'sha': SHA, 'status': 'success', 'source': 'push',
        'ref': SECRET, 'web_url': 'https://outside.invalid/' + SECRET, **changes}


def page(rows, number=1, next_page='', total=None, total_pages=None):
    headers = {'X-Page': str(number), 'X-Per-Page': '100', 'X-Next-Page': str(next_page),
               'X-Prev-Page': str(number-1) if number > 1 else ''}
    if total is not None:
        headers['X-Total'] = str(total)
    if total_pages is not None:
        headers['X-Total-Pages'] = str(total_pages)
    return {'json': rows, 'headers': headers}


def responses():
    return [{'json': info()}, page([job()], total=1, total_pages=1), page([], total=0, total_pages=0),
            {'json': job()}, {'raw': b'\x00raw selected artifact\xff', 'content_type': 'application/octet-stream'},
            {'json': job()}, page([job()]), page([]), {'json': info()}]


@pytest.fixture
def case(server, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv('PIPELINE_READ_TOKEN', SECRET)
    server['responses'] = responses()
    return {'server': server, 'args': {'origin': server['origin'], 'project_id': 7, 'pipeline_id': 42,
        'expected_sha': SHA, 'token_env': 'PIPELINE_READ_TOKEN', 'ca_bundle': server['ca'],
        'output': tmp_path / 'evidence', 'artifact_requests': ({'job_id': 99, 'path': 'publication/report.json'},)}}


def collect(case):
    return pipeline.collect_gitlab_pipeline(**case['args'])


def test_actual_authenticated_tls_freezes_exact_bytes_private_ca_and_safe_receipts(case, monkeypatch):
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(name, 'http://127.0.0.1:1')
    report = collect(case)
    assert report['decision'] == 'pass' and report['execution_status'] == 'complete'
    assert report['factory_decision'] == 'unknown' and report['origin_authority'] == 'not-issued'
    assert report['pipeline']['sha'] == SHA and report['jobs'][0]['runner_id'] == 8
    assert SECRET not in json.dumps(report)
    assert len(report['responses']) == len(report['transport_receipts']) == 9
    output = case['args']['output']
    artifact = report['artifacts'][0]
    raw = (output / artifact['file']).read_bytes()
    assert raw == b'\x00raw selected artifact\xff' and hashlib.sha256(raw).hexdigest() == artifact['sha256']
    assert output.stat().st_mode & 0o777 == 0o700
    for path in output.iterdir():
        assert path.stat().st_mode & 0o777 == (0o400 if path.name == 'ca.pem' else 0o600)
    for ref in report['responses']:
        raw = (output / ref['file']).read_bytes()
        assert len(raw) == ref['bytes'] and hashlib.sha256(raw).hexdigest() == ref['sha256']
        transport = report['transport_receipts'][ref['request_index']-1]
        assert transport['body_sha256'] == ref['sha256']
    paths = [urlsplit(row['path']).path for row in case['server']['requests']]
    assert paths == ['/api/v4/projects/7/pipelines/42', '/api/v4/projects/7/pipelines/42/jobs',
        '/api/v4/projects/7/pipelines/42/bridges', '/api/v4/projects/7/jobs/99',
        '/api/v4/projects/7/jobs/99/artifacts/publication/report.json', '/api/v4/projects/7/jobs/99',
        '/api/v4/projects/7/pipelines/42/jobs', '/api/v4/projects/7/pipelines/42/bridges',
        '/api/v4/projects/7/pipelines/42']
    for row in case['server']['requests']:
        assert row['authorization'] == 'Bearer ' + SECRET
    assert parse_qs(urlsplit(case['server']['requests'][1]['path']).query) == {
        'page': ['1'], 'per_page': ['100'], 'include_retried': ['false']}


@pytest.mark.parametrize('status', [401, 403, 404, 429, 500, 302, 307])
def test_denied_redirect_and_remote_diagnostics_do_not_escape_or_retry(case, status):
    case['server']['responses'] = [{'status': status, 'raw': SECRET.encode(),
                                    'headers': {'Location': 'https://outside.invalid/' + SECRET}}]
    report = collect(case)
    assert report['decision'] == 'unknown' and len(case['server']['requests']) == 1
    assert not report['artifacts'] and SECRET not in json.dumps(report)
    assert report['transport_receipts'][0]['body_sha256'] is None


@pytest.mark.parametrize('field,value', [('id', 43), ('id', True), ('project_id', 8),
    ('sha', 'b'*40), ('source', 'unknown-provider'), ('status', 'not-a-status')])
def test_pipeline_identity_and_protocol_mismatch_stops_before_jobs(case, field, value):
    case['server']['responses'][0] = {'json': info(**{field: value})}
    report = collect(case)
    assert report['decision'] == 'unknown' and len(case['server']['requests']) == 1


@pytest.mark.parametrize('fault', ['owner', 'commit', 'detail', 'retry', 'pipeline-status', 'required-job', 'foreign-artifact'])
def test_exact_job_identity_and_completion_races_remain_unknown(case, fault):
    rows = case['server']['responses']
    if fault == 'owner':
        rows[1]['json'][0]['pipeline']['id'] = 43
    elif fault == 'commit':
        rows[1]['json'][0]['commit']['id'] = 'b'*40
    elif fault == 'detail':
        rows[5]['json']['status'] = 'running'
    elif fault == 'retry':
        rows[6]['json'] = [job(100)]
    elif fault == 'pipeline-status':
        rows[8]['json']['status'] = 'running'
    elif fault == 'required-job':
        for index in (1, 6):
            rows[index]['json'][0]['status'] = 'manual'
        for index in (3, 5):
            rows[index]['json']['status'] = 'manual'
    else:
        case['args']['artifact_requests'] = ({'job_id': 100, 'path': 'report.json'},)
    report = collect(case)
    assert report['decision'] == 'unknown' and report['gaps']
    assert report['factory_decision'] == 'unknown'


@pytest.mark.parametrize('fault', ['child', 'parent', 'merge', 'pending', 'no-jobs'])
def test_uncovered_execution_context_is_explicit(case, fault):
    rows = case['server']['responses']
    if fault == 'child':
        rows[2] = page([{'id': 300, 'downstream_pipeline': {'web_url': 'https://outside.invalid/'}}])
    elif fault == 'no-jobs':
        rows[1] = page([])
    else:
        key, value = {'parent': ('source', 'parent_pipeline'), 'merge': ('ref', 'refs/merge-requests/1/merge'),
                      'pending': ('status', 'running')}[fault]
        rows[0]['json'][key] = rows[8]['json'][key] = value
    report = collect(case)
    assert report['decision'] == 'unknown' and report['gaps']
    assert all('outside.invalid' not in row['path'] for row in case['server']['requests'])


def test_stable_failed_pipeline_is_only_narrow_ci_failure(case):
    case['server']['responses'][0]['json']['status'] = 'failed'
    case['server']['responses'][8]['json']['status'] = 'failed'
    report = collect(case)
    assert report['decision'] == 'fail' and report['factory_decision'] == 'unknown'


@pytest.mark.parametrize('fault', ['missing', 'wrong-page', 'jump', 'total', 'duplicate', 'non-numeric', 'short-next'])
def test_pagination_cannot_silently_drop_or_repeat_jobs(case, fault):
    row = case['server']['responses'][1]
    if fault == 'missing':
        row['headers'].pop('X-Next-Page')
    elif fault == 'wrong-page':
        row['headers']['X-Page'] = '2'
    elif fault == 'jump':
        row['headers']['X-Next-Page'] = '3'
    elif fault == 'total':
        row['headers']['X-Total'] = '2'
    elif fault == 'duplicate':
        row['json'].append(job())
    elif fault == 'non-numeric':
        row['headers']['X-Page'] = 'https://outside.invalid'
    else:
        row['headers']['X-Next-Page'] = '2'
    report = collect(case)
    assert report['decision'] == 'unknown' and not report['artifacts']
    assert len(case['server']['requests']) == 2


def test_multiple_pages_and_retry_exclusion_are_rechecked(case):
    first = [job(index) for index in range(1, 101)]
    second = [job(101)]
    pages = [page(first, next_page=2, total=101, total_pages=2), page(second, number=2, total=101, total_pages=2)]
    case['args']['artifact_requests'] = ()
    case['server']['responses'] = [{'json': info()}, *deepcopy(pages), page([]), *deepcopy(pages), page([]), {'json': info()}]
    report = collect(case)
    assert report['decision'] == 'pass' and len(report['jobs']) == 101
    assert all(row['runner_manager_id'] == 9 for row in report['jobs'])


def test_maximum_pages_fails_closed(case):
    case['args']['max_pages'] = 1
    case['server']['responses'][1] = page([job(index) for index in range(1, 101)], next_page=2)
    report = collect(case)
    assert report['gaps'] == ['gitlab-pagination-limit'] and len(case['server']['requests']) == 2


def test_duplicate_numeric_pagination_headers_are_rejected(case):
    case['server']['responses'][1]['headers']['x-page'] = '1'
    report = collect(case)
    assert report['decision'] == 'unknown' and report['gaps'] == ['invalid-response']
    assert len(case['server']['requests']) == 2


def test_changing_pagination_totals_remains_incomplete(case):
    case['server']['responses'] = [{'json': info()},
        page([job(index) for index in range(1, 101)], next_page=2, total=101, total_pages=2),
        page([job(101)], number=2, total=102, total_pages=2)]
    report = collect(case)
    assert report['gaps'] == ['gitlab-pagination-changed'] and not report['artifacts']


def test_allowed_failed_job_does_not_turn_status_into_factory_approval(case):
    for index in (1, 6):
        case['server']['responses'][index]['json'][0].update(status='failed', allow_failure=True)
    for index in (3, 5):
        case['server']['responses'][index]['json'].update(status='failed', allow_failure=True)
    report = collect(case)
    assert report['decision'] == 'pass' and report['jobs'][0]['status'] == 'failed'
    assert report['origin_authority'] == 'not-issued' and report['factory_decision'] == 'unknown'


@pytest.mark.parametrize('raw', [b'{"id":42,"id":43}', b'{"id":NaN}', b'[]', b'not-json'])
def test_malformed_json_is_retained_privately_without_claiming_ci(case, raw):
    case['server']['responses'][0] = {'raw': raw}
    report = collect(case)
    assert report['decision'] == 'unknown' and report['gaps'] == ['invalid-json']
    assert (case['args']['output'] / report['responses'][0]['file']).read_bytes() == raw


@pytest.mark.parametrize('index', [1, 2], ids=['jobs', 'bridges'])
def test_invalid_array_trailing_object_member_cannot_become_valid_wrapper(case, index):
    response = case['server']['responses'][index]
    raw = json.dumps(response['json']).encode() + b',"extra":1'
    response['raw'] = raw
    report = collect(case)
    assert report['decision'] == 'unknown' and report['gaps'] == ['invalid-json']
    assert len(case['server']['requests']) == index + 1
    assert (case['args']['output'] / report['responses'][-1]['file']).read_bytes() == raw


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'a//b', 'a/./b', 'a/%2e/b', 'a?token=x', 'a\\b', 'café', 'a\n'])
def test_unsafe_artifact_routes_reject_before_network_or_output(case, path):
    case['args']['artifact_requests'] = ({'job_id': 99, 'path': path},)
    with pytest.raises(ValueError):
        collect(case)
    assert not case['server']['requests'] and not case['args']['output'].exists()


def test_missing_credentials_reject_before_network_or_output(case, monkeypatch):
    monkeypatch.delenv('PIPELINE_READ_TOKEN')
    with pytest.raises(ValueError):
        collect(case)
    assert not case['server']['requests'] and not case['args']['output'].exists()


def test_private_ca_is_required_and_no_tls_fallback(case):
    case['args']['ca_bundle'] = None
    report = collect(case)
    assert report['decision'] == 'unknown' and report['gaps'] == ['tls']
    assert not case['server']['requests']


def test_credential_is_frozen_without_serializing_it(case, monkeypatch):
    real = pipeline.HTTPSReadClient.get_bytes
    def get(self, *args, **kwargs):
        result = real(self, *args, **kwargs)
        monkeypatch.setenv('PIPELINE_READ_TOKEN', 'changed-token')
        return result
    monkeypatch.setattr(pipeline.HTTPSReadClient, 'get_bytes', get)
    assert collect(case)['decision'] == 'pass'
    assert {row['authorization'] for row in case['server']['requests']} == {'Bearer ' + SECRET}


def test_slow_body_shares_deadline_and_never_fetches_artifact_later(case):
    case['args']['timeout'] = 1
    case['server']['responses'][0]['body_delay'] = 1.3
    start = time.monotonic()
    report = collect(case)
    assert time.monotonic() - start < 2.5
    assert report['decision'] == 'unknown' and report['gaps'] == ['deadline']
    assert len(case['server']['requests']) == 1


def test_oversized_artifact_stops_before_retention(case):
    case['server']['responses'][4]['length'] = 8_000_001
    report = collect(case)
    assert report['decision'] == 'unknown' and report['gaps'] == ['body-limit']
    assert not report['artifacts']


@pytest.mark.parametrize('fault', ['deadline', 'route', 'artifact', 'ca'])
def test_terminal_complete_receipt_withdrawn_if_identity_or_deadline_changes(case, monkeypatch, fault):
    write = pipeline._write
    output = case['args']['output']
    def changed(directory, name, value):
        result = write(directory, name, value)
        if name == 'gitlab-pipeline.json':
            if fault == 'deadline':
                actual = time.monotonic
                monkeypatch.setattr(pipeline.time, 'monotonic', lambda: actual() + 1000)
            elif fault == 'route':
                output.rename(output.with_name('moved'))
                output.mkdir(mode=0o700)
            elif fault == 'ca':
                (output / 'ca.pem').chmod(0o600)
                (output / 'ca.pem').write_bytes(b'changed')
            else:
                (output / value['artifacts'][0]['file']).write_bytes(b'changed')
        return result
    monkeypatch.setattr(pipeline, '_write', changed)
    with pytest.raises(ValueError):
        collect(case)
    retained = output.with_name('moved') if fault == 'route' else output
    assert not (retained / 'gitlab-pipeline.json').exists()
