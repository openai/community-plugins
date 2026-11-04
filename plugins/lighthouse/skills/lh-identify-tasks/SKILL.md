---
name: lh-identify-tasks
description: Find and rank useful Harbor tasks from repository history, workflow briefs or reviewed trace evidence. Use for discovery, independent scouting, shortlists or revising task selection.
---

# Identify benchmark tasks

Produce a source-grounded shortlist with clear outcomes and proposed checks.
Keep task quality and expected reasoning difficulty separate.

## Step 1: Find candidates

Follow [discovery](references/discovery.md) to pin the source, set a search budget
and inspect requests, available context and proposed outcomes; read diffs and tests
where applicable. Follow the user's requested task types and
count. A shortlist alone needs no benchmark scaffold.

For local Codex traces, first use [lh-investigate-traces](../lh-investigate-traces/SKILL.md)
to review episodes and evidence. Carry reviewed candidate links into discovery;
imported events and machine suggestions do not establish a failure or qualification.

For a broad search, split independent agents across bounded scopes using
[scouting](references/scouting.md). Record exclusions and prior exposure.

## Step 2: Define and rate each task

Write the learner's ask, required outcomes, exclusions, value, proposed check and
grading obstacle. Separate explicit requirements from inferred intent. Pin that
brief before applying the six [scoring dimensions](references/scoring.md).

Explain every score with source evidence. Missing evidence is unknown, not zero.
Keep churn, setup cost and confidence separate from the anchored scores.

## Step 3: Challenge and select

For a broad selection, use a fresh challenger to assess a sample without seeing
the scouts' scores. Follow [challenge and consolidation](references/scouting.md).
Resolve supported findings within budget and retain disagreements and gaps.

Choose a useful portfolio, remove duplicates and keep related task families
visible. Do not choose only easy tasks to produce passing learner results.

## Step 4: Save the shortlist

In a benchmark, write `review/candidates.json` and a readable
`review/selection.md`. Use ordinary records containing:

- Search scope, pinned source or snapshot identities, request snapshots and limits.
- Each candidate's brief, family, proposed checks and reconstruction obstacles.
- Six scores with reasons, confidence, applicable diff measurements and evidence gaps.
- Selection decision and rationale, challenger findings and unresolved questions.

Check counts, links, duplicates and score anchors. Preserve earlier inventories.
These are candidates; [lh-create-task](../lh-create-task/SKILL.md) turns selected
work into runnable, qualified tasks. When comparisons need weighting, read
[weighting](references/weighting.md) before freezing the comparison plan.
