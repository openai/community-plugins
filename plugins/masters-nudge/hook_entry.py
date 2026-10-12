#!/usr/bin/env python3
"""Codex hook process: emit context only after a successful core judgment."""
import argparse
import json
import sys
from pathlib import Path
from masters_nudge import storage
from masters_nudge.codex_adapter import AUDIT_MARKER_KEY, CodexAdapter
from masters_nudge.contracts import ToolFault
from masters_nudge.core import NudgeCore
from masters_nudge.runtime import RuntimeSettings, active_guard


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="codex_cli", choices=["codex_cli"])
    args = parser.parse_args()
    if active_guard():
        return 0
    settings = RuntimeSettings.from_env(Path(__file__).resolve().parent, host=args.host)
    def log_error(message):
        try:
            storage.append_error(settings.paths.error_log, "codex-hook", message)
        except OSError as exc:
            # A broken journal must not prevent the host from receiving fault status.
            print(f"Masters' Nudge: {message}; error log unavailable: {exc}", file=sys.stderr)
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ToolFault("input_size", "事件超過 1 MiB")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ToolFault("input", "事件必須是 JSON 物件")
        core = NudgeCore(settings, log_error=log_error)
        output = CodexAdapter(core).process(payload)
        if output is not None:
            attempt = output.pop(AUDIT_MARKER_KEY, None)
            sys.stdout.write(json.dumps(output, ensure_ascii=True) + "\n")
            sys.stdout.flush()
            if attempt:
                try:
                    core.journal.delivered(attempt)
                except Exception as exc:
                    # The host already received one response; it cannot be retracted
                    # by appending a contradictory second JSON document.
                    log_error(f"反饋已送出，但交付紀錄寫入失敗：{exc}")
                    return 1 if settings.strict else 0
        return 0
    except Exception as exc:
        log_error(str(exc))
        output = {"systemMessage": "本輪反饋未執行"}
        if settings.strict:
            output.update({"continue": False, "stopReason": "Masters’ Nudge 測試故障，本輪無效"})
        print(json.dumps(output, ensure_ascii=True))
        return 1 if settings.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
