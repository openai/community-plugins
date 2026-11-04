# Scout and challenge a selection

For broad discovery, use independent subagents to inspect different parts of the
source or different workflow families. The coordinator owns the search budget and final shortlist.

## 1. Assign separate scopes

Split work by subsystem, history range, pull-request batch or workflow family. Give each scout:

- The source, pinned evidence identities and specific search boundary.
- Requested task types, exclusions and the [scoring anchors](scoring.md).
- An inspection/time limit, output path and stopping condition.
- Read-only discovery work, including requests, context and applicable diffs/tests.

Scouts return candidate briefs, evidence, scores with reasons, inspected counts
and unresolved questions. They do not modify the source or run learner attempts.
Use available collaboration tools directly; no separate agent protocol is needed.
Keep partial results and record overlapping inspections separately from unique
candidates.

## 2. Challenge independently

Reserve time for a fresh challenger. Supply briefs and raw source evidence for a
sample spanning task types and difficulty bands. Withhold scout scores and
selection decisions until the challenger has rated that sample.

Compare ratings and inspect unsupported requirements, grading obstacles, coverage
gaps and promising reserves. Review-task selections need both defect and
sound-change controls. When independence is unavailable, label the review as
self-review.

## 3. Choose the portfolio

Verify evidence, reconcile duplicate families and resolve supported findings
within the remaining budget. Record changed decisions, disagreements and gaps in
`review/selection.md`. Explain the usefulness and expected difficulty of each
chosen task without summing the six dimensions into a quality score.

Stop when the selection is supported or the budget is exhausted. Agreement among
scouts improves consistency; executed evaluator controls still come later in
[task creation](../../lh-create-task/SKILL.md).
