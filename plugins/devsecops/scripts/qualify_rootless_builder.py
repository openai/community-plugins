#!/usr/bin/env python3
"""Qualify an owned Linux rootless executor with an offline scratch/tool fixture."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from devsecops.factory.qualify_builder import cleanup, qualify  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--output", type=Path, required=True, help="New private absolute run path, at most 70 bytes")
    run.add_argument("--tools", type=Path, required=True, help="Directory containing the four pinned Linux executor binaries")
    run.add_argument("--owner-uid", dest="expected_uid", type=int, required=True)
    run.add_argument("--timeout", type=float, default=180)
    stop = sub.add_parser("cleanup")
    stop.add_argument("--output", dest="root", type=Path, required=True)
    stop.add_argument("--timeout", type=float, default=30)
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    try:
        report = qualify(**args) if command == "run" else cleanup(**args)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["decision"] == "pass" else 2
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        print(json.dumps({"decision": "unknown", "code": getattr(error, "code", "prerequisites-incomplete"),
            "message": "Owned executor prerequisites or cleanup authority could not be verified"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
