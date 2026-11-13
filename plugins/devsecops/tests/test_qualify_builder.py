"""Controlled tests of the real executor qualifier's boundaries and cleanup."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest

from devsecops.factory import qualify_builder as q
from tests.test_artifacts import oci_archive


@pytest.mark.parametrize("raw", [b"", b"demo:0:65536", b"demo:100000:1", b"demo:100000:65536\ndemo:200000:65536",
    b"demo:100000:65536\nother:120000:65536", b"demo:bad:65536", b"demo:100000:0", b"demo:4294967294:65536"])
def test_missing_invalid_or_overlapping_uid_allocations_rejected(raw):
    with pytest.raises(q.QualificationError):
        q._id_range(raw, "demo", 1000)


def test_exact_username_or_uid_allocation_is_admitted():
    for owner in ("demo", "1000"):
        assert q._id_range((owner + ":100000:65536\nother:200000:65536").encode(), "demo", 1000) == {"start": 100000, "count": 65536}


def test_prerequisites_never_have_non_linux_or_root_fallback(monkeypatch):
    monkeypatch.setattr(q.platform, "system", lambda: "Darwin")
    with pytest.raises(q.QualificationError, match="owned-nonroot-linux-user"):
        q.prerequisites(os.getuid())
    monkeypatch.setattr(q.platform, "system", lambda: "Linux")
    with pytest.raises(q.QualificationError, match="owned-nonroot-linux-user"):
        q.prerequisites(0)


def test_commands_are_fixed_offline_and_never_push_or_disable_process_sandbox():
    commands = q._commands(Path("/tmp/owned-probe"))
    assert "--net=none" in commands["rootlesskit"] and "--pidns" in commands["rootlesskit"]
    assert "--no-cache" in commands["buildctl"] and "dockerfile.v0" in commands["buildctl"]
    for command in commands.values():
        assert not any("no-process-sandbox" in value or "push=true" in value or "allow-insecure" in value for value in command)
        assert "--allow" not in command and "--secret" not in command and "--ssh" not in command
    assert set(q._environment(Path("/tmp/owned-probe"))) == {
        "PATH", "HOME", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
        "DOCKER_CONFIG", "TMPDIR", "OTEL_SDK_DISABLED"}


@pytest.fixture
def run_state(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix="dso-q-", dir="/tmp") as temporary:
        root = Path(temporary).resolve()
        boot = "d836be49-595f-4713-a4a7-fbc6a74150ee"
        monkeypatch.setattr(q, "_boot_id", lambda: boot)
        state = {"schema_version": 1, "kind": "owned-rootless-executor-processes", "run_id": boot,
                 "directory": q._root_identity(root), "boot_id": boot, "processes": {}}
        monkeypatch.setattr(q, "_untracked_processes", lambda *args: [])
        q._atomic(root, "process-state.json", state)
        yield root, state


def test_cleanup_removes_owned_runtime_links_without_following_them(run_state, tmp_path):
    root, _ = run_state
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "must-remain").write_text("private unrelated file")
    (root / "cache").mkdir()
    (root / "cache/link").symlink_to(outside, target_is_directory=True)
    (root / "home").symlink_to(outside, target_is_directory=True)
    (root / "image.oci.tar").write_text("retained evidence")
    assert q.cleanup(root)["decision"] == "pass"
    assert (outside / "must-remain").read_text() == "private unrelated file"
    assert not (root / "cache").exists() and not (root / "home").exists()
    assert (root / "image.oci.tar").read_text() == "retained evidence"
    assert q.cleanup(root)["decision"] == "pass"  # Repeated cleanup is harmless.


def _identity(root, role):
    argv = q._commands(root)[role]
    return {"pid": 424242, "uid": os.getuid(), "start_ticks": 12, "executable_device": 1, "executable_inode": 2,
            "argv_sha256": q._sha(b"\x00".join(os.fsencode(arg) for arg in argv) + b"\x00")}


def _install_pidfds(monkeypatch, deliver):
    descriptors = {}
    def open_pid(pid, flags):
        descriptor = os.open(os.devnull, os.O_RDONLY)
        descriptors[descriptor] = pid
        return descriptor
    def send(descriptor, sig, info, flags):
        assert info is None and flags == 0
        deliver(descriptors[descriptor], sig)
    monkeypatch.setattr(q.os, "pidfd_open", open_pid, raising=False)
    monkeypatch.setattr(q.signal, "pidfd_send_signal", send, raising=False)
    return descriptors


def test_cleanup_refuses_reused_pid_before_signaling(run_state, monkeypatch):
    root, state = run_state
    identity = _identity(root, "rootlesskit")
    state["processes"]["rootlesskit"] = identity
    q._atomic(root, "process-state.json", state)
    monkeypatch.setattr(q, "_proc", lambda pid: {**identity, "start_ticks": 13})
    monkeypatch.setattr(q.os, "kill", lambda *args: pytest.fail("Never signal a reused process identity"))
    with pytest.raises(q.QualificationError, match="process-identity-changed"):
        q.cleanup(root)
    assert not (root / "cleanup.json").exists()


def test_cleanup_refuses_arbitrary_process_command_even_with_matching_pid(run_state, monkeypatch):
    root, state = run_state
    identity = _identity(root, "rootlesskit")
    identity["argv_sha256"] = "0" * 64
    state["processes"]["rootlesskit"] = identity
    q._atomic(root, "process-state.json", state)
    monkeypatch.setattr(q.os, "kill", lambda *args: pytest.fail("Never signal unrelated command"))
    with pytest.raises(q.QualificationError, match="cleanup-process-authority"):
        q.cleanup(root)


def test_cleanup_stops_only_exact_owned_identity(run_state, monkeypatch):
    root, state = run_state
    identity = _identity(root, "rootlesskit")
    state["processes"]["rootlesskit"] = identity
    q._atomic(root, "process-state.json", state)
    running, signals = [True], []
    monkeypatch.setattr(q, "_proc", lambda pid: identity if running[0] else None)
    def kill(pid, sig):
        signals.append((pid, sig))
        running[0] = False
    _install_pidfds(monkeypatch, kill)
    result = q.cleanup(root)
    assert signals == [(identity["pid"], signal.SIGTERM)]
    assert result["processes_stopped"] == ["rootlesskit"]
    assert json.loads((root / "process-state.json").read_text())["processes"] == {}
    # A later unrelated PID reuse must not turn an already-clean run into a kill.
    monkeypatch.setattr(q, "_proc", lambda pid: {**identity, "start_ticks": 13})
    assert q.cleanup(root)["decision"] == "pass"
    assert len(signals) == 1


def test_live_untracked_process_prevents_cleanup_pass(run_state, monkeypatch):
    root, _ = run_state
    monkeypatch.setattr(q, "_untracked_processes", lambda *args: [{"role": "buildkitd", "identity": {}}])
    with pytest.raises(q.QualificationError, match="owned-processes-still-present"):
        q.cleanup(root)
    assert not (root / "cleanup.json").exists()


def test_changed_socket_cannot_be_removed(run_state):
    root, state = run_state
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(root / "buildkit.sock"))
        state["socket"] = {"device": 0, "inode": 0, "uid": os.getuid()}
        q._atomic(root, "process-state.json", state)
        with pytest.raises(q.QualificationError, match="cleanup-socket-identity"):
            q.cleanup(root)
        assert (root / "buildkit.sock").exists()


def test_cleanup_rejects_changed_run_directory(run_state):
    root, state = run_state
    state["directory"]["inode"] += 1
    q._atomic(root, "process-state.json", state)
    with pytest.raises(q.QualificationError, match="run-directory-identity-changed"):
        q.cleanup(root)


def test_cleanup_deadline_checked_during_tree_removal(tmp_path):
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        (tmp_path / "runtime").mkdir()
        with pytest.raises(q.QualificationError, match="qualification-deadline"):
            q._remove_tree(parent, "runtime", time.monotonic() - 1, budget=[0])
    finally:
        os.close(parent)


def test_cleanup_limits_depth_before_recursing(tmp_path):
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        (tmp_path / "runtime").mkdir()
        with pytest.raises(q.QualificationError, match="cleanup-depth-limit"):
            q._remove_tree(parent, "runtime", time.monotonic() + 10, budget=[0], depth=65)
    finally:
        os.close(parent)


def test_pid_reuse_between_initial_identity_check_and_pidfd_open_is_rejected(run_state, monkeypatch):
    root, state = run_state
    identity = _identity(root, "rootlesskit")
    state["processes"]["rootlesskit"] = identity
    q._atomic(root, "process-state.json", state)
    current = [identity]
    descriptors = []
    def open_pid(pid, flags):
        current[0] = {**identity, "start_ticks": 13}
        descriptor = os.open(os.devnull, os.O_RDONLY)
        descriptors.append(descriptor)
        return descriptor
    monkeypatch.setattr(q, "_proc", lambda pid: current[0])
    monkeypatch.setattr(q.os, "pidfd_open", open_pid, raising=False)
    monkeypatch.setattr(q.signal, "pidfd_send_signal", lambda *args: pytest.fail("Never signal the reused process"), raising=False)
    with pytest.raises(q.QualificationError, match="process-identity-changed"):
        q.cleanup(root)
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_pidfd_cannot_signal_reused_numeric_pid_after_identity_check(run_state, monkeypatch):
    root, _ = run_state
    identity = _identity(root, "rootlesskit")
    current = [identity]
    monkeypatch.setattr(q, "_proc", lambda pid: current[0])
    monkeypatch.setattr(q.os, "kill", lambda *args: pytest.fail("Numeric PID signaling is forbidden"))
    def old_process_exits(pid, sig):
        current[0] = {**identity, "start_ticks": 13}
        # A pidfd still references the exited original process; the replacement
        # PID receives no signal. Linux reports ESRCH for that old descriptor.
        raise ProcessLookupError()
    descriptors = _install_pidfds(monkeypatch, old_process_exits)
    assert q._signal_owned(identity, signal.SIGTERM) is False
    assert current[0]["start_ticks"] == 13
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_missing_pidfd_support_has_no_numeric_pid_fallback(run_state, monkeypatch):
    root, _ = run_state
    monkeypatch.delattr(q.os, "pidfd_open", raising=False)
    monkeypatch.setattr(q.os, "kill", lambda *args: pytest.fail("No weaker signaling fallback"))
    with pytest.raises(q.QualificationError, match="linux-pidfd-signaling-required"):
        q._signal_owned(_identity(root, "rootlesskit"), signal.SIGTERM)


@pytest.mark.parametrize("output", [Path("relative"), Path("/tmp/ambiguous?run"), Path("/tmp/a,dest=outside"), Path("/tmp") / ("x" * 71)])
def test_bad_run_path_rejected_before_prerequisites_or_processes(output, monkeypatch):
    monkeypatch.setattr(q, "prerequisites", lambda *args: pytest.fail("No prerequisite read for invalid path"))
    with pytest.raises(q.QualificationError, match="explicit-short-run-path"):
        q.qualify(output=output, tools=Path("/tmp/tools"), expected_uid=os.getuid())


def test_existing_run_directory_never_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "prerequisites", lambda *args: {})
    with tempfile.TemporaryDirectory(prefix="dso-q-", dir="/tmp") as temporary:
        root = Path(temporary).resolve()
        (root / "preserve").write_text("existing")
        with pytest.raises(FileExistsError):
            q.qualify(output=root, tools=Path("/tmp/tools"), expected_uid=os.getuid())
        assert (root / "preserve").read_text() == "existing"


@pytest.mark.parametrize("command", ["--help", "run", "cleanup"])
def test_qualifier_help_works_outside_checkout(tmp_path, command):
    argv = [sys.executable, str(q.ROOT / "scripts/qualify_rootless_builder.py"), command]
    if command != "--help":
        argv.append("--help")
    result = subprocess.run(argv, cwd=tmp_path, capture_output=True)
    assert result.returncode == 0 and b"usage:" in result.stdout


def test_cli_explains_missing_prerequisite_code_without_diagnostics(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("qualifier_cli", q.ROOT / "scripts/qualify_rootless_builder.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def unavailable(**kwargs):
        raise q.QualificationError("subordinate-id-allocation-required")
    monkeypatch.setattr(module, "qualify", unavailable)
    assert module.main(["run", "--output", "/tmp/new", "--tools", "/tmp/tools", "--owner-uid", "1000"]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "subordinate-id-allocation-required"


@pytest.mark.parametrize("defect", [None, "buildctl-umask-zero", "missing-run-output", "wrong-export-digest", "cleanup-incomplete", "cleanup-overrun",
    "publication-overrun", "publication-route-swap", "interrupted", "start-failure", "tool-tamper"])
def test_controlled_lifecycle_requires_actual_run_marker_export_identity_and_cleanup(tmp_path, monkeypatch, defect):
    """Synthetic process/worker responses exercise orchestration, never live gates."""
    source = tmp_path / "tools"
    source.mkdir()
    pins = {}
    for name in ("buildctl", "buildkitd", "buildkit-runc", "rootlesskit"):
        raw = ("controlled-never-executed-" + name).encode()
        (source / name).write_bytes(raw)
        pins[name] = {"sha256": q._sha(raw), "bytes": len(raw)}
    (tmp_path / "supplychain.lock.json").write_text(json.dumps({
        "builder": {"version": "0.34.1", "platforms": {"linux-amd64": {"binaries": {k: v for k, v in pins.items() if k != "rootlesskit"}}}},
        "rootlesskit": {"version": "3.3.0", "platforms": {"linux-amd64": {"binary_sha256": pins["rootlesskit"]["sha256"], "binary_bytes": pins["rootlesskit"]["bytes"]}}}}))
    monkeypatch.setattr(q, "ROOT", tmp_path)
    monkeypatch.setattr(q.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(q, "prerequisites", lambda uid: {"uid": uid, "controlled_test": True})
    monkeypatch.setattr(q, "_boot_id", lambda: "d836be49-595f-4713-a4a7-fbc6a74150ee")
    monkeypatch.setattr(q, "_untracked_processes", lambda *args: [])
    identities, children, listeners = {}, {}, []
    image, digest = oci_archive(tmp_path)
    image_bytes = image.read_bytes()
    native_popen = subprocess.Popen
    if defect == "tool-tamper":
        (source / "buildkitd").write_text("changed after the independent pin")
    class Process:
        def __init__(self, argv, **kwargs):
            role = Path(argv[0]).name
            if role == "rootlesskit" and defect == "start-failure":
                raise OSError("controlled startup failure")
            if role == "buildctl" and defect == "interrupted":
                raise q.Interrupted()
            self.pid = 42001 if role == "rootlesskit" else 42003
            self.returncode = None if role == "rootlesskit" else 0
            root = kwargs["cwd"]
            roles = (("rootlesskit", 42001), ("buildkitd", 42002)) if role == "rootlesskit" else (("buildctl", 42003),)
            for item, pid in roles:
                identity = _identity(root, item)
                identity["pid"] = pid
                identities[pid] = identity
            children[self.pid] = self
            if role == "rootlesskit":
                endpoint = socket.socket(socket.AF_UNIX)
                endpoint.bind(str(root / "buildkit.sock"))
                listeners.append(endpoint)
            else:
                kwargs["stderr"].write(b"#4 0.01 rootlesskit version 3.3.0\n" if defect != "missing-run-output" else b"build without execution proof\n")
                if defect == "buildctl-umask-zero":
                    # Real file operations in a child reproduce buildctl's Unix
                    # umask reset, os.Create export and atomic 0666 metadata.
                    script = """import json, os, pathlib, sys
os.umask(0)
pathlib.Path('image.oci.tar').write_bytes(pathlib.Path(sys.argv[1]).read_bytes())
pathlib.Path('metadata.tmp').write_text(json.dumps({'containerimage.digest': sys.argv[2]}))
os.replace('metadata.tmp', 'build-metadata.json')
assert pathlib.Path('build-metadata.json').stat().st_mode & 0o777 == 0o666
"""
                    child = native_popen([sys.executable, '-I', '-B', '-c', script, str(image), digest],
                                         cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, umask=0o077)
                    _, error = child.communicate(timeout=10)
                    assert child.returncode == 0, error.decode()
                else:
                    (root / "image.oci.tar").write_bytes(image_bytes)
                    (root / "image.oci.tar").chmod(0o600)
                    (root / "build-metadata.json").write_text(json.dumps({"containerimage.digest": "sha256:" + "0" * 64 if defect == "wrong-export-digest" else digest}))
        def poll(self):
            return self.returncode
        def wait(self, timeout):
            self.returncode = 0
            return 0
        def kill(self):
            self.returncode = -9
    monkeypatch.setattr(q.subprocess, "Popen", Process)
    monkeypatch.setattr(q, "_proc", lambda pid: identities.get(pid))
    def kill(pid, sig):
        identities.pop(pid, None)
        if pid in children:
            children[pid].returncode = 0
    _install_pidfds(monkeypatch, kill)
    monkeypatch.setattr(q, "_remember_daemon_peer", lambda root, state, argv, deadline: q._remember(root, state, "buildkitd", 42002, argv))
    monkeypatch.setattr(q, "inspect_rootless_worker", lambda **kwargs: {"peer": {"pid": 42002}, "worker": {"id": "controlled"}})
    original_cleanup = q.cleanup
    def cleanup(root):
        result = original_cleanup(root)
        if defect == "cleanup-overrun":
            monkeypatch.setattr(q.time, "monotonic", lambda: 10**12)
        return {"decision": "unknown", "code": "controlled-cleanup-failure"} if defect == "cleanup-incomplete" else result
    monkeypatch.setattr(q, "cleanup", cleanup)
    original_atomic = q._atomic
    def publish(root, name, value, **kwargs):
        result = original_atomic(root, name, value, **kwargs)
        if defect == "publication-overrun" and name == "qualification.json":
            monkeypatch.setattr(q.time, "monotonic", lambda: 10**12)
        elif defect == "publication-route-swap" and name == "qualification.json":
            root.rename(root.with_name("retained"))
            root.mkdir(mode=0o700)
        return result
    monkeypatch.setattr(q, "_atomic", publish)
    try:
        with tempfile.TemporaryDirectory(prefix="dso-q-", dir="/tmp") as temporary:
            root = Path(temporary).resolve() / "run"
            handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
            if defect in {"publication-overrun", "publication-route-swap"}:
                with pytest.raises(q.QualificationError):
                    q.qualify(output=root, tools=source, expected_uid=os.getuid())
                assert not (root / "qualification.json").exists()
                if defect == "publication-route-swap":
                    assert not (root.with_name("retained") / "qualification.json").exists()
                assert not identities
                return
            report = q.qualify(output=root, tools=source, expected_uid=os.getuid())
            assert report["canonical_demo"] == report["mission_build"] == "not-run"
            assert report["decision"] == ("pass" if defect in (None, "buildctl-umask-zero") else "unknown")
            if defect == "buildctl-umask-zero":
                assert (root / 'image.oci.tar').stat().st_mode & 0o777 == 0o600
                assert (root / 'build-metadata.json').stat().st_mode & 0o777 == 0o600
            if defect == "cleanup-overrun":
                assert report["cleanup"]["decision"] == "pass"
                assert {"code": "qualification-deadline"} in report["gaps"]
            assert not any((root / name).exists() for name in q.RUNTIME_NAMES)
            assert json.loads((root / "qualification.json").read_text())["decision"] == report["decision"]
            assert not identities
            assert {sig: signal.getsignal(sig) for sig in handlers} == handlers
    finally:
        for listener in listeners:
            listener.close()


@pytest.mark.parametrize("case", ["log-overflow", "image-overflow", "deadline"])
def test_runtime_watch_stops_on_resource_bound(run_state, monkeypatch, case):
    root, state = run_state
    class Running:
        def poll(self):
            return None
    files = {"client.stderr": 8, "image.oci.tar": 16}
    if case != "deadline":
        (root / ("client.stderr" if case == "log-overflow" else "image.oci.tar")).write_bytes(b"x" * 20)
    with pytest.raises(q.QualificationError, match="deadline|output-limit"):
        q._watch(root, Running(), state, time.monotonic() + (-1 if case == "deadline" else 2), files)
