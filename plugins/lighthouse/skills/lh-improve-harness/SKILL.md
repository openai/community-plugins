---
name: lh-improve-harness
description: Propose one harness change at a time, compare it against a fixed baseline on Harbor tasks, and test the selected candidate once on a held-out set.
---

# Improve a harness

Use this skill to test a specific improvement in correctness, cost or runtime.
Use `lh-run-bench` for execution and reporting. Keep the task and evaluator fixed.

## Step 1: Freeze the comparison

In `experiments/<comparison>/README.md`, record the baseline, disjoint development,
protected and final holdout tasks, repetitions, candidate limit, time/cost budget
and decision rule. Reserve an explicit allocation for the final holdout.

Choose the split unit for the claim. For generalization across workflows, hold
out whole worlds or scenario families; disjoint task IDs can still share context.
Disclose reused worlds and source exposure.

Save exact task files, versions, evaluator, images, model and harness settings.
For stateful tasks, also freeze seed assets, starting state, tool implementation
and evidence contract. Declare the supported harness fields or learner skill/plugin
contents that may change, with exact versions or hashes. Use native Harbor options;
do not add a second agent loop or patch its scheduler.

Decide success before seeing results: for example, higher paired task success
with no required-criterion regression, or equal success at a declared lower cost.
State acceptable variability and resource ceilings. Ties keep the baseline.

## Step 2: Propose one change

Give the proposer only development tasks and their saved evidence. Ask for one
small change based on an observed failure or efficiency hypothesis. Record its
expected effect and keep the baseline available.

Keep protected tasks, results, holdout membership and histories containing them
out of the proposer's accessible workspace. Separate agent contexts alone do not
isolate files on a shared filesystem. If access isolation is unavailable, call
this a development comparison and do not claim a protected evaluation.

## Step 3: Compare both arms

Use `lh-run-bench` on identical task/evaluator revisions, world/verifier images,
repetitions and timeouts. Start each attempt from fresh equivalent state. A changed
learner image or skill bundle must belong to the declared harness change.
Run both arms with the same
allocation and preserve every attempt.

Inspect required-criterion results before aggregate scores. Report paired outcomes,
variability, time, tokens and cost. Missing metrics or incomplete trials cannot
support the corresponding improvement claim. Keep infrastructure errors distinct
from learner failures. Do not tune the evaluator after seeing a candidate's score.

## Step 4: Decide and stop

Apply the recorded rule and retain rejected candidates with their evidence.
Give the proposer development lessons only. Stop at the candidate or resource
limit, on user request, or when the predeclared stopping rule is reached.

Test the selected candidate once against the baseline on the reserved holdout.
A failed or incomplete final comparison leaves the original baseline in place.
Do not use final results to continue searching or silently retry an interrupted
holdout. State the limitation and seek a new evaluation allocation if needed.

## Step 5: Report the finding

Report the selected candidate or no improvement, linked evidence, paired results,
resource use and remaining uncertainty. Keep the decision alongside native runs.
A result on these tasks does not establish general improvement.

Applying a candidate to the user's deployed harness is a separate action. Make
the proposed configuration diff reviewable and follow the user's authorization.
