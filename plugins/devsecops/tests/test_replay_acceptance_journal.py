"""Real durable lifecycle; controlled resource/native/delivery providers only.

This fixture cannot qualify a live demo or provide actual native provenance.
"""

from copy import deepcopy
import json
import os
from types import SimpleNamespace

import pytest

from devsecops.factory.cleanup_observation import ComponentObservations
from devsecops.factory.lifecycle import LifecycleEngine, PhaseOutcome
from devsecops.factory.owned_cleanup import OwnedCleanupCoordinator
from devsecops.factory.owned_production import OwnedProductionCoordinator
from devsecops.factory.replay_acceptance import OwnedReplayAcceptance
from devsecops.factory.state import RunJournal, _canonical
from tests.test_factory_lifecycle import FixtureDriver, inputs_for, finish_native
from tests.test_factory_state import Clock
from tests.test_replay_acceptance import cleaned, deliveries
from tests.test_runner_replay_history import retained_runner_children


@pytest.mark.parametrize(
    "tamper_cleanup",
    [
        False,
        True,
        "native-origin",
        "wrong-journal",
        "wrong-driver",
        "runner-up",
        "runner-down",
        "bootstrap-origin",
        "bootstrap-journal",
        "bootstrap-cleanup",
        "bootstrap-runner-vm",
        "bootstrap-runner-service",
        "bootstrap-runner-routes",
        "legacy-inventory",
    ],
)
def test_original_replay_acceptance_across_actual_journal_and_atomic_cleanup(
    tmp_path, monkeypatch, tamper_cleanup
):
    import sys
    from types import ModuleType
    from devsecops.factory import owned_git_delivery
    from devsecops.factory.native_factory_session import NativeFactorySession
    from devsecops.factory.canonical_bootstrap import OwnedBootstrapCoordinator

    inputs, identities = inputs_for(tmp_path)
    journal = RunJournal.create(
        tmp_path / "run", identities=identities, environment_id=inputs["config"]["environment"], clock=Clock()
    )
    driver = FixtureDriver()
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path / "locks", _test_driver=driver)
    production = object.__new__(OwnedProductionCoordinator)
    production.journal, production.driver, production.pid = journal, driver, os.getpid()
    production.run_id, production.identities = journal.state["run_id"], dict(identities)
    driver.journal = journal
    driver._auxiliary_artifacts = lambda: None

    def admit(ctx, deadline):
        assert journal._lifecycle_locked and journal._environment_locked
        assert journal.state["lifecycle"]["active_phase"]["phase_id"] == ctx.phase["id"]

    driver._admit = admit
    driver._package = lambda *args: None
    native = object.__new__(NativeFactorySession)
    native._journal = journal
    native._driver = driver
    native_paths = {}

    def original_step(step_id):
        row = next(r for r in journal.state["native_results"] if r["step_id"] == step_id)
        path = journal.run_dir / row["validation"]["path"]
        native_paths[step_id] = path
        return (path,)

    native.completed_step_artifacts = original_step
    driver._native_session = native
    cleanup = object.__new__(OwnedCleanupCoordinator)
    cleanup.journal, cleanup.pid, cleanup.attempts = journal, os.getpid(), {}
    cleanup._observations = ComponentObservations()
    production.cleanup = cleanup
    bootstrap = object.__new__(OwnedBootstrapCoordinator)
    bootstrap.driver, bootstrap.journal = driver, journal
    production.bootstrap = bootstrap
    monkeypatch.setattr(
        OwnedBootstrapCoordinator, "cleanup_inventory_contract", lambda self: "owned-runner-vm-v1"
    )
    origins = [object(), object(), object()]
    mapping = {}
    production.operations = SimpleNamespace(
        delivery=lambda attempt, stage: origins[
            [("primary", "baseline"), ("primary", "recovery"), ("replay", "baseline")].index((attempt, stage))
        ]
    )
    monkeypatch.setattr(owned_git_delivery, "require_delivery", lambda value, **kw: deepcopy(mapping[value]))
    interruption = object()
    module = ModuleType("devsecops.factory.case_interruption")
    interrupted = {
        "parent_run_id": journal.state["run_id"],
        "child_run_id": "controlled-separate-child",
        "request_id": "controlled-separate-request",
        "status": "cancelled",
        "spawned": True,
        "reaped": True,
        "turn_started": True,
        "accepted_results": 0,
        "usable_success_receipt": False,
    }
    module.execute_interrupted_review = lambda *args, **kwargs: interruption

    def require(value, *, journal):
        assert value is interruption
        return deepcopy(interrupted)

    module.require_interruption = require
    module.interruption_artifacts = lambda *args, **kwargs: ()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    acceptance = OwnedReplayAcceptance(production)

    def reset(ctx):
        acceptance.begin_reset(ctx)
        operation = journal.current_mutation("reset", "reset")
        rows = deliveries()
        for row in rows:
            row["run_id"] = ctx.run_id
        rows[-1]["operation_id"] = operation
        rows[-1]["source_provenance"].update(run_id=ctx.run_id, operation_id=operation)
        mapping.update(zip(origins, rows))
        path = tmp_path / "controlled-reset.json"
        path.write_bytes(_canonical({"controlled_delivery_provider": True}))
        return acceptance.retain_reset(
            ctx, PhaseOutcome("completed", "controlled-reset", (path,), mutation_effect="applied")
        )

    report = None

    def down(ctx):
        nonlocal report
        _, report, _ = cleaned()
        report.update(
            run_id=ctx.run_id,
            identities=dict(ctx.identities),
            operation_id=journal.current_mutation("cleanup", "down"),
        )
        runner = retained_runner_children(tmp_path, journal, report, monkeypatch)
        cleanup.components, cleanup.attempts = runner.components, runner.attempts
        bootstrap.cleanup = cleanup
        bootstrap._runner_vm = cleanup.components["runner-vm"]
        bootstrap._runner_service = cleanup.components["runner-service"]
        bootstrap._runner_routes = cleanup.components["runner-routes"]
        path = tmp_path / "controlled-full-cleanup.json"
        path.write_bytes(_canonical(report))
        outcome = PhaseOutcome("completed", "controlled-full-cleanup", (path,), mutation_effect="applied")
        cleanup._terminal = cleanup._observations.retain(outcome, report)
        return outcome

    def validate(raw):
        assert json.loads(raw[0]) == report
        return [
            "owned-resource-inventory-closed",
            "owned-resource-absence-observed",
            "retained-resources-protected",
            "cleanup-current-operation-bound",
        ]

    def finalize(ctx):
        assert journal.state["execution_status"] == "released"
        assert not journal.state["environment_reserved"]
        if tamper_cleanup is True:
            cleanup._terminal.artifacts[0].write_bytes(b"{}")
        elif tamper_cleanup == "native-origin":
            driver._native_session = {"completed_main_steps": 8}
        elif tamper_cleanup == "wrong-journal":
            native._journal = object()
        elif tamper_cleanup == "wrong-driver":
            native._driver = object()
        elif tamper_cleanup == "runner-up":
            cleanup.attempts["runner-vm"]["up_outcome"] = None
        elif tamper_cleanup == "runner-down":
            cleanup.attempts["runner-service"]["outcome"] = None
        elif tamper_cleanup == "bootstrap-origin":
            production.bootstrap = {"inventory_contract": "owned-runner-vm-v1"}
        elif tamper_cleanup == "bootstrap-journal":
            bootstrap.journal = object()
        elif tamper_cleanup == "bootstrap-cleanup":
            bootstrap.cleanup = object()
        elif isinstance(tamper_cleanup, str) and tamper_cleanup.startswith("bootstrap-runner-"):
            setattr(bootstrap, "_" + tamper_cleanup.removeprefix("bootstrap-").replace("-", "_"), object())
        elif tamper_cleanup == "legacy-inventory":
            monkeypatch.setattr(
                OwnedBootstrapCoordinator,
                "cleanup_inventory_contract",
                lambda self: "owned-container-runner-v1",
            )
        return acceptance.finalize(ctx)

    driver.behaviors.update(reset=reset, cleanup=down, finalize=finalize)
    driver.validate_cleanup = validate
    result = engine.advance()
    count = 0
    while result["exit_code"] == 3:
        count += 1
        result = finish_native(engine, result)
    assert count == 8 and driver.calls.count("reset") == 1 and driver.calls.count("cleanup") == 1
    assert journal.state["execution_status"] == "released"
    assert not result["canonical_success"]  # controlled acquisition remains explicitly unqualified
    if tamper_cleanup:
        assert not result["terminal"]
        assert journal.state["lifecycle"]["phase_records"][-1]["outcome"] != "completed"
    else:
        assert result["terminal"]
        assert len(native_paths) == 8
        final = journal.state["lifecycle"]["phase_records"][-1]
        assert len(final["assertions"]) == 7 and all(row["status"] == "pass" for row in final["assertions"])
        assert final["code"] == "owned-live-replay-and-cleanup-observed"
