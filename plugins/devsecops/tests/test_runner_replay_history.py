"""Original-handle acceptance with controlled observations; no guest execution.

The concrete production classes are required. Only their external acquisition
boundary is replaced by the actual resident byte-auditing observation store.
"""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from devsecops.factory.cleanup_observation import ComponentObservations
from devsecops.factory.lifecycle import PhaseOutcome
from devsecops.factory.replay_acceptance import _runner_originals
from devsecops.factory.state import StateError, _canonical
from tests.test_replay_acceptance import cleaned, runner_identity


def retained_runner_children(tmp_path, journal, report, monkeypatch):
    from devsecops.factory.owned_runner_vm import OwnedRunnerVM
    from devsecops.factory.owned_runner_service import OwnedRunnerService
    from devsecops.factory.runner_vm_routes import OwnedRunnerVMRoutes

    classes = {
        "runner-vm": OwnedRunnerVM,
        "runner-service": OwnedRunnerService,
        "runner-routes": OwnedRunnerVMRoutes,
    }
    cleanup = SimpleNamespace(components={}, attempts={})
    for kind, cls in classes.items():
        component = object.__new__(cls)
        component.journal = journal
        component._acceptance_test_observations = ComponentObservations()

        def observe(self, outcome):
            return self._acceptance_test_observations.observe(outcome)

        monkeypatch.setattr(cls, "cleanup_observation", observe)
        down = report["components"][kind]
        down.update(
            kind={
                "runner-vm": "owned-runner-vm",
                "runner-service": "owned-runner-service",
                "runner-routes": "owned-runner-vm-routes",
            }[kind],
            action="down",
            run_id=journal.state["run_id"],
            identities=dict(journal.identities),
            operation_id=report["operation_id"],
            runner_vm=runner_identity(journal.state["run_id"]),
        )
        up = deepcopy(down)
        up.update(action="up", operation_id="original-up-operation", complete_cleanup=False)
        if kind == "runner-service":
            up.update(runner_id=37, runner_manager_system_identity="s_controlled_runner", service_ready=True)
            down.update(
                runner_id=37, runner_manager_system_identity="s_controlled_runner", service_ready=False
            )
        up_path, down_path = tmp_path / (kind + "-up.json"), tmp_path / (kind + "-down.json")
        up_path.write_bytes(_canonical(up))
        down_path.write_bytes(_canonical(down))
        up_outcome = component._acceptance_test_observations.retain(
            PhaseOutcome("incomplete", "controlled-runner-up", (up_path,), mutation_effect="applied"), up
        )
        down_outcome = component._acceptance_test_observations.retain(
            PhaseOutcome("incomplete", "controlled-runner-down", (down_path,), mutation_effect="applied"),
            down,
        )
        cleanup.components[kind] = component
        cleanup.attempts[kind] = {
            "up_operation": up["operation_id"],
            "up_outcome": up_outcome,
            "down_attempted": True,
            "down_operation": down["operation_id"],
            "outcome": down_outcome,
            "observation": deepcopy(down),
        }
    vm = cleanup.components["runner-vm"]
    cleanup.components["runner-service"].vm = cleanup.components["runner-routes"].vm = vm
    vm._children = {
        "runner": cleanup.components["runner-service"],
        "routes": cleanup.components["runner-routes"],
    }
    return cleanup


def original_fixture(tmp_path, monkeypatch):
    state, report, _ = cleaned()
    journal = SimpleNamespace(state=state, identities=state["identities"])
    cleanup = retained_runner_children(tmp_path, journal, report, monkeypatch)
    return journal, report, cleanup


def test_original_runner_creation_and_removal_bind_one_guest_without_postcleanup_access(
    tmp_path, monkeypatch
):
    journal, report, cleanup = original_fixture(tmp_path, monkeypatch)
    value = _runner_originals(cleanup, journal=journal, report=report)
    assert value["runner_vm"] == runner_identity()
    assert set(value["component_history"]) == {"runner-vm", "runner-service", "runner-routes"}
    assert value["basis"] == "original-resident-up-and-cleanup-observations"
    assert all(
        row["down_operation_id"] == report["operation_id"] for row in value["component_history"].values()
    )


@pytest.mark.parametrize("scope", ["runner-vm", "runner-service", "runner-routes"])
@pytest.mark.parametrize(
    "change",
    [
        "missing-up",
        "missing-down",
        "never-down-attempted",
        "saved-object",
        "copied-up-handle",
        "changed-up-bytes",
        "changed-down-bytes",
        "wrong-journal",
        "other-up-operation",
        "other-down-operation",
        "changed-report",
        "different-vm",
    ],
)
def test_original_runner_history_rejects_missing_replaced_or_changed_handles(
    tmp_path, monkeypatch, scope, change
):
    journal, report, cleanup = original_fixture(tmp_path, monkeypatch)
    attempt = cleanup.attempts[scope]
    if change == "missing-up":
        attempt["up_outcome"] = None
    elif change == "missing-down":
        attempt["outcome"] = None
    elif change == "never-down-attempted":
        attempt["down_attempted"] = False
    elif change == "saved-object":
        cleanup.components[scope] = {"runner_vm": runner_identity()}
    elif change == "copied-up-handle":
        attempt["up_outcome"] = replace(attempt["up_outcome"])
    elif change == "changed-up-bytes":
        attempt["up_outcome"].artifacts[0].write_bytes(b"{}")
    elif change == "changed-down-bytes":
        attempt["outcome"].artifacts[0].write_bytes(b"{}")
    elif change == "wrong-journal":
        cleanup.components[scope].journal = object()
    elif change == "other-up-operation":
        attempt["up_operation"] = "different-up"
    elif change == "other-down-operation":
        attempt["down_operation"] = "different-down"
    elif change == "different-vm":
        if scope == "runner-vm":
            cleanup.components[scope]._children = {}
        else:
            cleanup.components[scope].vm = object()
    else:
        report["components"][scope]["runner_vm"]["guest_boot_id"] = "99999999-2222-3333-4444-555555555555"
    with pytest.raises(StateError):
        _runner_originals(cleanup, journal=journal, report=report)


@pytest.mark.parametrize(
    "change",
    [
        "different-guest",
        "different-down-guest",
        "null-preboot",
        "root-user",
        "wrong-runner-id",
        "wrong-manager",
        "no-ready-runner",
        "runner-still-ready-after-cleanup",
    ],
)
def test_authentic_original_handles_still_require_one_completed_nonroot_guest(tmp_path, monkeypatch, change):
    journal, report, cleanup = original_fixture(tmp_path, monkeypatch)
    kind = "runner-service"
    component, attempt = cleanup.components[kind], cleanup.attempts[kind]
    up = component.cleanup_observation(attempt["up_outcome"])
    down = component.cleanup_observation(attempt["outcome"])
    for row in (up, down) if change != "different-down-guest" else (down,):
        if change == "null-preboot":
            row["runner_vm"] = None
        elif change == "root-user":
            row["runner_vm"]["guest_uid"] = 0
        elif change in (
            "wrong-runner-id",
            "wrong-manager",
            "no-ready-runner",
            "runner-still-ready-after-cleanup",
        ):
            continue
        else:
            row["runner_vm"]["guest_boot_id"] = "99999999-2222-3333-4444-555555555555"
    if change == "wrong-runner-id":
        down["runner_id"] = 38
    elif change == "wrong-manager":
        down["runner_manager_system_identity"] = "another-runner"
    elif change == "no-ready-runner":
        up["service_ready"] = False
    elif change == "runner-still-ready-after-cleanup":
        down["service_ready"] = True
    for key, value in [("up_outcome", up), ("outcome", down)]:
        path = tmp_path / (kind + "-" + key + "-original-new.json")
        path.write_bytes(_canonical(value))
        attempt[key] = component._acceptance_test_observations.retain(
            PhaseOutcome("completed", "controlled-new-observation", (path,), mutation_effect="applied"), value
        )
    report["components"][kind] = deepcopy(down)
    attempt["observation"] = deepcopy(down)
    with pytest.raises(StateError):
        _runner_originals(cleanup, journal=journal, report=report)
