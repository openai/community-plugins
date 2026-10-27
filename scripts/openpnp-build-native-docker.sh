#!/usr/bin/env bash
set -euo pipefail
# Host source/runtime are read-only; generated outputs remain in this checkout.
root="$(cd "$(dirname "$0")/.." && pwd)"
upstream="${OPENPNP_SOURCE:?Set OPENPNP_SOURCE to the pinned OpenPnP source checkout}"
cache="${OPENPNP_MAVEN_CACHE:-$HOME/.cache/openpnp-codex/m2}"
mkdir -p "$cache"
platform="${OPENPNP_DOCKER_PLATFORM:-}"
if [[ -z "$platform" ]]; then
  case "$(uname -m)" in arm64|aarch64) platform=linux/arm64 ;; *) platform=linux/amd64 ;; esac
fi
mounts=(-v "$root:/workspace" -v "$upstream:/openpnp-source:ro" -v "$cache:/root/.m2")
envargs=(-e OPENPNP_SOURCE=/openpnp-source -e OPENPNP_MAVEN_CACHE=/root/.m2)
runtime="${OPENPNP_RUNTIME:-$upstream/target/codex-runtime}"
if [[ -f "$runtime/codex-build-manifest.json" ]]; then
  mounts+=(-v "$runtime:/openpnp-runtime:ro")
  envargs+=(-e OPENPNP_RUNTIME=/openpnp-runtime)
elif [[ -n "${OPENPNP_RUNTIME:-}" ]]; then
  echo "OPENPNP_RUNTIME requires a hash-inventoried stock runtime" >&2
  exit 1
fi
# Translate a host path inside the mounted checkout; relative paths are resolved
# by the canonical builder from /workspace.
container_path() {
  case "$1" in
    "$root"/*) printf '/workspace/%s' "${1#"$root"/}" ;;
    /*) echo "Output must be under the mounted checkout: $root" >&2; return 1 ;;
    *) printf '%s' "$1" ;;
  esac
}
args=()
while (($#)); do
  case "$1" in
    --output)
      [[ $# -ge 2 ]] || { echo "--output requires a path" >&2; exit 1; }
      args+=(--output "$(container_path "$2")"); shift 2 ;;
    --output=*) args+=(--output "$(container_path "${1#--output=}")"); shift ;;
    *) args+=("$1"); shift ;;
  esac
done
if [[ -n "${OPENPNP_BUILD_DIR:-}" ]]; then
  envargs+=(-e "OPENPNP_BUILD_DIR=$(container_path "$OPENPNP_BUILD_DIR")")
fi
if ((${#args[@]})); then set -- "${args[@]}"; else set --; fi
# Required build programs are installed only inside this disposable pinned Maven container.
exec docker run --rm --platform "$platform" "${mounts[@]}" "${envargs[@]}" -w /workspace \
  maven:3.9.9-eclipse-temurin-17@sha256:f58d59b6273e785ac0a4477f6e9b5ba1d7731c75b906c0f7b34076f1851318cc \
  bash -c 'set -euo pipefail; missing=(); for program in python3 patch git; do if ! command -v "$program" >/dev/null; then missing+=("$program"); fi; done; if ((${#missing[@]})); then apt-get update -qq; apt-get install -y -qq --no-install-recommends "${missing[@]}"; fi; git config --global --add safe.directory /openpnp-source; exec bash scripts/openpnp-build-native.sh "$@"' openpnp-native-build "$@"
