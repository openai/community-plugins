#!/usr/bin/env python3
"""Small management surface for the single supported Provider path."""
import argparse
import json
from pathlib import Path
from masters_nudge.management import (
    doctor, list_providers, get_provider, configure_provider, reset_provider_config, recent_nudges,
)

def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("--host", choices=["codex"], default="codex")
    provider = commands.add_parser("provider").add_subparsers(dest="action", required=True)
    provider.add_parser("list")
    provider.add_parser("get").add_argument("--host", choices=["codex"], default="codex")
    chosen = provider.add_parser("set")
    chosen.add_argument("provider", choices=["openai"])
    chosen.add_argument("--model", default="")
    provider.add_parser("reset")
    commands.add_parser("recent-nudges").add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if args.command == "doctor":
        result = doctor(Path(__file__).resolve().parent, args.host)
    elif args.command == "recent-nudges":
        result = recent_nudges(args.limit)
    elif args.action == "list":
        result = list_providers()
    elif args.action == "get":
        result = get_provider(host=args.host)
    elif args.action == "set":
        result = configure_provider(args.provider, model=args.model)
    else:
        result = reset_provider_config()
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 1 if result.get("error") or result.get("core_ready") is False else 0

if __name__ == "__main__":
    raise SystemExit(main())
