"""Evaluate deterministic findings without suppressing missing validation evidence.

Policy is trusted configuration. Exceptions match exact target, rule, and resource
identities and remain visible in the report; they never remove a finding. This
module receives findings only, never schema, renderer, transport, or coverage
errors. The caller must aggregate those independent failures and gaps separately.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import re


SEVERITIES = ("low", "medium", "high", "critical")
CHANGE_CLASSES = ("introduced", "worsened", "preexisting")
KNOWN_RULE_IDS = frozenset({
    "K8S-CAPABILITIES", "K8S-CRD-LIFECYCLE", "K8S-DEPLOYMENT-SELECTOR",
    "K8S-DESTRUCTIVE-REMOVAL", "K8S-ESCALATION", "K8S-HELM-HOOK",
    "K8S-HOST-ACCESS", "K8S-HOSTPATH", "K8S-IMAGE-MUTABLE",
    "K8S-IMMUTABLE-ROLE-REF", "K8S-IMMUTABLE-SELECTOR", "K8S-NETPOL-ALLOW-ALL",
    "K8S-NETPOL-CHANGE", "K8S-NONROOT-WEAKENING", "K8S-PRIVILEGED",
    "K8S-PVC-MIGRATION", "K8S-RBAC-BINDING-CHANGE", "K8S-RBAC-CLUSTER-ADMIN",
    "K8S-RBAC-DELEGATION", "K8S-RBAC-IMPERSONATE", "K8S-RBAC-SECRETS",
    "K8S-RBAC-SENSITIVE-BINDING", "K8S-RBAC-TOKEN", "K8S-RBAC-WILDCARD",
    "K8S-ROOT-USER", "K8S-ROOTFS-WEAKENING", "K8S-SECCOMP-WEAKENING",
    "K8S-SERVICE-EXPOSURE", "K8S-SERVICE-EXTERNAL-IPS", "K8S-SERVICE-SELECTOR",
    "K8S-TOKEN-AUTOMOUNT", "POLICY-IMAGE-DIGEST", "POLICY-IMAGE-REGISTRY",
    "POLICY-REQUIRED-LABEL",
})
_POLICY_KEYS = frozenset({
    "require_image_digest", "allowed_registries", "required_labels",
    "gate", "rule_severities", "exceptions",
})
_EXCEPTION_KEYS = frozenset({"id", "rule_id", "target", "resource", "reason", "owner", "expires"})
_TARGET = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
_EXCEPTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_RESOURCE = re.compile(
    r"(?:core|[a-z0-9][a-z0-9.-]*)/[A-Za-z][A-Za-z0-9]*/"
    r"(?:[A-Za-z0-9_.:-]+|<cluster>)/[A-Za-z0-9_.:-]+\Z"
)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


def _text(value, maximum):
    return (
        isinstance(value, str) and 0 < len(value) <= maximum and bool(value.strip())
        and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)
    )


def _expiry(value):
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValueError("Exception expiry must be a canonical calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Exception expiry must be a valid calendar date") from None


def validate_policy(policy: dict) -> None:
    """Validate configuration with fixed error messages that never echo inputs."""
    if not isinstance(policy, dict) or set(policy) - _POLICY_KEYS:
        raise ValueError("Policy contains unsupported configuration")
    if "require_image_digest" in policy and type(policy["require_image_digest"]) is not bool:
        raise ValueError("Image digest policy must be boolean")
    for key, pattern in (
        ("allowed_registries", r"[a-zA-Z0-9.:-]{1,253}"),
        ("required_labels", r"[A-Za-z0-9._/-]{1,253}"),
    ):
        values = policy.get(key, [])
        if not isinstance(values, list) or len(values) > 100 or any(
            not isinstance(value, str) or not re.fullmatch(pattern, value) for value in values
        ):
            raise ValueError("Policy registry or label list is invalid")
    gate = policy.get("gate", {})
    if not isinstance(gate, dict) or set(gate) - {"minimum_severity", "changes"}:
        raise ValueError("Policy gate contains unsupported configuration")
    if gate.get("minimum_severity", "high") not in SEVERITIES:
        raise ValueError("Policy gate severity is unsupported")
    changes = gate.get("changes", ["introduced", "worsened"])
    if (
        not isinstance(changes, list) or not 1 <= len(changes) <= len(CHANGE_CLASSES)
        or any(not isinstance(value, str) or value not in CHANGE_CLASSES for value in changes)
        or len(set(changes)) != len(changes)
    ):
        raise ValueError("Policy gate change classes must be supported and unique")
    overrides = policy.get("rule_severities", {})
    if not isinstance(overrides, dict) or any(
        rule not in KNOWN_RULE_IDS or severity not in SEVERITIES for rule, severity in overrides.items()
    ):
        raise ValueError("Policy severity overrides must name known rules and severities")
    exceptions = policy.get("exceptions", [])
    if not isinstance(exceptions, list) or len(exceptions) > 100:
        raise ValueError("Policy exceptions must be a bounded list")
    ids = set()
    for exception in exceptions:
        if not isinstance(exception, dict) or set(exception) != _EXCEPTION_KEYS:
            raise ValueError("Policy exception requires exactly the documented fields")
        identifier = exception["id"]
        if not isinstance(identifier, str) or not _EXCEPTION_ID.fullmatch(identifier):
            raise ValueError("Policy exception ID is invalid")
        if identifier in ids:
            raise ValueError("Policy exception IDs must be unique")
        ids.add(identifier)
        rule = exception["rule_id"]
        if not isinstance(rule, str) or rule not in KNOWN_RULE_IDS:
            raise ValueError("Policy exception must name a known deterministic rule")
        if not isinstance(exception["target"], str) or not _TARGET.fullmatch(exception["target"]):
            raise ValueError("Policy exception target must be an exact target name")
        if not isinstance(exception["resource"], str) or len(exception["resource"]) > 1024 or not _RESOURCE.fullmatch(exception["resource"]):
            raise ValueError("Policy exception resource must be an exact supported identity")
        if not _text(exception["reason"], 1000) or not _text(exception["owner"], 200):
            raise ValueError("Policy exception requires a bounded reason and owner")
        _expiry(exception["expires"])


def evaluate_findings(findings: list[dict], policy: dict, target_name: str, now: date | None = None) -> dict:
    """Apply documented gates and exact exceptions while retaining all evidence.

    Dates are evaluated in UTC unless the caller supplies a calendar date. An
    exception is valid through its expiry date. Exceptions for other targets
    are irrelevant here; expired and unmatched relevant exceptions are advisory
    gaps. Resolved and non-deterministic findings cannot trigger or be exempted
    from the machine gate.
    """
    validate_policy(policy)
    if not isinstance(target_name, str) or not _TARGET.fullmatch(target_name):
        raise ValueError("Policy evaluation target is invalid")
    if now is not None and type(now) is not date:
        raise ValueError("Policy evaluation requires a calendar date")
    evaluated = now if now is not None else datetime.now(timezone.utc).date()
    if not isinstance(findings, list) or len(findings) > 100_000:
        raise ValueError("Policy evaluation requires a bounded finding list")
    copied = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("Policy evaluation received an invalid finding")
        severity = finding.get("original_severity", finding.get("severity"))
        if severity not in SEVERITIES or finding.get("change") not in (*CHANGE_CLASSES, "resolved"):
            raise ValueError("Policy evaluation received unsupported finding classification")
        deterministic = finding.get("source_type", "deterministic") == "deterministic"
        rule = finding.get("rule_id")
        if deterministic and (not isinstance(rule, str) or rule not in KNOWN_RULE_IDS):
            raise ValueError("Policy evaluation received an unknown deterministic rule")
        if not _text(finding.get("resource"), 1024):
            raise ValueError("Policy evaluation received an invalid resource identity")
        item = deepcopy(finding)
        effective = policy.get("rule_severities", {}).get(rule, severity) if deterministic else severity
        item.update({
            "original_severity": severity, "effective_severity": effective, "severity": effective,
            "policy_suppressed": False, "policy_exception_ids": [],
        })
        copied.append(item)

    gaps, applied = [], []
    for exception in policy.get("exceptions", []):
        if exception["target"] != target_name:
            continue
        expired = _expiry(exception["expires"]) < evaluated
        matches = [] if expired else [item for item in copied if (
            item.get("source_type", "deterministic") == "deterministic"
            and item["change"] != "resolved" and item["rule_id"] == exception["rule_id"]
            and item["resource"] == exception["resource"]
        )]
        if not matches:
            gaps.append({
                "code": "policy-exception-expired" if expired else "policy-exception-unmatched",
                "message": "A relevant policy exception has expired and does not suppress findings." if expired
                else "A relevant policy exception matched no unresolved deterministic finding.",
                "required": False, "exception_id": exception["id"], "target": target_name,
            })
            continue
        for item in matches:
            item["policy_suppressed"] = True
            item["policy_exception_ids"].append(exception["id"])
        applied.append({
            **deepcopy(exception), "matched_findings": len(matches),
            "finding_fingerprints": sorted({item["fingerprint"] for item in matches
                                           if isinstance(item.get("fingerprint"), str)}),
        })
    gate = policy.get("gate", {})
    minimum = SEVERITIES.index(gate.get("minimum_severity", "high"))
    gated_changes = gate.get("changes", ["introduced", "worsened"])
    failure = any(
        item.get("source_type", "deterministic") == "deterministic"
        and not item["policy_suppressed"] and item["change"] in gated_changes
        and SEVERITIES.index(item["effective_severity"]) >= minimum
        for item in copied
    )
    return {
        "findings": copied, "gaps": gaps, "applied_exceptions": applied,
        "policy_failure": failure, "evaluated_at": evaluated.isoformat(),
    }
