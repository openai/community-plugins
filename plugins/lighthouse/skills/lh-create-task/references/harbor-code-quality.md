# Run code-quality review with Harbor RewardKit

Keep the ordinary Harbor task and its deterministic verifier unchanged. Use
`harbor trial regrade` on the saved submission with a same-name, quality-only
variant. Sol Medium grades first; Astra Medium independently reviews any valid Sol
failure against the same packet and all four criteria. The learner is not rerun.
The [benchmark report](../../lh-run-bench/references/metrics.md#complete-code-rubric)
requires both functional and quality passes. Harbor still owns execution.

Use [the quality rubric](code-quality.md) for the four dimensions and calibration.
This page covers execution, including two RewardKit 0.2.0 behaviors that matter
for missing evidence and timeouts.

## 1. Build a verifier-only variant with the original task name

Place the variant outside the benchmark's ordinary `tasks/` discovery root, for
example `review/quality-tasks/TASK/`. Keep the same `[task].name`, original
instruction, artifact declarations and a valid copy of the original environment
definition; replace only the verifier and assign a new verifier/task revision.
Harbor validates the agent environment definition at construction time even
though regrade does not start an agent container. Preserve the original source
trial and grading files.
The recorded source manifest must already contain every input the quality pass
needs, including changed tests; a missing input requires a new learner run or an
explicitly unmeasured result, not a reconstruction from the reference solution.

Use a separate verifier image built from the variant's `tests/` directory:

```text
tests/
  Dockerfile                      # Bake the verifier assets and RewardKit into the image
  test.sh                         # Lighthouse's verifier entrypoint
  publish_quality.py              # Validate native decisions and evidence paths
  quality_gate.py                 # Run the two-stage policy; write audit/binary results
  run_quality_judge.py            # Bounded official Responses transport for RewardKit
  tokenizer/                      # Bake the o200k_base cache here (or set TIKTOKEN_CACHE_DIR)
  build-review-packet.sh          # Task-owned: make the evidence file; do not execute the submission
  baseline/                       # Curator-owned source and context from the pinned starting revision
  rubric/
    quality.toml                  # One stage configuration, four binary criteria
    prompt.md                     # Evidence requirements and unmeasured marker
```

Copy the [entrypoint](../assets/code-quality/test.sh), [judge TOML](../assets/code-quality/quality.toml),
[judge prompt](../assets/code-quality/prompt.md), [result check](../scripts/publish_quality.py),
[gate](../scripts/quality_gate.py) and [transport](../scripts/run_quality_judge.py).
Make the two shell entrypoints executable in the image. Install and lock
`harbor-rewardkit==0.2.0` and its dependencies when building the verifier, rather
than installing at verification time. The exact Harbor `0.23.0` source declares
RewardKit `0.2.0`; the upstream skill's `0.1.*` example is stale at that same
revision. Do not upgrade or mix versions without repeating the controls below.
This template expects all four quality dimensions. If a task justifiably omits
one, keep it out of this policy or version its rubric and result check together,
then repeat the controls; do not remove dimensions after seeing a submission.

For an approved OpenAI API judge, a verifier configuration can use:

```toml
[verifier]
environment_mode = "separate"
timeout_sec = 900

[verifier.environment]
network_mode = "allowlist"
allowed_hosts = ["api.openai.com"]

[verifier.env]
LIGHTHOUSE_QUALITY_SOURCE_TRIAL_ID = "${LIGHTHOUSE_QUALITY_SOURCE_TRIAL_ID}"
OPENAI_API_KEY = "${OPENAI_API_KEY}"
```

The gate selects `openai/gpt-6-sol` then `openai/gpt-6-astra`, both with
reasoning `medium`. The pinned LiteLLM rejects this effort for models missing
from its metadata, so the small transport passes RewardKit’s unchanged messages
and JSON schema to the official OpenAI Responses SDK, with storage disabled,
42,000 estimated input tokens including a 1,000-token overhead allowance,
6,000 output tokens per request, no SDK retries and at most three native
RewardKit schema attempts per stage. Bake the `o200k_base` tiktoken data into
the image (run `python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"`
during build with `TIKTOKEN_CACHE_DIR=/tests/tokenizer` or the same readable
override used at runtime); the
restricted verifier should not download tokenizer data. A network override copied
from the original `[verifier]` can supersede the new
baseline; remove its `no-network` override in this variant. Leave the original
deterministic verifier offline. The quality verifier must only read submitted
bytes, never import or execute candidate code with judge credentials. Prefer the
plain LLM judge; an agent judge with tools is unnecessary for this rubric.
The entrypoint uses LiteLLM's packaged model-cost map so importing the judge does
not need a second host outside the declared provider allowlist.

## 2. Produce a complete, bounded evidence packet

The task-owned builder receives the restored submission root and output path. It
must write `/review/packet.md` with:

- The original learner request and the relevant learner-visible repository rules.
- A complete changed-path list and a baseline-to-submission diff, with paths and
  line numbers, for source and tests in the task's reviewable scope.
- Enough surrounding baseline/submission code and existing tests to judge the
  four dimensions, including when the patch adds no tests.
- Artifact/baseline identities and an explicit account of excluded generated or
  vendored files. Unexpected changed paths, missing baseline/input, unsupported
  content or size overflow must stop the pass unless the reviewer can still see
  the complete gradeable change under the task's frozen scope. Never silently
  truncate or follow submitted symlinks into credentials or the verifier.

The same builder must write `/review/evidence-paths.txt`: one exact
repository-relative source, test or convention path per line for every file
presented as evidence. The publisher checks every cited path on a failed
criterion against this trusted list, rejects shortened package-relative paths
and reversed ranges, and withholds the aggregate if any citation cannot be
resolved. Retain the manifest alongside the native diagnostics. This confirms
the path and range syntax; the reviewer must still confirm the cited lines
support the claimed consequence.

Compare the packet with an actual native regrade before calling the judge. A
Harbor replay can materialize baseline symlinks as ordinary files. A task can
treat that representation change as unchanged only when the submitted bytes match
the pinned baseline target exactly and file content is all the task needs to
grade. If link type itself matters, or the restored bytes differ, keep it visible
or mark the case unmeasured. Retain this check in the packet manifest.

The packet must be generated in the trusted verifier, not supplied by the learner.
Do not attach the gold solution as RewardKit's `reference`: valid designs remain
open. RewardKit `0.2.0` only visits the immediate files of a supplied directory;
a missing path is passed to the model as `[not found]`. Passing `/app` or a glob
does not capture a nested repository. Use the explicit packet path and fail
before calling the model when the builder cannot make a complete packet.

## 3. Regrade and read the native files

From the benchmark repository, using its locked Harbor environment:

```bash
uv run --locked harbor trial regrade jobs/RUN/TRIAL \
  -p review/quality-tasks/TASK -e docker -o regrades/code-quality \
  --ve LIGHTHOUSE_QUALITY_SOURCE_TRIAL_ID=ORIGINAL_TRIAL_UUID
```

Read the original UUID from `jobs/RUN/TRIAL/result.json` → `id`. Invoke the
trial command with the distinct original UUID for each attempt; do not use a
job-wide static UUID. Harbor independently records it at the regrade’s
`result.json` → `config.source_trial.trial_id`; the report validates both IDs.
Only exact-source regrades belong in the quality index; copied learner usage is
never a new learner attempt.

Inspect the returned regrade directory:

| File | Meaning |
| --- | --- |
| `result.json` | Native regrade result and independently recorded original trial UUID. |
| `verifier/quality-review.json` | Policy, original trial UUID, packet SHA-256, final dimension states, both judges' reasons, and adjudication if any; persists when no reward is available. |
| `verifier/packet.md` and `verifier/evidence-paths.txt` | Exact trusted review inputs for auditing and later adjudication. |
| `verifier/reward.json` | Binary quality gate only: `1` for all four passing or `0` for a confirmed failure. Absent for unresolved/unmeasured results. The original remains the functional score. |
| `verifier/{sol,astra}/reward-details.json` | Original RewardKit decisions or native errors; the Astra directory exists only for escalated submissions. |
| `verifier/{sol,astra}/usage.jsonl` | Provider call status and reported tokens, separate from learner usage; not billed spend. |
| `verifier/test-stdout.txt` | Native combined stdout/stderr; retain it for packet or setup errors. |

RewardKit's timeout can produce numeric zeros with `raw: null` and `error`; the
validator treats those as unmeasured. Missing evidence, explicit `UNMEASURED:`,
invalid citations or failed provider calls are also unmeasured, never poor code.
A disagreement writes audit evidence but withholds Harbor's binary quality reward.
Consequently Harbor may show a missing-reward exception for such a regrade; the
report reads the preserved audit status and does not mistake it for a failure.
A confirmed failure can still block a pass if another dimension is unresolved;
its diagnostic `Q` then remains unavailable.

For a disagreement, preserve the native regrade directory. Copy its `verifier/`
files to a new versioned adjudication directory, including the retained
`packet.md` and path manifest (its SHA-256 must match). Write a human JSON file, for example:

```json
{"reviewer":"Maintainer name","decisions":{"tests":{"decision":"pass","reason":"src/cache.py:42-49 the existing guard covers the flagged branch"}}}
```

Then, from the benchmark (with the copied helpers):

```bash
python scripts/quality_gate.py finalize --source-trial-id ORIGINAL_TRIAL_UUID \
  --packet path/to/exact-packet.md --evidence path/to/evidence-paths.txt \
  --output path/to/copied-verifier --adjudication path/to/human.json
```

The helper only accepts cited rulings on disputed dimensions. Add the new
`quality-review.json` to the [quality index](../../lh-run-bench/references/metrics.md#complete-code-rubric);
it verifies the packet and original judge evidence have not changed. If the
provider or packet was unavailable, perform a new native regrade instead.

Run controls for clean, confirmed-failure, disagreement, new Astra finding,
missing evidence, and provider timeout before qualifying a task. Keep judge usage
separate; continue [human calibration](code-quality.md#qualify-quality-review-on-patches-that-pass)
and disclose its coverage. Do not overwrite original functional results or prior
quality attempts.

Upstream sources at the pinned Harbor commit: [RewardKit skill](../../../vendor/harbor/skills/rewardkit/SKILL.md),
[package version](https://github.com/harbor-framework/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/packages/rewardkit/pyproject.toml),
[judge implementation](https://github.com/harbor-framework/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/packages/rewardkit/src/rewardkit/judges.py),
[output implementation](https://github.com/harbor-framework/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/packages/rewardkit/src/rewardkit/runner.py),
and [native regrading](https://github.com/harbor-framework/harbor/blob/1e5c5c6db929a10a140d05e606882c671ae20729/docs-mintlify/core-concepts/jobs/regrade.mdx).
