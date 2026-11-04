# Methodology

This benchmark measures useful work grounded in its retained source material. Its
instructions describe realistic requests. Private verifiers measure the requested
outcomes and learner-visible contracts using the qualified evidence contract.
Detailed rubrics and scoring stay in curator review files, outside learner inputs.

## Select tasks

Keep discovery bounds, source requests and pinned source/data identities in
`review/`. Rank candidates on reasoning complexity, intent clarity, observable correctness,
context sufficiency, practical relevance and scope separability using the
selection skill's 0–3 anchors. Do not sum the dimensions; reasoning complexity is
a composition choice, not a quality reward. Record unknowns and explain the portfolio.

For workflow tasks, retain the role, source brief, corpus provenance and tool
contracts. Keep tasks sharing a world or workflow family visible; split by that
family when claiming held-out evaluation. Historical tasks, public solutions and
generated variants of an exposed scenario do not establish performance on unseen work.

## Qualify the verifier

For each task, map graded requirements to the request or learner-visible
contract and save the executed evidence in `review/<task>/`. Accept reasonable
solutions where the task leaves a choice open. Qualification requires:

1. The reference solution and a meaningfully different valid alternative pass.
2. The unchanged baseline and plausible wrong solutions fail for the
   intended behavior, rather than an installation or runner error.
3. Every required promise has an executed check that separates correct from
   incorrect behavior.
4. A deliberately weakened verifier admits a relevant bad control that the
   shipped verifier rejects. Keep the weakened copy outside `tasks/`.
5. An independent reviewer inspects the final assets and raw evidence, with
   blocking findings resolved.

For export-based checks, private tests are baked into a separate verifier. Declare
all required submitted paths as native Harbor artifacts; missing files must not
be replaced with baseline inputs. For service-backed tasks, prove reset and trusted
state capture, or qualify native live-state verification with explicit replay
limits. A learner's claim that an action happened is not state evidence.
Keep failures and rerun affected controls after evaluator changes.

## Score and report

Overall success requires every required criterion. Save per-criterion details in
verifier logs. A learner's behavioral failure is a valid result; an infrastructure
failure is an incomplete attempt and remains visible.

For code-changing tasks, report deterministic functional pass, engineering-quality
pass and overall benchmark pass. The overall result passes only when both gates
pass. Sol Medium grades readability, maintainability, scope and test value;
Astra Medium independently reviews Sol failures on the same evidence. A confirmed
material issue fails quality; disagreements require a human, and missing or
disputed quality cannot produce a pass. Report verified overall passes over all
functionally measured submissions, showing unresolved quality and unavailable
functional attempts separately. Partial-credit diagnostics do not override either gate.

For non-code tasks, report their declared outcome criteria separately from code
quality. Check calculations, files and service effects deterministically where
possible. Use a qualified model judge only for criteria requiring interpretation;
retain its prompt, model, evidence and unresolved judgments.

Report task and aggregate results for the same benchmark version, model, settings
and repetitions. Keep tokens, cost and learner/trial duration scopes explicit.
Label estimated cost with its rates and date; estimates are not billed spend.
Authoring and qualification overhead are separate from learner cost.

Keep the native configs, results, trajectories and submitted artifacts. Regrading
creates a new result linked to the original; changing public requirements requires
new learner attempts. See [releases](releases.md).
