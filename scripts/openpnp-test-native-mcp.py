#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run packaged MCP workflows against fresh, owned, native OpenPnP simulators.

Each suite installs the packaged bridge and retains its complete evidence. The
mapped-axis suite uses a declared test-only launcher with existing mapped axes;
the other suites launch through the shipped CLI. No user configuration is used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "sensing": ("vacuum-sensing-live.test.mjs", "OPENPNP_SENSING_CONNECTION_FILE", "OPENPNP_SENSING_EVIDENCE_DIR", 300),
    "panel-library": ("panel-library-live.test.mjs", "OPENPNP_PANEL_LIBRARY_CONNECTION_FILE", "OPENPNP_PANEL_LIBRARY_EVIDENCE_DIR", 330),
    "panel-membership": ("panel-membership-live.test.mjs", "OPENPNP_PANEL_MEMBERSHIP_CONNECTION_FILE", "OPENPNP_PANEL_MEMBERSHIP_EVIDENCE_DIR", 360),
    "part-bindings": ("part-bindings-live.test.mjs", "OPENPNP_PART_BINDINGS_CONNECTION_FILE", "OPENPNP_PART_BINDINGS_EVIDENCE_DIR", 300),
    "topology": ("topology-live.test.mjs", "OPENPNP_TOPOLOGY_E2E_CONNECTION_FILE", "OPENPNP_TOPOLOGY_E2E_EVIDENCE_DIR", 450),
    "backlash": ("axis-backlash-live.test.mjs", "OPENPNP_BACKLASH_E2E_CONNECTION_FILE", "OPENPNP_BACKLASH_E2E_EVIDENCE_DIR", 240),
    "material": ("material-load-live.test.mjs", "OPENPNP_MATERIAL_CONNECTION_FILE", "OPENPNP_MATERIAL_EVIDENCE_DIR", 210),
    "core": ("live.test.mjs", "OPENPNP_E2E_CONNECTION_FILE", "OPENPNP_E2E_EVIDENCE_DIR", 660),
    "vision": ("vision-live.test.mjs", "OPENPNP_VISION_E2E_CONNECTION_FILE", "OPENPNP_VISION_E2E_EVIDENCE_DIR", 210),
    "structure": ("placement-structure-live.test.mjs", "OPENPNP_STRUCTURE_E2E_CONNECTION_FILE", "OPENPNP_STRUCTURE_E2E_EVIDENCE_DIR", 330),
    "placement": ("placement-live.test.mjs", "OPENPNP_JOB_EDIT_CONNECTION_FILE", "OPENPNP_JOB_EDIT_EVIDENCE_DIR", 330),
    "recovery": ("reconnect-live.test.mjs", "OPENPNP_E2E_RECOVERY_CONNECTION_FILE", "OPENPNP_E2E_EVIDENCE_DIR", 210),
    "portable": ("portable-live.test.mjs", "OPENPNP_PORTABLE_E2E_CONNECTION_FILE", "OPENPNP_PORTABLE_E2E_EVIDENCE_DIR", 270),
    "camera-scale": ("camera-scale-live.test.mjs", "OPENPNP_CAMERA_SCALE_E2E_CONNECTION_FILE", "OPENPNP_CAMERA_SCALE_E2E_EVIDENCE_DIR", 240),
    "camera": ("camera-settling-live.test.mjs", "OPENPNP_CAMERA_E2E_CONNECTION_FILE", "OPENPNP_CAMERA_E2E_EVIDENCE_DIR", 210),
    "stepping": ("stepping-live.test.mjs", "OPENPNP_STEP_E2E_CONNECTION_FILE", "OPENPNP_STEP_E2E_EVIDENCE_DIR", 210),
    "mapped": ("mapped-axis-live.test.mjs", "OPENPNP_MAPPED_E2E_CONNECTION_FILE", "OPENPNP_MAPPED_E2E_EVIDENCE_DIR", 210),
}
EVIDENCE = {"panel-library": "mcp-native-panel-library.json", "panel-membership": "mcp-native-panel-membership.json", "part-bindings": "mcp-native-part-bindings.json", "camera-scale": "mcp-native-camera-scale.json", "topology": "mcp-native-topology.json", "backlash": "mcp-native-axis-backlash.json", "material": "mcp-native-material-loads.json", "structure": "mcp-native-placement-structure.json", "core": "mcp-native-e2e.json", "vision": "mcp-native-vision.json", "placement": "mcp-placement-loads.json", "recovery": "mcp-native-reconnect.json", "portable": "mcp-native-portable.json", "camera": "mcp-native-camera-settling.json", "stepping": "mcp-native-stepping.json", "mapped": "mcp-native-mapped-axis.json"}
EVIDENCE["sensing"] = "mcp-native-vacuum-sensing.json"
INTERRUPTED = []
CLEANUP_DEPTH = 0


class RunnerInterrupted(Exception):
    pass


def interrupt(signum, _frame):
    INTERRUPTED.append(signum)
    # Preserve the first interrupt and allow bounded owned-child cleanup to finish.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if CLEANUP_DEPTH:
        return
    raise RunnerInterrupted(f"Runner interrupted by signal {signum}; remaining suites were not started.")


@contextmanager
def defer_interrupts():
    global CLEANUP_DEPTH
    CLEANUP_DEPTH += 1
    try:
        yield
    finally:
        CLEANUP_DEPTH -= 1


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(file, value):
    with file.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def clean_environment():
    # Test subprocesses receive only their selected connection and owned state.
    return {k: v for k, v in os.environ.items() if not k.startswith(("OPENPNP_", "DYLD_")) and k not in {
        "NODE_OPTIONS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH",
        "LD_PRELOAD", "LD_LIBRARY_PATH",
    }}


def executable(value):
    found = shutil.which(value)
    if not found:
        raise ValueError(f"Executable is unavailable: {value}")
    return Path(found).resolve(strict=True)


def mapped_class_inventory(directory):
    """Only the explicit test fixture family may precede the installed Bridge."""
    directory = directory.resolve(strict=True)
    result = {}
    for file in directory.rglob("*"):
        if file.is_symlink():
            raise ValueError("Mapped fixture class directory must not contain symbolic links.")
        if file.is_dir():
            continue
        relative = file.relative_to(directory).as_posix()
        if not file.is_file() or not re.fullmatch(r"org/openpnp/codex/NativeMappedAxisFixtureMain(?:\$[A-Za-z0-9_$]+)?\.class", relative):
            raise ValueError("Mapped fixture class directory may contain only the test launcher family.")
        result[file] = digest(file)
    if directory / "org/openpnp/codex/NativeMappedAxisFixtureMain.class" not in result:
        raise ValueError("Mapped fixture main class is missing.")
    return result


def input_inventory(runtime, node, java, mapped_classes=None):
    paths = {Path(__file__).resolve(), ROOT / "scripts/openpnp-read-response.mjs", node, java}
    java_home = java.parent.parent
    paths.update({java_home / "release", java_home / "lib/modules"})
    for directory in [ROOT / "plugins/openpnp", ROOT / "src/openpnp/node", ROOT / "tests/openpnp"]:
        for file in directory.rglob("*"):
            relative = file.relative_to(directory)
            if any(part in {"node_modules", "evidence", "__pycache__"} for part in relative.parts):
                continue
            if file.is_file():
                paths.add(file)
    # Actual loaded runtime and SDK dependencies are pinned as well as manifests.
    dependency_root = ROOT / "src/openpnp/node/node_modules"
    if not dependency_root.is_dir():
        raise ValueError("Install source SDK test dependencies before running the suite.")
    paths.update(file for file in dependency_root.rglob("*") if file.is_file())
    if mapped_classes is not None:
        paths.update(mapped_class_inventory(mapped_classes))
    paths.add(runtime / "codex-build-manifest.json")
    manifest = json.loads((runtime / "codex-build-manifest.json").read_text())
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        file = (runtime / relative).resolve(strict=True)
        if relative.is_absolute() or ".." in relative.parts or not file.is_relative_to(runtime):
            raise ValueError("Native runtime inventory escapes its distribution.")
        if digest(file) != entry["sha256"]:
            raise ValueError(f"Native runtime inventory mismatch: {relative}")
        paths.add(file)
    return {str(file.relative_to(ROOT)) if file.is_relative_to(ROOT) else str(file): digest(file)
            for file in sorted(paths)}


def stop_owned_group(process, timeout=20, disappearance_timeout=5):
    """Signal only a session/process group created by this runner, never a discovered PID."""
    with defer_interrupts():
        return _stop_owned_group(process, timeout, disappearance_timeout)


def _stop_owned_group(process, timeout, disappearance_timeout):
    if process is None:
        return None
    result = {"parent_exit_code": process.poll(), "forced_kill": False, "group_gone": False, "errors": []}
    def send(sig):
        try:
            os.killpg(process.pid, sig)
            return True
        except ProcessLookupError:
            return False
        except OSError as error:
            result["errors"].append(f"signal {sig}: {error}")
            return False
    def exists():
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except OSError as error:
            result["errors"].append(f"group status: {error}")
            return True
    # The CLI handles TERM and waits for its native child. If a test or launcher
    # failed, close the entire owned group so a native or MCP child cannot leak.
    send(signal.SIGTERM)
    try:
        result["parent_exit_code"] = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        result["forced_kill"] = send(signal.SIGKILL)
        try:
            result["parent_exit_code"] = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            result["errors"].append("Parent did not exit after owned group kill.")
    # Parent completion is insufficient when a descendant outlives it.
    if exists():
        result["forced_kill"] = send(signal.SIGKILL) or result["forced_kill"]
    deadline = time.monotonic() + disappearance_timeout
    while exists() and time.monotonic() < deadline:
        process.poll()
        time.sleep(0.05)
    result["group_gone"] = not exists()
    return result


def clean_stop(cleanup):
    return cleanup is not None and cleanup["group_gone"] and not cleanup["forced_kill"] and not cleanup["errors"]


def run_logged(command, cwd, env, logfile, timeout):
    process = None
    with logfile.open("xb") as log:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
            return process.wait(timeout=timeout)
        finally:
            with defer_interrupts():
                cleanup = stop_owned_group(process)
                write_json(logfile.with_suffix(".process.json"), cleanup)
            if process is not None and not clean_stop(cleanup):
                raise RuntimeError("Owned command needed forced cleanup or its process group did not disappear.")
            if INTERRUPTED:
                raise RunnerInterrupted("Runner interruption retained after owned command cleanup.")


def mapped_launch(suite, state, runtime, node, java, env, classes):
    if classes is None:
        raise ValueError("Mapped qualification requires --mapped-test-classes with a compiled test-only fixture family.")
    mapped_class_inventory(classes)
    # Reuse the shipped integrity checks; no arbitrary receipt paths become a
    # classpath before verifiedRuntime binds them to the packaged native build.
    source = "import {pathToFileURL} from 'node:url'; const {verifiedRuntime}=await import(pathToFileURL(process.argv[2])); console.log(JSON.stringify(await verifiedRuntime({stateDir:process.argv[3],openpnpHome:process.argv[4]})));"
    checked = run_logged([str(node), "--input-type=module", "-e", source, "--", "openpnp-runtime-verification-helper",
                          str(ROOT / "plugins/openpnp/scripts/openpnp.mjs"), str(state), str(runtime)],
                         ROOT, env, suite / "runtime-verification.log", 45)
    if checked:
        raise RuntimeError("Installed native runtime verification failed.")
    verified = json.loads((suite / "runtime-verification.log").read_text())
    manifest = json.loads((runtime / "codex-build-manifest.json").read_text())
    launcher = runtime / manifest["gui_launcher_jar"]
    listed = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    if not launcher.resolve().is_relative_to(runtime) or listed.get(manifest["gui_launcher_jar"]) != digest(launcher):
        raise ValueError("Isolated preferences launcher must belong to the verified runtime inventory.")
    classpath = [str(classes), verified["receipt"]["bridge_jar"], str(launcher), verified["jar"], *verified["libraries"]]
    if any(os.pathsep in value for value in classpath):
        raise ValueError("Classpath filenames cannot contain the classpath separator.")
    home, tmp = suite / "fixture-home", suite / "fixture-tmp"
    home.mkdir(mode=0o700); tmp.mkdir(mode=0o700)
    return [str(java), "-Xmx2g", "-XX:+ExitOnOutOfMemoryError", "-Dfile.encoding=UTF-8", "-Djava.awt.headless=true",
            "-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory",
            "-Duser.home=" + str(home), "-Djava.io.tmpdir=" + str(tmp),
            "--add-opens=java.base/java.lang=ALL-UNNAMED", "--add-opens=java.desktop/java.awt=ALL-UNNAMED",
            "--add-opens=java.desktop/java.awt.color=ALL-UNNAMED", "-cp", os.pathsep.join(classpath),
            "org.openpnp.codex.NativeMappedAxisFixtureMain", "--state-dir", str(state), "--sample-root", verified["samples"]]


def run_suite(name, output, runtime, node, java, env, mapped_classes=None):
    suite = output / name
    suite.mkdir(mode=0o700)
    state = suite / "state"
    test_file, connection_env, evidence_env, timeout = SUITES[name]
    cli = str(ROOT / "plugins/openpnp/scripts/openpnp.mjs")
    record = {"suite": name, "started_at": now(), "passed": False,
              "simulator_profile": "vacuum-sensing" if name == "sensing" else "sustained-workload" if name in {"recovery", "material"} else "native-simulator",
              "scope": "Fresh installed native simulator and actual packaged MCP; functional qualification only.",
              "launcher": "test-only existing mapped-axis fixture" if name == "mapped" else "shipped start-simulator CLI",
              "physical_qualification": False}
    launcher = None
    started = time.monotonic()
    try:
        installed = run_logged([str(node), cli, "install-bridge", "--state-dir", str(state)], ROOT, env,
                               suite / "install.log", 60)
        record["install_exit_code"] = installed
        if installed:
            raise RuntimeError("Packaged bridge installation failed; see install.log.")
        launch_command = [str(node), cli, "start-simulator", "--state-dir", str(state),
                          "--openpnp-home", str(runtime), "--java", str(java), "--profile", record["simulator_profile"]]
        if name == "mapped":
            launch_command = mapped_launch(suite, state, runtime, node, java, env, mapped_classes)
        record["launch_command"] = launch_command
        with (suite / "launcher.log").open("xb") as log:
            launcher = subprocess.Popen(launch_command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
            record["owned_launcher_pid"] = launcher.pid
            deadline = time.monotonic() + 75
            while not (state / "connection.json").is_file() or (name == "mapped" and not (state / "mapped-fixture/manifest.json").is_file()):
                if launcher.poll() is not None:
                    raise RuntimeError("Native simulator exited before readiness; see launcher.log.")
                if time.monotonic() >= deadline:
                    raise RuntimeError("Native simulator did not publish its verified connection in time.")
                time.sleep(0.1)
            # A fresh directory makes a stale connection impossible. The CLI doctor
            # independently checks protocol, runtime provenance and installation.
            doctor = run_logged([str(node), cli, "doctor", "--state-dir", str(state)], ROOT, env,
                                suite / "doctor.log", 30)
            doctor_result = json.loads((suite / "doctor.log").read_text())
            if doctor or doctor_result.get("ok") is not True:
                raise RuntimeError("Native simulator doctor failed; see doctor.log.")
            bridge = next(row["capabilities"] for row in doctor_result["checks"] if row["check"] == "bridge")
            if bridge.get("simulation") is not True or bridge.get("hardware_qualified") is not False:
                raise RuntimeError("This runner requires the unqualified isolated native simulator profile.")
            record["bridge_capabilities"] = bridge
            test_env = dict(env, **{connection_env: str(state / "connection.json"), evidence_env: str(suite / "evidence")})
            if name == "placement":
                test_env["OPENPNP_JOB_EDIT_MCP_SERVER"] = str(ROOT / "plugins/openpnp/mcp/server.mjs")
            if name == "topology":
                test_env["OPENPNP_TOPOLOGY_E2E_RUNTIME"] = str(runtime)
                test_env["OPENPNP_TOPOLOGY_E2E_JAVA"] = str(java)
            if name in ("portable", "panel-library"):
                test_env["OPENPNP_PORTABLE_E2E_RUNTIME"] = str(runtime)
                test_env["OPENPNP_PORTABLE_E2E_JAVA"] = str(java)
            if name == "mapped":
                test_env["OPENPNP_MAPPED_E2E_FIXTURE_DIR"] = str(state / "mapped-fixture")
            command = [str(node), "--test", "--test-reporter=tap", str(ROOT / "tests/openpnp" / test_file)]
            record["test_command"] = command
            record["test_exit_code"] = run_logged(command, ROOT, test_env, suite / "test.log", timeout)
            if record["test_exit_code"]:
                raise RuntimeError("Actual MCP suite failed; no mutation was replayed. See test.log.")
            tap = (suite / "test.log").read_text()
            counts = {key: int(value) for key, value in re.findall(r"^# (tests|pass|fail|skipped|todo) (\d+)$", tap, re.M)}
            record["test_counts"] = counts
            if counts.get("pass", 0) < 1 or any(counts.get(key) != 0 for key in ["fail", "skipped", "todo"]):
                raise RuntimeError("Live qualification requires passing assertions with no skipped or pending tests.")
            evidence_file = suite / "evidence" / EVIDENCE[name]
            native_evidence = json.loads(evidence_file.read_text())
            if native_evidence.get("passed") is not True:
                raise RuntimeError("Native MCP suite did not retain successful completion evidence.")
            if launcher.poll() is not None:
                raise RuntimeError("Simulator exited during the MCP suite.")
            record["passed"] = True
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        with defer_interrupts():
            record["launcher_cleanup"] = stop_owned_group(launcher)
            expected_exits = {0, 128 + signal.SIGTERM, -signal.SIGTERM} if name == "mapped" else {0}
            if record["passed"] and (INTERRUPTED or not clean_stop(record["launcher_cleanup"]) or record["launcher_cleanup"]["parent_exit_code"] not in expected_exits):
                record["passed"] = False
                record["error"] = "Native launcher did not complete an uninterrupted, intentional shutdown successfully."
            record["interrupted_signals"] = list(INTERRUPTED)
            record["completed_at"] = now()
            record["duration_seconds"] = time.monotonic() - started
            record["log_sha256"] = {file.name: digest(file) for file in suite.glob("*.log")}
            record["process_cleanup"] = {file.name: json.loads(file.read_text()) for file in suite.glob("*.process.json")}
            record["evidence_sha256"] = {file.name: digest(file) for file in (suite / "evidence").glob("*.json")}
            write_json(suite / "report.json", record)
    print(json.dumps({k: record[k] for k in ["suite", "passed", "duration_seconds"]}), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path, help="Verified native runtime directory from the native builder")
    parser.add_argument("--output", type=Path, help="New evidence directory within this checkout")
    parser.add_argument("--node", default="node")
    parser.add_argument("--java", default="java")
    parser.add_argument("--suite", choices=["all", *SUITES], default="all")
    parser.add_argument("--mapped-test-classes", type=Path, help="Explicit directory containing only the compiled mapped-axis test launcher family")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    if os.name != "posix":
        parser.error("This owned-process-group runner currently supports macOS and Linux.")
    node, java = executable(args.node), executable(args.java)
    runtime = args.runtime.resolve(strict=True)
    mapped_classes = args.mapped_test_classes.resolve(strict=True) if args.mapped_test_classes else None
    if (args.suite == "mapped" or (args.suite == "all" and "mapped" in SUITES)) and mapped_classes is None:
        parser.error("--mapped-test-classes is required when running the mapped fixture suite.")
    output = (args.output or ROOT / "validation" / ("native-mcp-" + uuid.uuid4().hex)).resolve()
    if not output.is_relative_to(ROOT) or output == ROOT:
        parser.error("--output must be a new directory inside this checkout.")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {"started_at": now(), "passed": False, "runtime": str(runtime), "suites": [],
              "physical_qualification": False, "performance_benchmark": False}
    try:
        before = input_inventory(runtime, node, java, mapped_classes)
        write_json(output / "inputs-before.json", before)
        for name in SUITES if args.suite == "all" else [args.suite]:
            report["suites"].append(run_suite(name, output, runtime, node, java, clean_environment(), mapped_classes))
            if INTERRUPTED:
                break
        after = input_inventory(runtime, node, java, mapped_classes)
        write_json(output / "inputs-after.json", after)
        report["inputs_unchanged"] = before == after
        report["input_count"] = len(before)
        report["passed"] = not INTERRUPTED and before == after and all(row["passed"] for row in report["suites"])
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        with defer_interrupts():
            if INTERRUPTED:
                report["passed"] = False
            report["interrupted_signals"] = list(INTERRUPTED)
            # All owned children have stopped at this terminal publication point.
            report["completed_at"] = now()
            write_json(output / "report.json", report)
    print(json.dumps({"passed": report["passed"], "report": str(output / "report.json")}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
