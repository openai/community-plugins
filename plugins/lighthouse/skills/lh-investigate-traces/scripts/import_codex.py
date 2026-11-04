"""Import explicitly selected local Codex rollouts as an ordinary evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections import defaultdict, deque
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class TraceImportError(ValueError):
    """An input or destination cannot produce a valid investigation bundle."""


def object_fields(value: object) -> dict[str, object]:
    """Narrow a decoded JSON object without trusting its contents."""
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def string_field(record: dict[str, object], key: str) -> str | None:
    """Return a string field, leaving other JSON types unknown."""
    value = record.get(key)
    return value if isinstance(value, str) else None


def visible_text(payload: dict[str, object]) -> str | None:
    """Extract visible message text, excluding reasoning and non-text blocks."""
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    texts: list[str] = []
    for block in content:
        fields = object_fields(block)
        if fields.get("type") in ("input_text", "output_text", "text"):
            value = string_field(fields, "text")
            if value is not None:
                texts.append(value)
    return "\n".join(texts) if texts else None


def result_text(value: object) -> tuple[str | None, int]:
    """Normalize string or structured text output and count omitted object blocks."""
    if isinstance(value, str):
        return value, 0
    if not isinstance(value, list) or any(not isinstance(block, dict) for block in value):
        return None, 0
    texts: list[str] = []
    omitted = 0
    for block in value:
        fields = object_fields(block)
        text = string_field(fields, "text")
        if fields.get("type") in ("input_text", "output_text", "text") and text is not None:
            texts.append(text)
        else:
            omitted += 1
    return "\n".join(texts), omitted


class Rollout:
    """Normalize one copied source; call identity never crosses source boundaries."""

    def __init__(self, source_id: str) -> None:
        """Start an importer with no inferred session identity or execution state."""
        self.source_id = source_id
        self.session_id: str | None = None
        self.session_ids: list[str] = []
        self.events: list[dict[str, object]] = []
        self.notices: list[dict[str, object]] = []
        self.fallback_ids: set[str] = set()

    def notice(self, line: int | None, code: str, detail: str) -> None:
        """Record a coverage gap without repeating arbitrary source payloads."""
        self.notices.append(
            {"source_id": self.source_id, "line": line, "code": code, "detail": detail}
        )

    def event(self, line: int, record: dict[str, object]) -> dict[str, object]:
        """Create the common event envelope with stable source-line provenance."""
        return {
            "id": f"{self.source_id}:{line}",
            "source_id": self.source_id,
            "line": line,
            "session_id": self.session_id,
            "timestamp": string_field(record, "timestamp"),
            "status": "unknown",
        }

    def read(self, data: bytes) -> None:
        """Normalize complete lines and retain notices for invalid or unknown records."""
        if not data:
            self.notice(None, "empty_source", "source contains no records")
        for line, raw in enumerate(data.splitlines(), start=1):
            try:
                decoded: object = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                self.notice(line, "malformed_record", "invalid JSON or text encoding")
                continue
            record = object_fields(decoded)
            if not record or not isinstance(record.get("payload"), dict):
                self.notice(line, "invalid_record", "expected record object with object payload")
                continue
            self.record(line, record)
        self.remove_duplicate_messages()
        self.pair_calls()

    def record(self, line: int, record: dict[str, object]) -> None:
        """Dispatch supported Codex record envelopes."""
        payload = object_fields(record["payload"])
        record_type = record.get("type")
        if record_type == "session_meta":
            self.session_id = string_field(payload, "id")
            if self.session_id and self.session_id not in self.session_ids:
                self.session_ids.append(self.session_id)
            if not self.session_id:
                self.notice(line, "missing_session_id", "session metadata has no string id")
            event = self.event(line, record)
            event.update(kind="session", metadata=self.session_metadata(payload))
            self.events.append(event)
        elif record_type == "turn_context":
            event = self.event(line, record)
            metadata = {
                key: value
                for key in ("turn_id", "cwd", "model")
                if (value := string_field(payload, key)) is not None
            }
            event.update(kind="turn", metadata=metadata)
            self.events.append(event)
        elif record_type == "response_item":
            self.response(line, record, payload)
        elif record_type == "event_msg":
            self.fallback(line, record, payload)
        else:
            self.notice(line, "unsupported_record", "record type omitted")

    @staticmethod
    def session_metadata(payload: dict[str, object]) -> dict[str, object]:
        """Keep identity fields, excluding prompts and account identifiers."""
        keys = ("id", "cwd", "originator", "cli_version", "source", "model_provider")
        metadata: dict[str, object] = {
            key: value for key in keys if (value := string_field(payload, key)) is not None
        }
        git = object_fields(payload.get("git"))
        if git:
            metadata["git"] = {
                key: value
                for key in ("commit_hash", "branch")
                if (value := string_field(git, key)) is not None
            }
        return metadata

    def response(self, line: int, record: dict[str, object], payload: dict[str, object]) -> None:
        """Read visible response items; never normalize reasoning payloads."""
        item_type = payload.get("type")
        event = self.event(line, record)
        if (
            item_type == "reasoning"
            or payload.get("channel") == "analysis"
            or payload.get("phase") == "analysis"
        ):
            self.notice(line, "reasoning_omitted", "reasoning omitted from normalized events")
            return
        if item_type == "message":
            text = visible_text(payload)
            role = payload.get("role")
            if role not in ("user", "assistant") or text is None:
                self.notice(line, "unsupported_message", "message has no supported visible content")
                return
            event.update(kind="message", role=role, text=text)
        elif item_type in ("function_call", "custom_tool_call"):
            if not self.tool_call(event, payload):
                self.notice(line, "invalid_tool_call", "call needs string id, name and input")
                return
        elif item_type in ("function_call_output", "custom_tool_call_output"):
            if not self.tool_result(event, payload):
                self.notice(
                    line,
                    "invalid_tool_result",
                    "result needs string id and string or structured text output",
                )
                return
            if event["status"] == "unknown":
                self.notice(line, "unknown_tool_status", "result has no explicit boolean is_error")
        else:
            self.notice(line, "unsupported_record", "response item type omitted")
            return
        self.events.append(event)

    @staticmethod
    def tool_call(event: dict[str, object], payload: dict[str, object]) -> bool:
        """Keep function JSON arguments and custom-tool raw input exactly as recorded."""
        call_id = string_field(payload, "call_id")
        name = string_field(payload, "name")
        input_key = "input" if payload.get("type") == "custom_tool_call" else "arguments"
        arguments = string_field(payload, input_key)
        if not call_id or not name or arguments is None:
            return False
        event.update(
            kind="tool_call",
            call_id=call_id,
            tool_name=name,
            arguments=arguments,
            paired_event_id=None,
        )
        return True

    def tool_result(self, event: dict[str, object], payload: dict[str, object]) -> bool:
        """Record only explicit execution status, never infer success from prose."""
        call_id = string_field(payload, "call_id")
        output, omitted = result_text(payload.get("output"))
        if not call_id or output is None:
            return False
        if omitted:
            line = event["line"]
            self.notice(
                line if isinstance(line, int) else None,
                "unsupported_result_content",
                f"{omitted} non-text or unsupported output blocks omitted",
            )
        is_error = payload.get("is_error")
        status = ("error" if is_error else "success") if isinstance(is_error, bool) else "unknown"
        event.update(
            kind="tool_result",
            call_id=call_id,
            output=output,
            status=status,
            paired_event_id=None,
        )
        return True

    def fallback(self, line: int, record: dict[str, object], payload: dict[str, object]) -> None:
        """Retain older user/agent message envelopes where no canonical copy exists."""
        roles = {"user_message": "user", "agent_message": "assistant"}
        item_type = string_field(payload, "type")
        text = string_field(payload, "message")
        if item_type not in roles or text is None:
            self.notice(line, "unsupported_record", "event message type omitted")
            return
        event = self.event(line, record)
        event.update(kind="message", role=roles[item_type], text=text)
        self.fallback_ids.add(str(event["id"]))
        self.events.append(event)

    def remove_duplicate_messages(self) -> None:
        """Deduplicate one adjacent canonical/fallback pair; retain uncertain repeats."""
        retained: list[dict[str, object]] = []
        previous: dict[str, object] | None = None
        for event in self.events:
            if previous is not None and self.mirrored_messages(previous, event):
                fallback = event if event["id"] in self.fallback_ids else previous
                canonical = previous if fallback is event else event
                retained[-1] = canonical
                line = fallback["line"]
                self.notice(
                    line if isinstance(line, int) else None,
                    "duplicate_message",
                    "adjacent canonical response-item message retained",
                )
                previous = None
            else:
                retained.append(event)
                previous = event
        self.events = retained

    def mirrored_messages(self, first: dict[str, object], second: dict[str, object]) -> bool:
        """Match adjacent visible messages with different envelopes and equal content."""
        if first["kind"] != "message" or second["kind"] != "message":
            return False
        same_message = all(first[key] == second[key] for key in ("session_id", "role", "text"))
        first_fallback = first["id"] in self.fallback_ids
        second_fallback = second["id"] in self.fallback_ids
        return same_message and first_fallback != second_fallback

    def pair_calls(self) -> None:
        """Pair call/result IDs in encounter order within this source and session."""
        pending: dict[tuple[str | None, str], deque[dict[str, object]]] = defaultdict(deque)
        for event in self.events:
            if event["kind"] not in ("tool_call", "tool_result"):
                continue
            session = string_field(event, "session_id")
            key = (session, str(event["call_id"]))
            if event["kind"] == "tool_call":
                pending[key].append(event)
            elif pending[key]:
                call = pending[key].popleft()
                call["paired_event_id"] = event["id"]
                event["paired_event_id"] = call["id"]
            else:
                self.pair_notice(event, "orphan_tool_result")
        for calls in pending.values():
            for event in calls:
                self.pair_notice(event, "missing_tool_result")

    def pair_notice(self, event: dict[str, object], code: str) -> None:
        """Attach a pairing coverage notice to its source line."""
        line = event["line"]
        self.notice(
            line if isinstance(line, int) else None,
            code,
            "no matching call/result in this source and session",
        )


def write_json(path: Path, value: object) -> None:
    """Write readable UTF-8 JSON with a trailing newline."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_bundle(repo: Path, traces: Sequence[Path], destination: Path) -> dict[str, object]:
    """Write a complete bundle into an already private staging directory."""
    source_directory = destination / "sources"
    source_directory.mkdir()
    sources: list[dict[str, object]] = []
    events: list[dict[str, object]] = []
    notices: list[dict[str, object]] = []
    for index, trace in enumerate(traces, start=1):
        data = trace.read_bytes()
        source_id = f"source-{index:03d}"
        relative = f"sources/{source_id}.jsonl"
        (destination / relative).write_bytes(data)
        rollout = Rollout(source_id)
        rollout.read(data)
        sources.append(
            {
                "id": source_id,
                "original_path": str(trace),
                "path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "byte_count": len(data),
                "line_count": len(data.splitlines()),
                "session_ids": rollout.session_ids,
            }
        )
        events.extend(rollout.events)
        notices.extend(rollout.notices)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "repository": {"path": str(repo), "historical_state": "unknown"},
        "sources": sources,
        "events_path": "events.jsonl",
        "annotations_path": "annotations.json",
        "event_count": len(events),
        "notices": notices,
    }
    (destination / "events.jsonl").write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8"
    )
    write_json(destination / "annotations.json", {"schema_version": 1, "annotations": []})
    (destination / ".gitignore").write_text("*\n", encoding="utf-8")
    write_json(destination / "investigation.json", manifest)
    return manifest


def import_traces(repo: Path, traces: Sequence[Path], output: Path) -> dict[str, object]:
    """Import explicit files without overwriting output or modifying selected inputs."""
    repo = repo.resolve()
    output = output.absolute()
    traces = [trace.resolve() for trace in traces]
    if not repo.is_dir() or not traces or any(not trace.is_file() for trace in traces):
        message = "Select an existing repository directory and at least one existing trace file."
        raise TraceImportError(message)
    if output.exists() or output.is_symlink():
        message = "The output path already exists; select a new directory."
        raise TraceImportError(message)
    if len(set(traces)) != len(traces):
        message = "The same trace file was selected more than once."
        raise TraceImportError(message)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".investigation-", dir=output.parent) as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir(mode=0o700)
        manifest = build_bundle(repo, traces, staging)
        # Reserve exclusively before rename: an existing empty directory must also survive.
        output.mkdir(mode=0o700)
        try:
            staging.rename(output)
        except OSError:
            output.rmdir()
            raise
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    """Import explicit local traces and report only a content-free completion summary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--trace", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = import_traces(args.repo, args.trace, args.output)
    except (TraceImportError, OSError) as error:
        sys.stderr.write(f"Import failed: {error}\n")
        return 1
    sys.stdout.write(
        f"Imported {manifest['event_count']} events. Review investigation.json notices.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
