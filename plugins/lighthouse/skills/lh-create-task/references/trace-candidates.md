# Turn a reviewed trace into a task

Use this reference for selected candidates from
[lh-investigate-traces](../../lh-investigate-traces/SKILL.md). Keep the ordinary
construction, evaluator controls and qualification workflow; Harbor owns execution.
Importing or reviewing a trace never replays its commands automatically.

## Pin the evidence and scope

Retain the [investigation bundle](../../lh-investigate-traces/references/bundle.md)
reference, original trace hash, episode event IDs, reviewer decision and source
identity in the curator record. Preserve originals and label derived excerpts.
Keep raw private traces local; retain only reviewed, shareable context in tracked
benchmark files. Record redactions and evidence gaps without filling them by guess.

Choose a scope that matches the request and can be checked:

| Scope | Starting context and outcome |
| --- | --- |
| Short tool episode | One bounded subgoal, required tool contract and controlled environment; verify useful results or state changes |
| End-to-end repository task | Original engineering request and starting source state; verify the requested behavior and preserved contracts |

Short and long horizons describe task scope, not separate execution engines.
Do not grade exact call sequences, retry counts or zero errors unless the original
request establishes that constraint. Successful tool calls can still produce the
wrong result; expected errors followed by correct recovery may satisfy the task.

## Reconstruct the starting state

Record the repository commit and any required uncommitted changes, relevant input
artifacts, tool schema and configuration identity. A checkout path or current HEAD
does not prove the state used by a historical episode. Do not read credential
values into the task; name supported authentication requirements separately.

Missing source snapshots or tool configuration may leave a useful investigation,
but block a replay-ready claim when required to reproduce the selected behavior.
Retain the candidate and its reconstruction obstacle until the missing evidence is
available. Check configuration readiness separately from model decisions: tool
absence, authorization failure or a broken server is not itself a learner failure.

For Red → Green repairs, reproduce the defect at the chosen starting state and
verify its repair. For Green → Red → Green regression candidates, establish that
the same behavior check passed before the change, failed after it and passed after
repair under comparable conditions. New failing tests can be normal TDD; weakened
checks or environment changes do not demonstrate recovery. A trace's ordering
alone does not establish which edit caused the defect.

## Write the learner request and qualify

Derive the learner request from the user's original ask and context available at
the selected start. If the task deliberately starts at an observed bug, include
the symptom a maintainer could supply. Keep future trace events, recovery steps,
reference patches, annotations and evaluator details curator-only. Do not turn
every observed mistake into an unrequested requirement.

Retain successful episodes as positive controls and allow meaningfully different
valid solutions. Confirm each proposed assertion with native Harbor controls;
transport success alone cannot verify tool selection, result interpretation or
subgoal completion. Follow [construction](construction.md) and the
[review workflow](review-workflow.md) before [qualification](qualification.md).

Keep related episodes, repairs and alternate tasks in the same family and the
same development/holdout split. Record exposure to original solutions. Review
decisions, static validation and successful historical traces do not substitute
for qualification of the newly packaged task.
