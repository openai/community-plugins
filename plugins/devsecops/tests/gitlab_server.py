"""Controlled loopback GitLab API fixture; requests are recorded without tokens.

Usage::

    routes = standard_routes(base_sha=base, head_sha=head)
    with GitLabServer(routes) as server:
        client = GitLabClient(server.url, synthetic_token, allow_loopback=True)

Route keys may include a query string. A route is a Response, JSON value, or a
callable receiving a sanitized request record. A list of Response objects is a
response sequence (the final response repeats).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit


@dataclass
class Response:
    body: object = None
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0
    chunk_size: int = 0
    chunk_delay: float = 0
    omit_length: bool = False


def standard_routes(
    *, base_sha="1" * 40, head_sha="2" * 40, start_sha=None,
    project_id=42, mr_iid=7, pipeline_id=100, job_id=101,
    pipeline_status="success", project_path="group/project",
):
    start_sha = start_sha or base_sha
    project = {"id": project_id, "path_with_namespace": project_path}
    mr = {
        "id": 700, "iid": mr_iid, "project_id": project_id,
        "source_project_id": project_id, "target_project_id": project_id,
        "sha": head_sha, "diff_refs": {
            "base_sha": base_sha, "head_sha": head_sha, "start_sha": start_sha,
        }, "source_branch": "candidate", "target_branch": "main",
    }
    pipeline = {
        "id": pipeline_id, "project_id": project_id, "sha": head_sha,
        "ref": f"refs/merge-requests/{mr_iid}/head",
        "status": pipeline_status, "source": "merge_request_event",
    }
    job = {
        "id": job_id, "name": "manifest-validation", "stage": "test",
        "status": "failed" if pipeline_status == "failed" else "success",
        "allow_failure": False, "commit": {"id": head_sha},
        "pipeline": pipeline.copy(),
    }
    base = f"/api/v4/projects/{project_id}"
    return {
        base: project,
        "/api/v4/projects/" + project_path.replace("/", "%2F"): project,
        f"{base}/merge_requests/{mr_iid}": mr,
        f"{base}/merge_requests/{mr_iid}/pipelines": [pipeline],
        f"{base}/pipelines/{pipeline_id}": pipeline,
        f"{base}/pipelines/{pipeline_id}/jobs": [job],
        f"{base}/pipelines/{pipeline_id}/trigger_jobs": [],
        f"{base}/pipelines/{pipeline_id}/bridges": [],
    }


class GitLabServer:
    def __init__(self, routes=None, *, expected_token=None):
        self.routes = standard_routes() if routes is None else routes
        self.expected_token = expected_token
        self.requests = []
        self._counts = {}
        self._server = None

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def dispatch(self):
                parsed = urlsplit(self.path)
                record = {
                    "method": self.command, "target": self.path, "path": parsed.path,
                    "query": parse_qs(parsed.query, keep_blank_values=True),
                    "token_present": "PRIVATE-TOKEN" in self.headers,
                    "token_matches": fixture.expected_token is not None
                    and self.headers.get("PRIVATE-TOKEN") == fixture.expected_token,
                }
                fixture.requests.append(record)
                key = self.path if self.path in fixture.routes else parsed.path
                value = fixture.routes.get(key, Response({"message": "not found"}, status=404))
                if self.command != "GET":
                    value = Response({"message": "GET only"}, status=405)
                if callable(value):
                    value = value(record)
                if isinstance(value, list) and value and all(isinstance(item, Response) for item in value):
                    index = fixture._counts.get(key, 0)
                    fixture._counts[key] = index + 1
                    value = value[min(index, len(value) - 1)]
                response = value if isinstance(value, Response) else Response(value)
                body = response.body
                if not isinstance(body, bytes):
                    body = json.dumps(body).encode("utf-8")
                if response.delay:
                    time.sleep(response.delay)
                try:
                    self.send_response(response.status)
                    if not response.omit_length and "Content-Length" not in response.headers:
                        self.send_header("Content-Length", str(len(body)))
                    self.send_header("Content-Type", "application/json")
                    for header, value in response.headers.items():
                        if header.lower() != "content-type":
                            self.send_header(header, value)
                    self.end_headers()
                    if response.chunk_size:
                        for offset in range(0, len(body), response.chunk_size):
                            self.wfile.write(body[offset : offset + response.chunk_size])
                            self.wfile.flush()
                            time.sleep(response.chunk_delay)
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            do_GET = dispatch
            do_POST = dispatch
            do_PUT = dispatch
            do_PATCH = dispatch
            do_DELETE = dispatch
            do_HEAD = dispatch

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=lambda: self._server.serve_forever(poll_interval=0.01), daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
