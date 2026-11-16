import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from test_argocd import ca, server

from devsecops.adapters.http import APIError, HTTPSReadClient
from devsecops.adapters.kubernetes import KubernetesCollector

__all__ = ["ca", "server"]  # Re-export the shared pytest fixtures.
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
DIGEST = "containerd://sha256:" + "a" * 64
DEP = "/apis/apps/v1/namespaces/demo/deployments/mission"
SVC = "/api/v1/namespaces/demo/services/mission"
SETS = "/apis/apps/v1/namespaces/demo/replicasets"
PODS = "/api/v1/namespaces/demo/pods"
SLICES = "/apis/discovery.k8s.io/v1/namespaces/demo/endpointslices"
POLICIES = "/apis/networking.k8s.io/v1/namespaces/demo/networkpolicies"
EVENTS = "/api/v1/namespaces/demo/events"


def resource(kind, name, uid, owner=None, *, api="v1"):
    meta = {"name": name, "namespace": "demo", "uid": uid, "resourceVersion": "17", "generation": 3,
            "labels": {"app": "mission"}, "annotations": {"private": "sensitive-value-must-not-escape"}}
    if owner:
        meta["ownerReferences"] = [{"kind": owner[0], "uid": owner[1], "name": "mission", "controller": True}]
    return {"kind": kind, "apiVersion": api, "metadata": meta}


def listing(kind, items, continuation="", version="100"):
    return {"kind": kind + "List", "metadata": {"resourceVersion": version, "continue": continuation}, "items": items}


def fixture():
    dep = resource("Deployment", "mission", "deployment-1", api="apps/v1")
    dep.update(spec={"replicas": 1, "selector": {"matchLabels": {"app": "mission"}}}, status={
        "observedGeneration": 3, "replicas": 1, "updatedReplicas": 1, "readyReplicas": 1, "availableReplicas": 1,
        "conditions": [{"type": "Available", "status": "True", "message": "sensitive-value-must-not-escape"}]})
    svc = resource("Service", "mission", "service-1")
    svc["spec"] = {"selector": {"app": "mission"}, "ports": [{"name": "health", "port": 8080, "targetPort": 8080},
                                                                  {"name": "metrics", "port": 9090, "targetPort": 9090}]}
    rs = resource("ReplicaSet", "mission-rs", "rs-1", ("Deployment", "deployment-1"), api="apps/v1")
    rs.update(spec={"replicas": 1}, status={"observedGeneration": 3, "readyReplicas": 1, "availableReplicas": 1})
    pod = resource("Pod", "mission-pod", "pod-1", ("ReplicaSet", "rs-1"))
    pod["spec"] = {"containers": [{"name": "mission", "env": [{"name": "SECRET", "value": "sensitive-value-must-not-escape"}]}]}
    pod["status"] = {"phase": "Running", "podIP": "10.0.0.4", "conditions": [{"type": "Ready", "status": "True"}],
                     "containerStatuses": [{"name": "mission", "imageID": DIGEST, "restartCount": 0, "ready": True,
                                            "state": {"running": {"startedAt": NOW.isoformat()}}}]}
    endpoint = resource("EndpointSlice", "mission-abc", "slice-1", ("Service", "service-1"), api="discovery.k8s.io/v1")
    endpoint["metadata"]["labels"] = {"kubernetes.io/service-name": "mission"}
    endpoint.update(addressType="IPv4", ports=[{"name": "metrics", "port": 9090}], endpoints=[{
        "addresses": ["10.0.0.4"], "conditions": {"ready": True},
        "targetRef": {"kind": "Pod", "name": "mission-pod", "namespace": "demo", "uid": "pod-1"}}])
    policy = resource("NetworkPolicy", "mission-metrics", "policy-1", api="networking.k8s.io/v1")
    policy["spec"] = {"podSelector": {"matchLabels": {"app": "mission"}}, "policyTypes": ["Ingress"], "ingress": [
        {"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}}}],
         "ports": [{"port": 9090, "protocol": "TCP"}]}]}
    event = resource("Event", "mission-event", "event-1")
    event.update(involvedObject={"kind": "Deployment", "namespace": "demo", "uid": "deployment-1"},
                 reason="sensitive-value-must-not-escape", message="sensitive-value-must-not-escape", type="Warning", count=2,
                 firstTimestamp="2026-10-25T12:00:00Z", lastTimestamp="2026-10-25T12:05:00Z")
    return {DEP: dep, SVC: svc, SETS: listing("ReplicaSet", [rs]), PODS: listing("Pod", [pod]),
            SLICES: listing("EndpointSlice", [endpoint]), POLICIES: listing("NetworkPolicy", [policy]), EVENTS: listing("Event", [event])}


class Client:
    def __init__(self, data=None):
        self.data = data or fixture()
        self.calls = []

    def get_json(self, path, params=None):
        self.calls.append((path, deepcopy(params)))
        result = self.data[path]
        if isinstance(result, list):
            result = result.pop(0)
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


def collect(client=None, **kwargs):
    return KubernetesCollector(client or Client()).collect("demo", "mission", "mission", now=NOW, **kwargs)


def codes(report):
    return {gap["code"] for gap in report["gaps"]}


def test_healthy_scoped_snapshot_has_owner_image_and_policy_evidence_only():
    client = Client()
    report = collect(client, expected_deployment_uid="deployment-1", expected_generation=3)
    assert report["decision"] == "pass" and report["execution_status"] == "complete"
    assert not report["confirmed_failure"]
    assert report["observations"]["replicasets"][0]["observed_generation"] == 3
    snapshot = report["observations"]["image_snapshot"]
    assert snapshot["containers"][0]["image_id"] == DIGEST
    assert snapshot["containers"][0]["pod_uid"] == "pod-1"
    assert report["observations"]["network_policies"][0]["ingress"][0]["ports"][0]["port"] == 9090
    assert "sensitive-value-must-not-escape" not in json.dumps(report)
    assert report["observations"]["deployment_events"][0]["reason"] == "Other"
    assert report["observations"]["deployment_events"][0]["last_observed_at"] == "2026-10-25T12:05:00+00:00"
    assert len(client.calls) == 9
    assert {path for path, _ in client.calls} == set(fixture())
    assert all("watch" not in (params or {}) for _, params in client.calls)
    assert next(params for path, params in client.calls if path == PODS)["labelSelector"] == "app=mission"
    assert next(params for path, params in client.calls if path == EVENTS)["fieldSelector"] == "involvedObject.uid=deployment-1"


@pytest.mark.parametrize("path,status,code", [(DEP, 404, "missing"), (PODS, 403, "denied"), (POLICIES, 500, "api-unavailable"), (PODS, 410, "expired-snapshot")])
def test_missing_denied_and_expired_evidence_remain_unknown(path, status, code):
    data = fixture()
    data[path] = APIError("sensitive-value-must-not-escape", status)
    report = collect(Client(data))
    assert report["decision"] == "unknown" and report["execution_status"] == "partial"
    assert "kubernetes-" + code in codes(report)
    assert not report["confirmed_failure"]
    assert "sensitive-value-must-not-escape" not in json.dumps(report)


def test_observed_failure_is_retained_when_unrelated_coverage_is_missing():
    data = fixture()
    data[DEP]["status"]["availableReplicas"] = 0
    data[POLICIES] = APIError("http-status", 403)
    report = collect(Client(data))
    assert report["decision"] == "unknown" and report["confirmed_failure"]


def test_complete_current_nonready_is_failure():
    data = fixture()
    data[DEP]["status"]["availableReplicas"] = 0
    report = collect(Client(data))
    assert report["decision"] == "fail" and report["confirmed_failure"]


def test_stale_generation_does_not_become_failure_or_success():
    data = fixture()
    data[DEP]["status"].update(observedGeneration=2, availableReplicas=0)
    report = collect(Client(data))
    assert report["decision"] == "unknown" and not report["confirmed_failure"]
    assert "kubernetes-stale-generation" in codes(report)


@pytest.mark.parametrize("path", [DEP, SVC])
def test_resource_race_is_unknown(path):
    data = fixture()
    changed = deepcopy(data[path])
    changed["metadata"]["resourceVersion"] = "18"
    data[path] = [data[path], changed]
    assert "kubernetes-race" in codes(collect(Client(data)))


@pytest.mark.parametrize("kwargs", [{"expected_deployment_uid": "other"}, {"expected_generation": 4}])
def test_expected_identity_mismatch_is_unknown(kwargs):
    assert collect(**kwargs)["decision"] == "unknown"


@pytest.mark.parametrize("path", [SETS, PODS, SLICES])
def test_foreign_owner_cannot_satisfy_scoped_readiness(path):
    data = fixture()
    data[path]["items"][0]["metadata"]["ownerReferences"][0]["uid"] = "foreign"
    report = collect(Client(data))
    assert report["decision"] == "unknown" and not report["confirmed_failure"]


def test_endpoint_uid_name_disagreement_is_unknown():
    data = fixture()
    data[SLICES]["items"][0]["endpoints"][0]["targetRef"]["name"] = "another-pod"
    assert collect(Client(data))["decision"] == "unknown"


def test_service_selector_and_unsupported_deployment_expressions_are_unknown():
    data = fixture()
    data[SVC]["spec"]["selector"] = {"app": "other"}
    assert "kubernetes-service-selector-mismatch" in codes(collect(Client(data)))
    data = fixture()
    data[DEP]["spec"]["selector"]["matchExpressions"] = [{"key": "app", "operator": "In", "values": ["mission"]}]
    assert "kubernetes-unsupported-selector" in codes(collect(Client(data)))


def test_missing_image_identity_preserves_pod_state_without_success():
    data = fixture()
    data[PODS]["items"][0]["status"]["containerStatuses"][0]["imageID"] = ""
    report = collect(Client(data))
    assert report["decision"] == "unknown"
    assert report["observations"]["pods"][0]["phase"] == "Running"
    assert "kubernetes-missing-image" in codes(report)


def test_bounded_pagination_preserves_list_snapshot_and_duplicate_detection():
    data = fixture()
    one = data[PODS]["items"][0]
    data[PODS] = [listing("Pod", [one], "next"), listing("Pod", [])]
    client = Client(data)
    assert collect(client)["decision"] == "pass"
    assert [params for path, params in client.calls if path == PODS][-1]["continue"] == "next"
    data = fixture()
    data[PODS] = [listing("Pod", [one], "next"), listing("Pod", [], version="101")]
    assert "kubernetes-list-race" in codes(collect(Client(data)))
    data = fixture()
    data[PODS] = listing("Pod", [one, one])
    assert collect(Client(data))["decision"] == "unknown"


def test_page_limit_does_not_fall_back_to_unbounded_read():
    data = fixture()
    data[PODS]["metadata"]["continue"] = "next"
    client = Client(data)
    report = KubernetesCollector(client, max_pages=1).collect("demo", "mission", "mission", now=NOW)
    assert "kubernetes-collection-limit" in codes(report)
    assert len([p for p, _ in client.calls if p == PODS]) == 1


@pytest.mark.parametrize("mutation", [
    lambda data: data[PODS]["items"][0].update(apiVersion="unexpected/v9"),
    lambda data: data[PODS]["items"][0]["status"]["containerStatuses"][0].update(ready=False),
    lambda data: data[PODS]["items"][0]["spec"]["containers"].append({"name": "missing-status"}),
    lambda data: data[PODS]["items"][0]["status"]["conditions"].append({"type": "Ready", "status": "False"}),
    lambda data: data[DEP]["metadata"].pop("generation"),
    lambda data: data[SETS]["items"][0]["status"].update(observedGeneration=2),
    lambda data: data[SLICES]["items"][0]["endpoints"][0].update(addresses=["10.0.0.99"]),
    lambda data: data[SLICES]["items"][0].update(addressType="IPv6"),
    lambda data: data[POLICIES]["items"][0]["spec"].update(unknownPolicyFeature=True),
])
def test_inconsistent_or_unsupported_resource_shapes_cannot_pass(mutation):
    data = fixture()
    mutation(data)
    assert collect(Client(data))["decision"] == "unknown"


def test_controller_failure_condition_is_observed_failure():
    data = fixture()
    data[DEP]["status"]["conditions"].append({"type": "Progressing", "status": "False"})
    assert collect(Client(data))["decision"] == "fail"


def test_collector_over_real_tls_uses_only_bounded_scoped_gets(server):
    data = fixture()
    server["responses"] = [{"json": data[path]} for path in (DEP, SVC, SETS, PODS, SLICES, POLICIES, EVENTS, DEP, SVC)]
    client = HTTPSReadClient(server["origin"], ca_bundle=server["ca"], max_requests=32)
    report = collect(client)
    assert report["decision"] == "pass"
    assert len(client.receipts) == len(server["requests"]) == 9
    assert all("/namespaces/demo/" in request["path"] for request in server["requests"])
    assert all(receipt["status"] == 200 for receipt in client.receipts)


@pytest.mark.parametrize("invalid", ["../other", "demo?watch=true", "demo/secret", "", "demo\n"])
def test_invalid_scope_is_rejected_before_requests(invalid):
    client = Client()
    with pytest.raises(ValueError):
        KubernetesCollector(client).collect(invalid, "mission", "mission")
    assert not client.calls
