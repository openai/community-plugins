#!/usr/bin/env python3
"""Installed fixed deployment MR validator; outputs one bounded public report."""

from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(1, str(ROOT / "scripts"))
from publish_and_prepare_mission_git import parser  # noqa: E402
from devsecops.factory.deployment_validation import validate_checkout  # noqa: E402


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report = validate_checkout(
            invocation=args.invocation,
            invocation_sha256=args.invocation_sha256,
            repository=args.expected_source_repository,
            revision=args.expected_source_revision,
        )
        print(json.dumps(report, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
