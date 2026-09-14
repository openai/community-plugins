#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Run inside the workflow's existing pinned Linux JDK17 image. No retries.
set -euo pipefail
repository="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repository"
qualification="${OPENPNP_QUALIFICATION_ROOT:-$repository/validation}"
build="$qualification/ci-native/build"
stage="$qualification/ci-packaged"
node="$(command -v node)"
java="$JAVA_HOME/bin/java"
test -x "$node"
test -x "$java"
test -x "$JAVA_HOME/bin/javac"
test "$(node -p 'process.versions.node')" = 22.19.0
export PYTHONDONTWRITEBYTECODE=1

python3 scripts/openpnp-ci-qualification.py stage --build "$build" --output "$stage"
python3 "$stage/scripts/openpnp-build-controller-history.py" \
  --openpnp-home "$build/runtime" --java-home "$JAVA_HOME" \
  --output "$stage/validation/history-build"
cp -R "$stage/validation/history-build/package/." "$stage/plugins/openpnp/controller-history/"
npm --prefix "$stage/src/openpnp/node" ci --ignore-scripts --no-audit --no-fund
npm --prefix "$stage/src/openpnp/node" run build
node "$stage/scripts/verify-openpnp-package.mjs" > "$stage/validation/package-verification.json"

# Each canonical runner uses new state, records failure, and owns its child groups.
python3 "$stage/scripts/openpnp-test-native-mcp.py" \
  --runtime "$build/runtime" --output "$stage/validation/mcp" --node "$node" --java "$java" \
  --suite all --mapped-test-classes "$stage/validation/mapped-classes"
node "$stage/scripts/openpnp-test-controller.mjs" \
  --runtime "$build/runtime" --output "$stage/validation/controller" --java "$java"
python3 "$stage/scripts/openpnp-ci-qualification.py" history \
  --build "$build" --native-tests "$qualification/ci-native/native-tests" \
  --controller "$stage/validation/controller" --output "$stage/validation/history" \
  --node "$node" --java "$java"
