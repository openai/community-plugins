"""The fixture's small Text-to-CAD 0.10.3 command boundary.

Runtime installation is explicit and separate. All server state is scoped to the
supplied directory; this module never changes the user's HOME or CAD installation.
"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

CAD_VERSION = "0.10.3"
URL_QUERY = re.compile(r"(https?://[^\s\"'<>?]+)\?[^\s\"'<>]+")
SECRET_FIELD = re.compile(
    r'''(?i)(["']?(?:token|access_token|authorization)["']?\s*[:=]\s*)'''
    r'''(?:"[^"]*"|'[^']*'|(?:Bearer|Basic)\s+[^\s,}]+|[^\s,}]+)''')


def sanitize(text: str) -> str:
    """Viewer URLs are bearer credentials. Never put their queries in logs."""
    return SECRET_FIELD.sub(r"\1[redacted]", URL_QUERY.sub(r"\1?[redacted]", text))


def environment(state_root: Path) -> dict[str, str]:
    named_root = Path(state_root)
    if named_root.is_symlink():
        raise OSError("CAD state root must not be a symlink")
    root = named_root.resolve()
    env = os.environ.copy()
    directories = {
        "CAD_STATE_DIR": "state", "CAD_CACHE_DIR": "cache",
        "CAD_ENGINE_DIR": "engine", "CAD_LAUNCHER_DIR": "bin",
        "MPLCONFIGDIR": "matplotlib",
    }
    # Inspect every child before creating or chmod-ing any directory. A caller
    # may name /tmp through the OS's normal parent alias, but its owned root and
    # immediate runtime-state children must never redirect writes elsewhere.
    for subdir in directories.values():
        folder = root / subdir
        if folder.is_symlink():
            raise OSError(f"CAD state directory must not be a symlink: {subdir}")
        if folder.exists() and not folder.is_dir():
            raise OSError(f"CAD state directory is not a directory: {subdir}")
    for key, subdir in directories.items():
        folder = root / subdir
        folder.mkdir(parents=True, exist_ok=True)
        env[key] = str(folder)
    (root / "engine").chmod(0o700)
    env.update(CAD_ENGINE_PORT="0", CAD_TELEMETRY="0", DO_NOT_TRACK="1",
               PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    # Parent-session CAD routing must never select another installation/worker.
    for key in ("CAD_INSTALLATION_VERSION", "CAD_ENGINE_WORKER", "CAD_ENGINE_URL"):
        env.pop(key, None)
    return env


def executable(runtime: Path, name: str) -> Path:
    runtime = Path(runtime).resolve()
    if runtime.is_file():
        return runtime if name == "cad" else runtime.parent / name
    return runtime / ("Scripts" if os.name == "nt" else "bin") / name


def run_cad(runtime: Path, state_root: Path, args: list[str], cwd: Path) -> dict:
    command = [str(executable(runtime, "cad")), *map(str, args)]
    started = time.monotonic()
    try:
        process = subprocess.run(command, cwd=Path(cwd).resolve(),
                                 env=environment(state_root), text=True,
                                 capture_output=True, timeout=600, check=False)
        code, stdout, stderr = process.returncode, process.stdout, process.stderr
    except subprocess.TimeoutExpired as error:
        code = 124
        stdout = error.stdout or ""
        stderr = (error.stderr or "")
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        stderr += "\nCAD command timed out; inspect the owned server before retrying."
    except OSError as error:
        code, stdout, stderr = 127, "", str(error)
    return {"command": [sanitize(arg) for arg in command], "returncode": code,
            "stdout": sanitize(stdout), "stderr": sanitize(stderr),
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def runtime_versions(runtime: Path) -> dict:
    requirements_path = Path(__file__).resolve().with_name("requirements.txt")
    requirement_bytes = requirements_path.read_bytes()
    required = {}
    for raw in requirement_bytes.decode("utf-8").splitlines():
        line = raw.partition("#")[0].strip()
        if not line:
            continue
        pin = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)", line)
        if pin is None:
            raise RuntimeError("The runtime requirements must contain exact name==version pins")
        name = re.sub(r"[-_.]+", "-", pin.group(1)).lower()
        if name in required:
            raise RuntimeError(f"Duplicate runtime requirement: {name}")
        required[name] = pin.group(2)
    if not required or any(required.get(name) != CAD_VERSION for name in ("cad", "cad-server")):
        raise RuntimeError(f"Runtime requirements must pin cad and cad-server {CAD_VERSION}")
    code = """import importlib.metadata as m, json, platform, re
packages = {}
for distribution in m.distributions():
    name = re.sub(r'[-_.]+', '-', distribution.metadata['Name']).lower()
    version = distribution.version
    if name in packages and packages[name] != version:
        raise RuntimeError('Conflicting installed distributions: ' + name)
    packages[name] = version
print(json.dumps({'python':platform.python_version(),'platform':platform.platform(),
                 'machine':platform.machine(),'packages':dict(sorted(packages.items()))}))
"""
    result = subprocess.run([str(executable(runtime, "python")), "-I", "-c", code],
                            capture_output=True, text=True, check=False, timeout=30)
    if result.returncode:
        raise RuntimeError("Cannot read CAD runtime versions: " + sanitize(result.stderr))
    versions = json.loads(result.stdout)
    if tuple(int(part) for part in versions["python"].split(".")[:2]) < (3, 14):
        raise RuntimeError("This fixture requires Python 3.14 or newer")
    installed = versions["packages"]
    differences = [f"{name}: expected {version}, found {installed.get(name, 'missing')}"
                   for name, version in required.items() if installed.get(name) != version]
    if differences:
        raise RuntimeError("CAD runtime differs from the pinned requirements: " + "; ".join(differences))
    versions["requirements_sha256"] = hashlib.sha256(requirement_bytes).hexdigest()
    versions["required_package_count"] = len(required)
    return versions


def build_from_source(runtime: Path, state_root: Path, source_dir: Path,
                      output_dir: Path, requirements_path: Path | None = None) -> dict:
    source_dir, output_dir = Path(source_dir).resolve(), Path(output_dir).resolve()
    requirements_path = Path(requirements_path or source_dir.parent / "requirements.json").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = {"status": "unknown", "stages": [], "document_ids": {},
              "measurements_file": str(output_dir / "measurements.json")}
    try:
        result["versions"] = runtime_versions(runtime)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        result["error"] = sanitize(str(error))
        return result

    def stage(name: str, args: list[str]) -> dict:
        record = run_cad(runtime, state_root, args, source_dir)
        record["stage"] = name
        result["stages"].append(record)
        return record

    # A fresh state root has no registered runtime yet. Doctor registers only in
    # the dedicated state/launcher directories selected above.
    if stage("runtime", ["doctor"])["returncode"] != 0:
        result["error"] = "Isolated CAD runtime setup failed"
        return result

    for name, script in (("build", "model.py"), ("export", "exports.py")):
        record = stage(name, ["run", str(source_dir / script), str(output_dir)])
        if record["returncode"] != 0:
            result["error"] = f"CAD {name} failed"
            return result
    docs = output_dir / "documents.json"
    if docs.is_file():
        try:
            identifiers = json.loads(docs.read_text())
            if not isinstance(identifiers, dict):
                raise ValueError("Document IDs must be an object")
            result["document_ids"] = {name: str(uuid.UUID(value)) for name, value in identifiers.items()}
        except (OSError, ValueError, TypeError, AttributeError) as error:
            result["error"] = "Invalid document ID map: " + sanitize(str(error))
            return result

    verifier = Path(__file__).resolve().with_name("verify_geometry.py")
    measured = stage("measure", ["run", str(verifier), str(output_dir / "assembly.step"),
                                 str(requirements_path), "--out", result["measurements_file"],
                                 "--print-dir", str(output_dir)])
    if measured["returncode"] not in (0, 1):
        result["error"] = "Geometry readback was incomplete or failed"
        return result
    try:
        result["measurements"] = json.loads(Path(result["measurements_file"]).read_text())
    except (OSError, ValueError) as error:
        result["error"] = "Missing geometry measurements: " + str(error)
        return result
    drawn = stage("drawing", ["run", str(source_dir / "drawing.py"), str(output_dir),
                               result["measurements_file"], str(requirements_path)])
    if drawn["returncode"] != 0:
        result["error"] = "Drawing generation failed"
        return result
    result["status"] = result["measurements"].get("status", "unknown")
    result["artifacts"] = sorted(p.name for p in output_dir.iterdir() if p.is_file())
    return result
