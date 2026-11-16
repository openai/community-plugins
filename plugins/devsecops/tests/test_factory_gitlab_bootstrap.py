"""Controlled GitLab protocol model only; never contacts a service or loads a credential."""
import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from devsecops.factory.gitlab_api import GitLabAPIError, GitLabSession
from devsecops.factory.gitlab_bootstrap import (
    BootstrapError, BootstrapScope, ProjectSeed, SeedFile, bootstrap_owned_projects,
)


def blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


SCOPE = BootstrapScope("https://gitlab.example.test", "demo", "a" * 64, 7, 17, "factory-user")
SEEDS = (
    ProjectSeed("source", (SeedFile("README.md", b"Owned source\n"),
                           SeedFile("app/main.py", b"print('synthetic fixture')\n"),
                           SeedFile(".gitlab-ci.yml", b"test:\n  script: echo controlled\n"))),
    ProjectSeed("deployment", (SeedFile("README.md", b"Owned deployment\n"),
                               SeedFile("deploy/values.yaml", b"replicas: 1\n"))),
)


class ControlledSession(GitLabSession):
    """Inert stateful model of the API. Tests can inject responses at one boundary."""

    def __init__(self):
        self.calls, self.projects, self.repositories, self.heads = [], {}, {}, {}
        self.next_id = 100
        self.version, self.enterprise = "19.5.3", False
        self.user_id, self.user_name, self.user_state = 7, "factory-user", "active"
        self.namespace = {"id": 17, "kind": "user", "path": "factory-user",
                          "full_path": "factory-user", "parent_id": None}
        self.hook = lambda method, path, payload, response: response
        self.before = lambda method, path, payload: None
        self.stopped = False

    @property
    def origin(self):
        return SCOPE.origin

    @property
    def expected_user_id(self):
        return 7

    def metadata(self):
        self.calls.append(("metadata", "", None))
        return {"version": self.version, "enterprise": self.enterprise}

    def principal(self):
        self.calls.append(("principal", "", None))
        return {"id": self.user_id, "username": self.user_name, "state": self.user_state}

    def stop_writes(self):
        self.stopped = True

    def get_collection(self, path, *, max_pages=8, max_items=800):
        assert max_pages == 1 and max_items == 64
        return self.request("GET", path)

    def request(self, method, path, payload=None, expected=(200,)):
        self.calls.append((method, path, deepcopy(payload)))
        if method != "GET":
            assert not self.stopped
        self.before(method, path, payload)
        route = urlsplit(path)
        parts = route.path.split("/")
        query = parse_qs(route.query)
        response = None
        if path == "/api/v4/namespaces/17":
            response = deepcopy(self.namespace)
        elif path == "/api/v4/projects" and method == "POST":
            project_id = self.next_id
            self.next_id += 1
            response = {**payload, "id": project_id, "path_with_namespace": "factory-user/" + payload["path"],
                        "owner": {"id": 7}, "namespace": deepcopy(self.namespace), "archived": False,
                        "http_url_to_repo": SCOPE.origin + "/factory-user/" + payload["path"] + ".git"}
            self.projects[project_id] = deepcopy(response)
            initial = f"{project_id:040x}"
            self.heads[project_id] = initial
            self.repositories[project_id] = {initial: {"README.md": b"Initial README\n"}}
        elif len(parts) == 5 and parts[3] == "projects":
            target = unquote(parts[4])
            response = deepcopy(next((value for key, value in self.projects.items()
                                      if target == str(key) or target == value["path_with_namespace"]), None))
            if response is None:
                raise GitLabAPIError("http-status", status=404)
        elif len(parts) >= 7 and parts[3] == "projects" and parts[5] == "repository":
            project_id = int(parts[4])
            if parts[6] == "branches":
                response = {"name": "main", "commit": {"id": self.heads[project_id]}}
            elif parts[6] == "commits" and method == "POST":
                parent = self.heads[project_id]
                files = deepcopy(self.repositories[project_id][parent])
                for action in payload["actions"]:
                    if action["action"] == "update":
                        assert action["file_path"] == "README.md"
                        assert action["last_commit_id"] == parent
                    else:
                        assert action["action"] == "create"
                        assert action["file_path"] not in files
                    files[action["file_path"]] = base64.b64decode(action["content"])
                if files == self.repositories[project_id][parent] and not payload.get("allow_empty", False):
                    raise GitLabAPIError("empty-commit", status=400, outcome="unknown")
                commit = f"{project_id + 1000:040x}"
                self.repositories[project_id][commit] = files
                self.heads[project_id] = commit
                response = {"id": commit, "parent_ids": [parent]}
            elif parts[6] == "tree":
                commit = query["ref"][0]
                files = self.repositories[project_id][commit]
                directories = {"/".join(path.split("/")[:n]) for path in files
                               for n in range(1, len(path.split("/")))}
                response = [{"path": path, "type": "tree", "mode": "040000", "id": "d" * 40}
                            for path in sorted(directories)]
                response += [{"path": path, "type": "blob", "mode": "100644", "id": blob(raw)}
                             for path, raw in sorted(files.items())]
            elif parts[6] == "files":
                file_path, commit = unquote(parts[7]), query["ref"][0]
                raw = self.repositories[project_id][commit][file_path]
                response = {"file_path": file_path, "ref": commit, "commit_id": commit,
                            "last_commit_id": commit, "blob_id": blob(raw), "encoding": "base64",
                            "execute_filemode": False, "size": len(raw),
                            "content": base64.b64encode(raw).decode(),
                            "content_sha256": hashlib.sha256(raw).hexdigest()}
        assert response is not None, (method, path)
        return self.hook(method, path, payload, deepcopy(response))


def records(root):
    return [json.loads(path.read_text()) for path in sorted((root / "gitlab-bootstrap-demo").glob("*.json"))]


def terminal(root):
    return records(root)[-1]


def writes(session):
    return [call for call in session.calls if call[0] in ("POST", "PUT", "DELETE")]


def run(tmp_path, session=None, seeds=SEEDS, scope=SCOPE):
    return bootstrap_owned_projects(session or ControlledSession(), scope, seeds, journal_root=tmp_path)


def test_complete_two_owned_seeded_projects_have_observed_inventory(tmp_path):
    session = ControlledSession()
    report = run(tmp_path, session)
    assert report["state"] == "component-complete"
    assert report["canonical_success"] is report["complete_cleanup"] is report["production_preflight_ready"] is False
    assert [row["project_id"] for row in report["resources"]] == [100, 101]
    assert [row["role"] for row in report["resources"]] == ["source", "deployment"]
    assert all(row["identity_verified"] and row["seed_verified"] for row in report["resources"])
    assert all(row["cleanup"] == "owned-project-deletion-required" and not row["deletion_authorized"]
               for row in report["resources"])
    assert len(writes(session)) == 4
    assert all(method == "POST" for method, _, _ in writes(session))
    assert terminal(tmp_path) == report
    for record in records(tmp_path):
        assert "synthetic fixture" not in json.dumps(record)
    for _, path, payload in writes(session):
        if path == "/api/v4/projects":
            assert payload["shared_runners_enabled"] is payload["group_runners_enabled"] is False
            assert payload["only_allow_merge_if_pipeline_succeeds"] is True
            assert payload["namespace_id"] == SCOPE.namespace_id
        else:
            assert payload["force"] is False
            assert payload["allow_empty"] is True
            readme = next(item for item in payload["actions"] if item["file_path"] == "README.md")
            assert readme["action"] == "update" and len(readme["last_commit_id"]) == 40


def test_unchanged_readme_only_seed_uses_observed_empty_commit(tmp_path):
    session = ControlledSession()
    seeds = tuple(ProjectSeed(role, (SeedFile("README.md", b"Initial README\n"),))
                  for role in ("source", "deployment"))
    report = run(tmp_path, session, seeds=seeds)
    assert report["state"] == "component-complete"
    assert len(writes(session)) == 4
    for resource in report["resources"]:
        assert resource["seed_verified"]
        assert resource["seed_commit"] != resource["base_commit"]
        files = session.repositories[resource["project_id"]]
        assert files[resource["seed_commit"]] == files[resource["base_commit"]]


@pytest.mark.parametrize("token_active", [True, False])
def test_real_session_protocol_composes_with_bootstrap_without_credentials_or_network(tmp_path, monkeypatch,
                                                                                     token_active):
    # Exercise the real identity/request/collection composition while replacing
    # only its HTTP exchange. Construct no TLS client and load no credential.
    session = object.__new__(GitLabSession)
    session._origin, session._expected_user_id = SCOPE.origin, 7
    session._poisoned, session._lock = False, threading.RLock()
    service = ControlledSession()
    identity_calls = []
    def exchange(self, method, path, payload, expected):
        if path in ("/api/v4/metadata", "/api/v4/user", "/api/v4/personal_access_tokens/self"):
            identity_calls.append(path)
            return {
                "/api/v4/metadata": {"version": "19.5.3", "enterprise": False},
                "/api/v4/user": {"id": 7, "username": "factory-user", "state": "active"},
                "/api/v4/personal_access_tokens/self": {"user_id": 7, "active": token_active,
                    "revoked": False, "scopes": ["api"], "expires_at": "2099-01-01"},
            }[path]
        self._pagination = {"x-page": "1", "x-per-page": "100", "x-next-page": ""}
        return service.request(method, path, payload, expected)
    monkeypatch.setattr(GitLabSession, "_exchange", exchange)
    if token_active:
        report = run(tmp_path, session)
        assert report["state"] == "component-complete"
        # Six workflow identity checks plus the transport's four per-write checks.
        assert identity_calls.count("/api/v4/personal_access_tokens/self") == 10
        assert len(writes(service)) == 4
    else:
        with pytest.raises(GitLabAPIError):
            run(tmp_path, session)
        assert not writes(service)


@pytest.mark.parametrize("attribute,value", [
    ("version", "19.5.2"), ("enterprise", True), ("user_id", 8), ("user_id", True),
    ("user_name", "somebody-else"), ("user_state", "blocked"),
])
def test_version_edition_and_principal_mismatch_precede_write(tmp_path, attribute, value):
    session = ControlledSession()
    setattr(session, attribute, value)
    with pytest.raises(BootstrapError):
        run(tmp_path, session)
    assert not writes(session)
    assert terminal(tmp_path)["resources"] == []


@pytest.mark.parametrize("change", [
    {"id": 18}, {"id": True}, {"kind": "group"}, {"path": "foreign"},
    {"full_path": "foreign"}, {"parent_id": 8},
])
def test_namespace_must_be_current_principal_personal_namespace(tmp_path, change):
    session = ControlledSession()
    session.namespace.update(change)
    with pytest.raises(BootstrapError, match="namespace-identity"):
        run(tmp_path, session)
    assert not writes(session)


def test_preexisting_second_project_refused_before_first_create(tmp_path):
    session = ControlledSession()
    session.projects[99] = {"path_with_namespace": "factory-user/factory-demo-deployment"}
    with pytest.raises(BootstrapError, match="preexisting-project"):
        run(tmp_path, session)
    assert not writes(session)


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_project_absence_check_does_not_convert_error_to_missing(tmp_path, status):
    session = ControlledSession()
    def before(method, path, payload):
        if "factory-user%2F" in path:
            raise GitLabAPIError("http-status", status=status)
    session.before = before
    with pytest.raises(GitLabAPIError):
        run(tmp_path, session)
    assert not writes(session)


@pytest.mark.parametrize("change,code", [
    ({"id": True}, "project-id"), ({"owner": {"id": 8}}, "project-owner"),
    ({"namespace": {"id": 18, "kind": "user", "full_path": "factory-user"}}, "project-owner"),
    ({"visibility": "public"}, "project-identity"), ({"description": "foreign"}, "project-identity"),
    ({"path_with_namespace": "foreign/project"}, "project-identity"),
    ({"http_url_to_repo": "https://evil.test/project.git"}, "project-identity"),
    ({"default_branch": "other"}, "project-identity"), ({"archived": True}, "project-identity"),
    ({"shared_runners_enabled": True}, "project-execution-policy"),
    ({"group_runners_enabled": True}, "project-execution-policy"),
    ({"auto_devops_enabled": True}, "project-execution-policy"),
    ({"only_allow_merge_if_pipeline_succeeds": False}, "project-execution-policy"),
    ({"allow_merge_on_skipped_pipeline": True}, "project-execution-policy"),
    ({"merge_method": "merge"}, "project-execution-policy"),
    ({"squash_option": "always"}, "project-execution-policy"),
    ({"only_allow_merge_if_all_discussions_are_resolved": False}, "project-execution-policy"),
    ({"remove_source_branch_after_merge": True}, "project-execution-policy"),
])
def test_bad_create_identity_retains_effect_but_cannot_seed_or_authorize_cleanup(tmp_path, change, code):
    session = ControlledSession()
    session.hook = lambda method, path, payload, response: ({**response, **change}
                    if method == "POST" and path == "/api/v4/projects" else response)
    with pytest.raises(BootstrapError, match=code):
        run(tmp_path, session)
    assert len(writes(session)) == 1
    assert session.stopped
    resource = terminal(tmp_path)["resources"][0]
    assert resource["create_effect"] == "applied"
    assert resource["seed_effect"] == "none"
    assert resource["identity_verified"] is resource["deletion_authorized"] is False


@pytest.mark.parametrize("which", ["create", "seed", "second-create"])
def test_ambiguous_write_stops_with_inventory_and_no_retry(tmp_path, which):
    session = ControlledSession()
    def hook(method, path, payload, response):
        if method == "POST" and ((which == "create" and response.get("id") == 100)
                or (which == "seed" and path.endswith("/commits"))
                or (which == "second-create" and response.get("id") == 101)):
            raise GitLabAPIError("transport", outcome="unknown")
        return response
    session.hook = hook
    with pytest.raises(GitLabAPIError):
        run(tmp_path, session)
    prior_calls = len(session.calls)
    with pytest.raises(FileExistsError):
        run(tmp_path, session)
    assert len(session.calls) == prior_calls
    resource = terminal(tmp_path)["resources"][-1]
    assert resource["seed_effect" if which == "seed" else "create_effect"] == "unknown"
    assert len(writes(session)) == {"create": 1, "seed": 2, "second-create": 3}[which]
    assert session.stopped
    if which == "second-create":
        assert terminal(tmp_path)["resources"][0]["seed_verified"] is True


def test_principal_changes_before_seed_after_owned_create(tmp_path):
    session = ControlledSession()
    def hook(method, path, payload, response):
        if method == "POST":
            session.user_id = 8
        return response
    session.hook = hook
    with pytest.raises(BootstrapError, match="principal-identity"):
        run(tmp_path, session)
    assert len(writes(session)) == 1
    assert terminal(tmp_path)["resources"][0]["identity_verified"] is True


def test_project_readback_owner_change_cannot_seed(tmp_path):
    session = ControlledSession()
    def hook(method, path, payload, response):
        if method == "GET" and path == "/api/v4/projects/100":
            response["owner"] = {"id": 8}
        return response
    session.hook = hook
    with pytest.raises(BootstrapError, match="project-owner"):
        run(tmp_path, session)
    assert len(writes(session)) == 1
    resource = terminal(tmp_path)["resources"][0]
    assert resource["project_id"] == 100 and resource["create_effect"] == "applied"
    assert resource["identity_verified"] is False
    assert resource["cleanup"] == "identity-reconciliation-required"


def test_first_project_drift_during_second_seed_is_detected_at_final_check(tmp_path):
    session = ControlledSession()
    def hook(method, path, payload, response):
        if method == "POST" and path == "/api/v4/projects/101/repository/commits":
            session.heads[100] = "e" * 40
        return response
    session.hook = hook
    with pytest.raises(BootstrapError, match="seed-final-branch-diverged"):
        run(tmp_path, session)
    assert len(writes(session)) == 4
    assert terminal(tmp_path)["state"] == "incomplete"
    assert session.stopped


def test_initial_foreign_tree_stops_before_seed(tmp_path):
    session = ControlledSession()
    def hook(method, path, payload, response):
        if "/tree?" in path:
            response.append({"path": "foreign", "id": "e" * 40, "type": "blob", "mode": "100644"})
        return response
    session.hook = hook
    with pytest.raises(BootstrapError, match="tree-paths"):
        run(tmp_path, session)
    assert len(writes(session)) == 1


def test_concurrent_parent_advance_is_applied_but_incomplete(tmp_path):
    session = ControlledSession()
    session.hook = lambda method, path, payload, response: ({**response, "parent_ids": ["e" * 40]}
                    if path.endswith("/commits") else response)
    with pytest.raises(BootstrapError, match="seed-parent-diverged"):
        run(tmp_path, session)
    resource = terminal(tmp_path)["resources"][0]
    assert resource["create_effect"] == resource["seed_effect"] == "applied"
    assert "seed_commit" in resource and "seed_verified" not in resource
    assert len(writes(session)) == 2
    assert session.stopped


@pytest.mark.parametrize("change,code", [
    ({"mode": "120000"}, "tree-mode"), ({"type": "commit"}, "tree-mode"),
    ({"id": "e" * 40}, "seed-tree-integrity"),
])
def test_seed_tree_integrity_and_modes_fail_closed(tmp_path, change, code):
    session = ControlledSession()
    def hook(method, path, payload, response):
        if "/tree?" in path and len(response) > 1:
            entry = next(entry for entry in response if entry["path"] == "README.md")
            entry.update(change)
        return response
    session.hook = hook
    with pytest.raises(BootstrapError, match=code):
        run(tmp_path, session)
    assert terminal(tmp_path)["resources"][0]["seed_effect"] == "applied"
    assert len(writes(session)) == 2


@pytest.mark.parametrize("change,code", [
    ({"content": "!!!"}, "file-content"), ({"size": True}, "file-identity"),
    ({"execute_filemode": True}, "file-identity"), ({"blob_id": "e" * 40}, "file-integrity"),
    ({"content_sha256": "e" * 64}, "file-integrity"), ({"commit_id": "e" * 40}, "file-identity"),
])
def test_file_readback_checks_observed_bytes(tmp_path, change, code):
    session = ControlledSession()
    def hook(method, path, payload, response):
        if "/files/" in path and "000000000000000000000000000000000000044c" in path:
            response.update(change)
        return response
    session.hook = hook
    with pytest.raises(BootstrapError, match=code):
        run(tmp_path, session)
    assert terminal(tmp_path)["resources"][0]["seed_effect"] == "applied"
    assert len(writes(session)) == 2


@pytest.mark.parametrize("files", [
    (), (SeedFile("not-readme", b"x"),),
    (SeedFile("README.md", b"x"), SeedFile("../outside", b"x")),
    (SeedFile("README.md", b"x"), SeedFile(".git/config", b"x")),
    (SeedFile("README.md", b"x"), SeedFile("README.md", b"y")),
    (SeedFile("README.md", b"x"), SeedFile("sub", b"x"), SeedFile("sub/file", b"x")),
    (SeedFile("README.md", b"\xff"),), (SeedFile("README.md", b"x" * 65537),),
    (SeedFile("README.md", "string"),),
])
def test_bad_seed_is_rejected_before_journal_or_transport(tmp_path, files):
    session = ControlledSession()
    with pytest.raises(BootstrapError):
        run(tmp_path, session, seeds=(ProjectSeed("source", files), SEEDS[1]))
    assert not session.calls
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change", [
    {"environment_id": "../demo"}, {"environment_fingerprint": "invalid"},
    {"namespace_id": True}, {"owner_user_id": 0}, {"namespace_path": "group/subgroup"},
    {"origin": "http://gitlab.example.test"},
])
def test_bad_scope_is_rejected_before_journal_or_transport(tmp_path, change):
    session = ControlledSession()
    with pytest.raises(ValueError):
        run(tmp_path, session, scope=replace(SCOPE, **change))
    assert not session.calls
    assert list(tmp_path.iterdir()) == []


def test_existing_journal_is_not_resumable_authority(tmp_path):
    run(tmp_path)
    session = ControlledSession()
    with pytest.raises(FileExistsError):
        run(tmp_path, session)
    assert not session.calls


def test_journal_symlink_parent_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    session = ControlledSession()
    with pytest.raises(OSError):
        bootstrap_owned_projects(session, SCOPE, SEEDS, journal_root=link)
    assert not session.calls


def test_intent_is_durable_before_mutation_and_never_logs_remote_error(tmp_path):
    session = ControlledSession()
    def before(method, path, payload):
        if method == "POST":
            found = records(tmp_path)
            assert found[-1]["target"] == "/api/v4/projects"
            raise RuntimeError("sensitive remote diagnostic")
    session.before = before
    with pytest.raises(RuntimeError):
        run(tmp_path, session)
    assert "sensitive" not in json.dumps(records(tmp_path))
    assert terminal(tmp_path)["resources"][0]["create_effect"] == "unknown"


def test_failed_intent_fsync_prevents_remote_write(tmp_path, monkeypatch):
    import devsecops.factory.gitlab_bootstrap as module
    original = module.os.fsync
    calls = []
    def fsync(fd):
        calls.append(fd)
        if len(calls) == 4:  # First create-intent file, after root and scope durability.
            raise OSError("controlled disk failure")
        return original(fd)
    monkeypatch.setattr(module.os, "fsync", fsync)
    session = ControlledSession()
    with pytest.raises(OSError):
        run(tmp_path, session)
    assert not writes(session)
    assert terminal(tmp_path)["resources"] == []


@pytest.mark.parametrize("failure_point", ["write", "file-fsync", "directory-fsync"])
def test_terminal_durability_failure_cannot_leave_success_failure_receipt(tmp_path, monkeypatch, failure_point):
    import devsecops.factory.gitlab_bootstrap as module
    original_write, original_fsync = module._Journal.write, module.os.fsync
    terminal_attempts, terminal_fsyncs = [], []
    active_terminal = False
    def write(self, kind, value):
        nonlocal active_terminal
        if kind == "terminal":
            terminal_attempts.append(value["state"])
            active_terminal = len(terminal_attempts) == 1
            if active_terminal and failure_point == "write":
                active_terminal = False
                raise OSError("controlled terminal write failure")
        try:
            return original_write(self, kind, value)
        finally:
            active_terminal = False
    def fsync(fd):
        if active_terminal:
            terminal_fsyncs.append(fd)
            if len(terminal_fsyncs) == {"file-fsync": 1, "directory-fsync": 2}.get(failure_point):
                raise OSError("controlled terminal fsync failure")
        return original_fsync(fd)
    monkeypatch.setattr(module._Journal, "write", write)
    monkeypatch.setattr(module.os, "fsync", fsync)
    session = ControlledSession()
    with pytest.raises(OSError):
        run(tmp_path, session)
    assert terminal_attempts == ["component-complete", "incomplete"]
    assert terminal(tmp_path)["state"] == "incomplete"
    assert terminal(tmp_path)["failure"] == "component-error"
    assert all(row["seed_verified"] for row in terminal(tmp_path)["resources"])
    assert session.stopped and len(writes(session)) == 4


def test_interruption_after_request_preserves_unknown_and_stops_session(tmp_path):
    session = ControlledSession()
    def hook(method, path, payload, response):
        if method == "POST":
            raise KeyboardInterrupt
        return response
    session.hook = hook
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, session)
    assert terminal(tmp_path)["resources"][0]["create_effect"] == "unknown"
    assert session.stopped and len(writes(session)) == 1


def test_pre_dispatch_api_failure_retains_explicit_no_effect(tmp_path):
    session = ControlledSession()
    def before(method, path, payload):
        if method == "POST":
            raise GitLabAPIError("budget-exhausted", outcome="none")
    session.before = before
    with pytest.raises(GitLabAPIError):
        run(tmp_path, session)
    resource = terminal(tmp_path)["resources"][0]
    assert resource["create_effect"] == "none" and resource["cleanup"] == "not-created"


def test_complete_seed_file_hash_cannot_substitute_different_bytes(tmp_path):
    session = ControlledSession()
    def hook(method, path, payload, response):
        if "/files/README.md" in path and "000000000000000000000000000000000000044c" in path:
            raw = b"different but self-consistent data"
            response.update(content=base64.b64encode(raw).decode(), size=len(raw),
                            content_sha256=hashlib.sha256(raw).hexdigest(), blob_id=blob(raw))
        return response
    session.hook = hook
    with pytest.raises(BootstrapError, match="file-content"):
        run(tmp_path, session)
    assert terminal(tmp_path)["resources"][0]["seed_effect"] == "applied"


def test_no_existing_native_dispatcher_references_component():
    root = Path(__file__).resolve().parents[1] / "src/devsecops/factory"
    for name in ("owned_linux.py", "native_composition.py", "lifecycle.py", "cli.py"):
        path = root / name
        if path.exists():
            assert "gitlab_bootstrap" not in path.read_text()
