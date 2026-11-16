"""Execute the actual probe script under controlled network and clock boundaries."""

import contextlib
import io
import json
import sys
from types import SimpleNamespace

import pytest
from test_portable_stack import module


def execute(monkeypatch, role, phase, events=None, address="10.43.1.2"):
    script = module("stack").network_probe_script(role, phase, "10.43.1.2")
    import queue

    assert queue.Queue
    clock = [0.0]
    pending = {port: list(values) for port, values in (events or {}).items()}
    fake_time = SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    class Socket:
        def __init__(self, status):
            self.status = status

        def settimeout(self, value):
            assert 0 < value <= 1

        def close(self):
            pass

    def connect(target, timeout):
        clock[0] += 0.001
        values = pending.get(target[1], [])
        default = "refused" if role == "denied" or (phase == "fault" and target[1] == 9090) else 200
        value = values.pop(0) if values else default
        if value == "refused":
            raise ConnectionRefusedError()
        if value == "timeout":
            clock[0] += timeout
            raise TimeoutError()
        return Socket(value)

    class HTTP:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, *args):
            pass

        def getresponse(self):
            return SimpleNamespace(status=self.sock.status)

        def close(self):
            pass

    class Thread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    client = SimpleNamespace(HTTPConnection=HTTP, HTTPException=Exception)
    fake_socket = SimpleNamespace(
        AF_INET=2,
        SOCK_STREAM=1,
        getaddrinfo=lambda *a: [(2, 1, 6, "", (address, 0))],
        create_connection=connect,
    )
    monkeypatch.setitem(sys.modules, "time", fake_time)
    monkeypatch.setitem(sys.modules, "socket", fake_socket)
    monkeypatch.setitem(sys.modules, "http", SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, "http.client", client)
    monkeypatch.setitem(sys.modules, "threading", SimpleNamespace(Thread=Thread))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        try:
            exec(compile(script, "controlled-probe", "exec"), {})
        except SystemExit:
            pass
    return json.loads(output.getvalue())


@pytest.mark.parametrize("phase", ["baseline", "fault", "recovered"])
def test_actual_script_retains_startup_failures_then_three_correct_samples(monkeypatch, phase):
    result = execute(monkeypatch, "allowed", phase, {8080: ["refused", "refused", 200]})
    assert [x["health"]["category"] for x in result["attempts"]] == [
        "connection-refused",
        "connection-refused",
        "http",
    ]
    assert result["expected_behavior_observed"] and len(result["final_observations"]) == 3
    assert (
        result["final_observations"][-1]["elapsed_seconds"]
        - result["final_observations"][0]["elapsed_seconds"]
        >= 2
    )
    assert result["health"] == {"category": "http", "status": 200}


def test_actual_script_denied_source_settles_before_all_three_denials(monkeypatch):
    result = execute(monkeypatch, "denied", "baseline")
    assert result["startup_elapsed_seconds"] >= 15 and result["elapsed_seconds"] <= 25
    assert result["expected_behavior_observed"] and len(result["final_observations"]) == 3


def test_startup_http500_is_fatal_without_success_retry(monkeypatch):
    result = execute(monkeypatch, "allowed", "baseline", {8080: [500, 200]})
    assert not result["expected_behavior_observed"] and len(result["attempts"]) == 1
    assert result["final_observations"] == []


def test_one_bad_final_sample_cannot_be_replaced_by_later_success(monkeypatch):
    result = execute(monkeypatch, "allowed", "baseline", {9090: [200, 500, 200]})
    assert not result["expected_behavior_observed"] and len(result["final_observations"]) == 2


def test_reachable_denied_source_is_a_failure(monkeypatch):
    result = execute(monkeypatch, "denied", "baseline", {8080: [200]})
    assert not result["expected_behavior_observed"] and len(result["final_observations"]) == 1


def test_dns_change_cannot_be_treated_as_policy_denial(monkeypatch):
    result = execute(monkeypatch, "denied", "baseline", address="10.43.1.3")
    assert not result["expected_behavior_observed"] and result["dns"] == "failed-or-changed"


def test_startup_has_a_fixed15second_limit(monkeypatch):
    result = execute(monkeypatch, "allowed", "baseline", {8080: ["refused"] * 100})
    assert not result["expected_behavior_observed"] and result["elapsed_seconds"] <= 15.001
    assert result["final_observations"] == []
