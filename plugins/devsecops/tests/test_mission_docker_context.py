"""Check real COPY inputs against the mission app's closed context allowlist."""

import shlex
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "demo/software-factory/apps/mission-status"


def copy_inputs(dockerfile):
    result = []
    for line in dockerfile.replace("\\\n", " ").splitlines():
        words = shlex.split(line, comments=True)
        if not words or words[0].upper() != "COPY":
            continue
        words = words[1:]
        while words and words[0].startswith("--"):
            assert words[0].startswith("--chown="), "Review context checks for new COPY semantics"
            words.pop(0)
        assert len(words) >= 2
        result.extend(words[:-1])
    assert result, "The runtime image must declare its copied inputs"
    return result


def missing_inputs(dockerfile, dockerignore):
    # This app intentionally uses deny-all followed by literal inclusions.
    # Refuse richer patterns rather than pretending to implement Docker's matcher.
    rules = [line.strip() for line in dockerignore.splitlines() if line.strip() and not line.startswith("#")]
    assert rules[0] == "**"
    assert all(rule.startswith("!") and not any(c in rule[1:] for c in "*?[]") for rule in rules[1:])
    allowed = [rule[1:].rstrip("/") for rule in rules[1:]]
    return [
        source
        for source in copy_inputs(dockerfile)
        if not any(source.rstrip("/") == rule or source.startswith(rule + "/") for rule in allowed)
    ]


def test_every_actual_dockerfile_copy_source_is_included_in_build_context():
    assert missing_inputs((APP / "Dockerfile").read_text(), (APP / ".dockerignore").read_text()) == []


def test_original_live_failure_is_reproduced_by_context_coverage_check():
    rules = (APP / ".dockerignore").read_text()
    assert rules.count("!probe.py\n") == 1
    assert missing_inputs((APP / "Dockerfile").read_text(), rules.replace("!probe.py\n", "")) == ["probe.py"]


def test_copied_static_sources_exist_and_only_wheelhouse_is_acquired_later():
    acquired = []
    for source in copy_inputs((APP / "Dockerfile").read_text()):
        if source.endswith("/"):
            acquired.append(source)
        else:
            assert (APP / source).is_file(), source
    assert acquired == ["wheelhouse/"]
