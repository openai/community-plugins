"""Real cold-package pass/fail/unknown reviews; no cluster, account or network access."""
from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def main():
    source = Path(sys.argv[1]).resolve()
    context = json.loads(Path(sys.argv[2]).read_text())
    environment = dict(os.environ, **context["environment"])
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    results = Path(context["results"])
    with tempfile.TemporaryDirectory(prefix="devsecops-cold-package-", dir=results) as temporary:
        root = Path(temporary).resolve()
        archives = []
        for suffix in ("a", "b"):
            archive = root / (suffix + ".zip")
            subprocess.run([sys.executable, "-I", "-B", str(source / "scripts/build_plugin.py"), "--output", str(archive)],
                           env=environment, check=True, timeout=120, stdout=subprocess.DEVNULL)
            archives.append(archive)
        assert archives[0].read_bytes() == archives[1].read_bytes(), "Same package produced different archive bytes"
        installed_parent = root / "arbitrary install location"
        with zipfile.ZipFile(archives[0]) as archive:
            names = archive.namelist()
            assert len(names) == len(set(names))
            assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)
            archive.extractall(installed_parent)
        installed = installed_parent / "devsecops"
        # A test-only synthetic secret exercises the actual packaged render/redaction path.
        import yaml
        canary = "SYNTHETIC_PACKAGED_SECRET_7f5efb"
        fixture = installed / "tests/fixtures/helm-web/values/staging.yaml"
        values = yaml.safe_load(fixture.read_text())
        values["secret"] = {"enabled": True, "token": canary}
        fixture.write_text(yaml.safe_dump(values))
        harness = root / "cold.py"
        harness.write_text("""import os, runpy, sys
blocked = os.path.realpath(sys.argv.pop(1))
def audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes)):
        candidate = os.path.realpath(os.fsdecode(args[0]))
        if candidate == blocked or candidate.startswith(blocked + os.sep):
            raise PermissionError('Original source checkout access denied by cold-package test')
    if event in ('socket.connect', 'socket.getaddrinfo'):
        raise PermissionError('Network denied by cold-package test')
sys.addaudithook(audit)
script = sys.argv.pop(1)
sys.argv[0] = script
runpy.run_path(script, run_name='__main__')
""")
        outcomes = []
        for name, expected, extra in [("safe", 0, []), ("risky", 1, ["--risk"]),
                                      ("missing-tool", 2, ["--helm", str(root / "missing-helm")])]:
            output = root / name
            invocation = [sys.executable, "-I", "-B", str(harness), str(source), str(installed / "scripts/demo.py"),
                          "--output", str(output), *extra]
            result = subprocess.run(invocation, cwd=root, env=environment, capture_output=True, text=True, timeout=120)
            assert result.returncode == expected, (name, result.returncode, result.stderr)
            report = json.loads((output / "report.json").read_text())
            assert report["decision"] == {0: "pass", 1: "fail", 2: "unknown"}[expected], report["decision"]
            assert (output / "run-manifest.json").is_file(), "Missing terminal report manifest"
            if expected == 1:
                assert report["summary"]["high_findings"] > 0
            if expected == 2:
                assert report["summary"]["required_gaps"] > 0
            for file in output.iterdir():
                if file.is_file():
                    raw = file.read_bytes()
                    assert canary.encode() not in raw and base64.b64encode(canary.encode()) not in raw, file.name
            assert canary not in result.stdout + result.stderr
            outcomes.append({"case": name, "exit_code": result.returncode, "decision": report["decision"]})
        assert not list(installed.rglob("__pycache__")), "Installed package was modified by Python imports"
        receipt = {"archive_sha256": hashlib.sha256(archives[0].read_bytes()).hexdigest(),
                   "reproducible_archive": True, "cases": outcomes,
                   "source_access_guard": "Python audit hook; not an OS sandbox",
                   "live_gitlab_or_cluster": False, "scope": "synthetic-local-real-tools"}
        target = results / ("package-check-" + root.name.rsplit("-", 1)[-1] + ".json")
        target.write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
