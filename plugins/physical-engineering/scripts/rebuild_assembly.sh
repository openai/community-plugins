#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then
  echo "Usage: sh rebuild.sh NEW_DIRECTORY" >&2
  exit 2
fi
bundle=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$bundle/source/scripts/engineering.py" assembly-build "$bundle/source/design" "$1" --baseline "$bundle"
