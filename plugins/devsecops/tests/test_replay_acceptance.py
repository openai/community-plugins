"""D6 evidence checks on explicit controlled documents; no live acceptance."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from devsecops.factory.replay_acceptance import (
    OwnedReplayAcceptance,
    _cleanup_checks,
    _runner_identity,
    _replay_checks,
    _reset_binding,
)
from devsecops.factory.state import StateError

ROOT = Path(__file__).resolve().parents[1]


def deliveries():
    common = {
        "run_id": "run",
        "delivery_stage": "merged-watched-branch",
        "tree": "d" * 40,
        "source_commit": "e" * 40,
        "repository_url": "https://owned.example.test/deployment.git",
        "project_id": 13,
        "image_refs": ["registry.example.test/mission@sha256:" + "f" * 64],
    }
    rows = [
        {**common, "deployment_commit": letter * 40, "watched_revision": letter * 40, "phase": phase}
        for letter, phase in [("a", "build-promote"), ("b", "primary-deliver"), ("c", "reset")]
    ]
    rows[2].update(
        attempt="replay",
        operation_id="reset-operation",
        parent="b" * 40,
        source_kind="qualified-policy-change",
        content_already_baseline=True,
        source_provenance={
            "kind": "qualified-policy-source",
            "run_id": "run",
            "operation_id": "reset-operation",
            "phase": "reset",
            "previous_commit": "b" * 40,
            "qualification_sha256": "1" * 64,
            "native_request_sha256": None,
        },
    )
    return rows


def test_reset_accepts_new_watched_marker_commit_with_exact_original_baseline_content():
    value = _reset_binding(*deliveries(), run_id="run", operation_id="reset-operation")
    assert value["reset_commit"] != value["baseline_commit"]
    assert value["restored_tree"] == "d" * 40


@pytest.mark.parametrize(
    "change", ["tree", "image", "reused-commit", "different-parent", "review-only", "unqualified-source"]
)
def test_reset_rejects_unrestored_or_unobserved_delivery(change):
    rows = deliveries()
    if change == "tree":
        rows[2]["tree"] = "9" * 40
    elif change == "image":
        rows[2]["image_refs"] = ["different-image"]
    elif change == "reused-commit":
        rows[2]["deployment_commit"] = rows[2]["watched_revision"] = "b" * 40
    elif change == "different-parent":
        rows[2]["parent"] = "9" * 40
    elif change == "review-only":
        rows[2]["delivery_stage"] = "review-branch"
    else:
        rows[2]["source_provenance"]["qualification_sha256"] = "unqualified"
    with pytest.raises(StateError):
        _reset_binding(*rows, run_id="run", operation_id="reset-operation")


def history():
    directory = ROOT / "demo/software-factory/scenarios/ironbank-mission-recovery-v1"
    scenario = json.loads((directory / "scenario.json").read_bytes())
    assertions = json.loads((directory / "assertions.json").read_bytes())
    records = []
    for phase in scenario["phases"][:-1]:
        checks = []
        if phase["operation"] == "verify":
            checks = [
                {"case_id": case, "assertion_id": name, "status": "pass"}
                for case in ("D1", "D2")
                for name in assertions["cases"][case]
            ]
        records.append({"phase_id": phase["id"], "outcome": "completed", "assertions": checks})
    native = [
        {"step_id": phase["id"], "request_id": "request-" + phase["id"], "status": "completed"}
        for phase in scenario["phases"]
        if phase["operation"] == "native"
    ]
    state = {
        "run_id": "run",
        "lifecycle": {"phases": scenario["phases"], "phase_records": records, "abort": None},
        "native_results": native,
        "native_requests": deepcopy(native),
    }
    return state, assertions


def test_replay_requires_every_actual_primary_and_replay_assertion_and_distinct_native_requests():
    state, assertions = history()
    value = _replay_checks(state, assertions, child_run_id="child", child_request_id="interrupted-request")
    assert value["replay_count"] == 1 and len(value["main_request_ids"]) == 8
    assert value["accepted_d1_d2_per_attempt"] == 22


@pytest.mark.parametrize(
    "change",
    [
        "missing-proof",
        "unknown-proof",
        "failed-proof",
        "duplicated-request",
        "interrupted-is-main",
        "second-reset",
        "aborted",
    ],
)
def test_replay_does_not_convert_missing_failed_or_duplicated_history_to_success(change):
    state, assertions = history()
    child = "interrupted-request"
    checks = next(
        row["assertions"] for row in state["lifecycle"]["phase_records"] if row["phase_id"] == "replay-verify"
    )
    if change == "missing-proof":
        checks.pop()
    elif change == "unknown-proof":
        checks[0]["status"] = "unknown"
    elif change == "failed-proof":
        checks[0]["status"] = "fail"
    elif change == "duplicated-request":
        state["native_results"][-1]["request_id"] = state["native_results"][0]["request_id"]
    elif change == "interrupted-is-main":
        child = state["native_results"][0]["request_id"]
    elif change == "second-reset":
        state["lifecycle"]["phase_records"].insert(19, deepcopy(state["lifecycle"]["phase_records"][18]))
    else:
        state["lifecycle"]["abort"] = {"outcome": "interrupted"}
    with pytest.raises(StateError):
        _replay_checks(state, assertions, child_run_id="child", child_request_id=child)


def cleaned():
    scope = {"package_sha256": "a" * 64, "environment_sha256": "b" * 64}
    state = {
        "run_id": "run",
        "identities": scope,
        "execution_status": "released",
        "environment_reserved": False,
        "active_mutation": None,
        "active_native": None,
        "lifecycle": {"final_result": None},
    }
    common = {
        "complete_cleanup": True,
        "mutation_effect": "applied",
        "unresolved_operations": [],
        "resources": [{"disposition": "remove", "state": "absent"}],
    }
    components = {
        name: deepcopy(common)
        for name in (
            "foundation",
            "registry",
            "services",
            "service-links",
            "kubernetes",
            "writer",
            "reader",
            "gitlab",
            "runner-service",
            "runner-routes",
            "runner-vm",
        )
    }
    components["foundation"].update(kind="owned-fresh-rke2-foundation")
    components["foundation"]["resources"].append(
        {"kind": "original-source", "disposition": "preserve", "state": "preserved"}
    )
    components["registry"]["observation"] = {
        "inflight": None,
        "cleanup": "owned-runtime-and-private-material-absent",
    }
    components["service-links"]["kind"] = "owned-service-links"
    components["runner-vm"]["resources"].append(
        {"kind": "supplied-assets", "disposition": "preserve", "state": "preserved"}
    )
    report = {
        "kind": "canonical-owned-cleanup",
        "inventory_contract": "owned-runner-vm-v1",
        "run_id": "run",
        "identities": scope,
        "operation_id": "cleanup-operation",
        "complete_cleanup": True,
        "mutation_effect": "applied",
        "gaps": [],
        "components": components,
        "never_dispatched_scopes": [],
        "resident_no_effects_closure": False,
    }
    validation = {
        "run_id": "run",
        "environment_sha256": scope["environment_sha256"],
        "operation_id": "cleanup-operation",
        "checks": [
            "owned-resource-inventory-closed",
            "owned-resource-absence-observed",
            "retained-resources-protected",
            "cleanup-current-operation-bound",
        ],
    }
    return state, report, validation


def test_cleanup_acceptance_is_bound_to_full_scope_protection_and_atomic_release_before_terminal():
    value = _cleanup_checks(*cleaned(), inventory_contract="owned-runner-vm-v1")
    assert set(value["component_scopes"]) == {
        "foundation",
        "registry",
        "services",
        "service-links",
        "kubernetes",
        "writer",
        "reader",
        "gitlab",
        "runner-service",
        "runner-routes",
        "runner-vm",
    }
    assert value["published_before_terminal"]
    assert value["protected_resources"][0]["kind"] == "original-source"


@pytest.mark.parametrize(
    "change",
    ["not-released", "early-cleanup", "residual", "changed-source", "wrong-validation", "already-terminal"],
)
def test_cleanup_rejects_early_partial_changed_or_unbound_observations(change):
    state, report, validation = cleaned()
    if change == "not-released":
        state["environment_reserved"] = True
    elif change == "early-cleanup":
        report["components"].pop("kubernetes")
        report["never_dispatched_scopes"] = ["kubernetes"]
    elif change == "residual":
        report["components"]["service-links"]["resources"][0]["state"] = "present"
    elif change == "changed-source":
        report["components"]["foundation"]["resources"][1]["state"] = "changed"
    elif change == "wrong-validation":
        validation["operation_id"] = "other-operation"
    else:
        state["lifecycle"]["final_result"] = {"canonical_success": True}
    with pytest.raises(StateError):
        _cleanup_checks(state, report, validation, inventory_contract="owned-runner-vm-v1")


@pytest.mark.parametrize("scope", ["runner-service", "runner-routes", "runner-vm"])
@pytest.mark.parametrize("change", ["missing", "residue", "unknown", "never-started"])
def test_runner_cleanup_scope_cannot_be_omitted_or_substituted(scope, change):
    state, report, validation = cleaned()
    if change == "missing":
        report["components"].pop(scope)
        report["components"]["unrelated-extra-scope"] = deepcopy(report["components"]["services"])
    elif change == "residue":
        report["components"][scope]["resources"][0]["state"] = "present"
    elif change == "unknown":
        report["components"][scope]["unresolved_operations"] = ["unobserved-write"]
    else:
        report["never_dispatched_scopes"] = [scope]
    with pytest.raises(StateError):
        _cleanup_checks(state, report, validation, inventory_contract="owned-runner-vm-v1")


@pytest.mark.parametrize("selected", ["owned-container-runner-v1", None, "unknown-contract"])
def test_legacy_or_unselected_cleanup_cannot_pass_full_d6(selected):
    state, report, validation = cleaned()
    report["inventory_contract"] = selected
    with pytest.raises(StateError, match="exact runner-VM"):
        _cleanup_checks(state, report, validation, inventory_contract=selected)


@pytest.mark.parametrize("change", ["missing-source", "changed-source", "preserved-as-absent", "no-removals"])
def test_runner_vm_cleanup_must_preserve_supplied_sources_and_remove_owned_payload(change):
    state, report, validation = cleaned()
    rows = report["components"]["runner-vm"]["resources"]
    if change == "missing-source":
        rows.pop()
    elif change == "changed-source":
        rows[-1]["state"] = "changed"
    elif change == "preserved-as-absent":
        rows[-1]["state"] = "absent"
    else:
        rows.pop(0)
    with pytest.raises(StateError):
        _cleanup_checks(state, report, validation, inventory_contract="owned-runner-vm-v1")


def runner_identity(run_id="run"):
    return {
        "binding": "c" * 64,
        "generation": "runner-vm-" + run_id,
        "guest_boot_id": "11111111-2222-3333-4444-555555555555",
        "guest_uid": 10001,
        "guest_gid": 10001,
        "root_identity": {"device": 1, "inode": 101},
        "disk_identity": {"device": 1, "inode": 102},
        "runtime_id": "d" * 64,
        "network_id": "e" * 64,
        "ssh_host_key_sha256": "f" * 64,
    }


def test_complete_original_nonroot_runner_identity_is_auditable_data_only():
    value = runner_identity()
    assert _runner_identity(value, run_id="run") == value


@pytest.mark.parametrize(
    "change",
    [
        "root-user",
        "root-group",
        "bool-user",
        "missing-boot",
        "other-generation",
        "missing-binding",
        "missing-runtime",
        "missing-network",
        "missing-disk",
        "reused-inode",
        "zero-inode",
        "extra-field",
    ],
)
def test_incomplete_preboot_or_changed_runner_identity_cannot_qualify_d6(change):
    value = runner_identity()
    if change == "root-user":
        value["guest_uid"] = 0
    elif change == "root-group":
        value["guest_gid"] = 0
    elif change == "bool-user":
        value["guest_uid"] = True
    elif change == "missing-boot":
        value["guest_boot_id"] = None
    elif change == "other-generation":
        value["generation"] = "runner-vm-other"
    elif change == "missing-binding":
        value["binding"] = ""
    elif change == "missing-runtime":
        value["runtime_id"] = None
    elif change == "missing-network":
        value["network_id"] = None
    elif change == "missing-disk":
        value["disk_identity"] = None
    elif change == "reused-inode":
        value["disk_identity"] = dict(value["root_identity"])
    elif change == "zero-inode":
        value["disk_identity"]["inode"] = 0
    else:
        value["saved_success"] = True
    with pytest.raises(StateError):
        _runner_identity(value, run_id="run")


def test_saved_document_cannot_construct_replay_authority():
    with pytest.raises(StateError, match="original concrete"):
        OwnedReplayAcceptance({"canonical_success": True})
