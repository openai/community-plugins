"""One-shot creation and verified seeding of two owned GitLab projects.

This component is deliberately absent from the native lifecycle dispatcher. Its
receipts are evidence, not resumable write authority or complete service cleanup.
The caller must already hold authority for the explicitly scoped service action.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote

from devsecops.adapters.artifacts import _open_directory
from devsecops.factory.git_destination import _origin
from devsecops.factory.gitlab_api import GitLabAPIError, GitLabSession

GITLAB_VERSION = "19.5.3"
MAX_FILES = 16
MAX_FILE_BYTES = 64 * 1024
MAX_SEED_BYTES = 256 * 1024
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_USER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
_SHA = re.compile(r"[a-f0-9]{64}")
_OID = re.compile(r"[a-f0-9]{40}")


class BootstrapError(ValueError):
    """Fixed-category failure; remote bodies and seed contents are never echoed."""


def _require(condition, code):
    if not condition:
        raise BootstrapError(code)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _positive(value):
    return type(value) is int and 0 < value < 2**63


def _oid(value):
    return isinstance(value, str) and bool(_OID.fullmatch(value))


@dataclass(frozen=True)
class BootstrapScope:
    origin: str
    environment_id: str
    environment_fingerprint: str
    owner_user_id: int
    namespace_id: int
    namespace_path: str

    def validate(self):
        _origin(self.origin)
        _require(isinstance(self.environment_id, str) and len(self.environment_id) <= 32
                 and bool(_SLUG.fullmatch(self.environment_id)), "scope-environment")
        _require(isinstance(self.environment_fingerprint, str)
                 and bool(_SHA.fullmatch(self.environment_fingerprint)), "scope-fingerprint")
        _require(_positive(self.owner_user_id) and _positive(self.namespace_id), "scope-identity")
        _require(isinstance(self.namespace_path, str) and bool(_USER.fullmatch(self.namespace_path))
                 and ".." not in self.namespace_path, "scope-namespace")

    @property
    def marker(self):
        return "factory-owned:" + self.environment_id + ":" + self.environment_fingerprint

    def project_path(self, role):
        return "factory-" + self.environment_id + "-" + role


@dataclass(frozen=True)
class SeedFile:
    path: str
    content: bytes


@dataclass(frozen=True)
class ProjectSeed:
    role: str
    files: tuple[SeedFile, ...]


def _seed_manifest(seeds):
    _require(type(seeds) is tuple and len(seeds) == 2
             and tuple(seed.role for seed in seeds if type(seed) is ProjectSeed) == ("source", "deployment"),
             "seed-projects")
    manifest = []
    for seed in seeds:
        _require(type(seed.files) is tuple and 1 <= len(seed.files) <= MAX_FILES, "seed-files")
        paths, entries, total = set(), [], 0
        for item in seed.files:
            _require(type(item) is SeedFile and isinstance(item.path, str)
                     and len(item.path) <= 240 and 1 <= len(item.path.split("/")) <= 4,
                     "seed-path")
            parts = item.path.split("/")
            _require(all(re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", part)
                         and part.lower() not in (".", "..", ".git") for part in parts)
                     and item.path not in paths, "seed-path")
            _require(type(item.content) is bytes and len(item.content) <= MAX_FILE_BYTES,
                     "seed-content")
            # The GitLab JSON file API may transcode non-UTF8 text. This contract
            # admits UTF8 only and checks both blob OID and bytes after the write.
            try:
                item.content.decode("utf-8")
            except UnicodeDecodeError:
                raise BootstrapError("seed-encoding") from None
            total += len(item.content)
            _require(total <= MAX_SEED_BYTES, "seed-size")
            paths.add(item.path)
            entries.append({"path": item.path, "size": len(item.content), "sha256": _sha(item.content)})
        _require("README.md" in paths, "seed-readme-required")
        _require(not any(path.startswith(other + "/") for path in paths for other in paths),
                 "seed-path-collision")
        manifest.append({"role": seed.role, "files": sorted(entries, key=lambda item: item["path"])})
    return manifest


class _Journal:
    def __init__(self, root, scope):
        _require(type(root) is Path or isinstance(root, Path), "journal-path")
        parent = _open_directory(root)
        try:
            name = "gitlab-bootstrap-" + scope.environment_id
            # Existing evidence, including an interrupted create, is a hard stop.
            os.mkdir(name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
            self.directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        self.sequence = 0

    def write(self, kind, value):
        self.sequence += 1
        name = f"{self.sequence:04d}-{kind}.json"
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.directory)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(_canonical(value) + b"\n")
                stream.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(self.directory)

    def close(self):
        os.close(self.directory)


def _identity(session, scope):
    _require(session.origin == scope.origin, "session-origin")
    metadata = session.metadata()
    _require(type(metadata) is dict and metadata.get("version") == GITLAB_VERSION
             and metadata.get("enterprise") is False, "server-contract")
    principal = session.principal()
    _require(type(principal) is dict and type(principal.get("id")) is int
             and principal["id"] == scope.owner_user_id
             and principal.get("username") == scope.namespace_path
             and principal.get("state") == "active", "principal-identity")
    namespace = session.request("GET", f"/api/v4/namespaces/{scope.namespace_id}", expected=(200,))
    _require(type(namespace) is dict and type(namespace.get("id")) is int
             and namespace["id"] == scope.namespace_id and namespace.get("kind") == "user"
             and namespace.get("full_path") == scope.namespace_path
             and namespace.get("path") == scope.namespace_path
             and namespace.get("parent_id") is None, "namespace-identity")


def _project_identity(value, scope, role, project_id=None):
    _require(type(value) is dict and _positive(value.get("id")), "project-id")
    _require(project_id is None or value["id"] == project_id, "project-id-changed")
    path = scope.project_path(role)
    full_path = scope.namespace_path + "/" + path
    namespace, owner = value.get("namespace"), value.get("owner")
    _require(type(namespace) is dict and type(owner) is dict
             and type(namespace.get("id")) is int and namespace["id"] == scope.namespace_id
             and namespace.get("kind") == "user" and namespace.get("full_path") == scope.namespace_path
             and type(owner.get("id")) is int and owner["id"] == scope.owner_user_id,
             "project-owner")
    _require(value.get("path") == path and value.get("path_with_namespace") == full_path
             and value.get("description") == scope.marker and value.get("visibility") == "private"
             and value.get("default_branch") == "main" and value.get("archived") is False
             and value.get("http_url_to_repo") == scope.origin + "/" + full_path + ".git",
             "project-identity")
    _require(value.get("shared_runners_enabled") is False and value.get("group_runners_enabled") is False
             and value.get("auto_devops_enabled") is False
             and value.get("only_allow_merge_if_pipeline_succeeds") is True
             and value.get("allow_merge_on_skipped_pipeline") is False
             and value.get("merge_method") == "ff" and value.get("squash_option") == "never"
             and value.get("only_allow_merge_if_all_discussions_are_resolved") is True
             and value.get("remove_source_branch_after_merge") is False,
             "project-execution-policy")
    return value["id"]


def _branch(session, project_id):
    branch = session.request("GET", f"/api/v4/projects/{project_id}/repository/branches/main", expected=(200,))
    _require(type(branch) is dict and branch.get("name") == "main"
             and type(branch.get("commit")) is dict and _oid(branch["commit"].get("id")),
             "branch-identity")
    return branch["commit"]["id"]


def _tree(session, project_id, commit, paths):
    entries = session.get_collection(f"/api/v4/projects/{project_id}/repository/tree"
                                     f"?ref={commit}&recursive=true&per_page=100", max_pages=1, max_items=64)
    _require(type(entries) is list and len(entries) < 100, "tree-bound")
    actual = {}
    for entry in entries:
        _require(type(entry) is dict and isinstance(entry.get("path"), str)
                 and entry["path"] not in actual and _oid(entry.get("id")), "tree-entry")
        actual[entry["path"]] = entry
    directories = {"/".join(path.split("/")[:index]) for path in paths
                   for index in range(1, len(path.split("/")))}
    _require(set(actual) == set(paths) | directories, "tree-paths")
    for path, entry in actual.items():
        _require(entry.get("type") == ("tree" if path in directories else "blob")
                 and entry.get("mode") == ("040000" if path in directories else "100644"),
                 "tree-mode")
    return actual


def _file(session, project_id, path, commit, *, expected_content=None):
    value = session.request("GET", f"/api/v4/projects/{project_id}/repository/files/{quote(path, safe='')}"
                            f"?ref={commit}", expected=(200,))
    _require(type(value) is dict and value.get("file_path") == path and value.get("ref") == commit
             and value.get("commit_id") == commit and _oid(value.get("last_commit_id"))
             and _oid(value.get("blob_id")) and value.get("encoding") == "base64"
             and value.get("execute_filemode") is False
             and type(value.get("size")) is int and 0 <= value["size"] <= MAX_FILE_BYTES
             and isinstance(value.get("content"), str) and len(value["content"]) <= 2 * MAX_FILE_BYTES,
             "file-identity")
    try:
        raw = base64.b64decode(value["content"], validate=True)
    except ValueError:
        raise BootstrapError("file-content") from None
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    _require(value["size"] == len(raw) and value.get("content_sha256") == _sha(raw)
             and value["blob_id"] == blob, "file-integrity")
    _require(expected_content is None or raw == expected_content, "file-content")
    return value


def bootstrap_owned_projects(session: GitLabSession, scope: BootstrapScope,
                             seeds: tuple[ProjectSeed, ...], *, journal_root: Path, cleanup=None):
    """Create and seed only fresh personal projects; never resume from a report.

    Transport errors or validation failures stop the operation. The immutable
    journal retains every known project and every ambiguous mutation. Cleanup
    requires separate reconciliation and scoped authority; this function has no
    DELETE, retry, adoption, account, server-install or runner-registration path.
    """
    _require(isinstance(session, GitLabSession) and type(scope) is BootstrapScope, "bootstrap-input")
    scope.validate()
    if cleanup is not None:
        from .owned_gitlab_cleanup import OwnedGitLabResources
        _require(type(cleanup) is OwnedGitLabResources, 'cleanup-owner')
        cleanup.assert_binding(session, scope)
    manifest = _seed_manifest(seeds)
    report = {"schema_version": 1, "kind": "gitlab-owned-project-bootstrap", "scope": asdict(scope),
              "seed_manifest_sha256": _sha(_canonical(manifest)), "state": "incomplete",
              "resources": [], "canonical_success": False, "production_preflight_ready": False,
              "complete_cleanup": False, "service_installation": "not_performed"}
    journal = _Journal(journal_root, scope)
    try:
        journal.write("scope", {"scope": asdict(scope), "seed_manifest": manifest,
                                "contract": {"version": GITLAB_VERSION, "edition": "CE", "api": "v4"}})
        _identity(session, scope)
        # Refuse both existing destinations before the first project write. A
        # 404 is only an absence observation, never proof a previous write failed.
        for seed in seeds:
            path = quote(scope.namespace_path + "/" + scope.project_path(seed.role), safe="")
            try:
                session.request("GET", "/api/v4/projects/" + path, expected=(200,))
            except GitLabAPIError as error:
                if error.status != 404:
                    raise
            else:
                raise BootstrapError("preexisting-project")
        for seed in seeds:
            _identity(session, scope)
            resource = {"role": seed.role, "path_with_namespace": scope.namespace_path + "/"
                        + scope.project_path(seed.role), "project_id": None,
                        "origin": scope.origin, "owner_user_id": scope.owner_user_id,
                        "namespace_id": scope.namespace_id, "ownership_marker": scope.marker,
                        "create_effect": "unknown", "identity_verified": False,
                        "seed_effect": "none", "cleanup": "unresolved-create",
                        "deletion_authorized": False}
            payload = {"name": scope.project_path(seed.role), "path": scope.project_path(seed.role),
                       "namespace_id": scope.namespace_id,
                       "description": scope.marker, "visibility": "private", "default_branch": "main",
                       "initialize_with_readme": True, "shared_runners_enabled": False,
                       "group_runners_enabled": False, "auto_devops_enabled": False,
                       "only_allow_merge_if_pipeline_succeeds": True,
                       "allow_merge_on_skipped_pipeline": False, "emails_enabled": False,
                       "merge_method": "ff", "squash_option": "never",
                       "only_allow_merge_if_all_discussions_are_resolved": True,
                       "remove_source_branch_after_merge": False,
                       "repository_object_format": "sha1"}
            journal.write("create-intent", {"role": seed.role, "target": "/api/v4/projects",
                                             "payload": payload})
            report["resources"].append(resource)
            cleanup_ticket = cleanup.project_intent(seed.role) if cleanup is not None else None
            try:
                created = session.request("POST", "/api/v4/projects", payload=payload, expected=(201,))
            except GitLabAPIError as error:
                resource["create_effect"] = error.outcome
                if error.outcome == "none":
                    resource["cleanup"] = "not-created"
                raise
            # Retain an observed numeric ID before validating ownership fields.
            # Unverified identity is never permission to delete that ID.
            resource["create_effect"] = "applied"
            if type(created) is dict and _positive(created.get("id")):
                resource["project_id"] = created["id"]
                resource["cleanup"] = "identity-reconciliation-required"
            journal.write("create-observed", resource)
            project_id = _project_identity(created, scope, seed.role)
            _require(project_id not in [prior["project_id"] for prior in report["resources"][:-1]],
                     "project-id-reused")
            fresh = session.request("GET", f"/api/v4/projects/{project_id}", expected=(200,))
            _project_identity(fresh, scope, seed.role, project_id)
            if cleanup is not None:
                cleanup.project_created(cleanup_ticket, project_id)
            resource["identity_verified"] = True
            resource["cleanup"] = "owned-project-deletion-required"
            journal.write("owned-project", resource)
            initial = _branch(session, project_id)
            tree = _tree(session, project_id, initial, {"README.md"})
            readme = _file(session, project_id, "README.md", initial)
            _require(tree["README.md"]["id"] == readme["blob_id"], "initial-tree-integrity")
            actions = []
            for item in sorted(seed.files, key=lambda item: item.path):
                action = {"action": "update" if item.path == "README.md" else "create",
                          "file_path": item.path, "encoding": "base64",
                          "content": base64.b64encode(item.content).decode()}
                if item.path == "README.md":
                    action["last_commit_id"] = readme["last_commit_id"]
                actions.append(action)
            _identity(session, scope)
            _project_identity(session.request("GET", f"/api/v4/projects/{project_id}", expected=(200,)),
                              scope, seed.role, project_id)
            _require(_branch(session, project_id) == initial, "initial-branch-changed")
            commit_payload = {"branch": "main", "commit_message": "Seed owned factory " + seed.role,
                              "actions": actions, "force": False, "allow_empty": True}
            journal.write("seed-intent", {"project_id": project_id, "role": seed.role,
                                           "base_commit": initial, "payload_sha256": _sha(_canonical(commit_payload))})
            resource["seed_effect"] = "unknown"
            resource["base_commit"] = initial
            try:
                commit = session.request("POST", f"/api/v4/projects/{project_id}/repository/commits",
                                         payload=commit_payload, expected=(201,))
            except GitLabAPIError as error:
                resource["seed_effect"] = error.outcome
                raise
            resource["seed_effect"] = "applied"
            if type(commit) is dict and _oid(commit.get("id")):
                resource["seed_commit"] = commit["id"]
            journal.write("seed-observed", resource)
            _require(type(commit) is dict and _oid(commit.get("id"))
                     and commit.get("parent_ids") == [initial], "seed-parent-diverged")
            commit_id = commit["id"]
            _require(_branch(session, project_id) == commit_id, "seed-branch-diverged")
            tree = _tree(session, project_id, commit_id, {item.path for item in seed.files})
            for item in seed.files:
                observed = _file(session, project_id, item.path, commit_id, expected_content=item.content)
                _require(tree[item.path]["id"] == observed["blob_id"], "seed-tree-integrity")
            _project_identity(session.request("GET", f"/api/v4/projects/{project_id}", expected=(200,)),
                              scope, seed.role, project_id)
            _require(_branch(session, project_id) == commit_id, "seed-final-branch-diverged")
            resource["seed_verified"] = True
            journal.write("seed-verified", resource)
        _identity(session, scope)
        for resource in report["resources"]:
            project_id = resource["project_id"]
            _project_identity(session.request("GET", f"/api/v4/projects/{project_id}", expected=(200,)),
                              scope, resource["role"], project_id)
            _require(_branch(session, project_id) == resource["seed_commit"], "seed-final-branch-diverged")
        report["state"] = "component-complete"
        journal.write("terminal", report)
        return report
    except BaseException as error:
        # Do not stringify exceptions: server bodies or caller file content can
        # contain secrets. Interrupted writes retain their pre-dispatch intent.
        report["state"] = "incomplete"
        report["failure"] = str(error) if type(error) is BootstrapError else "component-error"
        if report["resources"]:
            session.stop_writes()
        try:
            journal.write("terminal", report)
        except OSError:
            pass
        raise
    finally:
        journal.close()
