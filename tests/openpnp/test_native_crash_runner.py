#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Controlled runner boundary tests; fixtures are not native OpenPnP qualification."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

RUNNER = Path(__file__).resolve().parents[2] / "scripts/openpnp-test-native-crash.py"


def load():
    spec = importlib.util.spec_from_file_location("crash_runner_fixture", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CrashRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.runner = load()
        self.runner.ROOT = self.root
        (self.root / "validation").mkdir()
        self.build = self.root / "validation/build"
        self.runtime = self.build / "runtime"
        (self.runtime / "lib").mkdir(parents=True)
        (self.runtime / "samples").mkdir()
        self.jar(self.runtime / "native.jar", "lib/dependency.jar")
        self.jar(self.runtime / "lib/dependency.jar", ". optional.jar")
        (self.runtime / "samples/sample.txt").write_text("Controlled fixture; no native code")
        self.jar(self.build / "openpnp-codex-bridge.jar")
        source = self.root / "src/openpnp/java/org/openpnp/codex/Fixture.java"
        source.parent.mkdir(parents=True)
        source.write_text("// controlled metadata fixture, never compiled")
        test = self.root / "tests/openpnp/native/NativeJvmHaltLedgerTest.java"
        test.parent.mkdir(parents=True)
        test.write_text("// fixture only")
        self.java = self.root / "jdk"
        (self.java / "bin").mkdir(parents=True)
        (self.java / "lib").mkdir()
        for name in ("java", "javac"):
            file = self.java / "bin" / name
            file.write_text("#!/bin/sh\nexit 99\n")
            file.chmod(0o700)
        (self.java / "release").write_text("fixture")
        (self.java / "lib/modules").write_bytes(b"fixture")
        self.rm = {"upstream_commit": self.runner.PIN, "gui_jar": "native.jar", "libs_directory": "lib",
                   "samples_directory": "samples", "native_action_observer": {"api_version": 1}}
        self.bm = {"upstream_commit": self.runner.PIN,
                   "production_source_sha256": {source.relative_to(self.root).as_posix(): self.runner.digest(source)},
                   "bridge_sha256": self.runner.digest(self.build / "openpnp-codex-bridge.jar")}
        self.sync()

    def tearDown(self):
        self.temp.cleanup()

    def jar(self, path, classpath=None):
        with zipfile.ZipFile(path, "w") as jar:
            jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\r\n" +
                         ("Class-Path: " + classpath + "\r\n" if classpath else "") + "\r\n")

    def sync(self):
        self.rm["files"] = [{"path": name, "sha256": self.runner.digest(self.runtime / name)}
                            for name in ("native.jar", "lib/dependency.jar", "samples/sample.txt")]
        self.bm["patched_native_jar_sha256"] = self.runner.digest(self.runtime / "native.jar")
        self.write_manifests()

    def write_manifests(self):
        rm = self.runtime / "codex-build-manifest.json"
        rm.write_text(json.dumps(self.rm))
        self.bm["runtime_manifest_sha256"] = self.runner.digest(rm)
        (self.build / "build-manifest.json").write_text(json.dumps(self.bm))

    def verify(self):
        return self.runner.verify_inputs(self.build, self.java)

    def test_complete_inventory_and_optional_manifest_paths(self):
        inputs = self.verify()
        self.assertTrue(self.runner.unchanged(inputs))
        kinds = {x["kind"] for x in inputs["manifest_classpath"]}
        self.assertEqual(kinds, {"inventoried-file", "absent-optional-file", "owned-directory-with-exact-runtime-inventory"})
        (self.runtime / "lib/optional.jar").write_text("late injected file")
        self.assertFalse(self.runner.unchanged(inputs))
        with self.assertRaisesRegex(ValueError, "Uninventoried"):
            self.verify()

    def test_unlisted_class_and_symlink_runtime_refused(self):
        added = self.runtime / "lib/Unexpected.class"
        added.write_bytes(b"not a real class")
        with self.assertRaisesRegex(ValueError, "unlisted"):
            self.verify()
        added.unlink()
        added.symlink_to(self.root / "not-created")
        with self.assertRaisesRegex(ValueError, "Symbolic"):
            self.verify()

    def test_hash_source_and_upstream_mismatch_refused(self):
        original = self.bm["bridge_sha256"]
        self.bm["bridge_sha256"] = "0" * 64
        self.write_manifests()
        with self.assertRaisesRegex(ValueError, "Bridge digest"):
            self.verify()
        self.bm["bridge_sha256"] = original
        self.write_manifests()
        next((self.root / "src").rglob("*.java")).write_text("changed")
        with self.assertRaisesRegex(ValueError, "Current native source"):
            self.verify()
        self.rm["upstream_commit"] = "0" * 40
        self.write_manifests()
        with self.assertRaisesRegex(ValueError, "Upstream"):
            self.verify()

    def test_manifest_traversal_duplicate_and_remote_classpath_refused(self):
        original = self.rm["files"][0]["path"]
        self.rm["files"][0]["path"] = "../escape.jar"
        self.write_manifests()
        with self.assertRaisesRegex(ValueError, "canonical relative"):
            self.verify()
        self.rm["files"][0]["path"] = original
        self.rm["files"].append(self.rm["files"][0].copy())
        self.write_manifests()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.verify()
        self.jar(self.runtime / "lib/dependency.jar", "https://example.invalid/untrusted.jar")
        self.sync()
        with self.assertRaisesRegex(ValueError, "Remote"):
            self.verify()

    def test_output_existing_outside_overlap_and_symlink_refused(self):
        existing = self.root / "validation/existing"
        existing.mkdir()
        sentinel = existing / "preserve"
        sentinel.write_text("untouched")
        for path in (existing, self.root / "outside", self.build / "new-output", self.root / "validation"):
            with self.assertRaises((ValueError, FileExistsError)):
                self.runner.reserve_output(path, self.build)
        alias = self.root / "validation/alias"
        alias.symlink_to(existing, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symbolic"):
            self.runner.reserve_output(alias / "child", self.build)
        self.assertEqual(sentinel.read_text(), "untouched")
        output = self.runner.reserve_output(self.root / "validation/fresh", self.build)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(FileExistsError):
            self.runner.reserve_output(output, self.build)

    def test_environment_removes_jvm_and_native_loader_hooks(self):
        keys = ("JAVA_TOOL_OPTIONS", "JDK_JAVAC_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "CLASSPATH",
                "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH",
                "OPENPNP_CONFIG", "LIBPATH", "SHLIB_PATH", "JAVA_OPTS")
        with patch.dict(os.environ, {**{key: "fixture" for key in keys}, "PRESERVED_FIXTURE": "yes"}):
            env = self.runner.clean_environment()
        self.assertTrue(all(key not in env for key in keys))
        self.assertEqual(env["PRESERVED_FIXTURE"], "yes")

    def test_timeout_kills_only_owned_group_and_retains_receipt(self):
        sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        try:
            log = self.root / "timeout.log"
            with self.assertRaises(ValueError):
                self.runner.run_logged([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"],
                                       self.runner.clean_environment(), log, .5)
            receipt = json.loads(log.with_suffix(".process.json").read_text())
            self.assertTrue(receipt["timeout"])
            self.assertTrue(receipt["cleanup"]["forced_kill"])
            self.assertTrue(receipt["cleanup"]["group_gone"])
            self.assertIsNone(sentinel.poll())
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=5)

    def test_interruption_reaps_owned_child_and_preserves_failure(self):
        def interrupted(_signum, _frame):
            raise self.runner.RunnerInterrupted("controlled test signal")
        old = signal.signal(signal.SIGUSR1, interrupted)
        timer = threading.Timer(.5, lambda: os.kill(os.getpid(), signal.SIGUSR1))
        log = self.root / "interrupted.log"
        try:
            timer.start()
            with self.assertRaises(self.runner.RunnerInterrupted):
                self.runner.run_logged([sys.executable, "-c", "import time; time.sleep(30)"],
                                       self.runner.clean_environment(), log, 10)
            receipt = json.loads(log.with_suffix(".process.json").read_text())
            self.assertTrue(receipt["interrupted"])
            self.assertTrue(receipt["cleanup"]["group_gone"])
        finally:
            timer.cancel()
            signal.signal(signal.SIGUSR1, old)

    def test_case_oracle_rejects_wrong_native_state_and_second_recovery(self):
        directory = self.root / "case"
        directory.mkdir()
        (directory / "journal").mkdir()
        (directory / "journal/operations.jsonl").write_text("controlled fixture only")
        data = {"halt-observation.json": {"native_feed_count": 0, "native_total_feed_count": 0,
                "native_nozzle_part": None, "native_placed_count": 0}, "fixture.json": {"fixture_only": True}}
        recovery = {"native_action_recovery": {"unresolved_actions": [{"kind": "feed"}]}, "assertions": 1,
                    "native_complete_checkpoints": 0, "operation_state": "outcome_unknown", "automatic_replay": False,
                    "recovery_native_feed_events": 0, "recovery_nozzle_part_events": 0, "recovery_native_machine_events": 0}
        data.update({"recover1.json": recovery.copy(), "recover2.json": recovery.copy()})
        def write():
            for name, value in data.items():
                (directory / name).write_text(json.dumps(value))
        write()
        self.assertTrue(self.runner.verify_case("feed-intent-after", directory)["passed"])
        data["halt-observation.json"]["native_feed_count"] = 1
        write()
        with self.assertRaisesRegex(ValueError, "Unexpected native state"):
            self.runner.verify_case("feed-intent-after", directory)
        data["halt-observation.json"]["native_feed_count"] = 0
        data["recover2.json"]["native_action_recovery"] = {"unresolved_actions": []}
        write()
        with self.assertRaisesRegex(ValueError, "Second JVM"):
            self.runner.verify_case("feed-intent-after", directory)


if __name__ == "__main__":
    unittest.main(verbosity=2)
