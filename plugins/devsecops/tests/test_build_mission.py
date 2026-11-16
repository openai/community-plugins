import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from devsecops.factory import build as b


def git(repo, *args):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(repo),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Synthetic Test",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Synthetic Test",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    return (
        subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=repo,
            env=env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        .stdout.decode()
        .strip()
    )


@pytest.fixture
def inputs(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-q")
    source = repo / "mission"
    source.mkdir()
    for name in b.FILES:
        shutil.copyfile(b.MISSION / name, source / name)
    git(repo, "add", "mission")
    git(repo, "commit", "-qm", "synthetic exact mission source")
    revision = git(repo, "rev-parse", "HEAD")
    wheel = Path(
        os.environ.get(
            "DEVSECOPS_OFFLINE_WHEEL",
            str(Path.home() / ".cache/devsecops/wheelhouse/prometheus_client-0.26.0-py3-none-any.whl"),
        )
    )
    return dict(
        repo=repo,
        revision=revision,
        source_path="mission",
        wheel=wheel,
        output=tmp_path / "prepared",
        base_repository="registry1.dso.mil/ironbank/example/python",
        base_digest="sha256:" + "a" * 64,
        architecture="amd64",
    )


def test_exact_git_source_and_real_locked_wheel_without_checkout_side_effects(inputs):
    if not inputs["wheel"].is_file():
        pytest.skip("Stage the exact offline wheel before real preparation validation")
    repo = inputs["repo"]
    index = (repo / ".git/index").read_bytes()
    (repo / "mission/app.py").write_text("UNCOMMITTED_CANDIDATE_CANARY")
    marker = repo / "hook-ran"
    hook = repo / ".git/hooks/post-checkout"
    hook.write_text("#!/bin/sh\ntouch " + str(marker))
    hook.chmod(0o700)
    receipt = b.prepare_mission_build(**inputs)
    assert receipt["decision"] == "unknown" and set(receipt["operations"].values()) == {"not-started"}
    assert receipt["source"]["commit"] == inputs["revision"]
    assert "UNCOMMITTED_CANDIDATE_CANARY" not in (inputs["output"] / "context/app.py").read_text()
    assert (repo / ".git/index").read_bytes() == index and not marker.exists()
    for item in receipt["context"]["files"]:
        raw = (inputs["output"] / "context" / item["path"]).read_bytes()
        assert item["sha256"] == hashlib.sha256(raw).hexdigest() and item["bytes"] == len(raw)
    assert (inputs["output"] / "prepared.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("file", ["Dockerfile", "requirements.txt", "dependency-lock.json"])
def test_candidate_build_recipe_cannot_override_installed_recipe(inputs, file):
    (inputs["repo"] / "mission" / file).write_text("candidate changed recipe")
    git(inputs["repo"], "add", "mission")
    git(inputs["repo"], "commit", "-qm", "tampered candidate")
    inputs["revision"] = git(inputs["repo"], "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="differs from the installed recipe"):
        b.prepare_mission_build(**inputs)
    assert not inputs["output"].exists()


@pytest.mark.parametrize("revision", ["HEAD", "main", "latest", "--help", "a" * 39])
def test_mutable_or_invalid_revision_rejected(inputs, revision):
    inputs["revision"] = revision
    with pytest.raises(ValueError, match="immutable Git commit"):
        b.prepare_mission_build(**inputs)


@pytest.mark.parametrize(
    "repository,digest,architecture",
    [
        ("python", "sha256:" + "a" * 64, "amd64"),
        ("docker.io/library/python", "sha256:" + "a" * 64, "amd64"),
        ("registry1.dso.mil/ironbank/a/../python", "sha256:" + "a" * 64, "amd64"),
        ("registry1.dso.mil/ironbank/python", "latest", "amd64"),
        ("registry1.dso.mil/ironbank/python", "sha256:" + "a" * 64, "386"),
    ],
)
def test_base_substitution_or_unresolved_subject_rejected(inputs, repository, digest, architecture):
    inputs.update(base_repository=repository, base_digest=digest, architecture=architecture)
    with pytest.raises(ValueError, match="exact Iron Bank"):
        b.prepare_mission_build(**inputs)


def test_wrong_wheel_rejected(inputs, tmp_path):
    wheel = tmp_path / "wrong.whl"
    wheel.write_text("not the approved bytes")
    inputs["wheel"] = wheel
    with pytest.raises(ValueError, match="Offline wheel"):
        b.prepare_mission_build(**inputs)


def test_git_subdirectory_is_not_the_requested_repository(inputs):
    inputs["repo"] /= "mission"
    with pytest.raises(ValueError, match="exact Git worktree root"):
        b.prepare_mission_build(**inputs)


def test_candidate_symlink_not_materialized(inputs):
    app = inputs["repo"] / "mission/app.py"
    app.unlink()
    app.symlink_to("/etc/passwd")
    git(inputs["repo"], "add", "mission")
    git(inputs["repo"], "commit", "-qm", "symlink fixture")
    inputs["revision"] = git(inputs["repo"], "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="symlink or submodule"):
        b.prepare_mission_build(**inputs)


def test_build_gate_is_closed_and_starts_no_mutation(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("build_mission_script", b.ROOT / "scripts/build_mission.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("No subprocess is allowed at the unfinished build gate"),
    )
    assert module.main(["build-gate", "--output", str(tmp_path / "gate")]) == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["decision"] == "unknown"
    assert not any(receipt[k] for k in ("build_started", "push_started", "promotion_started"))
    assert any(g["code"] == "explicit-qualify-and-build-configuration-required" for g in receipt["gaps"])


def test_build_gate_withdraws_terminal_marker_on_directory_swap(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("build_mission_gate", b.ROOT / "scripts/build_mission.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output, moved = tmp_path / "gate", tmp_path / "moved"
    write = module._write

    def swap(*args):
        write(*args)
        output.rename(moved)
        output.mkdir()

    monkeypatch.setattr(module, "_write", swap)
    assert module.main(["build-gate", "--output", str(output)]) == 2
    assert not (moved / "build-gate.json").exists() and not capsys.readouterr().out


def test_preparation_withdraws_terminal_marker_on_directory_swap(inputs, monkeypatch):
    if not inputs["wheel"].is_file():
        pytest.skip("Stage the exact offline wheel before real preparation validation")
    write = b._write
    moved = inputs["output"].with_name("moved")

    def swap(directory, name, raw):
        write(directory, name, raw)
        if name == "prepared.json":
            inputs["output"].rename(moved)
            inputs["output"].mkdir()

    monkeypatch.setattr(b, "_write", swap)
    with pytest.raises(ValueError, match="identity changed"):
        b.prepare_mission_build(**inputs)
    assert not (moved / "prepared.json").exists()


@pytest.mark.parametrize(
    "command", ["--help", "prepare", "scan", "inspect-builder", "inspect-worker", "build", "build-gate"]
)
def test_cli_help_from_outside_checkout(command, tmp_path):
    argv = [sys.executable, str(b.ROOT / "scripts/build_mission.py"), command]
    if command != "--help":
        argv.append("--help")
    result = subprocess.run(argv, cwd=tmp_path, capture_output=True)
    assert result.returncode == 0 and b"usage:" in result.stdout


def test_non_linux_has_no_builder_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(b.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(b.socket, "socket", lambda *a, **k: pytest.fail("No fallback socket"))
    with pytest.raises(ValueError, match="Linux peer-credential"):
        b.inspect_rootless_builder(
            socket_path=tmp_path / "socket", expected_uid=1000, expected_device=1, expected_inode=1
        )
