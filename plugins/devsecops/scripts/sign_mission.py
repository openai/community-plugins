#!/usr/bin/env python3
"""Qualify, build, stage, assess and locally sign using four pinned configurations.

Writes image content to the explicit staging repository. Signatures remain local;
this command does not publish signatures, promote images or import prior reports.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devsecops.factory.mission_signing_operation import qualify_assess_and_sign  # noqa: E402


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'staging', 'security', 'signing'):
        p.add_argument('--' + name + '-config', type=Path, required=True)
        p.add_argument('--' + name + '-config-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--runtime-root', type=Path, required=True)
    p.add_argument('--timeout', type=float, default=3300)
    p.add_argument('--expected-source-repository', type=Path)
    p.add_argument('--expected-source-revision')
    return p


def main(argv=None):
    try:
        report, _ = qualify_assess_and_sign(**vars(parser().parse_args(argv)))
        print(json.dumps(report, sort_keys=True))
        return {'pass': 0, 'fail': 1, 'unknown': 2}[report['decision']]
    except (ValueError, OSError, TypeError, KeyError, IndexError):
        print(json.dumps({'decision': 'unknown', 'factory_decision': 'unknown',
            'message': 'Local signing incomplete; retain staging mutation intents and inspect exact digests before another attempt'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
