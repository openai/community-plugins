#!/usr/bin/env python3
"""Build, assess, sign and publish immutable evidence with five pinned configs.

Writes image and signed evidence to the exact staging repository, then verifies
registry readback. Does not promote images or establish CI execution provenance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devsecops.factory.mission_publication_operation import qualify_sign_and_publish  # noqa: E402


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'staging', 'security', 'signing', 'publication'):
        p.add_argument('--' + name + '-config', type=Path, required=True)
        p.add_argument('--' + name + '-config-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--runtime-root', type=Path, required=True)
    p.add_argument('--timeout', type=float, default=3500)
    p.add_argument('--expected-source-repository', type=Path)
    p.add_argument('--expected-source-revision')
    return p


def main(argv=None):
    try:
        report, _ = qualify_sign_and_publish(**vars(parser().parse_args(argv)))
        print(json.dumps(report, sort_keys=True))
        return {'pass': 0, 'fail': 1, 'unknown': 2}[report['decision']]
    except (ValueError, OSError, TypeError, KeyError, IndexError, AttributeError):
        print(json.dumps({'decision': 'unknown', 'factory_decision': 'unknown',
            'message': 'Evidence publication incomplete; retain mutation intents and inspect exact image and evidence digests before another attempt'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
