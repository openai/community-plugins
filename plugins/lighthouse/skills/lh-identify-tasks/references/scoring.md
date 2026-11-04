# Pin evidence and rate candidates

## Pin evidence and assess replay feasibility

For historical repairs/features, retain the exact parent and reference commit;
use the first-parent diff for merge commits. For reviews, retain the proposal head
and its merge-base with the recorded target revision, not today's target branch.
Record missing references explicitly for unresolved work.

For workflow tasks, pin the brief, corpus or seed assets and tool contracts.
Record which scenario inputs exist and which remain proposals.

Save inspected evidence or exact retrievable identities, source links and limitations.
Separate source claims, local observations and proposed checks. Flag dependency,
runtime and reconstruction uncertainty: a checkout or existing tests do not prove
replay readiness. Public history is not an unseen benchmark. Keep solution-bearing
provenance in curator artifacts, outside eventual learner inputs.

## Rank complexity

When a source diff exists, measure it with `git diff --no-renames --numstat BASE END`. Identify
the repository's production paths; retain added/deleted lines and file counts
separately for production, tests and docs/config. Churn means additions plus
deletions, not net growth. Flag generated files, code movement and unrelated churn;
retain raw measurements instead of inventing an adjusted size. Churn is supporting
evidence about one implementation, not a direct measure of learner difficulty.
For open PRs, this is proposal size, not a final merged diff.
If no diff exists, leave churn unavailable. Score each dimension from the pinned
brief and inspected context; missing diff evidence alone does not prevent a
complete set of supported scores. Label estimates from an unbuilt scenario as
proposed, and retain unknowns where the needed evidence is absent.

Use the fixed brief and these anchored **0–3 or null** dimensions. A null needs
a missing-evidence reason and next action; it is never a zero. Give every known
score a source-specific rationale. Keep confidence and execution status separate.

| Dimension | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| Reasoning complexity | Direct specified action, little inference | One bounded behavior with straightforward edges | Reconcile at least two causally interacting contracts or evidence sources | Uncover/preserve a non-obvious dependency, state, concurrency or recovery invariant |
| Intent clarity | Outcome absent, contradictory or disputed | Broad goal; essential outcome inferred | Main outcome explicit; consequential boundaries need clarification | Required outcomes and relevant boundaries explicit |
| Observable correctness | No defensible success/failure distinction | Indirect proxies or unanchored subjective judgment | Concrete main checks; a consequential discriminator is unsettled | Concrete observations or anchored rubrics reject plausible mistakes while allowing valid alternatives |
| Context sufficiency | Essential knowledge known to be inaccessible | Demonstrated major information gap changes the solution | Necessary evidence exists but needs collection or declared inclusion | Declared inputs and pinned context suffice without guessing requirements |
| Practical relevance | Outside requested capabilities | Editorial/control value or uncommon low-consequence case | Recurring work in the requested workflow or meaningful recoverable failure | Documented urgency or severe consequence with causal support |
| Scope separability | Cannot isolate a coherent task without inventing scope | Substantial entanglement with unclear dependencies | Coherent task needs explicit exclusions or prerequisites | Components serve one coherent outcome with clear boundaries |

Do not sum these scores. Complexity is a composition stratum, not a reward. If
L/M/H bands are useful, derive L from reasoning 0–1, M from 2, H from 3; unknown
reasoning gives an unknown band. Keep raw churn, breadth, setup cost and severity
separate. Name the interacting contracts or evidence for reasoning 2 and the
invariant for 3. Many files, tools or services alone do not justify either score.

Original intent is distinct from a curator's clearer, narrowed brief. A precise
review ask does not retroactively clarify the source request. Concrete proposed
checks may score well without execution; a missing runtime trial does not by
itself establish missing learner context. Relevance 3 requires specific supported
impact, not merely a security/bug label or hypothetical severity. Calibrate a few
category-specific examples before comparing ratings. These remain estimates until
later fixed-budget attempts establish empirical difficulty.

Record `candidate`, `reserve`, `exclude` or `unresolved` separately from scores:

- Candidate: coherent behavior/reasoning exercise, discriminating checks and a
  plausible bounded authoring step. No minimum complexity.
- Reserve: a named reason such as mechanical/control value, supported family
  redundancy or substantial reconstruction work. Preserve why it may be useful.
- Exclude: supported outside-scope or unusable reason. Size, author type and PR
  state alone do not justify exclusion.
- Unresolved: missing evidence or an unsettled portfolio choice prevents a decision.

Also record selection evidence as complete, needs evidence or packet blocked. A
candidate may need more evidence; complete selection evidence is not qualification.
For bugs, separate `introduces`, `fixes`, `both` and `no established relationship`.
Record causal revisions, preconditions and source links. Static reasoning remains
suspected; maintainer attribution and actual reproduction are distinct evidence
levels. No established bug link is not verified soundness.
