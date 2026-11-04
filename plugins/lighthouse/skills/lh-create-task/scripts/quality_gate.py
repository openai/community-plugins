"""Resolve the versioned Sol/Astra quality gate from native RewardKit evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import runpy
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from publish_quality import DIMENSIONS, Decision, evidence_paths, grounded, read_review
else:
    # Load only this trusted sibling, even when Python excludes script directories from sys.path.
    _sibling = runpy.run_path(str(Path(__file__).with_name("publish_quality.py")))
    DIMENSIONS = _sibling["DIMENSIONS"]
    Decision = _sibling["Decision"]
    evidence_paths = _sibling["evidence_paths"]
    grounded = _sibling["grounded"]
    read_review = _sibling["read_review"]

POLICY = "code-quality-v1"
MODELS = {"sol": "openai/gpt-6-sol", "astra": "openai/gpt-6-astra"}


@dataclass(frozen=True)
class Stage:
    """A native review, or a recorded reason it could not be measured."""

    decisions: dict[str, Decision] | None
    error: str | None = None

    def public(self, name: str) -> dict[str, object]:
        """Keep judge identity and each original reason available to auditors."""
        return {
            "model": MODELS[name],
            "effort": "medium",
            "error": self.error,
            "decisions": None
            if self.decisions is None
            else {
                key: {"score": value.score, "reason": value.reason}
                for key, value in self.decisions.items()
            },
        }


def load_stage(output: Path, name: str, evidence: Path) -> Stage:
    """Treat invalid native results as unknown instead of quality failures."""
    try:
        return Stage(read_review(output / name, evidence))
    except (OSError, ValueError) as exc:
        return Stage(None, f"Native evidence unavailable or invalid ({type(exc).__name__})")


def disputed(primary: Stage, reviewer: Stage | None) -> set[str]:
    """Find all disagreements, including issues raised only by the reviewer."""
    if primary.decisions is None or reviewer is None or reviewer.decisions is None:
        return set()
    return {
        key for key in DIMENSIONS if primary.decisions[key].score != reviewer.decisions[key].score
    }


def adjudicate(
    path: Path | None, available: set[str], evidence: Path
) -> tuple[str | None, dict[str, Decision]]:
    """Accept named, cited human rulings for divergent dimensions only."""
    if path is None:
        return None, {}
    raw: object = json.loads(path.read_text())
    reviewer = raw.get("reviewer") if isinstance(raw, dict) else None
    if (
        not isinstance(raw, dict)
        or not isinstance(reviewer, str)
        or not reviewer.strip()
        or not isinstance(raw.get("decisions"), dict)
    ):
        message = "Adjudication requires a named reviewer and a decisions object"
        raise ValueError(message)
    paths = evidence_paths(evidence)
    decisions: dict[str, Decision] = {}
    for key, value in raw["decisions"].items():
        if (
            key not in available
            or not isinstance(value, dict)
            or value.get("decision") not in ("pass", "fail")
            or not isinstance(value.get("reason"), str)
            or not grounded(value["reason"], paths)
        ):
            message = f"Adjudication must cite exact evidence for a disputed dimension: {key}"
            raise ValueError(message)
        decisions[key] = Decision(float(value["decision"] == "pass"), value["reason"])
    return reviewer, decisions


def dimension_states(
    primary: Stage, reviewer: Stage | None, human: dict[str, Decision]
) -> dict[str, str]:
    """Combine reviews without allowing disagreement or absent data to become a pass."""
    if primary.decisions is None:
        return dict.fromkeys(DIMENSIONS, "unmeasured")
    if all(value.score == 1 for value in primary.decisions.values()):
        return dict.fromkeys(DIMENSIONS, "pass")
    if reviewer is None or reviewer.decisions is None:
        return {key: "pass" if primary.decisions[key].score else "unmeasured" for key in DIMENSIONS}
    states = {}
    for key in DIMENSIONS:
        first, second = primary.decisions[key], reviewer.decisions[key]
        if key in human:
            states[key] = "pass" if human[key].score else "fail"
        elif first.score == second.score:
            states[key] = "pass" if first.score else "fail"
        else:
            states[key] = "review_required"
    return states


def overall(states: dict[str, str]) -> str:
    """A single confirmed failure is final; every dimension must pass for a pass."""
    values = set(states.values())
    if "fail" in values:
        return "fail"
    if "review_required" in values:
        return "review_required"
    if "unmeasured" in values:
        return "unmeasured"
    return "pass"


def run_stage(name: str, args: argparse.Namespace) -> None:
    """Run a single blind native RewardKit stage without exposing the other stage."""
    destination = args.output / name
    destination.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "REWARDKIT_JUDGE": MODELS[name],
        "QUALITY_USAGE_PATH": str(destination / "usage.jsonl"),
    }
    command = [
        sys.executable,
        str(Path(__file__).with_name("run_quality_judge.py")),
        str(args.rubric),
        "--workspace",
        str(args.workspace),
        "--output",
        str(destination / "reward.json"),
    ]
    with (destination / "runner.log").open("w") as log:
        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)  # noqa: S603 -- Fixed sibling executable, argv only; paths are verifier-controlled.


def write_report(args: argparse.Namespace, primary: Stage, reviewer: Stage | None) -> None:
    """Write durable diagnostics and a native binary reward only for definite outcomes."""
    packet_hash = hashlib.sha256(args.packet.read_bytes()).hexdigest()
    report_path = args.output / "quality-review.json"
    if args.action == "finalize" and report_path.exists():
        previous: object = json.loads(report_path.read_text())
        if (
            not isinstance(previous, dict)
            or previous.get("packet_sha256") != packet_hash
            or previous.get("source_trial_id") != str(UUID(args.source_trial_id))
        ):
            message = "Existing quality evidence has a different packet or original source"
            raise ValueError(message)
    reviewer_name, human = adjudicate(args.adjudication, disputed(primary, reviewer), args.evidence)
    states = dimension_states(primary, reviewer, human)
    status = overall(states)
    measured = all(value in ("pass", "fail") for value in states.values())
    report = {
        "policy": POLICY,
        "source_trial_id": str(UUID(args.source_trial_id)),
        "packet_sha256": packet_hash,
        "status": status,
        "quality_pass": {"pass": 1, "fail": 0}.get(status),
        "quality_score": sum(value == "pass" for value in states.values()) / len(DIMENSIONS)
        if measured
        else None,
        "dimensions": states,
        "primary": primary.public("sol"),
        "reviewer": reviewer.public("astra") if reviewer is not None else None,
        "adjudication": None
        if reviewer_name is None
        else {
            "reviewer": reviewer_name,
            "decisions": {
                key: {"score": value.score, "reason": value.reason} for key, value in human.items()
            },
        },
    }
    args.output.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    reward = args.output / "reward.json"
    if status in ("pass", "fail"):
        reward.write_text(json.dumps({"reward": report["quality_pass"]}) + "\n")
    else:
        reward.unlink(missing_ok=True)


def main() -> None:
    """Run or finalize a copyable verifier for one immutable original trial."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("run", "finalize"))
    parser.add_argument("--source-trial-id", required=True)
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rubric", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--adjudication", type=Path)
    args = parser.parse_args()
    try:
        UUID(args.source_trial_id)
        if args.action == "run":
            if args.rubric is None or args.workspace is None or args.adjudication is not None:
                parser.error("run requires --rubric and --workspace; adjudication is finalize-only")
            if any((args.output / name).exists() for name in MODELS):
                parser.error(
                    "run requires fresh judge output; preserve attempts and use finalize to adjudicate"
                )
            run_stage("sol", args)
        primary = load_stage(args.output, "sol", args.evidence)
        reviewer = None
        if primary.decisions is not None and any(
            value.score == 0 for value in primary.decisions.values()
        ):
            if args.action == "run":
                run_stage("astra", args)
            reviewer = load_stage(args.output, "astra", args.evidence)
        write_report(args, primary, reviewer)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Quality verifier could not finalize: {exc}\n")


if __name__ == "__main__":
    main()
