# Qualify the final task

Qualification means the saved evidence supports this exact task and evaluator.
It is a curator decision, with the controls available for inspection.

## 1. Check completeness

Read the instruction as a real work request before reading the rubric. It must
leave meaningful investigation to the learner. Check every rubric item against
the request or learner-visible context; a historical patch or gold deliverable alone
does not justify an exact algorithm, ordering or representation. Accept reasonable
alternatives where the task leaves a choice open.

Confirm that every required behavior has an executed discriminator. The
reference and meaningful valid alternative pass; baseline and wrong controls fail
for the intended criteria. The weakened-verifier experiment demonstrates a real
check, and no invalid control is being counted as a behavioral failure.

Check the [requirements table](construction.md#3-map-promises-to-checks) against
raw Harbor results and logs. Keep unresolved requirements and infrastructure
failures visible. Oracle success alone is insufficient.

For complex environments, inspect the [environment evidence](complex-environments.md#5-qualify-the-complete-workflow)
for the exact seed, scenario, tool implementation and backend. Confirm fresh
starting state, required tool access and complete trusted evidence capture.
State whether grading uses retained artifacts or live state, what was regraded,
and which isolation or portability claims remain untested. Reuse shared-world
controls only while their inputs and relevant contracts are unchanged; each
scenario still needs its own discriminating checks.

For code-changing tasks, [code quality](code-quality.md) is a required part of
a benchmark pass. Record its policy version, controls and current human-label
calibration coverage; do not qualify a task with unusable quality evidence. An
unresolved or missing quality grade cannot produce a benchmark pass. A historical
patch is a useful example, not a required design or automatic quality pass.

Set finite agent, verifier and build timeouts in `task.toml`, based on observed
setup and check times. Use the fields supported by the locked Harbor version.
Record the final task revision or content checksums, source commit, dependency
locks and agent/verifier image identities with the evidence.

Freeze clean bundles: omit generated caches and host bytecode, and keep review
outputs outside the task. Reviewer imports must not change the frozen tree
(for Python probes, use `PYTHONDONTWRITEBYTECODE=1`). Check file hashes before and
after qualification and learner runs. A control variant's native task checksum
can differ from the shipped task; record each identity and the exact differences
rather than using the control's checksum as the learner task's identity.
If Harbor records both a directory checksum and a packaged-task digest, retain
their different meanings and covered files; the hashes are not interchangeable.

## 2. Review the final revision

Give a fresh reviewer the final task, original source request, controls and raw
results. Follow the [review workflow](review-workflow.md#4-review-and-repair) for
repairs. Evidence for an older evaluator does not qualify a changed one; rerun
affected checks and retain both versions.

The readiness record in `review/<task-name>/qualification.md` should state:

- Final asset and environment identities.
- Control outcomes and links to native evidence.
- Reviewer identity, findings, resolutions and remaining limits.
- Qualified or incomplete, with the reason and next action.

Review agreement is not a substitute for the executed controls. Leave blockers
open when the evidence or budget is insufficient.

## 3. Add the task to the benchmark

Finalize the task README before freezing the reviewed bundle. When qualified,
move that exact bundle into `tasks/<task-name>/` and verify its identity. Update
the benchmark's task table, external review record and `docs/environments.md`
with the shared recipes and image identities. A later task-file edit, including
its README, changes the package identity and needs an explicit revision record.
Keep negative controls, weakened checkers
and failed drafts outside `tasks/` so Harbor does not mistake them for members.

For the optional Explorer, keep per-task `requirements.md`, `qualification.md`
and `promotion.json` under `review/<task-name>/`, even when they link to shared
family evidence. Its existing promotion receipt reads a `qualified` boolean;
include the reviewed identities and evidence links that support that decision.
Publishing a task directory alone does not establish qualification.

Use the benchmark's release rules when revising a previously released task. A
changed public request requires a new learner attempt. A compatible private
checker correction can be tested by native regrading while retaining the original
score; regrading does not rerun the learner.

Continue to [lh-run-bench](../../lh-run-bench/SKILL.md) for authorized learner
trials and leaderboard metrics. Packaging and qualification costs stay separate
from those learner-run costs.
