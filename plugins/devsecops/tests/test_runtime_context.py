"""Frozen intent integrity and scope; these fixtures are not qualification proof."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

import pytest
from test_factory_contracts import config, factory_lock
from test_argocd import app

from devsecops.runtime.context import (
    CONTEXT_SCHEMA, DELIVERY_SCHEMA, INTENT_SCHEMA, digest, freeze_runtime_context,
    load_input, validate_intent, verify_context, verify_runtime_authority,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def spec_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def application(name="mission", now=NOW):
    result = app()
    result["metadata"]["name"] = name
    result["metadata"]["uid"] = "app-" + name
    result["spec"]["destination"]["namespace"] = "demo"
    result["status"]["sync"]["comparedTo"]["destination"]["namespace"] = "demo"
    result["status"]["reconciledAt"] = now.isoformat()
    result["status"]["resources"][0].update(name=name, namespace="demo")
    return result


def inputs():
    operator, lock = config(), factory_lock()
    lock.update(freshness_seconds=45, observation_window_seconds=30)
    for service in operator["services"]:
        if service["id"] in {"kubernetes", "argocd", "prometheus"}:
            service["scope"] = {"namespaces": ["demo"], "applications": ["mission", "reference"]}
            if service["id"] == "argocd":
                service["scope"].update(namespaces=["demo", "argocd"], argocd_projects=["demo"])
            service["read_auth"] = {"mode": "none"}
            service.pop("operator_auth", None)
    receipts = {"inventory": {"claim": "synthetic inventory"}, "mapping": {"claim": "synthetic image mapping"},
                "intent": {"claim": "synthetic source intent"}}
    def ref(name):
        return {"id": name, "sha256": digest(receipts[name])}
    targets = []
    for name, role in (("mission", "mission"), ("reference", "reference-web")):
        argo = application(name)
        target = {"id": name, "roles": [role], "kubernetes": {
            "service_id": "kubernetes", "namespace": "demo", "deployment": name, "service": name,
            "deployment_uid": "deployment-1" if name == "mission" else "deployment-reference",
            "generation": 3, "service_uid": "service-1" if name == "mission" else "service-reference"},
            "argocd": {"service_id": "argocd", "project": "demo", "application_namespace": "argocd", "application": name,
                "sources": [{"position": 1, "repository_url": "https://example.test/mission.git", "revision": "a" * 40,
                    "path": "deploy", "renderer": "helm", "source_spec_sha256": spec_digest(argo["spec"]["source"]),
                    "rendered_artifact_sha256": "b" * 64, "intent_receipt": ref("intent")}],
                "destination": deepcopy(argo["spec"]["destination"]),
                "destination_spec_sha256": spec_digest(argo["spec"]["destination"]), "intent_receipt": ref("intent")},
            "image_mapping_receipt": ref("mapping")}
        if name == "mission":
            target["prometheus"] = {"service_id": "prometheus", "namespace": "demo", "service": "mission",
                                    "job": "mission", "instance": "10.0.0.4:9090"}
        targets.append(target)
    intent = {"schema_version": 1, "run_id": "run-1", "attempt_id": "primary", "phase": "baseline",
        "environment": operator["environment"], "profile": operator["profile"], "scenario_id": lock["scenario_id"],
        "identities": {"config_sha256": digest(operator), "lock_sha256": digest(lock),
                       "scenario_sha256": lock["scenario_sha256"], "assertions_sha256": lock["assertions_sha256"],
                       "package_sha256": "c" * 64, "environment_sha256": "d" * 64},
        "environment_inventory_receipt": ref("inventory"), "targets": targets}
    return operator, lock, intent, receipts


class TrustedContext(dict):
    """Test-only separate operator inputs; never serialized with the evidence."""
    def __init__(self, value, authority):
        super().__init__(value)
        self.authority = deepcopy(authority)


def frozen():
    config_doc, lock, intent, receipts = inputs()
    value = freeze_runtime_context(config_doc, lock, intent, receipt_documents=receipts, now=NOW)
    return TrustedContext(value, {"config": config_doc, "lock": lock, "intent": intent})


def codes(value):
    return {g["code"] for g in value["gaps"]}


def test_matched_receipt_bytes_never_authenticate_claims_or_export_bodies():
    context = frozen()
    verify_context(context)
    assert all(r["integrity"] == "matched" and r["authentication"] == "not-verified" for r in context["receipt_artifacts"])
    assert {"source-intent-authentication-unverified", "environment-inventory-authentication-unverified",
            "image-mapping-authentication-unverified"} <= codes(context)
    assert "synthetic inventory" not in json.dumps(context)
    assert "DO-NOT-EXPORT-SECRET" not in json.dumps(context)


@pytest.mark.parametrize("mutate", [
    lambda i: i.update(schema_version=2),
    lambda i: i.update(secret="SECRET_SENTINEL"),
    lambda i: i["targets"].append(deepcopy(i["targets"][0])),
    lambda i: i["targets"][0]["argocd"]["sources"][0].update(position=2),
    lambda i: i["targets"][0]["argocd"]["sources"][0].update(path="../secret"),
    lambda i: i["targets"][0]["argocd"]["sources"][0].update(repository_url="https://SECRET_SENTINEL@example.test/repo"),
    lambda i: i["targets"][0]["argocd"]["destination"].update(namespace="other"),
    lambda i: i["targets"][0]["prometheus"].update(service="other"),
    lambda i: i["targets"][0]["roles"].append("reference-web"),
    lambda i: i["targets"].pop(),
    lambda i: i["targets"][0].pop("prometheus"),
])
def test_invalid_intent_is_rejected_without_echo(mutate):
    config_doc, lock, intent, _ = inputs()
    mutate(intent)
    with pytest.raises(ValueError) as exc:
        freeze_runtime_context(config_doc, lock, intent, now=NOW)
    assert "SECRET_SENTINEL" not in str(exc.value)


def test_generic_target_schema_does_not_require_two_canonical_applications():
    _, _, intent, _ = inputs()
    intent["scenario_id"] = "customer-observation-v1"
    intent["targets"] = [intent["targets"][0]]
    intent["targets"][0]["roles"] = ["frontend"]
    intent["targets"][0].pop("prometheus")
    validate_intent(intent)
    # The current factory-lock schema remains canonical-only. Runtime does not bypass it.


@pytest.mark.parametrize("field", ["config_sha256", "lock_sha256", "scenario_sha256", "assertions_sha256"])
def test_input_identity_mismatches_fail_before_collection(field):
    config_doc, lock, intent, _ = inputs()
    intent["identities"][field] = "f" * 64
    with pytest.raises(ValueError):
        freeze_runtime_context(config_doc, lock, intent, now=NOW)


def test_missing_receipts_and_service_identity_are_explicit_required_gaps():
    config_doc, lock, intent, _ = inputs()
    intent.pop("environment_inventory_receipt")
    intent["targets"][0].pop("image_mapping_receipt")
    intent["targets"][0]["kubernetes"].pop("service_uid")
    result = freeze_runtime_context(config_doc, lock, intent, now=NOW)
    assert {"environment-inventory-missing", "image-mapping-missing", "service-uid-intent-missing",
            "referenced-receipt-missing"} <= codes(result)
    assert all(g["required"] for g in result["gaps"])


def test_receipt_mismatch_is_unknown_and_conflicting_references_rejected():
    config_doc, lock, intent, receipts = inputs()
    receipts["mapping"]["claim"] = "changed"
    result = freeze_runtime_context(config_doc, lock, intent, receipt_documents=receipts, now=NOW)
    assert "referenced-receipt-mismatch" in codes(result)
    intent["targets"][1]["image_mapping_receipt"]["sha256"] = "f" * 64
    with pytest.raises(ValueError):
        freeze_runtime_context(config_doc, lock, intent, receipt_documents=receipts, now=NOW)


def test_explicit_file_bytes_are_preserved_and_rechecked(tmp_path):
    config_doc, lock, intent, receipts = inputs()
    paths = {}
    for name, value in (("config", config_doc), ("lock", lock)):
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_text(json.dumps(value, indent=2))
        intent["identities"][name + "_sha256"] = hashlib.sha256(paths[name].read_bytes()).hexdigest()
    paths["intent"] = tmp_path / "intent.json"
    paths["intent"].write_text(json.dumps(intent))
    result = freeze_runtime_context(paths["config"], paths["lock"], paths["intent"], receipt_documents=receipts, now=NOW)
    assert result["input_artifacts"]["config"]["encoding"] == "raw-json"
    assert result["input_artifacts"]["config"]["sha256"] != result["input_artifacts"]["config"]["canonical_sha256"]
    verify_context(result)
    paths["config"].write_text(paths["config"].read_text() + "\n")
    verify_context(result)  # Recorded paths are inert even when originals change.
    with pytest.raises(ValueError):
        verify_runtime_authority(result, paths)


def test_paths_are_explicit_and_no_receipt_body_paths_are_followed(tmp_path):
    config_doc, lock, intent, receipts = inputs()
    secret = tmp_path / "secret.json"
    secret.write_text("not JSON; do not read")
    receipts["inventory"] = {"path": str(secret)}
    intent["environment_inventory_receipt"]["sha256"] = digest(receipts["inventory"])
    result = freeze_runtime_context(config_doc, lock, intent, receipt_documents=receipts, now=NOW)
    assert "not JSON" not in json.dumps(result)
    assert str(secret) not in json.dumps(result)


def test_historical_receipt_path_is_inert_and_never_authenticates_claims(tmp_path):
    config_doc, lock, intent, receipts = inputs()
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(receipts["inventory"]))
    intent["environment_inventory_receipt"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    receipts["inventory"] = path
    result = freeze_runtime_context(config_doc, lock, intent, receipt_documents=receipts, now=NOW)
    path.write_text('{"changed":true}')
    verify_context(result)
    verify_runtime_authority(result, {"config": config_doc, "lock": lock, "intent": intent})
    assert all(item['authentication'] == 'not-verified' for item in result['receipt_artifacts'])


def test_separate_authority_accepts_identical_canonical_file_bytes(tmp_path):
    from devsecops.runtime.context import canonical_bytes
    context = frozen()
    authority = {}
    for name, value in context.authority.items():
        authority[name] = tmp_path / (name + '.json')
        authority[name].write_bytes(canonical_bytes(value))
    verify_runtime_authority(context, authority)
    assert context['input_artifacts']['config']['encoding'] != 'raw-json'


def test_separate_authority_never_opens_embedded_paths(tmp_path, monkeypatch):
    from devsecops.runtime import context as module
    context = frozen()
    for artifact in context['input_artifacts'].values():
        artifact['path'] = str(tmp_path / 'untrusted-input-path.json')
    for receipt in context['receipt_artifacts']:
        receipt['artifact']['path'] = str(tmp_path / 'untrusted-receipt-path.json')
    context = module.seal(context, 'context_sha256')
    load = module.load_input
    def explicit_only(value):
        assert isinstance(value, dict), 'An embedded path was followed'
        return load(value)
    monkeypatch.setattr(module, 'load_input', explicit_only)
    verify_runtime_authority(context, context.authority)


@pytest.mark.parametrize('authority', [None, {}, [], {'config': {}}, {'config': {}, 'lock': {}, 'intent': {}, 'extra': {}}])
def test_authority_requires_exact_explicit_mapping(authority):
    with pytest.raises(ValueError, match='authority is required'):
        verify_runtime_authority(frozen(), authority)


def test_unsealed_mutation_and_symlinks_rejected(tmp_path):
    result = frozen()
    result["intent"]["environment"] = "changed"
    with pytest.raises(ValueError, match="integrity"):
        verify_context(result)
    path = tmp_path / "source.json"
    path.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        load_input(link)


def test_changed_private_ca_bytes_rejected_before_collection(tmp_path):
    config_doc, lock, intent, receipts = inputs()
    cert = tmp_path / "test-ca.pem"
    cert.write_text("synthetic bounded certificate bytes; no credentials")
    next(s for s in config_doc["services"] if s["id"] == "kubernetes")["ca_file"] = str(cert)
    intent["identities"]["config_sha256"] = digest(config_doc)
    context = freeze_runtime_context(config_doc, lock, intent, receipt_documents=receipts, now=NOW)
    assert context["ca_artifacts"]["kubernetes"]["state"] == "frozen"
    cert.write_text("changed certificate")
    verify_context(context)
    with pytest.raises(ValueError, match="certificate bytes differ"):
        verify_runtime_authority(context, {"config": config_doc, "lock": lock, "intent": intent})


def test_ca_named_pipe_is_rejected_without_a_blocking_read(tmp_path):
    import os
    from devsecops.runtime.context import file_hash
    pipe = tmp_path / "not-a-certificate"
    os.mkfifo(pipe)
    with pytest.raises(ValueError):
        file_hash(pipe)


def test_shared_service_targets_are_not_silently_treated_as_disjoint():
    _, _, intent, _ = inputs()
    intent["targets"][1]["kubernetes"]["service"] = "mission"
    with pytest.raises(ValueError, match="overlap"):
        validate_intent(intent)


@pytest.mark.parametrize("documents", [[], "", False])
def test_receipt_map_requires_explicit_mapping_type(documents):
    config_doc, lock, intent, _ = inputs()
    with pytest.raises(ValueError):
        freeze_runtime_context(config_doc, lock, intent, receipt_documents=documents, now=NOW)


@pytest.mark.parametrize("name,schema", [("runtime-intent", INTENT_SCHEMA), ("runtime-context", CONTEXT_SCHEMA),
                                         ("runtime-delivery-binding", DELIVERY_SCHEMA)])
def test_exported_runtime_schemas_match(name, schema):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert json.loads((root / "schemas" / (name + ".schema.json")).read_text()) == schema
