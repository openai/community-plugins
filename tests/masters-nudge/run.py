"""Run the original upstream suite against only the shipped plugin bytes.

A temporary source-shaped view accommodates the upstream tests' root layout;
no second implementation, installed Codex login or paid Provider call is used.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PLUGIN = REPO / "plugins/masters-nudge"

def main():
    receipt = json.loads((HERE / "source-receipt.json").read_text(encoding="utf-8"))
    for name, item in receipt["files"].items():
        raw = (REPO / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError(f"Source receipt mismatch: {name}")
    with tempfile.TemporaryDirectory(prefix="masters-nudge-suite-") as raw:
        view = Path(raw)
        package = view / "plugins/masters-nudge"
        shutil.copytree(PLUGIN, package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # The committed inventory owns which modules are mirrored for upstream imports.
        inventory = subprocess.check_output([
            sys.executable, "-B", "-c",
            "import json; from masters_nudge.plugin_inventory import package_files; print(json.dumps(package_files(source='generated')))"
        ], cwd=package, text=True)
        for name in json.loads(inventory):
            target = view / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package / name, target)
        shutil.copytree(HERE / "support/upstream-tests", view / "tests")
        (view / "tests/__init__.py").write_text("", encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(view), "PYTHONDONTWRITEBYTECODE": "1"}
        compilation = subprocess.run([sys.executable, "-m", "compileall", "-q", str(package)], env=env)
        if compilation.returncode:
            return compilation.returncode
        return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"], cwd=view, env=env).returncode

if __name__ == "__main__":
    raise SystemExit(main())
