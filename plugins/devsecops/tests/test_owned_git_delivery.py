"""Production delivery caller with controlled bootstrap/CI/HTTP boundaries.

Real native commit, complete transport, base audit, request and journal claim
execute. The synthetic upstream and HTTP observations are not live E2E proof.
"""

import json
import sys
from time import monotonic
from types import ModuleType

import pytest

from test_owned_writer_identity import ready
from test_deployment_commit import case
from test_git_base_possession import subject
from test_git_destination import packet
from test_git_native_request import advert
from devsecops.factory import git_base_possession, git_transport
from devsecops.factory import owned_git_delivery as module
from devsecops.factory.git_receive_http import GitReceiveError
from devsecops.factory.git_request_framing import command_pack
from devsecops.factory.lifecycle import PhaseContext
from devsecops.factory.state import StateError, _canonical, _sha

__all__ = ["ready", "case"]


@pytest.fixture
def prepared(ready, case, tmp_path, monkeypatch):
    r = ready
    r.cleanup.writer_up(r.context)
    case["args"]["run_scope"] = r.journal.identities
    case["binding"]["run_scope"] = r.journal.identities
    state = subject.__wrapped__(case, tmp_path, monkeypatch)
    state.config.update(
        origin=r.session.origin,
        target_ref="refs/heads/owned-demo/" + r.journal.state["run_id"] + "/build-promote",
    )
    state.args["config"].write_bytes(_canonical(state.config))
    state.args["expected_config_sha256"] = _sha(state.args["config"].read_bytes())
    report, observed = git_base_possession.verify_git_base_possession(**state.args)
    assert report["decision"] == "pass"
    scope = {
        key: state.args[key]
        for key in (
            "run_scope",
            "source_commit",
            "deployment_commit",
            "proposal_configuration_sha256",
            "configuration_sha256",
        )
    }
    handle = object()
    publication_origin = object()
    provider = ModuleType("devsecops.factory.owned_operations")

    def require(value, *, journal):
        assert value is handle and journal is r.journal
        git_transport.require_prepared_git_transport(state.plan, **scope)
        git_base_possession.require_observed_git_base(
            observed,
            transport=state.plan,
            **scope,
            expected_config_sha256=state.args["expected_config_sha256"],
        )
        return state.plan, observed, scope, state.args["expected_config_sha256"]

    def sources(value, *, journal):
        require(value, journal=journal)
        return case["args"]["proposal"], git_transport._PLANS[state.plan]["prepared"], publication_origin

    provider.require_operation_git = require
    provider.require_operation_git_sources = sources
    monkeypatch.setitem(sys.modules, "devsecops.factory.owned_operations", provider)
    monkeypatch.setattr(module, "_installed", lambda *args: None)  # Explicit package boundary only.
    calls = []
    fault = {"send": False}
    commit = git_transport._PLANS[state.plan]["prepared"].describe()["prepared"]

    class Wire:
        def __init__(self, **kwargs):
            assert kwargs["token"] == "SYNTHETIC_WRITER_SECRET_CANARY"
            self._token = kwargs["token"]
            self.budget = kwargs["budget"]

        def set_username(self, name):
            assert name == r.component.name

        def read_api(self, path, **kwargs):
            calls.append(("GET", path))
            expiry = r.component._rows["token"]["expires_at"]
            prefix = "/api/v4/projects/37"
            headers = {}
            if path == "/api/v4/version":
                value = {"version": "19.5.3", "revision": "a" * 40}
            elif path == "/api/v4/user":
                value = {
                    "id": 17,
                    "state": "active",
                    "bot": False,
                    "is_admin": False,
                    "username": r.component.name,
                }
            elif path == "/api/v4/personal_access_tokens/self":
                value = {
                    "id": 91,
                    "user_id": 17,
                    "active": True,
                    "revoked": False,
                    "scopes": ["read_api", "write_repository"],
                    "expires_at": expiry,
                }
            elif path == prefix:
                value = {
                    "id": 37,
                    "http_url_to_repo": r.session.origin + "/group/deploy.git",
                    "path_with_namespace": "group/deploy",
                }
            elif path == prefix + "/members/all/17":
                value = {"id": 17, "state": "active", "access_level": 30, "expires_at": None}
            elif path == prefix + "/protected_branches?per_page=100&page=1":
                value = []
                headers = {b"x-page": b"1", b"x-per-page": b"100", b"x-next-page": b""}
            elif path.startswith(prefix + "/repository/branches/"):
                value = {
                    "name": state.config["target_ref"].removeprefix("refs/heads/"),
                    "commit": {"id": commit["commit"]},
                }
            elif path == prefix + "/repository/commits/" + commit["commit"]:
                value = {"id": commit["commit"], "parent_ids": [commit["parent"]]}
            elif path == prefix + "/repository/blobs/" + commit["after_blob"] + "/raw":
                return 200, headers, case["after"]
            else:
                pytest.fail("Unselected writer read")
            return 200, headers, _canonical(value)

        def advertisement(self):
            calls.append(("GET", "/group/deploy.git/info/refs?service=git-receive-pack"))
            return advert(state)

        def send_once(self, body):
            command_pack(body, new_commit=commit["commit"], target_ref=state.config["target_ref"])
            calls.append(("POST", "/group/deploy.git/git-receive-pack"))
            if fault["send"]:
                raise GitReceiveError("controlled-lost-status")
            return (
                packet(b"unpack ok\n")
                + packet(("ok " + state.config["target_ref"] + "\n").encode())
                + b"0000"
            )

    monkeypatch.setattr(module, "GitReceiveClient", Wire)
    evidence = r.journal.run_dir / "controlled-platform-evidence"
    evidence.write_bytes(b"explicit controlled platform boundary\n")
    phases = json.loads(r.inputs["scenario"].read_bytes())["phases"]
    with r.journal.lifecycle_lock(), r.journal.mutation_lock(tmp_path / "writer-locks"):
        for phase in phases:
            r.journal.begin_phase(phase["id"])
            if phase["id"] == "build-promote":
                break
            r.journal.finish_phase(
                phase["id"], "completed", "controlled-platform", receipt_artifacts=[evidence]
            )
        operation = r.journal.begin_mutation("build-promote", "build-promote", intent_artifacts=[evidence])
        context = PhaseContext(
            r.journal.state["run_id"],
            phase,
            r.journal.identities,
            r.inputs,
            r.journal.state["lifecycle"]["inputs"],
            r.journal.run_dir,
            monotonic() + 120,
            "operator",
        )
        args = dict(
            journal=r.journal,
            prepared=handle,
            bootstrap_authority=r.bootstrap,
            destination=state.args["config"],
            destination_sha256=state.args["expected_config_sha256"],
            writer=r.policy,
            writer_sha256=r.component.policy_sha256,
            writer_origin=r.component.origin(),
            plugin_id="devsecops@test",
            plugin_version="test",
        )
        yield r, context, args, calls, fault, operation


def test_fixed_caller_claims_exact_request_and_requires_independent_readback(prepared):
    r, context, args, calls, _, operation = prepared
    value = module.deliver_prepared(context, **args)
    observed = module.require_delivery(value, journal=r.journal)
    assert observed["operation_id"] == operation and observed["delivery_stage"] == "review-branch"
    assert observed["watched_revision"] is None
    assert [path for method, path in calls if method == "POST"] == ["/group/deploy.git/git-receive-pack"]
    assert all(method == "GET" for method, path in calls if path.startswith("/api/v4/"))
    assert len(r.journal.state["active_mutation"]["receipt_artifacts"]) == 3
    before = list(calls)
    with pytest.raises((StateError, FileExistsError)):
        module.deliver_prepared(context, **args)
    assert calls == before


def test_lost_status_retains_spent_claim_and_no_delivery_origin(prepared):
    r, context, args, calls, fault, operation = prepared
    fault["send"] = True
    with pytest.raises(GitReceiveError, match="controlled-lost-status"):
        module.deliver_prepared(context, **args)
    assert operation in r.journal._consumed_git_request_operations
    assert r.journal.state["active_mutation"]["operation_id"] == operation
    assert not (
        r.journal.run_dir / "operations" / operation / "owned-git-delivery" / "delivery.json"
    ).exists()
    assert sum(method == "POST" for method, _ in calls) == 1


def mr_actor(r, binding, monkeypatch, *, drift=False):
    from devsecops.factory import owned_runner_service

    controlled_runner = object()
    monkeypatch.setattr(
        sys.modules["devsecops.factory.canonical_bootstrap"],
        "runner_origin_for",
        lambda *a: controlled_runner,
        raising=False,
    )

    def controlled_binding(value, *, current=False):
        assert value is controlled_runner
        return {
            "run_id": r.journal.state["run_id"],
            "runner_id": 67,
            "runner_manager_system_identity": "s_controlled",
            "origin": r.session.origin,
            "project_ids": [37],
        }

    monkeypatch.setattr(owned_runner_service, "runner_execution_binding", controlled_binding)
    owner = r.session
    active = {"merged": False, "description": None, "writes": []}
    project = "/api/v4/projects/37"
    source = binding["deployment_commit"]
    target = binding["parent"]
    branch = binding["target_ref"].removeprefix("refs/heads/")
    pipeline = {
        "id": 701,
        "project_id": 37,
        "sha": source,
        "ref": "refs/merge-requests/12/head",
        "status": "success",
        "source": "merge_request_event",
        "tag": False,
        "yaml_errors": None,
    }

    def mr():
        return {
            "iid": 12,
            "project_id": 37,
            "source_project_id": 37,
            "target_project_id": 37,
            "source_branch": branch,
            "target_branch": "main",
            "sha": source,
            "state": "merged" if active["merged"] else "opened",
            "draft": False,
            "author": {"id": 1},
            "description": active["description"],
            "detailed_merge_status": "mergeable",
            "has_conflicts": False,
            "blocking_discussions_resolved": True,
            "squash": False,
            "diff_refs": {"base_sha": target, "start_sha": target, "head_sha": source},
            "head_pipeline": pipeline,
            "merge_commit_sha": source if active["merged"] else None,
            "squash_commit_sha": None,
        }

    def request(method, path, payload=None, expected=(200,)):
        if method == "POST" and path == project + "/merge_requests":
            active["description"] = payload["description"]
            active["writes"].append((method, path, payload))
            return mr()
        if method == "PUT" and path == project + "/merge_requests/12/merge":
            assert payload == {
                "sha": source,
                "auto_merge": False,
                "squash": False,
                "should_remove_source_branch": False,
            }
            active["merged"] = True
            active["writes"].append((method, path, payload))
            return mr()
        assert method == "GET"
        if path == project:
            return {
                "id": 37,
                "path_with_namespace": "group/deploy",
                "owner": {"id": 1},
                "namespace": {"id": 20, "kind": "user", "full_path": "group"},
                "visibility": "private",
                "archived": False,
                "default_branch": "main",
                "permissions": {"project_access": {"access_level": 40}},
                "merge_method": "ff",
                "squash_option": "never",
                "only_allow_merge_if_pipeline_succeeds": True,
                "allow_merge_on_skipped_pipeline": False,
                "only_allow_merge_if_all_discussions_are_resolved": True,
                "remove_source_branch_after_merge": False,
            }
        if path.endswith("/repository/branches/main"):
            sha = ("f" * 40 if drift else source) if active["merged"] else target
            return {"name": "main", "commit": {"id": sha}, "protected": True}
        if "/repository/branches/" in path:
            return {
                "name": branch,
                "commit": {"id": source},
                "runner": {"id": 67},
                "runner_manager": {"system_id": "s_controlled", "platform": "linux", "architecture": "amd64"},
                "protected": False,
            }
        if path == project + "/merge_requests/12":
            return mr()
        if path == project + "/pipelines/701":
            return pipeline
        pytest.fail("Unexpected owner MR route")

    def collection(path):
        if "/jobs?" in path:
            return [
                {
                    "id": index,
                    "name": name,
                    "status": "success",
                    "allow_failure": False,
                    "erased_at": None,
                    "finished_at": "2026-10-30T00:00:00Z",
                    "pipeline": pipeline,
                    "commit": {"id": source},
                    "runner": {"id": 67},
                    "runner_manager": {
                        "system_id": "s_controlled",
                        "platform": "linux",
                        "architecture": "amd64",
                    },
                }
                for index, name in ((901, "review"), (902, "test"))
            ]
        if path.startswith(project + "/merge_requests?") or path.endswith("/trigger_jobs"):
            return []
        pytest.fail("Unexpected owner MR collection")

    monkeypatch.setattr(owner, "request", request)
    monkeypatch.setattr(owner, "get_collection", collection)
    monkeypatch.setattr(owner, "verify_identity", lambda: {"principal": {"id": 1, "username": "group"}})
    return active


def test_fixed_delivery_then_distinct_owner_mr_actor_observes_exact_watched_commit(prepared, monkeypatch):
    r, context, args, calls, _, _ = prepared
    value = module.deliver_prepared(context, **args)
    original = module.require_delivery(value, journal=r.journal)
    actor = mr_actor(r, original, monkeypatch)
    assert (
        module.merge_reviewed(
            context,
            journal=r.journal,
            delivery=value,
            session=r.session,
            required_jobs=("review", "test"),
            max_observations=1,
        )
        is value
    )
    final = module.require_delivery(value, journal=r.journal)
    assert final["delivery_stage"] == "merged-watched-branch"
    assert final["watched_revision"] == original["deployment_commit"]
    assert final["target_sha_atomic_precondition"] is False
    assert [method for method, _, _ in actor["writes"]] == ["POST", "PUT"]
    assert [path for method, path in calls if method == "POST"] == ["/group/deploy.git/git-receive-pack"]
    with pytest.raises(StateError):
        module.merge_reviewed(
            context, journal=r.journal, delivery=value, session=r.session, required_jobs=("review", "test")
        )
    # An observed merge remains historical evidence after the original image
    # grant expires; it never reissues a transport or writer capability.
    provider = sys.modules["devsecops.factory.owned_operations"]

    def expired(*args, **kwargs):
        raise StateError("controlled expired publication/transport")

    monkeypatch.setattr(provider, "require_operation_git", expired)
    monkeypatch.setattr(provider, "require_operation_git_sources", expired)
    assert module.require_delivery(value, journal=r.journal) == final
    artifacts = module.delivery_artifacts(value, journal=r.journal)
    assert {"request.body", "native-request.json", "git-base-possession.json", "merged-delivery.json"} <= {
        p.name for p in artifacts
    }
    assert any(p.parent.name.startswith("mr-") for p in artifacts)
    (module._DELIVERIES[value]["output"] / "unregistered-private.json").write_text("{}")
    assert module.delivery_artifacts(value, journal=r.journal) == artifacts
    artifact = next(iter(module._DELIVERIES[value]["source_artifacts"]))
    artifact.write_bytes(artifact.read_bytes() + b"changed")
    with pytest.raises(StateError, match="source artifact changed"):
        module.require_delivery(value, journal=r.journal)


def test_changed_merge_readback_revokes_origin_and_never_retries(prepared, monkeypatch):
    r, context, args, _, _, _ = prepared
    value = module.deliver_prepared(context, **args)
    actor = mr_actor(r, module.require_delivery(value, journal=r.journal), monkeypatch, drift=True)
    with pytest.raises(ValueError):
        module.merge_reviewed(
            context,
            journal=r.journal,
            delivery=value,
            session=r.session,
            required_jobs=("review", "test"),
            max_observations=1,
        )
    with pytest.raises(StateError, match="revoked"):
        module.require_delivery(value, journal=r.journal)
    assert len(actor["writes"]) == 2 and r.session.writes_blocked
