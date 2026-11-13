"""Fixed guest-only service DNS/trust changes with original-byte restoration.

This module is shipped as pinned source into the original owned fresh guest.
The caller supplies no paths, commands, credentials, resolver wildcard or proxy.
Receipts contain public identities/hashes only; preimages stay in the private
guest journal. Nothing in this module runs merely because it is imported.
"""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import ssl
import stat
import struct
import subprocess
import time
import tempfile
from urllib.parse import urlsplit
from uuid import UUID, uuid4

ROOT = Path("/var/lib/devsecops/service-links")
HOSTS = Path("/etc/hosts")
REGISTRIES = Path("/etc/rancher/rke2/registries.yaml")
TRUST = Path("/etc/rancher/rke2/devsecops-service-links")
KUBECTL = "/var/lib/rancher/rke2/bin/kubectl"
KUBECONFIG = "/etc/rancher/rke2/rke2.yaml"
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_MAX = 2_000_000


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


def validate_packet(packet):
    fields = {
        "schema_version",
        "action",
        "run_id",
        "operation_id",
        "binding",
        "cluster_uid",
        "foundation_binding",
        "expires_at",
        "timeout",
        "services",
        "coredns_image_pins",
    }
    if (
        type(packet) is not dict
        or set(packet) != fields
        or type(packet["schema_version"]) is not int
        or packet["schema_version"] != 1
        or packet["action"] not in {"up", "down", "reconcile"}
    ):
        raise ValueError("Closed guest service-link packet required")
    for key in ("run_id", "operation_id"):
        if str(UUID(packet[key])) != packet[key]:
            raise ValueError("Canonical operation/run UUID required")
    if (
        not _SHA.fullmatch(packet["binding"])
        or not _SHA.fullmatch(packet["foundation_binding"])
        or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", packet["cluster_uid"])
        or type(packet["timeout"]) not in (int, float)
        or not 1 <= packet["timeout"] <= 300
    ):
        raise ValueError("Guest identity or deadline is invalid")
    expires = datetime.fromisoformat(packet["expires_at"].replace("Z", "+00:00"))
    if expires.tzinfo is None or packet["action"] == "up" and datetime.now(timezone.utc) >= expires:
        raise ValueError("Guest service-link action expired")
    if type(packet["services"]) is not dict or set(packet["services"]) != {"gitlab", "registry"}:
        raise ValueError("Exactly GitLab and registry service routes are required")
    environment = None
    for role, address, port in (("gitlab", "10.0.2.100", 8444), ("registry", "10.0.2.101", 8443)):
        row = packet["services"][role]
        if type(row) is not dict or set(row) != {"origin", "address", "port", "ca_b64", "ca_sha256"}:
            raise ValueError("Closed fixed service route required")
        parsed = urlsplit(row["origin"])
        match = re.fullmatch(role + r"\.([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.test", parsed.hostname or "")
        if (
            not match
            or parsed.scheme != "https"
            or parsed.port != port
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or row["origin"] != "https://" + parsed.hostname + ":" + str(port)
            or row["address"] != address
            or row["port"] != port
        ):
            raise ValueError("Service route differs from the fixed guest-forward destination")
        if environment is not None and match[1] != environment:
            raise ValueError("Service domains belong to different environments")
        environment = match[1]
        ca = base64.b64decode(row["ca_b64"], validate=True)
        if (
            not 1 <= len(ca) <= 100_000
            or _sha(ca) != row["ca_sha256"]
            or not _SHA.fullmatch(row["ca_sha256"])
        ):
            raise ValueError("Service public CA bytes differ")
        ssl.create_default_context(cadata=ca.decode("ascii"))
    pins = packet["coredns_image_pins"]
    if (
        type(pins) is not list
        or not 1 <= len(pins) <= 8
        or len(set(pins)) != len(pins)
        or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./_:@+-]{1,511}", value) or "coredns" not in value
            for value in pins
        )
    ):
        raise ValueError("Original bounded CoreDNS image-list selections are required")
    return deepcopy(packet)


def hosts_after(before, packet):
    text = before.decode("ascii")
    if len(before) > 64000 or "\x00" in text or "devsecops-service-links" in text:
        raise ValueError("Hosts preimage contains an existing or ambiguous managed section")
    domains = {urlsplit(row["origin"]).hostname for row in packet["services"].values()}
    for line in text.splitlines():
        if domains & set(line.split("#", 1)[0].split()[1:]):
            raise ValueError("A selected service domain already has a hosts entry")
    marker = "# devsecops-service-links " + packet["binding"]
    lines = (
        [marker + " begin"]
        + [
            row["address"] + " " + urlsplit(row["origin"]).hostname
            for _, row in sorted(packet["services"].items())
        ]
        + [marker + " end"]
    )
    return before + (b"" if before.endswith(b"\n") else b"\n") + "\n".join(lines).encode() + b"\n"


def registries_after(before, packet):
    # JSON is a strict supported YAML subset. Arbitrary YAML anchors, merge
    # keys and duplicate mappings are not rewritten by this bounded helper.
    original = {} if before is None else _json(before)
    if type(original) is not dict:
        raise ValueError("Existing registries configuration is not a strict JSON mapping")
    result = deepcopy(original)
    configs = result.setdefault("configs", {})
    if type(configs) is not dict:
        raise ValueError("Existing registry configs is ambiguous")
    selected = packet["services"]["registry"]
    host = urlsplit(selected["origin"]).netloc
    if host in configs:
        raise ValueError("Selected registry already has foreign configuration")
    mirrors = result.get("mirrors", {})
    if type(mirrors) is not dict or "*" in mirrors or host in mirrors or "*" in configs:
        raise ValueError("Wildcard or selected registry mirrors/configuration are unsupported")
    configs[host] = {"tls": {"ca_file": str(TRUST / packet["run_id"] / "registry-ca.crt")}}
    return _bytes(result)


def corefile_after(before, packet, *, other_data=()):
    if (
        type(before) is not str
        or len(before.encode()) > 256000
        or not re.search(r"(?m)^\s*reload(?:\s+[^{}\n]+)?\s*$", before)
    ):
        raise ValueError("Discovered CoreDNS Corefile must already have its reload plugin")
    domains = [urlsplit(row["origin"]).hostname for row in packet["services"].values()]
    if any(
        "devsecops-service-links" in value or any(domain in value for domain in domains)
        for value in [before, *other_data]
    ):
        raise ValueError("CoreDNS already contains a selected or managed service domain")
    blocks = ["# devsecops-service-links " + packet["binding"] + " begin"]
    for role in ("gitlab", "registry"):
        row = packet["services"][role]
        domain = urlsplit(row["origin"]).hostname
        blocks.append(
            domain
            + ":53 {\n    errors\n    hosts {\n        "
            + row["address"]
            + " "
            + domain
            + "\n        ttl 1\n        no_reverse\n    }\n}"
        )
    blocks.append("# devsecops-service-links " + packet["binding"] + " end")
    return before + ("" if before.endswith("\n") else "\n") + "\n".join(blocks) + "\n"


def discover_coredns(deployments, services, configmaps, packet):
    """Discover the actual selected controller, mount/key and DNS Service."""
    choices = []
    for deployment in deployments.get("items", []):
        template = deployment.get("spec", {}).get("template", {})
        pod = template.get("spec", {})
        for container in pod.get("containers", []):
            if container.get("image") not in packet["coredns_image_pins"]:
                continue
            args = container.get("args", [])
            if type(args) is not list or args.count("-conf") != 1 or args.index("-conf") + 1 >= len(args):
                raise ValueError("Selected CoreDNS image has no unambiguous actual -conf argument")
            selected = args[args.index("-conf") + 1]
            if not selected.startswith("/") or ".." in Path(selected).parts:
                raise ValueError("CoreDNS config argument is outside a fixed mounted file")
            mounts = [
                m
                for m in container.get("volumeMounts", [])
                if selected.startswith(m.get("mountPath", "") + "/") and not m.get("subPath")
            ]
            if len(mounts) != 1:
                raise ValueError("CoreDNS config path does not have one explicit ConfigMap mount")
            mount = mounts[0]
            volumes = [
                v for v in pod.get("volumes", []) if v.get("name") == mount["name"] and "configMap" in v
            ]
            if len(volumes) != 1:
                raise ValueError("CoreDNS selected mount is not exactly one ConfigMap")
            source = volumes[0]["configMap"]
            relative = selected.removeprefix(mount["mountPath"] + "/")
            keys = (
                [r["key"] for r in source.get("items", []) if r.get("path") == relative]
                if "items" in source
                else [relative]
            )
            if len(keys) != 1 or "/" in keys[0]:
                raise ValueError("CoreDNS mounted key is ambiguous")
            choices.append((deployment, container, source["name"], keys[0], pod))
    if len(choices) != 1:
        raise ValueError("Exactly one selected installed CoreDNS controller is required")
    deployment, container, cm_name, key, pod = choices[0]
    metadata = deployment.get("metadata", {})
    if (
        metadata.get("namespace") != "kube-system"
        or not metadata.get("uid")
        or not metadata.get("generation")
    ):
        raise ValueError("CoreDNS controller identity is incomplete")
    cm = configmaps.get(cm_name)
    if (
        not cm
        or cm.get("metadata", {}).get("namespace") != "kube-system"
        or not cm["metadata"].get("uid")
        or not cm["metadata"].get("resourceVersion")
        or key not in cm.get("data", {})
        or cm.get("immutable") is True
    ):
        raise ValueError("Discovered CoreDNS ConfigMap identity/key is incomplete")
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    matched = [
        s
        for s in services.get("items", [])
        if s.get("spec", {}).get("selector")
        and all(labels.get(k) == v for k, v in s["spec"]["selector"].items())
        and any(p.get("port") == 53 and p.get("protocol") == "UDP" for p in s["spec"].get("ports", []))
    ]
    if len(matched) != 1 or not matched[0].get("metadata", {}).get("uid"):
        raise ValueError("CoreDNS needs exactly one observed DNS Service")
    dns = matched[0]
    addresses = dns.get("spec", {}).get("clusterIPs") or [dns.get("spec", {}).get("clusterIP")]
    if len(addresses) != 1 or ipaddress.ip_address(addresses[0]).version != 4:
        raise ValueError("This fixed guest DNS route requires one observed IPv4 ClusterIP")
    extras = [value for selected_key, value in cm.get("data", {}).items() if selected_key != key]
    for volume in pod.get("volumes", []):
        name = volume.get("configMap", {}).get("name")
        if name and name != cm_name and configmaps.get(name):
            extras.extend(configmaps[name].get("data", {}).values())
    changed = deepcopy(cm)
    changed["data"][key] = corefile_after(cm["data"][key], packet, other_data=extras)
    return {
        "deployment": deployment,
        "image": container["image"],
        "configmap": cm,
        "key": key,
        "after": changed,
        "service": dns,
        "dns_address": addresses[0],
    }


def _read(path, *, missing=False, limit=_MAX):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Guest service-link path contains a symlink")
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
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or info.st_nlink != 1
            or info.st_size > limit
        ):
            raise ValueError("Guest service-link file identity or bounds differ")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("Guest file grew beyond its bound")
        return raw


def _metadata(path):
    value = path.stat(follow_symlinks=False)
    return {
        "mode": stat.S_IMODE(value.st_mode),
        "uid": value.st_uid,
        "gid": value.st_gid,
        "device": value.st_dev,
        "inode": value.st_ino,
    }


def _sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, raw, *, before, mode=0o600, metadata=None):
    if _read(path, missing=True) != before:
        raise ValueError("Guest file changed before its exact CAS transition")
    original = _metadata(path) if before is not None else None
    metadata = metadata or original or {"mode": mode, "uid": os.getuid(), "gid": os.getgid()}
    temporary = path.parent / (".service-links-" + uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            os.fchown(stream.fileno(), metadata["uid"], metadata["gid"])
            os.fchmod(stream.fileno(), metadata["mode"])
            stream.flush()
            os.fsync(stream.fileno())
        if _read(path, missing=True) != before or before is not None and _metadata(path) != original:
            raise ValueError("Guest file changed during its exact CAS transition")
        os.replace(temporary, path)
        _sync_dir(path.parent)
        if _read(path) != raw or any(_metadata(path)[key] != metadata[key] for key in ("uid", "gid", "mode")):
            raise ValueError("Guest file transition readback differs")
    finally:
        temporary.unlink(missing_ok=True)


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


def _kubectl(arguments, *, deadline, document=None):
    return _json(
        _run(
            [KUBECTL, "--kubeconfig=" + KUBECONFIG, "--request-timeout=10s", *arguments],
            deadline=deadline,
            stdin=None if document is None else _bytes(document),
        )
    )


def _cluster(packet, deadline):
    namespace = _kubectl(["get", "namespace", "kube-system", "-o", "json"], deadline=deadline)
    if namespace.get("metadata", {}).get("uid") != packet["cluster_uid"]:
        raise ValueError("Original guest Kubernetes cluster UID changed")


def _invocation(deadline):
    value = _run(
        ["systemctl", "show", "rke2-server.service", "--property=InvocationID", "--value"], deadline=deadline
    ).strip()
    if not re.fullmatch(b"[a-f0-9]{32}", value):
        raise ValueError("Original RKE2 systemd invocation is unavailable")
    return value.decode()


def _ready(packet, deadline):
    _cluster(packet, deadline)
    nodes = _kubectl(["get", "nodes", "-o", "json"], deadline=deadline).get("items", [])
    if not nodes or not all(
        any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in n.get("status", {}).get("conditions", [])
        )
        for n in nodes
    ):
        raise ValueError("Original guest node readiness is unobserved")


def _restart(packet, deadline, *, before_invocation):
    _cluster(packet, deadline)
    if _invocation(deadline) != before_invocation:
        raise ValueError("Original RKE2 invocation changed before restart dispatch")
    _run(["systemctl", "restart", "--no-block", "rke2-server.service"], deadline=deadline)
    while time.monotonic() + 1 < deadline:
        try:
            current = _invocation(deadline)
            _ready(packet, deadline)
            if current != before_invocation:
                return {
                    "before_invocation": before_invocation,
                    "after_invocation": current,
                    "cluster_uid": packet["cluster_uid"],
                }
        except (ValueError, subprocess.TimeoutExpired):
            pass
        time.sleep(0.25)
    raise ValueError("Restart was dispatched but same-cluster node readiness remains unobserved")


def _dns(address, domain, *, deadline):
    identifier = int.from_bytes(os.urandom(2), "big")
    question = (
        b"".join(bytes([len(part)]) + part.encode("ascii") for part in domain.split("."))
        + b"\0"
        + struct.pack("!HH", 1, 1)
    )
    query = struct.pack("!HHHHHH", identifier, 0, 1, 0, 0, 0) + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(_remaining(deadline, 2))
        client.connect((address, 53))
        client.settimeout(_remaining(deadline, 2))
        client.send(query)
        client.settimeout(_remaining(deadline, 2))
        raw = client.recv(4096)
    _remaining(deadline)
    if len(raw) < 12 or len(raw) == 4096:
        raise ValueError("Bounded DNS response unavailable")
    ident, flags, questions, answers, _, _ = struct.unpack("!HHHHHH", raw[:12])
    if (
        ident != identifier
        or not flags & 0x8000
        or flags & 0x0200
        or questions != 1
        or raw[12 : 12 + len(question)] != question
    ):
        raise ValueError("DNS response identity differs from exact selected question")
    offset, values = 12 + len(question), []
    for _ in range(answers):
        if offset >= len(raw):
            raise ValueError("DNS answer is truncated")
        if raw[offset] & 0xC0 == 0xC0:
            if raw[offset : offset + 2] != b"\xc0\x0c":
                raise ValueError("DNS answer aliases an unselected name")
            offset += 2
        else:
            if raw[offset : offset + len(question) - 4] != question[:-4]:
                raise ValueError("DNS answer name differs from selected domain")
            offset += len(question) - 4
        if offset + 10 > len(raw):
            raise ValueError("DNS answer is truncated")
        kind, family, _, length = struct.unpack("!HHIH", raw[offset : offset + 10])
        offset += 10
        if kind != 1 or family != 1 or length != 4 or offset + length > len(raw):
            raise ValueError("DNS answer is not an exact IPv4 host response")
        values.append(str(ipaddress.IPv4Address(raw[offset : offset + 4])))
        offset += length
    return {"rcode": flags & 15, "addresses": sorted(set(values))}


def _tls_probe(row, role, *, deadline):
    # getent is isolated in a bounded child; libc DNS never holds the helper
    # past the absolute probe deadline. No alternate address is attempted.
    deadline = min(deadline, time.monotonic() + 5)
    host = urlsplit(row["origin"]).hostname
    resolved = _run(["getent", "ahostsv4", host], deadline=deadline, limit=8192).decode("ascii")
    addresses = {line.split()[0] for line in resolved.splitlines() if line.split()}
    if addresses != {row["address"]}:
        raise ValueError("Guest hostname resolution differs from its exact service route")
    context = ssl.create_default_context(
        cadata=base64.b64decode(row["ca_b64"], validate=True).decode("ascii")
    )
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as plain:
        plain.settimeout(_remaining(deadline))
        plain.connect((row["address"], row["port"]))
        plain.settimeout(_remaining(deadline))
        with context.wrap_socket(plain, server_hostname=host, do_handshake_on_connect=False) as client:
            client.settimeout(_remaining(deadline))
            client.do_handshake()
            route = "/-/readiness" if role == "gitlab" else "/v2/"
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
            first = response.split(b"\r\n", 1)[0]
            match = re.fullmatch(rb"HTTP/1\.[01] ([0-9]{3})(?: [^\r\n]*)?", first)
            status = int(match[1]) if match else None
            if b"\r\n\r\n" not in response or status not in ({200} if role == "gitlab" else {200, 401}):
                raise ValueError("Selected TLS service readiness response is incomplete")
            _remaining(deadline)
            return {
                "origin": row["origin"],
                "connected_address": row["address"],
                "status": status,
                "server_certificate_sha256": _sha(client.getpeercert(binary_form=True)),
                "ca_sha256": row["ca_sha256"],
            }


def _mkdir(path, *, private=False):
    if not path.exists():
        _mkdir(path.parent)
        path.mkdir(mode=0o700 if private else 0o755)
        _sync_dir(path.parent)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Guest directory contains a symlink")
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError("Guest directory has foreign ownership or writable permissions")
    if private and stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Guest journal directory must be private")


def _snapshot(path):
    raw = _read(path, missing=True)
    return {
        "bytes": None if raw is None else base64.b64encode(raw).decode(),
        "sha256": None if raw is None else _sha(raw),
        "metadata": None if raw is None else _metadata(path),
    }


def _raw(snapshot):
    value = snapshot["bytes"]
    raw = None if value is None else base64.b64decode(value, validate=True)
    if (None if raw is None else _sha(raw)) != snapshot["sha256"]:
        raise ValueError("Original private guest preimage changed")
    return raw


def _identity(packet):
    return {
        key: packet[key]
        for key in (
            "schema_version",
            "run_id",
            "binding",
            "cluster_uid",
            "foundation_binding",
            "services",
            "coredns_image_pins",
        )
    }


def _effect_guard(packet, deadline):
    _remaining(deadline)
    # Cleanup is an existing separately admitted scope; the provisioning
    # expiry cannot disable restoration. The current caller deadline and
    # original identity still bound every cleanup effect.
    if packet["action"] == "up" and datetime.now(timezone.utc) >= datetime.fromisoformat(
        packet["expires_at"].replace("Z", "+00:00")
    ):
        raise ValueError("Guest mutation authority expired before effect")
    _cluster(packet, deadline)


def _discover(packet, deadline):
    deployments = _kubectl(["get", "deployments", "-n", "kube-system", "-o", "json"], deadline=deadline)
    services = _kubectl(["get", "services", "-n", "kube-system", "-o", "json"], deadline=deadline)
    maps = _kubectl(["get", "configmaps", "-n", "kube-system", "-o", "json"], deadline=deadline)
    return discover_coredns(
        deployments, services, {row["metadata"]["name"]: row for row in maps.get("items", [])}, packet
    )


def _coredns_current(plan, deadline):
    observed = {}
    for key, kind in (("deployment", "deployment"), ("service", "service"), ("configmap", "configmap")):
        original = plan[key]
        name = original["metadata"]["name"]
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", name):
            raise ValueError("Discovered Kubernetes resource name is invalid")
        current = _kubectl(["get", kind, name, "-n", "kube-system", "-o", "json"], deadline=deadline)
        if (
            current.get("metadata", {}).get("uid") != original["metadata"]["uid"]
            or current["metadata"].get("namespace") != "kube-system"
            or not current["metadata"].get("resourceVersion")
        ):
            raise ValueError("Original DNS resource UID or namespace changed")
        if key != "configmap" and current.get("spec") != original.get("spec"):
            raise ValueError("Original DNS controller or Service selection changed")
        if key == "configmap" and (
            current.get("binaryData") != original.get("binaryData") or current.get("immutable") is True
        ):
            raise ValueError("Original DNS ConfigMap binary data or mutability changed")
        observed[key] = current
    return observed


def _controller_ready(deployment):
    desired = deployment.get("spec", {}).get("replicas", 1)
    status = deployment.get("status", {})
    if (
        not isinstance(desired, int)
        or desired < 1
        or status.get("observedGeneration", 0) < deployment["metadata"]["generation"]
        or any(
            status.get(key, 0) != desired
            for key in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")
        )
    ):
        raise ValueError("Selected DNS controller is not fully ready")


def _file_state(row):
    path = Path(row["path"])
    current = _read(path, missing=True)
    original = _raw(row["original"])
    applied = base64.b64decode(row["after"], validate=True)
    if _sha(applied) != row["after_sha256"]:
        raise ValueError("Planned guest file bytes changed")
    if current == original:
        if current is not None and any(
            _metadata(path)[k] != row["original"]["metadata"][k] for k in ("uid", "gid", "mode")
        ):
            raise ValueError("Original guest file metadata changed")
        return "original"
    if current == applied:
        expected = row.get("applied_metadata")
        if expected and _metadata(path) != expected:
            raise ValueError("Applied guest file inode or metadata changed")
        return "applied"
    raise ValueError("Guest file differs from both original and owned applied state")


def _observe(state, packet, deadline):
    _cluster(packet, deadline)
    files = {key: _file_state(row) for key, row in state["files"].items()}
    actual = _coredns_current(state["dns"], deadline)
    data = actual["configmap"].get("data", {})
    if data == state["dns"]["configmap"].get("data", {}):
        dns = "original"
    elif data == state["dns"]["after"]["data"]:
        dns = "applied"
    else:
        raise ValueError("DNS ConfigMap data has a foreign change")
    pending = state["pending"]
    unresolved = []
    restart = None
    directory = TRUST / packet["run_id"]
    if directory.exists():
        actual_directory = _metadata(directory)
        if directory.is_symlink() or actual_directory != state.get("trust_directory"):
            unresolved.append(state["up_operation_id"] + ":trust-directory-identity")
    if TRUST.exists():
        if TRUST.is_symlink() or _metadata(TRUST) != (
            state.get("trust_parent") or state["trust_parent_original"]
        ):
            unresolved.append(state["up_operation_id"] + ":trust-parent-identity")
    elif state["trust_parent_original"] is not None:
        unresolved.append(state["up_operation_id"] + ":trust-parent-missing")
    if pending:
        label = pending["label"]
        # An API request may still be in flight after its client exits. Only
        # the exact requested postimage closes an unacknowledged API effect.
        if label == "dns-up" and dns != "applied" or label == "dns-down" and dns != "original":
            unresolved.append(pending["operation_id"] + ":" + label)
        if label.startswith("restart-"):
            try:
                observed_invocation = _invocation(deadline)
                if observed_invocation == pending["before_invocation"]:
                    unresolved.append(pending["operation_id"] + ":" + label)
                else:
                    _ready(packet, deadline)
                    if _invocation(deadline) != observed_invocation:
                        raise ValueError("RKE2 invocation changed during reconciliation")
                    restart = {
                        "before_invocation": pending["before_invocation"],
                        "after_invocation": observed_invocation,
                        "cluster_uid": packet["cluster_uid"],
                    }
            except (ValueError, OSError, subprocess.TimeoutExpired):
                unresolved.append(pending["operation_id"] + ":" + label)
    return {
        "files": files,
        "dns": dns,
        "actual": actual,
        "unresolved": unresolved,
        "reconciled_restart": restart,
    }


def _verify(state, packet, deadline, *, restored):
    expected = "original" if restored else "applied"
    while True:
        observation = _observe(state, packet, deadline)
        if (
            observation["unresolved"]
            or set(observation["files"].values()) != {expected}
            or observation["dns"] != expected
        ):
            raise ValueError("Guest mappings have not reached the required exact state")
        _ready(packet, deadline)
        _controller_ready(observation["actual"]["deployment"])
        observed = {
            role: _dns(state["dns"]["dns_address"], urlsplit(row["origin"]).hostname, deadline=deadline)
            for role, row in packet["services"].items()
        }
        wanted = (
            state["dns_baseline"]
            if restored
            else {
                role: {"rcode": 0, "addresses": [row["address"]]} for role, row in packet["services"].items()
            }
        )
        if observed == wanted:
            break
        _remaining(deadline)
        time.sleep(min(0.25, _remaining(deadline)))
    return {
        "dns": observed,
        "tls": {}
        if restored
        else {role: _tls_probe(row, role, deadline=deadline) for role, row in packet["services"].items()},
    }


def _receipt(state, packet, observation, *, verification=None):
    resources = []
    for key, row in state["files"].items():
        original = row["original"]["bytes"] is not None
        current = observation["files"][key]
        resources.append(
            {
                "kind": "guest-service-link-" + key,
                "identity": {
                    "original_sha256": row["original"]["sha256"],
                    "applied_sha256": row["after_sha256"],
                },
                "disposition": "preserve" if original else "remove",
                "state": ("preserved" if original else "absent") if current == "original" else "present",
            }
        )
    resources.append(
        {
            "kind": "guest-service-link-dns",
            "identity": {
                "uid": state["dns"]["configmap"]["metadata"]["uid"],
                "original_data_sha256": _sha(_bytes(state["dns"]["configmap"].get("data", {}))),
                "applied_data_sha256": _sha(_bytes(state["dns"]["after"]["data"])),
            },
            "disposition": "preserve",
            "state": "preserved" if observation["dns"] == "original" else "modified",
        }
    )
    unresolved = list(observation["unresolved"])
    for label, path, original in (
        ("trust-directory", TRUST / packet["run_id"], None),
        ("trust-parent", TRUST, state["trust_parent_original"]),
    ):
        resources.append(
            {
                "kind": "guest-service-link-" + label,
                "identity": state.get(label.replace("-", "_")),
                "disposition": "preserve" if original else "remove",
                "state": "preserved"
                if original and path.exists()
                else "present"
                if path.exists()
                else "absent",
            }
        )
    resources.append(
        {
            "kind": "guest-service-link-private-journal",
            "identity": packet["binding"],
            "disposition": "preserve",
            "state": "preserved",
        }
    )
    return {
        "binding": packet["binding"],
        "operation_id": packet["operation_id"],
        "stage": state["stage"],
        "cluster_uid": packet["cluster_uid"],
        "resources": resources,
        "cleanup_eligible": not unresolved,
        "observed_partial": not unresolved and state["stage"] not in ("ready", "restored"),
        "ready": state["stage"] == "ready" and not unresolved and verification is not None,
        "complete_cleanup": state["stage"] == "restored" and not unresolved and verification is not None,
        "unresolved_operations": unresolved,
        "verification": verification,
        "mutation_effect": "not_applied"
        if packet["action"] == "reconcile"
        else "unknown"
        if unresolved
        else "applied",
    }


def execute(packet):
    """Run one original, bounded guest operation; never resume an uncertain up.

    A fresh down request may restore a partly applied up only after every
    pending effect has an exact observed terminal state. Reconcile performs
    reads only and does not replay writes or claim missing receipts as success.
    """
    packet = validate_packet(packet)
    deadline = time.monotonic() + packet["timeout"]
    _cluster(packet, deadline)
    root = ROOT / packet["run_id"]
    if packet["action"] == "up":
        _mkdir(ROOT)
        if root.exists():
            raise ValueError("Original guest service-link operation already exists; inspect before cleanup")
        _mkdir(root, private=True)
        _write(root / "owner.json", _bytes(_identity(packet)), before=None)
        _write(root / "lock", b"", before=None)
    if _json(_read(root / "owner.json")) != _identity(packet):
        raise ValueError("Guest service-link original ownership differs")
    _mkdir(root, private=True)
    if _read(root / "lock") != b"":
        raise ValueError("Original guest journal lock changed")
    fd = os.open(root / "lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.fstat(fd).st_ino != (root / "lock").stat().st_ino or os.fstat(fd).st_nlink != 1:
            raise ValueError("Original guest journal lock was replaced")
        if _json(_read(root / "owner.json")) != _identity(packet):
            raise ValueError("Guest service-link ownership changed after locking")
        state_path = root / "state.json"
        state = None

        def save():
            _write(state_path, _bytes(state), before=_read(state_path, missing=True))

        def effect(label, operation, *, extra=None, result_key=None):
            _effect_guard(packet, deadline)
            state["pending"] = {"label": label, "operation_id": packet["operation_id"], **(extra or {})}
            save()  # durable before a file/API/restart effect, including intent to remove
            _effect_guard(packet, deadline)
            result = operation()
            if result_key is not None:
                state[result_key] = result
            state["pending"] = None
            save()
            return result

        if packet["action"] == "up":
            plan = _discover(packet, deadline)
            _controller_ready(plan["deployment"])
            ca_path = TRUST / packet["run_id"] / "registry-ca.crt"
            if ca_path.parent.exists():
                raise ValueError("Selected trust directory already exists")
            files = {}
            for key, path in (("hosts", HOSTS), ("registries", REGISTRIES), ("ca", ca_path)):
                before = _snapshot(path)
                raw = _raw(before)
                if key == "hosts":
                    if raw is None:
                        raise ValueError("Original guest hosts file is missing")
                    after = hosts_after(raw, packet)
                elif key == "registries":
                    after = registries_after(raw, packet)
                else:
                    if raw is not None:
                        raise ValueError("Selected run trust file already exists")
                    after = base64.b64decode(packet["services"]["registry"]["ca_b64"], validate=True)
                files[key] = {
                    "path": str(path),
                    "original": before,
                    "after": base64.b64encode(after).decode(),
                    "after_sha256": _sha(after),
                }
            baseline = {
                role: _dns(plan["dns_address"], urlsplit(row["origin"]).hostname, deadline=deadline)
                for role, row in packet["services"].items()
            }
            if any(value["addresses"] for value in baseline.values()):
                raise ValueError("Selected service domain already resolves through cluster DNS")
            state = {
                "schema_version": 1,
                "identity": _identity(packet),
                "up_operation_id": packet["operation_id"],
                "stage": "prepared",
                "pending": None,
                "files": files,
                "dns": plan,
                "dns_baseline": baseline,
                "restart_up": None,
                "restart_down": None,
                "trust_directory": None,
                "trust_parent_original": _metadata(TRUST) if TRUST.exists() else None,
                "trust_parent": None,
            }
            save()
        else:
            state = _json(_read(state_path))
            if state.get("identity") != _identity(packet):
                raise ValueError("Guest service-link private state identity changed")
        observation = _observe(state, packet, deadline)
        if packet["action"] == "reconcile":
            verified = (
                _verify(state, packet, deadline, restored=state["stage"] == "restored")
                if state["stage"] in ("ready", "restored")
                else None
            )
            return _receipt(state, packet, observation, verification=verified)
        if observation["unresolved"]:
            return _receipt(state, packet, observation)
        try:
            if packet["action"] == "up":
                state["stage"] = "applying"
                save()

                def prepare_trust():
                    _mkdir(TRUST)
                    state["trust_parent"] = _metadata(TRUST)
                    _mkdir(ca_path.parent, private=True)
                    return _metadata(ca_path.parent)

                effect("trust-directory-up", prepare_trust, result_key="trust_directory")
                for key in ("ca", "hosts", "registries"):
                    row = state["files"][key]
                    path, after = Path(row["path"]), base64.b64decode(row["after"], validate=True)

                    def write_file(path=path, after=after, row=row):
                        _write(
                            path, after, before=_raw(row["original"]), metadata=row["original"]["metadata"]
                        )
                        row["applied_metadata"] = _metadata(path)

                    effect(key + "-up", write_file)
                current = _coredns_current(plan, deadline)["configmap"]
                if current.get("data", {}) != plan["configmap"].get("data", {}):
                    raise ValueError("DNS configuration changed before apply")
                updated = deepcopy(current)
                updated["data"] = plan["after"]["data"]

                def apply_dns():
                    _kubectl(["replace", "-f", "-", "-o", "json"], deadline=deadline, document=updated)
                    if _coredns_current(plan, deadline)["configmap"].get("data") != updated["data"]:
                        raise ValueError("DNS apply readback differs")

                effect("dns-up", apply_dns)
                before = _invocation(deadline)
                effect(
                    "restart-up",
                    lambda: _restart(packet, deadline, before_invocation=before),
                    extra={"before_invocation": before},
                    result_key="restart_up",
                )
                verified = _verify(state, packet, deadline, restored=False)
                _effect_guard(packet, deadline)
                state["stage"] = "ready"
                save()
            else:
                if state["stage"] == "restored":
                    return _receipt(
                        state,
                        packet,
                        observation,
                        verification=_verify(state, packet, deadline, restored=True),
                    )
                if observation["reconciled_restart"] is not None:
                    key = "restart_up" if state["pending"]["label"] == "restart-up" else "restart_down"
                    state[key] = observation["reconciled_restart"]
                state["stage"] = "restoring"
                state["pending"] = None  # observed terminal prior effect only; never drops unresolved above
                save()
                current = observation["actual"]["configmap"]
                if observation["dns"] == "applied":
                    restored = deepcopy(current)
                    restored["data"] = state["dns"]["configmap"].get("data", {})

                    def restore_dns():
                        _kubectl(["replace", "-f", "-", "-o", "json"], deadline=deadline, document=restored)
                        if (
                            _coredns_current(state["dns"], deadline)["configmap"].get("data", {})
                            != restored["data"]
                        ):
                            raise ValueError("DNS restoration readback differs")

                    effect("dns-down", restore_dns)
                for key in ("hosts", "registries", "ca"):
                    row = state["files"][key]
                    if _file_state(row) == "original":
                        continue

                    def restore_file(row=row):
                        path = Path(row["path"])
                        if _file_state(row) != "applied":
                            raise ValueError("Owned file changed before restoration")
                        original = _raw(row["original"])
                        if original is None:
                            path.unlink()
                            _sync_dir(path.parent)
                            if _read(path, missing=True) is not None:
                                raise ValueError("Owned file removal readback differs")
                        else:
                            _write(
                                path,
                                original,
                                before=base64.b64decode(row["after"], validate=True),
                                metadata=row["original"]["metadata"],
                            )

                    effect(key + "-down", restore_file)
                directory = TRUST / packet["run_id"]
                if directory.exists():

                    def remove_directory():
                        if directory.is_symlink() or _metadata(directory) != state["trust_directory"]:
                            raise ValueError("Original trust directory identity changed")
                        directory.rmdir()
                        _sync_dir(directory.parent)

                    effect("trust-directory-down", remove_directory)
                if state["trust_parent_original"] is None and TRUST.exists():

                    def remove_parent():
                        if TRUST.is_symlink() or _metadata(TRUST) != state["trust_parent"]:
                            raise ValueError("Original trust parent directory identity changed")
                        TRUST.rmdir()
                        _sync_dir(TRUST.parent)

                    effect("trust-parent-down", remove_parent)
                # One fresh restart is required even after a partly applied up;
                # it admits restored trust only after same-cluster readiness.
                if state["restart_down"] is None:
                    before = _invocation(deadline)
                    effect(
                        "restart-down",
                        lambda: _restart(packet, deadline, before_invocation=before),
                        extra={"before_invocation": before},
                        result_key="restart_down",
                    )
                elif _invocation(deadline) != state["restart_down"]["after_invocation"]:
                    raise ValueError("RKE2 invocation changed after the recorded cleanup restart")
                verified = _verify(state, packet, deadline, restored=True)
                _effect_guard(packet, deadline)
                state["stage"] = "restored"
                save()
            return _receipt(state, packet, _observe(state, packet, deadline), verification=verified)
        except (ValueError, OSError, subprocess.TimeoutExpired):
            # Leave the durable last intent in place. Do not include raw error
            # messages or private configuration in a public remote receipt.
            try:
                observation = _observe(state, packet, deadline)
            except (ValueError, OSError, subprocess.TimeoutExpired):
                raise ValueError(
                    "Guest operation has an unresolved durable intent; exact read-only reconciliation required"
                ) from None
            return _receipt(state, packet, observation)
    finally:
        os.close(fd)
