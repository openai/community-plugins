"""Real local TLS uploads/readback; controlled authority is never live F0 proof."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import pickle
import socket
import ssl
import tarfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from devsecops.adapters.http import APIError
from devsecops.adapters.oci import MANIFEST
from devsecops.adapters.staging_registry import _StagingRegistry
from devsecops.factory import mission_executor as builder
from devsecops.factory import mission_staging as stage
from devsecops.factory.state import IDENTITY_KEYS
from devsecops.runtime.context import verify_seal
from test_argocd import ca
from test_mission_executor import digest, encode, image, layer, sha

__all__ = ['ca']
REPOSITORY = 'demo/mission-staging'
TOKEN = 'LOCAL-TEST-STAGING-TOKEN'
PASSWORD = 'LOCAL-TEST-STAGING-PASSWORD'


@pytest.fixture
def registry(ca, monkeypatch):
    monkeypatch.setenv('STAGING_TEST_TOKEN', TOKEN)
    monkeypatch.setenv('STAGING_TEST_USER', 'local-fixture')
    monkeypatch.setenv('STAGING_TEST_PASSWORD', PASSWORD)
    state = {'requests': [], 'blobs': {}, 'manifests': {}, 'sessions': {}, 'fault': None, 'auth': 'bearer', 'count': 0}
    prefix = '/v2/' + REPOSITORY
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def respond(self, status, raw=b'', media='application/octet-stream', **headers):
            self.send_response(status)
            self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(raw)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(raw)
                self.wfile.flush()

        def close_without_response(self):
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            self.close_connection = True

        def authorized(self):
            state['requests'].append({'method': self.command, 'path': self.path, 'authorization': self.headers.get('Authorization')})
            if self.headers.get('Authorization') == 'Bearer ' + TOKEN:
                return True
            realm = state['origin'] + '/service/token'
            if state['fault'] == 'auth-other-origin':
                realm = 'https://outside.invalid/service/token'
            scope = 'repository:' + REPOSITORY + ':pull'
            if state['fault'] == 'auth-other-repository':
                scope = 'repository:another/repository:pull,push'
            self.respond(401, **{'WWW-Authenticate': f'Bearer realm="{realm}",service="staging",scope="{scope}"'})
            return False

        def do_HEAD(self):
            if self.authorized():
                self.respond(200 if self.path.removeprefix(prefix + '/manifests/') in state['manifests'] else 404)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == '/service/token':
                state['requests'].append({'method': 'GET', 'path': self.path, 'authorization': self.headers.get('Authorization')})
                self.respond(200, encode({'token': TOKEN, 'expires_in': 300}), 'application/json')
                return
            if not self.authorized():
                return
            if state['fault'] == 'readback-redirect':
                self.respond(307, Location='https://outside.invalid/image')
                return
            if parsed.path.startswith(prefix + '/manifests/'):
                key = parsed.path.removeprefix(prefix + '/manifests/')
                raw, media = state['manifests'].get(key), MANIFEST
            else:
                key = parsed.path.removeprefix(prefix + '/blobs/')
                raw, media = state['blobs'].get(key), 'application/octet-stream'
                if state['fault'] == 'readback-corrupt' and raw is not None:
                    raw = bytes([raw[0] ^ 1]) + raw[1:]
            if raw is None:
                self.respond(404)
            else:
                self.respond(200, raw, media, **{'Docker-Content-Digest': key})

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != prefix + '/blobs/uploads/':
                self.respond(400)
                return
            state['count'] += 1
            identity = 'session-' + str(state['count'])
            if state['fault'] == 'reused-session':
                identity = 'same-session'
            state['sessions'][identity] = None
            if state['fault'] == 'post-disconnect':
                self.close_without_response()
                return
            location = prefix + '/blobs/uploads/' + identity + '?_state=opaque%2Fvalue'
            if state['fault'] == 'external-location':
                location = 'https://outside.invalid' + location
            elif state['fault'] == 'other-repository-location':
                location = location.replace(REPOSITORY, 'other/repository')
            elif state['fault'] == 'encoded-path-location':
                location = location.replace('/blobs/', '/%62lobs/')
            elif state['fault'] == 'query-digest':
                location += '&digest=sha256%3A' + 'a' * 64
            elif state['fault'] == 'query-duplicate':
                location += '&_state=changed'
            elif state['fault'] == 'query-control':
                location += '&bad=%0A'
            elif state['fault'] == 'absolute-location':
                location = state['origin'] + location
            self.respond(202, Location=location)

        def do_PUT(self):
            if not self.authorized():
                return
            raw = self.rfile.read(int(self.headers['Content-Length']))
            parsed = urlsplit(self.path)
            if parsed.path.startswith(prefix + '/blobs/uploads/'):
                key = parse_qs(parsed.query)['digest'][0]
                if digest(raw) != key or parse_qs(parsed.query)['_state'] != ['opaque/value']:
                    self.respond(400)
                    return
                state['blobs'][key] = raw
                location = prefix + '/blobs/' + key
            elif parsed.path.startswith(prefix + '/manifests/'):
                key = parsed.path.removeprefix(prefix + '/manifests/')
                state['manifests'][key] = raw
                location = prefix + '/manifests/' + key
            else:
                self.respond(400)
                return
            if state['fault'] == 'put-disconnect':
                self.close_without_response()
                return
            if state['fault'] == 'put-delay':
                time.sleep(.5)
            if state['fault'] == 'put-redirect':
                self.respond(307, Location='https://outside.invalid/destination')
                return
            if state['fault'] == 'wrong-ack-location':
                location = prefix + '/blobs/' + 'sha256:' + 'a' * 64
            acknowledged = 'sha256:' + '0' * 64 if state['fault'] == 'wrong-ack-digest' else key
            self.respond(201, Location=location, **{'Docker-Content-Digest': acknowledged})

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, *_):
            pass
    httpd = Server(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(ca[0]), str(ca[1]))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    state.update(origin='https://127.0.0.1:' + str(httpd.server_port), ca=ca[0])
    yield state
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


@pytest.fixture
def case(tmp_path, registry):
    raw = layer([('hello.txt', b'controlled OCI test\n', 10001, 10001)])
    fixture = image({'os': 'linux', 'architecture': 'amd64', 'config': {'User': '10001:10001'},
        'rootfs': {'type': 'layers', 'diff_ids': [digest(raw)]}}, [raw])
    path = tmp_path / 'source.oci.tar'
    with tarfile.open(path, 'w') as archive:
        for name, content in {'oci-layout': fixture['marker'], 'index.json': fixture['index'],
                **{'blobs/sha256/' + name: content for name, content in fixture['blobs'].items()}}.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    path.chmod(0o600)
    scope = {name: sha(name.encode()) for name in IDENTITY_KEYS}
    # Test-only guard fixture, marked controlled; no production issuer accepts
    # this dictionary and this never claims a real canonical Iron Bank build.
    capability = object.__new__(builder.BuiltMission)
    binding = {'run_scope': scope, 'source_commit': 'a' * 40, 'architecture': 'amd64',
        'context_sha256': 'b' * 64, 'base': {'fixture': 'controlled'},
        'archive_path': str(path), 'archive_sha256': sha(path.read_bytes()), 'archive_bytes': path.stat().st_size,
        'manifest_digest': fixture['manifest'], 'config_digest': fixture['config'],
        'builder_report_sha256': 'c' * 64, 'execution_mode': 'controlled-test',
        'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()}
    builder._BUILT[capability] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic() + 600, 'binding': binding}
    selected = {'schema_version': 1, 'kind': 'mission-staging-configuration', 'origin': registry['origin'],
        'repository': REPOSITORY, 'credentials': {'token_env': 'STAGING_TEST_TOKEN'}, 'ca_bundle': str(registry['ca'])}
    config = tmp_path / 'staging.json'
    config.write_bytes(encode(selected))
    config.chmod(0o600)
    yield {'built': capability, 'run_scope': scope, 'source_commit': 'a' * 40, 'architecture': 'amd64',
        'config': config, 'expected_config_sha256': sha(config.read_bytes()), 'output': tmp_path / 'result',
        '_selected': selected, '_binding': binding, '_fixture': fixture}
    del builder._BUILT[capability]


def run(case, **kwargs):
    return stage.stage_mission(**{key: value for key, value in case.items() if not key.startswith('_')}, **kwargs)


def configure(case, change):
    change(case['_selected'])
    case['config'].write_bytes(encode(case['_selected']))
    case['expected_config_sha256'] = sha(case['config'].read_bytes())


def test_real_tls_upload_and_immutable_readback(case, registry, monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    report, capability = run(case)
    assert report['checks'] == dict.fromkeys(('build', 'artifact', 'upload', 'readback'), 'pass')
    assert report['decision'] == 'unknown' and capability is None
    assert report['execution_mode'] == 'controlled-test'
    verify_seal(report, 'stage_sha256')
    expected = case['_fixture']['blobs']
    for key, content in expected.items():
        assert (case['output'] / 'readback/layout/blobs/sha256' / key).read_bytes() == content
    assert set(registry['manifests']) == {case['_fixture']['manifest']}
    assert len(registry['blobs']) == 2
    assert all(row['state'] == 'content-confirmed-by-immutable-readback' for row in report['mutations'])
    assert len(report['mutations']) == 5
    assert len(registry['requests']) == 9
    assert not any(row['method'] in {'PATCH', 'DELETE'} for row in registry['requests'])
    serialized = json.dumps(report)
    assert TOKEN not in serialized and PASSWORD not in serialized and 'opaque' not in serialized


def test_real_basic_auth_scopes_push_then_independent_pull(case, registry):
    configure(case, lambda c: c.update(credentials={'basic_username_env': 'STAGING_TEST_USER',
        'basic_password_env': 'STAGING_TEST_PASSWORD', 'token_path': '/service/token', 'token_service': 'staging'}))
    report, capability = run(case)
    assert report['checks']['readback'] == 'pass' and capability is None
    exchanges = [parse_qs(urlsplit(row['path']).query) for row in registry['requests'] if row['path'].startswith('/service/token')]
    assert [row['scope'] for row in exchanges] == [['repository:' + REPOSITORY + ':pull,push'], ['repository:' + REPOSITORY + ':pull']]


@pytest.mark.parametrize('fault', ['external-location', 'other-repository-location', 'encoded-path-location',
    'query-digest', 'query-duplicate', 'query-control', 'post-disconnect', 'put-disconnect', 'put-redirect',
    'wrong-ack-location', 'wrong-ack-digest', 'readback-corrupt', 'readback-redirect', 'reused-session'])
def test_partial_or_misleading_registry_cannot_issue_authority(case, registry, fault):
    registry['fault'] = fault
    report, capability = run(case)
    assert report['decision'] == 'unknown' and capability is None
    assert 'staging-or-readback-incomplete' in report['gaps']
    assert not any(row['state'] == 'content-confirmed-by-immutable-readback' for row in report['mutations'])
    assert all(not row['path'].startswith('https://') for row in registry['requests'])
    for row in report['mutations']:
        assert (case['output'] / row['intent']).is_file()
    if fault in {'post-disconnect', 'put-disconnect', 'put-redirect', 'wrong-ack-location', 'wrong-ack-digest'}:
        assert sum(row['method'] == 'POST' for row in registry['requests']) == 1
        assert sum(row['method'] == 'PUT' for row in registry['requests']) <= 1
    if fault == 'put-disconnect':
        assert registry['blobs'] and not registry['manifests']  # A real partial write is retained.


def test_same_origin_absolute_upload_location_preserves_opaque_query(case, registry):
    registry['fault'] = 'absolute-location'
    report, _ = run(case)
    assert report['checks']['readback'] == 'pass'


@pytest.mark.parametrize('fault', ['auth-other-origin', 'auth-other-repository'])
def test_auth_challenge_cannot_expand_writer_scope(case, registry, fault):
    registry['fault'] = fault
    configure(case, lambda c: c.update(credentials={'basic_username_env': 'STAGING_TEST_USER',
        'basic_password_env': 'STAGING_TEST_PASSWORD', 'token_path': '/service/token', 'token_service': 'staging'}))
    report, _ = run(case)
    assert report['checks']['upload'] == 'unknown'
    assert [row['method'] for row in registry['requests']] == ['HEAD']
    assert not report['mutations']


@pytest.mark.parametrize('kind', ['dict', 'unissued', 'expired', 'foreign-process', 'scope', 'source', 'architecture'])
def test_build_authority_required_before_any_output_or_network(case, registry, kind):
    if kind == 'dict':
        case['built'] = deepcopy(case['_binding'])
    elif kind == 'unissued':
        case['built'] = object.__new__(builder.BuiltMission)
    elif kind == 'expired':
        builder._BUILT[case['built']]['expires_monotonic'] = time.monotonic()-1
    elif kind == 'foreign-process':
        builder._BUILT[case['built']]['pid'] += 1
    elif kind == 'scope':
        case['run_scope'] = dict.fromkeys(IDENTITY_KEYS, 'f' * 64)
    elif kind == 'source':
        case['source_commit'] = 'b' * 40
    else:
        case['architecture'] = 'arm64'
    with pytest.raises(ValueError):
        run(case)
    assert not registry['requests'] and not case['output'].exists()


@pytest.mark.parametrize('change', [
    lambda c: c.update(origin='https://registry1.dso.mil'),
    lambda c: c.update(origin='http://localhost'),
    lambda c: c.update(origin=c['origin'] + '/'),
    lambda c: c.update(repository='../other'),
    lambda c: c.update(credentials={'token': 'raw-secret'}),
    lambda c: c.update(credentials={'token_env': 'not a ref'}),
    lambda c: c.update(tag='latest'),
])
def test_strict_destination_config(case, registry, change):
    configure(case, change)
    with pytest.raises(ValueError):
        run(case)
    assert not registry['requests']


def test_config_pin_precedes_network(case, registry):
    case['expected_config_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='selected identity'):
        run(case)
    assert not registry['requests']


def test_changed_archive_prevents_all_network(case, registry):
    Path(case['_binding']['archive_path']).write_bytes(b'changed')
    report, capability = run(case)
    assert report['checks']['artifact'] == 'unknown' and capability is None
    assert not registry['requests']


def test_expiry_between_post_and_put_prevents_blob_write(case, registry, monkeypatch):
    original = _StagingRegistry.begin_blob
    def begin(self):
        location = original(self)
        builder._BUILT[case['built']]['expires_monotonic'] = time.monotonic()-1
        return location
    monkeypatch.setattr(_StagingRegistry, 'begin_blob', begin)
    report, capability = run(case)
    assert capability is None and report['decision'] == 'unknown'
    assert [row['method'] for row in registry['requests']] == ['HEAD', 'POST']


def test_real_response_deadline_stops_uncertain_mutation(registry):
    registry['fault'] = 'put-delay'
    client = _StagingRegistry(registry['origin'], repository=REPOSITORY, ca_bundle=registry['ca'],
        token_env='STAGING_TEST_TOKEN', deadline=time.monotonic() + .25)
    client.authenticate('sha256:' + 'a' * 64)
    location = client.begin_blob()
    raw = b'controlled deadline payload'
    started = time.monotonic()
    with pytest.raises(APIError, match='deadline'):
        client.finish_blob(location, io.BytesIO(raw), {'size': len(raw), 'digest': digest(raw)})
    assert time.monotonic() - started < 1
    assert client.receipts[-1]['outcome'] == 'deadline'
    assert registry['blobs'][digest(raw)] == raw
    with pytest.raises(APIError, match='authentication-required'):
        client.begin_blob()


def test_staging_type_cannot_be_imported_or_constructed(case):
    with pytest.raises(TypeError):
        stage.StagedMission()
    with pytest.raises(TypeError):
        pickle.dumps(object.__new__(stage.StagedMission))
    with pytest.raises(ValueError):
        stage.require_staged_mission({}, run_scope=case['run_scope'], source_commit=case['source_commit'],
            architecture='amd64', origin=case['_selected']['origin'], repository=REPOSITORY)


def test_typed_stage_guard_binds_scope_destination_expiry_and_process(case):
    # Exercise issuance plumbing with a test-injected input record. This is not
    # an end-to-end F0/build run and creates no portable authority constructor.
    case['_binding'].pop('execution_mode')
    report, capability = run(case)
    assert report['decision'] == 'pass' and capability is not None
    kwargs = {'run_scope': case['run_scope'], 'source_commit': case['source_commit'], 'architecture': 'amd64',
        'origin': case['_selected']['origin'], 'repository': REPOSITORY}
    assert stage.require_staged_mission(capability, **kwargs)['manifest_digest'] == case['_fixture']['manifest']
    for key, value in [('run_scope', {}), ('source_commit', 'b' * 40), ('architecture', 'arm64'),
                       ('origin', 'https://other.invalid'), ('repository', 'other/repository')]:
        with pytest.raises(ValueError):
            stage.require_staged_mission(capability, **{**kwargs, key: value})
    with pytest.raises(TypeError):
        pickle.dumps(capability)
    record = stage._STAGED[capability]
    record['pid'] += 1
    with pytest.raises(ValueError):
        stage.require_staged_mission(capability, **kwargs)
    record['pid'] = os.getpid()
    record['expires_monotonic'] = time.monotonic() - 1
    with pytest.raises(ValueError):
        stage.require_staged_mission(capability, **kwargs)


@pytest.mark.parametrize('change', ['deadline', 'build-expiry', 'layout-route', 'marker', 'archive'])
def test_terminal_success_publication_rechecks_and_withdraws(case, monkeypatch, change):
    case['_binding'].pop('execution_mode')
    original = stage._write
    def write(directory, name, value):
        result = original(directory, name, value)
        if name == 'mission-staging.json':
            assert value['decision'] == 'pass'
            if change == 'deadline':
                original_clock = time.monotonic
                monkeypatch.setattr(stage.time, 'monotonic', lambda: original_clock() + 1000)
            elif change == 'build-expiry':
                builder._BUILT[case['built']]['expires_monotonic'] = time.monotonic() - 1
            elif change == 'layout-route':
                layout = case['output'] / 'readback/layout'
                layout.rename(layout.with_name('retained-layout'))
                layout.mkdir()
            elif change == 'marker':
                (case['output'] / 'readback/layout/oci-layout').write_bytes(b'changed')
            else:
                (case['output'] / 'image.oci.tar').write_bytes(b'changed')
        return result
    monkeypatch.setattr(stage, '_write', write)
    with pytest.raises((ValueError, OSError)):
        run(case)
    assert not (case['output'] / 'mission-staging.json').exists()
    assert (case['output'] / 'mutation-001.json').exists()


def test_large_stream_does_not_use_metadata_body_limit(registry):
    raw = b'x' * 8_000_001
    client = _StagingRegistry(registry['origin'], repository=REPOSITORY, ca_bundle=registry['ca'],
        token_env='STAGING_TEST_TOKEN', deadline=time.monotonic() + 10)
    client.authenticate('sha256:' + 'a' * 64)
    location = client.begin_blob()
    client.finish_blob(location, io.BytesIO(raw), {'size': len(raw), 'digest': digest(raw)})
    assert client.receipts[-1]['sent_bytes'] == len(raw)
    assert registry['blobs'][digest(raw)] == raw


def test_cumulative_budget_prevents_next_body_before_request(registry, monkeypatch):
    from devsecops.adapters import staging_registry
    monkeypatch.setattr(staging_registry, 'MAX_UPLOAD', 5)
    client = _StagingRegistry(registry['origin'], repository=REPOSITORY, ca_bundle=registry['ca'],
        token_env='STAGING_TEST_TOKEN', deadline=time.monotonic() + 5)
    client.authenticate('sha256:' + 'a' * 64)
    location = client.begin_blob()
    client.finish_blob(location, io.BytesIO(b'1234'), {'size': 4, 'digest': digest(b'1234')})
    second = client.begin_blob()
    before = len(registry['requests'])
    with pytest.raises(APIError, match='byte-limit'):
        client.finish_blob(second, io.BytesIO(b'12'), {'size': 2, 'digest': digest(b'12')})
    assert len(registry['requests']) == before


def test_partial_send_charged_and_transport_poisoned(registry, monkeypatch):
    from devsecops.adapters import staging_registry
    client = _StagingRegistry(registry['origin'], repository=REPOSITORY, ca_bundle=registry['ca'],
        token_env='STAGING_TEST_TOKEN', deadline=time.monotonic() + 5)
    client.authenticate('sha256:' + 'a' * 64)
    location = client.begin_blob()
    original = staging_registry.http.client.HTTPSConnection.send
    payload = b'partial fixture body'
    def send(connection, value):
        if value == payload:
            original(connection, value[:5])
            raise OSError('controlled partial send')
        return original(connection, value)
    monkeypatch.setattr(staging_registry.http.client.HTTPSConnection, 'send', send)
    with pytest.raises(APIError, match='transport'):
        client.finish_blob(location, io.BytesIO(payload), {'size': len(payload), 'digest': digest(payload)})
    assert client._uploaded_bytes == len(payload)
    assert client.receipts[-1]['sent_bytes'] == 0
    with pytest.raises(APIError, match='authentication-required'):
        client.begin_blob()


def test_writer_manifest_selection_is_fixed_and_cannot_retry(registry):
    client = _StagingRegistry(registry['origin'], repository=REPOSITORY, ca_bundle=registry['ca'],
        token_env='STAGING_TEST_TOKEN', deadline=time.monotonic() + 5)
    raw = b'{"controlled":"manifest-binding-mechanics"}'
    selected = digest(raw)
    client.authenticate(selected)
    with pytest.raises(APIError, match='invalid-staging-manifest'):
        client.put_manifest(io.BytesIO(raw), {'digest': 'sha256:' + 'b' * 64, 'size': len(raw), 'mediaType': MANIFEST})
    assert len(registry['requests']) == 1
    client.put_manifest(io.BytesIO(raw), {'digest': selected, 'size': len(raw), 'mediaType': MANIFEST})
    with pytest.raises(APIError, match='invalid-staging-manifest'):
        client.put_manifest(io.BytesIO(raw), {'digest': selected, 'size': len(raw), 'mediaType': MANIFEST})
    with pytest.raises(APIError, match='invalid-staging-authentication'):
        client.authenticate(selected)
    assert len(registry['requests']) == 2


def test_replaced_archive_after_validation_is_rejected_before_auth(case, registry, monkeypatch):
    original = stage._oci_archive
    def validate(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        path.write_bytes(b'replaced after content validation')
        return result
    monkeypatch.setattr(stage, '_oci_archive', validate)
    report, capability = run(case)
    assert capability is None and report['checks']['upload'] == 'unknown'
    assert not registry['requests']


@pytest.mark.parametrize('boundary', ['authenticated', 'session-open'])
def test_retained_upload_source_change_stops_next_write(case, registry, monkeypatch, boundary):
    method = 'authenticate' if boundary == 'authenticated' else 'begin_blob'
    original = getattr(_StagingRegistry, method)
    def changed(self, *args):
        result = original(self, *args)
        path = case['output'] / 'image.oci.tar'
        path.chmod(0o600)
        path.write_bytes(b'changed retained source')
        return result
    monkeypatch.setattr(_StagingRegistry, method, changed)
    report, capability = run(case)
    assert capability is None and report['decision'] == 'unknown'
    assert [r['method'] for r in registry['requests']] == (['HEAD'] if boundary == 'authenticated' else ['HEAD', 'POST'])


def test_upload_stream_cannot_exceed_admitted_size(registry):
    client = _StagingRegistry(registry['origin'], repository=REPOSITORY, ca_bundle=registry['ca'],
        token_env='STAGING_TEST_TOKEN', deadline=time.monotonic() + 5)
    client.authenticate('sha256:' + 'a' * 64)
    location = client.begin_blob()
    class Oversized:
        def read(self, size):
            return b'x' * (size + 1)
    with pytest.raises(APIError, match='source-truncated'):
        client.finish_blob(location, Oversized(), {'size': 2, 'digest': digest(b'xx')})
    assert client._uploaded_bytes == 0 and client.receipts[-1]['sent_bytes'] == 0
