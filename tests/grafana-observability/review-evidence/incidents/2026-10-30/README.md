# Retained incident evaluations

These are exact outputs from real Codex CLI model sessions over frozen synthetic
evidence. They are not expected-answer fixtures or an installed-plugin model test.

`manifest.json` identifies the candidate and all 46 incident-scenario turns:

- Nine accepted outputs cover all required scenarios on the final candidate.
- Three other final-candidate drafts failed review for an unsupported request-rate
  regression label, unstated recovery criteria, or a pre-deployment timing claim.
  Their exact outputs and failing reviews remain; separate same-case repeats passed.
- One output on an earlier candidate failed exact inline citation fidelity.
  Its failed review remains under `rejected-final-deploy-reference`; a separate
  same-prompt repeat passed. Do not infer a reliability rate from these samples.
- Five earlier format-calibration outputs are excluded from acceptance.
- All nine outputs from before the retry-guidance correction are preserved. The
  partial-traces response incorrectly recommended replaying a saved report to
  obtain new evidence and has a failing semantic review. The other eight were
  not assigned acceptance grades after that failure was discovered.
- Nine previously accepted outputs remain bound to the candidate before final
  documentation and instruction changes.
- Ten outputs before the normalized-field clarification (nine cases and one
  repeat) retain their actual grades. Three failed because their next checks
  expected missing component/span fields from immutable event summaries. The
  contract now requires an appropriate separately reviewed source for those fields.

Each run retains its input, exact final output, sanitized execution trace,
consistency result and candidate/input/prompt/output/execution hashes. Accepted
and rejected runs include independent semantic reviews. Historical ungraded
records are explicitly marked. Earlier candidates are retained separately.
Prompts reconstruct from the versioned evaluator, candidate and input; no raw
client logs, reasoning, credentials or signing keys are included.
Current acceptance requires `finalOutputBinding: "MATCHED"`; original historical
receipts remain unchanged and do not claim that later event-stream check.

The default Grafana suite verifies these receipts without submitting model
turns. A consistency pass alone does not establish semantic correctness. See
the [evaluation guide](../../../../../docs/grafana-incident-evaluations.md) for
fresh runs and review requirements. This directory began with the October 30
qualification; each execution receipt retains its actual timestamp, including
the final October 31 Pacific review (November 1 UTC).
