"""Exact, base-bound scan policy for a disposable demo; no promotion authority."""

from __future__ import annotations

from collections import Counter
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat

MAX_POLICY_BYTES = 256 * 1024
MAX_SELECTORS = 256
MAX_FIELD_BYTES = 2048
FIELDS = ("vulnerability", "namespace", "severity", "type", "name", "version", "purl")
KNOWN_SEVERITIES = {"Critical", "High", "Medium", "Low", "Negligible"}


def _text(value):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value.encode("utf-8")) <= MAX_FIELD_BYTES
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _selector(value):
    if (
        not isinstance(value, dict)
        or set(value) != set(FIELDS)
        or value.get("severity") != "High"
        or any(not _text(value.get(field)) for field in FIELDS)
    ):
        raise ValueError("Require seven complete bounded High-finding selector fields")
    return tuple(value[field] for field in FIELDS)


def _aware(value, label):
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(label + " must be timezone-aware")
    return value.astimezone(dt.UTC)


def validate_policy(policy, base_manifest, at=None):
    if not isinstance(policy, dict):
        raise ValueError("Scan policy must be an object")
    try:
        raw = json.dumps(policy, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Scan policy must be bounded JSON") from exc
    if not 0 < len(raw) <= MAX_POLICY_BYTES:
        raise ValueError("Scan policy exceeds its 256 KiB bound")
    if (
        not isinstance(base_manifest, str)
        or not re.fullmatch(r"sha256:[a-f0-9]{64}", base_manifest)
        or policy.get("base_manifest_digest") != base_manifest
    ):
        raise ValueError("Scan policy targets another or missing base manifest")
    current = _aware(at if at is not None else dt.datetime.now(dt.UTC), "Observation time")
    expiry = policy.get("expires_at")
    if not isinstance(expiry, str) or len(expiry) > 128:
        raise ValueError("Scan policy expiry is missing or malformed")
    try:
        expires = _aware(dt.datetime.fromisoformat(expiry.replace("Z", "+00:00")), "Policy expiry")
    except ValueError as exc:
        raise ValueError("Scan policy expiry must be timezone-aware ISO 8601") from exc
    if current >= expires:
        raise ValueError("Scan policy expired")
    selectors = policy.get("allowed_high_selectors")
    if not isinstance(selectors, list) or len(selectors) > MAX_SELECTORS:
        raise ValueError("Require zero through 256 exact High selectors")
    expected = [_selector(row) for row in selectors]
    if len(set(expected)) != len(expected):
        raise ValueError("Allowed High selectors must be unique")
    if "allowed_high_findings" in policy:
        projection = policy["allowed_high_findings"]
        if (
            not isinstance(projection, list)
            or len(projection) != len(expected)
            or any(
                not isinstance(row, list) or len(row) != 3 or any(not _text(x) for x in row)
                for row in projection
            )
        ):
            raise ValueError("Legacy High projection must exactly agree with full selectors")
        projected = [(row["vulnerability"], row["name"], row["version"]) for row in selectors]
        if Counter(tuple(row) for row in projection) != Counter(projected):
            raise ValueError("Legacy High projection must exactly agree with full selectors")
    return policy


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate scan policy JSON member")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("Non-finite JSON is not a scan policy")


def load_policy(path, base_manifest, at=None):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Scan policy must be a regular non-symlink file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_POLICY_BYTES:
            raise ValueError("Scan policy must be a regular file of at most 256 KiB")
        raw = stream.read(MAX_POLICY_BYTES + 1)
        after = os.fstat(stream.fileno())
    def identity(value):
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if len(raw) != before.st_size or identity(before) != identity(after):
        raise ValueError("Scan policy changed while being read")
    try:
        policy = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Scan policy must be bounded valid JSON") from exc
    validate_policy(policy, base_manifest, at=at)
    return policy, hashlib.sha256(raw).hexdigest()


def evaluate_scan(scan, policy):
    validate_policy(policy, policy.get("base_manifest_digest") if isinstance(policy, dict) else None)
    if not isinstance(scan, dict) or not isinstance(scan.get("matches"), list):
        raise ValueError("Actual scan must contain a matches array")
    expected = {_selector(row) for row in policy["allowed_high_selectors"]}
    observed = []
    critical = unknown = 0
    for match in scan["matches"]:
        if not isinstance(match, dict) or not isinstance(match.get("vulnerability"), dict):
            raise ValueError("Malformed actual scan finding")
        vulnerability = match["vulnerability"]
        severity = vulnerability.get("severity")
        if not isinstance(severity, str) or severity not in KNOWN_SEVERITIES:
            unknown += 1
        elif severity == "Critical":
            critical += 1
        elif severity == "High":
            artifact = match.get("artifact")
            if not isinstance(artifact, dict):
                raise ValueError("High scan finding lacks complete artifact identity")
            observed.append(
                _selector(
                    {
                        "vulnerability": vulnerability.get("id"),
                        "namespace": vulnerability.get("namespace"),
                        "severity": severity,
                        **{field: artifact.get(field) for field in ("type", "name", "version", "purl")},
                    }
                )
            )
    actual = set(observed)
    duplicates = len(observed) - len(actual)
    unexpected, missing = len(actual - expected), len(expected - actual)
    permitted = not (critical or unknown or duplicates or unexpected or missing)
    return {
        "permitted": permitted,
        "decision": ("temporary-exception" if expected else "passed") if permitted else "blocked",
        "high_count": len(observed),
        "critical_count": critical,
        "unknown_severity_count": unknown,
        "unexpected_high_count": unexpected,
        "missing_expected_high_count": missing,
        "duplicate_high_count": duplicates,
    }
