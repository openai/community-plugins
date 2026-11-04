# Run and regrade

The benchmark pins Harbor in `pyproject.toml` and `uv.lock`. Native job configs,
trial results and submitted artifacts are the evidence. Keep them after the run.
Read task identity from native results, configs and locks; Harbor can truncate
long trial-directory names, so those names are not a reliable task key.

## Run Codex

Verify options with the installed Harbor version. Replace the placeholders with
the declared model, Codex version and experiment name:

```bash
uv run --locked harbor run -p tasks -a codex -m MODEL \
  --agent-kwarg version=CODEX_VERSION \
  --n-attempts 1 --n-concurrent 1 --max-retries 0 \
  --job-name RUN_NAME --jobs-dir "$PWD/jobs"
```

Use task-defined timeouts. A time limit is not a dollar cap. Monitor against the
approved budget and stop launching work when its remaining allocation is too small.
For larger runs, save a native Harbor job config and pass it with `--config`.
Keep the resolved job config and lock with the experiment record.

With Harbor 0.23.0 on Docker, pass an absolute `--jobs-dir` path. Relative artifact
paths can resolve against the Compose context and trigger failed copy attempts
before a fallback; confirm collection from the native manifest, not console output.

Use Harbor's supported model credentials. Check `harbor agent schema codex` and
the installed credential documentation. Reuse the user's existing explicit
authorization; ask only for missing setup before live calls. Harbor 0.23.0 supports
an existing Codex login through `CODEX_AUTH_JSON_PATH`, as well as API credentials.
Record the route and its approval, never the credential value.
Distinguish an expired login from a credential tool unable to reach its desktop
app through the command sandbox. Retain a sanitized diagnostic and use the
already authorized execution path before requesting account or trust changes.
Do not put credential values in command arguments or copy host credentials into
task images. An installed Codex agent has credentials available in its environment;
do not claim that the learner process cannot access them.

Before a first run, verify the resolved toolchain, lockfile index and Docker access.
Inspect the installed adapter's actual Codex command: Harbor 0.23.0 forces
`--dangerously-bypass-approvals-and-sandbox`, which can conflict with managed account
requirements. Test the selected authentication route and environment; a prior
managed-login failure does not establish that API-key runs need a patch. Keep
stock Harbor when it works within the applicable policy. Do not weaken account
policy to make it run. A local runtime patch
needs the applicable authorization and a retained diff/hash; it is not an upstream
feature or an unmodified installation. Test the permitted sandbox and configured
approval path inside the actual container. Model authentication alone does not
prove commands can run there.

Use a separate native job for the first learner task so an infrastructure failure
cannot start the rest of the batch. Keep setup and learner budgets distinct;
monitor installation progress before calling a download stalled. Freeze reviewed
task files while other work continues, and point the job at that snapshot.

## Preserve verification inputs

Follow the task's qualified evidence contract. For repository and export-based
tasks, use declared Harbor artifacts and a separate verifier. The learner has the
network access its task declares; deterministic verification stays offline, with
dependencies already in the verifier image. The native collector runs
after the learner phase; this is a collected-artifact contract, not a snapshot of
every byte in a stopped environment. Read the upstream
[separate verifier documentation](https://github.com/harbor-framework/harbor/blob/15da91c18580a25489f5bdf2ee71029f3ff3bb2e/docs-mintlify/core-concepts/tasks/separate-verifier.mdx).

For service state, confirm the tested finalization/export runs before collection,
or that the native live-state verifier runs before teardown. Preserve that trial's
state observations, tool identities and reset evidence. Use the tested concurrency
limit; shared services that cannot isolate resets require sequential execution.
See [complex environments](../../lh-create-task/references/complex-environments.md).

## Regrade a saved submission

Use this path only when the recorded artifacts contain every required input.
Live-state grading is not automatically replayable; use its tested restoration
contract or retain the original result and record that regrading is unsupported.

```bash
uv run --locked harbor trial regrade "$PWD/jobs/RUN_NAME/TRIAL_NAME" \
  -p tasks/TASK_NAME -e docker -o "$PWD/jobs/regrades"
```

For a whole job:

```bash
uv run --locked harbor job regrade "$PWD/jobs/RUN_NAME" \
  -p tasks -e docker --n-concurrent 1 --jobs-dir "$PWD/jobs/regrades"
```

Use absolute output paths for Docker regrades too; inspect restored artifact
hashes even when Harbor reports a successful copy fallback.
The updated task name must match the source. Every input required by the updated
verifier must already be in the recorded artifacts. Keep the source unchanged,
record the new checker revision, and link both result directories. A changed
instruction or missing submission input requires a new learner trial.

Regrade does not start an agent environment. It creates a new verifier result;
do not add copied usage to the learner leaderboard a second time. See the upstream
[regrade documentation](https://github.com/harbor-framework/harbor/blob/15da91c18580a25489f5bdf2ee71029f3ff3bb2e/docs-mintlify/core-concepts/jobs/regrade.mdx).

For the required code-quality gate, use the [quality-specific regrade recipe](../../lh-create-task/references/harbor-code-quality.md). It passes each original trial UUID individually, runs Sol Medium and conditionally Astra Medium, and leaves the original deterministic result untouched. Use the [quality index](metrics.md#complete-code-rubric) to include it in the complete rubric.
