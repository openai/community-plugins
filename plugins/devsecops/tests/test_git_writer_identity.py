"""Real local transport provenance and scripted GETs; no live writer credentials."""

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import time
from types import SimpleNamespace

import pytest

from devsecops.adapters.http import APIError, ReadResponse
from devsecops.factory import git_transport, git_writer_identity as writer
from devsecops.factory.state import IDENTITY_KEYS
from devsecops.runtime.context import canonical_bytes, verify_seal
from test_deployment_commit import case
from test_git_destination import selection
from test_git_transport import SCOPE, inventory, prepared_case

__all__ = ["case"]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def configuration(destination_pin="a" * 64):
    return {
        "schema_version": 1,
        "kind": "mission-git-writer-observation-configuration",
        "token_profile": "classic-personal-access-token",
        "token_env": "SYNTHETIC_WRITER_PAT",
        "expected_user_id": 17,
        "expected_token_id": 91,
        "expected_gitlab_version": "18.5.0-ee",
        "expected_project_access_level": 30,
        "expected_scopes": ["read_api", "write_repository"],
        "target_ref_prefix": "refs/heads/demo/",
        "destination_configuration_sha256": destination_pin,
    }


def documents(base="b" * 40):
    expiry = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
    return {
        "version": {"version": "18.5.0-ee", "revision": "a" * 40},
        "user": {"id": 17, "state": "active", "bot": False, "unselected_name": "PRIVATE_RESPONSE_CANARY"},
        "token": {
            "id": 91,
            "user_id": 17,
            "active": True,
            "revoked": False,
            "scopes": ["write_repository", "read_api"],
            "expires_at": expiry,
            "name": "PRIVATE_RESPONSE_CANARY",
        },
        "project": {
            "id": 37,
            "http_url_to_repo": "https://git.example.test/group/deploy.git",
            "path_with_namespace": "group/deploy",
            "description": "PRIVATE_RESPONSE_CANARY",
        },
        "member": {"id": 17, "state": "active", "access_level": 30, "expires_at": None},
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema_version", True),
        ("token_profile", "project-access-token"),
        ("token_profile", "group-access-token"),
        ("token_profile", "job-token"),
        ("token_profile", "deploy-token"),
        ("expected_project_access_level", 40),
        ("expected_project_access_level", True),
        ("expected_scopes", ["api", "write_repository"]),
        ("expected_scopes", ["write_repository"]),
        ("expected_scopes", ["read_api", "read_api"]),
        ("expected_scopes", ["read_api", 3]),
        ("expected_user_id", True),
        ("expected_token_id", 0),
        ("token_env", "bad-name"),
        ("expected_gitlab_version", "latest"),
        ("target_ref_prefix", "refs/heads/"),
        ("target_ref_prefix", "refs/heads/demo"),
        ("target_ref_prefix", "refs/heads/../"),
        ("destination_configuration_sha256", "x"),
    ],
)
def test_unsupported_or_ambiguous_configuration_rejected(key, value):
    config = configuration()
    config[key] = value
    with pytest.raises((ValueError, APIError)):
        writer.writer_configuration(canonical_bytes(config))


@pytest.mark.parametrize(
    "kind,key,value",
    [
        ("version", "version", "18.4.0-ee"),
        ("version", "revision", "unknown"),
        ("user", "id", True),
        ("user", "id", 18),
        ("user", "state", "blocked"),
        ("user", "bot", True),
        ("user", "bot", 0),
        ("user", "is_admin", True),
        ("user", "is_admin", 0),
        ("token", "id", 92),
        ("token", "user_id", 18),
        ("token", "active", False),
        ("token", "revoked", True),
        ("token", "scopes", ["api", "read_api", "write_repository"]),
        ("token", "granular", True),
        ("token", "granular", 0),
        ("token", "expires_at", None),
        ("token", "expires_at", "2000-01-01"),
        ("project", "id", 38),
        ("project", "http_url_to_repo", "https://other.invalid/group/deploy.git"),
        ("member", "id", 18),
        ("member", "access_level", 40),
        ("member", "state", "blocked"),
        ("member", "member_role_id", 12),
        ("member", "membership_state", "awaiting"),
        ("member", "expires_at", "2000-01-01"),
    ],
)
def test_response_mismatch_is_not_an_observed_grant(kind, key, value):
    data = documents()
    data[kind][key] = value
    with pytest.raises((APIError, ValueError)):
        writer._projection(
            kind,
            canonical_bytes(data[kind]),
            configuration(),
            selection("b" * 40),
            datetime.now(timezone.utc),
        )


def test_missing_admin_or_custom_role_fields_are_not_asserted_false():
    data = documents()
    now = datetime.now(timezone.utc)
    user = writer._projection(
        "user", canonical_bytes(data["user"]), configuration(), selection("b" * 40), now
    )
    member = writer._projection(
        "member", canonical_bytes(data["member"]), configuration(), selection("b" * 40), now
    )
    assert user["admin_status"] == "not-established"
    assert member["custom_role_field"] == member["membership_state"] == "not-returned"
    data["user"].pop("bot")
    with pytest.raises(APIError):
        writer._projection("user", canonical_bytes(data["user"]), configuration(), selection("b" * 40), now)
    data["member"].pop("expires_at")
    with pytest.raises(APIError):
        writer._projection(
            "member", canonical_bytes(data["member"]), configuration(), selection("b" * 40), now
        )


@pytest.fixture
def subject(case, tmp_path, monkeypatch):
    scope = {key: sha(key.encode()) for key in IDENTITY_KEYS}
    case["binding"]["run_scope"] = scope
    case["args"]["run_scope"] = scope
    args, _, _ = prepared_case(case)
    _, transport = git_transport.prepare_git_transport(**args)
    destination = selection(args["deployment_commit"])
    destination["target_ref"] = "refs/heads/demo/review-1"
    path = tmp_path.resolve() / "destination.json"
    path.write_bytes(canonical_bytes(destination))
    config = configuration(sha(path.read_bytes()))
    writer_path = tmp_path.resolve() / "writer.json"
    writer_path.write_bytes(canonical_bytes(config))
    data = documents()
    replies = [
        ReadResponse(200, canonical_bytes(data[kind]), "application/json")
        for kind in ("version", "user", "token", "project", "member")
    ] * 2
    selected = {key: args[key] for key in SCOPE}
    invoke = {
        "transport": transport,
        **selected,
        "plugin_id": "devsecops@devsecops-local",
        "plugin_version": "0.2.0-alpha.7",
        "destination_config": path,
        "expected_destination_config_sha256": sha(path.read_bytes()),
        "config": writer_path,
        "expected_config_sha256": sha(writer_path.read_bytes()),
        "output": tmp_path.resolve() / "writer-observation",
    }
    state = SimpleNamespace(
        args=invoke,
        config=config,
        destination=destination,
        data=data,
        replies=replies,
        calls=[],
        clients=[],
        packages=[],
        hook=None,
        case=case,
        transport_args=args,
    )

    def installed(package, plugin_id, version, deadline):
        state.packages.append(package)
        assert package == scope["package_sha256"]

    monkeypatch.setattr(writer, "_installed", installed)
    monkeypatch.setenv("SYNTHETIC_WRITER_PAT", "WRITER_TOKEN_CANARY")

    class Client:
        def __init__(self, origin, **options):
            self.origin, self.options, self.receipts = origin, options, []
            self._authorization = lambda: None
            state.clients.append(self)

        def get_bytes(self, path, *, accept):
            index = len(state.calls)
            state.calls.append({"path": path, "accept": accept, "authorization": self._authorization()})
            self.receipts.append({"method": "GET", "outcome": "complete", "request_index": index + 1})
            if state.hook is not None:
                state.hook(index)
            response = state.replies[index]
            if isinstance(response, BaseException):
                raise response
            return response

    monkeypatch.setattr(writer, "HTTPSReadClient", Client)
    return state


def update(subject):
    subject.args["config"].write_bytes(canonical_bytes(subject.config))
    subject.args["expected_config_sha256"] = sha(subject.args["config"].read_bytes())


def test_ten_same_secret_gets_preserve_sources_and_grant_no_write(subject, monkeypatch):
    roots = [subject.case["repo"], subject.case["args"]["output"], subject.transport_args["output"]]
    before = [inventory(root) for root in roots]
    subject.hook = (
        lambda index: monkeypatch.setenv("SYNTHETIC_WRITER_PAT", "CHANGED_AFTER_CAPTURE")
        if index == 0
        else None
    )
    result = writer.observe_git_writer(**subject.args)
    assert result["decision"] == "pass" and result["execution_status"] == "complete"
    verify_seal(result, "observation_sha256")
    assert result["snapshots"][0] == result["snapshots"][1]
    assert result["snapshots"][0]["user"]["admin_status"] == "not-established"
    paths = [
        "/api/v4/version",
        "/api/v4/user",
        "/api/v4/personal_access_tokens/self",
        "/api/v4/projects/37",
        "/api/v4/projects/37/members/all/17",
    ]
    assert [c["path"] for c in subject.calls] == paths * 2
    assert all(c["authorization"] == "Bearer WRITER_TOKEN_CANARY" for c in subject.calls)
    assert all(c._authorization() is None for c in subject.clients)
    assert all(
        c.options["max_requests"] == 10 and c.options["max_body_bytes"] == 65536 for c in subject.clients
    )
    assert result["write_authority"] == result["delivery_authority"] == "not-issued"
    assert result["branch_permission"] == result["least_privilege"] == "unverified"
    assert result["factory_decision"] == "unknown" and result["atomic_snapshot"] is False
    assert before == [inventory(root) for root in roots]
    for p in subject.args["output"].iterdir():
        assert b"CANARY" not in p.read_bytes()
        assert p.stat().st_mode & 0o077 == 0
    assert json.loads((subject.args["output"] / "git-writer-observation.json").read_bytes()) == result


@pytest.mark.parametrize(
    "failure",
    ["saved", "expired", "scope", "package", "destination-pin", "writer-pin", "prefix", "read-reference"],
)
def test_invalid_admission_stops_before_credentials_or_requests(subject, monkeypatch, failure):
    if failure == "saved":
        subject.args["transport"] = subject.args["transport"].describe()
    elif failure == "expired":
        git_transport._PLANS[subject.args["transport"]]["expires_monotonic"] = 0
    elif failure == "scope":
        subject.args["configuration_sha256"] = "0" * 64
    elif failure == "package":
        monkeypatch.setattr(
            writer, "_installed", lambda *a: (_ for _ in ()).throw(ValueError("changed package"))
        )
    elif failure == "destination-pin":
        subject.args["expected_destination_config_sha256"] = "0" * 64
    elif failure == "writer-pin":
        subject.args["expected_config_sha256"] = "0" * 64
    elif failure == "prefix":
        subject.config["target_ref_prefix"] = "refs/heads/other/"
        update(subject)
    else:
        subject.config["token_env"] = "UNIT_API_READ"
        update(subject)
    monkeypatch.setattr(writer, "_authorization", lambda *a: pytest.fail("Credential capture was reached"))
    with pytest.raises((ValueError, TypeError)):
        writer.observe_git_writer(**subject.args)
    assert not subject.calls and not subject.args["output"].exists()


@pytest.mark.parametrize(
    "failure",
    ["403", "redirect", "oversize", "pagination", "different-token", "blocked-user", "interruption"],
)
def test_failure_stops_without_retry_or_secret_leak(subject, failure):
    index = 2
    if failure == "403":
        subject.replies[index] = APIError("http-403")
    elif failure == "redirect":
        subject.replies[index] = APIError("redirect")
    elif failure == "oversize":
        subject.replies[index] = ReadResponse(200, b"x" * 65537, "application/json")
    elif failure == "pagination":
        subject.replies[index] = ReadResponse(200, b"{}", "application/json", pagination={"x-next-page": "2"})
    elif failure == "different-token":
        data = deepcopy(subject.data["token"])
        data["id"] = 92
        subject.replies[index] = ReadResponse(200, canonical_bytes(data), "application/json")
    elif failure == "blocked-user":
        index = 1
        data = deepcopy(subject.data["user"])
        data["state"] = "blocked"
        subject.replies[index] = ReadResponse(200, canonical_bytes(data), "application/json")
    else:
        subject.replies[index] = KeyboardInterrupt()
    if failure == "interruption":
        with pytest.raises(KeyboardInterrupt):
            writer.observe_git_writer(**subject.args)
        assert not (subject.args["output"] / "git-writer-observation.json").exists()
    else:
        result = writer.observe_git_writer(**subject.args)
        assert (
            result["decision"] == "unknown" and result["gaps"] and result["write_authority"] == "not-issued"
        )
    assert len(subject.calls) == index + 1
    assert all(c._authorization() is None for c in subject.clients)
    assert all(b"CANARY" not in p.read_bytes() for p in subject.args["output"].iterdir())


@pytest.mark.parametrize("failure", ["input", "package", "monotonic", "utc"])
def test_final_read_integrity_or_deadline_change_withholds_terminal(subject, monkeypatch, failure):
    def change(index):
        if index != 9:
            return
        if failure == "input":
            subject.args["config"].write_bytes(b"changed")
        elif failure == "package":
            monkeypatch.setattr(
                writer, "_installed", lambda *a: (_ for _ in ()).throw(ValueError("changed package"))
            )
        elif failure == "monotonic":
            original = writer.time.monotonic
            monkeypatch.setattr(writer.time, "monotonic", lambda: original() + 1000)
        else:

            class Late(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.now(tz) + timedelta(hours=1)

            monkeypatch.setattr(writer, "datetime", Late)

    subject.hook = change
    with pytest.raises((ValueError, APIError)):
        writer.observe_git_writer(**subject.args)
    assert len(subject.calls) == 10 and not (subject.args["output"] / "git-writer-observation.json").exists()
    assert all(c._authorization() is None for c in subject.clients)


def test_changed_second_snapshot_is_unknown_not_atomic_permission(subject):
    data = deepcopy(subject.data["version"])
    data["revision"] = "b" * 40
    subject.replies[5] = ReadResponse(200, canonical_bytes(data), "application/json")
    result = writer.observe_git_writer(**subject.args)
    assert result["decision"] == "unknown" and result["gaps"] == ["writer-observation-changed"]
    assert len(subject.calls) == 10 and result["write_authority"] == "not-issued"


def test_terminal_publication_rechecks_installed_and_utc_guard(subject, monkeypatch):
    original = writer._installed

    def expire(*args):
        original(*args)
        if (subject.args["output"] / "git-writer-observation.json").exists():
            clock = writer.time.monotonic
            monkeypatch.setattr(writer.time, "monotonic", lambda: clock() + 1000)

    monkeypatch.setattr(writer, "_installed", expire)
    with pytest.raises(ValueError):
        writer.observe_git_writer(**subject.args)
    assert not (subject.args["output"] / "git-writer-observation.json").exists()
    assert (subject.args["output"] / "writer-config.json").exists()


@pytest.mark.parametrize("failure", ["utc", "package", "interruption"])
def test_context_finalization_cannot_escape_final_guard(subject, monkeypatch, failure):
    original = writer.open_transport_consumer_output

    @contextmanager
    def wrapped(*args, **kwargs):
        with original(*args, **kwargs) as handle:
            yield handle
        if failure == "package":
            monkeypatch.setattr(
                writer, "_installed", lambda *a: (_ for _ in ()).throw(ValueError("late package change"))
            )
        elif failure == "interruption":
            raise KeyboardInterrupt
        else:

            class Late(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.now(tz) + timedelta(hours=1)

            monkeypatch.setattr(writer, "datetime", Late)

    monkeypatch.setattr(writer, "open_transport_consumer_output", wrapped)
    with pytest.raises(KeyboardInterrupt if failure == "interruption" else ValueError):
        writer.observe_git_writer(**subject.args)
    assert not (subject.args["output"] / "git-writer-observation.json").exists()
    assert (subject.args["output"] / "writer-config.json").exists()


def test_terminal_identity_requires_active_exact_completed_terminal(subject):
    args = {key: subject.args[key] for key in SCOPE}
    with git_transport.open_transport_consumer_output(
        subject.args["transport"], **args, output=subject.args["output"], deadline=time.monotonic() + 60
    ) as handle:
        handle.write_bytes("ordinary.json", b"{}")
        handle.write_bytes("terminal.json", b"{}", terminal=True)
        actual = (subject.args["output"] / "terminal.json").stat()
        assert handle.terminal_identity("terminal.json") == (actual.st_dev, actual.st_ino)
        for name in (None, "missing.json", "ordinary.json", "../terminal.json"):
            with pytest.raises(ValueError):
                handle.terminal_identity(name)
    with pytest.raises(ValueError):
        handle.terminal_identity("terminal.json")


@pytest.mark.parametrize("when", ["before-capture", "after-capture"])
@pytest.mark.parametrize("same_content", [True, False])
def test_terminal_replacement_is_never_adopted_or_removed(subject, monkeypatch, when, same_content):
    original = git_transport.TransportConsumerOutput.terminal_identity
    replacement = {}

    def replace():
        path = subject.args["output"] / "git-writer-observation.json"
        raw = path.read_bytes() if same_content else b"foreign terminal bytes"
        staged = path.with_name("replacement.tmp")
        staged.write_bytes(raw)
        staged.chmod(0o600)
        info = staged.stat()
        replacement.update(raw=raw, identity=(info.st_dev, info.st_ino))
        os.replace(staged, path)

    def capture(handle, name):
        if when == "before-capture":
            replace()
        identity = original(handle, name)
        if when == "after-capture":
            replace()
        return identity

    monkeypatch.setattr(git_transport.TransportConsumerOutput, "terminal_identity", capture)
    with pytest.raises(ValueError):
        writer.observe_git_writer(**subject.args)
    path = subject.args["output"] / "git-writer-observation.json"
    info = path.stat()
    assert (info.st_dev, info.st_ino) == replacement["identity"]
    assert path.read_bytes() == replacement["raw"]
    assert all(c._authorization() is None for c in subject.clients)
