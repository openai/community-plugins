#!/usr/bin/env python3
"""Installed plugin entry point for the explicit portable GitOps profile."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from devsecops.portable_runtime import main

if __name__ == "__main__":
    raise SystemExit(main())
