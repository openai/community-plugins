"""Deterministic offline observations and hypotheses, never causal proof.

Only explicit bundle Paths are read. Historical embedded paths are not reopened;
the caller must authenticate their raw hashes against its immutable journal.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path
import re

from devsecops.factory.contracts import DIGEST, NAME, array, document, object_schema, read_json, validate_document
from .collection import COLLECTION_SCHEMA, _image_comparison, _phase_bindings, _window
from .context import (
    CANONICALIZATION, IDENTITIES, MAX_BUNDLE, canonical_bytes, digest, parse_time,
    seal, validate, validate_intent, verify_seal,
)

STATES = ["up", "down", "progressing", "flat", "reset", "unknown", "ready", "not-ready",
          "Synced", "OutOfSync", "Healthy", "Degraded", "Progressing", "Missing", "Suspended", "Unknown",
          "unchanged", "changed", "not-observed"]
ERRORS = ["CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "ContainerCreating", "CreateContainerConfigError",
          "OOMKilled", "Completed", "Error"]
VALUE = {"anyOf": [{"enum": STATES}, {"type": "null"}, {"type": "integer", "minimum": 0},
                    array({"enum": ERRORS}, maximum=8)]}
REF = object_schema({"input": {"enum": ["baseline", "fault"]}, "pointer": {"type": "string", "maxLength": 2048}, "sha256": DIGEST})
GAP = object_schema({"code": NAME, "required": {"const": True}, "input": {"enum": ["baseline", "fault"]}, "target_id": NAME},
                    ["code", "required"])
INPUT = object_schema({"state": {"enum": ["valid", "missing", "invalid"]}, "sha256": DIGEST,
    "encoding": {"enum": ["raw-json", CANONICALIZATION]}, "bundle_sha256": DIGEST, "context_sha256": DIGEST}, ["state"])
FACT_KINDS = ["scrape-state", "counter-progression", "kubernetes-readiness", "argo-sync", "argo-health", "container-errors",
              "ready-endpoints", "service-selector", "service-ports", "network-policy", "runtime-identity", "deployment-generation"]
FACT = object_schema({"id": NAME, "target_id": NAME, "kind": {"enum": FACT_KINDS},
    "baseline": VALUE, "fault": VALUE, "comparison": {"enum": ["changed", "unchanged", "unavailable"]},
    "evidence": array(REF, maximum=4)})
ALTERNATIVE_IDS = ["network-policy", "discovery-or-selector", "application", "dns", "mesh-or-certificate"]
CONTROL_IDS = ["effective-policy-enforcement", "authorized-positive-and-negative-probes", "namespace-and-pod-labels",
    "monitoring-discovery", "endpoint-listener", "dns-resolution", "mesh-enrollment-and-authorization",
    "certificate-and-transport", "direct-application-http", "independent-functional-probe"]
ALTERNATIVE = object_schema({"target_id": NAME, "id": {"enum": ALTERNATIVE_IDS},
    "rank": {"type": ["integer", "null"], "minimum": 1, "maximum": 5},
    "evidence_status": {"enum": ["observed-support", "no-direct-support", "comparison-incomplete"]},
    "supporting_fact_ids": array(NAME, maximum=20), "counterevidence_fact_ids": array(NAME, maximum=20),
    "required_controls": array({"enum": CONTROL_IDS}, minimum=1, maximum=10), "causality": {"const": "unproven"}})
DIAGNOSIS_SCHEMA = document("runtime-diagnosis", {
    "schema_version": {"const": 1}, "kind": {"const": "runtime-diagnosis"},
    "observation_kind": {"const": "baseline-fault-comparison"}, "execution_status": {"const": "partial"},
    "decision": {"const": "unknown"}, "comparison_status": {"enum": ["aligned", "incomplete", "incompatible"]},
    "causality": {"const": "unproven"}, "recovery": {"const": "not-evaluated"}, "http_health": {"const": "not-observed"},
    "confirmed_failure": {"type": "boolean"}, "inputs": object_schema({"baseline": INPUT, "fault": INPUT}),
    "identities": {"anyOf": [IDENTITIES, {"type": "null"}]},
    "observations": array(FACT, maximum=100), "alternatives": array(ALTERNATIVE, maximum=40),
    "policy_changes": array(object_schema({"target_id": NAME, "namespace": NAME, "name": NAME,
        "baseline_sha256": {"type": ["string", "null"], "pattern": "^[a-f0-9]{64}$"},
        "fault_sha256": {"type": ["string", "null"], "pattern": "^[a-f0-9]{64}$"},
        "applicability": {"enum": ["selected", "not-selected", "unknown"]}}), maximum=1600),
    "source_changes": array(object_schema({"target_id": NAME, "position": {"type": "integer", "minimum": 1, "maximum": 32},
        "baseline_revision": {"type": "string", "pattern": "^(?:[a-f0-9]{40}|[a-f0-9]{64})$"},
        "fault_revision": {"type": "string", "pattern": "^(?:[a-f0-9]{40}|[a-f0-9]{64})$"},
        "baseline_source_spec_sha256": {"type": ["string", "null"], "pattern": "^[a-f0-9]{64}$"},
        "fault_source_spec_sha256": {"type": ["string", "null"], "pattern": "^[a-f0-9]{64}$"}}), maximum=256),
    "retained_failures": array(object_schema({"input": {"enum": ["baseline", "fault"]}, "target_id": NAME,
        "provider": {"enum": ["kubernetes", "argocd", "prometheus"]},
        "phase": {"enum": ["before", "after", "window"]},
        "fact": {"enum": ["workload-not-ready", "controller-out-of-sync", "controller-degraded", "scrape-down", "counter-flat"]},
        "freshness": {"enum": ["fresh-at-collection", "historical-window-only", "not-assessed"]},
        "evidence": REF}), maximum=200),
    "gaps": array(GAP, maximum=3000), "diagnosis_sha256": DIGEST,
})


def _gap(result, code, label=None, target=None):
    item = {"code": code, "required": True}
    if label:
        item["input"] = label
    if target:
        item["target_id"] = target
    if item not in result["gaps"]:
        result["gaps"].append(item)


def _ref(label, pointer, value):
    return {"input": label, "pointer": pointer, "sha256": digest(value)}


def _load(value):
    if value is None:
        return None, {"state": "missing"}
    artifact = {"state": "invalid"}
    try:
        if isinstance(value, Path):
            path = value.absolute()
            before = path.stat()
            data, sha = read_json(path, limit=MAX_BUNDLE)
            after = path.stat()
            keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, k) != getattr(after, k) for k in keys):
                raise ValueError
            artifact.update(sha256=sha, encoding="raw-json")
        elif isinstance(value, dict):
            raw = canonical_bytes(value)
            data = deepcopy(value)
            artifact.update(sha256=hashlib.sha256(raw).hexdigest(), encoding=CANONICALIZATION)
        else:
            raise ValueError
        validate(data, COLLECTION_SCHEMA)
        verify_seal(data, "bundle_sha256")
        context = data["context_snapshot"]
        verify_seal(context, "context_sha256")
        validate_document(context["factory_config"], "factory-config")
        validate_document(context["factory_lock"], "factory-lock")
        validate_intent(context["intent"])
        for name, field in (("config", "factory_config"), ("lock", "factory_lock"), ("intent", "intent")):
            if digest(context[field]) != context["input_artifacts"][name]["canonical_sha256"]:
                raise ValueError
        intent, config, lock = context["intent"], context["factory_config"], context["factory_lock"]
        identities = intent["identities"]
        if (data["context_sha256"] != context["context_sha256"] or data["identities"] != identities
                or any(identities[k] != lock[k] for k in ("scenario_sha256", "assertions_sha256"))
                or any(identities[k + "_sha256"] != context["input_artifacts"][k]["sha256"] for k in ("config", "lock"))
                or config["environment"] != intent["environment"] or not config["profile"] == lock["profile"] == intent["profile"]
                or intent["scenario_id"] != lock["scenario_id"]
                or set(data["targets"]) != {t["id"] for t in intent["targets"]}):
            raise ValueError
        if data["window"] is not None and data["window"] != _window(context, data["targets"]):
            raise ValueError
        artifact.update(state="valid", bundle_sha256=data["bundle_sha256"], context_sha256=data["context_sha256"])
        return data, artifact
    except (ValueError, OSError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        return None, artifact


def _integer(value):
    return type(value) is int and 0 <= value <= 2**53


def _name(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value) is not None


def _uid(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is not None


def _owner(value, kind, uid):
    owners = value.get("owners")
    if not isinstance(owners, list) or len(owners) > 32:
        return False
    controllers = [o for o in owners if isinstance(o, dict) and o.get("controller") is True]
    return len(controllers) == 1 and controllers[0].get("kind") == kind and controllers[0].get("uid") == uid


def _policy_semantics(value):
    # NetworkPolicy selectors, rules, peers and ports are unordered sets/unions.
    if isinstance(value, dict):
        return {k: _policy_semantics(v) for k, v in value.items()}
    if isinstance(value, list):
        items = {}
        for raw in value:
            item = _policy_semantics(raw)
            items[digest(item)] = item
        return [items[key] for key in sorted(items)]
    return value


def _kube(report, target):
    """Reconstruct narrow readiness and concrete owner/image identity from fields."""
    unknown = {"readiness": "unknown", "identity": None, "generation": None, "errors": [], "endpoints": None,
               "selector": None, "ports": None, "policies": None, "labels": None}
    try:
        spec = target["kubernetes"]
        if report.get("schema_version") != 1 or report.get("provider") != "kubernetes" or report.get("scope") != {
                k: spec[k] for k in ("namespace", "deployment", "service")}:
            return unknown
        obs = report["observations"]
        dep, svc = obs["deployment"], obs["service"]
        if (any(item.get("namespace") != spec["namespace"] for item in (dep, svc))
                or dep.get("name") != spec["deployment"] or svc.get("name") != spec["service"]
                or not _uid(dep.get("uid")) or not _uid(svc.get("uid"))):
            return unknown
        result = deepcopy(unknown)
        selector = dep.get("selector", {})
        labels = selector.get("matchLabels") if selector.get("matchExpressions") == [] else None
        if isinstance(labels, dict) and labels and all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()):
            result["labels"] = labels
        if isinstance(svc.get("selector"), dict):
            result["selector"] = digest(svc["selector"])
        if isinstance(svc.get("ports"), list) and len(svc["ports"]) <= 32:
            result["ports"] = digest(sorted(svc["ports"], key=digest))
        generation = dep.get("generation")
        if _integer(generation) and generation > 0:
            result["generation"] = generation
        sets, pods, slices = obs["replicasets"], obs["pods"], obs["endpoint_slices"]
        if any(not isinstance(v, list) or len(v) > 100 for v in (sets, pods, slices)):
            return result
        set_ids = {s["uid"] for s in sets if _uid(s.get("uid")) and _owner(s, "Deployment", dep["uid"])}
        if len(set_ids) != len(sets) or not pods:
            return result
        rows, owners, errors, ready_pods, pod_uids, pod_members = [], [], set(), 0, set(), {}
        for pod in pods:
            uid = pod.get("uid")
            if (not _uid(uid) or uid in pod_uids or not _name(pod.get("name"))
                    or pod.get("namespace") != spec["namespace"]):
                return result
            pod_uids.add(uid)
            pod_members[uid] = (pod["name"], pod.get("pod_ips", []))
            owner_ids = [rs for rs in set_ids if _owner(pod, "ReplicaSet", rs)]
            if len(owner_ids) != 1:
                return result
            owners.append((uid, owner_ids[0]))
            containers = pod.get("containers")
            if not isinstance(containers, list) or not 1 <= len(containers) <= 32:
                return result
            for container in containers:
                if container.get("reason") in ERRORS:
                    errors.add(container["reason"])
                rows.append({"pod_uid": uid, "pod_name": pod["name"], "container": container.get("name"),
                    "container_type": container.get("container_type"), "image_id": container.get("image_id"),
                    "restart_count": container.get("restart_count")})
            if (pod.get("phase") == "Running" and pod.get("deleting") is False
                    and {"type": "Ready", "status": "True"} in pod.get("conditions", [])
                    and all(c.get("ready") is True for c in containers if c.get("container_type") == "regular")):
                ready_pods += 1
        result["errors"] = sorted(errors)
        snapshot = obs["image_snapshot"]
        # Reuse the collection validator, then require the snapshot to describe the actual normalized Pods.
        def ordering(container):
            return container["pod_uid"], container["container_type"], container["container"]
        containers = sorted(snapshot["containers"], key=ordering)
        if (containers != sorted(rows, key=ordering) or snapshot["deployment_uid"] != dep["uid"]
                or snapshot["namespace"] != spec["namespace"] or snapshot["service"] != spec["service"]):
            return result
        if not _image_comparison(snapshot, snapshot, {"start": 0, "end": 0, "max_sample_age_seconds": 1})["stable"]:
            return result
        if (dep["uid"] != spec.get("deployment_uid") or svc["uid"] != spec.get("service_uid")
                or generation != spec.get("generation")):
            return result
        result["identity"] = {"deployment_uid": dep["uid"], "service_uid": svc["uid"], "pod_owners": sorted(owners), "containers": containers}
        endpoints = []
        for item in slices:
            if not _owner(item, "Service", svc["uid"]) or not isinstance(item.get("endpoints"), list):
                return result
            for endpoint in item["endpoints"]:
                member = pod_members.get(endpoint.get("pod_uid"))
                if (member is None or endpoint.get("pod_name") != member[0] or type(endpoint.get("ready")) is not bool
                        or not isinstance(endpoint.get("addresses"), list) or not endpoint["addresses"]
                        or any(address not in member[1] for address in endpoint["addresses"])):
                    return result
                endpoints.append(endpoint)
        ready_endpoints = sum(e["ready"] and not e.get("terminating") for e in endpoints)
        result["endpoints"] = ready_endpoints
        numbers = [dep.get(k) for k in ("desired_replicas", "updatedReplicas", "readyReplicas", "availableReplicas")]
        if (all(_integer(v) for v in numbers) and generation == dep.get("observed_generation")
                and spec.get("generation") == generation):
            desired, updated, ready, available = numbers
            bad_condition = any(c in dep.get("conditions", []) for c in (
                {"type": "ReplicaFailure", "status": "True"}, {"type": "Progressing", "status": "False"}, {"type": "Available", "status": "False"}))
            result["readiness"] = ("ready" if desired > 0 and not dep.get("deleting") and updated == desired
                and ready >= desired and available >= desired and ready_pods >= desired and ready_endpoints and not bad_condition else "not-ready")
        policies = obs.get("network_policies")
        if isinstance(policies, list) and len(policies) <= 100 and all(
                isinstance(p, dict) and _name(p.get("name")) and p.get("namespace") == spec["namespace"]
                and all(k in p for k in ("pod_selector", "policy_types", "ingress", "egress")) for p in policies):
            if len({p["name"] for p in policies}) == len(policies):
                result["policies"] = {p["name"]: _policy_semantics({k: p[k] for k in ("pod_selector", "policy_types", "ingress", "egress")}) for p in policies}
        return result
    except (TypeError, KeyError, ValueError, AttributeError):
        return unknown


def _argo(report, target, freshness):
    unknown = {"sync": "Unknown", "health": "Unknown", "identity": None}
    try:
        wanted = target["argocd"]
        scope = report["scope"]
        if (report.get("schema_version") != 1 or report.get("provider") != "argocd"
                or any(scope.get(k) != wanted[k] for k in ("project", "application_namespace", "application"))):
            return unknown
        obs = report["observations"]
        identity = obs["application"]
        age = (parse_time(report["observed_at"]) - parse_time(identity["reconciled_at"])).total_seconds()
        _, gaps = _phase_bindings(target, {"argocd": report}, "after")
        source_positions = [s["position"] for s in obs["sources"]]
        revisions = {s["position"]: s["revision"] for s in wanted["sources"]}
        if (not 0 <= age <= freshness or not _uid(identity.get("uid"))
                or not _integer(identity.get("generation")) or identity["generation"] < 1
                or len(source_positions) != len(set(source_positions)) or set(source_positions) != set(revisions)
                or any(s.get("observed_revision") != revisions[s["position"]] for s in obs["sources"])
                or any(g["code"].startswith("argo-") for g in gaps)
                or any(obs.get("comparison", {}).get(k) is not True for k in
                       ("identity_stable", "source_spec_matches", "destination_matches", "comparison_inputs_match"))):
            return unknown
        return {"sync": obs["sync_status"] if obs.get("sync_status") in {"Synced", "OutOfSync", "Unknown"} else "Unknown",
            "health": obs["health_status"] if obs.get("health_status") in {"Healthy", "Degraded", "Progressing", "Missing", "Suspended", "Unknown"} else "Unknown",
            "identity": {k: identity[k] for k in ("uid", "generation", "spec_sha256")}}
    except (ValueError, TypeError, KeyError, AttributeError):
        return unknown


def _samples(value, window, *, counter=False):
    if not isinstance(value, list) or not window["minimum_fresh_samples"] <= len(value) <= 241:
        return None
    previous, result = None, []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"source_timestamp", "value"}:
            return None
        timestamp, number = item["source_timestamp"], item["value"]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (timestamp, number)):
            return None
        if (not window["start"] <= timestamp <= window["end"]
                or (previous is not None and not 0 < timestamp - previous <= window["max_sample_age_seconds"])
                or (not 0 <= number <= 2**53 if counter else number not in (0, 1))):
            return None
        result.append((timestamp, number))
        previous = timestamp
    if window["end"] - result[-1][0] > window["max_sample_age_seconds"]:
        return None
    return result


def _metrics(report, target, window, result, label):
    unknown = {"scrape": "unknown", "counter": "unknown", "window_scrape": "unknown", "window_counter": "unknown"}
    if not isinstance(report, dict) or "prometheus" not in target or window is None:
        if "prometheus" in target:
            _gap(result, "telemetry-evidence-missing", label, target["id"])
        return unknown
    try:
        expected = {k: v for k, v in target["prometheus"].items() if k != "service_id"}
        if report.get("provider") != "prometheus" or report.get("schema_version") != 1 or report.get("scope") != expected:
            raise ValueError
        obs = report["observations"]
        if any(obs.get("window", {}).get(k) != window[k] for k in ("start", "end", "step_seconds", "max_sample_age_seconds", "minimum_fresh_samples")):
            raise ValueError
        up = _samples(obs.get("scrape", {}).get("samples"), window)
        counter = _samples(obs.get("functional", {}).get("samples"), window, counter=True)
        if obs.get("functional", {}).get("metric") != "mission_events_total":
            counter = None
        historical = {"window_scrape": "down" if up and any(v == 0 for _, v in up) else "up" if up else "unknown",
                      "window_counter": "unknown"}
        if up and counter and [t for t, _ in up] == [t for t, _ in counter]:
            changes = [b[1] - a[1] for a, b in zip(counter, counter[1:])]
            historical["window_counter"] = "reset" if any(d < 0 for d in changes) else "progressing" if all(d > 0 for d in changes) else "flat"
        finished = parse_time(report["transport"]["finished_at"]).timestamp()
        for key, samples in (("up", up), ("counter", counter)):
            if samples and (finished < window["end"] or finished - samples[-1][0] > window["max_sample_age_seconds"]):
                _gap(result, "telemetry-stale-at-collection", label, target["id"])
                if key == "up":
                    up = None
                else:
                    counter = None
        result_state = {"scrape": "down" if up and any(v == 0 for _, v in up) else "up" if up else "unknown", "counter": "unknown", **historical}
        if up is None or counter is None:
            _gap(result, "telemetry-samples-incomplete", label, target["id"])
        elif [t for t, _ in up] != [t for t, _ in counter]:
            _gap(result, "telemetry-source-window-mismatch", label, target["id"])
        else:
            changes = [b[1] - a[1] for a, b in zip(counter, counter[1:])]
            result_state["counter"] = "reset" if any(d < 0 for d in changes) else "progressing" if all(d > 0 for d in changes) else "flat"
            if result_state["counter"] == "reset":
                _gap(result, "telemetry-counter-reset", label, target["id"])
        return result_state
    except (ValueError, TypeError, KeyError, AttributeError):
        _gap(result, "telemetry-evidence-invalid", label, target["id"])
        return unknown


def _inspect(bundle, label, result):
    context, window = bundle["context_snapshot"], bundle["window"]
    targets, state = context["intent"]["targets"], {}
    origins = {s["id"]: s["origin"].rstrip("/") for s in context["factory_config"]["services"]}
    if bundle["stage"] != "measured" or window is None or not bundle["pending_sha256"]:
        _gap(result, "collection-not-measured", label)
    if window:
        observed = parse_time(bundle["observed_at"]).timestamp()
        if not window["end"] <= observed <= window["end"] + window["max_sample_age_seconds"]:
            _gap(result, "measurement-window-stale-or-early", label)
    for source_gap in bundle["gaps"]:
        _gap(result, source_gap["code"], label, source_gap.get("target_id"))
    for target in targets:
        target_id = target["id"]
        _gap(result, "image-mapping-authentication-unverified", label, target_id)
        if "prometheus" in target:
            _gap(result, "telemetry-target-binding-unverified", label, target_id)
        row = bundle["targets"][target_id]
        if sorted(row["roles"]) != sorted(target["roles"]):
            _gap(result, "target-role-binding-mismatch", label, target_id)
        info = {"kubernetes": {}, "argocd": {}, "metrics": None, "image_stable": False, "argo_stable": False}
        for phase in ("before", "after"):
            for provider in ("kubernetes", "argocd"):
                report = row[phase].get(provider, {})
                if provider == "argocd" and report.get("scope", {}).get("origin", "").rstrip("/") != origins.get(target[provider]["service_id"]):
                    _gap(result, "provider-origin-mismatch", label, target_id)
                    report = {}
                if report.get("execution_status") != "complete" or any(g.get("required", True) for g in report.get("gaps", []) if isinstance(g, dict)):
                    _gap(result, "provider-evidence-incomplete", label, target_id)
                if not report.get("transport", {}).get("receipts"):
                    _gap(result, "provider-transport-receipts-missing", label, target_id)
                values = (_kube(report, target) if provider == "kubernetes" else
                          _argo(report, target, context["factory_lock"]["freshness_seconds"]))
                info[provider][phase] = values
                if provider == "kubernetes" and values["readiness"] == "unknown":
                    _gap(result, "workload-readiness-evidence-incomplete", label, target_id)
                if provider == "argocd" and (values["sync"] == "Unknown" or values["health"] == "Unknown"):
                    _gap(result, "controller-state-evidence-incomplete", label, target_id)
                ref = _ref(label, f"/targets/{target_id}/{phase}/{provider}", report)
                failures = []
                if provider == "kubernetes" and values["readiness"] == "not-ready":
                    failures.append("workload-not-ready")
                if provider == "argocd":
                    if values["sync"] == "OutOfSync":
                        failures.append("controller-out-of-sync")
                    if values["health"] in {"Degraded", "Missing"}:
                        failures.append("controller-degraded")
                result["retained_failures"].extend({"input": label, "target_id": target_id, "provider": provider,
                    "phase": phase, "fact": fact, "freshness": "not-assessed", "evidence": ref} for fact in failures)
        before, after = info["kubernetes"]["before"], info["kubernetes"]["after"]
        if window:
            snapshots = [row[p].get("kubernetes", {}).get("observations", {}).get("image_snapshot") for p in ("before", "after")]
            comparison = _image_comparison(*snapshots, window)
            info["image_stable"] = bool(before["identity"] and before["identity"] == after["identity"]
                and comparison["stable"] and comparison["intervals_valid"])
        if not info["image_stable"]:
            _gap(result, "workload-ownership-or-image-evidence-incomplete", label, target_id)
        argo_before, argo_after = [info["argocd"][p]["identity"] for p in ("before", "after")]
        info["argo_stable"] = bool(argo_before and argo_before == argo_after)
        if not info["argo_stable"]:
            _gap(result, "controller-identity-or-source-evidence-incomplete", label, target_id)
        if before["policies"] != after["policies"]:
            _gap(result, "policy-state-changed-within-window", label, target_id)
        info["metrics"] = _metrics(row["prometheus"], target, window, result, label)
        if "prometheus" in target and isinstance(row["prometheus"], dict) and (
                row["prometheus"].get("execution_status") != "complete"
                or any(g.get("required", True) for g in row["prometheus"].get("gaps", []) if isinstance(g, dict))):
            _gap(result, "provider-evidence-incomplete", label, target_id)
        for key, failed, fact in (("scrape", "down", "scrape-down"), ("counter", "flat", "counter-flat")):
            if info["metrics"]["window_" + key] == failed:
                result["retained_failures"].append({"input": label, "target_id": target_id, "provider": "prometheus", "phase": "window",
                    "fact": fact, "freshness": "fresh-at-collection" if info["metrics"][key] == failed else "historical-window-only",
                    "evidence": _ref(label, f"/targets/{target_id}/prometheus", row["prometheus"])})
        state[target_id] = info
    return state


def _scope(target):
    argo = target["argocd"]
    return {"id": target["id"], "roles": sorted(target["roles"]),
        "kubernetes": {k: v for k, v in target["kubernetes"].items() if k != "generation"},
        "argocd": {k: argo.get(k) for k in ("service_id", "project", "application_namespace", "application", "destination", "destination_spec_sha256")},
        "sources": sorted([{k: s[k] for k in ("position", "repository_url", "path", "renderer")} for s in argo["sources"]], key=lambda s: s["position"]),
        "prometheus": target.get("prometheus")}


def _applicability(selector, labels):
    if not isinstance(selector, dict) or not isinstance(labels, dict):
        return "unknown"
    match, expressions = selector.get("matchLabels"), selector.get("matchExpressions")
    if not isinstance(match, dict) or not isinstance(expressions, list):
        return "unknown"
    unresolved = False
    for key, value in match.items():
        if key in labels and labels[key] != value:
            return "not-selected"
        if key not in labels:
            unresolved = True
    for expression in expressions:
        if not isinstance(expression, dict):
            return "unknown"
        key, operator, values = expression.get("key"), expression.get("operator"), expression.get("values", [])
        if not isinstance(key, str) or operator not in {"In", "NotIn", "Exists", "DoesNotExist"} or not isinstance(values, list):
            return "unknown"
        if key not in labels:
            unresolved = True
        elif (operator == "DoesNotExist" or operator == "In" and labels[key] not in values
              or operator == "NotIn" and labels[key] in values):
            return "not-selected"
    return "unknown" if unresolved else "selected"


def _fact(result, target_id, kind, baseline, fault, refs, aligned):
    unavailable = baseline is None or fault is None or baseline in ("unknown", "Unknown") or fault in ("unknown", "Unknown")
    item = {"id": f"fact-{len(result['observations']) + 1:03d}", "target_id": target_id, "kind": kind,
        "baseline": baseline, "fault": fault,
        "comparison": "unavailable" if not aligned or unavailable else "unchanged" if baseline == fault else "changed", "evidence": refs}
    result["observations"].append(item)
    return item


def _alternatives(result, target_id, facts, selected_policy_change, aligned):
    controls = {"network-policy": ["effective-policy-enforcement", "authorized-positive-and-negative-probes", "namespace-and-pod-labels"],
        "discovery-or-selector": ["monitoring-discovery", "endpoint-listener"],
        "application": ["direct-application-http", "independent-functional-probe", "endpoint-listener"],
        "dns": ["dns-resolution"], "mesh-or-certificate": ["mesh-enrollment-and-authorization", "certificate-and-transport"]}
    support = {key: [] for key in ALTERNATIVE_IDS}
    counter = {key: [] for key in ALTERNATIVE_IDS}
    outage = facts["scrape-state"]["baseline"] == "up" and facts["scrape-state"]["fault"] == "down"
    if aligned and selected_policy_change and outage:
        support["network-policy"].append(facts["network-policy"]["id"])
    for kind in ("service-selector", "service-ports", "ready-endpoints"):
        if facts[kind]["comparison"] == "changed":
            support["discovery-or-selector"].append(facts[kind]["id"])
    if facts["kubernetes-readiness"]["fault"] == "not-ready":
        support["application"].append(facts["kubernetes-readiness"]["id"])
    elif facts["kubernetes-readiness"]["fault"] == "ready":
        counter["application"].append(facts["kubernetes-readiness"]["id"])
    if facts["container-errors"]["fault"]:
        support["application"].append(facts["container-errors"]["id"])
    if facts["counter-progression"]["fault"] == "flat" and facts["scrape-state"]["fault"] == "up":
        support["application"].append(facts["counter-progression"]["id"])
    scores = {key: len(value) for key, value in support.items()}
    ranks = {score: index + 1 for index, score in enumerate(sorted(set(scores.values()) - {0}, reverse=True))}
    for alternative in sorted(ALTERNATIVE_IDS, key=lambda a: (-scores[a], ALTERNATIVE_IDS.index(a))):
        result["alternatives"].append({"target_id": target_id, "id": alternative,
            "rank": ranks[scores[alternative]] if scores[alternative] else None,
            "evidence_status": "observed-support" if support[alternative] else "no-direct-support" if aligned else "comparison-incomplete",
            "supporting_fact_ids": support[alternative], "counterevidence_fact_ids": counter[alternative],
            "required_controls": controls[alternative], "causality": "unproven"})


def diagnose_runtime(baseline_bundle, fault_bundle):
    """Return a deterministic, fail-closed comparison without any provider reads."""
    result = {"schema_version": 1, "kind": "runtime-diagnosis", "observation_kind": "baseline-fault-comparison",
        "execution_status": "partial", "decision": "unknown", "comparison_status": "incomplete", "causality": "unproven",
        "recovery": "not-evaluated", "http_health": "not-observed", "confirmed_failure": False,
        "inputs": {}, "identities": None, "observations": [], "alternatives": [], "policy_changes": [], "source_changes": [],
        "retained_failures": [], "gaps": []}
    bundles, states = {}, {}
    for label, value in (("baseline", baseline_bundle), ("fault", fault_bundle)):
        bundle, artifact = _load(value)
        result["inputs"][label] = artifact
        bundles[label] = bundle
        if bundle is None:
            _gap(result, "input-" + artifact["state"], label)
            states[label] = {}
            continue
        if bundle['execution_mode'] != 'direct':
            _gap(result, 'controlled-test-evidence', label)
        try:
            states[label] = _inspect(bundle, label, result)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
            _gap(result, "provider-evidence-invalid", label)
            states[label] = {}
    for code in ("immutable-journal-authentication-unverified", "environment-inventory-authentication-unverified",
                 "source-intent-authentication-unverified", "causality-not-proven", "direct-http-evidence-missing",
                 "independent-probe-controls-missing", "monitoring-discovery-evidence-missing", "dns-controls-missing",
                 "mesh-certificate-evidence-missing", "effective-policy-enforcement-unverified"):
        _gap(result, code)
    baseline, fault = bundles["baseline"], bundles["fault"]
    compatible = baseline is not None and fault is not None
    if compatible:
        old, new = baseline["context_snapshot"]["intent"], fault["context_snapshot"]["intent"]
        if (baseline["identities"] != fault["identities"] or any(old[k] != new[k] for k in ("run_id", "attempt_id", "environment", "scenario_id", "profile"))
                or any(baseline["context_snapshot"][key] != fault["context_snapshot"][key] for key in ("factory_config", "factory_lock"))):
            _gap(result, "foreign-or-changed-run-identity")
            compatible = False
        if old["phase"] != "baseline" or new["phase"] != "fault":
            _gap(result, "baseline-fault-phase-mismatch")
            compatible = False
        old_targets, new_targets = {t["id"]: t for t in old["targets"]}, {t["id"]: t for t in new["targets"]}
        if set(old_targets) != set(new_targets) or any(_scope(old_targets[k]) != _scope(new_targets[k]) for k in old_targets.keys() & new_targets.keys()):
            _gap(result, "target-scope-or-source-topology-changed")
            compatible = False
        if (baseline["stage"] != "measured" or fault["stage"] != "measured" or not baseline["window"] or not fault["window"]
                or baseline["window"]["end"] >= fault["window"]["start"]):
            _gap(result, "windows-not-ordered-and-disjoint")
            compatible = False
        if compatible and parse_time(baseline["observed_at"]).timestamp() > fault["window"]["start"]:
            _gap(result, "observation-intervals-overlap")
            compatible = False
        if compatible:
            result["identities"] = deepcopy(baseline["identities"])
            for target_id in sorted(old_targets):
                old_sources = {s["position"]: s for s in old_targets[target_id]["argocd"]["sources"]}
                for source in new_targets[target_id]["argocd"]["sources"]:
                    previous = old_sources[source["position"]]
                    if source["revision"] != previous["revision"] or source.get("source_spec_sha256") != previous.get("source_spec_sha256"):
                        result["source_changes"].append({"target_id": target_id, "position": source["position"],
                            "baseline_revision": previous["revision"], "fault_revision": source["revision"],
                            "baseline_source_spec_sha256": previous.get("source_spec_sha256"),
                            "fault_source_spec_sha256": source.get("source_spec_sha256")})
    else:
        old_targets, new_targets = {}, {}
    if baseline and fault and not compatible:
        result["comparison_status"] = "incompatible"
    target_ids = sorted(set(states["baseline"]) | set(states["fault"]))
    all_aligned = compatible
    for target_id in target_ids:
        old_state, new_state = states["baseline"].get(target_id), states["fault"].get(target_id)
        aligned = bool(compatible and old_state and new_state and old_state["image_stable"] and new_state["image_stable"]
            and old_state["argo_stable"] and new_state["argo_stable"]
            and old_state["kubernetes"]["after"]["identity"] == new_state["kubernetes"]["before"]["identity"]
            and old_state["kubernetes"]["after"]["generation"] == new_state["kubernetes"]["before"]["generation"]
            and old_state["argocd"]["after"]["identity"]["uid"] == new_state["argocd"]["before"]["identity"]["uid"])
        if not aligned:
            _gap(result, "cross-window-workload-or-controller-identity-unverified", target=target_id)
            all_aligned = False
        for state, target in ((old_state, old_targets.get(target_id)), (new_state, new_targets.get(target_id))):
            if state and target and "prometheus" in target and (
                    state["metrics"]["scrape"] == "unknown" or state["metrics"]["counter"] in {"unknown", "reset"}):
                all_aligned = False
        facts = {}
        def observation(kind, key, provider):
            values, refs = [], []
            for label, state in (("baseline", old_state), ("fault", new_state)):
                data = state[provider]["after"] if state and provider != "metrics" else state["metrics"] if state else {}
                value = data.get(key) if data else None
                if provider == "metrics":
                    pointer = f"/targets/{target_id}/prometheus"
                    payload = bundles[label]["targets"][target_id]["prometheus"] if state else None
                else:
                    pointer = f"/targets/{target_id}/after/{provider}"
                    payload = bundles[label]["targets"][target_id]["after"].get(provider) if state else None
                values.append(value)
                if payload is not None:
                    refs.append(_ref(label, pointer, payload))
            facts[kind] = _fact(result, target_id, kind, *values, refs, aligned)
        for kind, key, provider in (("scrape-state", "scrape", "metrics"), ("counter-progression", "counter", "metrics"),
            ("kubernetes-readiness", "readiness", "kubernetes"), ("argo-sync", "sync", "argocd"), ("argo-health", "health", "argocd"),
            ("container-errors", "errors", "kubernetes"), ("ready-endpoints", "endpoints", "kubernetes"),
            ("deployment-generation", "generation", "kubernetes")):
            observation(kind, key, provider)
        for kind, key in (("service-selector", "selector"), ("service-ports", "ports"), ("network-policy", "policies")):
            old_value = old_state["kubernetes"]["after"].get(key) if old_state else None
            new_value = new_state["kubernetes"]["after"].get(key) if new_state else None
            state = "unknown" if old_value is None or new_value is None or not aligned else "unchanged" if old_value == new_value else "changed"
            refs = [_ref(label, f"/targets/{target_id}/after/kubernetes", bundles[label]["targets"][target_id]["after"].get("kubernetes", {}))
                    for label, item in (("baseline", old_state), ("fault", new_state)) if item]
            facts[kind] = _fact(result, target_id, kind, "unchanged" if old_value is not None else "unknown", state, refs, aligned)
        identity_refs = [_ref(label, f"/targets/{target_id}/{phase}/kubernetes", bundles[label]["targets"][target_id][phase].get("kubernetes", {}))
                         for label, state in (("baseline", old_state), ("fault", new_state)) if state for phase in ("before", "after")]
        facts["runtime-identity"] = _fact(result, target_id, "runtime-identity", "unchanged" if old_state and old_state["image_stable"] else "unknown",
            "unchanged" if aligned else "changed" if old_state and new_state and old_state["image_stable"] and new_state["image_stable"] else "unknown", identity_refs, aligned)
        selected_policy_change = False
        if aligned:
            old_policies, new_policies = [s["kubernetes"]["after"]["policies"] for s in (old_state, new_state)]
            if old_policies is not None and new_policies is not None:
                for name in sorted(set(old_policies) | set(new_policies)):
                    before, after = old_policies.get(name), new_policies.get(name)
                    if before == after:
                        continue
                    applicability = [_applicability(p["pod_selector"], s["kubernetes"]["after"]["labels"])
                        for p, s in ((before, old_state), (after, new_state)) if p is not None]
                    applies = "selected" if "selected" in applicability else "unknown" if "unknown" in applicability else "not-selected"
                    result["policy_changes"].append({"target_id": target_id, "namespace": new_targets[target_id]["kubernetes"]["namespace"], "name": name,
                        "baseline_sha256": digest(before) if before is not None else None,
                        "fault_sha256": digest(after) if after is not None else None, "applicability": applies})
                    selected_policy_change |= applies == "selected"
        _alternatives(result, target_id, facts, selected_policy_change, aligned)
    if any(g["code"] in {"provider-evidence-incomplete", "provider-evidence-invalid", "provider-transport-receipts-missing",
                          "measurement-window-stale-or-early", "policy-state-changed-within-window", "target-role-binding-mismatch",
                          "workload-readiness-evidence-incomplete", "controller-state-evidence-incomplete"} for g in result["gaps"]):
        all_aligned = False
    if all_aligned and target_ids:
        result["comparison_status"] = "aligned"
    result["confirmed_failure"] = bool(result["retained_failures"])
    result = seal(result, "diagnosis_sha256")
    validate(result, DIAGNOSIS_SCHEMA)
    return result
