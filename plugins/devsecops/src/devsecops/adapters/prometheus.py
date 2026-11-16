"""Read-only, exact-target scrape and synthetic functional observations."""
from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from itertools import pairwise

from .http import APIError


class _Invalid(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__("Prometheus evidence is incomplete or unsupported")


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise _Invalid("invalid-sample")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise _Invalid("invalid-sample") from None
    if not math.isfinite(result):
        raise _Invalid("nonfinite-sample")
    return result


def _time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise _Invalid("invalid-image-evidence")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except ValueError:
        raise _Invalid("invalid-image-evidence") from None


def _identity(value, limit=253):
    if not isinstance(value, str) or len(value) > limit or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise _Invalid("invalid-image-evidence")
    return value


def _image_snapshot(snapshot, namespace, service):
    if not isinstance(snapshot, dict) or snapshot.get("namespace") != namespace or snapshot.get("service") != service:
        raise _Invalid("image-scope-mismatch")
    result = {"namespace": namespace, "service": service,
              "deployment_uid": _identity(snapshot.get("deployment_uid"), 128),
              "observed_at": _time(snapshot.get("observed_at")).isoformat(), "containers": []}
    result["observed_from"] = _time(snapshot.get("observed_from", result["observed_at"])).isoformat()
    result["observed_through"] = _time(snapshot.get("observed_through", result["observed_at"])).isoformat()
    if _time(result["observed_from"]) > _time(result["observed_through"]):
        raise _Invalid("invalid-image-evidence")
    containers = snapshot.get("containers")
    if not isinstance(containers, list) or not 1 <= len(containers) <= 100:
        raise _Invalid("missing-image-identities")
    seen = set()
    for container in containers:
        if not isinstance(container, dict):
            raise _Invalid("invalid-image-evidence")
        image = container.get("image_id")
        if not isinstance(image, str) or len(image) > 1024 or not re.fullmatch(
                r"(?:[a-z][a-z0-9+.-]*://)?(?:[A-Za-z0-9._:/-]+@)?sha256:[a-f0-9]{64}", image):
            raise _Invalid("missing-image-digest")
        item = {"pod_uid": _identity(container.get("pod_uid"), 128),
                "pod_name": _identity(container.get("pod_name")),
                "container": _identity(container.get("container"), 63),
                "container_type": container.get("container_type", "regular"),
                "image_id": image, "restart_count": container.get("restart_count")}
        if item["container_type"] not in {"regular", "init"} or type(item["restart_count"]) is not int or not 0 <= item["restart_count"] <= 2**53:
            raise _Invalid("invalid-image-evidence")
        identity = (item["pod_uid"], item["container_type"], item["container"])
        if identity in seen:
            raise _Invalid("duplicate-image-identity")
        seen.add(identity)
        result["containers"].append(item)
    result["containers"].sort(key=lambda item: (item["pod_uid"], item["container_type"], item["container"]))
    return result


class PrometheusCollector:
    """Four generated query_range requests; no caller-provided PromQL or targets list."""

    def __init__(self, client, *, window_seconds=120, step_seconds=15,
                 max_sample_age_seconds=45, minimum_samples=3):
        if any(type(v) is not int for v in (window_seconds, step_seconds, max_sample_age_seconds, minimum_samples)):
            raise ValueError("Invalid Prometheus observation limits")
        if (not 1 <= step_seconds <= 300 or not 2 * step_seconds <= window_seconds <= 3600
                or window_seconds % step_seconds or window_seconds // step_seconds + 1 > 241
                or not step_seconds <= max_sample_age_seconds <= 300
                or not 3 <= minimum_samples <= window_seconds // step_seconds + 1):
            raise ValueError("Invalid Prometheus observation limits")
        self.client = client
        self.window, self.step, self.max_age, self.minimum = window_seconds, step_seconds, max_sample_age_seconds, minimum_samples

    def _matrix(self, response, scope, start, end, metric):
        if response.get("status") != "success" or response.get("warnings") or response.get("infos"):
            raise _Invalid("query-incomplete")
        data = response.get("data", {})
        if not isinstance(data, dict) or data.get("resultType") != "matrix":
            raise _Invalid("unsupported-result")
        results = data.get("result")
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            raise _Invalid("missing-or-multiple-series")
        result = results[0]
        if result.get("histograms"):
            raise _Invalid("unsupported-result")
        labels = result.get("metric")
        if not isinstance(labels, dict) or len(labels) > 32 or any(labels.get(key) != value for key, value in scope.items()):
            raise _Invalid("target-identity-mismatch")
        if "__name__" in labels and labels["__name__"] != metric:
            raise _Invalid("metric-identity-mismatch")
        labels = {key: value for key, value in labels.items() if key != "__name__"}
        # Extra labels participate in equality checks but are not exported.
        if any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 128 or len(v) > 512 for k, v in labels.items()):
            raise _Invalid("invalid-labels")
        pairs = result.get("values")
        if not isinstance(pairs, list) or len(pairs) != self.window // self.step + 1:
            raise _Invalid("missing-or-excessive-samples")
        samples = {}
        previous = -math.inf
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2:
                raise _Invalid("invalid-sample")
            evaluation, value = _finite(pair[0]), _finite(pair[1])
            if evaluation <= previous or not start <= evaluation <= end or abs((evaluation - start) % self.step) > 0.001:
                raise _Invalid("invalid-evaluation-grid")
            samples[evaluation], previous = value, evaluation
        if end - previous >= self.step:
            raise _Invalid("incomplete-evaluation-window")
        return labels, samples

    def _source_samples(self, values, timestamps, *, start, end, counter=False, delivered_epoch=None):
        if values[0] != timestamps[0] or values[1].keys() != timestamps[1].keys():
            raise _Invalid("source-window-mismatch")
        result = []
        previous = None
        for evaluation, value in values[1].items():
            source = timestamps[1][evaluation]
            if source < 0 or source > evaluation or evaluation - source > self.max_age:
                raise _Invalid("stale-source-sample")
            if counter and not 0 <= value <= 2**53:
                raise _Invalid("invalid-counter")
            if not counter and value not in (0, 1):
                raise _Invalid("invalid-up-value")
            if previous is not None:
                if source < previous["source_timestamp"]:
                    raise _Invalid("source-time-regressed")
                if source == previous["source_timestamp"]:
                    if value != previous["value"]:
                        raise _Invalid("source-value-race")
                    continue
                if source - previous["source_timestamp"] > self.max_age:
                    raise _Invalid("source-scrape-gap")
            previous = {"source_timestamp": source, "value": value}
            if source >= start:
                if delivered_epoch is not None and source <= delivered_epoch:
                    raise _Invalid("pre-delivery-source-sample")
                result.append(previous)
        if not result or end - result[-1]["source_timestamp"] > self.max_age:
            raise _Invalid("stale-source-sample")
        return result

    def collect(self, namespace, service, job, instance, *, image_evidence=None, delivered_at=None, now=None):
        if any(not isinstance(value, str) or len(value) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value)
               for value in (namespace, service)):
            raise ValueError("Invalid Prometheus namespace or service scope")
        if not isinstance(job, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", job):
            raise ValueError("Invalid Prometheus job scope")
        if not isinstance(instance, str) or not re.fullmatch(r"[A-Za-z0-9_.:\[\]-]{1,253}", instance):
            raise ValueError("Invalid Prometheus instance scope")
        clock = now or datetime.now(UTC)
        if not isinstance(clock, datetime) or clock.tzinfo is None:
            raise ValueError("An aware observation time is required")
        delivery = None
        if delivered_at is not None:
            try:
                delivery = _time(delivered_at.isoformat() if isinstance(delivered_at, datetime) else delivered_at)
            except _Invalid:
                raise ValueError("An aware delivery time is required") from None
        end = int(clock.timestamp())
        start = end - self.window
        scope = {"namespace": namespace, "service": service, "job": job, "instance": instance}
        report = {"schema_version": 1, "provider": "prometheus", "execution_status": "partial", "decision": "unknown",
                  "confirmed_failure": False, "observation_kind": "scrape_and_functional_window",
                  "scope": scope, "observed_at": clock.astimezone(UTC).isoformat(),
                  "observations": {"window": {"start": start, "end": end, "step_seconds": self.step,
                       "max_sample_age_seconds": self.max_age, "minimum_fresh_samples": self.minimum}}, "gaps": []}
        observations = report["observations"]
        if delivery is not None:
            observations["window"]["delivered_at"] = delivery.isoformat()

        def gap(code):
            report["gaps"].append({"code": "prometheus-" + code,
                "message": "The scoped runtime or source-sample evidence is unavailable, inconsistent, or unsupported",
                "required": True})

        selector = "{" + ",".join(f"{key}={json.dumps(value)}" for key, value in scope.items()) + "}"
        queries = {"up": "up" + selector, "up_timestamp": "timestamp(up" + selector + ")",
                   "counter": "mission_events_total" + selector,
                   "counter_timestamp": "timestamp(mission_events_total" + selector + ")"}
        matrices = {}
        for name, query in queries.items():
            try:
                response = self.client.get_json("/api/v1/query_range", {"query": query, "start": start,
                    "end": end, "step": self.step, "timeout": "5s", "limit": 2})
                matrices[name] = self._matrix(response, scope, start, end, "up" if name.startswith("up") else "mission_events_total")
            except APIError as exc:
                gap("denied" if exc.status in (401, 403) else "api-unavailable")
            except _Invalid as exc:
                gap(exc.code)
            except (TypeError, KeyError, AttributeError, ValueError):
                gap("invalid-result")
        series = {}
        for key in ("up", "counter"):
            if key in matrices and key + "_timestamp" in matrices:
                try:
                    series[key] = self._source_samples(matrices[key], matrices[key + "_timestamp"],
                        start=start, end=end, counter=key == "counter", delivered_epoch=delivery.timestamp() if delivery else None)
                    if len(series[key]) < self.minimum:
                        gap("insufficient-fresh-samples")
                except _Invalid as exc:
                    gap(exc.code)
        if "up" in series:
            up = series["up"]
            observations["scrape"] = {"fresh_sample_count": len(up), "samples": up,
                                       "all_up": all(sample["value"] == 1 for sample in up)}
            report["confirmed_failure"] = any(sample["value"] == 0 for sample in up)
        if "counter" in series:
            counter = series["counter"]
            observations["functional"] = {"metric": "mission_events_total", "fresh_sample_count": len(counter),
                                          "samples": counter, "progressing": None}
            if any(b["value"] < a["value"] for a, b in pairwise(counter)):
                gap("counter-reset")
            elif len(counter) >= self.minimum:
                progressing = all(b["value"] > a["value"] for a, b in pairwise(counter))
                observations["functional"]["progressing"] = progressing
                if not progressing:
                    report["confirmed_failure"] = True
        if ("up" in series and "counter" in series and (matrices["up"][0] != matrices["counter"][0]
                or [p["source_timestamp"] for p in series["up"]] != [p["source_timestamp"] for p in series["counter"]])):
            gap("source-window-mismatch")
        try:
            if not isinstance(image_evidence, dict):
                raise _Invalid("missing-image-evidence")
            before = _image_snapshot(image_evidence.get("before"), namespace, service)
            after = _image_snapshot(image_evidence.get("after"), namespace, service)
            stable = before["containers"] == after["containers"] and before["deployment_uid"] == after["deployment_uid"]
            observations["image_comparison"] = {"basis": "caller-supplied-kubernetes-snapshots", "stable": stable,
                                                  "before": before, "after": after}
            if not stable:
                raise _Invalid("image-identity-or-restart-changed")
            if (_time(before["observed_through"]).timestamp() > start or _time(after["observed_from"]).timestamp() < end
                    or _time(after["observed_through"]).timestamp() > clock.timestamp() + self.max_age
                    or start - _time(before["observed_through"]).timestamp() > self.max_age
                    or any((_time(s["observed_through"]) - _time(s["observed_from"])).total_seconds() > self.max_age for s in (before, after))):
                raise _Invalid("image-window-mismatch")
            for matrix in matrices.values():
                if matrix[0].get("pod") is not None and matrix[0]["pod"] not in {c["pod_name"] for c in after["containers"]}:
                    raise _Invalid("image-target-mismatch")
        except _Invalid as exc:
            gap(exc.code)
        except (ValueError, TypeError, KeyError, AttributeError):
            gap("invalid-image-evidence")
        if not report["gaps"]:
            report["execution_status"] = "complete"
            report["decision"] = "fail" if report["confirmed_failure"] else "pass"
        return report
