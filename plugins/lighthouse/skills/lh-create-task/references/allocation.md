# Set a working budget

Reuse the user's endpoint and existing authorization. Record a short allocation
in `review/<task-name>/` before builds, model calls or learner trials.

Include the requested stages, models, task count, time/cost limits and concurrency.
Reserve some time for independent review and a finite number of repair cycles.
Keep authoring, review, control runs and learner attempts separately accounted.

Use the selected session model for authoring unless the user chose another. Verify
requested model availability before paid trials; do not silently substitute one.
Allocate model judging separately when the task requires it, including the
mandatory [quality gate](code-quality.md) for assessed software changes. Business
tasks need a judge only for criteria that require judgment; each judging role
has its own credentials and budget.

For several tasks, use a separate author and output directory per task. The
coordinator assigns shared build/container capacity and owns the total budget.
Only one author edits a task; reviewers return findings. Reuse immutable base
images across compatible tasks.

Stop when the requested evidence is complete or the budget is exhausted. Preserve
failed runs and unfinished work for resumption. More agents do not increase the
allocation, and a timeout does not authorize a longer learner attempt. Ask for
missing credentials or a paid-run budget when they become necessary.
