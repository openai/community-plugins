#!/bin/sh
set -eu

: "${LIGHTHOUSE_QUALITY_SOURCE_TRIAL_ID:?Set the original Harbor trial UUID}"
export LITELLM_LOCAL_MODEL_COST_MAP=True
export TIKTOKEN_CACHE_DIR="${TIKTOKEN_CACHE_DIR:-/tests/tokenizer}"
mkdir -p /review /logs/verifier
/tests/build-review-packet.sh /app /review/packet.md
test -s /review/packet.md
test -s /review/evidence-paths.txt
cp /review/evidence-paths.txt /logs/verifier/evidence-paths.txt
cp /review/packet.md /logs/verifier/packet.md
python /tests/quality_gate.py run --source-trial-id "$LIGHTHOUSE_QUALITY_SOURCE_TRIAL_ID" \
    --packet /review/packet.md --evidence /review/evidence-paths.txt \
    --rubric /tests/rubric --workspace /app --output /logs/verifier
