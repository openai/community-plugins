"""TTY controls exercise original engine/aggregate; no model or live calls."""

import io
import json
import pytest

from devsecops.factory import resident_cleanup_control as control
from devsecops.factory import cli
from devsecops.factory.state import StateError
from tests import test_cleanup_continuation as fixture_module
from tests.test_factory_cli import arguments

resident = fixture_module.resident


class Terminal(io.StringIO):
    def isatty(self):
        return True

    def fileno(self):
        return 99


def terminal(monkeypatch, response):
    output = Terminal()
    monkeypatch.setattr(control.sys, "stdin", Terminal())
    monkeypatch.setattr(control.sys, "stderr", output)

    def line(fd, deadline):
        assert fd == 99
        prompt = json.loads(output.getvalue().splitlines()[-1])
        return response(prompt)

    monkeypatch.setattr(control, "_line", line)
    return output


def test_terminal_explicit_reconcile_then_fresh_continue_finalizes_original_run(resident, monkeypatch):
    engine, _, _, calls, _, _ = resident
    output = terminal(monkeypatch, lambda prompt: prompt["command"])
    result = control.wait_for_cleanup(engine, engine._snapshot("reconciliation_required"), enabled=True)
    assert result["terminal"] and not result["canonical_success"]
    prompts = [json.loads(row) for row in output.getvalue().splitlines()]
    assert [row["action"] for row in prompts] == [
        "read-only-reconciliation",
        "new-authorized-cleanup-operation",
    ]
    assert prompts[0]["command"].split()[-1] != prompts[1]["command"].split()[-1]
    assert len([row for row in calls if row[:2] == ("service-links", "down")]) == 1


@pytest.mark.parametrize("response", ["stop", "timeout", "wrong-nonce"])
def test_stop_timeout_or_wrong_challenge_does_not_dispatch_cleanup(resident, monkeypatch, response):
    engine, _, previous, calls, _, _ = resident
    before = list(calls)
    terminal(
        monkeypatch,
        lambda prompt: prompt["stop_command"]
        if response == "stop"
        else None
        if response == "timeout"
        else "continue " + previous + " invalid",
    )
    result = engine._snapshot("reconciliation_required")
    if response == "wrong-nonce":
        with pytest.raises(StateError, match="fresh terminal challenge"):
            control.wait_for_cleanup(engine, result, enabled=True)
    else:
        assert control.wait_for_cleanup(engine, result, enabled=True) == result
    assert calls == before and engine.journal.state["active_mutation"]["operation_id"] == previous


def test_non_terminal_input_rejected_and_control_opt_in_is_required(resident, monkeypatch):
    engine, _, _, calls, _, _ = resident
    monkeypatch.setattr(control.sys, "stdin", io.StringIO())
    before = list(calls)
    result = engine._snapshot("reconciliation_required")
    assert control.wait_for_cleanup(engine, result) == result
    with pytest.raises(StateError, match="interactive"):
        control.wait_for_cleanup(engine, result, enabled=True)
    assert calls == before


def test_command_flag_requires_resident_native_driver_before_creating_journal(tmp_path, capsys):
    output = tmp_path / "must-not-exist"
    assert cli.main([*arguments(tmp_path), "--output", str(output), "--resident-cleanup-controls"]) == 2
    assert not output.exists()
    assert "resident driver" in capsys.readouterr().err


def test_canonical_native_command_reaches_control_before_first_model_handoff(resident, monkeypatch):
    from devsecops.factory import native_driver
    from devsecops.factory.native_session import NativeCodexSelection

    engine, _, _, calls, _, _ = resident
    selection = object.__new__(NativeCodexSelection)
    object.__setattr__(selection, "max_trace_bytes", 1024)
    monkeypatch.setattr(NativeCodexSelection, "__post_init__", lambda self: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("No native/model execution belongs to cleanup controls")

    monkeypatch.setattr(native_driver, "NativeFactorySession", forbidden)
    terminal(monkeypatch, lambda prompt: prompt["command"])
    result = native_driver.run(engine, selection=selection, cleanup_controls=True)
    assert result["terminal"] and engine.journal.state["execution_status"] == "released"
    assert len([row for row in calls if row[:2] == ("service-links", "continue")]) == 1


def test_late_terminal_read_cannot_dispatch_after_control_window(resident, monkeypatch):
    from types import SimpleNamespace

    engine, _, previous, calls, _, _ = resident
    clock = [100.0]
    monkeypatch.setattr(control, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def late(prompt):
        clock[0] = 401.0
        return prompt["command"]

    terminal(monkeypatch, late)
    before = list(calls)
    result = engine._snapshot("reconciliation_required")
    assert control.wait_for_cleanup(engine, result, enabled=True) == result
    assert calls == before and engine.journal.state["active_mutation"]["operation_id"] == previous
