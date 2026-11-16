from __future__ import annotations

import copy
import hashlib
import json
import time
import unittest
from unittest.mock import patch

from devsecops.gitlab import GitLabClient, MAX_ARTIFACT_BYTES, MAX_RESPONSE_BYTES
from gitlab_server import GitLabServer, Response, standard_routes


TOKEN = "synthetic-test-credential-do-not-use"
PROJECT = "/api/v4/projects/42"
MR = PROJECT + "/merge_requests/7"
PIPELINES = MR + "/pipelines"
PIPELINE = PROJECT + "/pipelines/100"
JOBS = PIPELINE + "/jobs"
TRIGGERS = PIPELINE + "/trigger_jobs"
ARTIFACT = PROJECT + "/jobs/101/artifacts/reports/check.json"


class GitLabContractTests(unittest.TestCase):
    def collect(self, routes=None, *, artifacts=None, project=42, timeout=3, max_pages=20):
        with GitLabServer(routes, expected_token=TOKEN) as server:
            client = GitLabClient(server.url, TOKEN, timeout=timeout, max_pages=max_pages, allow_loopback=True)
            result = client.collect(project, 7, artifacts)
            requests = list(server.requests)
        self.assertTrue(all(request["method"] == "GET" for request in requests))
        self.assertTrue(all(request["token_matches"] for request in requests))
        self.assertNotIn(TOKEN, json.dumps(result))
        return result, requests

    def assert_gap(self, result, code):
        self.assertEqual(result["decision"], "unknown")
        self.assertIn(code, {gap["code"] for gap in result["gaps"]})

    def test_exact_head_success_and_two_mr_reads(self):
        result, requests = self.collect()
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["refs"]["head_sha"], "2" * 40)
        self.assertEqual(result["pipeline"]["source"], "merge_request_event")
        self.assertEqual(result["jobs"][0]["pipeline_id"], 100)
        self.assertEqual(sum(request["path"] == MR for request in requests), 2)

    def test_path_project_is_encoded_and_verified(self):
        result, requests = self.collect(project="group/project")
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(requests[0]["path"], "/api/v4/projects/group%2Fproject")

    def test_failed_pipeline_fails(self):
        result, _ = self.collect(standard_routes(pipeline_status="failed"))
        self.assertEqual(result["decision"], "fail")

    def test_unresolved_pipeline_states_remain_distinct(self):
        for status in ("created", "pending", "running", "canceled", "skipped", "manual", "scheduled"):
            with self.subTest(status=status):
                result, _ = self.collect(standard_routes(pipeline_status=status))
                self.assert_gap(result, "ci_unresolved")
                self.assertEqual(result["pipeline"]["status"], status)

    def test_missing_pipeline_is_unknown(self):
        routes = standard_routes()
        routes[PIPELINES] = []
        result, _ = self.collect(routes)
        self.assert_gap(result, "no_head_pipeline")

    def test_merged_result_sha_does_not_substitute_for_candidate(self):
        routes = standard_routes()
        routes[PIPELINES] = [{**routes[PIPELINE], "sha": "3" * 40, "ref": "refs/merge-requests/7/merge"}]
        result, _ = self.collect(routes)
        self.assert_gap(result, "no_head_pipeline")

    def test_newer_merged_result_is_an_explicit_gap(self):
        routes = standard_routes()
        routes[PIPELINES].append({**routes[PIPELINE], "id": 105, "sha": "3" * 40, "ref": "refs/merge-requests/7/merge"})
        result, _ = self.collect(routes)
        self.assert_gap(result, "newer_merged_result")
        self.assertEqual(result["pipeline"]["id"], 100)

    def test_pipeline_race_is_not_a_success(self):
        routes = standard_routes()
        routes[PIPELINE] = [Response(routes[PIPELINE]), Response({**routes[PIPELINE], "status": "running"})]
        result, _ = self.collect(routes)
        self.assert_gap(result, "pipeline_changed")

    def test_pipeline_and_job_pagination_choose_latest_exact_head(self):
        routes = standard_routes()
        newer = {**routes[PIPELINE], "id": 102}
        old = {**routes[PIPELINE], "id": 99, "sha": "1" * 40}

        def pipelines(request):
            page = request["query"]["page"][0]
            return Response([old, routes[PIPELINE]] if page == "1" else [newer], headers={"X-Next-Page": "2" if page == "1" else "", "X-Total": "3"})

        routes[PIPELINES] = pipelines
        new_path = PROJECT + "/pipelines/102"
        routes[new_path] = newer
        job = {**routes[JOBS][0], "pipeline": newer}

        def jobs(request):
            page = request["query"]["page"][0]
            return Response([job if page == "1" else {**job, "id": 103}], headers={"X-Next-Page": "2" if page == "1" else ""})

        routes[new_path + "/jobs"] = jobs
        routes[new_path + "/trigger_jobs"] = []
        result, requests = self.collect(routes)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["pipeline"]["id"], 102)
        self.assertEqual([job["id"] for job in result["jobs"]], [101, 103])
        self.assertEqual(sum(request["path"] == PIPELINES for request in requests), 2)

    def test_exact_head_pipeline_in_source_fork_is_supported(self):
        routes = standard_routes()
        routes[MR]["source_project_id"] = 51
        pipeline = {**routes[PIPELINE], "project_id": 51}
        routes[PIPELINES] = [pipeline]
        path = "/api/v4/projects/51/pipelines/100"
        routes[path] = pipeline
        routes[path + "/jobs"] = [{**routes[JOBS][0], "pipeline": pipeline}]
        routes[path + "/trigger_jobs"] = []
        result, _ = self.collect(routes)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(result["pipeline"]["project_id"], 51)

    def test_mr_identity_and_refs_are_verified(self):
        for field, value in (("iid", 8), ("project_id", 99), ("target_project_id", 99), ("sha", "3" * 40)):
            with self.subTest(field=field):
                routes = standard_routes()
                routes[MR][field] = value
                result, _ = self.collect(routes)
                self.assert_gap(result, "mr_mismatch")

    def test_missing_diff_refs_does_not_crash_or_pass(self):
        routes = standard_routes()
        routes[MR]["diff_refs"] = {}
        result, _ = self.collect(routes)
        self.assert_gap(result, "missing_refs")

    def test_mr_race_invalidates_even_failed_pipeline(self):
        routes = standard_routes(pipeline_status="failed")
        after = copy.deepcopy(routes[MR])
        after["sha"] = after["diff_refs"]["head_sha"] = "3" * 40
        routes[MR] = [Response(routes[MR]), Response(after)]
        result, _ = self.collect(routes)
        self.assert_gap(result, "mr_changed")
        self.assertEqual(result["pipeline"]["status"], "failed")

    def test_changed_base_without_head_change_invalidates(self):
        routes = standard_routes()
        after = copy.deepcopy(routes[MR])
        after["diff_refs"]["base_sha"] = "4" * 40
        routes[MR] = [Response(routes[MR]), Response(after)]
        result, _ = self.collect(routes)
        self.assert_gap(result, "mr_changed")

    def test_pipeline_detail_and_job_ownership_mismatch(self):
        for owner_field, value in (("id", 2), ("project_id", 2), ("sha", "3" * 40)):
            for location in ("pipeline", "job"):
                with self.subTest(field=owner_field, location=location):
                    routes = standard_routes()
                    if location == "pipeline":
                        routes[PIPELINE] = {**routes[PIPELINE], owner_field: value}
                    else:
                        routes[JOBS][0]["pipeline"][owner_field] = value
                    result, _ = self.collect(routes)
                    self.assert_gap(result, "pipeline_mismatch" if location == "pipeline" else "job_mismatch")

    def test_unrelated_project_pipeline_is_unknown(self):
        routes = standard_routes()
        routes[PIPELINES] = [{**routes[PIPELINE], "project_id": 999}]
        result, _ = self.collect(routes)
        self.assert_gap(result, "pipeline_mismatch")

    def test_empty_jobs_and_inconsistent_job_state_are_unknown(self):
        routes = standard_routes()
        routes[JOBS] = []
        result, _ = self.collect(routes)
        self.assert_gap(result, "no_jobs")
        routes = standard_routes()
        routes[JOBS][0]["status"] = "running"
        result, _ = self.collect(routes)
        self.assert_gap(result, "inconsistent_job_status")

    def test_allowed_failure_and_skipped_jobs_preserve_status(self):
        routes = standard_routes()
        routes[JOBS][0].update({"status": "failed", "allow_failure": True})
        routes[JOBS].append({**routes[JOBS][0], "id": 102, "status": "skipped"})
        result, _ = self.collect(routes)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual([job["status"] for job in result["jobs"]], ["failed", "skipped"])

    def test_child_pipeline_is_explicitly_unknown(self):
        routes = standard_routes()
        routes[PIPELINE] = {**routes[PIPELINE], "source": "parent_pipeline"}
        result, _ = self.collect(routes)
        self.assert_gap(result, "downstream_pipeline")

    def test_trigger_job_creates_downstream_gap(self):
        routes = standard_routes()
        routes[TRIGGERS] = [{"id": 999, "downstream_pipeline": {"id": 200, "project_id": 42, "sha": "2" * 40}}]
        result, _ = self.collect(routes)
        self.assert_gap(result, "downstream_unverified")

    def test_old_gitlab_uses_bridges_only_after_404(self):
        routes = standard_routes()
        routes[TRIGGERS] = Response(status=404)
        result, requests = self.collect(routes)
        self.assertEqual(result["decision"], "pass")
        self.assertIn(PIPELINE + "/bridges", [request["path"] for request in requests])
        routes[TRIGGERS] = Response(status=403)
        result, requests = self.collect(routes)
        self.assert_gap(result, "http_error")
        self.assertNotIn(PIPELINE + "/bridges", [request["path"] for request in requests])

    def test_header_pagination_limit_and_duplicates(self):
        routes = standard_routes()
        routes[PIPELINES] = Response([routes[PIPELINE]], headers={"X-Next-Page": "2"})
        result, _ = self.collect(routes, max_pages=1)
        self.assert_gap(result, "page_limit")
        result, _ = self.collect(routes)
        self.assert_gap(result, "unstable_pagination")

    def test_full_page_without_headers_continues_until_empty(self):
        routes = standard_routes()
        entries = [{**routes[PIPELINE], "id": index, "sha": "1" * 40} for index in range(1, 100)] + [routes[PIPELINE]]
        routes[PIPELINES] = lambda request: entries if request["query"]["page"] == ["1"] else []
        result, requests = self.collect(routes)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(sum(request["path"] == PIPELINES for request in requests), 2)

    def test_pagination_links_never_change_origin(self):
        routes = standard_routes()
        routes[PIPELINES] = Response([routes[PIPELINE]], headers={"Link": '<https://attacker.invalid/token>; rel="next"'})
        result, requests = self.collect(routes)
        self.assert_gap(result, "invalid_pagination")
        self.assertTrue(all("attacker" not in request["target"] for request in requests))

    def test_valid_link_only_pagination(self):
        routes = standard_routes()
        with GitLabServer(routes, expected_token=TOKEN) as server:
            routes[PIPELINES] = lambda request: Response([routes[PIPELINE]], headers={"Link": f'<{server.url}{PIPELINES}?page=2&per_page=100>; rel="next"'}) if request["query"]["page"] == ["1"] else []
            result = GitLabClient(server.url, TOKEN, allow_loopback=True).collect(42, 7)
        self.assertEqual(result["decision"], "pass")

    def test_pagination_header_mismatch_and_total_truncation(self):
        for headers in ({"X-Next-Page": "1"}, {"X-Page": "9"}, {"X-Next-Page": "", "X-Total": "2"}):
            with self.subTest(headers=headers):
                routes = standard_routes()
                routes[PIPELINES] = Response([routes[PIPELINE]], headers=headers)
                result, _ = self.collect(routes)
                self.assertEqual(result["decision"], "unknown")

    def test_invalid_pagination_url_does_not_escape_as_an_exception(self):
        routes = standard_routes()
        routes[PIPELINES] = Response([routes[PIPELINE]], headers={"Link": '<http://[invalid/path>; rel="next"'})
        result, _ = self.collect(routes)
        self.assert_gap(result, "invalid_pagination")

    def test_total_pages_without_next_header_are_followed(self):
        routes = standard_routes()
        routes[PIPELINES] = lambda request: Response([routes[PIPELINE]] if request["query"]["page"] == ["1"] else [{**routes[PIPELINE], "id": 99}], headers={"X-Total-Pages": "2"})
        result, requests = self.collect(routes)
        self.assertEqual(result["decision"], "pass")
        self.assertEqual(sum(request["path"] == PIPELINES for request in requests), 2)

    def test_redirect_rejected_for_api_and_artifacts(self):
        for endpoint in (PROJECT, ARTIFACT):
            with self.subTest(endpoint=endpoint):
                routes = standard_routes()
                routes[endpoint] = Response(status=302, headers={"Location": "https://attacker.invalid/" + TOKEN})
                result, requests = self.collect(routes, artifacts=[{"job_id": 101, "path": "reports/check.json"}])
                self.assert_gap(result, "redirect_rejected")
                self.assertTrue(all("attacker" not in request["target"] for request in requests))

    def test_errors_do_not_disclose_response_bodies(self):
        for status in (401, 403, 404, 429, 500):
            with self.subTest(status=status):
                routes = standard_routes()
                routes[PIPELINES] = Response({"message": TOKEN, "other": "secret-response-marker"}, status=status)
                result, _ = self.collect(routes)
                self.assert_gap(result, "http_error")
                self.assertNotIn("secret-response-marker", json.dumps(result))

    def test_invalid_json_duplicate_keys_and_body_limit(self):
        for body, code in ((b'{"id":42,"id":43}', "invalid_json"), (b"not json " + TOKEN.encode(), "invalid_json"), (b"x" * (MAX_RESPONSE_BYTES + 1), "response_limit")):
            with self.subTest(code=code):
                routes = standard_routes()
                routes[PROJECT] = Response(body)
                result, _ = self.collect(routes)
                self.assert_gap(result, code)

    def test_malformed_status_structures_are_unknown(self):
        for location, key, code in ((PIPELINE, "status", "unsupported_pipeline"), (PIPELINE, "source", "unsupported_pipeline"), (JOBS, "status", "invalid_job")):
            with self.subTest(location=location, key=key):
                routes = standard_routes()
                if location == JOBS:
                    routes[JOBS][0][key] = []
                else:
                    routes[PIPELINE] = {**routes[PIPELINE], key: []}
                result, _ = self.collect(routes)
                self.assert_gap(result, code)

    def test_unknown_encoding_and_truncated_response(self):
        routes = standard_routes()
        routes[PROJECT] = Response(b"{}", headers={"Content-Encoding": "gzip"})
        result, _ = self.collect(routes)
        self.assert_gap(result, "unsupported_encoding")
        routes[PROJECT] = Response(b"{}", headers={"Content-Length": "10"})
        result, _ = self.collect(routes)
        self.assert_gap(result, "truncated_response")

    def test_deadline_covers_slow_drip_body(self):
        routes = standard_routes()
        routes[PROJECT] = Response({"id": 42}, chunk_size=1, chunk_delay=0.05)
        started = time.monotonic()
        result, _ = self.collect(routes, timeout=0.15)
        self.assert_gap(result, "timeout")
        self.assertLess(time.monotonic() - started, 1.5)

    def test_total_response_limit(self):
        with patch("devsecops.gitlab.MAX_TOTAL_BYTES", 10):
            result, _ = self.collect()
        self.assert_gap(result, "total_response_limit")

    def test_artifact_is_bound_hashed_and_never_returned(self):
        routes = standard_routes()
        artifact = b'{"secret":"sensitive-artifact-marker"}'
        routes[ARTIFACT] = Response(artifact)
        result, _ = self.collect(routes, artifacts=[{"job_id": 101, "path": "reports/check.json"}])
        self.assertEqual(result["decision"], "pass")
        metadata = result["artifacts"][0]
        self.assertEqual(metadata["sha256"], hashlib.sha256(artifact).hexdigest())
        self.assertEqual(metadata["bytes"], len(artifact))
        self.assertEqual(metadata["pipeline_id"], 100)
        self.assertNotIn("sensitive-artifact-marker", json.dumps(result))

    def test_wrong_artifact_job_is_never_requested(self):
        result, requests = self.collect(artifacts=[{"job_id": 999, "path": "reports/check.json"}])
        self.assert_gap(result, "artifact_job_mismatch")
        self.assertFalse(any("/artifacts/" in request["path"] for request in requests))

    def test_expired_and_oversized_artifacts_are_unknown(self):
        for response, code in ((Response(status=404), "http_error"), (Response(b"x" * (MAX_ARTIFACT_BYTES + 1)), "response_limit")):
            routes = standard_routes()
            routes[ARTIFACT] = response
            result, _ = self.collect(routes, artifacts=[{"job_id": 101, "path": "reports/check.json"}])
            self.assert_gap(result, code)
            self.assertFalse(result["artifacts"][0]["available"])

    def test_metadata_token_echo_is_redacted(self):
        routes = standard_routes()
        routes[JOBS][0]["name"] = "echo " + TOKEN
        result, _ = self.collect(routes)
        self.assertEqual(result["decision"], "pass")
        self.assertIn("[REDACTED]", result["jobs"][0]["name"])

    def test_invalid_requests_make_no_network_calls(self):
        for artifact_path in ("../secret", "/file", "reports/../file", "reports//file", "https://other/file", "file%2fsecret", "file?secret", "file\\secret"):
            with self.subTest(path=artifact_path):
                result, requests = self.collect(artifacts=[{"job_id": 101, "path": artifact_path}])
                self.assert_gap(result, "invalid_artifact_request")
                self.assertEqual(requests, [])

    def test_constructor_rejects_unsafe_configuration_without_echoing_token(self):
        invalid = [
            {"base_url": "http://example.com"}, {"base_url": "http://localhost", "allow_loopback": True},
            {"base_url": "https://user:" + TOKEN + "@gitlab.example"},
            {"base_url": "https://gitlab.example/api/v4"}, {"base_url": "https://gitlab.example?q=" + TOKEN},
            {"base_url": "https://gitlab.example/#fragment"}, {"token": TOKEN + "\nheader"},
            {"token": ""}, {"timeout": 0}, {"timeout": float("inf")}, {"timeout": True},
            {"max_pages": 0}, {"max_pages": True},
        ]
        for kwargs in invalid:
            with self.subTest(keys=list(kwargs)):
                args = {"base_url": "https://gitlab.example", "token": TOKEN, **kwargs}
                with self.assertRaises(ValueError) as context:
                    GitLabClient(**args)
                self.assertNotIn(TOKEN, str(context.exception))


if __name__ == "__main__":
    unittest.main()
