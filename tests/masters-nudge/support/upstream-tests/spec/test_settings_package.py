import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import masters_nudge_cli
from masters_nudge.runtime import HOOK_TIMEOUT_SEC, PROVIDER_TIMEOUT_SEC, RuntimePaths, RuntimeSettings
from masters_nudge.settings import load_user_settings, save_provider

ROOT = Path(__file__).resolve().parents[2]


class SettingsPackageTests(unittest.TestCase):
    def test_delivery_receipt_failure_does_not_emit_a_second_response(self):
        import hook_entry
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for strict in (False, True):
                settings = RuntimeSettings("openai", "model", RuntimePaths(ROOT, root, root, root / "error.log"), strict=strict)
                core = Mock()
                core.journal.delivered.side_effect = sqlite3.OperationalError("disk I/O error")
                response = {"hookSpecificOutput": {
                                "hookEventName": "PostToolUse",
                                "additionalContext": "feedback",
                            },
                             "_masters_nudge": "attempt-1"}
                output = io.StringIO()
                with patch.object(sys, "argv", ["hook_entry.py"]), \
                     patch.object(sys, "stdin", Mock(buffer=io.BytesIO(b"{}"))), \
                     patch.object(hook_entry, "active_guard", return_value=False), \
                     patch.object(hook_entry.RuntimeSettings, "from_env", return_value=settings), \
                     patch.object(hook_entry, "NudgeCore", return_value=core), \
                     patch.object(hook_entry.CodexAdapter, "process", return_value=response), redirect_stdout(output):
                    code = hook_entry.main()
                self.assertEqual(code, int(strict))
                self.assertEqual(len(output.getvalue().splitlines()), 1)
                self.assertEqual(
                    json.loads(output.getvalue())["hookSpecificOutput"]["additionalContext"],
                    "feedback",
                )
                self.assertNotIn("systemMessage", json.loads(output.getvalue()))
                self.assertIn("disk I/O error", settings.paths.error_log.read_text(encoding="utf-8"))

    def test_unwritable_journal_still_returns_fault_status(self):
        with tempfile.TemporaryDirectory() as raw:
            data_path = Path(raw) / "not-a-directory"
            data_path.write_text("occupied", encoding="utf-8")
            event = {"hook_event_name": "UserPromptSubmit", "session_id": "s", "turn_id": "t",
                     "cwd": raw, "prompt": "task", "transcript_path": None}
            for strict in ("0", "1"):
                env = {**os.environ, "MASTERS_NUDGE_ACTIVE": "0", "MASTERS_NUDGE_TEST_MODE": strict,
                       "MASTERS_NUDGE_DATA_DIR": str(data_path), "MASTERS_NUDGE_RUNTIME_DIR": str(ROOT)}
                result = subprocess.run([sys.executable, str(ROOT / "hook_entry.py")], input=json.dumps(event),
                                        capture_output=True, text=True, env=env, timeout=10)
                self.assertEqual(result.returncode, int(strict), result.stderr)
                response = json.loads(result.stdout)
                self.assertEqual(response["systemMessage"], "本輪反饋未執行")
                self.assertNotIn("hookSpecificOutput", response)
                self.assertTrue(result.stderr)

    def test_packaged_transport_initializes_from_relocated_package(self):
        from masters_nudge.management import _probe_mcp
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "中文 package with spaces"
            shutil.copytree(ROOT / "plugins/masters-nudge", package,
                            ignore=shutil.ignore_patterns("__pycache__"))
            server = json.loads((package / ".mcp.json").read_text())["mcpServers"]["masters_nudge"]
            transport = {**server, "type": "stdio", "cwd": str(package / server["cwd"])}
            env = {**os.environ, "MASTERS_NUDGE_DATA_DIR": str(Path(raw) / "data"),
                   "MASTERS_NUDGE_ACTIVE": "0", "PYTHONPATH": ""}
            self.assertTrue(_probe_mcp(transport, env))
            from masters_nudge.storage import recent_nudges
            self.assertEqual(recent_nudges(Path(raw) / "data"), [])
            event = {"hook_event_name": "UserPromptSubmit", "session_id": "s", "turn_id": "t",
                     "cwd": raw, "prompt": "task", "transcript_path": None}
            result = subprocess.run([server["command"], str(package / "launch.py"), "hook",
                                     "--host", "codex_cli"], cwd=raw, env=env,
                                    input=json.dumps(event), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_doctor_does_not_label_inventory_timeout_as_disabled(self):
        from masters_nudge.management import doctor
        with patch("masters_nudge.management._run_cli", side_effect=subprocess.TimeoutExpired("codex", 10)):
            result = doctor(ROOT, environ={**os.environ, "CODEX_CLI_PATH": sys.executable})
        self.assertIsNone(result["plugin_enabled"])
        self.assertIsNone(result["mcp_ready"])
        self.assertFalse(result["core_ready"])

    def test_probe_inherits_host_baseline_and_explicit_overrides_only(self):
        from masters_nudge.management import _probe_mcp
        replies = '\n'.join(json.dumps(row) for row in [
            {"id": 1, "result": {"serverInfo": {"name": "masters-nudge"}}},
            {"id": 2, "result": {"tools": [{"name": "review_patch"}]}},
        ])
        transport = {"type": "stdio", "command": "python", "env_vars": ["CUSTOM"],
                     "env": {"CUSTOM": "override"}}
        env = {"SystemRoot": "C:\\Windows", "PATH": "python-dir", "HOME": "/home/user",
               "CUSTOM": "inherited", "UNRELATED_SECRET": "must not inherit"}
        with patch("masters_nudge.management.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 0, replies, "")) as run:
            self.assertTrue(_probe_mcp(transport, env))
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(child_env["PATH"], "python-dir")
        self.assertEqual(child_env["CUSTOM"], "override")
        self.assertNotIn("UNRELATED_SECRET", child_env)
        if os.name == "nt":
            self.assertEqual(child_env["SYSTEMROOT"], "C:\\Windows")
        else:
            self.assertEqual(child_env["HOME"], "/home/user")

    def test_doctor_exposes_enabled_but_broken_transport(self):
        from masters_nudge.management import doctor
        transport = {"type": "stdio", "command": "python", "args": ["${PLUGIN_ROOT}/mcp_entry.py"]}
        replies = [subprocess.CompletedProcess([], 0, "Logged in", ""),
                   subprocess.CompletedProcess([], 0, json.dumps({"installed": [
                       {"pluginId": "masters-nudge@masters-nudge", "enabled": True}]}), ""),
                   subprocess.CompletedProcess([], 0, json.dumps([
                       {"name": "masters_nudge", "enabled": True, "transport": transport}]), "")]
        with patch("masters_nudge.management._run_cli", side_effect=replies), \
             patch("masters_nudge.management._probe_mcp", side_effect=ValueError("cannot open file")):
            result = doctor(ROOT, environ={**os.environ, "CODEX_CLI_PATH": sys.executable})
        self.assertTrue(result["plugin_enabled"])
        self.assertFalse(result["mcp_ready"])
        self.assertFalse(result["core_ready"])
        self.assertIn("cannot open file", result["error"])

    def test_settings_outside_data_and_only_supported_choice(self):
        with tempfile.TemporaryDirectory() as raw:
            paths = RuntimePaths.resolve(environ={"USERPROFILE": raw})
            self.assertNotEqual(paths.settings_dir, paths.data_dir)
            save_provider(paths.settings_dir, "openai", model="chosen")
            self.assertEqual(load_user_settings(paths.settings_dir).model, "chosen")
            self.assertEqual(set(json.loads((paths.settings_dir / "config.json").read_text())), {"provider", "model"})
            for provider in ("anthropic", "ollama"):
                with self.assertRaises(ValueError):
                    save_provider(paths.settings_dir, provider)

    def test_old_supported_setting_is_preserved_but_unsupported_never_falls_back(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "config.json"
            path.write_text(json.dumps({"provider": "openai", "model": "chosen", "ollama_url": "old", "lens": "old"}))
            settings = RuntimeSettings.from_env(environ={"MASTERS_NUDGE_DATA_DIR": raw,
                                 "MASTERS_NUDGE_MODEL": "ignored", "MASTERS_NUDGE_PROVIDER": "ignored"})
            self.assertEqual(settings.model, "chosen")
            self.assertFalse(settings.configuration_error)
            path.write_text('{"provider":"ollama","model":"chosen"}')
            self.assertTrue(RuntimeSettings.from_env(environ={"MASTERS_NUDGE_DATA_DIR": raw}).configuration_error)

    def test_cli_lists_only_openai_and_rejects_removed_commands(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["nudge", "provider", "list"]), redirect_stdout(output):
            self.assertEqual(masters_nudge_cli.main(), 0)
        self.assertEqual([p["id"] for p in json.loads(output.getvalue())["providers"]], ["openai"])
        for argv in (["nudge", "provider", "set", "ollama"], ["nudge", "lens"]):
            with patch.object(sys, "argv", argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                masters_nudge_cli.main()

    def test_post_tool_hook_calls_the_persistent_mcp_synchronously(self):
        manifest = json.loads((ROOT / "plugins/masters-nudge/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["mcpServers"], "./.mcp.json")
        hooks = json.loads((ROOT / "plugins/masters-nudge/hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertEqual(set(hooks), {"UserPromptSubmit", "PostToolUse"})
        hook = hooks["PostToolUse"][0]["hooks"][0]
        self.assertEqual(hook["type"], "mcp_tool")
        self.assertEqual((hook["server"], hook["tool"]), ("masters_nudge", "review_patch"))
        self.assertNotIn("async", hook)
        self.assertGreater(hook["timeout"], PROVIDER_TIMEOUT_SEC)
        self.assertEqual(hook["timeout"], HOOK_TIMEOUT_SEC)
        self.assertEqual(set(hook["input"]), {
            "hook_event_name", "session_id", "turn_id", "cwd", "transcript_path",
            "tool_name", "tool_use_id", "tool_input", "tool_response",
        })
        user_hook = hooks["UserPromptSubmit"][0]["hooks"][0]
        self.assertEqual(user_hook["type"], "command")

    def test_clean_package_starts_hook_and_reports_fault_without_actor_context(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = root / "package"
            shutil.copytree(ROOT / "plugins/masters-nudge", package, ignore=shutil.ignore_patterns("__pycache__"))
            env = {**os.environ, "MASTERS_NUDGE_DATA_DIR": str(root / "data"), "MASTERS_NUDGE_ACTIVE": "0",
                   "MASTERS_NUDGE_RUNTIME_DIR": str(package), "PYTHONPATH": ""}
            event = {"hook_event_name": "UserPromptSubmit", "session_id": "s", "turn_id": "t",
                     "cwd": str(root), "prompt": "task", "transcript_path": None}
            command = [sys.executable, str(package / "hook_entry.py")]
            result = subprocess.run(command, input=json.dumps(event), capture_output=True, text=True, env=env, cwd=root, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            bad = dict(event, hook_event_name="PostToolUse", tool_name="apply_patch")
            for strict in ("0", "1"):
                result = subprocess.run(command, input=json.dumps(bad), capture_output=True, text=True,
                                        env={**env, "MASTERS_NUDGE_TEST_MODE": strict}, cwd=root, timeout=10)
                output = json.loads(result.stdout)
                self.assertIn("本輪反饋未執行", output["systemMessage"])
                self.assertNotIn("hookSpecificOutput", output)
                self.assertEqual(result.returncode, int(strict))
