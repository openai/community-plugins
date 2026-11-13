"""Read-only owned GitLab seed materialization with exact native Git identities.

No network Git helper is executed. Bounded authenticated API tree/blob reads
reconstruct ordinary unsigned commits; every raw object must hash to the exact
advertised SHA. Unsupported commit headers remain an explicit gap, never a
replacement commit or a claimed clone. Only a new local repository is written.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
from weakref import WeakKeyDictionary

from devsecops.adapters.artifacts import _open_directory
from devsecops.runtime.cli import _new_directory, _write, _verify_output_path
from .git_transport_process import run
from .gitlab_bootstrap import BootstrapScope, _project_identity
from .state import StateError, _read, _sha

_CHECKOUTS = WeakKeyDictionary()


def object_id(kind, raw):
    return hashlib.sha1(kind.encode() + b" " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def commit_bytes(metadata, tree):
    """Only ordinary API-reconstructible headers; the final SHA is mandatory."""
    parents = metadata.get("parent_ids")
    if (
        type(parents) is not list
        or len(parents) > 1
        or any(not isinstance(item, str) or not re.fullmatch("[a-f0-9]{40}", item) for item in parents)
        or not isinstance(metadata.get("message"), str)
        or len(metadata["message"].encode()) > 64000
    ):
        raise StateError("Unsupported owned seed commit")
    lines = ["tree " + tree, *["parent " + item for item in parents]]
    for role, date_key in (("author", "authored_date"), ("committer", "committed_date")):
        name, email = metadata.get(role + "_name"), metadata.get(role + "_email")
        if any(
            not isinstance(value, str) or not value or any(char in value for char in "\r\n\x00<>")
            for value in (name, email)
        ):
            raise StateError("Unsupported owned seed identity")
        when = datetime.fromisoformat(metadata[date_key].replace("Z", "+00:00"))
        if when.tzinfo is None or when.microsecond:
            raise StateError("Owned commit timestamp is not exact")
        lines.append(f"{role} {name} <{email}> {int(when.timestamp())} {when.strftime('%z')}")
    raw = ("\n".join(lines) + "\n\n" + metadata["message"]).encode("utf-8")
    if object_id("commit", raw) != metadata.get("id"):
        raise StateError("API metadata does not reproduce the exact original raw commit")
    return raw


def collect_objects(session, project_id, revision):
    """Bounded complete ordinary history; external submodules/symlinks refused."""
    objects, commits, active, total = {}, set(), set(), 0

    def remember(kind, raw, expected):
        nonlocal total
        if object_id(kind, raw) != expected:
            raise StateError("Authenticated Git object identity differs")
        if expected not in objects:
            total += len(raw)
            if len(objects) >= 2048 or total > 32_000_000:
                raise StateError("Owned seed object closure exceeds its bound")
            objects[expected] = (kind, raw)

    def commit(sha):
        if sha in commits:
            return
        if sha in active or len(commits) + len(active) >= 32:
            raise StateError("Owned seed history is cyclic or exceeds its bound")
        active.add(sha)
        meta = session.request("GET", f"/api/v4/projects/{project_id}/repository/commits/{sha}")
        if type(meta) is not dict or meta.get("id") != sha:
            raise StateError("Owned commit metadata differs")
        rows = session.get_collection(
            f"/api/v4/projects/{project_id}/repository/tree?ref={sha}&recursive=true",
            max_pages=8,
            max_items=800,
        )
        entries, trees = {}, {""}
        for row in rows:
            if (
                type(row) is not dict
                or set(row) - {"id", "name", "type", "path", "mode"}
                or not isinstance(row.get("path"), str)
                or len(row["path"]) > 1024
                or any(
                    part in ("", ".", "..", ".git") or "\x00" in part or "\n" in part or "\r" in part
                    for part in row["path"].split("/")
                )
                or row["name"] != row["path"].split("/")[-1]
                or row["path"] in entries
                or not re.fullmatch("[a-f0-9]{40}", row.get("id", ""))
                or (row["type"], row["mode"])
                not in {("blob", "100644"), ("blob", "100755"), ("tree", "040000")}
            ):
                raise StateError("Unsupported or duplicate owned tree entry")
            entries[row["path"]] = row
            if row["type"] == "tree":
                trees.add(row["path"])
            elif row["id"] not in objects:
                raw = session.request_repository_blob(project_id, row["id"])
                remember("blob", raw, row["id"])
        for path in entries:
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            if parent not in trees:
                raise StateError("Incomplete recursive owned tree")

        def tree(path):
            children = [
                row
                for name, row in entries.items()
                if (name.rsplit("/", 1)[0] if "/" in name else "") == path
            ]
            chunks = []
            for row in sorted(
                children, key=lambda row: (row["name"] + ("/" if row["type"] == "tree" else "")).encode()
            ):
                if row["type"] == "tree":
                    nested = tree(row["path"])
                    if nested != row["id"]:
                        raise StateError("Owned subtree differs")
                mode = "40000" if row["type"] == "tree" else row["mode"]
                chunks.append(mode.encode() + b" " + row["name"].encode() + b"\0" + bytes.fromhex(row["id"]))
            raw = b"".join(chunks)
            oid = object_id("tree", raw)
            remember("tree", raw, oid)
            return oid

        root = tree("")
        raw = commit_bytes(meta, root)
        remember("commit", raw, sha)
        for parent in meta["parent_ids"]:
            commit(parent)
        active.remove(sha)
        commits.add(sha)

    commit(revision)
    return objects


class ObservedOwnedCheckout:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Owned checkout requires direct authenticated project materialization")

    def __reduce__(self):
        raise TypeError("Owned checkout cannot be serialized")


def require_owned_checkout(value, *, journal):
    record = _CHECKOUTS.get(value) if type(value) is ObservedOwnedCheckout else None
    if record is None or record["journal"] is not journal or record["pid"] != os.getpid():
        raise StateError("Current direct owned checkout required")
    # Native Git can reinterpret unchanged raw commits through these local
    # overlays. The materializer never creates them; saved origin bytes do not
    # authorize adding one later. Worktree edits remain governed by consumers.
    metadata = Path(record["binding"]["repository"]) / ".git"
    for name in (
        "info/grafts",
        "shallow",
        "refs/replace",
        "packed-refs",
        "objects/info/alternates",
        "objects/info/http-alternates",
    ):
        if os.path.lexists(metadata / name):
            raise StateError("Owned checkout ancestry metadata changed")
    from .canonical_bootstrap import require_bootstrap_authority

    require_bootstrap_authority(record["bootstrap"], journal)
    if record.get("delivery") is not None:
        from .owned_git_delivery import require_delivery

        delivered = require_delivery(record["delivery"], journal=journal)
        if delivered["watched_revision"] != record["binding"]["revision"]:
            raise StateError("Original merged delivery differs from materialized source")
    for path, ref in record["evidence"].items():
        raw = _read(path, ref["bytes"] + 1)
        if _sha(raw) != ref["sha256"] or len(raw) != ref["bytes"]:
            raise StateError("Owned checkout receipt changed")
    for path, expected in record["retained"].items():
        raw = _read(path, expected["bytes"] + 1)
        if len(raw) != expected["bytes"] or _sha(raw) != expected["sha256"]:
            raise StateError("Owned checkout original Git objects or metadata changed")
    return deepcopy(record["binding"])


def checkout_evidence_artifacts(value, *, journal):
    require_owned_checkout(value, journal=journal)
    return tuple(_CHECKOUTS[value]["evidence"])


def materialize_owned_repository(context, *, bootstrap, role, git_path, git_sha256, output):
    return _materialize_repository(
        context, bootstrap=bootstrap, role=role, git_path=git_path, git_sha256=git_sha256, output=output
    )


def materialize_delivered_repository(context, *, bootstrap, delivery, git_path, git_sha256, output):
    """Read exact originally merged revision; never reuse a declared local path."""
    from .owned_git_delivery import require_delivery

    bound = require_delivery(delivery, journal=bootstrap.journal)
    if (
        bound["delivery_stage"] != "merged-watched-branch"
        or bound["run_id"] != context.run_id
        or bound["watched_revision"] != bound["deployment_commit"]
    ):
        raise StateError("Original merged watched revision required")
    return _materialize_repository(
        context,
        bootstrap=bootstrap,
        role="deployment",
        git_path=git_path,
        git_sha256=git_sha256,
        output=output,
        delivery=delivery,
    )


def _materialize_repository(context, *, bootstrap, role, git_path, git_sha256, output, delivery=None):
    from .canonical_bootstrap import OwnedBootstrapCoordinator, require_bootstrap_authority

    if type(bootstrap) is not OwnedBootstrapCoordinator or role not in ("source", "deployment"):
        raise StateError("Direct owned project bootstrap required")
    authority, session = bootstrap.gitlab(context)
    facts = require_bootstrap_authority(authority, bootstrap.journal)
    receipt_offset = len(session.receipts)
    selected = facts["projects"][role]
    revision = selected["seed_commit"]
    if delivery is not None:
        from .owned_git_delivery import require_delivery

        bound = require_delivery(delivery, journal=bootstrap.journal)
        if (
            bound["project_id"] != selected["id"]
            or bound["repository_url"] != facts["origin"] + "/" + selected["path_with_namespace"] + ".git"
        ):
            raise StateError("Delivered source is outside original owned project")
        revision = bound["watched_revision"]
    scope = BootstrapScope(**dict(facts["scope"]))
    if not isinstance(output, Path) or not output.is_absolute() or ".." in output.parts:
        raise StateError("New explicit local repository output required")

    def observe():
        bootstrap.require_authority(context)
        _project_identity(
            session.request("GET", f"/api/v4/projects/{selected['id']}"), scope, role, selected["id"]
        )
        branch = session.request("GET", f"/api/v4/projects/{selected['id']}/repository/branches/main")
        if branch.get("name") != "main" or branch.get("commit", {}).get("id") != revision:
            raise StateError("Owned watched branch moved before materialization")
        if (
            delivery is not None
            and require_delivery(delivery, journal=bootstrap.journal)["watched_revision"] != revision
        ):
            raise StateError("Original merged revision changed")

    observe()
    objects = collect_objects(session, selected["id"], revision)
    observe()
    with _new_directory(output) as fd:
        repo = output / "repository"
        repo.mkdir(mode=0o700)
        home = output / "home"
        home.mkdir(mode=0o700)
        executable = os.open(git_path, os.O_RDONLY | os.O_NOFOLLOW)
        cwd = _open_directory(repo)
        try:
            env = {
                "PATH": "/usr/bin:/bin",
                "HOME": str(home),
                "LC_ALL": "C",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ALLOW_PROTOCOL": "",
            }

            def native(*args, data=None):
                bootstrap.require_authority(context)
                result = run(
                    [
                        str(git_path),
                        "-c",
                        "protocol.allow=never",
                        "-c",
                        "core.hooksPath=/dev/null",
                        "-c",
                        "core.autocrlf=false",
                        *args,
                    ],
                    cwd_fd=cwd,
                    executable_fd=executable,
                    executable_sha256=git_sha256,
                    scratch_fd=fd,
                    timeout=min(120, context.remaining_seconds()),
                    limit=2_000_000,
                    env=env,
                    stdin_bytes=data,
                )
                code, stdout, _, _ = result
                if code != 0:
                    raise StateError("Pinned native owned checkout operation failed")
                return stdout

            native("init", "--initial-branch=main", ".")
            for oid, (kind, raw) in objects.items():
                result = native("hash-object", "-w", "-t", kind, "--stdin", data=raw)
                if result.decode().strip() != oid:
                    raise StateError("Native owned object identity differs")
            native("update-ref", "refs/heads/main", revision)
            native("checkout", "--detach", revision)
            native("fsck", "--full", "--strict")
            observe()
            retained = {}
            paths = [repo / ".git/HEAD", repo / ".git/config", repo / ".git/refs/heads/main"]
            paths.extend(repo / ".git/objects" / oid[:2] / oid[2:] for oid in objects)
            for path in paths:
                raw = _read(path, 4_100_000)
                retained[path] = {"sha256": _sha(raw), "bytes": len(raw)}
            binding = {
                "run_id": context.run_id,
                "identities": dict(context.identities),
                "role": role,
                "project_id": selected["id"],
                "repository_url": facts["origin"] + "/" + selected["path_with_namespace"] + ".git",
                "repository": str(repo),
                "revision": revision,
                "objects": {
                    oid: {"kind": kind, "sha256": _sha(raw), "bytes": len(raw)}
                    for oid, (kind, raw) in objects.items()
                },
            }
            transport = deepcopy(session.receipts[receipt_offset:])
            if not transport or len(transport) > 512 or any(row["method"] != "GET" for row in transport):
                raise StateError("Bounded original checkout GET receipts required")
            observed = _write(
                fd,
                "readback.json",
                {
                    "kind": "authenticated-owned-project-readback",
                    "run_id": context.run_id,
                    "project_id": selected["id"],
                    "revision": revision,
                    "transport": transport,
                    "read_only": True,
                },
            )
            receipt = _write(fd, "checkout.json", {"kind": "authenticated-owned-project-checkout", **binding})
            os.fsync(fd)
            _verify_output_path(output, fd)
        finally:
            os.close(cwd)
            os.close(executable)
    cap = object.__new__(ObservedOwnedCheckout)
    _CHECKOUTS[cap] = {
        "journal": bootstrap.journal,
        "pid": os.getpid(),
        "bootstrap": authority,
        "binding": binding,
        "evidence": {output / "checkout.json": receipt, output / "readback.json": observed},
        "retained": retained,
        "delivery": delivery,
    }
    require_owned_checkout(cap, journal=bootstrap.journal)
    return cap
