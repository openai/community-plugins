"""Original resident aggregate + real durable journal; inert component boundaries."""

from datetime import timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devsecops.factory.cleanup_observation import ComponentObservations
from devsecops.factory.lifecycle import LifecycleEngine, PhaseOutcome
from devsecops.factory.owned_cleanup import OwnedCleanupCoordinator
from devsecops.factory.owned_foundation import OwnedRKE2Foundation
from devsecops.factory.owned_linux import OwnedLinuxDriver
from devsecops.factory.owned_registry import OwnedOCIRegistry
from devsecops.factory.owned_service_links import OwnedServiceLinks
from devsecops.factory.state import RunJournal, StateError
from tests.test_factory_lifecycle import FixtureDriver, inputs_for, fail_read
from tests.test_factory_state import Clock


@pytest.fixture
def resident(tmp_path, monkeypatch):
    inputs, identities = inputs_for(tmp_path)
    clock = Clock()
    journal = RunJournal.create(
        tmp_path / "run", identities=identities, environment_id=inputs["config"]["environment"], clock=clock
    )
    driver = FixtureDriver()
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path / "locks", _test_driver=driver)
    owned = object.__new__(OwnedLinuxDriver)
    owned.journal = journal

    def admit_driver(context, deadline):
        assert journal._lifecycle_locked and journal._environment_locked
        assert context.run_id == journal.state["run_id"] and deadline == context.deadline
        assert not journal.needs_reconciliation

    owned._admit = admit_driver
    coordinator = OwnedCleanupCoordinator(owned)

    def admit(context, action, operation_id=None):
        assert journal._lifecycle_locked and journal._environment_locked
        current = journal.current_mutation(
            context.phase["id"], action, reconciliation=operation_id is not None
        )
        assert operation_id is None or current == operation_id
        return current

    monkeypatch.setattr(coordinator, "_admit", admit)
    calls, controls = [], {"unknown": False, "second_unknown": False}
    for kind, cls in [
        ("foundation", OwnedRKE2Foundation),
        ("registry", OwnedOCIRegistry),
        ("service-links", OwnedServiceLinks),
    ]:
        component = object.__new__(cls)
        component.journal = journal
        observations = ComponentObservations()

        def perform(self, context, action, prior=None, *, selected=kind, cache=observations):
            operation = journal.current_mutation(
                context.phase["id"], context.phase["operation"], reconciliation=action == "reconcile"
            )
            calls.append((selected, action, operation, prior))
            if action == "continue":
                assert operation != prior
                assert journal.state["active_mutation"]["continues_operation"] == prior
            if action == "continue":
                self._test_closed = True
            partial = (
                selected == "service-links"
                and action in ("down", "reconcile")
                and not getattr(self, "_test_closed", False)
            )
            unknown = partial and (action == "down" or controls["unknown"])
            if selected == "registry" and controls["second_unknown"]:
                unknown = True
            report = {
                "kind": "owned-service-links" if selected == "service-links" else "component",
                "run_id": context.run_id,
                "identities": dict(context.identities),
                "operation_id": operation,
                "action": "down" if action == "continue" else action,
                "complete_cleanup": not partial and not unknown,
                "mutation_effect": "unknown" if unknown else "applied",
                "unresolved_operations": ["effect-unobserved"] if unknown else [],
                "cleanup_eligible": action == "reconcile" and not unknown,
                "resources": [
                    {"disposition": "remove", "state": "present" if partial else "absent"},
                    {"disposition": "preserve", "state": "preserved"},
                ],
                "runtime_cleanup": "observed-absent",
                "observation": {
                    "inflight": "unknown" if unknown else None,
                    "cleanup": "unknown" if unknown else "owned-runtime-and-private-material-absent",
                },
            }
            path = journal.run_dir / ("component-" + uuid4().hex + ".json")
            path.write_text(json.dumps(report))
            outcome = PhaseOutcome(
                "incomplete", "controlled-component", (path,), mutation_effect=report["mutation_effect"]
            )
            return cache.retain(outcome, report)

        monkeypatch.setattr(cls, "up", lambda self, ctx, fn=perform: fn(self, ctx, "up"))
        monkeypatch.setattr(cls, "down", lambda self, ctx, fn=perform: fn(self, ctx, "down"))
        monkeypatch.setattr(cls, "reconcile", lambda self, ctx, op, fn=perform: fn(self, ctx, "reconcile"))
        if kind == "service-links":
            monkeypatch.setattr(
                cls,
                "continue_cleanup",
                lambda self, ctx, prior, fn=perform: fn(self, ctx, "continue", prior),
                raising=False,
            )
        monkeypatch.setattr(
            cls, "cleanup_observation", lambda self, value, cache=observations: cache.observe(value)
        )
        getattr(coordinator, "register_" + kind.replace("-", "_"))(component)
    coordinator.components["services"] = SimpleNamespace(journal=journal)

    def provision(context):
        for kind in ("gitlab", "kubernetes", "writer", "reader"):
            coordinator.declare_deferred_scope(context, kind)
        coordinator.seal_inventory(context)
        coordinator.foundation_up(context)
        coordinator.registry_up(context)
        outcome = coordinator.service_links_up(context)
        return PhaseOutcome("completed", "controlled-up", outcome.artifacts, mutation_effect="applied")

    driver.behaviors["provision"] = provision
    driver.behaviors["primary-baseline"] = fail_read
    driver.behaviors["cleanup"] = coordinator.execute
    driver.reconcile_cleanup = coordinator.reconcile
    driver.validate_partial_cleanup = coordinator.validate_partial_cleanup
    driver.prepare_cleanup_continuation = coordinator.prepare_continuation
    driver.continue_cleanup = coordinator.continue_cleanup
    driver.validate_cleanup = coordinator.validate_cleanup
    result = engine.advance()
    assert result["execution_status"] == "reconciliation_required"
    previous = journal.state["active_mutation"]["operation_id"]
    return engine, coordinator, previous, calls, controls, clock


def test_partial_child_requires_explicit_new_operation_then_finishes_lower_scopes(resident):
    engine, coordinator, previous, calls, _, _ = resident
    original_calls = list(calls)
    with pytest.raises(StateError, match="terminal partial"):
        engine.continue_cleanup(prior_operation_id=previous)
    assert calls == original_calls
    result = engine.reconcile_cleanup(operation_id=previous)
    state = engine.journal.state
    assert result["execution_status"] == "cleanup_continuation_required"
    assert state["operations"][-1]["status"] == "partial"
    assert state["environment_reserved"] and state["active_mutation"] is None
    assert state["lifecycle"]["active_phase"]["phase_id"] == "cleanup"
    original_calls = list(calls)
    assert engine.advance()["execution_status"] == "cleanup_continuation_required"
    assert calls == original_calls
    result = engine.continue_cleanup(prior_operation_id=previous)
    state = engine.journal.state
    assert result["execution_status"] == "cleanup_completed"
    assert state["execution_status"] == "released" and not state["environment_reserved"]
    latest = state["operations"][-1]
    assert latest["status"] == "applied" and latest["operation_id"] != previous
    assert latest["continues_operation"] == previous
    assert [row[:2] for row in calls[-3:]] == [
        ("service-links", "continue"),
        ("registry", "down"),
        ("foundation", "down"),
    ]
    assert len([row for row in calls if row[:2] == ("service-links", "down")]) == 1
    assert engine.advance()["terminal"]
    assert not engine.journal.state["lifecycle"]["final_result"]["canonical_success"]


def test_unknown_reconciliation_retains_exact_old_operation_and_no_continuation(resident):
    engine, _, previous, calls, controls, _ = resident
    controls["unknown"] = True
    with pytest.raises(StateError, match="terminal partial"):
        engine.reconcile_cleanup(operation_id=previous)
    assert engine.journal.state["active_mutation"]["operation_id"] == previous
    assert engine.journal.state["environment_reserved"]
    assert not any(row[1] == "continue" for row in calls)


def test_same_operation_and_copied_report_cannot_admit_continuation(resident):
    engine, coordinator, previous, calls, _, _ = resident
    engine.reconcile_cleanup(operation_id=previous)
    with engine.journal.lifecycle_lock(), engine.journal.mutation_lock(engine.shared_lock_dir):
        phase = next(p for p in engine.docs["scenario"]["phases"] if p["operation"] == "down")
        context = engine._context(phase)
        # No new operation exists, so current_mutation refuses old authority.
        with pytest.raises(StateError):
            coordinator.continue_cleanup(context, previous)
    original = coordinator._terminal
    from dataclasses import replace

    coordinator._terminal = replace(original)
    with pytest.raises(StateError, match="original resident"):
        engine.continue_cleanup(prior_operation_id=previous)
    coordinator._terminal = original
    assert not any(row[1] == "continue" for row in calls)


def test_continuation_requires_unexpired_new_cleanup_grant(resident):
    engine, _, previous, calls, _, clock = resident
    engine.reconcile_cleanup(operation_id=previous)
    clock.value += timedelta(days=365)
    with pytest.raises(StateError, match="fresh unexpired"):
        engine.continue_cleanup(prior_operation_id=previous)
    assert not any(row[1] == "continue" for row in calls)


def test_later_unknown_preserves_new_chain_and_skips_completed_child(resident):
    engine, _, previous, calls, controls, _ = resident
    engine.reconcile_cleanup(operation_id=previous)
    controls["second_unknown"] = True
    result = engine.continue_cleanup(prior_operation_id=previous)
    assert result["execution_status"] == "reconciliation_required"
    second = engine.journal.state["active_mutation"]["operation_id"]
    assert second != previous and engine.journal.state["active_mutation"]["continues_operation"] == previous
    controls["second_unknown"] = False
    assert (
        engine.reconcile_cleanup(operation_id=second)["execution_status"] == "cleanup_continuation_required"
    )
    # The completed link scope must retain original absence and never be deleted twice.
    count = len([row for row in calls if row[:2] == ("service-links", "continue")])
    assert engine.continue_cleanup(prior_operation_id=second)["execution_status"] == "cleanup_completed"
    assert len([row for row in calls if row[:2] == ("service-links", "continue")]) == count
    assert len([row for row in calls if row[:2] == ("registry", "down")]) == 1


def test_failed_release_validator_keeps_new_operation_reserved(resident, monkeypatch):
    engine, _, previous, _, _, _ = resident
    engine.reconcile_cleanup(operation_id=previous)

    def reject(receipts):
        assert engine.journal._has_validation_read_lock()
        raise StateError("controlled release rejection")

    monkeypatch.setattr(engine.driver, "validate_cleanup", reject)
    with pytest.raises(StateError, match="controlled release"):
        engine.continue_cleanup(prior_operation_id=previous)
    state = engine.journal.state
    assert state["environment_reserved"] and state["active_mutation"]["status"] == "unknown"
    assert state["cleanup_validation"] is None


def test_partial_receipt_tamper_and_new_resident_instance_cannot_restore_authority(resident):
    engine, coordinator, previous, _, _, _ = resident
    engine.reconcile_cleanup(operation_id=previous)
    coordinator._terminal.artifacts[0].write_bytes(b"{}")
    with pytest.raises(StateError, match="evidence changed"):
        engine.continue_cleanup(prior_operation_id=previous)
    fresh = OwnedCleanupCoordinator(coordinator.driver)
    with pytest.raises(StateError, match="resident predecessor"):
        fresh._continuation_basis(previous)


def test_generic_mutation_observation_cannot_create_partial_cleanup_status(resident):
    engine, _, previous, _, _, _ = resident
    journal = engine.journal
    evidence = journal.run_dir / "generic-observation.json"
    evidence.write_text("{}")
    with journal.lifecycle_lock(), journal.mutation_lock(engine.shared_lock_dir):
        with pytest.raises(StateError, match="explicit outcome"):
            journal.reconcile_mutation(previous, "partial", receipt_artifacts=[evidence])
        with journal._run_lock():
            journal._load()
            with pytest.raises(StateError, match="Generic mutation observation"):
                journal._commit(
                    "mutation_observed",
                    {"operation_id": previous, "outcome": "partial", "receipt_artifacts": []},
                )
    assert journal.state["active_mutation"]["operation_id"] == previous


def test_partial_kubernetes_admission_accepts_only_original_fixed_type_and_known_effect(resident):
    from devsecops.factory.kubernetes_operations import OwnedKubernetesResources

    _, coordinator, previous, _, _, _ = resident
    component = object.__new__(OwnedKubernetesResources)
    coordinator.components['kubernetes'] = component
    observed = {'action': 'reconcile', 'operation_id': previous, 'cleanup_eligible': True,
                'unresolved_operations': [], 'mutation_effect': 'applied'}
    assert coordinator._continuable_child('kubernetes', observed, previous)
    observed['mutation_effect'] = 'unknown'
    assert not coordinator._continuable_child('kubernetes', observed, previous)
    observed['mutation_effect'] = 'applied'
    coordinator.components['kubernetes'] = SimpleNamespace(continue_cleanup=lambda *args: None)
    assert not coordinator._continuable_child('kubernetes', observed, previous)
