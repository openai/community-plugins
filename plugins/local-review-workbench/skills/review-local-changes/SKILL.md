---
name: review-local-changes
description: Explain and review a declared local change, branch diff, patch, or pull request using bounded review perspectives and independent validation, without editing or posting.
---

# Review local changes

Help a developer understand a change and decide what deserves human attention.
Use this skill for an explicitly requested review or explanation of a concrete
change. It does not apply fixes or approve a merge.

Read the [review contract](references/review-contract.md) before reviewing.
Use its candidate handoff and final output formats.

## Establish the review

1. Read applicable repository instructions. Use requirements and decisions
   already present in the current session; distinguish those from assumptions
   and from behavior inferred from code. Do not invent missing session history.
2. Resolve the user-selected target: local changes, explicit base/head refs,
   supplied patch, or PR. Prefer the declared target; do not silently include
   other worktrees, branches, or unrelated changes. If the target is ambiguous,
   resolve what is available and ask only for the information needed to proceed.
3. Record an immutable review identity: base and head commit IDs plus the exact
   compared diff. For a working tree, also capture the staged and unstaged
   change set and the content identity of included untracked files. A branch
   name, timestamp, or status listing alone is not a stable snapshot. Use
   read-only content hashes or retained tool results; do not create snapshot
   files, stash changes, or reset the checkout. Do not read ignored files,
   credential files, large generated artifacts, or unrelated untracked files.
4. For a branch/PR, use the target merge base and resolved head unless the user
   specifies a different comparison. Include staged/unstaged work only when it
   is part of the requested local scope. If refs, diff, or relevant source are
   unavailable, identify the gap and limit the review to evidence actually read.
5. Give a brief PR overview first: intended behavior, what changes, and the
   important files or contracts a human should inspect. Record any existing
   hosted-review feedback for later independent assessment.

For the bundled first-run example, review
[the synthetic requirements](../../fixtures/compatibility/requirements.md),
[before](../../fixtures/compatibility/before/api.mjs),
[after](../../fixtures/compatibility/after/api.mjs), and
[consumer](../../fixtures/compatibility/consumer.mjs).
Treat these before/after files as the declared comparison. Do not consult the
evaluation answer key during discovery or validation.

## Inspect with bounded perspectives

Use available subagent support when it improves the review. Launch at most
three read-only reviewers, each with the same review identity, requirements,
applicable instructions, and relevant code. Give each a focused lens:

- **Correctness and compatibility:** trace changed behavior and callers, API
  inputs/outputs/defaults/errors, persisted formats, flags, and mixed-version
  interactions where relevant. Look for a concrete regression introduced by
  this change. Trace a representative old caller for every changed public
  contract; an additive field may still affect strict parsers.
- **Simplification:** look for unnecessary complexity introduced by the change
  relative to its stated requirements. First enumerate the required behaviors
  that justify added complexity. Within that scope, inspect duplication, excess
  parameter or data passing, branching, and legacy paths whose support has
  actually ended. Preserve observable behavior and supported compatibility paths.
  Suggest exact simplifications with a concrete readability or maintenance
  benefit, and flag any potential end-user impact. Do not optimize raw line
  count, expand into unrelated cleanup, or propose speculative abstractions.
- **Naming and readability:** inspect names and structure that obscure the new
  behavior or contradict repository conventions. Treat personal preference as
  optional; misleading names are not automatically correctness bugs.

Each reviewer must return the contract's evidence handoff in the current
coordination flow. They must not edit files, run mutating checks, post comments,
invoke this coordinating skill recursively, or launch further reviewers.
Do not expose other reviewers' candidate answers
until their independent discovery is complete. Without subagents, perform the
same lenses sequentially and label the result as a single-agent review.
Do not claim those passes are independent agents.

## Validate and reconcile

The coordinator validates each candidate against the recorded before/after
state, requirement, and actual affected caller. Attempt to disprove it: check
guards, defaults, feature gates, intentional migrations, tests, and downstream
handling. Assess supplied hosted findings the same way; do not accept or reject
them because of where they originated.

Merge duplicate root causes, retain precise evidence, and drop disproven
candidates. Put unresolved but material questions in coverage gaps, not in the
validated finding list. Use one validation pass and, only when useful, one
targeted evidence follow-up. Do not loop until reviewers agree or produce a
minimum number of findings. Agreement is not validation.

Read tests and existing results by default. Run a command only when already
authorized and known to preserve files and avoid network/external writes;
otherwise identify the exact unrun check. Do not install, build, format, or
generate artifacts as part of review. Static reasoning can support a finding,
but must never be described as a passing or failing test execution.

## Return the review

Recheck the base/head, diff, and included local file content identities. If any
input changed during review, do not present stale findings as current. Explain
which scope changed and restart the affected review once if the scope is
stable; if changes continue, stop with the last reviewed identity and coverage
gap. Never stash, reset, or overwrite concurrent work.

Return the contract's concise output: PR overview and human review map,
validated findings, optional suggestions, and checks/coverage limits. Link exact
files and lines from the reviewed revision. Say explicitly when there are no
validated findings. Do not post, approve, merge, or apply fixes.

Optionally offer one short feedback prompt: Was a finding useful, wrong,
duplicated, or missed, and was the review too slow? Suggest the host's
`/feedback` only when it exists; otherwise offer a copyable feedback draft.
Do not invoke feedback tools or send source, conversation history, or feedback
without the user's explicit request. Do not require feedback to finish.
