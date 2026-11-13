"""Controlled acceptance wiring tests, with explicit original-provider boundaries.

No registry, lab or native capability is issued here. The positive orchestrator
fixture replaces fixed acquisition accessors; production rejection tests retain
the real exact-type accessors. Synthetic verdicts never qualify a live run.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from devsecops.factory import runtime_acceptance as acceptance, runtime_observations as runtime
from devsecops.factory.lifecycle import PhaseContext
from devsecops.runtime.context import canonical_bytes, digest
from devsecops.runtime.recovery import ASSURANCES, OriginVerification, verify_recovery
from test_runtime_observations import retained as _retained_fixture, bind
from test_runtime_recovery import NOW, TestProvenance

from test_runtime_image import runtime_case as _runtime_case_fixture, history_responses
from test_ci_publication import case as _ci_case_fixture
from test_owned_ci import wire as _wire_fixture

retained = _retained_fixture


@pytest.fixture
def controlled_proofs(retained, monkeypatch):
    session, original_context, _, _, bundles, contract, evidence = retained
    bind(retained)
    context = PhaseContext(
        contract["run_id"],
        {"id": "primary-verify", "attempt": "primary", "operation": "verify"},
        contract["identities"],
        {},
        {},
        original_context.run_dir,
        time.monotonic() + 60,
        "operator",
    )
    issued = runtime._ORIGINS[session._recovery["primary"]["origin"]]
    issued["phase_context"] = context
    report = verify_recovery(
        bundles["baseline"],
        bundles["fault"],
        bundles["recovery"],
        contract=contract,
        evidence=evidence,
        _test_provenance=TestProvenance(),
        now=NOW + timedelta(seconds=155),
    )
    # Controlled fixed-provider boundary only: retain a unique result object
    # to exercise current-call admission, never install an authentic origin.
    report["execution_mode"] = "direct"
    issued["verified_report"] = (report, canonical_bytes(report), context)
    monkeypatch.setattr(
        runtime,
        "verify_runtime_observation_origin",
        lambda *a, **k: OriginVerification("f" * 64, {}, tuple(ASSURANCES)),
    )
    for record in session._records.values():
        record["mode"] = "operator"
        record["bundle"]["execution_mode"] = "direct"
    session._records[("f0-network-policy", "recovery")] = deepcopy(session._records[("primary", "recovery")])
    monkeypatch.setattr(session, "_retained", lambda attempt, phase: session._records[(attempt, phase)])
    baseline = evidence["rendered_after"]["resources"][0]["body"]
    fault = evidence["rendered_before"]["resources"][0]["body"]
    binding = {
        "run_id": context.run_id,
        "identities": dict(context.identities),
        "resource_id": evidence["rendered_before"]["resources"][0]["id"],
        "baseline_spec_sha256": digest(baseline["spec"]),
        "fault_spec_sha256": digest(fault["spec"]),
        "qualification_sha256": "e" * 64,
    }
    plan = SimpleNamespace(baseline=canonical_bytes(baseline), fault=canonical_bytes(fault))
    monkeypatch.setattr(
        session, "qualified_policy_plan", lambda context: {"plan": plan, "qualification": binding}
    )
    reference = {
        "verification_sha256": "1" * 64,
        "publisher_trust_sha256": "2" * 64,
        "mapping_receipt_sha256": "3" * 64,
    }
    image = {
        "source_commit": "a" * 40,
        "manifest_digest": "sha256:" + "b" * 64,
        "ci": {"pipeline_id": 1},
        "history_readback": {"sha256": "4" * 64},
        "current_policy": {"decision": "pass", "gaps": []},
        "artifact_manifest": {"digest": "sha256:" + "5" * 64},
        "signer": {"controlled_boundary": True},
    }
    proof_path = context.run_dir / "controlled-current-image-proof.json"
    proof_path.write_bytes(b'{"controlled_boundary":true}\n')
    monkeypatch.setattr(acceptance, "_reference", lambda *a: (reference, (proof_path,)))
    monkeypatch.setattr(acceptance, "_mission", lambda *a: (image, (proof_path,)))
    docs = {
        key: {name: key + name for name in ("request_sha256", "report_sha256", "validation_sha256")}
        for key in ("review", "diagnose")
    }
    monkeypatch.setattr(acceptance, "_native_reports", lambda *a: (docs, (proof_path,)))
    monkeypatch.setattr(acceptance, "_patch", lambda *a: {"controlled_boundary": True})
    return session, context, report, issued, contract, proof_path, binding


def statuses(outputs):
    return {row.assertion_id: row.status for row in outputs}


def reseal_current(proofs):
    _, context, report, issued, *_ = proofs
    issued["verified_report"] = (report, canonical_bytes(report), context)


def test_all_eight_extra_checks_have_explicit_proofs_and_original_artifact_closure(controlled_proofs):
    session, context, report, _, _, path, _ = controlled_proofs
    acceptance._causal(session, context, controlled_proofs[4], report, set(ASSURANCES))
    checks, document, paths = acceptance.assertions_for_verification(session, context, report)
    assert {(row.case_id, row.assertion_id) for row in checks} == set(acceptance.CHECKS)
    assert set(statuses(checks).values()) == {"pass"}
    assert paths == (path,)
    assert all(row["proof"] for row in document["checks"])
    assert report["causality"] == "unproven"  # extra F0 proof does not rewrite the recovery domain report


@pytest.mark.parametrize(
    "change",
    [
        "copied-report",
        "changed-report",
        "other-attempt",
        "copied-context",
        "controlled-mode",
        "missing-origin",
    ],
)
def test_saved_or_other_attempt_results_cannot_close_any_acceptance(controlled_proofs, change):
    session, context, report, issued, *_ = controlled_proofs
    if change == "copied-report":
        report = deepcopy(report)
    elif change == "changed-report":
        report["decision"] = "forged"
    elif change == "other-attempt":
        context = replace(context, phase={"id": "replay-verify", "attempt": "replay", "operation": "verify"})
    elif change == "copied-context":
        context = replace(context)
    elif change == "controlled-mode":
        context = replace(context, execution_mode="controlled-test")
    elif change == "missing-origin":
        issued.pop("verified_report")
    checks, _, paths = acceptance.assertions_for_verification(session, context, report)
    assert set(statuses(checks).values()) == {"unknown"} and not paths


@pytest.mark.parametrize(
    "change", ["wrong-fault-policy", "replacement-uid", "unrelated-policy-change", "missing-policy"]
)
def test_actual_incident_policy_must_match_qualified_fault(controlled_proofs, change):
    session, context, report, *_ = controlled_proofs
    rows = session._records[("primary", "fault")]["bundle"]["targets"]["mission"]["after"]["kubernetes"][
        "observations"
    ]["network_policies"]
    if change == "wrong-fault-policy":
        rows[0]["ingress"] = [{"from": [], "ports": []}]
    elif change == "replacement-uid":
        rows[0]["uid"] = "replacement"
    elif change == "unrelated-policy-change":
        other = deepcopy(rows[0])
        other.update(name="unrelated", uid="other")
        rows.append(other)
    else:
        rows.clear()
    checks, _, _ = acceptance.assertions_for_verification(session, context, report)
    wanted = "unknown" if change == "missing-policy" else "fail"
    assert statuses(checks)["exact-qualified-policy-fault"] == wanted
    assert statuses(checks)["scrape-failure-causally-isolated"] == wanted


@pytest.mark.parametrize(
    "condition,phase,target",
    [
        ("telemetry-outage", "fault", "mission"),
        ("mission-health", "fault", "mission"),
        ("runtime-images-and-ownership", "all", "reference"),
        ("functional-telemetry", "recovery", "mission"),
    ],
)
def test_success_overall_does_not_hide_independent_condition_failure(
    controlled_proofs, condition, phase, target
):
    _, _, report, *_ = controlled_proofs
    row = next(
        r
        for r in report["conditions"]
        if r["id"] == condition and r["phase"] == phase and r.get("target_id") == target
    )
    row["observed_state"] = "not-recovered"
    report.update(execution_status="complete", recovery="verified", decision="pass")
    reseal_current(controlled_proofs)
    checks, _, _ = acceptance.assertions_for_verification(*controlled_proofs[:3])
    assert statuses(checks)["scrape-failure-causally-isolated"] == "fail"


@pytest.mark.parametrize("name", ["_reference", "_mission", "_native_reports", "_patch"])
def test_missing_original_leaf_is_unknown_without_blanket_success(controlled_proofs, monkeypatch, name):
    monkeypatch.setattr(
        acceptance, name, lambda *a: (_ for _ in ()).throw(ValueError("missing direct input"))
    )
    checks, _, _ = acceptance.assertions_for_verification(*controlled_proofs[:3])
    wanted = {"_reference": [0, 1], "_mission": [2, 3], "_native_reports": [6], "_patch": [7]}[name]
    for index in wanted:
        assert checks[index].status == "unknown"
    assert any(row.status == "pass" for row in checks)


def test_replay_uses_separate_report_context_and_incident_records(controlled_proofs):
    session, context, report, issued, *_ = controlled_proofs
    primary = acceptance.assertions_for_verification(session, context, report)
    assert all(row.status == "pass" for row in primary[0])
    replay_context = replace(
        context, phase={"id": "replay-verify", "attempt": "replay", "operation": "verify"}
    )
    replay = acceptance.assertions_for_verification(session, replay_context, report)
    assert all(row.status == "unknown" for row in replay[0])
    assert issued["verified_report"][2] is context


def test_real_original_leaf_accessors_reject_json_authority(retained):
    session, old_context, _, _, _, contract, _ = retained
    context = PhaseContext(
        contract["run_id"],
        {"id": "primary-verify", "attempt": "primary", "operation": "verify"},
        contract["identities"],
        {},
        {},
        old_context.run_dir,
        time.monotonic() + 60,
        "operator",
    )
    session._locked_images = {"verified": True}
    session._runtime_image = {"verified": True}
    session._mission_subject = {"source_commit": "a" * 40}
    session._driver = SimpleNamespace(_native_session={"completed": True})
    with pytest.raises(ValueError):
        acceptance._reference(session, context, contract)
    with pytest.raises(ValueError):
        acceptance._mission(session, context)
    with pytest.raises(ValueError):
        acceptance._native_reports(session, context)


def test_dispatch_registers_every_canonical_d1_d2_name_once_per_attempt(controlled_proofs, monkeypatch):
    session, context, report, *_ = controlled_proofs
    from devsecops.factory.native_factory_session import NativeFactorySession
    from devsecops.factory.owned_runtime_inputs import OwnedRuntimeInputCoordinator

    native = object.__new__(NativeFactorySession)
    monkeypatch.setattr(native, "origin", lambda **kwargs: object())
    producer = object.__new__(OwnedRuntimeInputCoordinator)
    monkeypatch.setattr(producer, "refresh_images", lambda context: None)
    session._input_producer = producer
    session._driver = SimpleNamespace(_native_session=native)
    monkeypatch.setattr(session, "verify", lambda context, **kwargs: report)
    report.update(execution_status="complete", recovery="verified")
    reseal_current(controlled_proofs)
    outcome = session.dispatch(context)
    expected = json.loads(
        (
            Path(runtime.__file__).resolve().parents[3]
            / "demo/software-factory/scenarios/ironbank-mission-recovery-v1/assertions.json"
        ).read_bytes()
    )["cases"]
    actual = [(row.case_id, row.assertion_id) for row in outcome.assertions]
    assert len(actual) == len(set(actual))
    assert set(actual) == {(case, name) for case in ("D1", "D2") for name in expected[case]}
    assert all(row.status == "pass" for row in outcome.assertions)
    assert {p.name for p in outcome.artifacts} >= {
        "acceptance.json",
        "recovery.json",
        "controlled-current-image-proof.json",
    }


def test_flat_counter_without_failed_scrapes_does_not_prove_causal_network_failure(controlled_proofs):
    session, context, report, *_ = controlled_proofs
    samples = session._records[("primary", "fault")]["bundle"]["targets"]["mission"]["prometheus"][
        "observations"
    ]["scrape"]["samples"]
    for sample in samples:
        sample["value"] = 1
    checks, _, _ = acceptance.assertions_for_verification(session, context, report)
    assert statuses(checks)["exact-qualified-policy-fault"] == "pass"
    assert statuses(checks)["scrape-failure-causally-isolated"] == "unknown"


def test_unrelated_reference_policy_changes_prevent_causal_acceptance(controlled_proofs):
    session, context, report, *_ = controlled_proofs
    rows = session._records[("primary", "fault")]["bundle"]["targets"]["reference"]["after"]["kubernetes"][
        "observations"
    ]["network_policies"]
    if rows:
        rows[0]["uid"] = "replaced-reference-policy"
    else:
        rows.append({"namespace": "demo", "name": "new-policy", "uid": "new"})
    checks, _, _ = acceptance.assertions_for_verification(session, context, report)
    assert statuses(checks)["scrape-failure-causally-isolated"] == "fail"


# Exercise the actual CI-history/runtime verifier with its controlled wire/crypto
# fixture, instead of replacing acceptance._mission or its admission accessor.

runtime_case = _runtime_case_fixture
case = _ci_case_fixture
wire = _wire_fixture


def test_mission_leaf_uses_actual_runtime_verifier_history_and_unchanged_subject(runtime_case, wire):
    from devsecops.factory import ci_publication

    data = runtime_case
    history_responses(wire, data["base"]["args"]["selector_raw"])
    cap = ci_publication.refresh_runtime_image(**data["args"])
    current = cap.describe()
    fields = (
        "run_scope",
        "source_commit",
        "architecture",
        "origin",
        "repository",
        "manifest_digest",
        "config_digest",
        "policy_sha256",
        "trust_policy_sha256",
        "publication_config_sha256",
        "base",
        "ci",
    )
    subject = {name: deepcopy(current[name]) for name in fields}
    subject["ci"].pop("observed_at", None)
    session = SimpleNamespace(_runtime_image=cap, _mission_subject=subject)
    context = SimpleNamespace(identities=current["run_scope"], run_id=current["ci"]["run_id"])
    image, paths = acceptance._mission(session, context)
    assert image["current_policy"]["decision"] == "pass"
    assert Path(image["history_readback"]["path"]) in paths
    session._mission_subject["config_digest"] = "sha256:" + "0" * 64
    with pytest.raises(acceptance._Conflict, match="selected-build-subject-changed"):
        acceptance._mission(session, context)
    session._mission_subject = subject = {name: deepcopy(current[name]) for name in fields}
    subject["ci"].pop("observed_at", None)
    history_path = Path(current["history_readback"]["path"])
    history_path.chmod(0o600)
    history_path.write_bytes(b"{}")
    with pytest.raises(ValueError):
        acceptance._mission(session, context)


@pytest.mark.parametrize("change", ["predicate", "platform", "config", "repository"])
def test_reference_leaf_requires_exact_locked_subject_and_predicates(retained, monkeypatch, change):
    from devsecops.factory import locked_images

    session, old, _, _, _, contract, _ = retained
    locked = contract["factory_lock"]["images"]["reference_web"]
    image = {
        "repository": locked["repository"],
        "requested_digest": "sha256:" + locked["manifest_sha256"],
        "platform_manifest_digest": "sha256:" + locked["platform_manifest_sha256"],
        "config_digest": "sha256:" + locked["config_sha256"],
        "architecture": locked["architecture"],
        "required_predicates": list(locked["required_predicates"]),
    }
    session._locked_images = object()
    marker = old.run_dir / "controlled-locked-proof.json"
    marker.write_bytes(b"{}")
    monkeypatch.setattr(locked_images, "require_locked_images", lambda origin, **scope: {"reference": image})
    monkeypatch.setattr(locked_images, "locked_image_artifacts", lambda origin, **scope: (marker,))
    assert acceptance._reference(session, old, contract) == (image, (marker,))
    if change == "predicate":
        image["required_predicates"] = ["unexpected-predicate"]
    elif change == "platform":
        image["platform_manifest_digest"] = "sha256:" + "0" * 64
    elif change == "config":
        image["config_digest"] = "sha256:" + "0" * 64
    else:
        image["repository"] = "different/repository"
    with pytest.raises(acceptance._Conflict):
        acceptance._reference(session, old, contract)


@pytest.mark.parametrize("change", ["attempt", "workflow", "mode", "runtime-authority", "bytes"])
def test_native_report_leaf_rechecks_original_type_attempt_and_retained_bytes(retained, monkeypatch, change):
    import os
    from devsecops.factory import native_reports
    from devsecops.factory.native_factory_session import NativeFactorySession

    session, old, _, _, _, contract, _ = retained
    context = SimpleNamespace(
        phase={"id": "primary-verify", "attempt": "primary", "operation": "verify"},
        run_id=contract["run_id"],
        identities=contract["identities"],
    )
    native = object.__new__(NativeFactorySession)
    origins = {}
    for suffix, workflow in (
        ("review", "review-kubernetes-change"),
        ("diagnose", "diagnose-gitops-deployment"),
    ):
        origin = object.__new__(native_reports.ValidatedNativeReport)
        path = old.run_dir / ("controlled-native-" + suffix + ".json")
        path.write_bytes(b'{"controlled_boundary":true}')
        document = {
            "step_id": "primary-" + suffix,
            "workflow": workflow,
            "run_id": context.run_id,
            "identities": dict(context.identities),
            "execution_mode": "direct",
            "runtime_authority": "not-issued",
        }
        native_reports._REPORTS[origin] = {
            "pid": os.getpid(),
            "document": document,
            "files": {path: path.read_bytes()},
        }
        origins["primary-" + suffix] = origin
    monkeypatch.setattr(native, "report", lambda *, step_id: origins[step_id])
    session._driver = SimpleNamespace(_native_session=native)
    docs, paths = acceptance._native_reports(session, context)
    assert set(docs) == {"review", "diagnose"} and len(paths) == 2
    doc = native_reports._REPORTS[origins["primary-review"]]["document"]
    if change == "attempt":
        doc["step_id"] = "replay-review"
    elif change == "workflow":
        doc["workflow"] = "verify-gitops-recovery"
    elif change == "mode":
        doc["execution_mode"] = "controlled-test"
    elif change == "runtime-authority":
        doc["runtime_authority"] = "issued"
    else:
        paths[0].write_bytes(b"changed")
    with pytest.raises(ValueError):
        acceptance._native_reports(session, context)


@pytest.mark.parametrize("change", ["patch", "attempt", "late-finish"])
def test_patch_leaf_binds_original_digest_and_completion_before_validation(retained, monkeypatch, change):
    from devsecops.factory import correction_phase, native_provenance
    from devsecops.factory.native_factory_session import NativeFactorySession
    from devsecops.factory.state import _sha
    from test_runtime_recovery import v2_execution

    session, old, _, _, bundles, contract, evidence = retained
    context = PhaseContext(
        contract["run_id"],
        {"id": "primary-verify", "attempt": "primary", "operation": "verify"},
        contract["identities"],
        {},
        {},
        old.run_dir,
        old.deadline,
        "operator",
    )
    native = object.__new__(NativeFactorySession)
    origin = object()
    monkeypatch.setattr(native, "origin", lambda *, attempt: origin)
    session._driver = SimpleNamespace(_native_session=native)
    monkeypatch.setattr(
        correction_phase,
        "require_validated_correction",
        lambda *args, **scope: {"execution_mode": "operator", "origin": origin},
    )
    monkeypatch.setattr(
        native_provenance,
        "verify_native_execution_origin",
        lambda *args, **scope: {"verified": True, "execution_mode": "direct"},
    )
    execution = v2_execution(evidence)
    artifacts = {
        "native_execution": canonical_bytes(execution),
        "patch": evidence["patch"],
        "native_validation": canonical_bytes(evidence["native_validation"]),
    }
    report = verify_recovery(
        bundles["baseline"],
        bundles["fault"],
        bundles["recovery"],
        contract=contract,
        evidence=evidence,
        _test_provenance=TestProvenance(),
        now=NOW + timedelta(seconds=155),
    )
    assert acceptance._patch(session, context, {"artifacts": artifacts}, report, set(ASSURANCES))[
        "patch_sha256"
    ] == _sha(evidence["patch"])
    if change == "patch":
        artifacts["patch"] += b"changed"
    elif change == "attempt":
        execution["attempt_id"] = "replay"
    else:
        execution["finished_at"] = (NOW + timedelta(seconds=120)).isoformat()
    artifacts["native_execution"] = canonical_bytes(execution)
    with pytest.raises(acceptance._Conflict):
        acceptance._patch(session, context, {"artifacts": artifacts}, report, set(ASSURANCES))


@pytest.mark.parametrize("limit", ["count", "bytes"])
def test_additional_original_evidence_never_silently_exceeds_phase_limits(
    controlled_proofs, monkeypatch, limit
):
    from devsecops.factory.state import StateError

    if limit == "count":
        monkeypatch.setattr(acceptance, "MAX_RESULT_ARTIFACTS", 3)
    else:
        monkeypatch.setattr(acceptance, "MAX_ARTIFACT_TOTAL_BYTES", 1)
    with pytest.raises(StateError, match="exceeds"):
        acceptance.assertions_for_verification(*controlled_proofs[:3])
