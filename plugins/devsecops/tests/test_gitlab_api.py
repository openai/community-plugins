"""Concrete bounded session against scripted sockets; never contacts a service."""
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from devsecops.factory import gitlab_api as api


TOKEN = 'SYNTHETIC_GITLAB_TOKEN_CANARY'


class Response:
    def __init__(self, value=None, *, status=200, raw=None, headers=None, failure=None):
        self.status = status
        self.raw = json.dumps(value).encode() if raw is None else raw
        self.headers = headers if headers is not None else [
            ('Content-Type', 'application/json'), ('Content-Length', str(len(self.raw)))]
        self.failure, self.offset, self.closed = failure, 0, False

    def getheaders(self):
        return self.headers

    def read1(self, count):
        if self.failure is not None:
            raise self.failure
        result = self.raw[self.offset:self.offset + count]
        self.offset += len(result)
        return result

    def close(self):
        self.closed = True


def page(value, number='1', following='', *, extra=None):
    return Response(value, headers=[('Content-Type', 'application/json'),
        ('X-Page', number), ('X-Per-Page', '100'), ('X-Next-Page', following)] + (extra or []))


@pytest.fixture
def wire(monkeypatch):
    state = SimpleNamespace(queue=[], calls=[], contexts=[], connections=[], socket_calls=[],
                            handshake_error=None, connect_error=None)
    monkeypatch.setenv('UNIT_GITLAB_TOKEN', TOKEN)

    class Socket:
        def __init__(self):
            self.closed = False

        def settimeout(self, seconds):
            assert seconds > 0

        def do_handshake(self):
            if state.handshake_error is not None:
                raise state.handshake_error

        def shutdown(self, mode):
            self.closed = True

        def close(self):
            self.closed = True

    class Context:
        minimum_version = None

        def wrap_socket(self, sock, *, server_hostname, do_handshake_on_connect):
            assert server_hostname == 'git.example.test'
            assert do_handshake_on_connect is False
            return sock

    def context(**kwargs):
        state.contexts.append(kwargs)
        return Context()

    def connect(client, address, timeout):
        state.socket_calls.append((address, timeout))
        if state.connect_error is not None:
            raise state.connect_error
        return Socket()

    class Connection:
        def __init__(self, host, port, *, timeout, context):
            assert (host, port) == ('git.example.test', 443)
            self.sock, self.response, self.closed = None, None, False
            state.connections.append(self)

        def request(self, method, path, *, body, headers):
            assert self.sock is not None
            state.calls.append({'method': method, 'path': path, 'body': body, 'headers': deepcopy(headers)})
            assert state.queue, 'Unexpected outbound scripted request'
            self.response = state.queue.pop(0)
            if isinstance(self.response, BaseException):
                raise self.response

        def getresponse(self):
            return self.response

        def close(self):
            self.closed = True
            if self.sock is not None:
                self.sock.close()

    monkeypatch.setattr(api.ssl, 'create_default_context', context)
    monkeypatch.setattr(api.HTTPSReadClient, '_connect', connect)
    monkeypatch.setattr(api.http.client, 'HTTPSConnection', Connection)
    return state


def session(**kwargs):
    return api.GitLabSession('https://git.example.test', token_env='UNIT_GITLAB_TOKEN',
        trust=kwargs.pop('trust', {'mode': 'system'}), expected_user_id=17, **kwargs)


def identity(wire, *, metadata=None, user=None, token=None):
    wire.queue.extend([Response(metadata if metadata is not None else
                               {'version': '19.5.3', 'enterprise': False}),
        Response(user if user is not None else {'id': 17, 'username': 'demo-owner', 'state': 'active'}),
        Response(token if token is not None else {'user_id': 17, 'active': True, 'revoked': False,
            'scopes': ['api'], 'expires_at': (date.today() + timedelta(days=3)).isoformat()})])


def test_operation_renewal_keeps_shared_unknown_write_latch(wire, monkeypatch):
    first = session()
    renewed = first.renew_for_operation()
    observed = first.bounded_read_session()
    assert observed.writes_blocked and not first.writes_blocked
    monkeypatch.setenv('UNIT_GITLAB_TOKEN', 'NOT_THE_ORIGINAL_TOKEN')
    identity(wire)
    wire.queue.append(Response(raw=b'{', status=201))
    with pytest.raises(api.GitLabAPIError):
        renewed.request('POST', '/api/v4/projects', {'name': 'owned'}, expected=(201,))
    assert first.writes_blocked and renewed.writes_blocked
    for old in (first, renewed, observed):
        with pytest.raises(api.GitLabAPIError, match='writes-blocked'):
            old.renew_for_operation()
    latest = first.bounded_read_session()
    wire.queue.append(Response({'id': 91}))
    assert latest.request('GET', '/api/v4/projects/91') == {'id': 91}
    assert all(call['headers']['Authorization'] == 'Bearer ' + TOKEN for call in wire.calls)


def test_read_only_renewal_cannot_upgrade_or_poison_owner(wire):
    owner = session()
    read = owner.bounded_read_session()
    with pytest.raises(api.GitLabAPIError, match='writes-blocked'):
        read.cleanup_session()
    with pytest.raises(api.GitLabAPIError, match='writes-blocked'):
        read.request('POST', '/api/v4/projects', {})
    assert not owner.writes_blocked
    clone = owner.renew_for_operation()
    assert clone._authorization == owner._authorization
    assert clone._context is owner._context and clone._lock is owner._lock
    owner.stop_writes()
    assert clone.writes_blocked


def test_write_checks_frozen_secret_identity_before_dispatch(wire, monkeypatch):
    client = session()
    monkeypatch.setenv('UNIT_GITLAB_TOKEN', 'REPLACEMENT_SYNTHETIC_SECRET')
    identity(wire)
    wire.queue.append(Response({'id': 91}, status=201))
    assert client.request('POST', '/api/v4/projects', {'name': 'demo'}, expected=(201,)) == {'id': 91}
    assert [call['path'] for call in wire.calls] == ['/api/v4/metadata', '/api/v4/user',
        '/api/v4/personal_access_tokens/self', '/api/v4/projects']
    assert {call['headers']['Authorization'] for call in wire.calls} == {'Bearer ' + TOKEN}
    assert client.origin == 'https://git.example.test' and client.expected_user_id == 17
    assert all(connection.closed for connection in wire.connections)
    assert TOKEN not in json.dumps(client.receipts)
    assert 'REPLACEMENT_SYNTHETIC_SECRET' not in json.dumps(client.receipts)


@pytest.mark.parametrize('metadata', [
    {'version': '19.5.2', 'enterprise': False}, {'version': '19.5.3-ee', 'enterprise': True},
    {'version': '19.5.3', 'enterprise': 0}, {'version': '19.5.3'}, []])
def test_unsupported_server_never_dispatches_write(wire, metadata):
    identity(wire, metadata=metadata)
    with pytest.raises(api.GitLabAPIError, match='unsupported-server'):
        session().request('POST', '/api/v4/projects', {})
    assert [call['method'] for call in wire.calls] == ['GET']


@pytest.mark.parametrize('change', [
    {'user_id': 18}, {'user_id': True}, {'active': False}, {'revoked': True},
    {'scopes': ['read_api']}, {'scopes': 'api'}, {'scopes': ['api', None]},
    {'expires_at': '2000-01-01'}, {'expires_at': None}, {'expires_at': 'bad-date'}])
def test_wrong_pat_permissions_never_dispatch_write(wire, change):
    token = {'user_id': 17, 'active': True, 'revoked': False, 'scopes': ['api'],
             'expires_at': (date.today() + timedelta(days=3)).isoformat()}
    token.update(change)
    identity(wire, token=token)
    with pytest.raises(api.GitLabAPIError, match='principal-or-scope'):
        session().request('POST', '/api/v4/projects', {})
    assert all(call['method'] == 'GET' for call in wire.calls)


@pytest.mark.parametrize('user', [
    {'id': 18, 'username': 'other', 'state': 'active'},
    {'id': 17, 'username': 'demo-owner', 'state': 'blocked'},
    {'id': 17, 'username': '', 'state': 'active'}])
def test_wrong_principal_never_dispatches_write(wire, user):
    identity(wire, user=user)
    with pytest.raises(api.GitLabAPIError, match='principal-or-scope'):
        session().request('PUT', '/api/v4/projects/91', {})
    assert all(call['method'] == 'GET' for call in wire.calls)


@pytest.mark.parametrize('reply', [
    Response({}, status=302), Response({}, status=403), Response({}, status=409),
    Response(raw=b'{"id":1,"id":2}'), Response(raw=b'NaN'),
    Response(raw=b'{"value":NaN}'), Response(raw=b'not-json'),
    Response({}, headers=[('Content-Type', 'text/html')]),
    Response({}, failure=TimeoutError('DO_NOT_EXPOSE_REMOTE_DIAGNOSTIC'))])
def test_uncertain_write_poisoned_but_read_reconciliation_available(wire, reply):
    client = session()
    identity(wire)
    wire.queue.append(reply)
    with pytest.raises(api.GitLabAPIError) as error:
        client.request('PUT', '/api/v4/projects/91', {})
    assert error.value.outcome == 'unknown' and client.writes_blocked
    assert 'DO_NOT_EXPOSE' not in str(error.value)
    assert client.receipts[-1]['outcome'] == 'unknown'
    count = len(wire.calls)
    with pytest.raises(api.GitLabAPIError, match='writes-blocked'):
        client.request('PUT', '/api/v4/projects/91', {})
    assert len(wire.calls) == count
    wire.queue.append(Response({'id': 91}))
    assert client.request('GET', '/api/v4/projects/91') == {'id': 91}


def test_cancellation_after_dispatch_poisoned(wire):
    client = session()
    identity(wire)
    wire.queue.append(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        client.request('POST', '/api/v4/projects', {})
    assert client.writes_blocked and client.receipts[-1]['outcome'] == 'unknown'


def test_connection_failure_is_known_before_dispatch(wire):
    client = session()
    wire.connect_error = TimeoutError('PRIVATE_CONNECTION_DIAGNOSTIC')
    with pytest.raises(api.GitLabAPIError) as error:
        client.request('GET', '/api/v4/projects/91')
    assert error.value.outcome == 'none'
    assert not wire.calls and not client.writes_blocked
    assert 'PRIVATE' not in str(error.value)


def test_mutation_tls_failure_is_known_before_dispatch(wire, monkeypatch):
    client = session()
    identity(wire)
    original = api.HTTPSReadClient._connect

    def fail_fourth(client, address, timeout):
        if len(wire.socket_calls) == 3:
            raise TimeoutError('PRIVATE_TLS_DIAGNOSTIC')
        return original(client, address, timeout)

    monkeypatch.setattr(api.HTTPSReadClient, '_connect', fail_fourth)
    with pytest.raises(api.GitLabAPIError) as error:
        client.request('POST', '/api/v4/projects', {})
    assert error.value.outcome == 'none' and not client.writes_blocked
    assert [call['method'] for call in wire.calls] == ['GET'] * 3


def test_stop_writes_keeps_reads_available(wire):
    client = session()
    client.stop_writes()
    with pytest.raises(api.GitLabAPIError, match='writes-blocked'):
        client.request('POST', '/api/v4/projects', {})
    wire.queue.append(Response({}))
    assert client.request('GET', '/api/v4/projects/91') == {}
    assert len(wire.calls) == 1


@pytest.mark.parametrize('path', ['https://other.invalid/api/v4/projects', '//other.invalid/api/v4/projects',
    '/api/v4/../projects', '/api/v4/%2e%2e/projects', '/api/v4/projects/%252e%252e',
    '/api/v4/projects/%0d', '/api/v4/projects/%GG', '/api/v4/projects?bad',
    '/api/v4/projects?a=1&a=2', '/api/v4/projects#fragment'])
def test_invalid_path_never_dispatches(wire, path):
    with pytest.raises(api.GitLabAPIError):
        session().request('GET', path)
    assert not wire.calls


def test_payload_and_request_limits(wire):
    client = session(max_requests=1)
    wire.queue.append(Response({}))
    client.request('GET', '/api/v4/projects/91')
    with pytest.raises(api.GitLabAPIError, match='budget-exhausted'):
        client.request('GET', '/api/v4/projects/91')
    assert len(wire.calls) == 1
    with pytest.raises(api.GitLabAPIError, match='invalid-request'):
        session().request('GET', '/api/v4/projects/91', {})


def test_body_limit_is_enforced(wire):
    wire.queue.append(Response({'large': 'x' * 128}))
    with pytest.raises(api.GitLabAPIError, match='body-limit'):
        session(max_body_bytes=64).request('GET', '/api/v4/projects/91')


def test_deadline_checked_after_json_parse(wire, monkeypatch):
    client = session()
    clock = [100.0]
    monkeypatch.setattr(api.time, 'monotonic', lambda: clock[0])
    original = api._json

    def slow_parse(raw):
        result = original(raw)
        clock[0] += 20
        return result

    monkeypatch.setattr(api, '_json', slow_parse)
    wire.queue.append(Response({}))
    with pytest.raises(api.GitLabAPIError, match='deadline'):
        client.request('GET', '/api/v4/projects/91')


def test_collection_follows_numeric_pages_on_same_origin(wire):
    wire.queue.extend([page([{'id': 1}], following='2'), page([{'id': 2}], number='2')])
    result = session().get_collection('/api/v4/projects/91/repository/tree?ref=' + 'a' * 40 +
                                     '&recursive=true&per_page=100')
    assert result == [{'id': 1}, {'id': 2}]
    assert all(call['path'].startswith('/api/v4/projects/91/repository/tree?') for call in wire.calls)
    assert wire.calls[-1]['path'].endswith('page=2')


@pytest.mark.parametrize('reply', [
    Response([]), page([], number='2'), page([], following='3'),
    page([], extra=[('Link', '<https://other.invalid/>; rel="next"')]),
    page([], extra=[('Link', '<https://other.invalid/>; rel="prev next"')]),
    page([], extra=[('Link', 'malformed')]),
    page({}, following=''), page(list(range(101))),
    page([], extra=[('X-Next-Page', '')])])
def test_incomplete_or_ambiguous_collection_rejected(wire, reply):
    wire.queue.append(reply)
    with pytest.raises(api.GitLabAPIError):
        session().get_collection('/api/v4/projects/91/merge_requests')
    assert len(wire.calls) == 1


def test_collection_preserves_non_next_relations_without_following_urls(wire):
    wire.queue.append(page([], extra=[('Link', '<https://other.invalid/>; rel="prev first"')]))
    assert session().get_collection('/api/v4/projects/91/merge_requests') == []
    assert len(wire.calls) == 1


@pytest.mark.parametrize('collection', [False, True])
def test_concurrent_request_rejected_without_waiting(wire, collection):
    client = session()
    locked, release = threading.Event(), threading.Event()

    def hold():
        with client._lock:
            locked.set()
            release.wait(2)

    holder = threading.Thread(target=hold)
    holder.start()
    assert locked.wait(1)
    try:
        with pytest.raises(api.GitLabAPIError, match='concurrent-request'):
            if collection:
                client.get_collection('/api/v4/projects/91/merge_requests')
            else:
                client.request('GET', '/api/v4/projects/91')
        assert not wire.calls
    finally:
        release.set()
        holder.join(2)


def test_collection_page_bound(wire):
    wire.queue.append(page([], following='2'))
    with pytest.raises(api.GitLabAPIError, match='collection-limit'):
        session().get_collection('/api/v4/projects/91/merge_requests', max_pages=1)


def test_pinned_bundle_snapshot_not_reopened(wire, tmp_path):
    ca = tmp_path / 'synthetic-ca.pem'
    raw = b'CONTROLLED_SYNTHETIC_CA_BYTES'
    ca.write_bytes(raw)
    client = session(trust={'mode': 'bundle', 'path': str(ca), 'sha256': hashlib.sha256(raw).hexdigest()})
    ca.write_bytes(b'CHANGED')
    wire.queue.append(Response({}))
    client.request('GET', '/api/v4/projects/91')
    assert wire.contexts == [{'cadata': raw.decode()}]


def test_wrong_bundle_pin_rejected_before_network(wire, tmp_path):
    ca = tmp_path / 'synthetic-ca.pem'
    ca.write_bytes(b'CONTROLLED_SYNTHETIC_CA_BYTES')
    with pytest.raises(api.GitLabAPIError, match='invalid-configuration'):
        session(trust={'mode': 'bundle', 'path': str(ca), 'sha256': 'a' * 64})
    assert not wire.calls and not wire.contexts


def test_delete_empty_response_is_bounded_and_principal_checked(wire):
    client = session()
    identity(wire)
    wire.queue.append(Response(raw=b'', status=204, headers=[]))
    assert client.request('DELETE', '/api/v4/runners/91', expected=(204,)) is None
    assert wire.calls[-1]['method'] == 'DELETE'
    assert not client.writes_blocked


@pytest.mark.parametrize('headers,raw', [([('Content-Length', '1')], b'x'),
    ([('Transfer-Encoding', 'chunked')], b''), ([], b'{}')])
def test_malformed_delete_acknowledgement_stays_unknown(wire, headers, raw):
    client = session()
    identity(wire)
    wire.queue.append(Response(raw=raw, status=204, headers=headers))
    with pytest.raises(api.GitLabAPIError):
        client.request('DELETE', '/api/v4/runners/91', expected=(204,))
    assert client.writes_blocked
    before = len(wire.calls)
    with pytest.raises(api.GitLabAPIError):
        client.request('DELETE', '/api/v4/runners/91', expected=(204,))
    assert len(wire.calls) == before


def test_cleanup_transport_uses_original_secret_and_cannot_clear_poison(wire, monkeypatch):
    client = session()
    monkeypatch.setenv('UNIT_GITLAB_TOKEN', 'REPLACEMENT_SYNTHETIC_SECRET')
    renewed = client.cleanup_session()
    assert renewed._authorization == client._authorization
    assert renewed._context is client._context
    client.stop_writes()
    with pytest.raises(api.GitLabAPIError):
        client.cleanup_session()
    observation = client.cleanup_session(read_only=True)
    assert observation.writes_blocked
    wire.queue.append(Response({'id': 91}))
    assert observation.request('GET', '/api/v4/runners/91')['id'] == 91
    before = len(wire.calls)
    with pytest.raises(api.GitLabAPIError):
        observation.request('DELETE', '/api/v4/runners/91', expected=(204,))
    assert len(wire.calls) == before
