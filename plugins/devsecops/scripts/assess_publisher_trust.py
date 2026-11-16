#!/usr/bin/env python3
"""Assess an explicit publisher key policy without issuing image/build authority."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devsecops.adapters.publisher_trust import assess_publisher_trust  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', dest='policy_path', type=Path, required=True)
    parser.add_argument('--policy-sha256', dest='expected_policy_sha256', required=True)
    parser.add_argument('--public-key', type=Path, required=True)
    parser.add_argument('--registry-origin', required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--package', dest='package_path', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--total-timeout', type=float, default=30)
    try:
        result = assess_publisher_trust(**vars(parser.parse_args(argv)))
        print(json.dumps({key: result[key] for key in ('decision', 'publisher_trust', 'build_authority',
            'execution_mode', 'policy_sha256', 'key', 'checks', 'source', 'gaps')}, sort_keys=True))
        return 0 if result['publisher_trust'] == 'pass' else 2
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        print(json.dumps({'publisher_trust': 'unknown', 'build_authority': 'not-issued',
                          'message': 'Publisher trust assessment could not complete'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
