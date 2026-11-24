"""Unit tests for skills/aimlapi-speech/scripts/speech.py. No network, no API key.

    python3 -m unittest tests.test_speech -v
"""

from __future__ import annotations

import contextlib
import http.server
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3] / "plugins" / "aimlapi"


def load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


speech = load("skills/aimlapi-speech/scripts/speech.py", "speech")


class TtsAnswer(unittest.TestCase):
    def test_audio_object_with_url(self):
        self.assertEqual(speech.audio_source({"audio": {"url": "https://example.com/a.mp3"}}), ("url", "https://example.com/a.mp3"))

    def test_audio_as_plain_string(self):
        self.assertEqual(speech.audio_source({"audio": "https://example.com/a.mp3"}), ("url", "https://example.com/a.mp3"))

    def test_audio_base64(self):
        self.assertEqual(speech.audio_source({"audio": {"base64": "AAE="}}), ("base64", "AAE="))

    def test_no_audio(self):
        self.assertIsNone(speech.audio_source({"meta": {}}))
        self.assertIsNone(speech.audio_source({"audio": {"url": ""}}))

    def test_sniff_format(self):
        self.assertEqual(speech.sniff_format(b"RIFF\x00\x00\x00\x00WAVEfmt "), "wav")
        self.assertEqual(speech.sniff_format(b"ID3\x04\x00"), "mp3")
        self.assertEqual(speech.sniff_format(b"\xff\xf3\x44\xc4"), "mp3")
        self.assertEqual(speech.sniff_format(b"\xff\xf1\x50\x80"), "aac")
        self.assertEqual(speech.sniff_format(b"OggS\x00"), "ogg")
        self.assertEqual(speech.sniff_format(b"fLaC\x00"), "flac")
        self.assertIsNone(speech.sniff_format(b"\x00\x01\x02\x03"))


class Transcript(unittest.TestCase):
    def test_text_shape(self):
        self.assertEqual(speech.transcript_text({"text": "hello", "words": []}), "hello")

    def test_deepgram_channels_list(self):
        result = {"metadata": {}, "results": {"channels": [{"alternatives": [{"transcript": "hi there", "words": []}]}]}}
        self.assertEqual(speech.transcript_text(result), "hi there")

    def test_deepgram_channels_object(self):
        result = {"results": {"channels": {"alternatives": [{"transcript": "as documented"}]}}}
        self.assertEqual(speech.transcript_text(result), "as documented")

    def test_bare_string_and_unknown(self):
        self.assertEqual(speech.transcript_text("plain"), "plain")
        self.assertIsNone(speech.transcript_text(None))
        self.assertIsNone(speech.transcript_text({"results": None}))
        self.assertIsNone(speech.transcript_text({"something": "else"}))


class Multipart(unittest.TestCase):
    def test_body_layout(self):
        body, ctype = speech.multipart(
            {"model": "m/x", "diarize": True, "keyterm": ["a", "b"]}, "audio", "/tmp/dir/meet ing.wav", b"\x00\x01", boundary="B"
        )
        self.assertEqual(ctype, "multipart/form-data; boundary=B")
        text = body.decode("latin-1")
        self.assertIn('--B\r\nContent-Disposition: form-data; name="model"\r\n\r\nm/x\r\n', text)
        self.assertIn('name="diarize"\r\n\r\ntrue\r\n', text)
        self.assertIn('name="keyterm"\r\n\r\n["a", "b"]\r\n', text)
        self.assertIn('name="audio"; filename="meet ing.wav"\r\nContent-Type: audio/', text)
        self.assertTrue(body.endswith(b"\x00\x01\r\n--B--\r\n"))

    def test_filename_cannot_break_headers(self):
        body, _ = speech.multipart({}, "file", 'a"b\r\nX: y.mp3', b"", boundary="B")
        self.assertIn(b'filename="a_b__X: y.mp3"', body)
        self.assertNotIn(b"\r\nX: y", body)

    def test_form_values(self):
        self.assertEqual(speech.form_value(False), "false")
        self.assertEqual(speech.form_value("en"), "en")
        self.assertEqual(speech.form_value(3), "3")
        self.assertEqual(speech.form_value({"a": 1}), '{"a": 1}')


class SchemaField(unittest.TestCase):
    def doc(self, props: dict) -> dict:
        return {"paths": {"/v1/stt/create": {"post": {"requestBody": {"content": {"application/json": {"schema": {"properties": props}}}}}}}}

    def test_audio_or_file(self):
        self.assertEqual(speech.file_field_from_schema(self.doc({"audio": {"format": "binary"}, "url": {}})), "audio")
        self.assertEqual(speech.file_field_from_schema(self.doc({"file": {"format": "binary"}, "url": {}})), "file")
        self.assertIsNone(speech.file_field_from_schema(self.doc({"url": {"format": "uri"}})))
        self.assertIsNone(speech.file_field_from_schema({}))

    def test_poll_path_quotes_the_id(self):
        self.assertEqual(speech.poll_path("abc_-1"), "/v1/stt/abc_-1")
        self.assertEqual(speech.poll_path("../x"), "/v1/stt/..%2Fx")


class LoadBody(unittest.TestCase):
    def test_inline_and_file(self):
        self.assertEqual(speech.load_body('{"model": "m"}'), {"model": "m"})
        self.assertEqual(speech.load_body(None), {})
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"text": "hi"}, f)
        try:
            self.assertEqual(speech.load_body("@" + f.name), {"text": "hi"})
        finally:
            os.unlink(f.name)
        with self.assertRaises(ValueError):
            speech.load_body("[1, 2]")


class LocalServer:
    """A local HTTP server; `routes` maps (method, path prefix) to a handler(request) -> (status, headers, body)."""

    def __init__(self, routes):
        seen = self.seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                seen.append({"method": self.command, "path": self.path, "headers": self.headers, "body": body})
                for (method, prefix), handler in routes.items():
                    if self.command == method and self.path.startswith(prefix):
                        status, headers, out = handler(self)
                        break
                else:
                    status, headers, out = 404, {}, b"{}"
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            do_GET = do_POST = _serve

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self._old = speech.API, os.environ.get("AIMLAPI_API_KEY")
        speech.API = self.base
        os.environ["AIMLAPI_API_KEY"] = "test-key"
        return self

    def __exit__(self, *exc):
        speech.API, old_key = self._old
        if old_key is None:
            os.environ.pop("AIMLAPI_API_KEY", None)
        else:
            os.environ["AIMLAPI_API_KEY"] = old_key
        self.server.shutdown()
        self.server.server_close()


def quiet(fn, *args):
    """Run fn with stdout and stderr captured; return (result or SystemExit code, stdout)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            result = fn(*args)
        except SystemExit as e:
            result = e.code
    return result, out.getvalue()


class KeyNeverLeavesTheApi(unittest.TestCase):
    def test_api_redirect_is_not_followed(self):
        routes = {
            ("POST", "/v1/tts"): lambda h: (302, {"Location": f"http://127.0.0.1:{h.server.server_port}/elsewhere"}, b""),
            ("GET", "/elsewhere"): lambda h: (200, {"Content-Type": "application/json"}, b'{"audio": {"url": "x"}}'),
        }
        with tempfile.TemporaryDirectory() as tmp, LocalServer(routes) as srv:
            args = types.SimpleNamespace(body='{"model": "m", "text": "hi"}', out=os.path.join(tmp, "a.mp3"), sandbox=False, timeout=10)
            code, _ = quiet(speech.cmd_tts, args)
        self.assertEqual(code, 3)
        self.assertEqual([r["path"] for r in srv.seen], ["/v1/tts"])
        self.assertEqual(srv.seen[0]["headers"].get("Authorization"), "Bearer test-key")
        self.assertEqual(srv.seen[0]["headers"].get("User-Agent"), speech.CLIENT)

    def test_audio_download_carries_no_key(self):
        routes = {
            ("POST", "/v1/tts"): lambda h: (
                200,
                {"Content-Type": "application/json"},
                json.dumps({"audio": {"url": f"http://127.0.0.1:{h.server.server_port}/files/a.mp3"}, "meta": {"usage": {"usd_spent": 0}}}).encode(),
            ),
            ("GET", "/files/"): lambda h: (200, {"Content-Type": "audio/mpeg"}, b"ID3audio"),
        }
        with tempfile.TemporaryDirectory() as tmp, LocalServer(routes) as srv:
            target = os.path.join(tmp, "a.mp3")
            args = types.SimpleNamespace(body='{"model": "m", "text": "hi"}', out=target, sandbox=False, timeout=10)
            code, _ = quiet(speech.cmd_tts, args)
            saved = Path(target).read_bytes()
        self.assertEqual(code, 0)
        self.assertEqual(saved, b"ID3audio")
        download = srv.seen[1]
        self.assertEqual(download["path"], "/files/a.mp3")
        self.assertNotIn("Authorization", download["headers"])
        self.assertEqual(download["headers"].get("User-Agent"), speech.CLIENT)

    def test_binary_answer_is_saved_as_is(self):
        routes = {("POST", "/v1/tts"): lambda h: (200, {"Content-Type": "audio/wav"}, b"ID3\x04binary-mp3")}
        with tempfile.TemporaryDirectory() as tmp, LocalServer(routes):
            target = os.path.join(tmp, "a.mp3")
            args = types.SimpleNamespace(body='{"model": "m", "text": "hi", "stream": true}', out=target, sandbox=False, timeout=10)
            code, _ = quiet(speech.cmd_tts, args)
            self.assertEqual(Path(target).read_bytes(), b"ID3\x04binary-mp3")
        self.assertEqual(code, 0)


class SttFlow(unittest.TestCase):
    def test_upload_then_poll_until_done(self):
        polls = {"n": 0}

        def poll(_h):
            polls["n"] += 1
            if polls["n"] == 1:
                return 503, {}, b"{}"  # transient: keep polling
            if polls["n"] == 2:
                return 200, {"Content-Type": "application/json"}, b'{"generation_id": "g1", "status": "generating"}'
            return 200, {"Content-Type": "application/json"}, b'{"generation_id": "g1", "status": "completed", "result": {"text": "hello world"}}'

        routes = {
            ("POST", "/v1/stt/create"): lambda h: (200, {"Content-Type": "application/json"}, b'{"generation_id": "g1", "status": "queued"}'),
            ("GET", "/v1/stt/g1"): poll,
        }
        with tempfile.TemporaryDirectory() as tmp, LocalServer(routes) as srv:
            audio = os.path.join(tmp, "meeting.wav")
            Path(audio).write_bytes(b"RIFF....WAVE")
            args = types.SimpleNamespace(model="m/x", url=None, file=audio, field="audio", body='{"diarize": true}', no_wait=False,
                                         interval=0.01, timeout=10, out=None, json=False, sandbox=True)
            code, out = quiet(speech.cmd_stt, args)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "hello world")
        submit = srv.seen[0]
        self.assertTrue(submit["headers"]["Content-Type"].startswith("multipart/form-data; boundary="))
        self.assertEqual(submit["headers"].get("X-AIMLAPI-Sandbox"), "1")
        self.assertIn(b'name="audio"; filename="meeting.wav"', submit["body"])
        self.assertIn(b'name="diarize"\r\n\r\ntrue', submit["body"])

    def test_failed_job_exits_1(self):
        routes = {
            ("POST", "/v1/stt/create"): lambda h: (200, {"Content-Type": "application/json"}, b'{"generation_id": "g2"}'),
            ("GET", "/v1/stt/g2"): lambda h: (
                200, {"Content-Type": "application/json"},
                b'{"generation_id": "g2", "status": "error", "error": {"name": "Error", "message": "bad audio"}}',
            ),
        }
        with LocalServer(routes) as srv:
            args = types.SimpleNamespace(model="m/x", url="https://example.com/a.mp3", file=None, field=None, body=None, no_wait=False,
                                         interval=0.01, timeout=10, out=None, json=False, sandbox=False)
            code, out = quiet(speech.cmd_stt, args)
        self.assertEqual(code, 1)
        self.assertIn("bad audio", out)
        self.assertEqual(json.loads(srv.seen[0]["body"]), {"model": "m/x", "url": "https://example.com/a.mp3"})


if __name__ == "__main__":
    unittest.main()
