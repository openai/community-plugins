"""Read-only endpoint scope with fixed admission and controlled package/input I/O."""

from copy import deepcopy
import json
import os
import time
from types import SimpleNamespace

import pytest

from devsecops.factory import owned_services as module
from devsecops.factory.lifecycle import PhaseContext, PhaseOutcome
from devsecops.factory.state import StateError, _sha
from tests.test_canonical_bootstrap import selected


def endpoint(tmp_path, monkeypatch, phase_id, action):
    recipe = selected(tmp_path)
    raw = module.bootstrap_recipe.canonical(recipe)
    recipe_path = tmp_path / "bootstrap.json"
    recipe_path.write_bytes(raw)
    identity = {"package_sha256": "a" * 64}
    phase = {"id": phase_id, "operation": action}
    refs = {"config": {"path": "config.json"}}
    docs = {
        "config": {
            "profile": "bigbang-live",
            "services": [
                {
                    "id": "gitlab",
                    "origin": recipe["services"]["origin"],
                    "ca_file": recipe["services"]["materials"]["ca"]["path"],
                    "operator_auth": {"token_env": recipe["services"]["credentials"]["operator_token_env"]},
                }
            ],
        }
    }
    lifecycle = {
        "driver_id": "owned-linux",
        "active_phase": {"phase_id": phase_id},
        "phases": [phase],
        "inputs": refs,
        "execution_mode": "operator",
        "final_result": None,
    }
    calls = []
    expected_action = "platform-dependency" if phase_id == "platform-dependency" else action

    def current(selected_phase, selected_action, *, reconciliation):
        assert reconciliation is True
        if (selected_phase, selected_action) != (phase_id, expected_action):
            raise StateError("Exact original mutation action required")
        calls.append(("read-admission", selected_phase, selected_action))
        return "original-unknown-operation"

    journal = SimpleNamespace(
        run_dir=tmp_path,
        identities=identity,
        _lifecycle_locked=True,
        needs_reconciliation=True,
        current_mutation=current,
        _artifact_path=lambda path: tmp_path / path,
        state={"run_id": "run", "environment_id": recipe["environment"], "lifecycle": lifecycle},
    )
    component = object.__new__(module.OwnedServiceStack)
    component.journal, component._pid = journal, os.getpid()
    component.driver = SimpleNamespace(
        plugin_id="controlled",
        plugin_version="controlled",
        _auxiliary_artifacts=lambda: calls.append(("auxiliary-audit",)),
    )
    component.recipe_path, component.recipe_sha256 = recipe_path, _sha(raw)
    component._unknown, component._evidence = False, {}
    hashes = {
        "factory/" + name: _sha((module.PACKAGE_ROOT / "src/devsecops/factory" / name).read_bytes())
        for name in module.MODULES
    }
    members = {"src/devsecops/" + key: value for key, value in hashes.items()}
    monkeypatch.setattr(module, "_load_inputs", lambda *args: ({}, deepcopy(docs)))
    monkeypatch.setattr(module, "_package", lambda *args: ({}, members))
    component._service_binding = module.bootstrap_recipe.binding(
        recipe, run_id="run", identities=identity, package_modules=hashes
    )
    report = {
        "resources": [
            {"identity": {"role": role, "kind": kind, "id": role + "-id", "name": role}, "state": "present"}
            for role, kind in [("server", "container"), ("network", "network")]
        ]
    }
    path = tmp_path / "original-services.json"
    path.write_text(json.dumps(report))
    outcome = PhaseOutcome("incomplete", "original-component", (path,))
    component._outcomes = [(outcome, report, _sha(path.read_bytes()))]
    context = PhaseContext("run", phase, identity, docs, refs, tmp_path, time.monotonic() + 30, "operator")
    monkeypatch.setattr(
        component, "_dispatch", lambda *args: pytest.fail("Endpoint read must not dispatch service effects")
    )
    return component, context, calls, members


@pytest.mark.parametrize(
    "phase_id,action",
    [
        ("provision", "up"),
        ("primary-build-promote", "build-promote"),
        ("primary-inject", "inject"),
        ("primary-deliver", "deliver"),
        ("reset", "reset"),
        ("cleanup", "down"),
        ("platform-dependency", "case"),
    ],
)
def test_unknown_nonservice_mutation_can_read_exact_original_endpoint(
    tmp_path, monkeypatch, phase_id, action
):
    component, context, calls, _ = endpoint(tmp_path, monkeypatch, phase_id, action)
    facts = component.service_endpoint(context, reconciliation_operation="original-unknown-operation")
    assert facts["server"]["id"] == "server-id" and facts["network"]["id"] == "network-id"
    assert facts["runner"] is None and facts["binding"] == component._service_binding
    assert calls[0] == (
        "read-admission",
        phase_id,
        "platform-dependency" if phase_id == "platform-dependency" else action,
    )
    with pytest.raises(TypeError):
        facts["server"]["id"] = "replacement"
    if action not in ("up", "down"):
        with pytest.raises(StateError, match="exact current lifecycle"):
            component._admit(context, "reconcile", "original-unknown-operation")


@pytest.mark.parametrize(
    "change",
    [
        "wrong-operation",
        "not-unknown",
        "source-pin",
        "ca-pin",
        "outcome-bytes",
        "binding",
        "wrong-process",
        "input-scope",
    ],
)
def test_endpoint_read_rejects_changed_original_scope(tmp_path, monkeypatch, change):
    component, context, _, members = endpoint(tmp_path, monkeypatch, "primary-deliver", "deliver")
    operation = "original-unknown-operation"
    if change == "wrong-operation":
        operation = "another-operation"
    elif change == "not-unknown":
        component.journal.needs_reconciliation = False
    elif change == "source-pin":
        members["src/devsecops/factory/owned_services.py"] = "b" * 64
    elif change == "ca-pin":
        (tmp_path / "ca").write_text("changed")
    elif change == "outcome-bytes":
        component._outcomes[0][0].artifacts[-1].write_text("{}")
    elif change == "binding":
        component._service_binding = "b" * 64
    elif change == "wrong-process":
        component._pid = -1
    else:
        context.inputs["config"]["services"][0]["origin"] = "https://wrong.test"
    with pytest.raises(StateError):
        component.service_endpoint(context, reconciliation_operation=operation)
