"""Owned Linux rootless executor qualification using a fixed offline fixture.

This is a real executor probe, not an Iron Bank mission build or promotion.
It makes no system configuration changes and never uses a Docker daemon.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import platform
import pwd
import re
import signal
import socket
import stat
import struct
import subprocess
import tarfile
import time
import uuid

from devsecops.adapters.artifacts import _BoundedTarInfo, _canonical, _oci_archive, _open_directory
from devsecops.adapters.http import _json
from devsecops.adapters.signatures import _snapshot
from devsecops.factory.build import inspect_rootless_worker

ROOT = Path(__file__).resolve().parents[3]
MAX_LOG = 8_000_000
MAX_IMAGE = 128_000_000
RUNTIME_NAMES = ("rootless", "buildkit-state", "home", "config", "cache", "docker", "tmp", "tools", "fixture")


class QualificationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class Interrupted(BaseException):
    pass


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _boot_id():
    value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if str(uuid.UUID(value)) != value:
        raise QualificationError("kernel-boot-identity-invalid")
    return value


def _remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise QualificationError("qualification-deadline")
    return value


def _regular(path, limit=64_000, *, root_owned=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit
                or root_owned and (before.st_uid != 0 or before.st_mode & 0o022)):
            raise QualificationError("prerequisite-file-authority")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if len(raw) > limit or any(getattr(before, key) != getattr(after, key) for key in keys):
        raise QualificationError("prerequisite-file-changed")
    return raw, before


def _image_receipt(path, limit, deadline):
    """Hash a bounded stable exported image without loading it all into RAM."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.getuid()
                or before.st_mode & 0o077 or not 0 < before.st_size <= limit):
            raise QualificationError('output-image-authority')
        size, checksum = 0, hashlib.sha256()
        while chunk := stream.read(65536):
            _remaining(deadline)
            size += len(chunk)
            if size > limit:
                raise QualificationError('output-image-limit')
            checksum.update(chunk)
        after = os.fstat(fd)
        if any(getattr(before, key) != getattr(after, key) for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
            raise QualificationError('output-image-changed')
        _remaining(deadline)
        return {'archive_sha256': checksum.hexdigest(), 'archive_bytes': size}


@contextmanager
def _private_image_export(output):
    """Reserve a private inode; buildctl resets umask before opening its export."""
    identity = _root_identity(output)
    parent, descriptor = _open_directory(output), None
    try:
        value = os.fstat(parent)
        if identity != {'device': value.st_dev, 'inode': value.st_ino, 'uid': value.st_uid}:
            raise QualificationError('run-directory-identity-changed')
        descriptor = os.open('image.oci.tar', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        os.fchmod(descriptor, 0o600)
        reserved = os.fstat(descriptor)
        yield
        if _root_identity(output) != identity:
            raise QualificationError('run-directory-identity-changed')
        exported = os.stat('image.oci.tar', dir_fd=parent, follow_symlinks=False)
        if (reserved.st_dev, reserved.st_ino) != (exported.st_dev, exported.st_ino):
            raise QualificationError('output-image-replaced')
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _private_export_metadata(output, limit=1_000_000):
    """Seal buildctl's atomic metadata output inside the owned private directory.

    Buildctl replaces this file with mode 0666, so precreation cannot protect it.
    Validate ownership and links before narrowing permissions through its fd.
    """
    identity = _root_identity(output)
    parent, descriptor = _open_directory(output), None
    try:
        value = os.fstat(parent)
        if identity != {'device': value.st_dev, 'inode': value.st_ino, 'uid': value.st_uid}:
            raise QualificationError('run-directory-identity-changed')
        descriptor = os.open('build-metadata.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.getuid()
                or not 0 < before.st_size <= limit):
            raise QualificationError('output-metadata-authority')
        os.fchmod(descriptor, 0o600)
        sealed = os.fstat(descriptor)
        if any(getattr(before, key) != getattr(sealed, key) for key in
               ('st_dev', 'st_ino', 'st_uid', 'st_nlink', 'st_size', 'st_mtime_ns')):
            raise QualificationError('output-metadata-changed')
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        raw = b''.join(chunks)
        after = os.fstat(descriptor)
        current = os.stat('build-metadata.json', dir_fd=parent, follow_symlinks=False)
        if ((current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
                or any(getattr(sealed, key) != getattr(after, key) for key in
                       ('st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))
                or stat.S_IMODE(after.st_mode) != 0o600 or len(raw) != after.st_size or len(raw) > limit):
            raise QualificationError('output-metadata-changed')
        if _root_identity(output) != identity:
            raise QualificationError('run-directory-identity-changed')
        return raw
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _proc(pid):
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes()
        fields = raw.rsplit(b")", 1)[1].split()
        if fields[0] == b"Z":
            return None
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
        if len(command) > 16_000:
            raise QualificationError("process-command-limit")
        executable = os.stat(f"/proc/{pid}/exe")
        owner = os.stat(f"/proc/{pid}").st_uid
        return {"pid": pid, "start_ticks": int(fields[19]), "uid": owner,
                "executable_device": executable.st_dev, "executable_inode": executable.st_ino,
                "argv_sha256": _sha(command)}
    except (FileNotFoundError, ProcessLookupError):
        return None


def _same_process(identity):
    current = _proc(identity["pid"])
    if current is not None and current != identity:
        raise QualificationError("process-identity-changed")
    return current is not None


def _signal_owned(identity, signum):
    """Pin the kernel process before checking identity and delivering a signal."""
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise QualificationError("linux-pidfd-signaling-required")
    try:
        descriptor = os.pidfd_open(identity["pid"], 0)
    except ProcessLookupError:
        return False
    try:
        if not _same_process(identity):
            return False
        try:
            signal.pidfd_send_signal(descriptor, signum, None, 0)
            return True
        except ProcessLookupError:
            return False
    finally:
        os.close(descriptor)


def _atomic(root, name, value, *, directory=None):
    raw = _canonical(value) + b"\n"
    if len(raw) > 64_000:
        raise QualificationError("receipt-size-limit")
    temporary = "." + name + ".tmp"
    directory = _open_directory(root) if directory is None else os.dup(directory)
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
    finally:
        os.close(directory)


def _root_identity(root):
    descriptor = _open_directory(root)
    try:
        value = os.fstat(descriptor)
        if value.st_uid != os.getuid() or value.st_mode & 0o077:
            raise QualificationError("run-directory-authority")
        return {"device": value.st_dev, "inode": value.st_ino, "uid": value.st_uid}
    finally:
        os.close(descriptor)


def _guard(root, state):
    if _root_identity(root) != state["directory"]:
        raise QualificationError("run-directory-identity-changed")
    boot = _boot_id()
    if boot != state["boot_id"]:
        raise QualificationError("kernel-boot-identity-changed")


def _id_range(raw, name, uid):
    selected, all_ranges = [], []
    for line in raw.decode("ascii").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            raise QualificationError("subordinate-id-format")
        start, count = int(parts[1]), int(parts[2])
        if start <= 0 or count <= 0 or start + count > 2**32 - 1:
            raise QualificationError("subordinate-id-range")
        item = (parts[0], start, start + count)
        all_ranges.append(item)
        if parts[0] in {name, str(uid)}:
            selected.append(item)
    if len(selected) != 1 or selected[0][2] - selected[0][1] < 65536:
        raise QualificationError("subordinate-id-allocation-required")
    chosen = selected[0]
    if any(item is not chosen and max(item[1], chosen[1]) < min(item[2], chosen[2]) for item in all_ranges):
        raise QualificationError("subordinate-id-allocation-overlap")
    return {"start": chosen[1], "count": chosen[2] - chosen[1]}


def prerequisites(expected_uid):
    if platform.system() != "Linux" or type(expected_uid) is not int or expected_uid <= 0 or expected_uid != os.getuid():
        raise QualificationError("owned-nonroot-linux-user-required")
    try:
        status, _ = _regular(Path("/proc/self/status"))
    except (OSError, QualificationError):
        raise QualificationError("linux-no-new-privs-status-unavailable") from None
    flags = [line.partition(b":")[2].strip() for line in status.splitlines()
             if line.partition(b":")[0] == b"NoNewPrivs"]
    if len(flags) != 1 or flags[0] not in (b"0", b"1"):
        raise QualificationError("linux-no-new-privs-status-invalid")
    # This fixed route needs privileged helpers to map subordinate IDs. The
    # inherited kernel flag cannot be cleared and prevents their setuid exec.
    if flags[0] == b"1":
        raise QualificationError("uidmap-incompatible-no-new-privs")
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise QualificationError("linux-pidfd-signaling-required")
    try:
        descriptor = os.pidfd_open(os.getpid(), 0)
        try:
            signal.pidfd_send_signal(descriptor, 0, None, 0)  # Read-only capability probe of this process.
        finally:
            os.close(descriptor)
    except OSError:
        raise QualificationError("linux-pidfd-signaling-required") from None
    user = pwd.getpwuid(expected_uid).pw_name
    result = {"uid": expected_uid, "gid": os.getgid(), "user": user, "helpers": {}, "pidfd_signaling": "available"}
    for name in ("newuidmap", "newgidmap"):
        raw, info = _regular(Path("/usr/bin") / name, 2_000_000, root_owned=True)
        if not info.st_mode & stat.S_ISUID or not info.st_mode & stat.S_IXOTH:
            raise QualificationError("root-owned-uidmap-helper-required")
        result["helpers"][name] = {"sha256": _sha(raw), "bytes": len(raw), "mode": stat.S_IMODE(info.st_mode)}
    for name in ("subuid", "subgid"):
        raw, _ = _regular(Path("/etc") / name, root_owned=True)
        result[name] = {"sha256": _sha(raw), **_id_range(raw, user, expected_uid)}
    return result


def _commands(root, recipe=None):
    tools = root / "tools"
    daemon = [str(tools / "buildkitd"), "--rootless", "--addr", "unix://" + str(root / "buildkit.sock"),
              "--root", str(root / "buildkit-state"), "--oci-worker=true", "--containerd-worker=false",
              "--oci-worker-snapshotter=native", "--oci-worker-binary", str(tools / "buildkit-runc"),
              "--oci-worker-net=host", "--config", str(root / "buildkitd.toml")]
    parent = [str(tools / "rootlesskit"), "--state-dir", str(root / "rootless"), "--net=none", "--pidns",
              "--ipcns", "--utsns", "--copy-up=/etc", "--copy-up=/run", *daemon]
    client = [str(tools / "buildctl"), "--addr", "unix://" + str(root / "buildkit.sock"), "build",
              "--frontend", "dockerfile.v0", "--local", "context=" + str(root / "fixture"),
              "--local", "dockerfile=" + str(root / "fixture"), "--no-cache", "--progress", "plain",
              "--output", "type=oci,dest=" + str(root / "image.oci.tar") + ",oci-mediatypes=true",
              "--metadata-file", str(root / "build-metadata.json")]
    if recipe is not None:
        if recipe.get('kind') == 'mission-image-build-v1':
            from .mission_recipe import arguments
            client.extend(arguments(root, recipe))
        else:
            from .runtime_recipe import client_arguments
            client.extend(client_arguments(root, recipe))
    return {"rootlesskit": parent, "buildkitd": daemon, "buildctl": client}


def _runtime_names(state):
    return RUNTIME_NAMES + (("base",) if state.get("recipe") is not None else ())


def _environment(root):
    # No inherited token, proxy, agent, Docker, telemetry or helper settings.
    return {"PATH": str(root / "tools") + ":/usr/bin:/bin", "HOME": str(root / "home"), "LANG": "C.UTF-8",
            "LC_ALL": "C", "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_RUNTIME_DIR": str(root / "tmp"), "DOCKER_CONFIG": str(root / "docker"),
            "TMPDIR": str(root / "tmp"), "OTEL_SDK_DISABLED": "true"}


def _remember(root, state, role, pid, argv):
    identity = _proc(pid)
    expected_command = _sha(b"\x00".join(os.fsencode(arg) for arg in argv) + b"\x00")
    if identity is None or identity["uid"] != os.getuid() or identity["argv_sha256"] != expected_command:
        raise QualificationError("owned-process-identity-unavailable")
    state["processes"][role] = identity
    _guard(root, state)
    _atomic(root, "process-state.json", state)
    return identity


def _remember_daemon_peer(root, state, argv, deadline):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(min(3, _remaining(deadline)))
        client.connect(str(root / "buildkit.sock"))
        pid, uid, _ = struct.unpack("3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != os.getuid():
        raise QualificationError("daemon-peer-owner-changed")
    return _remember(root, state, "buildkitd", pid, argv)


def _log_receipt(path):
    raw, _ = _regular(path, MAX_LOG)
    return {"sha256": _sha(raw), "bytes": len(raw)}


def _watch(root, process, state, deadline, files):
    while process.poll() is None:
        _remaining(deadline)
        _guard(root, state)
        for name, limit in files.items():
            path = root / name
            if path.exists() and (not path.is_file() or path.is_symlink() or path.stat().st_size > limit):
                raise QualificationError("execution-output-limit")
        time.sleep(0.05)
    for name, limit in files.items():
        path = root / name
        if path.exists() and (not path.is_file() or path.is_symlink() or path.stat().st_size > limit):
            raise QualificationError("execution-output-limit")
    return process.returncode


def _remove_tree(parent, name, deadline, *, budget, depth=0):
    _remaining(deadline)
    if depth > 64:
        raise QualificationError("cleanup-depth-limit")
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    budget[0] += 1
    if budget[0] > 50_000:
        raise QualificationError("cleanup-entry-limit")
    if stat.S_ISDIR(info.st_mode):
        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            with os.scandir(directory) as entries:
                names = []
                for entry in entries:
                    _remaining(deadline)
                    if len(names) + budget[0] > 50_000:
                        raise QualificationError("cleanup-entry-limit")
                    names.append(entry.name)
            for child in names:
                _remove_tree(directory, child, deadline, budget=budget, depth=depth + 1)
        finally:
            os.close(directory)
        os.rmdir(name, dir_fd=parent)
    else:
        os.unlink(name, dir_fd=parent)


def _untracked_processes(root, state, deadline):
    """Read kernel state for exact run commands; never infer cleanup from files."""
    commands = {_sha(b"\x00".join(os.fsencode(arg) for arg in argv) + b"\x00"): role
                for role, argv in _commands(root, state.get("recipe")).items()}
    remaining, examined = [], 0
    with os.scandir("/proc") as entries:
        for entry in entries:
            _remaining(deadline)
            if not entry.name.isdigit():
                continue
            examined += 1
            if examined > 32_000:
                raise QualificationError("process-inventory-limit")
            try:
                if entry.stat(follow_symlinks=False).st_uid != os.getuid():
                    continue
                with open(entry.path + "/cmdline", "rb") as stream:
                    raw = stream.read(16_001)
                role = commands.get(_sha(raw))
                if role is not None:
                    identity = _proc(int(entry.name))
                    if identity is not None:
                        remaining.append({"role": role, "identity": identity})
            except (FileNotFoundError, ProcessLookupError):
                continue
    return remaining


def cleanup(root, *, timeout=30):
    """Stop only recorded exact processes belonging to this private run directory."""
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 60:
        raise QualificationError("cleanup-timeout-unsupported")
    _root_identity(root)
    raw, info = _regular(root / "process-state.json")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise QualificationError("process-state-authority")
    state = _json(raw)
    if state.get("kind") != "owned-rootless-executor-processes" or state.get("schema_version") != 1:
        raise QualificationError("process-state-format")
    _guard(root, state)
    commands = _commands(root, state.get("recipe"))
    if not isinstance(state.get("processes"), dict) or not set(state["processes"]) <= set(commands):
        raise QualificationError("process-state-roles")
    deadline, stopped = time.monotonic() + timeout, []
    # Stop client first, then the namespace parent and its PID-namespace init.
    for role in ("buildctl", "rootlesskit", "buildkitd"):
        identity = state["processes"].get(role)
        if identity is None:
            continue
        expected = _sha(b"\x00".join(os.fsencode(arg) for arg in commands[role]) + b"\x00")
        if (identity.get("uid") != os.getuid() or identity.get("argv_sha256") != expected
                or type(identity.get("pid")) is not int or identity["pid"] <= 1):
            raise QualificationError("cleanup-process-authority")
        if _same_process(identity):
            _signal_owned(identity, signal.SIGTERM)
            grace = min(deadline, time.monotonic() + 5)
            while _same_process(identity) and time.monotonic() < grace:
                time.sleep(0.05)
            if _same_process(identity):
                _signal_owned(identity, signal.SIGKILL)
                while _same_process(identity):
                    _remaining(deadline)
                    time.sleep(0.05)
        stopped.append(role)
    if _untracked_processes(root, state, deadline):
        raise QualificationError("owned-processes-still-present")
    _guard(root, state)
    socket = root / "buildkit.sock"
    if socket.exists() or socket.is_symlink():
        current = socket.lstat()
        expected = state.get("socket")
        if (not stat.S_ISSOCK(current.st_mode) or expected is None
                or {"device": current.st_dev, "inode": current.st_ino, "uid": current.st_uid} != expected):
            raise QualificationError("cleanup-socket-identity")
        socket.unlink()
    budget, directory = [0], _open_directory(root)
    try:
        opened = os.fstat(directory)
        if {"device": opened.st_dev, "inode": opened.st_ino, "uid": opened.st_uid} != state["directory"]:
            raise QualificationError("run-directory-identity-changed")
        for name in _runtime_names(state):
            try:
                os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue
            _remove_tree(directory, name, deadline, budget=budget)
    finally:
        os.close(directory)
    result = {"kind": "rootless-executor-cleanup", "decision": "pass", "finished_at": _now(),
              "processes_stopped": stopped, "process_identities": state["processes"],
              "kernel_matching_processes_remaining": 0, "socket_removed": True,
              "runtime_directories_removed": list(_runtime_names(state))}
    state["historical_processes"] = state["processes"] or state.get("historical_processes", {})
    state["processes"] = {}
    state["runtime_removed"] = True
    _remaining(deadline)
    _guard(root, state)
    _atomic(root, "process-state.json", state)
    _atomic(root, "cleanup.json", result)
    return result


def qualify(*, output, tools, expected_uid, timeout=180, _python_probe=None, _mission_build=None):
    """Execute one fixed no-cache recipe, then remove its owned runtime."""
    if (not isinstance(output, Path) or not output.is_absolute() or ".." in output.parts
            or len(os.fsencode(output)) > 70 or not re.fullmatch(r"/[A-Za-z0-9._/-]+", str(output))
            or not isinstance(tools, Path) or not tools.is_absolute()
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 30 <= timeout <= 600):
        raise QualificationError("explicit-short-run-path-and-bounded-time-required")
    recipe = None
    mission = _mission_build is not None
    if mission:
        from .mission_recipe import authorize, descriptor
        if _python_probe is not None:
            raise QualificationError('one-closed-recipe-required')
        authorize(_mission_build)
        recipe = descriptor(_mission_build['recipe'])
    if _python_probe is not None:
        from .runtime_recipe import descriptor
        if not isinstance(_python_probe, dict) or set(_python_probe) != {"recipe", "acquired", "wheel"}:
            raise QualificationError("closed-runtime-probe-input-required")
        recipe = descriptor(_python_probe["recipe"])
    requirements = prerequisites(expected_uid)
    parent = _open_directory(output.parent)
    try:
        os.mkdir(output.name, 0o700, dir_fd=parent)
    finally:
        os.close(parent)
    state = {"schema_version": 1, "kind": "owned-rootless-executor-processes", "run_id": str(uuid.uuid4()),
             "directory": _root_identity(output), "boot_id": _boot_id(),
             "processes": {}}
    if recipe is not None:
        state["recipe"] = recipe
    _atomic(output, "process-state.json", state)
    report = {"schema_version": 1, "kind": "rootless-executor-qualification", "run_id": state["run_id"],
              "decision": "unknown", "started_at": _now(), "scope": "offline-scratch-tool-execution",
              "canonical_demo": "not-run", "mission_build": "not-run", "registry_trust": "not-tested",
              "prerequisites": requirements, "tools": {}, "gaps": [], "harness_sha256": _sha(Path(__file__).read_bytes()),
              "python_version": platform.python_version()}
    if recipe is not None:
        report["scope"] = "local-mission-image-build" if mission else "mission-python-runtime-probe"
        report["registry_trust"] = "separate-calling-issuer-check"
        report["base"] = recipe
    deadline, processes = time.monotonic() + timeout, []
    previous_handlers = {}
    try:
        for name in RUNTIME_NAMES:
            (output / name).mkdir(mode=0o700)
        lock_record = _snapshot(ROOT / "supplychain.lock.json", output / "supplychain.lock.json", 64_000, deadline=deadline)
        lock = _json((output / "supplychain.lock.json").read_bytes())
        architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine())
        if architecture is None or lock["builder"]["version"] != "0.34.1" or lock["rootlesskit"]["version"] != "3.3.0":
            raise QualificationError("locked-toolchain-platform-required")
        if recipe is not None and recipe["architecture"] != architecture:
            raise QualificationError("runtime-probe-host-platform-mismatch")
        expected = dict(lock["builder"]["platforms"]["linux-" + architecture]["binaries"])
        rk = lock["rootlesskit"]["platforms"]["linux-" + architecture]
        expected["rootlesskit"] = {"sha256": rk["binary_sha256"], "bytes": rk["binary_bytes"]}
        if set(expected) != {"buildctl", "buildkitd", "buildkit-runc", "rootlesskit"}:
            raise QualificationError("locked-toolchain-shape")
        for name, wanted in expected.items():
            receipt = _snapshot(tools / name, output / "tools" / name, 200_000_000, deadline=deadline)
            if receipt != wanted:
                raise QualificationError("toolchain-binary-mismatch")
            (output / "tools" / name).chmod(0o500)
            report["tools"][name] = receipt
        report["tool_lock"] = lock_record
        if recipe is None:
            _snapshot(output / "tools/rootlesskit", output / "fixture/probe", 32_000_000, deadline=deadline)
            (output / "fixture/probe").chmod(0o500)
            fixture = b'FROM scratch\nCOPY probe /probe\nRUN --network=none ["/probe", "--version"]\n'
            (output / "fixture/Dockerfile").write_bytes(fixture)
            (output / "fixture/Dockerfile").chmod(0o600)
        elif not mission:
            from .runtime_recipe import prepare
            report["runtime_inputs"] = prepare(output, recipe, acquired=_python_probe["acquired"],
                wheel=_python_probe["wheel"], deadline=deadline)
            fixture = (output / "fixture/Dockerfile").read_bytes()
        else:
            from .mission_recipe import prepare, MAX_IMAGE as MISSION_MAX_IMAGE
            report['mission_inputs'] = prepare(output, _mission_build, deadline)
            fixture = (output / 'fixture/Dockerfile').read_bytes()
        image_limit = MISSION_MAX_IMAGE if mission else MAX_IMAGE
        # Empty explicit config avoids reading candidate or user's daemon config.
        (output / "buildkitd.toml").write_text('[worker.oci]\n  max-parallelism = 1\n[cdi]\n  disabled = true\n')
        (output / "buildkitd.toml").chmod(0o600)
        (output / "docker/config.json").write_text('{"auths":{}}')
        (output / "docker/config.json").chmod(0o600)
        commands, env = _commands(output, recipe), _environment(output)
        def interrupted(signum, frame):
            raise Interrupted()
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, interrupted)
        _atomic(output, "intent.json", {"operation": "start-owned-offline-rootless-probe", "run_id": state["run_id"],
            "fixture_sha256": _sha(fixture), "tool_lock_sha256": lock_record["sha256"]})
        with (output / "daemon.log").open("xb") as daemon_log:
            os.chmod(output / "daemon.log", 0o600)
            if mission:
                authorize(_mission_build)
            daemon = subprocess.Popen(commands["rootlesskit"], cwd=output, env=env, stdin=subprocess.DEVNULL,
                stdout=daemon_log, stderr=daemon_log, start_new_session=True, umask=0o077)
            processes.append(daemon)
            _remember(output, state, "rootlesskit", daemon.pid, commands["rootlesskit"])
            socket = output / "buildkit.sock"
            while not socket.exists():
                _remaining(deadline)
                _guard(output, state)
                if daemon.poll() is not None:
                    raise QualificationError("rootless-daemon-start-failed")
                if (output / "daemon.log").stat().st_size > MAX_LOG:
                    raise QualificationError("daemon-log-limit")
                time.sleep(0.1)
            value = socket.lstat()
            if not stat.S_ISSOCK(value.st_mode) or value.st_uid != expected_uid:
                raise QualificationError("owned-daemon-socket-required")
            state["socket"] = {"device": value.st_dev, "inode": value.st_ino, "uid": value.st_uid}
            _atomic(output, "process-state.json", state)
            os.chmod(socket, 0o600, follow_symlinks=False)
            daemon_identity = _remember_daemon_peer(output, state, commands["buildkitd"], deadline)
            worker = inspect_rootless_worker(socket_path=socket, expected_uid=expected_uid,
                expected_device=value.st_dev, expected_inode=value.st_ino, buildctl=output / "tools/buildctl",
                architecture=architecture, timeout=min(60, _remaining(deadline)))
            if worker["peer"]["pid"] != daemon_identity["pid"] or not _same_process(daemon_identity):
                raise QualificationError("daemon-peer-changed-during-admission")
            report["worker_before"] = worker
            with (_private_image_export(output), (output / "client.stdout").open("xb") as stdout,
                  (output / "client.stderr").open("xb") as stderr):
                os.chmod(output / "client.stdout", 0o600)
                os.chmod(output / "client.stderr", 0o600)
                if mission:
                    authorize(_mission_build)
                client = subprocess.Popen(commands["buildctl"], cwd=output, env=env, stdin=subprocess.DEVNULL,
                    stdout=stdout, stderr=stderr, start_new_session=True, umask=0o077)
                processes.append(client)
                _remember(output, state, "buildctl", client.pid, commands["buildctl"])
                code = _watch(output, client, state, deadline,
                    {"client.stdout": MAX_LOG, "client.stderr": MAX_LOG, "daemon.log": MAX_LOG,
                     "image.oci.tar": image_limit, "build-metadata.json": 1_000_000})
            report["client"] = {"exit_code": code, "stdout": _log_receipt(output / "client.stdout"),
                                "stderr": _log_receipt(output / "client.stderr")}
            if code:
                raise QualificationError("scratch-executor-build-failed" if recipe is None else
                    "mission-image-build-failed" if mission else "base-runtime-probe-build-failed")
            text, _ = _regular(output / "client.stderr", MAX_LOG)
            marker = None if mission else (b"rootlesskit version 3.3.0" if recipe is None else
                      ("DEVSECOPS_RUNTIME_PROBE " + recipe["nonce"]).encode())
            if marker is not None and not re.search(rb"(?m)^#\d+\s+[0-9.]+\s+" + re.escape(marker) + rb"\s*$", text):
                raise QualificationError("actual-run-output-unverified")
            image = _image_receipt(output / "image.oci.tar", image_limit, deadline)
            with tarfile.open(output / "image.oci.tar", "r:", tarinfo=_BoundedTarInfo) as archive:
                member = archive.getmember("index.json")
                if member.size > 64_000 or not member.isfile():
                    raise QualificationError("output-index-format")
                index = _json(archive.extractfile(member).read(64_001))
            if len(index.get("manifests", [])) != 1:
                raise QualificationError("output-platform-count")
            digest = index["manifests"][0]["digest"]
            mapping = _oci_archive(output / "image.oci.tar", digest, architecture, deadline=deadline)
            metadata = _private_export_metadata(output)
            if _json(metadata).get("containerimage.digest") != digest:
                raise QualificationError("build-export-digest-mismatch")
            if recipe is not None and not mission:
                from .runtime_recipe import proof
                report["runtime_proof"] = proof(output / "image.oci.tar", digest, recipe,
                    report["runtime_inputs"]["dependency_version"], deadline)
            elif mission:
                from .mission_recipe import inspect_output
                report['image_lineage'] = inspect_output(output / 'image.oci.tar', digest,
                    base_layout=output / 'base/layout', base_manifest_digest=recipe['manifest_digest'],
                    context=report['mission_inputs']['context'], deadline=deadline)
            if _image_receipt(output / 'image.oci.tar', image_limit, deadline) != image:
                raise QualificationError('output-image-changed')
            report["artifact"] = {"path": "image.oci.tar", **image, **mapping,
                "metadata_sha256": _sha(metadata), "runtime_output": marker.decode() if marker is not None else None, "no_cache": True}
            after = inspect_rootless_worker(socket_path=socket, expected_uid=expected_uid,
                expected_device=value.st_dev, expected_inode=value.st_ino, buildctl=output / "tools/buildctl",
                architecture=architecture, timeout=min(60, _remaining(deadline)))
            if after["peer"] != worker["peer"] or after["worker"] != worker["worker"]:
                raise QualificationError("worker-changed-during-build")
            report["worker_after"] = after
            if prerequisites(expected_uid) != requirements:
                raise QualificationError("guest-user-authority-changed")
            report["execution"] = "verified"
    except (Interrupted, KeyboardInterrupt):
        report["gaps"].append({"code": "qualification-interrupted"})
    except (OSError, ValueError, TypeError, KeyError, IndexError, tarfile.TarError) as error:
        report["gaps"].append({"code": error.code if isinstance(error, QualificationError) else "qualification-incomplete"})
    finally:
        for signum in previous_handlers:
            signal.signal(signum, signal.SIG_IGN)
        # Popen references remain valid even if a state write failed immediately
        # after spawn. Terminate those children before independent reconciliation.
        for process in reversed(processes):
            if process.poll() is None and process.pid not in {v["pid"] for v in state["processes"].values()}:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    report["gaps"].append({"code": "unrecorded-owned-process-wait-timeout"})
        try:
            report["cleanup"] = cleanup(output)
        except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
            report["cleanup"] = {"decision": "unknown", "code": error.code if isinstance(error, QualificationError) else "cleanup-incomplete"}
        for process in processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                report["cleanup"] = {"decision": "unknown", "code": "owned-process-still-running"}
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    report["finished_at"] = _now()
    if mission:
        try:
            authorize(_mission_build)
        except (ValueError, TypeError, KeyError):
            report['gaps'].append({'code': 'mission-base-authority-expired-or-changed'})
    # Cleanup keeps its independent safety budget, but may not extend the
    # caller's deadline for a successful qualification.
    if time.monotonic() >= deadline:
        report["gaps"].append({"code": "qualification-deadline"})
    if report.get("execution") == "verified" and report["cleanup"]["decision"] == "pass" and not report["gaps"]:
        report["decision"] = "pass"
    directory = _open_directory(output)
    try:
        current = os.fstat(directory)
        if {"device": current.st_dev, "inode": current.st_ino, "uid": current.st_uid} != state["directory"]:
            raise QualificationError("run-directory-changed")
        _guard(output, state)
        try:
            _atomic(output, "qualification.json", report, directory=directory)
            _guard(output, state)
            if report["decision"] == "pass":
                _remaining(deadline)
                if mission:
                    authorize(_mission_build)
        except BaseException:
            # Retained directory authority permits withdrawing only this run's
            # receipt even if the route was swapped during publication.
            try:
                os.unlink("qualification.json", dir_fd=directory)
            except FileNotFoundError:
                pass
            os.fsync(directory)
            raise
    finally:
        os.close(directory)
    return report
