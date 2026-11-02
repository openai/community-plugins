#!/usr/bin/env python3
"""Controlled CLI cancellation tests, never native simulator qualification."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'scripts/openpnp-test-installed-lifecycle.py'
OWNED = ROOT / 'scripts/openpnp-test-native-mcp.py'

FAKE_NODE = r'''
import json, os, shutil, signal, sys
from pathlib import Path
root = Path(__file__).resolve().parent
command = sys.argv[2]
state = Path(sys.argv[sys.argv.index('--state-dir') + 1])
installed = state / 'bridge/fixture-version'
signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
(root / (command + '.pid')).write_text(str(os.getpid()))
if command == os.environ.get('LIFECYCLE_FIXTURE_WAIT'):
    (root / 'ready').write_text(command)
    while True: signal.pause()
if command == 'uninstall-bridge': shutil.rmtree(installed)
elif command == 'install-bridge': shutil.copytree(root / 'plugins/openpnp/bridge', installed)
else: raise AssertionError(command)
'''


def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def wait_for(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Controlled lifecycle fixture missed its checkpoint')
        time.sleep(0.01)


def group_gone(pid):
    try:
        os.killpg(pid, 0)
        return False
    except ProcessLookupError:
        return True


@unittest.skipUnless(os.name == 'posix', 'Owned process-group regression requires POSIX')
class InstalledLifecycleRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = os.environ.get('OPENPNP_LIFECYCLE_TEST_EVIDENCE_DIR')
        cls.evidence = Path(selected or tempfile.mkdtemp(prefix='openpnp-lifecycle-runner-')).resolve()
        cls.evidence.mkdir(parents=True, exist_ok=True)
        (cls.evidence / 'scope.json').write_text(json.dumps({
            'scope': 'Controlled CLI cancellation only; synthetic installation, no native execution.',
            'native_execution': False, 'physical_qualification': False,
            'runner_sha256': sha(RUNNER), 'owned_runner_sha256': sha(OWNED),
            'test_sha256': sha(Path(__file__)), 'python': sys.version,
        }, indent=2) + '\n')

    def exercise(self, phase=None, interrupt=None, report_signal=None):
        root = self.evidence / self._testMethodName
        root.mkdir()
        scripts = root / 'scripts'; scripts.mkdir()
        shutil.copy2(RUNNER, scripts / RUNNER.name)
        shutil.copy2(OWNED, scripts / OWNED.name)
        bridge = root / 'plugins/openpnp/bridge'; bridge.mkdir(parents=True)
        (bridge / 'openpnp-codex-bridge.jar').write_text('Synthetic fixture, not a JVM archive\n')
        (bridge / 'build-manifest.json').write_text('{"fixture_only":true}\n')
        cli = root / 'plugins/openpnp/scripts/openpnp.mjs'; cli.parent.mkdir()
        cli.write_text('// Fake CLI fixture; not executed\n')
        core = root / 'validation/core'; state = core / 'state'
        installed = state / 'bridge/fixture-version'
        installed.parent.mkdir(parents=True)
        shutil.copytree(bridge, installed)
        (state / 'journal').mkdir()
        journal = state / 'journal/operations.jsonl'
        journal.write_text('{"fixture_only":true,"actual_tool_calls":false}\n')
        (state / 'installation.json').write_text(json.dumps({
            'version': 'fixture-version', 'bridge_jar': str(installed / 'openpnp-codex-bridge.jar'),
            'bridge_sha256': sha(bridge / 'openpnp-codex-bridge.jar'),
        }))
        (core / 'report.json').write_text(json.dumps({'passed': True,
            'launcher_cleanup': {'group_gone': True, 'forced_kill': False, 'errors': []}}))
        fixture = root / 'fake-node.py'; fixture.write_text(FAKE_NODE)
        node = root / 'fake-node'
        node.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(fixture)) + ' "$@"\n')
        node.chmod(0o700)
        env = {k: v for k, v in os.environ.items() if not k.startswith('OPENPNP_')}
        if phase: env['LIFECYCLE_FIXTURE_WAIT'] = phase
        output = root / 'validation/result'
        process = None
        emergency = []
        expected_signal = interrupt or report_signal
        try:
            with (root / 'runner.log').open('wb') as log:
                command = [sys.executable, '-B', str(scripts / RUNNER.name)]
                if report_signal:
                    command = [sys.executable, '-B', str(Path(__file__).resolve()),
                               '--signal-at-report', str(scripts / RUNNER.name), str(report_signal)]
                process = subprocess.Popen(command + ['--core-suite', str(core), '--output', str(output), '--node', str(node)],
                    env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
                if interrupt:
                    wait_for((root / 'ready').exists)
                    self.assertEqual((root / 'ready').read_text(), phase)
                    process.send_signal(interrupt)
                self.assertEqual(process.wait(timeout=15), 1 if expected_signal else 0)
            report = json.loads((output / 'report.json').read_text())
            self.assertEqual(report['passed'], expected_signal is None)
            self.assertEqual(report['interrupted_signals'], [expected_signal] if expected_signal else [])
            if interrupt:
                self.assertIn('interrupt', report['error'].lower())
                self.assertNotIn('reinstall_preserves_all_other_files', report)
                if phase == 'uninstall-bridge':
                    self.assertFalse((root / 'install-bridge.pid').exists(), 'Reinstall must not dispatch after cancellation')
            for name in ('uninstall', 'reinstall'):
                pid_file = root / (('uninstall-bridge' if name == 'uninstall' else 'install-bridge') + '.pid')
                if not pid_file.exists(): continue
                pid = int(pid_file.read_text())
                self.assertTrue(group_gone(pid), 'Owned CLI process group survived runner completion')
                cleanup = json.loads((output / (name + '.process.json')).read_text())
                self.assertTrue(cleanup['group_gone'])
                self.assertFalse(cleanup['forced_kill'])
                self.assertEqual(cleanup['errors'], [])
                self.assertEqual(cleanup['parent_exit_code'], 0)
            self.assertEqual(sha(journal), report['original_journal_sha256'])
            (root / 'result.json').write_text(json.dumps({'passed': True,
                'native_execution': False, 'physical_qualification': False,
                'signal': expected_signal, 'phase': 'report-write' if report_signal else phase,
                'receipt_sha256': sha(output / 'report.json')}, indent=2) + '\n')
        finally:
            if process is not None and process.poll() is None:
                emergency.append(process.pid)
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=5)
            for pid_file in root.glob('*.pid'):
                pid = int(pid_file.read_text())
                if not group_gone(pid):
                    emergency.append(pid)
                    try: os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError: pass
            (root / 'test-cleanup.json').write_text(json.dumps({'emergency_cleanup_pids': emergency,
                'native_execution': False}, indent=2) + '\n')
            self.assertEqual(emergency, [], 'Regression needed emergency process cleanup')

    def test_uninterrupted_control(self):
        self.exercise()

    def test_sigterm_during_uninstall(self):
        self.exercise('uninstall-bridge', signal.SIGTERM)

    def test_sigint_during_uninstall(self):
        self.exercise('uninstall-bridge', signal.SIGINT)

    def test_sigterm_during_reinstall(self):
        self.exercise('install-bridge', signal.SIGTERM)

    def test_sigint_during_reinstall(self):
        self.exercise('install-bridge', signal.SIGINT)

    def test_sigterm_during_report_write(self):
        self.exercise(report_signal=signal.SIGTERM)

    def test_sigint_during_report_write(self):
        self.exercise(report_signal=signal.SIGINT)


def signal_at_report():
    runner, signum = Path(sys.argv[2]), int(sys.argv[3])
    spec = importlib.util.spec_from_file_location('lifecycle_runner_report_signal', runner)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    original = Path.write_text
    sent = False

    def write_then_signal(file, *args, **kwargs):
        nonlocal sent
        result = original(file, *args, **kwargs)
        if file.name == 'report.json' and file.parent.name == 'result' and not sent:
            sent = True
            os.kill(os.getpid(), signum)
        return result

    Path.write_text = write_then_signal
    sys.argv = [str(runner), *sys.argv[4:]]
    try:
        result = module.main()
        if not sent: raise AssertionError('Report checkpoint did not send the real signal')
        return result
    finally:
        Path.write_text = original


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--signal-at-report':
        raise SystemExit(signal_at_report())
    unittest.main(verbosity=2)
