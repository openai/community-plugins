"""Policy contract tests: exact scoped exceptions cannot hide missing evidence."""

from __future__ import annotations

import ast
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
import unittest

from devsecops.policy import KNOWN_RULE_IDS, evaluate_findings, validate_policy


TODAY = date(2026, 10, 21)
RESOURCE = "apps/Deployment/demo/web"


def finding(**changes):
    return {
        "rule_id": "K8S-PRIVILEGED", "severity": "high", "change": "introduced",
        "resource": RESOURCE, "source_type": "deterministic", "fingerprint": "fingerprint-1",
        "field": "/spec/template/spec/containers/0/securityContext/privileged",
        "evidence": {"source": "chart", "side": "head", "pointer": "/field"},
        "message": "Declared privilege", "remediation": "Review the requirement.", **changes,
    }


def exception(**changes):
    return {
        "id": "platform-123", "rule_id": "K8S-PRIVILEGED", "target": "web",
        "resource": RESOURCE, "reason": "Approved synthetic requirement for this workload.",
        "owner": "Synthetic platform team", "expires": "2026-10-21", **changes,
    }


def evaluate(findings=None, policy=None, target="web", now=TODAY):
    return evaluate_findings([finding()] if findings is None else findings, policy or {}, target, now=now)


class PolicyEvaluationTests(unittest.TestCase):
    def test_default_gate_only_new_or_worsened_high_findings(self):
        for change, severity, expected in (
            ("introduced", "high", True), ("worsened", "critical", True),
            ("introduced", "medium", False), ("preexisting", "high", False),
            ("resolved", "critical", False),
        ):
            with self.subTest(change=change, severity=severity):
                self.assertEqual(evaluate([finding(change=change, severity=severity)])["policy_failure"], expected)

    def test_configurable_gate_checks_exact_threshold_and_selected_classes(self):
        policy = {"gate": {"minimum_severity": "medium", "changes": ["preexisting"]}}
        self.assertTrue(evaluate([finding(change="preexisting", severity="medium")], policy)["policy_failure"])
        self.assertFalse(evaluate([finding(change="preexisting", severity="low")], policy)["policy_failure"])
        self.assertFalse(evaluate([finding(change="introduced", severity="critical")], policy)["policy_failure"])
        self.assertFalse(evaluate(policy={"gate": {"minimum_severity": "critical"}})["policy_failure"])
        self.assertTrue(evaluate([finding(severity="low")], {"gate": {"minimum_severity": "low"}})["policy_failure"])

    def test_overrides_preserve_original_severity_and_change_evidence(self):
        source = [finding()]
        original = deepcopy(source)
        result = evaluate(source, {"rule_severities": {"K8S-PRIVILEGED": "low"}})
        item = result["findings"][0]
        self.assertEqual(source, original)
        self.assertEqual(item["original_severity"], "high")
        self.assertEqual(item["effective_severity"], "low")
        self.assertEqual(item["severity"], "low")
        self.assertEqual(item["change"], "introduced")
        self.assertEqual(item["evidence"], original[0]["evidence"])
        self.assertFalse(result["policy_failure"])
        item["evidence"]["side"] = "changed-copy"
        self.assertEqual(source, original)

    def test_override_can_promote_advisory_finding_to_gate(self):
        result = evaluate([finding(rule_id="K8S-HELM-HOOK", severity="medium")], {
            "rule_severities": {"K8S-HELM-HOOK": "critical"},
        })
        self.assertTrue(result["policy_failure"])
        self.assertEqual(result["findings"][0]["original_severity"], "medium")

    def test_reevaluation_keeps_original_rule_severity(self):
        result = evaluate(policy={"rule_severities": {"K8S-PRIVILEGED": "low"}})
        repeated = evaluate(result["findings"], {"rule_severities": {"K8S-PRIVILEGED": "medium"}})
        self.assertEqual(repeated["findings"][0]["original_severity"], "high")
        self.assertEqual(repeated["findings"][0]["effective_severity"], "medium")

    def test_active_exact_exception_preserves_finding_and_full_traceability(self):
        policy = {"exceptions": [exception()]}
        before = deepcopy(policy)
        result = evaluate(policy=policy)
        self.assertEqual(policy, before)
        self.assertFalse(result["policy_failure"])
        self.assertEqual(result["evaluated_at"], "2026-10-21")
        item = result["findings"][0]
        self.assertEqual(item["severity"], "high")
        self.assertTrue(item["policy_suppressed"])
        self.assertEqual(item["policy_exception_ids"], ["platform-123"])
        self.assertEqual(item["evidence"], finding()["evidence"])
        applied = result["applied_exceptions"][0]
        for key, value in exception().items():
            self.assertEqual(applied[key], value)
        self.assertEqual(applied["matched_findings"], 1)
        self.assertEqual(applied["finding_fingerprints"], ["fingerprint-1"])
        self.assertFalse(result["gaps"])

    def test_exception_expires_after_its_calendar_date(self):
        policy = {"exceptions": [exception()]}
        active = evaluate(policy=policy, now=date(2026, 10, 21))
        expired = evaluate(policy=policy, now=date(2026, 10, 22))
        self.assertFalse(active["policy_failure"])
        self.assertTrue(expired["policy_failure"])
        self.assertFalse(expired["findings"][0]["policy_suppressed"])
        self.assertFalse(expired["applied_exceptions"])
        self.assertEqual(expired["gaps"][0]["code"], "policy-exception-expired")
        self.assertFalse(expired["gaps"][0]["required"])

    def test_exact_scope_prevents_rule_namespace_resource_and_target_bleed(self):
        policy = {"exceptions": [exception()]}
        for changes in (
            {"rule_id": "K8S-HOST-ACCESS"}, {"resource": "apps/Deployment/prod/web"},
            {"resource": "apps/Deployment/demo/web-other"}, {"resource": "core/Pod/demo/web"},
        ):
            with self.subTest(changes=changes):
                result = evaluate([finding(**changes)], policy)
                self.assertTrue(result["policy_failure"])
                self.assertFalse(result["findings"][0]["policy_suppressed"])
                self.assertEqual(result["gaps"][0]["code"], "policy-exception-unmatched")
                self.assertFalse(result["gaps"][0]["required"])
        other_target = evaluate(policy=policy, target="other")
        self.assertTrue(other_target["policy_failure"])
        self.assertFalse(other_target["gaps"])

    def test_exception_cannot_suppress_another_unexcepted_failure(self):
        result = evaluate([finding(), finding(rule_id="K8S-HOST-ACCESS")], {"exceptions": [exception()]})
        self.assertTrue(result["policy_failure"])
        self.assertEqual([item["policy_suppressed"] for item in result["findings"]], [True, False])

    def test_multiple_matching_exceptions_and_finding_anchors_remain_visible(self):
        result = evaluate(
            [finding(), finding(fingerprint="fingerprint-2", field="/other")],
            {"exceptions": [exception(), exception(id="platform-456")]},
        )
        self.assertFalse(result["policy_failure"])
        self.assertEqual(len(result["applied_exceptions"]), 2)
        self.assertEqual(result["applied_exceptions"][0]["matched_findings"], 2)
        self.assertEqual(result["applied_exceptions"][0]["finding_fingerprints"], ["fingerprint-1", "fingerprint-2"])
        self.assertEqual(result["findings"][0]["policy_exception_ids"], ["platform-123", "platform-456"])

    def test_resolved_finding_does_not_apply_exception_or_fail_gate(self):
        result = evaluate([finding(change="resolved")], {"exceptions": [exception()]})
        self.assertFalse(result["policy_failure"])
        self.assertFalse(result["findings"][0]["policy_suppressed"])
        self.assertEqual(result["gaps"][0]["code"], "policy-exception-unmatched")

    def test_reasoned_observations_never_change_machine_gate(self):
        result = evaluate([finding(source_type="reasoned", severity="critical")], {
            "exceptions": [exception()], "rule_severities": {"K8S-PRIVILEGED": "low"},
        })
        self.assertFalse(result["policy_failure"])
        self.assertFalse(result["findings"][0]["policy_suppressed"])
        self.assertEqual(result["findings"][0]["severity"], "critical")
        self.assertFalse(result["applied_exceptions"])

    def test_empty_finding_list_does_not_create_an_exception_failure(self):
        result = evaluate([], {"exceptions": [exception()]})
        self.assertFalse(result["policy_failure"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["gaps"][0]["code"], "policy-exception-unmatched")
        self.assertFalse(result["gaps"][0]["required"])


class PolicyValidationTests(unittest.TestCase):
    def test_rule_registry_tracks_real_deterministic_rules(self):
        module = ast.parse((Path(__file__).resolve().parents[1] / "src/devsecops/checks.py").read_text())
        actual = {node.value for node in ast.walk(module) if isinstance(node, ast.Constant)
                  and isinstance(node.value, str) and node.value.startswith(("K8S-", "POLICY-"))}
        self.assertEqual(KNOWN_RULE_IDS, actual, "Update the policy registry when adding or retiring deterministic rules")

    def test_existing_image_and_label_configuration_remains_valid(self):
        validate_policy({"require_image_digest": True, "allowed_registries": ["registry.example.invalid:443"],
                         "required_labels": ["example.invalid/team"]})
        validate_policy({})

    def test_unknown_or_execution_rule_ids_cannot_be_overridden_or_excepted(self):
        for rule in ("UNKNOWN-RULE", "schema-unavailable", "helm-render-failed", "gitlab-unavailable"):
            with self.subTest(rule=rule):
                with self.assertRaises(ValueError):
                    validate_policy({"rule_severities": {rule: "low"}})
                with self.assertRaises(ValueError):
                    validate_policy({"exceptions": [exception(rule_id=rule)]})

    def test_expiry_requires_canonical_real_calendar_date(self):
        for expiry in ("2026-1-1", "2026-02-30", "20261021", "2026-10-21T00:00:00Z", "0000-01-01", "2026-10-21\n", 20261021, None):
            with self.subTest(expiry=expiry):
                with self.assertRaises(ValueError):
                    validate_policy({"exceptions": [exception(expires=expiry)]})
        validate_policy({"exceptions": [exception(expires="2028-02-29")]})

    def test_duplicate_exception_ids_are_invalid_even_across_targets(self):
        with self.assertRaises(ValueError):
            validate_policy({"exceptions": [exception(), exception(target="other")]})

    def test_exception_scope_forbids_wildcards_and_regular_expressions(self):
        for field, value in (
            ("target", "*"), ("target", "web.*"), ("target", "web|other"),
            ("resource", "apps/Deployment/*/web"), ("resource", "apps/Deployment/demo/web.*"),
            ("resource", "apps/Deployment/demo/web[12]"), ("resource", "^apps/Deployment/demo/web$"),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    validate_policy({"exceptions": [exception(**{field: value})]})
        validate_policy({"exceptions": [exception(resource="rbac.authorization.k8s.io/ClusterRole/<cluster>/reader")]})

    def test_gate_configuration_rejects_invalid_thresholds_and_changes(self):
        for gate in (
            [], {"minimum_severity": "none"}, {"minimum_severity": {}},
            {"changes": []}, {"changes": "introduced"}, {"changes": ["resolved"]},
            {"changes": ["introduced", "introduced"]}, {"changes": [{}]}, {"command": "anything"},
        ):
            with self.subTest(gate=gate):
                with self.assertRaises(ValueError):
                    validate_policy({"gate": gate})

    def test_malformed_configuration_raises_sanitized_value_errors(self):
        sentinel = "SYNTHETIC_SENSITIVE_VALIDATION_SENTINEL"
        invalid = (
            None, [], {sentinel: True}, {"require_image_digest": 1},
            {"allowed_registries": [None]}, {"required_labels": ["*\n"]},
            {"rule_severities": []}, {"rule_severities": {sentinel: "high"}},
            {"rule_severities": {"K8S-PRIVILEGED": sentinel}}, {"exceptions": {}},
            {"exceptions": [None]}, {"exceptions": [exception(id=sentinel + "\n")]},
            {"exceptions": [exception(owner="")]}, {"exceptions": [exception(reason=" ")]},
            {"exceptions": [exception(reason=sentinel + "\n")]},
            {"exceptions": [exception(extra=sentinel)]},
        )
        for policy in invalid:
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError) as raised:
                    validate_policy(policy)
                self.assertNotIn(sentinel, str(raised.exception))

    def test_invalid_finding_classification_cannot_silently_pass(self):
        for invalid in (
            finding(rule_id="UNKNOWN"), finding(severity="informational"), finding(change="other"),
            finding(resource=""), finding(original_severity="invalid"), None,
        ):
            with self.subTest(finding=invalid):
                with self.assertRaises(ValueError):
                    evaluate([invalid])

    def test_date_injection_rejects_datetime_and_non_dates(self):
        for now in (datetime(2026, 10, 21), "2026-10-21", 1):
            with self.assertRaises(ValueError):
                evaluate(now=now)


if __name__ == "__main__":
    unittest.main()
