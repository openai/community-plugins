"""Bounded GitLab 19.5.3 CE transport; not an admission or factory grant.

This module is deliberately not registered in a CLI or factory preflight. Its
caller must obtain action-time authority for the selected external mutations.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone, date
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, unquote

from devsecops.adapters.http import APIError, HTTPSReadClient, _bearer, _unique
from .git_destination import _origin, _read_input

VERSION = '19.5.3'


class GitLabAPIError(ValueError):
    """Safe category only; never include remote bodies, headers or credentials."""
    def __init__(self, code, status=None, outcome='none'):
        self.code, self.status, self.outcome = code, status, outcome
        super().__init__('GitLab operation could not complete (' + code + ')')


def _json(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(value) not in (dict, list):
            raise ValueError
        pending, count = [(value, 0)], 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if depth > 48 or count > 100_000:
                raise ValueError
            if type(item) is dict:
                pending.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                pending.extend((child, depth + 1) for child in item)
            elif type(item) is float and not math.isfinite(item):
                raise ValueError
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise GitLabAPIError('invalid-json') from None


def _path(value):
    if (not isinstance(value, str) or len(value) > 4096
            or not re.fullmatch(r'/api/v4/[A-Za-z0-9_./%~?=&+:-]+', value)
            or re.search(r'%(?![0-9A-Fa-f]{2})', value)):
        raise GitLabAPIError('invalid-request')
    parsed = urlsplit(value)
    decoded = unquote(parsed.path)
    if (parsed.netloc or parsed.fragment or '\\' in decoded or '%' in decoded
            or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
            or any(part in ('', '.', '..') for part in decoded.split('/')[1:])):
        raise GitLabAPIError('invalid-request')
    try:
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise GitLabAPIError('invalid-request') from None
    if len(query) > 16 or len({key for key, _ in query}) != len(query):
        raise GitLabAPIError('invalid-request')
    return parsed, query


class GitLabSession:
    """One frozen PAT and trust snapshot, no redirects/proxies or write retries.

    Identity is verified again before every mutation. Unknown mutations poison
    this session; callers additionally retain a durable, exclusive intent before
    dispatch so a new session cannot turn an uncertain outcome into a retry.
    """
    def __init__(self, origin, *, token_env, trust, expected_user_id,
                 timeout=10, total_timeout=600, max_requests=160,
                 max_body_bytes=2_000_000, max_total_bytes=32_000_000, service_route=None):
        try:
            self._origin = _origin(origin)
            if (type(expected_user_id) is not int or not 0 < expected_user_id < 2**63
                    or not isinstance(token_env, str)
                    or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', token_env)
                    or any(type(v) not in (float, int) or not math.isfinite(v) or not 0 < v <= cap
                           for v, cap in ((timeout, 30), (total_timeout, 900)))
                    or any(type(v) is not int or not 0 < v <= cap for v, cap in
                           ((max_requests, 512), (max_body_bytes, 4_000_000),
                            (max_total_bytes, 64_000_000)))):
                raise ValueError
            self._authorization = _bearer(os.environ.get(token_env, ''))
            if trust == {'mode': 'system'}:
                context = ssl.create_default_context()
            elif (type(trust) is dict and set(trust) == {'mode', 'path', 'sha256'}
                    and trust['mode'] == 'bundle' and isinstance(trust['path'], str)
                    and Path(trust['path']).is_absolute()
                    and re.fullmatch('[a-f0-9]{64}', trust['sha256'])):
                raw, _ = _read_input(Path(trust['path']), 2_000_000, time.monotonic() + timeout)
                if hashlib.sha256(raw).hexdigest() != trust['sha256']:
                    raise ValueError
                context = ssl.create_default_context(cadata=raw.decode('ascii'))
            else:
                raise ValueError
            from devsecops.adapters.owned_route import bind_service_route
            route_ca = bind_service_route(self, service_route, origin=self._origin,
                ca_sha256=trust.get('sha256') if trust.get('mode') == 'bundle' else None)
            if route_ca is not None:
                context = ssl.create_default_context(cadata=route_ca.decode('ascii'))
            context.minimum_version = ssl.TLSVersion.TLSv1_2
        except (ValueError, TypeError, OSError, APIError, ssl.SSLError):
            raise GitLabAPIError('invalid-configuration') from None
        self._expected_user_id, self._context = expected_user_id, context
        parsed = urlsplit(origin)
        self._host, self._port = parsed.hostname, parsed.port or 443
        self._timeout, self._deadline = timeout, time.monotonic() + total_timeout
        self._max_requests, self._max_body, self._max_total = max_requests, max_body_bytes, max_total_bytes
        self._requests, self._bytes = 0, 0
        from devsecops.adapters.owned_route import audit_service_route
        route_binding = audit_service_route(self)
        self._write_state, self._read_only = {'unknown': False}, bool(route_binding and route_binding['read_only'])
        self._receipts, self._pagination = [], {}
        self._lock = threading.RLock()

    @property
    def origin(self):
        return self._origin

    @property
    def expected_user_id(self):
        return self._expected_user_id

    @property
    def receipts(self):
        return deepcopy(self._receipts)

    @property
    def writes_blocked(self):
        return self._read_only or self._poisoned

    @property
    def _poisoned(self):
        return self._write_state['unknown']

    @_poisoned.setter
    def _poisoned(self, value):
        # One irreversible latch shared by every transport from this origin.
        # The initialization branch also supports construction in socket tests.
        if not hasattr(self, '_write_state'):
            self._write_state, self._read_only = {'unknown': bool(value)}, False
        elif value:
            self._write_state['unknown'] = True

    def stop_writes(self):
        """A caller's ambiguous semantic readback also blocks subsequent writes."""
        self._poisoned = True

    def bounded_read_session(self, *, service_route=None):
        """Renew bounded polling reads with the exact frozen principal and trust."""
        return self.cleanup_session(read_only=True, service_route=service_route)

    def renew_for_operation(self, *, service_route=None):
        """Renew transport only; the lifecycle caller still admits the action.

        No credential reload, deadline mutation, or uncertain-write reset. The
        caller must revalidate current phase grants and fresh remote identities.
        """
        return self.cleanup_session(service_route=service_route)

    def cleanup_session(self, *, read_only=False, service_route=None):
        """Fresh bounded transport using the original frozen principal/trust.

        Only the owning lifecycle caller can authorize a cleanup operation.
        Renewal never clears uncertain-write poison or reloads credentials.
        """
        if not self._lock.acquire(blocking=False):
            raise GitLabAPIError('concurrent-request')
        try:
            if type(read_only) is not bool:
                raise GitLabAPIError('invalid-request')
            if self.writes_blocked and not read_only:
                raise GitLabAPIError('writes-blocked', outcome='unknown')
            result = object.__new__(GitLabSession)
            for name in ('_origin', '_authorization', '_expected_user_id', '_context',
                         '_host', '_port', '_timeout', '_max_requests', '_max_body', '_max_total'):
                setattr(result, name, getattr(self, name))
            from devsecops.adapters.owned_route import bind_service_route
            route = service_route if service_route is not None else getattr(self, '_service_route', None)
            # A direct session cannot be silently rebound to a new trust/origin.
            pin = getattr(self, '_service_route_ca_sha256', None)
            if route is not None and pin is None:
                raise GitLabAPIError('unselected-service-route')
            try:
                bind_service_route(result, route, origin=self._origin, ca_sha256=pin)
            except APIError as error:
                raise GitLabAPIError(error.code) from None
            result._deadline = time.monotonic() + 300
            result._requests, result._bytes = 0, 0
            from devsecops.adapters.owned_route import audit_service_route
            route_binding = audit_service_route(result)
            result._write_state = self._write_state
            result._read_only = read_only or bool(route_binding and route_binding['read_only'])
            result._receipts, result._pagination = [], {}
            result._lock = self._lock
            return result
        finally:
            self._lock.release()

    def metadata(self):
        result = self.request('GET', '/api/v4/metadata')
        if (type(result) is not dict or result.get('version') != VERSION
                or result.get('enterprise') is not False):
            raise GitLabAPIError('unsupported-server')
        return result

    def principal(self):
        user = self.request('GET', '/api/v4/user')
        token = self.request('GET', '/api/v4/personal_access_tokens/self')
        try:
            if (type(user) is not dict or type(token) is not dict
                    or type(user.get('id')) is not int or user['id'] != self.expected_user_id
                    or user.get('state') != 'active'
                    or not isinstance(user.get('username'), str) or not user['username']
                    or type(token.get('user_id')) is not int or token['user_id'] != user['id']
                    or token.get('active') is not True or token.get('revoked') is not False
                    or type(token.get('scopes')) is not list or 'api' not in token['scopes']
                    or not all(isinstance(item, str) for item in token['scopes'])
                    or date.fromisoformat(token['expires_at']) <= datetime.now(timezone.utc).date()):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise GitLabAPIError('principal-or-scope') from None
        return user

    def verify_identity(self):
        return {'metadata': self.metadata(), 'principal': self.principal()}

    def request(self, method, path, payload=None, expected=(200,)):
        if not self._lock.acquire(blocking=False):
            raise GitLabAPIError('concurrent-request')
        try:
            _path(path)
            if (method not in ('GET', 'POST', 'PUT', 'DELETE')
                    or (method == 'GET' and payload is not None)
                    or (method in ('POST', 'PUT') and type(payload) is not dict)
                    or (method == 'DELETE' and payload is not None and type(payload) is not dict)
                    or type(expected) is not tuple or not expected
                    or any(type(item) is not int or not 200 <= item <= 299 for item in expected)):
                raise GitLabAPIError('invalid-request')
            from devsecops.adapters.owned_route import service_request
            try:
                service_request(self, method)
            except APIError as error:
                raise GitLabAPIError(error.code) from None
            if method != 'GET':
                if self.writes_blocked:
                    raise GitLabAPIError('writes-blocked', outcome='unknown')
                self.verify_identity()
            return self._exchange(method, path, payload, expected)
        finally:
            self._lock.release()

    def request_artifact(self, project_id, job_id, path):
        """Read one exact job artifact with this session's frozen credential.

        No ref/latest selection, archive expansion, redirect, or external URL.
        The bytes remain untrusted input even after authenticated acquisition.
        """
        if (any(type(value) is not int or not 0 < value < 2**63 for value in (project_id, job_id))
                or not isinstance(path, str) or not 1 <= len(path) <= 1024
                or any(not re.fullmatch('[A-Za-z0-9_~.-]+', part) or part in ('.', '..')
                       for part in path.split('/'))):
            raise GitLabAPIError('invalid-artifact-selection')
        if not self._lock.acquire(blocking=False):
            raise GitLabAPIError('concurrent-request')
        try:
            route = f'/api/v4/projects/{project_id}/jobs/{job_id}/artifacts/' + path
            _path(route)
            return self._exchange('GET', route, None, (200,), artifact_bytes=True)
        finally:
            self._lock.release()

    def request_repository_blob(self, project_id, blob_sha):
        """Bounded exact Git blob bytes; no branch, file path or redirect lookup."""
        if (type(project_id) is not int or not 0 < project_id < 2**63
                or not isinstance(blob_sha, str) or not re.fullmatch('[a-f0-9]{40}', blob_sha)):
            raise GitLabAPIError('invalid-blob-selection')
        if not self._lock.acquire(blocking=False):
            raise GitLabAPIError('concurrent-request')
        try:
            route = f'/api/v4/projects/{project_id}/repository/blobs/{blob_sha}/raw'
            return self._exchange('GET', route, None, (200,), artifact_bytes=True)
        finally:
            self._lock.release()

    def _exchange(self, method, path, payload, expected, *, artifact_bytes=False):
        try:
            body = None if payload is None else json.dumps(payload, allow_nan=False,
                sort_keys=True, separators=(',', ':')).encode('utf-8')
        except (ValueError, TypeError, RecursionError):
            raise GitLabAPIError('invalid-request') from None
        if body is not None and len(body) > 2_000_000:
            raise GitLabAPIError('request-body-limit')
        from devsecops.adapters.owned_route import service_deadline
        try:
            self._deadline = service_deadline(self, self._deadline)
        except APIError as error:
            raise GitLabAPIError(error.code) from None
        budget = min(self._timeout, self._deadline - time.monotonic())
        if budget <= 0 or self._requests >= self._max_requests or self._bytes >= self._max_total:
            raise GitLabAPIError('budget-exhausted')
        self._requests += 1
        receipt = {'method': method, 'request_sha256': hashlib.sha256(path.encode()).hexdigest(),
                   'payload_sha256': None if body is None else hashlib.sha256(body).hexdigest(),
                   'status': None, 'outcome': 'none'}
        self._receipts.append(receipt)
        self._pagination = {}
        connection = http.client.HTTPSConnection(self._host, self._port, timeout=budget, context=self._context)
        end, expired, sockets = time.monotonic() + budget, threading.Event(), []

        def connect():
            left = end - time.monotonic()
            if left <= 0 or expired.is_set():
                raise GitLabAPIError('deadline')
            # Reuse the existing bounded resolver. Its abandoned DNS thread has
            # no socket, credential or permission to send a request.
            connection.sock = HTTPSReadClient._connect(self, (self._host, self._port), left)
            connection.sock = self._context.wrap_socket(connection.sock, server_hostname=self._host,
                                                        do_handshake_on_connect=False)
            sockets.append(connection.sock)
            left = end - time.monotonic()
            if left <= 0 or expired.is_set():
                raise GitLabAPIError('deadline')
            connection.sock.settimeout(left)
            connection.sock.do_handshake()
            from devsecops.adapters.owned_route import service_request
            service_request(self, method)
            if time.monotonic() >= end or expired.is_set():
                raise GitLabAPIError('deadline')

        def abort():
            expired.set()
            sock = sockets[0] if sockets else connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()

        connection.connect = connect
        timer = threading.Timer(budget, abort)
        timer.daemon = True
        attempted, response = False, None
        try:
            timer.start()
            # Connect before marking a write uncertain; no credential is sent
            # until TLS and the local deadline both pass.
            connection.connect()
            if expired.is_set() or time.monotonic() >= end:
                raise GitLabAPIError('deadline')
            attempted = True
            connection.request(method, path, body=body, headers={'Authorization': self._authorization,
                'Accept': 'application/json, text/plain, application/octet-stream' if artifact_bytes else 'application/json', 'Content-Type': 'application/json',
                'Accept-Encoding': 'identity', 'Connection': 'close'})
            response = connection.getresponse()
            receipt['status'] = response.status
            if response.status not in expected:
                raise GitLabAPIError('redirect' if 300 <= response.status <= 399 else 'http-status', response.status)
            headers = {}
            for name, value in response.getheaders():
                name = name.lower()
                if name in ('content-type', 'content-length', 'content-encoding', 'transfer-encoding',
                            'x-page', 'x-next-page', 'x-per-page', 'link'):
                    if name in headers or len(value) > 8192 or any(ord(c) < 32 or ord(c) > 126 for c in value):
                        raise GitLabAPIError('invalid-headers', response.status)
                    headers[name] = value
            empty = response.status == 204
            if (empty and (method != 'DELETE' or headers.get('content-length', '0') != '0'
                           or 'transfer-encoding' in headers)):
                raise GitLabAPIError('invalid-empty-response', response.status)
            media = ('application/json', 'text/plain', 'application/octet-stream') if artifact_bytes else ('application/json',)
            if ((not empty and headers.get('content-type', '').split(';')[0].strip().lower() not in media)
                    or headers.get('content-encoding', 'identity') != 'identity'
                    or ('content-length' in headers and 'transfer-encoding' in headers)
                    or headers.get('transfer-encoding', 'chunked') != 'chunked'):
                raise GitLabAPIError('invalid-headers', response.status)
            allowed = min(self._max_body, self._max_total - self._bytes)
            length = headers.get('content-length')
            if length is not None and (not re.fullmatch('[0-9]+', length) or int(length) > allowed):
                raise GitLabAPIError('body-limit', response.status)
            chunks, size = [], 0
            while True:
                if expired.is_set() or time.monotonic() >= end:
                    raise GitLabAPIError('deadline', response.status)
                chunk = response.read1(min(65536, allowed + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                self._bytes += len(chunk)
                if size > allowed:
                    raise GitLabAPIError('body-limit', response.status)
                chunks.append(chunk)
            if (expired.is_set() or time.monotonic() >= end
                    or (length is not None and int(length) != size)):
                raise GitLabAPIError('incomplete-response', response.status)
            raw = b''.join(chunks)
            if empty and raw:
                raise GitLabAPIError('invalid-empty-response', response.status)
            value = None if empty else raw if artifact_bytes else _json(raw)
            if expired.is_set() or time.monotonic() >= end:
                raise GitLabAPIError('deadline', response.status)
            self._pagination = {key: value for key, value in headers.items()
                                if key in ('x-page', 'x-next-page', 'x-per-page', 'link')}
            from devsecops.adapters.owned_route import audit_service_route
            audit_service_route(self)
            if time.monotonic() >= end or expired.is_set():
                raise GitLabAPIError('deadline', response.status)
            receipt.update(outcome='observed', body_sha256=hashlib.sha256(raw).hexdigest())
            return value
        except BaseException as error:
            unknown = method != 'GET' and attempted
            if unknown:
                self._poisoned = True
            receipt['outcome'] = 'unknown' if unknown else 'none'
            if not isinstance(error, Exception):
                raise
            code = error.code if isinstance(error, (GitLabAPIError, APIError)) else 'transport'
            raise GitLabAPIError(code, receipt['status'], receipt['outcome']) from None
        finally:
            timer.cancel()
            if response is not None:
                response.close()
            connection.close()

    def get_collection(self, path, *, max_pages=8, max_items=800):
        """Follow only local numeric pages, requiring explicit pagination closure."""
        if (type(max_pages) is not int or not 1 <= max_pages <= 8
                or type(max_items) is not int or not 1 <= max_items <= 800):
            raise GitLabAPIError('invalid-pagination')
        parsed, query = _path(path)
        params = dict(query)
        if 'page' in params or params.get('per_page', '100') != '100':
            raise GitLabAPIError('invalid-pagination')
        params['per_page'] = '100'
        result = []
        if not self._lock.acquire(blocking=False):
            raise GitLabAPIError('concurrent-request')
        try:
            for page in range(1, max_pages + 1):
                params['page'] = str(page)
                values = self.request('GET', parsed.path + '?' + urlencode(params))
                headers = self._pagination
                if (type(values) is not list or len(values) > 100
                        or headers.get('x-page') != str(page) or headers.get('x-per-page') != '100'
                        or 'x-next-page' not in headers):
                    raise GitLabAPIError('incomplete-pagination')
                result.extend(values)
                if len(result) > max_items:
                    raise GitLabAPIError('collection-limit')
                following = headers['x-next-page']
                link = headers.get('link', '')
                relation = r'(?:first|last|prev|next)(?: (?:first|last|prev|next))*'
                entry = r'<[^<>\x00-\x20]+>;\s*rel="' + relation + '"'
                if link and not re.fullmatch(entry + r'(?:,\s*' + entry + r')*', link):
                    raise GitLabAPIError('incomplete-pagination')
                if following == '':
                    if any('next' in relations.split() for relations in re.findall(r'rel="([^"]+)"', link)):
                        raise GitLabAPIError('incomplete-pagination')
                    return result
                if following != str(page + 1):
                    raise GitLabAPIError('incomplete-pagination')
        finally:
            self._lock.release()
        raise GitLabAPIError('collection-limit')
