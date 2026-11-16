"""Controlled subprocess and canonical D6 caller tests; no model or lab calls.

The peer is explicitly synthetic. Scope preparation/admission is controlled in
caller tests; real process supervision, pinned peer preflight, child journals,
artifact byte auditing and rejection of late results execute unchanged.
"""

from copy import deepcopy
from pathlib import Path
import json
import os
import pickle
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from test_native_codex import lab
from devsecops.factory import case_interruption as case, native_codex as native
from devsecops.factory.native_phase import NativeSourceSelection
from devsecops.factory.native_session import NativeCodexSelection
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha

__all__ = ["lab"]


def process(script, tmp_path):
    watch = native._TurnInterruption()
    result = native._process(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        stdin=b"",
        deadline=time.monotonic() + 3,
        limit=100000,
        environ=dict(os.environ),
        _interruption=watch,
    )
    return watch, result


def test_real_turn_cancellation_reaps_sigint_ignoring_peer(tmp_path):
    watch, result = process(
        """
import json, signal, time
signal.signal(signal.SIGINT, signal.SIG_IGN)
print(json.dumps({'type':'thread.started','thread_id':'controlled-thread'}),flush=True)
print(json.dumps({'type':'turn.started'}),flush=True)
time.sleep(120)
""",
        tmp_path,
    )
    assert result["reason"] == "cancelled-after-turn-start"
    assert watch.spawned and watch.turn_started and watch.interrupted_alive and watch.reaped
    assert watch.thread_id == "controlled-thread"


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "turn.started"}],
        [{"type": "thread.started", "thread_id": "bad/id"}],
        [{"type": "thread.started", "thread_id": "valid"}, {"type": "turn.completed"}],
        [
            {"type": "thread.started", "thread_id": "valid"},
            {"type": "item.completed", "item": {"type": "mcp_tool_call"}},
        ],
    ],
)
def test_malformed_or_unstarted_turn_never_qualifies(events, tmp_path):
    script = (
        "import json,time\nevents="
        + repr(events)
        + "\nfor e in events: print(json.dumps(e),flush=True)\ntime.sleep(120)"
    )
    watch, result = process(script, tmp_path)
    assert result["reason"] == "invalid-interrupted-turn-start"
    assert watch.spawned and watch.reaped and not watch.turn_started


def test_pre_dispatch_cancel_has_no_spawn_proof(tmp_path):
    stop = threading.Event()
    stop.set()
    watch = native._TurnInterruption()
    result = native._process(
        [sys.executable, "-c", "raise SystemExit(99)"],
        cwd=tmp_path,
        stdin=b"",
        deadline=time.monotonic() + 2,
        limit=10000,
        environ=dict(os.environ),
        cancel=stop,
        _interruption=watch,
    )
    assert result["spawned"] is False
    assert not watch.spawned and not watch.reaped and not watch.turn_started


def test_fragmented_turn_start_and_startup_notice(tmp_path):
    script = """
import json,sys,time
print(json.dumps({'type':'thread.started','thread_id':'controlled-thread'}),flush=True)
print(json.dumps({'type':'item.completed','item':{'id':'notice','type':'notice','message':'controlled'}}),flush=True)
sys.stdout.write('{"type":"turn.');sys.stdout.flush();time.sleep(.05)
print('started"}',flush=True)
time.sleep(120)
"""
    watch, result = process(script, tmp_path)
    assert result["reason"] == "cancelled-after-turn-start" and watch.reaped and watch.turn_started


@pytest.fixture
def caller(lab, tmp_path, monkeypatch):
    args = lab["args"]
    identities = lab["request"]["identities"]
    parent = RunJournal.create(
        tmp_path / "parent-run", identities=identities, environment_id="controlled-env"
    )
    selection = NativeCodexSelection(
        installed_root=args["installed_root"],
        package_sha256=args["package_sha256"],
        plugin_id=args["plugin_id"],
        executable=args["executable"],
        expected_client_sha256=_sha(args["executable"].read_bytes()),
        expected_client_version=args["expected_client_version"],
        model=args["model"],
        reasoning_effort=args["reasoning_effort"],
        engine_python=Path(args["engine_python"]),
        total_timeout=20,
    )
    session = SimpleNamespace(_cancel=threading.Event())
    source = NativeSourceSelection(
        repository=tmp_path / "repo",
        base_commit="a" * 40,
        fault_commit="b" * 40,
        profile=tmp_path / "profile",
        profile_sha256="c" * 64,
        correction_contract=tmp_path / "contract",
        correction_contract_sha256="d" * 64,
        tools={},
        schemas=tmp_path / "schemas",
        schemas_sha256="e" * 64,
    )
    production = SimpleNamespace(operations=SimpleNamespace(source_selection=lambda attempt: source))
    context = SimpleNamespace(
        run_id=parent.state["run_id"],
        identities=identities,
        inputs={"config": {"expires_at": lab["request"]["expires_at"]}},
        deadline=time.monotonic() + 40,
        remaining_seconds=lambda: 35,
    )
    monkeypatch.setattr(case, "_admit", lambda context, production: (session, parent, selection))

    def scope(**kwargs):
        path = kwargs["output"]
        path.mkdir()
        input_path = path / "review-profile"
        input_path.write_bytes(b'{"controlled_only":true}\n')
        return SimpleNamespace(input_artifacts={"review-profile": input_path})

    monkeypatch.setattr(case.native_reports, "prepare_review_scope", scope)
    monkeypatch.setattr(
        case.native_reports,
        "_scope",
        lambda scope: {
            "paths": scope.input_artifacts,
            "raw": {name: path.read_bytes() for name, path in scope.input_artifacts.items()},
        },
    )
    monkeypatch.setattr(case.native_reports, "_audit", lambda prepared, deadline: None)
    output = parent.run_dir / "interrupted"
    lab["mode"]("cancel")
    return SimpleNamespace(
        parent=parent,
        selection=selection,
        session=session,
        context=context,
        production=production,
        output=output,
        lab=lab,
    )


def run(caller):
    return case.execute_interrupted_review(caller.context, production=caller.production, output=caller.output)


def test_canonical_caller_real_peer_child_cancellation_and_late_result_rejected(caller):
    before = deepcopy(caller.parent.state)
    proof = run(caller)
    binding = case.require_interruption(proof, journal=caller.parent)
    assert binding["status"] == "cancelled" and binding["spawned"] and binding["reaped"]
    assert binding["accepted_results"] == 0 and binding["usable_success_receipt"] is False
    assert binding["child_run_id"] != binding["parent_run_id"]
    assert caller.parent.state == before
    artifacts = case.interruption_artifacts(proof, journal=caller.parent)
    assert len(artifacts) < 192 and all(path.is_relative_to(caller.parent.run_dir) for path in artifacts)
    assert caller.output / "execution/trace.jsonl" in artifacts
    assert caller.output / "request-run/journal/00000003.json" in artifacts
    record = case._PROOFS[proof]
    child, request = record["child"], record["request"]
    late = child.run_dir / request["result_path"]
    late.write_bytes(_canonical({"status": "completed", "fake_late_output": True}))
    with pytest.raises(StateError, match="No native result is pending"):
        child.accept_native(late, identities=child.identities, validator=lambda *_: ["forged"])
    assert case.require_interruption(proof, journal=caller.parent)["accepted_results"] == 0
    with pytest.raises(StateError):
        pickle.dumps(proof)
    with pytest.raises(StateError):
        case.require_interruption(binding, journal=caller.parent)


@pytest.mark.parametrize("target", ["execution/trace.jsonl", "request-run/state.json"])
def test_original_proof_rejects_changed_bytes(caller, target):
    proof = run(caller)
    path = caller.output / target
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(StateError):
        case.require_interruption(proof, journal=caller.parent)


def test_original_proof_is_historical_without_deadline_renewal(caller):
    proof = run(caller)
    caller.context.deadline = 0
    caller.session._cancel.set()
    assert case.require_interruption(proof, journal=caller.parent)["status"] == "cancelled"
    reopened = RunJournal.open(caller.parent.run_dir, identities=caller.parent.identities)
    with pytest.raises(StateError):
        case.require_interruption(proof, journal=reopened)


def test_caller_preflight_failure_cancels_child_without_issuing_proof(caller):
    caller.lab["settings"].write_text(json.dumps({"version": "wrong-client"}))
    count = len(case._PROOFS)
    with pytest.raises(StateError):
        run(caller)
    child = RunJournal.open(caller.output / "request-run", identities=caller.parent.identities)
    assert child.state["active_native"] is None and child.state["native_results"] == []
    assert len(child.state["native_cancellations"]) == 1 and len(case._PROOFS) == count


def test_interruption_mode_never_publishes_success_even_with_late_complete_trace(lab):
    # This peer emits the complete result in a burst; any already-exited peer
    # cannot qualify a real interruption, but neither path may publish success.
    watch = native._TurnInterruption()
    receipt = native.execute_native_codex(**lab["args"], _interruption=watch)
    assert receipt["execution_status"] == "incomplete" and receipt["result_sha256"] is None
    assert not (lab["root"] / "step/result.json").exists()
    assert not (lab["args"]["output"] / "native-result.json").exists()


def test_buffered_completed_turn_cannot_be_classified_as_active_interruption(tmp_path):
    script = """
import os,time
os.write(1,b'{"type":"thread.started","thread_id":"controlled"}\\n{"type":"turn.started"}\\n{"type":"turn.completed"}\\n')
time.sleep(120)
"""
    watch, result = process(script, tmp_path)
    assert result["reason"] == "invalid-interrupted-turn-start"
    assert watch.spawned and watch.reaped and not watch.interrupted_alive


def test_canonical_caller_rejects_buffered_completed_turn_still_alive(caller):
    binary = caller.lab["args"]["executable"]
    raw = binary.read_text()
    raw = raw.replace(
        "emit({'type': 'turn.started'})",
        """os.write(1,b'{"type":"turn.started"}\\n{"type":"turn.completed"}\\n')
time.sleep(120)""",
    )
    binary.write_text(raw)
    from dataclasses import replace

    selected = replace(caller.selection, expected_client_sha256=_sha(binary.read_bytes()))
    # Rebind only the explicit controlled selection to changed synthetic peer.
    original = case._admit

    def admitted(context, production):
        session, journal, _ = original(context, production)
        return session, journal, selected

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(case, "_admit", admitted)
        count = len(case._PROOFS)
        with pytest.raises(StateError, match="actual interrupted"):
            run(caller)
    assert len(case._PROOFS) == count
    child = RunJournal.open(caller.output / "request-run", identities=caller.parent.identities)
    assert child.state["native_results"] == [] and child.state["active_native"] is None


def test_unissued_original_looking_object_is_not_a_process_proof(caller):
    with pytest.raises(StateError):
        case.InterruptedReview()
    unissued = object.__new__(case.InterruptedReview)
    with pytest.raises(StateError):
        case.require_interruption(unissued, journal=caller.parent)
