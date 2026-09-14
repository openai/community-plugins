#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Qualify actual JVM halt at native ledger publication seams in fresh owned state.

Before means before target append begins; after means after write + force(true).
The native processor/ledger and recovering Bridge are real. Initial operation
admission is a fixture; reset simulator counters never establish recovered stock.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PIN = "5bd404cfc70f34103a3ca0fbb6b50c2b465f407c"
CASES = [f"{kind}-{event}-{side}" for kind in ("feed", "pick", "release")
         for event in ("intent", "outcome") for side in ("before", "after")]
CASES += ["checkpoint-before", "checkpoint-after"]
INTERRUPTED = []
CLEANUP_DEPTH = 0


class RunnerInterrupted(Exception):
    pass


def interrupt(signum, _frame):
    INTERRUPTED.append(signum)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if not CLEANUP_DEPTH:
        raise RunnerInterrupted(f"Runner interrupted by signal {signum}")


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


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def no_symlinks(path):
    path = Path(os.path.abspath(path))
    for ancestor in (path, *path.parents):
        require(not ancestor.is_symlink(), f"Symbolic path component refused: {ancestor}")
    return path


def regular(path, maximum=1024 * 1024 * 1024):
    path = no_symlinks(path)
    require(path.is_file() and path.stat().st_size <= maximum, f"Expected bounded regular input: {path}")
    return path


def read_json(path, maximum=2 * 1024 * 1024):
    return json.loads(regular(path, maximum).read_text(encoding="utf-8"))


def relative(value):
    require(isinstance(value, str) and 0 < len(value) <= 512 and "\\" not in value,
            "Invalid inventory path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(x not in ("", ".", "..") for x in value.split("/"))
            and str(path) == value, "Inventory path is not a canonical relative path")
    return Path(value)


def clean_environment():
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("OPENPNP_", "DYLD_", "LD_")) and key not in {
                "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH",
                "JDK_JAVAC_OPTIONS", "JAVA_OPTS", "LIBPATH", "SHLIB_PATH",
            }}


def reserve_output(value, build):
    output = no_symlinks(value)
    allowed = no_symlinks(ROOT / "validation")
    require(output.is_relative_to(allowed) and output != allowed,
            "Output must be a new directory below this checkout's validation directory")
    require(not output.is_relative_to(build) and not build.is_relative_to(output),
            "Output and immutable input build must not overlap")
    require(output.parent.is_dir(), "Output parent must already exist")
    output.mkdir(mode=0o700)  # Atomic reservation: no overwrite after an existence race.
    return output


def manifest_classpath(jar, runtime, inventory):
    references = []
    with zipfile.ZipFile(jar) as archive:
        info = archive.getinfo("META-INF/MANIFEST.MF")
        require(info.file_size <= 65536, "JAR manifest is too large")
        text = archive.read(info).decode("utf-8").replace("\r\n", "\n")
    unfolded = text.replace("\n ", "")
    for line in unfolded.splitlines():
        if line.lower().startswith("class-path:"):
            for entry in line.split(":", 1)[1].split():
                require(not any(char in entry for char in (":", "%", "?", "#")),
                        "Remote or encoded JAR manifest Class-Path is refused")
                child = Path(".") if entry == "." else relative(entry)
                path = no_symlinks(jar.parent / child)
                require(path.is_relative_to(runtime), "JAR manifest Class-Path escapes runtime")
                name = path.relative_to(runtime).as_posix()
                if path.is_dir():
                    references.append({"jar": jar.name, "entry": entry, "kind": "owned-directory-with-exact-runtime-inventory"})
                elif name in inventory:
                    references.append({"jar": jar.name, "entry": entry, "kind": "inventoried-file"})
                else:
                    require(not path.exists(), "Uninventoried existing JAR manifest Class-Path target")
                    references.append({"jar": jar.name, "entry": entry, "kind": "absent-optional-file"})
    return references


def verify_inputs(build, java_home):
    build = no_symlinks(build)
    require(build.is_dir() and build.is_relative_to(ROOT), "Build must be inside this checkout")
    runtime = no_symlinks(build / "runtime")
    bm_path, rm_path = build / "build-manifest.json", runtime / "codex-build-manifest.json"
    bm, rm = read_json(bm_path), read_json(rm_path)
    require(bm.get("upstream_commit") == rm.get("upstream_commit") == PIN, "Upstream pin mismatch")
    require(bm.get("runtime_manifest_sha256") == digest(rm_path), "Runtime manifest digest mismatch")
    files = rm.get("files")
    require(isinstance(files, list) and 0 < len(files) <= 512, "Invalid runtime inventory size")
    inventory = {}
    paths = {regular(bm_path), regular(rm_path)}
    for entry in files:
        require(isinstance(entry, dict) and set(entry) == {"path", "sha256"}, "Invalid runtime entry")
        name = relative(entry["path"]).as_posix()
        require(name not in inventory and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]),
                "Duplicate or invalid runtime digest")
        file = regular(runtime / name)
        require(digest(file) == entry["sha256"], f"Runtime file digest mismatch: {name}")
        inventory[name] = entry["sha256"]
        paths.add(file)
    native_name = relative(rm.get("gui_jar")).as_posix()
    require(native_name in inventory, "Native JAR missing from inventory")
    native = runtime / native_name
    require(digest(native) == bm.get("patched_native_jar_sha256"), "Patched native JAR mismatch")
    require(rm.get("native_action_observer", {}).get("api_version") == 1,
            "Crash qualification requires the patched production native observer")
    lib_dir = relative(rm.get("libs_directory")).as_posix() + "/"
    libraries = [runtime / name for name in sorted(inventory) if name.startswith(lib_dir) and name.endswith(".jar")]
    require(libraries, "No inventoried native libraries")
    classpath_references = []
    for jar in (native, *libraries):
        classpath_references.extend(manifest_classpath(jar, runtime, inventory))
    expected_runtime_files = set(inventory) | {"codex-build-manifest.json"}
    require(runtime_files(runtime) == expected_runtime_files, "Runtime contains unlisted or missing files")
    samples = no_symlinks(runtime / relative(rm.get("samples_directory")))
    require(samples.is_dir() and any((runtime / name).is_relative_to(samples) for name in inventory),
            "Native sample directory missing from inventory")
    bridge = regular(build / "openpnp-codex-bridge.jar")
    require(digest(bridge) == bm.get("bridge_sha256"), "Bridge digest mismatch")
    paths.add(bridge)
    sources = bm.get("production_source_sha256")
    require(isinstance(sources, dict) and 0 < len(sources) <= 128, "Missing production source inventory")
    for name, expected in sources.items():
        file = regular(ROOT / relative(name), 2 * 1024 * 1024)
        require(file.is_relative_to(ROOT / "src/openpnp/java") and name.endswith(".java"),
                "Production source inventory is outside its native source scope")
        require(digest(file) == expected, f"Current native source differs from selected build: {name}")
        paths.add(file)
    java_home = no_symlinks(java_home)
    java, javac = regular(java_home / "bin/java"), regular(java_home / "bin/javac")
    require(os.access(java, os.X_OK) and os.access(javac, os.X_OK), "JDK executables unavailable")
    paths.update((java, javac, regular(java_home / "release"), regular(java_home / "lib/modules")))
    source = regular(ROOT / "tests/openpnp/native/NativeJvmHaltLedgerTest.java", 2 * 1024 * 1024)
    paths.update((source, regular(Path(__file__))))
    return {"build": build, "runtime": runtime, "bridge": bridge, "native": native,
            "libraries": libraries, "samples": samples, "java": java, "javac": javac,
            "source": source, "sources": sources, "build_manifest": bm,
            "paths": paths, "runtime_files": expected_runtime_files, "manifest_classpath": classpath_references, "digests": {str(path): digest(path) for path in sorted(paths)}}


def runtime_files(runtime):
    files = set()
    for path in runtime.rglob("*"):
        require(not path.is_symlink(), "Symbolic runtime entry refused")
        if path.is_file():
            files.add(path.relative_to(runtime).as_posix())
    return files


def unchanged(inputs):
    try:
        return (runtime_files(inputs["runtime"]) == inputs["runtime_files"] and
                all(digest(regular(Path(name))) == expected for name, expected in inputs["digests"].items()))
    except (OSError, ValueError):
        return False


def write_receipt(path, value):
    data = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    temp = path.with_name(".receipt-" + uuid.uuid4().hex)
    with temp.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def stop_owned_group(process, grace=3):
    """Signal only the session created by this runner's own Popen call."""
    with defer_interrupts():
        result = {"parent_exit_code": process.poll(), "forced_kill": False, "group_gone": False}
        def exists():
            try:
                os.killpg(process.pid, 0)
                return True
            except ProcessLookupError:
                return False
        def send(sig):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
        if exists():
            send(signal.SIGTERM)
        try:
            result["parent_exit_code"] = process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            send(signal.SIGKILL)
            result["forced_kill"] = True
            result["parent_exit_code"] = process.wait(timeout=5)
        if exists():
            send(signal.SIGKILL)
            result["forced_kill"] = True
        deadline = time.monotonic() + 5
        while exists() and time.monotonic() < deadline:
            process.poll()
            time.sleep(0.05)
        result["group_gone"] = not exists()
        return result


def run_logged(command, env, logfile, timeout):
    process = None
    cleanup = None
    started = time.monotonic()
    result = {"exit_code": None, "timeout": False, "interrupted": False, "command": command}
    try:
        with logfile.open("xb") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
            try:
                result["exit_code"] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result["timeout"] = True
            except RunnerInterrupted:
                result["interrupted"] = True
                raise
    finally:
        if process is not None:
            cleanup = stop_owned_group(process)
        result.update({"cleanup": cleanup, "elapsed_seconds": time.monotonic() - started,
                       "log_sha256": digest(logfile) if logfile.exists() else None})
        write_receipt(logfile.with_suffix(".process.json"), result)
    require(cleanup is not None and cleanup["group_gone"] and not cleanup["forced_kill"],
            "Owned child required forced cleanup or its group remains")
    require(not result["timeout"] and not INTERRUPTED, "Owned child timed out or runner interrupted")
    return result


def verify_case(case, directory):
    observed, one, two = [read_json(directory / name) for name in
                          ("halt-observation.json", "recover1.json", "recover2.json")]
    checkpoint = case.startswith("checkpoint-")
    kind, event, side = case.split("-") if not checkpoint else ("checkpoint", "checkpoint", case.split("-")[1])
    feeds = 0 if kind == "feed" and event == "intent" else 1
    held = "R0603-1K" if (kind == "pick" and event == "outcome") or (kind == "release" and event == "intent") else None
    placed = 1 if checkpoint else 0
    require((observed["native_feed_count"], observed["native_total_feed_count"], observed["native_nozzle_part"],
             observed["native_placed_count"]) == (feeds, feeds, held, placed), "Unexpected native state at halt seam")
    pending = 1 if not checkpoint and ((event == "intent" and side == "after") or
                                      (event == "outcome" and side == "before")) else 0
    require(len(one["native_action_recovery"]["unresolved_actions"]) == pending, "Unresolved action oracle mismatch")
    require(one["native_action_recovery"] == two["native_action_recovery"], "Second JVM changed native action facts")
    require(two["native_complete_checkpoints"] == (1 if case == "checkpoint-after" else 0),
            "Completed checkpoint oracle mismatch")
    for recovery in (one, two):
        require(recovery["operation_state"] == "outcome_unknown" and not recovery["automatic_replay"],
                "Interrupted operation lost uncertainty")
        require(all(recovery[key] == 0 for key in ("recovery_native_feed_events", "recovery_nozzle_part_events",
                                                   "recovery_native_machine_events")), "Native recovery effect observed")
    return {"passed": True, "assertions": one["assertions"] + two["assertions"],
            "native_feed_count_at_halt": feeds, "native_placed_count_at_halt": placed,
            "native_nozzle_part_at_halt": held, "unresolved_actions_expected": pending,
            "durable_complete_checkpoints": two["native_complete_checkpoints"], "recovery_native_feeds": 0,
            "evidence_sha256": {name: digest(directory / name) for name in
                                ("fixture.json", "halt-observation.json", "recover1.json", "recover2.json",
                                 "journal/operations.jsonl")}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True, help="Immutable canonical native build below this checkout")
    parser.add_argument("--java-home", type=Path, default=os.environ.get("JAVA_HOME"), help="JDK17 home; defaults to JAVA_HOME")
    parser.add_argument("--output", type=Path, help="New directory below validation; never reused")
    parser.add_argument("--case", action="append", choices=CASES, help="Optional diagnostic subset; full qualification requires all14")
    args = parser.parse_args(argv)
    require(os.name == "posix", "This crash runner is qualified on POSIX only")
    require(args.java_home is not None, "Supply --java-home or JAVA_HOME")
    cases = args.case or CASES
    require(len(cases) == len(set(cases)), "Duplicate case selection refused")
    inputs = verify_inputs(Path(os.path.abspath(args.build)), Path(os.path.abspath(args.java_home)))
    validation = ROOT / "validation"
    if not validation.exists():
        validation.mkdir(mode=0o700)
    output = reserve_output(args.output or validation / ("native-crash-" + uuid.uuid4().hex), inputs["build"])
    report = {"schema_version": 1, "started_at": now(), "passed": False, "qualification_complete": False,
              "runtime_halt": True, "host_power_loss_test": False, "full_bridge_admission_test": False,
              "mcp_execution": False, "physical_qualification": False, "case_count_expected": len(cases),
              "upstream_commit": PIN, "bridge_sha256": digest(inputs["bridge"]),
              "native_jar_sha256": digest(inputs["native"]),
              "runtime_manifest_sha256": digest(inputs["runtime"] / "codex-build-manifest.json"),
              "harness_sha256": digest(inputs["source"]), "runner_sha256": digest(Path(__file__)),
              "input_sha256": inputs["digests"], "manifest_classpath": inputs["manifest_classpath"], "exact_runtime_inventory": True, "tests": [], "limits": {"child_seconds": 90, "total_seconds": 900,
              "heap_bytes": 2147483648}, "boundary": {"before": "before target append begins",
              "after": "after complete target write and force(true) return"}}
    started = time.monotonic()
    deadline = started + 900
    env = clean_environment()
    env["JAVA_HOME"] = str(inputs["java"].parent.parent)
    def save():
        report["inputs_unchanged"] = unchanged(inputs)
        write_receipt(output / "report.json", report)
    def execute(command, log, maximum):
        remaining = deadline - time.monotonic()
        require(remaining > 0, "Overall crash qualification deadline exceeded")
        result = run_logged(command, env, log, min(maximum, remaining))
        save()
        return result
    try:
        save()
        for name in ("classes", "source", "cases"):
            (output / name).mkdir()
        shutil.copy2(inputs["source"], output / "source" / inputs["source"].name)
        shutil.copy2(__file__, output / "source" / Path(__file__).name)
        for name in inputs["sources"]:
            target = output / "source" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        cp = os.pathsep.join(map(str, (inputs["bridge"], inputs["native"], *inputs["libraries"])))
        compile_result = execute([str(inputs["javac"]), "--release", "11", "-cp", cp, "-d", str(output / "classes"),
                                  str(output / "source" / inputs["source"].name)], output / "compile.log", 60)
        require(compile_result["exit_code"] == 0, "Crash harness compilation failed")
        version = execute([str(inputs["java"]), "-version"], output / "java-version.log", 10)
        require(version["exit_code"] == 0, "JDK version probe failed")
        report["jvm"] = (output / "java-version.log").read_text()
        base = [str(inputs["java"]), "-Xmx2g", "-XX:+ExitOnOutOfMemoryError", "-Djava.awt.headless=true",
                "-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory",
                "--add-opens=java.base/java.lang=ALL-UNNAMED", "--add-opens=java.desktop/java.awt=ALL-UNNAMED",
                "--add-opens=java.desktop/java.awt.color=ALL-UNNAMED"]
        for case in cases:
            directory = output / "cases" / case
            directory.mkdir(mode=0o700)
            (directory / "tmp").mkdir()
            entry = {"case": case, "passed": False, "phases": []}
            report["tests"].append(entry)
            for phase in ("crash", "recover1", "recover2"):
                require(unchanged(inputs), "Immutable inputs changed before child dispatch")
                command = base[:1] + ["-Duser.home=" + str(directory), "-Djava.io.tmpdir=" + str(directory / "tmp")]
                command += base[1:] + ["-cp", str(output / "classes") + os.pathsep + cp,
                    "org.openpnp.codex.NativeJvmHaltLedgerTest", phase, case, str(directory), str(inputs["samples"])]
                result = execute(command, directory / (phase + ".log"), 90)
                result.update({"phase": phase, "expected_exit_code": 73 if phase == "crash" else 0})
                entry["phases"].append(result)
                save()
                require(result["exit_code"] == result["expected_exit_code"], "Unexpected native child exit")
            entry.update(verify_case(case, directory))
            save()
            print(json.dumps({"case": case, "passed": True, "assertions": entry["assertions"]}), flush=True)
        report.update({"passed": True, "qualification_complete": set(cases) == set(CASES),
                       "actual_halts": len(cases), "recovery_jvms": 2 * len(cases),
                       "assertions": sum(test["assertions"] for test in report["tests"]),
                       "fixture_limits": ["Fixture operation admission and logical board-load scope",
                       "Production native processor, action ledger and recovering Bridge",
                       "Saved pre-run counters are not authoritative crash-time material recovery",
                       "No physical inspection, power-loss, torn-record or written-but-unforced qualification",
                       "No resume, abandonment, cleanup or replay after crash"]})
    except (Exception, KeyboardInterrupt) as failure:
        report["failure"] = {"type": type(failure).__name__, "message": str(failure)[:2000]}
        report["passed"] = False
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        report["interrupt_signals"] = INTERRUPTED[:]
        save()
        if not report["inputs_unchanged"] or INTERRUPTED:
            report["passed"] = report["qualification_complete"] = False
            save()
    print(json.dumps({"output": str(output), "passed": report["passed"], "qualification_complete": report["qualification_complete"],
                      "assertions": report.get("assertions"), "inputs_unchanged": report["inputs_unchanged"]}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as failure:
        print(json.dumps({"passed": False, "preflight_failure": str(failure)[:2000]}), file=sys.stderr)
        raise SystemExit(1)
