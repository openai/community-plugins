"""Package-pinned, nonroot runner supervisor. No disk receipt enrolls a process.

The host owns the original nonce and VM scope. The live guest supervisor owns
all child handles and cleanup state; losing it requires original VM disposal.
This module has no import-time effects and is bundled by the fixed VM worker.
"""

import json
import shlex

CONTEXT_TYPE = None

GUEST_PROGRAM = r"""
import base64
import ctypes
import hashlib
import hmac
import json
import math
import os
import platform
from pathlib import Path
import pwd
import re
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
from uuid import UUID

HOME = Path("/home/gitlab-runner")
ROOT = HOME / ".local/share/devsecops"
BOOT = Path("/proc/sys/kernel/random/boot_id")
RUNNER = Path("/usr/bin/gitlab-runner")
UID = 10001
MAX = 2400000


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise ValueError("Runner service deadline expired")
    return value


def uuid(value):
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("Canonical original UUID required")
    return value


def shape(value, keys):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise ValueError("Closed runner service selection required")


def paths(run_id):
    private = ROOT / UUID(run_id).hex
    return {
        "private": str(private),
        "inputs": str(private / "inputs"),
        "output": str(HOME / "builds" / ("factory-" + run_id) / "publication"),
        "runtime": str(HOME / "builds" / UUID(run_id).hex),
    }


def validate(packet):
    common = "schema_version action run_id operation_id runner_vm timeout service_nonce"
    action = packet.get("action") if type(packet) is dict else None
    shape(packet, common + (" installation" if action == "install" else " expected_process"))
    if (
        type(packet["schema_version"]) is not int
        or packet["schema_version"] != 1
        or action not in ("install", "observe", "down", "reconcile")
    ):
        raise ValueError("Unsupported runner service action")
    uuid(packet["run_id"])
    uuid(packet["service_nonce"])
    operation = packet["operation_id"]
    if type(operation) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", operation):
        raise ValueError("Original operation identity required")
    seconds = packet["timeout"]
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise ValueError("Finite bounded service deadline required")
    vm = packet["runner_vm"]
    shape(
        vm,
        "binding generation guest_boot_id guest_uid guest_gid root_identity disk_identity runtime_id network_id ssh_host_key_sha256",
    )
    if (
        vm["generation"] != "runner-vm-" + packet["run_id"]
        or type(vm["guest_uid"]) is not int
        or vm["guest_uid"] != UID
        or type(vm["guest_gid"]) is not int
        or vm["guest_gid"] != UID
    ):
        raise ValueError("Original separate nonroot runner VM required")
    uuid(vm["guest_boot_id"])
    for name in ("binding", "runtime_id", "network_id", "ssh_host_key_sha256"):
        if type(vm[name]) is not str or not re.fullmatch("[a-f0-9]{64}", vm[name]):
            raise ValueError("Complete VM pins required")
    for name in ("root_identity", "disk_identity"):
        shape(vm[name], "device inode")
        if any(type(v) is not int or v < 0 for v in vm[name].values()) or vm[name]["inode"] == 0:
            raise ValueError("Original VM inode identity required")
    if action == "install":
        validate_install(packet["installation"], packet["run_id"])
    elif packet["expected_process"] is not None and type(packet["expected_process"]) is not dict:
        raise ValueError("Original resident process observation required")
    return packet


def validate_install(value, run_id):
    shape(
        value,
        "runner_id runner_token description origin entries environment secrets service_trust route_trust guest_package guest_paths",
    )
    if (
        type(value["runner_id"]) is not int
        or value["runner_id"] <= 0
        or type(value["runner_token"]) is not str
        or not re.fullmatch(r"[A-Za-z0-9_.-]{20,8192}", value["runner_token"])
        or type(value["description"]) is not str
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,180}", value["description"])
        or type(value["origin"]) is not str
        or not re.fullmatch(r"https://gitlab\.[a-z0-9-]{1,63}\.test:8444", value["origin"])
        or value["guest_paths"] != paths(run_id)
    ):
        raise ValueError("Original runner registration or private guest paths differ")
    public = value["environment"]
    shape(
        public,
        "DEVSECOPS_GIT_INVOCATION DEVSECOPS_GIT_INVOCATION_SHA256 DEVSECOPS_PYTHON DEVSECOPS_PLUGIN_ROOT",
    )
    if (
        not re.fullmatch("[a-f0-9]{64}", public["DEVSECOPS_GIT_INVOCATION_SHA256"])
        or not public["DEVSECOPS_GIT_INVOCATION"].startswith(paths(run_id)["inputs"] + "/")
        or any(
            not isinstance(public[k], str)
            or not re.fullmatch("/opt/devsecops/[A-Za-z0-9_./-]+", public[k])
            or ".." in Path(public[k]).parts
            for k in ("DEVSECOPS_PYTHON", "DEVSECOPS_PLUGIN_ROOT")
        )
    ):
        raise ValueError("Fixed runner invocation and prebuilt tools required")
    secrets = value["secrets"]
    if type(secrets) is not dict or len(secrets) > 32:
        raise ValueError("Bounded explicitly selected secrets required")
    for key, text in secrets.items():
        if (
            not re.fullmatch("DEVSECOPS_[A-Z0-9_]{1,100}", key)
            or key in public
            or type(text) is not str
            or not 1 <= len(text) <= 8192
            or any(ord(c) < 32 or ord(c) > 126 for c in text)
        ):
            raise ValueError("Private runner environment differs")
    entries = value["entries"]
    if type(entries) is not dict or not 1 <= len(entries) <= 80:
        raise ValueError("Bounded selected input files required")
    total = 0
    for name, row in entries.items():
        shape(row, "content sha256 private")
        path = Path(name)
        if (
            not name.startswith(paths(run_id)["inputs"] + "/")
            or str(path) != name
            or ".." in path.parts
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
            or type(row["private"]) is not bool
        ):
            raise ValueError("Runner input escaped its private selection")
        raw = base64.b64decode(row["content"], validate=True)
        total += len(raw)
        if len(raw) > 1000000 or sha(raw) != row["sha256"] or total > 1500000:
            raise ValueError("Runner input bytes differ or exceed bound")
    if (
        public["DEVSECOPS_GIT_INVOCATION"] not in entries
        or entries[public["DEVSECOPS_GIT_INVOCATION"]]["sha256"] != public["DEVSECOPS_GIT_INVOCATION_SHA256"]
    ):
        raise ValueError("Invocation is not the exact installed input")
    trust = value["service_trust"]
    if (
        type(trust) is not dict
        or not {"gitlab", "registry"} <= set(trust)
        or not set(trust) <= {"gitlab", "registry", "external"}
    ):
        raise ValueError("Separate exact service trust required")
    for role, row in trust.items():
        shape(row, "path sha256")
        if (
            row["path"] != paths(run_id)["inputs"] + "/service-trust/" + role + "-ca.pem"
            or row["path"] not in entries
            or entries[row["path"]]["sha256"] != row["sha256"]
        ):
            raise ValueError("Selected CA path or bytes differ")
    route = value["route_trust"]
    shape(route, "files environment empty_directory")
    if type(route["files"]) is not dict or not 3 <= len(route["files"]) <= 8:
        raise ValueError("Original bounded route trust facts required")
    for name, row in route["files"].items():
        shape(row, "path sha256 bytes device inode uid gid mode")
        if (
            name
            not in {
                "gitlab-ca.pem",
                "registry-ca.pem",
                "external-ca.pem",
                "registry1-ca.pem",
                "p1docs-ca.pem",
                "ca-bundle.pem",
                "environment.json",
            }
            or row["path"] != paths(run_id)["inputs"] + "/service-trust/" + name
            or not re.fullmatch("[a-f0-9]{64}", row["sha256"])
            or any(
                type(row[k]) is not int or row[k] < 0
                for k in ("bytes", "device", "inode", "uid", "gid", "mode")
            )
            or not 0 < row["bytes"] <= 1000000
            or row["inode"] == 0
            or row["uid"] != UID
            or row["gid"] != UID
            or row["mode"] != 0o600
        ):
            raise ValueError("Original route trust file identity differs")
    bundle = paths(run_id)["inputs"] + "/service-trust/ca-bundle.pem"
    expected_env = {
        name: bundle for name in ("SSL_CERT_FILE", "GIT_SSL_CAINFO", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE")
    }
    expected_env["SSL_CERT_DIR"] = paths(run_id)["inputs"] + "/service-trust/empty-ca-directory"
    expected_env.update(
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
    empty = route["empty_directory"]
    shape(empty, "path device inode uid gid mode")
    if (
        empty["path"] != expected_env["SSL_CERT_DIR"]
        or any(type(empty[k]) is not int or empty[k] < 0 for k in ("device", "inode", "uid", "gid", "mode"))
        or empty["inode"] == 0
        or empty["uid"] != UID
        or empty["gid"] != UID
        or empty["mode"] != 0o700
    ):
        raise ValueError("Original empty private CA directory required")
    if route["environment"] != expected_env:
        raise ValueError("Fixed private CA environment required")
    if "ca-bundle.pem" not in route["files"]:
        raise ValueError("Original route CA bundle required")
    for row in trust.values():
        original = route["files"].get(Path(row["path"]).name)
        if original is None or any(original[k] != row[k] for k in ("path", "sha256")):
            raise ValueError("Selected service trust differs from original routes")
    package = value["guest_package"]
    shape(package, "root manifest_sha256 plugin_id plugin_version")
    if (
        package["root"] != public["DEVSECOPS_PLUGIN_ROOT"]
        or not re.fullmatch("[a-f0-9]{64}", package["manifest_sha256"])
        or not isinstance(package["plugin_id"], str)
        or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._+-]{0,119}@[A-Za-z0-9][A-Za-z0-9._+-]{0,119}", package["plugin_id"]
        )
        or not isinstance(package["plugin_version"], str)
        or not re.fullmatch("[A-Za-z0-9_.-]{1,100}", package["plugin_version"])
    ):
        raise ValueError("Actual current package identity required")


def identity(path):
    st = path.lstat()
    return {
        "device": st.st_dev,
        "inode": st.st_ino,
        "uid": st.st_uid,
        "gid": st.st_gid,
        "mode": stat.S_IMODE(st.st_mode),
    }


def safe(path):
    if path.resolve() != path or path.is_symlink():
        raise ValueError("Runner path changed or contains a symlink")
    return path


def directory(path):
    safe(path)
    row = identity(path)
    if not path.is_dir() or row["uid"] != UID or row["gid"] != UID or row["mode"] & 0o077:
        raise ValueError("Original private runner directory required")
    return row


def read(path, limit=MAX, *, owner=UID, private=True):
    safe(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_nlink != 1
            or st.st_uid != owner
            or st.st_mode & (0o077 if private else 0o022)
            or st.st_size > limit
        ):
            raise ValueError("Bounded original runner file required")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (
        len(raw) > limit
        or (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        or identity(path)["inode"] != st.st_ino
    ):
        raise ValueError("Runner file changed during observation")
    return raw


def immutable_path(path):
    path = path.resolve(strict=True)
    for parent in (path, *path.parents):
        row = identity(parent)
        if row["uid"] != 0 or row["mode"] & 0o022:
            raise ValueError("Prebuilt root-owned immutable tool or package required")
    return path


def immutable(path, limit=128000000):
    path = immutable_path(path)
    return path, sha(read(path, limit, owner=0, private=False))


def guard(packet, deadline):
    remaining(deadline)
    user = pwd.getpwuid(os.getuid())
    if (
        os.getuid() != UID
        or os.geteuid() != UID
        or os.getgid() != UID
        or os.getegid() != UID
        or set(os.getgroups()) - {UID}
        or user.pw_name != "gitlab-runner"
        or user.pw_dir != str(HOME)
        or BOOT.read_text().strip() != packet["runner_vm"]["guest_boot_id"]
    ):
        raise ValueError("Original nonroot guest identity changed")
    if sys.platform != "linux" or platform.machine() != "x86_64":
        raise ValueError("Prebuilt Linux amd64 runner guest required")
    status = Path("/proc/self/status").read_bytes()
    if len(status) > 64000:
        raise ValueError("Guest process status exceeds bound")
    lines = status.decode("ascii").splitlines()
    flags = [line.split(":", 1)[1].strip() for line in lines if line.startswith("NoNewPrivs:")]
    for name in ("CapEff", "CapPrm", "CapAmb"):
        caps = [line.split(":", 1)[1].strip() for line in lines if line.startswith(name + ":")]
        if len(caps) != 1 or not re.fullmatch("0{16}", caps[0]):
            raise ValueError("Unprivileged original supervisor required")
    if flags != ["0"]:
        raise ValueError("Prebuilt separate guest uidmap prerequisites unavailable")
    for path in (Path(paths(packet["run_id"])["private"]), Path(paths(packet["run_id"])["inputs"])):
        directory(path)


def proc(pid):
    root = Path("/proc") / str(pid)
    raw = (root / "stat").read_bytes()
    fields = raw[raw.rfind(b")") + 2 :].split()
    if len(fields) < 20:
        raise ValueError("Incomplete original process observation")
    status = (root / "status").read_text()
    uid = re.findall(r"^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$", status, re.M)
    gid = re.findall(r"^Gid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$", status, re.M)
    if len(uid) != 1 or len(gid) != 1:
        raise ValueError("Incomplete process account observation")
    exe = Path(os.readlink(root / "exe"))
    # Read the kernel's executable reference itself: a job may have a different
    # mount namespace. Never resolve its pathname against this host namespace.
    fd = os.open(root / "exe", os.O_RDONLY)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 128000000:
            raise ValueError("Bounded original executable observation required")
        digest = hashlib.sha256()
        count = 0
        while True:
            block = stream.read(65536)
            if not block:
                break
            count += len(block)
            if count > 128000000:
                raise ValueError("Executable observation exceeds bound")
            digest.update(block)
        after = os.fstat(stream.fileno())
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("Original executable changed during observation")
    argv = (root / "cmdline").read_bytes()
    if len(argv) > 262144:
        raise ValueError("Process arguments exceed fixed observation bound")
    return {
        "pid": pid,
        "start_ticks": int(fields[19]),
        "boot_id": BOOT.read_text().strip(),
        "uid": [int(x) for x in uid[0]],
        "gid": [int(x) for x in gid[0]],
        "executable": str(exe),
        "executable_sha256": digest.hexdigest(),
        "argv_sha256": sha(argv),
    }


def exited(fd):
    poll = select.poll()
    poll.register(fd, select.POLLIN)
    return bool(poll.poll(0))


def open_process(pid):
    fd = os.pidfd_open(pid, 0)
    try:
        value = proc(pid)
        if exited(fd):
            raise ValueError("Process ceased during identity observation")
        return fd, value
    except BaseException:
        os.close(fd)
        raise


def send(fd, value):
    signal.pidfd_send_signal(fd, value, None, 0)


def receive(sock, deadline):
    data = bytearray()
    while True:
        sock.settimeout(remaining(deadline))
        block = sock.recv(min(65536, MAX + 1 - len(data)))
        if not block:
            raise ValueError("Incomplete runner control response")
        data.extend(block)
        if len(data) > MAX:
            raise ValueError("Runner control message exceeds bound")
        if b"\n" in data:
            line, tail = data.split(b"\n", 1)
            if tail:
                raise ValueError("Extra runner control data rejected")
            return json.loads(line)


class Supervisor:
    def __init__(self, packet, deadline):
        self.packet = packet
        self.deadline = deadline
        self.nonce = packet["service_nonce"]
        self.root = Path(paths(packet["run_id"])["private"])
        self.socket_path = self.root / "runner.sock"
        self.files, self.dirs, self.handles = {}, {}, {}
        self.file_digests = {}
        self.pending_paths = set()
        self.removed = []
        self.down_operation = None
        self.runner = None
        self.runner_identity = None
        self.manager = None
        self.ready = False
        self.closed = False
        self.unresolved = []
        self.failure = None
        self.installation = packet["installation"]
        self.trust_rows = self.installation["route_trust"]["files"]
        self.empty_trust = self.installation["route_trust"]["empty_directory"]
        self.registration = self.installation["runner_id"]
        self.process_identity = None

    def write(self, path, raw):
        remaining(self.deadline)
        parent = path.parent
        pending = []
        while not parent.exists():
            pending.append(parent)
            parent = parent.parent
        directory(parent)
        for item in reversed(pending):
            self.pending_paths.add(str(item))
            os.mkdir(item, 0o700)
            self.dirs[str(item)] = identity(item)
            self.pending_paths.remove(str(item))
        safe(path.parent)
        self.pending_paths.add(str(path))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        # Retain ownership before the first write. A partial write remains ours;
        # cleanup compares its original inode, never a guessed content receipt.
        st = os.fstat(fd)
        self.files[str(path)] = {
            "device": st.st_dev,
            "inode": st.st_ino,
            "uid": st.st_uid,
            "gid": st.st_gid,
            "mode": stat.S_IMODE(st.st_mode),
        }
        self.pending_paths.remove(str(path))
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if identity(path) != self.files[str(path)]:
            raise ValueError("New runner file identity changed")
        self.file_digests[str(path)] = sha(raw)

    def preflight(self):
        package = self.installation["guest_package"]
        root = Path(package["root"])
        manifest = root / "SHA256SUMS"
        immutable_path(manifest)
        raw_manifest = read(manifest, 256000, owner=0, private=False)
        if sha(raw_manifest) != package["manifest_sha256"]:
            raise ValueError("Guest installed package manifest differs")
        lines = raw_manifest.decode("ascii").splitlines()
        if not 1 <= len(lines) <= 1024:
            raise ValueError("Guest package member count differs")
        seen, total = set(), 0
        for line in lines:
            remaining(self.deadline)
            match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_./-]+)", line)
            if (
                not match
                or match[2] in seen
                or match[2] == "SHA256SUMS"
                or Path(match[2]).is_absolute()
                or ".." in Path(match[2]).parts
                or str(Path(match[2])) != match[2]
            ):
                raise ValueError("Closed immutable package members required")
            seen.add(match[2])
            member = immutable_path(root / match[2])
            raw = read(member, 16000000, owner=0, private=False)
            total += len(raw)
            if total > 128000000 or sha(raw) != match[1]:
                raise ValueError("Guest package member differs or exceeds aggregate bound")
        actual, directories, queue = set(), 0, [root]
        while queue:
            remaining(self.deadline)
            directory_path = queue.pop()
            immutable_path(directory_path)
            directories += 1
            if directories > 1024:
                raise ValueError("Guest package directory bound exceeded")
            with os.scandir(directory_path) as entries:
                for entry in entries:
                    remaining(self.deadline)
                    if entry.is_symlink():
                        raise ValueError("Guest package contains a symlink")
                    if entry.is_dir(follow_symlinks=False):
                        queue.append(Path(entry.path))
                        if len(queue) + directories > 1024:
                            raise ValueError("Guest package directory bound exceeded")
                    elif entry.is_file(follow_symlinks=False):
                        actual.add(str(Path(entry.path).relative_to(root)))
                        if len(actual) > 1025:
                            raise ValueError("Guest package file bound exceeded")
                    else:
                        raise ValueError("Guest package contains a nonregular member")
        if actual != seen | {"SHA256SUMS"}:
            raise ValueError("Guest package has unmanifested or missing files")
        if ".codex-plugin/plugin.json" not in seen:
            raise ValueError("Original package manifest omits plugin identity")
        plugin = json.loads(read(root / ".codex-plugin/plugin.json", 64000, owner=0, private=False))
        if (
            plugin.get("name") != package["plugin_id"].split("@")[0]
            or plugin.get("version") != package["plugin_version"]
        ):
            raise ValueError("Guest plugin identity differs")
        immutable(Path(self.installation["environment"]["DEVSECOPS_PYTHON"]))
        binary, digest = immutable(RUNNER)
        version = subprocess.run(
            [str(binary), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=min(10, remaining(self.deadline)),
            check=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(HOME)},
        )
        if len(version.stdout) > 64000 or not re.search(rb"(?m)^Version:\s+19\.5\.1\s*$", version.stdout):
            raise ValueError("Prebuilt GitLab Runner 19.5.1 required")
        self.binary, self.binary_sha = str(binary), digest

    def install(self):
        guard(self.packet, self.deadline)
        self.preflight()
        trust_paths = {row["path"] for row in self.installation["service_trust"].values()}
        for name, row in self.installation["entries"].items():
            raw = base64.b64decode(row["content"], validate=True)
            path = Path(name)
            if name in trust_paths:
                if sha(read(path)) != row["sha256"]:
                    raise ValueError("Original route-owned CA bytes changed")
            else:
                self.write(path, raw)
        route = self.installation["route_trust"]
        for row in route["files"].values():
            path = Path(row["path"])
            raw = read(path, 1000000)
            if (
                identity(path) != {k: row[k] for k in ("device", "inode", "uid", "gid", "mode")}
                or len(raw) != row["bytes"]
                or sha(raw) != row["sha256"]
            ):
                raise ValueError("Original route-owned trust file changed")
        self.audit_empty_trust()
        env = {
            "HOME": str(HOME),
            "USER": "gitlab-runner",
            "LOGNAME": "gitlab-runner",
            "PATH": "/opt/devsecops/bin:/usr/local/bin:/usr/bin:/bin",
            **self.installation["environment"],
            **self.installation["secrets"],
            **route["environment"],
        }
        config = self.root / "runner-service/config.toml"

        def quote(value):
            return json.dumps(value, ensure_ascii=True)

        body = (
            "concurrent = 1\ncheck_interval = 3\n[[runners]]\nname = "
            + quote(self.installation["description"])
            + "\nurl = "
            + quote(self.installation["origin"])
            + "\nid = "
            + str(self.registration)
            + "\ntoken = "
            + quote(self.installation["runner_token"])
            + '\nexecutor = "shell"\nshell = "bash"\n'
            + "builds_dir = "
            + quote(str(HOME / "builds"))
            + "\nrequest_concurrency = 1\ntls-ca-file = "
            + quote(self.installation["service_trust"]["gitlab"]["path"])
            + "\nenvironment = ["
            + ",".join(
                quote(k + "=" + v)
                for k, v in sorted(env.items())
                if k.startswith("DEVSECOPS_") or k in route["environment"]
            )
            + "]\n"
        )
        self.write(config, body.encode())
        guard(self.packet, self.deadline)
        self.runner = subprocess.Popen(
            [self.binary, "run", "--config", str(config), "--working-directory", str(HOME)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            close_fds=True,
        )
        fd, observed = open_process(self.runner.pid)
        self.runner_identity = observed
        if (
            observed["executable"] != self.binary
            or observed["executable_sha256"] != self.binary_sha
            or observed["uid"] != [UID] * 4
            or observed["gid"] != [UID] * 4
        ):
            os.close(fd)
            raise ValueError("Actual runner process identity differs")
        self.handles[self.runner.pid] = (fd, observed)
        manager = config.parent / ".runner_system_id"
        while True:
            remaining(self.deadline)
            if exited(fd):
                raise ValueError("Runner exited before manager identity observation")
            if manager.exists():
                raw = read(manager, 100, private=False).strip()
                if not re.fullmatch(rb"[sr]_[A-Za-z0-9]{12}", raw):
                    raise ValueError("Actual runner manager identity differs")
                self.files[str(manager)] = identity(manager)
                self.file_digests[str(manager)] = sha(read(manager, 100, private=False))
                self.manager = raw.decode()
                break
            time.sleep(min(0.05, remaining(self.deadline)))
        self.ready = True
        # Secrets remain only in the private config and the running process.
        self.installation = None

    def processes(self):
        return {
            "supervisor": self.process_identity,
            "runner": self.runner_identity,
            "control_socket": self.socket_identity,
            "service_nonce_sha256": sha(self.nonce.encode()),
        }

    def audit_live(self):
        if proc(os.getpid()) != self.process_identity:
            raise ValueError("Original supervisor identity changed")
        if self.runner is None or proc(self.runner.pid) != self.runner_identity:
            raise ValueError("Original runner identity changed")
        if identity(self.socket_path) != self.socket_identity:
            raise ValueError("Original live control socket changed")
        for name, original in self.files.items():
            path = Path(name)
            if identity(safe(path)) != original or sha(
                read(path, private=not bool(original["mode"] & 0o077))
            ) != self.file_digests.get(name):
                raise ValueError("Original installed private input changed")
        self.audit_route_trust()

    def report(self, action):
        alive = self.ready and self.runner is not None and not exited(self.handles[self.runner.pid][0])
        if alive:
            try:
                self.audit_live()
            except BaseException:
                alive = False
                self.ready = False
                self.failure = "original-live-identity-changed"
        unresolved = list(self.unresolved)
        if self.failure and not self.closed:
            unresolved.append(self.packet["operation_id"])
        return {
            "runner_vm": self.packet["runner_vm"],
            "resources": [
                {
                    "kind": "runner-service",
                    "state": "absent" if self.closed else ("owned-present" if alive else "residual"),
                    "runner_id": self.registration,
                    "disposition": "remove",
                },
                {
                    "kind": "runner-private-inputs",
                    "state": "absent" if self.closed else "owned-present",
                    "disposition": "remove",
                },
                {"kind": "route-owned-trust", "state": "preserved", "disposition": "preserve"},
            ],
            "unresolved_operations": sorted(set(unresolved)),
            "mutation_effect": "applied" if not unresolved else "unknown",
            "complete_cleanup": self.closed,
            "service_ready": alive and not unresolved,
            "runner_id": self.registration,
            "runner_manager_system_identity": self.manager,
            "process": self.processes(),
            "descendants_reaped": self.closed,
            "supervisor_terminated": False,
            "action": action,
        }

    def child_edges(self, parent, deadline):
        remaining(deadline)
        root = Path("/proc") / str(parent) / "task"
        threads = list(root.iterdir())
        if len(threads) > 256:
            raise ValueError("Original process thread bound exceeded")
        children = set()
        for thread in threads:
            remaining(deadline)
            raw = (thread / "children").read_bytes()
            if len(raw) > 8192:
                raise ValueError("Runner child observation exceeds bound")
            children.update(int(item) for item in raw.split())
        return children

    def freeze(self, pid, deadline):
        fd, observed = self.handles[pid]
        if exited(fd):
            return
        if proc(pid) != observed:
            raise ValueError("Original descendant process changed")
        remaining(deadline)
        send(fd, signal.SIGSTOP)
        # SIGSTOP is asynchronous. Wait until every original thread is stopped
        # before examining its child edges; a mere signal send is insufficient.
        while not exited(fd):
            remaining(deadline)
            states = []
            for task in (Path("/proc") / str(pid) / "task").iterdir():
                raw = (task / "stat").read_bytes()
                states.append(raw[raw.rfind(b")") + 2 :].split()[0])
            if states and all(value in (b"T", b"t", b"Z") for value in states):
                return
            time.sleep(min(0.005, remaining(deadline)))

    def enroll(self, pid, parent):
        if pid in self.handles:
            return
        fd = os.pidfd_open(pid, 0)
        try:
            raw = (Path("/proc") / str(pid) / "stat").read_bytes()
            fields = raw[raw.rfind(b")") + 2 :].split()
            # Reparenting into our original subreaper is admissible; unrelated
            # PID reuse is not. The pidfd pins this exact kernel process.
            if int(fields[1]) not in (parent, os.getpid()):
                raise ValueError("Observed child left the original ancestry")
            if fields[0] == b"Z" and int(fields[1]) == os.getpid():
                reaped, _ = os.waitpid(pid, os.WNOHANG)
                if reaped != pid:
                    raise ValueError("Original zombie child reaping unobserved")
                return
            observed = proc(pid)
            if observed.get("uid") != [UID] * 4 or observed.get("gid") != [UID] * 4:
                raise ValueError("Privileged or mapped-account descendant requires original VM disposal")
            if exited(fd):
                raise ValueError("Original child exited during enrollment")
            self.handles[pid] = (fd, observed)
            fd = None
        finally:
            if fd is not None:
                os.close(fd)

    def collect(self, deadline):
        # Only live kernel child edges of this original subreaper enroll a
        # process. Freeze each parent before discovery, including all threads.
        visited = set()
        for _ in range(257):
            todo = [(pid, os.getpid()) for pid in self.child_edges(os.getpid(), deadline)]
            discovered = False
            while todo:
                remaining(deadline)
                pid, parent = todo.pop()
                if pid in visited:
                    continue
                if len(visited) >= 256:
                    raise ValueError("Runner descendant bound exceeded")
                try:
                    self.enroll(pid, parent)
                except (ProcessLookupError, FileNotFoundError):
                    # No signal is issued from a vanished edge. A survivor will
                    # appear in a subsequent original subreaper child read.
                    continue
                visited.add(pid)
                if pid not in self.handles:
                    continue  # Affirmatively reaped zombie.
                self.freeze(pid, deadline)
                discovered = True
                if not exited(self.handles[pid][0]):
                    todo.extend((child, pid) for child in self.child_edges(pid, deadline))
            fresh = self.child_edges(os.getpid(), deadline)
            if fresh <= visited:
                return
            if not discovered and not fresh:
                return
        raise ValueError("Original runner descendant closure remains uncertain")

    def audit_empty_trust(self):
        row = self.empty_trust
        path = Path(row["path"])
        if directory(path) != {k: row[k] for k in ("device", "inode", "uid", "gid", "mode")} or any(
            path.iterdir()
        ):
            raise ValueError("Original empty private CA directory changed")

    def audit_route_trust(self):
        for row in self.trust_rows.values():
            path = Path(row["path"])
            raw = read(path)
            if (
                identity(safe(path)) != {k: row[k] for k in ("device", "inode", "uid", "gid", "mode")}
                or len(raw) != row["bytes"]
                or sha(raw) != row["sha256"]
            ):
                raise ValueError("Preserved original route trust changed")
        self.audit_empty_trust()

    def stop(self, deadline):
        remaining(deadline)
        if self.pending_paths:
            raise ValueError("Unacknowledged file effect requires original VM disposal")
        self.ready = False
        self.audit_route_trust()
        self.collect(deadline)
        for fd, observed in self.handles.values():
            remaining(deadline)
            if not exited(fd):
                if proc(observed["pid"]) != observed:
                    raise ValueError("Original descendant process changed before termination")
                remaining(deadline)
                send(fd, signal.SIGKILL)
        while self.handles:
            remaining(deadline)
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                if self.handles:
                    raise ValueError("Original descendant reaping remains unproven")
                break
            if pid:
                if pid not in self.handles:
                    raise ValueError("Unexpected child cannot grant cleanup")
                os.close(self.handles.pop(pid)[0])
            else:
                time.sleep(min(0.01, remaining(deadline)))
        # Affirmative ECHILD is required after every enrolled process was reaped.
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            pass
        else:
            raise ValueError("Unobserved runner descendant remains")
        for name, original in reversed(list(self.files.items())):
            remaining(deadline)
            path = Path(name)
            if identity(safe(path)) != original:
                raise ValueError("Original private runner file changed identity")
            path.unlink()
            self.removed.append((name, original))
            del self.files[name]
        for name, original in reversed(list(self.dirs.items())):
            remaining(deadline)
            path = Path(name)
            if directory(path) != original:
                raise ValueError("Original private runner directory changed identity")
            path.rmdir()
            self.removed.append((name, original))
            del self.dirs[name]
        self.audit_route_trust()
        remaining(deadline)
        self.failure = None
        self.unresolved.clear()
        self.closed = True

    def serve(self):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER; no privilege change.
            raise ValueError("Original child reaper unavailable")
        immutable(Path(sys.executable))
        self.process_identity = proc(os.getpid())
        if self.process_identity["uid"] != [UID] * 4 or self.process_identity["gid"] != [UID] * 4:
            raise ValueError("Nonroot supervisor required")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self.socket_identity = identity(self.socket_path)
        sock.listen(1)
        try:
            self.install()
        except BaseException:
            self.failure = "installation-incomplete"
        while not self.closed:
            conn, _ = sock.accept()
            with conn:
                accepted_at = time.monotonic()
                deadline = accepted_at + 5
                try:
                    peer = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if peer[1:] != (UID, UID):
                        raise ValueError("Original guest control user required")
                    wire = receive(conn, deadline)
                    shape(wire, "challenge request authentication")
                    if (
                        type(wire["challenge"]) is not str
                        or not re.fullmatch("[a-f0-9]{64}", wire["challenge"])
                        or type(wire["request"]) is not dict
                        or "service_nonce" in wire["request"]
                        or type(wire["authentication"]) is not str
                    ):
                        raise ValueError("Closed authenticated runner control frame required")
                    signed = bytes.fromhex(wire["challenge"]) + encoded(wire["request"])
                    if not hmac.compare_digest(
                        wire["authentication"],
                        hmac.new(self.nonce.encode(), signed, hashlib.sha256).hexdigest(),
                    ):
                        raise ValueError("Original resident control authentication differs")
                    request = validate({**wire["request"], "service_nonce": self.nonce})
                    if (
                        request["action"] == "install"
                        or request["runner_vm"] != self.packet["runner_vm"]
                        or request["run_id"] != self.packet["run_id"]
                        or not hmac.compare_digest(request["service_nonce"], self.nonce)
                        or request["expected_process"] is not None
                        and request["expected_process"] != self.processes()
                    ):
                        raise ValueError("Original live runner controller required")
                    deadline = accepted_at + request["timeout"]
                    guard(request, deadline)
                    if request["action"] == "reconcile" and request["operation_id"] not in (
                        self.packet["operation_id"],
                        self.down_operation,
                    ):
                        raise ValueError("Reconciliation must identify the original uncertain operation")
                    if request["action"] == "down":
                        if self.down_operation is not None:
                            raise ValueError("Original cleanup already attempted; no implicit retry")
                        self.down_operation = request["operation_id"]
                        try:
                            self.stop(deadline)
                        except BaseException:
                            self.unresolved.append(request["operation_id"])
                    response = self.report(request["action"])
                    conn.settimeout(remaining(deadline))
                    conn.sendall(
                        encoded(
                            {
                                "result": response,
                                "authentication": hmac.new(
                                    self.nonce.encode(),
                                    bytes.fromhex(wire["challenge"]) + encoded(response),
                                    hashlib.sha256,
                                ).hexdigest(),
                            }
                        )
                        + b"\n"
                    )
                except BaseException:
                    # No exception text, configuration, token, nonce or environment
                    # is copied into a public report or inherited SSH output.
                    try:
                        conn.sendall(b'{"error":"runner-control-rejected"}\n')
                    except OSError:
                        pass
        sock.close()
        if identity(self.socket_path) != self.socket_identity:
            raise ValueError("Original supervisor socket changed")
        self.socket_path.unlink()


def peer(sock, packet, expected=None):
    pid, uid, gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != UID or gid != UID:
        raise ValueError("Original nonroot supervisor peer required")
    fd, observed = open_process(pid)
    if (
        observed["boot_id"] != packet["runner_vm"]["guest_boot_id"]
        or observed["uid"] != [UID] * 4
        or observed["gid"] != [UID] * 4
        or expected is not None
        and observed != expected
    ):
        os.close(fd)
        raise ValueError("Original resident supervisor process changed")
    return fd, observed


def transact(packet, deadline, *, spawned=None, spawned_pid=None):
    path = Path(paths(packet["run_id"])["private"]) / "runner.sock"
    expected = packet["expected_process"]
    while True:
        remaining(deadline)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(min(1, remaining(deadline)))
            sock.connect(str(path))
            break
        except (FileNotFoundError, ConnectionRefusedError):
            sock.close()
            if spawned is None:
                raise ValueError("Original resident supervisor is unavailable; VM disposal proof required")
            if exited(spawned):
                raise ValueError("Original supervisor exited before observation")
            time.sleep(min(0.02, remaining(deadline)))
    with sock:
        original_socket = identity(path)
        if expected is not None and original_socket != expected["control_socket"]:
            raise ValueError("Original control socket inode changed")
        fd, observed = peer(sock, packet, None if expected is None else expected["supervisor"])
        try:
            if spawned_pid is not None and observed["pid"] != spawned_pid:
                raise ValueError("Initial control peer is not the original forked supervisor")
            request = {key: value for key, value in packet.items() if key != "service_nonce"}
            request["timeout"] = min(packet["timeout"], remaining(deadline))
            challenge = os.urandom(32)
            authentication = hmac.new(
                packet["service_nonce"].encode(), challenge + encoded(request), hashlib.sha256
            ).hexdigest()
            sock.sendall(
                encoded({"challenge": challenge.hex(), "request": request, "authentication": authentication})
                + b"\n"
            )
            reply = receive(sock, deadline)
            shape(reply, "result authentication")
            result = reply["result"]
            if type(reply["authentication"]) is not str or not hmac.compare_digest(
                reply["authentication"],
                hmac.new(
                    packet["service_nonce"].encode(), challenge + encoded(result), hashlib.sha256
                ).hexdigest(),
            ):
                raise ValueError("Original supervisor response authentication differs")
            if (
                result.get("runner_vm") != packet["runner_vm"]
                or result.get("process", {}).get("supervisor") != observed
                or result["process"].get("control_socket") != original_socket
                or result["process"].get("service_nonce_sha256") != sha(packet["service_nonce"].encode())
            ):
                raise ValueError("Original live runner response differs")
            if result.get("complete_cleanup") is True:
                poll = select.poll()
                poll.register(fd, select.POLLIN)
                if not poll.poll(max(1, int(remaining(deadline) * 1000))):
                    raise ValueError("Original supervisor termination remains unobserved")
                if path.exists():
                    raise ValueError("Original runner control socket cleanup incomplete")
                result["supervisor_terminated"] = True
            remaining(deadline)
            return result
        finally:
            os.close(fd)


def dispatch(packet):
    validate(packet)
    deadline = time.monotonic() + packet["timeout"]
    guard(packet, deadline)
    if packet["action"] != "install":
        return transact(packet, deadline)
    # Existing socket/installation is never adopted or implicitly retried.
    path = Path(paths(packet["run_id"])["private"]) / "runner.sock"
    if path.exists() or path.is_symlink():
        raise ValueError("Runner install was already attempted")
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
            os.umask(0o077)
            null = os.open("/dev/null", os.O_RDWR)
            for number in (0, 1, 2):
                os.dup2(null, number)
            for number in list(Path("/proc/self/fd").iterdir()):
                if int(number.name) > 2:
                    try:
                        os.close(int(number.name))
                    except OSError:
                        pass
            Supervisor(packet, deadline).serve()
            os._exit(0)
        except BaseException:
            os._exit(73)
    fd = os.pidfd_open(pid, 0)
    try:
        request = {key: value for key, value in packet.items() if key != "installation"}
        request.update(action="observe", expected_process=None)
        return transact(request, deadline, spawned=fd, spawned_pid=pid)
    finally:
        os.close(fd)


def main():
    try:
        raw = sys.stdin.buffer.read(MAX + 1)
        if len(raw) > MAX:
            raise ValueError("Runner packet exceeds bound")
        value = dispatch(json.loads(raw))
        sys.stdout.buffer.write(encoded(value) + b"\n")
        sys.stdout.buffer.flush()
    except BaseException:
        sys.stderr.write(
            "Fixed runner guest operation incomplete; original outcome requires reconciliation.\n"
        )
        raise SystemExit(73)


if __name__ == "__main__":
    main()
"""


def execute(packet, *, scope):
    """Called only by the fixed VM worker with its original current scope."""
    if CONTEXT_TYPE is None or type(scope) is not CONTEXT_TYPE:
        raise ValueError("Original RunnerVMRemoteContext required")
    if (
        type(packet) is not dict
        or packet.get("runner_vm") != scope.identity()
        or packet.get("run_id") != scope.packet["run_id"]
    ):
        raise ValueError("Runner service packet differs from its original VM")
    namespace = {"__name__": "runner_service_validation"}
    exec(compile(GUEST_PROGRAM, "<fixed-runner-service>", "exec"), namespace)
    namespace["validate"](packet)
    scope.observe_guest()
    seconds = packet["timeout"]
    result = scope.command(
        [*scope.ssh_argv(), shlex.join(["python3", "-I", "-B", "-c", GUEST_PROGRAM])],
        seconds=seconds,
        input_bytes=json.dumps(packet, allow_nan=False).encode(),
    )
    if len(result.stdout) > 2400000:
        raise ValueError("Runner service observation exceeds bound")
    value = json.loads(result.stdout)
    if type(value) is not dict or value.get("runner_vm") != packet["runner_vm"]:
        raise ValueError("Runner service observation lacks its original VM")
    return value
