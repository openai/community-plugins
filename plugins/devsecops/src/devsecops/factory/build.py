"""Frozen mission build inputs and independent Linux rootless builder identity.

Preparation is not build authorization. In particular, a JSON qualification
flag, a lock, or a locally self-sealed receipt cannot authorize a registry push.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import platform
import re
import socket
import stat
import struct
import tempfile
import time

from devsecops.adapters.artifacts import _canonical, _hash, _open_directory, _output, _write
from devsecops.adapters.http import _json
from devsecops.adapters.oci import _digest, _repository
from devsecops.adapters.signatures import _snapshot
from devsecops.profiles import relative_path
from devsecops.snapshot import freeze_commit, resolve_ref, git
from devsecops.process import run

ROOT = Path(__file__).resolve().parents[3]
MISSION = ROOT/"demo/software-factory/apps/mission-status"
FILES = ("Dockerfile", "app.py", "probe.py", "requirements.txt", "dependency-lock.json")


def _base(repository, digest, architecture):
    prefix = "registry1.dso.mil/ironbank/"
    if (not isinstance(repository, str) or not repository.startswith(prefix) or not _repository(repository[len(prefix):])
            or not _digest(digest) or architecture not in {"amd64", "arm64"}):
        raise ValueError("An exact Iron Bank base repository, SHA-256 digest and Linux architecture are required")


def prepare_mission_build(*, repo, revision, source_path, wheel, output, base_repository, base_digest, architecture, timeout=None):
    """Materialize one exact Git commit plus the independently locked wheel.

    The Dockerfile, dependency lock and hashed requirements must match this
    installed plugin's fixed recipe. App/probe bytes are copied from Git without
    checkout filters or hooks. The result carries no claim of F0 qualification.
    """
    _base(base_repository, base_digest, architecture)
    if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 600):
        raise ValueError('Build preparation requires a bounded deadline')
    deadline = None if timeout is None else time.monotonic() + timeout
    def checkpoint():
        if deadline is not None:
            _remaining(deadline)
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
        raise ValueError("Build input revision must be a full immutable Git commit")
    source_path = relative_path(source_path)
    if not isinstance(repo, Path) or not repo.is_absolute() or not isinstance(wheel, Path) or not wheel.is_absolute():
        raise ValueError("Explicit absolute repository and wheel paths are required")
    opened = _open_directory(repo)
    os.close(opened)
    if Path(git(repo, 'rev-parse', '--show-toplevel', deadline=deadline).decode().strip()) != repo:
        raise ValueError("Repository input must be the exact Git worktree root")
    if resolve_ref(repo, revision, deadline=deadline) != revision:
        raise ValueError("Build input commit does not resolve exactly")
    with tempfile.TemporaryDirectory(prefix="devsecops-build-input-") as directory:
        scratch = Path(directory).resolve()
        source = scratch/"source"
        source.mkdir(mode=0o700)
        snapshot = freeze_commit(repo, revision, [source_path+"/"+name for name in FILES], source, deadline=deadline)
        selected = source/source_path
        if not all((selected/name).is_file() for name in FILES):
            raise ValueError("The exact mission build files are required in the selected commit")
        # Trusted installed recipe is compared byte-for-byte, never executed from
        # a candidate CI/Docker configuration supplied by the scanned repository.
        for name in ("Dockerfile", "requirements.txt", "dependency-lock.json"):
            _snapshot(MISSION/name, scratch/("trusted-"+name), 1_000_000, deadline=deadline)
            if (selected/name).read_bytes() != (scratch/("trusted-"+name)).read_bytes():
                raise ValueError("Candidate build recipe or dependency selection differs from the installed recipe")
        dependency = _json((scratch/"trusted-dependency-lock.json").read_bytes())
        wheel_receipt = _snapshot(wheel, scratch/"dependency.whl", 1_000_000, deadline=deadline)
        if wheel_receipt != {"sha256": dependency["sha256"], "bytes": dependency["bytes"]}:
            raise ValueError("Offline wheel does not match the installed dependency lock")
        filename = dependency["filename"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.whl", filename):
            raise ValueError("Installed wheel filename is unsupported")
        inputs = {name: (selected/name).read_bytes() for name in FILES}
        inputs["wheelhouse/"+filename] = (scratch/"dependency.whl").read_bytes()
        files = [{"path": name, "sha256": _hash(raw), "bytes": len(raw)} for name, raw in sorted(inputs.items())]
        receipt = {"schema_version": 1, "kind": "prepared-mission-build", "execution_status": "partial", "decision": "unknown",
            "observed_at": datetime.now(timezone.utc).isoformat(), "source": {"commit": revision, "path": source_path, "snapshot": snapshot},
            "context": {"sha256": _hash(_canonical(files)), "hash_scheme": "devsecops-build-files-sha256-v1", "files": files},
            "base": {"repository": base_repository, "digest": base_digest, "os": "linux", "architecture": architecture},
            "dependency": {"sha256": wheel_receipt["sha256"], "bytes": wheel_receipt["bytes"], "filename": filename},
            "operations": {"build": "not-started", "push": "not-started", "scan": "not-started", "sign": "not-started", "promote": "not-started"},
            "gaps": [{"code": code, "required": True} for code in ("base-qualification-unverified", "rootless-builder-unqualified",
                "registry-credentials-unverified", "registry-readback-unverified", "artifact-scan-unavailable", "artifact-signature-unavailable", "promotion-not-approved")]}
        with _output(output) as outdir:
            os.mkdir("context", mode=0o700, dir_fd=outdir)
            context = os.open("context", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=outdir)
            try:
                os.mkdir("wheelhouse", mode=0o700, dir_fd=context)
                wheels = os.open("wheelhouse", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=context)
                try:
                    for name, raw in inputs.items():
                        checkpoint()
                        _write(wheels if name.startswith("wheelhouse/") else context, name.split("/")[-1], raw)
                finally:
                    os.close(wheels)
            finally:
                os.close(context)
            _write(outdir, "prepared.json", _canonical(receipt)+b"\n")
            os.fsync(outdir)
            try:
                checkpoint()
                current = _open_directory(output)
                try:
                    if (os.fstat(current).st_dev, os.fstat(current).st_ino) != (os.fstat(outdir).st_dev, os.fstat(outdir).st_ino):
                        raise ValueError("Build output directory identity changed")
                finally:
                    os.close(current)
            except (OSError, ValueError):
                os.unlink("prepared.json", dir_fd=outdir)
                raise ValueError("Build output directory identity changed") from None
        return receipt


def _proc_file(pid, name, limit=64_000):
    fd = os.open(f"/proc/{pid}/{name}", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(limit+1)
    if len(raw) > limit:
        raise ValueError("Builder process metadata exceeds its limit")
    return raw


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("Builder inspection deadline exceeded")
    return remaining


def _deadline(timeout):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("Builder inspection timeout must be between zero and 120 seconds")
    return time.monotonic() + timeout


def _namespace(pid, name):
    # Kernel namespace links, never a supplied filesystem link.
    info = os.stat(f"/proc/{pid}/ns/{name}")
    return {"device": info.st_dev, "inode": info.st_ino}


def _socket_parent(path, expected_uid):
    """Reject writable ancestor routes before using a pathname-based RPC client."""
    if (not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
            or any(character in str(path) for character in "%?#\\\r\n\x00")
            or len(os.fsencode(path)) > 100):
        raise ValueError("Builder socket path is not an unambiguous bounded Unix endpoint")
    current = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in path.parent.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
            info = os.fstat(current)
            root_sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in {0, expected_uid} or info.st_mode & 0o022 and not root_sticky:
                raise ValueError("Builder socket ancestor is writable by another principal")
        info = os.fstat(current)
        if info.st_uid != expected_uid or info.st_mode & 0o022:
            raise ValueError("Builder socket parent must be owned and protected by its operator")
        result, current = current, None
        return result
    finally:
        if current is not None:
            os.close(current)


def inspect_rootless_builder(*, socket_path, expected_uid, expected_device, expected_inode, timeout=15):
    """Check an explicitly authorized socket against Linux kernel peer identity.

    This is read-only qualification evidence, not build authority or a claim of
    registry TLS, worker readiness, runtime support or network isolation.
    """
    deadline = _deadline(timeout)
    if platform.system() != "Linux" or not hasattr(socket, "SO_PEERCRED"):
        raise ValueError("Rootless builder qualification requires the Linux peer-credential API")
    if (type(expected_uid) is not int or expected_uid <= 0 or expected_uid != os.getuid()
            or type(expected_device) is not int or type(expected_inode) is not int or expected_inode <= 0
            or not isinstance(socket_path, Path) or not socket_path.is_absolute() or ".." in socket_path.parts):
        raise ValueError("An exact authorized non-root socket identity is required")
    parent = _socket_parent(socket_path, expected_uid)
    try:
        before = os.stat(socket_path.name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISSOCK(before.st_mode) or before.st_uid != expected_uid or before.st_mode & 0o022
                or (before.st_dev, before.st_ino) != (expected_device, expected_inode)):
            raise ValueError("Builder socket owner, mode or identity differs")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(min(3, _remaining(deadline)))
            client.connect(str(socket_path))
            pid, uid, _ = struct.unpack("3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if pid <= 0 or uid != expected_uid:
                raise ValueError("Builder peer owner differs")
            start = _proc_file(pid, "stat")
            command = _proc_file(pid, "cmdline").split(b"\0")
            uidmap = [tuple(int(x) for x in line.split()) for line in _proc_file(pid, "uid_map").splitlines()]
            if (b"--rootless" not in command or any(b"no-process-sandbox" in arg or b"allow-insecure-entitlement" in arg for arg in command)
                    or not uidmap or uidmap[0] != (0, expected_uid, 1) or any(len(row) != 3 or row[1] == 0 for row in uidmap)):
                raise ValueError("Builder does not satisfy explicit rootless/process-sandbox requirements")
            user_namespace = _namespace(pid, "user")
            network_namespace = _namespace(pid, "net")
            if user_namespace == _namespace("self", "user"):
                raise ValueError("Builder is not in a distinct user namespace")
            lock = _json((ROOT/"supplychain.lock.json").read_bytes())
            arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
            expected = lock["builder"]["platforms"]["linux-"+arch]["binaries"]["buildkitd"]
            # /proc/<peer>/exe is a kernel-managed symlink; this narrowly admitted
            # dereference verifies the actual running binary, not a caller path.
            with open(f"/proc/{pid}/exe", "rb") as stream:
                executable_identity = os.fstat(stream.fileno())
                h, size = hashlib.sha256(), 0
                while raw := stream.read(1_000_000):
                    _remaining(deadline)
                    size += len(raw)
                    if size > 200_000_000:
                        raise ValueError("Builder executable size exceeds its limit")
                    h.update(raw)
            if {"sha256": h.hexdigest(), "bytes": size} != expected:
                raise ValueError("Builder daemon is not the pinned executable")
            # Only process start time is stable in /proc/stat; CPU counters vary.
            start_ticks = start.rsplit(b")", 1)[1].split()[19]
            if _proc_file(pid, "stat").rsplit(b")", 1)[1].split()[19] != start_ticks:
                raise ValueError("Builder process identity changed")
            executable_after = os.stat(f"/proc/{pid}/exe")
            if ((executable_after.st_dev, executable_after.st_ino) != (executable_identity.st_dev, executable_identity.st_ino)
                    or _proc_file(pid, "cmdline").split(b"\0") != command
                    or [tuple(int(x) for x in line.split()) for line in _proc_file(pid, "uid_map").splitlines()] != uidmap
                    or _namespace(pid, "user") != user_namespace or _namespace(pid, "net") != network_namespace):
                raise ValueError("Builder process authority changed")
        after = os.stat(socket_path.name, dir_fd=parent, follow_symlinks=False)
        current_parent = _open_directory(socket_path.parent)
        try:
            if (os.fstat(current_parent).st_dev, os.fstat(current_parent).st_ino) != (os.fstat(parent).st_dev, os.fstat(parent).st_ino):
                raise ValueError("Builder socket directory changed")
        finally:
            os.close(current_parent)
        if (before.st_dev, before.st_ino, before.st_uid, before.st_mode) != (after.st_dev, after.st_ino, after.st_uid, after.st_mode):
            raise ValueError("Builder socket identity changed")
        _remaining(deadline)
        return {"schema_version": 1, "kind": "rootless-builder-peer", "uid": uid, "pid": pid,
            "process_start_ticks": int(start_ticks), "socket_device": before.st_dev, "socket_inode": before.st_ino,
            "buildkitd": expected, "version": lock["builder"]["version"], "rootless_peer": "verified",
            "user_namespace": user_namespace, "network_namespace": network_namespace,
            "network_namespace_distinct_from_caller": network_namespace != _namespace("self", "net"),
            "worker_readiness": "not-checked", "registry_tls": "not-checked", "network_isolation": "not-checked",
            "build_authorization": "not-granted"}
    finally:
        os.close(parent)


def inspect_rootless_worker(*, socket_path, expected_uid, expected_device, expected_inode,
                            buildctl, architecture, timeout=30):
    """Query the pinned daemon's actual worker RPC without executing a build.

    Distinct kernel namespaces and a process-sandboxed OCI worker are required.
    This does not prove egress policy, registry trust or permission to build.
    """
    deadline = _deadline(timeout)
    if architecture not in {"amd64", "arm64"}:
        raise ValueError("A supported Linux worker architecture is required")
    identity = dict(socket_path=socket_path, expected_uid=expected_uid,
                    expected_device=expected_device, expected_inode=expected_inode)
    before = inspect_rootless_builder(**identity, timeout=_remaining(deadline))
    if not before["network_namespace_distinct_from_caller"]:
        raise ValueError("Worker must have a network namespace distinct from the caller")
    lock = _json((ROOT/"supplychain.lock.json").read_bytes())["builder"]
    host_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    expected = lock["platforms"]["linux-"+host_arch]["binaries"]["buildctl"]
    commands = []
    with tempfile.TemporaryDirectory(prefix="devsecops-worker-inspection-") as temporary:
        scratch = Path(temporary).resolve()
        binary = scratch/"buildctl"
        if _snapshot(buildctl, binary, 200_000_000, deadline=deadline) != expected:
            raise ValueError("BuildKit client is not the pinned executable")
        binary.chmod(0o500)
        for name in ("home", "config", "cache", "docker", "empty-bin"):
            (scratch/name).mkdir(mode=0o700)
        (scratch/"docker/config.json").write_text('{"auths":{}}')
        env = {"PATH": str(scratch/"empty-bin"), "HOME": str(scratch/"home"),
               "XDG_CONFIG_HOME": str(scratch/"config"), "XDG_CACHE_HOME": str(scratch/"cache"),
               "DOCKER_CONFIG": str(scratch/"docker"), "OTEL_SDK_DISABLED": "true"}
        def execute(arguments):
            code, out, _, receipt = run([str(binary), *arguments], cwd=scratch,
                timeout=_remaining(deadline), limit=1_000_000, env=env)
            commands.append(receipt)
            if code:
                raise ValueError("Pinned BuildKit worker query failed")
            return out
        version = execute(["--version"])
        version_match = re.fullmatch(rb"buildctl github\.com/moby/buildkit v0\.34\.1 ([0-9a-f]{40})\s*", version)
        if not version_match:
            raise ValueError("Pinned BuildKit client version differs")
        raw = execute(["--addr", "unix://"+str(socket_path), "debug", "workers", "--format", "{{json .}}"])
        wrapped = _json(b'{"workers":'+raw+b'}')
        if set(wrapped) != {"workers"}:
            raise ValueError("Worker response must be one JSON array")
        workers = wrapped["workers"]
        if not isinstance(workers, list) or len(workers) != 1 or not isinstance(workers[0], dict):
            raise ValueError("Exactly one qualified worker is required")
        worker = workers[0]
        labels, worker_version = worker.get("labels"), worker.get("buildkitVersion")
        prefix = "org.mobyproject.buildkit.worker."
        if (not isinstance(labels, dict) or labels.get(prefix+"executor") != "oci"
                or labels.get(prefix+"oci.process-mode") != "sandbox"
                or labels.get(prefix+"snapshotter") not in {"overlayfs", "native", "fuse-overlayfs"}
                or not isinstance(worker_version, dict) or worker_version.get("package") != "github.com/moby/buildkit"
                or worker_version.get("version") != "v"+lock["version"]
                or worker_version.get("revision") != version_match.group(1).decode()
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", worker.get("id", ""))
                or worker.get("cdiDevices") not in (None, [])):
            raise ValueError("Worker implementation, sandbox or device configuration is unsupported")
        platforms = worker.get("platforms")
        if not isinstance(platforms, list) or not any(isinstance(p, dict) and p.get("os") == "linux"
                and p.get("architecture") == architecture
                and p.get("variant", "") in ({"", "v8"} if architecture == "arm64" else {""}) for p in platforms):
            raise ValueError("Worker does not report the required platform")
    after = inspect_rootless_builder(**identity, timeout=_remaining(deadline))
    if before != after:
        raise ValueError("Builder identity changed across worker query")
    return {"schema_version": 1, "kind": "rootless-builder-worker", "peer": after,
        "worker": {"id": worker["id"], "architecture": architecture, "os": "linux",
                   "version": {key: worker_version[key] for key in ("package", "version", "revision")},
                   "executor": "oci", "process_mode": "sandbox", "snapshotter": labels[prefix+"snapshotter"]},
        "worker_response_sha256": _hash(raw), "buildctl": expected, "commands": commands,
        "worker_readiness": "rpc-verified", "runtime_execution": "not-tested",
        "registry_tls": "not-tested", "egress_policy": "not-tested", "build_authorization": "not-granted"}
