"""Exercise the shipped sandbox helper without opening a network connection."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3] / "plugins" / "aimlapi"
spec = importlib.util.spec_from_file_location("sandbox_helper", ROOT / "skills/aimlapi-sandbox/scripts/try.py")
sandbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox)


class SandboxRequest(unittest.TestCase):
    def test_sends_sandbox_header_and_preserves_body_without_logging_key(self):
        body = {"model": "fixture/model", "messages": [{"role": "user", "content": "synthetic"}]}

        class Response:
            status = 200
            headers = {"x-aimlapi-sandbox": "true", "x-aimlapi-usd-spent": "0"}

            def read(self):
                return b'{"choices": [], "meta": {"usage": {"usd_spent": 0}}}'

        output = io.StringIO()
        with patch.dict(os.environ, {"AIMLAPI_API_KEY": "synthetic-test-key"}), \
                patch.object(sys, "argv", ["try.py", "chat", json.dumps(body)]), \
                patch.object(sandbox.urllib.request, "build_opener") as opener, \
                contextlib.redirect_stdout(output):
            opener.return_value.open.return_value = Response()
            self.assertEqual(sandbox.main(), 0)
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.aimlapi.com/v1/chat/completions")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.get_header("X-aimlapi-sandbox"), "1")
            self.assertEqual(request.get_header("Authorization"), "Bearer synthetic-test-key")
            self.assertEqual(json.loads(request.data), body)
            self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertNotIn("synthetic-test-key", output.getvalue())
        self.assertIn("x-aimlapi-sandbox: true", output.getvalue())

    def test_missing_key_does_not_open_a_connection(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(sys, "argv", ["try.py", "chat", "{}"]), \
                patch.object(sandbox.urllib.request, "build_opener") as opener:
            with self.assertRaisesRegex(SystemExit, "AIMLAPI_API_KEY is not set"):
                sandbox.main()
            opener.assert_not_called()
