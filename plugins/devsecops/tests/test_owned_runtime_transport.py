"""Closed route checks at controlled read boundaries; no cluster qualification."""

from types import SimpleNamespace
from pathlib import Path
import json
import time

import pytest

from devsecops.adapters.http import APIError
from devsecops.factory.owned_runtime_transport import (
    _OwnedReadClient,
    _ReadHTTP,
    require_bound_runtime_read,
    validate_read_transport,
)
from devsecops.factory.lifecycle import PhaseContext
from devsecops.factory.state import _sha
from devsecops.factory.state import StateError
from devsecops.runtime.collection import _Clients


def client(provider="kubernetes"):
    value = object.__new__(_OwnedReadClient)
    value.provider = provider
    value.service = {"scope": {"namespaces": ["mission", "monitoring"]}}
    value.target = {
        "kubernetes": {
            "namespace": "mission",
            "service": "mission",
            "deployment": "mission",
            "deployment_uid": "dep-uid",
        }
    }
    value._deployment_selector = "app=mission"
    return value


@pytest.mark.parametrize(
    "path,params,binary",
    [
        ("/api/v1/namespaces/mission/pods/pod-a/exec", None, False),
        ("/api/v1/namespaces/mission/secrets", None, False),
        ("/api/v1/namespaces/unselected/pods/pod-a", None, False),
        ("/api/v1/namespaces/mission/services/mission/proxy/admin", None, False),
        ("/api/v1/namespaces/mission/pods/pod-a/log", {"container": "admin"}, True),
        ("/api/v1/namespaces/mission/pods", {}, False),
        ("/api/v1/namespaces/mission/pods", {"labelSelector": "app=mission", "limit": 1000}, False),
        ("/api/v1/namespaces/mission/pods/pod-a", {"watch": True}, False),
        (
            "/apis/discovery.k8s.io/v1/namespaces/mission/endpointslices",
            {"labelSelector": "kubernetes.io/service-name=other", "limit": 100},
            False,
        ),
        (
            "/api/v1/namespaces/mission/events",
            {"fieldSelector": "involvedObject.uid=other", "limit": 100},
            False,
        ),
        ("/api/v1/namespaces/mission/../monitoring/pods", None, False),
    ],
)
def test_read_route_cannot_expand_to_other_operations_or_scope(path, params, binary):
    with pytest.raises(APIError):
        client()._kubernetes_route(path, params, binary)


@pytest.mark.parametrize(
    "path,params,binary",
    [
        ("/api/v1/namespaces/mission/pods/pod-a", None, False),
        ("/apis/apps/v1/namespaces/mission/deployments/mission", None, False),
        ("/api/v1/namespaces/mission/pods", {"labelSelector": "app=mission", "limit": 100}, False),
        (
            "/apis/discovery.k8s.io/v1/namespaces/mission/endpointslices",
            {"labelSelector": "kubernetes.io/service-name=mission", "limit": 100},
            False,
        ),
        (
            "/api/v1/namespaces/mission/events",
            {"fieldSelector": "involvedObject.uid=dep-uid", "limit": 100},
            False,
        ),
        ("/apis/networking.k8s.io/v1/namespaces/mission/networkpolicies", {"limit": 100}, False),
        (
            "/api/v1/namespaces/monitoring/pods/probe-a/log",
            {"container": "probe", "timestamps": "false", "limitBytes": 256000},
            True,
        ),
    ],
)
def test_fixed_observation_routes_are_available(path, params, binary):
    assert client()._kubernetes_route(path, params, binary) == path


def test_serialized_transport_binding_never_becomes_a_client():
    with pytest.raises(StateError):
        require_bound_runtime_read({"kind": "original-owned-runtime-read-binding"}, {})
    with pytest.raises(ValueError, match="mixed"):
        _Clients({}, {}, transport={"original": False})


def test_selected_proxy_targets_are_explicit_and_closed():
    selected = {
        "kind": "owned-kubernetes-read-v1",
        "services": {
            name: {"namespace": "monitoring", "name": name, "port": 9090, "scheme": "http"}
            for name in ("prometheus", "argocd")
        },
    }
    assert validate_read_transport(selected) == selected
    selected["services"]["prometheus"]["origin"] = "https://unrelated.example"
    with pytest.raises(StateError):
        validate_read_transport(selected)


@pytest.mark.parametrize(
    "change", [None, "other-metric", "other-target", "larger-window", "unbounded-result", "admin-route"]
)
def test_prometheus_queries_are_exact_target_and_window(change):
    value = client("prometheus")
    scope = {"namespace": "mission", "service": "mission", "job": "mission", "instance": "10.0.0.2:9000"}
    value.target = {"prometheus": scope}
    value.bound = SimpleNamespace(
        frozen={"factory_lock": {"observation_window_seconds": 60, "scrape_interval_seconds": 15}},
        _observed_reads=0,
    )
    value._current = lambda: None
    calls = []
    value._service_route = lambda path: path
    value._http = SimpleNamespace(
        get_bytes=lambda path, params: calls.append((path, params))
        or SimpleNamespace(body=b'{"status":"success"}')
    )
    selector = "{" + ",".join(k + "=" + json.dumps(v) for k, v in scope.items()) + "}"
    params = {"query": "up" + selector, "start": 100, "end": 160, "step": 15, "timeout": "5s", "limit": 2}
    path = "/api/v1/query_range"
    if change == "other-metric":
        params["query"] = "process_env"
    elif change == "other-target":
        params["query"] = 'up{namespace="unselected"}'
    elif change == "larger-window":
        params["start"] = 0
    elif change == "unbounded-result":
        params["limit"] = 0
    elif change == "admin-route":
        path = "/api/v1/admin/tsdb/delete_series"
    if change is None:
        assert value.get_json(path, params) == {"status": "success"}
        assert len(calls) == 1 and value.bound._observed_reads == 1
    else:
        with pytest.raises(APIError):
            value.get_json(path, params)
        assert calls == []


def test_owned_service_identity_read_uses_the_same_shorter_deadline(tmp_path):
    value = client("prometheus")
    value._deadline = time.monotonic() + 5
    captured = []
    selected = {"namespace": "monitoring", "name": "prometheus", "port": 9090, "scheme": "http"}
    observed = {
        "metadata": {"uid": "service-uid"},
        "spec": {"selector": {"app": "prometheus"}, "ports": [{"port": 9090}]},
    }
    calls = []
    resources = SimpleNamespace(
        read_owned=lambda *args: pytest.fail("administrative reader must not be used"),
        audit_owned_read=lambda context, descriptor, value: captured.append(context.deadline) or value,
    )
    value._http = SimpleNamespace(get_json=lambda path: calls.append(path) or observed)
    value._current = lambda: None
    bootstrap = SimpleNamespace(kubernetes=lambda context: captured.append(context.deadline) or resources)
    phase = PhaseContext("run", {}, {}, {}, {}, tmp_path, time.monotonic() + 300, "controlled-test")
    value.bound = SimpleNamespace(
        phase=phase,
        owner=SimpleNamespace(bootstrap=bootstrap, selection={"services": {"prometheus": selected}}),
    )
    value._route_receipts = []
    assert value._service_route("/api/v1/query_range").endswith("/proxy/api/v1/query_range")
    assert captured == [value._deadline, value._deadline]
    assert calls == ["/api/v1/namespaces/monitoring/services/prometheus"]
    assert phase.deadline > value._deadline  # The original phase is not extended or mutated.


def test_tls_uses_exact_pinned_ca_snapshot_not_a_later_path_read(tmp_path, monkeypatch):
    from devsecops.factory import owned_runtime_transport as module

    path = tmp_path / "ca.pem"
    original = b"controlled-original-ca"
    path.write_bytes(original)
    calls = []

    def context(**kwargs):
        calls.append(kwargs)
        path.write_bytes(b"controlled-raced-ca")
        return SimpleNamespace(minimum_version=None)

    monkeypatch.setattr(module.ssl, "create_default_context", context)
    bound = SimpleNamespace()
    value = _ReadHTTP(
        bound, {"origin": "https://cluster.test", "ca_path": str(path), "ca_sha256": _sha(original)}, 10
    )
    assert value._bound is bound
    assert calls[-1] == {"cadata": original.decode()}
    assert Path(path).read_bytes() != original


def test_service_reader_failure_never_falls_back_to_admin(tmp_path):
    value = client("prometheus")
    value._deadline = time.monotonic() + 5
    phase = PhaseContext("run", {}, {}, {}, {}, tmp_path, time.monotonic() + 300, "controlled-test")
    selected = {"namespace": "monitoring", "name": "prometheus", "port": 9090, "scheme": "http"}
    resources = SimpleNamespace(
        read_owned=lambda *args: pytest.fail("admin fallback"),
        audit_owned_read=lambda *args: pytest.fail("failed reads cannot be audited"),
    )
    value.bound = SimpleNamespace(
        phase=phase,
        owner=SimpleNamespace(
            bootstrap=SimpleNamespace(kubernetes=lambda context: resources),
            selection={"services": {"prometheus": selected}},
        ),
    )
    value._route_receipts = []

    def denied(path):
        raise APIError("forbidden")

    value._http = SimpleNamespace(get_json=denied)
    with pytest.raises(APIError, match="forbidden"):
        value._service_route("/api/v1/query_range")
    assert value._route_receipts == []
