"""Real adapter composition, fixed window resume, and actual local TLS reads."""
from copy import deepcopy
from datetime import timedelta, datetime
import hashlib
import json

import pytest
from test_argocd import ca, server
from test_kubernetes_adapter import fixture as kube_fixture, DEP, SVC, SETS, PODS, SLICES, POLICIES, EVENTS
from test_runtime_context import NOW, TrustedContext, application, codes, inputs

from devsecops.runtime import freeze_runtime_context, start_runtime_collection, finish_runtime_collection
from devsecops.runtime.context import UTC, digest, seal
from devsecops.runtime.collection import COLLECTION_SCHEMA

__all__ = ["ca", "server"]


def rename(value, name):
    if isinstance(value, str):
        if name != "mission":
            for prefix in ("deployment", "service", "pod", "rs", "slice", "policy", "event"):
                value = value.replace(prefix + "-1", prefix + "-" + name)
        return value.replace("mission", name)
    if isinstance(value, dict):
        return {rename(k, name): rename(v, name) for k, v in value.items()}
    if isinstance(value, list):
        return [rename(v, name) for v in value]
    return value


def matrix(params, *, counter_fault=None, up=1, timestamp_shift=0):
    query = params["query"]
    counter = "mission_events_total" in query
    timestamp = query.startswith("timestamp(")
    labels = {"namespace": "demo", "service": "mission", "job": "mission", "instance": "10.0.0.4:9090"}
    if not timestamp:
        labels["__name__"] = "mission_events_total" if counter else "up"
    values = []
    for index, evaluation in enumerate(range(params["start"], params["end"] + 1, params["step"])):
        if timestamp:
            value = evaluation - timestamp_shift
        elif counter:
            value = 10 if counter_fault == "flat" else 10 - index if counter_fault == "reset" else 10 + index
        else:
            value = up
        values.append([evaluation, str(value)])
    return {"status": "success", "data": {"resultType": "matrix", "result": [{"metric": labels, "values": values}]}}


class FixtureClient:
    def __init__(self, origin, provider, name="mission", now=NOW):
        self.origin, self.provider, self.name, self.now = origin, provider, name, now
        self.calls, self.receipts = [], []
        self.data = rename(kube_fixture(), name) if provider == "kubernetes" else application(name, now)
        self.prom_options = {}

    def get_json(self, path, params=None):
        self.calls.append((path, deepcopy(params)))
        if self.provider == "argocd":
            result = {"Version": "v3.5.6"} if path == "/api/version" else self.data
        elif self.provider == "kubernetes":
            result = self.data[path]
        else:
            result = matrix(params, **self.prom_options)
        result = deepcopy(result)
        self.receipts.append({"request_index": len(self.calls), "status": 200,
                              "body_sha256": hashlib.sha256(json.dumps(result).encode()).hexdigest()})
        return result


def setup(*, now=NOW, mutate=None):
    config, lock, intent, receipts = inputs()
    if mutate:
        mutate(config, lock, intent)
    intent["identities"].update(config_sha256=digest(config), lock_sha256=digest(lock))
    context = TrustedContext(freeze_runtime_context(config, lock, intent, receipt_documents=receipts, now=now),
                             {"config": config, "lock": lock, "intent": intent})
    return context, clients_for(context, now)


def clients_for(context, now):
    services = {s["id"]: s for s in context["factory_config"]["services"]}
    return {(target["id"], provider): FixtureClient(services[provider]["origin"], provider, target["id"], now)
            for target in context["intent"]["targets"] for provider in ("argocd", "kubernetes", "prometheus")
            if provider in target and provider in services}


def finish(context, pending, *, now=None, clients=None, delivery_receipt=None):
    end = datetime.fromtimestamp(pending["window"]["end"], UTC)
    now = now or end
    clients = clients or clients_for(context, now)
    return finish_runtime_collection(context, pending, authority=context.authority,
                                     delivery_receipt=delivery_receipt, clients=clients, now=now)


@pytest.mark.parametrize("window_seconds", [30, 120])
def test_positive_narrow_evidence_never_claims_recovery_and_keeps_hash_chain(window_seconds):
    start = NOW + timedelta(microseconds=250_000)
    context, clients = setup(now=start, mutate=lambda c, lock, i: lock.update(observation_window_seconds=window_seconds))
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=start)
    assert pending["stage"] == "pending" and pending["execution_status"] == "partial" and pending["decision"] == "unknown"
    assert pending['execution_mode'] == 'controlled-test' and 'controlled-test-evidence' in codes(pending)
    assert pending["window"]["start"] == int(NOW.timestamp()) + 1
    assert pending["window"]["end"] - pending["window"]["start"] == window_seconds
    assert not clients[("mission", "prometheus")].calls
    result = finish(context, pending)
    assert result["stage"] == "measured" and result["decision"] == "unknown" and result["execution_status"] == "partial"
    assert result['execution_mode'] == 'controlled-test' and 'controlled-test-evidence' in codes(result)
    assert not result["confirmed_failure"]
    assert result["pending_sha256"] == pending["bundle_sha256"]
    assert result["context_snapshot"] == context and result["identities"] == context["intent"]["identities"]
    assert {"recovery-contract-not-evaluated", "source-intent-authentication-unverified",
            "image-mapping-authentication-unverified"} <= codes(result)
    for target in result["targets"].values():
        for phase in ("before", "after"):
            assert target[phase]["kubernetes"]["decision"] == "pass"
            assert target[phase]["argocd"]["decision"] == "pass"
            assert target["bindings"][phase]["sources"]["matched"]
            assert target["bindings"][phase]["destination"]["matched"]
            assert target["bindings"][phase]["service_uid"]["matched"]
        assert target["bindings"]["image_interval"]["stable"]
        assert target["bindings"]["image_interval"]["intervals_valid"]
    prom = result["targets"]["mission"]["prometheus"]
    assert prom["decision"] == "pass"
    assert prom["observations"]["scrape"]["fresh_sample_count"] == window_seconds // 15 + 1
    assert "DO-NOT-EXPORT-SECRET" not in json.dumps(result)
    assert "sensitive-value-must-not-escape" not in json.dumps(result)


@pytest.mark.parametrize("seconds,stage,code", [(-1, "pending", "collection-window-not-ready"),
                                               (46, "blocked", "collection-window-expired")])
def test_early_and_expired_resume_make_zero_requests(seconds, stage, code):
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    now = datetime.fromtimestamp(pending["window"]["end"] + seconds, UTC)
    after_clients = clients_for(context, now)
    result = finish(context, pending, now=now, clients=after_clients)
    assert result["stage"] == stage and code in codes(result)
    assert all(not c.calls for c in after_clients.values())
    assert result["targets"] == pending["targets"] and result["window"] == pending["window"]


def test_early_resume_can_later_finish_exact_saved_window_without_wait():
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    early = finish(context, pending, now=NOW)
    result = finish(context, early)
    assert result["stage"] == "measured" and result["window"] == pending["window"]
    assert "collection-window-not-ready" not in codes(result)


@pytest.mark.parametrize("reseal", [False, True])
def test_changed_fixed_window_is_rejected_even_with_recomputed_plain_hash(reseal):
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    pending["window"]["end"] += 15
    if reseal:
        pending = seal(pending, "bundle_sha256")
    after = clients_for(context, NOW)
    with pytest.raises(ValueError):
        finish(context, pending, clients=after)
    assert all(not c.calls for c in after.values())


def test_changed_context_cannot_resume_previous_snapshot():
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    changed, after = setup(mutate=lambda c, lock, i: i.update(attempt_id="other"))
    with pytest.raises(ValueError):
        finish(changed, pending, clients=after)
    assert all(not c.calls for c in after.values())


def test_measured_or_blocked_bundle_cannot_be_reused_as_pending():
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    measured = finish(context, pending)
    with pytest.raises(ValueError):
        finish(context, measured)


@pytest.mark.parametrize("fault,expected_failure,expected_code", [
    ({"up": 0}, True, None), ({"counter_fault": "flat"}, True, None),
    ({"counter_fault": "reset"}, False, "prometheus-counter-reset"),
    ({"timestamp_shift": 60}, False, "prometheus-stale-source-sample"),
])
def test_actual_prom_adapter_distinguishes_outage_reset_and_staleness(fault, expected_failure, expected_code):
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    now = datetime.fromtimestamp(pending["window"]["end"], UTC)
    after = clients_for(context, now)
    after[("mission", "prometheus")].prom_options = fault
    result = finish(context, pending, clients=after)
    assert result["decision"] == "unknown" and result["confirmed_failure"] is expected_failure
    if expected_code:
        assert expected_code in codes(result)


def test_service_replacement_detected_even_when_kubernetes_readiness_passes():
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    now = datetime.fromtimestamp(pending["window"]["end"], UTC)
    after = clients_for(context, now)
    data = after[("mission", "kubernetes")].data
    data[SVC]["metadata"]["uid"] = "replacement-service"
    data[SLICES]["items"][0]["metadata"]["ownerReferences"][0]["uid"] = "replacement-service"
    result = finish(context, pending, clients=after)
    assert result["targets"]["mission"]["after"]["kubernetes"]["decision"] == "pass"
    assert {"service-uid-mismatch", "service-identity-changed-or-missing"} <= codes(result)


def test_restart_or_image_change_invalidates_concrete_image_bracket():
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    now = datetime.fromtimestamp(pending["window"]["end"], UTC)
    after = clients_for(context, now)
    after[("mission", "kubernetes")].data[PODS]["items"][0]["status"]["containerStatuses"][0]["restartCount"] = 1
    result = finish(context, pending, clients=after)
    assert "image-bracket-incomplete-or-changed" in codes(result)
    assert "prometheus-image-identity-or-restart-changed" in codes(result)


@pytest.mark.parametrize("part", ["source", "destination"])
def test_internal_argo_healthy_agreement_does_not_prove_frozen_external_intent(part):
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    now = datetime.fromtimestamp(pending["window"]["end"], UTC)
    after = clients_for(context, now)
    data = after[("mission", "argocd")].data
    if part == "source":
        data["spec"]["source"]["helm"]["releaseName"] = "different"
        data["status"]["operationState"]["syncResult"]["source"] = deepcopy(data["spec"]["source"])
    else:
        data["spec"]["destination"]["server"] = "https://different-cluster.invalid"
    data["status"]["sync"]["comparedTo"][part] = deepcopy(data["spec"][part])
    result = finish(context, pending, clients=after)
    assert result["targets"]["mission"]["after"]["argocd"]["decision"] == "pass"
    assert "argo-" + part + "-intent-parity-unverified" in codes(result)


@pytest.mark.parametrize("mutation,code", [
    (lambda s: s.update(read_auth={"mode": "basic", "username_env": "USER_REF", "password_env": "PASS_REF"}), "runtime-read-auth-unsupported"),
    (lambda s: s.update(read_auth={"mode": "bearer", "token_env": "TOKEN_REF"}, operator_auth={"mode": "bearer", "token_env": "TOKEN_REF"}), "runtime-read-operator-identity-overlap"),
    (lambda s: s["scope"].update(applications=["reference"]), "runtime-scope-not-authorized"),
])
def test_unsupported_auth_or_scope_does_not_use_injected_or_ambient_credentials(mutation, code):
    def change(config, _lock, _intent):
        mutation(next(s for s in config["services"] if s["id"] == "kubernetes"))
    context, clients = setup(mutate=change)
    result = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    assert code in codes(result)
    assert not clients[("mission", "kubernetes")].calls


@pytest.mark.parametrize("change", ["other-application-namespace", "other-project", "missing-project-allowlist",
                                  "missing-application-namespace", "missing-destination-namespace"])
def test_argo_exact_namespace_and_project_are_authorized_before_reads(change):
    def mutate(config, _lock, intent):
        scope = next(s for s in config["services"] if s["id"] == "argocd")["scope"]
        if change == "other-application-namespace":
            intent["targets"][0]["argocd"]["application_namespace"] = "other"
        elif change == "other-project":
            intent["targets"][0]["argocd"]["project"] = "other"
        elif change == "missing-project-allowlist":
            scope.pop("argocd_projects")
        elif change == "missing-application-namespace":
            scope["namespaces"].remove("argocd")
        else:
            scope["namespaces"].remove("demo")
    context, clients = setup(mutate=mutate)
    result = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    assert "runtime-scope-not-authorized" in codes(result)
    assert not clients[("mission", "argocd")].calls


def delivery(context, at=NOW):
    intent = context["intent"]
    return {"schema_version": 1, "kind": "runtime-delivery-binding", "run_id": intent["run_id"],
        "attempt_id": intent["attempt_id"], "environment": intent["environment"], "identities": deepcopy(intent["identities"]),
        "target_id": "mission", "source_position": 1, "delivered_revision": "a" * 40, "delivered_at": at.isoformat(),
        "native_handoff_sha256": "1" * 64, "patch_sha256": "2" * 64, "candidate_tree_sha256": "3" * 64,
        "validation_receipt_sha256": "4" * 64}


def test_native_delivery_hook_keeps_hashes_and_all_source_samples_strictly_after_delivery():
    context, clients = setup(mutate=lambda c, lock, i: i.update(phase="recovery"))
    binding = delivery(context)
    pending = start_runtime_collection(context, authority=context.authority, delivery_receipt=binding, clients=clients, now=NOW)
    result = finish(context, pending, delivery_receipt=binding)
    assert result["delivery"]["binding"] == binding
    assert result["delivery"]["artifact"]["sha256"] == digest(binding)
    # The first source sample is exactly the delivery time and cannot count as post-delivery evidence.
    assert "prometheus-pre-delivery-source-sample" in codes(result)
    assert "native-delivery-authentication-unverified" in codes(result)


def test_recovery_without_delivery_receipt_stays_unknown():
    context, clients = setup(mutate=lambda c, lock, i: i.update(phase="recovery"))
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    result = finish(context, pending)
    assert "delivery-binding-missing" in codes(result)
    assert result["targets"]["mission"]["prometheus"]["decision"] == "pass"
    assert result["decision"] == "unknown"


@pytest.mark.parametrize("field,value", [("attempt_id", "replay"), ("delivered_revision", "b" * 40),
                                        ("delivered_at", "2026-10-27T00:00:00Z")])
def test_wrong_or_future_delivery_is_rejected_before_network(field, value):
    context, clients = setup()
    binding = delivery(context)
    binding[field] = value
    with pytest.raises(ValueError):
        start_runtime_collection(context, authority=context.authority, delivery_receipt=binding, clients=clients, now=NOW)
    assert all(not c.calls for c in clients.values())


def test_config_expiry_blocks_all_reads():
    context, clients = setup(mutate=lambda c, lock, i: c.update(expires_at=NOW.isoformat()))
    result = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    assert result["stage"] == "blocked" and "environment-expired" in codes(result)
    assert all(not c.calls for c in clients.values())


def test_missing_hosted_metric_service_never_falls_back_to_discovery():
    def mutate(config, _lock, _intent):
        config["services"] = [s for s in config["services"] if s["id"] != "prometheus"]
    context, clients = setup(mutate=mutate)
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    result = finish(context, pending)
    prom = result["targets"]["mission"]["prometheus"]
    assert prom["decision"] == "unknown" and "runtime-service-not-configured" in codes(result)


def test_actual_client_origin_cannot_differ_from_authorized_origin():
    context, clients = setup()
    clients[("mission", "kubernetes")].origin = "https://outside.invalid"
    result = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    assert "runtime-client-origin-mismatch" in codes(result)
    assert not clients[("mission", "kubernetes")].calls


def test_whole_phase_budget_exhaustion_stops_requests(monkeypatch):
    context, clients = setup()
    monkeypatch.setattr("devsecops.runtime.collection.PHASE_TIMEOUT_SECONDS", 0)
    result = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    assert "runtime-phase-budget-exhausted" in codes(result)
    assert result["stage"] == "blocked" and result["window"] is None
    assert all(not c.calls for c in clients.values())


def test_ca_change_during_phase_cannot_redirect_subsequent_clients(tmp_path):
    from devsecops.adapters.http import APIError
    from devsecops.runtime.collection import _Clients
    cert = tmp_path / "ca.pem"
    cert.write_text("bounded synthetic CA bytes")
    def mutate(config, _lock, _intent):
        next(s for s in config["services"] if s["id"] == "kubernetes")["ca_file"] = str(cert)
    context, clients = setup(mutate=mutate)
    pool = _Clients(context, clients)
    cert.write_text("changed CA bytes after context verification")
    with pytest.raises(APIError, match="ca-input-changed"):
        pool.get("kubernetes", context["intent"]["targets"][0])
    assert not clients[("mission", "kubernetes")].calls


def test_resume_keeps_exact_query_range_when_called_late_but_within_bound():
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    later = datetime.fromtimestamp(pending["window"]["end"] + 10, UTC)
    after = clients_for(context, later)
    result = finish(context, pending, clients=after, now=later)
    assert result["targets"]["mission"]["prometheus"]["decision"] == "pass"
    for _, params in after[("mission", "prometheus")].calls:
        assert params["start"] == pending["window"]["start"]
        assert params["end"] == pending["window"]["end"]
    assert result["targets"]["mission"]["prometheus"]["transport"]["started_at"] == later.isoformat()


def test_complete_delivery_file_identity_preserved_and_change_rejected(tmp_path):
    context, clients = setup()
    binding = delivery(context, NOW - timedelta(seconds=1))
    path = tmp_path / "delivery.json"
    path.write_text(json.dumps(binding, indent=2))
    pending = start_runtime_collection(context, authority=context.authority, delivery_receipt=path, clients=clients, now=NOW)
    assert pending["delivery"]["artifact"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    result = finish(context, pending, delivery_receipt=path)
    assert result["targets"]["mission"]["prometheus"]["decision"] == "pass"
    path.write_text(path.read_text() + "\n")
    after = clients_for(context, NOW)
    with pytest.raises(ValueError, match="delivery receipt changed"):
        finish(context, pending, clients=after, delivery_receipt=path)
    assert all(not c.calls for c in after.values())


def test_reference_target_requires_concrete_images_even_without_prometheus():
    from devsecops.runtime.collection import _image_comparison
    before = {"namespace": "demo", "service": "reference", "deployment_uid": "deployment-reference",
              "containers": [], "observed_from": NOW.isoformat(), "observed_through": NOW.isoformat()}
    after = deepcopy(before)
    after.update(observed_from=(NOW + timedelta(seconds=30)).isoformat(),
                 observed_through=(NOW + timedelta(seconds=30)).isoformat())
    comparison = _image_comparison(before, after, {"start": NOW.timestamp(), "end": NOW.timestamp() + 30,
                                                 "max_sample_age_seconds": 45})
    assert comparison["intervals_valid"] and comparison["stable"] is False


def test_three_targets_are_supported_without_universal_two_app_limit():
    def extra(config, _lock, intent):
        other = deepcopy(intent["targets"][1])
        other.update(id="auxiliary", roles=["auxiliary"])
        other["kubernetes"].update(deployment="auxiliary", service="auxiliary", deployment_uid="deployment-auxiliary", service_uid="service-auxiliary")
        other["argocd"]["application"] = "auxiliary"
        intent["targets"].append(other)
        for service in config["services"]:
            if service["id"] in {"kubernetes", "argocd", "prometheus"}:
                service["scope"]["applications"].append("auxiliary")
    context, clients = setup(mutate=extra)
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    result = finish(context, pending)
    assert len(result["targets"]) == 3
    assert result["targets"]["auxiliary"]["after"]["kubernetes"]["decision"] == "pass"


def queue_phase(server, *, before, at, window=None):
    queue = []
    def argo_responses():
        for name in ("mission", "reference"):
            item = application(name, at)
            queue.extend([{"json": {"Version": "v3.5.6"}}, {"json": item}, {"json": item}])
    def kube_responses():
        for name in ("mission", "reference"):
            data = rename(kube_fixture(), name)
            queue.extend({"json": data[rename(path, name)]} for path in [DEP, SVC, SETS, PODS, SLICES, POLICIES, EVENTS, DEP, SVC])
    if before:
        argo_responses()
        kube_responses()
    else:
        kube_responses()
        argo_responses()
        for query in ("up{}", "timestamp(up{})", "mission_events_total{}", "timestamp(mission_events_total{})"):
            queue.append({"json": matrix({"query": query, "start": window["start"], "end": window["end"], "step": window["step_seconds"]})})
    server["responses"].extend(queue)


def test_actual_tls_factory_auth_complete_read_path_and_resume(server, monkeypatch):
    def tls_config(config, _lock, _intent):
        for service in config["services"]:
            if service["id"] in {"kubernetes", "argocd", "prometheus"}:
                service.update(origin=server["origin"], ca_file=str(server["ca"]),
                               read_auth={"mode": "bearer", "token_env": "RUNTIME_TEST_READ_TOKEN"})
    monkeypatch.setenv("RUNTIME_TEST_READ_TOKEN", "synthetic-secret-do-not-export")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    context, _ = setup(mutate=tls_config)
    queue_phase(server, before=True, at=NOW)
    pending = start_runtime_collection(context, authority=context.authority, now=NOW)
    assert pending["stage"] == "pending"
    assert len(server["requests"]) == 24
    end = datetime.fromtimestamp(pending["window"]["end"], UTC)
    queue_phase(server, before=False, at=end, window=pending["window"])
    result = finish_runtime_collection(context, pending, authority=context.authority, now=end)
    assert len(server["requests"]) == 52 and not server["responses"]
    assert result["targets"]["mission"]["prometheus"]["decision"] == "pass"
    assert result["decision"] == "unknown"
    assert all(r["authorization"] == "Bearer synthetic-secret-do-not-export" for r in server["requests"])
    assert all(not any(denied in r["path"] for denied in ("/logs", "/exec", "/secrets", "/targets", "refresh=")) for r in server["requests"])
    assert "synthetic-secret-do-not-export" not in json.dumps(result)
    assert all(s["sha256"] for s in context["ca_artifacts"].values())


def test_actual_tls_redirect_does_not_expand_scope(server):
    def tls_config(config, _lock, _intent):
        for service in config["services"]:
            if service["id"] in {"kubernetes", "argocd", "prometheus"}:
                service.update(origin=server["origin"], ca_file=str(server["ca"]))
    context, _ = setup(mutate=tls_config)
    server["responses"] = [{"status": 302, "headers": {"Location": server["origin"] + "/secrets"}}] * 24
    result = start_runtime_collection(context, authority=context.authority, now=NOW)
    assert result["stage"] == "blocked" and result["decision"] == "unknown"
    assert all("/secrets" not in r["path"] for r in server["requests"])


def test_exported_collection_schema_matches():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "schemas/runtime-collection.schema.json"
    assert json.loads(path.read_text()) == COLLECTION_SCHEMA


def forge_context(context, mutation):
    """Recompute every public seal while preserving separate operator authority."""
    forged = deepcopy(context)
    mutation(forged)
    forged['intent']['identities'].update(config_sha256=digest(forged['factory_config']),
                                         lock_sha256=digest(forged['factory_lock']))
    for name, key in [('config', 'factory_config'), ('lock', 'factory_lock'), ('intent', 'intent')]:
        forged['input_artifacts'][name].update(sha256=digest(forged[key]), canonical_sha256=digest(forged[key]), path=None)
    return seal(forged, 'context_sha256')


@pytest.mark.parametrize('mutation', [
    lambda c: c['factory_config']['services'][0].update(origin='https://outside.invalid'),
    lambda c: c['factory_config']['services'][0].update(read_auth={'mode': 'bearer', 'token_env': 'UNAPPROVED_TOKEN'}),
    lambda c: c['factory_config']['services'][0]['scope'].update(namespaces=['unapproved']),
    lambda c: c['intent'].update(phase='recovery'),
    lambda c: c['intent']['targets'][0]['argocd'].update(application_namespace='unapproved'),
    lambda c: c['intent']['targets'][0]['prometheus'].update(instance='10.9.9.9:9090'),
    lambda c: c['factory_lock'].update(observation_window_seconds=60),
])
def test_original_authority_blocks_forged_context_before_start_or_resume_reads(mutation):
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    forged = forge_context(context, mutation)
    rewritten = deepcopy(pending)
    rewritten.update(context_snapshot=forged, context_sha256=forged['context_sha256'],
                     identities=deepcopy(forged['intent']['identities']))
    rewritten = seal(rewritten, 'bundle_sha256')
    after = clients_for(context, NOW)
    with pytest.raises(ValueError):
        start_runtime_collection(forged, authority=context.authority, clients=after, now=NOW)
    with pytest.raises(ValueError):
        finish_runtime_collection(forged, rewritten, authority=context.authority, clients=after, now=NOW)
    assert all(not client.calls for client in after.values())


def test_missing_authority_cannot_use_testing_client_or_clock_as_bypass():
    context, clients = setup()
    with pytest.raises(TypeError, match='authority'):
        start_runtime_collection(context, clients=clients, now=NOW)
    with pytest.raises(ValueError, match='authority is required'):
        start_runtime_collection(context, authority=None, clients=clients, now=NOW)
    assert all(not client.calls for client in clients.values())
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    after = clients_for(context, NOW)
    with pytest.raises(TypeError, match='authority'):
        finish_runtime_collection(context, pending, clients=after, now=NOW)
    assert all(not client.calls for client in after.values())


def test_serialized_ca_state_cannot_disable_authorized_ca_recheck(tmp_path):
    cert = tmp_path / 'ca.pem'
    cert.write_text('synthetic CA bytes, no private key')
    def mutate(config, _lock, _intent):
        next(s for s in config['services'] if s['id'] == 'kubernetes')['ca_file'] = str(cert)
    context, clients = setup(mutate=mutate)
    context['ca_artifacts']['kubernetes'] = {'state': 'system-trust'}
    context = seal(context, 'context_sha256')
    with pytest.raises(ValueError, match='certificate bytes differ'):
        start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    assert all(not client.calls for client in clients.values())


def test_resume_delivery_is_explicit_and_its_embedded_path_is_inert(tmp_path):
    context, clients = setup()
    binding = delivery(context, NOW - timedelta(seconds=1))
    pending = start_runtime_collection(context, authority=context.authority, delivery_receipt=binding, clients=clients, now=NOW)
    pending['delivery']['artifact']['path'] = str(tmp_path / 'must-not-open.json')
    pending = seal(pending, 'bundle_sha256')
    after = clients_for(context, NOW)
    with pytest.raises(ValueError, match='Explicit trusted delivery'):
        finish(context, pending, clients=after)
    assert all(not client.calls for client in after.values())
    result = finish(context, pending, delivery_receipt=binding)
    assert result['stage'] == 'measured'
    assert {'context-authentication-unverified', 'pending-authentication-unverified'} <= codes(result)


def test_resume_cannot_add_delivery_to_an_existing_window():
    context, clients = setup()
    pending = start_runtime_collection(context, authority=context.authority, clients=clients, now=NOW)
    after = clients_for(context, NOW)
    with pytest.raises(ValueError, match='cannot be added'):
        finish(context, pending, clients=after, delivery_receipt=delivery(context))
    assert all(not client.calls for client in after.values())
