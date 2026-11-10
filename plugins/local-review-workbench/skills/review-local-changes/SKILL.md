---
name: review-local-changes
description: Explain a local change, branch diff, patch, or pull request. Review correctness, compatibility, simplicity, and naming; validate findings without editing or posting.
---

# Review local changes

Help a developer understand a change and decide where to focus their review.
Use this skill when the user explicitly asks to review or explain a concrete
change. Do not apply fixes or approve a merge.

Read the [review contract](references/review-contract.md) before reviewing.
It defines what reviewers must return and how to present the final review.

## 1. Establish the scope

Read the applicable repository instructions and the requirements and decisions
in the current session. Keep confirmed requirements separate from assumptions
and behavior inferred from code. Do not invent missing session history.

Resolve the user's selected local changes, base/head refs, patch, or PR. For a
branch or PR, compare the target merge base with the resolved head unless the
user specifies another comparison. Include staged or unstaged work only when it
belongs to the requested local scope. Keep other branches, worktrees, and
unrelated changes out of scope.

**Record exactly what will be reviewed:**

- Base and head commit IDs, plus the exact diff.
- For a working tree, the staged and unstaged changes and the content hashes
  or retained contents of included untracked files.

Use read-only hashes or retained tool results. A branch name, timestamp, or
status listing alone cannot identify the reviewed contents. Do not create
snapshot files, stash changes, or reset the checkout. Do not read ignored files,
credential files, large generated artifacts, or unrelated untracked files.

If the target is ambiguous, inspect what is available and ask only for missing
information needed to proceed. If refs, the diff, or source are unavailable,
state the gap and review only the evidence you can read.

Start with a brief overview: intended behavior, what changes, and which files
or contracts a human should inspect. Save any existing hosted-review comments
for independent assessment later.

For the bundled first-run example, review
[the synthetic requirements](../../fixtures/compatibility/requirements.md),
[before](../../fixtures/compatibility/before/api.mjs),
[after](../../fixtures/compatibility/after/api.mjs), and
[consumer](../../fixtures/compatibility/consumer.mjs).
Treat these before/after files as the declared comparison. Do not consult the
evaluation answer key during discovery or validation.

## 2. Review from three perspectives

Use subagents when available and useful. Launch at most three read-only
reviewers. Give each the same recorded revisions and contents, requirements,
repository instructions, and relevant code, with one of these review focuses:

- **Correctness and compatibility.** Trace changed behavior and calling code.
  Check API inputs, outputs, defaults and errors, plus stored formats, feature
  flags and mixed-version behavior where relevant. For every changed public
  contract, trace a representative old caller. Even an added field can break a
  strict parser. Look for concrete regressions introduced by this change.
- **Simplification.** First list the required behaviors that justify added
  complexity. Then inspect duplication, excess parameter or data passing,
  branching, and legacy paths whose support has ended. Suggest exact changes
  with a clear readability or maintenance benefit, and flag potential user
  impact. Preserve observable behavior and supported compatibility. Stay within
  the PR's requirements; do not optimize raw line count, add speculative
  abstractions, or expand into unrelated cleanup.
- **Naming and readability.** Look for names and structure that obscure the
  changed behavior or conflict with repository conventions. Keep personal
  preferences optional. A misleading name is not automatically a correctness bug.

Each reviewer must return the contract's evidence summary to the coordinator.
Keep reviewers' proposed findings separate until their independent reviews are
complete. Reviewers must not edit files, run checks that change files or external
state, post comments, invoke this skill recursively, or launch more reviewers.

Without subagents, perform the same reviews in sequence. Label the result as a
single-agent review; do not claim those passes used independent agents.

## 3. Validate the findings

The coordinator checks each proposed finding against the recorded before/after
code, requirement, and affected caller. Try to disprove it: inspect guards,
defaults, feature gates, intentional migrations, tests, and downstream handling.
Apply the same checks to hosted-review findings, regardless of their source.

Merge findings with the same root cause and retain the strongest evidence.
Drop disproven findings. Put important unanswered questions in the coverage
limits, outside the validated finding list.

Use one validation pass and, when useful, one focused follow-up to resolve
missing evidence. Do not loop until reviewers agree or reach a target number
of findings. Agreement alone does not validate a finding.

Read tests and existing results by default. Run a command only when already
authorized and known to preserve files and avoid network/external writes;
otherwise name the check that was not run. Do not install, build, format, or
generate artifacts during review. Code inspection can support a finding, but
it must never be described as a test that passed or failed.

## 4. Recheck and report

Recheck the base/head commits, diff, and contents of included local files. If
any input changed, explain what changed and withhold stale findings. Restart
the affected review once if the inputs are stable. If they keep changing,
stop and report the last reviewed revisions and contents, plus the resulting
coverage gap. Never stash, reset, or overwrite concurrent work.

Follow the contract's final response format: PR overview, where to focus human
review, validated findings, optional suggestions, and checks and limits. Link
exact files and lines from the reviewed revision. State clearly when there are
no validated findings. Do not post, approve, merge, or apply fixes.

Optionally offer one short feedback prompt: was a finding useful, wrong,
duplicated or missed, and was the review too slow? Suggest `/feedback` only if
the host supports it; otherwise offer a copyable draft. Do not use feedback
tools or send source, conversation history, or feedback without an explicit
user request. Feedback is never required to finish the review.
