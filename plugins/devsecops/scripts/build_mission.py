#!/usr/bin/env python3
"""One recipe entry point for local, GitLab and Jenkins artifact preparation.

The explicit build operation composes actual F0 qualification and owned offline
execution. Factory promotion remains separate. No file can self-authorize it.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from devsecops.adapters.artifacts import scan_local_artifact, _output, _write, _canonical, _open_directory  # noqa: E402
from devsecops.factory.build import prepare_mission_build, inspect_rootless_builder, inspect_rootless_worker  # noqa: E402


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='Freeze exact Git files and verify the offline wheel; no build or push')
    prepare.add_argument('--repo', type=Path, required=True)
    prepare.add_argument('--revision', required=True)
    prepare.add_argument('--source-path', default='demo/software-factory/apps/mission-status')
    prepare.add_argument('--wheel', type=Path, required=True)
    prepare.add_argument('--base-repository', required=True)
    prepare.add_argument('--base-digest', required=True)
    prepare.add_argument('--architecture', choices=['amd64', 'arm64'], required=True)
    prepare.add_argument('--output', type=Path, required=True)
    scan = commands.add_parser('scan', help='Run pinned Syft/SPDX and Grype on explicit local bytes')
    scan.add_argument('--source', type=Path, required=True)
    scan.add_argument('--source-kind', choices=['directory', 'oci-archive'], required=True)
    scan.add_argument('--source-sha256', dest='expected_sha256', required=True)
    scan.add_argument('--image-digest', dest='expected_image_digest')
    scan.add_argument('--architecture', choices=['amd64', 'arm64'])
    scan.add_argument('--syft', type=Path, required=True)
    scan.add_argument('--grype', type=Path, required=True)
    scan.add_argument('--database', dest='database_snapshot', type=Path)
    scan.add_argument('--database-sha256')
    scan.add_argument('--max-database-age-hours', type=int, default=120)
    scan.add_argument('--timeout', type=float, default=120)
    scan.add_argument('--output', type=Path, required=True)
    inspect = commands.add_parser('inspect-builder', help='Read the exact authorized Linux rootless socket peer; no build')
    inspect.add_argument('--socket', dest='socket_path', type=Path, required=True)
    inspect.add_argument('--owner-uid', dest='expected_uid', type=int, required=True)
    inspect.add_argument('--device', dest='expected_device', type=int, required=True)
    inspect.add_argument('--inode', dest='expected_inode', type=int, required=True)
    inspect.add_argument('--timeout', type=float, default=15)
    worker = commands.add_parser('inspect-worker', help='Verify the pinned rootless worker RPC/platform; no build')
    worker.add_argument('--socket', dest='socket_path', type=Path, required=True)
    worker.add_argument('--owner-uid', dest='expected_uid', type=int, required=True)
    worker.add_argument('--device', dest='expected_device', type=int, required=True)
    worker.add_argument('--inode', dest='expected_inode', type=int, required=True)
    worker.add_argument('--buildctl', type=Path, required=True)
    worker.add_argument('--architecture', choices=['amd64', 'arm64'], required=True)
    worker.add_argument('--timeout', type=float, default=30)
    build = commands.add_parser('build', help='Qualify the exact Iron Bank base and build the selected Git source; no push')
    build.add_argument('--config', type=Path, required=True)
    build.add_argument('--config-sha256', dest='expected_config_sha256', required=True)
    build.add_argument('--output', type=Path, required=True)
    build.add_argument('--runtime-root', type=Path, required=True)
    build.add_argument('--timeout', type=float, default=1500)
    build.add_argument('--expected-source-repository', type=Path, help='Pair with expected revision to bind a CI checkout')
    build.add_argument('--expected-source-revision', help='Exact CI commit; requires expected repository')
    gate = commands.add_parser('build-gate', help='Report the currently unfinished canonical F0/executor gate; performs no mutation')
    gate.add_argument('--output', type=Path, required=True)
    return p


def main(argv=None):
    args = vars(parser().parse_args(argv))
    command = args.pop('command')
    try:
        if command == 'prepare':
            report = prepare_mission_build(**args)
            code = 0  # Preparation completed. This is explicitly not a factory qualification verdict.
        elif command == 'scan':
            report = scan_local_artifact(**args)
            code = 2 if report['decision'] == 'unknown' else 1 if report['decision'] == 'fail' else 0
        elif command == 'inspect-builder':
            report = inspect_rootless_builder(**args)
            code = 0  # Narrow peer inspection only; no build authorization.
        elif command == 'inspect-worker':
            report = inspect_rootless_worker(**args)
            code = 0  # Narrow authenticated peer/RPC observation, no runtime qualification.
        elif command == 'build':
            from devsecops.factory.mission_operation import qualify_and_build
            report, _ = qualify_and_build(**args)
            code = 0 if report['decision'] == 'pass' else 1 if report['decision'] == 'fail' else 2
        else:
            report = {'schema_version': 1, 'kind': 'mission-build-gate', 'execution_status': 'partial', 'decision': 'unknown',
                'build_started': False, 'push_started': False, 'promotion_started': False,
                'gaps': [{'code': code, 'required': True} for code in ('explicit-qualify-and-build-configuration-required',
                    'live-rootless-execution-unqualified', 'live-staging-readback-chain-unqualified',
                    'sign-and-promotion-chain-unimplemented')]}
            with _output(args['output']) as directory:
                before = os.fstat(directory)
                _write(directory, 'build-gate.json', _canonical(report)+b'\n')
                os.fsync(directory)
                try:
                    current = _open_directory(args['output'])
                    try:
                        after = os.fstat(current)
                    finally:
                        os.close(current)
                    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                        raise ValueError('Output directory identity changed')
                except (OSError, ValueError):
                    os.unlink('build-gate.json', dir_fd=directory)
                    raise
            code = 2
        print(json.dumps(report, sort_keys=True))
        return code
    except (ValueError, OSError, TypeError, KeyError, IndexError):
        print(json.dumps({'execution_status': 'partial', 'decision': 'unknown',
            'message': 'Mission artifact operation could not verify its explicit inputs; no successful build or promotion is established'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
