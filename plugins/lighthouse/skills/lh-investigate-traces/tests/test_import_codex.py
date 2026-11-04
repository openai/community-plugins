"""Check provenance, incomplete evidence, call scoping and private source handling."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/import_codex.py"
FIXTURE = Path(__file__).parent / "fixtures/codex-rollout.jsonl"


def invoke(repo: Path, traces: list[Path], output: Path) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), "--repo", str(repo), "--output", str(output)]
    for trace in traces:
        command.extend(["--trace", str(trace)])
    return subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603 -- fixed local helper with explicit fixture paths; no shell


def write_trace(path: Path, payloads: list[dict[str, object]]) -> Path:
    records = [{"type": "response_item", "payload": payload} for payload in payloads]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def read_events(output: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]


def test_fixture_preserves_provenance_and_separates_execution_status(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    original = FIXTURE.read_bytes()
    assert invoke(tmp_path, [FIXTURE], output).returncode == 0
    manifest = json.loads((output / "investigation.json").read_text())
    source = manifest["sources"][0]
    assert source["sha256"] == hashlib.sha256(original).hexdigest()
    assert source["session_ids"] == ["synthetic-session"]
    assert (output / source["path"]).read_bytes() == original == FIXTURE.read_bytes()
    assert manifest["repository"] == {"path": str(tmp_path), "historical_state": "unknown"}
    events = read_events(output)
    assert len(events) == manifest["event_count"] == 10
    results = [event for event in events if event["kind"] == "tool_result"]
    assert [event["status"] for event in results] == ["error", "success", "unknown"]
    assert results[0]["paired_event_id"] == "source-001:5"
    custom = next(event for event in events if event["id"] == "source-001:7")
    assert custom["arguments"] == "*** Begin Patch\n*** End Patch"
    assert custom["status"] == "unknown"
    assert [event["id"] for event in events if event.get("role") == "user"] == ["source-001:4"]
    assert "Private synthetic reasoning" not in (output / "events.jsonl").read_text()
    assert json.loads((output / "annotations.json").read_text()) == {
        "schema_version": 1,
        "annotations": [],
    }
    assert (output / ".gitignore").read_text() == "*\n"


def test_pairing_never_crosses_sources_or_session_boundaries(tmp_path: Path) -> None:
    first = write_trace(
        tmp_path / "first.jsonl",
        [
            {"type": "function_call", "call_id": "same", "name": "tool", "arguments": "{}"},
        ],
    )
    second = write_trace(
        tmp_path / "second.jsonl",
        [
            {"type": "function_call_output", "call_id": "same", "output": "orphan"},
        ],
    )
    first.write_text(
        first.read_text()
        + json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "new-session"},
            }
        )
        + "\n"
        + second.read_text()
    )
    output = tmp_path / "bundle"
    assert invoke(tmp_path, [first, second], output).returncode == 0
    tools = [
        event for event in read_events(output) if event["kind"] in ("tool_call", "tool_result")
    ]
    assert all(event["paired_event_id"] is None for event in tools)
    manifest = json.loads((output / "investigation.json").read_text())
    codes = [notice["code"] for notice in manifest["notices"]]
    assert codes.count("orphan_tool_result") == 2
    assert codes.count("missing_tool_result") == 1


def test_reused_call_ids_pair_in_order(tmp_path: Path) -> None:
    trace = write_trace(
        tmp_path / "trace.jsonl",
        [
            {"type": "function_call", "call_id": "same", "name": "one", "arguments": "{}"},
            {"type": "function_call", "call_id": "same", "name": "two", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "same", "output": "first"},
            {"type": "function_call_output", "call_id": "same", "output": "second"},
        ],
    )
    output = tmp_path / "bundle"
    assert invoke(tmp_path, [trace], output).returncode == 0
    assert [event["paired_event_id"] for event in read_events(output)] == [
        "source-001:3",
        "source-001:4",
        "source-001:1",
        "source-001:2",
    ]


def test_partial_unknown_and_invalid_records_preserve_exact_source(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    original = b'[]\n{"type":"future","payload":{"secret":"not normalized"}}\n\xff\n{"partial":'
    trace.write_bytes(original)
    output = tmp_path / "bundle"
    assert invoke(tmp_path, [trace], output).returncode == 0
    assert read_events(output) == []
    manifest = json.loads((output / "investigation.json").read_text())
    assert [notice["code"] for notice in manifest["notices"]] == [
        "invalid_record",
        "unsupported_record",
        "malformed_record",
        "malformed_record",
    ]
    assert (output / "sources/source-001.jsonl").read_bytes() == original
    assert "not normalized" not in (output / "investigation.json").read_text()


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "function_call", "call_id": 2, "name": "tool", "arguments": "{}"},
        {"type": "custom_tool_call", "call_id": "id", "name": "tool", "input": {}},
        {"type": "function_call_output", "call_id": "id", "output": ["invalid"]},
        {"type": "message", "role": "user", "content": "invalid"},
    ],
)
def test_invalid_supported_payloads_become_notices(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    trace = write_trace(tmp_path / "trace.jsonl", [payload])
    output = tmp_path / "bundle"
    assert invoke(tmp_path, [trace], output).returncode == 0
    assert read_events(output) == []
    manifest = json.loads((output / "investigation.json").read_text())
    assert len(manifest["notices"]) == 1


def test_analysis_and_instructions_are_excluded_and_fallback_is_retained(tmp_path: Path) -> None:
    trace = write_trace(
        tmp_path / "trace.jsonl",
        [
            {
                "type": "message",
                "role": "assistant",
                "channel": "analysis",
                "content": [
                    {"type": "output_text", "text": "hidden analysis"},
                ],
            },
            {
                "type": "message",
                "role": "developer",
                "content": [
                    {"type": "input_text", "text": "private instruction"},
                ],
            },
        ],
    )
    with trace.open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "message": "visible answer",
                    },
                }
            )
            + "\n"
        )
    output = tmp_path / "bundle"
    assert invoke(tmp_path, [trace], output).returncode == 0
    assert [event["text"] for event in read_events(output)] == ["visible answer"]


@pytest.mark.parametrize("nonempty", [False, True])
def test_existing_output_is_never_overwritten(tmp_path: Path, *, nonempty: bool) -> None:
    output = tmp_path / "bundle"
    output.mkdir()
    if nonempty:
        (output / "keep").write_text("original")
    result = invoke(tmp_path, [FIXTURE], output)
    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert sorted(path.name for path in output.iterdir()) == (["keep"] if nonempty else [])


def test_missing_input_leaves_no_published_bundle(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    result = invoke(tmp_path, [FIXTURE, tmp_path / "absent.jsonl"], output)
    assert result.returncode == 1
    assert not output.exists()
    assert not list(tmp_path.glob(".investigation-*"))


def test_duplicate_matching_preserves_extra_identical_fallback_messages(tmp_path: Path) -> None:
    trace = write_trace(
        tmp_path / "trace.jsonl",
        [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Try again."},
                ],
            }
        ],
    )
    mirrored = (
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "Try again.",
                },
            }
        )
        + "\n"
    )
    trace.write_text(trace.read_text() + mirrored + mirrored)
    output = tmp_path / "bundle"
    assert invoke(tmp_path, [trace], output).returncode == 0
    assert [event["id"] for event in read_events(output)] == ["source-001:1", "source-001:3"]
    manifest = json.loads((output / "investigation.json").read_text())
    assert [notice["code"] for notice in manifest["notices"]] == ["duplicate_message"]


def test_non_boolean_status_does_not_claim_execution_success(tmp_path: Path) -> None:
    trace = write_trace(
        tmp_path / "trace.jsonl",
        [
            {
                "type": "function_call_output",
                "call_id": "id",
                "output": "successful",
                "is_error": "false",
            }
        ],
    )
    output = tmp_path / "bundle"
    assert invoke(tmp_path, [trace], output).returncode == 0
    assert read_events(output)[0]["status"] == "unknown"


@pytest.mark.parametrize(
    ("call_type", "result_type", "input_key"),
    [
        ("function_call", "function_call_output", "arguments"),
        ("custom_tool_call", "custom_tool_call_output", "input"),
    ],
)
def test_modern_output_blocks_preserve_text_and_pairing(
    tmp_path: Path,
    call_type: str,
    result_type: str,
    input_key: str,
) -> None:
    trace = write_trace(
        tmp_path / "trace.jsonl",
        [
            {"type": call_type, "call_id": "id", "name": "tool", input_key: "{}"},
            {
                "type": result_type,
                "call_id": "id",
                "output": [
                    {"type": "input_text", "text": "first block"},
                    {"type": "input_text", "text": "second block\nwith newline"},
                    {"type": "input_text", "text": "third block"},
                ],
            },
        ],
    )
    original = trace.read_bytes()
    output = tmp_path / "bundle"
    assert invoke(tmp_path, [trace], output).returncode == 0
    events = read_events(output)
    assert events[1]["output"] == "first block\nsecond block\nwith newline\nthird block"
    assert events[0]["paired_event_id"] == "source-001:2"
    assert events[1]["paired_event_id"] == "source-001:1"
    assert events[1]["status"] == "unknown"
    assert (output / "sources/source-001.jsonl").read_bytes() == original
    manifest = json.loads((output / "investigation.json").read_text())
    assert [notice["code"] for notice in manifest["notices"]] == ["unknown_tool_status"]


@pytest.mark.parametrize("include_text", [False, True])
def test_nontext_output_blocks_are_disclosed_without_losing_pairing(
    tmp_path: Path,
    *,
    include_text: bool,
) -> None:
    blocks = [{"type": "image", "data": "private binary content"}]
    if include_text:
        blocks.append({"type": "input_text", "text": "visible output"})
    trace = write_trace(
        tmp_path / "trace.jsonl",
        [
            {"type": "function_call", "call_id": "id", "name": "tool", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "id", "output": blocks},
        ],
    )
    output = tmp_path / "bundle"
    assert invoke(tmp_path, [trace], output).returncode == 0
    events = read_events(output)
    assert events[1]["output"] == ("visible output" if include_text else "")
    assert events[1]["paired_event_id"] == "source-001:1"
    assert "private binary content" not in (output / "events.jsonl").read_text()
    manifest = json.loads((output / "investigation.json").read_text())
    assert [notice["code"] for notice in manifest["notices"]] == [
        "unsupported_result_content",
        "unknown_tool_status",
    ]


@pytest.mark.parametrize(
    "intervening",
    [
        [{"type": "turn_context", "payload": {"turn_id": "next-turn"}}],
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "call_id": "id",
                    "name": "tool",
                    "arguments": "{}",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "id",
                    "output": "result",
                },
            },
        ],
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "I will try."},
                    ],
                },
            }
        ],
    ],
)
def test_identical_later_fallback_after_an_episode_is_not_a_duplicate(
    tmp_path: Path,
    intervening: list[dict[str, object]],
) -> None:
    trace = write_trace(
        tmp_path / "trace.jsonl",
        [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Try again."},
                ],
            }
        ],
    )
    fallback = {
        "type": "event_msg",
        "payload": {
            "type": "user_message",
            "message": "Try again.",
        },
    }
    trace.write_text(
        trace.read_text()
        + "".join(json.dumps(record) + "\n" for record in [*intervening, fallback])
    )
    output = tmp_path / "bundle"
    assert invoke(tmp_path, [trace], output).returncode == 0
    user_messages = [event for event in read_events(output) if event.get("role") == "user"]
    assert len(user_messages) == 2
    assert all(event["text"] == "Try again." for event in user_messages)
    manifest = json.loads((output / "investigation.json").read_text())
    assert "duplicate_message" not in [notice["code"] for notice in manifest["notices"]]
