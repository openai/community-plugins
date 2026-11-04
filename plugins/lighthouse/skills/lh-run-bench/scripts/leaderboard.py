"""Render one native Harbor Codex job; never run agents or alter saved evidence."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import runpy
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from complete_rubric import Quality, binary, joint, quality_index, summary
else:
    # Load only this trusted sibling, even when Python excludes script directories from sys.path.
    _sibling = runpy.run_path(str(Path(__file__).with_name("complete_rubric.py")))
    Quality = _sibling["Quality"]
    binary = _sibling["binary"]
    joint = _sibling["joint"]
    quality_index = _sibling["quality_index"]
    summary = _sibling["summary"]

METRICS = ("input", "cached", "output", "cost", "learner", "trial")


def mapping(value: object) -> dict[str, object]:
    """Read a JSON object; a missing optional object has no fields."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = "Expected a JSON object"
        raise TypeError(msg)
    return {str(key): item for key, item in value.items()}


def read_json(path: Path) -> dict[str, object]:
    """Load a saved native JSON object."""
    return mapping(json.loads(path.read_text()))


def number(value: object) -> float | None:
    """Preserve missing values and reject invalid numeric evidence."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        msg = f"Expected a number, got {value!r}"
        raise TypeError(msg)
    if not math.isfinite(value) or value < 0:
        msg = f"Expected a finite nonnegative number, got {value!r}"
        raise ValueError(msg)
    return float(value)


def tokens(value: object) -> float | None:
    """Reject fractional counts instead of rounding them in the report."""
    result = number(value)
    if result is not None and not result.is_integer():
        msg = f"Token counts must be integers, got {value!r}"
        raise ValueError(msg)
    return result


def duration(data: dict[str, object]) -> float | None:
    """Measure elapsed wall seconds only when both native timestamps exist."""
    start, end = data.get("started_at"), data.get("finished_at")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    return number((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def literal(value: object) -> str:
    """Render untrusted result labels as text inside a Markdown table."""
    text = html.escape(str(value)).replace("\n", " ").replace("\r", " ")
    for char in "\\`*_{}[]()#+.!|~-":
        text = text.replace(char, "\\" + char)
    return text


def link(path: Path, output: Path, label: str) -> str:
    """Create a portable relative evidence link."""
    relative = os.path.relpath(path.resolve(), output.parent.resolve())
    return f"[{label}]({quote(relative, safe='/')})"


def formatted(value: float | None, key: str) -> str:
    """Keep unavailable measurements distinct from measured zero."""
    if value is None:
        return "unknown"
    precision = 6 if key == "cost" else 2
    return f"{value:,.0f}" if key in {"input", "cached", "output"} else f"{value:,.{precision}f}"


def estimate_cost(
    metrics: dict[str, float | None], rates: dict[str, object], model: object
) -> float | None:
    """Price total input without charging cached tokens twice."""
    if not rates or any(metrics[key] is None for key in ("input", "cached", "output")):
        return None
    if rates.get("model") != model or rates.get("currency") != "USD":
        msg = "Rates must match the exact recorded model and use USD"
        raise ValueError(msg)
    for key in ("source", "date"):
        if not isinstance(rates.get(key), str) or not rates[key]:
            msg = f"Rates require a nonempty {key}"
            raise ValueError(msg)
    tokens_in, cached, tokens_out = metrics["input"], metrics["cached"], metrics["output"]
    rate_in = number(rates.get("input_per_million"))
    rate_cache = number(rates.get("cached_input_per_million"))
    rate_out = number(rates.get("output_per_million"))
    # Explicit narrowing keeps nullable measurements out of arithmetic.
    if tokens_in is None or cached is None or tokens_out is None:
        return None
    if rate_in is None or rate_cache is None or rate_out is None:
        return None
    return ((tokens_in - cached) * rate_in + cached * rate_cache + tokens_out * rate_out) / 1e6


@dataclass
class Trial:
    """A report row derived from one immutable trial directory."""

    name: str
    state: str
    rewards: dict[str, object]
    metrics: dict[str, float | None]
    cost_source: str
    evidence: str
    trial_id: str | None


def trial_row(path: Path, output: Path, rates: dict[str, object]) -> Trial:
    """Read the single-step Codex contract, preserving absent trajectory usage."""
    data = read_json(path)
    info = mapping(data.get("agent_info"))
    if info.get("name") != "codex" or data.get("step_results"):
        msg = f"Only single-step Codex learner results are supported: {path}"
        raise ValueError(msg)
    trajectory = path.parent / "agent/trajectory.json"
    trajectory_data = read_json(trajectory) if trajectory.is_file() else {}
    model_info = mapping(info.get("model_info"))
    model = model_info.get("name")
    qualified_model = f"{model_info['provider']}/{model}" if model_info.get("provider") else model
    trajectory_model = mapping(trajectory_data.get("agent")).get("model_name")
    if trajectory_model is not None and trajectory_model not in {model, qualified_model}:
        msg = f"Trajectory model {trajectory_model!r} differs from learner {model!r}: {path}"
        raise ValueError(msg)
    final = mapping(trajectory_data.get("final_metrics"))
    metrics = {
        "input": tokens(final.get("total_prompt_tokens")),
        "cached": tokens(final.get("total_cached_tokens")),
        "output": tokens(final.get("total_completion_tokens")),
        "cost": number(final.get("total_cost_usd")),
        "learner": duration(mapping(data.get("agent_execution"))),
        "trial": duration(data),
    }
    tokens_in, cached = metrics["input"], metrics["cached"]
    if tokens_in is not None and cached is not None and cached > tokens_in:
        msg = f"Cached tokens exceed total input: {path}"
        raise ValueError(msg)
    cost_source = "Harbor reported/estimated" if metrics["cost"] is not None else "unknown"
    extra = mapping(final.get("extra"))
    models = mapping(mapping(data.get("agent_result")).get("model_usage"))
    model = qualified_model
    same_model = not models or list(models) == [model]
    if metrics["cost"] is None and not extra.get("total_cache_write_input_tokens") and same_model:
        metrics["cost"] = estimate_cost(metrics, rates, model)
        if metrics["cost"] is not None:
            cost_source = "rate estimate"
    rewards = mapping(mapping(data.get("verifier_result")).get("rewards"))
    for value in rewards.values():
        number(value)
    has_score = number(rewards.get("reward")) is not None and all(
        value is not None for value in rewards.values()
    )
    state = "graded" if has_score and data.get("finished_at") else "incomplete"
    if data.get("exception_info"):
        state = "error"
    evidence = [link(path, output, "result")]
    for relative, label in (
        ("agent/trajectory.json", "usage"),
        ("verifier", "checks"),
        ("artifacts/manifest.json", "artifacts"),
    ):
        target = path.parent / relative
        if target.exists():
            evidence.append(link(target, output, label))
    return Trial(
        name=f"{data['task_name']} / {data['trial_name']}",
        state=state,
        rewards=rewards,
        metrics=metrics,
        cost_source=cost_source,
        evidence=" · ".join(evidence),
        trial_id=str(data["id"]) if data.get("id") is not None else None,
    )


def aggregate(rows: list[Trial], key: str, expected: int) -> str:
    """Show a total only for full coverage; otherwise show a labelled subtotal."""
    known = [row.metrics[key] for row in rows if row.metrics[key] is not None]
    total = sum(value for value in known if value is not None)
    if len(known) == expected and expected:
        return formatted(total, key)
    return f"unknown ({formatted(total, key)} known; {len(known)}/{expected})"


@dataclass(frozen=True)
class RubricOptions:
    """Explicit native functional gate and optional exact-source quality evidence."""

    functional_key: str = "solved"
    quality_path: Path | None = None
    functional_only: bool = False


def rubric_rows(rows: list[Trial], options: RubricOptions) -> list[tuple[str, Quality, str]]:
    """Join exact source UUIDs and reject indexes for another learner population."""
    if options.functional_only and options.quality_path is not None:
        msg = "A functional-only report cannot include a code-quality index"
        raise ValueError(msg)
    qualities = quality_index(options.quality_path)
    present_ids = [row.trial_id for row in rows if row.trial_id is not None]
    if len(present_ids) != len(set(present_ids)) or not set(qualities).issubset(present_ids):
        msg = (
            "Duplicate original trial IDs or quality index contains trials outside this learner job"
        )
        raise ValueError(msg)
    results = []
    for row in rows:
        functional = (
            binary(row.rewards.get(options.functional_key))
            if row.state == "graded"
            else "unmeasured"
        )
        quality = qualities.get(row.trial_id, Quality()) if row.trial_id is not None else Quality()
        if options.functional_only:
            results.append((functional, Quality("not_applicable"), "not_applicable"))
        else:
            results.append((functional, quality, joint(functional, quality.status)))
    return results


def rubric_summary(
    outcomes: list[tuple[str, Quality, str]], expected: int, options: RubricOptions
) -> list[str]:
    """Label explicitly selected non-code reports without presenting a combined code pass."""
    if not options.functional_only:
        return summary(
            [(functional, quality.status, final) for functional, quality, final in outcomes],
            expected,
        )
    measured = sum(functional in ("pass", "fail") for functional, _, _ in outcomes)
    passed = sum(functional == "pass" for functional, _, _ in outcomes)
    return [
        "## Functional-only rubric",
        "",
        f"- Functional pass: {passed}/{measured} functionally measured submissions; {expected - measured}/{expected} attempts unavailable. Code-quality and combined benchmark rates are not applicable to this explicitly selected report.",
    ]


def render(
    job: Path,
    identity: Path,
    output: Path,
    rates_path: Path | None = None,
    rubric: RubricOptions | None = None,
) -> str:
    """Build a leaderboard from one job without pooling benchmark revisions."""
    config = read_json(job / "config.json")
    result = read_json(job / "result.json")
    if number(mapping(result.get("stats")).get("n_retries")):
        msg = "Retried jobs can discard earlier usage; report retained attempts separately"
        raise ValueError(msg)
    if config.get("source_jobs"):
        msg = "Regrades reuse learner usage; report them separately from learner jobs"
        raise ValueError(msg)
    if not identity.is_file():
        msg = f"Missing experiment identity record: {identity}"
        raise ValueError(msg)
    expected = result.get("n_total_trials")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        msg = "Job result must declare a positive n_total_trials"
        raise ValueError(msg)
    rates = read_json(rates_path) if rates_path else {}
    rows = [trial_row(path, output, rates) for path in sorted(job.glob("*/result.json"))]
    options = rubric or RubricOptions()
    outcomes = rubric_rows(rows, options)
    if len(rows) > expected:
        msg = "More trial results than declared: inspect retries before reporting this job"
        raise ValueError(msg)
    graded = [row for row in rows if row.state == "graded"]
    scores = [number(row.rewards.get("reward")) for row in graded]
    mean = (
        None
        if not scores or None in scores
        else sum(v for v in scores if v is not None) / len(scores)
    )
    errors = sum(row.state == "error" for row in rows)
    missing = expected - len(rows)
    lines = [
        "# Benchmark leaderboard",
        "",
        (
            f"Experiment: {link(identity, output, 'identity and settings')} · "
            f"{link(job / 'config.json', output, 'job config')} · "
            f"{link(job / 'result.json', output, 'job result')}"
        ),
        "",
        (
            f"Trials: {expected} expected; {len(graded)} graded; {errors} errors; "
            f"{len(rows) - len(graded) - errors} incomplete; {missing} missing results."
        ),
        "",
        (
            f"Mean reward over graded trials: {formatted(mean, 'score')} "
            f"({len(graded)}/{expected} trials). This is not a pass rate."
        ),
        "",
        *rubric_summary(outcomes, expected, options),
        "",
        "| Task / trial | Status | Rewards | Functional | Quality | Q | Benchmark | Input tokens | Cached input | Output tokens | Cost USD | Learner s | Trial s | Cost source | Evidence |",
        "| --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row, (functional, quality, final) in zip(rows, outcomes, strict=True):
        cells = [
            literal(row.name),
            row.state,
            literal(json.dumps(row.rewards, sort_keys=True)),
            functional,
            quality.status,
            formatted(quality.score, "score"),
            final,
        ]
        cells.extend(formatted(row.metrics[key], key) for key in METRICS)
        evidence = row.evidence + (
            " · " + link(quality.path, output, "quality") if quality.path else ""
        )
        cells.extend((row.cost_source, evidence))
        lines.append("| " + " | ".join(cells) + " |")
    totals = [aggregate(rows, key, expected) for key in METRICS]
    lines.extend(
        ("", "All attempted trials, including errors (summed durations, not job wall time):", "")
    )
    lines.extend(f"- {key}: {value}" for key, value in zip(METRICS, totals, strict=True))
    lines.extend(
        (
            "",
            "Input includes cached tokens; cached input is a subset, never an extra token charge.",
            "Unknown measurements are not zero. Harbor cost may be estimated; it is not verified billed spend.",
            "Native cost provenance is unverified here; retain the resolved rate entry and source/date or provider receipt before claiming cost acceptance.",
            "Authoring, review and qualification overhead is excluded. Reward correctness and acceptance need evidence review.",
        )
    )
    if rates_path:
        lines.extend(
            (
                "",
                f"Fallback USD rates: {link(rates_path, output, 'pricing record')}. Estimates use recorded token usage.",
            )
        )
    if not result.get("finished_at"):
        lines.extend(("", "Job is unfinished; this report is provisional."))
    return "\n".join(lines) + "\n"


def main() -> None:
    """Write a deterministic report from explicitly selected local evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rates", type=Path)
    parser.add_argument(
        "--functional-key",
        default="solved",
        help="Exact native binary key; choose reward explicitly only when it was authored as binary",
    )
    parser.add_argument(
        "--quality-index", type=Path, help="Map original Harbor UUIDs to native quality regrades"
    )
    parser.add_argument(
        "--functional-only",
        action="store_true",
        help="Explicitly label an all-non-code job; never waive quality for a code-changing task",
    )
    args = parser.parse_args()
    try:
        report = render(
            args.job,
            args.identity,
            args.output,
            args.rates,
            RubricOptions(args.functional_key, args.quality_index, args.functional_only),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(2, f"{error}\n")
    sys.stderr.write(f"Wrote {args.output}\n")


if __name__ == "__main__":
    main()
