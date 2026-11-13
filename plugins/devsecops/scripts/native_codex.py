#!/usr/bin/env python3
"""Run one explicitly scoped installed native skill with private trace retention."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devsecops.factory.native_codex import execute_native_codex  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('request', 'run-root', 'installed-root', 'codex', 'engine-python', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    for name in ('request-sha256', 'package-sha256', 'plugin-id', 'model', 'client-version'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--reasoning-effort', choices=['none', 'minimal', 'low', 'medium', 'high',
                                                     'xhigh', 'max', 'ultra'], required=True)
    parser.add_argument('--total-timeout', type=float, default=900)
    parser.add_argument('--max-trace-bytes', type=int, default=16_000_000)
    args = vars(parser.parse_args(argv))
    args['request_path'] = args.pop('request')
    args['executable'] = args.pop('codex')
    args['expected_client_version'] = args.pop('client_version')
    cancelled = threading.Event()
    prior = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            prior[sig] = signal.signal(sig, lambda _sig, _frame: cancelled.set())
    try:
        result = execute_native_codex(**args, cancel=cancelled)
        print(json.dumps({key: result[key] for key in ('execution_status', 'scope', 'workflow',
            'request_sha256', 'result_sha256', 'trace_sha256', 'scenario_assertion_status',
            'runtime_native_authority', 'gaps', 'qualification_gaps')}, sort_keys=True))
        return 0 if result['execution_status'] == 'complete' else 130 if 'cancelled' in result['gaps'] else 2
    except (ValueError, OSError, TypeError, KeyError):
        print(json.dumps({'execution_status': 'incomplete', 'runtime_native_authority': 'not-issued',
                          'message': 'Explicit native Codex inputs could not be verified'}), file=sys.stderr)
        return 130 if cancelled.is_set() else 2
    finally:
        for sig, handler in prior.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    raise SystemExit(main())
