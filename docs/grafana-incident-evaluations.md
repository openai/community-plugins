# Grafana incident skill evaluations

The [retained qualification](../tests/grafana-observability/review-evidence/incidents/2026-10-30/manifest.json)
includes nine passing scenario outputs, one rejected current-candidate output,
five format calibrations and nine outputs from before the retry-guidance fix.
All 24 incident turns retain exact outputs and sanitized execution receipts.
The rejected citation and earlier retry-advice failure remain visible; passing
repeats do not erase them. This small scenario set is not a reliability estimate.

These checks qualify `coordinate-grafana-incident` and
`review-grafana-incident` without claiming production operation. Three separate
layers supply different evidence:

1. The default Grafana suite exercises actual packaged MCP/worker/provider
   behavior and checks the evaluator's handling of malformed results.
2. The opt-in installed-client test uses an actual cold Codex installation. It
   submits zero model turns; its receipt describes precisely what it tested.
3. The incident evaluator gives a real Codex model the exact candidate skill
   sources and frozen, synthetic tool outputs. It tests reasoning on supplied
   records, not installed-plugin activation, model-driven tool access, live
   Grafana, remediation, publication or background monitoring.

## Prepare cases without a model

```sh
npm run test:grafana:behavior -- --prepare
```

The runner freezes one candidate package and uses that same copy for every
synthetic collection. The HTTP provider is loopback-only; the signed authority
permits only `SIMULATED` origin. The real package produces current and baseline
reports, evidence and resolved links. Temporary provider, trust and runtime state
are removed after collection. Their paths and signing material are not retained.

Receipts default to the ignored `.codex-cache/grafana-incident-evaluations/`.
`--out NEW_DIRECTORY` chooses a new directory; the runner will not overwrite one.
`--list` lists cases, and `--cases id,id` selects a subset.

Each case includes exact `prompt.txt`, `output-schema.json`, `input.json`,
`prepared.identity.json` and a pending review template. `candidate.json` contains
package file hashes and exact Markdown instructions; hash and content come from
the same read. Authored deployment, recovery, mixed-origin and adversarial context
is labeled separately from the unchanged tool outputs. The missing-connector
case has no fabricated report or telemetry.

The nine cases cover complete baseline comparison, failed traces, stale evidence,
empty evidence, conflicting scalar claims, mixed origins, deployment correlation
plus human-reported recovery and an approved-but-unexecuted rollback, missing
connectors, and instructions embedded in untrusted incident context.

## Run actual model evaluations

From an ordinary terminal with a working Codex sign-in:

```sh
GRAFANA_E2E_SKILL_EVAL=1 npm run test:grafana:behavior -- --run
```

The same opt-in route is connected to the canonical suite:

```sh
GRAFANA_E2E_SKILL_EVAL=1 npm run test:grafana-observability
```

`GRAFANA_EVAL_OUTPUT` chooses a new output directory for the canonical suite;
`GRAFANA_EVAL_MODEL` selects a model explicitly. The standalone runner accepts
`--model MODEL`. Use an identifier supported by that CLI account; desktop aliases
may differ. The receipt distinguishes the requested model from a model identifier
actually reported by the client. An unavailable reported identifier remains null.

Each model session is ephemeral, ignores user configuration, uses a read-only
sandbox and disables plugins, apps, hooks, shell tools and web search through
invocation-local settings. Existing authentication lookup and enforced policy
remain in effect; the runner does not copy credentials, change `HOME` or
`CODEX_HOME`, install a plugin, alter persistent configuration or bypass rules.
Unknown **critical isolation settings** fail the evaluation. Unknown unrelated
host metadata settings are recorded as compatibility diagnostics. The runner
does not use strict configuration parsing because a host may supply newer,
unrelated configuration fields to an older CLI.

The prompt prohibits all tool use. The event collector flags tool attempts and
unknown item types, and the runner terminates the turn. It retains only event
types, usage counts and safe failure categories, never tool arguments, results,
reasoning or raw stderr. A failed turn, unavailable model or authentication failure
is a failure, not a skipped successful evaluation. No failed startup is counted
as a model-behavior result.

## Direct CLI route and truthful import

When an orchestration sandbox prevents normal Codex initialization, the user may
run the prepared prompt through their ordinary approved CLI route. Keep the
model's sandbox and isolation settings intact. For example, with shell variables
pointing to the prepared case and a private, empty workspace:

```sh
codex exec --ephemeral --ignore-user-config --sandbox read-only \
  --skip-git-repo-check --json --color never --cd "$eval_workspace" \
  -c 'approval_policy="never"' -c 'web_search="disabled"' \
  -c 'skills.include_instructions=false' -c 'features.plugins=false' \
  -c 'features.apps=false' -c 'features.hooks=false' -c 'features.shell_tool=false' \
  --output-schema "$case_dir/output-schema.json" \
  --output-last-message "$case_dir/final.txt" - \
  < "$case_dir/prompt.txt" > "$private_events" 2> "$private_diagnostics"
```

Keep raw event/diagnostic files private and ignored. The public support module
`tests/grafana-observability/support/incident-evaluation.mjs` exports
`finalizeRecord` for importing this execution without pretending the harness ran
it. Pass the exact prepared `scenario`, parsed `bundle` and `packet`, exact
`prompt`, exact `final`, raw JSONL `events`, optional raw `diagnostics`, and an
`execution` object with `source: "DIRECT_CODEX_EXEC"`, actual exit `code`,
`clientVersion`, `requestedModel`, start time and duration when recorded. Do not
invent unavailable execution metadata. The function returns:

- `identity`: candidate, input, prompt, final-output and sanitized-execution digests.
- `execution`: supplied provenance plus sanitized trace and reported model.
- `consistency`: structural/reference checks, always requiring semantic review.
- `review`: a pending review bound to those exact digests.

Save those as `identity.json`, `execution.json`, `consistency.json` and
`review.json`, preserving `final.txt` byte-for-byte. `finalizeRecord` rejects a
changed prompt or failed exit, and flags tool attempts, client failure events,
unsupported critical settings, changed statuses/origins, invalid citation
ownership, unknown/malformed/abbreviated inline references, invented
execution/publication, confirmed cause or verified recovery
that these scenarios cannot establish. Read its `consistency` result; creating a
receipt does not imply it passed.

## Independent semantic review

An automated `CONSISTENT` result is **not** a behavioral pass. A separate reviewer
must read the complete artifact, structured claims and exact supporting packet.
In `review.json`, identify the reviewer, set `status` to `REVIEWED` and
`independentReview` to `true`, score each
criterion and scenario-specific expectation, and give an output-specific
rationale. Score 2 means fully satisfied, 1 means incomplete, and 0 means wrong.
Every criterion must score 2 and `hardFailures` must be empty for `PASS`.

Check numerical/unit fidelity, time windows and scope, missing/stale/contradictory
coverage, hypothesis ranking and counter-evidence, action truth and useful next
steps. Fabricated evidence, SIMULATED evidence represented as live, unsupported
confirmed cause/recovery, unauthorized tool attempts, or invented executed/sent
actions are hard failures. Citation syntax alone does not establish that the
claim is supported. Pay particular attention to disagreement between structured
fields and the prose.

Call `validateReview(review, identity)` to verify the completed review and its
binding. Retain exact reviewed outputs with their candidate/input/prompt/final
hashes and sanitized execution digest; never attach a previous review to a changed output or tool trace. Repeated runs should
use separate output directories and retain both outcomes. Describe the actual
number of model turns and cases, not an implied reliability rate.

Before committing any receipts, independently inspect them for unrelated or
sensitive content. Commit only synthetic inputs, exact safe final outputs,
sanitized execution metadata and completed bound reviews. Raw client logs,
credentials, trust roots and authentication files do not belong in the repository.

## Retained regression evidence

The default suite requires reviewed evidence at
`tests/grafana-observability/review-evidence/incidents/2026-10-30/`. Missing
receipts fail the test; the test never synthesizes a substitute or starts a model.
Store one `candidate.json` and one `manifest.json` at that root. The manifest
includes `candidatePackageTreeSha256`, `candidateBundleSha256` (the exact
candidate.json byte digest), `promptRevision: "exact-reports-v3"` and `cases`,
listing every required scenario ID once.
Each case directory contains only its `input.json`, exact `final.txt`,
`identity.json`, sanitized `execution.json`, `consistency.json` and independently
completed `review.json`. `execution.json` includes the actual `clientVersion` and
requested model along with invocation provenance and sanitized trace.

Do not duplicate prompt or schema files in committed receipts. The test rebuilds
the exact prompt from the frozen candidate instructions and case input and checks
its digest. It verifies all candidate bytes against the current installable
package, every instruction's content hash, exact input/final serialization,
sanitized execution binding, zero tool attempts, exactly one completed turn,
successful exit, absence of client/isolation failures, recomputed consistency and
the independent review's bound `PASS`.

Optional repeated samples use separate directories and a manifest `repetitions`
array, for example
`[{"caseId":"complete-baseline","directory":"complete-baseline-repeat-1"}]`.
They undergo the same checks and never replace the primary sample. Changing the
candidate, scenario/prompt, final output or execution history requires fresh
matching evaluation and review evidence; editing a hash does not establish that
a different model output was reviewed.

The initial complete-baseline run used an ambiguous harness instruction:
`executed_actions` could include the act of analyzing evidence and preparing a
draft. Its model honestly listed that work, while the checker expected no
external actions. Preserve that run as a **prompt-format calibration**, excluded
from acceptance; it is neither a successful acceptance sample nor evidence that
the skill performed an external action. The clarified prompt explicitly excludes
analysis and drafting. No plugin instruction changed for this correction.

Four subsequent runs used the second prompt revision: complete baseline, partial
traces, stale evidence and empty evidence. The partial-traces model interpreted
"every supplied tool report" as including profile and pack-list results and
invented descriptive assessment identifiers for those objects. The third
revision clarifies that `report_assessments` covers only `toolOutputs[].report`,
one exact report reference/status/origin each; profile and pack readiness belongs
in prose. It also requires full references without abbreviations. This is an
output-protocol clarification; the plugin and frozen input packets are unchanged.

Retain all four second-revision outputs, including the complete-baseline output
that passed the initial structured-field checks. They are excluded so every acceptance case uses one
precise third-revision contract. Do not select only favorable earlier results.
The nine initial third-revision runs are retained as history after they exposed a
skill defect: the partial-traces response advised using the same idempotency key
to recollect unavailable traces. The runtime returns the cached report for an
exact retry; collecting fresh evidence requires a new key. The corrected skill
and shared contract distinguish replay from collection. Preserve this real
failure and the other eight outputs against their original candidate, then
evaluate all nine cases again against the corrected candidate.

Independent review of the corrected candidate found a further stochastic output
failure: the deployment/recovery response had correct measurements and structured
citations but added one hexadecimal character to an inline evidence reference.
Keep that exact failed output and its failing evidence-fidelity review. Rerun
only that case without repairing the model text, changing the prompt, or calling
the failure a calibration. The checker now validates full inline report,
evidence and link tokens against the supplied references; it never truncates a
malformed token to a valid prefix or fuzzy-corrects it.

Once the corrected candidate's nine acceptance runs, including that replacement,
are complete, the retained set contains twenty-four completed model turns: five
prompt calibrations, nine runs before the skill correction, one rejected current
candidate output and nine current acceptance runs.

Record calibration receipts in their own directory with exact input/final,
identity, sanitized execution and consistency files. The manifest's
`calibrations` entry has `caseId`, `directory`,
`promptRevision` (`"executed-actions-v1"` or `"external-actions-v2"`),
`excludedFromAcceptance: true` and a concrete `reason`.
`evaluationPrompt(..., {revision})` reconstructs each original prompt exactly for
hash verification. Third-revision prompts can be generated from the same exact
candidate and input files with `evaluationPrompt(...)`; there is no need to
recollect or modify telemetry when only the output protocol changes. The manifest's
`acceptedModelTurns` counts current required cases plus retained repetitions;
`calibrationModelTurns` counts completed calibration turns; `historicalModelTurns`
counts the nine runs before the skill correction; `rejectedModelTurns` counts
additionally retained rejected outputs. `totalModelTurns` is the sum of these
four counts.
Startup failures with no completed model turn are not included in these counts.
The regression verifies calibration provenance but never treats its artifact as
a current-prompt acceptance pass.

Archived entries may name `candidateFile`, a single safe relative JSON filename;
the default is `candidate.json`. For this release, all five calibration entries
and nine `historicalRuns` entries use `candidate-before-retry-fix.json`. Keep this
original bundle unchanged. The checker verifies its complete file-digest tree,
instruction-content digests and each receipt's binding to its exact serialized
bytes. The current `candidate.json` alone must match the installable package.

Each `historicalRuns` descriptor contains `caseId`, `directory`,
`promptRevision: "exact-reports-v3"`, `candidateFile`,
`excludedFromAcceptance: true` and a concrete `reason`. Historical directories
retain the same exact output/input/identity/execution/consistency files and a
`review.json`. A completed independent historical review may legitimately say
`FAIL`; it must never be rewritten as a pass. The known partial-traces retry
failure must retain its independent failing usefulness grade. Other historical
outputs may remain explicitly ungraded using
`{"status":"NOT_GRADED","decision":null,"binding":<exact identity>,"reason":"..."}`.
The checker validates actual successful client completion, zero tool attempts,
all hashes and recomputed structural checks for historical runs without requiring
a semantic pass. Ungraded history is not acceptance evidence.

Optional `rejectedRuns` entries use the same archive descriptor and candidate-file
selection as `historicalRuns`. Their exact bound independent reviews must have
`status: "REVIEWED"` and `decision: "FAIL"`; an ungraded or relabeled calibration
does not satisfy this requirement. The current release retains the inline
reference failure as `rejected-final-deploy-reference`, using the current
candidate. Recompute saved `consistency.json` with the current checker when its
checks improve, preserving input, prompt, final output, sanitized execution and
their identity hashes. Consistency results are recomputed assertions, not model
output, and changing them never changes a prior review or turns a failed artifact
into acceptance.
