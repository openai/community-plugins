"""Join the code benchmark's functional gate to exact-source quality regrades."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from pathlib import Path

POLICY = "code-quality-v1"
DIMENSIONS = {"readability", "maintainability", "scope", "tests"}
STATES = {"pass", "fail", "review_required", "unmeasured"}
HASH_LENGTH = 64


def read_object(path: Path) -> dict[str, object]:
    """Read a required JSON object without interpreting missing data as a result."""
    value: object = json.loads(path.read_text())
    return object_map(value)


def object_map(value: object) -> dict[str, object]:
    """Require explicit structured evidence."""
    if not isinstance(value, dict):
        message = "Expected a rubric evidence object"
        raise TypeError(message)
    return {str(key): item for key, item in value.items()}


def source_id(value: object) -> str:
    """Read a canonical immutable Harbor trial UUID."""
    if not isinstance(value, str) or str(UUID(value)) != value:
        message = "Quality matching requires a canonical Harbor trial UUID"
        raise ValueError(message)
    return value


def binary(value: object) -> str:
    """An absent functional decision is unknown; fractional rewards are never binary."""
    if value is None:
        return "unmeasured"
    if isinstance(value, bool) or value not in (0, 1):
        message = "Selected functional gate must be an explicit numeric 0 or 1"
        raise ValueError(message)
    return "pass" if value == 1 else "fail"


@dataclass(frozen=True)
class Quality:
    """Resolved policy status and independently reported optional fraction."""

    status: str = "unmeasured"
    score: float | None = None
    path: Path | None = None


def verified_report(path: Path, trial: str) -> tuple[Quality, dict[str, object]]:
    """Reject inconsistent, differently versioned or wrong-source gate artifacts."""
    item = read_object(path)
    status = item.get("status")
    dimensions = object_map(item.get("dimensions"))
    if (
        item.get("policy") != POLICY
        or source_id(item.get("source_trial_id")) != trial
        or not isinstance(status, str)
        or status not in STATES
        or dimensions.keys() != DIMENSIONS
        or any(value not in STATES for value in dimensions.values())
    ):
        message = f"Invalid policy, source, status or dimensions in {path}"
        raise ValueError(message)
    values = set(dimensions.values())
    calculated = next(
        value for value in ("fail", "review_required", "unmeasured", "pass") if value in values
    )
    expected_gate = {"pass": 1, "fail": 0}.get(calculated)
    expected_score = (
        sum(value == "pass" for value in dimensions.values()) / len(dimensions)
        if values <= {"pass", "fail"}
        else None
    )
    packet_hash = item.get("packet_sha256")
    if (
        status != calculated
        or item.get("quality_pass") != expected_gate
        or item.get("quality_score") != expected_score
        or not isinstance(packet_hash, str)
        or len(packet_hash) != HASH_LENGTH
        or any(char not in "0123456789abcdef" for char in packet_hash)
    ):
        message = f"Inconsistent quality gate or packet hash in {path}"
        raise ValueError(message)
    return Quality(status, expected_score, path), item


def verify_adjudication(raw: dict[str, object], final: dict[str, object], trial: str) -> None:
    """Allow a human artifact to settle only dimensions disputed in the native cascade."""
    if not final.get("adjudication") or any(
        raw.get(key) != final.get(key) for key in ("packet_sha256", "primary", "reviewer")
    ):
        message = f"Adjudication changes original judge evidence or packet for {trial}"
        raise ValueError(message)
    record = object_map(final["adjudication"])
    reviewer = record.get("reviewer")
    decisions = object_map(record.get("decisions"))
    before = object_map(raw.get("dimensions"))
    after = object_map(final.get("dimensions"))
    disputed = {key for key, value in before.items() if value == "review_required"}
    if (
        not isinstance(reviewer, str)
        or not reviewer.strip()
        or not decisions
        or not decisions.keys() <= disputed
    ):
        message = f"Adjudication needs a named reviewer and disputed decisions for {trial}"
        raise ValueError(message)
    expected = dict(before)
    for key, value in decisions.items():
        ruling = object_map(value)
        reason = ruling.get("reason")
        decision = binary(ruling.get("score"))
        if decision == "unmeasured" or not isinstance(reason, str) or not reason.strip():
            message = f"Adjudication needs evidence for {trial}: {key}"
            raise ValueError(message)
        expected[key] = decision
    if expected != after:
        message = f"Adjudication changes dimensions without a disputed ruling for {trial}"
        raise ValueError(message)


def quality_index(path: Path | None) -> dict[str, Quality]:
    """Validate original UUIDs against Harbor's regrade metadata before joining any grade."""
    if path is None:
        return {}
    results = {}
    for trial, value in read_object(path).items():
        source_id(trial)
        entry = object_map(value)
        regrade = entry.get("regrade")
        adjudicated = entry.get("adjudicated")
        if not isinstance(regrade, str) or (
            adjudicated is not None and not isinstance(adjudicated, str)
        ):
            message = "Quality entries need a regrade path and optional adjudicated artifact path"
            raise ValueError(message)
        root = path.parent / regrade
        native = read_object(root / "result.json")
        source = object_map(object_map(native.get("config")).get("source_trial"))
        if source.get("action") != "regrade" or source_id(source.get("trial_id")) != trial:
            message = f"Native regrade does not come from original Harbor trial {trial}"
            raise ValueError(message)
        audit = root / "verifier/quality-review.json"
        if not audit.is_file() and adjudicated is None:
            results[trial] = Quality(path=root / "result.json")
            continue
        quality, raw = verified_report(audit, trial)
        if isinstance(adjudicated, str):
            quality, final = verified_report(path.parent / adjudicated, trial)
            verify_adjudication(raw, final, trial)
        results[trial] = quality
    return results


def joint(functional: str, quality: str) -> str:
    """Only a complete functional and quality pass becomes a benchmark pass."""
    if functional == "fail":
        return "fail"
    if functional != "pass":
        return "unavailable"
    if quality in ("pass", "fail"):
        return quality
    return "review_required" if quality == "review_required" else "quality_unmeasured"


def summary(outcomes: list[tuple[str, str, str]], expected: int) -> list[str]:
    """Provide gate rates with explicit coverage and a conservative headline denominator."""
    eligible = sum(functional in ("pass", "fail") for functional, _, _ in outcomes)
    functional_pass = sum(functional == "pass" for functional, _, _ in outcomes)
    quality_measured = sum(quality in ("pass", "fail") for _, quality, _ in outcomes)
    quality_pass = sum(quality == "pass" for _, quality, _ in outcomes)
    confirmed = sum(outcome == "pass" for _, _, outcome in outcomes)
    failed = sum(outcome == "fail" for _, _, outcome in outcomes)
    review = sum(outcome == "review_required" for _, _, outcome in outcomes)
    unknown = sum(outcome == "quality_unmeasured" for _, _, outcome in outcomes)
    return [
        "## Complete code rubric",
        "",
        f"- **Benchmark pass: {confirmed}/{eligible} functionally measured submissions**; {failed} confirmed failures; {review} awaiting adjudication; {unknown} with quality unmeasured. {expected - eligible}/{expected} attempts have no usable functional gate and are outside this headline denominator.",
        f"- Functional pass: {functional_pass}/{eligible} functionally measured submissions.",
        f"- Quality pass: {quality_pass}/{quality_measured} definitively quality-graded submissions; coverage {quality_measured}/{expected} attempts, including any functional failures also reviewed.",
        "- Pending quality counts as no verified benchmark pass in the headline; it is not a quality failure. A quality issue cannot be averaged away by functional credit. Historical functional-only results do not establish benchmark passes.",
    ]
