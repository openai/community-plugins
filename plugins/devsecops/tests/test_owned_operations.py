"""Actual local journal boundary tests; no bootstrap or lab effects."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
import threading
import time

import pytest

from devsecops.factory.lifecycle import PhaseContext, _readonly
from devsecops.factory.operations_recipe import operations_configuration, bind_operations_recipe
from devsecops.factory.owned_operations import OwnedOperationCoordinator, PHASE_ROUTES
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha
from test_factory_lifecycle import inputs_for


def setup(tmp_path):
    inputs, _ = inputs_for(tmp_path)
    inputs["config"]["expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    raw = {
        name: _canonical(value) if type(value) is dict else value.read_bytes()
        for name, value in inputs.items()
    }
    identities = {name + "_sha256": _sha(value) for name, value in raw.items()}
    journal = RunJournal.create(
        tmp_path / "run", identities=identities, environment_id=inputs["config"]["environment"]
    )
    journal.initialize_lifecycle(inputs=raw, execution_mode="operator", driver_id="owned-linux")
    # Exercise the admission implementation directly, without issuing a fake
    # BootstrapAuthority or constructing an effect-capable production session.
    coordinator = object.__new__(OwnedOperationCoordinator)
    coordinator.journal = journal
    coordinator.pid = os.getpid()
    coordinator._thread = threading.get_ident()
    values = {name: json.loads(value) for name, value in raw.items() if name != "package"}
    values["package"] = raw["package"]

    def context(phase):
        return PhaseContext(
            journal.state["run_id"],
            _readonly(phase),
            _readonly(identities),
            _readonly(values),
            _readonly(journal.state["lifecycle"]["inputs"]),
            journal.run_dir,
            time.monotonic() + 60,
            "operator",
        )

    return journal, coordinator, values, context


def test_fixed_routes_cover_exact_33_phase_contract(tmp_path):
    _, _, values, _ = setup(tmp_path)
    assert len(PHASE_ROUTES) == 33
    assert set(PHASE_ROUTES) == {phase["id"] for phase in values["scenario"]["phases"]}
    assert PHASE_ROUTES["reset"] == "git-baseline-restoration"


def test_current_phase_context_is_readonly_normalized_and_byte_bound(tmp_path):
    journal, coordinator, values, context = setup(tmp_path)
    phase = values["scenario"]["phases"][0]
    with journal.lifecycle_lock():
        journal.begin_phase(phase["id"])
        assert coordinator._admit(context(phase)) is None
        values["config"]["allowed_mutations"].append("not-authorized")
        with pytest.raises(StateError, match="configuration differs"):
            coordinator._admit(context(phase))


def test_build_grant_does_not_substitute_for_current_build_promote_action(tmp_path):
    journal, coordinator, values, context = setup(tmp_path)
    phases = values["scenario"]["phases"]
    with journal.lifecycle_lock(), journal.mutation_lock(tmp_path / "locks"):
        for phase in phases[:3]:
            journal.begin_phase(phase["id"])
            receipt = tmp_path / (phase["id"] + ".json")
            receipt.write_text('{"controlled_journal_test":true}')
            journal.finish_phase(
                phase["id"], "completed", "controlled-journal-test", receipt_artifacts=(receipt,)
            )
        phase = phases[3]
        journal.begin_phase(phase["id"])
        with pytest.raises(StateError):
            coordinator._admit(context(phase), "build-promote")
        operation = journal.begin_mutation(phase["id"], "build-promote")
        assert coordinator._admit(context(phase), "build-promote") == operation
        with pytest.raises(StateError):
            coordinator._admit(context(phase), "build")


def recipe():
    from devsecops.factory.state import IDENTITY_KEYS

    pin = {"path": "/selected/input", "sha256": "a" * 64}
    return {
        "schema_version": 1,
        "kind": "owned-operations-recipe",
        "environment_id": "demo",
        "run_scope": {name: "b" * 64 for name in IDENTITY_KEYS},
        "plugin_id": "test",
        "plugin_version": "0.2.0a8",
        "writer": deepcopy(pin),
        "runtime": deepcopy(pin),
        "ci": {
            "required_jobs": ["publish_and_prepare_mission_git"],
            "publication_job": "publish_and_prepare_mission_git",
            "delivery_required_jobs": ["validate_owned_deployment"],
            "poll_seconds": 5,
            "max_observations": 120,
        },
        "native": {
            "repository": "/selected/repository",
            "profile": deepcopy(pin),
            "correction_contract": deepcopy(pin),
            "tools": {"helm": deepcopy(pin), "kubeconform": deepcopy(pin)},
            "schemas": deepcopy(pin),
        },
    }


def test_recipe_has_no_preselected_live_identity_or_authority():
    value = recipe()
    assert operations_configuration(_canonical(value)) == value
    assert (
        bind_operations_recipe(
            _canonical(value),
            identities=value["run_scope"],
            environment_id="demo",
            plugin_id="test",
            plugin_version="0.2.0a8",
        )
        == value
    )
    value["project_id"] = 91
    with pytest.raises(ValueError):
        operations_configuration(_canonical(value))


@pytest.mark.parametrize(
    "change", [{"run_scope": {}}, {"environment_id": "other"}, {"plugin_version": "other"}]
)
def test_recipe_cannot_rebind_current_run(change):
    value = recipe()
    other = {**value, **change}
    with pytest.raises(ValueError):
        bind_operations_recipe(
            _canonical(other),
            identities=value["run_scope"],
            environment_id="demo",
            plugin_id="test",
            plugin_version="0.2.0a8",
        )
