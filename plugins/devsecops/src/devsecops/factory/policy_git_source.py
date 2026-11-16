"""Direct, bounded policy source changes from F0 and accepted native correction.

This source capability is separate from an image publication proposal. Its image
identity is historical evidence, and it cannot grant a new publication or write.
"""

from copy import deepcopy
from datetime import datetime, timezone
import difflib
import os
from pathlib import Path
import shutil
import threading
from weakref import WeakKeyDictionary

import yaml

from devsecops import correction, render
from devsecops.engine import _schema_receipt
from devsecops.profiles import load_profile, relative_path
from devsecops.resources import strict_load_yaml
from devsecops.snapshot import _write_files
from devsecops.adapters.artifacts import _open_directory
from .git_transport_process import run
from .state import StateError, _canonical, _exclusive, _json, _read, _sha

_SOURCES = WeakKeyDictionary()


class PolicyGitSource:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Policy source requires original F0 or accepted native correction")

    def __reduce__(self):
        raise TypeError("Policy source cannot be reconstructed from documents")

    def describe(self):
        record = _SOURCES.get(self)
        if record is None:
            raise StateError("Unissued policy source")
        return require_policy_source(self, **record["scope"])


def _audit(record):
    ops, context = record["operations"], record["context"]
    if record["pid"] != os.getpid() or record["thread"] != threading.get_ident():
        raise StateError("Policy source belongs to its original process")
    if ops._admit(context, context.phase["operation"]) != record["operation"]:
        raise StateError("Policy source operation changed")
    if datetime.now(timezone.utc) >= record["expires"]:
        raise StateError("Policy source action window expired")
    from .owned_git_delivery import require_delivery

    if require_delivery(record["previous"], journal=ops.journal) != record["previous_binding"]:
        raise StateError("Policy predecessor delivery changed")
    if ops.source_checkout(*record["previous_key"]) != record["checkout"]:
        raise StateError("Policy predecessor source changed")
    selected = ops.runtime.qualified_policy_plan(context)
    if selected != record["qualification"]:
        raise StateError("Original qualified policy source selection changed")
    if record.get("native") is not None:
        from .correction_phase import require_validated_correction

        current = require_validated_correction(
            ops.bootstrap.driver, context, attempt=context.phase["attempt"]
        )
        if current != record["native"]:
            raise StateError("Original accepted native correction changed")
    for path, ref in record["artifacts"].items():
        raw = _read(path, max(64_000_000, ref["bytes"] + 1))
        if _sha(raw) != ref["sha256"] or len(raw) != ref["bytes"]:
            raise StateError("Policy source input or output bytes changed")
    if record.get("schemas") and _schema_receipt(record["schemas"])["sha256"] != record["schemas_sha256"]:
        raise StateError("Policy schemas changed")
    if context.remaining_seconds() <= 0:
        raise StateError("Policy source deadline expired")


def require_policy_source(value, **scope):
    record = _SOURCES.get(value) if type(value) is PolicyGitSource else None
    if record is None or record["scope"] != scope:
        raise StateError("Original exact policy source scope required")
    _audit(record)
    return deepcopy(record["binding"])


def _document(raw):
    docs = strict_load_yaml(raw.decode("utf-8"))
    if len(docs) != 1 or type(docs[0]) is not dict:
        raise StateError("Policy source must be one exact JSON/YAML mapping")
    return docs[0]


def _replace_pointer(value, pointer, before, after):
    import re

    if (
        not isinstance(pointer, str)
        or not pointer.startswith("/")
        or len(pointer) > 400
        or re.search(r"~(?![01])", pointer)
    ):
        raise StateError("Explicit bounded source JSON pointer required")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    current = value
    for part in parts[:-1]:
        if type(current) is not dict or part not in current:
            raise StateError("Selected policy source pointer is absent")
        current = current[part]
    if (
        type(current) is not dict
        or current.get(parts[-1]) != before
        or type(current.get(parts[-1])) is not type(before)
    ):
        raise StateError("Selected source scalar differs from qualified baseline")
    current[parts[-1]] = after


def _native(record, git, git_sha, root, repo, *args, data=None, cap=2_000_000):
    _audit(record)
    executable = os.open(git, os.O_RDONLY | os.O_NOFOLLOW)
    cwd = _open_directory(repo)
    scratch = _open_directory(root)
    try:
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(root),
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
        code, out, _, _ = run(
            [
                str(git),
                "-c",
                "protocol.allow=never",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.attributesFile=/dev/null",
                *args,
            ],
            cwd_fd=cwd,
            executable_fd=executable,
            executable_sha256=git_sha,
            scratch_fd=scratch,
            timeout=min(30, record["context"].remaining_seconds()),
            limit=cap,
            env=env,
            stdin_bytes=data,
        )
        if code:
            raise StateError("Exact bounded policy Git operation failed")
        _audit(record)
        return out
    finally:
        os.close(scratch)
        os.close(cwd)
        os.close(executable)


def _render(record, repository, revision, source_path, after, native, output, plan, *, reset):
    profile_path = Path(native["profile"]["path"])
    profile, profile_sha = load_profile(profile_path, repository, None, base_sha=revision)
    if profile_sha != native["profile"]["sha256"]:
        raise StateError("Selected policy profile changed")
    # The canonical production source is a bounded Helm chart. Other renderers
    # require their own bounded producer; do not silently weaken this path.
    if any(target["type"] != "helm" for target in profile["targets"]):
        raise StateError("Policy delivery requires the explicit bounded Helm profile")
    roots = sorted(
        {path for target in profile["targets"] for path in [target["path"], *target.get("values", [])]}
    )
    before_root = output / "render-base"
    after_root = output / "render-candidate"
    before_root.mkdir(mode=0o700)
    after_root.mkdir(mode=0o700)
    raw_tree = _native(
        record,
        record["git_path"],
        record["git_sha256"],
        output,
        repository,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        revision,
        "--",
        *roots,
        cap=2_000_000,
    )
    files = {}
    for entry in raw_tree.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        name = relative_path(name.decode())
        if mode not in ("100644", "100755") or kind != "blob" or name in files or len(files) >= 5000:
            raise StateError("Selected source tree has unsupported or duplicate entries")
        files[name] = _native(
            record,
            record["git_path"],
            record["git_sha256"],
            output,
            repository,
            "cat-file",
            "blob",
            oid,
            cap=min(2_000_000, 64_000_000 - sum(map(len, files.values()))),
        )
        if sum(map(len, files.values())) > 64_000_000:
            raise StateError("Selected source tree exceeds its bound")
    snapshot = _write_files(files, before_root, deadline=record["context"].deadline)
    snapshot["revision"] = revision
    if source_path not in {row["path"] for row in snapshot["files"]}:
        raise StateError("Policy source is outside the exact selected profile")
    shutil.copytree(before_root, after_root, dirs_exist_ok=True)
    (after_root / source_path).write_bytes(after)
    checker = correction.ScopeValidator([{"resource": plan.resource_id, "pointers": [plan.pointer]}])
    capture = correction._RenderCapture(checker, [target["name"] for target in profile["targets"]])
    schemas = Path(native["schemas"]["path"])
    if _schema_receipt(schemas)["sha256"] != native["schemas"]["sha256"]:
        raise StateError("Selected policy schemas changed")
    record.update(schemas=schemas, schemas_sha256=native["schemas"]["sha256"])
    for pin in (native["profile"], *native["tools"].values()):
        path = Path(pin["path"])
        raw = _read(path, 200_000_000)
        if _sha(raw) != pin["sha256"]:
            raise StateError("Selected policy tool or profile changed")
        record["artifacts"][path] = {"sha256": pin["sha256"], "bytes": len(raw)}
    for target in profile["targets"]:
        pair = []
        for side, tree in (("base", before_root), ("candidate", after_root)):
            _audit(record)
            scratch = output / (target["name"] + "-" + side)
            scratch.mkdir(mode=0o700)
            rendered = render.collect(
                tree,
                target,
                Path(native["tools"]["helm"]["path"]),
                scratch,
                deadline=record["context"].deadline,
            )
            if not rendered["present"] or rendered["gaps"]:
                raise StateError("Policy rendering did not cover every selected target")
            checked = render.validate(
                rendered["records"],
                target,
                Path(native["tools"]["kubeconform"]["path"]),
                schemas,
                scratch,
                deadline=record["context"].deadline,
            )
            if checked["decision"] != "pass" or checked["gaps"]:
                raise StateError("Policy source schema validation is incomplete")
            pair.append(rendered["records"])
            _audit(record)
        if capture(target, *pair)["decision"] != "pass":
            raise StateError("Policy change exceeds its exact qualified rendered selector")
    artifacts = capture.artifacts()
    if not reset and not checker.changed:
        raise StateError("Policy change did not alter the qualified selector")
    phase = record["context"].phase["operation"]
    expected_before = plan.fault if phase == "deliver" else plan.baseline
    expected_after = plan.fault if phase == "inject" else plan.baseline
    for name, expected in (
        ("rendered-before.json", expected_before),
        ("rendered-after.json", expected_after),
    ):
        rows = [r for r in _json(artifacts[name])["resources"] if r["id"] == plan.resource_id]
        if len(rows) != 1 or rows[0]["body"] != _json(expected):
            raise StateError("Rendered policy differs from the actual qualified experiment")
    final_root = output / "selected-tree"
    final_root.mkdir(mode=0o700)
    files[source_path] = after
    candidate = _write_files(files, final_root, deadline=record["context"].deadline)
    candidate.update(
        revision="WORKTREE", excluded="Ignored and unselected files are not part of this snapshot"
    )
    return artifacts, snapshot, candidate


def prepare_policy_source(context, *, operations, output):
    from .owned_operations import OwnedOperationCoordinator
    from .owned_git_delivery import require_delivery, _record
    from .deployment_commit import commit_configuration

    if (
        type(operations) is not OwnedOperationCoordinator
        or operations.journal is not operations.bootstrap.driver.journal
    ):
        raise StateError("Concrete same-journal policy operation coordinator required")
    phase = context.phase["operation"]
    attempt = context.phase.get("attempt")
    if phase not in ("inject", "deliver", "reset") or (
        attempt not in ("primary", "replay") and not (phase == "reset" and attempt == "shared")
    ):
        raise StateError("Fixed incident policy phase required")
    operation = operations._admit(context, phase)
    if "deliver" not in context.inputs["config"]["allowed_mutations"]:
        raise StateError("Policy change requires its explicit delivery grant")
    key = (
        (attempt, "baseline" if phase == "inject" else "fault")
        if phase != "reset"
        else ("primary", "recovery")
    )
    previous = operations.delivery(*key)
    binding = require_delivery(previous, journal=operations.journal)
    checkout = operations.source_checkout(*key)
    if checkout["revision"] != binding["deployment_commit"]:
        raise StateError("Actual policy source checkout differs from observed delivery")
    qualification = operations.runtime.qualified_policy_plan(context)
    plan = qualification["plan"]
    source_path = relative_path(qualification["source_path"])
    if (
        qualification["source_kind"] not in ("helm-values", "manifest")
        or qualification["render_resource_id"] != plan.resource_id
    ):
        raise StateError("Explicit original policy source mapping required")
    expires = datetime.fromisoformat(context.inputs["config"]["expires_at"].replace("Z", "+00:00"))
    record = dict(
        operations=operations,
        context=context,
        operation=operation,
        pid=os.getpid(),
        thread=threading.get_ident(),
        previous=previous,
        previous_binding=binding,
        previous_key=key,
        checkout=checkout,
        qualification=qualification,
        artifacts={},
        expires=expires,
        native=None,
    )
    _audit(record)
    output.mkdir(mode=0o700)
    repository = Path(checkout["repository"])
    revision = checkout["revision"]
    _, _, commit_raw = operations.selected_component("commit")
    config = commit_configuration(commit_raw)
    git = Path(config["git_path"])
    git_sha = config["git_sha256"]
    record.update(git_path=git, git_sha256=git_sha)
    before = _native(
        record, git, git_sha, output, repository, "show", revision + ":" + source_path, cap=64000
    )
    if _read(repository / source_path, 64000) != before:
        raise StateError("Materialized policy worktree differs from original Git object")
    if phase == "inject":
        candidate = _document(before)
        _replace_pointer(candidate, qualification["source_pointer"], plan.before, plan.after)
        after = yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True).encode()
    elif phase == "deliver":
        from .correction_phase import require_validated_correction

        accepted = require_validated_correction(operations.bootstrap.driver, context, attempt=attempt)
        if accepted["base_commit"] != revision or accepted["execution_mode"] != "operator":
            raise StateError("Native correction does not bind the actual delivered fault")
        report = _json(_read(accepted["artifacts"]["correction"][0], 16_000_000))
        if report["changed_files"] != [source_path]:
            raise StateError("Native correction must change exactly the selected policy source")
        record["native"] = accepted
        patch = _read(accepted["artifacts"]["patch"][0], 256000)
        private = output / "patch-source"
        private.mkdir(mode=0o700)
        target = private / source_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(before)
        _native(record, git, git_sha, output, private, "init", "--quiet", "--template=")
        patch_path = output / "native.patch"
        _exclusive(patch_path, patch)
        _native(
            record,
            git,
            git_sha,
            output,
            private,
            "apply",
            "--check",
            "--whitespace=error",
            "--",
            str(patch_path),
        )
        _native(record, git, git_sha, output, private, "apply", "--whitespace=error", "--", str(patch_path))
        after = _read(target, 64000)
        expected = [row for row in report["patched_tree"]["files"] if row["path"] == source_path]
        if len(expected) != 1 or _sha(after) != expected[0]["sha256"]:
            raise StateError("Applied native correction differs from its validated source bytes")
    else:
        baseline = operations.delivery("primary", "baseline")
        baseline_binding = require_delivery(baseline, journal=operations.journal)
        changed = _native(
            record,
            git,
            git_sha,
            output,
            repository,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-z",
            "-r",
            baseline_binding["deployment_commit"],
            revision,
        ).split(b"\0")
        if set(changed) - {b"", source_path.encode()}:
            raise StateError("Reset would alter unrelated Git source paths")
        after = _native(
            record,
            git,
            git_sha,
            output,
            repository,
            "show",
            baseline_binding["deployment_commit"] + ":" + source_path,
            cap=64000,
        )
    source_changes = correction._pointers(_document(before), _document(after))
    if source_changes != [qualification["source_pointer"]] and not (
        phase == "reset" and source_changes == []
    ):
        raise StateError("Policy source changes more than its exact selected scalar")
    patch = "".join(
        difflib.unified_diff(
            before.decode().splitlines(True),
            after.decode().splitlines(True),
            fromfile="a/" + source_path,
            tofile="b/" + source_path,
        )
    ).encode()
    rendered, snapshot, candidate_tree = _render(
        record,
        repository,
        revision,
        source_path,
        after,
        operations._recipe()["native"],
        output,
        plan,
        reset=phase == "reset",
    )
    if record["native"] is not None:
        if candidate_tree != report["patched_tree"]:
            raise StateError("Prepared selected tree differs from validated native correction")
        # Retain and compare the independent original validator's render bytes.
        for name in ("rendered-before.json", "rendered-after.json"):
            source_name = name.removesuffix(".json").replace("-", "_")
            selected = record["native"]["artifacts"].get(source_name) or record["native"]["artifacts"].get(
                name.removesuffix(".json")
            )
            if selected is None or _read(selected[0], 16_000_000) != rendered[name]:
                raise StateError("Policy rendering differs from accepted native correction")
    for name, raw in {
        "proposal.patch": patch,
        "values-before.yaml": before,
        "values-after.yaml": after,
        **rendered,
    }.items():
        _exclusive(output / name, raw)
        record["artifacts"][output / name] = {"sha256": _sha(raw), "bytes": len(raw)}
    original = _record(previous, operations.journal)
    historical = _json(_read(original["output"] / "historical-source.json", 16_000_000))["proposal"]
    config_body = {
        "kind": "qualified-policy-source",
        "run_id": context.run_id,
        "operation_id": operation,
        "phase": context.phase["id"],
        "source_path": source_path,
        "source_pointer": qualification["source_pointer"],
        "qualification_sha256": qualification["qualification"]["qualification_sha256"],
        "previous_commit": revision,
        "patch_sha256": _sha(patch),
        "native_request_sha256": record["native"]["request_sha256"] if record["native"] else None,
    }
    _exclusive(output / "policy-source.json", _canonical(config_body))
    proposal_sha = _sha(_canonical(config_body))
    record["artifacts"][output / "policy-source.json"] = {
        "sha256": proposal_sha,
        "bytes": len(_canonical(config_body)),
    }

    def ref(name):
        return dict(record["artifacts"][output / name])

    selected = {
        "run_scope": operations.journal.identities,
        "source_commit": binding["source_commit"],
        "origin": historical["origin"],
        "repository": historical["repository"],
        "manifest_digest": historical["manifest_digest"],
        "deployment_repository": str(repository),
        "deployment_commit": revision,
        "configuration_sha256": proposal_sha,
        "published_evidence": historical["published_evidence"],
        "expires_at": expires.isoformat(),
        "patch": {"path": str(output / "proposal.patch"), **ref("proposal.patch")},
        "values_path": source_path,
        "before": ref("values-before.yaml"),
        "after": ref("values-after.yaml"),
        "rendered_before": ref("rendered-before.json"),
        "rendered_after": ref("rendered-after.json"),
        "source_snapshot_sha256": snapshot["sha256"],
        "selected_tree": candidate_tree,
        "change_required": before != after,
        "allow_empty_reset": phase == "reset" and before == after,
        "source_kind": "qualified-policy-change",
        "source_provenance": config_body,
        "scope": "local-policy-source-only",
    }
    scope = {
        key: selected[key]
        for key in (
            "run_scope",
            "source_commit",
            "origin",
            "repository",
            "manifest_digest",
            "deployment_repository",
            "deployment_commit",
            "configuration_sha256",
        )
    }
    record.update(binding=selected, scope=scope)
    _audit(record)
    value = object.__new__(PolicyGitSource)
    _SOURCES[value] = record
    return value
