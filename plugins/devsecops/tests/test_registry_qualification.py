"""Actual localhost TLS and immutable OCI exchanges; never live Zot authority."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from devsecops.adapters.http import APIError
from devsecops.adapters.oci import INDEX, MANIFEST
from devsecops.factory import registry_qualification as qualification
from test_argocd import ca

__all__ = ['ca']


@pytest.fixture
def registry(ca, monkeypatch):
    auth = {}
    for actor in ('operator', 'reader'):
        monkeypatch.setenv(actor.upper()+'_USER', actor)
        monkeypatch.setenv(actor.upper()+'_PASS', 'private-'+actor+'-canary-password')
        auth[actor] = 'Basic '+base64.b64encode((actor+':private-'+actor+'-canary-password').encode()).decode()
    state = {'requests': [], 'blobs': {}, 'manifests': {}, 'sessions': 0, 'fault': None}
    prefix = '/v2/devsecops/controlled/qualification'
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *_):
            pass
        def respond(self, status, raw=b'', media='application/octet-stream', **headers):
            self.send_response(status)
            self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(raw)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(raw)
        def actor(self):
            state['requests'].append((self.command, self.path, self.headers.get('Authorization')))
            for actor, expected in auth.items():
                if self.headers.get('Authorization') == expected:
                    return actor
            if state['fault'] == 'anonymous':
                return 'reader'
            self.respond(401)
            return None
        def do_HEAD(self):
            if self.actor():
                self.respond(200 if self.path.split('/manifests/')[-1] in state['manifests'] else 404)
        def do_GET(self):
            if not self.actor():
                return
            path = urlsplit(self.path).path
            if '/referrers/' in path:
                if state['fault'] == 'no-referrers':
                    self.respond(404)
                    return
                subject = path.split('/referrers/')[-1]
                rows = []
                if state['fault'] != 'missing-discovery':
                    for raw in state['manifests'].values():
                        obj = json.loads(raw)
                        if obj.get('subject', {}).get('digest') == subject:
                            row = qualification.descriptor(raw, MANIFEST)
                            row['artifactType'] = qualification.ARTIFACT
                            rows.append(row)
                self.respond(200, qualification.canonical({'schemaVersion': 2, 'mediaType': INDEX, 'manifests': rows}), INDEX)
            elif '/manifests/' in path:
                raw = state['manifests'].get(path.split('/manifests/')[-1])
                self.respond(200 if raw else 404, raw or b'', MANIFEST)
            else:
                raw = state['blobs'].get(path.split('/blobs/')[-1])
                if raw and state['fault'] == 'readback-corrupt':
                    raw = raw+b'corrupt'
                self.respond(200 if raw else 404, raw or b'')
        def do_POST(self):
            actor = self.actor()
            if not actor:
                return
            if actor == 'reader' and state['fault'] != 'reader-write':
                self.respond(403)
                return
            state['sessions'] += 1
            if state['fault'] == 'post-disconnect':
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                self.close_connection = True
                return
            self.respond(202, Location=prefix+'/blobs/uploads/session-'+str(state['sessions']))
        def do_PUT(self):
            if self.actor() != 'operator':
                return
            raw = self.rfile.read(int(self.headers['Content-Length']))
            parsed = urlsplit(self.path)
            if '/blobs/uploads/' in parsed.path:
                digest = parse_qs(parsed.query)['digest'][0]
                state['blobs'][digest] = raw
                self.respond(201, Location=prefix+'/blobs/'+digest, **{'Docker-Content-Digest': digest})
            else:
                digest = parsed.path.split('/manifests/')[-1]
                state['manifests'][digest] = raw
                self.respond(201, Location=prefix+'/manifests/'+digest, **{'Docker-Content-Digest': digest})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(ca[0]), str(ca[1]))
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    state.update(origin='https://127.0.0.1:'+str(server.server_port), ca=ca[0])
    yield state
    server.shutdown()
    thread.join(timeout=3)
    server.server_close()


def invoke(registry, output):
    output.mkdir(exist_ok=True)
    return qualification.qualify(origin=registry['origin'], repository='devsecops/controlled/qualification',
        ca_bundle=registry['ca'], binding='a'*64, output=output,
        operator={'basic_username_env': 'OPERATOR_USER', 'basic_password_env': 'OPERATOR_PASS'},
        reader={'basic_username_env': 'READER_USER', 'basic_password_env': 'READER_PASS'})


def test_real_tls_upload_referrers_and_separate_reader_negative_authorization(registry, tmp_path):
    report = invoke(registry, tmp_path/'out')
    assert report['status'] == 'pass' and report['mutation_effect'] == 'applied'
    assert report['kind'] == 'registry-storage-qualification-only' and report['canonical_success'] is False
    assert report['anonymous_read'] == 'denied-401' and report['reader_write'] == 'denied-403'
    assert len(registry['blobs']) == 2 and len(registry['manifests']) == 2
    assert registry['sessions'] == 2 and len(report['effects']) == 7
    assert len(list((tmp_path/'out').glob('intent-*.json'))) == 7
    for path in (tmp_path/'out').iterdir():
        assert b'canary-password' not in path.read_bytes()
        assert b'Basic ' not in path.read_bytes()


@pytest.mark.parametrize('fault', ['no-referrers', 'missing-discovery', 'anonymous', 'reader-write',
                                  'post-disconnect', 'readback-corrupt'])
def test_failure_never_retries_ambiguous_write_or_creates_authority(registry, tmp_path, fault):
    registry['fault'] = fault
    report = invoke(registry, tmp_path/'out')
    assert report['status'] == 'unknown' and not report['canonical_success']
    assert report['mutation_effect'] == ('not_applied' if fault == 'no-referrers' else 'unknown')
    if fault == 'no-referrers':
        assert not report['effects'] and not any(row[0] == 'POST' for row in registry['requests'])
    if fault == 'post-disconnect':
        assert registry['sessions'] == 1 and len(report['effects']) == 1
        assert report['effects'][0]['outcome'] == 'unknown'
    if fault == 'reader-write':
        assert registry['sessions'] == 3


def test_terminal_deadline_expiry_withdraws_pass_but_keeps_write_intents(registry, tmp_path, monkeypatch):
    original = qualification._write
    def delayed(directory, name, value):
        identity = original(directory, name, value)
        if name == 'qualification.json':
            monkeypatch.setattr(qualification.time, 'monotonic', lambda: 10**12)
        return identity
    monkeypatch.setattr(qualification, '_write', delayed)
    with pytest.raises(APIError, match='deadline'):
        invoke(registry, tmp_path/'out')
    assert not (tmp_path/'out/qualification.json').exists()
    assert list((tmp_path/'out').glob('intent-*.json'))


def test_certificate_rejection_does_not_attempt_upload(registry, tmp_path, monkeypatch):
    monkeypatch.setitem(registry, 'ca', Path('/does-not-exist'))
    report = invoke(registry, tmp_path/'out')
    assert report['status'] == 'unknown' and report['mutation_effect'] == 'not_applied'
    assert not registry['requests']


@pytest.mark.parametrize('prior_fault', [None, 'post-disconnect'])
def test_existing_success_or_failure_receipt_is_preserved_without_requests(registry, tmp_path, prior_fault):
    registry['fault'] = prior_fault
    output = tmp_path/'out'
    prior = invoke(registry, output)
    assert prior['status'] == ('pass' if prior_fault is None else 'unknown')
    retained = {path.name: path.read_bytes() for path in output.iterdir()}
    requests = list(registry['requests'])
    with pytest.raises(ValueError, match='must be empty'):
        invoke(registry, output)
    assert registry['requests'] == requests
    assert {path.name: path.read_bytes() for path in output.iterdir()} == retained


def test_existing_attempt_without_terminal_receipt_cannot_be_retried(registry, tmp_path):
    output = tmp_path/'out'
    output.mkdir()
    (output/'attempt.json').write_bytes(b'earlier interrupted invocation')
    with pytest.raises(ValueError, match='must be empty'):
        invoke(registry, output)
    assert not registry['requests']
    assert (output/'attempt.json').read_bytes() == b'earlier interrupted invocation'


def test_terminal_exclusive_conflict_preserves_receipt_it_did_not_create(registry, tmp_path, monkeypatch):
    original = qualification._write
    prior = b'concurrent retained terminal evidence'
    def conflict(directory, name, value):
        if name == 'qualification.json':
            (tmp_path/'out'/name).write_bytes(prior)
        return original(directory, name, value)
    monkeypatch.setattr(qualification, '_write', conflict)
    with pytest.raises(FileExistsError):
        invoke(registry, tmp_path/'out')
    assert (tmp_path/'out/qualification.json').read_bytes() == prior
    assert (tmp_path/'out/attempt.json').is_file()
