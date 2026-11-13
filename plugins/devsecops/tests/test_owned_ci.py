"""Controlled sockets only: no live GitLab, CI, image publication or delivery."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import pickle
import time

import pytest

from devsecops.factory.gitlab_api import GitLabAPIError
from devsecops.factory.gitlab_bootstrap import BootstrapScope
from devsecops.factory.owned_ci import (
    AuthenticatedCIOrigin,
    CIPipelineError,
    OwnedCIPipeline,
    PipelineSelection,
    require_ci_artifact,
)
from test_gitlab_api import Response, identity, page, session, wire as _wire

wire = _wire
SHA = "a" * 40
SCOPE = BootstrapScope("https://git.example.test", "demo", "b" * 64, 17, 20, "demo-owner")


def project():
    path = "factory-demo-source"
    return {
        "id": 91,
        "path": path,
        "path_with_namespace": "demo-owner/" + path,
        "owner": {"id": 17},
        "namespace": {"id": 20, "kind": "user", "full_path": "demo-owner"},
        "description": SCOPE.marker,
        "visibility": "private",
        "default_branch": "main",
        "archived": False,
        "http_url_to_repo": SCOPE.origin + "/demo-owner/" + path + ".git",
        "shared_runners_enabled": False,
        "group_runners_enabled": False,
        "auto_devops_enabled": False,
        "only_allow_merge_if_pipeline_succeeds": True,
        "allow_merge_on_skipped_pipeline": False,
        "merge_method": "ff",
        "squash_option": "never",
        "only_allow_merge_if_all_discussions_are_resolved": True,
        "remove_source_branch_after_merge": False,
    }


def selected():
    return PipelineSelection(
        91,
        SHA,
        67,
        "s_controlled",
        ("publish_and_prepare_mission_git",),
        "publish_and_prepare_mission_git",
        "operation-1",
        "run-1",
    )


def pipe(status="success"):
    return {
        "id": 701,
        "project_id": 91,
        "sha": SHA,
        "ref": "main",
        "source": "api",
        "tag": False,
        "yaml_errors": None,
        "status": status,
    }


def jobs(wire):
    return [
        {
            "id": 901,
            "name": "publish_and_prepare_mission_git",
            "stage": "prepare",
            "status": "success",
            "allow_failure": False,
            "erased_at": None,
            "started_at": (wire.ci_finished_at - timedelta(minutes=10)).isoformat(),
            "finished_at": wire.ci_finished_at.isoformat(),
            "pipeline": pipe(),
            "commit": {"id": SHA},
            "runner": {"id": 67},
            "runner_manager": {"id": 97, "system_id": "s_controlled"},
        }
    ]


def project_reads(wire, value=None):
    identity(wire)
    wire.queue.extend([Response(value or project()), Response({"name": "main", "commit": {"id": SHA}})])


def started(wire, tmp_path):
    # One execution-time anchor for this original CI response stream, not collection time.
    wire.ci_finished_at = datetime.now(timezone.utc)
    flow = OwnedCIPipeline(
        session(), SCOPE, selected(), journal_root=tmp_path, deadline=time.monotonic() + 60
    )
    project_reads(wire)
    identity(wire)
    wire.queue.append(Response(pipe("pending"), status=201))
    project_reads(wire)
    assert flow.start()["id"] == 701
    return flow


def observed_responses(wire, rows=None, bridges=None):
    project_reads(wire)
    wire.queue.extend(
        [
            Response(pipe()),
            page(jobs(wire) if rows is None else rows),
            page(bridges or []),
            page(jobs(wire) if rows is None else rows),
            Response(pipe()),
        ]
    )
    project_reads(wire)


def test_exact_pipeline_job_and_artifact_origin(wire, tmp_path):
    flow = started(wire, tmp_path)
    observed_responses(wire)
    origin = flow.observe()["origin"]
    assert type(origin) is AuthenticatedCIOrigin
    assert origin.describe()["jobs"]["publish_and_prepare_mission_git"]["runner_manager_id"] == 97
    with pytest.raises(CIPipelineError, match="artifact"):
        require_ci_artifact(origin, b"{}")
    wire.queue.append(Response(raw=b'{"selector":true}', headers=[("Content-Type", "text/plain")]))
    observed_responses(wire)
    raw = flow.publication_report(origin)
    assert require_ci_artifact(origin, raw)["pipeline_id"] == 701
    assert not wire.queue
    with pytest.raises(CIPipelineError, match="artifact"):
        require_ci_artifact(origin, raw + b" ")
    with pytest.raises(TypeError):
        pickle.dumps(origin)
    with pytest.raises(CIPipelineError, match="already-attempted"):
        flow.start()
    assert len([row for row in wire.calls if row["method"] == "POST"]) == 1
    flow.close()
    with pytest.raises(CIPipelineError):
        origin.describe()


@pytest.mark.parametrize(
    "change",
    [
        {"runner": {"id": 1}},
        {"runner_manager": None},
        {"runner_manager": {"id": 97, "system_id": "other"}},
        {"allow_failure": True},
        {"status": "skipped"},
        {"erased_at": "2026-10-30"},
        {"pipeline": {**pipe(), "sha": "c" * 40}},
        {"commit": {"id": "c" * 40}},
    ],
)
def test_wrong_job_never_issues_origin(wire, tmp_path, change):
    flow = started(wire, tmp_path)
    rows = jobs(wire)
    rows[0].update(change)
    observed_responses(wire, rows)
    with pytest.raises(ValueError):
        flow.observe()
    flow.close()


def test_unknown_create_is_not_retried_or_resumed(wire, tmp_path):
    flow = OwnedCIPipeline(
        session(), SCOPE, selected(), journal_root=tmp_path, deadline=time.monotonic() + 60
    )
    project_reads(wire)
    identity(wire)
    wire.queue.append(OSError("synthetic disconnect"))
    with pytest.raises(GitLabAPIError):
        flow.start()
    assert flow.session.writes_blocked
    with pytest.raises(CIPipelineError):
        flow.start()
    flow.close()
    with pytest.raises(CIPipelineError, match="reconcile-only"):
        OwnedCIPipeline(
            session(),
            SCOPE,
            replace(selected(), operation_id="different"),
            journal_root=tmp_path,
            deadline=time.monotonic() + 60,
        )
    assert len([row for row in wire.calls if row["method"] == "POST"]) == 1


@pytest.mark.parametrize("path", ["../secret", "x//y", "/x", "x?token=secret", "x%2fy", "x\\y"])
def test_artifact_selection_refuses_routes_before_network(wire, path):
    with pytest.raises(GitLabAPIError):
        session().request_artifact(91, 901, path)
    assert not wire.calls


@pytest.mark.parametrize("media", ["application/json", "text/plain", "application/octet-stream"])
def test_artifact_bytes_are_never_auto_parsed_or_redirected(wire, media):
    wire.queue.append(Response(raw=b"not JSON", headers=[("Content-Type", media)]))
    assert session().request_artifact(91, 901, "report.json") == b"not JSON"
    wire.queue.append(Response(status=302))
    with pytest.raises(GitLabAPIError, match="redirect"):
        session().request_artifact(91, 901, "report.json")


def test_saved_or_unissued_origin_rejected():
    with pytest.raises(TypeError):
        AuthenticatedCIOrigin()
    with pytest.raises(CIPipelineError):
        require_ci_artifact({"pipeline_id": 701}, b"{}")


@pytest.mark.parametrize("status", ["failed", "canceled"])
def test_terminal_pipeline_observation_is_retained_and_never_hidden_by_retry(wire, tmp_path, status):
    flow = started(wire, tmp_path)
    project_reads(wire)
    wire.queue.append(Response(pipe(status)))
    assert flow.observe() == {"state": status, "origin": None}
    retained = json.loads((flow.path / "poll-0001.json").read_bytes())
    assert retained["pipeline"]["id"] == 701 and retained["pipeline"]["status"] == status
    assert retained["transport"]["body_sha256"]
    before = len(wire.calls)
    with pytest.raises(CIPipelineError):
        flow.observe()
    assert len(wire.calls) == before
    flow.deadline = 0
    unrelated = flow.path / "unregistered-private.json"
    unrelated.write_text('{"private":true}')
    artifacts = flow.evidence_artifacts(run_id=flow.selection.run_id)
    assert len(artifacts) == 1
    bundle = json.loads(artifacts[0].read_bytes())
    assert any(
        json.loads(item["utf8"]).get("pipeline", {}).get("status") == status for item in bundle["documents"]
    )
    assert not any(item["label"] == unrelated.name for item in bundle["documents"])
    for item in bundle["documents"]:
        assert len(item["utf8"].encode()) == item["bytes"]
        assert __import__("hashlib").sha256(item["utf8"].encode()).hexdigest() == item["sha256"]
    with pytest.raises(CIPipelineError, match="foreign"):
        flow.evidence_artifacts(run_id="other")
    artifacts[0].write_text("{}")
    with pytest.raises(ValueError):
        flow.evidence_artifacts(run_id=flow.selection.run_id)
    flow.close()


def test_modified_fsynced_selection_blocks_dispatch(wire, tmp_path):
    flow = OwnedCIPipeline(
        session(), SCOPE, selected(), journal_root=tmp_path, deadline=time.monotonic() + 60
    )
    (flow.path / "selection.json").chmod(0o600)
    (flow.path / "selection.json").write_text("{}")
    with pytest.raises(ValueError):
        flow.start()
    assert not wire.calls
    flow.close()
