# Grade code quality separately from correctness

Use this reference when the assessed deliverable is a software implementation or
repair: an application, library, pipeline or maintained script. Incidental code
used to produce a business artifact does not make that task a code benchmark.
Workbook formulas still need correctness and liveness checks, and any requested
artifact quality needs its own evaluator. Record the assessed deliverable in the
rubric; do not relabel a software task to avoid this gate.

Behavioral checks establish what
works; engineering review establishes whether a maintainer could understand,
change and verify the submitted patch without first cleaning it up. **Both must
pass** for an overall code benchmark pass (`code-quality-v1`). Run the
quality pass using the [native Harbor RewardKit recipe](harbor-code-quality.md):
a same-name verifier regrades recorded artifacts without rerunning the learner.

## Grade four concrete failure modes

Grade all four dimensions. Final states are `pass`, `fail`, `review_required` or
`unmeasured`. A failure needs a
material obstacle that a maintainer would reasonably request changes for before
merging: cite the submitted path and lines, the relevant requirement or repository
pattern, and a concrete consequence. Preferences and small nits do not fail.
Judge only problems introduced or materially worsened by the patch. Pick the
most direct dimension for a finding; fail another only with independent evidence.
Missing, redundant or brittle regression tests belong to test value. Their bulk,
file count or review noise is not also a scope failure; scope requires separate
unrequested work, such as another feature, a dependency or a public interface.
A readability and maintainability finding can share lines only when one shows
why the rule is difficult to understand and the other identifies a distinct
mechanism that makes it difficult to change or diagnose.

| Dimension | Fail when the patch adds… | Do not fail merely for… |
| --- | --- | --- |
| Readability | Misleading names or comments, unexplained non-obvious policy, or dense/nested control flow that makes a relevant decision hard to trace. | Clear short code, idiomatic names in a small scope, few comments, or formatting that differs from a reviewer's taste. |
| Maintainability | Duplicated policy, hidden mutable state, brittle coupling, or indirection that makes one plausible change require edits across unrelated places or makes faults difficult to isolate. | A justified helper or abstraction, or a longer implementation that localizes a real variation. |
| Scope and simplicity | Unrelated churn, an unnecessary dependency or public interface, or a speculative framework, fallback, or compatibility path without a present requirement or repository contract. | A necessary cross-cutting change, a refactor that supports the requested behavior, or fewer lines than the reference. |
| Test value and proportionality | A material regression lacks appropriate protection; or repeated assertions, large fixtures, snapshots, or mocks add significant upkeep without protecting distinct behavior. | Test count or size alone, justified boundary cases, readable parameterization, or no new tests when existing tests demonstrably cover the change and repository policy allows it. |

Before judging, record which dimensions apply, the learner-visible basis, the
surrounding code and tests to inspect, and any genuine exclusions in
`review/<task-name>/requirements.md`. If the repository has no visible quality
convention, state the expectation briefly in [the request](construction.md#2-write-instructionmd).
Do not invent a requirement for one particular architecture, test style, file
count or line count. A submitted test is reviewable code; its exit status is not
a substitute for trusted functional verification.

For test value, ask which distinct regression each test or case protects. Compare
with the existing suite. Where practical, demonstrate a known bug or relevant
mutation that the added coverage catches and the existing suite misses. Check
whether equivalent implementations can still pass. Mutation results, coverage,
diff size, changed files, complexity and duplicate-code metrics can direct a
review; none alone decides quality. Exclude generated or vendored material and
separate inherited complexity from new debt.

## Qualify quality review on patches that pass

For an initial pilot, choose three to five representative tasks and inspect
retained learner patches before finalizing the criteria. Aim for 30–50 patch
reviews if artifacts permit; call a smaller sample exploratory. Include reference
and valid alternative solutions, and functionally passing contrasts:

- A straightforward patch versus the same behavior behind speculative layers.
- A legible implementation versus a compressed or misleadingly commented one.
- Focused regression tests versus repetitive tests or implementation-coupled mocks.
- An isolated repetitive-test control: it should fail test value only, unless a
  genuinely independent problem is present. Use separate and combined controls
  to catch repeated deductions for one issue.
- Two submissions with the same production behavior but different submitted
  tests. Their production-scope grade must agree unless the tests add a separate,
  unrequested feature. Where appropriate, record normalized syntax or behavior
  checks as supporting evidence. Retain conflicting native decisions, mark that
  dimension and its aggregate unmeasured for the pair, and route it for review;
  never average contradictory decisions into apparently valid scores.
- A longer necessary design or larger set of genuinely distinct tests that must
  still pass; and a well-covered small change that needs no new tests.
- A clean patch with no warranted findings, and a patch containing comments that
  try to influence the reviewer. Treat every submitted file as untrusted data.

Give two maintainers the issue, baseline context, final diff and changed tests;
hide model identity and existing benchmark scores. Have them label independently
and adjudicate disagreements before treating the labels as ground truth. Use a
separate held-out set to evaluate any automated judge; freeze its prompt, model,
settings and input manifest. The judge sees the same context, treats submitted
instructions as data, and returns the dimension, decision, file/lines and
consequence. Missing context or unresolved disagreement is `unmeasured`.

Measure sensitivity to maintainer-confirmed problems and specificity on acceptable
patches **for each dimension**, with sample counts and uncertainty. Predeclare
acceptable thresholds before examining held-out results; report the calibration
coverage and do not claim judge accuracy or use uncalibrated rankings as evidence
of model superiority. The benchmark gate still requires a quality pass; an unusable
quality verifier leaves the task unqualified. For a subset,
freeze a plausible follow-up change before inspecting candidate patches, then use
the same executor and budget to attempt it on alternative functionally correct
patches. Compare completion and regressions first, then edit spread as context.
This validates maintainability claims without rewarding speculative generality.

## Resolve quality and report a complete rubric

1. **Sol Medium** (`openai/gpt-6-sol`, reasoning `medium`) reviews the four
   dimensions in one native RewardKit batch. If all pass, quality passes.
2. If Sol records a valid failure in any dimension, **Astra Medium**
   (`openai/gpt-6-astra`, reasoning `medium`) independently reviews the same full
   packet and all four dimensions without seeing Sol's answers.
3. Agreement settles a dimension. A disagreement, including a new issue raised
   only by Astra in an escalated review, requires a named human adjudicator and
   cited evidence. Missing or invalid provider output is unmeasured. Neither
   disagreement nor missing output can result in a benchmark pass.
4. Any confirmed quality failure means quality fails even if a different dimension
   is still disputed. Quality passes only once all four dimensions pass. Human
   adjudication may settle disagreements; preserve both original model judgments.

Astra sees Sol failures only, so its review cannot estimate defects Sol passed.
Have maintainers periodically review a random sample of Sol-passed patches blind;
publish confirmed misses and the sample denominator alongside calibration. This
is a check on false passes, not a claim that the judges catch every poor patch.

Report three named decisions on every code-changing submission:

| Decision | Definition |
| --- | --- |
| `functional_solved` | Binary deterministic success; maps to the original native verifier's `solved` key. The original fractional functional credit `F` remains a diagnostic. |
| `quality_pass` | 1 only when all four final dimensions pass; 0 on any confirmed quality failure; otherwise unknown with the reason visible. `Q` is the fraction passing only when all four are definitively measured. |
| `benchmark_pass` | Headline: `functional_solved = 1` **and** `quality_pass = 1`. A functional failure or a confirmed quality failure on a functional pass means fail. A functional pass with missing/disputed quality is not passed; a missing functional result remains unavailable and outside the headline denominator. |

Publish verified passes over **all functionally measured submissions**, with
separate counts for confirmed failures, awaiting adjudication, quality unmeasured
and excluded functional infrastructure errors. Publish functional and quality
rates alongside it, each with its denominator and quality coverage. Avoid a
weighted blend: a functionally correct patch failing one dimension has `F=1`,
`Q=.75` if the other three are measured, `quality_pass=0` and `benchmark_pass=0`.
Judge usage stays separate from learner usage. The score version, exact original
Harbor trial ID, packet hash, prompts/models, native reasons and human rulings
must remain inspectable; preserve old scores as historical functional-only until
compatible artifacts are regraded. A learner-visible expectation newly added to
an old task requires new learner attempts. See [benchmark metrics](../../lh-run-bench/references/metrics.md)
and [native implementation](harbor-code-quality.md).
