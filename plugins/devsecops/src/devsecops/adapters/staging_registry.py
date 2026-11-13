"""Fixed OCI staging writes; deliberately separate from GET-only adapters.

No redirect, proxy, tag, mount, chunk retry, delete, or cross-origin auth API.
A failed mutation stays ambiguous even when its response was never received.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import http.client
import math
import re
import socket
import ssl
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from .http import APIError, HTTPSReadClient, _bearer, _json, _now
from .oci import MANIFEST, _digest, _repository

MAX_UPLOAD = 512_000_000
MAX_REQUESTS = 264


def _token_route(path, service):
    if ((path is None) != (service is None) or path is not None and (
            not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_./~-]{1,255}', path)
            or path.startswith('//') or any(part in ('.', '..') for part in path.split('/'))
            or not isinstance(service, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', service))):
        raise APIError('invalid-staging-configuration')


def _challenge(value, origin, path, service, repository):
    if not isinstance(value, str) or not value[:7].lower() == 'bearer ' or value.rstrip().endswith(','):
        raise APIError('unsupported-auth-challenge')
    remaining, fields = value[7:].strip(), {}
    while remaining:
        match = re.match(r'([A-Za-z][A-Za-z0-9_-]*)="([^"\\\r\n]*)"(?:\s*,\s*|$)', remaining)
        if not match or match[1].lower() in fields:
            raise APIError('invalid-auth-challenge')
        fields[match[1].lower()] = match[2]
        remaining = remaining[match.end():]
    if (set(fields) != {'realm', 'service', 'scope'} or fields['realm'] != origin + path
            or fields['service'] != service or fields['scope'] not in (
                'repository:' + repository + ':pull', 'repository:' + repository + ':pull,push')):
        raise APIError('auth-challenge-outside-staging-scope')


class _StagingRegistry:
    """One explicit destination and one bounded, non-retrying upload session."""

    def __init__(self, origin, *, repository, deadline, token_env=None,
                 basic_username_env=None, basic_password_env=None,
                 token_path=None, token_service=None, ca_bundle=None, service_route=None):
        if (not _repository(repository) or type(deadline) not in (int, float)
                or not math.isfinite(deadline) or not 0 < deadline - time.monotonic() <= 901
                or token_env is None and basic_username_env is None):
            raise APIError('invalid-staging-configuration')
        _token_route(token_path, token_service)
        # Composition reuses validated TLS/DNS/credential mechanics only. The
        # GET adapter's methods and policy remain unchanged.
        self._transport = HTTPSReadClient(origin, token_env=token_env,
            basic_username_env=basic_username_env, basic_password_env=basic_password_env, ca_bundle=ca_bundle,
            **({} if service_route is None else {'service_route': service_route}))
        self.origin, self.repository = self._transport.origin, repository
        if self._transport._host == 'registry1.dso.mil':
            raise APIError('publisher-registry-is-not-staging')
        if origin != self.origin:
            raise APIError('staging-origin-must-be-canonical')
        self._credential = self._transport._authorization()
        self._deadline, self._prefix = deadline, '/v2/' + repository
        self._token_path, self._token_service = token_path, token_service
        self._basic = basic_username_env is not None
        self._ready, self._uploaded_bytes, self._response_bytes = False, 0, 0
        self._receipts, self._sessions, self._seen_sessions = [], set(), set()
        self._manifest_digest, self._manifest_attempted = None, False

    @property
    def receipts(self):
        return deepcopy(self._receipts)

    def close(self):
        self._credential = None
        self._ready = False

    def _location(self, value, *, upload=False, blob=None, manifest=None):
        if (not isinstance(value, str) or not 1 <= len(value) <= 8192
                or any(ord(c) < 33 or ord(c) > 126 for c in value) or '\\' in value or '#' in value):
            raise APIError('invalid-upload-location')
        parsed = urlsplit(value)
        if parsed.fragment or (parsed.scheme or parsed.netloc) and (
                parsed.scheme != 'https' or parsed.netloc != urlsplit(self.origin).netloc):
            raise APIError('upload-location-outside-scope')
        if upload:
            suffix = parsed.path.removeprefix(self._prefix + '/blobs/uploads/')
            if (parsed.path != self._prefix + '/blobs/uploads/' + suffix
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', suffix)):
                raise APIError('upload-location-outside-scope')
            if parsed.query:
                if re.search(r'%(?![0-9A-Fa-f]{2})', parsed.query):
                    raise APIError('invalid-upload-location')
                fields = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
                if (len({key for key, _ in fields}) != len(fields)
                        or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', key)
                            or key.lower() in {'digest', 'mount', 'from'}
                            or any(ord(c) < 33 or ord(c) > 126 for c in val) for key, val in fields)):
                    raise APIError('invalid-upload-location')
        elif (parsed.query or parsed.path != self._prefix + ('/blobs/' + blob if blob else '/manifests/' + manifest)):
            raise APIError('upload-location-outside-scope')
        return parsed.path + ('?' + parsed.query if parsed.query else '')

    def _request(self, method, path, *, body=None, size=0, digest=None, media='application/octet-stream'):
        """Only closed methods below construct paths. No retry after send starts."""
        began = time.monotonic()
        from .owned_route import service_deadline, audit_service_route, service_request
        service_request(self._transport, method)
        self._deadline = service_deadline(self._transport, self._deadline)
        if began >= self._deadline:
            raise APIError('deadline')
        if len(self._receipts) >= MAX_REQUESTS or self._uploaded_bytes + size > MAX_UPLOAD:
            raise APIError('staging-request-or-byte-limit')
        receipt = {'method': method, 'request_sha256': hashlib.sha256(path.encode()).hexdigest(),
            'started_at': _now(), 'status': None, 'sent_bytes': 0, 'outcome': 'unknown',
            'mutation': 'attempted' if method in {'POST', 'PUT'} else 'none'}
        self._receipts.append(receipt)
        connection, response, timer = None, None, None
        expired, active = threading.Event(), []
        budget = min(120, self._deadline - began)
        request_deadline = began + budget
        def check():
            if expired.is_set() or time.monotonic() >= request_deadline:
                raise APIError('deadline')
        try:
            transport = self._transport
            connection = http.client.HTTPSConnection(transport._host, transport._port,
                timeout=budget, context=transport._context)
            def connect():
                check()
                connection.sock = transport._connect((transport._host, transport._port), request_deadline-time.monotonic())
                connection.sock = transport._context.wrap_socket(connection.sock,
                    server_hostname=transport._host, do_handshake_on_connect=False)
                active.append(connection.sock)
                check()
                connection.sock.settimeout(max(.001, request_deadline-time.monotonic()))
                connection.sock.do_handshake()
                service_request(transport, method)
                check()
            connection.connect = connect
            def abort():
                expired.set()
                sock = active[0] if active else connection.sock
                if sock is not None:
                    try:
                        socket.socket.shutdown(sock, socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()
            timer = threading.Timer(budget, abort)
            timer.daemon = True
            timer.start()
            headers = {'Authorization': self._credential, 'Accept-Encoding': 'identity',
                'User-Agent': 'devsecops-staging/1', 'Connection': 'close', 'Accept': 'application/json, ' + MANIFEST}
            if method in {'POST', 'PUT'}:
                headers.update({'Content-Type': media, 'Content-Length': str(size)})
            connection.putrequest(method, path)
            for key, value in headers.items():
                connection.putheader(key, value)
            connection.endheaders()
            checksum, sent = hashlib.sha256(), 0
            if body is not None:
                while sent < size:
                    check()
                    allowed = min(65536, size-sent)
                    chunk = body.read(allowed)
                    if not isinstance(chunk, bytes) or not 0 < len(chunk) <= allowed:
                        raise APIError('staging-source-truncated')
                    # sendall may transmit a prefix before raising. Charge the
                    # whole attempted chunk even when its final outcome is unknown.
                    self._uploaded_bytes += len(chunk)
                    connection.send(chunk)
                    sent += len(chunk)
                    receipt['sent_bytes'] = sent
                    checksum.update(chunk)
                if 'sha256:' + checksum.hexdigest() != digest:
                    raise APIError('staging-source-changed')
            check()
            response = connection.getresponse()
            receipt['status'] = response.status
            def one(name, maximum=8192):
                values = [value for key, value in response.getheaders() if key.lower() == name]
                if len(values) > 1 or values and (len(values[0]) > maximum or any(ord(c) < 32 or ord(c) > 126 for c in values[0])):
                    raise APIError('invalid-response', response.status)
                return values[0] if values else None
            if 300 <= response.status <= 399:
                raise APIError('redirect', response.status)
            result = {'status': response.status, 'location': one('location'),
                'digest': one('docker-content-digest', 128), 'challenge': one('www-authenticate'), 'body': b''}
            if method == 'GET' and response.status == 200:
                if ((one('content-type', 512) or '').split(';')[0].strip() != 'application/json'
                        or (one('content-encoding') or 'identity') != 'identity'):
                    raise APIError('invalid-response', response.status)
                length, transfer = one('content-length', 20), one('transfer-encoding', 32)
                if (length and transfer or length is not None and (not length.isdigit() or int(length) > 64000)
                        or transfer is not None and transfer.lower() != 'chunked'):
                    raise APIError('invalid-response', response.status)
                chunks, count = [], 0
                while True:
                    check()
                    chunk = response.read1(min(65536, 64001-count))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    count += len(chunk)
                    self._response_bytes += len(chunk)
                    if count > 64000 or self._response_bytes > 128000:
                        raise APIError('staging-response-limit')
                if length is not None and int(length) != count:
                    raise APIError('invalid-response', response.status)
                result['body'] = b''.join(chunks)
            check()
            from .owned_route import audit_service_route
            audit_service_route(self._transport)
            check()
            receipt['outcome'] = 'response-received'
            return result
        except APIError as error:
            receipt['outcome'] = error.code
            if method in {'POST', 'PUT'}:
                self._ready = False
            raise
        except (OSError, http.client.HTTPException, ValueError, ssl.SSLError):
            receipt['outcome'] = 'deadline' if expired.is_set() or time.monotonic() >= request_deadline else 'transport'
            if method in {'POST', 'PUT'}:
                self._ready = False
            raise APIError(receipt['outcome'], receipt['status']) from None
        finally:
            if timer is not None:
                timer.cancel()
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()
            receipt.update(finished_at=_now(), duration_ms=round((time.monotonic()-began)*1000))

    def authenticate(self, manifest_digest):
        if not _digest(manifest_digest) or self._ready or self._manifest_digest is not None:
            raise APIError('invalid-staging-authentication')
        response = self._request('HEAD', self._prefix + '/manifests/' + manifest_digest)
        if response['status'] not in {200, 401, 404}:
            raise APIError('staging-preflight-denied', response['status'])
        if self._basic and self._token_path is not None:
            if response['status'] == 401:
                _challenge(response['challenge'], self.origin, self._token_path, self._token_service, self.repository)
            # Pull,push is granted by explicit operator staging configuration,
            # never by the image or by an arbitrary challenge permission set.
            query = urlencode({'service': self._token_service, 'scope': 'repository:' + self.repository + ':pull,push'})
            issued = self._request('GET', self._token_path + '?' + query)
            if issued['status'] != 200:
                raise APIError('staging-token-denied', issued['status'])
            document = _json(issued['body'])
            token = document.get('token', document.get('access_token'))
            if (('token' in document and 'access_token' in document and document['token'] != document['access_token'])
                    or 'expires_in' in document and (type(document['expires_in']) is not int or document['expires_in'] <= 0)):
                raise APIError('invalid-auth-token')
            self._credential = _bearer(token)
        elif response['status'] == 401:
            raise APIError('staging-authentication-required')
        self._manifest_digest = manifest_digest
        self._ready = True

    def begin_blob(self):
        if not self._ready:
            raise APIError('staging-authentication-required')
        try:
            response = self._request('POST', self._prefix + '/blobs/uploads/')
            if response['status'] != 202:
                raise APIError('staging-upload-not-acknowledged', response['status'])
            location = self._location(response['location'], upload=True)
            identity = urlsplit(location).path
            if identity in self._seen_sessions:
                raise APIError('staging-upload-session-reused')
            self._seen_sessions.add(identity)
            self._sessions.add(location)
            return location
        except (APIError, ValueError):
            self._ready = False
            raise

    def finish_blob(self, location, stream, descriptor):
        if (not self._ready or location not in self._sessions or not _digest(descriptor.get('digest'))
                or type(descriptor.get('size')) is not int or not 1 <= descriptor['size'] <= MAX_UPLOAD):
            raise APIError('invalid-staging-upload')
        self._sessions.remove(location)  # Single attempt, including ambiguous failure.
        self._location(location, upload=True)
        path = location + ('&' if '?' in location else '?') + urlencode({'digest': descriptor['digest']})
        response = self._request('PUT', path, body=stream, size=descriptor['size'], digest=descriptor['digest'])
        try:
            self._acknowledge(response, descriptor['digest'], blob=True)
        except APIError:
            self._ready = False
            raise

    def put_manifest(self, stream, descriptor):
        if (not self._ready or self._manifest_attempted or descriptor.get('digest') != self._manifest_digest
                or not _digest(descriptor.get('digest')) or descriptor.get('mediaType') != MANIFEST
                or type(descriptor.get('size')) is not int or not 1 <= descriptor['size'] <= 2_000_000):
            raise APIError('invalid-staging-manifest')
        self._manifest_attempted = True
        response = self._request('PUT', self._prefix + '/manifests/' + descriptor['digest'],
            body=stream, size=descriptor['size'], digest=descriptor['digest'], media=MANIFEST)
        try:
            self._acknowledge(response, descriptor['digest'], blob=False)
        finally:
            self._ready = False  # One selected manifest attempt concludes this writer.

    def _acknowledge(self, response, digest, *, blob):
        if response['status'] != 201:
            raise APIError('staging-write-not-acknowledged', response['status'])
        self._location(response['location'], **({'blob': digest} if blob else {'manifest': digest}))
        if response['digest'] is not None and response['digest'] != digest:
            raise APIError('staging-acknowledgement-digest-mismatch')
        self._receipts[-1]['outcome'] = 'acknowledged-awaiting-readback'
