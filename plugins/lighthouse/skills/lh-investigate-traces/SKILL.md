---
name: lh-investigate-traces
description: Investigate explicitly selected local Codex traces alongside a repository to find friction, review tool-use episodes and propose evidence-backed benchmark tasks.
---

# Investigate local traces

Start with a source repository and local Codex rollout JSONL files. Produce a
reviewable investigation and candidate briefs; no benchmark is required yet.
Harbor still owns task execution. Reading a trace never authorizes replaying it.

## Import selected evidence

Use trace paths supplied by the user or an already selected session. If only a
repository is known, identify a bounded set of sessions by metadata such as cwd
and session ID before reading their contents. Do not ingest the user's entire
Codex history. Record the chosen scope and selection limits.

Run the stdlib helper with Python 3.12 or later, using its installed skill path:

```sh
python3 /path/to/lighthouse/skills/lh-investigate-traces/scripts/import_codex.py \
  --repo /path/to/source-repo \
  --trace /path/to/rollout.jsonl \
  --output /path/to/source-repo/.local/investigations/first-review
```

Repeat `--trace` for more files. Choose a new local output directory; the importer
does not overwrite investigations. Read [the bundle contract](references/bundle.md)
when interpreting its files or connecting Explorer. The bundle retains original
trace bytes and is private local evidence, not a sanitized publishing artifact.
Keep it ignored and inspect/redact any selected excerpt before sharing it.

The repository path is a reference, not a captured historical tree. Check import
notices and missing call/result pairs before making claims. Preserve original
bytes; put corrections and annotations in separate files.

## Review episodes with the user

Follow [review and findings](references/review.md) to select a varied sample and
connect requests, tool calls/results, edits and observed checks. Start with
free-text observations. Include successful and recovered episodes as well as
failures; a tool error alone does not establish a failed task.

Use an available Explorer investigation view for the selected bundle. If that
surface is unavailable, review the same files directly and save notes using the
bundle annotation format. Do not claim a UI was opened or human review happened
unless it did. Keep agent suggestions separate from confirmed human annotations.

Separate configuration readiness, invocation validity, result usefulness and task
outcome. Mark absent source snapshots, check receipts and outcomes as unknown.
Comparable checks on exact states are required for reproduced Green → Red → Green
claims; deliberate test-first failures are not automatically regressions.

## Hand selected findings to task creation

Save a readable `findings.md` in the investigation directory with event IDs,
observations, hypotheses, reviewer decisions and evidence gaps. Follow
[trace candidates](../lh-create-task/references/trace-candidates.md) to propose a
bounded tool-use task or an end-to-end repository task. Preserve the original
request and keep future solution-bearing events outside learner inputs.

For requested task selection, continue with
[lh-identify-tasks](../lh-identify-tasks/SKILL.md), retaining trace provenance in
its candidate inventory. For an already selected task, continue with
[lh-create-task](../lh-create-task/SKILL.md). Neither annotations nor a successful
historical trace qualify a task; native evaluator controls and independent review
still apply. Stop at investigation when that is the requested endpoint.
