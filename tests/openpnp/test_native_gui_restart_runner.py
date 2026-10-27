# SPDX-License-Identifier: Apache-2.0
"""Admission/cleanup checks only; these do not execute native OpenPnP or a GUI."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("gui_restart_runner", ROOT / "scripts/openpnp-test-native-gui-restart.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class GuiRestartRunnerTests(unittest.TestCase):
    def record(self, code=0):
        return {"exit_code": code, "timeout": False, "interrupted": False,
                "primary_error": None if code == 0 else {"type": "NonZeroExit"},
                "cleanup": {"parent_reaped": True, "group_gone": True, "forced_cleanup": False, "errors": []}}

    def test_only_exact_predeclared_halt_is_accepted(self):
        self.assertEqual(runner.BOUNDARIES, {"intent-force": 73, "zip-before": 74})
        self.assertTrue(runner.expected_completion(self.record(), 0))
        for code in (73, 74):
            self.assertTrue(runner.expected_completion(self.record(code), code))
            self.assertFalse(runner.expected_completion(self.record(code), 0))
            self.assertFalse(runner.expected_completion(self.record(code), 147 - code))

    def test_timeout_interrupt_or_forced_cleanup_cannot_pass(self):
        for code in (0, 73, 74):
            for key in ("timeout", "interrupted"):
                row = self.record(code); row[key] = True
                self.assertFalse(runner.expected_completion(row, code))
            for key, value in (("parent_reaped", False), ("group_gone", False), ("forced_cleanup", True), ("errors", ["failure"])):
                row = self.record(code); row["cleanup"][key] = value
                self.assertFalse(runner.expected_completion(row, code))
            row = self.record(code); row["primary_error"] = {"type": "OSError"}
            self.assertFalse(runner.expected_completion(row, code))

    def test_full_public_gui_proof_requires_every_original_invariant(self):
        proof = {"passed": True, "actual_mainframe": True, "original_native_graph_preserved": True,
                 "actual_packaged_mcp_recovery_requests": True, "owned_bootstrap_loader_closed": True,
                 "all_public_workflow_tools_use_packaged_mcp": True, "post_restart_placements": 1,
                 "direct_public_bridge_workflow_calls": 0, "legacy_helper_direct_calls": 0}
        runner.verify_gui_proof(proof)
        for key in proof:
            bad = copy.deepcopy(proof); del bad[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                runner.verify_gui_proof(bad)
        for key in ("direct_public_bridge_workflow_calls", "legacy_helper_direct_calls"):
            bad = copy.deepcopy(proof); bad[key] = 1
            with self.assertRaises(ValueError):
                runner.verify_gui_proof(bad)
        proof["post_restart_placements"] = 0
        with self.assertRaises(ValueError):
            runner.verify_gui_proof(proof)

    def test_output_cannot_escape_or_reuse_the_build(self):
        build = ROOT / "validation/immutable-test-build"
        for path in (ROOT / "outside-validation", build / "nested", ROOT / "validation"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                runner.output_path(path, build)


if __name__ == "__main__":
    unittest.main()
