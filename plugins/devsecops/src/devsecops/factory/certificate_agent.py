"""Bounded certificate-only view of one existing ECDSA P256 agent key.

Never an issuer or general agent: one exact certificate, one user, one pinned
host and one host-bound session. Signed bytes are forwarded unchanged.
The pinned OpenSSH client authenticates the server; this view does not replace
its host-signature verification or invent support for an upstream extension.
"""
import base64
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import tempfile
import threading
import time

from devsecops.factory.tcg import TCGError, _certificate_grant, _certificate_identity, _path

MAX_PACKET = 256 * 1024
FAILURE = b'\x05'


def socket_root():
    # A short, fixed local path, independent of user-supplied TMPDIR.
    return '/private/tmp' if sys.platform == 'darwin' else '/tmp'


def wire(value):
    return struct.pack('>I', len(value)) + value


class Reader:
    def __init__(self, raw):
        if len(raw) > MAX_PACKET:
            raise ValueError('packet bound')
        self.raw, self.offset = raw, 0

    def take(self, size):
        if size < 0 or size > len(self.raw) - self.offset:
            raise ValueError('truncated packet')
        value = self.raw[self.offset:self.offset + size]
        self.offset += size
        return value

    def string(self):
        return self.take(struct.unpack('>I', self.take(4))[0])

    def done(self):
        if self.offset != len(self.raw):
            raise ValueError('trailing packet data')


def receive(stream, deadline):
    def exactly(size):
        output = bytearray()
        while len(output) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('agent packet deadline expired')
            stream.settimeout(min(5, remaining))
            chunk = stream.recv(size - len(output))
            if not chunk:
                raise EOFError
            output.extend(chunk)
        return bytes(output)
    size = struct.unpack('>I', exactly(4))[0]
    if not 1 <= size <= MAX_PACKET:
        raise ValueError('packet bound')
    return exactly(size)


class CertificateAgent:
    def __init__(self, certificate, identity, upstream, host_key, username, deadline):
        self.grant = _certificate_grant(certificate)
        if _certificate_identity(certificate) != identity:
            raise ValueError('certificate identity mismatch')
        self.cert = base64.b64decode(certificate.split()[1], validate=True)
        self.key = base64.b64decode(identity.split()[1], validate=True)
        self.host_key = host_key
        self.username = username.encode()
        if self.grant['algorithm'] != 'ecdsa-sha2-nistp256-cert-v01@openssh.com':
            raise ValueError('certificate view supports ECDSA P256 certificates only')
        self.upstream = _path(upstream)
        info = self.upstream.stat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('upstream must be the owned existing agent socket')
        self.upstream_identity = info.st_dev, info.st_ino, info.st_uid
        self.deadline = deadline
        self.session = None
        self.requests = 0
        self.audit = {'identity_answers': 0, 'host_bound_requests': 0, 'sign_attempts': 0,
                      'signatures': 0, 'rejected': 0}
        self.stop = threading.Event()
        self.connections = []
        self.server = None
        self.upstream_connection = None
        self.upstream_failed = False
        self.directory = None

    def exchange(self, body):
        """Keep the upstream connection and its native constraints intact."""
        if self.upstream_failed:
            raise ValueError('upstream connection cannot be reopened')
        deadline = min(self.deadline, time.monotonic() + 5)
        try:
            info = self.upstream.stat()
            if (info.st_dev, info.st_ino, info.st_uid) != self.upstream_identity:
                raise ValueError('upstream agent identity changed')
            if self.upstream_connection is None:
                upstream = socket.socket(socket.AF_UNIX)
                self.connections.append(upstream)
                self.upstream_connection = upstream
                upstream.settimeout(max(.001, deadline - time.monotonic()))
                upstream.connect(str(self.upstream))
                info = self.upstream.stat()
                if (info.st_dev, info.st_ino, info.st_uid) != self.upstream_identity:
                    raise ValueError('upstream agent changed during connection')
            upstream = self.upstream_connection
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('agent exchange deadline expired')
            upstream.settimeout(remaining)
            upstream.sendall(wire(body))
            return receive(upstream, deadline)
        except (OSError, ValueError, EOFError):
            self.upstream_failed = True
            raise

    def handle(self, body):
        try:
            self.requests += 1
            if (self.requests > 16 or time.monotonic() >= self.deadline
                    or not self.grant['valid_after'] <= time.time() < self.grant['valid_before']):
                raise ValueError('certificate command lifetime exhausted')
            reader = Reader(body)
            kind = reader.take(1)
            if kind == b'\x0b':
                reader.done()
                listed = Reader(self.exchange(body))
                if listed.take(1) != b'\x0c':
                    raise ValueError('upstream identity list failed')
                count = struct.unpack('>I', listed.take(4))[0]
                if count > 128:
                    raise ValueError('identity bound')
                matches = 0
                for _ in range(count):
                    matches += listed.string() == self.key
                    listed.string()
                listed.done()
                if matches != 1:
                    raise ValueError('original signer missing or ambiguous')
                self.audit['identity_answers'] += 1
                return b'\x0c' + struct.pack('>I', 1) + wire(self.cert) + wire(b'devsecops-scoped-certificate')
            if kind == b'\x1b':
                extension = reader.string()
                host, session, signature = reader.string(), reader.string(), reader.string()
                forwarding = reader.take(1)
                reader.done()
                if (extension != b'session-bind@openssh.com' or host != self.host_key or forwarding != b'\x00'
                        or not 16 <= len(session) <= 128 or not signature
                        or self.session is not None and self.session != session):
                    raise ValueError('unexpected host binding')
                self.session = session
                # Preserve the original agent's answer. Secretive4 refuses this
                # optional extension; the sign payload is still checked below.
                answer = self.exchange(body)
                if answer not in (b'\x05', b'\x06', b'\x1c'):
                    raise ValueError('unexpected extension response')
                return answer
            if kind == b'\x0d':
                key, data = reader.string(), reader.string()
                flags = reader.take(4)
                reader.done()
                if (key != self.cert or flags != b'\x00' * 4 or self.session is None
                        or self.audit['sign_attempts']):
                    raise ValueError('only bound certificate signing is allowed')
                auth = Reader(data)
                if (auth.string() != self.session or auth.take(1) != b'\x32' or auth.string() != self.username
                        or auth.string() != b'ssh-connection' or auth.string() != b'publickey-hostbound-v00@openssh.com'
                        or auth.take(1) != b'\x01' or auth.string() != self.grant['algorithm'].encode()
                        or auth.string() != self.cert or auth.string() != self.host_key):
                    raise ValueError('signing request exceeds the exact host/user/certificate scope')
                auth.done()
                self.audit['host_bound_requests'] += 1
                # A timeout can occur after the upstream key signed. Consume
                # the one-command budget before dispatch, including failure.
                self.audit['sign_attempts'] += 1
                answer = self.exchange(b'\x0d' + wire(self.key) + wire(data) + flags)
                signed = Reader(answer)
                if signed.take(1) != b'\x0e':
                    raise ValueError('original agent refused signing')
                signature = Reader(signed.string())
                if signature.string() != b'ecdsa-sha2-nistp256' or not signature.string():
                    raise ValueError('unexpected original signature algorithm')
                signature.done()
                signed.done()
                if (time.monotonic() >= self.deadline
                        or not self.grant['valid_after'] <= time.time() < self.grant['valid_before']):
                    raise ValueError('signature arrived after the command lifetime')
                self.audit['signatures'] += 1
                return answer
            raise ValueError('agent operation is not permitted')
        except (ValueError, OSError, EOFError, TCGError):
            self.audit['rejected'] += 1
            return FAILURE

    def serve(self):
        while not self.stop.is_set() and time.monotonic() < self.deadline:
            try:
                connection, _ = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                self.connections.append(connection)
                connection.settimeout(min(5, max(.01, self.deadline - time.monotonic())))
                self.session = None
                try:
                    while not self.stop.is_set() and time.monotonic() < self.deadline:
                        response = self.handle(receive(connection, self.deadline))
                        remaining = self.deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        connection.settimeout(min(5, remaining))
                        connection.sendall(wire(response))
                        if self.audit['sign_attempts'] or self.requests >= 16:
                            break
                except (OSError, EOFError, ValueError):
                    pass
            # One frontend connection per SSH command. Never reset a native
            # agent's session binding by reconnecting after ambiguity.
            break
        self.server.close()
        if self.upstream_connection is not None:
            self.upstream_connection.close()

    def __enter__(self):
        self.directory = Path(tempfile.mkdtemp(prefix='ds-cert-', dir=socket_root()))
        try:
            self.directory.chmod(0o700)
            self.path = self.directory / 'agent.sock'
            self.server = socket.socket(socket.AF_UNIX)
            self.server.bind(str(self.path))
            self.socket_identity = self.path.stat().st_dev, self.path.stat().st_ino
            self.path.chmod(0o600)
            self.server.listen(1)
            self.server.settimeout(.2)
            self.thread = threading.Thread(target=self.serve, daemon=True)
            self.thread.start()
        except BaseException:
            if self.server is not None:
                self.server.close()
            if hasattr(self, 'socket_identity'):
                self._unlink_owned()
            else:
                self.directory.rmdir()
            raise
        return self

    def _unlink_owned(self):
        info = self.path.lstat()
        if (info.st_dev, info.st_ino) != self.socket_identity or not stat.S_ISSOCK(info.st_mode):
            raise TCGError('owned adapter socket was replaced')
        self.path.unlink()
        self.directory.rmdir()

    def __exit__(self, *args):
        self.stop.set()
        self.server.close()
        for connection in self.connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.thread.join(6)
        if self.thread.is_alive():
            raise TCGError('bounded certificate adapter did not stop')
        self._unlink_owned()
