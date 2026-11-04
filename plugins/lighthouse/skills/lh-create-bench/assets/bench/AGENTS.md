# Working on this benchmark

Read the README and relevant task README before editing. This is a native Harbor
benchmark; it has no Lighthouse runtime dependency.

## Setup and execution

Use `uv sync --locked` and `uv run --locked harbor ...`. Keep `pyproject.toml` and
`uv.lock` together when updating Harbor. Use the Linux Docker environment documented
in `docs/environments.md`; task-specific setup belongs in task assets.

## Authoring

With Lighthouse installed, `$lh` selects the next step. Direct skills are
`$lh-identify-tasks`, `$lh-create-task` and `$lh-run-bench`. Without the plugin,
the README, native Harbor files and saved review evidence remain sufficient to
inspect and run existing tasks.

Keep drafts in `.local/drafts/` and qualification evidence in `review/<task>/`.
Move a task into `tasks/` only after its reference solution, valid alternative and
adversarial controls have executed as described in `docs/methodology.md`.
Keep private verifier code and solutions outside the learner environment.

## Results and changes

Preserve native job results, submitted artifacts and failed attempts. Derive the
leaderboard from saved results, with unavailable metrics labelled honestly.
Record execution budgets in `review/allocation.md` before paid model runs.

Task changes require a new task version and affected qualification checks.
Keep the task index current and follow `docs/releases.md` before naming a release.
Local work does not authorize publication of tasks, results or images.
