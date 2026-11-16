#!/usr/bin/env python3
"""Publish selected evidence and prepare local Git objects with bounded base GETs.

The upstream operation can write its selected image registry. This command never
pushes Git or deploys. It emits diagnostic evidence, not serialized authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from devsecops.factory.mission_git_invocation import load_invocation, operation_arguments  # noqa: E402
from devsecops.factory.mission_git_operation import qualify_publish_and_prepare_git  # noqa: E402
from devsecops.runtime.context import verify_seal  # noqa: E402


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid mission Git command arguments\n")


def parser():
    p = _Parser(description=__doc__, allow_abbrev=False)
    p.add_argument('--invocation', type=Path, required=True)
    p.add_argument('--invocation-sha256', required=True)
    p.add_argument('--expected-source-repository', type=Path, required=True)
    p.add_argument('--expected-source-revision', required=True)
    return p


def summary(report=None):
    """Project only bounded enums and the sealed result hash; no private paths."""
    value = {'schema_version': 1, 'kind': 'mission-git-preparation-cli-result',
        'decision': 'unknown', 'factory_decision': 'unknown', 'ci_execution': 'not-established',
        'lifecycle_completion': 'not-established', 'git_write': 'not-performed',
        'delivery_authority': 'not-issued', 'omission_authorized': False}
    if report is not None:
        verify_seal(report, 'git_operation_sha256')
        if (type(report.get('schema_version')) is not int or report['schema_version'] != 1
                or report.get('kind') != 'mission-publish-and-prepare-git'
                or any(report.get(key) != expected for key, expected in value.items()
                       if key not in ('kind', 'decision'))
                or report.get('decision') not in ('pass', 'fail', 'unknown')
                or type(report.get('checks')) is not dict
                or set(report['checks']) != {'publication_proposal', 'commit', 'transport', 'base'}
                or any(item not in ('pass', 'fail', 'unknown', 'not-started') for item in report['checks'].values())
                or not isinstance(report.get('git_operation_sha256'), str)
                or not re.fullmatch('[a-f0-9]{64}', report['git_operation_sha256'])):
            raise ValueError('Invalid direct operation result')
        value.update(decision=report['decision'], checks=dict(report['checks']),
                     git_operation_sha256=report['git_operation_sha256'])
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        selected = load_invocation(args.invocation, args.invocation_sha256)
        report, _, _ = qualify_publish_and_prepare_git(**operation_arguments(selected,
            expected_source_repository=args.expected_source_repository,
            expected_source_revision=args.expected_source_revision))
        result = summary(report)
    except (ValueError, OSError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
        result = summary()
    print(json.dumps(result, sort_keys=True))
    return {'pass': 0, 'fail': 1, 'unknown': 2}[result['decision']]


if __name__ == '__main__':
    raise SystemExit(main())
