"""Native local Git objects with a controlled authenticated API boundary."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from devsecops.factory.owned_checkout import (
    collect_objects,
    object_id,
    materialize_owned_repository,
    materialize_delivered_repository,
    require_owned_checkout,
)
from devsecops.factory.state import StateError


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "Owned Factory",
        "GIT_AUTHOR_EMAIL": "factory@example.test",
        "GIT_COMMITTER_NAME": "Owned Factory",
        "GIT_COMMITTER_EMAIL": "factory@example.test",
        "GIT_AUTHOR_DATE": "2026-10-30T12:00:00+00:00",
        "GIT_COMMITTER_DATE": "2026-10-30T12:00:00+00:00",
    }

    def git(*args):
        return subprocess.run(
            ["/usr/bin/git", *args], cwd=repo, env=env, check=True, capture_output=True
        ).stdout

    git("init", "--initial-branch=main")
    (repo / "README.md").write_text("Initial owned seed\n")
    git("add", ".")
    git("commit", "-m", "Initialize owned project")
    (repo / "app").mkdir()
    (repo / "app/main.py").write_text('print("controlled")\n')
    git("add", ".")
    git("commit", "-m", "Seed owned source")
    revision = git("rev-parse", "HEAD").decode().strip()

    class Model:
        def __init__(self):
            self.changes = {}
            self.calls = []
            self.receipts = []

        def request(self, method, path):
            assert method == "GET"
            self.calls.append(path)
            self.receipts.append({"method": method, "path": path, "status": 200, "body_sha256": "a" * 64})
            sha = path.rsplit("/", 1)[-1]
            raw = git("cat-file", "commit", sha).decode()
            headers, message = raw.split("\n\n", 1)
            row = {"id": sha, "parent_ids": [], "message": message}
            for line in headers.splitlines():
                kind, _, value = line.partition(" ")
                if kind == "parent":
                    row["parent_ids"].append(value)
                if kind in ("author", "committer"):
                    identity, stamp, zone = value.rsplit(" ", 2)
                    name, email = identity.rsplit(" <", 1)
                    row[kind + "_name"] = name
                    row[kind + "_email"] = email[:-1]
                    assert zone == "+0000"
                    row["authored_date" if kind == "author" else "committed_date"] = datetime.fromtimestamp(
                        int(stamp), timezone.utc
                    ).isoformat()
            row.update(self.changes)
            return row

        def get_collection(self, path, **kwargs):
            assert kwargs == {"max_pages": 8, "max_items": 800}
            sha = path.split("ref=")[1].split("&")[0]
            rows = []
            for line in git("ls-tree", "-r", "-t", "-z", sha).split(b"\0"):
                if not line:
                    continue
                metadata, path = line.decode().split("\t")
                mode, kind, oid = metadata.split(" ")
                rows.append(
                    {"id": oid, "name": path.rsplit("/", 1)[-1], "path": path, "mode": mode, "type": kind}
                )
            return rows

        def request_repository_blob(self, project_id, sha):
            assert project_id == 91
            return git("cat-file", "blob", sha)

    return git, revision, Model()


def test_authenticated_api_closure_matches_actual_native_git(repository):
    git, sha, model = repository
    objects = collect_objects(model, 91, sha)
    assert objects[sha] == ("commit", git("cat-file", "commit", sha))
    assert len([kind for kind, _ in objects.values() if kind == "commit"]) == 2
    for oid, (kind, raw) in objects.items():
        assert object_id(kind, raw) == oid
        assert git("cat-file", kind, oid) == raw


@pytest.mark.parametrize(
    "field,value",
    [
        ("message", "substituted\n"),
        ("author_name", "Other"),
        ("authored_date", "2026-10-30T12:00:00.001+00:00"),
        ("committer_email", "bad\nidentity"),
        ("parent_ids", ["1" * 40, "2" * 40]),
    ],
)
def test_metadata_cannot_synthesize_a_different_commit(repository, field, value):
    _, sha, model = repository
    model.changes[field] = value
    with pytest.raises(StateError):
        collect_objects(model, 91, sha)


def test_blob_substitution_is_rejected_before_native_objects(repository):
    _, sha, model = repository
    model.request_repository_blob = lambda *_: b"wrong"
    with pytest.raises(StateError, match="identity"):
        collect_objects(model, 91, sha)


def test_duplicate_tree_entry_rejected(repository):
    _, sha, model = repository
    original = model.get_collection

    def duplicate(*args, **kwargs):
        rows = original(*args, **kwargs)
        return rows + [deepcopy(rows[0])]

    model.get_collection = duplicate
    with pytest.raises(StateError, match="duplicate"):
        collect_objects(model, 91, sha)


@pytest.mark.parametrize("delivered", [False, True])
def test_actual_native_local_materialization_preserves_exact_history(
    repository, tmp_path, monkeypatch, delivered
):
    """Bootstrap itself is a model; native process and resulting repo are real."""
    from dataclasses import asdict
    import hashlib
    from test_owned_ci import SCOPE, project

    git, sha, model = repository
    role = "deployment" if delivered else "source"
    owned_project = project()
    if delivered:
        for key in ("path", "path_with_namespace", "http_url_to_repo"):
            owned_project[key] = owned_project[key].replace("factory-demo-source", "factory-demo-deployment")
    request = model.request

    def augmented(method, path):
        if path == "/api/v4/projects/91":
            return owned_project
        if path.endswith("/repository/branches/main"):
            return {"name": "main", "commit": {"id": sha}}
        return request(method, path)

    model.request = augmented
    journal = object()
    authority = object()
    facts = {
        "scope": asdict(SCOPE),
        "projects": {
            role: {
                "id": 91,
                "seed_commit": git("rev-parse", "HEAD^").decode().strip() if delivered else sha,
                "path_with_namespace": owned_project["path_with_namespace"],
            }
        },
        "origin": SCOPE.origin,
    }
    module = ModuleType("devsecops.factory.canonical_bootstrap")

    class Bootstrap:
        def __init__(self):
            self.journal = journal

        def gitlab(self, context):
            return authority, model

        def require_authority(self, context):
            return authority

    def require(value, selected_journal):
        assert value is authority and selected_journal is journal
        return facts

    module.OwnedBootstrapCoordinator = Bootstrap
    module.require_bootstrap_authority = require
    monkeypatch.setitem(sys.modules, module.__name__, module)
    context = SimpleNamespace(
        run_id="controlled-checkout", identities={"controlled": "b" * 64}, remaining_seconds=lambda: 60
    )
    delivery = object()
    if delivered:
        writer_module = ModuleType("devsecops.factory.owned_git_delivery")
        bound = {
            "delivery_stage": "merged-watched-branch",
            "run_id": context.run_id,
            "watched_revision": sha,
            "deployment_commit": sha,
            "project_id": 91,
            "repository_url": owned_project["http_url_to_repo"],
        }

        def require_delivery(value, *, journal):
            assert value is delivery
            return deepcopy(bound)

        writer_module.require_delivery = require_delivery
        monkeypatch.setitem(sys.modules, writer_module.__name__, writer_module)
    # Avoid the environment's multicall Git routing wrapper; this test exercises
    # the explicitly pinned native local executable, as the component requires.
    tool = (
        Path("/opt/homebrew/bin/git").resolve()
        if Path("/opt/homebrew/bin/git").exists()
        else Path("/usr/bin/git").resolve()
    )
    args = dict(
        bootstrap=Bootstrap(),
        git_path=tool,
        git_sha256=hashlib.sha256(tool.read_bytes()).hexdigest(),
        output=tmp_path / "materialized",
    )
    cap = (
        materialize_delivered_repository(context, delivery=delivery, **args)
        if delivered
        else materialize_owned_repository(context, role=role, **args)
    )
    binding = require_owned_checkout(cap, journal=journal)
    clone = Path(binding["repository"])
    observed = (
        subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=clone, check=True, capture_output=True)
        .stdout.decode()
        .strip()
    )
    assert observed == sha
    assert (clone / "app/main.py").read_bytes() == b'print("controlled")\n'
    from devsecops.factory.owned_checkout import checkout_evidence_artifacts

    artifacts = checkout_evidence_artifacts(cap, journal=journal)
    assert {path.name for path in artifacts} == {"checkout.json", "readback.json"}
    assert json.loads(next(path for path in artifacts if path.name == "readback.json").read_bytes())[
        "transport"
    ]
    # These overlays can change ancestry without changing any retained object.
    for name in (
        "info/grafts",
        "shallow",
        "refs/replace",
        "packed-refs",
        "objects/info/alternates",
        "objects/info/http-alternates",
    ):
        overlay = clone / ".git" / name
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(sha + "\n")
        with pytest.raises(StateError, match="ancestry metadata changed"):
            require_owned_checkout(cap, journal=journal)
        overlay.unlink()
        assert require_owned_checkout(cap, journal=journal)["revision"] == sha
    # Broken symbolic links must also fail closed.
    overlay = clone / ".git/info/grafts"
    overlay.symlink_to("missing-graft")
    with pytest.raises(StateError, match="ancestry metadata changed"):
        require_owned_checkout(cap, journal=journal)
    overlay.unlink()
    (clone / ".git/HEAD").write_text("ref: refs/heads/other\n")
    with pytest.raises(StateError, match="metadata changed"):
        require_owned_checkout(cap, journal=journal)
