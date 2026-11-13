"""Owned API recipe and CLI boundaries; upstream publication is controlled."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import time

import pytest
import yaml

from devsecops.factory.mission_publication import PublishedMission, _PUBLISHED
from devsecops.factory.state import IDENTITY_KEYS, _sha
from devsecops.runtime.context import canonical_bytes, seal

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "owned_ci_recipe_cli", ROOT / "scripts/publish_owned_mission.py"
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


@pytest.fixture
def selected(tmp_path, monkeypatch):
    scope = {name: "b" * 64 for name in IDENTITY_KEYS}
    build = {
        "schema_version": 1,
        "kind": "mission-build-configuration",
        "source": {"repository": "/selected/template", "revision": "a" * 40, "path": "mission"},
        "publisher": {
            "policy": "/selected/publisher",
            "policy_sha256": "c" * 64,
            "public_key": "/selected/key",
            "package": None,
        },
        "image": {"policy": "/selected/image", "policy_sha256": "d" * 64},
        "tools": {"cosign": "/selected/cosign", "build_tools": "/selected/tools", "wheel": "/selected/wheel"},
        "owner_uid": 10001,
        "run_scope": scope,
        "registry_auth": {},
    }
    values = {
        "build": build,
        **{name: {"controlled": name} for name in ("staging", "security", "signing", "publication")},
    }
    refs = {}
    for name, value in values.items():
        path = tmp_path / (name + ".json")
        path.write_bytes(canonical_bytes(value))
        refs[name] = {"path": str(path), "sha256": _sha(path.read_bytes())}
    invocation = {
        "run_scope": scope,
        "plugin_id": "selected",
        "plugin_version": "0.2.0a8",
        "timeout": 3500,
        "source": {"repository": "/selected/template", "revision": "a" * 40},
        "configurations": refs,
        "output": str(tmp_path / "publication-output"),
        "runtime_root": str(tmp_path / "runtime"),
    }
    monkeypatch.setattr(cli, "load_invocation", lambda *_: deepcopy(invocation))
    checks = []
    monkeypatch.setattr(cli, "_installed", lambda *args: checks.append(args))
    calls = []
    source = tmp_path / "actual-ci-checkout"
    source.mkdir()
    revision = "e" * 40
    bound = {
        "run_scope": scope,
        "source_commit": revision,
        "architecture": "amd64",
        "origin": "https://registry.example.test",
        "repository": "demo/mission",
        "policy_sha256": "f" * 64,
        "trust_policy_sha256": "0" * 64,
        "publication_config_sha256": refs["publication"]["sha256"],
        "manifest_digest": "sha256:" + "1" * 64,
        "artifact_manifest": {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": "sha256:" + "2" * 64,
            "size": 123,
            "artifactType": "application/vnd.devsecops.mission-evidence.v1",
        },
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }
    cap = object.__new__(PublishedMission)
    _PUBLISHED[cap] = {"pid": os.getpid(), "expires_monotonic": time.monotonic() + 600, "binding": bound}

    def publish(**kwargs):
        calls.append(kwargs)
        actual = json.loads(kwargs["build_config"].read_bytes())
        assert actual == {
            **build,
            "source": {**build["source"], "repository": str(source), "revision": revision},
        }
        assert (
            kwargs["expected_source_repository"] == source and kwargs["expected_source_revision"] == revision
        )
        for name in values:
            assert _sha(kwargs[name + "_config"].read_bytes()) == kwargs[name + "_config_sha256"]
        assert not any(key.startswith(("proposal_", "commit_", "destination_")) for key in kwargs)
        return seal({"decision": "pass"}, "publication_operation_sha256"), cap

    monkeypatch.setattr(cli, "qualify_sign_and_publish", publish)
    args = [
        "--invocation",
        str(tmp_path / "invocation.json"),
        "--invocation-sha256",
        "9" * 64,
        "--expected-source-repository",
        str(source),
        "--expected-source-revision",
        revision,
    ]
    return invocation, refs, checks, calls, args, cap


def test_ci_wrapper_publishes_only_from_actual_job_checkout_and_pins(selected, capsys):
    invocation, refs, checks, calls, args, cap = selected
    assert cli.main(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["kind"] == "owned-ci-publication-selector"
    assert value["source_commit"] == "e" * 40
    assert value["publication_config_sha256"] == refs["publication"]["sha256"]
    assert "path" not in json.dumps(value) and "decision" not in value
    assert len(checks) == 2 and len(calls) == 1
    assert json.loads(Path(refs["build"]["path"]).read_bytes())["source"] == invocation["source"] | {
        "path": "mission"
    }


def test_changed_template_is_rejected_before_any_component_effect(selected, capsys):
    _, refs, _, calls, args, _ = selected
    Path(refs["build"]["path"]).write_text("{}")
    assert cli.main(args) == 2
    assert not calls and not capsys.readouterr().out


def test_unknown_component_never_prints_a_publication_selector(selected, monkeypatch, capsys):
    *_, args, cap = selected
    monkeypatch.setattr(
        cli,
        "qualify_sign_and_publish",
        lambda **_: (seal({"decision": "unknown"}, "publication_operation_sha256"), None),
    )
    assert cli.main(args) == 2 and not capsys.readouterr().out


def test_explicit_api_recipe_does_not_enable_seed_push_or_change_mr_recipe():
    value = yaml.safe_load((ROOT / "demo/software-factory/ci/gitlab-owned-api-v1.yml").read_bytes())
    assert value["workflow"]["rules"] == [{"if": '$CI_PIPELINE_SOURCE == "api"'}, {"when": "never"}]
    job = value["publish_and_prepare_mission_git"]
    assert job["retry"] == 0 and job["tags"] == ["devsecops-owned"]
    assert job["artifacts"]["paths"] == ["mission-ci-publication.json"]
    assert "publish_owned_mission.py" in job["script"][0]
    original = yaml.safe_load((ROOT / "demo/software-factory/ci/gitlab-git-preparation.yml").read_bytes())
    assert original["publish_and_prepare_mission_git"]["rules"] == [
        {"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'}
    ]
