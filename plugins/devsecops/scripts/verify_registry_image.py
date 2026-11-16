#!/usr/bin/env python3
"""Acquire a scoped registry image's evidence and verify its exact signed subjects."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devsecops.adapters.image_verification import verify_registry_image  # noqa: E402
from devsecops.adapters.http import APIError  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('origin', 'repository', 'digest', 'key-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--architecture', choices=['amd64', 'arm64'], required=True)
    parser.add_argument('--variant')
    parser.add_argument('--required-predicate', action='append', required=True)
    parser.add_argument('--public-key', type=Path, required=True)
    parser.add_argument('--cosign', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ca-bundle', type=Path)
    parser.add_argument('--blob-download-policy', type=Path)
    parser.add_argument('--blob-download-policy-sha256')
    parser.add_argument('--attestation-size-policy', type=Path)
    parser.add_argument('--attestation-size-policy-sha256')
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument('--token-env')
    auth.add_argument('--basic-username-env')
    parser.add_argument('--basic-password-env')
    parser.add_argument('--token-path')
    parser.add_argument('--token-service')
    parser.add_argument('--timeout', type=float, default=5)
    parser.add_argument('--total-timeout', type=float, default=180)
    args = vars(parser.parse_args(argv))
    if (args['attestation_size_policy'] is None) != (args['attestation_size_policy_sha256'] is None):
        parser.error('--attestation-size-policy and --attestation-size-policy-sha256 must be supplied together')
    if (args['blob_download_policy'] is None) != (args['blob_download_policy_sha256'] is None):
        parser.error('--blob-download-policy and --blob-download-policy-sha256 must be supplied together')
    args['requested_digest'] = args.pop('digest')
    args['expected_key_sha256'] = args.pop('key_sha256')
    args['required_predicates'] = args.pop('required_predicate')
    args['allowed_repositories'] = [args['repository']]
    try:
        result = verify_registry_image(**args)
        print(json.dumps({'decision': result['decision'], 'verification': result['verification'],
            'build_authority': result['build_authority'], 'checks': result['checks'],
            'gaps': result['gaps'], 'output': str(args['output'])}, sort_keys=True))
        return 2  # Publisher/runtime/promotion authority is still incomplete.
    except (ValueError, OSError, TypeError, KeyError, APIError):
        print(json.dumps({'decision': 'unknown', 'verification': 'unknown', 'build_authority': 'not-issued',
                          'message': 'Explicit registry verification inputs could not be verified'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
