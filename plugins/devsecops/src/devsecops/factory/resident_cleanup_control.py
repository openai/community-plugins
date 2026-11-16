"""Bounded explicit terminal controls retaining the original lifecycle process.

Only an opted-in interactive terminal can request these fixed actions. No saved
report, control file, pipe, command callback or automatic deletion retry exists.
"""

from __future__ import annotations

import json
import os
import secrets
import select
import sys
import time

from .state import StateError, PARENT_DISPOSAL_MODE


def require_terminal():
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise StateError("Resident cleanup controls require interactive stdin and stderr terminals")
    return sys.stdin.fileno()


def _line(fd, deadline):
    value = bytearray()
    while len(value) < 512:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        readable, _, _ = select.select([fd], [], [], min(60, remaining))
        if not readable:
            continue
        item = os.read(fd, 1)
        if not item:
            return None
        if item == b"\n":
            if time.monotonic() >= deadline:
                return None
            try:
                return value.decode("ascii").strip()
            except UnicodeDecodeError as error:
                raise StateError("Cleanup control must be bounded ASCII") from error
        value.extend(item)
    raise StateError("Cleanup control exceeds its bound")


def wait_for_cleanup(engine, result, *, enabled=False):
    if not enabled:
        return result
    from .lifecycle import LifecycleEngine

    if type(engine) is not LifecycleEngine:
        raise StateError("Resident cleanup controls require the original lifecycle engine")
    fd = require_terminal()
    deadline = time.monotonic() + 300
    for _ in range(16):
        state = engine.journal.state
        if state["execution_status"] == "released":
            return engine.advance()
        pending = state["active_mutation"]
        previous = next((o for o in reversed(state["operations"]) if o["action"] == "down"), None)
        if pending is not None and pending["action"] == "down":
            action, operation = "reconcile", pending["operation_id"]
        elif pending is None and previous is not None and previous["status"] == "partial":
            modes = previous.get("cleanup_continuation_modes", ["normal"])
            if modes not in (["normal"], [PARENT_DISPOSAL_MODE]):
                raise StateError("Unsupported cleanup continuation selection")
            action = "dispose-runner-guest" if modes == [PARENT_DISPOSAL_MODE] else "continue"
            operation = previous["operation_id"]
        else:
            return result
        nonce = secrets.token_hex(12)
        print(
            json.dumps(
                {
                    "kind": "resident-cleanup-control",
                    "run_id": state["run_id"],
                    "operation_id": operation,
                    "state": "unknown" if action == "reconcile" else "terminal-partial",
                    "command": f"{action} {operation} {nonce}",
                    "stop_command": f"stop {nonce}",
                    "action": "read-only-reconciliation"
                    if action == "reconcile"
                    else "new-authorized-runner-guest-disposal"
                    if action == "dispose-runner-guest"
                    else "new-authorized-cleanup-operation",
                    "expires_in_seconds": max(0, int(deadline - time.monotonic())),
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        command = _line(fd, deadline)
        if time.monotonic() >= deadline or command is None or command == f"stop {nonce}":
            return result
        if command != f"{action} {operation} {nonce}":
            raise StateError(
                "Cleanup command does not match the current operation and fresh terminal challenge"
            )
        try:
            result = (
                engine.reconcile_cleanup(operation_id=operation)
                if action == "reconcile"
                else engine.continue_cleanup(prior_operation_id=operation,
                    cleanup_mode=PARENT_DISPOSAL_MODE)
                if action == "dispose-runner-guest"
                else engine.continue_cleanup(prior_operation_id=operation)
            )
        except Exception:
            # Do not disclose provider/credential details. The current journal
            # determines whether the next explicit action is still read-only.
            result = engine._snapshot("cleanup-control-requires-inspection")
            print(
                '{"kind":"resident-cleanup-control","action_result":"not-completed-inspect-retained-evidence"}',
                file=sys.stderr,
                flush=True,
            )
    return result
