# Review trace evidence

Read the import notices first. A partial trace can support a useful observation
while leaving the task outcome or earlier source state unknown. The initial
importer records messages and tool events, not verified app-state transitions.

## Choose episodes

Use a request and its related action/result sequence as the review unit. Keep the
surrounding context available; a highlight is an entry point, not the complete
evidence. When multiple sessions are imported, retain their separate identities
and do not infer a shared chronological order from timestamps alone.

Sample across tool families, session length and available outcomes. Include clean
successes, recovered errors, apparent failures and incomplete evidence. Record
the sampling rule and reviewed count. A failure-enriched discovery sample cannot
estimate how common failures are across all work.

## Annotate before assigning a taxonomy

Write what happened in the reviewer's words and link the relevant event IDs.
For example: “The search returned a next-page cursor, but the summary used only
the first page.” Record a suspected explanation separately from that observation.
Use labels only once repeated observations make them useful.

Save machine-proposed notes as `suggested`. Only a human decision makes a note
`confirmed`; accepting an observation does not prove its cause. When a definition
changes, revisit earlier notes rather than silently relabeling them. Follow the
[bundle format](bundle.md) for annotation fields.

For each useful finding, distinguish these questions:

| Question | Evidence to inspect |
| --- | --- |
| Was the capability available? | Recorded configuration, server startup/discovery and environment evidence |
| Was the invocation valid? | Tool schema, arguments and explicit protocol or execution result |
| Was the result used correctly? | Returned records/state, subsequent actions and the requested subgoal |
| Did the task succeed? | Independent behavior checks and the user's actual requirements |

A successful call can target the wrong resource or return incomplete data. An
expected negative probe or successful recovery can still be competent behavior.
Do not turn call counts, error-looking text or a confident final answer into a
task score. Recorded output may itself be truncated or inaccurate.

## Connect edits to checks

For a suspected transition, record the named behavior check, source-state identity,
test/runner/config identity, environment and actual receipt. A commit alone does
not capture dirty or untracked files. Current source is not historical evidence.

Compare the same check at the relevant states. Check whether assertions, selected
tests, skips, dependencies or services changed. New red tests may represent TDD;
a new test that also fails before the edit reveals an older defect. If states
cannot be reconstructed, report “failure observed after these edits” with the
gap, rather than assigning a causal regression label.

Do not execute recorded commands or MCP calls during investigation. Reproduction
belongs to a selected task with a declared disposable environment and trusted
checks under the existing task-creation workflow.

## Save the handoff

Keep `findings.md` concise: chosen scope, reviewed coverage, evidence-linked
observations, suggested versus confirmed decisions, unresolved explanations and
proposed candidates. A candidate needs the original request, a proposed starting
state, required outcome and an independent check. Explicitly list reconstruction
gaps. See [trace candidates](../../lh-create-task/references/trace-candidates.md).

Group related episodes, repairs and short/long variants into one task family for
development/holdout splitting. Keep full continuations, fixes and hidden rubric
material in curator evidence. A trace supplies provenance, not the learner's
answer and not automatic permission to run or publish an evaluation.
