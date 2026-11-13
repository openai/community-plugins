"""Real local HTTP with explicit Python-only test transport; no cluster evidence."""
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

from jsonschema import Draft202012Validator, FormatChecker
import pytest

ROOT = Path(__file__).parents[1]
APP_ROOT = ROOT / "demo/software-factory/apps/mission-status"
SPEC = importlib.util.spec_from_file_location("mission_http_probe", APP_ROOT / "probe.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)
ADDRESS = "10.96.0.7"
BODY = b'{"status":"ok","service":"mission-status","data":"synthetic"}\n'


def request():
    return {"schema_version": 1, "kind": "http-probe-request", "run_id": "run-one", "attempt_id": "attempt-one",
        "phase": "baseline", "case_id": "mission-health", "identities": {key: "a" * 64 for key in PROBE.IDENTITY_KEYS},
        "source": {"expected": {"namespace": "probe-space", "name": "probe-pod", "uid": "probe-uid"},
            "environment": {"namespace": "DEVSECOPS_PROBE_POD_NAMESPACE", "name": "DEVSECOPS_PROBE_POD_NAME", "uid": "DEVSECOPS_PROBE_POD_UID"}},
        "destination": {"namespace": "mission-demo", "service": "mission-status", "cluster_domain": "cluster.local",
            "port": 8080, "path": "/health", "scheme": "http", "expected_addresses": [ADDRESS]},
        "expectation": {"status": 200, "body_sha256": hashlib.sha256(BODY).hexdigest()},
        "limits": {"dns_seconds": .5, "connect_seconds": .5, "header_seconds": .5, "body_seconds": .5,
            "total_seconds": 2, "body_bytes": 1024}}


def environment(value):
    return {env: value["source"]["expected"][key] for key, env in value["source"]["environment"].items()}


@pytest.fixture
def server():
    state = {"requests": [], "response": {}}
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *_):
            pass
        def do_GET(self):
            state["requests"].append({"path": self.path, "headers": dict(self.headers)})
            settings = state["response"]
            try:
                if settings.get("header_delay"):
                    time.sleep(settings["header_delay"])
                if settings.get("raw") is not None:
                    self.close_connection = True
                    raw = settings["raw"]
                    if not settings.get("header_trickle"):
                        self.wfile.write(raw)
                        self.wfile.flush()
                        return
                    for byte in raw:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(settings.get("header_trickle", 0))
                    return
                self.send_response(settings.get("status", 200))
                for key, value in settings.get("headers", []):
                    self.send_header(key, value)
                body = settings.get("body", BODY)
                if not settings.get("no_length"):
                    self.send_header("Content-Length", str(settings.get("length", len(body))))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.flush()
                if settings.get("body_delay"):
                    time.sleep(settings["body_delay"])
                if settings.get("body_trickle"):
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(settings["body_trickle"])
                else:
                    self.wfile.write(body)
                    self.wfile.flush()
            except OSError:
                pass
    class QuietServer(ThreadingHTTPServer):
        daemon_threads = True
        def handle_error(self, *_):
            pass
    httpd = QuietServer(("127.0.0.1", 0), Handler)
    state["port"] = httpd.server_port
    worker = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=.005), daemon=True)
    worker.start()
    yield state
    httpd.shutdown()
    httpd.server_close()
    worker.join(timeout=1)


class TestSocket:
    __test__ = False
    def __init__(self, raw, peer=ADDRESS):
        self.raw, self.peer = raw, peer
    def getpeername(self):
        return self.peer, self.raw.getpeername()[1]
    def __getattr__(self, name):
        return getattr(self.raw, name)


def collect(server, value=None, *, resolver=None, connector=None, env=None):
    value = deepcopy(value or request())
    value["destination"]["port"] = server["port"]
    def connect(address, port, timeout):
        assert address == ADDRESS and port == server["port"]
        return TestSocket(socket.create_connection(("127.0.0.1", port), timeout))
    result = PROBE.probe(value, _resolver=resolver or (lambda host, port: [ADDRESS]),
                         _connector=connector or connect, _environ=environment(value) if env is None else env)
    Draft202012Validator(PROBE.RECEIPT_SCHEMA, format_checker=FormatChecker()).validate(result)
    assert result["receipt_sha256"] == PROBE._sha({k: v for k, v in result.items() if k != "receipt_sha256"})
    return result


def test_real_http_observation_has_exact_scope_and_unverified_identity_claims(server):
    result = collect(server)
    obs = result["observation"]
    assert obs["http_decision"] == "pass" and obs["outcome"] == "http-response"
    assert obs["body_sha256"] == hashlib.sha256(BODY).hexdigest() and obs["body_bytes"] == len(BODY)
    assert result["execution_status"] == "complete" and result["decision"] == "unknown"
    assert result["policy_verdict"] == "unknown" and result["recovery"] == "not-evaluated"
    assert result["execution_mode"] == "controlled-test"
    assert result["source_identity"]["matched"] and result["source_identity"]["authentication"] == "not-verified"
    assert result["network"] == {"host": "mission-status.mission-demo.svc.cluster.local", "dns_addresses": [ADDRESS], "connected_peer": ADDRESS}
    assert len(server["requests"]) == 1 and server["requests"][0]["path"] == "/health"
    assert server["requests"][0]["headers"]["Host"] == f"mission-status.mission-demo.svc.cluster.local:{server['port']}"
    assert "Authorization" not in server["requests"][0]["headers"]
    assert "Cookie" not in server["requests"][0]["headers"]
    assert BODY.decode() not in json.dumps(result)


def test_factory_uuid_run_identity_is_preserved(server):
    value = request()
    value.update(run_id="12345678-1234-1234-1234-123456789abc", attempt_id="primary")
    result = collect(server, value)
    assert result["observation"]["http_decision"] == "pass"
    assert result["request"]["run_id"] == value["run_id"]


@pytest.mark.parametrize("path", ["/", "/health", "/healthz", "/metrics"])
def test_only_declared_approved_path_is_sent(server, path):
    value = request()
    value["destination"]["path"] = path
    assert collect(server, value)["observation"]["http_decision"] == "pass"
    assert server["requests"][0]["path"] == path


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_never_followed_and_response_headers_not_exported(server, status):
    server["response"] = {"status": status, "headers": [("Location", "http://elsewhere.invalid/secret-token")]}
    result = collect(server)
    assert result["observation"]["outcome"] == "redirect-rejected"
    assert result["observation"]["http_decision"] == "fail"
    assert len(server["requests"]) == 1
    assert "secret-token" not in json.dumps(result)


def test_proxy_environment_is_ignored(server, monkeypatch):
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(key, "http://bad-user:secret-token@127.0.0.1:1")
    result = collect(server)
    assert result["observation"]["http_decision"] == "pass" and len(server["requests"]) == 1
    assert "secret-token" not in json.dumps(result)


@pytest.mark.parametrize("status", [400, 403, 404, 500])
def test_http_status_failure_is_narrow_observation(server, status):
    server["response"] = {"status": status, "body": b"do not echo errors or credentials"}
    result = collect(server)
    assert result["observation"]["outcome"] == "unexpected-status"
    assert result["observation"]["http_decision"] == "fail" and result["decision"] == "unknown"
    assert result["execution_status"] == "complete"
    assert "do not echo" not in json.dumps(result)


def test_body_mismatch_and_optional_body_expectation(server):
    server["response"] = {"body": b"different response"}
    result = collect(server)
    assert result["observation"]["outcome"] == "body-mismatch" and result["observation"]["body_sha256_matched"] is False
    value = request()
    value["expectation"]["body_sha256"] = None
    result = collect(server, value)
    assert result["observation"]["http_decision"] == "pass" and result["observation"]["body_sha256_matched"] is None


@pytest.mark.parametrize("env,outcome", [({}, "source-identity-missing"),
    ({"DEVSECOPS_PROBE_POD_NAMESPACE": "probe-space", "DEVSECOPS_PROBE_POD_NAME": "probe-pod", "DEVSECOPS_PROBE_POD_UID": "secret-token"}, "source-identity-mismatch")])
def test_missing_or_wrong_source_identity_stops_before_dns(server, env, outcome):
    calls = []
    result = collect(server, env=env, resolver=lambda *args: calls.append(args))
    assert result["observation"]["outcome"] == outcome
    assert not calls and not server["requests"] and result["source_identity"]["claims"] is None
    assert "secret-token" not in json.dumps(result)


@pytest.mark.parametrize("addresses", [["10.96.0.8"], [ADDRESS, "10.96.0.8"], [], ["127.0.0.1"], ["::1"],
    ["169.254.169.254"], ["fe80::1"], ["224.0.0.1"], ["::"], ["::ffff:127.0.0.1"], ["bad-ip"]])
def test_dns_result_must_exactly_match_safe_expected_addresses(server, addresses):
    calls = []
    result = collect(server, resolver=lambda *args: addresses, connector=lambda *args: calls.append(args))
    assert result["observation"]["outcome"] == "dns-address-mismatch"
    assert not calls and not server["requests"]
    assert result["network"]["dns_addresses"] == []


@pytest.mark.parametrize("peer", ["10.96.0.8", "127.0.0.1", "169.254.169.254"])
def test_connected_peer_mismatch_stops_before_http(server, peer):
    def connector(address, port, timeout):
        return TestSocket(socket.create_connection(("127.0.0.1", port), timeout), peer)
    result = collect(server, connector=connector)
    assert result["observation"]["outcome"] == "peer-address-mismatch"
    assert not server["requests"] and result["network"]["connected_peer"] is None


def test_dual_stack_requires_both_declared_addresses_without_failover(server):
    value = request()
    value["destination"]["expected_addresses"] = [ADDRESS, "fd00::7"]
    assert collect(server, value, resolver=lambda *args: ["fd00::7", ADDRESS])["observation"]["http_decision"] == "pass"
    assert collect(server, value)["observation"]["outcome"] == "dns-address-mismatch"
    assert len(server["requests"]) == 1


def test_default_dns_uses_exact_absolute_service_name_and_stream_records(monkeypatch):
    seen = []
    def resolve(*args):
        seen.append(args)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ADDRESS, 8080))]
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    assert PROBE._resolve("mission.demo.svc.cluster.local", 8080) == [ADDRESS]
    assert seen == [("mission.demo.svc.cluster.local.", 8080, socket.AF_UNSPEC, socket.SOCK_STREAM, socket.IPPROTO_TCP)]


@pytest.mark.parametrize("stage", ["dns", "header", "body", "total"])
def test_stage_and_total_deadlines_are_actual_wall_bounds(server, stage):
    value = request()
    value["limits"].update(dns_seconds=.1, connect_seconds=.1, header_seconds=.1, body_seconds=.1, total_seconds=.4)
    resolver = None
    if stage == "dns":
        def resolver(*args):
            time.sleep(.3)
            return [ADDRESS]
    elif stage == "header":
        server["response"] = {"header_delay": .3}
    elif stage == "body":
        server["response"] = {"body_delay": .3}
    else:
        value["limits"].update(total_seconds=.1)
        server["response"] = {"body_delay": .3}
    start = time.monotonic()
    result = collect(server, value, resolver=resolver)
    assert time.monotonic() - start < .3
    assert result["observation"]["outcome"] == stage + "-timeout"
    assert result["observation"]["http_decision"] == "unknown"
    if stage == "dns":
        time.sleep(.25)
        assert not server["requests"]


@pytest.mark.parametrize("stage", ["header", "body"])
def test_trickle_cannot_renew_phase_deadline(server, stage):
    value = request()
    value["limits"].update(header_seconds=.1, body_seconds=.1)
    server["response"] = ({"raw": b"HTTP/1.1 200 OK\r\nX-Trickle: never-finished\r\n", "header_trickle": .01}
                          if stage == "header" else {"body_trickle": .01})
    start = time.monotonic()
    result = collect(server, value)
    assert time.monotonic() - start < .3
    assert result["observation"]["outcome"] == stage + "-timeout"


def test_late_connector_result_is_closed_and_never_sends_http(server):
    value = request()
    value["limits"]["connect_seconds"] = .05
    sockets = []
    def late(address, port, timeout):
        time.sleep(.15)
        raw = socket.create_connection(("127.0.0.1", port), .5)
        sockets.append(raw)
        return TestSocket(raw)
    result = collect(server, value, connector=late)
    assert result["observation"]["outcome"] == "connect-timeout"
    time.sleep(.2)
    assert sockets and sockets[0].fileno() == -1 and not server["requests"]


def test_resolver_failure_is_sanitized_and_does_not_connect(server):
    def resolver(*args):
        raise socket.gaierror("secret-token")
    result = collect(server, resolver=resolver)
    assert result["observation"]["outcome"] == "dns-failed"
    assert not server["requests"] and "secret-token" not in json.dumps(result)


def test_caller_request_mutation_cannot_change_frozen_scope(server):
    value = request()
    value["destination"]["port"] = server["port"]
    def resolver(*args):
        value["destination"]["path"] = "/admin"
        return [ADDRESS]
    result = PROBE.probe(value, _resolver=resolver,
        _connector=lambda address, port, timeout: TestSocket(socket.create_connection(("127.0.0.1", port), timeout)),
        _environ=environment(value))
    assert result["observation"]["http_decision"] == "pass"
    assert result["request"]["destination"]["path"] == "/health"
    assert server["requests"][0]["path"] == "/health"


def test_interrupted_connector_is_closed_after_completion_without_http(server, monkeypatch):
    original_get = PROBE.queue.Queue.get
    sockets, entered = [], threading.Event()
    def get(self, *args, **kwargs):
        if kwargs.get("timeout") is not None:
            assert entered.wait(.2)
            raise KeyboardInterrupt
        return original_get(self, *args, **kwargs)
    # Exercise the worker boundary directly so DNS does not consume the interruption.
    def connect(_remaining):
        entered.set()
        time.sleep(.05)
        raw = socket.create_connection(("127.0.0.1", server["port"]), .5)
        sockets.append(raw)
        return TestSocket(raw)
    monkeypatch.setattr(PROBE.queue.Queue, "get", get)
    with pytest.raises(KeyboardInterrupt):
        PROBE._bounded_call(connect, .5, time.monotonic() + 1, "connect-timeout", late_close=True)
    time.sleep(.1)
    assert sockets and sockets[0].fileno() == -1 and not server["requests"]


@pytest.mark.parametrize("error,outcome", [(ConnectionRefusedError("secret-token"), "connection-refused"),
    (OSError("secret-token"), "connect-failed"), (TimeoutError("secret-token"), "connect-timeout")])
def test_connect_failures_do_not_assert_policy_denial(server, error, outcome):
    def connect(*args):
        raise error
    result = collect(server, connector=connect)
    assert result["observation"]["outcome"] == outcome and result["policy_verdict"] == "unknown"
    assert result["observation"]["http_decision"] == "unknown" and "secret-token" not in json.dumps(result)


@pytest.mark.parametrize("settings,outcome", [
    ({"length": 2000}, "response-too-large"),
    ({"body": b"x" * 2000, "no_length": True}, "response-too-large"),
    ({"length": 100, "body": b"short"}, "invalid-http-response"),
    ({"headers": [("Content-Length", "60")]}, "unsupported-http-framing"),
    ({"headers": [("Transfer-Encoding", "chunked")]}, "unsupported-http-framing"),
    ({"headers": [("Content-Encoding", "gzip")]}, "unsupported-content-encoding"),
    ({"raw": b"HTTP/1.1 200 OK\r\nX-Big: " + b"x" * 17000 + b"\r\n\r\n"}, "headers-too-large"),
    ({"raw": b"HTTP/1.1 999 Unsupported\r\nContent-Length: 0\r\n\r\n"}, "invalid-http-response"),
])
def test_framing_headers_and_body_are_bounded(server, settings, outcome):
    server["response"] = settings
    result = collect(server)
    assert result["observation"]["outcome"] == outcome
    assert result["execution_status"] == "partial" and result["observation"]["body_sha256"] is None


@pytest.mark.parametrize("raw", [
    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nBad Header\r\nTransfer-Encoding: chunked\r\n\r\nok",
    b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 0",
    b"HTTP/1.1 200 OK\r\nContent-Length : 0\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n Transfer-Encoding: chunked\r\n\r\nok",
    b"HTTP/1.1 200 OK\nContent-Length: 0\n\n",
    b"HTTP/1.1 200 OK\r\nX-Invalid: secret\x00value\r\nContent-Length: 0\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nX-Invalid\x7f: secret\r\nContent-Length: 0\r\n\r\n",
    b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 0\r\n",
])
def test_malformed_or_unterminated_header_block_never_passes(server, raw):
    server["response"] = {"raw": raw}
    value = request()
    value["expectation"]["body_sha256"] = None
    result = collect(server, value)
    assert result["observation"]["outcome"] == "invalid-http-response"
    assert result["observation"]["http_decision"] == "unknown"
    assert result["execution_status"] == "partial"
    assert result["observation"]["body_sha256"] is None
    assert "secret" not in json.dumps(result)


def test_complete_valid_header_after_continue_is_supported(server):
    server["response"] = {"raw": b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"}
    value = request()
    value["expectation"]["body_sha256"] = None
    assert collect(server, value)["observation"]["http_decision"] == "pass"


def test_wrong_status_fact_survives_incomplete_body_after_valid_headers(server):
    server["response"] = {"raw": b"HTTP/1.1 503 Unavailable\r\nContent-Length: 10\r\n\r\nshort"}
    result = collect(server)
    assert result["observation"]["status"] == 503
    assert result["observation"]["outcome"] == "invalid-http-response"
    assert result["observation"]["http_decision"] == "fail"
    assert result["execution_status"] == "partial" and result["decision"] == "unknown"


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "169.254.169.254", "fe80::1", "224.0.0.1", "ff02::1", "0.0.0.0", "::", "255.255.255.255", "::ffff:10.96.0.7", "fd00:0:0:0:0:0:0:7"])
def test_unsafe_or_noncanonical_declared_addresses_rejected_before_io(address):
    value = request()
    value["destination"]["expected_addresses"] = [address]
    result = PROBE.probe(value, _resolver=lambda *a: pytest.fail("No DNS on invalid request"))
    assert result["observation"]["outcome"] == "input-invalid"


@pytest.mark.parametrize("damage", ["query", "other-path", "credentials", "extra", "boolean-port", "huge-body", "nan-timeout", "beyond-total", "empty-addresses", "same-family", "empty-domain", "duplicate-env"])
def test_invalid_scopes_or_budgets_fail_closed(damage):
    value = request()
    destination = value["destination"]
    if damage == "query":
        destination["path"] = "/health?secret-token=value"
    elif damage == "other-path":
        destination["path"] = "/admin"
    elif damage == "credentials":
        destination["cluster_domain"] = "user:secret-token@cluster.local"
    elif damage == "extra":
        value["authorization"] = "secret-token"
    elif damage == "boolean-port":
        destination["port"] = True
    elif damage == "huge-body":
        value["limits"]["body_bytes"] = 65537
    elif damage == "nan-timeout":
        value["limits"]["body_seconds"] = float("nan")
    elif damage == "beyond-total":
        value["limits"]["total_seconds"] = .1
    elif damage == "empty-addresses":
        destination["expected_addresses"] = []
    elif damage == "same-family":
        destination["expected_addresses"] = [ADDRESS, "10.96.0.8"]
    elif damage == "empty-domain":
        destination["cluster_domain"] = "cluster..local"
    else:
        value["source"]["environment"]["uid"] = value["source"]["environment"]["name"]
    result = PROBE.probe(value, _resolver=lambda *a: pytest.fail("No DNS on invalid request"))
    assert result["observation"]["outcome"] == "input-invalid"
    assert result["request"] is None and "secret-token" not in json.dumps(result)


def test_explicit_https_is_unsupported_without_downgrade_or_network():
    value = request()
    value["destination"]["scheme"] = "https"
    result = PROBE.probe(value, _environ=environment(value), _resolver=lambda *args: pytest.fail("No TLS fallback"))
    assert result["observation"]["outcome"] == "unsupported-scheme" and result["observation"]["http_decision"] == "unknown"


def test_credential_environment_references_cannot_be_selected():
    value = request()
    value["source"]["environment"]["uid"] = "AWS_SECRET_ACCESS_KEY"
    result = PROBE.probe(value, _environ={"AWS_SECRET_ACCESS_KEY": "secret-token"})
    assert result["observation"]["outcome"] == "input-invalid" and "secret-token" not in json.dumps(result)


def test_actual_mission_app_health_and_metrics_with_unchanged_listeners():
    from test_mission_app import APP
    value = request()
    with APP.MissionService(health_port=0, metrics_port=0, event_interval=.01) as service:
        assert collect({"port": service.health_address[1]}, value)["observation"]["http_decision"] == "pass"
        value["destination"]["path"] = "/metrics"
        value["expectation"]["body_sha256"] = None
        result = collect({"port": service.metrics_address[1]}, value)
        assert result["observation"]["http_decision"] == "pass"
        assert result["observation"]["body_bytes"] > 0
        value["destination"]["path"] = "/healthz"
        result = collect({"port": service.health_address[1]}, value)
        assert result["observation"]["status"] == 404
        assert result["observation"]["http_decision"] == "fail"


def test_cli_has_no_test_bypass_and_redacts_argument_errors(tmp_path):
    result = subprocess.run([sys.executable, str(APP_ROOT / "probe.py"), "--allow-loopback", "secret-token"], capture_output=True, text=True, timeout=3)
    assert result.returncode == 2 and "secret-token" not in result.stderr
    path = tmp_path / "request.json"
    value = request()
    value["destination"]["scheme"] = "https"
    raw = json.dumps(value, indent=2).encode()
    path.write_bytes(raw)
    result = subprocess.run([sys.executable, str(APP_ROOT / "probe.py"), "--request", str(path)],
                            env={**os.environ, **environment(value)}, capture_output=True, text=True, timeout=3)
    assert result.returncode == 2 and not result.stderr
    receipt = json.loads(result.stdout)
    Draft202012Validator(PROBE.RECEIPT_SCHEMA).validate(receipt)
    assert receipt["execution_mode"] == "operator" and receipt["observation"]["outcome"] == "unsupported-scheme"
    assert receipt["request_artifact"]["sha256"] == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("kind", ["symlink", "oversize", "duplicate", "non-object"])
def test_bounded_safe_file_input(tmp_path, kind):
    path = tmp_path / "request.json"
    if kind == "symlink":
        original = tmp_path / "original.json"
        original.write_bytes(PROBE._canonical(request()))
        path.symlink_to(original)
    elif kind == "oversize":
        path.write_bytes(b" " * 65537)
    elif kind == "duplicate":
        path.write_text('{"schema_version":1,"schema_version":1}')
    else:
        path.write_text('[]')
    assert PROBE.probe(path)["observation"]["outcome"] == "input-invalid"


def test_exported_schemas_and_container_copy_match():
    for name, schema in (("request", PROBE.REQUEST_SCHEMA), ("receipt", PROBE.RECEIPT_SCHEMA)):
        assert json.loads((ROOT / f"schemas/http-probe-{name}.schema.json").read_text()) == schema
        Draft202012Validator.check_schema(schema)
    Draft202012Validator(PROBE.REQUEST_SCHEMA).validate(request())
    assert "COPY --chown=10001:10001 probe.py ./probe.py" in (APP_ROOT / "Dockerfile").read_text()
