#!/usr/bin/env python3
"""Qualify, build, stage and scan an image under three pinned configurations.

Writes to the explicit staging repository before scanning. Never signs,
promotes or imports earlier reports as authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devsecops.factory.mission_security_operation import qualify_stage_and_assess  # noqa: E402


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'staging', 'security'):
        p.add_argument('--' + name + '-config', type=Path, required=True)
        p.add_argument('--' + name + '-config-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--runtime-root', type=Path, required=True)
    p.add_argument('--timeout', type=float, default=3000)
    p.add_argument('--expected-source-repository', type=Path)
    p.add_argument('--expected-source-revision')
    return p


def main(argv=None):
    try:
        report, _ = qualify_stage_and_assess(**vars(parser().parse_args(argv)))
        print(json.dumps(report, sort_keys=True))
        return {'pass': 0, 'fail': 1, 'unknown': 2}[report['decision']]
    except (ValueError, OSError, TypeError, KeyError, IndexError):
        print(json.dumps({'decision': 'unknown', 'factory_decision': 'unknown',
            'message': 'Assessment incomplete; retain staging mutation intents and inspect exact digests before another attempt'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
