"""Direct private runner staging with controlled parsers; no VM or CI actions."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from devsecops.factory import bootstrap_recipe, runner_install, runner_vm_recipe
from devsecops.factory.state import StateError, _sha
from tests.test_canonical_bootstrap import selected
from tests.test_runner_install import fixture
from tests.test_runner_vm_recipe import selection


def vm_fixture(tmp_path, monkeypatch):
    stack, context, material, configurations = fixture(tmp_path, monkeypatch)
    vm, foundation = selection(tmp_path)
    external = tmp_path / "external-public-ca.pem"
    external.write_bytes(b"controlled-public-trust")
    vm["egress"] = {
        "external_origins": ["https://registry1.dso.mil"],
        "external_ca_bundle": {"path": str(external), "sha256": _sha(external.read_bytes())},
    }
    vm_path = tmp_path / "runner-vm.json"
    vm_path.write_bytes(runner_vm_recipe.canonical(vm))
    recipe = json.loads(stack.recipe_path.read_bytes())
    recipe.update(schema_version=2, kind="owned-bootstrap-v2")
    recipe["services"].pop("runner_image")
    recipe["services"]["runner_vm"] = {"path": str(vm_path), "sha256": _sha(vm_path.read_bytes())}
    stack.recipe_path.write_bytes(bootstrap_recipe.canonical(recipe))
    stack.recipe_sha256 = _sha(stack.recipe_path.read_bytes())
    context.inputs = {"config": {"runtime": {"foundation": foundation}}}
    build = Path(configurations["build"]["path"])
    value = json.loads(build.read_bytes())
    value["registry_auth"] = {"ca_bundle": str(external), "token_env": "APPROVED_REGISTRY_REFERENCE"}
    build.write_bytes(bootstrap_recipe.canonical(value))
    invocation = stack.driver.auxiliary_sources["mission_git_invocation"][0]
    value = json.loads(invocation.read_bytes())
    value["configurations"]["build"]["sha256"] = _sha(build.read_bytes())
    value["plugin_version"] = "0.2.0-alpha.9"
    invocation.write_bytes(bootstrap_recipe.canonical(value))
    stack.driver.auxiliary_sources["mission_git_invocation"] = (invocation, _sha(invocation.read_bytes()))
    stack.driver.plugin_id, stack.driver.plugin_version = value["plugin_id"], value["plugin_version"]
    return stack, context, vm_path, external


def test_v2_derives_only_original_inputs_into_nonroot_private_scope(tmp_path, monkeypatch):
    stack, context, _, _ = vm_fixture(tmp_path, monkeypatch)
    original = stack.driver.auxiliary_sources["mission_git_invocation"][0].read_bytes()
    plan = runner_install.prepare_runner_install(stack, context)
    actual = runner_install.require_runner_install(plan, stack, context)
    paths = runner_vm_recipe.guest_paths(context.run_id)
    assert actual["runner_profile"] == runner_vm_recipe.KIND
    assert actual["guest_paths"] == paths
    assert all(path.startswith(paths["inputs"] + "/") for path in actual["entries"])
    environment = actual["environment"]
    assert environment["DEVSECOPS_GIT_INVOCATION"].startswith(paths["inputs"] + "/")
    invocation = json.loads(actual["entries"][environment["DEVSECOPS_GIT_INVOCATION"]]["content"])
    assert invocation["output"] == paths["output"] and invocation["runtime_root"] == paths["runtime"]
    assert environment["DEVSECOPS_GIT_INVOCATION_SHA256"] == _sha(
        actual["entries"][environment["DEVSECOPS_GIT_INVOCATION"]]["content"]
    )
    build = json.loads(actual["entries"][paths["inputs"] + "/build.json"]["content"])
    assert build["owner_uid"] == 10001
    assert build["registry_auth"]["ca_bundle"] == paths["inputs"] + "/service-trust/external-ca.pem"
    assert build["registry_auth"]["token_env"] == "APPROVED_REGISTRY_REFERENCE"
    assert actual["guest_package"] == {
        "root": environment["DEVSECOPS_PLUGIN_ROOT"],
        "manifest_sha256": stack.journal.identities["package_sha256"],
        "plugin_id": stack.driver.plugin_id,
        "plugin_version": stack.driver.plugin_version,
    }
    assert stack.driver.auxiliary_sources["mission_git_invocation"][0].read_bytes() == original


@pytest.mark.parametrize("damage", ["vm-recipe", "external-ca", "bootstrap-recipe"])
def test_original_vm_and_trust_bytes_reaudited_before_staging(tmp_path, monkeypatch, damage):
    stack, context, vm_path, external = vm_fixture(tmp_path, monkeypatch)
    plan = runner_install.prepare_runner_install(stack, context)
    {"vm-recipe": vm_path, "external-ca": external, "bootstrap-recipe": stack.recipe_path}[
        damage
    ].write_bytes(b"changed")
    with pytest.raises(StateError, match="changed"):
        runner_install.require_runner_install(plan, stack, context)


@pytest.mark.parametrize("damage", ["package-version", "external-route", "external-ca-mismatch"])
def test_v2_refuses_old_package_or_implicit_external_trust(tmp_path, monkeypatch, damage):
    stack, context, vm_path, external = vm_fixture(tmp_path, monkeypatch)
    if damage == "package-version":
        stack.driver.plugin_version = "future-or-image-era-version"
    elif damage == "external-route":
        vm = json.loads(vm_path.read_bytes())
        vm["egress"] = {"external_origins": [], "external_ca_bundle": None}
        vm_path.write_bytes(runner_vm_recipe.canonical(vm))
        recipe = json.loads(stack.recipe_path.read_bytes())
        recipe["services"]["runner_vm"]["sha256"] = _sha(vm_path.read_bytes())
        stack.recipe_path.write_bytes(bootstrap_recipe.canonical(recipe))
        stack.recipe_sha256 = _sha(stack.recipe_path.read_bytes())
    else:
        invocation = stack.driver.auxiliary_sources["mission_git_invocation"][0]
        value = json.loads(invocation.read_bytes())
        build = Path(value["configurations"]["build"]["path"])
        config = json.loads(build.read_bytes())
        config["registry_auth"].pop("ca_bundle")
        build.write_bytes(bootstrap_recipe.canonical(config))
        value["configurations"]["build"]["sha256"] = _sha(build.read_bytes())
        invocation.write_bytes(bootstrap_recipe.canonical(value))
        stack.driver.auxiliary_sources["mission_git_invocation"] = (invocation, _sha(invocation.read_bytes()))
    with pytest.raises(StateError, match="package identity|explicit Registry1"):
        runner_install.prepare_runner_install(stack, context)


def test_outer_version_selects_vm_explicitly_without_container_fallback(tmp_path):
    original = selected(tmp_path)
    value = deepcopy(original)
    value.update(schema_version=2, kind="owned-bootstrap-v2")
    value["services"].pop("runner_image")
    value["services"]["runner_vm"] = {"path": str(tmp_path / "vm.json"), "sha256": "a" * 64}
    assert bootstrap_recipe.validate(value) == value
    assert bootstrap_recipe.validate(original) == original
    value["services"]["runner_image"] = original["services"]["runner_image"]
    with pytest.raises(ValueError, match="closed"):
        bootstrap_recipe.validate(value)
