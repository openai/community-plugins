"""Actual GitLab monitor routing semantics; controlled network/process boundaries."""

import importlib.util
import json
from pathlib import Path
import ssl
import urllib.error
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "portable_readiness_stack", ROOT / "demo/portable-gitops/stack.py"
)
stack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stack)


@pytest.fixture
def ready(tmp_path, monkeypatch):
    cid = "a" * 64
    owner = {"owner_id": "owned-demo", "gitlab_origin": "https://10.248.1.4:8443", "uid": 1000}
    record = {"id": cid, "owner_id": owner["owner_id"], "image": "gitlab/gitlab-ce@sha256:" + "b" * 64}
    (tmp_path / "gitlab-container.json").write_text(json.dumps(record))
    inspected = {
        "Id": cid,
        "Config": {"Labels": {"devsecops.owner": owner["owner_id"]}, "Image": record["image"]},
        "State": {"Running": True},
    }
    health = {
        "status": "ok",
        "db_check": [{"status": "ok"}],
        "cache_check": [{"status": "ok"}],
        "gitaly_check": [{"status": "ok"}],
    }
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return json.dumps([inspected] if argv[:2] == ["docker", "inspect"] else health).encode()

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    response = Response()
    urls = []

    def request(state, url, **kwargs):
        urls.append(url)
        return response

    monkeypatch.setattr(stack, "run", run)
    monkeypatch.setattr(stack, "private_open", request)
    return SimpleNamespace(
        state=tmp_path,
        owner=owner,
        record=record,
        inspected=inspected,
        health=health,
        calls=calls,
        urls=urls,
        response=response,
    )


def test_external_ui_and_owned_loopback_all_checks_keep_monitor_whitelist(ready):
    observation = stack.gitlab_readiness(ready.state, ready.owner, stack.time.monotonic() + 30)
    assert observation["all_dependency_checks_ok"] is True
    assert ready.urls == [ready.owner["gitlab_origin"] + "/users/sign_in"]
    assert ready.calls[0] == ["docker", "inspect", ready.record["id"]]
    cmd = ready.calls[1]
    assert cmd[:3] == ["docker", "exec", ready.record["id"]]
    assert cmd[-1] == "https://127.0.0.1:8443/-/readiness?all=1"
    assert cmd[cmd.index("--cacert") + 1] == "/etc/gitlab/demo-tls/ca.crt"
    assert "--insecure" not in cmd
    assert cmd[cmd.index("--noproxy") + 1] == "*"


@pytest.mark.parametrize("change", ["owner", "image", "id"])
def test_container_substitution_fails_before_external_or_internal_request(ready, change):
    if change == "owner":
        ready.inspected["Config"]["Labels"]["devsecops.owner"] = "other"
    elif change == "image":
        ready.inspected["Config"]["Image"] = "other"
    else:
        ready.inspected["Id"] = "c" * 64
    with pytest.raises(ValueError, match="ownership/image"):
        stack.gitlab_readiness(ready.state, ready.owner, stack.time.monotonic() + 30)
    assert len(ready.calls) == 1 and not ready.urls


@pytest.mark.parametrize("change", ["failed", "missing", "empty", "unknown"])
def test_dependencies_must_all_be_genuinely_healthy(ready, change):
    if change == "failed":
        ready.health["db_check"][0]["status"] = "failed"
    elif change == "missing":
        del ready.health["db_check"]
    elif change == "empty":
        ready.health["db_check"] = []
    else:
        ready.health["status"] = "unknown"
    with pytest.raises(RuntimeError, match="not all healthy"):
        stack.gitlab_readiness(ready.state, ready.owner, stack.time.monotonic() + 30)


def test_signin_failure_cannot_be_replaced_by_container_health(ready):
    ready.response.status = 503
    with pytest.raises(RuntimeError, match="sign-in"):
        stack.gitlab_readiness(ready.state, ready.owner, stack.time.monotonic() + 30)
    assert len(ready.calls) == 1


def test_expired_budget_cannot_dispatch_docker(ready):
    with pytest.raises(RuntimeError, match="deadline"):
        stack.gitlab_readiness(ready.state, ready.owner, stack.time.monotonic() - 1)
    assert not ready.calls


@pytest.mark.parametrize("wrapped", [False, True])
def test_tls_verification_failure_does_not_wait_or_retry(ready, monkeypatch, wrapped):
    error = ssl.SSLCertVerificationError("controlled invalid peer")
    if wrapped:
        error = urllib.error.URLError(error)

    def failed(*args, **kwargs):
        raise error

    monkeypatch.setattr(stack, "private_open", failed)
    with pytest.raises(type(error)):
        stack.wait_services(ready.state, ready.owner)
    assert len(ready.calls) == 1


def test_readiness_is_not_latched_across_different_attempts(ready, monkeypatch):
    (ready.state / "kubeconfig").write_text("controlled")
    clock = [0]
    monkeypatch.setattr(stack.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(stack.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + 600))
    replies = iter([b"ok", b"not ok"])
    monkeypatch.setattr(stack, "kubectl", lambda *args, **kwargs: next(replies))

    def health(*args):
        if clock[0] == 0:
            raise RuntimeError("warming")
        return {"all_dependency_checks_ok": True}

    monkeypatch.setattr(stack, "gitlab_readiness", health)
    with pytest.raises(RuntimeError, match="20-minute"):
        stack.wait_services(ready.state, ready.owner)
    assert not (ready.state / "services-ready.json").exists()
