"""Exact selector matching, empty policies and bounded authentic policy bytes."""

from copy import deepcopy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "portable_scan_policy", ROOT / "demo/portable-gitops/scan_policy.py"
)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
BASE = "sha256:" + "a" * 64


def selector(index=0):
    return {
        "vulnerability": f"CVE-controlled-{index}",
        "namespace": "alpine:distro:3.23",
        "severity": "High",
        "type": "apk",
        "name": f"package-{index}",
        "version": "1.0-r0",
        "purl": f"pkg:apk/alpine/package-{index}@1.0-r0",
    }


def selected(count=0):
    rows = [selector(i) for i in range(count)]
    return {
        "base_manifest_digest": BASE,
        "expires_at": (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat(),
        "allowed_high_selectors": rows,
        "allowed_high_findings": [[r["vulnerability"], r["name"], r["version"]] for r in rows],
    }


def scan(rows):
    return {
        "matches": [
            {
                "vulnerability": {
                    "id": r["vulnerability"],
                    "namespace": r["namespace"],
                    "severity": r["severity"],
                },
                "artifact": {k: r[k] for k in ("type", "name", "version", "purl")},
            }
            for r in rows
        ]
    }


@pytest.mark.parametrize("count", [0, 1, 14, 31, 256])
def test_zero_or_any_bounded_exact_set_has_the_correct_verdict(count):
    chosen = selected(count)
    result = policy.evaluate_scan(scan(chosen["allowed_high_selectors"]), chosen)
    assert result["permitted"] is True
    assert result["decision"] == ("temporary-exception" if count else "passed")
    assert result["high_count"] == count and result["duplicate_high_count"] == 0


@pytest.mark.parametrize("field", policy.FIELDS)
def test_every_selector_field_is_bound(field):
    chosen = selected(1)
    changed = deepcopy(chosen["allowed_high_selectors"])
    changed[0][field] = "changed"
    result = policy.evaluate_scan(scan(changed), chosen)
    assert not result["permitted"] and result["decision"] == "blocked"


@pytest.mark.parametrize("severity", ["Critical", "Unknown", "high", "", None, []])
def test_critical_or_unknown_cannot_pass_an_empty_policy(severity):
    report = {"matches": [{"vulnerability": {"severity": severity}}]}
    result = policy.evaluate_scan(report, selected())
    assert not result["permitted"] and result["critical_count"] + result["unknown_severity_count"] == 1


def test_expected_missing_unexpected_and_duplicates_are_distinct():
    chosen = selected(2)
    report = scan([selector(0), selector(0), selector(9)])
    result = policy.evaluate_scan(report, chosen)
    assert result["high_count"] == 3 and result["duplicate_high_count"] == 1
    assert result["missing_expected_high_count"] == 1 and result["unexpected_high_count"] == 1
    assert result["decision"] == "blocked"


@pytest.mark.parametrize("field", policy.FIELDS)
def test_incomplete_high_cannot_be_used_as_an_approval(field):
    chosen = selected(1)
    del chosen["allowed_high_selectors"][0][field]
    with pytest.raises(ValueError):
        policy.validate_policy(chosen, BASE)


@pytest.mark.parametrize(
    "change",
    [
        "wrong-base",
        "missing-base",
        "expired",
        "naive",
        "missing-expiry",
        "duplicate",
        "too-many",
        "projection",
        "empty-field",
        "oversized-field",
        "control-field",
    ],
)
def test_bad_policy_is_rejected(change):
    chosen = selected(1)
    if change == "wrong-base":
        chosen["base_manifest_digest"] = "sha256:" + "b" * 64
    if change == "missing-base":
        del chosen["base_manifest_digest"]
    if change == "expired":
        chosen["expires_at"] = (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)).isoformat()
    if change == "naive":
        chosen["expires_at"] = "2099-01-01T00:00:00"
    if change == "missing-expiry":
        del chosen["expires_at"]
    if change == "duplicate":
        chosen["allowed_high_selectors"] *= 2
    if change == "too-many":
        chosen = selected(257)
    if change == "projection":
        chosen["allowed_high_findings"][0][0] = "other"
    if change == "empty-field":
        chosen["allowed_high_selectors"][0]["purl"] = " "
    if change == "oversized-field":
        chosen["allowed_high_selectors"][0]["purl"] = "x" * 2049
    if change == "control-field":
        chosen["allowed_high_selectors"][0]["purl"] = "pkg:test\n"
    with pytest.raises(ValueError):
        policy.validate_policy(chosen, BASE)


def test_projection_is_optional_and_order_independent():
    chosen = selected(2)
    chosen["allowed_high_findings"].reverse()
    policy.validate_policy(chosen, BASE)
    del chosen["allowed_high_findings"]
    policy.validate_policy(chosen, BASE)


def test_policy_hash_is_over_the_exact_parsed_bytes(tmp_path):
    path = tmp_path / "policy.json"
    raw = json.dumps(selected(1), indent=4).encode() + b"\n"
    path.write_bytes(raw)
    loaded, digest = policy.load_policy(path, BASE)
    assert digest == hashlib.sha256(raw).hexdigest() and loaded == json.loads(raw)


@pytest.mark.parametrize("bad", [b'{"x":1,"x":2}', b'{"x":NaN}', b"x" * (256 * 1024 + 1), b"", b"[]"])
def test_malformed_or_oversized_file_is_not_admitted(tmp_path, bad):
    path = tmp_path / "policy.json"
    path.write_bytes(bad)
    with pytest.raises(ValueError):
        policy.load_policy(path, BASE)


def test_fifo_and_symlink_are_rejected_without_reading(tmp_path):
    source = tmp_path / "source"
    source.write_text(json.dumps(selected()))
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(ValueError):
        policy.load_policy(link, BASE)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        policy.load_policy(fifo, BASE)


@pytest.mark.parametrize(
    "broken",
    [{}, {"matches": None}, {"matches": [{}]}, {"matches": [{"vulnerability": {"severity": "High"}}]}],
)
def test_malformed_scan_cannot_be_a_clean_result(broken):
    with pytest.raises(ValueError):
        policy.evaluate_scan(broken, selected())
