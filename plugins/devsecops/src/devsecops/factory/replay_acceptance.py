"""D6 acceptance from this process's interrupted child, Git reset and cleanup.

The final check is read-only. It cannot restore a native, delivery or cleanup
capability from a report, and it performs no API access after resource removal.
"""

from copy import deepcopy
from dataclasses import replace
import os
import re

from .lifecycle import AssertionResult, PhaseContext, PhaseOutcome
from .state import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_TOTAL_BYTES,
    StateError,
    _canonical,
    _exclusive,
    _json,
    _read,
    _sha,
)

ASSERTIONS = (
    "interruption-has-no-success-receipt",
    "baseline-restored-through-git",
    "one-new-attempt-d1-d2-replay",
    "unowned-resources-preserved",
    "owned-resource-deletions-verified",
    "residual-resources-reported",
    "cleanup-before-terminal-result",
)
_MAIN_STEPS = tuple(
    attempt + "-" + step
    for attempt in ("primary", "replay")
    for step in ("review", "diagnose", "correction", "recovery")
)
_RUNNER_CONTRACT = "owned-runner-vm-v1"
_CLEANUP_SCOPES = frozenset(
    {
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
)


def _runner_identity(value, *, run_id):
    """Check a retained original observation, never issue a VM capability."""
    fields = {
        "binding",
        "generation",
        "guest_boot_id",
        "guest_uid",
        "guest_gid",
        "root_identity",
        "disk_identity",
        "runtime_id",
        "network_id",
        "ssh_host_key_sha256",
    }
    if (
        type(value) is not dict
        or set(value) != fields
        or value["generation"] != "runner-vm-" + run_id
        or type(value["guest_uid"]) is not int
        or value["guest_uid"] != 10001
        or type(value["guest_gid"]) is not int
        or value["guest_gid"] != 10001
        or any(
            not isinstance(value[key], str) or not re.fullmatch("[a-f0-9]{64}", value[key])
            for key in ("binding", "runtime_id", "network_id", "ssh_host_key_sha256")
        )
        or not isinstance(value["guest_boot_id"], str)
        or not re.fullmatch("[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value["guest_boot_id"])
    ):
        raise StateError("D6 requires the complete original nonroot runner guest identity")
    for key in ("root_identity", "disk_identity"):
        if (
            type(value[key]) is not dict
            or set(value[key]) != {"device", "inode"}
            or any(type(item) is not int or item < 0 for item in value[key].values())
            or value[key]["inode"] == 0
        ):
            raise StateError("D6 runner guest filesystem identity is missing or changed")
    if value["root_identity"] == value["disk_identity"]:
        raise StateError("D6 runner root and disk cannot be the same filesystem object")
    return deepcopy(value)


def _runner_originals(cleanup, *, journal, report):
    """Audit original up/down handles after cleanup; no endpoint access."""
    from .owned_runner_vm import OwnedRunnerVM
    from .owned_runner_service import OwnedRunnerService
    from .runner_vm_routes import OwnedRunnerVMRoutes
    from .owned_linux import _plain

    selected = {
        "runner-vm": OwnedRunnerVM,
        "runner-service": OwnedRunnerService,
        "runner-routes": OwnedRunnerVMRoutes,
    }
    vm = cleanup.components.get("runner-vm")
    if (
        type(vm) is not OwnedRunnerVM
        or getattr(cleanup.components.get("runner-service"), "vm", None) is not vm
        or getattr(cleanup.components.get("runner-routes"), "vm", None) is not vm
        or getattr(vm, "_children", None)
        != {
            "runner": cleanup.components.get("runner-service"),
            "routes": cleanup.components.get("runner-routes"),
        }
    ):
        raise StateError("D6 requires the original runner service and routes bound to this VM")
    original, history = None, {}
    for kind, cls in selected.items():
        component, attempt = cleanup.components.get(kind), cleanup.attempts.get(kind)
        if (
            type(component) is not cls
            or component.journal is not journal
            or type(attempt) is not dict
            or attempt.get("up_outcome") is None
            or attempt.get("outcome") is None
            or not attempt.get("up_operation")
            or attempt.get("down_attempted") is not True
        ):
            raise StateError("D6 requires original runner creation and cleanup handles: " + kind)
        up = _plain(component.cleanup_observation(attempt["up_outcome"]))
        down = _plain(component.cleanup_observation(attempt["outcome"]))
        if (
            up.get("run_id") != journal.state["run_id"]
            or down.get("run_id") != journal.state["run_id"]
            or up.get("identities") != journal.identities
            or down.get("identities") != journal.identities
            or up.get("action") != "up"
            or up.get("operation_id") != attempt["up_operation"]
            or down.get("action") not in ("down", "reconcile")
            or down.get("operation_id") != attempt.get("down_operation")
            or down != report["components"].get(kind)
            or down != attempt.get("observation")
        ):
            raise StateError("D6 runner history differs from original accepted observations: " + kind)
        identity = _runner_identity(up.get("runner_vm"), run_id=journal.state["run_id"])
        if identity != down.get("runner_vm") or original is not None and identity != original:
            raise StateError("D6 runner service, routes and VM do not share one original guest")
        original = identity
        history[kind] = {
            "up_operation_id": up["operation_id"],
            "down_operation_id": down["operation_id"],
            "up_observation_sha256": _sha(_canonical(up)),
            "down_observation_sha256": _sha(_canonical(down)),
        }
        if kind == "runner-service":
            registration = {key: up.get(key) for key in ("runner_id", "runner_manager_system_identity")}
            if (
                type(registration["runner_id"]) is not int
                or registration["runner_id"] <= 0
                or not isinstance(registration["runner_manager_system_identity"], str)
                or not 1 <= len(registration["runner_manager_system_identity"]) <= 256
                or up.get("service_ready") is not True
                or down.get("service_ready") is not False
                or any(down.get(key) != value for key, value in registration.items())
            ):
                raise StateError("D6 runner service removal differs from its original running registration")
            history[kind]["registration"] = registration
    if len({row["up_operation_id"] for row in history.values()}) != 1:
        raise StateError("D6 runner components were not created by the same original provisioning operation")
    return {
        "runner_vm": original,
        "component_history": history,
        "basis": "original-resident-up-and-cleanup-observations",
    }


def _reset_binding(baseline, recovery, reset, *, run_id, operation_id):
    """Check actual authenticated deliveries; this function issues no origin."""
    rows = (baseline, recovery, reset)
    if any(
        row.get("run_id") != run_id
        or row.get("delivery_stage") != "merged-watched-branch"
        or row.get("watched_revision") != row.get("deployment_commit")
        or not re.fullmatch("[a-f0-9]{40}", row.get("deployment_commit", ""))
        for row in rows
    ):
        raise StateError("D6 requires the original watched Git deliveries")
    if (
        baseline.get("phase") != "build-promote"
        or recovery.get("phase") != "primary-deliver"
        or reset.get("phase") != "reset"
        or reset.get("attempt") != "replay"
        or reset.get("operation_id") != operation_id
        or reset.get("parent") != recovery["deployment_commit"]
        or reset["deployment_commit"] in {baseline["deployment_commit"], recovery["deployment_commit"]}
        or any(
            reset.get(key) != baseline.get(key) or reset.get(key) is None
            for key in ("tree", "source_commit", "repository_url", "project_id", "image_refs")
        )
        or reset.get("source_kind") != "qualified-policy-change"
    ):
        raise StateError(
            "D6 reset must create a new observed commit restoring the exact baseline tree and images"
        )
    source = reset.get("source_provenance", {})
    if (
        source.get("kind") != "qualified-policy-source"
        or source.get("run_id") != run_id
        or source.get("operation_id") != operation_id
        or source.get("phase") != "reset"
        or source.get("previous_commit") != recovery["deployment_commit"]
        or not re.fullmatch("[a-f0-9]{64}", source.get("qualification_sha256", ""))
        or source.get("native_request_sha256") is not None
    ):
        raise StateError("D6 reset differs from its original qualified baseline-restoration source")
    return {
        "baseline_commit": baseline["deployment_commit"],
        "recovery_commit": recovery["deployment_commit"],
        "reset_commit": reset["deployment_commit"],
        "restored_tree": reset["tree"],
        "source_commit": reset["source_commit"],
        "image_refs": deepcopy(reset["image_refs"]),
        "operation_id": operation_id,
        "source_provenance": deepcopy(source),
    }


def _replay_checks(state, assertion_document, *, child_run_id, child_request_id):
    """Require the actual accepted main history, not a count or fixture verdict."""
    life = state["lifecycle"]
    expected = [phase["id"] for phase in life["phases"] if phase["id"] != "finalize"]
    records = life["phase_records"]
    if (
        life.get("abort") is not None
        or [row["phase_id"] for row in records] != expected
        or len(set(expected)) != 32
        or any(row["outcome"] != "completed" for row in records)
    ):
        raise StateError("D6 needs exactly the completed primary, reset, replay and cleanup history")
    phases = {phase["id"]: phase for phase in life["phases"]}
    for attempt in ("primary", "replay"):
        seen = set()
        for row in records:
            if phases[row["phase_id"]]["attempt"] != attempt:
                continue
            for check in row["assertions"]:
                if check["case_id"] not in ("D1", "D2"):
                    continue
                if check["status"] != "pass":
                    raise StateError("D6 cannot promote failed or unknown primary/replay evidence")
                seen.add((check["case_id"], check["assertion_id"]))
        required = {(case, check) for case in ("D1", "D2") for check in assertion_document["cases"][case]}
        if not required or seen != required:
            raise StateError("D6 requires every original D1/D2 assertion in both attempts")
    native = state["native_results"]
    if (
        [row["step_id"] for row in native] != list(_MAIN_STEPS)
        or any(row["status"] != "completed" for row in native)
        or len({row["request_id"] for row in native}) != 8
        or child_request_id in {row["request_id"] for row in native}
        or child_run_id == state["run_id"]
    ):
        raise StateError("D6 needs eight distinct accepted main requests and a separate interrupted attempt")
    requests = state["native_requests"]
    if [row["step_id"] for row in requests] != list(_MAIN_STEPS) or {
        row["request_id"] for row in requests
    } != {row["request_id"] for row in native}:
        raise StateError("D6 main native request history differs from its accepted results")
    return {
        "main_steps": list(_MAIN_STEPS),
        "main_request_ids": [row["request_id"] for row in native],
        "replay_count": 1,
        "accepted_d1_d2_per_attempt": len(required),
    }


def _cleanup_checks(state, report, validation, *, inventory_contract):
    checks = {
        "owned-resource-inventory-closed",
        "owned-resource-absence-observed",
        "retained-resources-protected",
        "cleanup-current-operation-bound",
    }
    if (
        state["execution_status"] != "released"
        or state["environment_reserved"]
        or state["active_mutation"] is not None
        or state["active_native"] is not None
        or state["lifecycle"]["final_result"] is not None
        or report.get("kind") != "canonical-owned-cleanup"
        or report.get("complete_cleanup") is not True
        or report.get("mutation_effect") != "applied"
        or report.get("gaps") != []
        or report.get("run_id") != state["run_id"]
        or report.get("identities") != state["identities"]
        or validation.get("run_id") != state["run_id"]
        or validation.get("environment_sha256") != state["identities"]["environment_sha256"]
        or validation.get("operation_id") != report.get("operation_id")
        or not checks <= set(validation.get("checks", []))
    ):
        raise StateError("D6 needs the original validated and durably released complete cleanup")
    scopes = _CLEANUP_SCOPES
    if (
        inventory_contract != _RUNNER_CONTRACT
        or report.get("inventory_contract") != inventory_contract
        or set(report.get("components", {})) != scopes
        or report.get("never_dispatched_scopes") != []
        or report.get("resident_no_effects_closure") is not False
    ):
        raise StateError("D6 requires the exact runner-VM factory inventory, not legacy or early cleanup")
    from .owned_cleanup import OwnedCleanupCoordinator

    if any(
        not isinstance(value, dict) or not OwnedCleanupCoordinator._resolved(kind, value)
        for kind, value in report["components"].items()
    ):
        raise StateError("D6 has an unresolved component cleanup observation")
    foundation = report["components"]["foundation"]
    protected = [row for row in foundation.get("resources", []) if row.get("disposition") == "preserve"]
    if not protected or any(row.get("state") != "preserved" for row in protected):
        raise StateError("D6 lacks actual preservation of selected source and baseline resources")
    for name in ("runner-vm", "runner-service", "runner-routes"):
        rows = report["components"][name].get("resources", [])
        if (
            not rows
            or not any(row.get("disposition") == "remove" for row in rows)
            or any(
                row.get("disposition") not in ("remove", "preserve")
                or row.get("state") != ("absent" if row["disposition"] == "remove" else "preserved")
                for row in rows
            )
        ):
            raise StateError("D6 runner resource removal or preservation is incomplete: " + name)
    runner_protected = [
        row for row in report["components"]["runner-vm"]["resources"] if row.get("disposition") == "preserve"
    ]
    if not runner_protected:
        raise StateError("D6 lacks preservation of the runner VM source assets")
    removed = [
        row
        for value in report["components"].values()
        for row in value.get("resources", [])
        if row.get("disposition") == "remove"
    ]
    if not removed or any(row.get("state") != "absent" for row in removed):
        raise StateError("D6 owned resource absence is incomplete")
    return {
        "operation_id": report["operation_id"],
        "inventory_contract": inventory_contract,
        "component_scopes": sorted(scopes),
        "protected_resources": deepcopy(protected),
        "runner_protected_resources": deepcopy(runner_protected),
        "removed_resource_count": len(removed),
        "reported_gaps": [],
        "unowned_scope": "selected-preserved-inputs-and-original-baseline-resources",
        "published_before_terminal": True,
    }


class OwnedReplayAcceptance:
    def __init__(self, production):
        from .owned_production import OwnedProductionCoordinator

        if type(production) is not OwnedProductionCoordinator:
            raise StateError("D6 needs the original concrete production coordinator")
        self.production, self.journal, self.pid = production, production.journal, os.getpid()
        self.run_id, self.identities = self.journal.state["run_id"], dict(self.journal.identities)
        self._started, self._interruption, self._reset = False, None, None
        self._files, self._deliveries = {}, None

    def __reduce__(self):
        raise StateError("D6 acceptance cannot be restored from saved evidence")

    def _admit(self, context, operation):
        self.production._current()
        if (
            self.pid != os.getpid()
            or self.production.journal is not self.journal
            or self.journal.state["run_id"] != self.run_id
            or self.journal.identities != self.identities
            or type(context) is not PhaseContext
            or context.phase["id"] != operation
            or context.phase["operation"] != operation
            or context.run_id != self.run_id
            or context.run_dir != self.journal.run_dir
            or dict(context.identities) != self.identities
            or context.remaining_seconds() <= 0
        ):
            raise StateError("D6 requires its original current reset or finalization phase")
        self.production.driver._admit(context, context.deadline)
        self.production.driver._package(self.identities["package_sha256"], context.deadline)
        for path, pin in self._files.items():
            if _sha(_read(path, MAX_ARTIFACT_BYTES)) != pin:
                raise StateError("Original D6 acceptance bytes changed")

    def _save(self, name, value):
        path = self.journal.run_dir / "canonical-cases" / "D6" / name
        _exclusive(path, _canonical(value))
        self._files[path] = _sha(_read(path, MAX_ARTIFACT_BYTES))
        return path

    def begin_reset(self, context):
        self._admit(context, "reset")
        if self._started:
            raise StateError("D6 reset/interruption was already attempted")
        self.journal.current_mutation("reset", "reset")
        self._started = True
        root = self.journal.run_dir / "canonical-cases"
        root.mkdir(mode=0o700, exist_ok=True)
        (root / "D6").mkdir(mode=0o700)
        from .case_interruption import execute_interrupted_review, require_interruption

        self._interruption = execute_interrupted_review(
            context, production=self.production, output=root / "D6" / "interruption"
        )
        binding = require_interruption(self._interruption, journal=self.journal)
        self._interrupted(binding)
        self._save("interruption-acceptance.json", binding)

    def _interrupted(self, value):
        if (
            value.get("parent_run_id") != self.run_id
            or value.get("child_run_id") == self.run_id
            or not value.get("child_run_id")
            or not value.get("request_id")
            or value.get("status") != "cancelled"
            or value.get("spawned") is not True
            or value.get("reaped") is not True
            or value.get("accepted_results") != 0
            or value.get("usable_success_receipt") is not False
        ):
            raise StateError("D6 needs an actual spawned, reaped and cancelled separate native attempt")

    def retain_reset(self, context, outcome):
        self._admit(context, "reset")
        if self._interruption is None or self._reset is not None:
            raise StateError("D6 reset requires its original interruption and one new reset effect")
        if outcome.status != "completed" or outcome.mutation_effect != "applied":
            return outcome
        operations = self.production.operations
        keys = (("primary", "baseline"), ("primary", "recovery"), ("replay", "baseline"))
        self._deliveries = tuple(operations.delivery(*key) for key in keys)
        from .owned_git_delivery import require_delivery

        bindings = [require_delivery(value, journal=self.journal) for value in self._deliveries]
        self._reset = _reset_binding(
            *bindings, run_id=self.run_id, operation_id=self.journal.current_mutation("reset", "reset")
        )
        self._save(
            "reset-git-acceptance.json", {"run_id": self.run_id, "identities": self.identities, **self._reset}
        )
        return replace(outcome, artifacts=self._artifacts(outcome.artifacts))

    def _artifacts(self, other=()):
        from .case_interruption import interruption_artifacts

        paths = tuple(
            sorted(
                set(
                    (*other, *self._files, *interruption_artifacts(self._interruption, journal=self.journal))
                ),
                key=str,
            )
        )
        if (
            len(paths) > 255
            or sum(len(_read(path, MAX_ARTIFACT_BYTES)) for path in paths) > MAX_ARTIFACT_TOTAL_BYTES
        ):
            raise StateError("D6 original evidence exceeds the phase artifact bound")
        return paths

    def finalize(self, context):
        self._admit(context, "finalize")
        if self._reset is None or self._deliveries is None or self._interruption is None:
            raise StateError("D6 has no original reset/interruption proof")
        from .case_interruption import require_interruption
        from .owned_git_delivery import require_delivery
        from .owned_cleanup import OwnedCleanupCoordinator
        from .native_factory_session import NativeFactorySession
        from .canonical_bootstrap import OwnedBootstrapCoordinator

        interrupted = require_interruption(self._interruption, journal=self.journal)
        self._interrupted(interrupted)
        bindings = [require_delivery(value, journal=self.journal) for value in self._deliveries]
        if (
            _reset_binding(*bindings, run_id=self.run_id, operation_id=self._reset["operation_id"])
            != self._reset
        ):
            raise StateError("D6 original Git reset binding changed")
        state = self.journal.state
        replay = _replay_checks(
            state,
            context.inputs["assertions"],
            child_run_id=interrupted["child_run_id"],
            child_request_id=interrupted["request_id"],
        )
        native = self.production.driver._native_session
        if (
            type(native) is not NativeFactorySession
            or native._journal is not self.journal
            or native._driver is not self.production.driver
        ):
            raise StateError("D6 needs the original resident main native session")
        # The same accessor already binds each accepted result/request/manifest
        # to its resident origin. Audit all eight again, without duplicating
        # their trace files into this final phase or renewing model authority.
        for step in _MAIN_STEPS:
            native.completed_step_artifacts(step)
        cleanup = self.production.cleanup
        if (
            type(cleanup) is not OwnedCleanupCoordinator
            or cleanup.journal is not self.journal
            or cleanup.pid != os.getpid()
            or cleanup._terminal is None
        ):
            raise StateError("D6 requires the original resident cleanup observer")
        report = cleanup._observations.observe(cleanup._terminal)
        originals = tuple(_read(path, MAX_ARTIFACT_BYTES) for path in cleanup._terminal.artifacts)
        frozen = tuple(
            _read(self.journal._artifact_path(ref["path"]), MAX_ARTIFACT_BYTES)
            for ref in state["cleanup_artifacts"]
        )
        if originals != frozen:
            raise StateError("D6 cleanup differs from the exact atomically accepted receipts")
        for kind, attempt in cleanup.attempts.items():
            if attempt["outcome"] is not None:
                from .owned_linux import _plain

                if (
                    _plain(cleanup.components[kind].cleanup_observation(attempt["outcome"]))
                    != attempt["observation"]
                ):
                    raise StateError("D6 original cleanup component evidence changed")
        validation_path = self.journal._artifact_path(state["cleanup_validation"]["path"])
        validation = _json(_read(validation_path, MAX_ARTIFACT_BYTES))
        bootstrap = self.production.bootstrap
        if (
            type(bootstrap) is not OwnedBootstrapCoordinator
            or bootstrap.journal is not self.journal
            or bootstrap.driver is not self.production.driver
            or bootstrap.cleanup is not cleanup
            or any(
                getattr(bootstrap, attribute, None) is not cleanup.components.get(scope)
                for attribute, scope in (
                    ("_runner_vm", "runner-vm"),
                    ("_runner_service", "runner-service"),
                    ("_runner_routes", "runner-routes"),
                )
            )
        ):
            raise StateError("D6 requires the original bootstrap inventory selection")
        cleaned = _cleanup_checks(
            state, report, validation, inventory_contract=bootstrap.cleanup_inventory_contract()
        )
        cleaned["runner"] = _runner_originals(cleanup, journal=self.journal, report=report)
        self._save(
            "case-result.json",
            {
                "schema_version": 1,
                "kind": "original-live-replay-acceptance",
                "run_id": self.run_id,
                "identities": self.identities,
                "interruption": interrupted,
                "git_reset": self._reset,
                "replay": replay,
                "cleanup": cleaned,
                "assertions": list(ASSERTIONS),
                "authority": "historical-acceptance-only",
                "canonical_success": False,
            },
        )
        return PhaseOutcome(
            "completed",
            "owned-live-replay-and-cleanup-observed",
            self._artifacts((*cleanup._terminal.artifacts, validation_path)),
            tuple(AssertionResult("D6", name, "pass") for name in ASSERTIONS),
            mutation_effect="not_applied",
        )
