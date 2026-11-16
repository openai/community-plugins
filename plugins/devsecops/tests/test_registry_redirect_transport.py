"""Real local TLS exchanges for scoped blob downloads; no Registry1 qualification."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import socket
import ssl
import subprocess
import threading
import time
from urllib.parse import urlencode

import pytest

from devsecops.adapters.http import APIError, HTTPSReadClient
from devsecops.adapters.http_blob import HTTPSBlobReadClient
from devsecops.adapters.oci import _RegistryClient, _RegistrySession
from devsecops.adapters.registry_download_policy import (
    RegistryBlobPolicy, RedirectPolicyError, load_blob_download_policy, parse_blob_download_policy,
)

REPO = 'selected/image'
BODY = b'actual-local-blob'
DIGEST = 'sha256:' + hashlib.sha256(BODY).hexdigest()
SOURCE_PATH = '/v2/' + REPO + '/blobs/' + DIGEST
ACCESS = 'SYNTHETICACCESSKEY000'
SIGNATURE = 'd' * 64
SECRET = 'REGISTRY-CREDENTIAL-CANARY'
ACCEPT = ('application/octet-stream',)


def signed_url(origin):
    return origin + '/blob/data?' + urlencode({
        'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
        'X-Amz-Credential': ACCESS + '/20261103/us-gov-west-1/s3/aws4_request',
        'X-Amz-Date': '20261103T030000Z', 'X-Amz-Expires': '300',
        'X-Amz-Signature': SIGNATURE, 'X-Amz-SignedHeaders': 'host'})


@pytest.fixture(scope='module')
def tls_material(tmp_path_factory):
    root = tmp_path_factory.mktemp('redirect-tls')
    cert, key = root / 'cert.pem', root / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
        '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost',
        '-addext', 'subjectAltName=DNS:localhost'], check=True, capture_output=True, timeout=15)
    cert.chmod(0o600)
    key.chmod(0o600)
    return cert, key


@contextmanager
def tls_server(material):
    state = {'requests': [], 'responses': []}
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *_):
            pass
        def do_GET(self):
            state['requests'].append({'path': self.path, 'authorization': self.headers.get('Authorization'),
                'cookie': self.headers.get('Cookie'), 'certificate': self.connection.getpeercert()})
            spec = state['responses'].pop(0) if state['responses'] else {}
            if spec.get('entered'):
                spec['entered'].set()
            if spec.get('wait'):
                spec['wait'].wait(2)
            if spec.get('delay'):
                time.sleep(spec['delay'])
            try:
                self.send_response(spec.get('status', 200))
                for key, value in spec.get('headers', []):
                    self.send_header(key, value)
                self.send_header('Content-Type', spec.get('media', 'application/octet-stream'))
                raw = spec.get('body', BODY)
                if not spec.get('no_length'):
                    self.send_header('Content-Length', str(spec.get('length', len(raw))))
                self.send_header('Connection', 'close')
                self.end_headers()
                for chunk in spec.get('chunks', [raw]):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    if spec.get('trickle'):
                        time.sleep(spec['trickle'])
            except (OSError, ssl.SSLError):
                pass
    class QuietServer(ThreadingHTTPServer):
        daemon_threads = True
        def handle_error(self, *_):
            pass
    server = QuietServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(material[0]), str(material[1]))
    context.load_verify_locations(cafile=str(material[0]))
    context.verify_mode = ssl.CERT_OPTIONAL
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    worker.start()
    state['origin'] = 'https://localhost:' + str(server.server_port)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.fixture
def pair(tls_material, monkeypatch):
    defaults = ssl.get_default_verify_paths()
    # Model platform roots explicitly. Environment override paths remain invalid,
    # proving the storage transport does not inherit the registry's CA selection.
    monkeypatch.setattr(ssl, 'get_default_verify_paths', lambda: defaults._replace(
        openssl_cafile=str(tls_material[0]), openssl_capath='/nonexistent-platform-ca-directory'))
    monkeypatch.setenv('SSL_CERT_FILE', '/must-not-load-private-registry-ca')
    monkeypatch.setenv('SSL_CERT_DIR', '/must-not-load-private-registry-ca-directory')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('REDIRECT_REGISTRY_USER', 'registry-user')
    monkeypatch.setenv('REDIRECT_REGISTRY_PASSWORD', SECRET)
    with tls_server(tls_material) as source, tls_server(tls_material) as storage:
        source['platform_roots'] = defaults
        source['responses'] = [{'status': 307, 'headers': [('Location', signed_url(storage['origin']))]}]
        storage['responses'] = [{'body': BODY}]
        yield source, storage, tls_material


def session(pair, *, streaming=False, policy=True, **kwargs):
    source, storage, material = pair
    cls = HTTPSBlobReadClient if streaming else _RegistryClient
    client = cls(source['origin'], ca_bundle=material[0],
        basic_username_env='REDIRECT_REGISTRY_USER', basic_password_env='REDIRECT_REGISTRY_PASSWORD',
        client_certificate=material, **kwargs)
    selected = RegistryBlobPolicy(source['origin'], (REPO,), (storage['origin'],)) if policy else None
    return _RegistrySession(client, repository=REPO, token_path='/service/token',
        token_service='registry-service', blob_download_policy=selected)


def get(value, *, streaming=False, tmp_path=None, expected_size=len(BODY)):
    if not streaming:
        return value.request(SOURCE_PATH, ACCEPT, expected_size=expected_size)
    path = tmp_path / 'layer'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        return value.blob(DIGEST, fd=fd, expected_size=expected_size, accept=ACCEPT)
    finally:
        os.close(fd)


@pytest.mark.parametrize('streaming', [False, True])
def test_separate_storage_peer_strips_credentials_certificates_and_private_trust(pair, tmp_path, streaming):
    value = session(pair, streaming=streaming)
    result = get(value, streaming=streaming, tmp_path=tmp_path)
    source, storage, _ = pair
    assert result.status == 200
    assert source['requests'][0]['authorization'].startswith('Basic ')
    assert source['requests'][0]['certificate']
    assert storage['requests'][0]['authorization'] is None
    assert storage['requests'][0]['cookie'] is None
    assert not storage['requests'][0]['certificate']
    assert value.client._requests == 2 and value.client._bytes == len(BODY)
    assert [r['request_index'] for r in value.client.receipts] == [1, 2]
    assert value.client.receipts[1]['role'] == 'registry-blob-storage'
    exported = repr(result) + json.dumps(value.client.receipts)
    assert all(s not in exported for s in (ACCESS, SIGNATURE, SECRET, signed_url(storage['origin'])))
    assert ((tmp_path / 'layer').read_bytes() if streaming else result.body) == BODY


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('status', [401, 403, 307])
def test_storage_denial_or_second_redirect_never_exchanges_registry_identity(pair, tmp_path, status, streaming):
    source, storage, _ = pair
    storage['responses'] = [{'status': status, 'headers': [
        ('Location', signed_url(source['origin'])),
        ('WWW-Authenticate', 'Bearer realm="'+source['origin']+'/service/token",service="registry-service",scope="repository:'+REPO+':pull"')]}]
    value = session(pair, streaming=streaming)
    with pytest.raises(APIError, match='blob-download-(denied|redirect)'):
        get(value, streaming=streaming, tmp_path=tmp_path)
    assert len(source['requests']) == len(storage['requests']) == 1
    assert value.authentication['token_exchange'] == 'not-attempted'
    if streaming:
        assert (tmp_path / 'layer').read_bytes() == b''


@pytest.mark.parametrize('case', ['disabled', 'wrong-origin', 'duplicate-location', 'missing-location', 'unsupported-status'])
def test_redirect_is_opt_in_exact_and_unambiguous_before_storage_request(pair, case):
    source, storage, _ = pair
    value = session(pair, policy=case != 'disabled')
    if case == 'wrong-origin':
        source['responses'][0]['headers'] = [('Location', signed_url('https://attacker.invalid'))]
    elif case == 'duplicate-location':
        source['responses'][0]['headers'] *= 2
    elif case == 'missing-location':
        source['responses'][0]['headers'] = []
    elif case == 'unsupported-status':
        source['responses'][0]['status'] = 302
    with pytest.raises(APIError):
        get(value)
    assert storage['requests'] == []


@pytest.mark.parametrize('streaming', [False, True])
def test_source_and_storage_share_request_limit(pair, tmp_path, streaming):
    value = session(pair, streaming=streaming, max_requests=1)
    with pytest.raises(APIError, match='request-limit'):
        get(value, streaming=streaming, tmp_path=tmp_path)
    assert value.client._requests == 1 and pair[1]['requests'] == []


@pytest.mark.parametrize('streaming', [False, True])
def test_partial_storage_reads_consume_budget_and_cannot_retry_with_fresh_caps(pair, tmp_path, streaming):
    value = session(pair, streaming=streaming, max_total_bytes=5)
    pair[1]['responses'] = [{'body': BODY, 'no_length': True}]
    with pytest.raises(APIError, match='body-limit'):
        get(value, streaming=streaming, tmp_path=tmp_path)
    assert value.client._bytes == 6
    assert value.client.receipts[-1]['body_bytes'] == 6
    pair[0]['responses'] = [{'body': BODY}]
    with pytest.raises(APIError, match='body-limit'):
        value.request(SOURCE_PATH, ACCEPT, expected_size=len(BODY))
    assert value.client._bytes == 6


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('bad', ['digest', 'size', 'header', 'encoding'])
def test_buffered_and_streamed_integrity_fail_closed(pair, tmp_path, streaming, bad):
    response = pair[1]['responses'][0]
    if bad == 'digest':
        response['body'] = b'x' * len(BODY)
    elif bad == 'size':
        response['body'] = BODY[:-1]
    elif bad == 'header':
        response['headers'] = [('Docker-Content-Digest', 'sha256:' + 'f' * 64)]
    else:
        response['headers'] = [('Content-Encoding', 'gzip')]
    with pytest.raises(APIError):
        get(session(pair, streaming=streaming), streaming=streaming, tmp_path=tmp_path)


def test_token_response_source_retry_and_storage_consume_same_accounting(pair):
    source, storage, _ = pair
    token = b'{"token":"SYNTHETIC-BEARER-TOKEN"}'
    redirect = source['responses'][0]
    source['responses'] = [
        {'status': 401, 'headers': [('WWW-Authenticate', 'Bearer realm="'+source['origin']+'/service/token",service="registry-service",scope="repository:'+REPO+':pull"')]},
        {'body': token, 'media': 'application/json'}, redirect]
    value = session(pair)
    assert get(value).body == BODY
    assert value.client._requests == 4 and value.client._bytes == len(token) + len(BODY)
    assert source['requests'][-1]['authorization'] == 'Bearer SYNTHETIC-BEARER-TOKEN'
    assert storage['requests'][0]['authorization'] is None


def test_whole_session_is_serialized_across_redirect_followup(pair):
    entered, release = threading.Event(), threading.Event()
    pair[1]['responses'][0].update(entered=entered, wait=release)
    value, results = session(pair), []
    worker = threading.Thread(target=lambda: results.append(get(value)))
    worker.start()
    try:
        assert entered.wait(2)
        with pytest.raises(APIError, match='concurrent-request'):
            get(value)
    finally:
        release.set()
        worker.join(3)
    assert len(results) == 1 and results[0].body == BODY


@pytest.mark.parametrize('streaming', [False, True])
def test_storage_cannot_reset_original_deadline(pair, tmp_path, streaming):
    pair[0]['responses'][0]['delay'] = .12
    pair[1]['responses'][0]['delay'] = .15
    value = session(pair, streaming=streaming, timeout=.3, total_timeout=.22)
    before = time.monotonic()
    with pytest.raises(APIError, match='deadline'):
        get(value, streaming=streaming, tmp_path=tmp_path)
    assert time.monotonic() - before < .65
    assert value.client._requests == 2


@pytest.mark.parametrize('stage', ['dns', 'headers', 'body'])
def test_followup_stalls_keep_absolute_deadline(pair, monkeypatch, stage):
    source, storage, _ = pair
    value = session(pair, timeout=.12, total_timeout=.14)
    if stage == 'dns':
        resolve = socket.getaddrinfo
        port = int(storage['origin'].rsplit(':', 1)[1])
        def slow(host, selected_port, **kwargs):
            if selected_port == port:
                time.sleep(.3)
            return resolve(host, selected_port, **kwargs)
        monkeypatch.setattr(socket, 'getaddrinfo', slow)
    elif stage == 'headers':
        storage['responses'][0]['delay'] = .3
    else:
        storage['responses'][0].update(chunks=[b'a'] * len(BODY), trickle=.04)
    before = time.monotonic()
    with pytest.raises(APIError, match='deadline') as caught:
        get(value)
    assert time.monotonic() - before < .6
    assert value.client._requests == 2
    assert all(s not in repr(caught.value) + json.dumps(value.client.receipts) for s in (ACCESS, SIGNATURE, SECRET))
    if stage == 'dns':
        assert storage['requests'] == []
    if stage == 'body':
        assert 0 < value.client._bytes < len(BODY)


def test_followup_stalled_tls_keeps_absolute_deadline(pair):
    source, storage, _ = pair
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(2)
    started = threading.Event()
    def stall():
        try:
            connection, _ = listener.accept()
            with connection:
                started.set()
                time.sleep(.3)
        except OSError:
            pass
    worker = threading.Thread(target=stall, daemon=True)
    worker.start()
    storage['origin'] = 'https://localhost:' + str(listener.getsockname()[1])
    source['responses'][0]['headers'] = [('Location', signed_url(storage['origin']))]
    value = session(pair, timeout=.12, total_timeout=.14)
    before = time.monotonic()
    try:
        with pytest.raises(APIError, match='deadline'):
            get(value)
        assert started.is_set() and time.monotonic() - before < .6
        assert value.client._requests == 2 and value.client._bytes == 0
    finally:
        listener.close()
        worker.join(1)


def test_storage_certificate_failure_cannot_fall_back_to_source_ca(pair, monkeypatch):
    # The actual platform roots do not trust our test server. A caller-selected
    # registry CA may not make the independently authenticated storage peer pass.
    monkeypatch.setattr(ssl, 'get_default_verify_paths', lambda: pair[0]['platform_roots'])
    value = session(pair)
    with pytest.raises(APIError, match='blob-download-tls'):
        get(value)
    assert pair[1]['requests'] == []


@pytest.mark.parametrize('path', ['/service/token', '/v2/' + REPO + '/manifests/' + DIGEST])
def test_default_metadata_and_manifest_redirects_stay_disabled(pair, path):
    value = session(pair)
    with pytest.raises(APIError):
        if path == '/service/token':
            value.client.get_json(path)
        else:
            value.request(path, ACCEPT)
    assert pair[1]['requests'] == []


def test_policy_loader_preserves_pin_and_intersects_existing_scope(tmp_path):
    document = {'schema_version': 1, 'kind': 'registry-blob-download-policy',
        'registry_origin': 'https://registry.example', 'repositories': [REPO, 'other/repo'],
        'storage_origins': ['https://storage.example']}
    raw = json.dumps(document).encode()
    path = tmp_path / 'policy.json'
    path.write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    selected = load_blob_download_policy(path, pin, registry_origin=document['registry_origin'],
        allowed_repositories=[REPO])
    assert selected.repositories == (REPO,) and selected.policy_sha256 == pin
    assert parse_blob_download_policy(raw).repositories == (REPO, 'other/repo')
    assert selected.identity_record()['repositories'] == [REPO]
    with pytest.raises(RedirectPolicyError, match='policy-service-scope-mismatch'):
        load_blob_download_policy(path, pin, registry_origin=document['registry_origin'], allowed_repositories=['unselected/repo'])
    with pytest.raises(RedirectPolicyError, match='policy-identity-mismatch'):
        load_blob_download_policy(path, 'f' * 64, registry_origin=document['registry_origin'], allowed_repositories=[REPO])


def attestation_selection(pair, raw, *, size=None):
    from devsecops.adapters.registry_attestation_policy import parse_attestation_size_policy
    digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
    size = len(raw) if size is None else size
    selected = parse_attestation_size_policy(json.dumps({
        'schema_version': 1, 'kind': 'registry-attestation-size-policy',
        'origin': pair[0]['origin'], 'repository': REPO, 'subject_digest': 'sha256:' + '1' * 64,
        'descriptors': [{'digest': digest, 'size_bytes': size}],
    }).encode())
    return digest, selected.select_blob(digest, size)


@pytest.mark.parametrize('redirected', [False, True])
@pytest.mark.parametrize('size', [8_000_001, 8 * 1024**2])
def test_selected_attestation_exact_boundary_keeps_global_limits(pair, redirected, size):
    raw = b'a' * size
    digest, selected = attestation_selection(pair, raw)
    value = session(pair)
    receiver = pair[1] if redirected else pair[0]
    receiver['responses'] = [{'body': raw}]
    path = '/v2/' + REPO + '/blobs/' + digest
    assert value.request(path, ACCEPT, expected_size=size, attestation_selection=selected).body == raw
    assert value.client._max_body == 2_000_000
    assert value.client._max_total == 16_000_000
    assert value.client._bytes == size
    assert value.client._requests == (2 if redirected else 1)
    before = len(pair[0]['requests'])
    with pytest.raises(APIError, match='object-limit'):
        value.request(path, ACCEPT, expected_size=size)
    assert len(pair[0]['requests']) == before
    assert all(s not in json.dumps(value.client.receipts) for s in (ACCESS, SIGNATURE, SECRET))


@pytest.mark.parametrize('redirected', [False, True])
@pytest.mark.parametrize('no_length', [False, True])
def test_selected_attestation_rejects_one_byte_over_descriptor(pair, redirected, no_length):
    size = 8 * 1024**2
    raw = b'a' * size
    digest, selected = attestation_selection(pair, raw)
    value = session(pair)
    receiver = pair[1] if redirected else pair[0]
    receiver['responses'] = [{'body': raw + b'x', 'no_length': no_length}]
    with pytest.raises(APIError, match='body-limit'):
        value.request('/v2/' + REPO + '/blobs/' + digest, ACCEPT,
            expected_size=size, attestation_selection=selected)
    assert value.client._bytes == (size + 1 if no_length else 0)
    assert value.client.receipts[-1]['outcome'] == 'body-limit'
    assert value.client._max_body == 2_000_000


def test_selected_attestation_keeps_shared_byte_budget(pair):
    raw = b'a' * 8_000_001
    digest, selected = attestation_selection(pair, raw)
    value = session(pair, max_total_bytes=len(raw) - 1)
    pair[1]['responses'] = [{'body': raw, 'no_length': True}]
    with pytest.raises(APIError, match='body-limit'):
        value.request('/v2/' + REPO + '/blobs/' + digest, ACCEPT,
            expected_size=len(raw), attestation_selection=selected)
    assert value.client._bytes == len(raw)
    assert value.client._requests == 2
    assert value.client._max_total == len(raw) - 1
    with pytest.raises(APIError, match='body-limit'):
        value.request(SOURCE_PATH, ACCEPT, expected_size=len(BODY))
    assert value.client._bytes == len(raw)


def test_selected_attestation_keeps_shared_request_budget(pair):
    digest, selected = attestation_selection(pair, BODY)
    value = session(pair, max_requests=1)
    with pytest.raises(APIError, match='request-limit'):
        value.request('/v2/' + REPO + '/blobs/' + digest, ACCEPT,
            expected_size=len(BODY), attestation_selection=selected)
    assert value.client._requests == 1 and pair[1]['requests'] == []


@pytest.mark.parametrize('damage', ['forged', 'replaced', 'mutated', 'digest', 'size', 'manifest', 'token'])
def test_attestation_scope_or_forgery_fails_before_network(pair, damage):
    from devsecops.adapters.registry_attestation_policy import SelectedAttestationBlob
    digest, selected = attestation_selection(pair, BODY)
    path, size = '/v2/' + REPO + '/blobs/' + digest, len(BODY)
    if damage == 'forged':
        selected = SelectedAttestationBlob(selected.origin, selected.repository, selected.subject_digest,
            selected.digest, selected.size_bytes, selected.policy_sha256)
    elif damage == 'replaced':
        selected = replace(selected)
    elif damage == 'mutated':
        object.__setattr__(selected, 'policy_sha256', 'f' * 64)
    elif damage == 'digest':
        path = '/v2/' + REPO + '/blobs/sha256:' + 'f' * 64
    elif damage == 'size':
        size += 1
    elif damage == 'manifest':
        path = '/v2/' + REPO + '/manifests/' + digest
    else:
        path = '/service/token'
    with pytest.raises(APIError):
        session(pair).request(path, ACCEPT, expected_size=size, attestation_selection=selected)
    assert pair[0]['requests'] == pair[1]['requests'] == []


@pytest.mark.parametrize('damage', ['replaced', 'path', 'origin', 'query', 'json', 'stream'])
def test_internal_attestation_allowance_is_bound_to_exact_request(pair, damage):
    from devsecops.adapters.http import _attestation_body_allowance
    digest, selected = attestation_selection(pair, BODY)
    allowance = _attestation_body_allowance(selected, origin=pair[0]['origin'],
        repository=REPO, digest=digest, size_bytes=len(BODY))
    value = session(pair).client
    path, params = SOURCE_PATH, None
    if damage == 'replaced':
        allowance = replace(allowance)
    elif damage == 'path':
        path = '/service/token'
    elif damage == 'origin':
        value = HTTPSReadClient(pair[1]['origin'], ca_bundle=pair[2][0])
    elif damage == 'query':
        params = {'unexpected': 'value'}
    options = {'as_json': damage == 'json', 'accept': ACCEPT, '_attestation_allowance': allowance}
    if damage == 'stream':
        options['_stream'] = object()
    # JSON routes reject the blob digest's colon before reaching allowance
    # admission; both paths stop before a connection is attempted.
    expected = 'invalid-request' if damage == 'json' else 'invalid-attestation-allowance'
    with pytest.raises(APIError, match=expected):
        value._request(path, params, **options)
    assert pair[0]['requests'] == pair[1]['requests'] == []


@pytest.mark.parametrize('redirected', [False, True])
def test_selected_attestation_still_verifies_digest(pair, redirected):
    raw = b'a' * 8_000_001
    digest, selected = attestation_selection(pair, raw)
    receiver = pair[1] if redirected else pair[0]
    receiver['responses'] = [{'body': b'b' * len(raw)}]
    with pytest.raises(APIError, match='digest-mismatch'):
        session(pair).request('/v2/' + REPO + '/blobs/' + digest, ACCEPT,
            expected_size=len(raw), attestation_selection=selected)


def test_generic_http_configuration_still_rejects_above_eight_million(pair):
    with pytest.raises(APIError, match='invalid-configuration'):
        HTTPSReadClient(pair[0]['origin'], ca_bundle=pair[2][0], max_body_bytes=8_000_001)
