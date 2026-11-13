"""Controlled creation/assignment/cleanup API; no actual GitLab operations."""

import pytest

from devsecops.factory.gitlab_api import GitLabSession, GitLabAPIError
from devsecops.factory.state import StateError
from test_owned_gitlab_cleanup import owned as owned, provision
from test_factory_gitlab_bootstrap import SCOPE


def runner(owned, tmp_path):  # noqa: F811 - pytest injects the registered fixture
    resources, context, model, changes, session = owned
    down = provision(owned, tmp_path)
    ticket = resources.runner_intent(100, SCOPE.marker)
    changes["runners"][31] = {
        "id": 31,
        "description": SCOPE.marker,
        "runner_type": "project_type",
        "is_shared": False,
        "projects": [{"id": 100}],
        "paused": True,
        "locked": False,
    }
    resources.runner_created(ticket, 31)
    return resources, ticket, changes, session, down


def test_exact_two_project_assignment_deletes_runner_before_both_projects(owned, tmp_path):  # noqa: F811 - pytest injects the registered fixture
    resources, ticket, changes, session, down = runner(owned, tmp_path)
    resources.runner_assignment_intent(ticket, 101)
    assert resources.runners[0][1]["state"] == "assignment-unknown"
    changes["runners"][31]["projects"].append({"id": 101})
    resources.runner_assigned(ticket, 101)
    row = resources.runners[0][1]
    assert row["project_ids"] == [100, 101]
    assert row["assignment"]["state"] == "observed-assigned"
    result = resources.down(down)
    assert result["complete_cleanup"]
    assert changes["deletes"][0] == ("/api/v4/runners/31", None)
    assert [path for path, _ in changes["deletes"][1:]] == ["/api/v4/projects/101"] * 2 + [
        "/api/v4/projects/100"
    ] * 2


@pytest.mark.parametrize("bad", [100, 999, True])
def test_assignment_cannot_expand_or_repeat_owned_project_scope(owned, tmp_path, bad):  # noqa: F811 - pytest injects the registered fixture
    resources, ticket, changes, session, down = runner(owned, tmp_path)
    with pytest.raises(StateError):
        resources.runner_assignment_intent(ticket, bad)
    assert changes["deletes"] == []


def test_uncertain_assignment_blocks_cleanup_and_read_reconciliation_never_writes(owned, tmp_path):  # noqa: F811 - pytest injects the registered fixture
    resources, ticket, changes, session, down = runner(owned, tmp_path)
    resources.runner_assignment_intent(ticket, 101)
    changes["runners"][31]["projects"].append({"id": 101})
    session.stop_writes()  # Modeled lost response after the assignment occurred.
    with pytest.raises(GitLabAPIError, match="writes-blocked"):
        resources.down(down)
    assert changes["deletes"] == []
    result = resources.reconcile_runner_assignment(owned[1], ticket, "up-op")
    assert result["project_ids"] == [100, 101] and session.writes_blocked
    assert changes["deletes"] == []
    with pytest.raises(StateError):
        resources.runner_assignment_intent(ticket, 101)


def test_assignment_readback_race_poison_is_retained(owned, tmp_path, monkeypatch):  # noqa: F811 - pytest injects the registered fixture
    resources, ticket, changes, session, down = runner(owned, tmp_path)
    resources.runner_assignment_intent(ticket, 101)
    changes["runners"][31]["projects"].append({"id": 101})
    original = GitLabSession._exchange
    seen = 0

    def race(client, method, path, payload, expected, **kw):
        nonlocal seen
        if method == "GET" and path == "/api/v4/runners/31":
            seen += 1
            if seen == 2:
                changes["runners"][31]["projects"].append({"id": 999})
        return original(client, method, path, payload, expected, **kw)

    monkeypatch.setattr(GitLabSession, "_exchange", race)
    with pytest.raises(StateError):
        resources.runner_assigned(ticket, 101)
    assert resources.runners[0][1]["state"] == "assignment-unknown"
    assert session.writes_blocked and not changes["deletes"]


def test_foreign_assignment_before_delete_blocks_global_runner_removal(owned, tmp_path):  # noqa: F811 - pytest injects the registered fixture
    resources, ticket, changes, session, down = runner(owned, tmp_path)
    resources.runner_assignment_intent(ticket, 101)
    changes["runners"][31]["projects"].append({"id": 101})
    resources.runner_assigned(ticket, 101)
    changes["runners"][31]["projects"].append({"id": 999})
    with pytest.raises(StateError):
        resources.down(down)
    assert not changes["deletes"]
