---
name: lh
description: Create or improve native Harbor benchmarks from repository work or custom workflows, investigate local Codex traces, or inspect existing tasks.
---

# Lighthouse

Turn repository work or a custom workflow into a readable benchmark repository
with native Harbor tasks and a measured leaderboard. Use this skill when the
request spans stages or the next step is unclear. A focused request can use the
relevant skill directly.

## Find the next step

Read the user's request and any existing benchmark README, `review/selection.md`
and experiment notes. Check the actual task and result files before resuming.
Load only the skill needed for the next unfinished, authorized step:

| Need | Skill | Output |
| --- | --- | --- |
| Investigate local Codex traces, repository friction or observed failures | [lh-investigate-traces](../lh-investigate-traces/SKILL.md) | Reviewed episodes, failure modes and candidate evidence |
| Create the benchmark repository | [lh-create-bench](../lh-create-bench/SKILL.md) | Setup, docs and an empty leaderboard |
| Find and rank useful work | [lh-identify-tasks](../lh-identify-tasks/SKILL.md) | Evidence-backed shortlist |
| Package and qualify a selected task | [lh-create-task](../lh-create-task/SKILL.md) | Native task and executed evaluator controls |
| Run tasks or regrade saved work | [lh-run-bench](../lh-run-bench/SKILL.md) | Harbor results and measured leaderboard |
| Test a harness improvement | [lh-improve-harness](../lh-improve-harness/SKILL.md) | Matched comparison and decision |
| Count, list, or check the status of existing tasks; inspect a named task or rubric; open the dashboard | [lh-open-explorer](../lh-open-explorer/SKILL.md) | Task catalog or named task beside this conversation, or the global dashboard when requested |

For inventory questions such as “how many tasks do we have in this bench now”,
“what are the 10 tasks?” or “Which Lighthouse tasks are qualified?”, open the
task catalog beside the current Codex conversation before giving the answer.
Once the absolute benchmark path is known, call `lighthouse_open_task_authoring`
with `benchmarkPath` alone; do not pick a task for the user. Then answer from
the evidence, distinguishing published tasks from drafts where relevant. A request
to find new candidates belongs to `lh-identify-tasks` instead.

For an end-to-end request, continue through creation, selection, task qualification
and learner runs. Finding candidates or passing oracle runs does not complete a
request for a measured benchmark. Pass each stage's saved artifacts to the next
skill; do not introduce a separate Lighthouse config or command layer.

A custom workflow can start from a brief, files and tool contracts without source
history. Use the same creation and authoring skills; `lh-create-task` owns shared
worlds, application services and their evidence contracts. If the user already
selected the work, proceed to authoring rather than requiring repository scouting.

Trace investigation can start with local traces and a source repository, before
a benchmark exists. Pass reviewed candidates to `lh-identify-tasks` for selection,
then selected work to `lh-create-task`; an observed failure is not a qualified task.

## Continue within the request

Reuse the agreed task count, model, budget and concurrency. Save these in
`review/allocation.md` before paid model runs. Ask only for missing execution
choices or credentials; continue independent preparation while they are missing.
Keep credential values out of prompts and benchmark files.

Resume from retained evidence without overwriting completed or uncertain trials.
Stop at the requested endpoint or resource limit. Report what is qualified, what
ran, which metrics are available, and the next unfinished action. Keep failed
attempts visible. Local benchmark work does not require publishing it.
