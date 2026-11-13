"""Controlled API model; no live service route or external resource changes."""

import sys
import hashlib
import time
from types import ModuleType

import pytest

from devsecops.factory.gitlab_api import GitLabSession, GitLabAPIError
from test_owned_gitlab_cleanup import owned as owned, provision


@pytest.mark.parametrize("unknown", [False, True])
def test_resource_cleanup_renews_original_route_and_binds_reconciliation_operation(
    owned,  # noqa: F811 - pytest injects the registered fixture
    tmp_path,
    monkeypatch,
    unknown,
):
    resources, _, _, changes, session = owned
    context = provision(owned, tmp_path)
    route = object()
    ca = tmp_path / "synthetic-route-ca.pem"
    ca.write_bytes(b"synthetic-public-ca-for-controlled-issuer")
    facts = {
        "origin": session.origin,
        "connect_host": "127.0.0.1",
        "connect_port": 8444,
        "ca_path": str(ca),
        "ca_sha256": hashlib.sha256(ca.read_bytes()).hexdigest(),
        "deadline": time.monotonic() + 60,
        "read_only": False,
    }
    routes = {route: facts}
    requested = []
    issued = []
    audited = []
    sessions = [session]
    module = ModuleType("devsecops.factory.owned_service_links")

    def renew(value, *, context, operation_id=None):
        assert value is sessions[-1]._service_route
        requested.append((context, operation_id))
        fresh = object()
        routes[fresh] = {**facts, "read_only": operation_id is not None}
        issued.append(fresh)
        return fresh

    def require(value, *, origin):
        assert value in routes and origin == session.origin
        audited.append(value)
        return dict(routes[value])

    module.renew_service_route = renew
    module.require_service_route = require
    monkeypatch.setitem(sys.modules, module.__name__, module)
    from devsecops.adapters.owned_route import bind_service_route

    bind_service_route(session, route, origin=session.origin, ca_bundle=ca)
    transports = []
    original_clone = GitLabSession.cleanup_session

    # Only issuer and HTTP exchange are controlled. Real route binding, clone,
    # request admission, readonly state and shared unknown-write latch execute.
    def clone(original, **kwargs):
        assert original is sessions[-1] and kwargs["service_route"] is issued[-1]
        transports.append({key: value for key, value in kwargs.items() if key != "service_route"})
        result = original_clone(original, **kwargs)
        assert result is not original
        assert result._service_route is issued[-1]
        assert result._write_state is original._write_state
        assert result._read_only is routes[issued[-1]]["read_only"]
        sessions.append(result)
        return result

    monkeypatch.setattr(GitLabSession, "cleanup_session", clone)
    if unknown:
        changes["lost"] = "permanent"
        with pytest.raises(GitLabAPIError):
            resources.down(context)
        deletes = list(changes["deletes"])
        resources.reconcile(context, resources._down_operation)
        assert changes["deletes"] == deletes
        assert requested == [(context, None), (context, resources._down_operation)]
        assert transports == [{}, {"read_only": True}]
        assert session.writes_blocked
    else:
        assert resources.down(context)["complete_cleanup"] is True
        assert requested == [(context, None)] and transports == [{}]
    assert session._service_route is route
    assert all(value in audited for value in issued)
