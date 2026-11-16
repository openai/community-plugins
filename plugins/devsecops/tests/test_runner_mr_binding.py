"""Real MR gate with controlled API and original process-local runner origin."""

import time

import pytest
from devsecops.factory import owned_runner_service as service
from devsecops.factory.gitlab_mr import MergeRequestWorkflow, MergeRequestError
from devsecops.factory.state import StateError
from test_owned_runner_service import original as _original
from test_gitlab_mr import selected, create_responses, gates_responses, jobs
from test_gitlab_api import session, wire as _wire

original = _original
wire = _wire


def flow_for(original, tmp_path):
    _, _, origin = original
    api = session()
    service._ORIGINS[origin]["facts"].update(origin=api.origin, project_ids=[91])
    return MergeRequestWorkflow(api, selected(), journal_root=tmp_path, runner_origin=origin)


def current_jobs():
    values = jobs()
    for row in values:
        row["runner"] = {"id": 67}
        row["runner_manager"] = {"system_id": "s_owned", "platform": "linux", "architecture": "amd64"}
    return values


def test_actual_required_jobs_bind_original_guest_registration_and_manager(original, wire, tmp_path):
    with flow_for(original, tmp_path) as flow:
        create_responses(wire, flow)
        flow.create()
        gates_responses(wire, flow, job_values=current_jobs())
        assert flow.observe_gates()["required_jobs"] == {"review": 901, "test": 902}
        assert (flow.path / "runner-origin.json").is_file()
        assert not wire.queue


@pytest.mark.parametrize(
    "damage",
    ["runner", "manager", "platform", "architecture", "missing-runner", "missing-manager", "bool-id"],
)
def test_unrelated_or_unobserved_runner_cannot_satisfy_deployment_gate(original, wire, tmp_path, damage):
    with flow_for(original, tmp_path) as flow:
        create_responses(wire, flow)
        flow.create()
        rows = current_jobs()
        if damage == "runner":
            rows[0]["runner"]["id"] = 68
        elif damage == "manager":
            rows[0]["runner_manager"]["system_id"] = "s_other"
        elif damage == "platform":
            rows[0]["runner_manager"]["platform"] = "windows"
        elif damage == "architecture":
            rows[0]["runner_manager"]["architecture"] = "arm64"
        elif damage == "missing-runner":
            rows[0].pop("runner")
        elif damage == "missing-manager":
            rows[0].pop("runner_manager")
        else:
            rows[0]["runner"]["id"] = True
        gates_responses(wire, flow, job_values=rows)
        with pytest.raises(MergeRequestError, match="required-job-original-runner"):
            flow.observe_gates()
        assert not any(row["method"] in ("PUT", "DELETE") for row in wire.calls)


def test_expired_runner_origin_cannot_create_new_mr(original, wire, tmp_path):
    with flow_for(original, tmp_path) as flow:
        service._ORIGINS[original[2]]["deadline"] = time.monotonic() - 1
        with pytest.raises(StateError, match="lifetime"):
            flow.create()
        assert not wire.calls


@pytest.mark.parametrize("damage", ["origin", "project"])
def test_mr_scope_must_match_original_runner_assignment(original, wire, tmp_path, damage):
    _, _, origin = original
    api = session()
    service._ORIGINS[origin]["facts"].update(origin=api.origin, project_ids=[91])
    if damage == "origin":
        service._ORIGINS[origin]["facts"]["origin"] = "https://other.example.test"
    else:
        service._ORIGINS[origin]["facts"]["project_ids"] = [92]
    with pytest.raises(MergeRequestError, match="project-binding"):
        MergeRequestWorkflow(api, selected(), journal_root=tmp_path, runner_origin=origin)
    assert not wire.calls
