"""Offline behavior checks for a cold copied Loupe runner."""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shlex
import tempfile
import time
import unittest
import unittest.mock


def load_runner():
    """Load the copied runtime script without importing provider tools."""
    spec = importlib.util.spec_from_file_location("loupe_runner_under_test", os.environ["LOUPE_TEST_RUNNER"])
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def run_runner(runner, arguments, reviewers):
    """Capture one runner invocation and its public JSON stream."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = runner.main(arguments, reviewers=reviewers, environment={})
    return exit_code, stdout.getvalue(), stderr.getvalue()


class RunnerContractTests(unittest.TestCase):
    """Exercise the real runner with local shell commands only."""

    def setUp(self):
        self.runner = load_runner()

    def test_missing_provider_and_helper_do_not_launch(self):
        provider = self.runner.Reviewer("provider", "printf unexpected", "{review_scope}", required_executable="codex")
        with unittest.mock.patch.object(self.runner, "executable_is_available", return_value=False):
            exit_code, stdout, _stderr = run_runner(self.runner, ["selected scope"], (provider,))
        self.assertEqual(exit_code, 1)
        self.assertEqual(json.loads(stdout)["reviewers"], [])

        helper = self.runner.Reviewer("helper", "printf unexpected", "{review_scope}", required_executable="codex", additional_required_executables=("jq",))
        with unittest.mock.patch.object(self.runner, "executable_is_available", side_effect=lambda name: name == "codex"):
            exit_code, stdout, _stderr = run_runner(self.runner, ["selected scope"], (helper,))
        self.assertEqual(exit_code, 1)
        result = json.loads(stdout)["reviewers"][0]
        self.assertEqual(result["status"], "launch_failed")
        self.assertIsNone(result["return_code"])
        self.assertEqual(result["stdout"], "")

    def test_scope_is_forwarded_exactly_and_blank_scope_is_rejected(self):
        reviewer = self.runner.Reviewer("scope", "printf '%s' {prompt}", "{review_scope}")
        scope = "HEAD~2..HEAD with spaces"
        exit_code, stdout, _stderr = run_runner(self.runner, [scope], (reviewer,))
        payload = json.loads(stdout)
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["review_scope"], scope)
        self.assertEqual(payload["reviewers"][0]["stdout"], scope)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.runner.parse_args(["   "], reviewers=(reviewer,), environment={})

    def test_partial_failure_retains_both_results_and_exact_output(self):
        reviewers = (
            self.runner.Reviewer("good", "printf good", "{review_scope}"),
            self.runner.Reviewer("bad", "printf partial; printf warning >&2; exit 7", "{review_scope}"),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "reviewers.json"
            exit_code, stdout, _stderr = run_runner(self.runner, ["--output", str(output), "mixed scope"], reviewers)
            self.assertEqual(output.read_text(encoding="utf-8"), stdout)
        self.assertEqual(exit_code, 1)
        good, bad = json.loads(stdout)["reviewers"]
        self.assertEqual((good["status"], good["stdout"]), ("succeeded", "good"))
        self.assertEqual((bad["status"], bad["return_code"], bad["stdout"], bad["stderr"]), ("failed", 7, "partial", "warning"))

    def test_timeout_retains_partial_output_and_stops_child_process(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = pathlib.Path(directory) / "late-write"
            output = pathlib.Path(directory) / "reviewers.json"
            command = "printf began; (sleep 1; printf late > {}) & wait".format(shlex.quote(str(marker)))
            reviewer = self.runner.Reviewer("slow", command, "{review_scope}")
            exit_code, stdout, _stderr = run_runner(self.runner, ["--output", str(output), "--timeout-seconds", "0.5", "slow scope"], (reviewer,))
            self.assertEqual(output.read_text(encoding="utf-8"), stdout)
            result = json.loads(stdout)["reviewers"][0]
            self.assertEqual(exit_code, 1)
            self.assertEqual(result["status"], "timed_out")
            self.assertTrue(result["timed_out"])
            self.assertEqual(result["stdout"], "began")
            time.sleep(1.2)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
