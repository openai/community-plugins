"""Frozen range evidence must also be fresh when collection actually finishes."""
from datetime import datetime, timedelta
import json

import pytest

from devsecops.adapters.kubernetes import KubernetesCollector
from devsecops.runtime import collection
from devsecops.runtime.context import UTC, validate
from test_runtime_collection import clients_for, finish, setup
from test_runtime_context import NOW, codes


def pending_window():
    context, clients = setup(mutate=lambda _config, lock, _intent: lock.update(observation_window_seconds=60))
    pending = collection.start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    return context, pending, datetime.fromtimestamp(pending["window"]["end"], UTC)


@pytest.mark.parametrize("delay,fresh", [(0, True), (30, True), (30.000001, False), (40, False)])
def test_near_expiry_uses_source_age_at_collection_not_evaluation_end(delay, fresh):
    context, pending, end = pending_window()
    observed = end + timedelta(seconds=delay)
    after = clients_for(context, observed)
    client = after[("mission", "prometheus")]
    client.prom_options = {"timestamp_shift": 15}
    result = finish(context, pending, now=observed, clients=after)
    report = result["targets"]["mission"]["prometheus"]
    observations = report["observations"]
    assert observations["window_assessment"]["decision"] == "pass"
    assert observations["window_assessment"]["execution_status"] == "complete"
    assert observations["evaluated_at"] == end.isoformat()
    assert report["observed_at"] == report["transport"]["finished_at"] == observed.isoformat()
    assert observations["collection_freshness"]["reference_time"] == observed.isoformat()
    assert observations["collection_freshness"]["decision"] == ("pass" if fresh else "unknown")
    assert report["decision"] == ("pass" if fresh else "unknown")
    assert report["execution_status"] == ("complete" if fresh else "partial")
    assert not report["confirmed_failure"]
    for source in observations["collection_freshness"]["series"].values():
        assert source["fresh"] is fresh
        assert source["age_seconds"] == pytest.approx(delay + 15)
        assert source["last_source_timestamp"] == end.timestamp() - 15
    # Counts and values describe the original window, even if now too old.
    assert observations["scrape"]["fresh_sample_count"] == 4
    assert observations["functional"]["progressing"] is True
    assert ("runtime-prometheus-stale-at-collection" in codes(result)) is not fresh
    assert len(client.calls) == 4
    assert all(params["start"] == pending["window"]["start"] and params["end"] == pending["window"]["end"]
               for _path, params in client.calls)
    assert result["window"] == pending["window"]
    assert result["decision"] == "unknown" and result["execution_status"] == "partial"
    validate(result, collection.COLLECTION_SCHEMA)


@pytest.mark.parametrize("failure", [{"up": 0}, {"counter_fault": "flat"}])
def test_completion_staleness_retains_confirmed_failure_in_original_window(failure):
    context, pending, end = pending_window()
    observed = end + timedelta(seconds=40)
    after = clients_for(context, observed)
    after[("mission", "prometheus")].prom_options = {"timestamp_shift": 15, **failure}
    result = finish(context, pending, now=observed, clients=after)
    report = result["targets"]["mission"]["prometheus"]
    assert report["observations"]["window_assessment"] == {
        "decision": "fail", "execution_status": "complete", "confirmed_failure": True}
    assert report["decision"] == "unknown" and report["execution_status"] == "partial"
    assert report["confirmed_failure"] and result["confirmed_failure"]
    assert "runtime-prometheus-stale-at-collection" in codes(report)


@pytest.mark.parametrize("read_delay,shift,expected_codes", [
    (0, 15, set()),
    (1, 15, {"runtime-prometheus-stale-at-collection"}),
    (16, 0, {"runtime-prometheus-stale-at-collection", "runtime-collection-window-expired"}),
    (-1, 15, {"runtime-collection-clock-regressed"}),
    (-40, 0, {"runtime-prometheus-source-time-future", "runtime-collection-clock-regressed"}),
])
def test_delayed_final_response_evaluates_completion_clock_without_sliding_query(
        monkeypatch, read_delay, shift, expected_codes):
    context, pending, end = pending_window()
    started = end + timedelta(seconds=30)
    current = [started]
    after = clients_for(context, started)
    client = after[("mission", "prometheus")]
    client.prom_options = {"timestamp_shift": shift}
    target = next(t for t in context["intent"]["targets"] if t["id"] == "mission")
    snapshot = KubernetesCollector(after[("mission", "kubernetes")]).collect(
        "demo", "mission", "mission", now=started)["observations"]["image_snapshot"]
    images = {"before": pending["targets"]["mission"]["before"]["kubernetes"]["observations"]["image_snapshot"],
              "after": snapshot}
    original_read = client.get_json

    def delayed_read(path, params=None):
        result = original_read(path, params)
        if len(client.calls) == 4:
            current[0] += timedelta(seconds=read_delay)
        return result

    monkeypatch.setattr(client, "get_json", delayed_read)
    monkeypatch.setattr(collection, "clock", lambda value=None: current[0] if value is None else value)
    report = collection._read("prometheus", target, collection._Clients(context, after), None,
                              window=pending["window"], images=images)
    assert report["transport"]["started_at"] == started.isoformat()
    assert report["transport"]["finished_at"] == (started + timedelta(seconds=read_delay)).isoformat()
    assert report["observations"]["window_assessment"]["decision"] == "pass"
    assert codes(report) == expected_codes
    assert report["decision"] == ("unknown" if expected_codes else "pass")
    assert len(client.calls) == len(report["transport"]["receipts"]) == 4
    assert all(params["end"] == end.timestamp() for _path, params in client.calls)
    assert "DO-NOT-EXPORT-SECRET" not in json.dumps(report)


def test_missing_source_time_is_unknown_without_erasing_provider_gap():
    context, pending, end = pending_window()
    after = clients_for(context, end)
    after[("mission", "prometheus")].prom_options = {"timestamp_shift": 60}
    report = finish(context, pending, now=end, clients=after)["targets"]["mission"]["prometheus"]
    assert {"prometheus-stale-source-sample", "runtime-prometheus-source-time-unavailable"} <= codes(report)
    assert report["decision"] == "unknown"
    assert report["observations"]["window_assessment"]["decision"] == "unknown"
    for series in report["observations"]["collection_freshness"]["series"].values():
        assert series == {"last_source_timestamp": None, "age_seconds": None, "fresh": None}
