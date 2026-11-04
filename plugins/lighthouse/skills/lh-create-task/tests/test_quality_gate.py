"""Exercise the decision cascade through its copyable CLI and trusted native file contract."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

SCRIPTS = Path(__file__).parents[1] / "scripts"
SOURCE = "d24a952d-fd96-4c16-933b-6b79d1270b40"
NAMES = ("readability", "maintainability", "scope", "tests")


def write_native(
    root: Path, judge: str, failed: tuple[str, ...] = (), *, invalid: bool = False
) -> None:
    stage = root / judge
    stage.mkdir(parents=True)
    score = (4 - len(failed)) / 4
    (stage / "reward.json").write_text(json.dumps({"reward": score}))
    criteria = [
        {
            "name": name,
            "value": int(name not in failed),
            "raw": None if invalid else {},
            "reasoning": "src/change.py:2-3 duplicates an independently changed rule"
            if name in failed
            else "Reviewed relevant code",
        }
        for name in NAMES
    ]
    (stage / "reward-details.json").write_text(
        json.dumps({"reward": {"kind": "llm", "score": score, "criteria": criteria}})
    )


def arguments(root: Path) -> list[str]:
    (root / "packet.md").write_text("Trusted snapshot")
    (root / "evidence.txt").write_text("src/change.py\n")
    return [
        "--source-trial-id",
        SOURCE,
        "--packet",
        str(root / "packet.md"),
        "--evidence",
        str(root / "evidence.txt"),
        "--output",
        str(root),
    ]


def invoke(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPTS / "quality_gate.py"),
        "finalize",
        *arguments(root),
        *extra,
    ]
    return subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603 -- Invokes only the fixed local verifier.


def result(root: Path) -> dict[str, object]:
    return cast("dict[str, object]", json.loads((root / "quality-review.json").read_text()))


@pytest.mark.parametrize(
    ("sol", "astra", "status", "score"),
    [
        ((), None, "pass", 1),
        (("tests",), ("tests",), "fail", 0.75),
        (("tests",), (), "review_required", None),
        (("tests",), ("scope",), "review_required", None),
        (("tests", "scope"), ("tests",), "fail", None),
        (("tests",), None, "unmeasured", None),
    ],
)
def test_native_gate_never_promotes_failure_or_unknown(
    tmp_path: Path,
    sol: tuple[str, ...],
    astra: tuple[str, ...] | None,
    status: str,
    score: float | None,
) -> None:
    write_native(tmp_path, "sol", sol)
    if astra is not None:
        write_native(tmp_path, "astra", astra)
    assert invoke(tmp_path).returncode == 0
    data = result(tmp_path)
    assert data["status"] == status
    assert data["quality_score"] == score
    assert data["source_trial_id"] == SOURCE
    reward = tmp_path / "reward.json"
    if status in {"pass", "fail"}:
        assert json.loads(reward.read_text()) == {"reward": int(status == "pass")}
    else:
        assert not reward.exists()
    if not sol:
        assert data["reviewer"] is None


def test_native_error_withholds_reward_and_does_not_consult_astra(tmp_path: Path) -> None:
    write_native(tmp_path, "sol", ("tests",), invalid=True)
    write_native(tmp_path, "astra", ("tests",))
    (tmp_path / "reward.json").write_text('{"reward": 1}')
    assert invoke(tmp_path).returncode == 0
    data = result(tmp_path)
    assert data["status"] == "unmeasured"
    assert data["reviewer"] is None
    assert not (tmp_path / "reward.json").exists()


def test_grounded_human_can_clear_only_a_disputed_dimension(tmp_path: Path) -> None:
    write_native(tmp_path, "sol", ("tests",))
    write_native(tmp_path, "astra")
    human = tmp_path / "human.json"
    human.write_text(
        json.dumps(
            {
                "reviewer": "Maintainer A",
                "decisions": {
                    "tests": {
                        "decision": "pass",
                        "reason": "src/change.py:2 the preceding branch handles this case",
                    }
                },
            }
        )
    )
    assert invoke(tmp_path, "--adjudication", str(human)).returncode == 0
    data = result(tmp_path)
    assert data["status"] == "pass"
    assert data["adjudication"]
    for dimension, reason in (
        ("scope", "src/change.py:2 agreed result"),
        ("tests", "unknown.py:2 wrong context"),
    ):
        human.write_text(
            json.dumps(
                {
                    "reviewer": "Maintainer A",
                    "decisions": {dimension: {"decision": "pass", "reason": reason}},
                }
            )
        )
        assert invoke(tmp_path, "--adjudication", str(human)).returncode == 2


def test_run_uses_same_rubric_and_workspace_only_dispatching_astra_on_failure(
    tmp_path: Path,
) -> None:
    for helper in ("quality_gate.py", "publish_quality.py"):
        (tmp_path / helper).write_text((SCRIPTS / helper).read_text())
    fixture = tmp_path / "saved"
    write_native(fixture, "sol", ("tests",))
    write_native(fixture, "astra")
    (tmp_path / "run_quality_judge.py").write_text(
        """import os, shutil, sys\nfrom pathlib import Path\nroot=Path(__file__).parent\nname=os.environ["REWARDKIT_JUDGE"].split("-")[-1]\nwith (root/"calls").open("a") as f: f.write(name+" "+" ".join(sys.argv[1:4])+"\\n")\nout=Path(sys.argv[-1]).parent\nfor namefile in ("reward.json","reward-details.json"): shutil.copyfile(root/"saved"/name/namefile,out/namefile)\n"""
    )
    for index, sol_fail in enumerate((True, False)):
        if not sol_fail:
            (fixture / "sol/reward.json").write_bytes((fixture / "astra/reward.json").read_bytes())
            (fixture / "sol/reward-details.json").write_bytes(
                (fixture / "astra/reward-details.json").read_bytes()
            )
        output = tmp_path / f"run{index}"
        output.mkdir()
        args = arguments(output)
        command = [
            sys.executable,
            str(tmp_path / "quality_gate.py"),
            "run",
            *args,
            "--rubric",
            "/trusted/rubric",
            "--workspace",
            "/trusted/workspace",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603 -- Runs the fixed verifier with a local stand-in provider.
        assert completed.returncode == 0, completed.stderr
        assert result(output)["status"] == ("review_required" if sol_fail else "pass")
        assert subprocess.run(command, capture_output=True, text=True, check=False).returncode == 2  # noqa: S603 -- Exact same local command; existing judge outputs must be preserved.
    assert (tmp_path / "calls").read_text().splitlines() == [
        "sol /trusted/rubric --workspace /trusted/workspace",
        "astra /trusted/rubric --workspace /trusted/workspace",
        "sol /trusted/rubric --workspace /trusted/workspace",
    ]


def test_responses_transport_preserves_schema_limits_and_sanitizes(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "quality_transport_test", SCRIPTS / "run_quality_judge.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requests: list[dict[str, object]] = []

    class Provider:
        async def create(self, **kwargs: object) -> SimpleNamespace:
            requests.append(kwargs)
            if len(requests) == 2:
                message = "sensitive text"
                raise ValueError(message)
            return SimpleNamespace(
                output_text="{}",
                model="gpt-6-sol",
                id="resp-test",
                status="completed",
                usage={"input_tokens": 12},
            )

    tokenizer = SimpleNamespace(encode=list)
    transport = module.Transport(Provider(), tokenizer, tmp_path / "usage.jsonl")
    native = {
        "model": "openai/gpt-6-sol",
        "reasoning_effort": "medium",
        "timeout": 120,
        "messages": [{"role": "user", "content": "packet"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "Grade", "schema": {"type": "object"}, "strict": True},
        },
    }
    assert asyncio.run(transport.complete(**native)).choices[0].message.content == "{}"
    assert requests[0]["model"] == "gpt-6-sol"
    assert requests[0]["reasoning"] == {"effort": "medium"}
    assert requests[0]["store"] is False
    assert requests[0]["service_tier"] == "default"
    assert requests[0]["max_output_tokens"] == 6000
    assert cast("dict[str, object]", requests[0]["text"])["format"] == {
        "type": "json_schema",
        "name": "Grade",
        "schema": {"type": "object"},
        "strict": True,
    }
    with pytest.raises(RuntimeError, match="ValueError: HTTP None"):
        asyncio.run(transport.complete(**native))
    with pytest.raises(ValueError, match="medium"):
        asyncio.run(transport.complete(**{**native, "reasoning_effort": "max"}))
    with pytest.raises(RuntimeError, match="request cap"):
        asyncio.run(transport.complete(**native))
    assert len(requests) == 2
    assert "sensitive" not in (tmp_path / "usage.jsonl").read_text()
    too_big = module.Transport(Provider(), tokenizer, tmp_path / "too-big.jsonl")
    with pytest.raises(RuntimeError, match="before transmission"):
        asyncio.run(
            too_big.complete(**{**native, "messages": [{"role": "user", "content": "a" * 43_000}]})
        )
    assert not (tmp_path / "too-big.jsonl").exists()
