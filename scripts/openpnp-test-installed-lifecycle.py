#!/usr/bin/env python3
"""Verify uninstall/reinstall against the stopped, populated core simulator state."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import signal

ROOT = Path(__file__).resolve().parents[1]


def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def inventory(state, excluded):
    result = {}
    for file in sorted(state.rglob('*')):
        if file.is_symlink():
            raise ValueError('Symbolic simulator state is not a qualification input')
        if file.is_file() and not file.is_relative_to(excluded):
            result[file.relative_to(state).as_posix()] = sha(file)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-suite', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--node', type=Path, required=True)
    args = parser.parse_args()
    core, output, node = args.core_suite.resolve(), args.output.resolve(), args.node.resolve()
    if not core.is_relative_to(ROOT / 'validation') or not output.is_relative_to(ROOT / 'validation') or output.exists():
        parser.error('Use this checkout\'s successful core suite and a fresh validation output')
    original = json.loads((core / 'report.json').read_text())
    cleanup = original['launcher_cleanup']
    if not original['passed'] or not cleanup['group_gone'] or cleanup['forced_kill'] or cleanup['errors']:
        raise ValueError('Core workflow must have passed and its owned simulator must be stopped')
    state = core / 'state'
    receipt = json.loads((state / 'installation.json').read_text())
    version = receipt['version']
    installed = state / 'bridge' / version
    if Path(receipt['bridge_jar']).resolve() != installed / 'openpnp-codex-bridge.jar':
        raise ValueError('Installation receipt escaped the selected state')
    before = inventory(state, installed)
    journal = state / 'journal' / 'operations.jsonl'
    if not journal.is_file() or journal.stat().st_size == 0:
        raise ValueError('Actual populated native operation history is required')
    output.mkdir(mode=0o700)
    spec = importlib.util.spec_from_file_location('owned_mcp_runner', ROOT / 'scripts/openpnp-test-native-mcp.py')
    owned = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owned)
    cli = ROOT / 'plugins/openpnp/scripts/openpnp.mjs'
    report = {'kind': 'actual-installed-simulator-uninstall-reinstall', 'passed': False,
              'physical_qualification': False, 'core_report_sha256': sha(core / 'report.json'),
              'bridge_sha256': receipt['bridge_sha256'], 'preserved_file_count': len(before),
              'original_journal_sha256': sha(journal)}
    previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        for sig in previous_handlers:
            signal.signal(sig, owned.interrupt)
        code = owned.run_logged([str(node), str(cli), 'uninstall-bridge', '--version', version,
                                 '--state-dir', str(state)], ROOT, owned.clean_environment(), output / 'uninstall.log', 30)
        if code != 0 or installed.exists() or inventory(state, installed) != before:
            raise AssertionError('Uninstall must remove only the selected Bridge directory')
        report['uninstall_preserves_all_other_files'] = True
        code = owned.run_logged([str(node), str(cli), 'install-bridge', '--state-dir', str(state)],
                                ROOT, owned.clean_environment(), output / 'reinstall.log', 30)
        if code != 0 or sha(installed / 'openpnp-codex-bridge.jar') != receipt['bridge_sha256']:
            raise AssertionError('Reinstall must restore the exact qualified Bridge')
        if inventory(state, installed) != before:
            raise AssertionError('Reinstall changed retained native state or installation identity')
        if sha(installed / 'build-manifest.json') != sha(ROOT / 'plugins/openpnp/bridge/build-manifest.json'):
            raise AssertionError('Installed manifest differs from this package')
        report['reinstall_preserves_all_other_files'] = True
        report['final_journal_sha256'] = sha(journal)
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
    finally:
        def write_report():
            report['interrupted_signals'] = list(owned.INTERRUPTED)
            if owned.INTERRUPTED:
                report['passed'] = False
            (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report))

        with owned.defer_interrupts():
            try:
                write_report()
            finally:
                for sig, handler in previous_handlers.items():
                    signal.signal(sig, handler)
            # The owned handler defers interruptions during report I/O. Close
            # its lifetime before flushing the final retained signal state.
            if report['interrupted_signals'] != owned.INTERRUPTED:
                write_report()
    return 0 if report['passed'] and not owned.INTERRUPTED else 1


if __name__ == '__main__':
    raise SystemExit(main())
