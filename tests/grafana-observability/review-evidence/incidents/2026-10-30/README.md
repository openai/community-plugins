# Retained incident evaluations

These are exact outputs from real Codex CLI model sessions over frozen synthetic
evidence. They are not expected-answer fixtures or an installed-plugin model test.

`manifest.json` identifies the candidate and all 24 incident-scenario turns:

- Nine accepted outputs cover all required scenarios on the corrected candidate.
- One additional output on that candidate failed exact inline citation fidelity.
  Its failed review remains under `rejected-final-deploy-reference`; a separate
  same-prompt repeat passed. Do not infer a reliability rate from these samples.
- Five earlier format-calibration outputs are excluded from acceptance.
- All nine outputs from before the retry-guidance correction are preserved. The
  partial-traces response incorrectly recommended replaying a saved report to
  obtain new evidence and has a failing semantic review. The other eight were
  not assigned acceptance grades after that failure was discovered.

Each run retains its input, exact final output, sanitized execution trace,
consistency result and candidate/input/prompt/output/execution hashes. Accepted
and rejected runs include independent semantic reviews. Historical ungraded
records are explicitly marked. Earlier candidates are retained separately.
Prompts reconstruct from the versioned evaluator, candidate and input; no raw
client logs, reasoning, credentials or signing keys are included.

The default Grafana suite verifies these receipts without submitting model
turns. A consistency pass alone does not establish semantic correctness. See
the [evaluation guide](../../../../../docs/grafana-incident-evaluations.md) for
fresh runs and review requirements. The qualification date is Pacific local
time; some recorded UTC timestamps fall on October 31.
