"""Controlled issued reader boundary with actual native Git base reconstruction."""

from copy import deepcopy
import json
import sys
from types import ModuleType

import pytest

from devsecops.factory import git_base_possession as base
from test_git_base_possession import subject as subject, case as case, rewrite


def test_direct_owned_reader_never_uses_ambient_or_writer_credentials(subject, monkeypatch):  # noqa: F811 - pytest injects the registered fixture
    origin, journal, authority = object(), object(), object()
    binding = {
        "origin": subject.config["origin"],
        "project_id": subject.config["project_id"],
        "path_with_namespace": subject.config["path_with_namespace"],
        "username": "owned-reader",
        "user_id": 17,
        "token_id": 92,
    }
    module = ModuleType("devsecops.factory.owned_reader_identity")

    def require(value, *, journal, bootstrap_authority):
        if value is not origin or bootstrap_authority is not authority:
            raise ValueError("Original controlled reader boundary required")
        return deepcopy(binding)

    def secret(value, **kwargs):
        require(value, **kwargs)
        return "OWNED_READER_SECRET_CANARY"

    module.require_owned_reader_origin = require
    module._reader_secret = secret
    monkeypatch.setitem(sys.modules, module.__name__, module)
    subject.config.update(
        kind="owned-git-destination-observation-configuration",
        api_read_credential={"kind": "owned-reader", "transport": "api"},
        git_read_credential={"kind": "owned-reader", "transport": "git"},
    )
    rewrite(subject)
    monkeypatch.setattr(
        base, "_authorization", lambda *_: pytest.fail("Ambient credentials must not be consulted")
    )
    args = {**subject.args, "reader_origin": origin, "journal": journal, "bootstrap_authority": authority}
    with pytest.raises(ValueError):
        base.verify_git_base_possession(**{**args, "reader_origin": {}})
    assert not subject.calls
    report, cap = base.verify_git_base_possession(**args)
    assert report["decision"] == "pass" and cap is not None
    assert all("OWNED_READER_SECRET_CANARY" not in json.dumps(report) for _ in (0,))
    assert all(client._authorization() is None for client in subject.clients)
    assert any(call["authorization"] == "Bearer OWNED_READER_SECRET_CANARY" for call in subject.calls)
    binding["project_id"] += 1
    with pytest.raises(ValueError, match="reader origin changed"):
        cap.describe()
