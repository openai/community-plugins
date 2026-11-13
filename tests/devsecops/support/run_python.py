"""Run every packaged pytest case and retain explicit coverage gaps."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import time
import pytest


class Receipt:
    def __init__(self, output):
        self.output = output
        self.stream = (output / "outcomes.jsonl").open("x", buffering=1)
        self.collected = 0
        self.outcomes = []
        self.deselected = 0

    def pytest_deselected(self, items):
        self.deselected += len(items)

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)
        (self.output / "collection.json").write_text(json.dumps({
            "collected": self.collected,
            "expected_floor": 6498,
            "nodeids": [item.nodeid for item in session.items],
        }, indent=2) + "\n")
        if self.collected < 6498 or self.deselected:
            raise pytest.UsageError("The complete preview suite must retain at least 6,498 cases and deselect none")

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or report.outcome in {"failed", "skipped"}:
            row = {"nodeid": report.nodeid, "phase": report.when, "outcome": report.outcome}
            if report.skipped or report.failed:
                row["reason"] = str(report.longrepr)[-8000:]
            self.outcomes.append(row)
            self.stream.write(json.dumps(row) + "\n")


def main():
    plugin, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    os.chdir(plugin)
    os.environ.pop("PYTEST_ADDOPTS", None)
    sys.path[:0] = [str(plugin / "src"), str(plugin), str(plugin / "tests")]
    receipt = Receipt(output)
    start = time.monotonic()
    wall_start = time.time()
    code = int(pytest.main([str(plugin / "tests"), "-q", "-ra", "--strict-markers",
                           "-o", "addopts=", "-o", "cache_dir=" + str(output / "pytest-cache"),
                           "--junitxml=" + str(output / "junit.xml")], plugins=[receipt]))
    receipt.stream.close()
    skipped = [row for row in receipt.outcomes if row["outcome"] == "skipped"]
    duration = time.monotonic() - start
    wall_duration = time.time() - wall_start
    clock_consistent = abs(wall_duration - duration) <= 5
    required_missing = [row for row in skipped if any(value in row["reason"] for value in
                        ["Real Helm", "real Helm", "Real approved Helm", "Real approved Kustomize",
                         "Real Kustomize required", "Real kubeconform", "Pinned real Helm", "pinned Helm/Kubeconform",
                         "Real pinned Cosign", "Provision exact tools", "schema bootstrap required",
                         "offline wheel", "offline dependency wheel"]) ]
    external_reasons = (
        "Case-insensitive alias regression requires a supporting filesystem",
        "Case-sensitive filesystem has no case alias",
        "Filesystem does not expose a case alias",
        "Case aliases are unavailable on this filesystem",
        "Official BuildKit archive has not been staged for maintainer qualification",
        "Official RootlessKit archive has not been staged for maintainer qualification",
        "Explicit host macOS network-denial qualification",
        "Explicit retained official package required; no acquisition in tests",
        "Opt-in installed CLI configuration preflight; never invokes a model",
    )
    unclassified = [row for row in skipped if not any(reason in row["reason"] for reason in external_reasons)]
    result = {"collected": receipt.collected, "deselected": receipt.deselected, "pytest_exit_code": code,
              "duration_seconds": duration, "wall_duration_seconds": wall_duration,
              "clock_consistent": clock_consistent, "coverage_gaps": skipped,
              "missing_required_tool_coverage": required_missing,
              "unclassified_coverage_gaps": unclassified,
              "outcomes": receipt.outcomes, "live_qualification": False,
              "scope": "complete-packaged-suite-with-explicit-external-platform-gaps"}
    (output / "python-suite.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Collected {receipt.collected}; explicit skipped coverage gaps: {len(skipped)}; pytest exit: {code}")
    for row in skipped:
        print("COVERAGE GAP:", row["nodeid"], row["reason"])
    return code or (1 if required_missing or unclassified or not clock_consistent else 0)


if __name__ == "__main__":
    raise SystemExit(main())
