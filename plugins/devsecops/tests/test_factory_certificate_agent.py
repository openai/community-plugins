"""Controlled protocol fixtures; no existing user agent or network is touched."""
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time

import pytest

from devsecops.factory.tcg import _certificate_identity
from devsecops.factory import certificate_agent
from devsecops.factory.certificate_agent import CertificateAgent, Reader, receive, socket_root, wire


@pytest.fixture
def adapter(tmp_path):
    root = tmp_path.resolve()
    for name, kind in [('ca', 'ed25519'), ('key', 'ecdsa')]:
        subprocess.run(['ssh-keygen', '-q', '-t', kind, '-N', '', '-f', str(root / name)],
                       check=True, capture_output=True, timeout=10)
    subprocess.run(['ssh-keygen', '-q', '-s', str(root / 'ca'), '-I', 'unit-only', '-n', 'operator',
                    '-V', '-1m:+1h', str(root / 'key.pub')], check=True, capture_output=True, timeout=10)
    cert = (root / 'key-cert.pub').read_bytes()
    with tempfile.TemporaryDirectory(prefix='ds-test-', dir=socket_root()) as temporary:
        path = Path(temporary) / 'upstream'
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            server.listen(1)
            value = CertificateAgent(cert, _certificate_identity(cert), path, b'pinned-host-key', 'operator',
                                     time.monotonic() + 30)
            value.test_upstream_server = server
            yield value


def binding(agent, **changes):
    values = dict(host=agent.host_key, session=b's' * 32, signature=b'host-signature', forwarded=b'\x00')
    values.update(changes)
    return b'\x1b' + wire(b'session-bind@openssh.com') + wire(values['host']) + wire(values['session']) + wire(values['signature']) + values['forwarded']


def auth(agent, **changes):
    values = dict(session=b's' * 32, user=b'operator', service=b'ssh-connection',
                  method=b'publickey-hostbound-v00@openssh.com', algorithm=agent.grant['algorithm'].encode(),
                  cert=agent.cert, host=agent.host_key)
    values.update(changes)
    return wire(values['session']) + b'\x32' + wire(values['user']) + wire(values['service']) + wire(values['method']) + b'\x01' + wire(values['algorithm']) + wire(values['cert']) + wire(values['host'])


def request(agent, data=None, key=None, flags=0):
    return b'\x0d' + wire(agent.cert if key is None else key) + wire(auth(agent) if data is None else data) + struct.pack('>I', flags)


def test_only_exact_certificate_is_advertised(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'exchange', lambda body: b'\x0c' + struct.pack('>I', 2) + wire(b'other-user-key') + wire(b'other') + wire(adapter.key) + wire(b'existing-key'))
    reply = Reader(adapter.handle(b'\x0b'))
    assert reply.take(1) == b'\x0c' and reply.take(4) == struct.pack('>I', 1)
    assert reply.string() == adapter.cert
    reply.string()
    reply.done()


def test_signed_payload_is_unchanged_and_only_signer_reference_is_translated(adapter, monkeypatch):
    forwarded = []
    response = b'\x0e' + wire(wire(b'ecdsa-sha2-nistp256') + wire(b'unit-signature'))
    def exchange(body):
        forwarded.append(body)
        return b'\x05' if body[:1] == b'\x1b' else response
    monkeypatch.setattr(adapter, 'exchange', exchange)
    assert adapter.handle(binding(adapter)) == b'\x05'
    data = auth(adapter)
    assert adapter.handle(request(adapter, data)) == response
    sent = Reader(forwarded[-1])
    assert sent.take(1) == b'\x0d' and sent.string() == adapter.key
    assert sent.string() == data and sent.take(4) == b'\x00' * 4
    sent.done()
    assert adapter.audit['signatures'] == 1


@pytest.mark.parametrize('change', [{'user': b'other'}, {'host': b'other-host'}, {'session': b'x' * 32},
    {'service': b'other'}, {'method': b'publickey'}, {'algorithm': b'ecdsa-sha2-nistp256'}, {'cert': b'other-cert'}])
def test_signing_scope_changes_fail_before_upstream_signing(adapter, monkeypatch, change):
    adapter.session = b's' * 32
    monkeypatch.setattr(adapter, 'exchange', lambda body: pytest.fail('out-of-scope signing reached upstream'))
    assert adapter.handle(request(adapter, auth(adapter, **change))) == b'\x05'


@pytest.mark.parametrize('body', [b'\x11', b'\x12', b'\x13', b'\x16', b'\x1b\x00\x00\x00\x03bad', b'\x0bextra'])
def test_mutation_and_unknown_operations_are_rejected(adapter, monkeypatch, body):
    monkeypatch.setattr(adapter, 'exchange', lambda body: pytest.fail('operation reached upstream'))
    assert adapter.handle(body) == b'\x05'


def test_raw_signing_and_unbound_session_are_rejected(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'exchange', lambda body: pytest.fail('operation reached upstream'))
    assert adapter.handle(request(adapter)) == b'\x05'
    adapter.session = b's' * 32
    assert adapter.handle(request(adapter, key=adapter.key)) == b'\x05'
    assert adapter.handle(request(adapter, flags=2)) == b'\x05'


@pytest.mark.parametrize('change', [{'host': b'other'}, {'session': b'short'}, {'forwarded': b'\x01'}, {'signature': b''}])
def test_other_host_forwarding_and_invalid_binding_are_rejected(adapter, monkeypatch, change):
    monkeypatch.setattr(adapter, 'exchange', lambda body: pytest.fail('invalid binding reached upstream'))
    assert adapter.handle(binding(adapter, **change)) == b'\x05'


def test_private_frontend_socket_is_removed_without_touching_upstream(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'exchange', lambda body: b'\x0c' + struct.pack('>I', 1) + wire(adapter.key) + wire(b'original'))
    with adapter:
        path, directory = adapter.path, adapter.directory
        assert path.stat().st_mode & 0o777 == 0o600
        assert directory.stat().st_mode & 0o777 == 0o700
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(2)
            client.connect(str(path))
            client.sendall(wire(b'\x0b'))
            assert receive(client, time.monotonic() + 2).startswith(b'\x0c\x00\x00\x00\x01')
    assert not path.exists() and not directory.exists() and adapter.upstream.exists()


def test_expired_certificate_and_excessive_packet_size_fail_closed(adapter, monkeypatch):
    adapter.grant['valid_before'] = time.time() - 1
    monkeypatch.setattr(adapter, 'exchange', lambda body: pytest.fail('expired certificate reached upstream'))
    assert adapter.handle(b'\x0b') == b'\x05'
    with pytest.raises(ValueError):
        Reader(b'x' * (256 * 1024 + 1))


@pytest.mark.parametrize('count', [0, 2])
def test_missing_or_duplicate_original_signer_is_rejected(adapter, monkeypatch, count):
    response = b'\x0c' + struct.pack('>I', count) + (wire(adapter.key) + wire(b'key')) * count
    monkeypatch.setattr(adapter, 'exchange', lambda body: response)
    assert adapter.handle(b'\x0b') == b'\x05'


def test_one_successful_signature_per_command(adapter, monkeypatch):
    adapter.session = b's' * 32
    adapter.audit['signatures'] = 1
    adapter.audit['sign_attempts'] = 1
    monkeypatch.setattr(adapter, 'exchange', lambda body: pytest.fail('second signing reached upstream'))
    assert adapter.handle(request(adapter)) == b'\x05'


@pytest.mark.parametrize('failure', ['timeout', 'malformed'])
def test_ambiguous_upstream_response_consumes_sign_attempt(adapter, monkeypatch, failure):
    adapter.session = b's' * 32
    calls = []

    def exchange(body):
        calls.append(body)
        if failure == 'timeout':
            raise TimeoutError('controlled timeout after upstream dispatch')
        return b'\x0e\x00\x00'

    monkeypatch.setattr(adapter, 'exchange', exchange)
    assert adapter.handle(request(adapter)) == b'\x05'
    assert adapter.audit['sign_attempts'] == 1 and adapter.audit['signatures'] == 0
    assert adapter.handle(request(adapter)) == b'\x05'
    assert len(calls) == 1


@pytest.mark.parametrize('body', [b'', b'\x0d', b'\x0d\xff\xff\xff\xff', b'\x1b\xff\xff\xff\xff'])
def test_truncated_and_oversized_fields_never_reach_upstream(adapter, monkeypatch, body):
    monkeypatch.setattr(adapter, 'exchange', lambda body: pytest.fail('malformed packet reached upstream'))
    assert adapter.handle(body) == b'\x05'


def test_failed_startup_removes_only_owned_temporary_socket(adapter, monkeypatch):
    original = socket.socket

    class FailingSocket(original):
        def listen(self, backlog):
            raise OSError('controlled listen failure')

    monkeypatch.setattr(socket, 'socket', FailingSocket)
    with pytest.raises(OSError, match='controlled listen failure'):
        adapter.__enter__()
    assert not adapter.path.exists() and not adapter.directory.exists()
    assert adapter.upstream.exists()


def test_binding_identity_and_signing_share_one_upstream_connection(adapter):
    received = []
    errors = []
    response = b'\x0e' + wire(wire(b'ecdsa-sha2-nistp256') + wire(b'unit-signature'))

    def server():
        try:
            adapter.test_upstream_server.settimeout(2)
            connection, _ = adapter.test_upstream_server.accept()
            with connection:
                for answer in (b'\x05', b'\x0c' + struct.pack('>I', 1) + wire(adapter.key) + wire(b'key'), response):
                    received.append(receive(connection, time.monotonic() + 2))
                    connection.sendall(wire(answer))
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    with adapter:
        assert adapter.handle(binding(adapter)) == b'\x05'
        assert adapter.handle(b'\x0b').startswith(b'\x0c')
        assert adapter.handle(request(adapter)) == response
    thread.join(3)
    assert not errors and not thread.is_alive()
    assert [message[:1] for message in received] == [b'\x1b', b'\x0b', b'\x0d']


def test_ambiguous_upstream_connection_is_never_reopened(adapter):
    adapter.upstream_failed = True
    with pytest.raises(ValueError, match='cannot be reopened'):
        adapter.exchange(b'\x0b')
    assert adapter.upstream_connection is None


def test_packet_deadline_applies_across_partial_reads(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(certificate_agent.time, 'monotonic', lambda: clock[0])

    class SlowStream:
        def __init__(self):
            self.data = bytearray(wire(b'\x0b'))

        def settimeout(self, value):
            assert 0 < value <= 1

        def recv(self, size):
            clock[0] += .3
            return bytes([self.data.pop(0)])

    with pytest.raises(TimeoutError, match='deadline'):
        receive(SlowStream(), 101.0)
    assert clock[0] < 101.3


@pytest.mark.parametrize('expired', ['clock', 'certificate'])
def test_late_signature_reply_is_rejected_without_reopening_budget(adapter, monkeypatch, expired):
    adapter.session = b's' * 32

    def exchange(body):
        if expired == 'clock':
            adapter.deadline = time.monotonic() - 1
        else:
            adapter.grant['valid_before'] = time.time() - 1
        return b'\x0e' + wire(wire(b'ecdsa-sha2-nistp256') + wire(b'unit-signature'))

    monkeypatch.setattr(adapter, 'exchange', exchange)
    assert adapter.handle(request(adapter)) == b'\x05'
    assert adapter.audit['sign_attempts'] == 1 and adapter.audit['signatures'] == 0
