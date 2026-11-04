# Local investigation bundle (version 1)

`import_codex.py --repo /absolute/source/repo --trace /absolute/rollout.jsonl
--output /absolute/source/repo/.local/investigations/example` imports explicit
Codex rollout files. Repeat `--trace` for multiple files. The output must not exist.
No history discovery, tool replay, repository checkout or network access occurs.
Keep bundles in an ignored `.local/` directory: raw trace copies can contain private
prompts, tool results and credentials. They are investigation evidence, never learner
inputs. The importer adds a bundle-local `.gitignore` containing `*`.

## Files

- `investigation.json`: manifest below.
- `events.jsonl`: normalized events in input-file order, then original line order.
- `annotations.json`: initially `{"schema_version":1,"annotations":[]}`. Reviewers
  own these records; import does not generate confirmed findings.
- `sources/<source_id>.jsonl`: byte-for-byte source copies, including malformed or
  incomplete records. SHA-256 is calculated from these exact copied bytes.

The importer builds a temporary sibling directory and publishes it only after all
files are written. Existing destinations are never overwritten.

## Manifest

```json
{
  "schema_version": 1,
  "repository": {"path": "/absolute/source/repo", "historical_state": "unknown"},
  "sources": [{
    "id": "source-001",
    "original_path": "/absolute/rollout.jsonl",
    "path": "sources/source-001.jsonl",
    "sha256": "hex digest",
    "byte_count": 1234,
    "line_count": 12,
    "session_ids": ["session UUID"]
  }],
  "events_path": "events.jsonl",
  "annotations_path": "annotations.json",
  "event_count": 10,
  "notices": [{"source_id":"source-001","line":4,"code":"unsupported_record","detail":"record type omitted"}]
}
```

`repository.path` records the selected existing directory. It makes no claim about
historical Git revision, working-tree content, test status or trace ownership.
Session metadata and turn context can show recorded working directories, but a
matching path does not prove matching contents. Missing snapshots remain unknown.
`notices` includes malformed records, unsupported records, omitted reasoning,
duplicate messages, unknown tool status, and missing/orphan call pairs. `line` is
one-based or null for a source-level notice. Notice details never copy raw content.

## Normalized events

Every event has `id` (`source-001:4`), `source_id`, one-based `line`, nullable
`session_id`, nullable `timestamp`, `kind`, and `status` (`success`, `error`, or
`unknown`). Fields not applicable to a kind are omitted:

| kind | Additional fields |
| --- | --- |
| `session` | `metadata`: allowlisted `id`, `cwd`, `originator`, `cli_version`, `source`, `model_provider`, `git` strings/object (Git metadata contains only `commit_hash` and `branch`) |
| `turn` | `metadata`: allowlisted `turn_id`, `cwd`, `model` strings |
| `message` | `role`: `user` or `assistant`; `text`: visible text only |
| `tool_call` | `call_id`, `tool_name`, `arguments` (string); nullable `paired_event_id` |
| `tool_result` | `call_id`, `output` (string; text blocks joined with newlines); nullable `paired_event_id` |

`status` describes only explicit recorded tool-result execution status; messages,
calls and metadata remain `unknown`. A valid call/result pair does not imply a
successful result, correct tool use, completed task or green application state.
Results with no explicit boolean `is_error` stay `unknown`; the importer does not
interpret arbitrary result prose as a check or infer task success. A paired call's
status stays `unknown`, and consumers inspect its result event for execution status.
Outputs may be strings or arrays of content-block objects. Recognized `input_text`,
`output_text` and `text` blocks retain their text in order. Unsupported/non-text
blocks produce an `unsupported_result_content` notice; a result containing only
non-text blocks retains its pairing with empty normalized output. Raw blocks remain
in the source copy. Primitive arrays are invalid result payloads.

Pairing uses `(source_id, session_id, call_id)`; it never joins across source files.
Repeated IDs pair in encounter order; missing or orphan partners retain null links
and a notice. A session without metadata has a null session ID.

Supported records are `session_meta`, `turn_context`, response-item user/assistant
messages, function calls/results and custom tool calls/results. `event_msg`
`user_message` and `agent_message` serve as fallbacks. Deduplication consumes one
adjacent pair of normalized messages with identical source/session, role and text,
where one is canonical and one is a fallback. An intervening normalized event
(message, tool, turn or session) breaks matching. Uncertain repeats and extra
identical fallback messages remain visible. Reasoning
records and assistant analysis messages are omitted from the normalized view.
Originals are retained only in `sources/`. Unknown structures produce notices,
without serializing their arbitrary payloads into the normalized view.

## Reviewer annotations and task handoff

Each annotation is an object with a stable `id`, `event_ids` (existing normalized
IDs), `note` (free text), optional `label` (free text), and `review_status` (`suggested` or
`confirmed`). Optional `candidate_id` links to an ordinary candidate in the
existing task-creation workflow. A UI must preserve annotations it does not edit.
An annotation is reviewer judgment, not execution evidence. Candidate promotion
must carry source hash and event IDs as provenance, separate from learner inputs;
qualification remains owned by `lh-create-task`.
