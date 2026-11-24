"""Unit tests for the skill scripts. No network, no API key.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "plugins" / "aimlapi"


def load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


webhook = load("skills/aimlapi-webhooks/scripts/verify_webhook.py", "verify_webhook")
batch = load("skills/aimlapi-batch/scripts/batch.py", "batch")
models = load("skills/aimlapi-models/scripts/models.py", "models")
job = load("skills/aimlapi-async-jobs/scripts/job.py", "job")
check = load(str(Path(__file__).resolve().parents[1] / "support" / "upstream-check.py"), "check")

# Published test vector of the Standard Webhooks specification.
# Built from two parts so secret scanners do not report a known public test value.
SECRET = "whsec_" + "MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
MSG_ID = "msg_p5jXN8AQM9LWM0D4loKWxJek"
TIMESTAMP = 1614265330
BODY = b'{"test": 2432232314}'
SIGNATURE = "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="


def headers(signature: str = SIGNATURE, timestamp: int = TIMESTAMP) -> dict:
    return {"webhook-id": MSG_ID, "webhook-timestamp": str(timestamp), "webhook-signature": signature}


class WebhookVerification(unittest.TestCase):
    def test_reference_vector(self):
        self.assertEqual(webhook.sign(SECRET, MSG_ID, TIMESTAMP, BODY), SIGNATURE)
        self.assertEqual(webhook.verify(BODY, headers(), SECRET, now=TIMESTAMP), {"test": 2432232314})

    def test_header_names_are_case_insensitive(self):
        mixed = {k.title(): v for k, v in headers().items()}
        self.assertEqual(webhook.verify(BODY, mixed, SECRET, now=TIMESTAMP)["test"], 2432232314)

    def test_tampered_body(self):
        with self.assertRaises(webhook.WebhookVerificationError):
            webhook.verify(b'{"test": 2432232315}', headers(), SECRET, now=TIMESTAMP)

    def test_old_and_future_timestamps(self):
        for skew in (-301, 301):
            with self.assertRaises(webhook.WebhookVerificationError):
                webhook.verify(BODY, headers(), SECRET, now=TIMESTAMP + skew)

    def test_rotation_carries_two_signatures(self):
        both = "v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= " + SIGNATURE
        self.assertTrue(webhook.verify(BODY, headers(both), SECRET, now=TIMESTAMP))

    def test_wrong_secret(self):
        with self.assertRaises(webhook.WebhookVerificationError):
            webhook.verify(BODY, headers(), "whsec_" + "A" * 32, now=TIMESTAMP)

    def test_missing_header(self):
        incomplete = headers()
        del incomplete["webhook-signature"]
        with self.assertRaises(webhook.WebhookVerificationError):
            webhook.verify(BODY, incomplete, SECRET, now=TIMESTAMP)


def line(custom_id: str, model: str = "anthropic/claude-haiku-5-5", **body) -> str:
    payload = {"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10}
    payload.update(body)
    return json.dumps({"custom_id": custom_id, "method": "POST", "url": "/v1/chat/completions", "body": payload})


class BatchFileCheck(unittest.TestCase):
    def check(self, *lines: str) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(lines) + "\n")
        return batch.check(f.name)

    def test_valid_file(self):
        report = self.check(line("a"), line("b"))
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["lines"], 2)

    def test_duplicate_custom_id(self):
        self.assertTrue(any("duplicate" in p for p in self.check(line("a"), line("a"))["problems"]))

    def test_max_tokens_required(self):
        bad = json.loads(line("a"))
        del bad["body"]["max_tokens"]
        self.assertTrue(any("max_tokens" in p for p in self.check(json.dumps(bad))["problems"]))

    def test_one_model_per_file(self):
        report = self.check(line("a"), line("b", model="openai/gpt-4o-mini"))
        self.assertTrue(any("one model per file" in p for p in report["problems"]))

    def test_unknown_field_and_n(self):
        problems = self.check(line("a", n=2, logprobs=True))["problems"]
        self.assertTrue(any("n may only be 1" in p for p in problems))
        self.assertTrue(any("logprobs" in p for p in problems))

    def test_wrong_url_and_stream(self):
        bad = json.loads(line("a", stream=True))
        bad["url"] = "/v1/embeddings"
        problems = self.check(json.dumps(bad))["problems"]
        self.assertTrue(any("url" in p for p in problems))
        self.assertTrue(any("stream" in p for p in problems))

    def test_not_json_and_empty(self):
        self.assertTrue(any("not JSON" in p for p in self.check("{oops")["problems"]))
        self.assertTrue(any("no requests" in p for p in self.check("")["problems"]))


class BatchRunOnce(unittest.TestCase):
    """A re-run of `batch.py run` on the same file must not create and pay for a second batch."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "requests.jsonl")
        with open(self.path, "w") as f:
            f.write(line("a") + "\n")
        self.calls = {"upload": 0, "create": 0, "keys": []}

    def fakes(self, crash_after_create=False):
        def upload(path):
            self.calls["upload"] += 1
            return "file-1"

        def create(file_id, webhook_url, key):
            self.calls["create"] += 1
            self.calls["keys"].append(key)
            if crash_after_create:
                raise KeyboardInterrupt  # the process dies before the batch id is saved
            return {"id": "batch_1"}

        return dict(upload_fn=upload, create_fn=create,
                    wait_fn=lambda batch_id, interval: {"id": batch_id, "status": "completed"},
                    results_fn=lambda batch, out: None)

    def test_rerun_resumes(self):
        batch.run(self.path, self.dir, 0, **self.fakes())
        batch.run(self.path, self.dir, 0, **self.fakes())
        self.assertEqual((self.calls["upload"], self.calls["create"]), (1, 1))

    def test_crash_between_create_and_save_reuses_key(self):
        with self.assertRaises(KeyboardInterrupt):
            batch.run(self.path, self.dir, 0, **self.fakes(crash_after_create=True))
        batch.run(self.path, self.dir, 0, **self.fakes())
        self.assertEqual(self.calls["upload"], 1)  # the uploaded file is reused
        self.assertEqual(len(set(self.calls["keys"])), 1)  # same Idempotency-Key, so the API returns the same batch

    def test_changed_file_stops(self):
        batch.run(self.path, self.dir, 0, **self.fakes())
        with open(self.path, "a") as f:
            f.write(line("b") + "\n")
        with self.assertRaises(SystemExit):
            batch.run(self.path, self.dir, 0, **self.fakes())
        self.assertEqual(self.calls["create"], 1)


class BatchDownloadRedirect(unittest.TestCase):
    """GET /v1/files/{id}/content answers 302 to a signed link that refuses a second credential."""

    def test_follows_redirect_without_the_api_key(self):
        import http.server
        import os
        import threading

        seen = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/v1/files/"):
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/signed?sig=x")
                    self.end_headers()
                    return
                seen["authorization"] = self.headers.get("Authorization")
                status = 400 if self.headers.get("Authorization") else 200
                self.send_response(status)
                self.end_headers()
                self.wfile.write(b'{"custom_id": "a"}\n')

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        old_api, old_key = batch.API, os.environ.get("AIMLAPI_API_KEY")
        batch.API = f"http://127.0.0.1:{server.server_port}"
        os.environ["AIMLAPI_API_KEY"] = "test-key"
        try:
            body = batch.request("GET", "/v1/files/file-1/content", raw=True)
        finally:
            batch.API = old_api
            if old_key is None:
                del os.environ["AIMLAPI_API_KEY"]
            else:
                os.environ["AIMLAPI_API_KEY"] = old_key
            server.shutdown()
        self.assertEqual(body, b'{"custom_id": "a"}\n')
        self.assertIsNone(seen["authorization"])


class ModelSchema(unittest.TestCase):
    def test_required_only_when_every_alternative_requires_it(self):
        schema = {
            "anyOf": [
                {"properties": {"model": {}, "messages": {}, "system": {}}, "required": ["model", "messages", "system"]},
                {"properties": {"model": {}, "messages": {}, "stop": {}}, "required": ["model", "messages"]},
            ]
        }
        props, required = models.flatten(schema)
        self.assertEqual(set(props), {"model", "messages", "system", "stop"})
        self.assertEqual(required, {"model", "messages"})

    def test_describe_shows_nested_enums(self):
        text = models.describe({"anyOf": [{"type": "object", "properties": {"width": {}}}, {"type": "string", "enum": ["square"]}], "default": "square"})
        self.assertIn('one of ["square"]', text)
        self.assertIn("{width}", text)


class JobResult(unittest.TestCase):
    def test_result_url_shapes(self):
        self.assertEqual(job.result_url({"video": {"url": "v"}}), "v")
        self.assertEqual(job.result_url({"audio_file": {"url": "a"}}), "a")
        self.assertIsNone(job.result_url({"status": "completed"}))

    def test_job_id_from_either_field(self):
        self.assertEqual(job.job_id({"id": "x"}), "x")
        self.assertEqual(job.job_id({"generation_id": "y"}), "y")


if __name__ == "__main__":
    unittest.main()


class LeakCheck(unittest.TestCase):
    def problems(self, text: str, rel: str = "skills/x/SKILL.md") -> list:
        return check.content_problems(rel, text)

    def test_planted_keys_fail(self):
        planted = [
            "export AIMLAPI_API_KEY=" + "3f9a" * 8,
            'client = OpenAI(api_key="' + "Ab1_" * 6 + '")',
            "Authorization: Bearer " + "x7Y" * 9,
            '{"webhook_secret": "' + "q" * 24 + '"}',
        ]
        for text in planted:
            with self.subTest(text=text[:30]):
                self.assertTrue(self.problems(text), text)

    def test_placeholders_and_pins_pass(self):
        clean = [
            'api_key=os.environ["AIMLAPI_API_KEY"]',
            "Authorization: Bearer $AIMLAPI_API_KEY",
            "uses: actions/checkout@" + "1d" * 20 + " # v4",
            "sha256: " + "ab" * 32,
            '"api_key": "<YOUR_KEY>"',
        ]
        for text in clean:
            with self.subTest(text=text[:30]):
                self.assertEqual(self.problems(text), [], text)


class LocalServer:
    """A throwaway HTTP server on 127.0.0.1; `handler(req)` answers each GET."""

    def __init__(self, handler):
        import http.server
        import threading

        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                handler(self)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        outer.seen = []

    def close(self):
        self.server.shutdown()


class JobPollAndDownload(unittest.TestCase):
    def args(self, download=None):
        import argparse
        return argparse.Namespace(interval=0.001, timeout=30, sandbox=False, download=download)

    def test_transient_polls_keep_waiting(self):
        answers = iter([job.Transient("HTTP 503"), job.Transient("HTTP 429"), ({"status": "completed"}, {})])

        def fake_call(*args, **kwargs):
            item = next(answers)
            if isinstance(item, Exception):
                raise item
            return item

        old = job.call
        job.call = fake_call
        try:
            self.assertEqual(job.wait("video", "gen-1", self.args()), 0)
        finally:
            job.call = old

    def test_download_sends_a_named_user_agent_and_no_key(self):
        seen = {}

        def handler(req):
            seen["ua"], seen["auth"] = req.headers.get("User-Agent"), req.headers.get("Authorization")
            blocked = (req.headers.get("User-Agent") or "").startswith("Python-urllib")  # what the CDN refuses
            req.send_response(403 if blocked else 200)
            req.end_headers()
            req.wfile.write(b"video-bytes")

        srv = LocalServer(handler)
        target = os.path.join(tempfile.mkdtemp(), "out.mp4")
        try:
            self.assertTrue(job.download(srv.url + "/result.mp4", target))
        finally:
            srv.close()
        self.assertTrue(seen["ua"].startswith("aimlapi-skills"))
        self.assertIsNone(seen["auth"])
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"video-bytes")

    def test_failed_download_after_completion_is_exit_4(self):
        old_call, old_download = job.call, job.download
        job.call = lambda *a, **k: ({"status": "completed", "video": {"url": "https://example.com/v.mp4"}}, {})
        job.download = lambda url, target: False
        try:
            self.assertEqual(job.wait("video", "gen-1", self.args(download="out.mp4")), 4)
        finally:
            job.call, job.download = old_call, old_download

    def test_api_call_does_not_follow_a_redirect_with_the_key(self):
        hits = []

        def handler(req):
            if req.path.startswith("/elsewhere"):
                hits.append(req.headers.get("Authorization"))
                req.send_response(200)
                req.end_headers()
                req.wfile.write(b"{}")
                return
            req.send_response(302)
            req.send_header("Location", "/elsewhere")
            req.end_headers()

        srv = LocalServer(handler)
        old_api, old_key = job.API, os.environ.get("AIMLAPI_API_KEY")
        job.API, os.environ["AIMLAPI_API_KEY"] = srv.url, "test-key"
        try:
            with self.assertRaises(SystemExit):
                job.call("GET", "/v2/video/generations?generation_id=x", None, False)
        finally:
            job.API = old_api
            if old_key is None:
                del os.environ["AIMLAPI_API_KEY"]
            else:
                os.environ["AIMLAPI_API_KEY"] = old_key
            srv.close()
        self.assertEqual(hits, [])


class BatchPollRetries(unittest.TestCase):
    def test_transient_polls_keep_waiting(self):
        answers = iter([batch.Transient("HTTP 502"), {"id": "b", "status": "completed", "request_counts": {}}])

        def fake_request(*args, **kwargs):
            item = next(answers)
            if isinstance(item, Exception):
                raise item
            return item

        old = batch.request
        batch.request = fake_request
        try:
            self.assertEqual(batch.wait("b", 0)["status"], "completed")
        finally:
            batch.request = old


class WebhookEmptySecret(unittest.TestCase):
    def test_empty_or_bare_prefix_is_refused(self):
        for secret in ("", "whsec_", "whsec_AAAA", None):
            with self.subTest(secret=secret), self.assertRaises(webhook.WebhookVerificationError):
                webhook.verify(BODY, headers(), secret, now=TIMESTAMP)


class ModelSearchRows(unittest.TestCase):
    def test_one_row_per_model_with_the_main_type_first(self):
        rows = models.group([
            {"id": "a", "type": "anthropic/batches"},
            {"id": "a", "type": "openai/chat-completions"},
            {"id": "b", "type": "openai/image-generations"},
        ])
        self.assertEqual([r["id"] for r in rows], ["a", "b"])
        self.assertEqual(rows[0]["type"], "openai/chat-completions")
        self.assertEqual(rows[0]["types"], ["openai/chat-completions", "anthropic/batches"])


class ModelSpeed(unittest.TestCase):
    def test_repeats_aliases_and_reports_missing(self):
        import argparse
        import contextlib
        import io
        seen = {}

        def fake_get(path, params=None):
            seen["path"], seen["params"] = path, params
            return {"window_seconds": 300, "generated_at": "t", "models": [
                {"alias": "a/x", "endpoint": "openai/chat-completions",
                 "metrics": {"tps": {"p50": 30, "p75": 40, "p90": 50, "p99": 90}}}]}

        old = models.get
        models.get = fake_get
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                models.cmd_speed(argparse.Namespace(models=["a/x", "b/y"], json=False))
        finally:
            models.get = old
        self.assertEqual(seen, {"path": "/models/metrics", "params": {"aliases": ["a/x", "b/y"]}})
        self.assertIn("tokens/s p50 30", out.getvalue())
        self.assertIn("b/y: no data", out.getvalue())
