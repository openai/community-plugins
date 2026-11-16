"""Fixed nonroot guest trust routes; serialized receipts issue no authority.

Only the original VM worker invokes this package-pinned helper. Host mappings
are immutable first-boot selections, never changed by the job/SSH user. No tool,
package, registry credential, system trust, proxy, sudo or RKE2 access is added.
"""

from __future__ import annotations
import base64
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import ssl
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

HOME = Path("/home/gitlab-runner")
ROOT = HOME / ".local/share/devsecops"
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
UID = GID = 10001
_MAX = 2_000_000
SHA = re.compile(r"[a-f0-9]{64}\Z")
ROLE = {
    "gitlab": ("10.0.2.100", 8444),
    "registry": ("10.0.2.101", 8443),
    "registry1": ("10.0.2.102", 443),
    "p1docs": ("10.0.2.103", 443),
    "registry1-storage": ("10.0.2.104", 443),
}
EXTERNAL = {"registry1": "https://registry1.dso.mil", "p1docs": "https://p1docs.dso.mil",
            "registry1-storage": "https://ib-prod-harbor-storage.s3.us-gov-west-1.amazonaws.com"}


def _blob_policy(packet):
    selection = packet.get("blob_download_policy")
    storage_selected = "registry1-storage" in packet["services"]
    if selection is None:
        if storage_selected or "blob_download_policy" in packet:
            raise ValueError("Storage route needs its original explicit blob policy")
        return None
    if (not storage_selected or "registry1" not in packet["services"]
            or type(selection) is not dict or set(selection) != {"sha256", "document_b64"}
            or type(selection["sha256"]) is not str or not SHA.fullmatch(selection["sha256"])
            or type(selection["document_b64"]) is not str or len(selection["document_b64"]) > 21848):
        raise ValueError("Closed storage policy binding required")
    raw = base64.b64decode(selection["document_b64"], validate=True)
    if not 1 <= len(raw) <= 16384 or hashlib.sha256(raw).hexdigest() != selection["sha256"]:
        raise ValueError("Storage policy bytes differ from selected identity")
    def unique(pairs):
        value = {}
        for name, child in pairs:
            if name in value:
                raise ValueError("Duplicate storage policy field")
            value[name] = child
        return value
    policy = json.loads(raw, object_pairs_hook=unique)
    if (type(policy) is not dict or set(policy) != {"schema_version", "kind", "registry_origin", "repositories", "storage_origins"}
            or type(policy["schema_version"]) is not int or policy["schema_version"] != 1
            or policy["kind"] != "registry-blob-download-policy"
            or policy["registry_origin"] != EXTERNAL["registry1"]
            or policy["storage_origins"] != [EXTERNAL["registry1-storage"]]
            or type(policy["repositories"]) is not list or not 1 <= len(policy["repositories"]) <= 32
            or any(type(name) is not str or len(name) > 255 or not re.fullmatch(
                r'[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*(?:/[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*)*', name)
                for name in policy["repositories"])
            or len(set(policy["repositories"])) != len(policy["repositories"])):
        raise ValueError("Storage policy differs from the selected closed profile")
    return policy


def validate_packet(packet):
    fields = {
        "schema_version",
        "action",
        "run_id",
        "operation_id",
        "binding",
        "runner_vm",
        "expires_at",
        "timeout",
        "services",
    }
    if (
        type(packet) is not dict
        or set(packet) - {"blob_download_policy"} != fields
        or type(packet["schema_version"]) is not int
        or packet["schema_version"] != 1
        or packet["action"] not in {"up", "down", "reconcile", "observe"}
    ):
        raise ValueError("Closed runner guest routes packet required")
    for key in ("run_id", "operation_id"):
        if type(packet[key]) is not str or str(UUID(packet[key])) != packet[key]:
            raise ValueError("Original canonical run and operation UUID required")
    vm = packet["runner_vm"]
    if (
        type(vm) is not dict
        or set(vm)
        != {
            "binding",
            "generation",
            "guest_boot_id",
            "guest_uid",
            "guest_gid",
            "root_identity",
            "disk_identity",
            "runtime_id",
            "network_id",
            "ssh_host_key_sha256",
        }
        or vm["generation"] != "runner-vm-" + packet["run_id"]
        or vm["guest_uid"] != UID
        or type(vm["guest_uid"]) is not int
        or vm["guest_gid"] != GID
        or type(vm["guest_gid"]) is not int
        or str(UUID(vm["guest_boot_id"])) != vm["guest_boot_id"]
    ):
        raise ValueError("Original distinct runner VM identity required")
    for value in (
        packet["binding"],
        vm["binding"],
        vm["runtime_id"],
        vm["network_id"],
        vm["ssh_host_key_sha256"],
    ):
        if type(value) is not str or not SHA.fullmatch(value):
            raise ValueError("Runner VM identity pin differs")
    for key in ("root_identity", "disk_identity"):
        if (
            type(vm[key]) is not dict
            or set(vm[key]) != {"device", "inode"}
            or any(type(v) is not int or v < 0 for v in vm[key].values())
            or vm[key]["inode"] == 0
        ):
            raise ValueError("Original VM root/disk identity is incomplete")
    expiry = datetime.fromisoformat(packet["expires_at"].replace("Z", "+00:00"))
    if (
        expiry.tzinfo is None
        or type(packet["timeout"]) not in (int, float)
        or not math.isfinite(packet["timeout"])
        or not 1 <= packet["timeout"] <= 300
        or packet["action"] == "up"
        and datetime.now(timezone.utc) >= expiry
    ):
        raise ValueError("Guest route grant or deadline differs")
    services = packet["services"]
    if (
        type(services) is not dict
        or not {"gitlab", "registry"} <= set(services)
        or not set(services) <= set(ROLE)
        or "p1docs" in services
        and "registry1" not in services
    ):
        raise ValueError("Only selected owned routes and explicit external dependency routes are supported")
    _blob_policy(packet)
    environment = None
    for role, row in services.items():
        if type(row) is not dict or set(row) != {"origin", "address", "port", "ca_b64", "ca_sha256"}:
            raise ValueError("Closed selected TLS route required")
        parsed = urlsplit(row["origin"])
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or (row["address"], row["port"]) != ROLE[role]
            or type(row["port"]) is not int
        ):
            raise ValueError("Route origin or fixed guest-forward destination differs")
        if role in EXTERNAL:
            if row["origin"] != EXTERNAL[role]:
                raise ValueError("External route must use its sole explicit origin")
        else:
            match = re.fullmatch(
                role + r"\.([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.test", parsed.hostname or ""
            )
            if (
                not match
                or row["origin"] != "https://" + parsed.hostname + ":" + str(row["port"])
                or environment is not None
                and environment != match[1]
            ):
                raise ValueError("Owned service TLS domain differs from its original environment")
            environment = match[1]
        ca = base64.b64decode(row["ca_b64"], validate=True)
        if (
            not 1 <= len(ca) <= 100000
            or type(row["ca_sha256"]) is not str
            or not SHA.fullmatch(row["ca_sha256"])
            or _sha(ca) != row["ca_sha256"]
        ):
            raise ValueError("Separately pinned CA bytes required; no system trust fallback")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=ca.decode("ascii"))
    return deepcopy(packet)


def _identity(path):
    st = path.lstat()
    return {
        "device": st.st_dev,
        "inode": st.st_ino,
        "mode": stat.S_IMODE(st.st_mode),
        "uid": st.st_uid,
        "gid": st.st_gid,
    }


def _directory(path):
    if path.resolve() != path or path.is_symlink():
        raise ValueError("Original guest directory route changed")
    row = _identity(path)
    if not path.is_dir() or row["uid"] != UID or row["gid"] != GID or row["mode"] & 0o077:
        raise ValueError("Private runner-owned directory required")
    return row


def _read(path, *, missing=False):
    if path.resolve() != path or path.is_symlink():
        raise ValueError("Guest route file contains a symlink")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if missing:
            return None
        raise
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != UID
            or info.st_gid != GID
            or info.st_mode & 0o077
            or info.st_size > _MAX
        ):
            raise ValueError("Private bounded original runner file required")
        raw = stream.read(_MAX + 1)
        after = os.fstat(stream.fileno())
    if (
        len(raw) != info.st_size
        or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        or _identity(path)
        != {
            "device": after.st_dev,
            "inode": after.st_ino,
            "mode": stat.S_IMODE(after.st_mode),
            "uid": after.st_uid,
            "gid": after.st_gid,
        }
    ):
        raise ValueError("Guest route file changed while reading")
    return {**_identity(path), "bytes": len(raw), "sha256": _sha(raw)}, raw


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _exclusive(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _sync(path.parent)
    return _read(path)[0]


def _guard(packet, deadline):
    _remaining(deadline)
    if (
        os.getuid() != UID
        or os.geteuid() != UID
        or os.getgid() != GID
        or os.getegid() != GID
        or BOOT_ID.read_text().strip() != packet["runner_vm"]["guest_boot_id"]
    ):
        raise ValueError("Original nonroot runner VM boot/user identity changed")
    if packet["action"] == "up" and datetime.now(timezone.utc) >= datetime.fromisoformat(
        packet["expires_at"].replace("Z", "+00:00")
    ):
        raise ValueError("Provisioning grant expired")
    root = ROOT / UUID(packet["run_id"]).hex
    _directory(root)
    _directory(root / "inputs")
    return root


def planned_files(packet):
    """Fixed public trust bytes and exact process environment; never credentials."""
    root = ROOT / UUID(packet["run_id"]).hex / "inputs/service-trust"
    result = {
        role + "-ca.pem": base64.b64decode(row["ca_b64"], validate=True)
        for role, row in packet["services"].items()
    }
    external = [result[role + "-ca.pem"] for role in EXTERNAL if role in packet["services"]]
    if external:
        if any(raw != external[0] for raw in external):
            raise ValueError("External routes require the one explicit selected CA bundle")
        result["external-ca.pem"] = external[0]
    distinct = []
    for name in sorted(result):
        if result[name] not in distinct:
            distinct.append(result[name])
    result["ca-bundle.pem"] = b"\n".join(distinct)
    env = {
        name: str(root / "ca-bundle.pem")
        for name in ("SSL_CERT_FILE", "GIT_SSL_CAINFO", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE")
    }
    env["SSL_CERT_DIR"] = str(root / "empty-ca-directory")
    env.update(
        {
            name: ""
            for name in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
            )
        }
    )
    result["environment.json"] = _bytes(env)
    return result


def _storage_tls_context():
    # This helper is bundled standalone into the guest. Keep the explicit
    # OpenSSL path selection aligned with HTTPSReadClient's storage transport;
    # SSL_CERT_FILE/DIR and the staged registry CA are not storage trust inputs.
    roots = ssl.get_default_verify_paths()
    cafile = roots.openssl_cafile if roots.openssl_cafile and Path(roots.openssl_cafile).is_file() else None
    capath = roots.openssl_capath if roots.openssl_capath and Path(roots.openssl_capath).is_dir() else None
    if cafile is None and capath is None:
        raise ValueError("Storage readiness requires existing OpenSSL system trust roots")
    return ssl.create_default_context(cafile=cafile, capath=capath)


def _tls_probe(row, role, deadline):
    deadline = min(deadline, time.monotonic() + 5)
    host = urlsplit(row["origin"]).hostname
    raw = _run(["getent", "ahostsv4", host], deadline=deadline, limit=8192).decode("ascii")
    if {line.split()[0] for line in raw.splitlines() if line.split()} != {row["address"]}:
        raise ValueError("Seeded hostname differs from the original exact route")
    if role == "registry1-storage":
        context = _storage_tls_context()
    else:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=base64.b64decode(row["ca_b64"], validate=True).decode("ascii"))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as plain:
        plain.settimeout(_remaining(deadline))
        plain.connect((row["address"], row["port"]))
        with context.wrap_socket(plain, server_hostname=host, do_handshake_on_connect=False) as client:
            client.settimeout(_remaining(deadline))
            client.do_handshake()
            if role == "registry1-storage":
                # This proves only the selected TLS peer, not access to a blob.
                # Never invent a presigned URL or issue an unsigned object GET.
                _remaining(deadline)
                return {"origin": row["origin"], "address": row["address"], "port": row["port"],
                        "trust": "openssl-system-roots",
                        "certificate_sha256": _sha(client.getpeercert(binary_form=True)),
                        "status": None, "probe": "tls-peer-only"}
            route = (
                "/-/readiness"
                if role == "gitlab"
                else "/iron-bank/tutorials/cosign"
                if role == "p1docs"
                else "/v2/"
            )
            client.settimeout(_remaining(deadline))
            client.sendall(
                (
                    "GET "
                    + route
                    + " HTTP/1.1\r\nHost: "
                    + urlsplit(row["origin"]).netloc
                    + "\r\nConnection: close\r\n\r\n"
                ).encode("ascii")
            )
            response = b""
            while b"\r\n\r\n" not in response and len(response) < 8192:
                client.settimeout(_remaining(deadline))
                chunk = client.recv(min(1024, 8192 - len(response)))
                if not chunk:
                    break
                response += chunk
            match = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?", response.split(b"\r\n", 1)[0])
            status = int(match[1]) if match else None
            if b"\r\n\r\n" not in response or status not in (
                {200, 401} if role in ("registry", "registry1") else {200}
            ):
                raise ValueError("Exact TLS readiness failed; redirects and fallback are forbidden")
            _remaining(deadline)
            return {
                "origin": row["origin"],
                "address": row["address"],
                "port": row["port"],
                "ca_sha256": row["ca_sha256"],
                "certificate_sha256": _sha(client.getpeercert(binary_form=True)),
                "status": status,
            }


def _scope(packet):
    return {name: packet[name] for name in ("run_id", "binding", "runner_vm", "services")}


def _observe(state, packet, root):
    if (
        type(state) is not dict
        or set(state)
        != {
            "scope",
            "root_identity",
            "up_operation_id",
            "stage",
            "files",
            "trust_identity",
            "empty_identity",
            "pending",
        }
        or state["scope"] != _scope(packet)
        or state["root_identity"] != _directory(root)
        or state["stage"] not in {"prepared", "ready", "restored"}
        or type(state["files"]) is not dict
        or set(state["files"]) - set(planned_files(packet))
        or str(UUID(state["up_operation_id"])) != state["up_operation_id"]
    ):
        raise ValueError("Guest route scope differs from original run/VM")
    directory = root / "inputs/service-trust"
    unknown = []
    if directory.exists():
        if state["trust_identity"] != _directory(directory):
            raise ValueError("Original private trust directory changed")
        allowed = set(planned_files(packet)) | {"empty-ca-directory"}
        if set(p.name for p in directory.iterdir()) - allowed:
            raise ValueError("Foreign files appeared in owned trust directory")
    current = {}
    for name, wanted in planned_files(packet).items():
        row = _read(directory / name, missing=True)
        if row is None:
            current[name] = "absent"
        elif row[1] == wanted and row[0] == state["files"].get(name):
            current[name] = "applied"
        else:
            current[name] = "unknown"
            unknown.append(name)
    ready = directory.exists() and all(v == "applied" for v in current.values())
    if ready:
        empty = directory / "empty-ca-directory"
        ready = empty.is_dir() and _directory(empty) == state["empty_identity"] and not any(empty.iterdir())
    return {
        "files": current,
        "unknown": unknown,
        "ready": ready,
        "clean": not directory.exists() and all(v == "absent" for v in current.values()),
    }


def execute(raw_packet):
    started = time.monotonic()
    packet = validate_packet(raw_packet)
    deadline = started + packet["timeout"]
    root = _guard(packet, deadline)
    state_path = root / "route-state.json"
    lock = root / "route-state.lock"
    if packet["action"] == "up":
        _exclusive(lock, b"")
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _read(lock)
        old = _read(state_path, missing=True)
        if packet["action"] == "up":
            if old is not None or (root / "inputs/service-trust").exists():
                raise ValueError("Existing route attempt requires reconciliation, never duplicate up")
            state = {
                "scope": _scope(packet),
                "root_identity": _directory(root),
                "up_operation_id": packet["operation_id"],
                "stage": "prepared",
                "files": {},
                "trust_identity": None,
                "empty_identity": None,
                "pending": None,
            }
            _exclusive(state_path, _bytes(state))
        else:
            if old is None:
                raise ValueError("Original route journal is missing; outcome remains unobserved")
            state = _json(old[1])

        def save():
            before = _read(state_path)[0]
            temporary = root / (".route-state-" + uuid4().hex)
            _exclusive(temporary, _bytes(state))
            if _read(state_path)[0] != before:
                raise ValueError("Original guest journal changed")
            os.replace(temporary, state_path)
            _sync(root)

        directory = root / "inputs/service-trust"
        if packet["action"] == "up":
            state["pending"] = "trust-directory"
            save()
            _guard(packet, deadline)
            directory.mkdir(mode=0o700)
            state["trust_identity"] = _directory(directory)
            state["pending"] = None
            save()
            state["pending"] = "empty-ca-directory"
            save()
            _guard(packet, deadline)
            empty = directory / "empty-ca-directory"
            empty.mkdir(mode=0o700)
            state["empty_identity"] = _directory(empty)
            state["pending"] = None
            save()
            for name, wanted in planned_files(packet).items():
                _guard(packet, deadline)
                state["pending"] = name
                save()
                _guard(packet, deadline)
                state["files"][name] = _exclusive(directory / name, wanted)
                state["pending"] = None
                save()
            observation = _observe(state, packet, root)
            if not observation["ready"]:
                raise ValueError("Route trust is incomplete")
            verification = {role: _tls_probe(row, role, deadline) for role, row in packet["services"].items()}
            _guard(packet, deadline)
            state["stage"] = "ready"
            save()
        else:
            observation = _observe(state, packet, root)
            verification = {}
            if packet["action"] == "down":
                if observation["unknown"] or state["pending"] is not None:
                    raise ValueError("Uncertain guest effects require original reconciliation")
                for name in reversed(list(planned_files(packet))):
                    if observation["files"][name] == "absent":
                        continue
                    _guard(packet, deadline)
                    if _read(directory / name)[0] != state["files"][name]:
                        raise ValueError("Owned guest trust file changed before removal")
                    state["pending"] = "remove:" + name
                    save()
                    _guard(packet, deadline)
                    (directory / name).unlink()
                    _sync(directory)
                    state["pending"] = None
                    save()
                if directory.exists():
                    empty = directory / "empty-ca-directory"
                    if empty.exists():
                        if _directory(empty) != state["empty_identity"] or any(empty.iterdir()):
                            raise ValueError("Owned empty CA directory changed")
                        _guard(packet, deadline)
                        empty.rmdir()
                        _sync(directory)
                    if _directory(directory) != state["trust_identity"]:
                        raise ValueError("Owned trust directory changed")
                    _guard(packet, deadline)
                    directory.rmdir()
                    _sync(directory.parent)
                state["stage"] = "restored"
                save()
                observation = _observe(state, packet, root)
            elif observation["ready"] and datetime.now(timezone.utc) < datetime.fromisoformat(
                packet["expires_at"].replace("Z", "+00:00")
            ):
                verification = {
                    role: _tls_probe(row, role, deadline) for role, row in packet["services"].items()
                }
        _guard(packet, deadline)
        observation = _observe(state, packet, root)
        return {
            "schema_version": 1,
            "binding": packet["binding"],
            "runner_vm": packet["runner_vm"],
            "operation_id": packet["operation_id"],
            "up_operation_id": state["up_operation_id"],
            "ready": observation["ready"] and len(verification) == len(packet["services"]),
            "complete_cleanup": observation["clean"],
            "unresolved": observation["unknown"] + ([state["pending"]] if state["pending"] else []),
            "mutation_effect": "applied" if packet["action"] in {"up", "down"} else "not_applied",
            "state_sha256": _read(state_path)[0]["sha256"],
            "files": deepcopy(state["files"]),
            "trust_directory": str(directory),
            "trust_identity": state["trust_identity"],
            "empty_identity": state["empty_identity"],
            "environment": _json(planned_files(packet)["environment.json"]),
            "verification": verification,
            "stage": state["stage"],
        }
    finally:
        os.close(fd)


def _bytes(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"
    ).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    if type(raw) is not bytes or len(raw) > _MAX:
        raise ValueError("Bounded JSON bytes required")
    return json.loads(
        raw,
        object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON")),
    )


def _remaining(deadline, ceiling=30):
    remaining = min(ceiling, deadline - time.monotonic())
    if remaining <= 0:
        raise ValueError("Guest service-link deadline exhausted")
    return remaining


def _run(argv, *, deadline, stdin=None, limit=_MAX):
    # Neither a chatty child nor an unbounded pipe can consume the guest or
    # reset the absolute action budget. Input is bounded before process start.
    if stdin is not None and len(stdin) > _MAX:
        raise ValueError("Fixed guest command input exceeds its bound")
    end = time.monotonic() + _remaining(deadline)
    with tempfile.TemporaryFile() as source:
        if stdin is not None:
            source.write(stdin)
            source.seek(0)
        process = subprocess.Popen(
            argv,
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
            start_new_session=True,
        )
        streams = {process.stdout: bytearray(), process.stderr: bytearray()}
        complete = False
        try:
            with selectors.DefaultSelector() as poll:
                for stream in streams:
                    os.set_blocking(stream.fileno(), False)
                    poll.register(stream, selectors.EVENT_READ)
                while poll.get_map():
                    for event, _ in poll.select(_remaining(end)):
                        try:
                            chunk = os.read(event.fileobj.fileno(), 16384)
                        except (BlockingIOError, InterruptedError):
                            continue
                        if not chunk:
                            poll.unregister(event.fileobj)
                            continue
                        target = streams[event.fileobj]
                        target.extend(chunk)
                        if len(target) > (limit if event.fileobj is process.stdout else 64000):
                            raise ValueError("Fixed guest command output exceeds its bound")
                if process.wait(timeout=_remaining(end)):
                    raise ValueError("Fixed guest read or service operation failed")
            complete = True
            return bytes(streams[process.stdout])
        finally:
            if not complete or process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
