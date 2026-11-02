#!/usr/bin/env bash
set -euo pipefail
# Canonical build entry point. Input source/runtime and packaged artifacts are read-only.
root="$(cd "$(dirname "$0")/.." && pwd)"
exec "${PYTHON:-python3}" "$root/scripts/openpnp-build-native.py" "$@"
