"""Controlled API model through real bootstrap/session/cleanup caller seams."""

from copy import deepcopy
from types import SimpleNamespace
import threading

import pytest

from devsecops.factory.gitlab_api import GitLabSession, GitLabAPIError
from devsecops.factory.gitlab_bootstrap import bootstrap_owned_projects
from devsecops.factory.owned_gitlab_cleanup import OwnedGitLabResources
from devsecops.factory.state import StateError
from test_factory_gitlab_bootstrap import ControlledSession, SCOPE, SEEDS


@pytest.fixture
def owned(tmp_path, monkeypatch):
    model = ControlledSession()
    session = object.__new__(GitLabSession)
    for key, value in {
        "_origin": SCOPE.origin,
        "_expected_user_id": 7,
        "_poisoned": False,
        "_lock": threading.RLock(),
        "_authorization": "SYNTHETIC",
        "_context": object(),
        "_host": "gitlab.example.test",
        "_port": 443,
        "_timeout": 10,
        "_max_requests": 512,
        "_max_body": 2_000_000,
        "_max_total": 64_000_000,
    }.items():
        setattr(session, key, value)
    changes = {"lost": None, "delayed": False, "runners": {}, "deletes": [], "token_revoked": False}

    def exchange(client, method, path, payload, expected):
        client._pagination = {"x-page": "1", "x-per-page": "100", "x-next-page": ""}
        if path == "/api/v4/metadata":
            return model.metadata()
        if path == "/api/v4/user":
            return model.principal()
        if path == "/api/v4/personal_access_tokens/self":
            if changes["token_revoked"]:
                raise GitLabAPIError("http-status", status=401)
            return {
                "id": 44,
                "user_id": 7,
                "active": True,
                "revoked": False,
                "scopes": ["api"],
                "expires_at": "2099-01-01",
            }
        if method == "DELETE":
            changes["deletes"].append((path, deepcopy(payload)))
            identifier = int(path.rsplit("/", 1)[1])
            if "/personal_access_tokens/" in path:
                assert identifier == 44
                changes["token_revoked"] = True
                if changes["lost"] == "token":
                    client._poisoned = True
                    raise GitLabAPIError("transport", outcome="unknown")
                return None
            if "/runners/" in path:
                del changes["runners"][identifier]
                return None
            if payload is None:
                model.projects[identifier]["marked_for_deletion_on"] = "2026-10-30"
            elif not changes["delayed"]:
                del model.projects[identifier]
            if changes["lost"] == ("permanent" if payload else "mark"):
                client._poisoned = True
                raise GitLabAPIError("transport", outcome="unknown")
            return {"message": "accepted"}
        if path.startswith("/api/v4/projects?owned="):
            return list(deepcopy(model.projects).values())
        if "/runners?" in path:
            return list(deepcopy(changes["runners"]).values())
        if path.startswith("/api/v4/runners/"):
            row = changes["runners"].get(int(path.rsplit("/", 1)[1]))
            if row is None:
                raise GitLabAPIError("http-status", status=404)
            return deepcopy(row)
        return model.request(method, path, payload, expected)

    monkeypatch.setattr(GitLabSession, "_exchange", exchange)
    context = SimpleNamespace(
        run_id="synthetic-run", identities={"package_sha256": "a" * 64}, phase={"operation": "up"}
    )
    owner = SimpleNamespace(
        journal=SimpleNamespace(run_dir=tmp_path),
        _admit=lambda ctx, action, **kw: "down-op" if action == "down" else "up-op",
    )
    resources = OwnedGitLabResources(owner, context, session, SCOPE)
    return resources, context, model, changes, session


def provision(owned, tmp_path):
    resources, context, model, changes, session = owned
    bootstrap_owned_projects(session, SCOPE, SEEDS, journal_root=tmp_path, cleanup=resources)
    return SimpleNamespace(run_id=context.run_id, identities=context.identities, phase={"operation": "down"})


def test_bootstrap_direct_handles_drive_two_stage_exact_cleanup(owned, tmp_path):
    resources, _, model, changes, _ = owned
    down = provision(owned, tmp_path)
    result = resources.down(down)
    assert result["complete_cleanup"] is True and not model.projects
    assert [path for path, _ in changes["deletes"]] == ["/api/v4/projects/101"] * 2 + [
        "/api/v4/projects/100"
    ] * 2
    for path, payload in changes["deletes"][1::2]:
        assert payload["permanently_remove"] is True
        assert payload["full_path"].startswith("factory-user/factory-demo-")
    with pytest.raises(StateError, match="already attempted"):
        resources.down(down)


def test_runner_is_removed_before_its_owned_projects(owned, tmp_path):
    resources, _, model, changes, _ = owned
    down = provision(owned, tmp_path)
    handle = resources.runner_intent(100, SCOPE.marker)
    changes["runners"][31] = {
        "id": 31,
        "description": SCOPE.marker,
        "runner_type": "project_type",
        "is_shared": False,
        "projects": [{"id": 100}],
    }
    resources.runner_created(handle, 31)
    assert resources.down(down)["complete_cleanup"]
    assert changes["deletes"][0] == ("/api/v4/runners/31", None)


def test_ambiguous_delete_stops_following_projects_and_reconciliation_never_retries(owned, tmp_path):
    resources, _, model, changes, _ = owned
    down = provision(owned, tmp_path)
    changes["lost"] = "permanent"
    with pytest.raises(GitLabAPIError):
        resources.down(down)
    assert list(model.projects) == [100]
    before = deepcopy(changes["deletes"])
    result = resources.reconcile(down, "down-op")
    assert changes["deletes"] == before
    assert result["complete_cleanup"] is False
    assert next(row for row in result["resources"] if row["id"] == 101)["state"] == "absent"
    assert next(row for row in result["resources"] if row["id"] == 100)["state"] == "owned-present"


def test_async_deletion_acceptance_is_not_absence_or_retry_permission(owned, tmp_path):
    resources, _, model, changes, _ = owned
    down = provision(owned, tmp_path)
    changes["delayed"] = True
    assert resources.down(down)["complete_cleanup"] is False
    assert len(changes["deletes"]) == 2
    resources.reconcile(down, "down-op")
    assert len(changes["deletes"]) == 2 and len(model.projects) == 2


def test_changed_owner_blocks_first_delete(owned, tmp_path):
    resources, _, model, changes, _ = owned
    down = provision(owned, tmp_path)
    model.projects[101]["owner"]["id"] = 999
    with pytest.raises(ValueError):
        resources.down(down)
    assert changes["deletes"] == []


def test_copied_creation_ticket_cannot_adopt_existing_project(owned):
    resources, _, _, changes, _ = owned
    resources.project_intent("source")
    with pytest.raises(StateError, match="handle"):
        resources.project_created(object(), 100)
    assert changes["deletes"] == []


def register_token(resources, monkeypatch, changes):
    import sys
    from types import ModuleType

    module = ModuleType("devsecops.factory.owned_services")

    class OwnedServiceStack:
        def principal(self, context):
            assert context is resources.context
            return {"owner_user_id": 7, "namespace_id": 17, "token_id": 44}

        def token_status(self, context, *, token_id, owner_user_id):
            assert token_id == 44 and owner_user_id == 7
            return {
                "token_id": 44,
                "owner_user_id": 7,
                "token_name": "factory-owned",
                "revoked": changes.get("server_revoked", changes["token_revoked"]),
            }

    module.OwnedServiceStack = OwnedServiceStack
    monkeypatch.setitem(sys.modules, "devsecops.factory.owned_services", module)
    stack = OwnedServiceStack()
    resources.owner.components = {"services": stack}
    resources.accept_service_token(stack)


def test_created_token_is_invalidated_only_after_projects(owned, tmp_path, monkeypatch):
    resources, _, _, changes, _ = owned
    register_token(resources, monkeypatch, changes)
    down = provision(owned, tmp_path)
    assert resources.down(down)["complete_cleanup"]
    assert changes["deletes"][-1] == ("/api/v4/personal_access_tokens/44", None)
    assert changes["token_revoked"]


def test_lost_token_revocation_response_reconciles_without_repeated_delete(owned, tmp_path, monkeypatch):
    resources, _, _, changes, _ = owned
    register_token(resources, monkeypatch, changes)
    down = provision(owned, tmp_path)
    changes["lost"] = "token"
    with pytest.raises(GitLabAPIError):
        resources.down(down)
    before = deepcopy(changes["deletes"])
    assert resources.reconcile(down, "down-op")["complete_cleanup"]
    assert changes["deletes"] == before


@pytest.mark.parametrize("reason", ["expired-token", "invalid-token"])
def test_authentication_rejection_without_owned_server_revocation_is_not_cleanup(
    owned, tmp_path, monkeypatch, reason
):
    resources, _, _, changes, _ = owned
    register_token(resources, monkeypatch, changes)
    down = provision(owned, tmp_path)
    # Model a 401 independently of the actual server-side revoked field.
    changes["server_revoked"] = False
    result = resources.down(down)
    assert changes["token_revoked"] is True  # The API would reject these credentials.
    assert result["complete_cleanup"] is False
    before = deepcopy(changes["deletes"])
    assert resources.reconcile(down, "down-op")["complete_cleanup"] is False
    assert changes["deletes"] == before


def test_original_public_resource_evidence_survives_phase_change_and_rejects_tampering(owned, tmp_path):
    resources, context, _, _, _ = owned
    provision(owned, tmp_path)
    calls = []
    resources.owner.driver = SimpleNamespace(_admit=lambda ctx, deadline: calls.append((ctx, deadline)))
    current = SimpleNamespace(run_id=context.run_id, identities=context.identities, deadline=99999999)
    paths = resources.evidence_artifacts(current)
    assert paths and calls == [(current, current.deadline)]
    extra = tmp_path / "not-registered.json"
    extra.write_text('{"private":true}')
    assert extra not in resources.evidence_artifacts(current)
    paths[0].write_text("{}")
    with pytest.raises(StateError, match="event bytes changed"):
        resources.evidence_artifacts(current)
