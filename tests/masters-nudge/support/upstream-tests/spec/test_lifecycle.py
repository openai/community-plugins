"""Lifecycle qualification against real processes and the durable journal."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from masters_nudge import providers
from masters_nudge.contracts import ProviderRun, SessionRef, ToolCompleted, ToolFault
from masters_nudge.core import NudgeCore
from masters_nudge.runtime import RuntimePaths, RuntimeSettings
from masters_nudge.storage import Journal

ROOT = Path(__file__).resolve().parents[2]


class JournalLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = SessionRef('s', 't', str(self.root))

    def test_cancel_is_terminal_preserves_history_and_next_turn_can_review(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        for interruption in (KeyboardInterrupt(), SystemExit(130)):
            with self.subTest(interruption=type(interruption).__name__):
                data = self.root / type(interruption).__name__
                paths = RuntimePaths(ROOT, data, data, data / 'error.log')
                dispatch = mock.Mock(return_value=ProviderRun('{"feedback":null}'))
                core = NudgeCore(RuntimeSettings('openai', 'model', paths), dispatch=dispatch)
                core.start_round(self.session, 'keep behavior')
                event = ToolCompleted('p', 'apply_patch',
                    '*** Begin Patch\n*** Add File: job.py\n+value = 1\n*** End Patch', 'Success')
                core.process_event(self.session, event)
                dispatch.side_effect = interruption
                with self.assertRaises(type(interruption)):
                    core.process_event(self.session, event)
                journal = Journal(data)
                self.assertEqual(sorted(row['outcome'] for row in journal.recent()), ['fault', 'silence'])
                fault = next(row for row in journal.recent() if row['outcome'] == 'fault')
                self.assertEqual(fault['detail']['fault']['kind'], 'interrupted')
                self.assertEqual(fault['delivered'], 0)
                self.assertEqual(journal.unresolved(), [])
                with self.assertRaises(ToolFault):
                    core.process_event(self.session, event)
                self.assertEqual(dispatch.call_count, 2)
                dispatch.side_effect = None
                core.start_round(self.session, 'continue')
                core.process_event(self.session, event)
                self.assertEqual(dispatch.call_count, 3)
                self.assertEqual(len(Journal(data).recent()), 3)

    def test_killed_writer_keeps_start_and_recovers_as_fault_without_replaying_provider(self):
        script = (
            'import pathlib,sys,time; from masters_nudge.storage import Journal; '
            'from masters_nudge.contracts import SessionRef; '
            'root=pathlib.Path(sys.argv[1]); j=Journal(root); '
            "s=SessionRef('s','t',str(root)); j.start_round(s,'task'); "
            "j.begin(s,[{'tool_use_id':'original'}]); "
            "(root/'ready').write_text('ready'); time.sleep(30)"
        )
        child = subprocess.Popen([sys.executable, '-c', script, str(self.root)], cwd=ROOT)
        try:
            deadline = time.monotonic() + 5
            while not (self.root / 'ready').exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue((self.root / 'ready').exists())
            child.kill()
            child.wait(timeout=3)
            journal = Journal(self.root, provider_timeout_sec=0)
            self.assertEqual(journal.recent(), [])
            self.assertEqual(len(journal.unresolved()), 1)
            # Advance the recorded deadline, without waiting 20 minutes in a test.
            with journal.connect() as db:
                db.execute("UPDATE judgment_events SET happened=0,payload='{}' WHERE kind='started'")
            with self.assertRaises(ToolFault):
                journal.begin(self.session, [{'tool_use_id': 'next'}])
            self.assertEqual(journal.unresolved(), [])
            self.assertEqual(journal.recent()[0]['outcome'], 'fault')
            with journal.connect() as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(db.execute("SELECT COUNT(*) FROM judgment_events WHERE kind='started'").fetchone()[0], 1)
            journal.start_round(self.session, 'new turn')
            attempt, task = journal.begin(self.session, [])
            self.assertTrue(journal.finish(self.session, attempt, task, 'silence', {}))
            self.assertEqual(len(Journal(self.root).recent()), 2)
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)

    def test_interrupt_is_turn_scoped_and_rejects_late_feedback(self):
        journal = Journal(self.root)
        journal.start_round(self.session, 'task')
        attempt, task = journal.begin(self.session, [])
        journal.interrupt(SessionRef('s', 'wrong-turn', str(self.root)))
        self.assertFalse(journal.cancelled(attempt))
        journal.interrupt(self.session)
        journal.interrupt(self.session)
        self.assertTrue(journal.cancelled(attempt))
        self.assertFalse(journal.finish(self.session, attempt, task, 'feedback', {'raw_output':'late'}))
        self.assertEqual(len(journal.recent()), 1)
        self.assertEqual(journal.recent()[0]['delivered'], 0)
        self.assertEqual(journal.unresolved(), [])
        journal.start_round(self.session, 'new request')
        next_attempt, next_task = journal.begin(self.session, [])
        self.assertFalse(journal.cancelled(next_attempt))
        self.assertTrue(journal.finish(self.session, next_attempt, next_task, 'silence', {}))

    def test_competing_finalizers_write_one_terminal_fact(self):
        journal = Journal(self.root)
        journal.start_round(self.session, 'task')
        attempt, task = journal.begin(self.session, [])
        barrier = threading.Barrier(2)
        outcomes, errors = [], []
        def finish(outcome):
            try:
                other = Journal(self.root)
                barrier.wait(timeout=3)
                outcomes.append(other.finish(self.session, attempt, task, outcome, {'original': outcome}))
            except Exception as exc:
                errors.append(exc)
        workers = [threading.Thread(target=finish, args=(outcome,)) for outcome in ('silence', 'fault')]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual(len(Journal(self.root).recent()), 1)
        self.assertEqual(journal.unresolved(), [])
        with journal.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM judgment_events WHERE kind='finished'").fetchone()[0], 1)

    def test_storage_failure_rolls_back_and_corruption_is_not_silence(self):
        journal = Journal(self.root)
        journal.start_round(self.session, 'original')
        with self.assertRaises(sqlite3.OperationalError):
            with journal.connect() as db:
                db.execute("UPDATE rounds SET request='partial'")
                db.execute('INSERT INTO missing_table VALUES(1)')
        with journal.connect() as db:
            self.assertEqual(db.execute('SELECT request FROM rounds').fetchone()[0], 'original')
        corrupt = self.root / 'corrupt'
        corrupt.mkdir()
        (corrupt / 'feedback.sqlite3').write_bytes(b'not a database')
        dispatch = mock.Mock()
        paths = RuntimePaths(ROOT, corrupt, corrupt, corrupt / 'error.log')
        with self.assertRaises(sqlite3.DatabaseError):
            NudgeCore(RuntimeSettings('openai', 'model', paths), dispatch=dispatch)
        dispatch.assert_not_called()

    def test_restart_recovers_abandoned_queue_without_hook_completion(self):
        journal = Journal(self.root)
        journal.start_round(self.session, 'task')
        with journal.connect() as db:
            round_id = db.execute('SELECT round_id FROM rounds').fetchone()[0]
            journal._append_event(db, session='s', turn='t', round_id=round_id,
                kind='queued', attempt='abandoned', batch_id='batch', payload={'deadline': 0})
        recovered = Journal(self.root)
        self.assertEqual(recovered.recent()[0]['detail']['fault']['kind'], 'timeout')
        with recovered.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM judgment_events WHERE kind='finished'").fetchone()[0], 1)
        self.assertFalse(recovered.finish(self.session, 'abandoned',
            {'round_id': round_id, 'batch_id': 'batch'}, 'feedback', {}))

    def test_cancel_while_waiting_releases_followers_and_rejects_active_result(self):
        journal = Journal(self.root)
        journal.start_round(self.session, 'task')
        attempt, task = journal.begin(self.session, [])
        cancel = threading.Event()
        outcomes = []
        def wait_for_review():
            try:
                journal.begin(self.session, [], cancel_event=cancel)
            except ToolFault as exc:
                outcomes.append(exc.kind)
        worker = threading.Thread(target=wait_for_review, daemon=True)
        worker.start()
        deadline = time.monotonic() + 3
        while len(journal.unresolved()) < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(len(journal.unresolved()), 2)
        cancel.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes, ['interrupted'])
        self.assertEqual(journal.unresolved(), [])
        self.assertTrue(journal.cancelled(attempt))
        self.assertFalse(journal.finish(self.session, attempt, task, 'feedback', {}))

    def test_persisted_deadline_survives_restart_with_different_defaults(self):
        journal = Journal(self.root)
        journal.start_round(self.session, 'task')
        attempt, task = journal.begin(self.session, [])
        restarted = Journal(self.root, provider_timeout_sec=-9999)
        self.assertFalse(restarted.cancelled(attempt))
        with restarted.connect() as db:
            db.execute("UPDATE judgment_events SET payload=? WHERE kind='started'",
                       (json.dumps({'deadline': 0}),))
        recovered = Journal(self.root)
        self.assertTrue(recovered.cancelled(attempt))
        self.assertFalse(recovered.finish(self.session, attempt, task, 'silence', {}))

    def test_interrupt_before_first_patch_closes_round_and_new_round_reopens(self):
        journal = Journal(self.root)
        journal.start_round(self.session, 'task')
        journal.interrupt(self.session)
        with self.assertRaises(ToolFault):
            journal.begin(self.session, [])
        self.assertEqual(journal.unresolved(), [])
        journal.start_round(self.session, 'new request')
        attempt, task = journal.begin(self.session, [])
        self.assertTrue(journal.finish(self.session, attempt, task, 'silence', {}))

    def test_capacity_stops_admission_but_keeps_finalization_and_history(self):
        journal = Journal(self.root)
        journal.start_round(self.session, 'task')
        attempt, task = journal.begin(self.session, [])
        with journal.connect() as db:
            used = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
        with mock.patch('masters_nudge.storage.JOURNAL_ADMISSION_BYTES', used), \
             mock.patch('masters_nudge.storage.JOURNAL_MAX_BYTES', used + 32768):
            with self.assertRaises(ToolFault) as caught:
                journal.begin(self.session, [])
            self.assertEqual(caught.exception.kind, 'storage')
            self.assertTrue(journal.finish(self.session, attempt, task, 'silence', {'proof': 'retained'}))
            with self.assertRaises(sqlite3.DatabaseError):
                with journal.connect() as db:
                    db.execute("UPDATE attempts SET detail=? WHERE id=?", ('x' * 131072, attempt))
            self.assertEqual(journal.recent()[0]['detail']['proof'], 'retained')
            with journal.connect() as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


@unittest.skipUnless(os.name == 'nt', 'Windows installed-platform lifecycle')
class WindowsLifecycleTests(unittest.TestCase):
    def setUp(self):
        import ctypes
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handles = []
        self.addCleanup(self.cleanup_processes)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.record = Path(self.temp.name) / 'pids.json'

    def cleanup_processes(self):
        for handle in self.handles:
            if self.kernel.WaitForSingleObject(handle, 0) != 0:
                self.kernel.TerminateProcess(handle, 1)
            self.kernel.CloseHandle(handle)

    def tree_script(self, delay=30):
        leaf = 'import time; time.sleep(30)'
        branch = (
            'import os,subprocess,sys,json,time,pathlib; '
            f'leaf=subprocess.Popen([sys.executable,"-c",{leaf!r}]); '
            f'record=pathlib.Path({str(self.record)!r}); '
            "staged=record.with_suffix('.tmp'); staged.write_text(json.dumps([os.getpid(),leaf.pid])); staged.replace(record); "
            f'time.sleep({delay})'
        )
        return branch

    def track(self):
        deadline = time.monotonic() + 5
        while not self.record.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.record.exists(), 'Provider tree did not start')
        pids = json.loads(self.record.read_text())
        for pid in pids:
            handle = self.kernel.OpenProcess(0x00100000 | 0x0001, False, pid)
            self.assertTrue(handle, f'Cannot track {pid}')
            self.handles.append(handle)

    def assert_tree_ended(self):
        for handle in self.handles:
            self.assertEqual(self.kernel.WaitForSingleObject(handle, 3000), 0, 'Provider descendant survived')

    def test_forced_caller_termination_reclaims_provider_and_descendant(self):
        script = (
            'import os,sys; from masters_nudge.providers import _run_cli_process; '
            f'_run_cli_process([sys.executable,"-c",{self.tree_script()!r}],'
            'environment=dict(os.environ),timeout_sec=60)'
        )
        caller = subprocess.Popen([sys.executable, '-c', script], cwd=ROOT)
        try:
            self.track()
            caller.kill()
            caller.wait(timeout=3)
            self.assert_tree_ended()
        finally:
            if caller.poll() is None:
                caller.kill()
            caller.wait(timeout=3)

    def test_keyboard_cancellation_cleans_tree_and_propagates(self):
        from masters_nudge.windows_process import WindowsProcessJob
        wait = WindowsProcessJob.wait
        interrupted = False
        def cancel(process, timeout=None):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                self.track()
                raise KeyboardInterrupt()
            return wait(process, timeout)
        with mock.patch.object(WindowsProcessJob, 'wait', cancel):
            with self.assertRaises(KeyboardInterrupt):
                providers._run_cli_process([sys.executable, '-c', self.tree_script()],
                                           environment=dict(os.environ), timeout_sec=30)
        self.assert_tree_ended()

    def test_normal_exit_reclaims_descendant_and_keeps_unicode_streams(self):
        script = self.tree_script(delay=.5) + '; print("提醒已完成")'
        results = []
        worker = threading.Thread(target=lambda: results.append(providers._run_cli_process(
            [sys.executable, '-c', script], environment={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, timeout_sec=5)))
        worker.start()
        self.track()
        worker.join(6)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].returncode, 0)
        self.assertIn('提醒已完成', results[0].stdout)
        self.assert_tree_ended()

    def test_job_setup_failure_never_executes_provider(self):
        from masters_nudge.windows_process import WindowsProcessJob
        original = WindowsProcessJob.start
        def fail(job, *args, **kwargs):
            with mock.patch.object(job.kernel, 'UpdateProcThreadAttribute', return_value=False):
                return original(job, *args, **kwargs)
        with mock.patch.object(WindowsProcessJob, 'start', fail):
            with self.assertRaises(OSError):
                providers._run_cli_process([sys.executable, '-c', self.tree_script()],
                                           environment=dict(os.environ), timeout_sec=2)
        self.assertFalse(self.record.exists())

    def test_mcp_cancel_disconnect_and_interrupt_hook_stop_live_provider_and_journal_fault(self):
        for mode in ('cancel', 'disconnect', 'interrupt_hook'):
            with self.subTest(mode=mode):
                data = Path(self.temp.name) / mode
                data.mkdir()
                self.record = data / 'pids.json'
                journal = Journal(data)
                journal.start_round(SessionRef('s', 't', str(data)), 'task')
                subprocess.run(['git', 'init', '-q', str(data)], check=True)
                script = (
                    'import os,sys,pathlib; import mcp_entry; '
                    'from masters_nudge.core import NudgeCore; '
                    'from masters_nudge.codex_adapter import CodexAdapter; '
                    'from masters_nudge.runtime import RuntimeSettings,RuntimePaths; '
                    'from masters_nudge.providers import _run_cli_process; '
                    'from masters_nudge.contracts import ProviderRun; '
                    f'data=pathlib.Path({str(data)!r}); '
                    'settings=RuntimeSettings("openai","model",RuntimePaths(pathlib.Path.cwd(),data,data,data/"error.log")); '
                    f'dispatch=lambda **kw: (_run_cli_process([sys.executable,"-c",{self.tree_script()!r}],'
                    'input_text="",environment=dict(os.environ),timeout_sec=30,cancel_event=kw["cancel_event"]),'
                    "ProviderRun('{\"feedback\":null}'))[1]; "
                    'core=NudgeCore(settings,dispatch=dispatch); mcp_entry.serve(core,CodexAdapter(core))'
                )
                server = subprocess.Popen([sys.executable, '-c', script], cwd=ROOT,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', env={**os.environ, "MASTERS_NUDGE_ACTIVE":"0",
                        "PYTHONIOENCODING":"utf-8"})
                try:
                    early_reply = []
                    payload = {'hook_event_name':'PostToolUse', 'session_id':'s','turn_id':'t',
                        'cwd':str(data),'transcript_path':None,'tool_name':'apply_patch','tool_use_id':'p',
                        'tool_input':'*** Begin Patch\n*** Add File: job.py\n+value = 1\n*** End Patch',
                        'tool_response':'Success'}
                    server.stdin.write(json.dumps({'id':7,'method':'tools/call',
                        'params':{'name':'review_patch','arguments':payload}})+'\n')
                    server.stdin.flush()
                    try:
                        self.track()
                    except AssertionError:
                        server.stdin.close()
                        server.stdin = None
                        output, error = server.communicate(timeout=3)
                        raise AssertionError(f"Provider did not start: {output} {error} journal={Journal(data).recent()}")
                    if mode == 'cancel':
                        server.stdin.write(json.dumps({'method':'notifications/cancelled',
                            'params':{'requestId':7,'reason':'user interrupted'}})+'\n')
                        server.stdin.flush()
                    elif mode == 'interrupt_hook':
                        package = ROOT / 'plugins/masters-nudge'
                        hooks = json.loads((package / 'hooks/hooks.json').read_text(encoding='utf-8'))['hooks']
                        interrupt = dict(payload, hook_event_name='Interrupt')
                        result = subprocess.run(
                            [sys.executable, str(package / 'launch.py'), 'hook', '--host', 'codex_cli'],
                            input=json.dumps(interrupt), capture_output=True, text=True,
                            timeout=hooks['Interrupt'][0]['hooks'][0]['timeout'],
                            cwd=data, env={**os.environ, 'MASTERS_NUDGE_ACTIVE': '0',
                                'MASTERS_NUDGE_DATA_DIR': str(data), 'MASTERS_NUDGE_RUNTIME_DIR': str(package)})
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout, '')
                        self.assert_tree_ended()
                        reader = threading.Thread(target=lambda: early_reply.append(server.stdout.readline()), daemon=True)
                        reader.start()
                        reader.join(3)
                        self.assertFalse(reader.is_alive(), 'Interrupt hook left the MCP request unfinished')
                        self.assertIsNone(server.poll())
                        rows = Journal(data).recent()
                        self.assertEqual(rows[0]['detail']['fault']['kind'], 'interrupted')
                        self.assertEqual(Journal(data).unresolved(), [])
                    server.stdin.close()
                    server.stdin = None
                    output, error = server.communicate(timeout=8)
                    self.assertEqual(server.returncode, 0, error)
                    reply = json.loads(''.join(early_reply) + output)
                    if mode == 'interrupt_hook':
                        self.assertEqual(reply['id'], 7)
                        self.assertNotIn('hookSpecificOutput', json.loads(reply['result']['content'][0]['text']))
                    else:
                        self.assertEqual(reply['error']['code'], -32800)
                    self.assert_tree_ended()
                    rows = Journal(data).recent()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]['outcome'], 'fault')
                    self.assertEqual(rows[0]['detail']['fault']['kind'], 'interrupted')
                    self.assertEqual(rows[0]['delivered'], 0)
                    self.assertEqual(Journal(data).unresolved(), [])
                finally:
                    if server.poll() is None:
                        server.kill()
                    server.communicate(timeout=3)
