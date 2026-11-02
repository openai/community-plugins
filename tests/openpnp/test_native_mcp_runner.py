#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Controlled POSIX runner regressions. Fake CLI fixtures are never OpenPnP qualification."""
import hashlib
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

RUNNER = Path(os.environ.get("OPENPNP_RUNNER_UNDER_TEST", Path(__file__).resolve().parents[2] / "scripts/openpnp-test-native-mcp.py")).resolve()


def load_runner(path=RUNNER):
    spec = importlib.util.spec_from_file_location("native_mcp_runner_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wait_for(predicate, seconds=8):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Controlled fixture did not reach its bounded checkpoint")
        time.sleep(0.01)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def reap_if_adopted(pid):
    try: os.waitpid(pid, os.WNOHANG)
    except ChildProcessError: pass
    return not alive(pid)


def json_write(file, value):
    file.write_text(json.dumps(value, indent=2) + "\n")


FAKE_NODE = r'''
import json, os, signal, sys, time
from pathlib import Path
args=sys.argv[1:]
mode=os.environ.get('RUNNER_FIXTURE_MODE','normal')
def stopped(signum, frame): sys.exit(0)
signal.signal(signal.SIGTERM,stopped)
if '--test' in args:
    name, evidence_key, connection_key=next((n,e,c) for n,e,c in [
        ('core','OPENPNP_E2E_EVIDENCE_DIR','OPENPNP_E2E_CONNECTION_FILE'),
        ('vision','OPENPNP_VISION_E2E_EVIDENCE_DIR','OPENPNP_VISION_E2E_CONNECTION_FILE'),
        ('placement','OPENPNP_JOB_EDIT_EVIDENCE_DIR','OPENPNP_JOB_EDIT_CONNECTION_FILE'),
        ('topology','OPENPNP_TOPOLOGY_E2E_EVIDENCE_DIR','OPENPNP_TOPOLOGY_E2E_CONNECTION_FILE'),
        ('camera-scale','OPENPNP_CAMERA_SCALE_E2E_EVIDENCE_DIR','OPENPNP_CAMERA_SCALE_E2E_CONNECTION_FILE'),
        ('backlash','OPENPNP_BACKLASH_E2E_EVIDENCE_DIR','OPENPNP_BACKLASH_E2E_CONNECTION_FILE')]
        if e in os.environ)
    state=Path(os.environ[connection_key]).parent
    (state/'test.pid').write_text(str(os.getpid()))
    (state/'test-ready').write_text('controlled fixture; no MCP or Java started')
    (state/'test-invocation.json').write_text(json.dumps({'argv':args,'suite':name,'connection_key':connection_key,'connection_file':os.environ[connection_key],'evidence_key':evidence_key,'evidence_dir':os.environ[evidence_key],'native_execution':False}))
    if mode=='wait-test':
        while True: signal.pause()
    out=Path(os.environ[evidence_key]);out.mkdir(parents=True)
    file={'camera-scale':'mcp-native-camera-scale.json','core':'mcp-native-e2e.json','vision':'mcp-native-vision.json','placement':'mcp-placement-loads.json','backlash':'mcp-native-axis-backlash.json','topology':'mcp-native-topology.json'}[name]
    if mode=='wrong-receipt': file='unrelated-success-receipt.json'
    (out/file).write_text(json.dumps({'passed':True,'fixture_only':True,'native_execution':False}))
    print('# tests '+('2' if mode=='skip' else '1')+'\n# pass 1\n# fail 0\n# skipped '+('1' if mode=='skip' else '0')+'\n# todo 0')
else:
    command=args[1];state=Path(args[args.index('--state-dir')+1])
    if command=='install-bridge': state.mkdir(parents=True)
    elif command=='doctor': print(json.dumps({'ok':True,'checks':[{'check':'bridge','capabilities':{'simulation':True,'hardware_qualified':False,'fixture_only':True}}]}))
    elif command=='start-simulator':
        (state/'launcher.pid').write_text(str(os.getpid()))
        (state/'connection.json').write_text('{}')
        if mode=='interrupt-cleanup':
            def defer(signum, frame): (state/'launcher-term').write_text('cleanup checkpoint')
            signal.signal(signal.SIGTERM,defer)
        while True: signal.pause()
    else: raise AssertionError(command)
'''


@unittest.skipUnless(os.name == "posix", "Controlled process-group regression requires POSIX")
class NativeMcpRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reap deliberately orphaned fixture children even in Linux containers
        # whose PID1 does not reap them. This affects only the test process.
        cls.subreaper = sys.platform.startswith("linux")
        if cls.subreaper:
            cls.libc = ctypes.CDLL(None, use_errno=True)
            if cls.libc.prctl(36, 1, 0, 0, 0) != 0: raise OSError(ctypes.get_errno(), "Cannot adopt controlled fixture children")
        selected = os.environ.get("OPENPNP_RUNNER_TEST_EVIDENCE_DIR")
        cls.evidence = Path(selected if selected else tempfile.mkdtemp(prefix="openpnp-runner-review-")).resolve()
        cls.evidence.mkdir(parents=True, exist_ok=True)
        cls.runner = load_runner()
        json_write(cls.evidence / "scope.json", {
            "scope": "Controlled subprocess plumbing only; fake CLI/SDK/JDK files are fixtures, never native or physical qualification.",
            "runner_sha256": hashlib.sha256(RUNNER.read_bytes()).hexdigest(),
            "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "python": sys.version, "native_execution": False, "hardware_qualified": False,
        })

    @classmethod
    def tearDownClass(cls):
        if cls.subreaper: cls.libc.prctl(36, 0, 0, 0, 0)

    def setUp(self):
        self.directory = self.evidence / self._testMethodName
        self.directory.mkdir()
        self.children = []
        self.pid_files = []

    def tearDown(self):
        emergency = []
        for process in self.children:
            if process.poll() is None:
                emergency.append({"pid": process.pid, "kind": "test-owned-parent"})
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=5)
        for file in self.pid_files:
            if not file.exists(): continue
            pid = int(file.read_text())
            if alive(pid):
                emergency.append({"pid": pid, "kind": "fixture-recorded-child"})
                try: os.kill(pid, signal.SIGKILL)
                except ProcessLookupError: pass
                wait_for(lambda: reap_if_adopted(pid), 8)
        json_write(self.directory / "test-cleanup.json", {"emergency_cleanup": emergency, "no_leftover_processes": True, "native_execution": False})

    def fixture(self):
        root = (self.directory / "fixture").resolve()
        for directory in ["plugins/openpnp/scripts", "src/openpnp/node/node_modules", "tests/openpnp", "scripts", "fake-jdk/bin", "fake-jdk/lib", "runtime"]:
            (root / directory).mkdir(parents=True, exist_ok=True)
        (root / "scripts/openpnp-read-response.mjs").write_text("// Fixture only\n")
        (root / "plugins/openpnp/scripts/openpnp.mjs").write_text("// Never executed; fake CLI plumbing fixture only\n")
        (root / "src/openpnp/node/node_modules/fixture.txt").write_text("No SDK execution\n")
        (root / "fake-jdk/release").write_text("Fixture only, no JDK\n")
        (root / "fake-jdk/lib/modules").write_text("Fixture module bytes\n")
        (root / "fake-node.py").write_text(FAKE_NODE)
        node = root / "fake-node"
        node.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(root / "fake-node.py")) + ' "$@"\n')
        node.chmod(0o700)
        java = root / "fake-jdk/bin/java"
        java.write_text("#!/bin/sh\nexit 99\n")
        java.chmod(0o700)
        asset = root / "runtime/asset.txt"; asset.write_text("Verified fixture asset, no OpenPnP\n")
        json_write(root / "runtime/codex-build-manifest.json", {"files": [{"path": "asset.txt", "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()}]})
        return root, node, java

    def start_main(self, mode, suites="all"):
        root, _, _ = self.fixture()
        env = self.runner.clean_environment(); env["RUNNER_FIXTURE_MODE"] = mode
        log = (self.directory / "runner.log").open("wb")
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--fixture-runner", str(RUNNER), str(root), suites], env=env,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        log.close(); self.children.append(process)
        first = "core" if suites == "all" else suites
        state = root / "validation/result" / first / "state"
        self.pid_files.extend([state / "launcher.pid", state / "test.pid"])
        return process, root, state

    def test_environment_scrubs_loader_and_runtime_injection(self):
        values = {"PATH": "/fixture/bin", "LANG": "C", "OPENPNP_CONNECTION_FILE": "stale", "NODE_OPTIONS": "injected", "JAVA_TOOL_OPTIONS": "injected",
                  "JDK_JAVA_OPTIONS": "injected", "_JAVA_OPTIONS": "injected", "CLASSPATH": "injected", "LD_PRELOAD": "injected", "LD_LIBRARY_PATH": "injected", "DYLD_INSERT_LIBRARIES": "injected"}
        with patch.dict(os.environ, values, clear=True): actual = self.runner.clean_environment()
        self.assertEqual(actual, {"PATH": "/fixture/bin", "LANG": "C"})
        json_write(self.directory / "result.json", {"passed": True, "stripped_keys": sorted(set(values) - set(actual))})

    def test_inventory_binds_selected_jdk_modules_and_manifest(self):
        root, node, java = self.fixture()
        with patch.object(self.runner, "ROOT", root):
            before = self.runner.input_inventory(root / "runtime", node, java)
            self.assertIn("fake-jdk/lib/modules", before); self.assertIn("fake-jdk/release", before)
            (root / "fake-jdk/lib/modules").write_text("Changed fixture modules\n")
            after = self.runner.input_inventory(root / "runtime", node, java)
            self.assertNotEqual(before, after)
            (root / "runtime/asset.txt").write_text("Unlisted replacement bytes\n")
            with self.assertRaisesRegex(ValueError, "inventory mismatch"): self.runner.input_inventory(root / "runtime", node, java)
        json_write(self.directory / "result.json", {"passed": True, "before": before, "after": after})

    def test_mapped_fixture_inventory_rejects_production_overrides_and_symlinks(self):
        classes = self.directory / "classes"
        family = classes / "org/openpnp/codex"
        family.mkdir(parents=True)
        main = family / "NativeMappedAxisFixtureMain.class"
        main.write_bytes(b"test inventory fixture, not Java bytecode")
        before = self.runner.mapped_class_inventory(classes)
        main.write_bytes(b"changed fixture")
        self.assertNotEqual(before, self.runner.mapped_class_inventory(classes))
        override = family / "Bridge.class"
        override.write_bytes(b"unapproved override")
        with self.assertRaisesRegex(ValueError, "only the test launcher"):
            self.runner.mapped_class_inventory(classes)
        override.unlink()
        alias = family / "NativeMappedAxisFixtureMain$Alias.class"
        alias.symlink_to(main)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            self.runner.mapped_class_inventory(classes)
        alias.unlink(); main.unlink()
        with self.assertRaisesRegex(ValueError, "main class is missing"):
            self.runner.mapped_class_inventory(classes)
        json_write(self.directory / "result.json", {"passed": True, "native_execution": False,
            "rejected": ["production class override", "class symlink", "missing fixture main"], "changed_class_detected": True})

    def test_clean_stop_waits_for_owned_parent(self):
        ready = self.directory / "ready"
        code = "import signal,sys;from pathlib import Path;signal.signal(signal.SIGTERM,lambda s,f:sys.exit(0));Path(sys.argv[1]).write_text('ready');signal.pause()"
        process = subprocess.Popen([sys.executable, "-c", code, str(ready)], start_new_session=True)
        self.children.append(process); wait_for(ready.exists)
        result = self.runner.stop_owned_group(process, timeout=1, disappearance_timeout=1)
        json_write(self.directory / "result.json", result)
        self.assertEqual(result["parent_exit_code"], 0); self.assertTrue(self.runner.clean_stop(result)); self.assertFalse(alive(process.pid))

    def test_exited_parent_with_lingering_child_requires_forced_cleanup(self):
        pidfile = self.directory / "lingering.pid"; self.pid_files.append(pidfile)
        child = "import os,signal,sys;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(sys.argv[1]).write_text(str(os.getpid()));signal.pause()"
        parent = "import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);p=Path(sys.argv[2]);\nwhile not p.exists():time.sleep(.01)"
        process = subprocess.Popen([sys.executable, "-c", parent, child, str(pidfile)], start_new_session=True)
        self.children.append(process); process.wait(timeout=5); child_pid = int(pidfile.read_text()); self.assertTrue(alive(child_pid))
        reaper = None
        if self.subreaper:
            reaper = threading.Thread(target=lambda: os.waitpid(child_pid, 0), daemon=True)
            reaper.start()
        result = self.runner.stop_owned_group(process, timeout=1, disappearance_timeout=3)
        if reaper: reaper.join(timeout=5)
        json_write(self.directory / "result.json", result)
        self.assertEqual(result["parent_exit_code"], 0); self.assertTrue(result["forced_kill"]); self.assertTrue(result["group_gone"])
        self.assertFalse(self.runner.clean_stop(result)); self.assertFalse(alive(child_pid))

    def test_skipped_live_test_cannot_pass_with_success_evidence(self):
        process, root, _ = self.start_main("skip", "core")
        self.assertEqual(process.wait(timeout=15), 1)
        report = json.loads((root / "validation/result/report.json").read_text())
        suite = json.loads((root / "validation/result/core/report.json").read_text())
        self.assertFalse(report["passed"]); self.assertEqual(suite["test_counts"]["skipped"], 1)
        self.assertIn("no skipped", suite["error"]); self.assertTrue(self.runner.clean_stop(suite["launcher_cleanup"]))

    def test_backlash_routes_default_cli_and_exact_receipt(self):
        process, root, state = self.start_main("normal", "backlash")
        self.assertEqual(process.wait(timeout=15), 0)
        report = json.loads((root / "validation/result/report.json").read_text())
        suite = json.loads((root / "validation/result/backlash/report.json").read_text())
        invocation = json.loads((state / "test-invocation.json").read_text())
        self.assertTrue(report["passed"]); self.assertTrue(suite["passed"])
        self.assertEqual(len(report["suites"]), 1)
        self.assertEqual(suite["simulator_profile"], "native-simulator")
        self.assertEqual(suite["launcher"], "shipped start-simulator CLI")
        command = suite["launch_command"]
        self.assertIn("start-simulator", command)
        self.assertEqual(command[command.index("--profile") + 1], "native-simulator")
        self.assertNotIn("org.openpnp.codex.NativeMappedAxisFixtureMain", command)
        self.assertEqual(Path(suite["test_command"][-1]).name, "axis-backlash-live.test.mjs")
        self.assertEqual(invocation["connection_key"], "OPENPNP_BACKLASH_E2E_CONNECTION_FILE")
        self.assertEqual(invocation["evidence_key"], "OPENPNP_BACKLASH_E2E_EVIDENCE_DIR")
        self.assertEqual(Path(invocation["connection_file"]), state / "connection.json")
        evidence = root / "validation/result/backlash/evidence/mcp-native-axis-backlash.json"
        self.assertEqual(suite["evidence_sha256"][evidence.name], hashlib.sha256(evidence.read_bytes()).hexdigest())
        self.assertEqual(suite["test_counts"], {"tests": 1, "pass": 1, "fail": 0, "skipped": 0, "todo": 0})
        self.assertTrue(self.runner.clean_stop(suite["launcher_cleanup"]))
        self.assertTrue(all(not alive(int(p.read_text())) for p in self.pid_files if p.exists()))
        json_write(self.directory / "result.json", {"passed": True, "fixture_only": True, "native_execution": False,
            "scope": "Runner routing/receipt plumbing only; actual OpenPnP qualification is separately retained in mcp03."})

    def test_backlash_wrong_success_receipt_cannot_pass(self):
        process, root, state = self.start_main("wrong-receipt", "backlash")
        self.assertEqual(process.wait(timeout=15), 1)
        report = json.loads((root / "validation/result/report.json").read_text())
        suite = json.loads((root / "validation/result/backlash/report.json").read_text())
        self.assertFalse(report["passed"]); self.assertFalse(suite["passed"])
        self.assertEqual(suite["test_exit_code"], 0)
        self.assertIn("FileNotFoundError", suite["error"])
        self.assertIn("mcp-native-axis-backlash.json", suite["error"])
        wrong = root / "validation/result/backlash/evidence/unrelated-success-receipt.json"
        self.assertTrue(json.loads(wrong.read_text())["passed"])
        self.assertTrue(self.runner.clean_stop(suite["launcher_cleanup"]))
        self.assertTrue(all(not alive(int(p.read_text())) for p in self.pid_files if p.exists()))
        json_write(self.directory / "result.json", {"passed": True, "fixture_only": True, "native_execution": False,
            "scope": "Passing TAP and an unrelated successful receipt cannot substitute for the selected suite receipt."})

    def test_topology_routes_default_cli_and_exact_receipt(self):
        process, root, state = self.start_main("normal", "topology")
        self.assertEqual(process.wait(timeout=15), 0)
        report = json.loads((root / "validation/result/report.json").read_text())
        suite = json.loads((root / "validation/result/topology/report.json").read_text())
        invocation = json.loads((state / "test-invocation.json").read_text())
        self.assertTrue(report["passed"]); self.assertTrue(suite["passed"])
        self.assertEqual(len(report["suites"]), 1)
        self.assertEqual(suite["simulator_profile"], "native-simulator")
        self.assertEqual(suite["launcher"], "shipped start-simulator CLI")
        command = suite["launch_command"]
        self.assertIn("start-simulator", command)
        self.assertEqual(command[command.index("--profile") + 1], "native-simulator")
        self.assertNotIn("org.openpnp.codex.NativeMappedAxisFixtureMain", command)
        self.assertEqual(Path(suite["test_command"][-1]).name, "topology-live.test.mjs")
        self.assertEqual(invocation["connection_key"], "OPENPNP_TOPOLOGY_E2E_CONNECTION_FILE")
        self.assertEqual(invocation["evidence_key"], "OPENPNP_TOPOLOGY_E2E_EVIDENCE_DIR")
        self.assertEqual(Path(invocation["connection_file"]), state / "connection.json")
        evidence = root / "validation/result/topology/evidence/mcp-native-topology.json"
        self.assertEqual(suite["evidence_sha256"][evidence.name], hashlib.sha256(evidence.read_bytes()).hexdigest())
        self.assertEqual(suite["test_counts"], {"tests": 1, "pass": 1, "fail": 0, "skipped": 0, "todo": 0})
        self.assertTrue(self.runner.clean_stop(suite["launcher_cleanup"]))
        self.assertTrue(all(not alive(int(p.read_text())) for p in self.pid_files if p.exists()))
        json_write(self.directory / "result.json", {"passed": True, "fixture_only": True, "native_execution": False,
            "scope": "Runner routing/receipt plumbing only; actual OpenPnP qualification is separately retained in the topology MCP receipt."})

    def test_topology_wrong_success_receipt_cannot_pass(self):
        process, root, state = self.start_main("wrong-receipt", "topology")
        self.assertEqual(process.wait(timeout=15), 1)
        report = json.loads((root / "validation/result/report.json").read_text())
        suite = json.loads((root / "validation/result/topology/report.json").read_text())
        self.assertFalse(report["passed"]); self.assertFalse(suite["passed"])
        self.assertEqual(suite["test_exit_code"], 0)
        self.assertIn("FileNotFoundError", suite["error"])
        self.assertIn("mcp-native-topology.json", suite["error"])
        wrong = root / "validation/result/topology/evidence/unrelated-success-receipt.json"
        self.assertTrue(json.loads(wrong.read_text())["passed"])
        self.assertTrue(self.runner.clean_stop(suite["launcher_cleanup"]))
        self.assertTrue(all(not alive(int(p.read_text())) for p in self.pid_files if p.exists()))
        json_write(self.directory / "result.json", {"passed": True, "fixture_only": True, "native_execution": False,
            "scope": "Passing TAP and an unrelated successful receipt cannot substitute for the selected suite receipt."})

    def test_camera_scale_routes_default_cli_and_exact_receipt(self):
        process, root, state = self.start_main("normal", "camera-scale")
        self.assertEqual(process.wait(timeout=15), 0)
        report = json.loads((root / "validation/result/report.json").read_text())
        suite = json.loads((root / "validation/result/camera-scale/report.json").read_text())
        invocation = json.loads((state / "test-invocation.json").read_text())
        self.assertTrue(report["passed"]); self.assertTrue(suite["passed"])
        self.assertEqual(len(report["suites"]), 1)
        self.assertEqual(suite["simulator_profile"], "native-simulator")
        self.assertEqual(suite["launcher"], "shipped start-simulator CLI")
        command = suite["launch_command"]
        self.assertIn("start-simulator", command)
        self.assertEqual(command[command.index("--profile") + 1], "native-simulator")
        self.assertNotIn("org.openpnp.codex.NativeMappedAxisFixtureMain", command)
        self.assertEqual(Path(suite["test_command"][-1]).name, "camera-scale-live.test.mjs")
        self.assertEqual(invocation["connection_key"], "OPENPNP_CAMERA_SCALE_E2E_CONNECTION_FILE")
        self.assertEqual(invocation["evidence_key"], "OPENPNP_CAMERA_SCALE_E2E_EVIDENCE_DIR")
        self.assertEqual(Path(invocation["connection_file"]), state / "connection.json")
        evidence = root / "validation/result/camera-scale/evidence/mcp-native-camera-scale.json"
        self.assertEqual(suite["evidence_sha256"][evidence.name], hashlib.sha256(evidence.read_bytes()).hexdigest())
        self.assertEqual(suite["test_counts"], {"tests": 1, "pass": 1, "fail": 0, "skipped": 0, "todo": 0})
        self.assertTrue(self.runner.clean_stop(suite["launcher_cleanup"]))
        self.assertTrue(all(not alive(int(p.read_text())) for p in self.pid_files if p.exists()))
        json_write(self.directory / "result.json", {"passed": True, "fixture_only": True, "native_execution": False,
            "scope": "Runner routing/receipt plumbing only; actual OpenPnP qualification is separately retained in the camera-scale MCP receipt."})

    def test_camera_scale_wrong_success_receipt_cannot_pass(self):
        process, root, state = self.start_main("wrong-receipt", "camera-scale")
        self.assertEqual(process.wait(timeout=15), 1)
        report = json.loads((root / "validation/result/report.json").read_text())
        suite = json.loads((root / "validation/result/camera-scale/report.json").read_text())
        self.assertFalse(report["passed"]); self.assertFalse(suite["passed"])
        self.assertEqual(suite["test_exit_code"], 0)
        self.assertIn("FileNotFoundError", suite["error"])
        self.assertIn("mcp-native-camera-scale.json", suite["error"])
        wrong = root / "validation/result/camera-scale/evidence/unrelated-success-receipt.json"
        self.assertTrue(json.loads(wrong.read_text())["passed"])
        self.assertTrue(self.runner.clean_stop(suite["launcher_cleanup"]))
        self.assertTrue(all(not alive(int(p.read_text())) for p in self.pid_files if p.exists()))
        json_write(self.directory / "result.json", {"passed": True, "fixture_only": True, "native_execution": False,
            "scope": "Passing TAP and an unrelated successful receipt cannot substitute for the selected suite receipt."})

    def test_camera_scale_skipped_live_test_cannot_pass(self):
        process, root, _ = self.start_main("skip", "camera-scale")
        self.assertEqual(process.wait(timeout=15), 1)
        report = json.loads((root / "validation/result/report.json").read_text())
        suite = json.loads((root / "validation/result/camera-scale/report.json").read_text())
        self.assertFalse(report["passed"]); self.assertEqual(suite["test_counts"]["skipped"], 1)
        self.assertIn("no skipped", suite["error"]); self.assertTrue(self.runner.clean_stop(suite["launcher_cleanup"]))

    def test_sigterm_preserves_evidence_and_stops_remaining_suites(self):
        process, root, state = self.start_main("wait-test")
        wait_for((state / "test-ready").exists); process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=15), 1)
        report = json.loads((root / "validation/result/report.json").read_text())
        self.assertFalse(report["passed"]); self.assertEqual(report["interrupted_signals"], [signal.SIGTERM]); self.assertEqual(len(report["suites"]), 1)
        self.assertFalse((root / "validation/result/vision").exists()); self.assertFalse((root / "validation/result/placement").exists())
        suite = json.loads((root / "validation/result/core/report.json").read_text())
        self.assertIn("RunnerInterrupted", suite["error"]); self.assertTrue(self.runner.clean_stop(suite["launcher_cleanup"]))
        self.assertTrue(self.runner.clean_stop(suite["process_cleanup"]["test.process.json"]))
        self.assertTrue(all(not alive(int(p.read_text())) for p in self.pid_files if p.exists()))

    def test_sigterm_during_cleanup_still_reaps_owned_groups(self):
        process, root, state = self.start_main("interrupt-cleanup")
        wait_for((state / "launcher-term").exists); process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=15), 1)
        report = json.loads((root / "validation/result/report.json").read_text())
        self.assertFalse(report["passed"]); self.assertEqual(report["interrupted_signals"], [signal.SIGTERM])
        self.assertTrue((root / "validation/result/core/report.json").exists(), "Interrupt during cleanup must preserve its suite report")
        self.assertTrue(all(not alive(int(p.read_text())) for p in self.pid_files if p.exists()), "Cleanup interrupt must not strand an owned launcher or test")


def fixture_main():
    runner, fixture, suites = Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve(), sys.argv[4]
    module = load_runner(runner); module.ROOT = fixture
    # This fake CLI supports exactly these six plumbing suites; mapped-axis
    # launch is qualified separately against the real JVM and packaged bridge.
    module.SUITES = {name: module.SUITES[name] for name in ("core", "vision", "placement", "backlash", "topology", "camera-scale")}
    # Bound fixture cleanup latency while retaining the production cleanup code.
    stop = module.stop_owned_group
    module.stop_owned_group = lambda process: stop(process, timeout=0.5, disappearance_timeout=1)
    sys.argv = [str(runner), "--runtime", str(fixture / "runtime"), "--output", str(fixture / "validation/result"), "--node", str(fixture / "fake-node"), "--java", str(fixture / "fake-jdk/bin/java"), "--suite", suites]
    return module.main()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fixture-runner": sys.exit(fixture_main())
    unittest.main(verbosity=2)
