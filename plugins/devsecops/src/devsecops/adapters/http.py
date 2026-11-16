"""HTTPS GET transport. Adapters must separately enforce semantic read routes."""
from __future__ import annotations

import base64
import hashlib
import http.client
import ipaddress
import json
import math
import os
import queue
import re
import socket
import ssl
import stat
import tempfile
import threading
import time
import weakref
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit


class APIError(ValueError):
    """Only safe category/status escape the transport; never remote diagnostics."""
    def __init__(self, code, status=None):
        self.code, self.status = code, status
        super().__init__('Service evidence request could not complete ('+code+')')


DEFAULT_JSON_VALUES = 100_000
MAX_SELECTED_JSON_VALUES = 300_000
MAX_LARGE_ATTESTATION_JSON_VALUES = 500_000


class _JSONLimitError(APIError):
    """Retain a fixed shape-limit category without exporting input content."""
    def __init__(self, dimension):
        self.dimension = dimension
        super().__init__('invalid-json')


def _tls_failure(error, phase, peer_authenticated=False):
    """Safe TLS diagnostics: fixed categories, never raw peer/error messages."""
    if isinstance(error, ssl.SSLCertVerificationError):
        kind, exception = 'certificate-verification', 'SSLCertVerificationError'
    elif isinstance(error, ssl.SSLEOFError):
        kind = 'handshake-eof' if phase == 'tls-handshake' else 'tls-protocol'
        exception = 'SSLEOFError'
    else:
        kind, exception = 'tls-protocol', 'SSLError'
    known_reasons = {'CERTIFICATE_VERIFY_FAILED', 'UNEXPECTED_EOF_WHILE_READING',
                     'TLSV13_ALERT_CERTIFICATE_REQUIRED', 'TLSV1_ALERT_UNKNOWN_CA',
                     'SSLV3_ALERT_BAD_CERTIFICATE', 'TLSV1_ALERT_BAD_CERTIFICATE',
                     'SSLV3_ALERT_HANDSHAKE_FAILURE', 'TLSV1_ALERT_PROTOCOL_VERSION',
                     'WRONG_VERSION_NUMBER', 'NO_SHARED_CIPHER', 'UNSUPPORTED_PROTOCOL',
                     'TLSV1_ALERT_INTERNAL_ERROR', 'TLSV1_ALERT_DECRYPT_ERROR', 'TLSV1_ALERT_ACCESS_DENIED'}
    reason = getattr(error, 'reason', None)
    verify_code = getattr(error, 'verify_code', None)
    return {'category': kind, 'exception': exception, 'phase': phase, 'peer_authenticated': peer_authenticated,
            'reason': reason if isinstance(reason, str) and reason in known_reasons else 'OTHER',
            'verify_code': verify_code if type(verify_code) is int and 0 <= verify_code <= 65535 else None}


@dataclass(frozen=True)
class ReadResponse:
    """Bounded internal response; body and authentication data never have reprs."""
    status: int
    body: bytes = field(repr=False)
    media_type: str | None = None
    content_digest: str | None = field(default=None, repr=False)
    auth_challenge: str | None = field(default=None, repr=False)
    pagination: dict[str, str] = field(default_factory=dict, repr=False)
    blob_location: str | None = field(default=None, repr=False)


@dataclass
class _TransportState:
    """Shared accounting for one serialized registry session, including follow-ups."""
    deadline: float | None = None
    requests: int = 0
    bytes: int = 0
    receipts: list = field(default_factory=list)
    lock: object = field(default_factory=threading.Lock, repr=False)


@dataclass(frozen=True, eq=False)
class _AttestationBodyAllowance:
    """One internal buffered request; no global client limit is changed."""
    selection: object = field(repr=False)
    source_origin: str
    repository: str
    digest: str
    size_bytes: int
    recipient_origin: str
    recipient_path: str = field(repr=False)
    query_sha256: str = field(repr=False)


_ATTESTATION_ALLOWANCES = weakref.WeakKeyDictionary()
_ATTESTATION_ALLOWANCE_LOCK = threading.Lock()


def _allowance_identity(value):
    return (id(value.selection), value.source_origin, value.repository, value.digest,
            value.size_bytes, value.recipient_origin, value.recipient_path, value.query_sha256)


def _attestation_body_allowance(selection, *, origin, repository, digest, size_bytes, target=None):
    """Bind a policy-issued descriptor to its source or a validated blob target.

    Only the registry session calls this after checking the exact source route
    and, for storage, the fresh redirect and its independent download policy.
    """
    from .registry_attestation_policy import validate_attestation_selection, V3_MAX_ENVELOPE
    maximum = validate_attestation_selection(selection, origin=origin, repository=repository,
                                               digest=digest, size_bytes=size_bytes)
    if type(maximum) is not int or not 1 <= size_bytes <= maximum <= V3_MAX_ENVELOPE:
        raise APIError('invalid-attestation-allowance')
    recipient, path, params = origin, '/v2/' + repository + '/blobs/' + digest, {}
    if target is not None:
        from .registry_download_policy import BlobDownloadTarget
        if type(target) is not BlobDownloadTarget or target.expected_digest != digest:
            raise APIError('invalid-attestation-allowance')
        recipient, (path, params) = target.origin, target.request_parts()
    allowance = _AttestationBodyAllowance(selection, origin, repository, digest, size_bytes,
        recipient, path, hashlib.sha256(urlencode(params).encode()).hexdigest())
    with _ATTESTATION_ALLOWANCE_LOCK:
        _ATTESTATION_ALLOWANCES[allowance] = _allowance_identity(allowance)
    return allowance


def _attestation_body_limit(allowance, *, origin, path, query, as_json, stream):
    if type(allowance) is not _AttestationBodyAllowance:
        raise APIError('invalid-attestation-allowance')
    with _ATTESTATION_ALLOWANCE_LOCK:
        registered = _ATTESTATION_ALLOWANCES.get(allowance)
    if (registered != _allowance_identity(allowance) or as_json or stream is not None
            or allowance.recipient_origin != origin or allowance.recipient_path != path
            or allowance.query_sha256 != hashlib.sha256(query.encode()).hexdigest()):
        raise APIError('invalid-attestation-allowance')
    from .registry_attestation_policy import validate_attestation_selection, V3_MAX_ENVELOPE
    maximum = validate_attestation_selection(allowance.selection, origin=allowance.source_origin,
        repository=allowance.repository, digest=allowance.digest, size_bytes=allowance.size_bytes)
    if type(maximum) is not int or not 1 <= allowance.size_bytes <= maximum <= V3_MAX_ENVELOPE:
        raise APIError('invalid-attestation-allowance')
    return allowance.size_bytes


def _bearer(token):
    if not isinstance(token, str) or not 1 <= len(token) <= 8192 or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise APIError('credential-unavailable')
    return 'Bearer '+token


def _now():
    return datetime.now(UTC).isoformat()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate key')
        value[key] = item
    return value


def _json(raw, *, max_values=DEFAULT_JSON_VALUES):
    return _bounded_json(raw, max_values, MAX_SELECTED_JSON_VALUES)


def _large_attestation_json(raw, *, max_values):
    """Separate version 3 DSSE parser; ordinary HTTP JSON keeps its ceiling."""
    return _bounded_json(raw, max_values, MAX_LARGE_ATTESTATION_JSON_VALUES)


def _bounded_json(raw, max_values, ceiling):
    if (type(max_values) is not int
            or not DEFAULT_JSON_VALUES <= max_values <= ceiling):
        raise APIError('invalid-json-limit')
    def constant(value):
        raise ValueError('non-finite number')
    try:
        result = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=constant)
        if not isinstance(result, dict):
            raise ValueError('top-level object required')
        pending, count = [(result, 0)], 0
        while pending:
            value, depth = pending.pop()
            count += 1
            if depth > 64:
                raise _JSONLimitError('depth')
            if count > max_values:
                raise _JSONLimitError('value')
            if isinstance(value, dict):
                pending.extend((child, depth+1) for child in value.values())
            elif isinstance(value, list):
                pending.extend((child, depth+1) for child in value)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError('non-finite number')
        return result
    except _JSONLimitError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise APIError('invalid-json') from None


def _load_client_certificate(context, paths):
    """Freeze explicit certificate/key bytes before OpenSSL opens private copies."""
    try:
        if not isinstance(paths, tuple) or len(paths) != 2:
            raise ValueError
        frozen = []
        for index, supplied in enumerate(paths):
            path = Path(supplied)
            if (not path.is_absolute() or '..' in path.parts
                    or any(part.is_symlink() for part in (path, *path.parents))):
                raise ValueError
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if (not stat.S_ISREG(before.st_mode) or before.st_size > 1_000_000
                        or before.st_nlink != 1 or before.st_mode & 0o022
                        or (index == 1 and (before.st_uid != os.getuid() or before.st_mode & 0o077))):
                    raise ValueError
                raw = stream.read(1_000_001)
                after = os.fstat(stream.fileno())
                if (not raw or len(raw) > 1_000_000 or
                        (before.st_size, before.st_ino, before.st_mtime_ns) !=
                        (after.st_size, after.st_ino, after.st_mtime_ns)):
                    raise ValueError
                frozen.append(raw)
        with tempfile.TemporaryDirectory(prefix='devsecops-mtls-') as directory:
            files = [Path(directory) / name for name in ('certificate.pem', 'key.pem')]
            for path, raw in zip(files, frozen, strict=True):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
            # Supplying a callback prevents OpenSSL from prompting for an encrypted key.
            context.load_cert_chain(str(files[0]), str(files[1]), password=lambda: b'')
    except (OSError, ValueError, TypeError, ssl.SSLError):
        raise APIError('client-certificate-unavailable') from None


class HTTPSReadClient:
    """One exact origin, bounded collection, env credentials, no proxy/redirects.

    The client exposes GET only, but a service-specific adapter still must
    prohibit GET operations that trigger reconciliation or other mutations.
    Create one client per collection; budgets are not reset between requests.
    """
    def __init__(self, origin, *, token_env=None, basic_username_env=None, basic_password_env=None,
                 ca_bundle=None, client_certificate=None, timeout=5.0,
                 total_timeout=20.0, max_body_bytes=2_000_000, max_requests=8,
                 max_total_bytes=16_000_000, service_route=None, _system_roots_only=False):
        try:
            if not isinstance(origin, str) or len(origin) > 2048 or any(ord(c) < 33 for c in origin):
                raise ValueError
            parsed = urlsplit(origin)
            hostname, port = parsed.hostname, parsed.port
            if (parsed.scheme != 'https' or not hostname or parsed.username is not None
                    or parsed.password is not None or parsed.query or parsed.fragment
                    or parsed.path not in ('', '/') or '%' in hostname or '\\' in origin):
                raise ValueError
            try:
                address = ipaddress.ip_address(hostname)
                authority = '['+hostname+']' if address.version == 6 else hostname
            except ValueError:
                if (len(hostname) > 253
                        or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                   for label in hostname.split('.'))
                        or all(part.isdigit() for part in hostname.split('.'))):
                    raise ValueError
                authority = hostname
            if port is not None and not 1 <= port <= 65535:
                raise ValueError
            if parsed.netloc.lower() != authority+(':'+str(port) if port is not None else ''):
                raise ValueError
            if any(type(v) not in (int, float) or not 0 < v <= cap or not math.isfinite(v)
                   for v, cap in ((timeout, 30), (total_timeout, 120))):
                raise ValueError
            if any(type(v) is not int or not 1 <= v <= cap for v, cap in (
                    (max_requests, 64), (max_body_bytes, 8_000_000), (max_total_bytes, 64_000_000))):
                raise ValueError
            if any(value is not None and (not isinstance(value, str)
                    or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', value))
                    for value in (token_env, basic_username_env, basic_password_env)):
                raise ValueError
            if ((basic_username_env is None) != (basic_password_env is None)
                    or (token_env is not None and basic_username_env is not None)):
                raise ValueError
            if (type(_system_roots_only) is not bool or _system_roots_only and any(v is not None
                    for v in (token_env, basic_username_env, basic_password_env, ca_bundle,
                              client_certificate, service_route))):
                raise ValueError
        except (ValueError, TypeError):
            raise APIError('invalid-configuration') from None
        self._host, self._port = hostname.lower(), port or 443
        host_text = '['+self._host+']' if ':' in self._host else self._host
        self.origin = 'https://'+host_text+(':'+str(self._port) if self._port != 443 else '')
        self.token_env = token_env
        self._basic_username_env, self._basic_password_env = basic_username_env, basic_password_env
        self._timeout, self._total_timeout = float(timeout), float(total_timeout)
        self._max_body, self._max_requests, self._max_total = max_body_bytes, max_requests, max_total_bytes
        self._state = _TransportState()
        self._lock = self._state.lock
        try:
            from .owned_route import bind_service_route
            route_ca = bind_service_route(self, service_route, origin=self.origin, ca_bundle=ca_bundle)
            if ca_bundle is not None and (not Path(ca_bundle).is_file() or Path(ca_bundle).stat().st_size > 2_000_000):
                raise ValueError
            if _system_roots_only:
                roots = ssl.get_default_verify_paths()
                cafile = roots.openssl_cafile if roots.openssl_cafile and Path(roots.openssl_cafile).is_file() else None
                capath = roots.openssl_capath if roots.openssl_capath and Path(roots.openssl_capath).is_dir() else None
                if cafile is None and capath is None:
                    raise ValueError
                self._context = ssl.create_default_context(cafile=cafile, capath=capath)
            else:
                self._context = (ssl.create_default_context(cadata=route_ca.decode('ascii')) if route_ca is not None else
                                 ssl.create_default_context(cafile=str(ca_bundle) if ca_bundle is not None else None))
            self._context.minimum_version = ssl.TLSVersion.TLSv1_2
        except APIError:
            raise
        except (OSError, ValueError, TypeError, ssl.SSLError):
            raise APIError('invalid-ca') from None
        if client_certificate is not None:
            _load_client_certificate(self._context, client_certificate)

    @property
    def receipts(self):
        return deepcopy(self._receipts)

    # Preserve the transport's internal attribute contract while ensuring that
    # a fresh, credential-free storage connection cannot reset session caps.
    _deadline = property(lambda self: self._state.deadline,
                         lambda self, value: setattr(self._state, 'deadline', value))
    _requests = property(lambda self: self._state.requests,
                         lambda self, value: setattr(self._state, 'requests', value))
    _bytes = property(lambda self: self._state.bytes,
                     lambda self, value: setattr(self._state, 'bytes', value))
    _receipts = property(lambda self: self._state.receipts)

    def _share_transport_budget(self, source):
        self._state, self._lock = source._state, source._lock
        self._timeout, self._total_timeout = source._timeout, source._total_timeout
        self._max_requests, self._max_total = source._max_requests, source._max_total
        self._max_body = source._max_body

    def _authorization(self):
        if self.token_env is not None:
            return _bearer(os.environ.get(self.token_env, ''))
        if self._basic_username_env is not None:
            username = os.environ.get(self._basic_username_env, '')
            password = os.environ.get(self._basic_password_env, '')
            if (not 1 <= len(username) <= 255 or ':' in username or not 1 <= len(password) <= 8192
                    or any(ord(c) < 33 or ord(c) > 126 for c in username+password)):
                raise APIError('credential-unavailable')
            return 'Basic '+base64.b64encode((username+':'+password).encode('ascii')).decode('ascii')
        return None

    def _connect(self, address, timeout, source_address=None, **kwargs):
        # Bound the caller's DNS wait. A timed-out daemon may finish resolving,
        # but it never connects or possesses credentials after the request ends.
        end = time.monotonic()+timeout
        from .owned_route import service_address
        address = service_address(self, address)
        if getattr(self, '_service_route', None) is not None:
            # The original link is numeric loopback; do not resolve the logical
            # service hostname or consult a process/global proxy setting.
            addresses = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', address)]
        else:
            results = queue.Queue(maxsize=1)
            def resolve():
                try:
                    results.put(socket.getaddrinfo(address[0], address[1], type=socket.SOCK_STREAM))
                except OSError:
                    results.put(None)
            threading.Thread(target=resolve, daemon=True).start()
            try:
                addresses = results.get(timeout=max(.001, end-time.monotonic()))
            except queue.Empty:
                raise APIError('deadline') from None
        if not addresses:
            raise APIError('transport')
        for family, socktype, protocol, _, sockaddr in addresses[:8]:
            remaining = end-time.monotonic()
            if remaining <= 0:
                raise APIError('deadline')
            sock = socket.socket(family, socktype, protocol)
            try:
                sock.settimeout(remaining)
                sock.connect(sockaddr)
                return sock
            except OSError:
                sock.close()
        raise APIError('transport')

    def get_json(self, path, params=None):
        return self._request(path, params, as_json=True)

    def get_bytes(self, path, params=None, *, accept=('application/json',), allow_unauthorized=False,
                  _allow_blob_redirect=False, _attestation_allowance=None):
        """Raw GET for digest verification; caller enforces routes and media types.

        A 401 can expose one bounded challenge in memory only, without reading
        its body. Redirects and every other non-200 response remain errors.
        """
        if (type(allow_unauthorized) is not bool or type(_allow_blob_redirect) is not bool
                or not isinstance(accept, (tuple, list))
                or not 1 <= len(accept) <= 8 or any(not isinstance(item, str) or len(item) > 128
                    or not re.fullmatch(r'[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+', item) for item in accept)):
            raise APIError('invalid-request')
        return self._request(path, params, as_json=False, accept=tuple(accept), allow_unauthorized=allow_unauthorized,
                             _allow_blob_redirect=_allow_blob_redirect,
                             _attestation_allowance=_attestation_allowance)

    def _request(self, path, params, *, as_json, accept=('application/json',), allow_unauthorized=False,
                 _stream=None, _allow_blob_redirect=False, _attestation_allowance=None):
        if not self._lock.acquire(blocking=False):
            raise APIError('concurrent-request')
        try:
            return self._get_response(path, params, as_json=as_json, accept=accept,
                                      allow_unauthorized=allow_unauthorized, _stream=_stream,
                                      _allow_blob_redirect=_allow_blob_redirect,
                                      _attestation_allowance=_attestation_allowance)
        finally:
            self._lock.release()

    def _get_response(self, path, params, *, as_json, accept, allow_unauthorized, _stream=None,
                      _allow_blob_redirect=False, _attestation_allowance=None):
        pattern = r'/[A-Za-z0-9_./~-]{0,2047}' if as_json else r'/[A-Za-z0-9_./~:-]{0,2047}'
        if (not isinstance(path, str) or not re.fullmatch(pattern, path)
                or path.startswith('//') or any(part in ('.', '..') for part in path.split('/'))):
            raise APIError('invalid-request')
        if params is not None and not isinstance(params, dict):
            raise APIError('invalid-request')
        for key, value in (params or {}).items():
            if (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}', key)
                    or type(value) not in (str, int) or len(str(value)) > 8192):
                raise APIError('invalid-request')
        query = urlencode(params or {})
        if len(query) > 16384:
            raise APIError('invalid-request')
        body_limit = self._max_body
        if _attestation_allowance is not None:
            body_limit = _attestation_body_limit(_attestation_allowance, origin=self.origin,
                path=path, query=query, as_json=as_json, stream=_stream)
        now = time.monotonic()
        if self._deadline is None:
            self._deadline = now+self._total_timeout
        from .owned_route import service_deadline
        self._deadline = service_deadline(self, self._deadline)
        if now >= self._deadline:
            raise APIError('deadline')
        if self._requests >= self._max_requests:
            raise APIError('request-limit')
        self._requests += 1
        receipt = {'request_index': self._requests, 'method': 'GET', 'started_at': _now(),
                   'request_sha256': hashlib.sha256((path+('?'+query if query else '')).encode()).hexdigest(),
                   'status': None, 'body_bytes': 0, 'body_sha256': None, 'outcome': 'unknown'}
        self._receipts.append(receipt)
        if getattr(self, '_registry_download', False):
            receipt.update(role='registry-blob-storage', origin=self.origin)
        phase, tls_peer_authenticated = 'request-setup', False
        connection, response, timer = None, None, None
        active_socket = []
        expired = threading.Event()
        try:
            headers = {'Accept': ', '.join(accept), 'Accept-Encoding': 'identity',
                       'User-Agent': 'devsecops-evidence/1', 'Connection': 'close'}
            authorization = self._authorization()
            if authorization is not None:
                headers['Authorization'] = authorization
            budget = min(self._timeout, self._deadline-time.monotonic())
            if budget <= 0:
                raise APIError('deadline')
            connection = http.client.HTTPSConnection(self._host, self._port, timeout=budget, context=self._context)
            request_deadline = time.monotonic()+budget
            if _stream is not None:
                _stream.deadline = min(_stream.deadline, request_deadline)
            def connect():
                nonlocal phase, tls_peer_authenticated
                # Assign the TLS socket before its handshake so the deadline
                # timer can interrupt a peer that stalls during TLS or headers.
                left = request_deadline-time.monotonic()
                if left <= 0 or expired.is_set():
                    raise APIError('deadline')
                phase = 'tcp-connect'
                connection.sock = self._connect((self._host, self._port), left)
                phase = 'tls-handshake'
                connection.sock = self._context.wrap_socket(connection.sock, server_hostname=self._host,
                                                            do_handshake_on_connect=False)
                active_socket.append(connection.sock)
                left = request_deadline-time.monotonic()
                if left <= 0 or expired.is_set():
                    raise APIError('deadline')
                connection.sock.settimeout(left)
                connection.sock.do_handshake()
                tls_peer_authenticated = True
                from .owned_route import audit_service_route
                audit_service_route(self)
                if time.monotonic() >= request_deadline or expired.is_set():
                    raise APIError('deadline')
                phase = 'request-send'
            connection.connect = connect
            def abort():
                expired.set()
                sock = active_socket[0] if active_socket else connection.sock
                if sock is not None:
                    try:
                        # HTTPConnection can release its socket for a response
                        # with Connection: close while the response file owns
                        # the fd. Interrupt that fd, including a blocked read.
                        socket.socket.shutdown(sock, socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()
            timer = threading.Timer(budget, abort)
            timer.daemon = True
            timer.start()
            connection.request('GET', path+('?'+query if query else ''), headers=headers)
            phase = 'response-headers'
            response = connection.getresponse()
            receipt['status'] = response.status
            # Some Python versions return partial headers after the timer closes
            # the socket. Preserve the actual deadline cause before validation.
            if time.monotonic() >= request_deadline or expired.is_set():
                raise APIError('deadline', response.status)
            try:
                server_date = parsedate_to_datetime(response.getheader('Date', ''))
                if server_date.tzinfo is not None:
                    receipt['server_date'] = server_date.astimezone(UTC).isoformat()
            except (ValueError, TypeError, OverflowError):
                pass
            def one_header(name, limit):
                values = [value for key, value in response.getheaders() if key.lower() == name]
                if (len(values) > 1 or (values and (len(values[0]) > limit
                        or any(ord(c) < 32 or ord(c) > 126 for c in values[0])))):
                    raise APIError('invalid-response', response.status)
                return values[0] if values else None
            if 300 <= response.status <= 399:
                location = one_header('location', 8192)
                if (_allow_blob_redirect and not as_json and params is None and response.status == 307
                        and re.fullmatch(r'/v2/[a-z0-9][a-z0-9._/-]*/blobs/sha256:[a-f0-9]{64}', path)):
                    from .owned_route import audit_service_route
                    audit_service_route(self)
                    if time.monotonic() >= request_deadline or expired.is_set():
                        raise APIError('deadline', response.status)
                    if not location:
                        raise APIError('invalid-response', response.status)
                    receipt['outcome'] = 'blob-redirect-observed'
                    return ReadResponse(status=307, body=b'', blob_location=location)
                raise APIError('redirect', response.status)
            if response.status == 401 and allow_unauthorized:
                from .owned_route import audit_service_route
                audit_service_route(self)
                if time.monotonic() >= request_deadline or expired.is_set():
                    raise APIError('deadline', response.status)
                challenge = one_header('www-authenticate', 8192)
                receipt['outcome'] = 'unauthorized'
                return ReadResponse(status=401, body=b'', auth_challenge=challenge)
            if response.status != 200:
                raise APIError('denied' if response.status in (401, 403) else 'http-error', response.status)
            content_type = (one_header('content-type', 512) or '').split(';', 1)[0].strip().lower()
            if ((as_json and content_type != 'application/json'
                    and not (content_type.startswith('application/') and content_type.endswith('+json')))
                    or (not as_json and content_type not in accept)):
                raise APIError('invalid-response', response.status)
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise APIError('unsupported-encoding', response.status)
            lengths = [value for key, value in response.getheaders() if key.lower() == 'content-length']
            if len(lengths) > 1 or (lengths and response.getheader('Transfer-Encoding')):
                raise APIError('invalid-response', response.status)
            allowed = min(body_limit if _stream is None else _stream.expected_size,
                          self._max_total-self._bytes)
            if allowed < 1:
                raise APIError('body-limit', response.status)
            if lengths and (not re.fullmatch(r'[0-9]+', lengths[0]) or int(lengths[0]) > allowed):
                raise APIError('body-limit', response.status)
            chunks, size = [], 0
            phase = 'response-body'
            while True:
                if expired.is_set() or time.monotonic() >= self._deadline:
                    raise APIError('deadline', response.status)
                chunk = response.read1(min(65536, allowed+1-size))
                if not chunk:
                    break
                size += len(chunk)
                self._bytes += len(chunk)
                receipt['body_bytes'] = size
                if size > allowed:
                    raise APIError('body-limit', response.status)
                if _stream is None:
                    chunks.append(chunk)
                else:
                    _stream.write(chunk)
            if expired.is_set():
                raise APIError('deadline', response.status)
            if lengths and int(lengths[0]) != size:
                raise APIError('invalid-response', response.status)
            if _stream is None:
                raw = b''.join(chunks)
                receipt.update(body_bytes=len(raw), body_sha256=hashlib.sha256(raw).hexdigest())
                pagination = {}
                for name in ('x-page', 'x-per-page', 'x-next-page', 'x-prev-page', 'x-total', 'x-total-pages'):
                    value = one_header(name, 12)
                    if value is not None:
                        if not re.fullmatch(r'[0-9]{0,12}', value):
                            raise APIError('invalid-pagination', response.status)
                        pagination[name] = value
                result = (_json(raw) if as_json else ReadResponse(status=200, body=raw, media_type=content_type,
                    content_digest=one_header('docker-content-digest', 256), pagination=pagination))
            else:
                result = _stream.finish(size, content_type, one_header('docker-content-digest', 256))
                receipt.update(body_bytes=size, body_sha256=result.sha256)
            if time.monotonic() >= self._deadline or expired.is_set():
                raise APIError('deadline', response.status)
            from .owned_route import audit_service_route
            audit_service_route(self)
            if time.monotonic() >= request_deadline or expired.is_set():
                raise APIError('deadline', response.status)
            receipt['outcome'] = 'complete'
            return result
        except APIError as error:
            receipt['outcome'] = error.code
            if error.status is None:
                error.status = receipt['status']
            raise
        except TimeoutError:
            receipt['outcome'] = 'deadline'
            raise APIError('deadline', receipt['status']) from None
        except ssl.SSLCertVerificationError as error:
            # A verification failure is fatal even if its deadline races it.
            receipt['outcome'] = 'tls'
            receipt['tls_failure'] = _tls_failure(error, phase, tls_peer_authenticated)
            raise APIError('tls', receipt['status']) from None
        except ssl.SSLError as error:
            failure = _tls_failure(error, phase, tls_peer_authenticated)
            code = 'deadline' if expired.is_set() and failure['category'] == 'handshake-eof' else 'tls'
            receipt['outcome'] = code
            receipt['tls_failure'] = failure
            raise APIError(code, receipt['status']) from None
        except (OSError, http.client.HTTPException, ValueError):
            code = 'deadline' if expired.is_set() else 'transport'
            receipt['outcome'] = code
            raise APIError(code, receipt['status']) from None
        finally:
            if timer is not None:
                timer.cancel()
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()
            receipt.update(finished_at=_now(), duration_ms=round((time.monotonic()-now)*1000))
