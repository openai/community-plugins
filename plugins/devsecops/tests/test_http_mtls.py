"""Real localhost mTLS and bounded failure tests; no hosted-service claims."""
import json
import os
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_argocd import ca

from devsecops.adapters.http import APIError, HTTPSReadClient

__all__ = ['ca']


@pytest.fixture
def mtls_server(ca):
    state = {'client_verified': False, 'trickle': None}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            state['client_verified'] = bool(self.connection.getpeercert())
            try:
                if state['trickle'] == 'headers':
                    for byte in b'HTTP/1.1 200 OK\r\nX-Slow: slowly-delivered-header\r\n':
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.01)
                    return
                raw = json.dumps({'authenticated': True}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                if state['trickle'] == 'body':
                    for byte in raw:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(.02)
                else:
                    self.wfile.write(raw)
            except (OSError, ssl.SSLError):
                pass

    class QuietServer(ThreadingHTTPServer):
        daemon_threads = True
        def handle_error(self, *_):
            pass

    server = QuietServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(ca[0]), str(ca[1]))
    context.load_verify_locations(cafile=str(ca[0]))
    context.verify_mode = ssl.CERT_REQUIRED
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    state['origin'] = f'https://127.0.0.1:{server.server_port}'
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_real_mutual_tls_requires_certificate_and_freezes_source_bytes(mtls_server, ca, tmp_path):
    with pytest.raises(APIError):
        HTTPSReadClient(mtls_server['origin'], ca_bundle=ca[0]).get_json('/api')
    paths = tuple(tmp_path.resolve() / name for name in ('certificate.pem', 'key.pem'))
    for source, path in zip(ca, paths, strict=True):
        path.write_bytes(source.read_bytes())
        path.chmod(0o600)
    client = HTTPSReadClient(mtls_server['origin'], ca_bundle=ca[0], client_certificate=paths)
    for path in paths:
        path.write_text('changed after construction')
    assert client.get_json('/api') == {'authenticated': True}
    assert mtls_server['client_verified']
    rendered = json.dumps(client.receipts)
    assert 'PRIVATE KEY' not in rendered
    assert str(paths[1]) not in rendered


@pytest.mark.parametrize('trickle', ['headers', 'body'])
def test_mtls_retains_absolute_header_and_body_deadlines(mtls_server, ca, trickle):
    mtls_server['trickle'] = trickle
    client = HTTPSReadClient(mtls_server['origin'], ca_bundle=ca[0], client_certificate=ca,
                             timeout=.15, total_timeout=.2)
    started = time.monotonic()
    with pytest.raises(APIError) as failure:
        client.get_json('/api')
    assert failure.value.code == 'deadline'
    assert client.receipts[-1]['outcome'] == 'deadline'
    assert time.monotonic() - started < .8


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'public-key-permissions', 'oversized', 'relative'])
def test_client_key_paths_fail_closed_without_hanging(ca, tmp_path, kind):
    key = tmp_path.resolve() / 'key'
    if kind == 'symlink':
        key.symlink_to(ca[1])
    elif kind == 'fifo':
        os.mkfifo(key, 0o600)
    elif kind == 'relative':
        key = 'relative.pem'
    else:
        key.write_bytes(b'x' * 1_000_001 if kind == 'oversized' else ca[1].read_bytes())
        key.chmod(0o644 if kind == 'public-key-permissions' else 0o600)
    started = time.monotonic()
    with pytest.raises(APIError) as failure:
        HTTPSReadClient('https://127.0.0.1', ca_bundle=ca[0], client_certificate=(ca[0], key))
    assert failure.value.code == 'client-certificate-unavailable'
    assert time.monotonic() - started < 1


def test_encrypted_key_does_not_prompt_or_expose_diagnostics(ca, tmp_path):
    encrypted = tmp_path.resolve() / 'encrypted.pem'
    subprocess.run(['openssl', 'pkey', '-in', str(ca[1]), '-aes-256-cbc',
                    '-passout', 'pass:unit-fixture-only', '-out', str(encrypted)],
                   check=True, capture_output=True, timeout=10)
    encrypted.chmod(0o600)
    started = time.monotonic()
    with pytest.raises(APIError) as failure:
        HTTPSReadClient('https://127.0.0.1', ca_bundle=ca[0], client_certificate=(ca[0], encrypted))
    assert str(failure.value) == 'Service evidence request could not complete (client-certificate-unavailable)'
    assert time.monotonic() - started < 1
