"""Real Helm/kubeconform review bound to a controlled GET-only GitLab API.

Run with DEVSECOPS_HELM, DEVSECOPS_KUBECONFORM, and DEVSECOPS_SCHEMAS.
The injected HTTP transport is Python-only; CLI configuration remains HTTPS.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

from devsecops.gitlab import GitLabClient
from gitlab_server import GitLabServer, Response, standard_routes


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "synthetic-e2e-credential"
MR_PATH = "/api/v4/projects/42/merge_requests/7"


@unittest.skipUnless(
    all(os.environ.get(key) for key in ("DEVSECOPS_HELM", "DEVSECOPS_KUBECONFORM", "DEVSECOPS_SCHEMAS")),
    "Real Helm, kubeconform, and local schemas must be supplied via DEVSECOPS_* variables",
)
class GitLabRealToolsEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="devsecops-gitlab-e2e-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.repo = self.work / "repository with spaces"
        shutil.copytree(ROOT / "tests" / "fixtures" / "helm-web", self.repo)
        for args in (
            ("init", "-q"), ("config", "user.name", "Synthetic E2E fixture"),
            ("config", "user.email", "fixture@example.invalid"), ("add", "."),
            ("commit", "-qm", "Synthetic baseline"),
        ):
            self.git(*args)
        self.base = self.git("rev-parse", "HEAD").strip()
        self.profile = self.work / "trusted-profile.json"
        self.profile.write_text(json.dumps({
            "schema_version": 1, "id": "gitlab-fixture", "revision": "1",
            "targets": [{
                "name": "web", "type": "helm", "path": "chart",
                "release": "web", "namespace": "demo", "mode": "upgrade",
                "values": ["values/staging.yaml"], "kubernetes_version": "1.34.0",
                "api_versions": [],
            }],
            "policy": {
                "require_image_digest": True,
                "allowed_registries": ["registry.example.invalid"], "required_labels": ["team"],
            },
        }), encoding="utf-8")

    def git(self, *args):
        result = subprocess.run(
            ["git", *args], cwd=self.repo, text=True, capture_output=True,
            timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def candidate(self, *, risky=False, changes=None):
        path = self.repo / "values" / "staging.yaml"
        values = yaml.safe_load(path.read_text(encoding="utf-8"))
        values.update(
            yaml.safe_load((self.repo / "values" / "risky.yaml").read_text(encoding="utf-8"))
            if risky else {"replicaCount": 3}
        )
        values.update(changes or {})
        path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
        self.git("add", "values/staging.yaml")
        self.git("commit", "-qm", "Synthetic candidate")
        return self.git("rev-parse", "HEAD").strip()

    def run_review(self, routes, *, extra_args=None):
        from devsecops.cli import build_parser, write_report
        from devsecops.engine import review

        output = self.work / "review-output"
        args = build_parser().parse_args([
            "review", "--repo", str(self.repo), "--profile", str(self.profile),
            "--output", str(output), "--gitlab-url", "https://gitlab.example.invalid",
            "--gitlab-project", "42", "--gitlab-mr", "7",
            "--helm", os.environ["DEVSECOPS_HELM"],
            "--kubeconform", os.environ["DEVSECOPS_KUBECONFORM"],
            "--schema-location", os.environ["DEVSECOPS_SCHEMAS"], *(extra_args or []),
        ])
        with GitLabServer(routes, expected_token=TOKEN) as server:
            client = GitLabClient(server.url, TOKEN, timeout=10, allow_loopback=True)
            report = review(args, ci_client=client)
            write_report(report, output)
            requests = list(server.requests)
        self.assertTrue(requests)
        self.assertTrue(all(record["method"] == "GET" for record in requests))
        self.assertTrue(all(record["token_matches"] for record in requests))
        self.assertFalse(any("/repository/" in record["path"] for record in requests))
        self.assertNotIn(TOKEN, json.dumps(report))
        for filename in ("report.json", "report.md", "report.html", "run-manifest.json"):
            path = output / filename
            self.assertTrue(path.is_file(), filename)
            self.assertNotIn(TOKEN, path.read_text(encoding="utf-8"))
        self.assertEqual(json.loads((output / "report.json").read_text(encoding="utf-8")), report)
        manifest = json.loads((output / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["finished"])
        self.assertEqual(manifest["decision"], report["decision"])
        for name, metadata in manifest["artifacts"].items():
            raw = (output / name).read_bytes()
            self.assertEqual(metadata["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(metadata["bytes"], len(raw))
        return report

    def assert_real_tools(self, report):
        receipts = report["targets"][0]["receipts"]
        operations = {receipt["operation"] for receipt in receipts}
        self.assertIn("helm-template", operations)
        self.assertIn("kubeconform", operations)
        self.assertTrue(all(receipt.get("stdout_sha256") for receipt in receipts))

    def test_exact_gitlab_refs_and_real_tools_pass_without_mutation(self):
        head = self.candidate()
        dirty = self.repo / "chart" / "values.yaml"
        dirty.write_text(dirty.read_text(encoding="utf-8") + "\n# Uncommitted unrelated note\n", encoding="utf-8")
        files = {path.relative_to(self.repo): path.read_bytes() for path in self.repo.rglob("*") if path.is_file() and ".git" not in path.relative_to(self.repo).parts}
        before_status = self.git("status", "--porcelain=v1")
        before_index = hashlib.sha256((self.repo / ".git" / "index").read_bytes()).hexdigest()
        before_config = (self.repo / ".git" / "config").read_bytes()
        report = self.run_review(standard_routes(base_sha=self.base, head_sha=head))
        self.assertEqual(report["decision"], "pass", report)
        self.assertEqual(report["ci"]["decision"], "pass", report)
        self.assertEqual(report["scope"]["base"], self.base)
        self.assertEqual(report["scope"]["head"], head)
        self.assert_real_tools(report)
        self.assertEqual(self.git("status", "--porcelain=v1"), before_status)
        self.assertEqual(hashlib.sha256((self.repo / ".git" / "index").read_bytes()).hexdigest(), before_index)
        self.assertEqual((self.repo / ".git" / "config").read_bytes(), before_config)
        for name, content in files.items():
            self.assertEqual((self.repo / name).read_bytes(), content)

    def test_successful_ci_does_not_hide_static_risk(self):
        head = self.candidate(risky=True)
        report = self.run_review(standard_routes(base_sha=self.base, head_sha=head))
        self.assertEqual(report["ci"]["decision"], "pass", report)
        self.assertEqual(report["decision"], "fail", report)
        self.assertEqual(report["targets"][0]["decision"], "fail", report)
        self.assertTrue(report["targets"][0]["findings"])
        self.assert_real_tools(report)

    def test_stale_ci_is_unknown_and_retains_independent_static_failure(self):
        head = self.candidate(risky=True)
        routes = standard_routes(base_sha=self.base, head_sha=head)
        initial = routes[MR_PATH]
        changed = {**initial, "sha": "a" * 40, "diff_refs": {**initial["diff_refs"], "head_sha": "a" * 40}}
        routes[MR_PATH] = [Response(initial), Response(changed)]
        report = self.run_review(routes)
        self.assertEqual(report["decision"], "unknown", report)
        self.assertEqual(report["ci"]["decision"], "unknown", report)
        self.assertIn("mr_changed", {gap["code"] for gap in report["ci"]["gaps"]})
        self.assertEqual(report["targets"][0]["decision"], "fail", report)
        self.assertTrue(report["targets"][0]["findings"])
        self.assert_real_tools(report)

    def test_missing_exact_head_ci_cannot_pass_static_success(self):
        head = self.candidate()
        routes = standard_routes(base_sha=self.base, head_sha=head)
        routes[MR_PATH + "/pipelines"] = []
        report = self.run_review(routes)
        self.assertEqual(report["decision"], "unknown", report)
        self.assertEqual(report["ci"]["decision"], "unknown", report)
        self.assertEqual(report["targets"][0]["decision"], "pass", report)
        self.assert_real_tools(report)

    def test_failed_exact_head_ci_fails_independently_of_static_pass(self):
        head = self.candidate()
        report = self.run_review(standard_routes(base_sha=self.base, head_sha=head, pipeline_status="failed"))
        self.assertEqual(report["decision"], "fail", report)
        self.assertEqual(report["ci"]["decision"], "fail", report)
        self.assertEqual(report["targets"][0]["decision"], "pass", report)
        self.assert_real_tools(report)

    def test_explicit_local_revision_cannot_inherit_different_commit_ci(self):
        head = self.candidate()
        report = self.run_review(
            standard_routes(base_sha=self.base, head_sha=head),
            extra_args=["--base", self.base, "--head", self.base],
        )
        self.assertEqual(report["scope"]["head"], self.base)
        self.assertEqual(report["decision"], "unknown", report)
        self.assertEqual(report["targets"][0]["decision"], "pass", report)
        self.assertIn("ci-revision-mismatch", {gap["code"] for gap in report["ci"]["gaps"]})
        self.assert_real_tools(report)

    def test_opt_in_verified_job_artifact_persists_digest_without_body(self):
        head = self.candidate()
        routes = standard_routes(base_sha=self.base, head_sha=head)
        payload = b"synthetic-private-artifact-body-not-for-the-report"
        routes["/api/v4/projects/42/jobs/101/artifacts/reports/result.txt"] = Response(payload)
        report = self.run_review(routes, extra_args=["--artifact", "101:reports/result.txt"])
        self.assertEqual(report["decision"], "pass", report)
        self.assertEqual(len(report["ci"]["artifacts"]), 1)
        artifact = report["ci"]["artifacts"][0]
        self.assertEqual(artifact["sha256"], hashlib.sha256(payload).hexdigest())
        for path in (self.work / "review-output").iterdir():
            self.assertNotIn(payload, path.read_bytes())
        self.assert_real_tools(report)

    def apply_synthetic_exception(self):
        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        profile["policy"]["exceptions"] = [{
            "id": "synthetic-approved-privilege", "rule_id": "K8S-PRIVILEGED", "target": "web",
            "resource": "apps/Deployment/demo/web-web", "reason": "Synthetic evaluation fixture only.",
            "owner": "Synthetic platform owner", "expires": "9999-12-31",
        }]
        self.profile.write_text(json.dumps(profile), encoding="utf-8")

    def test_exact_policy_exception_is_applied_with_real_tools_and_visible_evidence(self):
        head = self.candidate(changes={"securityContext": {"privileged": True}})
        self.apply_synthetic_exception()
        report = self.run_review(standard_routes(base_sha=self.base, head_sha=head))
        self.assertEqual(report["decision"], "pass", report)
        target = report["targets"][0]
        item = next(item for item in target["findings"] if item["rule_id"] == "K8S-PRIVILEGED")
        self.assertTrue(item["policy_suppressed"])
        self.assertEqual(item["original_severity"], "high")
        self.assertTrue(item["evidence"])
        self.assertEqual(target["applied_exceptions"][0]["id"], "synthetic-approved-privilege")
        self.assert_real_tools(report)

    def test_policy_exception_cannot_suppress_independent_schema_failure(self):
        head = self.candidate(changes={"securityContext": {"privileged": True}, "replicaCount": "invalid"})
        self.apply_synthetic_exception()
        report = self.run_review(standard_routes(base_sha=self.base, head_sha=head))
        self.assertEqual(report["ci"]["decision"], "pass", report)
        self.assertNotEqual(report["decision"], "pass", report)
        self.assertTrue(any(item["counts"]["invalid"] for item in report["targets"][0]["validation"]))
        self.assert_real_tools(report)


if __name__ == "__main__":
    unittest.main()
