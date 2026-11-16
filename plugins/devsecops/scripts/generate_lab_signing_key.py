#!/usr/bin/env python3
"""Create private local application signing credentials; does not approve trust.

Creates a new directory containing an encrypted key, random password, public key
and retrieval metadata. Never generates or claims a Platform One publisher key.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devsecops.adapters.local_signing import generate_lab_key  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cosign', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=30)
    try:
        report = generate_lab_key(**vars(parser.parse_args(argv)))
        print(json.dumps(report, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, KeyError, IndexError):
        print(json.dumps({'decision': 'unknown', 'scope': 'local-credential-generation-only',
            'message': 'Local credential setup incomplete; inspect the selected private directory without exposing its contents'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
