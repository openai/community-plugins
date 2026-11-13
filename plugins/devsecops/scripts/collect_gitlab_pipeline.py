#!/usr/bin/env python3
"""Read one exact GitLab pipeline and explicitly selected job artifacts.

Uses a named read-token environment variable and verified HTTPS. Does not trigger
jobs, follow latest refs, write remote state or issue factory origin authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devsecops.adapters.gitlab_pipeline import _requests, collect_gitlab_pipeline  # noqa: E402


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default includes invalid input values; keep them out of
        # normalized output even if someone accidentally supplied a credential.
        self.print_usage(sys.stderr)
        self.exit(2, 'Invalid collection arguments; use --help for explicit IDs, paths and credential references.\n')


def _identifier(value):
    if not re.fullmatch(r'[0-9]{1,19}', value) or not 1 <= int(value) < 2**63:
        raise argparse.ArgumentTypeError('A bounded positive numeric identifier is required')
    return int(value)


def _artifact(value):
    if len(value) > 1044 or ':' not in value:
        raise argparse.ArgumentTypeError('Use JOB_ID:RELATIVE_PATH')
    identifier, path = value.split(':', 1)
    try:
        item = {'job_id': _identifier(identifier), 'path': path}
        _requests((item,))  # Reuse the actual API's path/ID admission.
    except (ValueError, argparse.ArgumentTypeError):
        raise argparse.ArgumentTypeError('Use a positive job ID and normalized relative artifact path') from None
    return item


def parser():
    selected = _Parser(description=__doc__, allow_abbrev=False)
    selected.add_argument('--origin', required=True, help='Exact GitLab HTTPS origin')
    selected.add_argument('--project-id', type=_identifier, required=True)
    selected.add_argument('--pipeline-id', type=_identifier, required=True)
    selected.add_argument('--expected-sha', required=True, help='Full exact source commit SHA')
    selected.add_argument('--token-env', required=True, help='Read-token environment-variable name, never a token value')
    selected.add_argument('--ca-bundle', type=Path, help='Absolute private-CA file; omit for system trust')
    selected.add_argument('--output', type=Path, required=True, help='New absolute private output directory')
    selected.add_argument('--timeout', type=float, default=60, help='One total deadline of 1–120 seconds')
    selected.add_argument('--artifact', dest='artifact_requests', action='append', type=_artifact, default=[],
                          metavar='JOB_ID:RELATIVE_PATH', help='Repeat for at most eight exact artifact selections')
    return selected


def main(argv=None):
    try:
        report = collect_gitlab_pipeline(**vars(parser().parse_args(argv)))
        print(json.dumps(report, sort_keys=True))
        return {'pass': 0, 'fail': 1, 'unknown': 2}[report['decision']]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        print(json.dumps({'decision': 'unknown', 'factory_decision': 'unknown', 'origin_authority': 'not-issued',
            'message': 'Exact GitLab collection incomplete; check selected IDs, credential reference, trust and output path'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
