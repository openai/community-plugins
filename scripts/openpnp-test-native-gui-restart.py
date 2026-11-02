#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fresh halt -> actual GUI -> packaged MCP placement -> third-JVM history.

The retained source41 full-MCP Java/Node assertions remain unchanged. This portable
runner selects current manifest-bound artifacts, never dated evidence or state.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("openpnp_gui_restart_owned", ROOT / "scripts/openpnp-test-native-candidate.py")
owned = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(owned)
BOUNDARIES = {"intent-force": 73, "zip-before": 74}
HELPER_NAMES = (
    "NativeGuiRestartFullMcpTest",
    "NativeReplacementIntentCrashBridgeTest",
    "NativeRestartExecutionBridgeTest",
    "NativeFaultedSensingReplacementContinuationBridgeTest",
)


def expected_completion(record, expected_exit):
    """Only the declared producer's exact halt is an expected nonzero exit."""
    cleanup = record.get("cleanup") or {}
    primary = record.get("primary_error")
    allowed_error = primary is None or (expected_exit != 0 and primary.get("type") == "NonZeroExit")
    return bool(record.get("exit_code") == expected_exit and allowed_error
                and not record.get("timeout") and not record.get("interrupted")
                and cleanup.get("parent_reaped") and cleanup.get("group_gone")
                and not cleanup.get("forced_cleanup") and not cleanup.get("errors"))


def verify_gui_proof(proof):
    for name in ("passed", "actual_mainframe", "original_native_graph_preserved",
                 "actual_packaged_mcp_recovery_requests", "owned_bootstrap_loader_closed",
                 "all_public_workflow_tools_use_packaged_mcp"):
        owned.require(proof.get(name) is True, "Missing GUI restart proof: " + name)
    owned.require(proof.get("post_restart_placements") == 1, "Expected one actual post-restart placement")
    owned.require(proof.get("direct_public_bridge_workflow_calls") == 0, "Public workflow bypassed MCP")
    owned.require(proof.get("legacy_helper_direct_calls") == 0, "Legacy direct helper called")


def output_path(value, build):
    raw = value.absolute()
    resolved = raw.resolve()
    owned.require(raw == resolved and resolved.is_relative_to(ROOT / "validation"),
                  "Output must be a nonsymbolic descendant of repository validation/")
    owned.require(not resolved.exists() and not resolved.is_relative_to(build)
                  and not build.is_relative_to(resolved), "Use a fresh output distinct from the immutable build")
    return resolved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--java-home", required=True, type=Path)
    parser.add_argument("--node", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--boundary", required=True, choices=BOUNDARIES)
    args = parser.parse_args(argv)
    build, java_home, node = args.build.resolve(), args.java_home.resolve(), args.node.resolve()
    owned.require(build.is_relative_to(ROOT), "Select an immutable build below this checkout")
    owned.require(node.is_file() and os.access(node, os.X_OK), "Explicit executable Node 22+ required")
    out = output_path(args.run, build)
    runtime = build / "runtime"
    before, receipt, native, files = owned.inventory(build, runtime, java_home)
    package = ROOT / "plugins/openpnp"
    jar, bootstrap, server = package / "bridge/openpnp-codex-bridge.jar", package / "bridge/bootstrap.js", package / "mcp/server.mjs"
    owned.require(owned.sha(jar) == receipt["bridge_sha256"], "Packaged Bridge differs from the current native build")
    owned.require(owned.sha(bootstrap) == receipt["bootstrap_sha256"], "Packaged bootstrap differs from current native build")
    owned.require(owned.read_json(package / "bridge/build-manifest.json") == receipt,
                  "Package must include this exact build manifest")
    support = ROOT / "tests/openpnp/support/gui-restart"
    sources = [support / (HELPER_NAMES[0] + ".java")]
    sources += [ROOT / "tests/openpnp/native" / (name + ".java") for name in HELPER_NAMES[1:]]
    outer = support / "NativeGuiRestartFullMcpLauncher.java"
    driver = ROOT / "tests/openpnp/native-sensing-restart-packaged-mcp.mjs"
    generic = ROOT / "tests/openpnp/native-gui-full-packaged-mcp.mjs"
    dependencies = ROOT / "src/openpnp/node/node_modules"
    owned.require((dependencies / "@modelcontextprotocol/sdk/package.json").is_file(), "Install the locked source SDK first")
    paths = [Path(__file__), node, java_home / "bin/javac", jar, bootstrap, server, driver, generic, outer, *sources,
             ROOT / "scripts/openpnp-read-response.mjs", package / "bridge/build-manifest.json"]
    paths += owned.tree_files(package)
    # npm creates internal executable symlinks; bind their regular targets too.
    dependency_paths = [p for p in dependencies.rglob("*") if p.is_file()]
    for path in dependency_paths:
        owned.require(path.resolve().is_relative_to(dependencies), "Dependency symlink escaped the locked tree")
    paths += [p.resolve() for p in dependency_paths]
    before.update({str(path): owned.sha(owned.regular(path)) for path in paths})
    out.mkdir(parents=True, mode=0o700)
    for name in ("classes", "outer-classes", "sources"):
        (out / name).mkdir(mode=0o700)
    snapshots = []
    for source in [*sources, outer]:
        copied = out / "sources" / source.name
        shutil.copyfile(source, copied)
        before[str(copied)] = owned.sha(copied)
        if source != outer:
            snapshots.append(copied)
    owned.write_json(out / "inputs-before.json", before)
    launcher = runtime / native["gui_launcher_jar"]
    nativecp = os.pathsep.join(map(str, [launcher, runtime / native["gui_jar"],
        *[runtime / name for name in sorted(files) if name.startswith(native["libs_directory"] + "/") and name.endswith(".jar")]]))
    compilecp = str(jar) + os.pathsep + nativecp
    env = owned.clean_environment()
    for key in list(env):
        if key.startswith(("JAVA_", "JDK_", "_JAVA_", "CLASSPATH", "NODE_OPTIONS", "NODE_PATH")):
            del env[key]
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        signal.signal(sig, owned.interrupt)
    processes, error, proof, compiled = [], None, None, {}

    def run(name, command, timeout, expected=0):
        record = owned.run_logged(list(map(str, command)), env, out / (name + ".log"), timeout, cwd=ROOT)
        # Preserve the generic runner's original nonzero result. The campaign
        # separately admits only its predeclared producer halt and clean group.
        processes.append({"name": name, "expected_exit": expected, **record})
        owned.require(expected_completion(record, expected), name + " failed or required process cleanup")
        return record

    def jvm(name, clazz, state, gui=False):
        home, tmp = out / (name + "-home"), out / (name + "-tmp")
        home.mkdir(mode=0o700); tmp.mkdir(mode=0o700)
        cp = str(out / "outer-classes") + os.pathsep + nativecp if gui else str(out / "classes") + os.pathsep + nativecp + os.pathsep + str(jar)
        manifest = runtime / "codex-build-manifest.json"
        command = [java_home / "bin/java", "-Xmx1g", "-XX:+ExitOnOutOfMemoryError", "-Djava.awt.headless=" + str(not gui).lower(),
            "-Djava.util.prefs.PreferencesFactory=org.openpnp.codex.IsolatedPreferencesFactory",
            "-Duser.home=" + str(home), "-Djava.io.tmpdir=" + str(tmp),
            "-Dopenpnp.codex.runtimeManifest=" + str(manifest), "-Dopenpnp.codex.runtimeManifestSha256=" + owned.sha(manifest),
            "-Dopenpnp.codex.testMcpDriver=" + str(driver), "-Dopenpnp.codex.testMcpGenericDriver=" + str(generic),
            "-Dopenpnp.codex.testMcpNode=" + str(node), "-Dopenpnp.codex.testMcpServer=" + str(server),
            "-Dopenpnp.codex.testMcpServerSha256=" + owned.sha(server), "-Dopenpnp.codex.testNativeLauncher=" + str(launcher),
            "--add-opens=java.base/java.lang=ALL-UNNAMED", "--add-opens=java.base/java.net=ALL-UNNAMED",
            "--add-opens=java.desktop/java.awt=ALL-UNNAMED", "--add-opens=java.desktop/java.awt.color=ALL-UNNAMED", "-cp", cp, clazz]
        return command + ([runtime, jar, bootstrap, state, out / "classes"] if gui else [runtime / "samples", state])

    try:
        run("compile-helpers", [java_home / "bin/javac", "--release", "17", "-sourcepath", "", "-cp", compilecp, "-d", out / "classes", *snapshots], 60)
        run("compile-native-launcher", [java_home / "bin/javac", "--release", "17", "-sourcepath", "", "-cp", nativecp, "-d", out / "outer-classes", out / "sources" / outer.name], 60)
        helpers = {str(p.relative_to(out / "classes")): owned.sha(p) for p in (out / "classes").rglob("*.class")}
        with zipfile.ZipFile(jar) as archive:
            canonical_names = set(archive.namelist())
        owned.require(helpers and all(name not in canonical_names and name.split("/")[-1].split("$")[0].split(".")[0] in HELPER_NAMES for name in helpers), "Test-only helper class family collided with production")
        outer_classes = {str(p.relative_to(out / "outer-classes")): owned.sha(p) for p in (out / "outer-classes").rglob("*.class")}
        owned.require(outer_classes and all(Path(name).name.startswith("NativeGuiRestartFullMcpLauncher") for name in outer_classes), "Unexpected outer launcher class")
        compiled = {str(out / folder / name): digest for folder, rows in (("classes", helpers), ("outer-classes", outer_classes)) for name, digest in rows.items()}
        owned.write_json(out / "test-only-class-inventory.json", {"no_production_name_collisions": True, "helper_classes": helpers, "outer_classes": outer_classes})
        state = out / "state"
        crash = run(args.boundary + "-crash", jvm("crash", "org.openpnp.codex.NativeReplacementIntentCrashBridgeTest", state) + ["crash", args.boundary], 180, BOUNDARIES[args.boundary])
        marker = owned.read_json(state / "crash-boundary.json")
        owned.require(marker["pid"] == crash["pid"] and marker["expected_exit"] == BOUNDARIES[args.boundary], "Crash receipt identity mismatch")
        original = {str(p.relative_to(state)): owned.sha(p) for p in owned.tree_files(state)}
        shutil.copytree(state, out / "preserved-crash")
        owned.write_json(out / "crash-inventory.json", original)
        run("gui-packaged-restart", jvm("gui", "org.openpnp.codex.NativeGuiRestartFullMcpLauncher", state, True), 240)
        proof = owned.read_json(state / "restart-execution-proof.json")
        verify_gui_proof(proof)
        owned.require(original == {str(p.relative_to(out / "preserved-crash")): owned.sha(p) for p in owned.tree_files(out / "preserved-crash")}, "Preserved original crash state changed")
        run("third-jvm-history", jvm("history", "org.openpnp.codex.NativeGuiRestartFullMcpTest", state) + ["history"], 90)
        owned.require(owned.read_json(state / "restart-history-proof.json")["passed"] is True, "Fresh third-JVM history failed")
    except BaseException as failure:
        error = type(failure).__name__ + ": " + str(failure)
    finally:
        after, inventory_errors = owned.observe_inputs(build, runtime, java_home, before)
        extra = {str(p) for p in package.rglob("*") if p.is_file()}
        extra.update(str(p.resolve()) for p in dependencies.rglob("*") if p.is_file())
        after.update({name: owned.sha(Path(name)) if Path(name).is_file() else None for name in extra})
        compiled_after = {str(p): owned.sha(p) for folder in ("classes", "outer-classes") for p in (out / folder).rglob("*.class")}
        unchanged = before == after and compiled == compiled_after and not inventory_errors
        owned.write_json(out / "inputs-after.json", after)
        result = {"passed": error is None and unchanged and not owned.INTERRUPTED, "error": error,
            "input_count": len(before), "inputs_unchanged": unchanged, "crash_boundary": args.boundary,
            "canonical_bridge_sha256": owned.sha(jar), "runtime_manifest_sha256": receipt["runtime_manifest_sha256"],
            "packaged_mcp_sha256": owned.sha(server), "processes": processes, "proof": proof,
            "scope": "Fresh producer halt, actual shipped GUI bootstrap, full public workflow through packaged MCP, one native placement, and third-JVM history; programmatic Swing decisions",
            "physical_machine_qualified": False, "desktop_gestures_qualified": False, "fresh_skill_actor_competence_qualified": False}
        owned.write_json(out / "receipt.json", result)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    print(json.dumps({key: result[key] for key in ("passed", "error", "input_count", "inputs_unchanged", "crash_boundary")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
