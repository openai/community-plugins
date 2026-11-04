"""Exercise the standalone renderer against the documented native Harbor contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/leaderboard.py"
START = "2026-09-23T10:00:00+00:00"
END = "2026-09-23T10:01:00+00:00"


def save(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def job(tmp_path: Path) -> Path:
    path = tmp_path / "jobs/run"
    save(path / "config.json", {"n_attempts": 1})
    save(path / "result.json", {"n_total_trials": 1, "finished_at": END})
    (tmp_path / "identity.md").write_text("Local benchmark; exact task files retained.")
    return path


def trial(job: Path, name: str = "task-a", **changes: object) -> None:
    data: dict[str, object] = {
        "task_name": name,
        "trial_name": name + "__1",
        "agent_info": {"name": "codex", "model_info": {"name": "test-model"}},
        "agent_result": {"n_input_tokens": 0, "n_output_tokens": 0},
        "verifier_result": {"rewards": {"reward": 1, "criterion_R1": 1}},
        "started_at": START,
        "finished_at": END,
        "agent_execution": {"started_at": START, "finished_at": END},
    }
    data.update(changes)
    save(job / name / "result.json", data)
    save(
        job / name / "agent/trajectory.json",
        {
            "final_metrics": {
                "total_prompt_tokens": 1000000,
                "total_cached_tokens": 500000,
                "total_completion_tokens": 100000,
                "total_cost_usd": 3,
            },
        },
    )


def run_report(job: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        str(job),
        "--identity",
        str(job.parents[1] / "identity.md"),
        "--output",
        str(job.parents[1] / "leaderboard/README.md"),
        *extra,
    ]
    return subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603 -- Fixed local Python helper, no shell.


def report(job: Path) -> str:
    result = run_report(job)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    return (job.parents[1] / "leaderboard/README.md").read_text()


def test_graded_failure_is_valid_and_error_usage_stays_in_total(job: Path) -> None:
    save(job / "result.json", {"n_total_trials": 3, "finished_at": END})
    trial(job)
    trial(job, "task-b", verifier_result={"rewards": {"reward": 0, "criterion_R1": 0}})
    trial(job, "task-c", exception_info={"exception_type": "AgentTimeoutError"})
    text = report(job)
    assert "3 expected; 2 graded; 1 errors" in text
    assert "Mean reward over graded trials: 0.50 (2/3 trials)" in text
    assert "- cost: 9.000000" in text
    assert "- input: 3,000,000" in text
    assert "criterion\\_R1" in text
    assert "[result](../jobs/run/task-a/result.json)" in text


def test_unknown_usage_never_uses_codex_zero_fallback(job: Path) -> None:
    trial(job)
    (job / "task-a/agent/trajectory.json").unlink()
    text = report(job)
    assert "- input: unknown (0 known; 0/1)" in text
    assert "- cost: unknown (0.000000 known; 0/1)" in text


def test_approval_reviewer_trajectory_is_not_reported_as_learner_usage(job: Path) -> None:
    trial(job)
    path = job / "task-a/agent/trajectory.json"
    data = json.loads(path.read_text())
    data["agent"] = {"model_name": "codex-auto-review"}
    save(path, data)
    result = run_report(job)
    assert result.returncode == 2
    assert "Trajectory model 'codex-auto-review' differs from learner 'test-model'" in result.stderr
    assert not (job.parents[1] / "leaderboard/README.md").exists()


@pytest.mark.parametrize("trajectory_model", ["test-model", "openai/test-model"])
def test_trajectory_matches_provider_qualified_learner(job: Path, trajectory_model: str) -> None:
    trial(
        job,
        agent_info={"name": "codex", "model_info": {"name": "test-model", "provider": "openai"}},
    )
    path = job / "task-a/agent/trajectory.json"
    data = json.loads(path.read_text())
    data["agent"] = {"model_name": trajectory_model}
    save(path, data)
    assert "- input: 1,000,000" in report(job)


def test_missing_trial_and_phase_timing_remain_incomplete(job: Path) -> None:
    save(job / "result.json", {"n_total_trials": 2, "finished_at": None})
    trial(job, agent_execution=None, verifier_result={"rewards": {}}, finished_at=None)
    text = report(job)
    assert "0 graded; 0 errors; 1 incomplete; 1 missing results" in text
    assert "- cost: unknown (3.000000 known; 1/2)" in text
    assert "- learner: unknown (0.00 known; 0/2)" in text
    assert "Job is unfinished" in text


def test_explicit_estimate_subtracts_cached_input(job: Path) -> None:
    trial(job)
    save(
        job / "task-a/agent/trajectory.json",
        {
            "final_metrics": {
                "total_prompt_tokens": 1000000,
                "total_cached_tokens": 500000,
                "total_completion_tokens": 100000,
            }
        },
    )
    rates = job / "rates.json"
    save(
        rates,
        {
            "model": "test-model",
            "currency": "USD",
            "date": "2026-09-23",
            "source": "Test-only rates, not a live price",
            "input_per_million": 2,
            "cached_input_per_million": 0.5,
            "output_per_million": 10,
        },
    )
    result = run_report(job, "--rates", str(rates))
    assert result.returncode == 0, result.stderr
    text = (job.parents[1] / "leaderboard/README.md").read_text()
    assert "- cost: 2.250000" in text
    assert "rate estimate" in text


def test_native_zero_cost_is_preserved(job: Path) -> None:
    trial(job)
    save(job / "task-a/agent/trajectory.json", {"final_metrics": {"total_cost_usd": 0}})
    text = report(job)
    assert "- cost: 0.000000" in text
    assert "Harbor reported/estimated" in text


def test_regrade_is_not_counted_as_new_learner_usage(job: Path) -> None:
    save(job / "config.json", {"source_jobs": [{"action": "regrade"}]})
    result = run_report(job)
    assert result.returncode == 2
    assert "Regrades reuse learner usage" in result.stderr


@pytest.mark.parametrize("cached", [1000001, -1, True, "500"])
def test_invalid_usage_is_rejected(job: Path, cached: object) -> None:
    trial(job)
    save(
        job / "task-a/agent/trajectory.json",
        {
            "final_metrics": {
                "total_prompt_tokens": 1000000,
                "total_cached_tokens": cached,
            }
        },
    )
    assert run_report(job).returncode == 2


def test_untrusted_labels_cannot_create_links_or_table_cells(job: Path) -> None:
    trial(job, task_name="[click](https://invalid.example) | <script>")
    text = report(job)
    assert "\\[click\\]\\(https://invalid\\.example\\) \\| &lt;script&gt;" in text
    assert "[click](https://invalid.example)" not in text


def test_oracle_qualification_is_not_a_learner_score(job: Path) -> None:
    trial(job, agent_info={"name": "oracle"})
    result = run_report(job)
    assert result.returncode == 2
    assert "Only single-step Codex learner results" in result.stderr


def test_null_criterion_leaves_trial_incomplete(job: Path) -> None:
    trial(job, verifier_result={"rewards": {"reward": 1, "criterion_R1": None}})
    assert "0 graded; 0 errors; 1 incomplete" in report(job)


def test_missing_cached_usage_prevents_an_estimate(job: Path) -> None:
    trial(job)
    save(
        job / "task-a/agent/trajectory.json",
        {
            "final_metrics": {
                "total_prompt_tokens": 1000000,
                "total_completion_tokens": 100000,
            }
        },
    )
    rates = job / "rates.json"
    save(
        rates,
        {
            "model": "test-model",
            "currency": "USD",
            "date": "2026-09-23",
            "source": "Test-only rates",
            "input_per_million": 2,
            "cached_input_per_million": 0.5,
            "output_per_million": 10,
        },
    )
    assert run_report(job, "--rates", str(rates)).returncode == 0
    text = (job.parents[1] / "leaderboard/README.md").read_text()
    assert "- cost: unknown (0.000000 known; 0/1)" in text


def test_native_retries_cannot_hide_discarded_usage(job: Path) -> None:
    trial(job)
    save(job / "result.json", {"n_total_trials": 1, "finished_at": END, "stats": {"n_retries": 1}})
    result = run_report(job)
    assert result.returncode == 2
    assert "Retried jobs can discard earlier usage" in result.stderr


def test_provider_qualified_model_uses_its_exact_rates(job: Path) -> None:
    trial(
        job,
        agent_info={"name": "codex", "model_info": {"name": "test-model", "provider": "openai"}},
        agent_result={"model_usage": {"openai/test-model": {"n_input_tokens": 1000000}}},
    )
    save(
        job / "task-a/agent/trajectory.json",
        {
            "final_metrics": {
                "total_prompt_tokens": 1000000,
                "total_cached_tokens": 500000,
                "total_completion_tokens": 100000,
            }
        },
    )
    rates = job / "rates.json"
    save(
        rates,
        {
            "model": "openai/test-model",
            "currency": "USD",
            "date": "2026-09-23",
            "source": "Test-only rates",
            "input_per_million": 2,
            "cached_input_per_million": 0.5,
            "output_per_million": 10,
        },
    )
    result = run_report(job, "--rates", str(rates))
    assert result.returncode == 0, result.stderr
    text = (job.parents[1] / "leaderboard/README.md").read_text()
    assert "- cost: 2.250000" in text


SOURCE_A = "d24a952d-fd96-4c16-933b-6b79d1270b40"
SOURCE_B = "d24a952d-fd96-4c16-933b-6b79d1270b41"
SOURCE_C = "d24a952d-fd96-4c16-933b-6b79d1270b42"
SOURCE_D = "d24a952d-fd96-4c16-933b-6b79d1270b43"


def quality_record(root: Path, trial_id: str, states: dict[str, str]) -> Path:
    path = root / trial_id
    save(
        path / "result.json",
        {"config": {"source_trial": {"action": "regrade", "trial_id": trial_id}}},
    )
    dims = {
        "readability": "pass",
        "maintainability": "pass",
        "scope": "pass",
        "tests": "pass",
        **states,
    }
    status = next(
        value
        for value in ("fail", "review_required", "unmeasured", "pass")
        if value in dims.values()
    )
    score = (
        sum(value == "pass" for value in dims.values()) / 4
        if set(dims.values()) <= {"pass", "fail"}
        else None
    )
    save(
        path / "verifier/quality-review.json",
        {
            "policy": "code-quality-v1",
            "source_trial_id": trial_id,
            "packet_sha256": "a" * 64,
            "status": status,
            "quality_pass": {"pass": 1, "fail": 0}.get(status),
            "quality_score": score,
            "dimensions": dims,
            "primary": {"original": True},
            "reviewer": {"original": True},
            "adjudication": None,
        },
    )
    return path


def quality_report(
    job: Path, records: dict[str, dict[str, str]]
) -> subprocess.CompletedProcess[str]:
    index = job.parents[1] / "quality-index.json"
    save(
        index,
        {
            key: {"regrade": str(quality_record(job.parents[1] / "regrades", key, dims))}
            for key, dims in records.items()
        },
    )
    return run_report(job, "--quality-index", str(index))


def test_correct_but_poor_or_unresolved_code_does_not_pass_headline(job: Path) -> None:
    save(job / "result.json", {"n_total_trials": 4, "finished_at": END})
    trial(job, "a", id=SOURCE_A, verifier_result={"rewards": {"reward": 1, "solved": 1}})
    trial(job, "b", id=SOURCE_B, verifier_result={"rewards": {"reward": 1, "solved": 1}})
    trial(job, "c", id=SOURCE_C, verifier_result={"rewards": {"reward": 1, "solved": 1}})
    trial(job, "d", id=SOURCE_D, verifier_result={"rewards": {"reward": 0.6, "solved": 0}})
    completed = quality_report(
        job, {SOURCE_A: {}, SOURCE_B: {"tests": "fail"}, SOURCE_C: {"scope": "review_required"}}
    )
    assert completed.returncode == 0, completed.stderr
    text = (job.parents[1] / "leaderboard/README.md").read_text()
    assert "Benchmark pass: 1/4" in text
    assert "2 confirmed failures; 1 awaiting adjudication; 0 with quality unmeasured" in text
    assert "Functional pass: 3/4" in text
    assert "Quality pass: 1/2" in text
    assert "coverage 2/4" in text
    assert "| pass | fail | 0.75 | fail |" in text
    assert "| pass | review_required | unknown | review_required |" in text
    assert "| fail | unmeasured | unknown | fail |" in text


def test_quality_missing_never_fills_in_a_functional_pass_and_errors_are_separate(
    job: Path,
) -> None:
    save(job / "result.json", {"n_total_trials": 3, "finished_at": END})
    trial(job, "a", id=SOURCE_A, verifier_result={"rewards": {"reward": 1, "solved": 1}})
    trial(
        job,
        "b",
        id=SOURCE_B,
        exception_info={"exception_type": "VerifierError"},
        verifier_result={"rewards": {"reward": 1, "solved": 1}},
    )
    assert quality_report(job, {SOURCE_B: {"tests": "fail"}}).returncode == 0
    text = (job.parents[1] / "leaderboard/README.md").read_text()
    assert "Benchmark pass: 0/1" in text
    assert "1 with quality unmeasured. 2/3 attempts have no usable functional gate" in text
    assert "| unmeasured | fail | 0.75 | unavailable |" in text


def test_quality_index_rejects_native_source_mismatch_and_foreign_population(job: Path) -> None:
    trial(job, id=SOURCE_A, verifier_result={"rewards": {"reward": 1, "solved": 1}})
    assert quality_report(job, {SOURCE_B: {}}).returncode == 2
    entry = quality_record(job.parents[1] / "regrades", SOURCE_A, {})
    save(
        entry / "result.json",
        {"config": {"source_trial": {"action": "regrade", "trial_id": SOURCE_B}}},
    )
    index = job.parents[1] / "quality-index.json"
    save(index, {SOURCE_A: {"regrade": str(entry)}})
    failed = run_report(job, "--quality-index", str(index))
    assert failed.returncode == 2
    assert "Native regrade does not come from original" in failed.stderr


def test_native_regrade_without_audit_reports_unmeasured_and_links_failure(job: Path) -> None:
    trial(job, id=SOURCE_A, verifier_result={"rewards": {"reward": 1, "solved": 1}})
    entry = quality_record(job.parents[1] / "regrades", SOURCE_A, {})
    (entry / "verifier/quality-review.json").unlink()
    index = job.parents[1] / "quality-index.json"
    save(index, {SOURCE_A: {"regrade": str(entry)}})
    assert run_report(job, "--quality-index", str(index)).returncode == 0
    text = (job.parents[1] / "leaderboard/README.md").read_text()
    assert "Benchmark pass: 0/1" in text
    assert "1 with quality unmeasured" in text
    assert f"regrades/{SOURCE_A}/result.json" in text


def test_only_explicit_binary_historical_reward_can_be_used_as_functional_gate(job: Path) -> None:
    trial(job, id=SOURCE_A)
    assert "Benchmark pass: 0/0" in report(job)
    assert run_report(job, "--functional-key", "reward").returncode == 0
    text = (job.parents[1] / "leaderboard/README.md").read_text()
    assert "Benchmark pass: 0/1" in text
    trial(job, id=SOURCE_A, verifier_result={"rewards": {"reward": 0.8}})
    assert (
        "Selected functional gate must be" in run_report(job, "--functional-key", "reward").stderr
    )


def test_explicit_non_code_report_is_not_mislabeled_as_a_combined_benchmark(job: Path) -> None:
    trial(job, id=SOURCE_A, verifier_result={"rewards": {"reward": 1, "solved": 1}})
    assert run_report(job, "--functional-only").returncode == 0
    text = (job.parents[1] / "leaderboard/README.md").read_text()
    assert "Functional-only rubric" in text
    assert "Functional pass: 1/1" in text
    assert "Benchmark pass:" not in text
    index = job.parents[1] / "quality-index.json"
    save(index, {})
    assert run_report(job, "--functional-only", "--quality-index", str(index)).returncode == 2


def test_adjudication_must_preserve_original_packet_and_judge_evidence(job: Path) -> None:
    trial(job, id=SOURCE_A, verifier_result={"rewards": {"reward": 1, "solved": 1}})
    entry = quality_record(job.parents[1] / "regrades", SOURCE_A, {"tests": "review_required"})
    final = (
        quality_record(job.parents[1] / "adjudication", SOURCE_A, {})
        / "verifier/quality-review.json"
    )
    evidence = json.loads(final.read_text())
    evidence["adjudication"] = {
        "reviewer": "Maintainer",
        "decisions": {"tests": {"score": 1, "reason": "src/file.py:2 valid"}},
    }
    save(final, evidence)
    index = job.parents[1] / "quality-index.json"
    save(index, {SOURCE_A: {"regrade": str(entry), "adjudicated": str(final)}})
    assert run_report(job, "--quality-index", str(index)).returncode == 0
    assert "Benchmark pass: 1/1" in (job.parents[1] / "leaderboard/README.md").read_text()
    evidence["packet_sha256"] = "b" * 64
    save(final, evidence)
    assert (
        "changes original judge evidence" in run_report(job, "--quality-index", str(index)).stderr
    )
    evidence["packet_sha256"] = "a" * 64
    evidence["adjudication"] = {
        "reviewer": "Maintainer",
        "decisions": {"scope": {"score": 1, "reason": "src/file.py:2 already agreed"}},
    }
    save(final, evidence)
    assert "disputed decisions" in run_report(job, "--quality-index", str(index)).stderr
