#!/usr/bin/env python3
"""Assess an explicitly pinned policy against acquired, verified image evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devsecops.adapters.image_policy import verify_registry_image_policy  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', dest='policy_path', type=Path, required=True)
    parser.add_argument('--policy-sha256', dest='expected_policy_sha256', required=True)
    for name in ('public-key', 'cosign', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
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
    parser.add_argument('--total-timeout', type=float, default=240)
    args = vars(parser.parse_args(argv))
    if (args['attestation_size_policy'] is None) != (args['attestation_size_policy_sha256'] is None):
        parser.error('--attestation-size-policy and --attestation-size-policy-sha256 must be supplied together')
    if (args['blob_download_policy'] is None) != (args['blob_download_policy_sha256'] is None):
        parser.error('--blob-download-policy and --blob-download-policy-sha256 must be supplied together')
    try:
        result = verify_registry_image_policy(**args)
        print(json.dumps({key: result[key] for key in ('decision', 'build_authority', 'policy_satisfaction', 'checks', 'gaps')}, sort_keys=True))
    except (ValueError, OSError, TypeError, KeyError, IndexError):
        print(json.dumps({'decision': 'unknown', 'build_authority': 'not-issued',
                          'message': 'Image policy assessment could not complete'}), file=sys.stderr)
    return 2  # This component does not establish complete base-image authority.


if __name__ == '__main__':
    raise SystemExit(main())
