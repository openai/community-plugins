---
name: lh-create-task
description: Build, repair and qualify native Harbor tasks from repository work or custom workflows, including shared worlds, stateful tools and cross-application deliverables.
---

# Create a benchmark task

Turn a selected candidate into an ordinary Harbor task. Use the benchmark's
locked Harbor dependency; the finished task runs without Lighthouse.

## Step 1: Pin the task

Retain the original request, relevant context and exact starting state. Follow
[construction](references/construction.md) to write a realistic learner request
and a separate curator rubric. Leave investigation and solution choices to the learner;
keep solutions and evaluator details outside its inputs.

For shared worlds, stateful tools, application services or work spanning several
artifacts, read [complex environments](references/complex-environments.md).
Use it to construct the task's inputs, tool surface and evidence lifecycle within
this workflow. A source repository or historical reference patch is not required.

When repackaging an existing evaluation, read
[adapting evaluations](references/adapting-evals.md) to compare its actual inputs,
tools and grading contract before choosing a faithful port or a new adaptation.

For a candidate derived from local traces, read [trace candidates](references/trace-candidates.md)
to pin the episode, reconstruct the starting state and separate learner context
from later recovery evidence. A reviewed trace is a lead, not an executed control.

In a Codex coding thread, open the task's side panel once the benchmark scaffold
(including `benchmark.lighthouse`) exists and its absolute path and task slug
are known. Call `lighthouse_open_task_authoring`
with `benchmarkPath`, `taskId` and `pane`: use `instruction` for the learner
request, `rubric` for what is evaluated, or `evaluation` for controls and
qualification. Call it again when the focus changes, not after every edit; the
panel follows saved files. Keep the rubric and evaluator details in curator
files, never in the learner request.

For tasks assessing software implementation or repair, use
[code quality](references/code-quality.md) to plan
separate checks for readability, maintainability, scope and test value. A benchmark
pass requires correctness and a quality pass: Sol Medium grades first; Astra
Medium independently reviews Sol failures. Unresolved reviews cannot pass. Use
the [native Harbor RewardKit recipe](references/harbor-code-quality.md) for that
quality pass; the ordinary functional verifier stays deterministic and offline.

Use [allocation](references/allocation.md) to record the authorized endpoint,
resource limits and repair reserve. Continue through the requested stages; a
brief or a passing oracle is an intermediate result.

## Step 2: Build the Harbor task

Read Harbor's [create-task skill](../../vendor/harbor/skills/create-task/SKILL.md)
for scaffolding, configuration, solution and verifier conventions. Work in
`.local/drafts/<task-name>/` until the task is qualified; write the curator rubric
and qualification evidence in `review/<task-name>/`:

```sh
uv run --locked harbor tasks init BENCHMARK/TASK --tasks-dir .local/drafts
```

For source-only repository tasks, keep the established public-network agent and separate
offline functional verifier. Read [shared foundations](references/shared-foundation.md)
for source layers, artifact capture and smoke checks. Reuse compatible dependency
images; each trial gets its own mutable state.

For complex environments, choose the smallest native Harbor setup that preserves
the requested workflow, then verify it on the locked version and execution backend.
Prefer separate verification over captured artifacts and service state. Where
grading requires live services, record and test that contract and its replay limits.
Use deterministic checks for observable outcomes; use the review workflow's model
grading only for criteria that need judgment. Code quality keeps its separate
network-restricted verifier. Preserve the requested application work when choosing
the environment and its evidence contract.

## Step 3: Test the evaluator

Write the reference solution and trusted tests. Follow the
[review workflow](references/review-workflow.md) to run a meaningfully different
valid solution, baseline/no-op, plausible wrong solutions and a weakened verifier.
Map each required promise to an executed check and the control it rejects.

Use native Harbor runs for these controls. Keep disposable variants and their
results outside `tasks/`; reuse Docker layers, not mutable workspaces.

## Step 4: Review and qualify

Follow [qualification](references/qualification.md). Have a fresh reviewer inspect
the final assets and raw evidence before seeing previous reviewers' conclusions.
Repair supported findings and rerun affected controls within budget.

When evidence is complete, place the exact frozen task in `tasks/<task-name>/`
and update the benchmark task index and external review record. Finalize the
task README before freezing it. Qualification is a documented
decision backed by native results, not a Lighthouse runtime gate.

## Step 5: Run the benchmark

For requested learner attempts and leaderboard metrics, continue with
[lh-run-bench](../lh-run-bench/SKILL.md). Keep authoring and qualification overhead
separate from learner usage. Report incomplete stages and blockers explicitly.
