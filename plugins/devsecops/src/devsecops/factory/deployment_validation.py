"""Fixed offline validation of the actual owned deployment MR checkout.

Only the installed chart, selected values, pinned tools and packaged local
schemas are read. Candidate repository scripts, hooks, plugins and downloads
are never part of this job. Its report is evidence, not delivery authority.
"""

from __future__ import annotations

from pathlib import Path
import platform
import re
import tempfile
import time

from devsecops.adapters.signatures import _snapshot
from devsecops.adapters.vulnerability_policy import checkpoint
from devsecops.factory.deployment_proposal import CHART, CHART_FILES, proposal_configuration, _tree_guard
from devsecops.factory.runner_package import installed_deployment_package as _installed
from devsecops.factory.mission_git_invocation import load_invocation
from devsecops.factory.state import _json, _read, _sha
from devsecops.profiles import relative_path
from devsecops.render import collect, local_schema_only, tool_identity, validate
from devsecops.runtime.context import canonical_bytes, seal
from devsecops.schema_catalog import select_schema_set
from devsecops.snapshot import freeze_commit, git, resolve_ref

ROOT = Path(__file__).resolve().parents[3]
RECIPE = ROOT / "demo/software-factory/ci/gitlab-owned-deployment-v1.yml"
JOB = "validate_owned_deployment"


def validate_checkout(*, invocation, invocation_sha256, repository, revision):
    selected = load_invocation(invocation, invocation_sha256)
    deadline = time.monotonic() + min(selected["timeout"], 600)
    if (
        not isinstance(repository, Path)
        or not repository.is_absolute()
        or ".." in repository.parts
        or not isinstance(revision, str)
        or not re.fullmatch("[a-f0-9]{40}", revision)
    ):
        raise ValueError("Exact current CI repository and revision required")
    _installed(
        selected["run_scope"]["package_sha256"], selected["plugin_id"], selected["plugin_version"], deadline
    )
    ref = selected["configurations"]["proposal"]
    raw = _read(Path(ref["path"]), 64000)
    if _sha(raw) != ref["sha256"]:
        raise ValueError("Original staged deployment validation configuration changed")
    configuration = proposal_configuration(raw)
    # Fresh server/project commits and job paths cannot be selected beforehand.
    # Only these source fields follow the authenticated CI checkout; no tool,
    # path within Git, image selector, namespace or schema selection is changed.
    configuration["deployment_repository"] = str(repository)
    configuration["deployment_commit"] = revision
    configuration = proposal_configuration(canonical_bytes(configuration))
    if (
        Path(git(repository, "rev-parse", "--show-toplevel", deadline=deadline).decode().strip())
        != repository
        or resolve_ref(repository, "HEAD", deadline=deadline) != revision
    ):
        raise ValueError("Actual CI checkout HEAD differs from selected MR revision")
    lock = _json(_read(ROOT / "toolchain.lock.json", 64000))
    schema_source, schema_set = select_schema_set(lock, configuration["kubernetes_version"])
    target_platform = (
        platform.system().lower()
        + "-"
        + {"aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine(), platform.machine())
    )
    with tempfile.TemporaryDirectory(prefix="owned-deployment-validation-") as directory:
        scratch = Path(directory).resolve()
        tree = scratch / "source"
        tree.mkdir(mode=0o700)
        roots = sorted({".gitlab-ci.yml", configuration["chart_path"], configuration["values_path"]})
        snapshot = freeze_commit(repository, revision, roots, tree, deadline=deadline)
        expected = {".gitlab-ci.yml", configuration["values_path"]} | {
            configuration["chart_path"] + "/" + name for name in CHART_FILES
        }
        refs = {item["path"]: {key: item[key] for key in ("sha256", "bytes")} for item in snapshot["files"]}
        if set(refs) != expected or (tree / ".gitlab-ci.yml").read_bytes() != _read(RECIPE, 64000):
            raise ValueError("Exact installed deployment CI and chart inventory required")
        for name in CHART_FILES:
            path = configuration["chart_path"] + "/" + name
            if path != configuration["values_path"] and _read(tree / path, 1_000_000) != _read(
                CHART / name, 1_000_000
            ):
                raise ValueError("Deployment candidate changed the installed chart")
        tools, tool_refs = {}, {}
        tool_root = scratch / "tools"
        tool_root.mkdir(mode=0o700)
        for name, args in [("helm", ["version", "--short"]), ("kubeconform", ["-v"])]:
            binary = tool_root / name
            actual = _snapshot(Path(configuration[name + "_path"]), binary, 200_000_000, deadline=deadline)
            if actual["sha256"] != configuration[name + "_sha256"]:
                raise ValueError("Selected deployment validation executable changed")
            binary.chmod(0o500)
            identity = tool_identity(binary, args, scratch, deadline=deadline)
            if (
                identity["sha256"] != actual["sha256"]
                or identity["version"].removeprefix("v").split("+")[0]
                != lock["tools"][target_platform][name]["version"]
            ):
                raise ValueError("Deployment validator tool differs from packaged version")
            tools[name], tool_refs[name] = binary, actual
        schemas = scratch / "schemas"
        schemas.mkdir(mode=0o700)
        schema_refs = {}
        for item in schema_set:
            name = relative_path(item["path"])
            path = schemas / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            actual = _snapshot(Path(configuration["schemas_path"]) / name, path, 8_000_000, deadline=deadline)
            if actual["sha256"] != item["sha256"]:
                raise ValueError("Selected deployment schema changed")
            local_schema_only(_json(path.read_bytes()))
            schema_refs[name] = actual
        target = {
            "name": "mission",
            "type": "helm",
            "path": configuration["chart_path"],
            "values": [configuration["values_path"]],
            "release": configuration["release"],
            "namespace": configuration["namespace"],
            "kubernetes_version": configuration["kubernetes_version"],
            "mode": "upgrade",
        }
        work = scratch / "render"
        work.mkdir(mode=0o700)
        _tree_guard(tree, refs, deadline)
        rendered = collect(tree, target, tools["helm"], work, deadline=deadline)
        if not rendered["present"] or rendered["gaps"] or not rendered["records"]:
            raise ValueError("Deployment Helm rendering is incomplete")
        checked = validate(
            rendered["records"], target, tools["kubeconform"], schemas, work, deadline=deadline
        )
        if checked["decision"] != "pass" or checked["gaps"]:
            raise ValueError("Deployment local schema validation failed")
        for root, original in ((tree, refs), (tool_root, tool_refs), (schemas, schema_refs)):
            _tree_guard(root, original, deadline)
        if (
            _read(Path(ref["path"]), 64000) != raw
            or resolve_ref(repository, "HEAD", deadline=deadline) != revision
        ):
            raise ValueError("Deployment inputs changed during validation")
        _installed(
            selected["run_scope"]["package_sha256"],
            selected["plugin_id"],
            selected["plugin_version"],
            deadline,
        )
        checkpoint(deadline)
        return seal(
            {
                "schema_version": 1,
                "kind": "owned-deployment-validation",
                "decision": "pass",
                "run_scope": selected["run_scope"],
                "source_commit": revision,
                "configuration_sha256": ref["sha256"],
                "source_snapshot_sha256": snapshot["sha256"],
                "render_sha256": rendered["render_sha256"],
                "schema_source": schema_source,
                "tool_sha256": {name: item["sha256"] for name, item in tool_refs.items()},
                "delivery_authority": "not-issued",
            },
            "validation_sha256",
        )
