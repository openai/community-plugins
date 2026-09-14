#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Fixed phases dispatched by the root master; no arbitrary command or retry path.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
phase="${1:?master phase required}"
output="${2:?fresh repository-relative output required}"
case "$output" in validation/*) ;; *) echo 'Output must be below validation/' >&2; exit 1 ;; esac
case "/$output/" in */../*|*/./*) echo 'Noncanonical output refused' >&2; exit 1 ;; esac
image=maven:3.9.9-eclipse-temurin-17@sha256:f58d59b6273e785ac0a4477f6e9b5ba1d7731c75b906c0f7b34076f1851318cc
case "$phase" in
  native)
    exec bash scripts/openpnp-build-native-docker.sh --test --output "$output/ci-native"
    ;;
  packaged-mcp-controller-history)
    exec docker run --rm --init -v "$root:/workspace" -w /workspace \
      -v "${OPENPNP_TEST_CONTAINER_NODE_ROOT:?explicit Linux Node root required}:/opt/ci-node:ro" \
      -e "OPENPNP_QUALIFICATION_ROOT=/workspace/$output" "$image" \
      bash -c 'set -euo pipefail; export PATH="/opt/ci-node/bin:$PATH"; if ! command -v python3 >/dev/null; then apt-get update -qq; apt-get install -y -qq --no-install-recommends python3; fi; exec bash scripts/openpnp-ci-qualification.sh'
    ;;
  jvm-halts)
    exec docker run --rm --init -v "$root:/workspace" -w /workspace \
      -e "OPENPNP_QUALIFICATION_ROOT=/workspace/$output" "$image" \
      bash -c 'set -euo pipefail; if ! command -v python3 >/dev/null; then apt-get update -qq; apt-get install -y -qq --no-install-recommends python3; fi; exec python3 scripts/openpnp-test-native-crash.py --build "$OPENPNP_QUALIFICATION_ROOT/ci-native/build" --java-home "$JAVA_HOME" --output "$OPENPNP_QUALIFICATION_ROOT/ci-native-crash"'
    ;;
  *) echo 'Unknown master phase' >&2; exit 1 ;;
esac
