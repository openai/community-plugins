#!/usr/bin/env python3
"""Persistent Codex-facing MCP transport for synchronous post-patch judgment."""
from __future__ import annotations

import json
import queue
import threading
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
MAX_PENDING_REQUESTS = 64
MAX_REQUEST_CHARS = 1024 * 1024


def _reply(output: dict | None) -> tuple[dict, str | None]:
    if output is None:
        return {"content": []}, None
    attempt = output.pop(AUDIT_MARKER_KEY, None)
    return {"content": [{"type": "text", "text": json_text(output)}]}, attempt


def serve(core: NudgeCore, adapter: CodexAdapter, *, input_stream=sys.stdin, output_stream=sys.stdout):
    requests = queue.Queue(maxsize=MAX_PENDING_REQUESTS)
    pending = {}
    lock = threading.Lock()
    output_lock = threading.Lock()

    def send(response):
        with output_lock:
            output_stream.write(json_text(response) + "\n")
            output_stream.flush()

    def read_requests():
        try:
            while True:
                line = input_stream.readline(MAX_REQUEST_CHARS + 1)
                if not line:
                    break
                if len(line) > MAX_REQUEST_CHARS:
                    while line and not line.endswith("\n"):
                        line = input_stream.readline(MAX_REQUEST_CHARS + 1)
                    core.log_error("MCP request exceeds 1 Mi characters")
                    continue
                try:
                    request = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(request, dict):
                    continue
                if request.get("method") == "notifications/cancelled":
                    params = request.get("params") or {}
                    with lock:
                        event = pending.get(params.get("requestId"))
                        if event is not None:
                            event.set()
                    continue
                if "id" not in request:
                    continue
                if not isinstance(request["id"], (str, int)) or isinstance(request["id"], bool):
                    send({"jsonrpc": "2.0", "id": None, "error": {
                        "code": -32600, "message": "Request ID must be a string or integer"}})
                    continue
                event = threading.Event()
                with lock:
                    rejected = (request["id"] in pending or len(pending) >= MAX_PENDING_REQUESTS)
                    if not rejected:
                        pending[request["id"]] = event
                if rejected:
                    send({"jsonrpc": "2.0", "id": request["id"], "error": {
                        "code": -32600, "message": "Duplicate request ID or pending request limit reached"}})
                    continue
                requests.put((request, event))
        finally:
            with lock:
                for event in pending.values():
                    event.set()
            requests.put(None)

    reader = threading.Thread(target=read_requests, daemon=True)
    reader.start()
    while True:
        item = requests.get()
        if item is None:
            break
        request, cancel_event = item
        method = request.get("method")
        params = request.get("params") or {}
        attempt = None
        if method == "tools/call" and cancel_event.is_set():
            with lock:
                pending.pop(request["id"], None)
            send({"jsonrpc": "2.0", "id": request["id"], "error": {
                "code": -32800, "message": "Request cancelled"}})
            continue
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
                result, attempt = _reply(adapter.process(dict(arguments), cancel_event=cancel_event))
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
            with lock:
                pending.pop(request["id"], None)
            send(response)
            continue
        with lock:
            pending.pop(request["id"], None)
        if method == "tools/call" and cancel_event.is_set():
            attempt = None
            response = {"jsonrpc": "2.0", "id": request["id"],
                        "error": {"code": -32800, "message": "Request cancelled"}}
        else:
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        send(response)
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
