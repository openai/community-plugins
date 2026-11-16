"""Bounded, GET-only GitLab evidence collection (no repository or CI writes).

Only identifiers from a verified MR select subsequent API endpoints. Response
URLs, raw response bodies, job logs, and artifact contents never enter reports.
The Python-only ``allow_loopback`` switch is for controlled contract tests.

API contracts: https://docs.gitlab.com/api/merge_requests/,
https://docs.gitlab.com/api/pipelines/, https://docs.gitlab.com/api/jobs/,
https://docs.gitlab.com/api/job_artifacts/, https://docs.gitlab.com/api/rest/.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import queue
import re
import socket
import ssl
import threading
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_ARTIFACT_REQUESTS = 10
PER_PAGE = 100
_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
_MIME = re.compile(r"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+\Z")
_PIPELINE_STATES = {
    "created", "waiting_for_resource", "preparing", "pending", "running",
    "success", "failed", "canceled", "skipped", "manual", "scheduled",
}
_JOB_STATES = _PIPELINE_STATES | {"canceling", "waiting_for_callback"}
_PIPELINE_SOURCES = {
    "push", "web", "trigger", "schedule", "api", "external", "pipeline",
    "chat", "webide", "merge_request_event", "external_pull_request_event",
    "parent_pipeline", "ondemand_dast_scan", "ondemand_dast_validation",
    "security_orchestration_policy", "container_registry_push", "duo_workflow",
}


class _CoverageError(Exception):
    """Only constant safe messages may be carried to user-visible reports."""

    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _integer(value: object) -> int:
    if type(value) is not int or value <= 0 or value > 2**63 - 1:
        raise _CoverageError("invalid_identity", "GitLab returned an invalid numeric identity.")
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise _CoverageError("missing_refs", "GitLab did not provide complete commit references.")
    return value.lower()


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise _CoverageError("invalid_response", "GitLab returned an unexpected response structure.")
    return value


def _unique_json(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class GitLabClient:
    """Read one MR's exact-head CI evidence using an explicitly supplied token.

    ``timeout`` is the total collection deadline, not a per-page allowance.
    Each paginated collection is limited to ``max_pages``. No proxy or redirect
    configuration is inherited, and TLS verification uses the platform trust
    store. ``base_url`` must be an origin, not an API URL or arbitrary path.
    """

    def __init__(self, base_url, token, timeout=10, max_pages=20, allow_loopback=False):
        try:
            if not isinstance(base_url, str) or any(ord(c) < 33 for c in base_url):
                raise ValueError
            parsed = urlsplit(base_url)
            hostname, port = parsed.hostname, parsed.port
            if (
                not hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")
                or "%" in hostname or "\\" in base_url
            ):
                raise ValueError
            loopback = False
            try:
                loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                pass  # Hostnames do not qualify for the test-only HTTP bypass.
            if parsed.scheme != "https" and not (
                parsed.scheme == "http" and allow_loopback is True and loopback
            ):
                raise ValueError
            if port is not None and not 1 <= port <= 65535:
                raise ValueError
            if type(allow_loopback) is not bool:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("GitLab URL must be an HTTPS origin without credentials, path, query, or fragment.") from None
        if (
            not isinstance(token, str) or not 1 <= len(token) <= 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in token)
        ):
            raise ValueError("GitLab token must be a nonempty printable header value.")
        if (
            type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 0 < timeout <= 300
        ):
            raise ValueError("GitLab timeout must be between zero and 300 seconds.")
        if type(max_pages) is not int or not 1 <= max_pages <= 100:
            raise ValueError("GitLab max_pages must be an integer from 1 to 100.")
        self.base_url = base_url.rstrip("/")
        self.timeout, self.max_pages = float(timeout), max_pages
        self._scheme, self._host, self._port = parsed.scheme, hostname, port
        self._token = token
        self._deadline = 0.0
        self._bytes = 0

    def _text(self, value: object, limit: int = 160) -> str:
        if not isinstance(value, str):
            return ""
        # Only narrowly selected metadata is copied; never raw API text/errors.
        value = value.replace(self._token, "[REDACTED]")
        return "".join(c if 32 <= ord(c) < 127 else "?" for c in value)[:limit]

    def _request(self, path: str, limit: int = MAX_RESPONSE_BYTES) -> tuple[bytes, dict]:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise _CoverageError("timeout", "GitLab collection exceeded its time limit.")
        completed: queue.Queue = queue.Queue(maxsize=1)
        canceled = threading.Event()
        connection_type = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        options = {"timeout": remaining}
        if self._scheme == "https":
            options["context"] = ssl.create_default_context()
        connection = connection_type(self._host, self._port, **options)

        def fetch():
            outcome = None
            response = None
            try:
                connection.connect()
                if canceled.is_set():
                    return
                connection.request("GET", path, headers={
                    "PRIVATE-TOKEN": self._token,
                    "Accept": "application/json, application/octet-stream",
                    "Accept-Encoding": "identity",
                    "User-Agent": "devsecops-review/0.1",
                })
                response = connection.getresponse()
                if 300 <= response.status < 400:
                    raise _CoverageError("redirect_rejected", "GitLab redirected the request; redirects are not followed.", response.status)
                if response.status != 200:
                    raise _CoverageError("http_error", f"GitLab returned HTTP {response.status}; evidence is unavailable.", response.status)
                headers = {}
                for key, value in response.getheaders():
                    key = key.lower()
                    if key in headers:
                        if key == "link":
                            headers[key] += ", " + value
                        elif key in {"content-length", "content-encoding"} or key.startswith("x-"):
                            raise _CoverageError("invalid_response", "GitLab returned ambiguous response headers.")
                    else:
                        headers[key] = value
                if headers.get("content-encoding", "identity").lower() not in ("identity", ""):
                    raise _CoverageError("unsupported_encoding", "GitLab returned an unsupported content encoding.")
                length = headers.get("content-length")
                if length is not None:
                    if not re.fullmatch(r"[0-9]{1,12}", length):
                        raise _CoverageError("invalid_response", "GitLab returned an invalid content length.")
                    if int(length) > limit:
                        raise _CoverageError("response_limit", "GitLab response exceeded its size limit.")
                chunks, count = [], 0
                while not canceled.is_set():
                    chunk = response.read1(min(65536, limit + 1 - count))
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > limit:
                        raise _CoverageError("response_limit", "GitLab response exceeded its size limit.")
                    chunks.append(chunk)
                if canceled.is_set():
                    return
                if length is not None and count != int(length):
                    raise _CoverageError("truncated_response", "GitLab response ended before its declared size.")
                outcome = (b"".join(chunks), headers)
            except _CoverageError as error:
                outcome = error
            except (TimeoutError, socket.timeout):
                outcome = _CoverageError("timeout", "GitLab collection exceeded its time limit.")
            except (OSError, http.client.HTTPException, ValueError):
                outcome = _CoverageError("transport_error", "GitLab request failed; no response details were retained.")
            finally:
                if response is not None:
                    response.close()
                connection.close()
                if not canceled.is_set() and outcome is not None:
                    completed.put_nowait(outcome)

        # A daemon worker also bounds slow header/body delivery and DNS delays.
        # A timed-out connect cannot send credentials later: canceled is checked
        # immediately after connect. Active sockets are interrupted on timeout.
        threading.Thread(target=fetch, daemon=True, name="gitlab-evidence-get").start()
        try:
            outcome = completed.get(timeout=remaining)
        except queue.Empty:
            canceled.set()
            sock = connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()
            raise _CoverageError("timeout", "GitLab collection exceeded its time limit.") from None
        if isinstance(outcome, _CoverageError):
            raise outcome
        body, headers = outcome
        self._bytes += len(body)
        if self._bytes > MAX_TOTAL_BYTES:
            raise _CoverageError("total_response_limit", "GitLab collection exceeded its total size limit.")
        return body, headers

    def _json(self, path: str) -> tuple[object, dict]:
        body, headers = self._request(path)
        try:
            return json.loads(body, object_pairs_hook=_unique_json, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())), headers
        except (ValueError, RecursionError, UnicodeError):
            raise _CoverageError("invalid_json", "GitLab returned invalid or ambiguous JSON.") from None

    def _pages(self, path: str, query: dict | None = None) -> list[dict]:
        results, seen, expected_total = [], set(), None
        for page in range(1, self.max_pages + 1):
            parameters = {**(query or {}), "per_page": str(PER_PAGE), "page": str(page)}
            data, headers = self._json(path + "?" + urlencode(parameters))
            if not isinstance(data, list) or len(data) > PER_PAGE:
                raise _CoverageError("invalid_pagination", "GitLab returned an invalid page of results.")
            for item in data:
                item = _object(item)
                identity = _integer(item.get("id"))
                if identity in seen:
                    raise _CoverageError("unstable_pagination", "GitLab repeated an item while paging; coverage is incomplete.")
                seen.add(identity)
                results.append(item)
            for header, value in (("x-page", page), ("x-per-page", PER_PAGE)):
                if header in headers and headers[header] != str(value):
                    raise _CoverageError("invalid_pagination", "GitLab pagination metadata did not match the request.")
            total = headers.get("x-total")
            if total is not None:
                if not re.fullmatch(r"[0-9]{1,12}", total):
                    raise _CoverageError("invalid_pagination", "GitLab returned invalid pagination totals.")
                if expected_total is not None and expected_total != int(total):
                    raise _CoverageError("unstable_pagination", "GitLab result totals changed during collection.")
                expected_total = int(total)
            next_page = headers.get("x-next-page")
            link = headers.get("link", "")
            next_links = re.findall(r'<([^>]+)>\s*;\s*rel\s*=\s*"?next"?(?=\s*(?:,|$|;))', link, re.IGNORECASE)
            if len(next_links) > 1 or (re.search(r'\bnext\b', link) and not next_links):
                raise _CoverageError("invalid_pagination", "GitLab returned an unsupported pagination link.")
            if next_links:
                try:
                    parsed = urlsplit(next_links[0])
                except ValueError:
                    raise _CoverageError("invalid_pagination", "GitLab returned an invalid pagination URL.") from None
                origin = urlsplit(self.base_url)
                if (
                    parsed.scheme != origin.scheme or parsed.netloc != origin.netloc
                    or parsed.path != path or parsed.fragment
                ):
                    raise _CoverageError("invalid_pagination", "GitLab pagination attempted to change the requested endpoint.")
                linked = parse_qs(parsed.query, keep_blank_values=True)
                linked_page = linked.pop("page", [])
                expected = {key: [value] for key, value in parameters.items() if key != "page"}
                if linked != expected or linked_page != [str(page + 1)]:
                    raise _CoverageError("invalid_pagination", "GitLab pagination did not advance to the next page.")
                if next_page is not None and next_page != linked_page[0]:
                    raise _CoverageError("invalid_pagination", "GitLab pagination headers disagree.")
                next_page = linked_page[0]
            if next_page is not None and next_page not in ("", str(page + 1)):
                raise _CoverageError("invalid_pagination", "GitLab pagination did not advance to the next page.")
            total_pages = headers.get("x-total-pages")
            if total_pages is not None:
                if not re.fullmatch(r"[0-9]{1,12}", total_pages):
                    raise _CoverageError("invalid_pagination", "GitLab returned invalid pagination totals.")
                page_count = int(total_pages)
                if (
                    (page_count < page and not (page == 1 and page_count == 0 and not data))
                    or (page_count > page and next_page == "")
                    or (next_page and page_count <= page)
                ):
                    raise _CoverageError("invalid_pagination", "GitLab pagination totals disagree.")
                if next_page is None:
                    next_page = str(page + 1) if page_count > page else ""
            finished = next_page == "" or (next_page is None and len(data) < PER_PAGE)
            if finished:
                if expected_total is not None and len(results) != expected_total:
                    raise _CoverageError("incomplete_pagination", "GitLab enumeration did not cover the reported result count.")
                return results
        raise _CoverageError("page_limit", "GitLab pagination exceeded the configured page limit.")

    @staticmethod
    def _mr(value: object, project_id: int, iid: int) -> dict:
        mr = _object(value)
        if (
            _integer(mr.get("iid")) != iid or _integer(mr.get("project_id")) != project_id
            or _integer(mr.get("target_project_id")) != project_id
        ):
            raise _CoverageError("mr_mismatch", "GitLab returned a different merge request or target project.")
        refs = _object(mr.get("diff_refs"))
        refs = {key: _sha(refs.get(key)) for key in ("base_sha", "head_sha", "start_sha")}
        if _sha(mr.get("sha")) != refs["head_sha"]:
            raise _CoverageError("mr_mismatch", "Merge-request SHA and diff references disagree.")
        return {
            "id": _integer(mr.get("id")), "iid": iid, "project_id": project_id,
            "source_project_id": _integer(mr.get("source_project_id")), "refs": refs,
            "source_branch": mr.get("source_branch"), "target_branch": mr.get("target_branch"),
        }

    def _pipeline(self, value: object, project_id: int, pipeline_id: int, head: str) -> dict:
        data = _object(value)
        if (
            _integer(data.get("id")) != pipeline_id or _integer(data.get("project_id")) != project_id
            or _sha(data.get("sha")) != head
        ):
            raise _CoverageError("pipeline_mismatch", "Pipeline detail does not match the selected project, pipeline, and MR head.")
        status, source = data.get("status"), data.get("source")
        if not isinstance(status, str) or not isinstance(source, str) or status not in _PIPELINE_STATES or source not in _PIPELINE_SOURCES:
            raise _CoverageError("unsupported_pipeline", "Pipeline status or source is missing or unsupported.")
        return {
            "id": pipeline_id, "project_id": project_id, "sha": head,
            "status": status, "source": source, "ref": self._text(data.get("ref")),
        }

    def _job(self, value: object, pipeline: dict) -> dict:
        data = _object(value)
        owner = _object(data.get("pipeline"))
        if (
            _integer(owner.get("id")) != pipeline["id"]
            or _integer(owner.get("project_id")) != pipeline["project_id"]
            or _sha(owner.get("sha")) != pipeline["sha"]
        ):
            raise _CoverageError("job_mismatch", "Job ownership does not match the selected project, pipeline, and MR head.")
        if "commit" in data and _sha(_object(data["commit"]).get("id")) != pipeline["sha"]:
            raise _CoverageError("job_mismatch", "Job commit does not match the selected MR head.")
        if not isinstance(data.get("status"), str) or data["status"] not in _JOB_STATES or type(data.get("allow_failure")) is not bool:
            raise _CoverageError("invalid_job", "Job status or failure policy is missing or unsupported.")
        return {
            "id": _integer(data.get("id")), "project_id": pipeline["project_id"],
            "pipeline_id": pipeline["id"], "sha": pipeline["sha"],
            "status": data["status"], "allow_failure": data["allow_failure"],
            "name": self._text(data.get("name")), "stage": self._text(data.get("stage")),
        }

    @staticmethod
    def _artifact_requests(value: object) -> list[tuple[int, str]]:
        if value is None:
            return []
        if not isinstance(value, list) or len(value) > MAX_ARTIFACT_REQUESTS:
            raise _CoverageError("invalid_artifact_request", "Artifact requests must be a bounded list of job IDs and relative paths.")
        result = []
        for request in value:
            if not isinstance(request, dict) or set(request) != {"job_id", "path"}:
                raise _CoverageError("invalid_artifact_request", "Each artifact request needs only a job ID and a relative path.")
            path = request["path"]
            if (
                not isinstance(path, str) or not 1 <= len(path) <= 1024
                or any(ord(c) < 32 or ord(c) == 127 for c in path)
                or any(c in path for c in "\\%?#:")
                or any(part in ("", ".", "..") for part in path.split("/"))
            ):
                raise _CoverageError("invalid_artifact_request", "Artifact paths must identify a single relative file without traversal.")
            item = (_integer(request["job_id"]), path)
            if item in result:
                raise _CoverageError("invalid_artifact_request", "Artifact requests must not contain duplicates.")
            result.append(item)
        return result

    def collect(self, project, mr_iid, artifact_requests=None) -> dict:
        """Return sanitized evidence; incomplete association/coverage is unknown."""
        self._deadline, self._bytes = time.monotonic() + self.timeout, 0
        result = {"refs": {}, "decision": "unknown", "pipeline": {}, "jobs": [], "artifacts": [], "gaps": []}
        before, mr_path = None, None

        def gap(error):
            item = {"code": error.code, "message": error.message}
            if item not in result["gaps"]:
                result["gaps"].append(item)

        try:
            requested = self._artifact_requests(artifact_requests)
            if type(mr_iid) is str and re.fullmatch(r"[1-9][0-9]{0,18}", mr_iid):
                mr_iid = int(mr_iid)
            iid = _integer(mr_iid)
            if type(project) is int:
                project = str(_integer(project))
            if (
                not isinstance(project, str) or not 1 <= len(project) <= 1024
                or any(ord(c) < 33 or ord(c) > 126 for c in project)
                or any(c in project for c in "\\%?#:")
                or any(part in ("", ".", "..") for part in project.split("/"))
            ):
                raise _CoverageError("invalid_project", "GitLab project must be a numeric ID or a namespace/project path.")
            project_data = _object(self._json("/api/v4/projects/" + quote(project, safe=""))[0])
            project_id = _integer(project_data.get("id"))
            if (project.isdecimal() and project_id != int(project)) or (
                not project.isdecimal() and project_data.get("path_with_namespace") != project
            ):
                raise _CoverageError("project_mismatch", "GitLab returned a different project than requested.")
            mr_path = f"/api/v4/projects/{project_id}/merge_requests/{iid}"
            before = self._mr(self._json(mr_path)[0], project_id, iid)
            result["refs"] = before["refs"].copy()
            result["project_id"], result["mr_iid"] = project_id, iid
            pipelines = self._pages(mr_path + "/pipelines")
            candidates = []
            for item in pipelines:
                owner = _integer(item.get("project_id"))
                if owner not in {project_id, before["source_project_id"]}:
                    raise _CoverageError("pipeline_mismatch", "MR pipeline belongs to an unrelated project.")
                if _sha(item.get("sha")) == before["refs"]["head_sha"]:
                    candidates.append(item)
            if not candidates:
                raise _CoverageError("no_head_pipeline", "No pipeline in the complete MR list matches the exact candidate SHA; merged-result and other SHAs are not substituted.")
            selected = max(candidates, key=lambda item: item["id"])
            pipeline_id, owner = selected["id"], selected["project_id"]
            if any(
                item["id"] > pipeline_id
                and re.search(r"(?:^|/)merge-requests/[0-9]+/(?:merge|train)(?:$|/)", self._text(item.get("ref")))
                for item in pipelines
            ):
                gap(_CoverageError("newer_merged_result", "A newer merged-result pipeline tests a different SHA; its result is not covered by the selected exact-head pipeline."))
            pipeline_path = f"/api/v4/projects/{owner}/pipelines/{pipeline_id}"
            pipeline = self._pipeline(self._json(pipeline_path)[0], owner, pipeline_id, before["refs"]["head_sha"])
            result["pipeline"] = pipeline
            if pipeline["source"] in {"parent_pipeline", "pipeline"}:
                gap(_CoverageError("downstream_pipeline", "Selected pipeline is a child or downstream pipeline; parent and sibling coverage is unverified."))
            if re.search(r"(?:^|/)merge-requests/[0-9]+/(?:merge|train)(?:$|/)", pipeline["ref"]):
                gap(_CoverageError("merged_result_pipeline", "Merged-result pipeline semantics are outside exact-head CI coverage."))
            jobs = self._pages(pipeline_path + "/jobs", {"include_retried": "false"})
            result["jobs"] = [self._job(job, pipeline) for job in jobs]
            if not jobs:
                gap(_CoverageError("no_jobs", "The selected pipeline returned no jobs; job coverage cannot be established."))
            try:
                triggers = self._pages(pipeline_path + "/trigger_jobs")
            except _CoverageError as error:
                if error.status != 404:
                    raise
                triggers = self._pages(pipeline_path + "/bridges")
            if triggers:
                gap(_CoverageError("downstream_unverified", "Pipeline has trigger jobs; child or multi-project pipeline evidence is not collected in v0.1."))
            for job_id, artifact_path in requested:
                item = {"job_id": job_id, "path": self._text(artifact_path, 1024), "available": False}
                result["artifacts"].append(item)
                if job_id not in {job["id"] for job in result["jobs"]}:
                    gap(_CoverageError("artifact_job_mismatch", "Requested artifact job is not among the verified jobs of the selected pipeline."))
                    item["error"] = "artifact_job_mismatch"
                    continue
                item.update({"project_id": owner, "pipeline_id": pipeline_id, "sha": pipeline["sha"]})
                try:
                    body, headers = self._request(
                        f"/api/v4/projects/{owner}/jobs/{job_id}/artifacts/" + quote(artifact_path, safe="/"),
                        MAX_ARTIFACT_BYTES,
                    )
                    content_type = headers.get("content-type", "").split(";", 1)[0].strip()
                    item.update({"available": True, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
                    if _MIME.fullmatch(content_type):
                        item["content_type"] = self._text(content_type, 100)
                    del body
                except _CoverageError as error:
                    item["error"] = error.code
                    gap(error)
            latest_detail = self._pipeline(self._json(pipeline_path)[0], owner, pipeline_id, before["refs"]["head_sha"])
            if latest_detail != pipeline:
                gap(_CoverageError("pipeline_changed", "Selected pipeline status or identity changed while collecting jobs and artifacts."))
            status = pipeline["status"]
            if status == "success":
                unsettled = {"created", "waiting_for_resource", "preparing", "pending", "running", "canceled", "canceling", "waiting_for_callback", "scheduled"}
                if any(job["status"] in unsettled or (job["status"] in {"failed", "manual"} and not job["allow_failure"]) for job in result["jobs"]):
                    gap(_CoverageError("inconsistent_job_status", "Successful pipeline contains unresolved or unsuccessful required jobs."))
            elif status != "failed":
                gap(_CoverageError("ci_unresolved", "The exact-head pipeline has not completed successfully or failed definitively."))
        except _CoverageError as error:
            gap(error)
        finally:
            if before is not None and mr_path is not None:
                try:
                    after = self._mr(self._json(mr_path)[0], before["project_id"], before["iid"])
                    if before != after:
                        gap(_CoverageError("mr_changed", "Merge-request identity or commit references changed during collection; evidence association is stale."))
                except _CoverageError as error:
                    gap(error)
        if not result["gaps"] and result["pipeline"]:
            result["decision"] = {"success": "pass", "failed": "fail"}.get(result["pipeline"]["status"], "unknown")
        result["status"] = "complete" if result["decision"] != "unknown" else "partial"
        return result
