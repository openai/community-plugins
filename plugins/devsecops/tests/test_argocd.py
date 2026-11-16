"""Real local TLS transport tests plus scoped Argo semantics, not live Argo qualification."""
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import ssl
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from devsecops.adapters.argocd import collect_argocd
from devsecops.adapters.http import APIError, HTTPSReadClient

SHA = 'a'*40
OTHER = 'b'*40
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
SECRET = 'DO-NOT-EXPORT-SECRET'


@pytest.fixture(scope='module')
def ca(tmp_path_factory):
    root = tmp_path_factory.mktemp('ephemeral tls')
    key, cert = root/'key.pem', root/'cert.pem'
    # An ephemeral, localhost-only test key. No key or credential ships in fixtures.
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=127.0.0.1',
                    '-addext', 'subjectAltName=IP:127.0.0.1'], check=True, capture_output=True, timeout=15)
    key.chmod(0o600)
    return cert, key


@pytest.fixture
def server(ca):
    state = {'requests': [], 'responses': []}
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *_):
            pass
        def do_GET(self):
            state['requests'].append({'path': self.path, 'authorization': self.headers.get('Authorization')})
            response = state['responses'].pop(0) if state['responses'] else {'json': {}}
            if response.get('header_trickle'):
                try:
                    for byte in b'HTTP/1.1 200 OK\r\nX-Slow: infinite-header\r\n':
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.01)
                except OSError:
                    pass
                return
            if response.get('delay'):
                time.sleep(response['delay'])
            self.send_response(response.get('status', 200))
            body = response.get('raw', json.dumps(response.get('json', {})).encode())
            for key, value in response.get('headers', {}).items():
                self.send_header(key, value)
            self.send_header('Content-Type', response.get('content_type', 'application/json'))
            self.send_header('Content-Length', str(response.get('length', len(body))))
            self.end_headers()
            try:
                if response.get('body_delay'):
                    time.sleep(response['body_delay'])
                if response.get('trickle'):
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(response['trickle'])
                else:
                    self.wfile.write(body)
                    self.wfile.flush()
            except (OSError, ssl.SSLError):
                pass
    class QuietServer(ThreadingHTTPServer):
        daemon_threads = True
        def handle_error(self, *_):
            pass
    httpd = QuietServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(ca[0]), str(ca[1]))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    state['origin'] = 'https://127.0.0.1:'+str(httpd.server_port)
    state['ca'] = ca[0]
    yield state
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def client(server, **kwargs):
    return HTTPSReadClient(server['origin'], ca_bundle=server['ca'], **kwargs)


def app():
    source = {'repoURL': 'https://example.test/mission.git', 'path': 'deploy', 'targetRevision': 'main',
              'helm': {'values': 'password: '+SECRET}}
    destination = {'server': 'https://kubernetes.default.svc', 'namespace': 'mission'}
    return {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
            'metadata': {'name': 'mission', 'namespace': 'argocd', 'uid': 'acbd1234-abcd-1234-abcd-12345678abcd',
                         'resourceVersion': '12345', 'generation': 2, 'annotations': {'private': SECRET}},
            'spec': {'project': 'demo', 'source': source, 'destination': destination},
            'status': {'reconciledAt': '2026-10-26T11:59:30Z',
                       'sync': {'status': 'Synced', 'revision': SHA,
                                'comparedTo': {'source': deepcopy(source), 'destination': deepcopy(destination)}},
                       'health': {'status': 'Healthy', 'message': SECRET},
                       'operationState': {'phase': 'Succeeded', 'message': SECRET,
                                          'startedAt': '2026-10-26T11:59:00Z', 'finishedAt': '2026-10-26T11:59:10Z',
                                          'syncResult': {'revision': SHA, 'source': deepcopy(source)}},
                       'resources': [{'group': 'apps', 'version': 'v1', 'kind': 'Deployment', 'namespace': 'mission',
                                      'name': 'mission', 'status': 'Synced', 'health': {'status': 'Healthy', 'message': SECRET}}]}}


def collect(server, before=None, after=None, version='v3.5.6+abc123', **kwargs):
    before = app() if before is None else before
    after = deepcopy(before) if after is None else after
    server['responses'] = [{'json': {'Version': version}}, {'json': before}, {'json': after}]
    return collect_argocd(client(server), project='demo', application_namespace='argocd', application='mission',
                          selected_revisions=kwargs.pop('selected_revisions', {1: SHA}), now=NOW, **kwargs)


def codes(result):
    return {gap['code'] for gap in result['gaps']}


def test_real_tls_exact_routes_and_narrow_positive(server, monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    result = collect(server)
    assert result['execution_status'] == 'complete'
    assert result['decision'] == 'pass' and result['confirmed_failure'] is False
    assert result['observations']['decisions']['functional'] == 'unknown'
    assert result['observations']['generation']['observed'] is None
    assert all(not gap['required'] for gap in result['gaps'])
    assert SECRET not in json.dumps(result)
    assert len(result['observations']['receipts']) == 3
    assert server['requests'][0]['path'] == '/api/version'
    for request in server['requests'][1:]:
        route = urlsplit(request['path'])
        assert route.path == '/api/v1/applications/mission'
        assert parse_qs(route.query) == {'project': ['demo'], 'appNamespace': ['argocd']}


@pytest.mark.parametrize(('area', 'field', 'value'), [
    ('source', 'helm', {'values': 'password: '+SECRET+'\nlabel: café\n', 'releaseName': 'other'}),
    ('source', 'repoURL', 'https://user:'+SECRET+'@example.test/other.git'),
    ('destination', 'namespace', 'other'),
    ('destination', 'server', 'https://user:'+SECRET+'@cluster.example.test'),
])
def test_complete_source_and_destination_digests_are_private_and_sensitive(server, area, field, value):
    before = app()
    baseline = collect(server, before)['observations']
    changed = deepcopy(before)
    changed['spec'][area][field] = value
    changed['status']['sync']['comparedTo'][area] = deepcopy(changed['spec'][area])
    changed['status']['operationState']['syncResult']['source'] = deepcopy(changed['spec']['source'])
    result = collect(server, changed)
    observed = result['observations']
    assert result['decision'] == 'pass'
    assert observed['spec_digest_format'] == 'devsecops-json-sha256-v1'
    def digest(value):
        import hashlib
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                         ensure_ascii=True, allow_nan=False).encode('utf-8')).hexdigest()
    assert observed['destination_spec_sha256'] == digest(changed['spec']['destination'])
    assert observed['sources'][0]['source_spec_sha256'] == digest(changed['spec']['source'])
    assert (observed['destination_spec_sha256'] != baseline['destination_spec_sha256']) == (area == 'destination')
    assert (observed['sources'][0]['source_spec_sha256'] != baseline['sources'][0]['source_spec_sha256']) == (area == 'source')
    assert observed['application']['spec_sha256'] != baseline['application']['spec_sha256']
    assert SECRET not in json.dumps(result)
    assert observed['decisions']['functional'] == 'unknown'


def test_env_credentials_and_receipts_do_not_export_token(server, monkeypatch):
    monkeypatch.setenv('ARGO_TEST_TOKEN', SECRET)
    transport = client(server, token_env='ARGO_TEST_TOKEN')
    assert transport.get_json('/api/version') == {}
    assert server['requests'][0]['authorization'] == 'Bearer '+SECRET
    assert SECRET not in json.dumps(transport.receipts)
    receipt = transport.receipts
    receipt[0]['outcome'] = 'changed'
    assert transport.receipts[0]['outcome'] == 'complete'


@pytest.mark.parametrize('status', [301, 302, 307, 308, 401, 403, 404, 429, 500])
def test_redirects_errors_no_following_or_remote_diagnostics(server, status):
    server['responses'] = [{'status': status, 'raw': SECRET.encode(),
                            'headers': {'Location': 'https://127.0.0.1:1/'+SECRET}}]
    transport = client(server)
    with pytest.raises(APIError) as caught:
        transport.get_json('/api/version')
    assert caught.value.status == status
    assert SECRET not in str(caught.value)
    assert SECRET not in json.dumps(transport.receipts)
    assert len(server['requests']) == 1
    assert transport.receipts[0]['body_bytes'] == 0


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'[]',
                               b'not json '+SECRET.encode(), b'{"x":'+b'['*65+b'0'+b']'*65+b'}'])
def test_json_is_strict_and_sanitized(server, raw):
    server['responses'] = [{'raw': raw}]
    with pytest.raises(APIError, match='invalid-json') as caught:
        client(server).get_json('/api/version')
    assert SECRET not in str(caught.value)


def test_ca_trust_and_hostname_are_verified(server):
    with pytest.raises(APIError, match='tls'):
        HTTPSReadClient(server['origin']).get_json('/api/version')
    with pytest.raises(APIError, match='tls'):
        HTTPSReadClient(server['origin'].replace('127.0.0.1', 'localhost'), ca_bundle=server['ca']).get_json('/api/version')
    assert server['requests'] == []


@pytest.mark.parametrize('response,options,code', [
    ({'raw': b'{"padding":"'+b'x'*200+b'"}'}, {'max_body_bytes': 64}, 'body-limit'),
    ({'headers': {'Content-Encoding': 'gzip'}}, {}, 'unsupported-encoding'),
    ({'content_type': 'text/html', 'raw': SECRET.encode()}, {}, 'invalid-response'),
    ({'delay': .3}, {'timeout': .05}, 'deadline'),
    ({'raw': b'{"x":"'+b'x'*20+b'"}', 'trickle': .02}, {'timeout': .1}, 'deadline'),
    ({'header_trickle': True}, {'timeout': .05}, 'deadline'),
])
def test_time_and_response_bounds(server, response, options, code):
    server['responses'] = [response]
    started = time.monotonic()
    with pytest.raises(APIError) as caught:
        client(server, **options).get_json('/api/version')
    assert caught.value.code == code
    assert time.monotonic()-started < 1


def test_collection_budgets_are_not_reset(server):
    transport = client(server, max_requests=1)
    transport.get_json('/api/version')
    with pytest.raises(APIError, match='request-limit'):
        transport.get_json('/api/version')


def test_collection_byte_and_time_budgets_are_not_reset(server):
    transport = client(server, max_total_bytes=3)
    transport.get_json('/api/version')
    with pytest.raises(APIError, match='body-limit'):
        transport.get_json('/api/version')
    transport = client(server, total_timeout=.05)
    transport.get_json('/api/version')
    time.sleep(.06)
    with pytest.raises(APIError, match='deadline'):
        transport.get_json('/api/version')


def test_connection_close_response_keeps_wall_deadline_across_headers_and_body(server):
    server['responses'] = [{'headers': {'Connection': 'close'}, 'delay': .25, 'body_delay': .25}]
    started = time.monotonic()
    with pytest.raises(APIError, match='deadline'):
        client(server, timeout=.3).get_json('/api/version')
    assert time.monotonic()-started < .45


def test_dns_wait_bounded_and_no_late_request(server, monkeypatch):
    original = socket.getaddrinfo
    def slow(*args, **kwargs):
        time.sleep(.15)
        return original(*args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', slow)
    started = time.monotonic()
    with pytest.raises(APIError, match='deadline'):
        client(server, timeout=.03).get_json('/api/version')
    assert time.monotonic()-started < .1
    time.sleep(.2)
    assert server['requests'] == []


@pytest.mark.parametrize('origin', ['http://example.test', 'https://user:password@example.test',
                                   'https://example.test/argocd', 'https://example.test?token=secret',
                                   'https://example.test#fragment', 'https://example.test\\@evil.test',
                                   'https://example.test:', 'https://a..b', 'https://a.-b',
                                   'https://'+'a'*64+'.test', 'https://2130706433', 'https://127.000.0.1',
                                   'https://example.test:0443', 'https://[::1]:'])
def test_origin_rejects_credentials_paths_and_insecure_transport(origin):
    with pytest.raises(APIError, match='invalid-configuration'):
        HTTPSReadClient(origin)


@pytest.mark.parametrize('path', ['//evil.test/', 'https://evil.test/', '/api/../other', '/api/%2e%2e/other',
                                '/api/version?refresh=hard', '/api/version\r\nHeader: x'])
def test_request_cannot_override_origin_or_embed_query(server, path):
    with pytest.raises(APIError, match='invalid-request'):
        client(server).get_json(path)
    assert server['requests'] == []


def test_missing_or_header_injecting_credentials_never_reach_network(server, monkeypatch):
    for value in ('', 'secret\r\nInjected: value'):
        monkeypatch.setenv('ARGO_TEST_TOKEN', value)
        with pytest.raises(APIError, match='credential-unavailable'):
            client(server, token_env='ARGO_TEST_TOKEN').get_json('/api/version')
    assert server['requests'] == []


@pytest.mark.parametrize('area,key,value,code', [
    ('metadata', 'name', 'other', 'scope-mismatch'),
    ('metadata', 'namespace', 'other', 'scope-mismatch'),
    ('spec', 'project', 'other', 'scope-mismatch'),
    ('metadata', 'uid', None, 'identity-incomplete'),
    ('metadata', 'generation', True, 'identity-incomplete'),
    ('status', 'reconciledAt', '2026-10-26T10:59:30Z', 'stale-or-unavailable'),
    ('status', 'reconciledAt', '2026-10-26T12:01:30Z', 'stale-or-unavailable'),
    ('status', 'reconciledAt', None, 'stale-or-unavailable'),
    ('status', 'conditions', [{'type': 'ComparisonError', 'message': SECRET}], 'controller-error-condition'),
    ('status', 'conditions', [{'type': [SECRET]}], 'conditions-incomplete'),
    ('status', 'health', {'status': ['Healthy']}, 'controller-status-unknown'),
    ('status', 'sync', {'status': ['Synced']}, 'controller-status-unknown'),
    ('status', 'operationState', {'phase': ['Succeeded']}, 'operation-unknown'),
])
def test_invalid_stale_or_out_of_scope_evidence_is_unknown(server, area, key, value, code):
    document = app()
    document[area][key] = value
    result = collect(server, document)
    assert result['decision'] == 'unknown' and result['execution_status'] == 'partial'
    assert code in codes(result)
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('key,value', [('uid', 'replacement-uid'), ('generation', 3), ('resourceVersion', '12346')])
def test_identity_changes_in_flight_are_gaps(server, key, value):
    document = app()
    document['metadata'][key] = value
    result = collect(server, app(), document)
    assert result['decision'] == 'unknown' and 'identity-race' in codes(result)


def test_spec_change_without_version_change_still_detected(server):
    document = app()
    document['spec']['source']['targetRevision'] = OTHER
    result = collect(server, app(), document)
    assert {'identity-race', 'comparison-inputs-mismatch'} <= codes(result)


@pytest.mark.parametrize('version', ['v3.5.5', 'v3.6.0', 'v3.5.6-rc1', SECRET, None])
def test_unknown_or_unqualified_versions_are_gaps(server, version):
    result = collect(server, version=version)
    assert result['decision'] == 'unknown'
    assert codes(result) & {'unknown-version', 'unsupported-version'}
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('change', ['chart', 'oci', 'plugin', 'hydrate', 'unknown-field'])
def test_unsupported_resolution_modes_are_explicit(server, change):
    document = app()
    source = document['spec']['source']
    if change == 'chart':
        source['chart'] = 'nginx'
    elif change == 'oci':
        source['repoURL'] = 'oci://registry.example.test/chart'
    elif change == 'plugin':
        source['plugin'] = {}
    elif change == 'hydrate':
        document['spec']['sourceHydrator'] = {}
    else:
        source['unqualifiedMode'] = {}
    document['status']['sync']['comparedTo']['source'] = deepcopy(source)
    result = collect(server, document)
    assert result['decision'] == 'unknown' and 'unsupported-source-mode' in codes(result)


def test_multisource_revision_order_and_config_are_bound(server):
    document = app()
    first = document['spec'].pop('source')
    second = {'repoURL': 'https://example.test/values.git', 'targetRevision': 'main', 'ref': 'values'}
    document['spec']['sources'] = [first, second]
    compared = document['status']['sync']['comparedTo']
    compared.pop('source')
    compared['sources'] = deepcopy(document['spec']['sources'])
    document['status']['sync']['revisions'] = [SHA, OTHER]
    document['status'].pop('operationState')
    result = collect(server, document, selected_revisions={'1': SHA, '2': OTHER})
    assert result['decision'] == 'pass'
    result = collect(server, document, selected_revisions={1: OTHER, 2: SHA})
    assert result['decision'] == 'unknown' and 'selected-revision-mismatch' in codes(result)
    result = collect(server, document, selected_revisions={1: SHA})
    assert result['decision'] == 'unknown' and 'source-mapping' in codes(result)
    document['status']['sync']['revisions'] = [SHA]
    result = collect(server, document, selected_revisions={1: SHA, 2: OTHER})
    assert result['decision'] == 'unknown' and 'revision-mapping' in codes(result)


def test_floating_or_wrong_resolved_revision_cannot_pass(server):
    for revision in ('main', SHA[:8], OTHER):
        document = app()
        document['status']['sync']['revision'] = revision
        result = collect(server, document)
        assert result['decision'] == 'unknown' and 'selected-revision-mismatch' in codes(result)


def test_one_element_sources_array_still_uses_multisource_wire_fields(server):
    document = app()
    source = document['spec'].pop('source')
    document['spec']['sources'] = [source]
    compared = document['status']['sync']['comparedTo']
    compared['sources'] = [compared.pop('source')]
    document['status']['sync'].pop('revision')
    document['status']['sync']['revisions'] = [SHA]
    document['status'].pop('operationState')
    result = collect(server, document)
    assert result['decision'] == 'pass'
    assert result['observations']['sources'][0]['matched']


def test_failed_operation_without_configuration_binding_is_unknown(server):
    document = app()
    document['status']['operationState']['phase'] = 'Failed'
    document['status']['operationState']['syncResult'].pop('source')
    result = collect(server, document)
    assert result['decision'] == 'unknown' and 'operation-context-unknown' in codes(result)


def test_secret_resource_and_untrusted_messages_are_identity_only(server):
    document = app()
    document['status']['resources'].append({'group': '', 'version': 'v1', 'kind': 'Secret', 'name': 'app-token',
                                           'namespace': 'mission', 'data': {'password': SECRET},
                                           'health': {'status': 'Healthy', 'message': SECRET}})
    result = collect(server, document)
    assert result['decision'] == 'pass'
    assert SECRET not in json.dumps(result)
    assert set(result['observations']['resources'][-1]) == {
        'group', 'version', 'kind', 'name', 'namespace', 'sync_status', 'health_status'}


def test_current_failure_is_separate_from_historical_operation(server):
    document = app()
    document['status']['operationState']['phase'] = 'Failed'
    result = collect(server, document)
    assert result['decision'] == 'unknown' and 'operation-status-conflict' in codes(result)
    document['status']['operationState']['syncResult']['revision'] = OTHER
    result = collect(server, document)
    assert result['decision'] == 'pass'
    assert result['observations']['operation']['selected_revisions_match'] is False
    document['status']['health']['status'] = 'Degraded'
    result = collect(server, document)
    assert result['decision'] == 'fail' and result['confirmed_failure']
    document['status']['reconciledAt'] = None
    result = collect(server, document)
    assert result['decision'] == 'unknown' and not result['confirmed_failure']


def test_running_or_requested_operations_are_not_settled(server):
    document = app()
    document['status']['operationState']['phase'] = 'Running'
    assert 'operation-in-progress' in codes(collect(server, document))
    document = app()
    document['operation'] = {'sync': {'revision': SHA}}
    assert collect(server, document)['decision'] == 'unknown'


def test_api_failure_is_common_unknown_receipt(server):
    server['responses'] = [{'status': 403, 'raw': SECRET.encode()}]
    result = collect_argocd(client(server), project='demo', application_namespace='argocd', application='mission',
                           selected_revisions={1: SHA}, now=NOW)
    assert result['decision'] == 'unknown' and result['execution_status'] == 'partial'
    assert 'transport-denied' in codes(result)
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('revisions', [{}, {1: 'main'}, {True: SHA}, {'01': SHA}, {1: SHA, '1': SHA}])
def test_trusted_revision_selection_validation_precedes_network(server, revisions):
    with pytest.raises(APIError, match='invalid-configuration'):
        collect_argocd(client(server), project='demo', application_namespace='argocd', application='mission',
                       selected_revisions=revisions, now=NOW)
    assert not server['requests']
