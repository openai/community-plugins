# {{benchmark}}

A benchmark of useful work grounded in [the source material]({{source_url}}).
Each task contains a concrete request, a reproducible starting environment and an
independently checked verifier. Tasks run directly with Harbor.

| Identity | Value |
| --- | --- |
| Source material | {{source_url}} |
| Starting source identity | `{{source_commit}}` |
| Authoring plugin | Lighthouse `{{plugin_version}}` |
| Created | {{created_date}} |
| Benchmark version | Unreleased; no measured runs |

## Setup

Install uv `0.11.33`, then use the Python version in `.python-version`:

```bash
uv sync --locked
uv run --locked harbor --help
```

This project uses public PyPI. If your shell's `UV_DEFAULT_INDEX` selects another
registry, prefix the commands with `env -u UV_DEFAULT_INDEX` when public PyPI is
permitted, or regenerate the lock against your required registry. Locking and
running must use the same registry.

Running tasks requires a working Linux Docker backend and the images described in
[environments](docs/environments.md). Real agent runs also need the selected model's
supported credentials. Keep credential values outside this repository.

## Tasks

No qualified tasks yet. Discovery and qualification evidence will live in `review/`.
As tasks qualify, add their description, source revision or data snapshot, and
linked task README here. Record shared scenario families so related tasks remain
visible.

## Run

Once qualified tasks exist, check their reference solutions:

```bash
uv run --locked harbor run -p tasks --agent oracle --env docker --n-concurrent 1
```

For learner runs, use the native Harbor config saved with the experiment:

```bash
uv run --locked harbor run --config experiments/<run>/job.yaml
```

Each experiment records its model, repetitions, limits and task/image identities,
including the input data and tool/service versions used by workflow tasks.
Do not infer learner performance from reference-solution runs.

## Results

With Lighthouse installed, click **Lighthouse → Open plugin** in Codex and
select this benchmark. Tasks and curator rubrics can be browsed before any jobs
are recorded. The [`benchmark.lighthouse`](benchmark.lighthouse) marker lists
which compatible results and quality indexes to show; raw ignored evidence is
available only in checkouts that contain it.

The [leaderboard](leaderboard/README.md) has no results yet. It will show task
outcomes, token use, cost and duration, with links to retained Harbor evidence.
For code-changing tasks it will show functional, engineering-quality and overall
pass rates. A correct submission with a confirmed material quality issue fails
overall; an unresolved quality review cannot count as a pass.
Unknown measurements remain unavailable. The table separates benchmark versions
and infrastructure errors from valid task failures.

See [methodology](docs/methodology.md) for selection and scoring, and
[releases](docs/releases.md) for versioning. [AGENTS.md](AGENTS.md) explains how to
continue authoring and evaluation.
