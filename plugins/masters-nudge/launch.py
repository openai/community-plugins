"""Shared Python entry point for Codex hooks and the MCP server."""
import runpy
import sys
from pathlib import Path


def main():
    if sys.version_info < (3, 10):
        print("masters-nudge: the python command must run Python 3.10+", file=sys.stderr)
        return 1
    entries = {"hook": "hook_entry.py", "mcp": "mcp_entry.py"}
    if len(sys.argv) < 2 or sys.argv[1] not in entries:
        print("masters-nudge: expected hook or mcp", file=sys.stderr)
        return 1
    entry = Path(__file__).resolve().parent / entries[sys.argv[1]]
    sys.argv = [str(entry), *sys.argv[2:]]
    runpy.run_path(str(entry), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
