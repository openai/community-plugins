# Find useful tasks

Start with repository history, a workflow brief or reviewed traces and the user's
requested capabilities. Keep enough
evidence to explain why each selected task matters and how it can be checked.

## 1. Pin the search

Record the source identity, task types, search limit and prior
exposure. In a benchmark, save the inventory in `review/candidates.json` and the
selection rationale in `review/selection.md`. Keep raw downloads in `.local/`;
copy the source snapshots needed to understand selected tasks into `review/`.
A shortlist alone needs only a task-owned output directory.

For repository work, pin the repository URL and discovery commit. Read its
contribution guidance and locate its production code and
tests. Combine a bounded history sample with targeted issue or pull-request
searches. For example:

```sh
git -C SOURCE rev-parse HEAD
git -C SOURCE log REVISION --first-parent --max-count=100 --format='%H %s'
gh pr list --repo OWNER/REPO --state merged --limit 25 --json number,title,url,mergedAt
gh pr view NUMBER --repo OWNER/REPO --json title,body,comments,baseRefOid,headRefOid,mergeCommit,url
```

Use available GitHub tools instead of `gh` when appropriate. Save the query,
limit, retrieval date and returned evidence. Inspect original requests, relevant
discussion, diffs and tests before scoring. Labels and commit titles are leads.
Include open proposals when they fit the requested work.

For workflow candidates, retain the brief, available corpus or fixtures, starting
application state, time assumptions, tool contracts and intended deliverables.
Separate supplied evidence from proposed synthetic context; identify what still
needs authoring or reconstruction. Use snapshot hashes and recipe versions where
applicable, without inventing a historical commit or treating generated facts as
observed customer behavior.

Local Codex traces are another source. Start with reviewed evidence from
[trace investigation](../../lh-investigate-traces/SKILL.md) and retain its
[bundle](../../lh-investigate-traces/references/bundle.md) links, source hashes,
event IDs and reviewer decisions. Do not copy raw private traces into tracked
review files. Keep the local evidence reference and only the reviewed, shareable
request context needed for the candidate.

## 2. Define each candidate

Write the learner's ask, required outcomes, exclusions, value, proposed check and
main grading obstacle. Link the original request and retain its relevant text.
Separate explicit requirements from inferred intent; the reference patch is one
implementation, not the specification.

| Task type | Source and starting state |
| --- | --- |
| Repair | The reported failure and exact revision before its fix |
| Feature | The requested behavior and exact revision before its implementation |
| Review | The proposal head, recorded target revision and their merge-base |
| Workflow | The request, versioned corpus or seed assets, starting state and tool contract |

For merged work, inspect the actual merge and parents rather than assuming a
current PR base is the historical starting point. Keep bug-introducing revisions
separate from fix revisions. A review candidate's defects remain hypotheses until
supported; include sound changes as controls.

For trace-derived candidates, name the scope: a short tool episode with one
observable subgoal, or end-to-end repository work. Retain successful examples and
valid alternatives alongside failures. Separate configuration readiness from
model behavior; a missing MCP server is not evidence of an incorrect tool choice.
Identify source-state gaps before proposing replay. Missing snapshots allow
investigation but block a claim that a task is ready to reproduce. Follow
[trace candidates](../../lh-create-task/references/trace-candidates.md) when
selecting the starting state and learner-visible context.

## 3. Consolidate

Deduplicate by source and request, then group alternate fixes, shared worlds and
related scenarios into families. Keep these families together in later development/holdout
splits. Preserve inspected candidates, exclusions and unresolved evidence; an
uninspected change is not a rejected candidate.

Keep related trace episodes, recoveries and tasks from the same incident in one
family and one development/holdout split. Human review accepts the observation;
native evaluator controls are still required to qualify the resulting task.

Pin each brief before applying the [scoring anchors](scoring.md). Report search
limits and shortfalls rather than filling a requested count with weak candidates.
Keep solution-bearing source evidence outside eventual learner inputs.
