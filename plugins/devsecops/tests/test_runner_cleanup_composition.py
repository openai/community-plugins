"""Fixed eleven-scope inventory and explicit contained-residual mode; no effects."""

from types import SimpleNamespace
import json

import pytest
from devsecops.factory import owned_cleanup as c
from devsecops.factory.lifecycle import PhaseOutcome
from devsecops.factory.cleanup_observation import ComponentObservations
from devsecops.factory.state import StateError
from test_owned_cleanup import composed as _composed

composed = _composed


def test_vm_inventory_cannot_be_satisfied_by_legacy_eight_scopes(composed):
    owner, up, _, _, _ = composed
    owner._sealed = None
    owner.attempts = {}
    with pytest.raises(StateError, match="All fixed cleanup scopes"):
        owner.seal_inventory(up, inventory_contract="owned-runner-vm-v1")
    assert len(owner._scopes()) == 11


def test_full_inventory_selection_is_recorded_before_components_dispatch(composed):
    owner, up, _, _, _ = composed
    owner._sealed = None
    owner.attempts = {}
    for kind in ("runner-service", "runner-routes", "runner-vm"):
        owner.components[kind] = SimpleNamespace(journal=owner.journal)
    owner.seal_inventory(up, inventory_contract="owned-runner-vm-v1")
    assert json.loads(owner._events[-1].read_text())["value"]["inventory_contract"] == "owned-runner-vm-v1"
    order = owner._down_order()
    assert (
        order.index("gitlab")
        < order.index("runner-service")
        < order.index("runner-routes")
        < order.index("runner-vm")
        < order.index("services")
    )
    with pytest.raises(StateError, match="cannot be replaced"):
        owner.seal_inventory(up, inventory_contract="owned-container-runner-v1")


@pytest.mark.parametrize(
    "damage", ["empty", "unknown", "retained-deleted", "missing-disposition", "still-present"]
)
def test_closed_runner_resources_require_positive_exact_dispositions(damage):
    value = {
        "complete_cleanup": True,
        "mutation_effect": "applied",
        "unresolved_operations": [],
        "resources": [{"disposition": "remove", "state": "absent"}],
    }
    if damage == "empty":
        value["resources"] = []
    elif damage == "unknown":
        value["unresolved_operations"] = ["child"]
    elif damage == "retained-deleted":
        value["resources"] = [{"disposition": "preserve", "state": "absent"}]
    elif damage == "missing-disposition":
        value["resources"] = [{"state": "absent"}]
    else:
        value["resources"][0]["state"] = "present"
    for kind in ("runner-service", "runner-routes", "runner-vm"):
        assert not c.OwnedCleanupCoordinator._resolved(kind, value)


def test_contained_partial_refuses_normal_continuation(composed, monkeypatch):
    owner, _, down, _, _ = composed
    owner._cleanup_operation = "down-op"
    owner._terminal_context = down
    path = owner.journal.run_dir / "contained.json"
    report = {
        "operation_id": "down-op",
        "reconciliation": True,
        "complete_cleanup": False,
        "continuation_eligible": True,
        "cleanup_continuation_modes": ["dispose-owned-runner-guest"],
        "identities": dict(down.identities),
    }
    path.write_text(json.dumps(report))
    result = PhaseOutcome("incomplete", "contained", (path,), mutation_effect="unknown")
    owner._terminal = owner._observations.retain(result, report)
    monkeypatch.setattr(owner, "_contained_partial", lambda operation: operation == "down-op")
    monkeypatch.setattr(owner, "_terminal_partial", lambda operation: False)
    with pytest.raises(StateError, match="terminal partial"):
        owner._continuation_basis("down-op")
    assert owner._continuation_basis("down-op", cleanup_mode="dispose-owned-runner-guest") == report
    checks = owner.validate_partial_cleanup(tuple(p.read_bytes() for p in result.artifacts))
    assert checks == [
        "owned-guest-contained-residual-parent-disposal-only",
        "original-runner-parent-ownership-observed",
        "original-runner-registration-revoked-observed",
    ]


def test_original_never_dispatched_vm_closure_does_not_imply_guest_was_ready():
    report = {
        "kind": "owned-runner-vm",
        "never_dispatched": True,
        "complete_cleanup": True,
        "mutation_effect": "not_applied",
        "unresolved_operations": [],
        "resources": [
            {
                "kind": "original-runner-vm-inventory",
                "basis": "original-resident-no-dispatch",
                "disposition": "remove",
                "state": "absent",
            }
        ],
    }
    assert c.OwnedCleanupCoordinator._resolved("runner-vm", report)
    assert not c.OwnedCleanupCoordinator._resolved("runner-service", report)
    report["resources"][0]["basis"] = "file-missing"
    assert not c.OwnedCleanupCoordinator._resolved("runner-vm", report)


@pytest.mark.parametrize("lost_disposal_reply", [False, True, "before-dispatch"])
def test_contained_guest_requires_original_host_proof_then_explicit_new_mode(
    composed, monkeypatch, lost_disposal_reply
):
    from devsecops.factory.owned_runner_service import OwnedRunnerService
    from devsecops.factory.runner_vm_routes import OwnedRunnerVMRoutes
    from devsecops.factory.owned_runner_vm import OwnedRunnerVM

    owner, up, down, calls, _ = composed
    owner._inventory_contract = "owned-runner-vm-v1"
    owner._cleanup_operation = "down-op"
    vm = object.__new__(OwnedRunnerVM)
    routes = object.__new__(OwnedRunnerVMRoutes)
    service = object.__new__(OwnedRunnerService)
    vm.journal = routes.journal = service.journal = owner.journal
    routes.vm = service.vm = vm
    owner.components.update({"runner-vm": vm, "runner-routes": routes, "runner-service": service})
    identity = {"binding": "c" * 64}
    cache = ComponentObservations()
    proof = object()
    revoked = object()

    def retained(kind, ctx, op, **extra):
        report = {
            "run_id": ctx.run_id,
            "identities": dict(ctx.identities),
            "action": "down",
            "operation_id": op,
            "runner_vm": identity,
            "complete_cleanup": True,
            "mutation_effect": "applied",
            "unresolved_operations": [],
            "resources": [{"disposition": "remove", "state": "absent"}],
            **extra,
        }
        path = owner.journal.run_dir / (
            kind + "-" + str(len(list(owner.journal.run_dir.glob(kind + "*")))) + ".json"
        )
        path.write_text(json.dumps(report))
        outcome = PhaseOutcome("incomplete", kind, (path,), mutation_effect=report["mutation_effect"])
        return cache.retain(outcome, report)

    for kind, component in [("runner-vm", vm), ("runner-routes", routes), ("runner-service", service)]:
        owner.attempts[kind] = {
            "up_operation": "up-op",
            "up_outcome": None,
            "down_attempted": kind == "runner-service",
            "down_operation": "down-op" if kind == "runner-service" else None,
            "outcome": None,
            "observation": None,
        }
        monkeypatch.setattr(component, "cleanup_observation", cache.observe)

    def lost(*args):
        raise StateError("controlled guest unavailable")

    monkeypatch.setattr(service, "reconcile", lost)
    monkeypatch.setattr(service, "revocation_proof", lambda *a, **kw: revoked)
    route_basis = retained(
        "host-basis",
        down,
        "down-op",
        action="reconcile-host",
        complete_cleanup=False,
        host_disposal_eligible=True,
        host_unresolved=[],
        guest_unresolved=["lost-guest"],
    )
    monkeypatch.setattr(routes, "prepare_disposal", lambda *a: route_basis)
    monkeypatch.setattr(vm, "prepare_disposal", lambda *a, **kw: proof)
    monkeypatch.setattr(
        vm,
        "disposal_observation",
        lambda value, ctx: {"parent_disposal_eligible": value is proof, "runner_vm": identity},
    )
    monkeypatch.setattr(vm, "disposal_artifacts", lambda value: ())
    owner.gitlab = SimpleNamespace(
        _down_operation="down-op",
        token={"state": "absent"},
        events=[],
        observation=lambda op: {
            "complete_cleanup": True,
            "mutation_effect": "applied",
            "unresolved_operations": [],
            "resources": [{"state": "absent"}],
        },
        reconcile=lambda *a: None,
    )
    outcome = owner.reconcile(down, "down-op")
    report = json.loads(outcome.artifacts[0].read_text())
    assert report["complete_cleanup"] is False
    assert report["cleanup_continuation_modes"] == ["dispose-owned-runner-guest"]
    assert report["contained_guest_residuals"] == ["runner-routes", "runner-service"]
    assert (
        owner.validate_partial_cleanup(tuple(p.read_bytes() for p in outcome.artifacts))[-1]
        == "original-runner-registration-revoked-observed"
    )
    owner.journal.state["active_mutation"] = {
        "operation_id": "next-op",
        "action": "down",
        "continues_operation": "down-op",
        "cleanup_mode": "dispose-owned-runner-guest",
    }
    monkeypatch.setattr(owner, "_admit", lambda *a, **kw: kw.get("operation_id") or "next-op")
    with pytest.raises(StateError):
        owner.continue_cleanup(down, "down-op")

    def host(ctx, old):
        calls.append(("runner-routes", "host-only"))
        return retained("host-down", ctx, "next-op", complete_cleanup=False)

    monkeypatch.setattr(routes, "prepare_host_disposal", host)
    monkeypatch.setattr(routes, "host_cleanup_ready", lambda c: True)

    def dispose(ctx, **kw):
        assert kw == {"revocation": revoked, "basis": proof}
        calls.append(("runner-vm", "explicit-disposal"))
        if lost_disposal_reply:
            raise StateError("controlled lost disposal acknowledgement")
        return retained("vm-down", ctx, "next-op"), proof

    monkeypatch.setattr(vm, "dispose_guest", dispose)
    monkeypatch.setattr(
        vm, "disposal_was_dispatched", lambda *a, **kw: lost_disposal_reply != "before-dispatch"
    )
    for kind, component in [("runner-service", service), ("runner-routes", routes)]:
        monkeypatch.setattr(
            component,
            "accept_guest_cessation",
            lambda value, ctx, k=kind, **kw: retained(
                k + "-cessation",
                ctx,
                "next-op",
                action="reconcile" if kw.get("reconciliation_operation") == "next-op" else "down",
            ),
        )
    # Remaining legacy fixture children retain their old controlled operation;
    # stop immediately after testing original disposal composition, before them.
    monkeypatch.setattr(owner, "_execute_remaining", lambda ctx, op: ("remaining", op))
    if lost_disposal_reply:
        with pytest.raises(StateError, match="lost disposal acknowledgement"):
            owner.continue_cleanup(down, "down-op", cleanup_mode="dispose-owned-runner-guest")
        assert owner.attempts["runner-vm"]["outcome"] is None
        assert owner._disposal_operation == "next-op"

        def reconcile_disposal(ctx, *, operation_id, revocation, basis):
            assert (operation_id, revocation, basis) == ("next-op", revoked, proof)
            calls.append(("runner-vm", "observe-original-disposal"))
            return retained("vm-absence", ctx, operation_id, action="reconcile"), proof

        monkeypatch.setattr(vm, "reconcile_disposal", reconcile_disposal)
        observed = owner.reconcile(down, "next-op")
        assert json.loads(observed.artifacts[0].read_text())["reconciliation"] is True
        assert calls.count(("runner-vm", "explicit-disposal")) == 1
        if lost_disposal_reply == "before-dispatch":
            assert ("runner-vm", "observe-original-disposal") not in calls
            assert owner._disposal_basis["operation"] == "next-op"
            report = json.loads(observed.artifacts[0].read_text())
            assert report["complete_cleanup"] is False
            assert report["cleanup_continuation_modes"] == ["dispose-owned-runner-guest"]
            assert owner._disposal_request["prior"] == "down-op"
        else:
            assert calls[-1] == ("runner-vm", "observe-original-disposal")
        assert all(path in observed.artifacts for path in owner._history[0].artifacts)
    else:
        assert owner.continue_cleanup(down, "down-op", cleanup_mode="dispose-owned-runner-guest") == (
            "remaining",
            "next-op",
        )
        assert calls[-2:] == [("runner-routes", "host-only"), ("runner-vm", "explicit-disposal")]
    if lost_disposal_reply == "before-dispatch":
        return
    assert all(
        owner._resolved(kind, owner.attempts[kind]["observation"])
        for kind in ("runner-service", "runner-routes", "runner-vm")
    )


def test_repeated_disposal_reconciliation_retains_original_request_after_successor_basis(
    composed, monkeypatch
):
    owner, _, down, _, _ = composed
    old_basis, successor, revocation = object(), object(), object()
    owner._disposal_operation = owner._cleanup_operation = "current"
    owner._continuation_of = "prior"
    owner._disposal_request = {
        "operation": "current",
        "prior": "prior",
        "basis": old_basis,
        "revocation": revocation,
    }
    owner._disposal_basis = {"operation": "current", "vm": successor}
    owner.attempts["runner-vm"] = {"down_attempted": True, "down_operation": "current"}
    seen = []

    def dispatched(ctx, **kwargs):
        seen.append(kwargs)
        return False

    owner.components["runner-vm"] = SimpleNamespace(disposal_was_dispatched=dispatched)
    owner._reconcile_runner_disposal(down, "current")
    owner._reconcile_runner_disposal(down, "current")
    assert seen == [{"operation_id": "current", "revocation": revocation, "basis": old_basis}] * 2
    assert owner._disposal_basis["vm"] is successor
    with pytest.raises(StateError, match="Original attempted"):
        owner._reconcile_runner_disposal(down, "different")
