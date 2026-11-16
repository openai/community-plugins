"""Canonical policy caller through real source/render/commit/transport.

Authenticated remote-base and write/MR boundaries are controlled; no real remote
write or live F0/native model invocation is made by these tests.
"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_policy_git_source import policy_case, correction
from test_git_destination import selection
from test_owned_git_writer_contract import selection as writer_selection
from devsecops.factory import owned_policy_delivery as module, owned_git_delivery, git_base_possession
from devsecops.factory import canonical_bootstrap
from devsecops.factory.state import _canonical, _sha

__all__ = ["policy_case", "correction"]


@pytest.mark.parametrize("policy_case", ["inject", "deliver", "reset"], indirect=True)
def test_canonical_caller_uses_original_policy_transport_and_fixed_writer_mr_path(
    policy_case, tmp_path, monkeypatch
):
    c = policy_case
    stage, writer_origin, reader = object(), object(), object()
    owner = SimpleNamespace()
    c.ops.bootstrap.writer = lambda context: (stage, writer_origin)
    c.ops.bootstrap.reader = lambda context: (stage, reader)
    c.ops.bootstrap.gitlab = lambda context: (stage, owner)
    facts = {
        "origin": "https://git.example.test",
        "projects": {"deployment": {"id": 37, "path_with_namespace": "group/deploy"}},
    }

    def bootstrap(authority, journal):
        assert authority is stage and journal is c.ops.journal
        return deepcopy(facts)

    monkeypatch.setattr(canonical_bootstrap, "require_bootstrap_authority", bootstrap)
    policy = writer_selection()
    policy.update(
        run_scope=c.ops.journal.identities,
        environment_id="lab",
        expires_at=c.context.inputs["config"]["expires_at"],
    )
    inputs = {
        "commit": c.commit,
        "destination": selection(c.commit["deployment_commit"]),
        "owned_git_writer": policy,
    }
    pairs = {}
    for name, value in inputs.items():
        path = tmp_path / (name + ".json")
        raw = _canonical(value)
        path.write_bytes(raw)
        pairs[name] = (path, _sha(raw), raw)
    c.ops.selected_component = lambda name: pairs[name]
    recipe = c.ops._recipe()
    recipe.update(
        plugin_id="controlled@test",
        plugin_version="test",
        ci={"delivery_required_jobs": ["review", "test"], "poll_seconds": 1, "max_observations": 1},
    )
    c.ops._recipe = lambda: recipe
    parent = c.ops.journal.run_dir / "operations" / "controlled-operation"
    parent.mkdir(parents=True)
    route = object()
    c.ops.bootstrap.service_route = lambda context, service: route if context is c.context and service == 'gitlab' else None
    observed = object()
    selected = {}
    calls = []

    def observe(**kw):
        assert (
            kw["reader_origin"] is reader
            and kw["bootstrap_authority"] is stage
            and kw["journal"] is c.ops.journal
        )
        selected.update(kw)
        assert kw["transport"].describe()["new_commit"]
        assert kw['service_route'] is route
        return {"decision": "pass"}, observed

    def audit(value, **kw):
        assert value is observed and kw["transport"] is selected["transport"]
        return {"controlled_remote_base": True}

    monkeypatch.setattr(git_base_possession, "verify_git_base_possession", observe)
    monkeypatch.setattr(git_base_possession, "require_observed_git_base", audit)
    delivery = object()
    final = {}

    def write(context, **kw):
        assert (
            context is c.context
            and kw["bootstrap_authority"] is stage
            and kw["writer_origin"] is writer_origin
        )
        assert kw['service_route'] is route
        handles, origins = owned_git_delivery._prepared_inputs(kw["prepared"], c.ops.journal)
        assert handles[0] is selected["transport"] and handles[1] is observed
        source, commit, previous = origins
        bound = source.describe()
        prepared = commit.describe()["prepared"]
        assert (
            bound["source_kind"] == "qualified-policy-change"
            and prepared["parent"] == c.commit["deployment_commit"]
        )
        final.update(delivery_stage="merged-watched-branch", watched_revision=prepared["commit"])
        calls.append("write")
        return delivery

    def merge(context, **kw):
        assert (
            kw["delivery"] is delivery
            and kw["session"] is owner
            and kw["required_jobs"] == ("review", "test")
        )
        calls.append("merge")
        return delivery

    old = owned_git_delivery.require_delivery
    monkeypatch.setattr(owned_git_delivery, "deliver_prepared", write)
    monkeypatch.setattr(owned_git_delivery, "merge_reviewed", merge)
    monkeypatch.setattr(
        owned_git_delivery,
        "require_delivery",
        lambda value, **kw: deepcopy(final) if value is delivery else old(value, **kw),
    )
    assert module.execute_policy_delivery(c.context, operations=c.ops) is delivery
    assert calls == ["write", "merge"]
