"""Publish a Harbor RewardKit 0.2.0 quality result only when every criterion was measured."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Never

DIMENSIONS = ("readability", "maintainability", "scope", "tests")
EXPECTED = frozenset(DIMENSIONS)
REPOSITORY_PATH = r"(?:[\w.@+-]+/)*[\w.@+-]+"
EVIDENCE = re.compile(
    rf"(?<![\w./-])(?P<path>{REPOSITORY_PATH}):(?P<start>[1-9]\d*)"
    r"(?:-(?P<end>[1-9]\d*))?\b"
)


class QualityUnmeasuredError(ValueError):
    """The native outputs do not establish all quality decisions."""


@dataclass(frozen=True)
class Decision:
    """A measured native binary decision and its original reasoning."""

    score: float
    reason: str


def _invalid(message: str) -> Never:
    raise QualityUnmeasuredError(message)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        _invalid("Expected a native RewardKit JSON object")
    return {str(key): item for key, item in value.items()}


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid("Expected a numeric native RewardKit score")
    return float(value)


def grounded(reason: str, paths: frozenset[str]) -> bool:
    """Check that a reason cites only exact packet paths and valid line ranges."""
    citations = list(EVIDENCE.finditer(reason))
    return bool(citations) and all(
        citation["path"] in paths
        and int(citation["end"] or citation["start"]) >= int(citation["start"])
        for citation in citations
    )


def _decision(value: object, paths: frozenset[str]) -> tuple[str, Decision]:
    entry = _object(value)
    name = entry.get("name")
    reason = entry.get("reasoning")
    if not isinstance(name, str) or name not in EXPECTED:
        _invalid("Unknown or missing native quality criterion")
    if entry.get("error") or entry.get("raw") is None:
        _invalid(f"{name}: native judge reported an error or no raw decision")
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or reason.lstrip().startswith("UNMEASURED:")
    ):
        _invalid(f"{name}: no measured reasoning")
    score = _number(entry.get("value"))
    if score not in (0, 1):
        _invalid(f"{name}: non-binary score")
    if score == 0 and not grounded(reason, paths):
        _invalid(f"{name}: failure without exact repository paths and valid line ranges")
    return name, Decision(score, reason)


def _read(path: Path) -> dict[str, object]:
    payload: object = json.loads(path.read_text())
    return _object(payload)


def evidence_paths(manifest: Path) -> frozenset[str]:
    """Read and validate a trusted repository path manifest."""
    paths = frozenset(manifest.read_text().splitlines())
    if not paths or any(
        re.fullmatch(REPOSITORY_PATH, path) is None or ".." in path.split("/") for path in paths
    ):
        _invalid("Missing or invalid repository evidence paths")
    return paths


def read_review(raw_directory: Path, manifest: Path) -> dict[str, Decision]:
    """Return all original decisions only when the native details and totals agree."""
    raw_path = raw_directory / "reward.json"
    detail = _object(_read(raw_directory / "reward-details.json").get("reward"))
    criteria = detail.get("criteria")
    if detail.get("kind") != "llm" or not isinstance(criteria, list):
        _invalid("Missing native LLM criterion details")
    paths = evidence_paths(manifest)
    decisions = dict(_decision(item, paths) for item in criteria)
    if decisions.keys() != EXPECTED or len(criteria) != len(EXPECTED):
        _invalid("Not all four quality criteria were measured exactly once")
    expected = sum(decision.score for decision in decisions.values()) / len(EXPECTED)
    if (
        _number(detail.get("score")) != expected
        or _number(_read(raw_path).get("reward")) != expected
    ):
        _invalid("Native quality totals disagree with the four measured criteria")
    return decisions


def publish(raw_directory: Path, destination: Path, evidence_paths: Path) -> None:
    """Validate native details and copy the unchanged native score to Harbor's reward path."""
    read_review(raw_directory, evidence_paths)
    raw_path = raw_directory / "reward.json"
    shutil.copyfile(raw_path, destination)


def main() -> None:
    """Publish from the raw-output directory into the separately supplied Harbor path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_directory", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("evidence_paths", type=Path)
    args = parser.parse_args()
    try:
        publish(args.raw_directory, args.destination, args.evidence_paths)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Quality unmeasured: {exc}\n")


if __name__ == "__main__":
    main()
