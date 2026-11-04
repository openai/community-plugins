# Leaderboard metrics

The renderer supports one native Harbor job containing single-step Codex trials
without retries. Harbor can delete failed-attempt evidence on retry; jobs with
recorded retries are rejected so retained usage is not presented as total usage.
Run it separately for each model, benchmark version or comparison arm. It reads
saved files only; it does not run Harbor, judge correctness or admit tasks.

## Sources and totals

| Measurement | Native source | Meaning |
| --- | --- | --- |
| Reward and criteria | Trial `verifier_result.rewards` | `reward` is the task's normalized aggregate; other keys retain criterion details |
| Input tokens | Trajectory `final_metrics.total_prompt_tokens` | All input, including cache hits |
| Cached input | Trajectory `final_metrics.total_cached_tokens` | Subset of input, not additional usage |
| Output tokens | Trajectory `final_metrics.total_completion_tokens` | Recorded output tokens |
| Cost in USD | Trajectory `final_metrics.total_cost_usd` | Harbor-reported or estimated cost |
| Learner seconds | Trial `agent_execution` timestamps | Wall time within the learner phase |
| Trial seconds | Trial start/finish timestamps | Environment, setup, learner and verification wall time |

The pinned Codex adapter calculates cost from model pricing when its logs lack a
reported cost. Harbor does not always distinguish those sources in final metrics;
the table therefore says **Harbor reported/estimated**, never billed spend. Its
rate provenance remains unverified until you retain the resolved model rate entry,
source and date used for this run, or a provider cost receipt. LiteLLM can load a
changing pricing map: its package version alone does not pin the rates. A retained
entry from a pinned bundled map is also sufficient. If you cannot recover this
evidence, cost acceptance remains incomplete. Do not add a pricing service.

Use trajectory fields for tokens: the adapter can replace missing token values
with zero in `agent_result`. A missing trajectory value stays unknown, including
a cached-token count that the adapter omitted because it was zero.

First confirm the trajectory belongs to the learner. Harbor 0.23.0 chooses the
lexicographically last session file; an automatic approval review can create a
second session that is mistakenly selected. The renderer rejects a known model
mismatch. Also match the trajectory session ID to Codex's `thread.started` event
and compare usage with `turn.completed`. Preserve incorrect native output; record
corrections separately from the matching raw learner session. Approval reviewer
usage is separate overhead, not learner tokens or evidence of a different learner
model. A matching model name alone cannot distinguish two sessions of that model.

The table preserves each repetition and error. Mean reward uses only completed,
error-free graded trials and shows its denominator. It is not a pass rate; inspect
required criteria separately. Resource totals include all attempted trials. If
any required value is missing, the total is unknown with a labelled known subtotal.
Summed trial durations are not concurrent job wall time.

The table is evidence for review, not an acceptance gate. Confirm artifact
completeness, task qualification, run identities and all required checks separately.
Do not sum a regrade and its source as two learner attempts.

## Complete code rubric

Show three metrics together: deterministic **functional pass**, **quality pass**,
and headline **benchmark pass**. A benchmark pass requires both; functional credit
cannot compensate for poor code. Publish `verified passes / all functionally
measured submissions`, with confirmed failures, pending adjudication, missing
quality and unavailable functional attempts visible. Give quality's own measured
denominator and coverage. `F` (native fractional reward) and `Q` (fraction of
four final quality dimensions passed, when all are known) are diagnostics.

The leaderboard defaults to the original verifier's named binary `solved` as
`functional_solved`. For a historical native task whose `reward` was authored as
binary, specify `--functional-key reward`; the helper rejects fractional values.
No quality evidence means no benchmark pass. Old functional-only scores retain
that meaning; only compatible saved submissions can be regraded. Do not send a
multi-key reward to Harbor's native `pass@k`, which expects exactly one binary
reward. If a later workflow uses native `pass@k`, materialize only definite joint
binary decisions in a separate comparable input and disclose excluded cases and
its changed denominator; the current report's conservative headline includes
pending quality in the functional denominator.

For a job made entirely of non-code tasks, select `--functional-only` to label
the report accordingly and omit the combined code pass rate. Report non-code
and code jobs separately; do not use that option to waive code quality.

Pass `--quality-index experiments/RUN/quality-index.json`. Paths are relative to
the index; use each original native trial UUID as the key:

```json
{
  "d24a952d-fd96-4c16-933b-6b79d1270b40": {
    "regrade": "../../regrades/code-quality/TRIAL",
    "adjudicated": "../../review/adjudications/TRIAL/verifier/quality-review.json"
  }
}
```

Omit `adjudicated` unless a named human resolved one or more disputes. The helper
checks Harbor's `config.source_trial.trial_id` and the quality record both match
the original trial in this learner job. An adjudication must preserve the same
packet hash and original model judgments. Per-trial links point to that evidence.
Judge usage comes from its verifier artifacts, never from the learner trajectory.

## Estimate an unknown cost

When native cost is unavailable, use documented rates for the exact recorded
model identifier, including `provider/` when supplied to Harbor. The renderer
reconstructs it from `agent_info.model_info`. Supply
`--rates experiments/<run>/rates.json` with this shape:

```json
{
  "model": "EXACT_RECORDED_MODEL",
  "currency": "USD",
  "date": "YYYY-MM-DD",
  "source": "URL_OR_CONTRACT_REFERENCE",
  "input_per_million": 0.0,
  "cached_input_per_million": 0.0,
  "output_per_million": 0.0
}
```

Replace the placeholders and example zero rates with verified prices before use.
The calculation is `(input - cached) × input_rate + cached × cached_rate +
output × output_rate`, divided by one million. Missing usage stays unknown.
The small helper does not estimate multi-model or cache-write pricing. Retain
that limitation rather than silently charging all tokens at one model's rate.

A cost estimate is not billed spend. Authoring, scouting, review, reference
solutions and qualification are separate overhead; record their usage where
available and disclose gaps. The learner leaderboard is not the total cost of
creating a benchmark.
