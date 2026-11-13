"""Bounded fixed-origin receive-pack bridge; admission belongs to its caller.

Native Git generates commands/packs. This module decodes bounded HTTP and Git
status only. One token snapshot, no redirect, proxy, authentication resend or
write retry. Every encrypted socket byte shares one total budget.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import re
import ssl
import time
from urllib.parse import urlsplit

from devsecops.adapters.http import HTTPSReadClient
from .git_destination import _origin
from .git_request_framing import MAX_ADVERTISEMENT, MAX_BODY, _packet


class GitReceiveError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('Git transfer stopped (' + code + ')')


@dataclass(frozen=True)
class Limits:
    wire: int = 18 * 1024 * 1024
    headers: int = 16 * 1024
    requests: int = 32
    upload: int = MAX_BODY
    status: int = 256 * 1024
    timeout: float = 120


class Budget:
    def __init__(self, limits=Limits()):
        if type(limits) is not Limits:
            raise GitReceiveError('invalid-budget')
        for name, maximum in Limits().__dict__.items():
            value = getattr(limits, name)
            if type(value) not in ((int, float) if name == 'timeout' else (int,)) or not 0 < value <= maximum:
                raise GitReceiveError('invalid-budget')
        self.limits, self.deadline = limits, time.monotonic() + limits.timeout
        self.sent = self.received = self.requests = self.upload = 0

    def remaining(self):
        value = self.deadline - time.monotonic()
        if value <= 0:
            raise GitReceiveError('deadline')
        return value

    def wire(self, amount, *, send):
        if self.sent + self.received + amount > self.limits.wire:
            raise GitReceiveError('wire-cap')
        if send:
            self.sent += amount
        else:
            self.received += amount


class _TLS:
    def __init__(self, host, port, context, budget, *, service_route=None, ca_sha256=None, origin=None):
        self.budget = budget
        from devsecops.adapters.owned_route import bind_service_route
        bind_service_route(self, service_route, origin=origin, ca_sha256=ca_sha256)
        self.incoming, self.outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self.tls = context.wrap_bio(self.incoming, self.outgoing, server_hostname=host)
        self.sock = HTTPSReadClient._connect(self, (host, port), budget.remaining())
        try:
            self._call(self.tls.do_handshake)
        except BaseException:
            self.sock.close()
            raise

    def _flush(self):
        while self.outgoing.pending:
            raw = self.outgoing.read(min(16384, self.outgoing.pending))
            self.budget.wire(len(raw), send=True)
            self.sock.settimeout(self.budget.remaining())
            self.sock.sendall(raw)

    def _call(self, fn):
        while True:
            self.budget.remaining()
            try:
                value = fn()
                self._flush()
                return value
            except ssl.SSLWantWriteError:
                self._flush()
            except ssl.SSLWantReadError:
                self._flush()
                available = self.budget.limits.wire - self.budget.sent - self.budget.received
                if available <= 0:
                    raise GitReceiveError('wire-cap')
                self.sock.settimeout(self.budget.remaining())
                raw = self.sock.recv(min(16384, available))
                if not raw:
                    raise GitReceiveError('tls-eof')
                self.budget.wire(len(raw), send=False)
                self.incoming.write(raw)

    def send(self, raw):
        offset = 0
        while offset < len(raw):
            offset += self._call(lambda: self.tls.write(raw[offset:offset + 16384]))

    def read(self, count):
        return self._call(lambda: self.tls.read(count))

    def close(self):
        self.sock.close()


def _exact(channel, size):
    value = bytearray()
    while len(value) < size:
        chunk = channel.read(min(16384, size-len(value)))
        if not chunk:
            raise GitReceiveError('incomplete-response')
        value.extend(chunk)
    return bytes(value)


def _line(channel, limit):
    value = bytearray()
    while not value.endswith(b'\r\n'):
        if len(value) >= limit:
            raise GitReceiveError('response-line-cap')
        value.extend(_exact(channel, 1))
    return bytes(value[:-2])


class GitReceiveClient:
    def __init__(self, *, origin, repository_path, context, token, budget, service_route=None, ca_sha256=None):
        _origin(origin)
        if (not isinstance(repository_path, str) or not re.fullmatch(r'/[A-Za-z0-9_~./-]+\.git', repository_path)
                or any(part in ('.', '..', '') for part in repository_path.split('/')[1:])
                or type(context) is not ssl.SSLContext or context.verify_mode != ssl.CERT_REQUIRED
                or not context.check_hostname or type(budget) is not Budget
                or not isinstance(token, str) or not 1 <= len(token) <= 8192
                or any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise GitReceiveError('invalid-selection')
        self.origin, self.repository_path, self.budget = origin, repository_path, budget
        from devsecops.adapters.owned_route import bind_service_route
        route_ca = bind_service_route(self, service_route, origin=origin, ca_sha256=ca_sha256)
        if route_ca is not None:
            context = ssl.create_default_context(cadata=route_ca.decode('ascii'))
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        from devsecops.adapters.owned_route import service_deadline
        self.budget.deadline = service_deadline(self, self.budget.deadline)
        self._context, self._token = context, token
        parsed = urlsplit(origin)
        self._host, self._port, self._authority = parsed.hostname, parsed.port or 443, parsed.netloc
        self._advertised = self._spent = False
        self._username = None

    def __reduce__(self):
        raise TypeError('Writer transport cannot be serialized')

    def set_username(self, username):
        if (self._username is not None or not isinstance(username, str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,255}', username)):
            raise GitReceiveError('username-selection')
        self._username = username

    def _exchange(self, method, path, *, body=b'', cap, media, git=False, expected=(200,)):
        from devsecops.adapters.owned_route import service_request
        service_request(self, method)
        if self.budget.requests >= self.budget.limits.requests:
            raise GitReceiveError('request-cap')
        if (method not in ('GET', 'POST') or not path.startswith('/') or len(path) > 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in path) or '\\' in path
                or type(body) is not bytes or len(body) > self.budget.limits.upload):
            raise GitReceiveError('invalid-request')
        if (method == 'POST' and (not git or not self._spent
                or path != self.repository_path+'/git-receive-pack')
                or method == 'GET' and body
                or git and method == 'GET' and path != self.repository_path+'/info/refs?service=git-receive-pack'):
            raise GitReceiveError('unselected-write-or-git-route')
        if git:
            if self._username is None:
                raise GitReceiveError('username-unverified')
            authorization = 'Basic ' + base64.b64encode((self._username+':'+self._token).encode()).decode()
        else:
            authorization = 'Bearer ' + self._token
        self.budget.requests += 1
        headers = [f'{method} {path} HTTP/1.1', 'Host: '+self._authority,
            'Connection: close', 'Accept-Encoding: identity', 'Authorization: '+authorization, 'Accept: '+media]
        if method == 'POST':
            headers += ['Content-Type: application/x-git-receive-pack-request', 'Content-Length: '+str(len(body))]
        raw_headers = ('\r\n'.join(headers)+'\r\n\r\n').encode()
        if len(raw_headers) > self.budget.limits.headers:
            raise GitReceiveError('request-header-cap')
        options = {} if getattr(self, '_service_route', None) is None else dict(service_route=self._service_route,
            ca_sha256=self._service_route_ca_sha256, origin=self.origin)
        channel = _TLS(self._host, self._port, self._context, self.budget, **options)
        try:
            service_request(self, method)
            self.budget.remaining()
            channel.send(raw_headers)
            if body:
                self.budget.upload += len(body)
                if self.budget.upload > self.budget.limits.upload:
                    raise GitReceiveError('upload-cap')
                channel.send(body)
            first = _line(channel, self.budget.limits.headers)
            match = re.fullmatch(rb'HTTP/1\.[01] ([0-9]{3}) [\x20-\x7e]*', first)
            if not match or int(match[1]) not in expected:
                raise GitReceiveError('http-status')
            fields, count = {}, len(first)+2
            while True:
                line = _line(channel, self.budget.limits.headers-count)
                count += len(line)+2
                if not line:
                    break
                key, sep, value = line.partition(b':')
                key, value = key.lower(), value.strip()
                if (not sep or not re.fullmatch(rb'[a-z0-9-]+', key) or key in fields
                        or any(c < 32 or c > 126 for c in value)):
                    raise GitReceiveError('response-headers')
                fields[key] = value
            if (fields.get(b'content-type', b'').split(b';')[0] != media.encode()
                    or fields.get(b'content-encoding', b'identity') != b'identity'
                    or b'content-length' in fields and b'transfer-encoding' in fields):
                raise GitReceiveError('response-encoding')
            if b'content-length' in fields:
                length = fields[b'content-length']
                if not re.fullmatch(rb'[0-9]{1,10}', length) or int(length) > cap:
                    raise GitReceiveError('response-body-cap')
                raw = _exact(channel, int(length))
            elif fields.get(b'transfer-encoding') == b'chunked':
                content = bytearray()
                while True:
                    size = _line(channel, 32)
                    if not re.fullmatch(rb'[a-fA-F0-9]{1,8}', size):
                        raise GitReceiveError('response-chunk')
                    size = int(size, 16)
                    if len(content)+size > cap:
                        raise GitReceiveError('response-body-cap')
                    if not size:
                        if _line(channel, 2):
                            raise GitReceiveError('response-trailers')
                        break
                    content.extend(_exact(channel, size))
                    if _exact(channel, 2) != b'\r\n':
                        raise GitReceiveError('response-chunk')
                raw = bytes(content)
            else:
                raise GitReceiveError('response-length-required')
            self.budget.remaining()
            from devsecops.adapters.owned_route import audit_service_route
            audit_service_route(self)
            self.budget.remaining()
            return int(match[1]), fields, raw
        finally:
            channel.close()

    def read_api(self, path, *, cap=64000, expected=(200,), media='application/json'):
        if self._spent or not path.startswith('/api/v4/'):
            raise GitReceiveError('read-phase-closed')
        return self._exchange('GET', path, cap=cap, media=media, expected=expected)

    def advertisement(self):
        if self._advertised or self._spent:
            raise GitReceiveError('advertisement-already-consumed')
        self._advertised = True
        return self._exchange('GET', self.repository_path+'/info/refs?service=git-receive-pack',
            cap=MAX_ADVERTISEMENT, media='application/x-git-receive-pack-advertisement', git=True)[2]

    def send_once(self, body):
        if self._spent or not self._advertised:
            raise GitReceiveError('write-already-consumed')
        self._spent = True  # Never retry, including failed TLS or interrupted send.
        return self._exchange('POST', self.repository_path+'/git-receive-pack', body=body,
            cap=self.budget.limits.status, media='application/x-git-receive-pack-result', git=True)[2]


def validate_status(raw, *, target_ref, request_body):
    if type(raw) is not bytes or len(raw) > Limits().status:
        raise GitReceiveError('status-cap')
    command, _ = _packet(request_body, 0)
    sideband = b'side-band-64k' in command.split(b'\0', 1)[1].split()
    def packets(value):
        rows, offset = [], 0
        while True:
            row, offset = _packet(value, offset)
            if row is None:
                break
            rows.append(row)
        if offset != len(value):
            raise GitReceiveError('status-trailing-bytes')
        return rows
    if sideband:
        channels = packets(raw)
        if any(not row or row[0] not in (1, 2) for row in channels):
            raise GitReceiveError('status-channel')
        raw = b''.join(row[1:] for row in channels if row[0] == 1)
    if packets(raw) != [b'unpack ok\n', ('ok '+target_ref+'\n').encode()]:
        raise GitReceiveError('status-not-exact-success')
    return True
