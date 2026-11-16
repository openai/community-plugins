"""Controlled direct-origin tests; no VM, SSH, registration or CI is executed."""

from copy import deepcopy
import os
import pickle
from types import SimpleNamespace
import time

import pytest
from devsecops.factory import owned_runner_service as s
from devsecops.factory.owned_gitlab_cleanup import OwnedGitLabResources
from devsecops.factory.state import StateError, _sha


@pytest.fixture
def original(tmp_path):
    journal = SimpleNamespace(
        run_dir=tmp_path, identities={"package_sha256": "a" * 64}, state={"run_id": "run"}
    )
    owner = object.__new__(s.OwnedRunnerService)
    owner._pid = os.getpid()
    owner.journal = journal
    owner.driver = SimpleNamespace(_admit=lambda *args: None)
    owner.vm = object()
    owner._unknown = False
    owner._terminal = None
    owner._runner_vm = {"binding": "b" * 64}
    owner._runner_id = 67
    owner._description = "owned-runner"
    path = tmp_path / "api-event.json"
    path.write_bytes(b'{"event":"owned-runner-observed"}')
    api = object.__new__(OwnedGitLabResources)
    api.pid = os.getpid()
    api.owner = SimpleNamespace(journal=journal)
    api.context = SimpleNamespace(run_id="run")
    api._down_operation = "cleanup-1"
    api.events = [path]
    api._event_refs = {path: {"bytes": path.stat().st_size, "sha256": _sha(path.read_bytes())}}
    ticket = object()
    api.runners = [
        (ticket, {"id": 67, "description": "owned-runner", "state": "owned-present", "project_ids": [91, 92]})
    ]
    owner._registration = (api, ticket)
    owner._evidence = {}
    owner._revocation = None
    origin = object.__new__(s.RunnerExecutionOrigin)
    s._ORIGINS[origin] = {
        "pid": os.getpid(),
        "owner": owner,
        "deadline": time.monotonic() + 30,
        "facts": {
            "run_id": "run",
            "runner_id": 67,
            "runner_manager_system_identity": "s_owned",
            "runner_vm": deepcopy(owner._runner_vm),
            "process": {"supervisor": {"pid": 42}},
        },
    }
    return owner, api, origin


def require(origin, **kwargs):
    return s.require_runner_execution(origin, run_id="run", runner_id=67, manager="s_owned", **kwargs)


def test_original_process_and_registration_are_both_required(original):
    owner, api, origin = original
    assert require(origin, current=True)["runner_vm"] == owner._runner_vm
    with pytest.raises(StateError):
        require(require(origin))
    with pytest.raises(TypeError):
        pickle.dumps(origin)
    api.runners[0][1]["id"] = 68
    with pytest.raises(StateError, match="registration differs"):
        require(origin)


def test_historical_evidence_does_not_renew_current_runner_lifetime(original):
    owner, _, origin = original
    s._ORIGINS[origin]["deadline"] = time.monotonic() - 1
    assert require(origin)["runner_id"] == 67
    with pytest.raises(StateError, match="lifetime"):
        require(origin, current=True)
    owner._terminal = object()
    assert require(origin)["runner_id"] == 67


def test_unknown_write_stops_new_ci_but_preserves_original_history(original):
    owner, _, origin = original
    owner._unknown = True
    with pytest.raises(StateError):
        require(origin, current=True)
    assert require(origin)["runner_id"] == 67


def test_registration_byte_tamper_invalidates_even_historical_origin(original):
    _, api, origin = original
    api.events[0].write_bytes(b"changed")
    with pytest.raises(StateError, match="evidence changed"):
        require(origin)


def test_revocation_needs_original_ticket_actual_absence_and_current_cleanup(original):
    owner, api, _ = original
    context = SimpleNamespace(run_id="run", phase={"operation": "down"}, deadline=time.monotonic() + 30)
    with pytest.raises(StateError, match="unobserved"):
        owner.revocation_proof(context)
    api.runners[0][1]["state"] = "absent"
    proof = owner.revocation_proof(context)
    assert s.require_runner_revocation(proof, vm=owner.vm, context=context)["runner_id"] == 67
    with pytest.raises(StateError):
        s.require_runner_revocation({"runner_id": 67}, vm=owner.vm, context=context)
    with pytest.raises(StateError):
        s.require_runner_revocation(proof, vm=object(), context=context)
    api.runners[0][1]["state"] = "delete-unknown"
    with pytest.raises(StateError):
        s.require_runner_revocation(proof, vm=owner.vm, context=context)


def test_cleanup_ready_requires_original_complete_outcome(original, monkeypatch):
    owner, _, _ = original
    assert owner.vm_cleanup_ready(None) is False
    owner._terminal = object()
    monkeypatch.setattr(
        owner,
        "cleanup_observation",
        lambda outcome: {
            "runner_vm": owner._runner_vm,
            "complete_cleanup": True,
            "unresolved_operations": ["lost-child"],
            "resources": [],
        },
    )
    assert owner.vm_cleanup_ready(None) is False


def staged_dependencies(mode="operator-trusted-package"):
    from devsecops.factory.state import IDENTITY_KEYS
    from devsecops.factory.bootstrap_recipe import canonical

    source = {"kind": mode}
    if mode == "operator-trusted-package":
        source.update(package_sha256="a" * 64, max_age_seconds=300)
    policy = {
        "schema_version": 1,
        "kind": "publisher-public-key-policy",
        "publisher": "platform-one-iron-bank",
        "scope": {"registry_origin": "https://registry1.dso.mil", "repository_prefix": "ironbank/"},
        "key": {"pem_sha256": "a" * 64, "spki_sha256": "b" * 64},
        "validity": {"not_before": "2026-10-01T00:00:00Z", "not_after": "2026-11-01T00:00:00Z"},
        "source": source,
        "revoked_spki_sha256": [],
    }
    raw = canonical(policy)
    build = {
        "schema_version": 1,
        "kind": "mission-build-configuration",
        "owner_uid": 10001,
        "source": {"repository": "/work/source", "revision": "a" * 40, "path": "mission"},
        "publisher": {
            "policy": "/inputs/publisher.json",
            "policy_sha256": _sha(raw),
            "public_key": "/inputs/key.pem",
            "package": "/inputs/authority.json" if mode == "operator-trusted-package" else None,
        },
        "image": {"policy": "/inputs/image.json", "policy_sha256": "c" * 64},
        "tools": {"cosign": "/opt/cosign", "build_tools": "/opt/build.json", "wheel": "/opt/wheels"},
        "registry_auth": {"token_env": "DEVSECOPS_REGISTRY_TOKEN", "ca_bundle": "/inputs/external-ca.pem"},
        "run_scope": {name: "d" * 64 for name in IDENTITY_KEYS},
    }
    encoded = canonical(build)
    invocation = canonical(
        {"configurations": {"build": {"path": "/inputs/build.json", "sha256": _sha(encoded)}}}
    )
    return {
        "entries": {
            "/inputs/publisher.json": {"content": raw},
            "/inputs/build.json": {"content": encoded},
            "/inputs/invocation.json": {"content": invocation},
        },
        "environment": {"DEVSECOPS_GIT_INVOCATION": "/inputs/invocation.json"},
        "secrets": {"DEVSECOPS_REGISTRY_TOKEN": "private-controlled"},
        "service_trust": {"external": {"path": "/inputs/external-ca.pem", "sha256": "e" * 64}},
    }


@pytest.mark.parametrize(
    "mode,origins",
    [
        ("operator-trusted-package", ["https://registry1.dso.mil"]),
        ("p1-current-document", ["https://registry1.dso.mil", "https://p1docs.dso.mil"]),
    ],
)
def test_external_admission_uses_actual_pinned_publisher_choice(mode, origins):
    assert s._admit_external_dependencies(staged_dependencies(mode), {"external_origins": origins}) == tuple(
        sorted(origins)
    )


@pytest.mark.parametrize("damage", ["missing-registry", "extra-p1docs", "credentials", "ca", "policy-bytes"])
def test_external_admission_rejects_missing_or_broadened_inputs(damage):
    selected = staged_dependencies()
    origins = ["https://registry1.dso.mil"]
    if damage == "missing-registry":
        origins = []
    elif damage == "extra-p1docs":
        origins.append("https://p1docs.dso.mil")
    elif damage == "credentials":
        selected["secrets"] = {}
    elif damage == "ca":
        selected["service_trust"]["external"]["path"] = "/inputs/different.pem"
    else:
        selected["entries"]["/inputs/publisher.json"]["content"] += b" "
    with pytest.raises(StateError):
        s._admit_external_dependencies(selected, {"external_origins": origins})


def test_original_host_service_binds_install_packet_then_current_origin(original, monkeypatch):
    from devsecops.factory import runner_install
    from devsecops.factory.cleanup_observation import ComponentObservations

    owner, api, _ = original
    owner._started = False
    owner._plan = object()
    owner.services = object()
    owner._token = "glrt-" + "a" * 24
    owner._service_nonce = "12345678-1234-1234-1234-123456789012"
    owner._down_operation = None
    owner._dispatches = []
    owner._observations = ComponentObservations()
    owner._runner = None
    context = SimpleNamespace(
        run_id="run",
        phase={"id": "up", "operation": "up"},
        deadline=time.monotonic() + 30,
        identities=owner.journal.identities,
    )
    owner.journal.current_mutation = lambda *a: "up-op"
    route = {
        "external_origins": ["https://registry1.dso.mil"],
        "route_trust": {"files": {}, "environment": {}},
    }
    owner.routes = SimpleNamespace(require_ready=lambda c: route)
    owner.vm = SimpleNamespace(endpoint=lambda c: {"runner_vm": owner._runner_vm})
    selected = staged_dependencies()
    selected.update(
        runner_profile="owned-runner-vm-v1",
        guest_package={},
        guest_paths={},
        config_sha256={"build": "a" * 64},
        original_template_sha256={"build": "b" * 64},
    )
    for row in selected["entries"].values():
        row["private"] = False
    monkeypatch.setattr(runner_install, "require_runner_install", lambda *a: selected)
    sent = []
    issued = []

    def dispatch(c, *, action, packet, **kw):
        sent.append(deepcopy(packet))
        report = {
            "runner_vm": owner._runner_vm,
            "resources": [{"state": "owned-present", "disposition": "remove"}],
            "unresolved_operations": [],
            "mutation_effect": "applied",
            "complete_cleanup": False,
            "service_ready": True,
            "runner_id": 67,
            "runner_manager_system_identity": "s_owned",
            "process": {"supervisor": {"pid": 42}},
        }
        value = {"observation": report, "transport_receipt": {"artifact": "controlled", "sha256": "a" * 64}}
        issued.append(value)
        return value

    owner.vm.dispatch_runner = dispatch
    owner.vm.require_dispatch = (
        lambda value, *a, **kw: value
        if any(value is x for x in issued)
        else (_ for _ in ()).throw(StateError("copied"))
    )
    api.scope = SimpleNamespace(origin="https://gitlab.demo.test:8444")
    outcome = owner.up(context)
    assert owner.cleanup_observation(outcome)["runner_id"] == 67
    assert sent[0]["installation"]["runner_token"] == "glrt-" + "a" * 24
    assert sent[0]["service_nonce"] == owner._service_nonce
    origin = owner.execution_origin(context)
    assert require(origin, current=True)["process"] == {"supervisor": {"pid": 42}}
    assert owner._runner["runner_input_config_sha256"] == selected["config_sha256"]
    assert sent[1]["expected_process"] == sent[0].get("expected_process", {"supervisor": {"pid": 42}})
    assert "installation" not in sent[1]
    assert all(
        "glrt-" not in p.read_text() and owner._service_nonce not in p.read_text()
        for p in owner.evidence_artifacts(context)
    )
    with pytest.raises(StateError, match="already attempted"):
        owner.up(context)


def test_ci_constructor_admits_original_runner_and_retains_public_guest_proof(original, tmp_path):
    from devsecops.factory.gitlab_api import GitLabSession
    from devsecops.factory.gitlab_bootstrap import BootstrapScope
    from devsecops.factory.owned_ci import OwnedCIPipeline, PipelineSelection

    owner, api, origin = original
    session = object.__new__(GitLabSession)
    session._origin = "https://git.example.test"
    session._expected_user_id = 17
    scope = BootstrapScope(session.origin, "demo", "b" * 64, 17, 20, "demo-owner")
    selected = PipelineSelection(
        91,
        "a" * 40,
        67,
        "s_owned",
        ("publish_and_prepare_mission_git",),
        "publish_and_prepare_mission_git",
        "operation-1",
        "run",
    )
    flow = OwnedCIPipeline(
        session, scope, selected, journal_root=tmp_path, deadline=time.monotonic() + 30, runner_origin=origin
    )
    try:
        assert (flow.path / "runner-origin.json").is_file()
        assert "private-controlled" not in (flow.path / "runner-origin.json").read_text()
        flow._audit()
        api.events[0].write_bytes(b"replaced")
        with pytest.raises(StateError):
            flow._audit_retained(time.monotonic() + 1)
    finally:
        flow.close()


def test_revoked_registration_blocks_new_ci_without_erasing_history(original):
    _, api, origin = original
    api.runners[0][1]["state"] = "absent"
    with pytest.raises(StateError):
        require(origin, current=True)
    assert require(origin)["runner_id"] == 67


@pytest.mark.parametrize("available", [False, True])
def test_canonical_prepare_checks_actual_external_dependencies_before_provision(
    tmp_path, monkeypatch, available
):
    from devsecops.factory import canonical_bootstrap as bootstrap, runner_install
    from devsecops.factory.bootstrap_recipe import canonical
    from test_runner_vm_recipe import selection

    vm, _ = selection(tmp_path)
    if available:
        vm["egress"] = {
            "external_origins": ["https://registry1.dso.mil"],
            "external_ca_bundle": {"path": str(tmp_path / "external.pem"), "sha256": "a" * 64},
        }
    path = tmp_path / "vm.json"
    path.write_bytes(canonical(vm))
    recipe = {
        "kind": "owned-bootstrap-v2",
        "services": {"runner_vm": {"path": str(path), "sha256": _sha(path.read_bytes())}},
    }
    coordinator = object.__new__(bootstrap.OwnedBootstrapCoordinator)
    coordinator._preflight = object()
    coordinator._prepared = False
    coordinator.services = object()
    coordinator.recipe_sha256 = "b" * 64
    coordinator.journal = SimpleNamespace(current_mutation=lambda *a: "prepare-op", run_dir=tmp_path)
    coordinator._evidence = {}
    monkeypatch.setattr(coordinator, "_admit", lambda ctx: recipe)
    monkeypatch.setattr(coordinator, "_seeds", lambda selected: ())
    monkeypatch.setattr(coordinator, "evidence_artifacts", lambda ctx: tuple(coordinator._evidence))
    plan = object()
    events = []
    monkeypatch.setattr(
        runner_install, "prepare_runner_install", lambda *a: events.append("compile-local") or plan
    )

    def inspect(value, stack, context):
        assert value is plan and stack is coordinator.services
        events.append("inspect-original")
        return staged_dependencies()

    monkeypatch.setattr(runner_install, "inspect_runner_install", inspect)
    context = SimpleNamespace(run_id="12345678-1234-1234-1234-123456789012", phase={"id": "prepare"})
    if available:
        assert coordinator.prepare(context).status == "completed"
        assert coordinator._prepared is True
    else:
        with pytest.raises(StateError, match="external routes"):
            coordinator.prepare(context)
        assert not coordinator._prepared and not coordinator._evidence
    assert events == ["compile-local", "inspect-original"]


@pytest.mark.parametrize("read_operation", [None, "disposal-op"])
def test_cessation_consumes_original_proof_mode_and_retains_parent_bytes(
    original, monkeypatch, read_operation
):
    from devsecops.factory import owned_runner_vm
    from devsecops.factory.cleanup_observation import ComponentObservations

    owner, api, _ = original
    api.runners[0][1]["state"] = "absent"
    owner._runner = {"runner_manager_system_identity": "s_owned", "process": {"supervisor": {"pid": 42}}}
    owner._observations = ComponentObservations()
    parent = owner.journal.run_dir / "parent-absence.json"
    parent.write_bytes(b'{"original-parent":"absent"}')
    proof, observed = object(), []
    owner.vm = SimpleNamespace(
        guest_cessation_artifacts=lambda original: (parent,) if original is proof else ()
    )

    def admit(value, *, vm, context, **kwargs):
        assert value is proof and vm is owner.vm
        observed.append(kwargs)
        return {"runner_vm": owner._runner_vm, "operation_id": "disposal-op", "ci_effects": "not-reconciled"}

    monkeypatch.setattr(owned_runner_vm, "require_guest_kernel_cessation", admit)
    context = SimpleNamespace(run_id="run", identities=owner.journal.identities)
    outcome = owner.accept_guest_cessation(proof, context, reconciliation_operation=read_operation)
    assert observed == ([{}] if read_operation is None else [{"reconciliation_operation": read_operation}])
    report = owner.cleanup_observation(outcome)
    assert report["action"] == ("down" if read_operation is None else "reconcile")
    assert report["ci_effects"] == "not-reconciled" and report["service_ready"] is False
    assert parent in outcome.artifacts
    parent.write_bytes(b"changed")
    with pytest.raises(StateError, match="evidence changed"):
        owner.cleanup_observation(outcome)


@pytest.mark.parametrize("changed", ["process", "manager", "runner_id"])
def test_changed_fresh_process_cannot_replace_original_service_identity(original, changed):
    from devsecops.factory.cleanup_observation import ComponentObservations

    owner, _, origin = original
    owner._runner = deepcopy(s._ORIGINS[origin]["facts"])
    owner._runner["runner_input_config_sha256"] = {"build": "c" * 64}
    before = deepcopy(owner._runner)
    owner._dispatches = []
    owner._observations = ComponentObservations()
    report = {
        "runner_vm": owner._runner_vm,
        "resources": [],
        "unresolved_operations": [],
        "mutation_effect": "not_applied",
        "complete_cleanup": False,
        "service_ready": True,
        "runner_id": 67,
        "runner_manager_system_identity": "s_owned",
        "process": {"supervisor": {"pid": 43}},
    }
    if changed != "process":
        report["process"] = deepcopy(before["process"])
        report["runner_manager_system_identity" if changed == "manager" else "runner_id"] = (
            "s_other" if changed == "manager" else 68
        )
    value = {"observation": report, "transport_receipt": {}}
    owner.vm = SimpleNamespace(dispatch_runner=lambda *a, **kw: value, require_dispatch=lambda *a, **kw: None)
    context = SimpleNamespace(run_id="run", identities=owner.journal.identities)
    packet = {"operation_id": "current", "runner_vm": owner._runner_vm}
    with pytest.raises(StateError, match="Original runner process"):
        owner._observe(context, "observe", packet)
    assert owner._runner == before and owner._unknown is True
    with pytest.raises(StateError):
        require(origin, current=True)
    assert require(origin)["process"] == {"supervisor": {"pid": 42}}
