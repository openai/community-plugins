#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI-only fresh package staging and offline checks of this build's controller journals.

No historical local fixture paths, machine launch, publication, or implicit retries.
The workflow separately invokes the canonical native, MCP and controller runners.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PIN = "5bd404cfc70f34103a3ca0fbb6b50c2b465f407c"
MODES = ("connect-intent-force", "identify-before-ack", "succeeded-force")
MAPPED = re.compile(r"NativeMappedAxisFixtureMain(?:\$[A-Za-z0-9_$]+)?\.class")
AUTHORITY = ("native_authority_restored", "native_machine_opened", "controller_connection_opened",
             "journal_appended", "physical_qualification")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def read(file):
    require(file.is_file() and not file.is_symlink(), f"Expected regular input: {file}")
    return json.loads(file.read_text())


def save(file, value):
    with file.open("x") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")


def bounded(root, relative):
    path = Path(relative)
    require(not path.is_absolute() and ".." not in path.parts and path.parts, "Invalid relative input")
    file = root / path
    require(not any((root / Path(*path.parts[:i])).is_symlink() for i in range(1, len(path.parts) + 1)),
            "Symbolic input refused")
    require(file.resolve(strict=True).is_relative_to(root.resolve(strict=True)), "Input escaped root")
    require(file.is_file(), "Input is not a regular file")
    return file


def build_inputs(root, build):
    bm = read(build / "build-manifest.json")
    require(bm["upstream_commit"] == PIN, "Unqualified upstream commit")
    require(sha(build / "openpnp-codex-bridge.jar") == bm["bridge_sha256"], "Bridge hash mismatch")
    runtime = build / "runtime"
    require(sha(runtime / "codex-build-manifest.json") == bm["runtime_manifest_sha256"], "Runtime binding mismatch")
    rm = read(runtime / "codex-build-manifest.json")
    require(rm["upstream_commit"] == PIN, "Runtime upstream mismatch")
    rows = rm["files"]
    require(rows and len({row["path"] for row in rows}) == len(rows), "Empty or duplicate runtime inventory")
    for row in rows:
        require(sha(bounded(runtime, row["path"])) == row["sha256"], "Runtime file mismatch")
    require(sha(bounded(runtime, rm["gui_jar"])) == bm["patched_native_jar_sha256"], "Native JAR mismatch")
    for key, prefix in (("production_source_sha256", "src/openpnp/java/"),
                        ("test_source_sha256", "tests/openpnp/native/")):
        require(bm[key], "Empty source inventory")
        for name, digest in bm[key].items():
            require(name.startswith(prefix), "Unexpected source inventory path")
            require(sha(bounded(root, name)) == digest, "Build/source mismatch: " + name)
    return bm


def stage(root, build, destination):
    require(not destination.exists() and destination.is_relative_to(root / "validation"),
            "Use a fresh CI staging directory under validation")
    bm = build_inputs(root, build)  # Fail before creating any stage on mismatched inputs.
    paths = [root / "package.json"]
    for name in ("plugins/openpnp", "src/openpnp", "tests/openpnp", "fixtures/openpnp"):
        for file in (root / name).rglob("*"):
            relative = file.relative_to(root / name)
            if any(part in {"node_modules", "__pycache__", "evidence"} for part in relative.parts):
                continue
            require(not file.is_symlink(), "Source tree must not contain symbolic links")
            if file.is_file():
                paths.append(file)
    paths.extend(p for p in (root / "scripts").glob("*openpnp*") if p.is_file())
    destination.mkdir(parents=True)
    copied = {}
    for source in sorted(set(paths)):
        relative = source.relative_to(root)
        bounded(root, relative)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied[str(relative)] = sha(source)
    for name in ("openpnp-codex-bridge.jar", "build-manifest.json"):
        shutil.copyfile(build / name, destination / "plugins/openpnp/bridge" / name)
    metadata = destination / "plugins/openpnp/assets/evaluations/job-edit-tool-contracts.json"
    document = read(metadata)
    # Replace only the CURRENT artifact block. Keep dated evaluations byte-for-value;
    # none of their success claims are rebound to this newly built CI package.
    document["candidate_packaged_mcp"] = {
        "native_bridge_sha256": bm["bridge_sha256"],
        "native_runtime_manifest_sha256": bm["runtime_manifest_sha256"],
        "qualification_status": "CI staging only; qualification has not run",
        "historical_receipts_apply_to_this_build": False,
    }
    metadata.write_text(json.dumps(document, indent=2) + "\n")
    classes = build / "test-classes/org/openpnp/codex"
    family = sorted(p for p in classes.glob("NativeMappedAxisFixtureMain*.class") if MAPPED.fullmatch(p.name))
    require(any(p.name == "NativeMappedAxisFixtureMain.class" for p in family), "Mapped fixture main missing")
    mapped = destination / "validation/mapped-classes/org/openpnp/codex"
    mapped.mkdir(parents=True)
    for file in family:
        require(not file.is_symlink(), "Symbolic fixture class refused")
        shutil.copyfile(file, mapped / file.name)
    receipt = {"kind": "fresh-ci-package-stage", "qualification_performed": False,
               "bridge_sha256": bm["bridge_sha256"], "runtime_manifest_sha256": bm["runtime_manifest_sha256"],
               "copied_source_sha256": copied, "mapped_class_sha256": {p.name: sha(p) for p in family}}
    save(destination / "validation/stage.json", receipt)
    return receipt


def history_cases(root, native, controller, bm):
    receipt = read(native / "receipt.json")
    require(receipt["bridge_sha256"] == bm["bridge_sha256"] and
            receipt["runtime_manifest_sha256"] == bm["runtime_manifest_sha256"], "Native receipt artifact mismatch")
    entries = [r for r in receipt["tests"] if r["main"] == "NativeControllerCrashRecoveryTest"]
    require(len(entries) == 1 and entries[0]["exit_code"] == 0, "This build's controller halt test must pass")
    require(entries[0]["source_sha256"] == sha(root / "tests/openpnp/native/NativeControllerCrashRecoveryTest.java"),
            "Crash fixture source mismatch")
    require(entries[0]["log_sha256"] == sha(native / "NativeControllerCrashRecoveryTest.log"), "Crash log mismatch")
    # The canonical runner reserves a separate temporary directory for each main.
    # Do not inspect an old shared tmp directory or select another test's history.
    temporary = native / "NativeControllerCrashRecoveryTest-tmp"
    require(temporary.is_dir() and not temporary.is_symlink(), "Expected canonical controller test temporary directory")
    candidates = list(temporary.glob("openpnp-controller-crash-*"))
    require(len(candidates) == 1 and candidates[0].is_dir() and not candidates[0].is_symlink(),
            "Expected exactly one fresh controller crash fixture; no latest-directory fallback")
    crash = candidates[0]
    proof = read(crash / "report.json")
    require(proof["passed"] is True and proof["suite"] == "NativeControllerCrashRecoveryTest", "Crash proof failed")
    require([r["mode"] for r in proof["cases"]] == list(MODES), "Missing or duplicate crash modes")
    control = read(controller / "report.json")
    require(control["passed"] is True and control["bridge_sha256"] == bm["bridge_sha256"] and
            control["mcp_sha256"] == sha(root / "plugins/openpnp/mcp/server.mjs"), "Controller package mismatch/failure")
    require(control["launcher_exit"] == {"code": 0, "signal": None} and control["forced_cleanup"] is False,
            "Controller launcher did not close cleanly")
    cases = [{"name": "controller-success", "file": controller / "native-state/journal/operations.jsonl",
              "state": "succeeded", "operation_id": control["operation"]["operation_id"],
              "request_id": control["request"]["request_id"], "controller_instance_id": control["request"]["controller_instance_id"]}]
    for row in proof["cases"]:
        mode = row["mode"]
        original = row["crash_proof"]
        require(row["passed"] is True and original["bridge_artifact_sha256"] == bm["bridge_sha256"], "Crash Bridge mismatch")
        require(len(row["recoveries"]) == 2, "Expected two fresh recoveries")
        for recovery in row["recoveries"]:
            observed = recovery["observation"]
            require(observed["passed"] is True and observed["bridge_artifact_sha256"] == bm["bridge_sha256"] and
                    observed["controller_connections"] == 0 and observed["controller_bytes"] == 0, "Invalid recovery proof")
        for suffix, relative, state, digest in (
            ("producer", "producer", "succeeded" if mode == "succeeded-force" else "running", original["journal_sha256"]),
            ("recovered", "recovery-state", "succeeded" if mode == "succeeded-force" else "outcome_unknown",
             row["recoveries"][-1]["observation"]["journal_after_sha256"]),
        ):
            file = bounded(crash, mode + "/" + relative + "/journal/operations.jsonl")
            require(sha(file) == digest, "Closed native journal differs from its retained proof")
            cases.append({"name": mode + "-" + suffix, "file": file, "state": state,
                          **{key: original["original_" + key] for key in ("operation_id", "request_id", "controller_instance_id")}})
    return cases


def check_history(result, case, bm):
    require(result["schema_version"] == 1 and result["mode"] == "offline-recorded-controller-history", "Wrong inspector mode")
    require(all(result[key] is False for key in AUTHORITY), "Inspector claimed native authority")
    require(result["valid"] is True and result["input_unchanged"] is True and
            result["shared_read_lock_held_during_scan"] is True, "Invalid or mutable history input")
    require(result["journal_sha256"] == sha(case["file"]) and result["journal_bytes"] == case["file"].stat().st_size,
            "Inspector journal identity mismatch")
    require(result["provenance"]["bridge_sha256"] == bm["bridge_sha256"] and
            result["cli_provenance"]["runtime_manifest_sha256"] == bm["runtime_manifest_sha256"], "Inspector artifact mismatch")
    require(result["inspector_process"]["reaped"] is True, "Inspector process was not reaped")
    require(result["recorded_bytes_prove_force_or_power_loss_survival"] is False, "Unsupported durability claim")
    history = result["history"]
    for key in ("operation_id", "request_id", "controller_instance_id"):
        require(history[key] == case[key], "Wrong recorded " + key)
    success = case["state"] == "succeeded"
    require(history["recorded_operation_state"] == case["state"] and
            history["offline_disposition"] == ("recorded-success" if success else "outcome-unknown") and
            history["requires_reconciliation"] is (not success) and
            history["complete_recipe_recorded"] is success and history["wrapper_success_recorded"] is success,
            "Unsafe or incorrect recorded disposition")


def mutations(original):
    require(original.endswith(b"\n"), "Original journal lacks complete framing")
    rows = [json.loads(line) for line in original.splitlines()]
    duplicate = re.sub(rb'"sequence"\s*:\s*1(?=[,}])', b'"sequence":1,"sequence":1', original, count=1)
    require(duplicate != original, "Duplicate-key fixture was not constructed")
    terminals = [r for r in rows if r["type"] == "operation" and r["payload"]["state"] == "succeeded"]
    require(len(terminals) == 1, "Expected one actual successful terminal")
    terminals[0]["payload"]["native_completion"]["native_wrapper_completed"] = False
    return {"partial-tail": original[:-1], "duplicate-key": duplicate,
            "contradictory-wrapper": ("\n".join(json.dumps(r) for r in rows) + "\n").encode()}


def invoke(node, cli, arguments, output, name):
    # The shipped inspector owns/reaps its bounded JVM; the outer limit is longer.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENPNP_", "LD_", "DYLD_")) and
           k not in {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH"}}
    try:
        run = subprocess.run([str(node), str(cli), *map(str, arguments)], capture_output=True, timeout=45, env=env)
    except subprocess.TimeoutExpired as error:
        (output / (name + ".stdout.log")).write_bytes(error.stdout or b"")
        (output / (name + ".stderr.log")).write_bytes(error.stderr or b"")
        raise
    (output / (name + ".stdout.log")).write_bytes(run.stdout)
    (output / (name + ".stderr.log")).write_bytes(run.stderr)
    require(len(run.stdout) <= 2 * 1024 * 1024 and not run.stderr, "Unexpected inspector output")
    return run.returncode, json.loads(run.stdout)


def history(root, build, native, controller, output, node, java):
    require(not output.exists() and output.is_relative_to(root / "validation"), "Choose fresh history output")
    bm = build_inputs(root, build)
    cases = history_cases(root, native, controller, bm)
    output.mkdir(parents=True)
    report = {"kind": "ci-offline-controller-history", "passed": False, "cases": [], "refusals": [],
              "bridge_sha256": bm["bridge_sha256"], "runtime_manifest_sha256": bm["runtime_manifest_sha256"],
              "physical_qualification": False, "native_authority_restored": False}
    before = {str(c["file"]): sha(c["file"]) for c in cases}
    cli = root / "plugins/openpnp/scripts/openpnp.mjs"
    state = output / "installed"  # Never an upload path: contains installed state and bearer material.
    try:
        code, installed = invoke(node, cli, ["install-bridge", "--state-dir", state], output, "install")
        require(code == 0 and installed["installed"] is True, "History install failed")
        installed_before = {str(p): sha(p) for p in state.rglob("*") if p.is_file()}
        def inspect(file, name):
            return invoke(node, cli, ["inspect-controller-history", "--state-dir", state,
                          "--openpnp-home", build / "runtime", "--java", java, "--journal", file], output, name)
        for case in cases:
            code, result = inspect(case["file"], case["name"])
            require(code == 0, "Valid recorded history was refused")
            check_history(result, case, bm)
            report["cases"].append({"name": case["name"], "passed": True,
                                    "journal_sha256": result["journal_sha256"], "state": case["state"]})
        for name, content in mutations(cases[0]["file"].read_bytes()).items():
            file = output / (name + ".jsonl")
            file.write_bytes(content)
            digest = sha(file)
            code, result = inspect(file, name)
            require(code == 2 and result["valid"] is False and
                    result["mode"] == "offline-recorded-controller-history" and
                    all(result[key] is False for key in AUTHORITY) and
                    result["inspector_process"]["reaped"] is True and sha(file) == digest,
                    "Malformed history was not conservatively refused")
            report["refusals"].append({"name": name, "passed": True, "journal_sha256": digest,
                                       "error_code": result["error_code"]})
        require(installed_before == {str(p): sha(p) for p in state.rglob("*") if p.is_file()}, "Inspector changed installation")
        require(len(report["cases"]) == 7 and len(report["refusals"]) == 3, "Incomplete history coverage")
        report["passed"] = True
    except Exception as error:
        report["failure"] = str(error)
    finally:
        try:
            report["inputs_unchanged"] = all(sha(Path(p)) == digest for p, digest in before.items())
        except OSError as error:
            report["inputs_unchanged"] = False
            report.setdefault("failure", str(error))
        report["passed"] = report["passed"] and report["inputs_unchanged"]
        save(output / "report.json", report)
    require(report["passed"], "CI history checks failed; inspect retained report and first-failure logs")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    staged = commands.add_parser("stage")
    staged.add_argument("--build", required=True, type=Path)
    staged.add_argument("--output", required=True, type=Path)
    inspected = commands.add_parser("history")
    for name in ("build", "native-tests", "controller", "output", "node", "java"):
        inspected.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    if args.command == "stage":
        result = stage(ROOT, args.build.resolve(strict=True), args.output.resolve())
    else:
        result = history(ROOT, args.build.resolve(strict=True), args.native_tests.resolve(strict=True),
                         args.controller.resolve(strict=True), args.output.resolve(), args.node.resolve(strict=True),
                         args.java.resolve(strict=True))
    print(json.dumps({"command": args.command, "output": str(args.output), "passed": result.get("passed"),
                      "qualification_performed": result.get("qualification_performed")}))


if __name__ == "__main__":
    main()
