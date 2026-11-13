"""Fixed, one-shot GitLab API pipeline with retained authenticated job origin.

The canonical coordinator supplies the exact observed owned project and runner.
This component does not infer successful execution from environment variables or
artifact verdicts. Unknown dispatches cannot be retried with a new object.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import time
from weakref import WeakKeyDictionary
from uuid import uuid4

from devsecops.adapters.artifacts import _open_directory
from devsecops.adapters.gitlab_pipeline import _job, _pipeline
from devsecops.runtime.cli import _verify_output_path, _write
from devsecops.runtime.context import canonical_bytes
from .gitlab_api import GitLabSession
from .gitlab_bootstrap import BootstrapScope, _project_identity

_ORIGINS = WeakKeyDictionary()
_HISTORY = WeakKeyDictionary()


class CIPipelineError(ValueError):
    pass


@dataclass(frozen=True)
class PipelineSelection:
    project_id: int
    source_sha: str
    runner_id: int
    runner_manager_system_identity: str
    required_jobs: tuple[str, ...]
    publication_job: str
    operation_id: str
    run_id: str

    def validate(self):
        if (
            any(
                type(value) is not int or not 0 < value < 2**63 for value in (self.project_id, self.runner_id)
            )
            or not isinstance(self.runner_manager_system_identity, str)
            or not re.fullmatch("[a-zA-Z0-9_-]{1,128}", self.runner_manager_system_identity)
            or not re.fullmatch("[a-f0-9]{40}", self.source_sha)
            or type(self.required_jobs) is not tuple
            or not 1 <= len(self.required_jobs) <= 16
            or len(set(self.required_jobs)) != len(self.required_jobs)
            or any(not re.fullmatch("[a-z][a-z0-9_]{0,63}", name) for name in self.required_jobs)
            or self.publication_job != "publish_and_prepare_mission_git"
            or self.publication_job not in self.required_jobs
            or any(
                not isinstance(value, str) or not re.fullmatch("[a-zA-Z0-9_-]{1,128}", value)
                for value in (self.operation_id, self.run_id)
            )
        ):
            raise CIPipelineError("invalid-pipeline-selection")


class AuthenticatedCIOrigin:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("CI origin requires direct authenticated pipeline observation")

    def __reduce__(self):
        raise TypeError("CI origin cannot be serialized")

    def describe(self):
        return require_ci_origin(self)


class HistoricalCIReadback:
    """Fresh read of the original successful job; grants no CI mutation."""

    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Historical CI readback requires the original direct workflow")

    def __reduce__(self):
        raise TypeError("Historical CI readback cannot be serialized")


def require_ci_history(value, *, raw=None):
    record = _HISTORY.get(value) if type(value) is HistoricalCIReadback else None
    if record is None or record["pid"] != os.getpid() or time.monotonic() >= record["deadline"]:
        raise CIPipelineError("fresh-historical-ci-readback-required")
    record["workflow"]._audit_retained(record["deadline"])
    from .mission_security import _read_retained

    _read_retained(record["workflow"]._fd, record["receipt_name"], record["receipt_ref"], record["deadline"])
    if raw is not None and (
        type(raw) is not bytes or hashlib.sha256(raw).hexdigest() != record["artifact_sha256"]
    ):
        raise CIPipelineError("historical-job-artifact-changed")
    return deepcopy(record["binding"])


def refresh_ci_history(origin, *, timeout=120, service_route=None):
    """Reobserve exact original job/artifact without renewing any mutation cap."""
    old = _ORIGINS.get(origin) if type(origin) is AuthenticatedCIOrigin else None
    if (
        old is None
        or old["pid"] != os.getpid()
        or "artifact" not in old
        or type(timeout) not in (int, float)
        or not 2 <= timeout <= 180
    ):
        raise CIPipelineError("original-authenticated-artifact-required")
    flow, binding, deadline = old["workflow"], old["binding"], time.monotonic() + timeout
    flow._audit_retained(deadline)
    session = flow.session.bounded_read_session(
        **({} if service_route is None else {"service_route": service_route})
    )
    session.verify_identity()
    _project_identity(
        session.request("GET", f"/api/v4/projects/{flow.selection.project_id}"),
        flow.scope,
        "source",
        flow.selection.project_id,
    )
    prefix = f"/api/v4/projects/{flow.selection.project_id}/pipelines/{flow._pipeline}"
    first = flow._pipe(session.request("GET", prefix))
    if first["status"] != "success":
        raise CIPipelineError("historical-pipeline-no-longer-successful")
    rows = session.get_collection(prefix + "/jobs?include_retried=false")
    jobs = flow._qualified_jobs(rows)
    if jobs != binding["jobs"] or session.get_collection(prefix + "/bridges"):
        raise CIPipelineError("historical-original-jobs-changed")
    job = jobs[binding["publication_job"]]["id"]
    raw = session.request_artifact(flow.selection.project_id, job, "mission-ci-publication.json")
    if old["artifact"] != {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}:
        raise CIPipelineError("historical-original-artifact-changed")
    repeated = session.get_collection(prefix + "/jobs?include_retried=false")
    final = flow._pipe(session.request("GET", prefix))
    if rows != repeated or first != final:
        raise CIPipelineError("historical-pipeline-changed-during-read")
    session.verify_identity()
    _project_identity(
        session.request("GET", f"/api/v4/projects/{flow.selection.project_id}"),
        flow.scope,
        "source",
        flow.selection.project_id,
    )
    flow._audit_retained(deadline)
    name = "history-" + uuid4().hex + ".json"
    receipt = {
        "kind": "authenticated-original-ci-history-readback",
        "run_id": binding["run_id"],
        "operation_id": binding["operation_id"],
        "original_ci": deepcopy(binding),
        "pipeline": final,
        "jobs": deepcopy(jobs),
        "artifact": deepcopy(old["artifact"]),
        "first_jobs_sha256": hashlib.sha256(canonical_bytes(rows)).hexdigest(),
        "final_jobs_sha256": hashlib.sha256(canonical_bytes(repeated)).hexdigest(),
        "transport": session.receipts,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "authority": "runtime-history-readback-only",
        "writes_performed": False,
    }
    if len(flow._history_refs) >= 32:
        raise CIPipelineError("historical-readback-limit")
    ref = _write(flow._fd, name, receipt)
    flow._history_refs[name] = ref
    os.fsync(flow._fd)
    flow._audit_retained(deadline)
    result = object.__new__(HistoricalCIReadback)
    _HISTORY[result] = {
        "pid": os.getpid(),
        "deadline": time.monotonic() + 900,
        "workflow": flow,
        "receipt_name": name,
        "receipt_ref": ref,
        "artifact_sha256": old["artifact"]["sha256"],
        "binding": {
            **deepcopy(binding),
            "historical_observed_at": binding["observed_at"],
            "history_readback": {"path": str(flow.path / name), **ref},
            "observed_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    require_ci_history(result, raw=raw)
    return result, raw


def require_ci_origin(value, *, run_id=None, operation_id=None, source_sha=None):
    record = _ORIGINS.get(value) if type(value) is AuthenticatedCIOrigin else None
    if (
        record is None
        or record["pid"] != os.getpid()
        or time.monotonic() >= record["deadline"]
        or any(
            expected is not None and record["binding"][key] != expected
            for key, expected in (
                ("run_id", run_id),
                ("operation_id", operation_id),
                ("source_sha", source_sha),
            )
        )
    ):
        raise CIPipelineError("current-authenticated-ci-origin-required")
    record["workflow"]._audit()
    return deepcopy(record["binding"])


def require_ci_artifact(value, raw):
    """Bind the exact downloaded bytes to this direct successful job origin."""
    binding = require_ci_origin(value)
    record = _ORIGINS[value]
    if type(raw) is not bytes or record.get("artifact") != {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }:
        raise CIPipelineError("direct-authenticated-job-artifact-required")
    return binding


class OwnedCIPipeline:
    """One fixed API pipeline. Its exclusive directory is never a resume token."""

    def __init__(self, session, scope, selection, *, journal_root, deadline, runner_origin=None):
        if (
            type(session) is not GitLabSession
            or type(scope) is not BootstrapScope
            or type(selection) is not PipelineSelection
        ):
            raise CIPipelineError("direct-session-and-selection-required")
        scope.validate()
        selection.validate()
        if (
            session.origin != scope.origin
            or session.expected_user_id != scope.owner_user_id
            or not isinstance(journal_root, Path)
            or not journal_root.is_absolute()
            or ".." in journal_root.parts
            or not time.monotonic() < deadline <= time.monotonic() + 3600
        ):
            raise CIPipelineError("pipeline-scope-mismatch")
        self._runner_origin = runner_origin
        if runner_origin is not None:
            from .owned_runner_service import require_runner_execution

            require_runner_execution(
                runner_origin,
                run_id=selection.run_id,
                runner_id=selection.runner_id,
                manager=selection.runner_manager_system_identity,
                current=True,
            )
        self.session, self.scope, self.selection = session, scope, selection
        self.deadline, self._pid, self._fd, self._pipeline, self._state = (
            deadline,
            os.getpid(),
            None,
            None,
            "new",
        )
        # Stable external target + exact commit, independent of caller labels.
        key = hashlib.sha256(
            canonical_bytes([scope.origin, selection.project_id, selection.source_sha])
        ).hexdigest()
        self.path = journal_root / ("ci-" + key)
        root = _open_directory(journal_root)
        try:
            info = os.fstat(root)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise CIPipelineError("pipeline-root-ownership")
            try:
                os.mkdir(self.path.name, mode=0o700, dir_fd=root)
            except FileExistsError:
                raise CIPipelineError("existing-pipeline-attempt-reconcile-only") from None
            os.fsync(root)
            self._fd = os.open(self.path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        finally:
            os.close(root)
        self._refs = {}
        self._history_refs = {}
        self._poll_sequence = 0
        try:
            self._event("selection", {"scope": asdict(scope), "selection": asdict(selection)})
            if self._runner_origin is not None:
                self._event("runner-origin", self._audit_runner(current=True))
        except BaseException:
            self.close()
            raise

    def _audit(self):
        if self._pid != os.getpid() or self._fd is None or time.monotonic() >= self.deadline:
            raise CIPipelineError("pipeline-expired-or-foreign")
        self._audit_runner(current=True)
        self._audit_retained(self.deadline)

    def _audit_runner(self, *, current=False):
        if self._runner_origin is not None:
            from .owned_runner_service import require_runner_execution

            return require_runner_execution(
                self._runner_origin,
                run_id=self.selection.run_id,
                runner_id=self.selection.runner_id,
                manager=self.selection.runner_manager_system_identity,
                current=current,
            )

    def _audit_retained(self, deadline):
        # Historical readback can audit old bytes under its own read deadline;
        # this never changes the original workflow deadline or its authority.
        if self._pid != os.getpid() or self._fd is None or time.monotonic() >= deadline:
            raise CIPipelineError("original-pipeline-retention-required")
        self._audit_runner()
        _verify_output_path(self.path, self._fd)
        from .mission_security import _read_retained

        for name, ref in {**self._refs, **self._history_refs}.items():
            _read_retained(self._fd, name, ref, deadline)

    def evidence_artifacts(self, *, run_id):
        """One immutable bundle of exact original public event and history bytes.

        This is an export of retained evidence, not a renewed CI capability.
        Unregistered files never enter the bundle.
        """
        if run_id != self.selection.run_id:
            raise CIPipelineError("foreign-pipeline-evidence")
        deadline = time.monotonic() + 30
        self._audit_retained(deadline)
        refs = {**self._refs, **self._history_refs}
        if len(refs) > 170 or sum(ref["bytes"] for ref in refs.values()) > 8_000_000:
            raise CIPipelineError("pipeline-evidence-limit")
        from .mission_security import _read_retained

        documents = []
        for name, ref in refs.items():
            raw = _read_retained(self._fd, name, ref, deadline, materialize=True)
            documents.append(
                {"label": name, "path": str(self.path / name), **ref, "utf8": raw.decode("utf-8")}
            )
        body = {
            "schema_version": 1,
            "kind": "original-ci-evidence-bundle",
            "run_id": run_id,
            "operation_id": self.selection.operation_id,
            "documents": documents,
            "authority": "historical-evidence-only",
        }
        raw = canonical_bytes(body)
        if len(raw) > 16_000_000:
            raise CIPipelineError("pipeline-evidence-bundle-limit")
        digest = hashlib.sha256(raw).hexdigest()
        name = "evidence-" + digest + ".json"
        known = getattr(self, "_bundles", {}).get(name)
        if known is None:
            ref = _write(self._fd, name, raw.decode("utf-8"))
            os.fsync(self._fd)
            if not hasattr(self, "_bundles"):
                self._bundles = {}
            self._bundles[name] = ref
        else:
            ref = known
        _read_retained(self._fd, name, ref, deadline)
        self._audit_retained(deadline)
        return (self.path / name,)

    def _event(self, name, value):
        self._audit()
        self._refs[name + ".json"] = _write(self._fd, name + ".json", value)
        os.fsync(self._fd)
        self._audit()

    def _project(self):
        self._audit()
        if self._state != "new":
            # Polling renews only the original frozen read principal. The new
            # bounded transport is permanently write-blocked, including after
            # an earlier uncertain write. The workflow never dispatches twice.
            if self.session._deadline - time.monotonic() < 30 or self.session._requests > 450:
                self.session = self.session.bounded_read_session()
        self.session.verify_identity()
        value = self.session.request("GET", f"/api/v4/projects/{self.selection.project_id}")
        _project_identity(value, self.scope, "source", self.selection.project_id)
        branch = self.session.request(
            "GET", f"/api/v4/projects/{self.selection.project_id}/repository/branches/main"
        )
        if (
            type(branch) is not dict
            or branch.get("name") != "main"
            or branch.get("commit", {}).get("id") != self.selection.source_sha
        ):
            raise CIPipelineError("source-branch-changed")
        self._audit()

    def _pipe(self, value):
        selected = self.selection
        if type(value) is not dict or type(value.get("id")) is not int:
            raise CIPipelineError("pipeline-identity")
        actual = _pipeline(value, selected.project_id, self._pipeline or value["id"], selected.source_sha)
        if (
            value.get("source") != "api"
            or value.get("ref") != "main"
            or value.get("tag") is not False
            or value.get("yaml_errors") is not None
        ):
            raise CIPipelineError("pipeline-kind")
        return actual

    def start(self):
        if self._state != "new":
            raise CIPipelineError("pipeline-already-attempted")
        self._project()
        self._event(
            "create-intent",
            {
                "project_id": self.selection.project_id,
                "source_sha": self.selection.source_sha,
                "operation_id": self.selection.operation_id,
                "payload": {"ref": "main"},
                "effect": "unknown",
            },
        )
        self._state = "unknown"
        try:
            value = self.session.request(
                "POST",
                f"/api/v4/projects/{self.selection.project_id}/pipeline",
                payload={"ref": "main"},
                expected=(201,),
            )
            self._event("create-response", value)
            observed = self._pipe(value)
            self._pipeline = observed["id"]
            self._project()
            self._event("created", observed)
            self._state = "created"
            return deepcopy(observed)
        except BaseException:
            self.session.stop_writes()
            raise

    def observe(self):
        if self._state not in ("created", "running", "success") or self._pipeline is None:
            raise CIPipelineError("current-created-pipeline-required")
        if self._poll_sequence >= 128:
            raise CIPipelineError("pipeline-observation-limit")
        self._project()
        prefix = f"/api/v4/projects/{self.selection.project_id}/pipelines/{self._pipeline}"
        first = self._pipe(self.session.request("GET", prefix))
        self._poll_sequence += 1
        self._event(
            "poll-" + str(self._poll_sequence).zfill(4),
            {
                "pipeline": first,
                "transport": self.session.receipts[-1],
                "observed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        if first["status"] != "success":
            self._state = (
                "terminal" if first["status"] in ("failed", "canceled", "skipped", "manual") else "running"
            )
            return {"state": first["status"], "origin": None}
        rows = self.session.get_collection(prefix + "/jobs?include_retried=false")
        jobs = self._qualified_jobs(rows)
        if self.session.get_collection(prefix + "/bridges"):
            raise CIPipelineError("missing-job-or-downstream-pipeline")
        repeated = self.session.get_collection(prefix + "/jobs?include_retried=false")
        if sorted(repeated, key=lambda row: row.get("id", -1)) != sorted(
            rows, key=lambda row: row.get("id", -1)
        ):
            raise CIPipelineError("jobs-changed-during-observation")
        if self._state == "success" and self._success_jobs != jobs:
            raise CIPipelineError("original-successful-jobs-changed")
        final = self._pipe(self.session.request("GET", prefix))
        if first != final:
            raise CIPipelineError("pipeline-changed")
        self._project()
        binding = {
            "run_id": self.selection.run_id,
            "operation_id": self.selection.operation_id,
            "origin": self.session.origin,
            "project_id": self.selection.project_id,
            "source_sha": self.selection.source_sha,
            "pipeline_id": self._pipeline,
            "runner_id": self.selection.runner_id,
            "runner_manager_system_identity": self.selection.runner_manager_system_identity,
            "jobs": jobs,
            "publication_job": self.selection.publication_job,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        if self._state != "success":
            self._event("observed-success", binding)
        self._state = "success"
        self._success_jobs = deepcopy(jobs)
        origin = object.__new__(AuthenticatedCIOrigin)
        _ORIGINS[origin] = {
            "pid": os.getpid(),
            "deadline": self.deadline,
            "binding": binding,
            "workflow": self,
        }
        return {"state": "success", "origin": origin}

    def _qualified_jobs(self, rows):
        from devsecops.adapters.vulnerability_policy import timestamp

        jobs, names = {}, set()
        for row in rows:
            result = _job(row, self.selection.project_id, self._pipeline, self.selection.source_sha)
            name = row["name"]
            if name in names or result["id"] in {item["id"] for item in jobs.values()}:
                raise CIPipelineError("duplicate-job")
            names.add(name)
            if (
                result["status"] != "success"
                or result["allow_failure"] is not False
                or row.get("erased_at") is not None
                or not row.get("finished_at")
                or result["runner_id"] != self.selection.runner_id
                or result["runner_manager_id"] is None
                or row["runner_manager"].get("system_id") != self.selection.runner_manager_system_identity
            ):
                raise CIPipelineError("job-not-qualified")
            jobs[name] = result
            if (
                not timestamp(row.get("started_at"))
                <= timestamp(row["finished_at"])
                <= datetime.now(timezone.utc)
            ):
                raise CIPipelineError("job-timing-unavailable")
            result.update(started_at=row["started_at"], finished_at=row["finished_at"])
        if not set(self.selection.required_jobs) <= names:
            raise CIPipelineError("missing-job-or-downstream-pipeline")
        return jobs

    def publication_report(self, origin):
        binding = require_ci_origin(
            origin,
            run_id=self.selection.run_id,
            operation_id=self.selection.operation_id,
            source_sha=self.selection.source_sha,
        )
        if _ORIGINS[origin]["workflow"] is not self:
            raise CIPipelineError("foreign-pipeline-origin")
        job = binding["jobs"][binding["publication_job"]]["id"]
        # A bounded single-job artifact is a selector, not publication authority.
        raw = self.session.request_artifact(self.selection.project_id, job, "mission-ci-publication.json")
        latest = self.observe()
        if latest["state"] != "success" or latest["origin"].describe()["jobs"] != binding["jobs"]:
            raise CIPipelineError("pipeline-changed-during-artifact-read")
        self._audit()
        _ORIGINS[origin]["artifact"] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        return raw

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
