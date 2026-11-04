# Test the evaluator

Test both sides of each requirement: useful solutions pass and plausible wrong
solutions fail for the intended reason. Save control scripts or patches in
`review/<task-name>/controls/`; link their native results from the review record.
Keep generated task copies and large raw jobs in ignored authoring directories.

## 1. Prepare controls

Write the reference solution and a meaningfully different valid solution.
An alternative should vary a choice the rubric permits: implementation, tool
sequence or deliverable structure. Renaming a variable or paraphrasing a heading
does not show that the verifier accepts different solutions.
Derive a valid alternative from the public task before consulting the reference
output. For multi-application work, vary a permitted responsibility assignment or
evidence-linking route as well as syntax; an oracle-shaped alternative can preserve
the same hidden assumptions as the checker.

Add a baseline/no-op and plausible wrong solutions covering every required
promise. For each control, record the expected criteria and why. Keep mutations
small enough that their results are interpretable.

Make disposable task copies under `.local/controls/`. Replace each copy's oracle
solution with the control being tested. Every control starts from the exact task
baseline, not a previous reference run. Apply its implementation directly; do not
have a replacement `solve.sh` call itself.

For stateful tasks, the reference must exercise the learner's permitted surface;
directly seeding the expected final database does not qualify those tools. Use
targeted controls for the actual risks: stale source selection, a polished answer
without the requested write, collateral changes, fabricated evidence, or listing
several incompatible answers to satisfy an inclusion-only judge.

For structured deliverables, include empty, null, wrong-type, truncated and
non-finite candidate values where relevant. They should fail the intended
criterion, not crash the verifier. Distinguish them from missing or corrupt
trusted collection evidence, which leaves the trial incomplete. Fields authored
by the learner remain candidate data even inside an authentic service export.

For formulas or executable artifacts, exercise the advertised language and valid
equivalents as well as malformed syntax. Perturb inputs to distinguish live
behavior from hard-coded answers. Verify required preservation beyond cell
values or final record fields when the task promises styles, visibility or no
transient changes. Validate native file types too: formula-looking text in an
XLSX cell is not a live formula. Include a plausible but content-free explanation
when one is required; a prose-length check should not pass it.

## 2. Run through Harbor

From the benchmark root, run each variant with the locked dependency:

```sh
uv run --locked harbor run -p .local/controls/CONTROL -a oracle -e docker \
  --n-concurrent 1 --max-retries 0 --jobs-dir "$PWD/jobs"
```

Retain the native job/trial directory, submitted artifacts, logs, durations and
criterion details. Check that installation and the verifier completed before
interpreting rewards. Infrastructure errors leave the control unresolved.
Keep `--jobs-dir` absolute for Docker runs and regrades. On Harbor 0.23.0,
relative collection paths can resolve against the Compose context and trigger
copy failures before fallback; check the native manifest and restored bytes.

The reference and valid alternative must pass. The baseline and wrong solutions
must fail their intended criteria. Every required promise needs an executed
assertion and a corresponding detected violation. Record these links in the
[requirements table](construction.md#3-map-promises-to-checks).

## 3. Challenge the checker

Make a disposable weakened-verifier copy that omits a relevant assertion. Run the
same violating control against both versions: it must pass the weakened copy and
fail the intended verifier. This demonstrates that the check changes the outcome.
Never copy the weakened verifier into the released task.

For a service-owned outcome, challenge whether a learner can edit the evidence or
claim the change without making it. Exercise the applicable reset, collection
and restoration checks in [complex environments](complex-environments.md).

Inspect the verifier's trust boundary too. Load assertions, fixtures and runner
configuration from the verifier image, not learner-editable files. If submitted
code can replace a test runner or imported helper, test a control that tries it.
Submitted test counts and exit codes alone are not trusted evidence.

For submitted code, treat imports and stdout as untrusted execution. A private test file
can still be bypassed if candidate code exits before its assertions or fabricates
the result. Probe this boundary, including candidate-controlled serialization
when extracting data. Keep assertions and expected-output generation outside
that process where possible, and apply any privilege drop before submitted imports.

## 4. Review and repair

A fresh reviewer first reads the instruction and learner-visible context, then
the task assets, original request, requirements table and raw results. Check for
solution hints and hidden requirements as well as false acceptance and rejection.
Withhold earlier conclusions until it has assessed them independently. Add a
specialist for a specific unresolved question, not simply to increase the number
of reviewers.

For each supported finding, retain the counterexample and old/new results:

- False acceptance: the wrong solution passes the old checker and fails the repair.
- False rejection: the valid solution fails before and passes after the repair.

Rerun the reference, alternative and affected negatives. Update the requirements
table and get fresh review of the changed assets. A test edit or reviewer vote
does not replace execution evidence. Stop incomplete when the repair reserve is
exhausted, preserving failed revisions and unresolved findings.

## Optional model grading

Use a judge only where deterministic observations cannot express the criterion.
Pin its prompt, model, settings and evidence inputs; declare its separate network
and cost needs. A source investigator can gather evidence first, then a judge
scores that fixed evidence. Missing facts remain unknown.

Run valid alternatives and adversarial controls for judged criteria too. Keep
judge uncertainty visible and prevent subjective scores from compensating for a
failed required deterministic check. Harbor's [RewardKit workflow](../../../vendor/harbor/skills/rewardkit/SKILL.md)
supports native LLM judging. For engineering quality use the
[Lighthouse execution recipe](harbor-code-quality.md), which regrades recorded
artifacts in a separate quality verifier; it does not replace functional checks.

For code-changing tasks, use the [code-quality controls and calibration
procedure](code-quality.md#qualify-quality-review-on-patches-that-pass). Include
patches that still pass the functional suite but introduce real maintenance cost,
and valid patches that are longer or add more useful tests. Qualify the gate on
these controls and track accuracy against held-out human labels. Sol Medium grades
first; Astra Medium independently reviews Sol failures. Unresolved disagreement
requires adjudication and cannot count as a benchmark pass.
