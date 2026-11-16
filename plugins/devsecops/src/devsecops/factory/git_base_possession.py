"""Bounded remote base observation; never permission to omit objects or write Git."""

from __future__ import annotations

from contextlib import contextmanager
import base64
from copy import deepcopy
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import ssl
import time
import uuid
from weakref import WeakKeyDictionary

from devsecops.adapters.artifacts import _open_directory
from devsecops.adapters.http import APIError, HTTPSReadClient, _json
from devsecops.adapters.vulnerability_policy import checkpoint
from devsecops.factory.deployment_commit import _object_id, _oid, commit_configuration
from devsecops.factory.git_destination import (
    MAX_CA_BYTES,
    _MEDIA,
    _advertisement,
    _authorization,
    _now,
    _project,
    _read_input,
    destination_configuration,
)
from devsecops.factory.git_transport import (
    MAX_CLOSURE_OBJECTS,
    MAX_CLOSURE_RAW,
    MAX_DEPTH,
    MAX_OBJECT_BYTES,
    MAX_TREE_ENTRIES,
    _directory_at,
    _identity,
    _snapshot_to,
    open_transport_consumer_output,
    require_prepared_git_transport,
)
from devsecops.factory.git_transport_process import run
from devsecops.factory.mission_security import _read_retained
from devsecops.runtime.cli import _verify_output_path
from devsecops.runtime.context import canonical_bytes, seal

MAX_REQUESTS = 64
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_NATIVE_BYTES = 16 * 1024 * 1024
_SHA = re.compile(r"[a-f0-9]{64}")
_OID = re.compile(r"[a-f0-9]{40}")
_OBSERVATIONS = WeakKeyDictionary()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def observed_base_artifact_refs(value):
    """Current original readback files for retention by the actual writer."""
    if type(value) is not ObservedGitBase:
        raise ValueError('Original observed base required')
    value.describe()
    record=_OBSERVATIONS[value]
    return {record['output']/name:deepcopy(ref) for name,ref in record['artifacts'].items()}


class ObservedGitBase:
    """Current exact-base evidence only; no writer or object-omission authority."""

    __slots__ = ("__weakref__",)

    def __new__(cls, *args, **kwargs):
        raise TypeError("Base observations require direct transport and fresh service reads")

    def __reduce__(self):
        raise TypeError("Base observations cannot be serialized")

    def describe(self):
        record = _OBSERVATIONS.get(self)
        if record is None:
            raise ValueError("Unissued base observation")
        return require_observed_git_base(
            self,
            transport=record["transport"],
            **record["scope"],
            expected_config_sha256=record["config_sha256"],
        )


def require_observed_git_base(
    value,
    *,
    transport,
    run_scope,
    source_commit,
    deployment_commit,
    proposal_configuration_sha256,
    configuration_sha256,
    expected_config_sha256,
    deadline=None,
):
    """Audit direct provenance and retained bytes; a saved report is never authority."""
    now = time.monotonic()
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise ValueError("Finite audit deadline required")
    deadline = min(now + 10, deadline) if deadline is not None else now + 10
    scope = dict(
        run_scope=run_scope,
        source_commit=source_commit,
        deployment_commit=deployment_commit,
        proposal_configuration_sha256=proposal_configuration_sha256,
        configuration_sha256=configuration_sha256,
    )
    record = _OBSERVATIONS.get(value) if type(value) is ObservedGitBase else None
    if (
        record is None
        or record["pid"] != os.getpid()
        or record["transport"] is not transport
        or record["scope"] != scope
        or record["config_sha256"] != expected_config_sha256
        or now >= record["expires_monotonic"]
    ):
        raise ValueError("Unissued, foreign, expired or mismatched base observation")

    def audit_inputs():
        if (
            require_prepared_git_transport(transport, **scope, deadline=deadline)
            != record["transport_binding"]
        ):
            raise ValueError("Direct transport binding changed")
        for path, maximum, digest, identity in record["inputs"]:
            raw, current = _read_input(path, maximum, deadline)
            if identity != current or _sha(raw) != digest:
                raise ValueError("Selected base observation input changed")
        if record.get('reader') is not None:
            from .owned_reader_identity import require_owned_reader_origin
            origin,journal,authority,original = record['reader']
            if require_owned_reader_origin(origin,journal=journal,bootstrap_authority=authority) != original:
                raise ValueError('Original owned reader origin changed')

    audit_inputs()
    directory = _open_directory(record["output"])
    try:
        if _identity(directory) != record["output_identity"]:
            raise ValueError("Base observation directory changed")
        if set(os.listdir(directory)) != set(record["artifacts"]):
            raise ValueError("Base observation artifact membership changed")
        identities = {}
        fields = ("st_dev", "st_ino", "st_size", "st_mode", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        for name, ref in record["artifacts"].items():
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
            identities[name] = tuple(getattr(before, key) for key in fields)
            _read_retained(directory, name, ref, deadline)
        # Actual source UTC and input authority must remain current after the
        # potentially large retained-byte reads, not just before them.
        audit_inputs()
        if set(os.listdir(directory)) != set(record["artifacts"]):
            raise ValueError("Base observation artifact membership changed")
        for name, identity in identities.items():
            after = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if tuple(getattr(after, key) for key in fields) != identity:
                raise ValueError("Base observation artifact changed during audit")
        _verify_output_path(record["output"], directory)
    finally:
        os.close(directory)
    checkpoint(deadline)
    if time.monotonic() >= record["expires_monotonic"]:
        raise ValueError("Base observation expired during audit")
    return deepcopy(record["binding"])


@contextmanager
def _native_workspace(output, config, audit, deadline, commands):
    """Descriptor-anchored sibling scratch, independent of ambient temporary roots."""
    parent = _open_directory(output.parent)
    parent_identity = _identity(parent)
    name = ".git-base-" + uuid.uuid4().hex
    root = output.parent / name
    directory = executable = private = None
    created = False
    try:
        audit()
        _verify_output_path(output.parent, parent)
        os.mkdir(name, 0o700, dir_fd=parent)
        created = True
        directory = _directory_at(parent, name)
        identity = _identity(directory)
        audit()
        _verify_output_path(output.parent, parent)
        _verify_output_path(root, directory)
        tool = _snapshot_to(Path(config["git_path"]), directory, "git", 64 * 1024 * 1024, deadline)
        if tool["sha256"] != config["git_sha256"]:
            raise ValueError("Pinned native Git changed")
        executable = os.open("git", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        os.fchmod(executable, 0o500)
        fixed = [
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.pager=cat",
            "-c",
            "core.attributesFile=/dev/null",
            "-c",
            "protocol.allow=never",
            "-c",
            "protocol.http.allow=never",
            "-c",
            "protocol.https.allow=never",
            "-c",
            "protocol.ssh.allow=never",
            "-c",
            "protocol.git.allow=never",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "credential.helper=",
            "-c",
            "submodule.recurse=false",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "gc.auto=0",
        ]
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
        total = 0

        def scratch_audit():
            audit()
            if _identity(parent) != parent_identity or _identity(directory) != identity:
                raise ValueError("Private native directory changed")
            _verify_output_path(output.parent, parent)
            _verify_output_path(root, directory)
            _read_retained(directory, "git", tool, deadline)

        def native(*args, data=None, limit=MAX_OBJECT_BYTES):
            nonlocal total
            scratch_audit()
            limit = min(limit, MAX_NATIVE_BYTES - total)
            if limit <= 0:
                raise ValueError("Native output budget exhausted")
            code, raw, error, receipt = run(
                [str(root / "git"), *fixed, *args],
                cwd_fd=directory if args[0] == "init" else private,
                executable_fd=executable,
                executable_sha256=tool["sha256"],
                scratch_fd=directory,
                timeout=min(30, deadline - time.monotonic()),
                limit=limit,
                env=env,
                stdin_bytes=data,
            )
            total += len(raw) + len(error)
            commands.append({"operation": args[0], **receipt})
            scratch_audit()
            if code or total > MAX_NATIVE_BYTES:
                raise ValueError("Bounded native reconstruction failed")
            return raw

        native("init", "--bare", "--quiet", "--template=", "--object-format=sha1", "objects.git")
        private = _directory_at(directory, "objects.git")
        yield native
        scratch_audit()
    finally:
        for fd in (private, executable):
            if fd is not None:
                os.close(fd)
        try:
            if created:
                if directory is not None:
                    _verify_output_path(root, directory)
                _verify_output_path(output.parent, parent)
                shutil.rmtree(name, dir_fd=parent)
                os.fsync(parent)
        finally:
            try:
                if directory is not None:
                    os.close(directory)
            finally:
                os.close(parent)


def _tree_page(raw, path, page, headers):
    # Reuse the duplicate-key/depth/finite-number decoder for the array payload.
    decoded = _json(b'{"rows":' + raw + b"}")
    rows = decoded["rows"]
    if set(decoded) != {"rows"} or not isinstance(rows, list) or len(rows) > 100:
        raise APIError("git-base-tree-page")
    allowed = {"x-page", "x-per-page", "x-next-page", "x-prev-page", "x-total", "x-total-pages"}
    if set(headers) - allowed:
        raise APIError("git-base-pagination")
    for key, wanted in (
        ("x-page", str(page)),
        ("x-per-page", "100"),
        ("x-prev-page", "" if page == 1 else str(page - 1)),
    ):
        if key in headers and headers[key] != wanted:
            raise APIError("git-base-pagination")
    next_page = headers.get("x-next-page")
    if next_page not in (None, "", str(page + 1)):
        raise APIError("git-base-pagination")
    if next_page and len(rows) != 100:
        raise APIError("git-base-pagination")
    if next_page == "" and len(rows) == 100:
        # Request one more fixed page: omission cannot hide behind a terminal header.
        next_page = str(page + 1)
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "name", "type", "path", "mode"}:
            raise APIError("git-base-tree-entry")
        name = row["name"]
        if (
            not isinstance(name, str)
            or not name
            or name in (".", "..")
            or len(name.encode("utf-8")) > 255
            or any(c in name for c in "/\0\r\n\t")
            or row["path"] != (path + "/" if path else "") + name
            or not isinstance(row["id"], str)
            or not _OID.fullmatch(row["id"])
            or (row["mode"], row["type"])
            not in (("040000", "tree"), ("100644", "blob"), ("100755", "blob"), ("120000", "blob"))
        ):
            raise APIError("git-base-tree-entry")
    return rows, bool(next_page) or next_page is None and len(rows) == 100


def verify_git_base_possession(
    *,
    transport,
    run_scope,
    source_commit,
    deployment_commit,
    proposal_configuration_sha256,
    configuration_sha256,
    config,
    expected_config_sha256,
    output,
    timeout=120,
    reader_origin=None,
    bootstrap_authority=None,
    journal=None,
    service_route=None,
):
    """Reobserve selected destination and full base trees/blobs using GET only."""
    if (
        type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or not 1 <= timeout <= 120
        or not isinstance(config, Path)
        or not config.is_absolute()
        or ".." in config.parts
        or not isinstance(output, Path)
        or not output.is_absolute()
        or ".." in output.parts
        or not isinstance(expected_config_sha256, str)
        or not _SHA.fullmatch(expected_config_sha256)
    ):
        raise ValueError("Pinned selection, new absolute output and bounded timeout required")
    deadline = time.monotonic() + timeout
    scope = deepcopy(
        dict(
            run_scope=run_scope,
            source_commit=source_commit,
            deployment_commit=deployment_commit,
            proposal_configuration_sha256=proposal_configuration_sha256,
            configuration_sha256=configuration_sha256,
        )
    )
    binding = require_prepared_git_transport(transport, **scope, deadline=deadline)
    raw, identity = _read_input(config, 64_000, deadline)
    if _sha(raw) != expected_config_sha256:
        raise ValueError("Selected Git configuration changed")
    selected = destination_configuration(raw)
    owned_reader = selected['kind'] == 'owned-git-destination-observation-configuration'
    reader_binding = None
    if owned_reader:
        from .owned_reader_identity import require_owned_reader_origin, _reader_secret
        reader_binding=require_owned_reader_origin(reader_origin,journal=journal,bootstrap_authority=bootstrap_authority)
        if (reader_binding['origin'] != selected['origin'] or reader_binding['project_id'] != selected['project_id']
                or reader_binding['path_with_namespace'] != selected['path_with_namespace']
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',reader_binding['username'])):
            raise ValueError('Direct owned reader differs from selected deployment project')
    elif any(value is not None for value in (reader_origin,bootstrap_authority,journal)):
        raise ValueError('Owned reader authority cannot override legacy credential selections')
    if selected["base_commit"] != deployment_commit or binding["base_commit"] != deployment_commit:
        raise ValueError("Selected remote base differs from the direct local plan")
    inputs = [(config, 64_000, _sha(raw), identity)]
    config_raw, config_identity = _read_input(
        Path(binding["output_path"]) / "commit-config.json", 64_000, deadline
    )
    if _sha(config_raw) != binding["artifacts"]["commit-config.json"]["sha256"]:
        raise ValueError("Prepared Git configuration differs")
    local_config = commit_configuration(config_raw)
    inputs.append(
        (Path(binding["output_path"]) / "commit-config.json", 64_000, _sha(config_raw), config_identity)
    )
    expected = binding["closure"]["objects"]
    if not 1 <= len(expected) <= MAX_CLOSURE_OBJECTS:
        raise ValueError("Unsupported base closure")
    selection = {
        key: deepcopy(selected[key])
        for key in (
            "origin",
            "project_id",
            "repository_path",
            "base_ref",
            "base_commit",
            "target_ref",
            "path_with_namespace",
        )
    }
    report = {
        "schema_version": 1,
        "kind": "mission-git-base-possession",
        "decision": "unknown",
        "execution_status": "partial",
        "factory_decision": "unknown",
        "omission_authorized": False,
        "delivery_authority": "not-issued",
        "scope": "bounded-base-possession-observation-only",
        "remote_possession": "unverified",
        "configuration_sha256": expected_config_sha256,
        "transport_scope": deepcopy(scope),
        "selection": selection,
        "new_commit": binding["new_commit"],
        "image_publication": deepcopy(binding["image_publication"]),
        "snapshots": [],
        "responses": [],
        "commands": [],
        "gaps": [],
        "started_at": _now(),
        "objects": {},
        "commit_raw_bytes_retrieved": False,
        "atomic_snapshot": False,
        "principal_identity": "unverified",
        "server_permission_scope": "unverified",
    }
    clients, auth = {}, None
    observation = None
    with open_transport_consumer_output(transport, **scope, output=output, deadline=deadline) as admitted:
        retained = {}

        def audit():
            checkpoint(deadline)
            admitted.audit()
            if require_prepared_git_transport(transport, **scope, deadline=deadline) != binding:
                raise ValueError("Direct Git binding changed")
            for path, maximum, digest, original in inputs:
                value, current = _read_input(path, maximum, deadline)
                if current != original or _sha(value) != digest:
                    raise ValueError("Selected Git input changed")
            if owned_reader and require_owned_reader_origin(reader_origin,journal=journal,
                    bootstrap_authority=bootstrap_authority) != reader_binding:
                raise ValueError('Original owned reader origin changed')
            checkpoint(deadline)

        def store(name, contents, terminal=False):
            audit()
            ref = admitted.write_bytes(name, contents, terminal=terminal)
            retained[name] = ref
            audit()
            return ref

        total = 0

        def request(kind, path, *, params=None, media=("application/json",), empty=False):
            nonlocal total
            audit()
            if len(report["responses"]) >= MAX_REQUESTS:
                raise APIError("request-limit")
            remaining = MAX_TOTAL_BYTES - total
            if remaining <= 0:
                raise APIError("body-limit")
            client = clients[kind]
            # Both clients share a hard body budget. Bound the next transport
            # read before it starts; rejecting a full oversized body afterward
            # would already have exceeded that acquisition bound.
            client._max_body = min(MAX_RESPONSE_BYTES, remaining)
            client._max_total = client._bytes + remaining
            response = client.get_bytes(path, params=params, accept=media)
            audit()
            if (
                response.status != 200
                or response.media_type not in media
                or not isinstance(response.body, bytes)
                or len(response.body) > MAX_RESPONSE_BYTES
                or not empty
                and not response.body
            ):
                raise APIError("git-base-response")
            total += len(response.body)
            if total > MAX_TOTAL_BYTES:
                raise APIError("body-limit")
            report["responses"].append(
                {
                    "index": len(report["responses"]) + 1,
                    "kind": kind,
                    "observed_at": _now(),
                    "body_bytes": len(response.body),
                    "body_sha256": _sha(response.body),
                }
            )
            return response

        project_path = f"/api/v4/projects/{selected['project_id']}"

        def snapshot():
            project = request("api", project_path)
            advert = request(
                "git",
                selected["repository_path"] + "/info/refs",
                params={"service": "git-upload-pack"},
                media=(_MEDIA,),
            )
            commit = request("api", project_path + "/repository/commits/" + deployment_commit)
            if project.pagination or advert.pagination or commit.pagination:
                raise APIError("git-base-pagination")
            metadata = _json(commit.body)
            if (
                metadata.get("id") != deployment_commit
                or metadata.get("parent_ids") != binding["closure"]["base_parents_not_imported"]
            ):
                raise APIError("git-base-commit-mismatch")
            return {
                "project": _project(project.body, selected),
                "advertisement": _advertisement(advert.body, selected, deadline),
                "commit": {"id": metadata["id"], "parent_ids": metadata["parent_ids"]},
            }

        try:
            store("git-destination-config.json", raw)
            context = None
            if selected["trust"]["mode"] == "bundle":
                path = Path(selected["trust"]["path"])
                ca, ca_identity = _read_input(path, MAX_CA_BYTES, deadline)
                if _sha(ca) != selected["trust"]["sha256"]:
                    raise ValueError("Selected Git CA changed")
                inputs.append((path, MAX_CA_BYTES, _sha(ca), ca_identity))
                store("ca.pem", ca)
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.load_verify_locations(cadata=ca.decode("ascii"))
            for kind in ("api", "git"):
                client = HTTPSReadClient(
                    selected["origin"],
                    timeout=min(30, timeout),
                    total_timeout=timeout,
                    max_requests=MAX_REQUESTS,
                    max_body_bytes=MAX_RESPONSE_BYTES,
                    max_total_bytes=MAX_TOTAL_BYTES,
                    **({} if service_route is None else {'service_route': service_route,
                        'ca_bundle': Path(selected['trust']['path']) if selected['trust']['mode']=='bundle' else None}),
                )
                if client.origin != selected["origin"]:
                    raise ValueError("Git origin was normalized")
                client._deadline = deadline
                if context is not None:
                    client._context = context
                clients[kind] = client
            audit()
            if owned_reader:
                from devsecops.adapters.http import _bearer
                secret=_reader_secret(reader_origin,journal=journal,bootstrap_authority=bootstrap_authority)
                auth={'api':_bearer(secret),'git':'Basic '+base64.b64encode(
                    (reader_binding['username']+':'+secret).encode('ascii')).decode('ascii')}
                secret=None
            else:
                auth = {kind: _authorization(selected[kind + "_read_credential"]) for kind in ("api", "git")}
            for kind, client in clients.items():
                client._authorization = lambda chosen=auth[kind]: chosen
            report["snapshots"].append(snapshot())
            raw_objects, expanded = bytearray(), 0

            def accept_object(kind, oid, contents):
                record = {"kind": kind, "bytes": len(contents), "sha256": _sha(contents)}
                if expected.get(oid) != record or _object_id(kind, contents) != oid:
                    raise APIError("git-base-object-mismatch")
                if oid not in report["objects"]:
                    if (
                        len(report["objects"]) >= MAX_CLOSURE_OBJECTS
                        or len(raw_objects) + len(contents) > MAX_CLOSURE_RAW
                    ):
                        raise APIError("git-base-closure-limit")
                    report["objects"][oid] = {**record, "offset": len(raw_objects)}
                    raw_objects.extend(contents)

            with _native_workspace(output, local_config, audit, deadline, report["commands"]) as native:
                visited = set()

                def tree(path, oid, depth):
                    nonlocal expanded
                    audit()
                    if depth > MAX_DEPTH or expected.get(oid, {}).get("kind") != "tree":
                        raise APIError("git-base-tree-scope")
                    rows, names, page = [], set(), 1
                    while True:
                        response = request(
                            "api",
                            project_path + "/repository/tree",
                            params={
                                "ref": deployment_commit,
                                "path": path,
                                "recursive": "false",
                                "per_page": 100,
                                "page": page,
                            },
                        )
                        batch, more = _tree_page(response.body, path, page, response.pagination)
                        for row in batch:
                            if row["name"] in names:
                                raise APIError("git-base-duplicate-tree-entry")
                            names.add(row["name"])
                            rows.append(row)
                        expanded += len(batch)
                        if expanded > MAX_TREE_ENTRIES:
                            raise APIError("git-base-tree-limit")
                        if not more:
                            break
                        page += 1
                    encoded = b"".join(
                        row["mode"].encode()
                        + b" "
                        + row["type"].encode()
                        + b" "
                        + row["id"].encode()
                        + b"\t"
                        + row["name"].encode("utf-8")
                        + b"\0"
                        for row in rows
                    )
                    if len(encoded) > MAX_OBJECT_BYTES:
                        raise APIError("git-base-tree-limit")
                    reconstructed = _oid(native("mktree", "-z", "--missing", data=encoded))
                    if reconstructed != oid:
                        raise APIError("git-base-tree-mismatch")
                    accept_object("tree", oid, native("cat-file", "tree", oid))
                    for row in rows:
                        child = row["id"]
                        if row["type"] == "tree":
                            if child not in visited:
                                visited.add(child)
                                tree(row["path"], child, depth + 1)
                        elif child not in report["objects"]:
                            if expected.get(child, {}).get("kind") != "blob":
                                raise APIError("git-base-object-scope")
                            response = request(
                                "api",
                                project_path + "/repository/blobs/" + child + "/raw",
                                media=("application/octet-stream", "text/plain"),
                                empty=True,
                            )
                            if response.pagination:
                                raise APIError("git-base-pagination")
                            accept_object("blob", child, response.body)

                root = binding["closure"]["base_tree"]
                visited.add(root)
                tree("", root, 0)
                if set(report["objects"]) != set(expected) - {deployment_commit}:
                    raise APIError("git-base-closure-incomplete")
                audit()
            # Scratch cleanup has completed before terminal evidence or capability issuance.
            report["snapshots"].append(snapshot())
            if report["snapshots"][0] != report["snapshots"][1]:
                raise APIError("git-base-destination-changed")
            store("verified-objects.bin", bytes(raw_objects))
            report["decision"], report["execution_status"] = "pass", "complete"
            report["remote_possession"] = "commit-api-identity-and-complete-tree-blob-bytes-observed"
            report["commit_observation"] = {
                "id": deployment_commit,
                "raw_bytes_retrieved": False,
                "local_raw_identity": deepcopy(expected[deployment_commit]),
                "meaning": "Authenticated commit API identity; original raw commit remains locally pinned",
            }
            report["observed_body_bytes"] = total
            audit()
        except APIError as error:
            report["gaps"].append(error.code)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, ssl.SSLError):
            report["gaps"].append("git-base-observation-incomplete")
        finally:
            report["transport_receipts"] = {kind: client.receipts for kind, client in clients.items()}
            for client in clients.values():
                client._authorization = lambda: None
            auth = None
        if report["gaps"]:
            report["decision"], report["execution_status"] = "unknown", "partial"
        report["retained"] = deepcopy(retained)
        report["finished_at"] = _now()
        report = seal(report, "observation_sha256")
        store("git-base-possession.json", canonical_bytes(report), terminal=True)
        audit()
        if report["decision"] == "pass":
            observation = object.__new__(ObservedGitBase)
            observation_binding = {
                key: deepcopy(report[key])
                for key in (
                    "scope",
                    "transport_scope",
                    "selection",
                    "new_commit",
                    "remote_possession",
                    "objects",
                    "commit_observation",
                    "configuration_sha256",
                    "omission_authorized",
                    "delivery_authority",
                )
            }
            _OBSERVATIONS[observation] = {
                "pid": os.getpid(),
                "transport": transport,
                "scope": scope,
                "transport_binding": binding,
                "config_sha256": expected_config_sha256,
                "inputs": inputs,
                "output": output,
                "output_identity": _identity(admitted.directory_fd),
                "artifacts": deepcopy(retained),
                "binding": observation_binding,
                "expires_monotonic": deadline,
                'reader': (reader_origin,journal,bootstrap_authority,deepcopy(reader_binding)) if owned_reader else None,
            }
            # This final issuer check remains inside the terminal-withdrawal context.
            require_observed_git_base(
                observation,
                transport=transport,
                **scope,
                expected_config_sha256=expected_config_sha256,
                deadline=deadline,
            )
            audit()
    return report, observation
