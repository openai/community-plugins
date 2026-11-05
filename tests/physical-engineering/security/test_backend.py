"""Credential-bearing logs and CAD process routing are security boundaries."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "physical_engineering_backend", ROOT / "plugins/physical-engineering/scripts/cad_backend.py")
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)


class BackendBoundary(unittest.TestCase):
    def test_symlinked_state_cannot_modify_an_outside_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary = Path(temporary)
            outside = temporary / "outside"
            outside.mkdir(mode=0o755)
            sentinel = outside / "sentinel"
            sentinel.write_text("preserve this file")
            original_mode = outside.stat().st_mode
            for name in ("root", "state", "cache", "engine", "bin", "matplotlib"):
                with self.subTest(name=name):
                    root = temporary / f"owned-{name}"
                    if name == "root":
                        root.symlink_to(outside, target_is_directory=True)
                    else:
                        root.mkdir()
                        (root / name).symlink_to(outside, target_is_directory=True)
                    with self.assertRaises(OSError):
                        backend.environment(root)
                    self.assertEqual(sentinel.read_text(), "preserve this file")
                    self.assertEqual(outside.stat().st_mode, original_mode)
                    self.assertEqual([item.name for item in outside.iterdir()], ["sentinel"])
                    if name != "root":
                        self.assertEqual([item.name for item in root.iterdir()], [name])

    def test_viewer_credentials_are_removed_from_logs(self):
        source = ('view: http://127.0.0.1:4321/model?token=viewer-secret&other=value\n'
                  '{"url":"https://example.test/model?access_token=second-secret"}\n'
                  'token=third-secret\n{"access_token":"fourth-secret"}\n'
                  'Authorization: Bearer fifth-secret\n{"authorization":"Bearer sixth-secret"}')
        result = backend.sanitize(source)
        for secret in ("viewer-secret", "second-secret", "third-secret", "fourth-secret",
                       "fifth-secret", "sixth-secret"):
            self.assertNotIn(secret, result)
        self.assertIn("http://127.0.0.1:4321/model?[redacted]", result)
        self.assertIn("\n", result)

    def test_owned_environment_does_not_change_home_or_user_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = (Path(temporary) / "cad").resolve()
            ambient = {"HOME": "/existing/home", "CAD_STATE_DIR": "/existing/state",
                       "CAD_ENGINE_DIR": "/existing/engine", "CAD_ENGINE_PORT": "3245",
                       "CAD_INSTALLATION_VERSION": "other-release", "CAD_ENGINE_WORKER": "1"}
            with patch.dict(os.environ, ambient):
                env = backend.environment(root)
                self.assertEqual(os.environ["CAD_STATE_DIR"], "/existing/state")
                self.assertEqual(env["HOME"], "/existing/home")
            for key in ("CAD_STATE_DIR", "CAD_CACHE_DIR", "CAD_ENGINE_DIR",
                        "CAD_LAUNCHER_DIR", "MPLCONFIGDIR"):
                self.assertTrue(Path(env[key]).is_relative_to(root))
                self.assertTrue(Path(env[key]).is_dir())
            self.assertEqual(env["CAD_ENGINE_PORT"], "0")
            self.assertEqual(env["CAD_TELEMETRY"], "0")
            self.assertEqual(env["DO_NOT_TRACK"], "1")
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
            self.assertNotIn("CAD_INSTALLATION_VERSION", env)
            self.assertNotIn("CAD_ENGINE_WORKER", env)
            self.assertEqual((root / "engine").stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
