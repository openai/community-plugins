"""Real-tool review regressions.

Set DEVSECOPS_HELM, DEVSECOPS_KUBECONFORM and DEVSECOPS_SCHEMAS to run the
integration cases. Pure presentation assertions run without those dependencies.
"""

from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "src"))

from devsecops.presentation import render_html, render_markdown  # noqa: E402


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=120, check=False)


class PresentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = {
            "schema_version": 1,
            "plugin_version": "0.1.0",
            "status": "partial",
            "decision": "unknown",
            "scope": {"repo": "/local/repo", "base": "a" * 40, "head": "WORKTREE", "profile_id": "generic", "profile_sha256": "b" * 64},
            "summary": {"findings": 1, "high_findings": 1, "required_gaps": 1},
            "targets": [{
                "name": "web",
                "type": "helm",
                "status": "partial",
                "decision": "unknown",
                "findings": [{
                    "rule_id": "demo-rule",
                    "severity": "high",
                    "resource": "Deployment/demo/web",
                    "field": "spec.template.spec.containers[0]",
                    "message": "Privilege increased",
                    "evidence": {"field": "privileged", "value": True},
                    "change": "introduced",
                    "remediation": "Restore the least-privilege setting.",
                }],
                "inventory": {"base_count": 8, "head_count": 8},
                "validation": [{"tool": "helm", "status": "pass"}],
                "receipts": [],
                "changes": [],
                "gaps": ["A required schema is unavailable"],
            }],
            "ci": None,
            "gaps": ["A required schema is unavailable"],
        }

    def test_reports_preserve_uncertainty(self) -> None:
        page = render_html(self.report)
        markdown = render_markdown(self.report)
        for result in [page, markdown]:
            self.assertIn("Review needs more evidence", result)
            self.assertIn("Privilege increased", result)
            self.assertIn("schema is unavailable", result)
        self.assertIn("default-src 'none'", page)
        self.assertNotIn("<script", page)
        self.assertIn("prefers-color-scheme:dark", page)

    def test_evidence_cannot_inject_html_or_markdown(self) -> None:
        attack = '<script>alert("bad")</script> [click](https://evil.invalid)'
        self.report["scope"]["repo"] = attack
        self.report["targets"][0]["findings"][0]["message"] = attack
        self.report["targets"][0]["findings"][0]["evidence"] = {"payload": attack}
        self.report["ci"] = {"status": attack}
        page = render_html(self.report)
        markdown = render_markdown(self.report)
        self.assertNotIn("<script>", page)
        self.assertNotIn("<script>", markdown)
        self.assertNotIn('<a href="https://evil.invalid"', page)
        self.assertNotIn("[click](https://evil.invalid)", markdown)
        self.assertIn("&lt;script&gt;", page)

    def test_rendering_does_not_mutate_authoritative_report(self) -> None:
        original = copy.deepcopy(self.report)
        self.assertEqual(render_html(self.report), render_html(self.report))
        self.assertEqual(render_markdown(self.report), render_markdown(self.report))
        self.assertEqual(original, self.report)

    def test_severity_override_is_visible_without_erasing_original(self) -> None:
        finding = self.report["targets"][0]["findings"][0]
        finding.update(original_severity="high", effective_severity="low", severity="low")
        page = render_html(self.report)
        markdown = render_markdown(self.report)
        self.assertIn("Original severity: HIGH", page)
        self.assertIn("Effective severity: LOW", page)
        self.assertIn("Original severity: high; effective severity: low", markdown)
        self.assertIn("Privilege increased", page)

    def test_applied_exception_is_explained_with_owner_reason_expiry_and_scope(self) -> None:
        target = self.report["targets"][0]
        self.report.update(decision="pass", status="complete")
        target.update(decision="pass", status="complete", policy_evaluated_at="2026-10-21")
        finding = target["findings"][0]
        finding.update(
            original_severity="high", effective_severity="high", policy_suppressed=True,
            policy_exception_ids=["EXC-DEMO-1"],
        )
        target["applied_exceptions"] = [{
            "id": "EXC-DEMO-1", "owner": "Platform reviewer", "reason": "Synthetic evaluation only",
            "expires": "2026-11-01", "target": "web", "rule_id": "demo-rule",
            "resource": "apps/Deployment/demo/web", "matched_findings": 1,
        }]
        for result in (render_html(self.report), render_markdown(self.report)):
            for value in ("Applied policy exceptions", "EXC-DEMO-1", "Platform reviewer",
                          "Synthetic evaluation only", "2026-11-01", "2026-10-21",
                          "Privilege increased", "Excluded"):
                self.assertIn(value, result)

    def test_exception_reason_and_owner_cannot_inject_markup(self) -> None:
        target = self.report["targets"][0]
        target["applied_exceptions"] = [{
            "id": "EXC-1", "owner": '<img src=x onerror="bad">',
            "reason": "[approve](https://evil.invalid)", "expires": "2026-11-01",
        }]
        self.assertNotIn("<img", render_html(self.report))
        self.assertNotIn("[approve](https://evil.invalid)", render_markdown(self.report))


@unittest.skipUnless(
    all(os.environ.get(key) for key in ("DEVSECOPS_HELM", "DEVSECOPS_KUBECONFORM", "DEVSECOPS_SCHEMAS")),
    "Real Helm, Kubeconform and local schemas must be supplied via DEVSECOPS_* variables",
)
class ReviewEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="devsecops-e2e-")
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.repo = self.work / "repo"
        shutil.copytree(FIXTURES / "helm-web", self.repo)
        for command in [
            ("git", "init", "-q"),
            ("git", "config", "user.name", "DevSecOps fixture"),
            ("git", "config", "user.email", "fixture@example.invalid"),
            ("git", "add", "."),
            ("git", "commit", "-qm", "Synthetic baseline"),
        ]:
            result = run(*command, cwd=self.repo)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.base = run("git", "rev-parse", "HEAD", cwd=self.repo).stdout.strip()
        self.profile = self.work / "profile.json"
        self.profile_data = {
            "schema_version": 1,
            "id": "fixture",
            "revision": "1",
            "targets": [{
                "name": "demo",
                "type": "helm",
                "path": "chart",
                "values": ["values/staging.yaml"],
                "namespace": "demo",
                "release": "demo",
                "kubernetes_version": "1.34.0",
                "api_versions": [],
                "mode": "upgrade",
            }],
            "policy": {"require_image_digest": True, "allowed_registries": ["registry.example.invalid"], "required_labels": ["team"]},
        }
        self.profile.write_text(json.dumps(self.profile_data), encoding="utf-8")

    def review(self, output_name: str = "review", *, extra: list[str] | None = None) -> tuple[subprocess.CompletedProcess[str], dict, Path]:
        output = self.work / output_name
        args = [
            sys.executable, str(ROOT / "scripts" / "devsecops_review.py"), "review",
            "--repo", str(self.repo), "--base", self.base, "--head", "WORKTREE",
            "--profile", str(self.profile), "--target", "demo",
            "--output", str(output), "--helm", os.environ["DEVSECOPS_HELM"],
            "--kubeconform", os.environ["DEVSECOPS_KUBECONFORM"],
            "--schema-location", os.environ["DEVSECOPS_SCHEMAS"],
        ]
        result = run(*(args + (extra or [])), cwd=ROOT)
        report_path = output / "report.json"
        self.assertTrue(report_path.exists(), f"No report: exit={result.returncode}; stderr={result.stderr}; stdout={result.stdout}")
        return result, json.loads(report_path.read_text(encoding="utf-8")), output

    def write_values(self, changes: dict) -> None:
        path = self.repo / "values" / "staging.yaml"
        values = yaml.safe_load(path.read_text(encoding="utf-8"))
        values.update(changes)
        path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")

    def test_unchanged_baseline_passes_real_local_validation(self) -> None:
        result, report, output = self.review()
        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(report["status"], "complete", report)
        self.assertEqual(report["decision"], "pass", report)
        self.assertEqual(report["summary"]["findings"], 0, report)
        target = report["targets"][0]
        self.assertEqual(target["inventory"]["base_count"], target["inventory"]["head_count"])
        self.assertGreaterEqual(target["inventory"]["head_count"], 7)
        for artifact in ("report.md", "report.html", "run-manifest.json"):
            self.assertTrue((output / artifact).is_file(), artifact)

    def test_risky_change_has_evidence_and_never_leaks_secret_payload(self) -> None:
        changes = yaml.safe_load((self.repo / "values" / "risky.yaml").read_text(encoding="utf-8"))
        secret = "SYNTHETIC_NEVER_REPORT_THIS_20d672ac"
        changes["secret"] = {"enabled": True, "token": secret}
        self.write_values(changes)
        result, report, output = self.review()
        self.assertEqual(result.returncode, 1, report)
        self.assertEqual(report["decision"], "fail", report)
        findings = report["targets"][0]["findings"]
        self.assertTrue(any(f["severity"] in ("high", "critical") for f in findings), findings)
        text = json.dumps(findings).lower()
        for risk in ("privileg", "image", "rbac"):
            self.assertIn(risk, text)
        self.assertTrue(all(f.get("rule_id") and f.get("resource") and f.get("remediation") for f in findings))
        encoded = base64.b64encode(secret.encode()).decode()
        combined = result.stdout + result.stderr
        for artifact in output.rglob("*"):
            if artifact.is_file():
                combined += artifact.read_text(encoding="utf-8", errors="replace")
        self.assertNotIn(secret, combined)
        self.assertNotIn(encoded, combined)

    def test_invalid_rendered_resource_cannot_pass(self) -> None:
        self.write_values({"replicaCount": "not-an-integer"})
        result, report, _ = self.review()
        self.assertIn(result.returncode, (1, 2), report)
        self.assertNotEqual(report["decision"], "pass", report)

    def test_unknown_custom_resource_schema_is_coverage_gap(self) -> None:
        custom = self.repo / "chart" / "templates" / "custom.yaml"
        custom.write_text(
            "apiVersion: fixture.example.invalid/v1\nkind: SyntheticReviewThing\n"
            "metadata:\n  name: example\nspec:\n  enabled: true\n",
            encoding="utf-8",
        )
        result, report, _ = self.review()
        self.assertEqual(result.returncode, 2, report)
        self.assertEqual(report["decision"], "unknown", report)
        self.assertGreater(report["summary"]["required_gaps"], 0, report)

    def test_review_does_not_modify_checkout_or_git_index(self) -> None:
        self.write_values({"replicaCount": 3})
        before = run("git", "status", "--porcelain=v1", cwd=self.repo).stdout
        tracked = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(self.repo).parts}
        self.review()
        after = run("git", "status", "--porcelain=v1", cwd=self.repo).stdout
        self.assertEqual(before, after)
        for path, content in tracked.items():
            self.assertEqual(content, (self.repo / path).read_bytes(), str(path))


if __name__ == "__main__":
    unittest.main()
