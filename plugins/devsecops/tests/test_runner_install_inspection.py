"""Preparation can inspect the original plan without admitting guest effects."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from devsecops.factory import runner_install as module
from devsecops.factory.state import StateError
from tests.test_runner_install import fixture


def test_prepare_inspection_cannot_grant_installation(tmp_path, monkeypatch):
    stack, context, _, _ = fixture(tmp_path, monkeypatch)
    context.phase = {"id": "prepare", "operation": "prepare"}
    plan = module.prepare_runner_install(stack, context)
    reads, installations = [], []
    monkeypatch.setattr(stack.driver, "_admit", lambda ctx, deadline: reads.append((ctx, deadline)))

    def admitted(ctx, action):
        installations.append(action)
        if ctx.phase["operation"] != action:
            raise StateError("Installation requires original up operation")

    monkeypatch.setattr(stack, "_admit", admitted)
    selected = module.inspect_runner_install(plan, stack, context)
    assert reads == [(context, context.deadline)] and installations == []
    with pytest.raises(StateError, match="original up"):
        module.require_runner_install(plan, stack, context)
    assert installations == ["up"]
    with pytest.raises(StateError, match="Original same-process"):
        module.require_runner_install(selected, stack, context)
    selected["environment"].clear()
    assert module.inspect_runner_install(plan, stack, context)["environment"]
    context.phase = {"id": "provision", "operation": "up"}
    assert module.require_runner_install(plan, stack, context)["config_sha256"]


@pytest.mark.parametrize("change", ["material", "template", "secret", "recipe"])
def test_preparation_rechecks_original_inputs(tmp_path, monkeypatch, change):
    stack, context, material, templates = fixture(tmp_path, monkeypatch)
    plan = module.prepare_runner_install(stack, context)
    if change == "material":
        material.write_bytes(b"changed")
    elif change == "template":
        Path(templates["build"]["path"]).write_text("{}")
    elif change == "recipe":
        stack.recipe_path.write_text("{}")
    else:
        monkeypatch.setenv("DEVSECOPS_SELECTED_SECRET", "changed")
    with pytest.raises(StateError, match="changed"):
        module.inspect_runner_install(plan, stack, context)


@pytest.mark.parametrize("change", ["pid", "run", "stack", "copy"])
def test_preparation_rejects_nonoriginal_plan_before_driver_admission(tmp_path, monkeypatch, change):
    stack, context, _, _ = fixture(tmp_path, monkeypatch)
    plan = module.prepare_runner_install(stack, context)
    if change == "pid":
        module._PLANS[plan]["pid"] += 1
    elif change == "run":
        context = SimpleNamespace(**{**vars(context), "run_id": "foreign"})
    elif change == "stack":
        stack = SimpleNamespace(driver=stack.driver)
    else:
        plan = deepcopy(module.inspect_runner_install(plan, stack, context))
    monkeypatch.setattr(stack.driver, "_admit", lambda *args: pytest.fail("Foreign admission"))
    with pytest.raises(StateError, match="Original same-process"):
        module.inspect_runner_install(plan, stack, context)


def test_preparation_requires_current_driver_admission(tmp_path, monkeypatch):
    stack, context, _, _ = fixture(tmp_path, monkeypatch)
    plan = module.prepare_runner_install(stack, context)

    def expired(*args):
        raise StateError("Current operation expired")

    monkeypatch.setattr(stack.driver, "_admit", expired)
    with pytest.raises(StateError, match="expired"):
        module.inspect_runner_install(plan, stack, context)
