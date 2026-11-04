# Write the request and rubric

A good task states what must work without prescribing the reference patch. Keep
the original request close enough for a reviewer to trace each requirement.

## 1. Retain the source

Save the request, relevant discussion, exact starting context and preparation/check
commands under `review/<task-name>/`. For repository work, retain starting/reference
revisions; for workflow tasks, retain the brief, corpus or world snapshot, tool
contracts and scenario identity. Record contradictions,
exclusions and prior exposure. Use [task discovery](../../lh-identify-tasks/SKILL.md)
if a candidate still needs source evidence.

For repairs and features, start before the reference implementation. For reviews,
start with the proposal under review. Export only the intended source; later Git
history, private findings and solved code do not belong in the learner image.

For an authored workflow, preserve which facts came from supplied material and
which were generated. Freeze the actual inputs; a generation seed alone does not
identify changing model output. Use [complex environments](complex-environments.md)
when the task needs shared world assets, services or stateful tools.

## 2. Write instruction.md

Write the request a colleague would give someone doing the work: the problem or
desired outcome, useful starting context and essential constraints.
Use a few short paragraphs; add detail when the work needs it, not to mirror the
test suite. Follow the [Terminal-Bench examples](../../lh-create-bench/references/terminal-bench.md#task-instructions).

For example: "Failed profile updates sometimes erase the saved settings. Fix the
update flow so a rejected update leaves the previous settings intact and the user
can retry successfully. The app is in `/app`; use its existing dependencies."

Leave investigation and implementation to the learner. Omit criterion IDs, reward
math, verifier internals, exhaustive edge-case lists and repair steps learned from
the reference patch. A path helps when it locates the project or an input; do not
turn it into a tour of every function to change. Necessary output formats, domain
rules and operational constraints still belong in the request.

Do not hide an arbitrary requirement by deleting its explanation. Each graded
behavior must follow from the request or the supplied context's existing
contract. If competent workers could choose differently, accept those choices
or state the essential product decision briefly. Do not copy the evaluator into
another learner-visible file just to shorten `instruction.md`.

For code-changing tasks, make the default engineering-quality expectation
learner-visible when the repository does not already establish it. For example:
"Keep the change focused, follow the surrounding code, and add or update tests
where they protect the changed behavior." Keep the scoring and reviewer anchors
in the curator rubric.

## 3. Map promises to checks

Keep the detailed rubric in `review/<task-name>/requirements.md`, outside learner
inputs. Give each observable promise a stable ID and its basis in the request or
learner-visible context:

| Requirement and learner-visible basis | Observable check | Valid alternative | Violating control | Native result or gap |
| --- | --- | --- | --- | --- |

Several promises can share a test, but record their assertions separately. Check
state changes and return values independently when both are promised. Exercise
later actions when recovery, persistence or repeated use is part of the request.
A setup crash does not demonstrate that a violating control was detected.

Record scoring here and in the task README. Use `reward.json` with a normalized
`reward` and named criterion results, for example
`{"reward": 1.0, "criterion_R1": 1.0}`. By default, success requires all required
criteria. Document any partial-credit aggregation for curators and result readers.
Private fixtures, answers and controls stay outside the learner environment;
curator files in a public benchmark are not a confidential holdout.

For cross-application work, distinguish deliverable correctness, required state
changes and preservation of unrelated state. A final message claiming success
does not prove a write occurred. Grade a particular action or ordering only when
the request or supplied policy requires it; valid tool sequences may differ.
Check that required conventions and authoritative versions can be discovered
through the learner's tools. Missing business context is an authoring gap.

Check relationships as well as individual values: a correct issue for the wrong
project, a draft attached to an old workbook, or a status copied from another
revision is still wrong. Accept equivalent supporting sources where the public
contract permits them; a reference solution's citation set is not a hidden quota.
Resolve proof through the relationships the public contract permits, such as
ticket citations and linked action records, rather than one preferred field.
Still verify the proof's scope, identity, timing and any required execution; an unlinked
or stale record is not an alternative way to satisfy the request.

Compare business identity rather than an incidental display string. Require a
specific ID only when the public contract does; otherwise accept documented
equivalent names through the supplied identity records. Keep wrong people and
ambiguous names distinct. Include a valid alternative using another permitted
representation instead of assuming the reference's spelling is mandatory.
Apply that identity contract consistently to ACL principals, scheduling and
artifact ownership. Keep responsibilities separate: the person obtaining approval,
the approver and the issue owner need not be the same person. A checker must not
silently make those roles identical when the supplied policy distinguishes them.
When a public record contract specifies required fields, compare those facts and
relationships rather than the reference's entire JSON object. Test permitted
additional commentary without weakening required coverage or duplicate checks.
Preserve exact comparisons for contractual byte identity and opaque IDs;
normalize only variations the public contract permits.

Required explanations need checks for their factual claims, or a qualified judge
when judgment is essential. Length, keywords and a matching decision ID do not
establish an explanation. Structured business facts can support deterministic
checks when they are a natural, disclosed output contract; record any narrowed
scope. Do not quietly drop explanation quality while claiming it was evaluated.

Separate learner outcomes from environment guarantees. If a mailbox offers no
send operation, prove that constraint in environment controls rather than counting
an always-passing "did not send" criterion as measured learner compliance. A broken
environment guarantee is infrastructure evidence, not a learner mistake.

For review tasks, define coverage and accuracy separately. Private known findings
are examples, not a hidden quota: accept equivalent wording and supported new
findings. Include correct findings, false positives, omissions and a valid
no-blocker review among the controls.

For code-changing tasks, use [the code-quality rubric](code-quality.md) to record
engineering findings separately from behavioral checks. Emit an explicit binary
`solved` for the **functional** verifier (alongside any fractional `reward`); the
benchmark report combines it with the versioned quality gate. A passing functional
suite alone cannot count as a benchmark pass. Run the quality controls before
qualifying a task; old results stay functional-only until compatibly regraded.

## 4. Document the task

Complete Harbor's README with the task's purpose, source provenance, environment,
verifier behavior and concrete run commands. Link the instruction and private
review record so a curator can inspect both. Keep it current after repairs.

Only runnable, qualified tasks enter `tasks/`. Drafts, control variants and
incomplete attempts remain in `.local/`, with durable decisions in `review/`.
