---
name: lh-create-bench
description: Create the repository, locked Harbor setup, documentation, and empty leaderboard for repository or workflow tasks, or repair an existing benchmark foundation.
---

# Create a benchmark

Create an ordinary `<name>-bench` repository that people can read and run with
Harbor. This skill builds the foundation; [lh](../lh/SKILL.md) continues through
task discovery, authoring and execution when the user requested the full workflow.

## Step 1: Pin the source

For repository work, use the supplied checkout or clone the requested repository.
Record the remote URL and full discovery commit. Inspect its README, runtime
declaration, dependency locks and test commands; leave the source unchanged.

For a workflow or simulated world, retain the source brief, supplied assets and
tool contracts in `review/`. Record the starting data's provenance and snapshot
hashes, or its seed and generation recipe revision. Mark missing assets pending;
do not invent repository history. The brief should identify the role, requested
outcomes and available tools. Task authoring builds and qualifies the environment.

Default repository output to a sibling `<repo>-bench`; otherwise use a descriptive
`<name>-bench` in the requested workspace. If it already exists, read its
README, `AGENTS.md`, lockfile and review notes. Repair missing pieces instead of
copying the template over existing work.

## Step 2: Create the repository

Read the [Terminal-Bench reference](references/terminal-bench.md) for the layout
and release model. Copy [assets/bench](assets/bench/) into the new output directory,
including its dotfiles. Replace the documented template values:

| Value | Replace with |
| --- | --- |
| `{{benchmark}}` | Lowercase repository name, such as `grimoire-bench` |
| `{{source_url}}` | Source repository URL or link to the retained workflow brief |
| `{{source_commit}}` | Full discovery commit SHA or exact starting-data snapshot/seed identity |
| `{{plugin_version}}` | Installed plugin version, plus commit if available |
| `{{created_date}}` | Date of creation |

Adapt the source labels and opening description to the benchmark. A generated
corpus also records its recipe revision and asset hashes in `review/`.

Create `tasks/`, `environments/`, `review/` and `experiments/` as they become useful.
Keep unfinished tasks under `.local/drafts/`; only qualified tasks enter `tasks/`.
Initialize Git if wanted, but do not make commits or publish unless authorized.
The generated README is the task index; no registry or scaffold executable is needed.
Keep the `benchmark.lighthouse` launcher at the benchmark root. Its initial
cohorts are empty; once learner jobs exist, list only the saved result files or
job directories belonging to each compatible cohort and add any exact-source
quality indexes. The launcher is optional metadata for compatible viewers;
Harbor remains sufficient without a viewer, the launcher or the plugin.

## Step 3: Lock Harbor

The template pins Harbor `0.23.0` and uses the public PyPI index. Preserve a user's
required package registry when creating their benchmark. From the benchmark:

```bash
uv lock
uv sync --locked
uv run --locked harbor --help
uv run --locked harbor task init --help
```

Retain `uv.lock`. Confirm the native commands are available before adding execution
examples. For corpora, tools, services or continuing workflows, read
[complex environments](../lh-create-task/references/complex-environments.md).
Verify needed features against the installed Harbor schema, selected agent and
backend; a documented field alone does not prove that combination works. If a
different Harbor version is needed, pin it explicitly and update the lock and
matching references. A working Linux Docker backend is needed to run tasks, but
not to create this repository. Follow [lh-run-bench](../lh-run-bench/SKILL.md) for credentials,
the live run plan and its standalone leaderboard helper.

Check the registry recorded in `uv.lock`. An inherited `UV_DEFAULT_INDEX` overrides
the project's index. If public PyPI is authorized and that override is unintended,
prefix the commands above with `env -u UV_DEFAULT_INDEX`. Use the same effective
registry for locking, syncing and running; leave global configuration unchanged.

## Step 4: Make the handoff concrete

Update the README with the benchmark's purpose, source identity and current state.
Start with no task rows or scores. Document the first shared dependency family in
`docs/environments.md` once the source or workflow identifies it. Include required
data, tools and services where relevant; task authoring owns the actual images,
reset behavior and compatibility checks.

For code-changing tasks, preserve the template's complete code rubric: functional
and engineering quality must both pass. [Task authoring](../lh-create-task/references/code-quality.md)
defines the judges and [benchmark metrics](../lh-run-bench/references/metrics.md)
define the three rates and their denominators. Non-code tasks use their own
outcome criteria and are reported separately from code-quality rates.

Check that template values are gone, the lock is current (`uv lock --check`), and
the README's local links resolve. Report the output path and verified setup.
For a full benchmark request, continue to [lh-identify-tasks](../lh-identify-tasks/SKILL.md)
when candidates need discovery, or [lh-create-task](../lh-create-task/SKILL.md)
when the requested tasks are already defined.
