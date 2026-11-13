"""Resident ownership/continuation proof tests with controlled transport only."""

from copy import deepcopy
from dataclasses import replace
import os
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devsecops.factory import owned_runner_vm as owned
from devsecops.factory.cleanup_observation import ComponentObservations
from devsecops.factory.lifecycle import PhaseContext
from devsecops.factory.owned_fresh_foundation import OwnedFreshRKE2Foundation
from devsecops.factory.owned_linux import OwnedLinuxDriver
from devsecops.factory.owned_services import OwnedServiceStack
from devsecops.factory.state import IDENTITY_KEYS, RunJournal, StateError


@pytest.fixture
def vm_scope(tmp_path, monkeypatch):
    identities = {name: "a" * 64 for name in IDENTITY_KEYS}
    journal = RunJournal.create(tmp_path / "journal", identities=identities, environment_id="controlled")
    journal._lifecycle_locked = True
    projection = journal.state
    projection["lifecycle"] = {"final_result": None}
    original_state = RunJournal.state.fget
    monkeypatch.setattr(
        RunJournal,
        "state",
        property(lambda self: deepcopy(projection) if self is journal else original_state(self)),
    )
    driver = object.__new__(OwnedLinuxDriver)
    driver.journal = journal
    driver.plugin_id = "devsecops@devsecops-local"
    driver.plugin_version = "0.2.0-alpha.9"
    foundation = object.__new__(OwnedFreshRKE2Foundation)
    foundation.journal = journal
    foundation._resident_transport_enabled = False
    foundation._host_session = None
    services = object.__new__(OwnedServiceStack)
    services.journal = journal
    services.driver = driver
    vm = owned.OwnedRunnerVM(driver, foundation=foundation, services=services)
    context = PhaseContext(
        journal.state["run_id"],
        {"id": "provision", "operation": "up"},
        identities,
        {"config": {"expires_at": "2099-01-01T00:00:00Z"}},
        {},
        journal.run_dir,
        time.monotonic() + 60,
        "controlled-test",
    )
    operation = [str(uuid4())]
    recipe = {"private_state_directory": str(tmp_path / "private")}

    def admit(current, *, action=None, reconciliation_operation=None):
        assert current.run_id == journal.state["run_id"]
        if action in ("up", "down"):
            assert current.phase["operation"] == action
        if reconciliation_operation is not None:
            assert reconciliation_operation == operation[0]
        return operation[0], recipe, {"owned_runner_vm": "b" * 64}, "c" * 64

    monkeypatch.setattr(vm, "_admit", admit)
    origin = {
        "binding": "c" * 64,
        "generation": "runner-vm-" + context.run_id,
        "guest_boot_id": str(uuid4()),
        "guest_uid": 10001,
        "guest_gid": 10001,
        "root_identity": {"device": 1, "inode": 2},
        "disk_identity": {"device": 1, "inode": 3},
        "runtime_id": "d" * 64,
        "network_id": "e" * 64,
        "ssh_host_key_sha256": "f" * 64,
    }
    calls = []

    def dispatch(current, action, op, selected, hashes, binding, child=None):
        if action == "down" and child and child.get("action") == "explicit-parent-disposal":
            vm._mark_parent_dispatch(op, child)
        vm._remote_attempted = True
        calls.append((action, op, deepcopy(child)))
        complete = action == "down"
        actual = {
            "binding": binding,
            "operation_id": op,
            "root_identity": origin["root_identity"],
            "runner_vm": deepcopy(origin),
            "state": "absent" if complete else "ready",
            "guest_observation": "not-requeried" if action == "host-observe" else "original",
            "unresolved_operations": [],
            "source_assets_preserved": True,
            "tool_image_preserved": True,
            "complete_cleanup": complete,
            "cleanup_eligible": True,
            "mutation_effect": "applied",
            "resources": [
                {"kind": "runtime", "disposition": "remove", "state": "absent" if complete else "present"},
                {"kind": "supplied-assets", "disposition": "preserve", "state": "preserved"},
            ],
            "endpoint": {"runner_vm": deepcopy(origin)},
        }
        if child is not None and action in ("runner", "routes"):
            actual["child"] = {"complete_cleanup": False, "mutation_effect": "applied"}
        return actual

    monkeypatch.setattr(vm, "_dispatch", dispatch)
    return SimpleNamespace(
        vm=vm,
        journal=journal,
        context=context,
        operation=operation,
        origin=origin,
        calls=calls,
        state=projection,
    )


def down(scope):
    return replace(scope.context, phase={"id": "cleanup", "operation": "down"})


def test_constructor_requires_exact_original_types():
    with pytest.raises(StateError, match="exact original"):
        owned.OwnedRunnerVM(SimpleNamespace(), foundation=SimpleNamespace(), services=SimpleNamespace())


def test_original_pid_guard_precedes_transport(vm_scope):
    scope = vm_scope
    scope.vm._pid = os.getpid() + 1
    with pytest.raises(StateError, match="another process"):
        owned.OwnedRunnerVM._admit(scope.vm, scope.context)
    assert scope.calls == []


def test_up_intent_and_original_outcome_byte_audit(vm_scope):
    scope = vm_scope
    outcome = scope.vm.up(scope.context)
    assert outcome.artifacts[0].name == "intent.json" and outcome.artifacts[0].exists()
    assert scope.vm.cleanup_observation(outcome)["runner_vm"] == scope.origin
    with pytest.raises(StateError, match="original resident"):
        scope.vm.cleanup_observation(replace(outcome))
    outcome.artifacts[-1].write_text("changed")
    with pytest.raises(StateError, match="evidence changed"):
        scope.vm.cleanup_observation(outcome)


def test_up_one_shot_and_unknown_transport_never_repeated(vm_scope, monkeypatch):
    scope = vm_scope

    def lost(*_a, **_kw):
        scope.vm._remote_attempted = True
        raise TimeoutError("controlled lost reply")

    monkeypatch.setattr(scope.vm, "_dispatch", lost)
    with pytest.raises(TimeoutError):
        scope.vm.up(scope.context)
    assert scope.vm._unknown and scope.vm._last_unknown_operation == scope.operation[0]
    with pytest.raises(StateError, match="unknown effects"):
        scope.vm.up(scope.context)


def test_never_dispatched_closure_is_resident_not_file_absence(vm_scope):
    scope = vm_scope
    outcome = scope.vm.down(down(scope))
    report = scope.vm.cleanup_observation(outcome)
    assert report["complete_cleanup"] and report["never_dispatched"]
    assert report["mutation_effect"] == "not_applied" and scope.calls == []
    scope.vm._remote_attempted = True
    with pytest.raises(StateError, match="never-dispatched"):
        scope.vm._no_effects(down(scope))


def test_normal_down_refuses_unresolved_original_child(vm_scope):
    scope = vm_scope
    scope.vm.up(scope.context)
    scope.vm._child_attempts["runner"] = True
    scope.vm._children["runner"] = SimpleNamespace(vm_cleanup_ready=lambda _: False)
    with pytest.raises(StateError, match="child cleanup"):
        scope.vm.down(down(scope))
    assert [row[0] for row in scope.calls] == ["up"]


def test_child_dispatch_requires_original_result_not_copied_dict(vm_scope):
    scope = vm_scope
    scope.vm.up(scope.context)
    scope.vm._children["runner"] = object()
    result = scope.vm.dispatch_runner(scope.context, action="install", packet={"action": "install"})
    assert scope.vm.require_dispatch(result, scope.context) is result
    with pytest.raises(StateError, match="original resident"):
        scope.vm.require_dispatch(dict(result), scope.context)


def test_parent_cessation_and_basis_cannot_be_constructed_or_restored(vm_scope):
    scope = vm_scope
    for cls in (owned.GuestKernelCessation, owned.RunnerVMDisposalBasis):
        with pytest.raises(TypeError):
            cls()
    with pytest.raises(StateError, match="Original runner VM"):
        owned.require_guest_kernel_cessation({}, vm=scope.vm, context=down(scope))
    with pytest.raises(StateError, match="Original resident"):
        scope.vm.disposal_observation({}, down(scope))


def test_explicit_parent_disposal_requires_revocation_mode_and_positive_absence(vm_scope, monkeypatch):
    scope = vm_scope
    scope.vm.up(scope.context)
    old = str(uuid4())
    scope.operation[0] = old
    context = down(scope)
    revocation = object()

    def require(value, *, vm, context, reconciliation_operation=None):
        assert value is revocation and vm is scope.vm
        assert reconciliation_operation in (None, old)
        return {"runner_id": 17, "state": "absent"}

    monkeypatch.setitem(
        sys.modules,
        "devsecops.factory.owned_runner_service",
        SimpleNamespace(require_runner_revocation=require),
    )
    monkeypatch.setattr(owned, "_package", lambda *_a: (b"controlled", {}))
    scope.vm._unknown = True
    scope.vm._last_unknown_action = "runner"
    scope.vm._last_unknown_operation = old
    scope.vm._child_attempts["runner"] = True
    basis = scope.vm.prepare_disposal(context, prior_operation_id=old, revocation=revocation)
    historical = scope.vm.disposal_observation(basis, replace(context, deadline=time.monotonic() - 10))
    assert historical["parent_disposal_eligible"] and "unresolved" in historical["guest_residual"]
    scope.operation[0] = str(uuid4())
    scope.state["active_mutation"] = {"continues_operation": old}
    before = list(scope.calls)
    with pytest.raises(StateError, match="explicit mode"):
        scope.vm.dispose_guest(context, revocation=revocation, basis=basis)
    assert scope.calls == before
    scope.state["active_mutation"]["cleanup_mode"] = "dispose-owned-runner-guest"
    outcome, proof = scope.vm.dispose_guest(context, revocation=revocation, basis=basis)
    assert scope.vm.cleanup_observation(outcome)["complete_cleanup"]
    result = owned.require_guest_kernel_cessation(proof, vm=scope.vm, context=context)
    assert result["guest_kernel"] == "ceased" and result["ci_effects"] == "not-reconciled"
    assert [row[0] for row in scope.calls[-2:]] == ["host-observe", "down"]


def test_unknown_parent_effect_cannot_be_contained_as_guest_only(vm_scope, monkeypatch):
    scope = vm_scope
    scope.vm.up(scope.context)
    scope.vm._unknown = True
    scope.vm._last_unknown_action = "down"
    monkeypatch.setitem(
        sys.modules,
        "devsecops.factory.owned_runner_service",
        SimpleNamespace(require_runner_revocation=lambda *_a, **_kw: pytest.fail("No API admission")),
    )
    with pytest.raises(StateError, match="unknown parent"):
        scope.vm.prepare_disposal(down(scope), prior_operation_id=scope.operation[0], revocation=object())


@pytest.mark.parametrize(
    "damage", ["host-unknown", "wrong-link", "wrong-mode", "changed-basis", "incomplete-parent"]
)
def test_parent_disposal_never_converts_missing_gates_to_guest_absence(vm_scope, monkeypatch, damage):
    scope = vm_scope
    scope.vm.up(scope.context)
    old = str(uuid4())
    scope.operation[0] = old
    context = down(scope)
    revocation = object()

    def require(value, *, vm, context, reconciliation_operation=None):
        if value is not revocation or vm is not scope.vm:
            raise StateError("Original runner revocation required")
        return {"runner_id": 17, "state": "absent"}

    monkeypatch.setitem(
        sys.modules,
        "devsecops.factory.owned_runner_service",
        SimpleNamespace(require_runner_revocation=require),
    )
    monkeypatch.setattr(owned, "_package", lambda *_a: (b"controlled", {}))
    basis = scope.vm.prepare_disposal(context, prior_operation_id=old, revocation=revocation)
    scope.operation[0] = str(uuid4())
    scope.state["active_mutation"] = {
        "continues_operation": old,
        "cleanup_mode": "dispose-owned-runner-guest",
    }
    if damage == "host-unknown":
        scope.vm._child_attempts["routes"] = True
        scope.vm._children["routes"] = SimpleNamespace(host_cleanup_ready=lambda _: False)
    elif damage == "wrong-link":
        scope.state["active_mutation"]["continues_operation"] = str(uuid4())
    elif damage == "wrong-mode":
        scope.state["active_mutation"]["cleanup_mode"] = "normal"
    elif damage == "changed-basis":
        scope.vm.disposal_artifacts(basis)[-1].write_text("changed original host observation")
    else:
        original = scope.vm._dispatch

        def incomplete(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[1] == "down":
                result["complete_cleanup"] = False
                result["unresolved_operations"] = [{"kind": "unknown-parent-stop"}]
            return result

        monkeypatch.setattr(scope.vm, "_dispatch", incomplete)
    before = len(scope.calls)
    with pytest.raises(StateError):
        scope.vm.dispose_guest(context, revocation=revocation, basis=basis)
    if damage != "incomplete-parent":
        assert len(scope.calls) == before
    assert all(owner is not scope.vm for owner, *_ in owned._CESSATIONS.values())


def test_component_observation_is_not_serialized_authority():
    with pytest.raises(StateError):
        ComponentObservations().observe({"complete_cleanup": True})


@pytest.mark.parametrize("predecessor", [None, "other-operation"])
def test_continuation_requires_exact_durable_predecessor(vm_scope, predecessor):
    scope = vm_scope
    scope.vm.up(scope.context)
    previous = str(uuid4())
    scope.operation[0] = previous
    scope.vm.reconcile(down(scope), previous)
    scope.operation[0] = str(uuid4())
    scope.state["active_mutation"] = {} if predecessor is None else {"continues_operation": predecessor}
    before = list(scope.calls)
    with pytest.raises(StateError, match="linked"):
        scope.vm.continue_cleanup(down(scope), previous)
    assert scope.calls == before


def test_continuation_accepts_exact_terminal_predecessor(vm_scope):
    scope = vm_scope
    scope.vm.up(scope.context)
    previous = str(uuid4())
    scope.operation[0] = previous
    scope.vm.reconcile(down(scope), previous)
    scope.operation[0] = str(uuid4())
    scope.state["active_mutation"] = {"continues_operation": previous}
    result = scope.vm.continue_cleanup(down(scope), previous)
    assert scope.vm.cleanup_observation(result)["complete_cleanup"]
    assert [row[0] for row in scope.calls] == ["up", "reconcile", "down"]


def test_normal_continuation_cannot_consume_parent_disposal_mode(vm_scope):
    scope = vm_scope
    scope.vm.up(scope.context)
    previous = str(uuid4())
    scope.operation[0] = previous
    scope.vm.reconcile(down(scope), previous)
    scope.operation[0] = str(uuid4())
    scope.state["active_mutation"] = {
        "continues_operation": previous,
        "cleanup_mode": "dispose-owned-runner-guest",
    }
    before = list(scope.calls)
    with pytest.raises(StateError, match="linked"):
        scope.vm.continue_cleanup(down(scope), previous)
    assert scope.calls == before


def test_historical_evidence_survives_terminal_cleanup_and_expired_phase(vm_scope):
    scope = vm_scope
    up = scope.vm.up(scope.context)
    scope.operation[0] = str(uuid4())
    terminal = scope.vm.down(down(scope))
    paths = scope.vm.evidence_artifacts(replace(down(scope), deadline=0))
    assert paths == tuple(scope.vm._evidence)
    assert set(up.artifacts + terminal.artifacts) <= set(paths)
    assert [row[0] for row in scope.calls] == ["up", "down"]


@pytest.mark.parametrize("change", ["pid", "run", "identities", "run_dir", "journal"])
def test_historical_evidence_rejects_changed_original_identity(vm_scope, change):
    scope = vm_scope
    scope.vm.up(scope.context)
    current = replace(scope.context, deadline=0)
    if change == "pid":
        scope.vm._pid += 1
    elif change == "run":
        current = replace(current, run_id=str(uuid4()))
    elif change == "identities":
        current = replace(current, identities={**current.identities, "package_sha256": "0" * 64})
    elif change == "run_dir":
        current = replace(current, run_dir=current.run_dir.parent)
    else:
        scope.state["run_id"] = str(uuid4())
    with pytest.raises(StateError, match="historical evidence"):
        scope.vm.evidence_artifacts(current)


def test_historical_evidence_rejects_modified_original_intent(vm_scope):
    scope = vm_scope
    outcome = scope.vm.up(scope.context)
    outcome.artifacts[0].write_text("changed")
    with pytest.raises(StateError, match="historical evidence changed"):
        scope.vm.evidence_artifacts(replace(scope.context, deadline=0))


@pytest.fixture
def disposal_attempt(vm_scope, monkeypatch):
    scope = vm_scope
    scope.vm.up(scope.context)
    old = str(uuid4())
    scope.operation[0] = old
    context = down(scope)
    revocation = object()
    reads = []

    def require(value, *, vm, context, reconciliation_operation=None):
        if value is not revocation or vm is not scope.vm:
            raise StateError("Original runner revocation required")
        reads.append(reconciliation_operation)
        return {"runner_id": 17, "state": "absent"}

    monkeypatch.setitem(
        sys.modules,
        "devsecops.factory.owned_runner_service",
        SimpleNamespace(require_runner_revocation=require),
    )
    monkeypatch.setattr(owned, "_package", lambda *_a: (b"controlled", {}))
    basis = scope.vm.prepare_disposal(context, prior_operation_id=old, revocation=revocation)
    scope.operation[0] = str(uuid4())
    scope.state["active_mutation"] = {
        "continues_operation": old,
        "cleanup_mode": "dispose-owned-runner-guest",
    }
    original = scope.vm._dispatch
    actual = {}

    def controlled(current, action, *args, **kwargs):
        if action == "reconcile" and actual:
            scope.calls.append((action, scope.operation[0], None))
            return deepcopy(actual)
        result = original(current, action, *args, **kwargs)
        if action == "down":
            assert scope.vm._disposal_attempt is not None
            assert scope.vm._disposal_attempt["intent"].exists()
            actual.update(result)
            raise TimeoutError("controlled parent removed but reply lost")
        return result

    monkeypatch.setattr(scope.vm, "_dispatch", controlled)
    with pytest.raises(TimeoutError):
        scope.vm.dispose_guest(context, revocation=revocation, basis=basis)
    return SimpleNamespace(
        scope=scope, context=context, basis=basis, revocation=revocation, actual=actual, reads=reads
    )


def test_lost_disposal_reply_reconciles_original_absence_without_second_delete(disposal_attempt):
    d = disposal_attempt
    vm = d.scope.vm
    operation = d.scope.operation[0]
    before = list(d.scope.calls)
    with pytest.raises(StateError, match="attempted"):
        vm.dispose_guest(d.context, revocation=d.revocation, basis=d.basis)
    assert d.scope.calls == before
    outcome, proof = vm.reconcile_disposal(
        d.context, operation_id=operation, revocation=d.revocation, basis=d.basis
    )
    assert d.scope.calls[len(before) :] == [("reconcile", operation, None)]
    assert sum(row[0] == "down" for row in d.scope.calls) == 1
    assert vm.cleanup_observation(outcome)["complete_cleanup"]
    facts = owned.require_guest_kernel_cessation(
        proof, vm=vm, context=d.context, reconciliation_operation=operation
    )
    assert facts["guest_kernel"] == "ceased" and facts["ci_effects"] == "not-reconciled"
    assert d.reads[-1] == operation
    with pytest.raises(StateError, match="admission mode"):
        owned.require_guest_kernel_cessation(proof, vm=vm, context=d.context)
    artifacts = vm.guest_cessation_artifacts(proof)
    assert artifacts[0].name == "explicit-disposal-intent.json"
    assert set(outcome.artifacts) <= set(artifacts)
    outcome.artifacts[-1].write_text("changed original parent observation")
    with pytest.raises(StateError, match="evidence changed"):
        vm.guest_cessation_artifacts(proof)


@pytest.mark.parametrize("damage", ["mode", "link", "basis", "revocation", "operation", "source", "intent"])
def test_disposal_reconciliation_requires_original_attempt(disposal_attempt, damage):
    d = disposal_attempt
    vm = d.scope.vm
    operation = d.scope.operation[0]
    basis, revoked = d.basis, d.revocation
    if damage == "mode":
        d.scope.state["active_mutation"].pop("cleanup_mode")
    elif damage == "link":
        d.scope.state["active_mutation"]["continues_operation"] = str(uuid4())
    elif damage == "basis":
        basis = object()
    elif damage == "revocation":
        revoked = object()
    elif damage == "operation":
        operation = d.scope.operation[0] = str(uuid4())
    elif damage == "source":
        vm._source_assets = {"changed": True}
    else:
        vm._disposal_attempt["intent"].write_text("changed")
    before = list(d.scope.calls)
    with pytest.raises(StateError):
        vm.reconcile_disposal(d.context, operation_id=operation, revocation=revoked, basis=basis)
    assert d.scope.calls == before
    assert all(owner is not vm for owner, *_ in owned._CESSATIONS.values())


def test_disposal_reconciliation_keeps_incomplete_parent_unknown(disposal_attempt):
    d = disposal_attempt
    d.actual["complete_cleanup"] = False
    d.actual["unresolved_operations"] = [{"kind": "unknown-original-parent-stop"}]
    with pytest.raises(StateError, match="positive complete absence"):
        d.scope.vm.reconcile_disposal(
            d.context, operation_id=d.scope.operation[0], revocation=d.revocation, basis=d.basis
        )
    assert d.scope.vm._unknown
    assert sum(row[0] == "down" for row in d.scope.calls) == 1
    assert all(owner is not d.scope.vm for owner, *_ in owned._CESSATIONS.values())


def test_lost_local_cessation_issuance_is_readonly_recoverable(vm_scope, monkeypatch):
    scope = vm_scope
    scope.vm.up(scope.context)
    old = str(uuid4())
    scope.operation[0] = old
    context = down(scope)
    revocation = object()
    monkeypatch.setitem(
        sys.modules,
        "devsecops.factory.owned_runner_service",
        SimpleNamespace(require_runner_revocation=lambda *a, **kw: {}),
    )
    monkeypatch.setattr(owned, "_package", lambda *_a: (b"controlled", {}))
    basis = scope.vm.prepare_disposal(context, prior_operation_id=old, revocation=revocation)
    scope.operation[0] = str(uuid4())
    scope.state["active_mutation"] = {
        "continues_operation": old,
        "cleanup_mode": "dispose-owned-runner-guest",
    }
    original_issue = scope.vm._issue_cessation
    monkeypatch.setattr(
        scope.vm, "_issue_cessation", lambda *a, **kw: (_ for _ in ()).throw(OSError("lost local issuance"))
    )
    with pytest.raises(OSError):
        scope.vm.dispose_guest(context, revocation=revocation, basis=basis)
    assert scope.vm._absent
    actual = scope.vm.cleanup_observation(scope.vm._outcomes[-1])
    original_dispatch = scope.vm._dispatch

    def observed(current, action, *args, **kwargs):
        if action == "reconcile":
            result = dict(actual)
            result.pop("action")
            scope.calls.append((action, scope.operation[0], None))
            return result
        return original_dispatch(current, action, *args, **kwargs)

    monkeypatch.setattr(scope.vm, "_dispatch", observed)
    monkeypatch.setattr(scope.vm, "_issue_cessation", original_issue)
    outcome, proof = scope.vm.reconcile_disposal(
        context, operation_id=scope.operation[0], revocation=revocation, basis=basis
    )
    assert scope.vm.cleanup_observation(outcome)["complete_cleanup"]
    assert scope.vm.guest_cessation_artifacts(proof)
    assert [row[0] for row in scope.calls[-2:]] == ["down", "reconcile"]


@pytest.mark.parametrize("failure_stage", ["host-probe", "down-preparation"])
def test_undispatched_parent_probe_failure_requires_new_exact_successor(vm_scope, monkeypatch, failure_stage):
    scope = vm_scope
    scope.vm.up(scope.context)
    old = str(uuid4())
    scope.operation[0] = old
    context = down(scope)
    revocation = object()

    def require(value, *, vm, context, reconciliation_operation=None):
        if value is not revocation or vm is not scope.vm:
            raise StateError("Original runner revocation required")
        return {"runner_id": 17, "state": "absent"}

    monkeypatch.setitem(
        sys.modules,
        "devsecops.factory.owned_runner_service",
        SimpleNamespace(require_runner_revocation=require),
    )
    monkeypatch.setattr(owned, "_package", lambda *_a: (b"controlled", {}))
    original_basis = scope.vm.prepare_disposal(context, prior_operation_id=old, revocation=revocation)
    first = scope.operation[0] = str(uuid4())
    scope.state["active_mutation"] = {
        "continues_operation": old,
        "cleanup_mode": "dispose-owned-runner-guest",
    }
    original_dispatch = scope.vm._dispatch

    def lost_probe(*args, **kwargs):
        if failure_stage == "down-preparation" and args[1] == "host-observe":
            return original_dispatch(*args, **kwargs)
        assert args[1] == ("host-observe" if failure_stage == "host-probe" else "down")
        assert scope.vm._disposal_attempt["parent_dispatched"] is False
        assert scope.vm._disposal_attempt["intent"].exists()
        raise TimeoutError("controlled read-only host probe lost")

    monkeypatch.setattr(scope.vm, "_dispatch", lost_probe)
    with pytest.raises(TimeoutError):
        scope.vm.dispose_guest(context, revocation=revocation, basis=original_basis)
    assert not any(row[0] == "down" for row in scope.calls)
    assert (
        scope.vm.disposal_was_dispatched(
            context, operation_id=first, revocation=revocation, basis=original_basis
        )
        is False
    )
    with pytest.raises(StateError, match="not been dispatched"):
        scope.vm.reconcile_disposal(context, operation_id=first, revocation=revocation, basis=original_basis)
    assert all(owner is not scope.vm for owner, *_ in owned._CESSATIONS.values())
    monkeypatch.setattr(scope.vm, "_dispatch", original_dispatch)
    successor = scope.vm.prepare_disposal(context, prior_operation_id=first, revocation=revocation)
    assert (
        scope.vm.disposal_observation(successor, context)["guest_residual"]
        == "unresolved-contained-in-original-VM"
    )
    assert (
        scope.vm.disposal_was_dispatched(
            context, operation_id=first, revocation=revocation, basis=original_basis
        )
        is False
    )
    with pytest.raises(StateError, match="attempted"):
        scope.vm.dispose_guest(context, revocation=revocation, basis=successor)
    next_operation = scope.operation[0] = str(uuid4())
    scope.state["active_mutation"] = {
        "continues_operation": first,
        "cleanup_mode": "dispose-owned-runner-guest",
    }
    with pytest.raises(StateError, match="attempted"):
        scope.vm.dispose_guest(context, revocation=revocation, basis=original_basis)
    outcome, proof = scope.vm.dispose_guest(context, revocation=revocation, basis=successor)
    assert scope.vm.cleanup_observation(outcome)["complete_cleanup"]
    assert (
        owned.require_guest_kernel_cessation(proof, vm=scope.vm, context=context)["operation_id"]
        == next_operation
    )
    assert sum(row[0] == "down" for row in scope.calls) == 1
    assert len(scope.vm._disposal_attempt_history) == 1


def test_missing_original_dispatch_ledger_is_never_reported_as_no_effect(vm_scope):
    scope = vm_scope
    with pytest.raises(StateError, match="original disposal attempt"):
        scope.vm.disposal_was_dispatched(
            down(scope), operation_id=scope.operation[0], revocation=object(), basis=object()
        )
    assert not scope.calls


def test_original_unknown_delete_is_not_classified_undispatched(disposal_attempt):
    d = disposal_attempt
    assert (
        d.scope.vm.disposal_was_dispatched(
            d.context, operation_id=d.scope.operation[0], revocation=d.revocation, basis=d.basis
        )
        is True
    )
    with pytest.raises(StateError, match="unknown parent"):
        d.scope.vm.prepare_disposal(
            d.context, prior_operation_id=d.scope.operation[0], revocation=d.revocation
        )


@pytest.mark.parametrize("failure", ["source", "hash-missing", "ssh-context", "deadline", None])
def test_original_disposal_latch_is_at_final_transport_boundary(disposal_attempt, monkeypatch, failure):
    from contextlib import contextmanager
    from pathlib import Path
    from hashlib import sha256
    import json

    d = disposal_attempt
    vm = d.scope.vm
    vm._disposal_attempt["parent_dispatched"] = False  # controlled pretransport original attempt
    operation = d.scope.operation[0]
    child = {"action": "explicit-parent-disposal"}
    raw = b"controlled source bundle"
    fields = (
        "tcg",
        "runner_vm_recipe",
        "registry_remote",
        "runner_vm_remote",
        "runner_service_guest",
        "runner_vm_routes_remote",
        "runner_vm_guest_routes",
        "qemu_acceleration",
    )
    hashes = {name: sha256(raw).hexdigest() for name in fields}
    if failure == "hash-missing":
        hashes.pop("qemu_acceleration")
    original_read = owned._read

    def read(path, limit):
        if Path(path).parent == owned.PACKAGE_ROOT / "src/devsecops/factory":
            if failure == "source":
                raise OSError("controlled original package unavailable")
            return raw
        return original_read(path, limit)

    monkeypatch.setattr(owned, "_read", read)
    monkeypatch.setattr(vm.foundation, "current_transport_for", lambda *a: {})
    invoked = []

    @contextmanager
    def ssh_context(*_a):
        if failure == "ssh-context":
            raise OSError("controlled local transport preparation failed")
        if failure == "deadline":
            monkeypatch.setattr(owned.time, "monotonic", lambda: d.context.deadline + 1)
        yield ["ssh"]

    def transport(argv, deadline, **kwargs):
        assert vm._disposal_attempt["parent_dispatched"] is True
        invoked.append(argv)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "binding": "c" * 64,
                    "operation_id": operation,
                    "observation": {"binding": "c" * 64, "operation_id": operation},
                }
            ).encode(),
        )

    monkeypatch.setattr(owned.tcg, "_ssh_context", ssh_context)
    monkeypatch.setattr(owned.tcg, "_run", transport)

    def call():
        return owned.OwnedRunnerVM._dispatch(
            vm, d.context, "down", operation, {"transport": {}}, hashes, "c" * 64, child
        )

    if failure is None:
        call()
        assert len(invoked) == 1
    else:
        with pytest.raises((OSError, StateError)):
            call()
        assert not invoked and vm._disposal_attempt["parent_dispatched"] is False
