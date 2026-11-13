"""Closed, peer-scoped runner VM TLS relays; source and original identity bound.

No generic proxy, redirects, alternate origins, DNS action at import, root SSH,
network joins, or restored capabilities from JSON. External routes are explicit
selection only; the original resident operation must independently admit them.
"""

from __future__ import annotations
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import selectors
import shlex
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from types import ModuleType

GUEST_SOURCE_BYTES = None
_AUDIT_BUDGET = threading.local()
CONTEXT_TYPE = None
ROLES = {
    "gitlab": ("10.0.2.100", 8444),
    "registry": ("10.0.2.101", 8443),
    "registry1": ("10.0.2.102", 443),
    "p1docs": ("10.0.2.103", 443),
    "registry1-storage": ("10.0.2.104", 443),
}
EXTERNAL = {"registry1": "https://registry1.dso.mil", "p1docs": "https://p1docs.dso.mil",
            "registry1-storage": "https://ib-prod-harbor-storage.s3.us-gov-west-1.amazonaws.com"}


def client_hello_origin(raw):
    """One bounded TLS ClientHello, exactly one ASCII SNI host, no plaintext."""
    if type(raw) is not bytes or not 9 <= len(raw) <= 65536:
        raise ValueError("Bounded TLS ClientHello required")
    body = bytearray()
    cursor = 0
    while cursor < len(raw):
        if (
            cursor + 5 > len(raw)
            or raw[cursor] != 22
            or raw[cursor + 1 : cursor + 3] not in (b"\x03\x01", b"\x03\x02", b"\x03\x03")
        ):
            raise ValueError("Only an initial TLS handshake record is admitted")
        length = int.from_bytes(raw[cursor + 3 : cursor + 5], "big")
        if not 1 <= length <= 18432 or cursor + 5 + length > len(raw):
            raise ValueError("Truncated or oversized TLS record")
        body.extend(raw[cursor + 5 : cursor + 5 + length])
        cursor += 5 + length
    if len(body) < 4 or body[0] != 1 or int.from_bytes(body[1:4], "big") != len(body) - 4:
        raise ValueError("Exactly one complete ClientHello is required")
    value = bytes(body[4:])
    offset = 34
    if len(value) < 35 or value[:2] != b"\x03\x03":
        raise ValueError("Only TLS1.2+ ClientHello is supported")

    def take(size):
        nonlocal offset
        if size < 0 or offset + size > len(value):
            raise ValueError("Truncated ClientHello field")
        out = value[offset : offset + size]
        offset += size
        return out

    take(take(1)[0])
    ciphers = int.from_bytes(take(2), "big")
    if ciphers < 2 or ciphers % 2:
        raise ValueError("Invalid TLS cipher list")
    take(ciphers)
    compression = take(1)[0]
    if compression != 1 or take(compression) != b"\x00":
        raise ValueError("TLS compression is not admitted")
    extensions = int.from_bytes(take(2), "big")
    if extensions != len(value) - offset:
        raise ValueError("TLS extension length differs")
    host = None
    seen = set()
    while offset < len(value):
        kind = int.from_bytes(take(2), "big")
        size = int.from_bytes(take(2), "big")
        data = take(size)
        if kind in seen:
            raise ValueError("Duplicate TLS extension")
        seen.add(kind)
        if kind == 0:
            if (
                len(data) < 5
                or int.from_bytes(data[:2], "big") != len(data) - 2
                or data[2] != 0
                or int.from_bytes(data[3:5], "big") != len(data) - 5
            ):
                raise ValueError("Exactly one SNI hostname required")
            host = data[5:].decode("ascii")
    if host is None or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host) or ".." in host:
        raise ValueError("Canonical non-wildcard SNI hostname required")
    return host


def receive_client_hello(client, *, expected_host, deadline):
    raw = bytearray()
    body = bytearray()

    def receive(count):
        out = bytearray()
        while len(out) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("TLS initial handshake deadline exhausted")
            client.settimeout(remaining)
            chunk = client.recv(count - len(out))
            if not chunk:
                raise ValueError("TLS initial handshake ended early")
            out.extend(chunk)
        return bytes(out)

    while len(raw) < 65536:
        header = receive(5)
        if header[0] != 22 or header[1:3] not in (b"\x03\x01", b"\x03\x02", b"\x03\x03"):
            raise ValueError("TLS handshake required before route forwarding")
        length = int.from_bytes(header[3:5], "big")
        if not 1 <= length <= 18432 or len(raw) + 5 + length > 65536:
            raise ValueError("TLS initial record exceeds bound")
        data = receive(length)
        raw.extend(header + data)
        body.extend(data)
        if len(body) >= 4 and len(body) >= 4 + int.from_bytes(body[1:4], "big"):
            if client_hello_origin(bytes(raw)) != expected_host or time.monotonic() >= deadline:
                raise ValueError("TLS route SNI differs from sole selected origin")
            return bytes(raw)
    raise ValueError("TLS initial handshake exceeds bound")


def external_addresses(role):
    """Executed only inside an admitted live action; never during selection."""
    if role not in EXTERNAL:
        raise ValueError("Unknown external route")
    host = EXTERNAL[role].removeprefix("https://")
    # The bounded fixed subprocess prevents libc resolution from escaping the
    # caller's timeout. It cannot use a caller URL, wildcard, proxy or alternate.
    raw = _docker_inspection(["getent", "ahostsv4", host]).decode("ascii")
    result = sorted({line.split()[0] for line in raw.splitlines() if line.split()})
    if not 1 <= len(result) <= 16 or any(not ipaddress.IPv4Address(v).is_global for v in result):
        raise ValueError("Selected external origin did not resolve to bounded public IPv4 addresses")
    return result


def _guest_module():
    if type(GUEST_SOURCE_BYTES) is not bytes or len(GUEST_SOURCE_BYTES) > 131072:
        raise ValueError("Original fixed guest-route source is missing")
    value = ModuleType("runner_vm_guest_routes")
    exec(compile(GUEST_SOURCE_BYTES, "runner_vm_guest_routes", "exec"), value.__dict__)
    return value


def _validate(packet, scope):
    if CONTEXT_TYPE is None or type(scope) is not CONTEXT_TYPE:
        raise ValueError("Original runner-VM remote child scope is required")
    guest = _guest_module()
    if type(packet) is not dict or set(packet) != {"action", "guest", "targets", "guest_source_sha256"}:
        raise ValueError("Closed runner route child packet required")
    selected = guest.validate_packet(packet["guest"])
    expected_action = {"down-host": "down", "reconcile-host": "reconcile"}.get(
        packet["action"], packet["action"]
    )
    if (
        packet["action"] not in {"up", "down", "observe", "reconcile", "down-host", "reconcile-host"}
        or expected_action != selected["action"]
    ):
        raise ValueError("Original route dispatcher and guest action differ")
    if (
        packet["guest_source_sha256"] != sha(GUEST_SOURCE_BYTES)
        or selected["runner_vm"] != scope.identity()
        or selected["run_id"] != scope.packet["run_id"]
        or selected["operation_id"] != scope.packet["operation_id"]
    ):
        raise ValueError("Runner route packet differs from original source/VM operation")
    expected = [EXTERNAL[r] for r in EXTERNAL if r in selected["services"]]
    if expected != scope.recipe["egress"]["external_origins"]:
        raise ValueError("External route differs from the explicit frozen VM selection")
    policy_ref = scope.recipe["egress"].get("blob_download_policy")
    policy_selection = selected.get("blob_download_policy")
    if (policy_ref is None) != (policy_selection is None) or policy_ref is not None and (
        policy_selection["sha256"] != policy_ref["sha256"]
    ):
        raise ValueError("Storage route policy differs from original VM selection")
    for role in EXTERNAL:
        if (
            role in selected["services"]
            and selected["services"][role]["ca_sha256"]
            != scope.recipe["egress"]["external_ca_bundle"]["sha256"]
        ):
            raise ValueError("External route CA differs from its original selected bundle")
    if type(packet["targets"]) is not dict or set(packet["targets"]) != {"gitlab", "registry"}:
        raise ValueError("Exactly two original owned service endpoints required")
    for role, target in packet["targets"].items():
        if (
            type(target) is not dict
            or set(target) != {"container", "network", "origin", "port"}
            or target["origin"] != selected["services"][role]["origin"]
            or target["port"] != ROLES[role][1]
        ):
            raise ValueError("Owned service target differs from selected TLS route")
        address(inspect(target["container"]), inspect(target["network"]))
    if packet["action"] not in {"down-host", "reconcile-host"}:
        scope.observe_guest()
    return selected


def _root_guard(config):
    vm = config["runner_vm"]
    root = Path(config["vm_root"])
    if root.resolve() != root or root.is_symlink():
        raise ValueError("Original VM root route changed")
    st = root.stat()
    if (
        st.st_uid != os.getuid()
        or st.st_mode & 0o077
        or {"device": st.st_dev, "inode": st.st_ino} != vm["root_identity"]
    ):
        raise ValueError("Original VM root identity changed")
    owner = json.loads(read(root / "owner.json"))
    disk = root / "data/probe.qcow2"
    info = disk.lstat()
    if (
        owner["binding"] != vm["binding"]
        or owner["run_id"] != config["run_id"]
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or {"device": info.st_dev, "inode": info.st_ino} != vm["disk_identity"]
    ):
        raise ValueError("Original VM disk/owner changed")
    runtime = inspect(config["runtime"])
    network = inspect(config["network"])
    if runtime["Id"] != vm["runtime_id"] or network["Id"] != vm["network_id"]:
        raise ValueError("Original VM runtime/network changed")
    peer, gateway = address(runtime, network)
    if peer != config["peer"] or gateway != config["gateway"]:
        raise ValueError("Original VM route changed")
    if sha(read(root / "data/service-link-forward.py")) != config["forwarder_sha256"]:
        raise ValueError("Original VM forwarder source changed")
    return peer


class _RelayGuard:
    """Daemon-local observation cache, never a capability or saved authority.

    Already admitted streams reuse a successful full observation for at most
    one second. Every new upstream connection requires a fresh full audit.
    Local immutable inputs and expiry are checked before every effect.
    """

    def __init__(self, config_path, raw, script_raw):
        self.path = Path(config_path)
        self.root = self.path.parent
        self.raw, self.script_raw = raw, script_raw
        self.config = json.loads(raw)
        self.expiry = datetime.fromisoformat(self.config["expires_at"].replace("Z", "+00:00"))
        vm_root = Path(self.config["vm_root"])
        self.paths = (
            self.path,
            self.root / "relay.py",
            vm_root / "owner.json",
            vm_root / "data/service-link-forward.py",
        )
        self.metadata = tuple(self._metadata(path) for path in self.paths)
        root_metadata = self._metadata(self.root)
        self.root_identity = (root_metadata[0], root_metadata[1], root_metadata[5], root_metadata[6])
        self.last = None
        self.lock = threading.Lock()

    @staticmethod
    def _metadata(path):
        info = path.lstat()
        if path.is_symlink() or path.resolve() != path:
            raise ValueError("Original local relay input route changed")
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_mode,
            info.st_uid,
            info.st_nlink,
        )

    def _local(self):
        root_metadata = self._metadata(self.root)
        if (
            datetime.now(timezone.utc) >= self.expiry
            or (root_metadata[0], root_metadata[1], root_metadata[5], root_metadata[6]) != self.root_identity
            or tuple(self._metadata(path) for path in self.paths) != self.metadata
        ):
            raise ValueError("Original local relay identity/source/config/expiry changed")

    def __call__(self, *, force=False):
        self._local()
        now = time.monotonic()
        if not force and self.last is not None and 0 <= now - self.last < 1:
            return
        deadline = now + 10
        if not self.lock.acquire(timeout=10):
            raise ValueError("Original relay audit deadline exhausted")
        try:
            self._local()
            now = time.monotonic()
            if now >= deadline:
                raise ValueError("Original relay audit deadline exhausted")
            if not force and self.last is not None and 0 <= now - self.last < 1:
                return
            self.last = None
            previous = getattr(_AUDIT_BUDGET, "deadline", None)
            _AUDIT_BUDGET.deadline = deadline
            try:
                _root_guard(self.config)
                if read(self.path) != self.raw or read(self.root / "relay.py") != self.script_raw:
                    raise ValueError("Original relay config/source bytes changed")
                self._local()
                if time.monotonic() >= deadline:
                    raise ValueError("Original relay audit deadline exhausted")
                self.last = now
            finally:
                if previous is None:
                    del _AUDIT_BUDGET.deadline
                else:
                    _AUDIT_BUDGET.deadline = previous
        finally:
            self.lock.release()


def _daemon(config_path):
    config_path = Path(config_path)
    root = config_path.parent
    raw = read(config_path)
    config = json.loads(raw)
    script = root / "relay.py"
    script_raw = read(script)
    if sha(script_raw) != config["script_sha256"]:
        raise ValueError("Original relay source changed")
    listeners = {}
    sockets = []
    semaphore = threading.BoundedSemaphore(16)
    guard = _RelayGuard(config_path, raw, script_raw)

    def serve(client, peer, role):
        try:
            guard(force=True)
            if peer != config["peer"]:
                raise ValueError("Only the original VM container may use this route")
            prefix = receive_client_hello(
                client,
                expected_host=config["services"][role]["origin"].split("//", 1)[1].split(":", 1)[0],
                deadline=time.monotonic() + 5,
            )
            if role in EXTERNAL:
                if external_addresses(role) != config["external_addresses"][role]:
                    raise ValueError("Original selected external DNS identity changed")
                destination = config["external_addresses"][role][0]
                port = 443
            else:
                selected = config["targets"][role]
                destination, _ = address(inspect(selected["container"]), inspect(selected["network"]))
                port = selected["port"]
            guard(force=True)
            with socket.create_connection((destination, port), timeout=5) as upstream:
                guard()
                upstream.sendall(prefix)
                client.setblocking(False)
                upstream.setblocking(False)
                ends = [client, upstream]
                deadline = time.monotonic() + 300
                while ends and time.monotonic() < deadline:
                    guard()
                    ready, _, _ = select.select(ends, [], [], min(1, deadline - time.monotonic()))
                    for source in ready:
                        guard()
                        data = source.recv(65536)
                        target = upstream if source is client else client
                        if not data:
                            ends.remove(source)
                            target.shutdown(socket.SHUT_WR)
                            continue
                        pending = memoryview(data)
                        while pending:
                            guard()
                            _, writable, _ = select.select(
                                [], [target], [], min(1, max(0, deadline - time.monotonic()))
                            )
                            if not writable or time.monotonic() >= deadline:
                                raise ValueError("Relay transfer deadline exhausted")
                            guard()
                            sent = target.send(pending)
                            if sent <= 0:
                                raise ValueError("Relay peer closed")
                            pending = pending[sent:]
        except Exception:
            pass
        finally:
            client.close()
            semaphore.release()

    try:
        for role in config["services"]:
            guard()
            listener = socket.socket()
            listener.bind((config["gateway"], 0))
            listener.listen(16)
            sockets.append(listener)
            listeners[role] = {"host": config["gateway"], "port": listener.getsockname()[1]}
        write(
            root / "daemon.json",
            canonical(
                {
                    "binding": config["binding"],
                    "runner_vm": config["runner_vm"],
                    "process": process_identity(os.getpid()),
                    "listeners": listeners,
                    "config_sha256": sha(raw),
                    "script_sha256": sha(script_raw),
                }
            ),
        )
        while True:
            guard()
            ready, _, _ = select.select(sockets, [], [], 1)
            for listener in ready:
                client, peer = listener.accept()
                if not semaphore.acquire(blocking=False):
                    client.close()
                    continue
                role = next(
                    r for r, v in listeners.items() if (v["host"], v["port"]) == listener.getsockname()
                )
                threading.Thread(target=serve, args=(client, peer[0], role), daemon=True).start()
    finally:
        for listener in sockets:
            listener.close()


_GUEST_BOOT = """import base64,hashlib,json,sys,types
p=json.loads(sys.stdin.buffer.read(3000001));b=base64.b64decode(p['source'],validate=True)
assert hashlib.sha256(b).hexdigest()==p['source_sha256']
m=types.ModuleType('runner_vm_guest_routes');exec(compile(b,'runner_vm_guest_routes','exec'),m.__dict__)
print(json.dumps(m.execute(p['packet']),sort_keys=True))
"""


def _guest_action(scope, packet):
    scope.observe_guest()
    value = {
        "source": base64.b64encode(GUEST_SOURCE_BYTES).decode(),
        "source_sha256": sha(GUEST_SOURCE_BYTES),
        "packet": packet,
    }
    result = scope.command(
        [*scope.ssh_argv(), shlex.join(["python3", "-I", "-B", "-c", _GUEST_BOOT])],
        seconds=min(300, packet["timeout"]),
        input_bytes=canonical(value),
    )
    observed = json.loads(result.stdout)
    if (
        observed.get("binding") != packet["binding"]
        or observed.get("runner_vm") != scope.identity()
        or observed.get("operation_id") != packet["operation_id"]
    ):
        raise ValueError("Actual guest route observation belongs to another VM/operation")
    scope.observe_guest()
    return observed


def _replace_state(path, value, previous):
    if read(path) != previous:
        raise ValueError("Original route ledger changed")
    temp = path.parent / (".state-" + str(os.getpid()))
    write(temp, canonical(value))
    if read(path) != previous:
        raise ValueError("Original route ledger changed before replacement")
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _daemon_observation(root, config):
    path = root / "daemon.json"
    if not path.exists():
        return None
    value = json.loads(read(path))
    process = value.get("process")
    expected_argv = [
        sys.executable,
        "-I",
        "-B",
        str(root / "relay.py"),
        "--daemon",
        str(root / "config.json"),
    ]
    if (
        value.get("binding") != config["binding"]
        or value.get("runner_vm") != config["runner_vm"]
        or value.get("config_sha256") != sha(read(root / "config.json"))
        or value.get("script_sha256") != sha(read(root / "relay.py"))
        or type(process) is not dict
        or process.get("process_group") != process.get("pid")
        or process.get("session") != process.get("pid")
        or process.get("command_sha256") != sha(b"\0".join(v.encode() for v in expected_argv) + b"\0")
        or set(value.get("listeners", {})) != set(config["services"])
    ):
        raise ValueError("Original relay receipt/source/process identity differs")
    for selected in value["listeners"].values():
        if (
            set(selected) != {"host", "port"}
            or selected["host"] != config["gateway"]
            or type(selected["port"]) is not int
            or not 1024 < selected["port"] < 65536
        ):
            raise ValueError("Relay listener differs from its original owned gateway")
    current = process_identity(process["pid"])
    if current is not None and current != process:
        raise ValueError("Relay PID was reused or original process changed")
    return {
        "original": value,
        "alive": current is not None,
        "absent": current is None and process_group_absent(process),
    }


def execute(packet, *, scope):
    started = time.monotonic()
    selected = _validate(packet, scope)
    action = packet["action"]
    deadline = started + selected["timeout"]
    config = None

    def budget():
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (
            action == "up"
            and datetime.now(timezone.utc)
            >= datetime.fromisoformat(selected["expires_at"].replace("Z", "+00:00"))
        ):
            raise ValueError("Original route effect deadline or provisioning grant expired")
        return remaining

    def effect_guard():
        budget()
        if config is not None:
            _root_guard(config)
        budget()

    def guest_call():
        effect_guard()
        remaining = budget()
        if remaining < 1:
            raise ValueError("Original guest route deadline exhausted")
        return _guest_action(scope, {**selected, "timeout": min(300, remaining)})

    budget()
    root = scope.root / "runner-routes"
    mapping = scope.data / "service-links.json"
    if action == "up":
        # Intent is outside the newly created subdirectory and precedes all
        # listeners, mapping files, or guest changes. Never adopt an old attempt.
        effect_guard()
        write(
            scope.root / "runner-routes-intent.json",
            canonical(
                {
                    "binding": selected["binding"],
                    "operation_id": selected["operation_id"],
                    "runner_vm": scope.identity(),
                    "packet_sha256": sha(canonical(packet)),
                }
            ),
        )
        effect_guard()
        root.mkdir(mode=0o700)
        effect_guard()
        write(root / "lock", b"")
    if (
        root.resolve() != root
        or not root.is_dir()
        or root.stat().st_uid != os.getuid()
        or root.stat().st_mode & 0o077
    ):
        raise ValueError("Original route generation is unobserved or changed")
    fd = os.open(root / "lock", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        read(root / "lock")
        endpoint = scope.endpoint()
        state_path = root / "state.json"
        if action == "up":
            runtime, network = scope.resources["runtime"], scope.resources["network"]
            peer, gateway = address(runtime, network)
            source = globals().get("SOURCE_BYTES")
            if type(source) is not bytes or not 1 <= len(source) <= 131072:
                raise ValueError("Original package-pinned relay source is unavailable")
            config = {
                "binding": selected["binding"],
                "run_id": selected["run_id"],
                "runner_vm": scope.identity(),
                "vm_root": str(scope.root),
                "peer": peer,
                "gateway": gateway,
                "expires_at": selected["expires_at"],
                "services": selected["services"],
                "targets": packet["targets"],
                "script_sha256": sha(source),
                "forwarder_sha256": sha(read(scope.data / "service-link-forward.py")),
                "runtime": {"kind": "container", "id": runtime["Id"], "labels": runtime["Config"]["Labels"]},
                "network": {"kind": "network", "id": network["Id"], "labels": network["Labels"]},
                "external_addresses": {
                    role: external_addresses(role) for role in EXTERNAL if role in selected["services"]
                },
            }
            if "blob_download_policy" in selected:
                config["blob_download_policy_sha256"] = selected["blob_download_policy"]["sha256"]
            effect_guard()
            write(root / "config.json", canonical(config))
            effect_guard()
            write(root / "relay.py", source)
            state = {
                "binding": selected["binding"],
                "runner_vm": scope.identity(),
                "operation_id": selected["operation_id"],
                "stage": "prepared",
                "pending": None,
                "daemon": None,
                "mapping": None,
                "guest": None,
                "host_cleanup": False,
                "guest_cleanup": False,
            }
            effect_guard()
            write(state_path, canonical(state))
        else:
            config = json.loads(read(root / "config.json"))
            state = json.loads(read(state_path))
            if (
                state["binding"] != selected["binding"]
                or state["runner_vm"] != scope.identity()
                or config["services"] != selected["services"]
                or config["targets"] != packet["targets"]
                or config["script_sha256"] != sha(globals()["SOURCE_BYTES"])
                or config["runner_vm"] != scope.identity()
                or config["vm_root"] != str(scope.root)
                or config["run_id"] != selected["run_id"]
                or config.get("blob_download_policy_sha256")
                != selected.get("blob_download_policy", {}).get("sha256")
            ):
                raise ValueError("Original runner route target/VM/source changed")
        _root_guard(config)

        def save():
            _replace_state(state_path, state, read(state_path))

        if action == "up":
            state["pending"] = "start-relay"
            save()
            command = [
                sys.executable,
                "-I",
                "-B",
                str(root / "relay.py"),
                "--daemon",
                str(root / "config.json"),
            ]
            effect_guard()
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
            state["spawn_pid"] = process.pid
            save()
            observation = None
            startup_deadline = min(deadline, time.monotonic() + 10)
            while time.monotonic() < startup_deadline:
                observation = _daemon_observation(root, config)
                if observation is not None:
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            if (
                not observation
                or not observation["alive"]
                or observation["original"]["process"]["pid"] != process.pid
            ):
                raise ValueError("Original relay start outcome remains unobserved")
            state["daemon"] = observation["original"]
            state["pending"] = None
            save()
            forward = {
                "schema_version": 1,
                "binding": selected["binding"],
                "gateway": config["gateway"],
                **{role: row["port"] for role, row in state["daemon"]["listeners"].items()},
            }
            state["pending"] = "write-forwarder"
            state["mapping_bytes_sha256"] = sha(canonical(forward))
            save()
            effect_guard()
            write(mapping, canonical(forward))
            info = mapping.stat()
            state["mapping"] = {"device": info.st_dev, "inode": info.st_ino, "sha256": sha(read(mapping))}
            state["pending"] = None
            save()
            state["pending"] = "guest-up"
            save()
            state["guest"] = guest_call()
            if state["guest"].get("ready") is not True or state["guest"].get("unresolved"):
                raise ValueError("Original guest routes are not ready")
            state["pending"] = None
            state["stage"] = "ready"
            save()
        elif action in {"down", "down-host"}:
            # Host-only cleanup is an explicit separate branch. It never
            # declares guest files/processes absent or revives an up operation.
            pending_observation = _daemon_observation(root, config)
            resolved_host_removal = (
                state["pending"] == "stop-relay"
                and pending_observation is not None
                and pending_observation["absent"]
                or state["pending"] == "remove-forwarder"
                and state["mapping"] is not None
                and not mapping.exists()
            )
            if state["pending"] not in {None, "guest-up", "guest-down"} and not resolved_host_removal:
                raise ValueError("Original host write outcome must be reconciled before cleanup")
            if action == "down" and not state["guest_cleanup"]:
                state["pending"] = "guest-down"
                save()
                state["guest"] = guest_call()
                if state["guest"].get("complete_cleanup") is not True or state["guest"].get("unresolved"):
                    raise ValueError("Guest cleanup remains unobserved; keep original host relay")
                state["guest_cleanup"] = True
                state["pending"] = None
                save()
            observation = _daemon_observation(root, config)
            if observation is None:
                raise ValueError("Original relay process receipt remains unobserved")
            if observation["alive"]:
                state["pending"] = "stop-relay"
                save()
                original = observation["original"]["process"]
                if process_identity(original["pid"]) != original:
                    raise ValueError("Original relay changed before stop")
                effect_guard()
                os.killpg(original["pid"], signal.SIGTERM)
                stop = min(deadline, time.monotonic() + 5)
                while time.monotonic() < stop and not process_group_absent(original):
                    time.sleep(0.05)
                if not process_group_absent(original):
                    if process_identity(original["pid"]) != original:
                        raise ValueError("Relay changed before bounded stop")
                    effect_guard()
                    os.killpg(original["pid"], signal.SIGKILL)
                if not process_group_absent(original):
                    raise ValueError("Original relay cessation remains unobserved")
            if mapping.exists():
                info = mapping.stat()
                owned = state["mapping"]
                if (
                    owned is None
                    or {"device": info.st_dev, "inode": info.st_ino, "sha256": sha(read(mapping))} != owned
                ):
                    raise ValueError("Original forwarding file changed; preserve it")
                state["pending"] = "remove-forwarder"
                save()
                effect_guard()
                mapping.unlink()
                directory_fd = os.open(mapping.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            state["pending"] = None
            state["host_cleanup"] = True
            state["stage"] = "restored" if state["guest_cleanup"] else "host-restored-guest-unobserved"
            save()
        else:
            observation = _daemon_observation(root, config)
            if observation is None:
                raise ValueError("Existing relay outcome remains unobserved")
            # A current read checks the retained original guest and files. It
            # grants no permission to replay pending mutations.
            if action != "reconcile-host" and not state["guest_cleanup"] and not state["host_cleanup"]:
                state_guest = guest_call()
            else:
                state_guest = state["guest"]
            state = {**state, "guest": state_guest}
        current = _daemon_observation(root, config)
        host_absent = bool(current and current["absent"] and not mapping.exists())
        guest = state["guest"] or {}
        host_unknown, guest_unknown = [], []
        pending = state["pending"]
        if pending in {"guest-up", "guest-down"}:
            observed_guest = (
                action != "reconcile-host"
                and guest.get("unresolved") == []
                and (
                    guest.get("ready") is True
                    if pending == "guest-up"
                    else guest.get("complete_cleanup") is True
                )
            )
            if not observed_guest:
                guest_unknown.append(pending)
        elif pending is not None:
            # An independently absent original process/mapping resolves the
            # terminal effect of an interrupted removal, never a create.
            resolved = (
                pending == "stop-relay"
                and current is not None
                and current["absent"]
                or pending == "remove-forwarder"
                and state["mapping"] is not None
                and not mapping.exists()
            )
            if not resolved:
                host_unknown.append(pending)
        if mapping.exists():
            info = mapping.stat()
            if state["mapping"] != {
                "device": info.st_dev,
                "inode": info.st_ino,
                "sha256": sha(read(mapping)),
            }:
                host_unknown.append("forwarder-identity")
        if current is None:
            host_unknown.append("relay-observation")
        if (action in {"reconcile-host", "down-host"} or state["host_cleanup"]) and not state[
            "guest_cleanup"
        ]:
            guest_unknown.append("guest-local-state-unobserved")
        elif guest.get("unresolved"):
            guest_unknown.extend(guest["unresolved"])
        host_unknown = list(dict.fromkeys(host_unknown))
        guest_unknown = list(dict.fromkeys(guest_unknown))
        ready = bool(
            current
            and current["alive"]
            and guest.get("ready") is True
            and not host_unknown
            and not guest_unknown
            and state["stage"] == "ready"
            and action != "reconcile-host"
        )
        budget()
        return {
            "schema_version": 1,
            "binding": selected["binding"],
            "operation_id": selected["operation_id"],
            "runner_vm": scope.identity(),
            "ready": ready,
            "complete_cleanup": host_absent and state["guest_cleanup"],
            "host_cleanup": host_absent,
            "guest_cleanup": state["guest_cleanup"],
            "guest": guest,
            "relay": None if current is None else current["original"],
            "stage": state["stage"],
            "unresolved": host_unknown + guest_unknown,
            "host_unresolved": host_unknown,
            "guest_unresolved": guest_unknown,
            "host_disposal_eligible": action == "reconcile-host" and current is not None and not host_unknown,
            "mutation_effect": "applied" if action in {"up", "down", "down-host"} else "not_applied",
            "remote_state_sha256": sha(read(state_path)),
            "endpoint_binding": endpoint["runner_vm"],
        }
    finally:
        os.close(fd)


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, limit=2_000_000):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_size > limit
            or info.st_mode & 0o022
        ):
            raise ValueError("Link original file ownership/type/size differs")
        value = os.read(fd, limit + 1)
        after = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("Link original file changed while reading")
        if len(value) > limit:
            raise ValueError("Link file exceeds bound")
        return value
    finally:
        os.close(fd)


def write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def process_identity(pid):
    try:
        raw = Path("/proc", str(pid), "stat").read_text()
        start = raw[raw.rfind(")") + 2 :].split()[19]
        command = Path("/proc", str(pid), "cmdline").read_bytes()
        if not command:
            return None
        return {
            "pid": pid,
            "start": start,
            "command_sha256": sha(command),
            "process_group": os.getpgid(pid),
            "session": os.getsid(pid),
        }
    except (FileNotFoundError, ProcessLookupError):
        return None


def process_group_absent(original):
    if original.get("process_group") != original["pid"] or original.get("session") != original["pid"]:
        raise ValueError("Relay process is not the original exclusively created session")
    try:
        os.killpg(original["process_group"], 0)
        return False
    except ProcessLookupError:
        return True


def _docker_inspection(argv):
    """Fixed read-only Docker observation with limits enforced while reading."""
    deadline = min(time.monotonic() + 10, getattr(_AUDIT_BUDGET, "deadline", float("inf")))
    if time.monotonic() >= deadline:
        raise ValueError("Original relay audit deadline exhausted")
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=False,
    )
    output = {process.stdout: bytearray(), process.stderr: bytearray()}
    try:
        with selectors.DefaultSelector() as ready:
            for stream in output:
                os.set_blocking(stream.fileno(), False)
                ready.register(stream, selectors.EVENT_READ)
            while ready.get_map():
                if time.monotonic() >= deadline:
                    raise ValueError("Docker observation exceeded its time budget")
                for key, _ in ready.select(min(0.2, deadline - time.monotonic())):
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except (BlockingIOError, InterruptedError):
                        continue
                    if not chunk:
                        ready.unregister(key.fileobj)
                        continue
                    output[key.fileobj].extend(chunk)
                    if len(output[key.fileobj]) > (2_000_000 if key.fileobj is process.stdout else 64000):
                        raise ValueError("Docker observation exceeded its byte budget")
        if process.wait(timeout=max(0.001, deadline - time.monotonic())):
            raise ValueError("Original Docker inspection failed")
        return bytes(output[process.stdout])
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        for stream in output:
            stream.close()


def inspect(target):
    kind = target["kind"]
    identifier = target["id"]
    if kind not in ("container", "network") or not re.fullmatch("[a-f0-9]{64}", identifier):
        raise ValueError("Link targets require exact observed Docker IDs")
    rows = json.loads(_docker_inspection(["docker", kind, "inspect", identifier]))
    if type(rows) is not list or len(rows) != 1 or rows[0].get("Id") != identifier:
        raise ValueError("Link original Docker ID changed")
    item = rows[0]
    labels = item.get("Labels", {}) if kind == "network" else item.get("Config", {}).get("Labels", {})
    if any(labels.get(k) != v for k, v in target["labels"].items()):
        raise ValueError("Link original Docker ownership changed")
    if kind == "container" and item.get("State", {}).get("Running") is not True:
        raise ValueError("Link original container is no longer running")
    return item


def address(container, network):
    attached = container.get("NetworkSettings", {}).get("Networks", {})
    rows = [v for v in attached.values() if v.get("NetworkID") == network["Id"]]
    if len(rows) != 1 or len(attached) != 1:
        raise ValueError("Original container no longer belongs to its exact owned network")
    addr = ipaddress.IPv4Address(rows[0]["IPAddress"])
    if not addr.is_private or addr.is_loopback:
        raise ValueError("Owned service bridge address is invalid")
    configs = network.get("IPAM", {}).get("Config", [])
    matches = [v for v in configs if addr in ipaddress.IPv4Network(v["Subnet"])]
    if len(matches) != 1 or network.get("Driver") != "bridge":
        raise ValueError("Original owned bridge scope differs")
    gateway = ipaddress.IPv4Address(matches[0]["Gateway"])
    if gateway not in ipaddress.IPv4Network(matches[0]["Subnet"]) or gateway == addr:
        raise ValueError("Original bridge gateway differs")
    return str(addr), str(gateway)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--daemon":
        raise SystemExit(2)
    _daemon(Path(sys.argv[2]))
