#!/usr/bin/env python3
"""Installed canonical qualification entry point; no alternate demo driver."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from devsecops.factory.qualification_runner import main

if __name__ == '__main__':
    raise SystemExit(main())
