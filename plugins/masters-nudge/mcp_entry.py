#!/usr/bin/env python3
"""Persistent Codex-facing MCP transport for synchronous post-patch judgment."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from masters_nudge import storage
from masters_nudge.codex_adapter import AUDIT_MARKER_KEY, CodexAdapter
from masters_nudge.contracts import ToolFault, json_text
from masters_nudge.core import NudgeCore
from masters_nudge.runtime import RuntimeSettings, active_guard


TOOLS = [{
    "name": "review_patch",
    "description": "Judge one completed apply_patch event and return optional context for the Actor's next decision.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "hook_event_name": {"const": "PostToolUse"},
            "session_id": {"type": "string", "minLength": 1},
            "turn_id": {"type": "string", "minLength": 1},
            "cwd": {"type": "string", "minLength": 1},
            "transcript_path": {},
            "tool_name": {"const": "apply_patch"},
            "tool_use_id": {"type": "string", "minLength": 1},
            "tool_input": {},
            "tool_response": {},
        },
        "required": [
            "hook_event_name", "session_id", "turn_id", "cwd", "transcript_path",
            "tool_name", "tool_use_id", "tool_input", "tool_response",
        ],
        "additionalProperties": False,
    },
}]


def _reply(output: dict | None) -> tuple[dict, str | None]:
    if output is None:
        return {"content": []}, None
    attempt = output.pop(AUDIT_MARKER_KEY, None)
    return {"content": [{"type": "text", "text": json_text(output)}]}, attempt


def serve(core: NudgeCore, adapter: CodexAdapter, *, input_stream=sys.stdin, output_stream=sys.stdout):
    for line in input_stream:
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if "id" not in request:
            continue
        method = request.get("method")
        params = request.get("params") or {}
        attempt = None
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "masters-nudge", "version": "1.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call" and params.get("name") == "review_patch":
            try:
                arguments = params.get("arguments")
                if not isinstance(arguments, dict):
                    raise ToolFault("input", "MCP 事件必須是 JSON 物件")
                result, attempt = _reply(adapter.process(dict(arguments)))
            except Exception as exc:
                core.log_error(str(exc))
                fault = {"systemMessage": "本輪反饋未執行"}
                if core.settings.strict:
                    fault.update({"continue": False, "stopReason": "Masters’ Nudge 測試故障，本輪無效"})
                result = {"content": [{"type": "text", "text": json_text(fault)}]}
        elif method == "ping":
            result = {}
        else:
            response = {"jsonrpc": "2.0", "id": request["id"],
                        "error": {"code": -32601, "message": "Unknown method"}}
            output_stream.write(json_text(response) + "\n")
            output_stream.flush()
            continue
        response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        output_stream.write(json_text(response) + "\n")
        output_stream.flush()
        if attempt:
            try:
                core.journal.delivered(attempt)
            except Exception as exc:
                core.log_error(f"反饋已送出，但交付紀錄寫入失敗：{exc}")


def main() -> int:
    if active_guard():
        return 0
    settings = RuntimeSettings.from_env(Path(__file__).resolve().parent, host="codex_cli")

    def log_error(message):
        try:
            storage.append_error(settings.paths.error_log, "codex-mcp", message)
        except OSError as exc:
            print(f"Masters' Nudge: {message}; error log unavailable: {exc}", file=sys.stderr, flush=True)

    core = NudgeCore(settings, log_error=log_error)
    serve(core, CodexAdapter(core))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
