#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Pure CI wiring tests. Synthetic files and mocked CLI results; never starts Java."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/openpnp-ci-qualification.py"
spec = importlib.util.spec_from_file_location("ci_qualification", SCRIPT)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n" if isinstance(value, (dict, list)) else value)
    return path


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.build = self.root / "validation/native/build"
        self.native = self.root / "validation/native/native-tests"
        self.controller = self.root / "validation/controller"
        self.source = "src/openpnp/java/org/openpnp/codex/Bridge.java"
        self.test = "tests/openpnp/native/NativeControllerCrashRecoveryTest.java"
        for name in (self.source, self.test):
            write(self.root / name, "synthetic fixture")
        write(self.root / "package.json", {})
        write(self.root / "scripts/openpnp-script.py", "# fixture")
        write(self.root / "plugins/openpnp/bridge/build-manifest.json", {"historical": True})
        write(self.root / "plugins/openpnp/bridge/openpnp-codex-bridge.jar", "old bridge")
        write(self.root / "plugins/openpnp/mcp/server.mjs", "synthetic MCP")
        self.meta = self.root / "plugins/openpnp/assets/evaluations/job-edit-tool-contracts.json"
        write(self.meta, {"old_evaluation": {"passed": True, "bridge": "old"}, "candidate_packaged_mcp": {"old": True}})
        native = write(self.build / "runtime/native.jar", "synthetic native")
        rm = {"upstream_commit": ci.PIN, "gui_jar": "native.jar", "files": [{"path": "native.jar", "sha256": ci.sha(native)}]}
        manifest = write(self.build / "runtime/codex-build-manifest.json", rm)
        bridge = write(self.build / "openpnp-codex-bridge.jar", "synthetic bridge")
        self.bm = {"upstream_commit": ci.PIN, "bridge_sha256": ci.sha(bridge),
                   "runtime_manifest_sha256": ci.sha(manifest), "patched_native_jar_sha256": ci.sha(native),
                   "production_source_sha256": {self.source: ci.sha(self.root / self.source)},
                   "test_source_sha256": {self.test: ci.sha(self.root / self.test)}}
        write(self.build / "build-manifest.json", self.bm)
        for name in ("NativeMappedAxisFixtureMain.class", "NativeMappedAxisFixtureMain$1.class", "Bridge.class"):
            write(self.build / "test-classes/org/openpnp/codex" / name, "fixture class")
        self.stage = self.root / "validation/staged"

    def native_fixtures(self):
        ids = {"operation_id": "operation", "request_id": "request", "controller_instance_id": "controller"}
        journal = (json.dumps({"sequence": 1, "type": "operation", "payload": {
            "state": "succeeded", "native_completion": {"native_wrapper_completed": True}}}) + "\n").encode()
        success = self.controller / "native-state/journal/operations.jsonl"
        write(success, journal.decode())
        write(self.controller / "report.json", {"passed": True, "bridge_sha256": self.bm["bridge_sha256"],
              "mcp_sha256": ci.sha(self.root / "plugins/openpnp/mcp/server.mjs"), "launcher_exit": {"code": 0, "signal": None},
              "forced_cleanup": False, "operation": ids, "request": ids})
        log = write(self.native / "NativeControllerCrashRecoveryTest.log", "synthetic native test output")
        write(self.native / "receipt.json", {"bridge_sha256": self.bm["bridge_sha256"],
              "runtime_manifest_sha256": self.bm["runtime_manifest_sha256"], "tests": [{"main": "NativeControllerCrashRecoveryTest",
              "exit_code": 0, "source_sha256": ci.sha(self.root / self.test), "log_sha256": ci.sha(log)}]})
        self.crash = self.native / "NativeControllerCrashRecoveryTest-tmp/openpnp-controller-crash-fresh"
        rows = []
        for mode in ci.MODES:
            producer = write(self.crash / mode / "producer/journal/operations.jsonl", journal.decode())
            recovery = write(self.crash / mode / "recovery-state/journal/operations.jsonl", journal.decode())
            proof = {"bridge_artifact_sha256": self.bm["bridge_sha256"], "journal_sha256": ci.sha(producer),
                     **{"original_" + key: value for key, value in ids.items()}}
            observed = {"passed": True, "bridge_artifact_sha256": self.bm["bridge_sha256"], "controller_connections": 0,
                        "controller_bytes": 0, "journal_after_sha256": ci.sha(recovery)}
            rows.append({"mode": mode, "passed": True, "crash_proof": proof,
                         "recoveries": [{"observation": observed}, {"observation": observed}]})
        write(self.crash / "report.json", {"passed": True, "suite": "NativeControllerCrashRecoveryTest", "cases": rows})

    def result(self, case):
        success = case["state"] == "succeeded"
        return {"schema_version": 1, "mode": "offline-recorded-controller-history", "valid": True,
                **{key: False for key in ci.AUTHORITY}, "input_unchanged": True, "shared_read_lock_held_during_scan": True,
                "journal_sha256": ci.sha(case["file"]), "journal_bytes": case["file"].stat().st_size,
                "provenance": {"bridge_sha256": self.bm["bridge_sha256"]},
                "cli_provenance": {"runtime_manifest_sha256": self.bm["runtime_manifest_sha256"]},
                "inspector_process": {"reaped": True}, "recorded_bytes_prove_force_or_power_loss_survival": False,
                "history": {**{key: case[key] for key in ("operation_id", "request_id", "controller_instance_id")},
                    "recorded_operation_state": case["state"], "offline_disposition": "recorded-success" if success else "outcome-unknown",
                    "requires_reconciliation": not success, "complete_recipe_recorded": success, "wrapper_success_recorded": success}}

    def test_stages_exact_new_bridge_keeps_old_evaluations_and_scopes_fixture(self):
        old = self.meta.read_bytes()
        write(self.root / "src/openpnp/node/node_modules/private", "must not copy")
        receipt = ci.stage(self.root, self.build, self.stage)
        self.assertFalse(receipt["qualification_performed"])
        self.assertEqual(ci.sha(self.stage / "plugins/openpnp/bridge/openpnp-codex-bridge.jar"), self.bm["bridge_sha256"])
        metadata = ci.read(self.stage / self.meta.relative_to(self.root))
        self.assertEqual(metadata["old_evaluation"], json.loads(old)["old_evaluation"])
        self.assertFalse(metadata["candidate_packaged_mcp"]["historical_receipts_apply_to_this_build"])
        self.assertEqual(self.meta.read_bytes(), old)
        self.assertFalse((self.stage / "src/openpnp/node/node_modules").exists())
        self.assertEqual(len(list((self.stage / "validation/mapped-classes").rglob("*.class"))), 2)
        with self.assertRaises(ValueError):
            ci.stage(self.root, self.build, self.stage)

    def test_mismatched_build_runtime_source_and_pin_fail_before_stage(self):
        files = [self.build / "openpnp-codex-bridge.jar", self.build / "runtime/native.jar", self.root / self.source]
        for file in files:
            with self.subTest(file=file):
                original = file.read_bytes()
                file.write_bytes(original + b" changed")
                with self.assertRaises(ValueError):
                    ci.stage(self.root, self.build, self.stage)
                self.assertFalse(self.stage.exists())
                file.write_bytes(original)
        self.bm["upstream_commit"] = "wrong"
        write(self.build / "build-manifest.json", self.bm)
        with self.assertRaises(ValueError):
            ci.stage(self.root, self.build, self.stage)

    def test_runtime_inventory_duplicate_escape_and_symlink_refused(self):
        manifest = self.build / "runtime/codex-build-manifest.json"
        original = ci.read(manifest)
        for rows in ([*original["files"], *original["files"]], [{"path": "../openpnp-codex-bridge.jar", "sha256": "x"}],
                     [{"path": "/etc/passwd", "sha256": "x"}]):
            with self.subTest(rows=rows):
                write(manifest, {**original, "files": rows})
                self.bm["runtime_manifest_sha256"] = ci.sha(manifest)
                write(self.build / "build-manifest.json", self.bm)
                with self.assertRaises(ValueError):
                    ci.build_inputs(self.root, self.build)
        write(manifest, original)
        self.bm["runtime_manifest_sha256"] = ci.sha(manifest)
        write(self.build / "build-manifest.json", self.bm)
        native = self.build / "runtime/native.jar"
        native.unlink()
        native.symlink_to(self.build / "openpnp-codex-bridge.jar")
        with self.assertRaises(ValueError):
            ci.build_inputs(self.root, self.build)

    def test_stage_destination_escape_and_source_symlink_refused(self):
        with self.assertRaises(ValueError):
            ci.stage(self.root, self.build, self.root / "outside")
        (self.root / "plugins/openpnp/alias").symlink_to(self.meta)
        with self.assertRaises(ValueError):
            ci.stage(self.root, self.build, self.stage)

    def test_seven_fresh_cases_require_complete_native_artifact_proof(self):
        self.native_fixtures()
        cases = ci.history_cases(self.root, self.native, self.controller, self.bm)
        self.assertEqual(len(cases), 7)
        self.assertEqual([c["state"] for c in cases], ["succeeded", "running", "outcome_unknown", "running", "outcome_unknown", "succeeded", "succeeded"])
        proof = ci.read(self.crash / "report.json")
        proof["cases"].pop()
        write(self.crash / "report.json", proof)
        with self.assertRaises(ValueError):
            ci.history_cases(self.root, self.native, self.controller, self.bm)

    def test_legacy_shared_temp_directory_is_not_reused(self):
        self.native_fixtures()
        legacy = self.native / "tmp" / self.crash.name
        legacy.parent.mkdir()
        self.crash.rename(legacy)
        with self.assertRaisesRegex(ValueError, "exactly one fresh"):
            ci.history_cases(self.root, self.native, self.controller, self.bm)

    def test_symbolic_controller_temporary_directory_is_refused(self):
        self.native_fixtures()
        temporary = self.crash.parent
        moved = self.native / "moved-controller-fixture"
        temporary.rename(moved)
        temporary.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "canonical controller"):
            ci.history_cases(self.root, self.native, self.controller, self.bm)

    def test_ambiguous_fixture_does_not_choose_latest(self):
        self.native_fixtures()
        (self.native / "NativeControllerCrashRecoveryTest-tmp/openpnp-controller-crash-older").mkdir()
        with self.assertRaises(ValueError):
            ci.history_cases(self.root, self.native, self.controller, self.bm)

    def test_wrong_artifact_failed_native_test_or_modified_journal_refused(self):
        self.native_fixtures()
        receipt_path = self.native / "receipt.json"
        original = ci.read(receipt_path)
        for change in ("bridge", "runtime", "exit", "source", "log"):
            edited = copy.deepcopy(original)
            if change in ("bridge", "runtime"):
                edited["bridge_sha256" if change == "bridge" else "runtime_manifest_sha256"] = "wrong"
            else:
                edited["tests"][0][{"exit": "exit_code", "source": "source_sha256", "log": "log_sha256"}[change]] = "wrong"
            write(receipt_path, edited)
            with self.subTest(change=change), self.assertRaises(ValueError):
                ci.history_cases(self.root, self.native, self.controller, self.bm)
        write(receipt_path, original)
        write(self.crash / ci.MODES[0] / "producer/journal/operations.jsonl", "changed")
        with self.assertRaises(ValueError):
            ci.history_cases(self.root, self.native, self.controller, self.bm)

    def test_disposition_identity_authority_and_provenance_must_all_match(self):
        self.native_fixtures()
        for case in ci.history_cases(self.root, self.native, self.controller, self.bm):
            ci.check_history(self.result(case), case, self.bm)
        case = ci.history_cases(self.root, self.native, self.controller, self.bm)[2]
        for branch, key, value in [("history", "requires_reconciliation", False), ("history", "operation_id", "foreign"),
            ("history", "recorded_operation_state", "succeeded"), ("provenance", "bridge_sha256", "old"),
            ("cli_provenance", "runtime_manifest_sha256", "old"), ("inspector_process", "reaped", False),
            (None, "native_authority_restored", True), (None, "input_unchanged", False), (None, "journal_sha256", "old")]:
            result = self.result(case)
            (result[branch] if branch else result)[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                ci.check_history(result, case, self.bm)

    def test_history_cli_plan_and_refusals_without_native_execution(self):
        self.native_fixtures()
        cases = {c["name"]: c for c in ci.history_cases(self.root, self.native, self.controller, self.bm)}
        calls = []
        def fake(node, cli, arguments, output, name):
            calls.append(arguments)
            if name == "install":
                write(output / "installed/installation.json", {"synthetic": True})
                return 0, {"installed": True}
            self.assertEqual(arguments[0], "inspect-controller-history")
            self.assertEqual(set(arguments[1::2]), {"--state-dir", "--openpnp-home", "--java", "--journal"})
            if name in cases:
                return 0, self.result(cases[name])
            return 2, {"valid": False, "mode": "offline-recorded-controller-history", **{k: False for k in ci.AUTHORITY},
                       "inspector_process": {"reaped": True}, "error_code": "SYNTHETIC_REFUSAL"}
        output = self.root / "validation/history"
        with patch.object(ci, "invoke", side_effect=fake):
            report = ci.history(self.root, self.build, self.native, self.controller, output, Path("node"), Path("java"))
        self.assertTrue(report["passed"])
        self.assertEqual(len(calls), 11)
        self.assertEqual(len(report["cases"]), 7)
        self.assertEqual(len(report["refusals"]), 3)
        self.assertTrue(report["inputs_unchanged"])

    def test_first_cli_failure_retained_and_not_retried(self):
        self.native_fixtures()
        output = self.root / "validation/failure"
        with patch.object(ci, "invoke", return_value=(1, {"installed": False})) as invoke:
            with self.assertRaises(ValueError):
                ci.history(self.root, self.build, self.native, self.controller, output, Path("node"), Path("java"))
            self.assertEqual(invoke.call_count, 1)
        report = ci.read(output / "report.json")
        self.assertFalse(report["passed"])
        self.assertTrue(report["inputs_unchanged"])
        self.assertIn("install failed", report["failure"])

    def test_shell_command_wiring_and_first_failure_without_process_qualification(self):
        script = SCRIPT.with_suffix(".sh")
        for failure in ("none", "stage", "mcp"):
            with self.subTest(failure=failure):
                root = self.root / ("shell fixture " + failure)
                target = root / "scripts/openpnp-ci-qualification.sh"
                target.parent.mkdir(parents=True)
                shutil.copyfile(script, target)
                binaries = root / "fake-bin"
                binaries.mkdir()
                java = write(root / "jdk/bin/java", "#!/bin/sh\nexit 99\n")
                java.chmod(0o755)
                shutil.copyfile(java, java.with_name("javac"))
                java.with_name("javac").chmod(0o755)
                stub = write(binaries / "stub", "#!" + sys.executable + "\n" + '''
import json,os,sys
from pathlib import Path
name=Path(sys.argv[0]).name; args=sys.argv[1:]
with open(os.environ['CI_WIRING_CALLS'],'a') as out:out.write(json.dumps([name,*args])+'\\n')
if name=='node' and args[:1]==['-p']:print('22.19.0');sys.exit(0)
if name=='python3' and args[1:2]==['stage']:
 if os.environ['CI_WIRING_FAILURE']=='stage':sys.exit(37)
 stage=Path(args[args.index('--output')+1])
 (stage/'validation').mkdir(parents=True)
 (stage/'plugins/openpnp/controller-history').mkdir(parents=True)
if name=='python3' and args[0].endswith('openpnp-build-controller-history.py'):
 package=Path(args[args.index('--output')+1])/'package';package.mkdir(parents=True)
 (package/'synthetic-marker').write_text('not a Java artifact')
if name=='python3' and args[0].endswith('openpnp-test-native-mcp.py') and os.environ['CI_WIRING_FAILURE']=='mcp':sys.exit(38)
''')
                stub.chmod(0o755)
                for name in ("python3", "node", "npm"):
                    (binaries / name).symlink_to(stub)
                call_file = root / "calls.jsonl"
                env = {**os.environ, "PATH": str(binaries) + ":/usr/bin:/bin", "JAVA_HOME": str(root / "jdk"),
                       "CI_WIRING_CALLS": str(call_file), "CI_WIRING_FAILURE": failure}
                result = subprocess.run(["/bin/bash", str(target)], env=env, capture_output=True, timeout=10)
                calls = [json.loads(line) for line in call_file.read_text().splitlines()]
                self.assertEqual(result.returncode, {"none": 0, "stage": 37, "mcp": 38}[failure], result.stderr)
                staged = [c for c in calls if len(c) > 2 and c[0] == "python3" and c[2] == "stage"]
                self.assertEqual(len(staged), 1)
                native_calls = [c for c in calls if any("test-native-mcp.py" in arg for arg in c)]
                history_calls = [c for c in calls if len(c) > 2 and c[2] == "history"]
                controller_calls = [c for c in calls if any("test-controller.mjs" in arg for arg in c)]
                if failure != "stage":
                    self.assertEqual(len(native_calls), 1)
                    call = native_calls[0]
                    self.assertEqual(call[call.index("--suite") + 1], "all")
                    self.assertEqual(call[call.index("--runtime") + 1], str(root / "validation/ci-native/build/runtime"))
                    self.assertEqual(call[call.index("--mapped-test-classes") + 1], str(root / "validation/ci-packaged/validation/mapped-classes"))
                self.assertEqual(len(controller_calls), 1 if failure == "none" else 0)
                self.assertEqual(len(history_calls), 1 if failure == "none" else 0)
                if history_calls:
                    call = history_calls[0]
                    self.assertEqual(call[call.index("--native-tests") + 1], str(root / "validation/ci-native/native-tests"))


if __name__ == "__main__":
    unittest.main()
