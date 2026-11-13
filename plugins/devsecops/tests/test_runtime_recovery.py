"""Adapter-composed recovery evidence; injected origins stay controlled-test."""
from copy import deepcopy
from datetime import timedelta, datetime
import hashlib
import json
from pathlib import Path
import socket

import pytest

from test_runtime_collection import setup, clients_for, finish
from test_runtime_context import NOW
from test_kubernetes_adapter import POLICIES
from test_http_probe import PROBE, request as http_request, BODY
from devsecops.runtime import start_runtime_collection
from devsecops.runtime.context import UTC, canonical_bytes, digest, seal, validate, verify_seal
from devsecops.runtime.recovery import (
    ASSURANCES, OriginVerification, RECOVERY_SCHEMA, RECOVERY_CONTRACT_SCHEMA,
    RECOVERY_INVENTORY_SCHEMA, GIT_DELIVERY_SCHEMA, NATIVE_EXECUTION_SCHEMA, verify_recovery,
)

RUN = "12345678-1234-1234-1234-123456789abc"
REQUEST = "abcdef12-1234-1234-1234-123456789abc"
IMAGE = "containerd://sha256:" + "a" * 64


def at(seconds):
    return (NOW + timedelta(seconds=seconds)).isoformat()


def tree(text):
    raw = text.encode()
    row = {"path": "deploy/policy.yaml", "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    value = hashlib.sha256((row["path"] + "\0" + row["sha256"] + "\n").encode()).hexdigest()
    return {"sha256": value, "file_count": 1, "bytes": len(raw), "files": [row]}


class TestProvenance:
    __test__ = False
    def verify(self, *, artifacts):
        return OriginVerification("f" * 64, {k: hashlib.sha256(v).hexdigest() for k, v in artifacts.items()}, ASSURANCES)


def fixture():
    patch = b"diff --git a/deploy/policy.yaml b/deploy/policy.yaml\n--- a/deploy/policy.yaml\n+++ b/deploy/policy.yaml\n@@ -1 +1 @@\n-wrong\n+correct\n"
    reviewed = {"schema_version": 1, "id": "restore-metrics", "base_commit": "b" * 40, "profile_sha256": "5" * 64,
        "allowed_files": ["deploy/policy.yaml"], "allowed_rendered_changes": [{"resource": "networking.k8s.io/NetworkPolicy/demo/mission-metrics", "pointers": ["/spec/ingress"]}]}
    native_request = {"schema_version": 1, "request_id": REQUEST, "run_id": RUN, "step_id": "primary-correct",
        "workflow": "prepare-gitops-correction", "identities": {}, "created_at": at(91), "expires_at": at(110),
        "required_artifact_kind": "patch", "input_artifacts": [{"name": "correction-contract", "path": "/inert/contract", "sha256": digest(reviewed), "bytes": len(canonical_bytes(reviewed))}], "result_path": "/inert/result.json", "output_directory": "/inert"}
    correction = {"schema_version": 1, "execution_status": "complete", "gaps": [], "static_review_decision": "pass", "observed_at": at(100), "contract_sha256": digest(reviewed),
        "source": {"base_commit": "b" * 40, "profile_sha256": "5" * 64}, "patch_sha256": hashlib.sha256(patch).hexdigest(),
        "patch_bytes": len(patch), "changed_files": ["deploy/policy.yaml"], "base_tree": tree("wrong"), "patched_tree": tree("correct")}
    contexts, bundles, policies = {}, {}, {}
    for phase, offset, revision in (("baseline", 0, "a"), ("fault", 60, "b"), ("recovery", 120, "c")):
        def change(config, lock, intent):
            intent.update(run_id=RUN, phase=phase)
            target = intent["targets"][0]
            target["argocd"]["sources"][0]["revision"] = revision * 40
        context, clients = setup(now=NOW + timedelta(seconds=offset), mutate=change)
        contexts[phase] = context
        native_request["identities"] = deepcopy(context["intent"]["identities"])
        def modify(client_map):
            for (target, provider), client in client_map.items():
                if target == "mission" and provider == "argocd":
                    client.data["status"]["sync"]["revision"] = revision * 40
                    client.data["status"]["operationState"]["syncResult"]["revision"] = revision * 40
                if target == "mission" and provider == "kubernetes":
                    policy = client.data[POLICIES]["items"][0]
                    policy["spec"]["ingress"][0]["from"][0]["podSelector"] = {"matchLabels": {"app": "prometheus"}}
                    if phase == "fault":
                        policy["spec"]["ingress"] = []
                    policies[phase] = deepcopy(policy)
                if target == "mission" and provider == "prometheus":
                    client.prom_options = {"up": 0 if phase == "fault" else 1}
        modify(clients)
        native = {"schema_version": 1, "request_id": REQUEST, "run_id": RUN, "step_id": "primary-correct", "request_sha256": digest(native_request),
            "status": "completed", "artifacts": [{"name": "proposal", "kind": "patch", "path": "/inert/patch", "sha256": hashlib.sha256(patch).hexdigest(), "bytes": len(patch)}], "gaps": []}
        delivery = {"schema_version": 1, "kind": "runtime-delivery-binding", "run_id": RUN, "attempt_id": "primary",
            "environment": context["intent"]["environment"], "identities": deepcopy(context["intent"]["identities"]),
            "target_id": "mission", "source_position": 1, "delivered_revision": "c" * 40, "delivered_at": at(110),
            "native_handoff_sha256": digest(native_request), "patch_sha256": hashlib.sha256(patch).hexdigest(),
            "candidate_tree_sha256": correction["patched_tree"]["sha256"], "validation_receipt_sha256": digest(correction)}
        supplied_delivery = delivery if phase == "recovery" else None
        pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW + timedelta(seconds=offset), delivery_receipt=supplied_delivery)
        end = datetime.fromtimestamp(pending["window"]["end"], UTC)
        after = clients_for(context, end)
        modify(after)
        bundles[phase] = finish(context, pending, clients=after, delivery_receipt=supplied_delivery)
    cases, probes = [], {}
    for phase, offset in (("baseline", 0), ("fault", 60), ("recovery", 120)):
        matrix = [("mission-health", "mission", "allowed", 8080, "/health", 3),
                  ("reference-health", "reference", "allowed", 8080, "/", 4)]
        if phase != "fault":
            matrix += [("metrics-allowed-before", "mission", "allowed", 9090, "/metrics", 6),
                ("metrics-denied-wrong-namespace", "mission", "wrong-namespace", 9090, "/metrics", 8),
                ("metrics-denied-wrong-label", "mission", "wrong-label", 9090, "/metrics", 10),
                ("metrics-allowed-after", "mission", "allowed", 9090, "/metrics", 12)]
        for kind, target, source, port, path, seconds in matrix:
            case = {"id": phase + "-" + kind, "phase": phase, "kind": kind, "target_id": target, "source_id": source,
                "port": port, "path": path, "body_sha256": hashlib.sha256(BODY).hexdigest() if kind.endswith("health") else None}
            cases.append(case)
            req = http_request()
            req.update(run_id=RUN, attempt_id="primary", case_id=case["id"], phase=phase, identities=deepcopy(native_request["identities"]))
            req["source"]["expected"] = {"namespace": "other" if source == "wrong-namespace" else "monitoring", "name": source, "uid": "pod-" + source}
            req["destination"].update(namespace="demo", service=target, port=port, path=path,
                expected_addresses=["10.96.0.7" if target == "mission" else "10.96.0.8"])
            req["expectation"]["body_sha256"] = case["body_sha256"]
            # Use the actual producer to establish the complete receipt envelope,
            # then supply explicit controlled observation fixtures for offline logic.
            receipt = PROBE.probe(req, _environ={})
            receipt.update(started_at=at(offset+seconds), finished_at=at(offset+seconds+.1), elapsed_seconds=.1)
            receipt["source_identity"].update(matched=True, claims=deepcopy(req["source"]["expected"]))
            negative = kind.startswith("metrics-denied-")
            receipt["network"] = {"host": target + ".demo.svc.cluster.local", "dns_addresses": req["destination"]["expected_addresses"],
                                  "connected_peer": None if negative else req["destination"]["expected_addresses"][0]}
            if negative:
                receipt["observation"].update(outcome="connect-timeout", http_decision="unknown")
            else:
                receipt["execution_status"] = "complete"
                receipt["observation"].update(outcome="http-response", http_decision="pass", status=200, body_bytes=len(BODY),
                    body_sha256=hashlib.sha256(BODY).hexdigest(), body_sha256_matched=True if case["body_sha256"] else None)
            probes["http:" + case["id"]] = seal(receipt, "receipt_sha256")
    common = {k: deepcopy(contexts["recovery"]["intent"][k]) for k in ("run_id", "attempt_id", "environment", "identities")}
    lock = contexts["recovery"]["factory_lock"]
    contract = {"schema_version": 1, "id": "mission-recovery", **common, "factory_lock": deepcopy(lock),
        "intents": {p: deepcopy(contexts[p]["intent"]) for p in bundles}, "mission_target": "mission", "reference_target": "reference",
        "delivery_target": "mission", "delivery_source_position": 1, "correction_contract_sha256": digest(reviewed), "probe_cases": cases,
        "metrics_authorization": {"namespace_labels": {"kubernetes.io/metadata.name": "monitoring"}, "pod_labels": {"app": "prometheus"}}}
    inventory = {"schema_version": 1, **common, "cluster_uid": "cluster-one",
        "kubernetes_origin_sha256": digest(next(s["origin"] for s in contexts["recovery"]["factory_config"]["services"] if s["id"] == "kubernetes")),
        "argo_destination_sha256": digest(contract["intents"]["recovery"]["targets"][0]["argocd"]["destination"]),
        "services": [{"target_id": t, "uid": "service-1" if t == "mission" else "service-reference", "cluster_ips": [ip],
            "cluster_domain": "cluster.local", "observed_before": at(-1), "observed_after": at(160)} for t, ip in (("mission", "10.96.0.7"), ("reference", "10.96.0.8"))],
        "probe_sources": [{"id": name, "namespace": "other" if name == "wrong-namespace" else "monitoring", "name": name,
            "uid": "pod-" + name, "namespace_uid": "ns-other" if name == "wrong-namespace" else "ns-monitoring",
            "pod_labels": {"app": "wrong" if name == "wrong-label" else "prometheus"},
            "namespace_labels": {"kubernetes.io/metadata.name": "other" if name == "wrong-namespace" else "monitoring"},
            "image_id": IMAGE, "observed_before": at(-1), "observed_after": at(160)} for name in ("allowed", "wrong-namespace", "wrong-label")],
        "images": [{"target_id": t, "container": t, "container_type": "regular", "runtime_image_id": IMAGE, "manifest_digest": "sha256:" + "a"*64} for t in ("mission", "reference")],
        "telemetry": {"target_id": "mission", "service_uid": "service-1", "pod_uid": "pod-1", "job": "mission", "instance": "10.0.0.4:9090"},
        "topology": {"cni": lock["platform"]["cni"], "mesh_mode": lock["platform"]["mesh_mode"], "probe_transport": "plain-http-networkpolicy", "qualification_sha256": "e"*64}}
    evidence = {"inventory": inventory, "delivery": delivery, "native_request": native_request, "native_result": native,
        "native_validation": {"schema_version": 1, "request_sha256": digest(native_request), "result_sha256": digest(native), "checks": ["patch-validated"],
            "coverage": "artifact_validation_only", "scenario_assertion_status": "not_evaluated", "validated_at": at(101)},
        "native_execution": {**common, "schema_version": 1, "kind": "runtime-native-execution", "host_id": "test-host", "turn_id": "test-turn", "trace_sha256": "8"*64, "request_sha256": digest(native_request), "result_sha256": digest(native), "patch_sha256": hashlib.sha256(patch).hexdigest(),
            "package_sha256": common["identities"]["package_sha256"], "run_id": RUN, "attempt_id": "primary", "started_at": at(92), "finished_at": at(95)},
        "patch": patch, "correction": correction, "correction_contract": reviewed,
        "git_delivery": {**common, "schema_version": 1, "kind": "runtime-git-delivery", "rendered_artifact_sha256": "7"*64, "repository_url": "https://example.test/mission.git", "revision": "c"*40, "source_path": "deploy",
            "selected_tree": deepcopy(correction["patched_tree"]), "observed_at": at(111)},
        "rendered_before": {"resources": [{"id": "networking.k8s.io/NetworkPolicy/demo/mission-metrics", "body": policies["fault"]}]},
        "rendered_after": {"resources": [{"id": "networking.k8s.io/NetworkPolicy/demo/mission-metrics", "body": policies["recovery"]}]}, **probes}
    contract["mission_application_container"] = "mission"
    contract["reference_application_container"] = "reference"
    contract["image_expectations"] = []
    for image in inventory["images"]:
        target = next(t for t in contract["intents"]["recovery"]["targets"] if t["id"] == image["target_id"])
        wanted = lock["images"]["reference_web"] if image["target_id"] == "reference" else {"platform_manifest_sha256": "a"*64, "config_sha256": "b"*64}
        image.update(manifest_digest="sha256:"+wanted["platform_manifest_sha256"], config_digest="sha256:"+wanted["config_sha256"], mapping_receipt_sha256=target["image_mapping_receipt"]["sha256"])
        contract["image_expectations"].append({k: image[k] for k in ("target_id", "container", "container_type", "mapping_receipt_sha256")} | {k: wanted[k] for k in ("platform_manifest_sha256", "config_sha256")})
    for phase, artifact in (("fault", "rendered_before"), ("recovery", "rendered_after")):
        bundle = bundles[phase]
        context = bundle["context_snapshot"]
        context["intent"]["targets"][0]["argocd"]["sources"][0]["rendered_artifact_sha256"] = digest(evidence[artifact])
        contract["intents"][phase] = deepcopy(context["intent"])
        context["input_artifacts"]["intent"]["sha256"] = digest(context["intent"])
        context["input_artifacts"]["intent"]["canonical_sha256"] = digest(context["intent"])
        bundle["context_snapshot"] = seal(context, "context_sha256")
        bundle["context_sha256"] = bundle["context_snapshot"]["context_sha256"]
        bundles[phase] = seal(bundle, "bundle_sha256")
    evidence["git_delivery"]["rendered_artifact_sha256"] = digest(evidence["rendered_after"])
    correction['rendered_export'] = {
        'status': 'complete', 'encoding': 'devsecops-runtime-json-sha256-v1',
        'input_binding': {'base_commit': correction['source']['base_commit'],
            'base_tree_sha256': correction['base_tree']['sha256'],
            'candidate_tree_sha256': correction['patched_tree']['sha256'],
            'profile_sha256': correction['source']['profile_sha256'],
            'contract_sha256': correction['contract_sha256'], 'patch_sha256': correction['patch_sha256']},
        'artifacts': {filename: {'sha256': digest(evidence[name]), 'bytes': len(canonical_bytes(evidence[name]))}
                      for filename, name in (('rendered-before.json', 'rendered_before'),
                                             ('rendered-after.json', 'rendered_after'))}}
    delivery['validation_receipt_sha256'] = digest(correction)
    bundles['recovery']['delivery']['binding'] = deepcopy(delivery)
    bundles['recovery']['delivery']['artifact']['sha256'] = digest(delivery)
    bundles['recovery']['delivery']['artifact']['canonical_sha256'] = digest(delivery)
    bundles['recovery'] = seal(bundles['recovery'], 'bundle_sha256')
    return bundles, contract, evidence


@pytest.fixture
def evidence_set():
    return fixture()


def run(value, *, auth=True, now=None):
    bundles, contract, evidence = value
    return verify_recovery(bundles["baseline"], bundles["fault"], bundles["recovery"], contract=contract, evidence=evidence,
        _test_provenance=TestProvenance() if auth else None, now=now or NOW + timedelta(seconds=155))


def condition(result, id, phase=None, target=None):
    return next(c for c in result["conditions"] if c["id"] == id and (phase is None or c["phase"] == phase) and (target is None or c.get("target_id") == target))


def test_complete_measured_conditions_are_useful_but_test_origin_never_live_pass(evidence_set, monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("Offline verifier cannot read a provider"))
    result = run(evidence_set)
    validate(result, RECOVERY_SCHEMA)
    verify_seal(result, "recovery_sha256")
    assert result["execution_mode"] == "controlled-test" and result["decision"] == "unknown"
    assert result["gaps"] == [{"code": "controlled-test-evidence", "required": True}], result["gaps"]
    assert all(c["observed_state"] == "recovered" for c in result["conditions"]), result["conditions"]
    assert result["causality"] == "unproven" and result["recovery"] == "unknown"
    assert any(f["code"] == "historical-window-observation" for f in result["retained_failures"])
    assert not result["confirmed_failure"]
    assert "sensitive-value-must-not-escape" not in json.dumps(result)
    assert all(c["evidence"] for c in result["conditions"])


def test_unavailable_provenance_does_not_hide_observed_successes(evidence_set):
    result = run(evidence_set, auth=False)
    assert result["provenance"]["verified"] is False and result["decision"] == "unknown"
    assert any(g["code"] == "evidence-origin-authentication-unavailable" for g in result["gaps"])
    assert condition(result, "functional-telemetry", "recovery")["observed_state"] == "recovered"
    assert not any(c["authenticated"] for c in result["conditions"])


@pytest.mark.parametrize('damage', ['missing', 'changed-render-hash', 'foreign-input-binding'])
def test_render_pair_must_be_exported_by_the_same_bound_correction(evidence_set, damage):
    correction = evidence_set[2]['correction']
    if damage == 'missing':
        correction.pop('rendered_export')
    elif damage == 'changed-render-hash':
        correction['rendered_export']['artifacts']['rendered-after.json']['sha256'] = 'f' * 64
    else:
        correction['rendered_export']['input_binding']['candidate_tree_sha256'] = 'e' * 64
    # Even rebinding the outer report hash cannot authenticate a different render.
    evidence_set[2]['delivery']['validation_receipt_sha256'] = digest(correction)
    assert condition(run(evidence_set), 'native-patch-delivery-chain')['observed_state'] == 'unknown'


@pytest.mark.parametrize("name", ["inventory", "delivery", "git_delivery", "native_request", "native_result", "native_validation", "native_execution", "patch", "correction", "correction_contract", "rendered_before", "rendered_after"])
def test_each_missing_independent_artifact_is_explicit_unknown(evidence_set, name):
    evidence_set[2].pop(name)
    result = run(evidence_set)
    assert result["decision"] == "unknown"
    assert {"code": "evidence-missing", "required": True, "input": name} in result["gaps"]
    assert condition(result, "functional-telemetry", "recovery")["observed_state"] == "recovered"


@pytest.mark.parametrize("phase", ["baseline", "fault", "recovery"])
def test_missing_collection_is_not_synthesized(evidence_set, phase):
    evidence_set[0][phase] = None
    result = run(evidence_set)
    assert result["decision"] == "unknown"
    assert {"code": "collection-missing-or-invalid", "required": True, "input": phase} in result["gaps"]


@pytest.mark.parametrize("identity", ["scenario_sha256", "assertions_sha256", "package_sha256", "lock_sha256", "config_sha256", "environment_sha256"])
def test_foreign_identity_never_joins_a_chain_even_if_resealed(evidence_set, identity):
    value = evidence_set[0]["recovery"]
    value["identities"][identity] = "9" * 64
    evidence_set[0]["recovery"] = seal(value, "bundle_sha256")
    result = run(evidence_set)
    assert any(g["code"] == "collection-missing-or-invalid" for g in result["gaps"])


@pytest.mark.parametrize("name,field,value", [
    ("delivery", "patch_sha256", "9"*64),
    ("delivery", "native_handoff_sha256", "9"*64),
    ("delivery", "validation_receipt_sha256", "9"*64),
    ("delivery", "candidate_tree_sha256", "9"*64),
    ("delivery", "delivered_revision", "9"*40),
    ("git_delivery", "revision", "9"*40),
    ("git_delivery", "repository_url", "https://other.invalid/project.git"),
    ("git_delivery", "attempt_id", "replay"),
    ("native_request", "workflow", "review-kubernetes-change"),
    ("native_result", "request_sha256", "9"*64),
    ("native_execution", "attempt_id", "replay"),
    ("native_execution", "finished_at", at(120)),
    ("native_validation", "coverage", "live-success"),
    ("correction", "patch_bytes", 1),
])
def test_native_patch_and_delivery_links_are_recomputed(evidence_set, name, field, value):
    evidence_set[2][name][field] = value
    result = run(evidence_set)
    assert condition(result, "native-patch-delivery-chain")["observed_state"] == "unknown"


def test_substituted_patch_cannot_inherit_success_flags(evidence_set):
    evidence_set[2]["patch"] = b"a replacement fixture patch"
    evidence_set[2]["correction"]["decision"] = "pass"
    result = run(evidence_set)
    assert condition(result, "native-patch-delivery-chain")["observed_state"] == "unknown"


@pytest.mark.parametrize("kind", ["missing-file", "changed-image", "outside-pointer", "no-change"])
def test_rendered_scope_recomputed_from_actual_bodies(evidence_set, kind):
    evidence = evidence_set[2]
    before, after = evidence["rendered_before"], evidence["rendered_after"]
    if kind == "missing-file":
        after["resources"] = []
    elif kind == "changed-image":
        after["resources"][0]["body"]["spec"]["image"] = "wrong"
    elif kind == "outside-pointer":
        after["resources"][0]["body"]["metadata"]["labels"]["extra"] = "wrong"
    else:
        after["resources"] = deepcopy(before["resources"])
    result = run(evidence_set)
    assert condition(result, "rendered-change-scope")["observed_state"] != "recovered"


@pytest.mark.parametrize("fault", ["flat", "reset", "same-timestamp", "wrong-series-time", "old-source-time", "nan", "wrong-service"])
def test_healthy_summary_does_not_replace_real_source_samples(evidence_set, fault):
    bundle = evidence_set[0]["recovery"]
    report = bundle["targets"]["mission"]["prometheus"]
    report.update(decision="pass", confirmed_failure=False)
    obs = report["observations"]
    if fault in {"flat", "reset"}:
        for i, row in enumerate(obs["functional"]["samples"]):
            row["value"] = 10 if fault == "flat" else 10-i
    elif fault == "same-timestamp":
        for row in obs["scrape"]["samples"]:
            row["source_timestamp"] = obs["scrape"]["samples"][0]["source_timestamp"]
    elif fault == "wrong-series-time":
        obs["functional"]["samples"][0]["source_timestamp"] += 1
    elif fault == "old-source-time":
        for row in obs["scrape"]["samples"]:
            row["source_timestamp"] -= 60
    elif fault == "nan":
        obs["functional"]["samples"][0]["value"] = "NaN"
    else:
        report["scope"]["service"] = "other"
    evidence_set[0]["recovery"] = seal(bundle, "bundle_sha256")
    result = run(evidence_set)
    assert condition(result, "functional-telemetry", "recovery")["observed_state"] != "recovered"
    if fault == "flat":
        assert result["confirmed_failure"]


def test_stale_recovery_retains_historical_down_observation(evidence_set):
    bundle = evidence_set[0]["recovery"]
    report = bundle["targets"]["mission"]["prometheus"]
    for row in report["observations"]["scrape"]["samples"]:
        row["value"] = 0
    report["transport"]["finished_at"] = at(200)
    report["observed_at"] = at(200)
    evidence_set[0]["recovery"] = seal(bundle, "bundle_sha256")
    result = run(evidence_set, now=NOW+timedelta(seconds=205))
    assert condition(result, "functional-telemetry", "recovery")["observed_state"] == "unknown"
    assert any(f["id"] == "historical-down" and f["phase"] == "recovery" for f in result["retained_failures"])


@pytest.mark.parametrize("key", ["image_id", "restart_count"])
def test_concrete_runtime_image_or_restart_change_retained_as_failure(evidence_set, key):
    bundle = evidence_set[0]["recovery"]
    obs = bundle["targets"]["mission"]["after"]["kubernetes"]["observations"]
    value = "containerd://sha256:" + "9" * 64 if key == "image_id" else 1
    obs["pods"][0]["containers"][0][key] = value
    obs["image_snapshot"]["containers"][0][key] = value
    evidence_set[0]["recovery"] = seal(bundle, "bundle_sha256")
    result = run(evidence_set)
    assert condition(result, "runtime-images-and-ownership", target="mission")["observed_state"] == "not-recovered"
    assert result["confirmed_failure"]


def test_wrong_http_status_retained_despite_forged_pass(evidence_set):
    name = "http:recovery-mission-health"
    receipt = evidence_set[2][name]
    receipt["observation"].update(status=503, http_decision="pass", outcome="http-response")
    evidence_set[2][name] = seal(receipt, "receipt_sha256")
    result = run(evidence_set)
    assert condition(result, "mission-health", "recovery")["observed_state"] == "not-recovered"
    assert result["confirmed_failure"] and result["decision"] == "unknown"


@pytest.mark.parametrize("fault", ["uid", "source-namespace", "dns", "peer", "before-delivery", "body", "missing-terminator-outcome", "unsealed"])
def test_http_source_destination_timing_and_payload_checked(evidence_set, fault):
    name = "http:recovery-mission-health"
    receipt = evidence_set[2][name]
    if fault == "uid":
        receipt["source_identity"]["claims"]["uid"] = "other-pod"
    elif fault == "source-namespace":
        receipt["source_identity"]["claims"]["namespace"] = "other"
    elif fault == "dns":
        receipt["network"]["dns_addresses"] = ["10.96.0.9"]
    elif fault == "peer":
        receipt["network"]["connected_peer"] = "10.96.0.9"
    elif fault == "before-delivery":
        receipt["started_at"], receipt["finished_at"] = at(100), at(101)
    elif fault == "body":
        receipt["observation"]["body_sha256"] = "9"*64
    elif fault == "missing-terminator-outcome":
        receipt["observation"].update(outcome="invalid-http-response", http_decision="pass")
    else:
        receipt["elapsed_seconds"] += .01
    if fault != "unsealed":
        evidence_set[2][name] = seal(receipt, "receipt_sha256")
    result = run(evidence_set)
    assert condition(result, "mission-health", "recovery")["observed_state"] != "recovered"


def test_negative_probe_http_response_is_failure_even_with_unknown_summary(evidence_set):
    name = "http:recovery-metrics-denied-wrong-label"
    receipt = evidence_set[2][name]
    receipt["observation"].update(status=200, http_decision="unknown")
    evidence_set[2][name] = seal(receipt, "receipt_sha256")
    result = run(evidence_set)
    assert condition(result, "metrics-denied-wrong-label", "recovery")["observed_state"] == "not-recovered"


def test_timeout_without_positive_bracketing_is_unknown_control(evidence_set):
    name = "http:recovery-metrics-allowed-after"
    receipt = evidence_set[2][name]
    receipt.update(started_at=at(127), finished_at=at(127.1))
    evidence_set[2][name] = seal(receipt, "receipt_sha256")
    result = run(evidence_set)
    assert condition(result, "metrics-denied-wrong-label", "recovery")["observed_state"] == "recovered"
    assert condition(result, "scoped-control-bracketing", "recovery")["observed_state"] == "unknown"
    assert result["causality"] == "unproven"


def test_broad_ingress_rule_cannot_be_hidden_by_successful_negative_probe_claim(evidence_set):
    bundle = evidence_set[0]["recovery"]
    for side in ("before", "after"):
        policies = bundle["targets"]["mission"][side]["kubernetes"]["observations"]["network_policies"]
        policies[0]["ingress"] = [{}]
    evidence_set[0]["recovery"] = seal(bundle, "bundle_sha256")
    result = run(evidence_set)
    assert condition(result, "metrics-policy-selector-controls", "recovery")["observed_state"] == "not-recovered"


@pytest.mark.parametrize("field", ["cluster_uid", "probe_transport"])
def test_unsupported_or_missing_independent_inventory_stays_unknown(evidence_set, field):
    inventory = evidence_set[2]["inventory"]
    if field == "cluster_uid":
        inventory.pop(field)
    else:
        inventory["topology"][field] = "unsupported"
    result = run(evidence_set)
    assert any(g["code"] in {"independent-inventory-missing-or-mismatch", "topology-or-transport-unsupported"} for g in result["gaps"])


@pytest.mark.parametrize("damage", ["missing-assurance", "wrong-bytes", "exception"])
def test_provenance_callback_must_bind_every_frozen_byte_and_assurance(evidence_set, damage):
    class Broken:
        def verify(self, *, artifacts):
            if damage == "exception":
                raise RuntimeError("SECRET-SENTINEL")
            answer = TestProvenance().verify(artifacts=artifacts)
            return OriginVerification(answer.receipt_sha256,
                dict(answer.artifact_sha256) if damage != "wrong-bytes" else {},
                answer.assurances if damage != "missing-assurance" else answer.assurances[:-1])
    bundles, contract, evidence = evidence_set
    result = verify_recovery(*[bundles[p] for p in ("baseline", "fault", "recovery")], contract=contract, evidence=evidence,
        _test_provenance=Broken(), now=NOW+timedelta(seconds=155))
    assert result["provenance"]["verified"] is False
    assert "SECRET-SENTINEL" not in json.dumps(result)


def test_explicit_paths_preserve_raw_hashes_and_embedded_paths_are_inert(evidence_set, tmp_path):
    bundles, contract, evidence = evidence_set
    for key in ("native_request", "native_result"):
        assert "/inert" in json.dumps(evidence[key])
    paths = {}
    for name, value in bundles.items():
        path = tmp_path / (name + ".json")
        raw = json.dumps(value, indent=2).encode()
        path.write_bytes(raw)
        paths[name] = path
    result = verify_recovery(paths["baseline"], paths["fault"], paths["recovery"], contract=contract, evidence=evidence,
        _test_provenance=TestProvenance(), now=NOW+timedelta(seconds=155))
    assert result["inputs"]["baseline"]["sha256"] == hashlib.sha256(paths["baseline"].read_bytes()).hexdigest()
    assert all(c["observed_state"] == "recovered" for c in result["conditions"])


@pytest.mark.parametrize("bad", [b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{}' + b' '*16_000_001])
def test_invalid_bounded_json_does_not_become_evidence(evidence_set, tmp_path, bad):
    path = tmp_path / "bad.json"
    path.write_bytes(bad)
    evidence_set[2]["inventory"] = path
    result = run(evidence_set)
    assert result["inputs"]["inventory"]["state"] == "invalid"


def test_symlink_input_not_followed(evidence_set, tmp_path):
    real = tmp_path / "real.json"
    real.write_bytes(canonical_bytes(evidence_set[2]["inventory"]))
    link = tmp_path / "link.json"
    link.symlink_to(real)
    evidence_set[2]["inventory"] = link
    assert run(evidence_set)["inputs"]["inventory"]["state"] == "invalid"


@pytest.mark.parametrize("damage", ["missing-probe", "reference-revision", "arbitrary-scenario", "extra-field"])
def test_operator_contract_cannot_silently_weaken_canonical_checks(evidence_set, damage):
    contract = evidence_set[1]
    if damage == "missing-probe":
        contract["probe_cases"].pop()
    elif damage == "reference-revision":
        contract["intents"]["recovery"]["targets"][1]["argocd"]["sources"][0]["revision"] = "9"*40
    elif damage == "arbitrary-scenario":
        contract["intents"]["recovery"]["scenario_id"] = "other"
    else:
        contract["skip_checks"] = True
    with pytest.raises(ValueError, match="contract is invalid"):
        run(evidence_set)


def test_exported_schemas_match_code():
    root = Path(__file__).parents[1]
    for name, value in (("runtime-recovery", RECOVERY_SCHEMA), ("runtime-recovery-contract", RECOVERY_CONTRACT_SCHEMA), ("runtime-recovery-inventory", RECOVERY_INVENTORY_SCHEMA), ("runtime-git-delivery", GIT_DELIVERY_SCHEMA), ("runtime-native-execution", NATIVE_EXECUTION_SCHEMA)):
        assert json.loads((root / "schemas" / (name + ".schema.json")).read_bytes()) == value


def v2_execution(evidence):
    execution = deepcopy(evidence['native_execution'])
    execution.pop('turn_id')
    execution.update(schema_version=2, driver='codex-cli-single-turn',
        execution_id='12345678-1234-4123-8123-123456789abc', execution_mode='direct',
        authority_scope='native-execution-and-artifact-validation-only', thread_id='test-observed-thread',
        turn_ordinal=1, server_turn_id=None, request_id=evidence['native_request']['request_id'],
        step_id=evidence['native_request']['step_id'], workflow='prepare-gitops-correction',
        configured_model='test-model', configured_reasoning_effort='ultra', client_version='codex-cli 0.144.3',
        executable_sha256='d'*64, engine_python_identity_sha256='d'*64,
        invocation_sha256='d'*64, driver_execution_sha256='d'*64, execution_manifest_sha256='d'*64,
        validator_identity_sha256='d'*64, validation_receipt_sha256=digest(evidence['native_validation']),
        validated_at=evidence['native_validation']['validated_at'])
    return execution


def test_v2_native_document_is_compatible_offline_but_cannot_authenticate_itself(evidence_set):
    evidence_set[2]['native_execution'] = v2_execution(evidence_set[2])
    result = run(evidence_set, auth=False)
    assert condition(result, 'native-patch-delivery-chain')['observed_state'] == 'recovered'
    assert result['decision'] == 'unknown' and not result['provenance']['verified']
    assert not any(row['authenticated'] for row in result['conditions'])


@pytest.mark.parametrize('field,value', [('turn_ordinal', 2), ('server_turn_id', 'fabricated-provider-turn'),
    ('validation_receipt_sha256', '9'*64), ('request_id', 'different-request'),
    ('step_id', 'different-step'), ('workflow', 'review-kubernetes-change')])
def test_v2_native_binding_mismatch_or_fabricated_turn_cannot_link_delivery(evidence_set, field, value):
    evidence_set[2]['native_execution'] = v2_execution(evidence_set[2])
    evidence_set[2]['native_execution'][field] = value
    result = run(evidence_set)
    assert condition(result, 'native-patch-delivery-chain')['observed_state'] == 'unknown'


def test_imported_v2_cannot_be_passed_as_native_origin(evidence_set):
    bundles, contract, evidence = evidence_set
    evidence['native_execution'] = v2_execution(evidence)
    with pytest.raises(ValueError, match='directly issued'):
        verify_recovery(bundles['baseline'], bundles['fault'], bundles['recovery'], contract=contract,
                        evidence=evidence, native_execution_origin=evidence['native_execution'])


def test_current_freshness_is_checked_for_each_source_series(evidence_set):
    bundle = evidence_set[0]["recovery"]
    report = bundle["targets"]["mission"]["prometheus"]
    samples = report["observations"]["functional"]["samples"]
    for sample in samples:
        sample["source_timestamp"] -= 20
    evidence_set[0]["recovery"] = seal(bundle, "bundle_sha256")
    result = run(evidence_set, now=NOW+timedelta(seconds=180))
    assert condition(result, "post-delivery-sample-freshness", "recovery")["observed_state"] == "unknown"


@pytest.mark.parametrize("field", ["manifest_digest", "config_digest", "mapping_receipt_sha256"])
def test_stable_runtime_id_does_not_allow_foreign_desired_content_mapping(evidence_set, field):
    image = evidence_set[2]["inventory"]["images"][0]
    image[field] = ("sha256:" if field.endswith("digest") else "") + "9"*64
    result = run(evidence_set)
    assert condition(result, "runtime-images-and-ownership", target="mission")["observed_state"] == "recovered"
    assert condition(result, "independent-inventory-association")["observed_state"] == "unknown"


def test_reference_application_must_match_locked_ironbank_content(evidence_set):
    image = next(i for i in evidence_set[1]["image_expectations"] if i["target_id"] == "reference")
    image["platform_manifest_sha256"] = "9"*64
    with pytest.raises(ValueError, match="contract is invalid"):
        run(evidence_set)


def test_policy_selector_using_unobserved_extra_pod_label_is_not_excluded(evidence_set):
    bundle = evidence_set[0]["recovery"]
    for side in ("before", "after"):
        policies = bundle["targets"]["mission"][side]["kubernetes"]["observations"]["network_policies"]
        extra = deepcopy(policies[0])
        extra.update(name="possible-broad-policy", uid="other-policy")
        extra["pod_selector"]["matchLabels"] = {"extra-label": "unknown"}
        extra["ingress"] = [{}]
        policies.append(extra)
    evidence_set[0]["recovery"] = seal(bundle, "bundle_sha256")
    result = run(evidence_set)
    assert condition(result, "metrics-policy-selector-controls", "recovery")["observed_state"] == "unknown"


def test_rendered_inventory_identity_cannot_lie_about_body_kind(evidence_set):
    evidence_set[2]["rendered_after"]["resources"][0]["body"]["kind"] = "Secret"
    result = run(evidence_set)
    assert condition(result, "rendered-change-scope")["observed_state"] == "unknown"


def test_hidden_selected_file_change_does_not_inherit_a_reported_allowed_list(evidence_set):
    evidence = evidence_set[2]
    evidence["correction"]["changed_files"] = ["different.yaml"]
    result = run(evidence_set)
    assert condition(result, "native-patch-delivery-chain")["observed_state"] == "unknown"


def test_no_command_or_provenance_backend_loaded_from_evidence(evidence_set):
    bundles, contract, evidence = evidence_set
    for value in ("python:arbitrary", {}, True):
        with pytest.raises(ValueError, match="adapter is unavailable"):
            verify_recovery(*[bundles[p] for p in ("baseline", "fault", "recovery")], contract=contract, evidence=evidence, provenance=value)
    evidence["run_shell"] = "echo SECRET-SENTINEL"
    with pytest.raises(ValueError, match="evidence names are invalid"):
        run(evidence_set)


def test_repository_qualified_runtime_digest_is_preserved(evidence_set):
    replacement = "docker-pullable://registry.example.invalid/demo/image@sha256:" + "a"*64
    def replace(value):
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return replacement if value == IMAGE else value
    bundles, contract, evidence = evidence_set
    for phase in bundles:
        bundles[phase] = seal(replace(bundles[phase]), "bundle_sha256")
    evidence["inventory"] = replace(evidence["inventory"])
    result = run((bundles, contract, evidence))
    assert condition(result, "independent-inventory-association")["observed_state"] == "recovered"
    assert condition(result, "runtime-images-and-ownership", target="mission")["observed_state"] == "recovered"


@pytest.mark.parametrize("series", ["scrape", "functional"])
def test_empty_source_series_returns_unknown_without_crashing(evidence_set, series):
    bundle = evidence_set[0]["recovery"]
    bundle["targets"]["mission"]["prometheus"]["observations"][series]["samples"] = []
    evidence_set[0]["recovery"] = seal(bundle, "bundle_sha256")
    assert condition(run(evidence_set), "post-delivery-sample-freshness", "recovery")["observed_state"] == "unknown"


@pytest.mark.parametrize("bad", [None, 1, "pass", ["untrusted"], [{"required": "false"}]])
def test_malformed_provider_gap_metadata_never_crashes_or_closes_coverage(evidence_set, bad):
    bundle = evidence_set[0]["recovery"]
    bundle["targets"]["mission"]["before"]["kubernetes"]["gaps"] = bad
    bundle["targets"]["mission"]["prometheus"]["gaps"] = bad
    evidence_set[0]["recovery"] = seal(bundle, "bundle_sha256")
    codes = {g["code"] for g in run(evidence_set)["gaps"]}
    assert {"provider-coverage-incomplete", "telemetry-provider-coverage-incomplete"} <= codes


@pytest.mark.parametrize("kind", ["foreign-origin", "empty-receipts", "before-finishes-late", "after-starts-early", "unresolved-gap"])
def test_runtime_origin_transport_and_unresolved_gaps_are_not_discarded(evidence_set, kind):
    bundle = evidence_set[0]["recovery"]
    before = bundle["targets"]["mission"]["before"]["argocd"]
    after = bundle["targets"]["mission"]["after"]["argocd"]
    if kind == "foreign-origin":
        before["scope"]["origin"] = "https://foreign.example.invalid"
        code = "provider-origin-mismatch"
    elif kind == "empty-receipts":
        before["transport"]["receipts"] = []
        code = "provider-coverage-incomplete"
    elif kind == "before-finishes-late":
        before["transport"]["finished_at"] = at(122)
        code = "provider-time-evidence-stale-or-invalid"
    elif kind == "after-starts-early":
        after["transport"]["started_at"] = at(149)
        code = "provider-time-evidence-stale-or-invalid"
    else:
        bundle["gaps"].append({"code": "collection-canceled", "required": True})
        code = "unresolved-collection-evidence-gap"
    evidence_set[0]["recovery"] = seal(bundle, "bundle_sha256")
    assert code in {g["code"] for g in run(evidence_set)["gaps"]}


def test_probe_source_image_must_be_the_approved_mission_probe_image(evidence_set):
    evidence_set[2]["inventory"]["probe_sources"][0]["image_id"] = "containerd://sha256:"+"9"*64
    assert condition(run(evidence_set), "independent-inventory-association")["observed_state"] == "unknown"


@pytest.mark.parametrize("bad", [None, 1, "snapshot", [1]])
def test_malformed_after_image_snapshot_returns_unknown(evidence_set, bad):
    bundle = evidence_set[0]["recovery"]
    bundle["targets"]["mission"]["after"]["kubernetes"]["observations"]["image_snapshot"] = bad
    evidence_set[0]["recovery"] = seal(bundle, "bundle_sha256")
    assert condition(run(evidence_set), "runtime-images-and-ownership", target="mission")["observed_state"] == "unknown"
