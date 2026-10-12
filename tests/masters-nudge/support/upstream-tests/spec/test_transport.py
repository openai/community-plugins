import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from masters_nudge import providers
from masters_nudge.contracts import ToolFault

ROOT = Path(__file__).resolve().parents[2]


class TransportTests(unittest.TestCase):
    def call(self):
        return providers.call_codex_result("SYSTEM-unique", "PACKET-unique", "model",
                   schema_path=ROOT / "nudge-schema.json", timeout_sec=5, workspace_root=str(ROOT),
                   remaining_chars=1500, codex_bin_resolver=lambda: "codex")

    def fake_process(self, command, **kwargs):
        output = Path(command[command.index("-o") + 1])
        output.write_text('{"feedback":null}', encoding="utf-8")
        args = json.loads(next(part.split("=", 1)[1] for part in command if part.startswith("mcp_servers.readrepo.args=")))
        Path(args[args.index("--audit") + 1]).write_text(json.dumps({"name": "initialize", "text": "", "fault": ""}) + "\n")
        return subprocess.CompletedProcess(command, 0, '{"type":"turn.completed","usage":{"input_tokens":123}}\n', "")

    def test_windows_prefers_the_native_codex_process_over_the_command_wrapper(self):
        locations = {"codex.exe": r"C:\native\codex.exe", "codex": r"C:\npm\codex.CMD"}
        with mock.patch.object(providers.os, "name", "nt"), \
             mock.patch.object(providers.shutil, "which", side_effect=locations.get):
            self.assertEqual(providers.resolve_codex_bin(), locations["codex.exe"])

    def test_one_prompt_and_only_repository_tools(self):
        with mock.patch.object(providers, "_run_cli_process", side_effect=self.fake_process) as process:
            result = self.call()
        args = process.call_args.args[0]
        self.assertIn("--ignore-user-config", args)
        self.assertIn("features.shell_tool=false", args)
        self.assertIn("features.view_image=false", args)
        self.assertIn("features.plugins=false", args)
        self.assertIn('model_reasoning_effort="medium"', args)
        self.assertIn("mcp_servers.readrepo.required=true", args)
        self.assertIn('mcp_servers.readrepo.enabled_tools=["search_repo","read_file"]', args)
        self.assertIn('mcp_servers.readrepo.env={PYTHONIOENCODING="utf-8"}', args)
        self.assertIn("read-only", args)
        self.assertEqual(process.call_args.kwargs["input_text"].count("SYSTEM-unique"), 1)
        self.assertEqual(process.call_args.kwargs["input_text"].count("PACKET-unique"), 1)
        self.assertEqual(result.usage["input_tokens"], 123)

    def test_successful_null_without_mcp_startup_is_a_fault(self):
        def process(command, **kwargs):
            Path(command[command.index("-o") + 1]).write_text('{"feedback":null}')
            return subprocess.CompletedProcess(command, 0, "", "MCP failed")
        with mock.patch.object(providers, "_run_cli_process", side_effect=process), self.assertRaises(ToolFault) as fault:
            self.call()
        self.assertEqual(fault.exception.kind, "mcp_startup")

    def test_mcp_material_and_failures_survive_provider_transport(self):
        from masters_nudge.contracts import MaterialLine, json_text
        line = {"source": "current_structure", "path": "src/狀態.py", "line": 2,
                "text": '狀態 = "完成"'}
        cases = (
            (json_text({"segments": [{"path": "src/狀態.py", "start": 2,
                                       "lines": ['狀態 = "完成"']}], "truncated": False}),
             "", None, (MaterialLine(**line),)),
            (json_text({"error": "read denied"}), "read denied", None, ()),
            ("x" * 1501, "", "mcp_budget", ()),
        )
        for text, fault, expected_fault, expected_materials in cases:
            with self.subTest(expected_fault=expected_fault):
                def process(command, **kwargs):
                    result = self.fake_process(command, **kwargs)
                    args = json.loads(next(part.split("=", 1)[1] for part in command
                                           if part.startswith("mcp_servers.readrepo.args=")))
                    audit = Path(args[args.index("--audit") + 1])
                    with audit.open("a", encoding="utf-8") as stream:
                        stream.write(json_text({"name": "read_file", "arguments": {"path": line["path"]},
                                                "text": text, "fault": fault}) + "\n")
                    return result  # Provider still returned feedback:null.
                with mock.patch.object(providers, "_run_cli_process", side_effect=process):
                    if expected_fault:
                        with self.assertRaises(ToolFault) as caught:
                            self.call()
                        self.assertEqual(caught.exception.kind, expected_fault)
                    else:
                        result = self.call()
                        self.assertEqual(result.materials, expected_materials)
                        self.assertEqual(result.trace[-1]["text"], text)

    def test_nonzero_and_timeout_never_recover_a_successful_looking_null(self):
        for problem in (subprocess.CompletedProcess([], 1, '{"feedback":null}', "failed"),
                        subprocess.TimeoutExpired("codex", 5, output='{"feedback":null}')):
            with self.subTest(problem=problem):
                behavior = {"side_effect": problem} if isinstance(problem, Exception) else {"return_value": problem}
                with mock.patch.object(providers, "_run_cli_process", **behavior), self.assertRaises(ToolFault):
                    self.call()

    @unittest.skipUnless(os.name == "nt", "Windows process-tree cleanup")
    def test_timeout_ends_real_parent_and_child_with_bounded_cleanup(self):
        import ctypes
        from ctypes import wintypes
        import threading
        import time

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handles, outcomes = [], []
        finished = threading.Event()
        with tempfile.TemporaryDirectory() as raw:
            pid_file = Path(raw) / "processes.json"
            provider = (
                "import json,os,pathlib,subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                f"record=pathlib.Path({str(pid_file)!r}); "
                "pending=record.with_suffix('.tmp'); "
                "pending.write_text(json.dumps([os.getpid(),child.pid])); pending.replace(record); "
                "time.sleep(30)"
            )
            started = time.monotonic()
            def run():
                try:
                    outcomes.append(providers._run_cli_process(
                        [sys.executable, "-c", provider], environment=dict(os.environ), timeout_sec=3))
                except Exception as exc:
                    outcomes.append(exc)
                finally:
                    finished.set()
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            try:
                while not pid_file.exists() and time.monotonic() - started < 2:
                    time.sleep(0.01)
                self.assertTrue(pid_file.exists(), "Provider did not start its child")
                for pid in json.loads(pid_file.read_text()):
                    handle = kernel.OpenProcess(0x00100000 | 0x0001, False, pid)
                    self.assertTrue(handle, f"Cannot track process {pid}")
                    handles.append(handle)
                self.assertTrue(finished.wait(8), "Timeout cleanup did not return")
                self.assertLess(time.monotonic() - started, 8)
                self.assertEqual(len(outcomes), 1)
                self.assertIsInstance(outcomes[0], subprocess.TimeoutExpired)
                for handle in handles:
                    self.assertEqual(kernel.WaitForSingleObject(handle, 0), 0,
                                     "Timed-out Provider or its child is still alive")
            finally:
                for handle in handles:
                    if kernel.WaitForSingleObject(handle, 0) != 0:
                        kernel.TerminateProcess(handle, 1)
                    kernel.CloseHandle(handle)
                worker.join(5)

    @unittest.skipUnless(os.name == "nt", "Windows inherited-handle behavior")
    def test_exited_provider_does_not_wait_for_a_descendant_holding_its_output(self):
        child = "import time; time.sleep(3)"
        provider = (
            "import subprocess,sys; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}],stdout=sys.stdout,stderr=sys.stderr)"
        )
        probe = (
            "import os,sys,time; from masters_nudge.providers import _run_cli_process; "
            "started=time.monotonic(); "
            f"_run_cli_process([sys.executable,'-c',{provider!r}],environment=dict(os.environ),timeout_sec=1); "
            "raise SystemExit(0 if time.monotonic()-started < 1.5 else 1)"
        )
        result = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, timeout=5)
        self.assertEqual(result.returncode, 0)

    @unittest.skipUnless(os.name == "nt", "Windows pipe behavior")
    def test_timeout_still_applies_when_child_never_reads_large_stdin(self):
        code = (
            "import os,subprocess,sys; from masters_nudge.providers import _run_cli_process; "
            "\ntry: _run_cli_process([sys.executable,'-c','import time; time.sleep(10)'],"
            "input_text='x'*1000000,environment=dict(os.environ),timeout_sec=1)"
            "\nexcept subprocess.TimeoutExpired: raise SystemExit(0)"
            "\nraise SystemExit(1)"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, timeout=5)
        self.assertEqual(result.returncode, 0)

    def test_failure_message_uses_terminal_event_then_error_then_stderr(self):
        capacity = 'Selected model is at capacity. Please try a different model.'
        cases = (
            (json.dumps({'type': 'error', 'message': capacity}) + '\n' +
             json.dumps({'type': 'turn.failed', 'error': {'message': capacity}}),
             'WARN shell_snapshot: Shell snapshot not supported yet for PowerShell', capacity),
            ('{"type":"error","message":"earlier"}\n'
             '{"type":"turn.failed","error":{"message":"final failure"}}', 'warning', 'final failure'),
            ('{"type":"error","message":"authentication failed"}', 'warning', 'authentication failed'),
            ('not json\n[]\nnull\n{"type":"turn.failed","error":null}', 'process failed', 'process failed'),
            ('', '', '未提供錯誤訊息'),
        )
        for stdout, stderr, expected in cases:
            with self.subTest(expected=expected):
                result = subprocess.CompletedProcess([], 1, stdout, stderr)
                with mock.patch.object(providers, '_run_cli_process', return_value=result) as process:
                    with self.assertRaises(ToolFault) as caught:
                        self.call()
                self.assertEqual(caught.exception.detail, f'Codex 結束碼 1：{expected}')
                self.assertEqual(caught.exception.evidence['stdout'], stdout)
                self.assertEqual(caught.exception.evidence['stderr'], stderr)
                process.assert_called_once()

    def test_real_stdio_mcp_initializes_reads_and_records(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "source.txt").write_text("single source\n", encoding="utf-8")
            audit = root / "audit.jsonl"
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_file", "arguments": {"path": "source.txt"}}},
            ]
            result = subprocess.run([os.sys.executable, str(ROOT / "masters_nudge/read_only_repo_mcp.py"),
                                     "--root", str(root), "--budget", "1000", "--audit", str(audit)],
                                     input="\n".join(json.dumps(message) for message in messages) + "\n",
                                     text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            replies = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual({tool["name"] for tool in replies[1]["result"]["tools"]}, {"search_repo", "read_file"})
            self.assertIn("single source", replies[2]["result"]["content"][0]["text"])
            self.assertEqual(len(audit.read_text().splitlines()), 2)
