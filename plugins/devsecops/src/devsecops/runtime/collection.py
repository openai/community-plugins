"""Two bounded read phases with an immutable intervening observation window.

No sleeping, retries, mutations, native patch generation, direct HTTP probes, or
recovery verdicts occur here. A measured bundle is always an unknown workflow
decision; provider results describe only their narrow observation contracts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
import re
import time

from devsecops.adapters.argocd import collect_argocd
from devsecops.adapters.http import APIError, HTTPSReadClient
from devsecops.adapters.kubernetes import KubernetesCollector
from devsecops.adapters.prometheus import PrometheusCollector
from devsecops.factory.contracts import DIGEST, NAME, array, document, object_schema
from .context import (
    ARTIFACT, CONTEXT_SCHEMA, DATE, DELIVERY_SCHEMA, GAP, IDENTITIES, SPEC_DIGEST_FORMAT, UTC,
    clock, digest, file_hash, gap, load_input, parse_time, seal, unique_gaps, validate, verify_runtime_authority, verify_seal,
)

PHASE_TIMEOUT_SECONDS = 120
PER_CLIENT_TIMEOUT_SECONDS = 20
WINDOW = object_schema({
    "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 0},
    "next_eligible_at": DATE, "expires_at": DATE,
    "window_seconds": {"type": "integer", "minimum": 2, "maximum": 3600},
    "step_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
    "max_sample_age_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
    "minimum_fresh_samples": {"type": "integer", "minimum": 3, "maximum": 20},
})
DELIVERY = object_schema({"binding": DELIVERY_SCHEMA, "artifact": ARTIFACT, "authentication": {"const": "not-verified"}})
# Provider payloads retain their own versioned schema. This layer fixes its
# envelope and hash-binds the entire payload instead of silently projecting it.
PROVIDER = {"type": "object", "maxProperties": 32}
PHASE = object_schema({"argocd": PROVIDER, "kubernetes": PROVIDER}, [])
TARGET_EVIDENCE = object_schema({
    "roles": array(NAME, minimum=1, maximum=8), "before": PHASE, "after": PHASE,
    "prometheus": {"anyOf": [PROVIDER, {"type": "null"}]},
    "bindings": {"type": "object", "maxProperties": 16},
})
COLLECTION_SCHEMA = document("runtime-collection", {
    "schema_version": {"const": 1}, "kind": {"const": "runtime-collection"},
    "observation_kind": {"const": "measured-runtime-collection"},
    "execution_mode": {"enum": ["direct", "controlled-test"]},
    "stage": {"enum": ["pending", "measured", "blocked"]},
    "execution_status": {"const": "partial"}, "decision": {"const": "unknown"},
    "confirmed_failure": {"type": "boolean"}, "observed_at": DATE,
    "context_sha256": DIGEST, "context_snapshot": CONTEXT_SCHEMA, "identities": IDENTITIES,
    "delivery": {"anyOf": [DELIVERY, {"type": "null"}]},
    "window": {"anyOf": [WINDOW, {"type": "null"}]},
    "targets": {"type": "object", "minProperties": 1, "maxProperties": 8, "additionalProperties": TARGET_EVIDENCE},
    "gaps": array(GAP, maximum=1000), "pending_sha256": {"type": ["string", "null"], "pattern": "^[a-f0-9]{64}$"},
    "bundle_sha256": DIGEST,
})


class _Clients:
    """Per-target provider budgets, plus one wall deadline for the whole phase."""
    def __init__(self, context, supplied, transport=None):
        self.context = context
        self.supplied = {} if supplied is None else supplied
        if not isinstance(self.supplied, dict):
            raise ValueError("Runtime client injection must be a Python mapping")
        self.transport = transport
        if transport is not None:
            from devsecops.factory.owned_runtime_transport import require_bound_runtime_read
            if supplied is not None:
                raise ValueError('Owned read transport cannot be mixed with injected clients')
            require_bound_runtime_read(transport, context)
        self.deadline = time.monotonic() + PHASE_TIMEOUT_SECONDS

    def get(self, provider, target):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise APIError("phase-budget-exhausted")
        config = self.context["factory_config"]
        services = {s["id"]: s for s in config["services"]}
        if provider not in services:
            raise APIError("service-not-configured")
        service = services[provider]
        scope = service["scope"]
        if provider == "argocd":
            namespace = target["argocd"]["destination"]["namespace"]
            names = [target["argocd"]["application"]]
            if (target["argocd"]["application_namespace"] not in scope.get("namespaces", [])
                    or target["argocd"]["project"] not in scope.get("argocd_projects", [])):
                raise APIError("scope-not-authorized")
        else:
            namespace = target[provider]["namespace"]
            names = [target[provider]["service"]]
            if provider == "kubernetes":
                names.append(target[provider]["deployment"])
        if namespace not in scope.get("namespaces", []) or any(n not in scope.get("applications", []) for n in names):
            raise APIError("scope-not-authorized")
        if self.transport is not None:
            return self.transport.client(provider, target, budget=min(PER_CLIENT_TIMEOUT_SECONDS, remaining))
        auth = service["read_auth"]
        if auth["mode"] not in {"none", "bearer"}:
            raise APIError("read-auth-unsupported")
        operator = service.get("operator_auth", {})
        if (auth["mode"] == "bearer" and auth["token_env"] in [v for k, v in operator.items() if k.endswith("_env")]):
            raise APIError("read-operator-identity-overlap")
        ca = self.context["ca_artifacts"].get(provider, {})
        if ca.get("state") == "unreadable":
            raise APIError("ca-input-unreadable")
        def check_ca():
            if ca.get("state") == "frozen":
                try:
                    if file_hash(service["ca_file"]) != ca["sha256"]:
                        raise ValueError
                except ValueError:
                    raise APIError("ca-input-changed") from None
        check_ca()
        injected = self.supplied.get((target["id"], provider), self.supplied.get(provider))
        if injected is not None:
            if getattr(injected, "origin", None) != service["origin"].rstrip("/"):
                raise APIError("client-origin-mismatch")
            return injected
        client = HTTPSReadClient(service["origin"], token_env=auth.get("token_env"), ca_bundle=service.get("ca_file"),
            timeout=min(5.0, remaining), total_timeout=min(PER_CLIENT_TIMEOUT_SECONDS, remaining),
            max_requests=32, max_body_bytes=2_000_000, max_total_bytes=16_000_000)
        check_ca()
        return client


def _missing(provider, code, now):
    # The code comes from our constant transport categories, never response text.
    return {"schema_version": 1, "provider": provider, "execution_status": "partial", "decision": "unknown",
            "confirmed_failure": False, "observed_at": clock(now).isoformat(), "scope": {}, "observations": {},
            "gaps": [{"code": "runtime-" + code, "required": True}]}


def _completion_freshness(report, window, finished):
    """Separate evaluation-window evidence from freshness when reads finish.

    A range query can be internally valid but already too old at collection.
    Keep that historical evidence (including failures); never slide its range
    or silently replace it with another query to obtain a passing observation.
    """
    observations = report["observations"]
    observations["evaluated_at"] = report["observed_at"]
    observations["window_assessment"] = {
        key: report[key] for key in ("execution_status", "decision", "confirmed_failure")}
    report["observed_at"] = finished.isoformat()
    freshness = {"reference_time": finished.isoformat(),
                 "max_sample_age_seconds": window["max_sample_age_seconds"],
                 "decision": "pass", "series": {}}
    findings = []
    for field, metric in (("scrape", "up"), ("functional", "mission_events_total")):
        samples = observations.get(field, {}).get("samples", [])
        source = samples[-1].get("source_timestamp") if samples and isinstance(samples[-1], dict) else None
        valid = type(source) in (int, float) and math.isfinite(source) and source >= 0
        age = finished.timestamp() - source if valid else None
        fresh = 0 <= age <= window["max_sample_age_seconds"] if age is not None else None
        freshness["series"][metric] = {"last_source_timestamp": source if valid else None,
                                       "age_seconds": age, "fresh": fresh}
        if not valid:
            findings.append("runtime-prometheus-source-time-unavailable")
        elif age < 0:
            findings.append("runtime-prometheus-source-time-future")
        elif not fresh:
            findings.append("runtime-prometheus-stale-at-collection")
    if (finished.timestamp() < window["end"]
            or finished < parse_time(report["transport"]["started_at"])):
        findings.append("runtime-collection-clock-regressed")
    if finished.timestamp() > window["end"] + window["max_sample_age_seconds"]:
        findings.append("runtime-collection-window-expired")
    if findings:
        freshness["decision"] = "unknown"
        report["execution_status"], report["decision"] = "partial", "unknown"
        report["gaps"].extend({"code": code,
            "message": "The recorded source samples are not proven fresh at actual collection completion",
            "required": True} for code in sorted(set(findings)))
    observations["collection_freshness"] = freshness


def _read(provider, target, pool, now, *, window=None, images=None, delivered_at=None):
    client = None
    try:
        client = pool.get(provider, target)
        started = clock(now).isoformat()
        receipt_start = len(client.receipts)
        if provider == "kubernetes":
            spec = target[provider]
            report = KubernetesCollector(client).collect(spec["namespace"], spec["deployment"], spec["service"],
                expected_deployment_uid=spec.get("deployment_uid"), expected_generation=spec.get("generation"), now=now)
        elif provider == "argocd":
            spec = target[provider]
            report = collect_argocd(client, project=spec["project"], application_namespace=spec["application_namespace"],
                application=spec["application"], selected_revisions={s["position"]: s["revision"] for s in spec["sources"]},
                max_age_seconds=pool.context["factory_lock"]["freshness_seconds"], now=now)
        else:
            spec = target[provider]
            collector = PrometheusCollector(client, window_seconds=window["window_seconds"], step_seconds=window["step_seconds"],
                max_sample_age_seconds=window["max_sample_age_seconds"], minimum_samples=window["minimum_fresh_samples"])
            # This is the frozen range endpoint, not the actual collection time.
            report = collector.collect(spec["namespace"], spec["service"], spec["job"], spec["instance"],
                image_evidence=images, delivered_at=delivered_at, now=datetime.fromtimestamp(window["end"], UTC))
        finished = clock(now)
        report["transport"] = {"started_at": started, "finished_at": finished.isoformat(),
                               "receipts": deepcopy(client.receipts[receipt_start:])}
        if pool.transport is not None:
            report['transport']['owned_read_binding'] = client.binding()
        if provider == "prometheus":
            _completion_freshness(report, window, finished)
        return report
    except APIError as exc:
        return _missing(provider, exc.code, now)
    except ValueError:
        return _missing(provider, "collector-input-invalid", now)


def _provider_gaps(report, target_id):
    result = [gap(g["code"], target_id) for g in report.get("gaps", []) if g.get("required", True)]
    if report.get("execution_status") != "complete" and not result:
        result.append(gap("provider-evidence-incomplete", target_id))
    if not report.get("transport", {}).get("receipts"):
        result.append(gap("transport-receipts-missing", target_id))
    return result


def _phase_bindings(target, report, phase):
    """Compare observed identity to expected identity, without conferring trust."""
    target_id, findings = target["id"], []
    kube = report.get("kubernetes", {}).get("observations", {})
    expected = target["kubernetes"].get("service_uid")
    observed = kube.get("service", {}).get("uid")
    service = {"expected": expected, "observed": observed, "matched": bool(expected and expected == observed)}
    if not expected:
        findings.append(gap("service-uid-intent-missing", target_id))
    elif not service["matched"]:
        findings.append(gap("service-uid-mismatch", target_id))
    argo = report.get("argocd", {}).get("observations", {})
    expected_sources = {str(s["position"]): s.get("source_spec_sha256") for s in target["argocd"]["sources"]}
    observed_sources = {str(s["position"]): s.get("source_spec_sha256") for s in argo.get("sources", [])}
    expected_destination = target["argocd"].get("destination_spec_sha256")
    observed_destination = argo.get("destination_spec_sha256")
    format_valid = argo.get("spec_digest_format") == SPEC_DIGEST_FORMAT
    source_match = bool(format_valid and all(expected_sources.values()) and expected_sources == observed_sources)
    destination_match = bool(format_valid and expected_destination and expected_destination == observed_destination)
    if not source_match:
        findings.append(gap("argo-source-intent-parity-unverified", target_id))
    if not destination_match:
        findings.append(gap("argo-destination-intent-parity-unverified", target_id))
    return {phase: {"service_uid": service,
        "sources": {"expected": expected_sources, "observed": observed_sources, "matched": source_match},
        "destination": {"expected": expected_destination, "observed": observed_destination, "matched": destination_match},
        "spec_digest_format": SPEC_DIGEST_FORMAT, "intent_authentication": "not-verified"}}, findings


def _image(phase):
    return phase.get("kubernetes", {}).get("observations", {}).get("image_snapshot")


def _window(context, targets):
    latest = []
    for item in targets.values():
        snapshot = _image(item["before"])
        if snapshot:
            latest.append(parse_time(snapshot["observed_through"]).timestamp())
    if not latest:
        return None
    lock = context["factory_lock"]
    start = math.ceil(max(latest))
    end = start + lock["observation_window_seconds"]
    return {"start": start, "end": end, "next_eligible_at": datetime.fromtimestamp(end, UTC).isoformat(),
            "expires_at": datetime.fromtimestamp(end + lock["freshness_seconds"], UTC).isoformat(),
            "window_seconds": lock["observation_window_seconds"], "step_seconds": lock["scrape_interval_seconds"],
            "max_sample_age_seconds": lock["freshness_seconds"], "minimum_fresh_samples": lock["required_fresh_scrapes"]}


def _delivery(context, value, now):
    if value is None:
        return None
    binding, artifact = load_input(value)
    validate(binding, DELIVERY_SCHEMA)
    intent = context["intent"]
    target = next((t for t in intent["targets"] if t["id"] == binding["target_id"]), None)
    if (any(binding[k] != intent[k] for k in ("run_id", "attempt_id", "environment", "identities")) or target is None
            or parse_time(binding["delivered_at"]) > clock(now)):
        raise ValueError("Delivery binding does not match the frozen runtime context")
    source = next((s for s in target["argocd"]["sources"] if s["position"] == binding["source_position"]), None)
    if source is None or source["revision"] != binding["delivered_revision"]:
        raise ValueError("Delivery revision does not match the frozen source position")
    return {"binding": binding, "artifact": artifact, "authentication": "not-verified"}


def _result(value):
    value['gaps'].append(gap('context-authentication-unverified'))
    if value.get('pending_sha256') is not None:
        value['gaps'].append(gap('pending-authentication-unverified'))
    if value['execution_mode'] == 'controlled-test':
        value['gaps'].append(gap('controlled-test-evidence'))
    value["gaps"] = unique_gaps(value["gaps"])
    value["confirmed_failure"] = any(
        report.get("confirmed_failure") is True for target in value["targets"].values()
        for report in [*target["before"].values(), *target["after"].values(), target["prometheus"] or {}])
    value = seal(value, "bundle_sha256")
    validate(value, COLLECTION_SCHEMA)
    return value


def start_runtime_collection(context, *, authority, delivery_receipt=None, clients=None, now=None, transport=None):
    """Collect before snapshots and return an incomplete, resumable fixed window."""
    context = deepcopy(context)
    verify_runtime_authority(context, authority)
    now = clock(now) if now is not None else None
    delivery = _delivery(context, delivery_receipt, now)
    result = {"schema_version": 1, "kind": "runtime-collection", "observation_kind": "measured-runtime-collection",
        "execution_mode": "controlled-test" if clients is not None or now is not None else "direct",
        "stage": "pending", "execution_status": "partial", "decision": "unknown", "confirmed_failure": False,
        "observed_at": clock(now).isoformat(), "context_sha256": context["context_sha256"],
        "context_snapshot": deepcopy(context), "identities": deepcopy(context["intent"]["identities"]),
        "delivery": delivery, "window": None, "targets": {}, "gaps": deepcopy(context["gaps"]), "pending_sha256": None}
    for target in context["intent"]["targets"]:
        result["targets"][target["id"]] = {"roles": deepcopy(target["roles"]), "before": {}, "after": {},
                                             "prometheus": None, "bindings": {}}
    if clock(now) >= parse_time(context["factory_config"]["expires_at"]):
        result["stage"] = "blocked"
        result["gaps"].append(gap("environment-expired"))
        return _result(result)
    if context["intent"]["phase"] == "recovery" and delivery is None:
        result["gaps"].append(gap("delivery-binding-missing"))
    if delivery is not None:
        result["gaps"].append(gap("native-delivery-authentication-unverified"))
    pool = _Clients(context, clients, transport)
    # Do every Argo read first, then end the before phase with image snapshots.
    for provider in ("argocd", "kubernetes"):
        for target in context["intent"]["targets"]:
            report = _read(provider, target, pool, now)
            result["targets"][target["id"]]["before"][provider] = report
            result["gaps"].extend(_provider_gaps(report, target["id"]))
    for target in context["intent"]["targets"]:
        item = result["targets"][target["id"]]
        bindings, gaps = _phase_bindings(target, item["before"], "before")
        item["bindings"].update(bindings)
        result["gaps"].extend(gaps)
    result["window"] = _window(context, result["targets"])
    result["gaps"].append(gap("collection-window-pending"))
    if result["window"] is None:
        result["stage"] = "blocked"
        result["gaps"].append(gap("before-image-interval-missing"))
    result["observed_at"] = clock(now).isoformat()
    return _result(result)


def _image_comparison(before, after, window):
    if not before or not after:
        return {"stable": None, "intervals_valid": False}
    def containers(snapshot):
        items = snapshot.get("containers")
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            return None
        keys = []
        for item in items:
            if (not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k] for k in
                    ("pod_uid", "pod_name", "container", "container_type", "image_id"))
                    or item["container_type"] not in {"regular", "init"}
                    or type(item.get("restart_count")) is not int or not 0 <= item["restart_count"] <= 2**53
                    or not re.fullmatch(r"(?:[a-z][a-z0-9+.-]*://)?(?:[A-Za-z0-9._:/-]+@)?sha256:[a-f0-9]{64}", item["image_id"])):
                return None
            keys.append((item["pod_uid"], item["container_type"], item["container"]))
        if len(set(keys)) != len(keys):
            return None
        return sorted(items, key=lambda c: (c["pod_uid"], c["container_type"], c["container"]))
    before_containers, after_containers = containers(before), containers(after)
    stable = (all(before.get(k) and before.get(k) == after.get(k) for k in ("namespace", "service", "deployment_uid"))
              and before_containers is not None and before_containers == after_containers)
    try:
        b_start, b_end = [parse_time(before[k]).timestamp() for k in ("observed_from", "observed_through")]
        a_start, a_end = [parse_time(after[k]).timestamp() for k in ("observed_from", "observed_through")]
        age = window["max_sample_age_seconds"]
        intervals_valid = (b_start <= b_end <= window["start"] and window["start"] - b_end <= age
                           and window["end"] <= a_start <= a_end <= window["end"] + age
                           and b_end - b_start <= age and a_end - a_start <= age)
    except (KeyError, ValueError):
        intervals_valid = False
    return {"stable": stable, "intervals_valid": intervals_valid,
            "before_sha256": digest(before), "after_sha256": digest(after), "mapping_authentication": "not-verified"}


def finish_runtime_collection(context, pending, *, authority, delivery_receipt=None, clients=None, now=None, transport=None):
    """Resume the exact saved range; early/late calls make no provider requests."""
    context, pending = deepcopy(context), deepcopy(pending)
    verify_runtime_authority(context, authority)
    validate(pending, COLLECTION_SCHEMA)
    verify_seal(pending, "bundle_sha256")
    if (pending["stage"] != "pending" or pending["context_sha256"] != context["context_sha256"]
            or pending["context_snapshot"] != context or pending["identities"] != context["intent"]["identities"]
            or set(pending["targets"]) != {t["id"] for t in context["intent"]["targets"]}
            or any(t["after"] or t["prometheus"] is not None for t in pending["targets"].values())
            or pending["window"] is None or pending["window"] != _window(context, pending["targets"])):
        raise ValueError("Pending runtime collection does not match the frozen context or window")
    delivery = pending["delivery"]
    if delivery is not None:
        if delivery_receipt is None:
            raise ValueError("Explicit trusted delivery receipt is required for resume")
        supplied = _delivery(context, delivery_receipt, now)
        if (supplied["binding"] != delivery["binding"]
                or any(supplied["artifact"][key] != delivery["artifact"][key]
                       for key in ("sha256", "canonical_sha256"))):
            raise ValueError("Frozen delivery receipt changed")
    elif delivery_receipt is not None:
        raise ValueError("A delivery receipt cannot be added to the pending collection")
    now = clock(now) if now is not None else None
    current = clock(now)
    result = deepcopy(pending)
    if clients is not None or now is not None:
        result['execution_mode'] = 'controlled-test'
    result["pending_sha256"] = pending["bundle_sha256"]
    result["observed_at"] = current.isoformat()
    window = result["window"]
    if current.timestamp() < window["end"]:
        result["gaps"].append(gap("collection-window-not-ready"))
        return _result(result)
    if (current.timestamp() > window["end"] + window["max_sample_age_seconds"]
            or current >= parse_time(context["factory_config"]["expires_at"])):
        result["stage"] = "blocked"
        result["gaps"].append(gap("collection-window-expired"))
        return _result(result)
    result["gaps"] = [g for g in result["gaps"] if g["code"] not in {"collection-window-pending", "collection-window-not-ready"}]
    pool = _Clients(context, clients, transport)
    for provider in ("kubernetes", "argocd"):
        for target in context["intent"]["targets"]:
            report = _read(provider, target, pool, now)
            result["targets"][target["id"]]["after"][provider] = report
            result["gaps"].extend(_provider_gaps(report, target["id"]))
    for target in context["intent"]["targets"]:
        target_id = target["id"]
        item = result["targets"][target_id]
        bindings, gaps = _phase_bindings(target, item["after"], "after")
        item["bindings"].update(bindings)
        result["gaps"].extend(gaps)
        comparison = _image_comparison(_image(item["before"]), _image(item["after"]), window)
        item["bindings"]["image_interval"] = comparison
        if not comparison["stable"] or not comparison["intervals_valid"]:
            result["gaps"].append(gap("image-bracket-incomplete-or-changed", target_id))
        before = item["before"].get("argocd", {}).get("observations", {}).get("application", {})
        after = item["after"].get("argocd", {}).get("observations", {}).get("application", {})
        identity_stable = bool(before.get("uid") and all(before.get(k) == after.get(k)
                                for k in ("uid", "generation", "spec_sha256")))
        item["bindings"]["argo_identity_stable"] = identity_stable
        if not identity_stable:
            result["gaps"].append(gap("argo-identity-changed-or-missing", target_id))
        before_uid = item["bindings"].get("before", {}).get("service_uid", {}).get("observed")
        after_uid = item["bindings"]["after"]["service_uid"]["observed"]
        if not before_uid or before_uid != after_uid:
            result["gaps"].append(gap("service-identity-changed-or-missing", target_id))
        if "prometheus" in target:
            if clock(now).timestamp() > window["end"] + window["max_sample_age_seconds"]:
                report = _missing("prometheus", "collection-window-expired", now)
            else:
                report = _read("prometheus", target, pool, now, window=window,
                    images={"before": _image(item["before"]), "after": _image(item["after"])},
                    delivered_at=delivery["binding"]["delivered_at"] if delivery is not None else None)
            item["prometheus"] = report
            result["gaps"].extend(_provider_gaps(report, target_id))
    result["stage"] = "measured"
    result["gaps"].append(gap("recovery-contract-not-evaluated"))
    result["observed_at"] = clock(now).isoformat()
    return _result(result)
