#!/usr/bin/env python3
"""Review a bundled synthetic Helm change and save a report you can open.

Run after ``uv sync --frozen`` and approved tool provisioning. The default
changes a replica count; --risk seeds deliberate privilege, RBAC, exposure,
and lifecycle changes. Exit 1 is the expected result for that risky example.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from devsecops.cli import main as review_main  # noqa: E402
from devsecops.process import run  # noqa: E402
from devsecops.resources import strict_load_yaml  # noqa: E402


def _git(repo: Path, *arguments: str) -> str:
    # Git writes affect only this synthetic repository. Fixed identity/date make
    # its commit IDs reproducible without inheriting user signing or hook setup.
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "DevSecOps synthetic demo", "GIT_AUTHOR_EMAIL": "demo@example.invalid",
        "GIT_COMMITTER_NAME": "DevSecOps synthetic demo", "GIT_COMMITTER_EMAIL": "demo@example.invalid",
        "GIT_AUTHOR_DATE": "2026-10-21T00:00:00+00:00", "GIT_COMMITTER_DATE": "2026-10-21T00:00:00+00:00",
    }
    code, output, _, _ = run([
        "git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
        "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=demo", *arguments,
    ], cwd=repo, env=environment, limit=100_000)
    if code:
        raise ValueError("The synthetic Git repository could not be prepared; verify Git is available.")
    return output.decode("utf-8").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="New report directory; use a canonical path without symlink components")
    parser.add_argument("--risk", action="store_true", help="Review deliberately risky fixture changes; exit 1 is expected")
    parser.add_argument("--helm", default=os.environ.get("DEVSECOPS_HELM"), help="Approved Helm executable; otherwise use the CLI default")
    parser.add_argument("--kubeconform", default=os.environ.get("DEVSECOPS_KUBECONFORM"), help="Approved kubeconform executable; otherwise use the CLI default")
    parser.add_argument("--schema-location", default=os.environ.get("DEVSECOPS_SCHEMAS"), help="Trusted local schema directory; otherwise use the CLI default")
    args = parser.parse_args(argv)
    output = args.output.expanduser().absolute()
    try:
        if output.exists():
            raise ValueError("Select a new output directory; an earlier demo will not be overwritten.")
        fixture = ROOT / "tests/fixtures/helm-web"
        generic_profile = ROOT / "profiles/generic.example.json"
        if not fixture.is_dir() or not generic_profile.is_file():
            raise ValueError("The package is incomplete: its synthetic fixture or generic profile is unavailable.")
        with tempfile.TemporaryDirectory(prefix="devsecops-synthetic-demo-") as temporary:
            workspace = Path(temporary).resolve()
            repo = workspace / "repository"
            shutil.copytree(fixture, repo)
            _git(repo, "init", "-q", "--template=")
            _git(repo, "add", ".")
            _git(repo, "commit", "-qm", "Synthetic baseline")
            base = _git(repo, "rev-parse", "HEAD")
            values_path = repo / "values/staging.yaml"
            values = strict_load_yaml(values_path.read_text(encoding="utf-8"))[0]
            if args.risk:
                values.update(strict_load_yaml((repo / "values/risky.yaml").read_text(encoding="utf-8"))[0])
            else:
                values["replicaCount"] = 3
            values_path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
            _git(repo, "add", "values/staging.yaml")
            _git(repo, "commit", "-qm", "Synthetic risky candidate" if args.risk else "Synthetic replica-count change")
            head = _git(repo, "rev-parse", "HEAD")
            trusted_profile = workspace / "generic-profile.json"
            trusted_profile.write_bytes(generic_profile.read_bytes())
            command = [
                "review", "--repo", str(repo), "--base", base, "--head", head,
                "--profile", str(trusted_profile), "--output", str(output),
            ]
            for option in ("helm", "kubeconform", "schema_location"):
                if getattr(args, option):
                    command.extend(["--" + option.replace("_", "-"), str(getattr(args, option))])
            print("Reviewing the bundled synthetic Helm change…", file=sys.stderr)
            result = review_main(command)
        expected = 1 if args.risk else 0
        if result == expected:
            print(f"Demo complete. Open {output / 'report.html'}", file=sys.stderr)
            if args.risk:
                print("Exit 1 is expected: the review found the deliberately risky changes.", file=sys.stderr)
        else:
            print("The demo did not produce its expected decision. Review the report's coverage gaps and tool setup.", file=sys.stderr)
        return result
    except ValueError as error:
        print(f"Demo setup failed: {error}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print("Demo setup failed. Check Git, installed Python dependencies, the bundled fixture, and the new output path.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
