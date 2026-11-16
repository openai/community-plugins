"""Real loopback HTTP behavior of the synthetic mission application."""
from __future__ import annotations

import importlib.util
import json
import math
import os
import selectors
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

import pytest
from prometheus_client.parser import text_string_to_metric_families


APP_ROOT = Path(__file__).resolve().parents[1] / "demo/software-factory/apps/mission-status"
SPEC = importlib.util.spec_from_file_location("mission_status_demo", APP_ROOT / "app.py")
APP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(APP)


def request(address, path, method="GET"):
    opener = build_opener(ProxyHandler({}))
    response = opener.open(Request(f"http://{address[0]}:{address[1]}{path}", method=method), timeout=2)
    with response:
        return response.status, dict(response.headers), response.read()


def samples(address):
    _, _, body = request(address, "/metrics")
    return {sample.name: sample.value for family in text_string_to_metric_families(body.decode())
            for sample in family.samples}


@pytest.fixture
def service():
    with APP.MissionService(health_port=0, metrics_port=0, event_interval=0.02) as running:
        yield running


def test_health_and_metrics_have_independent_listeners(service):
    assert service.health_address[1] != service.metrics_address[1]
    status, headers, body = request(service.health_address, "/health")
    assert status == 200
    assert json.loads(body) == {"status": "ok", "service": "mission-status", "data": "synthetic"}
    assert headers["Cache-Control"] == "no-store"
    assert headers["Content-Type"] == "application/json"
    _, metrics_headers, metrics = request(service.metrics_address, "/metrics")
    assert metrics_headers["Content-Type"].startswith("text/plain")
    assert b"mission_events_total" in metrics
    for address, path in [(service.health_address, "/metrics"), (service.metrics_address, "/health")]:
        with pytest.raises(HTTPError) as result:
            request(address, path)
        assert result.value.code == 404


def test_events_advance_without_health_requests_or_continuous_scraping(service):
    before = samples(service.metrics_address)
    # The producer is independent of requests. Allow multiple scheduled ticks.
    time.sleep(0.12)
    after = samples(service.metrics_address)
    assert after["mission_events_total"] > before["mission_events_total"]
    assert after["mission_last_event_timestamp_seconds"] > before["mission_last_event_timestamp_seconds"]
    assert 0 <= time.time() - after["mission_last_event_timestamp_seconds"] < 1
    assert not any(name.startswith(("process_", "python_")) for name in after)


def test_head_unknown_paths_and_methods(service):
    _, headers, body = request(service.health_address, "/health", "HEAD")
    assert not body and int(headers["Content-Length"]) > 0
    for address, path, method, expected in [
        (service.health_address, "/health?secret=do-not-reflect", "GET", 404),
        (service.metrics_address, "/metrics", "POST", 405),
        (service.health_address, "/health", "PUT", 405),
    ]:
        with pytest.raises(HTTPError) as result:
            request(address, path, method)
        assert result.value.code == expected
        assert b"do-not-reflect" not in result.value.read()


def test_service_instances_do_not_share_metric_registries():
    with APP.MissionService(health_port=0, metrics_port=0, event_interval=10) as first:
        first.events.inc(1000)
        with APP.MissionService(health_port=0, metrics_port=0, event_interval=10) as second:
            assert samples(first.metrics_address)["mission_events_total"] >= 1000
            assert samples(second.metrics_address)["mission_events_total"] < 1000


@pytest.mark.parametrize("kwargs", [
    {"health_port": 8080, "metrics_port": 8080}, {"health_port": -1}, {"metrics_port": 65536},
    {"event_interval": 0}, {"event_interval": -1}, {"event_interval": math.nan},
    {"event_interval": math.inf}, {"event_interval": 3601}, {"host": "example.invalid"},
])
def test_invalid_runtime_configuration_fails_before_bind(kwargs):
    with pytest.raises(ValueError):
        APP.MissionService(**kwargs)


def test_failed_second_bind_releases_first_listener():
    with socket.socket() as occupied, socket.socket() as available:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        available.bind(("127.0.0.1", 0))
        free_port = available.getsockname()[1]
        available.close()
        service = APP.MissionService(health_port=free_port, metrics_port=occupied.getsockname()[1])
        with pytest.raises(OSError):
            service.start()
        with socket.socket() as check:
            check.bind(("127.0.0.1", free_port))
        assert not service.running


def test_stop_closes_both_listeners_and_is_idempotent():
    service = APP.MissionService(health_port=0, metrics_port=0).start()
    addresses = [service.health_address, service.metrics_address]
    service.stop()
    service.stop()
    assert not service.running
    for address in addresses:
        with pytest.raises(OSError):
            with socket.create_connection(address, timeout=0.5):
                pass
    with pytest.raises(RuntimeError):
        service.start()


def test_cli_sigterm_exits_cleanly_after_serving_real_http():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    child = subprocess.Popen(
        [sys.executable, str(APP_ROOT / "app.py"), "--health-port", "0", "--metrics-port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=5), "application did not announce its listeners"
        announced = json.loads(child.stdout.readline())
        address = ("127.0.0.1", announced["health_port"])
        assert request(address, "/health")[0] == 200
        child.terminate()
        _, error = child.communicate(timeout=5)
        assert child.returncode == 0 and not error
        with pytest.raises(OSError):
            with socket.create_connection(address, timeout=0.5):
                pass
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
