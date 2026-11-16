"""Controlled phase-call boundaries; no network, lab, image or Git effects."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from devsecops.factory.lifecycle import PhaseOutcome, AssertionResult
from devsecops.factory.owned_operations import OwnedOperationCoordinator
from devsecops.factory.state import StateError


@pytest.mark.parametrize("operation", ["inject", "deliver", "reset"])
def test_policy_dispatch_preserves_runtime_outcome_and_joins_original_operation_evidence(
    tmp_path, monkeypatch, operation
):
    from devsecops.factory import owned_policy_delivery

    coordinator = object.__new__(OwnedOperationCoordinator)
    phase = {
        "id": "reset" if operation == "reset" else "primary-" + operation,
        "operation": operation,
        "attempt": "shared" if operation == "reset" else "primary",
    }
    context = SimpleNamespace(phase=phase)
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text('{"controlled":true}')
    git_path = tmp_path / "git.json"
    git_path.write_text('{"controlled":true}')
    original = PhaseOutcome(
        "incomplete",
        "controlled-probe-not-finished",
        (runtime_path,),
        (AssertionResult("primary", "controlled", "unknown"),),
        "unknown",
    )
    coordinator._deliveries = {}
    coordinator._admit = lambda *args: None
    coordinator._runtime_input_coordinator = lambda: None
    coordinator.runtime_image = lambda ctx: object()
    coordinator.locked_images = lambda ctx: object()
    coordinator.runtime = SimpleNamespace(
        bind_runtime_image=lambda *a: None, bind_locked_images=lambda *a: None
    )
    coordinator.prepare_runtime = lambda ctx: original
    coordinator.evidence_artifacts = lambda ctx: (git_path, runtime_path)
    calls = []
    marker = object()
    monkeypatch.setattr(
        owned_policy_delivery,
        "execute_policy_delivery",
        lambda ctx, operations: (calls.append("actual-fixed-producer"), marker)[1],
    )
    coordinator._retain_delivery = lambda ctx, value, **kw: calls.append((value, kw))
    result = coordinator.dispatch(context)
    assert result.status == original.status and result.code == original.code
    assert result.assertions == original.assertions and result.mutation_effect == "unknown"
    assert set(result.artifacts) == {runtime_path, git_path}
    assert calls[1] == (
        marker,
        {
            "attempt": "replay" if operation == "reset" else "primary",
            "stage": {"inject": "fault", "deliver": "recovery", "reset": "baseline"}[operation],
        },
    )


def test_evidence_union_is_bounded_and_cannot_use_nonfiles(tmp_path):
    coordinator = object.__new__(OwnedOperationCoordinator)
    paths = []
    for index in range(257):
        path = tmp_path / str(index)
        path.write_text("x")
        paths.append(path)
    with pytest.raises(StateError, match="path closure"):
        coordinator._bounded_artifacts(paths)
    with pytest.raises((StateError, OSError, ValueError)):
        coordinator._bounded_artifacts((tmp_path,))


def test_build_dispatch_joins_ci_delivery_and_runtime_outcomes(tmp_path, monkeypatch):
    from dataclasses import asdict
    from devsecops.factory import canonical_bootstrap, owned_git_delivery, owned_operations
    from test_owned_ci import SCOPE

    coordinator = object.__new__(OwnedOperationCoordinator)
    context = SimpleNamespace(
        phase={"id": "build-promote", "operation": "build-promote", "attempt": "shared"},
        inputs={"config": {"allowed_mutations": ["promote", "deliver", "provision", "probe"]}},
        run_id="controlled",
        deadline=12345678,
        remaining_seconds=lambda: 30,
    )
    runtime = tmp_path / "runtime.json"
    runtime.write_text("{}")
    ci = tmp_path / "ci.json"
    ci.write_text("{}")
    original = PhaseOutcome("completed", "runtime-ready", (runtime,), mutation_effect="applied")
    coordinator.journal = SimpleNamespace(run_dir=tmp_path, state={}, identities={})
    coordinator._admit = lambda *args: "operation-1"
    coordinator._recipe = lambda: {
        "plugin_id": "controlled",
        "plugin_version": "test",
        "ci": {
            "required_jobs": ["publish_and_prepare_mission_git"],
            "publication_job": "publish_and_prepare_mission_git",
            "delivery_required_jobs": ["validate_owned_deployment"],
            "poll_seconds": 1,
            "max_observations": 1,
        },
    }
    coordinator._runtime_input_coordinator = lambda: None
    coordinator._ci = None
    route = object()
    runner_origin = object()  # Controlled identity; no real runner capability.
    coordinator.bootstrap = SimpleNamespace(
        gitlab=lambda ctx: (object(), object()),
        writer=lambda ctx: (object(), object()),
        runner_origin=lambda ctx: runner_origin,
        service_route=lambda ctx, name: route
        if name == "gitlab"
        else pytest.fail("wrong fixed service route"),
    )
    facts = {
        "scope": asdict(SCOPE),
        "projects": {"source": {"id": 91, "seed_commit": "a" * 40}},
        "runner_id": 67,
        "runner_manager_system_identity": "controlled",
    }
    monkeypatch.setattr(canonical_bootstrap, "require_bootstrap_authority", lambda *a: facts)
    origin = object()
    calls = []
    flow = SimpleNamespace(
        start=lambda: calls.append("ci-start"),
        observe=lambda: {"state": "success", "origin": origin},
        publication_report=lambda cap: b"{}",
    )

    def pipeline(*args, **kwargs):
        assert kwargs["runner_origin"] is runner_origin
        return flow

    monkeypatch.setattr(owned_operations, "OwnedCIPipeline", pipeline)
    prepared = object.__new__(owned_operations.PreparedOperationGit)
    owned_operations._GIT[prepared] = {
        "writer": (tmp_path / "writer", "a" * 64),
        "destination": tmp_path / "destination",
        "destination_sha256": "b" * 64,
    }
    coordinator._prepare_git = lambda *a: prepared
    marker = object()

    def deliver(*args, **kwargs):
        assert kwargs["service_route"] is route
        return marker

    monkeypatch.setattr(owned_git_delivery, "deliver_prepared", deliver)
    monkeypatch.setattr(owned_git_delivery, "merge_reviewed", lambda *a, **kw: marker)
    monkeypatch.setattr(
        owned_git_delivery,
        "require_delivery",
        lambda *a, **kw: {
            "delivery_stage": "merged-watched-branch",
            "watched_revision": "c" * 40,
            "deployment_commit": "c" * 40,
        },
    )
    coordinator._retain_delivery = lambda *a, **kw: calls.append("retained-delivery")
    coordinator.publication = lambda: object()
    coordinator.runtime_image = lambda ctx: object()
    coordinator.locked_images = lambda ctx: object()
    coordinator.runtime = SimpleNamespace(
        bind_mission_publication=lambda *a: None,
        bind_runtime_image=lambda *a: None,
        bind_locked_images=lambda *a: None,
    )
    coordinator.prepare_runtime = lambda ctx: original
    coordinator.evidence_artifacts = lambda ctx: (ci,)
    result = coordinator.dispatch(context)
    assert calls == ["ci-start", "retained-delivery"]
    assert set(result.artifacts) == {runtime, ci} and result.mutation_effect == "applied"
    assert result.code == original.code


def test_owned_probe_observes_existing_actors_without_requesting_a_new_mutation():
    coordinator = object.__new__(OwnedOperationCoordinator)
    calls = []
    coordinator._admit = lambda ctx, action=None: calls.append(action)
    outcome = PhaseOutcome("completed", "observed", mutation_effect="not_applied")
    coordinator.runtime = SimpleNamespace(dispatch=lambda ctx: outcome)
    assert (
        coordinator.dispatch(SimpleNamespace(phase={"id": "primary-probes", "operation": "probe"})) is outcome
    )
    assert calls == [None, None]


# Explicit fixture exports preserve pytest registration and import order.
from test_ci_publication import case as case  # noqa: E402
from test_owned_ci import wire as wire  # noqa: E402
from test_runtime_image import runtime_case as runtime_case, history_responses  # noqa: E402


def test_phase_closure_exports_original_ci_image_and_fresh_history_bytes(runtime_case, wire, tmp_path):  # noqa: F811 - pytest injects the registered fixture
    from devsecops.factory import ci_publication

    c = runtime_case
    history_responses(wire, c["base"]["args"]["selector_raw"])
    image = ci_publication.refresh_runtime_image(**c["args"])
    coordinator = object.__new__(OwnedOperationCoordinator)
    coordinator._ci = c["base"]["flow"]
    scope = c["publication"].describe()["run_scope"]
    coordinator.journal = SimpleNamespace(identities=scope)
    coordinator._admit = lambda ctx: None
    bootstrap = tmp_path / "original-bootstrap.json"
    bootstrap.write_text('{"controlled":true}')
    coordinator.bootstrap = SimpleNamespace(evidence_artifacts=lambda ctx: (bootstrap,))
    coordinator._publication = c["publication"]
    coordinator._runtime_image = image
    coordinator._locked_images = None
    coordinator._derived_evidence = {}
    coordinator._deliveries = {}
    coordinator._source_checkouts = {}
    context = SimpleNamespace(
        run_id=coordinator._ci.selection.run_id,
        phase={"id": "build-promote", "operation": "build-promote", "attempt": "shared"},
    )
    paths = coordinator.evidence_artifacts(context)
    assert bootstrap in paths
    assert Path(image.describe()["history_readback"]["path"]) in paths
    assert len([path for path in paths if path.name.startswith("evidence-")]) == 1
    assert len([path for path in paths if path.name == "ci-publication.json"]) == 2
    assert all(path.is_file() for path in paths)
