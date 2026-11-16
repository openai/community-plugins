"""Direct issued handles and real native local Git; scripted base GETs only."""

import hashlib
import os
import time
from contextlib import contextmanager

import pytest

from devsecops.factory import git_base_possession as base
from devsecops.factory import git_transport
from devsecops.factory import git_native_request as request
from test_git_base_possession import subject
from test_deployment_commit import case
from test_git_transport import SCOPE, inventory
from test_git_destination import packet

__all__ = ["case", "subject"]


@pytest.fixture
def ready(subject, tmp_path):
    report, observed = base.verify_git_base_possession(**subject.args)
    assert report["decision"] == "pass", report
    args = dict(
        transport=subject.plan,
        observed_base=observed,
        **{k: subject.args[k] for k in SCOPE},
        expected_destination_sha256=subject.args["expected_config_sha256"],
        advertisement=advert(subject),
        output=tmp_path.resolve() / "request",
    )
    return subject, args


def advert(subject):
    return (
        packet(b"# service=git-receive-pack\n")
        + b"0000"
        + packet(
            subject.config["base_commit"].encode()
            + b" refs/heads/main\0report-status delete-refs ofs-delta object-format=sha1 agent=git/2.54.0\n"
        )
        + b"0000"
    )


def test_native_request_is_local_create_only_and_source_preserved(ready):
    subject, args = ready
    before = inventory(subject.case["repo"])
    transport_before = inventory(subject.args["output"].parent / "transport")
    report, body = request.prepare_native_git_request(**args)
    assert report["decision"] == "request-prepared"
    assert report["write_attempts"] == report["network_requests"] == 0
    assert report["delivery_authority"] == "not-issued" and report["omission_authorized"] is False
    assert report["mutation_effect"] == "not-attempted" and report["native_delivery_exit"] is None
    assert report["sender_outcome"] == "cancelled-before-server-status"
    assert report["owned_sender_cleanup"] and report["private_scratch_removed"]
    assert report["object_contract"]["required"]
    assert set(report["object_contract"]["required"]) <= set(report["object_contract"]["emitted"])
    assert report["request"] == {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    assert (args["output"] / "request.body").read_bytes() == body
    assert inventory(subject.case["repo"]) == before
    assert inventory(subject.args["output"].parent / "transport") == transport_before
    assert not list(args["output"].parent.glob(".git-request-*"))


@pytest.mark.parametrize(
    "field,change",
    [
        ("transport", "dict"),
        ("transport", "unissued"),
        ("observed_base", "dict"),
        ("observed_base", "unissued"),
        ("run_scope", "mismatch"),
        ("expected_destination_sha256", "mismatch"),
    ],
)
def test_reject_unissued_or_mismatched_before_output(ready, field, change):
    subject, args = ready
    args = dict(args)
    if change == "dict":
        args[field] = args[field].describe()
    elif change == "unissued":
        args[field] = object.__new__(type(args[field]))
    else:
        args[field] = "f" * 64
    with pytest.raises((ValueError, TypeError)):
        request.prepare_native_git_request(**args)
    assert not args["output"].exists()


@pytest.mark.parametrize("kind", ["expired", "pid", "wrong-transport", "artifact", "config"])
def test_current_observation_is_rechecked(ready, kind):
    subject, args = ready
    record = base._OBSERVATIONS[args["observed_base"]]
    if kind == "expired":
        record["expires_monotonic"] = time.monotonic() - 1
    elif kind == "pid":
        record["pid"] = os.getpid() + 1
    elif kind == "wrong-transport":
        record["transport"] = object.__new__(git_transport.PreparedGitTransport)
    elif kind == "artifact":
        (subject.args["output"] / "verified-objects.bin").write_bytes(b"changed")
    else:
        subject.args["config"].write_bytes(b"{}")
    with pytest.raises(ValueError):
        request.prepare_native_git_request(**args)
    assert not args["output"].exists()


@pytest.mark.parametrize("where", ["source", "transport", "base"])
def test_protected_output_paths_rejected(ready, where):
    subject, args = ready
    targets = {
        "source": subject.case["repo"],
        "transport": subject.args["output"].parent / "transport",
        "base": subject.args["output"],
    }
    args = {**args, "output": targets[where] / "consumer"}
    with pytest.raises(ValueError):
        request.prepare_native_git_request(**args)
    assert not args["output"].exists()


def test_late_drift_withdraws_terminal(ready, monkeypatch):
    subject, args = ready
    original = request._emitted_contract

    def changed(*a, **kw):
        result = original(*a, **kw)
        base._OBSERVATIONS[args["observed_base"]]["expires_monotonic"] = time.monotonic() - 1
        return result

    monkeypatch.setattr(request, "_emitted_contract", changed)
    with pytest.raises(ValueError):
        request.prepare_native_git_request(**args)
    assert not (args["output"] / "native-request.json").exists()
    assert not list(args["output"].parent.glob(".git-request-*"))


def test_emitted_bytes_not_trusted_from_object_names(ready, monkeypatch):
    subject, args = ready
    original = request.command_pack

    def changed(*a, **kw):
        pack = original(*a, **kw)
        return pack[:-1] + bytes([pack[-1] ^ 1])

    monkeypatch.setattr(request, "command_pack", changed)
    with pytest.raises(ValueError):
        request.prepare_native_git_request(**args)
    assert not (args["output"] / "native-request.json").exists()
    assert not list(args["output"].parent.glob(".git-request-*"))


def test_cancel_during_body_cleans_owned_process_and_scratch(ready, monkeypatch):
    subject, args = ready

    def cancel(child):
        raise KeyboardInterrupt()

    monkeypatch.setattr(request, "read_body", cancel)
    with pytest.raises(KeyboardInterrupt):
        request.prepare_native_git_request(**args)
    assert not (args["output"] / "native-request.json").exists()
    assert not list(args["output"].parent.glob(".git-request-*"))


def test_source_context_final_failure_withdraws_terminal(ready, monkeypatch):
    subject, args = ready
    original = request._open_request_source

    @contextmanager
    def changed(*a, **kw):
        with original(*a, **kw) as value:
            yield value
            raise ValueError("Controlled source final audit failure")

    monkeypatch.setattr(request, "_open_request_source", changed)
    with pytest.raises(ValueError, match="Controlled source"):
        request.prepare_native_git_request(**args)
    assert not (args["output"] / "native-request.json").exists()
    assert not list(args["output"].parent.glob(".git-request-*"))


def test_native_sender_omits_only_exact_observed_shared_blob(case, tmp_path, monkeypatch):
    # Arrange an existing base blob equal to the planned values replacement.
    from test_deployment_commit import git

    raw = (tmp_path / "proposal" / "values-after.yaml").read_bytes()
    (case["repo"] / "already-present.yaml").write_bytes(raw)
    git(case["repo"], "add", "--all")
    git(case["repo"], "commit", "--quiet", "-m", "Existing shared prepared blob")
    state = subject.__wrapped__(case, tmp_path, monkeypatch)
    report, observed = base.verify_git_base_possession(**state.args)
    assert report["decision"] == "pass"
    args = dict(
        transport=state.plan,
        observed_base=observed,
        **{k: state.args[k] for k in SCOPE},
        expected_destination_sha256=state.args["expected_config_sha256"],
        advertisement=advert(state),
        output=tmp_path.resolve() / "shared-request",
    )
    report, body = request.prepare_native_git_request(**args)
    contract = report["object_contract"]
    oid = base._object_id("blob", raw)
    assert contract["optional_shared"] == contract["omitted_shared"] == [oid]
    assert oid not in contract["emitted"]
    assert report["omission_authorized"] is False and report["write_attempts"] == 0


@pytest.mark.parametrize("artifact", ["base.pack", "prepared.pack"])
def test_transport_artifact_drift_rejected_before_output(ready, artifact):
    subject, args = ready
    target = subject.args["output"].parent / "transport" / artifact
    target.chmod(0o600)
    target.write_bytes(b"corrupt transport")
    with pytest.raises(ValueError):
        request.prepare_native_git_request(**args)
    assert not args["output"].exists()


def test_tool_snapshot_mismatch_cleans_private_scratch(ready, monkeypatch):
    subject, args = ready
    original = request._snapshot_to

    def mismatch(*a, **kw):
        row = original(*a, **kw)
        return {**row, "sha256": "a" * 64}

    monkeypatch.setattr(request, "_snapshot_to", mismatch)
    with pytest.raises(ValueError, match="Git changed"):
        request.prepare_native_git_request(**args)
    assert not (args["output"] / "native-request.json").exists()
    assert not list(args["output"].parent.glob(".git-request-*"))


@pytest.mark.parametrize("exit_audit", [1, 2])
def test_base_expiry_in_consumer_finalization_withdraws_terminal(ready, monkeypatch, exit_audit):
    subject, args = ready
    original_source = request._open_request_source
    original_audit = git_transport._audit_consumer
    closed = False
    count = 0

    @contextmanager
    def source(*a, **kw):
        nonlocal closed
        with original_source(*a, **kw) as value:
            yield value
        closed = True

    def audit(record):
        nonlocal count
        if closed:
            count += 1
            if count == exit_audit:
                base._OBSERVATIONS[args["observed_base"]]["expires_monotonic"] = time.monotonic() - 1
        return original_audit(record)

    monkeypatch.setattr(request, "_open_request_source", source)
    monkeypatch.setattr(git_transport, "_audit_consumer", audit)
    with pytest.raises(ValueError):
        request.prepare_native_git_request(**args)
    assert count == exit_audit
    assert not (args["output"] / "native-request.json").exists()
    assert not list(args["output"].parent.glob(".git-request-*"))


def test_additive_consumer_guard_audits_now_and_cannot_be_replaced(ready):
    subject, args = ready
    calls = []
    with git_transport.open_transport_consumer_output(
        args["transport"],
        **{k: args[k] for k in SCOPE},
        output=args["output"],
        deadline=time.monotonic() + 60,
    ) as admitted:
        git_transport._bind_consumer_audit(admitted, lambda: calls.append("guard"))
        assert calls == ["guard"]
        with pytest.raises(ValueError, match="Exactly one"):
            git_transport._bind_consumer_audit(admitted, lambda: None)
        admitted.write_bytes("test.json", b"{}", terminal=True)
    assert len(calls) >= 5


def test_guard_registration_failure_withdraws_terminal(ready):
    subject, args = ready

    def reject():
        raise ValueError("current observation rejected")

    with pytest.raises(ValueError, match="observation rejected"):
        with git_transport.open_transport_consumer_output(
            args["transport"],
            **{k: args[k] for k in SCOPE},
            output=args["output"],
            deadline=time.monotonic() + 60,
        ) as admitted:
            admitted.write_bytes("test.json", b"{}", terminal=True)
            git_transport._bind_consumer_audit(admitted, reject)
    assert not (args["output"] / "test.json").exists()


def test_clock_jump_during_base_audit_rejected_before_output(ready, monkeypatch):
    subject, args = ready
    original = request.require_observed_git_base
    wall = request.time.time
    count = 0
    jumped = False

    def checked(*a, **kw):
        nonlocal count, jumped
        result = original(*a, **kw)
        count += 1
        if count == 2:
            jumped = True
        return result

    monkeypatch.setattr(request, "require_observed_git_base", checked)
    monkeypatch.setattr(request.time, "time", lambda: wall() + (10 if jumped else 0))
    with pytest.raises(ValueError, match="clock"):
        request.prepare_native_git_request(**args)
    assert not args["output"].exists()
