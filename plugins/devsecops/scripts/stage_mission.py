#!/usr/bin/env python3
"""Qualify, build and upload to an explicit staging registry in one process.

This command writes exact image blobs and its immutable manifest to the pinned
staging destination. It never imports build reports or signs/promotes an image.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devsecops.factory.mission_delivery import qualify_build_and_stage  # noqa: E402


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-config', type=Path, required=True)
    p.add_argument('--build-config-sha256', required=True)
    p.add_argument('--staging-config', type=Path, required=True)
    p.add_argument('--staging-config-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--runtime-root', type=Path, required=True)
    p.add_argument('--timeout', type=float, default=2400)
    p.add_argument('--expected-source-repository', type=Path)
    p.add_argument('--expected-source-revision')
    return p


def main(argv=None):
    try:
        report, _ = qualify_build_and_stage(**vars(parser().parse_args(argv)))
        print(json.dumps(report, sort_keys=True))
        return 0 if report['decision'] == 'pass' else 2
    except (ValueError, OSError, TypeError, KeyError, IndexError):
        print(json.dumps({'decision': 'unknown', 'factory_decision': 'unknown',
            'message': 'Delivery incomplete; retain any staging mutation intents and inspect exact digests before another attempt'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
