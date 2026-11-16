"""Runner compiler guards with controlled component parsers; no CI execution."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from devsecops.factory import runner_install as r, bootstrap_recipe as recipe
from devsecops.factory.owned_services import OwnedServiceStack
from devsecops.factory.state import IDENTITY_KEYS, StateError, _sha
from devsecops.factory.mission_git_invocation import NAMES
from tests.test_canonical_bootstrap import selected


def fixture(tmp_path, monkeypatch):
    value = selected(tmp_path)
    from tests.test_owned_registry import recipe as registry_recipe

    registry = registry_recipe(tmp_path, value["environment"])
    registry_path = Path(value["registry"]["path"])
    registry_path.write_bytes(recipe.canonical(registry))
    value["registry"]["sha256"] = _sha(registry_path.read_bytes())
    scope = {key: "a" * 64 for key in IDENTITY_KEYS}
    material = tmp_path / "private-material"
    material.write_bytes(b"private-selected-material")
    material.chmod(0o600)
    value["services"]["runner_materials"] = [
        {
            "target": "/opt/devsecops/runner-inputs/material.pem",
            "file": {"path": str(material), "sha256": _sha(material.read_bytes())},
            "private": True,
        }
    ]
    value["services"]["runner_secret_environment"] = ["DEVSECOPS_SELECTED_SECRET"]
    monkeypatch.setenv("DEVSECOPS_SELECTED_SECRET", "selected-private-value")
    configurations = {}
    for name in NAMES:
        path = tmp_path / (name + ".json")
        path.write_bytes(
            recipe.canonical(
                {
                    "kind": name,
                    "path": str(material),
                    "origin": "https://unchanged.example.test",
                    "owner_uid": 501,
                    "run_scope": scope,
                }
            )
        )
        configurations[name] = {"path": str(path), "sha256": _sha(path.read_bytes())}
    invocation = {
        "schema_version": 1,
        "kind": "mission-git-preparation-invocation",
        "configurations": configurations,
        "run_scope": scope,
        "plugin_id": "devsecops@devsecops-local",
        "plugin_version": "0.2.0-alpha.8",
        "source": {"repository": str(tmp_path / "source"), "revision": "b" * 40},
        "output": str(tmp_path / "host-output"),
        "runtime_root": str(tmp_path / "host-runtime"),
        "timeout": 3000,
    }
    inv = tmp_path / "invocation.json"
    inv.write_bytes(recipe.canonical(invocation))
    value["services"]["runner_environment"]["DEVSECOPS_GIT_INVOCATION_SHA256"] = _sha(inv.read_bytes())
    source = tmp_path / "bootstrap.json"
    source.write_bytes(recipe.canonical(value))
    journal = SimpleNamespace(identities=scope)
    driver = SimpleNamespace(
        journal=journal,
        auxiliary_sources={"mission_git_invocation": (inv, _sha(inv.read_bytes()))},
        _admit=lambda *a: None,
    )
    stack = object.__new__(OwnedServiceStack)
    stack.driver = driver
    stack.journal = journal
    stack.recipe_path = source
    stack.recipe_sha256 = _sha(source.read_bytes())
    monkeypatch.setattr(stack, "_admit", lambda *a: None)
    from devsecops.factory import (
        mission_operation,
        mission_staging,
        mission_security_operation,
        mission_signing,
        mission_publication,
        deployment_proposal,
    )

    for module, name in (
        (mission_operation, "_configuration"),
        (mission_staging, "_configuration"),
        (mission_security_operation, "_configuration"),
        (mission_signing, "signing_configuration"),
        (mission_publication, "publication_configuration"),
        (deployment_proposal, "proposal_configuration"),
    ):
        monkeypatch.setattr(module, name, json.loads)
    ctx = SimpleNamespace(
        run_id="12345678-1234-1234-1234-123456789012", deadline=__import__("time").monotonic() + 30
    )
    return stack, ctx, material, configurations


def test_exact_six_template_derivation_preserves_origins_and_records_both_hash_sets(tmp_path, monkeypatch):
    stack, ctx, material, templates = fixture(tmp_path, monkeypatch)
    plan = r.prepare_runner_install(stack, ctx)
    data = r.require_runner_install(plan, stack, ctx)
    assert set(data["config_sha256"]) == {"build", "staging", "security", "signing", "publication", "proposal"}
    for name in data["config_sha256"]:
        target = "/opt/devsecops/runner-inputs/" + name + ".json"
        raw = data["entries"][target]["content"]
        doc = json.loads(raw)
        assert (
            doc["path"] == "/opt/devsecops/runner-inputs/material.pem"
            and doc["origin"] == "https://unchanged.example.test"
        )
        assert (
            data["config_sha256"][name] == _sha(raw)
            and data["original_template_sha256"][name] == templates[name]["sha256"]
        )
    assert (
        json.loads(data["entries"]["/opt/devsecops/runner-inputs/build.json"]["content"])["owner_uid"]
        == 10001
    )
    assert data["secrets"] == {"DEVSECOPS_SELECTED_SECRET": "selected-private-value"}
    assert r.require_runner_install(plan, stack, ctx)["entries"]["/opt/devsecops/runner-inputs/material.pem"][
        "private"
    ]
    with pytest.raises(TypeError):
        deepcopy(plan)


@pytest.mark.parametrize("mutation", ["material", "template", "secret"])
def test_original_inputs_are_reaudited_before_staging(tmp_path, monkeypatch, mutation):
    stack, ctx, material, templates = fixture(tmp_path, monkeypatch)
    plan = r.prepare_runner_install(stack, ctx)
    if mutation == "material":
        material.write_bytes(b"replaced")
    elif mutation == "template":
        Path(templates["signing"]["path"]).write_text("{}")
    else:
        monkeypatch.setenv("DEVSECOPS_SELECTED_SECRET", "replacement")
    with pytest.raises(StateError, match="changed"):
        r.require_runner_install(plan, stack, ctx)


def test_copied_descriptor_is_not_runner_install_authority(tmp_path, monkeypatch):
    stack, ctx, material, templates = fixture(tmp_path, monkeypatch)
    plan = r.prepare_runner_install(stack, ctx)
    with pytest.raises(StateError, match="Original same-process"):
        r.require_runner_install(r.require_runner_install(plan, stack, ctx), stack, ctx)


def test_runner_validation_derivation_uses_selected_linux_tools_without_changing_host_template(tmp_path, monkeypatch):
    stack, ctx, material, templates = fixture(tmp_path, monkeypatch)
    original = Path(templates["proposal"]["path"]).read_bytes()
    plan = r.prepare_runner_install(stack, ctx)
    data = r.require_runner_install(plan, stack, ctx)
    proposal = json.loads(data["entries"]["/opt/devsecops/runner-inputs/proposal.json"]["content"])
    assert proposal["helm_path"] == "/opt/devsecops/tools/helm"
    assert proposal["helm_sha256"] == "3" * 64
    assert proposal["kubeconform_sha256"] == "4" * 64
    assert proposal["schemas_path"] == "/opt/devsecops/schemas"
    assert Path(templates["proposal"]["path"]).read_bytes() == original
    assert data["original_template_sha256"]["proposal"] == _sha(original)
    assert data["config_sha256"]["proposal"] != _sha(original)


@pytest.mark.parametrize("run_id", [
    "12345678-1234-1234-1234-123456789012",
    "12345678-1234-1234-1234-123456789013",
])
def test_compiled_runtime_reaches_real_build_admission(tmp_path, monkeypatch, run_id):
    """Actual compiler + invocation + build validator; stop before Linux/acquisition."""
    import uuid
    from devsecops.factory import mission_operation
    from devsecops.factory.mission_git_invocation import invocation_configuration
    from tests.test_mission_operation import configuration

    original_parser = mission_operation._configuration
    stack, ctx, _, _ = fixture(tmp_path, monkeypatch)
    ctx.run_id = run_id
    document, build_args = configuration(tmp_path)
    document["run_scope"] = stack.journal.identities
    build_args["config"].write_bytes(recipe.canonical(document))
    invocation_path = stack.driver.auxiliary_sources["mission_git_invocation"][0]
    invocation = json.loads(invocation_path.read_bytes())
    invocation["configurations"]["build"] = {
        "path": str(build_args["config"]),
        "sha256": _sha(build_args["config"].read_bytes()),
    }
    invocation_path.write_bytes(recipe.canonical(invocation))
    stack.driver.auxiliary_sources["mission_git_invocation"] = (
        invocation_path, _sha(invocation_path.read_bytes())
    )
    monkeypatch.setattr(mission_operation, "_configuration", original_parser)
    plan = r.prepare_runner_install(stack, ctx)
    selected = r.require_runner_install(plan, stack, ctx)
    raw = selected["entries"][selected["environment"]["DEVSECOPS_GIT_INVOCATION"]]["content"]
    compiled = invocation_configuration(raw)
    runtime = Path(compiled["runtime_root"])
    assert runtime.parent == Path("/home/gitlab-runner/builds")
    assert uuid.UUID(hex=runtime.name) == uuid.UUID(run_id)
    assert len(str(runtime).encode()) == 59
    assert compiled["output"] == "/home/gitlab-runner/builds/factory-" + run_id + "/publication"
    staged = tmp_path / "staged-build.json"
    staged.write_bytes(selected["entries"]["/opt/devsecops/runner-inputs/build.json"]["content"])

    class ReachedPrerequisites(Exception):
        pass

    def stop_before_platform(uid):
        assert uid == 10001
        raise ReachedPrerequisites

    monkeypatch.setattr(mission_operation, "prerequisites", stop_before_platform)
    monkeypatch.setattr(mission_operation, "qualify_mission_base", lambda **_: pytest.fail("No acquisition allowed"))
    with pytest.raises(ReachedPrerequisites):
        mission_operation.qualify_and_build(
            config=staged, expected_config_sha256=_sha(staged.read_bytes()),
            output=tmp_path / "not-created-output", runtime_root=runtime,
        )
    assert not (tmp_path / "not-created-output").exists()
    # The old producer value is rejected before even the controlled platform gate.
    with pytest.raises(ValueError, match="new outputs and bounded runtime"):
        mission_operation.qualify_and_build(
            config=staged, expected_config_sha256=_sha(staged.read_bytes()),
            output=tmp_path / "not-created-output",
            runtime_root=Path("/home/gitlab-runner/builds/factory-" + run_id + "/runtime"),
        )
