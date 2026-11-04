"""Check native quality evidence is not confused with missing or errored judging."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/publish_quality.py"
DIMENSIONS = ("readability", "maintainability", "scope", "tests")


def results(tmp_path: Path, *, score: float = 1) -> tuple[Path, Path, list[dict[str, object]]]:
    raw = tmp_path / "quality-raw"
    raw.mkdir()
    rows: list[dict[str, object]] = [
        {
            "name": name,
            "value": score,
            "raw": bool(score),
            "reasoning": "src/file.py:10 supports this decision",
        }
        for name in DIMENSIONS
    ]
    return raw, tmp_path / "reward.json", rows


def write(raw: Path, rows: list[dict[str, object]], *, score: float) -> None:
    (raw / "reward.json").write_text(json.dumps({"reward": score}))
    details = {"reward": {"kind": "llm", "score": score, "criteria": rows}}
    (raw / "reward-details.json").write_text(json.dumps(details))


def invoke(
    raw: Path, target: Path, paths: str = "src/file.py\n"
) -> subprocess.CompletedProcess[str]:
    manifest = raw / "evidence-paths.txt"
    manifest.write_text(paths)
    command = [sys.executable, str(SCRIPT), str(raw), str(target), str(manifest)]
    return subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603 -- fixed local helper, no shell


@pytest.mark.parametrize("score", [0, 1])
def test_measured_failures_and_passes_publish_the_native_bytes(
    tmp_path: Path, score: float
) -> None:
    raw, target, rows = results(tmp_path, score=score)
    write(raw, rows, score=score)
    assert invoke(raw, target).returncode == 0
    assert target.read_bytes() == (raw / "reward.json").read_bytes()
    assert (raw / "reward-details.json").is_file()


def test_mixed_real_decisions_keep_the_native_fraction(tmp_path: Path) -> None:
    raw, target, rows = results(tmp_path)
    rows[0].update(value=0, raw=False)
    write(raw, rows, score=0.75)
    assert invoke(raw, target).returncode == 0
    assert json.loads(target.read_text()) == {"reward": 0.75}


@pytest.mark.parametrize(
    "change",
    [
        {"value": 0, "raw": None, "reasoning": "", "error": "judge timed out after 120s"},
        {"value": 0, "raw": False, "reasoning": "UNMEASURED: surrounding source is absent"},
        {"value": 0, "raw": False, "reasoning": "This could be cleaner"},
        {"value": 1, "raw": True, "reasoning": ""},
        {"value": 0.5, "raw": True},
        {"name": "scope"},
    ],
)
def test_incomplete_judging_never_becomes_a_harbor_score(
    tmp_path: Path, change: dict[str, object]
) -> None:
    raw, target, rows = results(tmp_path)
    rows[0].update(change)
    write(raw, rows, score=0.75)
    result = invoke(raw, target)
    assert result.returncode == 1
    assert "Quality unmeasured:" in result.stderr
    assert not target.exists()
    assert (raw / "reward-details.json").is_file()


def test_missing_criterion_or_bad_aggregate_cannot_publish(tmp_path: Path) -> None:
    raw, target, rows = results(tmp_path)
    write(raw, rows[:-1], score=1)
    assert invoke(raw, target).returncode == 1
    write(raw, rows, score=0)
    assert invoke(raw, target).returncode == 1
    assert not target.exists()


@pytest.mark.parametrize(
    "reason",
    [
        "The issue is at `src/file.py:10-14`.",
        "The real path repo/src/file.py:10 and shortened src/file.py:12 agree.",
        "The range repo/src/file.py:14-10 is reversed.",
        "The range repo/src/file.py:0 is impossible.",
    ],
)
def test_shortened_unknown_or_impossible_citations_withhold_reward(
    tmp_path: Path, reason: str
) -> None:
    raw, target, rows = results(tmp_path)
    rows[0].update(value=0, raw=False, reasoning=reason)
    write(raw, rows, score=0.75)
    result = invoke(raw, target, "repo/src/file.py\n")
    assert result.returncode == 1
    assert not target.exists()


def test_every_failure_citation_must_resolve_in_the_evidence_manifest(tmp_path: Path) -> None:
    raw, target, rows = results(tmp_path)
    rows[0].update(
        value=0,
        raw=False,
        reasoning="Compare `repo/src/file.py:10-14` and (docs/conventions.md:2).",
    )
    write(raw, rows, score=0.75)
    assert invoke(raw, target, "repo/src/file.py\ndocs/conventions.md\n").returncode == 0
    assert json.loads(target.read_text()) == {"reward": 0.75}
