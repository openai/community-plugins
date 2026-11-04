"""Run ordinary plugin checks without a Harbor installation or model credentials."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Check lock, package paths and helper behavior; stop at the first failure."""
    root = Path(__file__).resolve().parents[1]
    commands = [
        ["uv", "lock", "--check"],
        [sys.executable, "scripts/check_plugin.py"],
        ["uv", "run", "--locked", "ruff", "check", "."],
        ["uv", "run", "--locked", "ruff", "format", "--check", "."],
        ["uv", "run", "--locked", "ty", "check"],
        ["uv", "run", "--locked", "pytest", *sys.argv[1:]],
    ]
    for command in commands:
        sys.stderr.write("Check: " + " ".join(command) + "\n")
        result = subprocess.run(  # noqa: S603 — fixed tools; optional arguments are explicit pytest selectors.
            command, cwd=root, check=False
        )
        if result.returncode:
            raise SystemExit(result.returncode)
    sys.stderr.write("Ordinary plugin checks passed.\n")


if __name__ == "__main__":
    main()
