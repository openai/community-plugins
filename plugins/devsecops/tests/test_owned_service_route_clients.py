"""Real loopback TLS and controlled original-route boundary; no live services."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import ssl
import subprocess
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from devsecops.adapters import owned_route
from devsecops.adapters.http import APIError, HTTPSReadClient
from devsecops.adapters.staging_registry import _StagingRegistry
from devsecops.factory.gitlab_api import GitLabSession, GitLabAPIError
from devsecops.factory.git_receive_http import Budget, GitReceiveClient
from tests import test_registry_evidence as registry_fixture

registry = registry_fixture.registry
ca = registry_fixture.ca
install = registry_fixture.install
collect = registry_fixture.collect


@pytest.fixture
def routed(tmp_path, monkeypatch):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "/usr/bin/openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=git.factory.invalid",
            "-addext",
            "subjectAltName=DNS:git.factory.invalid",
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    state = SimpleNamespace(calls=[], sni=[], expired=False, after_response=False, ca=cert)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            state.calls.append(
                (self.command, self.path, self.headers.get("Host"), self.headers.get("Authorization"))
            )
            raw = b'{"id":17}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if state.after_response:
                state.expired = True
            self.wfile.write(raw)

        def do_HEAD(self):
            state.calls.append(
                (self.command, self.path, self.headers.get("Host"), self.headers.get("Authorization"))
            )
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    class QuietServer(ThreadingHTTPServer):
        daemon_threads = False

        def handle_error(self, request, address):
            pass

    server = QuietServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)

    def sni(sock, name, ctx):
        state.sni.append(name)
        if getattr(state, "expire_on_sni", False):
            state.expired = True

    context.set_servername_callback(sni)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    state.origin = "https://git.factory.invalid"
    state.route = object()
    state.routes = {state.route}
    state.sha = hashlib.sha256(cert.read_bytes()).hexdigest()
    state.deadline = time.monotonic() + 30

    def binding(route, origin):
        if route not in state.routes or origin != state.origin or state.expired and route is state.route:
            raise APIError("owned-service-route-unavailable")
        return dict(
            origin=origin,
            connect_host="127.0.0.1",
            connect_port=server.server_port,
            ca_path=cert,
            ca_sha256=state.sha,
            deadline=state.deadline,
            read_only=getattr(state, "read_only", False),
        )

    monkeypatch.setattr(owned_route, "_binding", binding)
    monkeypatch.setenv("CONTROLLED_ROUTE_TOKEN", "controlled-private-token")
    state.trust = {"mode": "bundle", "path": str(cert), "sha256": state.sha}
    state.binding = binding
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("kind", ["read", "gitlab", "receive", "registry"])
def test_selected_route_uses_actual_socket_but_preserves_host_sni_ca(routed, kind):
    s = routed
    if kind == "read":
        client = HTTPSReadClient(
            s.origin, ca_bundle=s.ca, token_env="CONTROLLED_ROUTE_TOKEN", service_route=s.route
        )
        assert client.get_json("/api") == {"id": 17}
    elif kind == "gitlab":
        client = GitLabSession(
            s.origin,
            token_env="CONTROLLED_ROUTE_TOKEN",
            trust=s.trust,
            expected_user_id=17,
            service_route=s.route,
        )
        assert client.request("GET", "/api/v4/user") == {"id": 17}
    elif kind == "receive":
        client = GitReceiveClient(
            origin=s.origin,
            repository_path="/owned/repo.git",
            context=ssl.create_default_context(),
            token="controlled-private-token",
            budget=Budget(),
            service_route=s.route,
            ca_sha256=s.sha,
        )
        assert json.loads(client.read_api("/api/v4/user")[2]) == {"id": 17}
    else:
        client = _StagingRegistry(
            s.origin,
            repository="owned/repo",
            deadline=time.monotonic() + 20,
            token_env="CONTROLLED_ROUTE_TOKEN",
            ca_bundle=s.ca,
            service_route=s.route,
        )
        assert client._request("HEAD", "/v2/owned/repo/manifests/sha256:" + "1" * 64)["status"] == 200
    assert s.sni == ["git.factory.invalid"]
    assert len(s.calls) == 1 and s.calls[0][2] == "git.factory.invalid"
    assert s.calls[0][3] == "Bearer controlled-private-token"


@pytest.mark.parametrize("damage", ["expired", "ca"])
def test_route_reaudited_before_each_read_without_direct_dns_fallback(routed, damage):
    s = routed
    client = HTTPSReadClient(s.origin, ca_bundle=s.ca, service_route=s.route)
    assert client.get_json("/api") == {"id": 17}
    if damage == "expired":
        s.expired = True
    else:
        s.sha = "0" * 64
    with pytest.raises(APIError):
        client.get_json("/api")
    assert len(s.calls) == 1


def test_read_response_after_route_expiry_is_not_accepted(routed):
    s = routed
    client = HTTPSReadClient(s.origin, ca_bundle=s.ca, service_route=s.route)
    s.after_response = True
    with pytest.raises(APIError):
        client.get_json("/api")
    assert len(s.calls) == 1 and client.receipts[-1]["outcome"] != "complete"


def test_ca_override_and_unselected_origin_fail_before_socket(routed):
    s = routed
    with pytest.raises(APIError):
        HTTPSReadClient(s.origin, service_route=s.route)
    with pytest.raises(APIError):
        HTTPSReadClient("https://other.invalid", ca_bundle=s.ca, service_route=s.route)
    with pytest.raises(APIError):
        GitReceiveClient(
            origin=s.origin,
            repository_path="/owned/repo.git",
            context=ssl.create_default_context(),
            token="controlled",
            budget=Budget(),
            service_route=s.route,
            ca_sha256="0" * 64,
        )
    assert s.calls == []


def test_explicit_route_renewal_keeps_original_secret_and_unknown_write_latch(routed, monkeypatch):
    s = routed
    first = GitLabSession(
        s.origin,
        token_env="CONTROLLED_ROUTE_TOKEN",
        trust=s.trust,
        expected_user_id=17,
        service_route=s.route,
    )
    fresh = object()
    s.routes.add(fresh)
    s.expired = True
    monkeypatch.setenv("CONTROLLED_ROUTE_TOKEN", "replacement-not-selected")
    with pytest.raises(GitLabAPIError):
        first.renew_for_operation()
    renewed = first.renew_for_operation(service_route=fresh)
    assert renewed.request("GET", "/api/v4/user") == {"id": 17}
    first.stop_writes()
    with pytest.raises(GitLabAPIError, match="writes-blocked"):
        renewed.renew_for_operation(service_route=fresh)
    readonly = first.bounded_read_session(service_route=fresh)
    assert readonly.request("GET", "/api/v4/user") == {"id": 17}
    assert all(call[3] == "Bearer controlled-private-token" for call in s.calls)


def test_registry_evidence_canonical_collector_forwards_original_route(registry, tmp_path, monkeypatch):
    # Existing real authenticated registry fixture. Change logical port only;
    # the certificate still verifies the original IP. No DNS or proxy override.
    port = urlsplit(registry["origin"]).port
    registry["origin"] = "https://127.0.0.1"
    route = object()
    sha = hashlib.sha256(registry["ca"].read_bytes()).hexdigest()

    def binding(value, origin):
        if value is not route or origin != registry["origin"]:
            raise APIError("route")
        return dict(
            origin=origin,
            connect_host="127.0.0.1",
            connect_port=port,
            ca_path=registry["ca"],
            ca_sha256=sha,
            deadline=time.monotonic() + 30,
            read_only=False,
        )

    monkeypatch.setattr(owned_route, "_binding", binding)
    data = install(registry)
    result = collect(registry, data, tmp_path / "routed-registry", service_route=route)
    assert result["observations"]["acquisition"]["decision"] == "pass"
    assert len(registry["requests"]) == 12


def test_actual_mission_evidence_reader_forwards_original_route(routed):
    from devsecops.adapters.mission_evidence import EvidenceReader

    s = routed
    selected = {
        "origin": s.origin,
        "repository": "owned/mission",
        "credentials": {"token_env": "CONTROLLED_ROUTE_TOKEN"},
    }
    reader = EvidenceReader(selected, ca_bundle=s.ca, deadline=time.monotonic() + 20, service_route=s.route)
    assert reader.client.get_json("/api") == {"id": 17}
    assert s.sni == ["git.factory.invalid"] and s.calls[0][2] == "git.factory.invalid"


def test_owned_socket_never_uses_dns_and_phase_deadline_is_not_extended(routed, monkeypatch):
    import socket

    s = routed
    original_deadline = s.deadline
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("owned route consulted DNS")
    )
    client = HTTPSReadClient(s.origin, ca_bundle=s.ca, service_route=s.route, total_timeout=120)
    assert client.get_json("/api") == {"id": 17}
    assert client._deadline == original_deadline


@pytest.mark.parametrize("kind", ["read", "gitlab", "receive", "registry"])
def test_route_revoked_during_tls_never_sends_credentials(routed, kind):
    s = routed
    if kind == "read":
        client = HTTPSReadClient(
            s.origin, ca_bundle=s.ca, token_env="CONTROLLED_ROUTE_TOKEN", service_route=s.route
        )

        def execute():
            return client.get_json("/api")
    elif kind == "gitlab":
        client = GitLabSession(
            s.origin,
            token_env="CONTROLLED_ROUTE_TOKEN",
            trust=s.trust,
            expected_user_id=17,
            service_route=s.route,
        )

        def execute():
            return client.request("GET", "/api/v4/user")
    elif kind == "receive":
        client = GitReceiveClient(
            origin=s.origin,
            repository_path="/owned/repo.git",
            context=ssl.create_default_context(),
            token="controlled-private-token",
            budget=Budget(),
            service_route=s.route,
            ca_sha256=s.sha,
        )

        def execute():
            return client.read_api("/api/v4/user")
    else:
        client = _StagingRegistry(
            s.origin,
            repository="owned/repo",
            deadline=time.monotonic() + 20,
            token_env="CONTROLLED_ROUTE_TOKEN",
            ca_bundle=s.ca,
            service_route=s.route,
        )

        def execute():
            return client._request("HEAD", "/v2/owned/repo/manifests/sha256:" + "1" * 64)

    s.expire_on_sni = True
    with pytest.raises((APIError, GitLabAPIError)):
        execute()
    assert s.calls == [] and s.sni == ["git.factory.invalid"]


@pytest.mark.parametrize("kind", ["gitlab", "receive", "registry"])
def test_read_only_reconciliation_route_cannot_send_mutations(routed, kind):
    s = routed
    s.read_only = True
    if kind == "gitlab":
        client = GitLabSession(
            s.origin,
            token_env="CONTROLLED_ROUTE_TOKEN",
            trust=s.trust,
            expected_user_id=17,
            service_route=s.route,
        )
        assert client.writes_blocked

        def execute():
            return client.request("POST", "/api/v4/projects", payload={"name": "forbidden"})
    elif kind == "receive":
        client = GitReceiveClient(
            origin=s.origin,
            repository_path="/owned/repo.git",
            context=ssl.create_default_context(),
            token="controlled-private-token",
            budget=Budget(),
            service_route=s.route,
            ca_sha256=s.sha,
        )
        client._spent = True
        client.set_username("controlled")

        def execute():
            return client._exchange(
                "POST",
                "/owned/repo.git/git-receive-pack",
                body=b"forbidden",
                cap=100,
                media="application/x-git-receive-pack-result",
                git=True,
            )
    else:
        client = _StagingRegistry(
            s.origin,
            repository="owned/repo",
            deadline=time.monotonic() + 20,
            token_env="CONTROLLED_ROUTE_TOKEN",
            ca_bundle=s.ca,
            service_route=s.route,
        )

        def execute():
            return client._request("PUT", "/v2/owned/repo/manifests/sha256:" + "1" * 64)

    with pytest.raises((APIError, GitLabAPIError), match="read-only"):
        execute()
    assert s.calls == [] and s.sni == []


def test_real_route_issuer_rejects_dictionary_and_unissued_object_before_socket(tmp_path):
    from devsecops.factory.owned_service_links import OwnedServiceRoute

    for route in (
        {"origin": "https://git.factory.invalid", "connect_host": "127.0.0.1", "connect_port": 1},
        object.__new__(OwnedServiceRoute),
    ):
        with pytest.raises(APIError, match="owned-service-route-unavailable"):
            HTTPSReadClient(
                "https://git.factory.invalid", service_route=route, ca_bundle=tmp_path / "unused-ca"
            )
