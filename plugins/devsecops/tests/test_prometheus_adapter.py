import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from test_argocd import ca, server

from devsecops.adapters.http import APIError, HTTPSReadClient
from devsecops.adapters.prometheus import PrometheusCollector

__all__ = ["ca", "server"]  # Re-export the shared pytest fixtures.
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
END = int(NOW.timestamp())
SCOPE = {"namespace": "demo", "service": "mission", "job": "mission", "instance": "10.0.0.4:9090"}
DIGEST = "containerd://sha256:" + "a" * 64


def snapshot(when):
    return {"namespace": "demo", "service": "mission", "deployment_uid": "deployment-1", "observed_at": when.isoformat(),
            "containers": [{"pod_uid": "pod-1", "pod_name": "mission-pod", "container": "mission", "container_type": "regular",
                            "image_id": DIGEST, "restart_count": 0}]}


def images(window=120):
    return {"before": snapshot(NOW - timedelta(seconds=window + 1)), "after": snapshot(NOW)}


def response(values, *, metric="up", labels=None):
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": dict(SCOPE, **(labels or {}), **({"__name__": metric} if metric else {})), "values": values}]}}


def fixture(window=120):
    times = list(range(END - window, END + 1, 15))
    return {"up": response([[t, "1"] for t in times]),
            "up_timestamp": response([[t, str(t)] for t in times], metric=None),
            "counter": response([[t, str(i + 10)] for i, t in enumerate(times)], metric="mission_events_total"),
            "counter_timestamp": response([[t, str(t)] for t in times], metric=None)}


class Client:
    def __init__(self, data=None):
        self.data = data or fixture()
        self.calls = []

    def get_json(self, path, params=None):
        self.calls.append((path, deepcopy(params)))
        query = params["query"]
        key = "counter" if "mission_events_total" in query else "up"
        if query.startswith("timestamp("):
            key += "_timestamp"
        result = self.data[key]
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


def collect(client=None, evidence=None, **options):
    return PrometheusCollector(client or Client(), **options).collect(**SCOPE, image_evidence=images(options.get("window_seconds", 120)) if evidence is None else evidence, now=NOW)


def codes(report):
    return {gap["code"] for gap in report["gaps"]}


def values(data, key):
    return data[key]["data"]["result"][0]["values"]


def test_pass_requires_three_real_fresh_scrapes_progression_and_concrete_images():
    client = Client(fixture(30))
    report = collect(client, window_seconds=30)
    assert report["decision"] == "pass" and report["execution_status"] == "complete"
    assert report["observations"]["scrape"]["fresh_sample_count"] == 3
    assert report["observations"]["functional"]["progressing"]
    assert report["observations"]["image_comparison"]["before"]["containers"][0]["image_id"] == DIGEST
    assert len(client.calls) == 4
    assert all(path == "/api/v1/query_range" and params["limit"] == 2 for path, params in client.calls)
    assert sum(params["query"].startswith("timestamp(") for _, params in client.calls) == 2
    for _, params in client.calls:
        assert all(f'{key}="{value}"' in params["query"] for key, value in SCOPE.items())


def test_repeated_interpolated_samples_are_not_three_scrapes():
    data = fixture(30)
    for key in ("up_timestamp", "counter_timestamp"):
        for pair in values(data, key):
            pair[1] = str(END - 30)
    for pair in values(data, "counter"):
        pair[1] = "10"
    report = collect(Client(data), window_seconds=30)
    assert report["decision"] == "unknown"
    assert "prometheus-insufficient-fresh-samples" in codes(report)
    assert report["observations"]["scrape"]["fresh_sample_count"] == 1


def test_interpolated_value_changes_at_same_source_time_are_rejected():
    data = fixture(30)
    for pair in values(data, "counter_timestamp"):
        pair[1] = str(END - 30)
    assert "prometheus-source-value-race" in codes(collect(Client(data), window_seconds=30))


def test_current_evaluation_timestamps_cannot_hide_stale_sources():
    data = fixture()
    for key in ("up_timestamp", "counter_timestamp"):
        for pair in values(data, key):
            pair[1] = str(pair[0] - 60)
    report = collect(Client(data))
    assert report["decision"] == "unknown" and "prometheus-stale-source-sample" in codes(report)


def test_up_zero_is_failure_and_retained_when_counter_is_missing():
    data = fixture()
    values(data, "up")[-1][1] = "0"
    assert collect(Client(data))["decision"] == "fail"
    data["counter"]["data"]["result"] = []
    report = collect(Client(data))
    assert report["decision"] == "unknown" and report["confirmed_failure"]
    assert not report["observations"]["scrape"]["all_up"]


def test_counter_stall_is_confirmed_functional_failure():
    data = fixture()
    values(data, "counter")[-1][1] = values(data, "counter")[-2][1]
    report = collect(Client(data))
    assert report["decision"] == "fail" and report["confirmed_failure"]
    assert not report["observations"]["functional"]["progressing"]


def test_counter_reset_cannot_establish_uninterrupted_function():
    data = fixture()
    values(data, "counter")[-1][1] = "0"
    report = collect(Client(data))
    assert report["decision"] == "unknown" and not report["confirmed_failure"]
    assert "prometheus-counter-reset" in codes(report)


def test_counter_and_up_must_use_same_source_window():
    data = fixture()
    for pair in values(data, "counter_timestamp"):
        pair[1] = str(pair[0] - 1)
    assert "prometheus-source-window-mismatch" in codes(collect(Client(data)))


@pytest.mark.parametrize("mutation,code", [
    (lambda r: r["data"]["result"].append(deepcopy(r["data"]["result"][0])), "missing-or-multiple-series"),
    (lambda r: r["data"]["result"][0]["metric"].update(namespace="other"), "target-identity-mismatch"),
    (lambda r: r.update(warnings=["sensitive-value-must-not-escape"]), "query-incomplete"),
    (lambda r: r.update(infos=["sensitive-value-must-not-escape"]), "query-incomplete"),
    (lambda r: r.update(status="error", error="sensitive-value-must-not-escape"), "query-incomplete"),
    (lambda r: r["data"].update(resultType="vector"), "unsupported-result"),
])
def test_partial_malformed_or_unscoped_query_never_passes(mutation, code):
    data = fixture()
    mutation(data["up"])
    report = collect(Client(data))
    assert report["decision"] == "unknown" and "prometheus-" + code in codes(report)
    assert "sensitive-value-must-not-escape" not in json.dumps(report)


@pytest.mark.parametrize("invalid", ["NaN", "+Inf", "-Inf", True, None, "not-a-number"])
def test_invalid_numeric_samples_are_unknown(invalid):
    data = fixture()
    values(data, "counter")[-1][1] = invalid
    assert collect(Client(data))["decision"] == "unknown"


@pytest.mark.parametrize("mutation", [
    lambda image: image["after"]["containers"][0].update(restart_count=1),
    lambda image: image["after"]["containers"][0].update(pod_uid="replacement-pod"),
    lambda image: image["after"]["containers"][0].update(image_id="sha256:" + "b" * 64),
    lambda image: image["after"].update(deployment_uid="replacement-deployment"),
    lambda image: image["after"].update(service="other"),
    lambda image: image["after"]["containers"][0].update(image_id="latest"),
    lambda image: image["after"]["containers"].append(deepcopy(image["after"]["containers"][0])),
    lambda image: image["before"].update(observed_at=NOW.isoformat()),
    lambda image: image["after"].update(observed_at=(NOW - timedelta(seconds=15)).isoformat()),
])
def test_images_restarts_identity_and_bracketing_are_required(mutation):
    image = images()
    mutation(image)
    report = collect(evidence=image)
    assert report["decision"] == "unknown"


@pytest.mark.parametrize("evidence", [True, False, {}, {"image_stable": True}])
def test_bare_image_stability_claims_are_not_evidence(evidence):
    assert collect(evidence=evidence)["decision"] == "unknown"


def test_extra_series_labels_participate_in_matching_without_exporting_them():
    data = fixture()
    for result in data.values():
        result["data"]["result"][0]["metric"]["private"] = "sensitive-value-must-not-escape"
    report = collect(Client(data))
    assert report["decision"] == "pass"
    assert "sensitive-value-must-not-escape" not in json.dumps(report)
    data["counter"]["data"]["result"][0]["metric"]["private"] = "other"
    assert collect(Client(data))["decision"] == "unknown"


def test_api_denial_is_sanitized_and_other_queries_continue():
    data = fixture()
    data["counter"] = APIError("sensitive-value-must-not-escape", 403)
    client = Client(data)
    report = collect(client)
    assert report["decision"] == "unknown" and "prometheus-denied" in codes(report)
    assert len(client.calls) == 4
    assert "sensitive-value-must-not-escape" not in json.dumps(report)


def test_missing_evaluation_points_cannot_hide_a_gap_between_fresh_samples():
    data = fixture()
    values(data, "up").pop(3)
    report = collect(Client(data))
    assert report["decision"] == "unknown"


def test_delivery_boundary_uses_source_time_instead_of_query_time():
    collector = PrometheusCollector(Client(fixture(30)), window_seconds=30)
    report = collector.collect(**SCOPE, image_evidence=images(30), now=NOW, delivered_at=NOW - timedelta(seconds=30))
    assert report["decision"] == "unknown" and "prometheus-pre-delivery-source-sample" in codes(report)
    report = PrometheusCollector(Client(fixture(30)), window_seconds=30).collect(
        **SCOPE, image_evidence=images(30), now=NOW, delivered_at=(NOW - timedelta(seconds=31)).isoformat())
    assert report["decision"] == "pass"
    assert report["observations"]["window"]["delivered_at"]


def test_image_freshness_is_relative_to_each_end_of_window():
    evidence = images()
    # The before snapshot is 121 seconds old at window end, but just one second
    # older than its relevant boundary: the start of the 120-second window.
    assert collect(evidence=evidence)["decision"] == "pass"
    evidence["before"]["observed_at"] = (NOW - timedelta(seconds=166)).isoformat()
    assert collect(evidence=evidence)["decision"] == "unknown"


def test_image_collection_bounds_must_bracket_the_source_window():
    evidence = images()
    evidence["after"].update(observed_from=NOW.isoformat(), observed_through=(NOW + timedelta(seconds=10)).isoformat())
    assert collect(evidence=evidence)["decision"] == "pass"
    evidence["after"]["observed_from"] = (NOW - timedelta(seconds=1)).isoformat()
    assert collect(evidence=evidence)["decision"] == "unknown"


def test_invalid_delivery_time_fails_before_network():
    client = Client()
    with pytest.raises(ValueError):
        PrometheusCollector(client).collect(**SCOPE, delivered_at="not-a-time", now=NOW)
    assert not client.calls


def test_collector_over_real_tls_reads_four_exact_query_ranges(server):
    data = fixture()
    server["responses"] = [{"json": data[key]} for key in ("up", "up_timestamp", "counter", "counter_timestamp")]
    client = HTTPSReadClient(server["origin"], ca_bundle=server["ca"])
    report = collect(client)
    assert report["decision"] == "pass"
    assert len(client.receipts) == len(server["requests"]) == 4
    assert all(request["path"].startswith("/api/v1/query_range?") for request in server["requests"])
    assert all(receipt["status"] == 200 for receipt in client.receipts)


def test_kubernetes_collector_snapshots_satisfy_the_prometheus_contract():
    from test_kubernetes_adapter import Client as KubeClient

    from devsecops.adapters.kubernetes import KubernetesCollector

    before = KubernetesCollector(KubeClient()).collect("demo", "mission", "mission", now=NOW - timedelta(seconds=121))
    after = KubernetesCollector(KubeClient()).collect("demo", "mission", "mission", now=NOW)
    report = collect(evidence={"before": before["observations"]["image_snapshot"], "after": after["observations"]["image_snapshot"]})
    assert before["decision"] == after["decision"] == report["decision"] == "pass"


@pytest.mark.parametrize("key,value", [("namespace", "../other"), ("service", "mission\n"), ("job", 'x"} or up{'),
                                        ("instance", "user:secret@host:9090")])
def test_query_injection_or_credential_bearing_target_rejected_before_network(key, value):
    scope = dict(SCOPE, **{key: value})
    client = Client()
    with pytest.raises(ValueError):
        PrometheusCollector(client).collect(**scope, now=NOW)
    assert not client.calls


@pytest.mark.parametrize("options", [{"minimum_samples": 2}, {"window_seconds": 3601}, {"step_seconds": 0},
                                     {"window_seconds": 600, "step_seconds": 1}, {"max_sample_age_seconds": True}])
def test_unbounded_or_weak_sample_configuration_is_invalid(options):
    with pytest.raises(ValueError):
        PrometheusCollector(Client(), **options)
