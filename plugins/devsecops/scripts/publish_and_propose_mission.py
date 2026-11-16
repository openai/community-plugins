#!/usr/bin/env python3
"""Build and publish signed evidence, then prepare a local deployment proposal.

Writes image/evidence only to the explicitly configured staging registry. Uses
six pinned configurations in one process. Does not apply a patch, push Git,
create a merge request, promote an image or deploy a workload.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devsecops.factory.mission_proposal_operation import qualify_publish_and_propose  # noqa: E402


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'staging', 'security', 'signing', 'publication', 'proposal'):
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
        report, _ = qualify_publish_and_propose(**vars(parser().parse_args(argv)))
        print(json.dumps(report, sort_keys=True))
        return {'pass': 0, 'fail': 1, 'unknown': 2}[report['decision']]
    except (ValueError, OSError, TypeError, KeyError, IndexError, AttributeError):
        print(json.dumps({'decision': 'unknown', 'factory_decision': 'unknown',
            'message': 'Publication/proposal incomplete; retain upstream mutation intents and inspect evidence before another attempt'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
