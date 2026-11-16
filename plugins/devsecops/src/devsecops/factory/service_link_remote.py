"""Fixed peer-scoped relays on the original owned bridges; no network joins.

The installed copy also serves as the bounded relay daemon. Its config is created
only by execute after original fresh/container/source ownership is re-observed.
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
import signal
import socket
import stat
import subprocess
import sys
import threading
import time


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
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=False,
    )
    output = {process.stdout: bytearray(), process.stderr: bytearray()}
    deadline = time.monotonic() + 10
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


def resolve_connection(config, root, spec, peer):
    role = spec["service"]
    service = config["targets"][role]
    target = service["registry" if role == "registry" else "server"]
    target_addr, _ = address(inspect(target), inspect(service["network"]))
    if spec["peer"] == "desktop":
        if peer != "127.0.0.1":
            raise ValueError("Desktop relay rejects nonloopback peers")
    elif spec["peer"] == "guest":
        foundation = config["targets"]["foundation"]
        expected, _ = address(inspect(foundation["runtime"]), inspect(foundation["network"]))
        if peer != expected:
            raise ValueError("Guest relay peer is not the original runtime")
    else:
        # Created only after direct original runner observation; daemon
        # cannot infer a runner identity from its IP or a name.
        runner = json.loads(read(root / "runner-peer.json"))
        if runner["binding"] != config["binding"]:
            raise ValueError("Runner peer belongs to another link generation")
        expected, _ = address(inspect(runner["identity"]), inspect(config["targets"]["gitlab"]["network"]))
        if peer != expected:
            raise ValueError("Runner relay peer is not the original owned runner")
    return target_addr, service["port"]


def _daemon_origin_guard(foundation):
    root = Path(foundation["root"])
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("Original relay fresh generation path changed")
    info = root.stat()
    if (
        info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or not stat.S_ISDIR(info.st_mode)
        or {"device": info.st_dev, "inode": info.st_ino} != foundation["root_identity"]
    ):
        raise ValueError("Original relay fresh generation identity changed")
    owner = json.loads(read(root / "owner.json"))
    if (
        owner.get("binding") != foundation["binding"]
        or owner.get("run_id") != foundation["run_id"]
        or owner.get("up_operation_id") != foundation["up_operation_id"]
    ):
        raise ValueError("Original relay fresh generation owner changed")
    disk = (root / "data/probe.qcow2").lstat()
    if (
        not stat.S_ISREG(disk.st_mode)
        or disk.st_uid != os.getuid()
        or disk.st_nlink != 1
        or {"device": disk.st_dev, "inode": disk.st_ino} != foundation["root_disk_creation"]["disk_identity"]
    ):
        raise ValueError("Original relay guest disk changed")
    if sha(read(root / "data/service-link-forward.py")) != foundation["guest_forward_source_sha256"]:
        raise ValueError("Original relay guest helper source changed")


def _daemon(config_path):
    config_path = Path(config_path)
    root = config_path.parent
    config_raw = read(config_path)
    config = json.loads(config_raw)
    script = Path(__file__)
    script_raw = read(script)
    if sha(script_raw) != config["script_sha256"]:
        raise ValueError("Relay script differs from original selected source")
    info = root.stat()
    original = (info.st_dev, info.st_ino)
    expiry = datetime.fromisoformat(config["expires_at"].replace("Z", "+00:00"))
    listeners, sockets = {}, []
    semaphore = threading.BoundedSemaphore(32)
    _daemon_origin_guard(config["targets"]["foundation"])
    source_pins = _source_snapshot(config["targets"]["foundation"], time.monotonic() + 300)

    def guard():
        _daemon_origin_guard(config["targets"]["foundation"])
        for path, original_source in source_pins.items():
            source_info = path.stat()
            if (
                source_info.st_dev,
                source_info.st_ino,
                source_info.st_size,
                source_info.st_mtime_ns,
                source_info.st_ctime_ns,
            ) != original_source:
                raise ValueError("Original relay source assets changed")
        st = root.stat()
        if (
            (st.st_dev, st.st_ino) != original
            or read(config_path) != config_raw
            or read(script) != script_raw
        ):
            raise ValueError("Relay original generation or source changed")
        if datetime.now(timezone.utc) >= expiry:
            raise ValueError("Relay authorization window expired")

    def serve(client, peer, spec):
        try:
            guard()
            target_addr, target_port = resolve_connection(config, root, spec, peer)
            upstream = socket.create_connection((target_addr, target_port), timeout=10)
            with upstream:
                client.setblocking(False)
                upstream.setblocking(False)
                ends = [client, upstream]
                deadline = time.monotonic() + 300
                while ends and time.monotonic() < deadline:
                    guard()
                    ready, _, _ = select.select(ends, [], [], min(5, deadline - time.monotonic()))
                    for source in ready:
                        data = source.recv(65536)
                        destination = upstream if source is client else client
                        if not data:
                            ends.remove(source)
                            destination.shutdown(socket.SHUT_WR)
                            continue
                        pending = memoryview(data)
                        while pending:
                            guard()
                            _, writable, _ = select.select([], [destination], [], 5)
                            if not writable or time.monotonic() >= deadline:
                                raise ValueError("Owned relay connection exceeded its bound")
                            pending = pending[destination.send(pending) :]
        except Exception:
            pass  # No request bytes, credentials, or target material enters logs.
        finally:
            client.close()
            semaphore.release()

    try:
        for name, spec in config["listeners"].items():
            guard()
            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            listener.bind((spec["host"], spec["port"]))
            listener.listen(32)
            sockets.append(listener)
            listeners[name] = {**spec, "port": listener.getsockname()[1]}
        receipt = {
            "binding": config["binding"],
            "process": process_identity(os.getpid()),
            "listeners": listeners,
            "config_sha256": sha(config_raw),
            "script_sha256": sha(script_raw),
        }
        write(root / "daemon.json", canonical(receipt))
        while True:
            guard()
            ready, _, _ = select.select(sockets, [], [], 1)
            for listener in ready:
                client, peer = listener.accept()
                if not semaphore.acquire(blocking=False):
                    client.close()
                    continue
                name = next(
                    n for n, v in listeners.items() if (v["host"], v["port"]) == listener.getsockname()
                )
                threading.Thread(target=serve, args=(client, peer[0], listeners[name]), daemon=True).start()
    finally:
        for listener in sockets:
            listener.close()


RUNNER_HELPER = r"""import base64,hashlib,json,os,socket,ssl,stat,sys
p=json.loads(sys.stdin.buffer.read(64001));root='/var/lib/devsecops/service-links-'+p['run_id'];h='/etc/hosts'
def read(path):
 f=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);s=os.fstat(f)
 if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>64000:raise ValueError('runner file identity')
 b=os.read(f,64001);os.close(f);return b
sha=lambda b:hashlib.sha256(b).hexdigest()
for role in ('gitlab','registry'):
 if sha(read('/opt/devsecops/runner-inputs/service-trust/'+role+'-ca.pem'))!=p['ca_sha256'][role]:raise ValueError('runner staged CA differs')
if p['action']=='up':
 os.mkdir(root,0o700);raw=read(h)
 if any(p['hostname'].encode() in l.split()[1:] for l in raw.splitlines() if l.strip() and not l.lstrip().startswith(b'#')):raise ValueError('runner hostname preexists')
 desired=raw+b'\n'+p['gateway'].encode()+b' '+p['hostname'].encode()+b' # devsecops '+p['binding'].encode()+b'\n'
 state={'binding':p['binding'],'root_device':os.stat(root).st_dev,'root_inode':os.stat(root).st_ino,'before':base64.b64encode(raw).decode(),'after_sha256':sha(desired),'hosts_inode':os.stat(h).st_ino,'hosts_device':os.stat(h).st_dev}
 f=os.open(root+'/intent.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.write(f,json.dumps(state).encode());os.fsync(f);os.close(f)
else:state=json.loads(read(root+'/intent.json'))
if state['binding']!=p['binding'] or (os.stat(root).st_ino,os.stat(root).st_dev)!=(state['root_inode'],state['root_device']) or (os.stat(h).st_ino,os.stat(h).st_dev)!=(state['hosts_inode'],state['hosts_device']):raise ValueError('runner ownership changed')
raw=read(h);before=base64.b64decode(state['before'],validate=True)
if p['action']=='down':
 if sha(raw)!=state['after_sha256']:raise ValueError('runner mapping drift')
 desired=before
if p['action'] in ('up','down'):
 f=os.open(h,os.O_WRONLY|os.O_NOFOLLOW)
 if read(h)!=raw:raise ValueError('runner mapping CAS')
 os.ftruncate(f,0);os.write(f,desired);os.fsync(f);os.close(f)
raw=read(h);absent=raw==before;present=sha(raw)==state['after_sha256']
if p['action']=='up':
 ctx=ssl.create_default_context(cafile='/opt/devsecops/runner-inputs/service-trust/registry-ca.pem')
 with socket.create_connection((p['hostname'],8443),timeout=15) as connection:
  with ctx.wrap_socket(connection,server_hostname=p['hostname']) as tls:
   tls.settimeout(15);tls.sendall(('GET /v2/ HTTP/1.1\r\nHost: '+p['hostname']+':8443\r\nConnection: close\r\n\r\n').encode());response=tls.recv(4096)
   if response.split(b'\r\n',1)[0].split()[1:2]!=[b'401']:raise ValueError('runner registry TLS/auth challenge differs')
print(json.dumps({'binding':p['binding'],'complete_cleanup':absent,'mutation_effect':'applied' if absent or present else 'unknown','unresolved_operations':[] if absent or present else ['runner-hosts-write'],'resources':[{'kind':'runner-hosts-mapping','disposition':'remove','state':'absent' if absent else 'present' if present else 'unresolved'},{'kind':'runner-original-hosts-and-staged-trust','disposition':'preserve','state':'preserved' if absent or present else 'changed'}]}))
"""


def _validate_targets(packet):
    targets = packet["targets"]
    if type(targets) is not dict or set(targets) != {"foundation", "gitlab", "registry"}:
        raise ValueError("Unsupported fixed service-link target graph")
    static = json.loads(json.dumps(targets))
    static["gitlab"]["runner"] = None
    if sha(json.dumps(static, sort_keys=True, separators=(",", ":")).encode()) != packet["binding"]:
        raise ValueError("Service-link exact original target binding differs")
    for target in targets.values():
        if target["run_id"] != packet["run_id"] or target["identities"] != packet["identities"]:
            raise ValueError("Service-link targets belong to another original run")
    from urllib.parse import urlsplit

    names = []
    for role, port in (("gitlab", 8444), ("registry", 8443)):
        target = targets[role]
        url = urlsplit(target["origin"])
        if (
            url.scheme != "https"
            or url.port != port
            or target["port"] != port
            or url.path
            or url.query
            or url.fragment
            or url.username
            or url.password
            or not re.fullmatch(role + r"\.[a-z][a-z0-9-]{0,62}\.test", url.hostname or "")
        ):
            raise ValueError("Service-link fixed TLS origin differs")
        names.append(url.hostname.split(".", 1)[1])
    if names[0] != names[1]:
        raise ValueError("Service-link endpoints belong to different environments")
    foundation = targets["foundation"]
    if foundation["generation"] != packet["run_id"] or foundation["root"] != str(
        Path(foundation["recipe"]["remote_parent"]) / packet["run_id"]
    ):
        raise ValueError("Service-link generation path differs")


def _source_snapshot(foundation, deadline):
    snapshots = {}
    for row in foundation["source_assets"].values():
        path = Path(row["path"])
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("Original source path contains a link")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or before.st_mode & 0o022
                or (before.st_dev, before.st_ino, before.st_size)
                != (row["device"], row["inode"], row["bytes"])
            ):
                raise ValueError("Original supplied source identity changed")
            digest = hashlib.sha256()
            count = 0
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError("Original source snapshot exceeded action deadline")
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > row["bytes"]:
                    raise ValueError("Original source grew")
                digest.update(block)
            after = os.fstat(fd)

            def key(st):
                return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

            if key(before) != key(after) or count != row["bytes"] or digest.hexdigest() != row["sha256"]:
                raise ValueError("Original supplied source bytes changed")
            snapshots[path] = key(after)
        finally:
            os.close(fd)
    return snapshots


def _foundation_guard(foundation, support):
    root = Path(foundation["root"])
    info = support._private(root).stat()
    owner = json.loads(read(root / "owner.json"))
    if (
        owner.get("binding") != foundation["binding"]
        or owner.get("run_id") != foundation["run_id"]
        or owner.get("up_operation_id") != foundation["up_operation_id"]
        or owner.get("root_identity") != foundation["root_identity"]
        or foundation["root_identity"] != {"device": info.st_dev, "inode": info.st_ino}
    ):
        raise ValueError("Original fresh generation ownership changed")
    if sha(read(root / "data/service-link-forward.py")) != foundation["guest_forward_source_sha256"]:
        raise ValueError("Original guest forwarding source changed")
    disk = (root / "data/probe.qcow2").lstat()
    if (
        not stat.S_ISREG(disk.st_mode)
        or disk.st_uid != os.getuid()
        or disk.st_nlink != 1
        or {"device": disk.st_dev, "inode": disk.st_ino} != foundation["root_disk_creation"]["disk_identity"]
    ):
        raise ValueError("Original fresh disposable disk was replaced")
    for source in foundation["source_assets"].values():
        path = Path(source["path"])
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("Original supplied source path changed")
        item = path.stat()
        if (
            not stat.S_ISREG(item.st_mode)
            or item.st_uid != os.getuid()
            or item.st_nlink != 1
            or (item.st_dev, item.st_ino, item.st_size)
            != (source["device"], source["inode"], source["bytes"])
        ):
            raise ValueError("Original supplied source identity changed")
    return owner


def relay_configuration(normalized, binding, expires_at, source_sha256, fresh_gateway, service_gateway):
    return {
        "binding": binding,
        "targets": normalized,
        "expires_at": expires_at,
        "script_sha256": source_sha256,
        "listeners": {
            "guest-gitlab": {"host": fresh_gateway, "port": 0, "service": "gitlab", "peer": "guest"},
            "guest-registry": {
                "host": fresh_gateway,
                "port": 0,
                "service": "registry",
                "peer": "guest",
            },
            "desktop-gitlab": {
                "host": "127.0.0.1",
                "port": 0,
                "service": "gitlab",
                "peer": "desktop",
            },
            "desktop-registry": {
                "host": "127.0.0.1",
                "port": 0,
                "service": "registry",
                "peer": "desktop",
            },
            "runner-registry": {
                "host": service_gateway,
                "port": 8443,
                "service": "registry",
                "peer": "runner",
            },
        },
    }


def execute(packet, support, guest):
    if packet["schema_version"] != 1 or packet["action"] not in ("up", "runner", "down", "reconcile"):
        raise ValueError("Unsupported service-link operation")
    action = packet["action"]
    operation = packet["operation_id"]
    binding = packet["binding"]
    if not re.fullmatch("[a-f0-9-]{36}", operation) or not re.fullmatch("[a-f0-9]{64}", binding):
        raise ValueError("Service-link operation identity is invalid")
    _validate_targets(packet)
    targets = packet["targets"]
    foundation = targets["foundation"]
    root = Path(foundation["root"])
    original = foundation["root_identity"]
    timeout = packet["timeout"]
    if type(timeout) not in (int, float) or not 0 < timeout <= 600:
        raise ValueError("Service-link timeout differs")
    deadline = time.monotonic() + timeout
    expiry = datetime.fromisoformat(packet["expires_at"].replace("Z", "+00:00"))
    if expiry.tzinfo is None or action in ("up", "runner") and datetime.now(timezone.utc) >= expiry:
        raise ValueError("Service-link provisioning grant expired before any effect")
    root_info = support._private(root).stat()
    owner = _foundation_guard(foundation, support)
    source_snapshot = _source_snapshot(foundation, deadline)
    if (
        owner["binding"] != foundation["binding"]
        or owner["run_id"] != packet["run_id"]
        or owner["root_identity"] != original
        or original != {"device": root_info.st_dev, "inode": root_info.st_ino}
    ):
        raise ValueError("Service-link original fresh generation changed")
    normalized = json.loads(json.dumps(targets))
    normalized["foundation"]["runtime"] = {
        "kind": "container",
        "id": foundation["runtime_id"],
        "labels": {"io.devsecops.fresh.binding": foundation["binding"], "io.devsecops.fresh.role": "runtime"},
    }
    normalized["foundation"]["network"] = {
        "kind": "network",
        "id": foundation["network_id"],
        "labels": {"io.devsecops.fresh.binding": foundation["binding"], "io.devsecops.fresh.role": "network"},
    }
    fresh_container = inspect(normalized["foundation"]["runtime"])
    fresh_network = inspect(normalized["foundation"]["network"])
    if fresh_container.get("Config", {}).get("Cmd") != foundation["qemu_argv"]:
        raise ValueError("Original fixed guest forwarding command changed")
    if fresh_network.get("Internal") is not True:
        raise ValueError("Fresh network isolation changed")
    fresh_address, fresh_gateway = address(fresh_container, fresh_network)
    if fresh_address != foundation["address"]:
        raise ValueError("Fresh guest address differs from its original observation")
    for role in ("gitlab", "registry"):
        target = targets[role]
        address(inspect(target["server" if role == "gitlab" else "registry"]), inspect(target["network"]))
        if sha(base64.b64decode(packet["materials"][role], validate=True)) != target["ca_sha256"]:
            raise ValueError("Service public CA pin differs")
    _, service_gateway = address(inspect(targets["gitlab"]["server"]), inspect(targets["gitlab"]["network"]))
    source = getattr(sys.modules[__name__], "SOURCE_BYTES", None) or read(Path(__file__))
    daemon_targets = json.loads(json.dumps(normalized))
    daemon_targets["gitlab"]["runner"] = None
    expected_config = relay_configuration(
        daemon_targets, binding, packet["expires_at"], sha(source), fresh_gateway, service_gateway
    )
    links = root / "service-links"
    if action == "up":
        # Both intent and exclusive generation precede every relay/file effect.
        write(root / "service-links-intent.json", canonical({"binding": binding, "operation_id": operation}))
        links.mkdir(mode=0o700)
        write(links / "owner.json", canonical({"binding": binding, "operation_id": operation}))
        write(links / "lock", b"")
    info = support._private(links).stat()
    root_identity = {"device": info.st_dev, "inode": info.st_ino}
    if packet["expected_root_identity"] is not None and packet["expected_root_identity"] != root_identity:
        raise ValueError("Service-link generation was replaced")
    if json.loads(read(links / "owner.json"))["binding"] != binding:
        raise ValueError("Service-link generation belongs to another owner")
    if action != "up":
        if read(links / "relay.py") != source or read(links / "relay-config.json") != canonical(
            expected_config
        ):
            raise ValueError("Original relay source or fixed listener configuration changed")
    prior = packet.get("expected_observation")
    if prior is not None:
        if prior.get("root_identity") != root_identity:
            raise ValueError("Original remote link observation differs")
        for name, digest in prior.get("file_pins", {}).items():
            if (
                name not in ("relay.py", "relay-config.json", "daemon.json", "owner.json", "runner-peer.json")
                or sha(read(links / name)) != digest
            ):
                raise ValueError("Original link source/config/identity audit changed")
    fd = support._lock_descriptor(links / "lock")
    fcntl.flock(fd, (fcntl.LOCK_SH if action == "reconcile" else fcntl.LOCK_EX) | fcntl.LOCK_NB)

    def check():
        if (
            time.monotonic() >= deadline
            or action in ("up", "runner")
            and datetime.now(timezone.utc) >= expiry
        ):
            raise ValueError("Service-link action window expired")
        current = support._private(links).stat()
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino) or json.loads(
            read(links / "owner.json")
        )["binding"] != binding:
            raise ValueError("Service-link ownership changed")
        for path, original_source in source_snapshot.items():
            source_info = path.stat()
            if (
                source_info.st_dev,
                source_info.st_ino,
                source_info.st_size,
                source_info.st_mtime_ns,
                source_info.st_ctime_ns,
            ) != original_source:
                raise ValueError("Original supplied source changed during service-link action")
        if _foundation_guard(foundation, support) != owner:
            raise ValueError("Original fresh owner changed")

    def command(argv, *, data=None, seconds=30):
        check()
        result = support._run(argv, min(deadline, time.monotonic() + seconds), data=data)
        check()
        return result

    def guest_call(which):
        pin = foundation["recipe"]["rke2_assets"]["rke2-images.linux-amd64.txt"]
        images = read(pin["path"])
        if sha(images) != pin["sha256"] or len(images) != pin["bytes"]:
            raise ValueError("Original RKE2 image-list input changed")
        services = {
            role: {
                "origin": targets[role]["origin"],
                "address": "10.0.2.100" if role == "gitlab" else "10.0.2.101",
                "port": targets[role]["port"],
                "ca_b64": packet["materials"][role],
                "ca_sha256": targets[role]["ca_sha256"],
            }
            for role in ("gitlab", "registry")
        }
        selected = {
            "schema_version": 1,
            "action": which,
            "run_id": packet["run_id"],
            "operation_id": operation,
            "binding": binding,
            "cluster_uid": foundation["cluster_uid"],
            "foundation_binding": foundation["binding"],
            "expires_at": packet["expires_at"],
            "timeout": min(300, max(0.001, deadline - time.monotonic() - 5)),
            "services": services,
            "coredns_image_pins": [
                line.strip() for line in images.decode().splitlines() if "coredns" in line
            ],
        }
        source = guest.SOURCE_BYTES
        launcher = (
            'import base64,json,sys;ns={"__name__":"owned_guest_links"};exec(compile(base64.b64decode('
            + repr(base64.b64encode(source).decode())
            + '),"owned_guest_links","exec"),ns);print(json.dumps(ns["execute"](json.loads(sys.stdin.buffer.read(2000001)))))'
        )
        argv = support._guest_ssh(root / "data", fresh_address, root, foundation["runtime_id"])
        import shlex

        result = command(
            [*argv, shlex.join(["sudo", "python3", "-I", "-B", "-c", launcher])],
            data=canonical(selected),
            seconds=300,
        )
        value = json.loads(result.stdout)
        if value.get("binding") != binding:
            raise ValueError("Guest service-link result binding differs")
        return value

    def runner_call(which):
        target = targets["gitlab"]["runner"]
        if target is None:
            raise ValueError("Runner operation lacks direct original identity")
        current = inspect(target)
        _, gateway = address(current, inspect(targets["gitlab"]["network"]))
        if gateway != service_gateway:
            raise ValueError("Runner original gateway differs")
        selected = {
            "run_id": packet["run_id"],
            "binding": binding,
            "action": which,
            "gateway": gateway,
            "hostname": __import__("urllib.parse", fromlist=["urlsplit"])
            .urlsplit(targets["registry"]["origin"])
            .hostname,
            "ca_sha256": {r: targets[r]["ca_sha256"] for r in ("gitlab", "registry")},
        }
        result = command(
            ["docker", "exec", "-i", "--user", "0", target["id"], "python3", "-I", "-c", RUNNER_HELPER],
            data=canonical(selected),
        )
        value = json.loads(result.stdout)
        if value.get("binding") != binding:
            raise ValueError("Runner mapping result belongs to another operation")
        return value

    try:
        check()
        state_path = links / "state.json"
        if action == "up":
            state = {
                "binding": binding,
                "up_operation_id": operation,
                "stage": "intent",
                "guest": None,
                "runner": None,
                "pending": "relay-start",
            }
            write(state_path, canonical(state))
        else:
            state = json.loads(read(state_path))
        if state["binding"] != binding:
            raise ValueError("Service-link retained intent changed")

        def save():
            check()
            support._write(state_path, canonical(state), replace=True)

        if action == "up":
            write(links / "relay.py", source)
            config = expected_config
            write(links / "relay-config.json", canonical(config))
            log = os.open(links / "relay.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                process = subprocess.Popen(
                    ["python3", "-I", "-B", str(links / "relay.py"), str(links / "relay-config.json")],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                state["process"] = process_identity(process.pid)
                save()
            finally:
                os.close(log)
            while not (links / "daemon.json").exists():
                check()
                if process.poll() is not None:
                    raise ValueError("Owned relay failed before listener establishment")
                time.sleep(0.05)
            daemon = json.loads(read(links / "daemon.json"))
            if daemon["process"] != state["process"] or daemon["binding"] != binding:
                raise ValueError("Owned relay daemon identity differs")
            config = {
                "schema_version": 1,
                "binding": binding,
                "gateway": fresh_gateway,
                **{r: daemon["listeners"]["guest-" + r]["port"] for r in ("gitlab", "registry")},
            }
            state.update(
                stage="relay-ready", pending="guest-mapping", guest_forward_sha256=sha(canonical(config))
            )
            save()
            write(root / "data/service-links.json", canonical(config))
            state["guest"] = guest_call("up")
            save()
            if state["guest"].get("ready") is not True or state["guest"].get("unresolved_operations"):
                raise ValueError("Guest service links remain unready or unresolved")
            state["pending"] = None
            state["stage"] = "ready"
            save()
        elif action == "runner":
            if state["pending"] is not None or state["stage"] != "ready" or state["runner"] is not None:
                raise ValueError("Runner route cannot repeat or follow unknown effects")
            state["pending"] = "runner-mapping"
            save()
            write(
                links / "runner-peer.json",
                canonical({"binding": binding, "identity": targets["gitlab"]["runner"]}),
            )
            state["runner"] = runner_call("up")
            save()
            if (
                state["runner"].get("unresolved_operations")
                or state["runner"].get("mutation_effect") != "applied"
            ):
                raise ValueError("Runner service mapping remains unresolved")
            state["pending"] = None
            save()
        elif action == "down":
            if state["stage"] == "absent" or state.get("cleanup_operation_id") == operation:
                raise ValueError("Service-link cleanup was already attempted; reconcile without repeating")
            if state["pending"] is not None:
                proof = (prior or {}).get("reconciliation")
                if (
                    not isinstance(proof, dict)
                    or proof.get("state_sha256") != sha(read(state_path))
                    or proof.get("terminal_observed") is not True
                ):
                    raise ValueError(
                        "Unresolved links require the original terminal reconciliation before cleanup"
                    )
                # Proof was retained directly by the same coordinator. Re-read
                # original exact resources again before continuing a new down.
                daemon = json.loads(read(links / "daemon.json")) if (links / "daemon.json").exists() else None
                original_process = daemon["process"] if daemon else state.get("process")
                if original_process is None or process_identity(original_process["pid"]) not in (
                    None,
                    original_process,
                ):
                    raise ValueError("Reconciled original relay is changed or unobserved")
                if state["pending"] in ("guest-mapping", "cleanup"):
                    state["guest"] = guest_call("reconcile")
                    if state["guest"].get("unresolved_operations"):
                        raise ValueError("Guest effects changed after reconciliation")
                if state["pending"] == "runner-mapping" or state["runner"] is not None:
                    state["runner"] = runner_call("reconcile")
                    if state["runner"].get("unresolved_operations"):
                        raise ValueError("Runner effects changed after reconciliation")
                state["pending"] = None
            state["pending"] = "cleanup"
            state["cleanup_operation_id"] = operation
            save()
            if state["runner"] is not None and not state["runner"].get("complete_cleanup"):
                state["runner"] = runner_call("down")
                save()
                if state["runner"].get("complete_cleanup") is not True or state["runner"].get(
                    "unresolved_operations"
                ):
                    raise ValueError("Runner mapping cleanup is unresolved")
            if state["guest"] is not None and not state["guest"].get("complete_cleanup"):
                state["guest"] = guest_call("down")
                save()
                if state["guest"].get("complete_cleanup") is not True or state["guest"].get(
                    "unresolved_operations"
                ):
                    raise ValueError("Guest link cleanup is unresolved; preserve relay")
            daemon = json.loads(read(links / "daemon.json")) if (links / "daemon.json").exists() else None
            original_process = daemon["process"] if daemon else state.get("process")
            if original_process is None:
                raise ValueError("Relay launch has no original process observation")
            current = process_identity(original_process["pid"])
            if current is not None:
                if current != original_process:
                    raise ValueError("Refusing to stop a replaced relay process")
                if state.get("relay_stop_attempted"):
                    raise ValueError("Original relay stop remains unresolved; do not repeat signal")
                state["relay_stop_attempted"] = True
                save()
                if current.get("process_group") != current["pid"] or current.get("session") != current["pid"]:
                    raise ValueError("Original relay process session changed")
                os.killpg(current["process_group"], signal.SIGTERM)
                while process_identity(current["pid"]) is not None or not process_group_absent(current):
                    check()
                    time.sleep(0.05)
            forward = root / "data/service-links.json"
            if state.get("guest_forward_sha256") is not None:
                if forward.exists():
                    if sha(read(forward)) != state["guest_forward_sha256"]:
                        raise ValueError("Guest service forwarding config changed before removal")
                    forward.unlink()
                elif (prior or {}).get("reconciliation", {}).get("terminal_observed") is not True:
                    raise ValueError("Guest forwarding removal is unobserved")
            state["pending"] = None
            state["stage"] = "absent"
            save()
        daemon = json.loads(read(links / "daemon.json")) if (links / "daemon.json").exists() else None
        original_process = daemon["process"] if daemon else state.get("process")
        current = process_identity(original_process["pid"]) if original_process else None
        if current is not None and current != original_process:
            raise ValueError("Original relay process identity changed")
        group_absent = (
            original_process is not None and current is None and process_group_absent(original_process)
        )
        reconciliation = None
        if action == "reconcile":
            # No mutation/restart retry. A completed original effect is accepted
            # only from its independent same-identity readback; missing files
            # cannot establish that an action was never dispatched.
            if state["guest"] is not None or state["pending"] in ("guest-mapping", "cleanup"):
                state = {**state, "guest": guest_call("reconcile")}
            if state["runner"] is not None or state["pending"] == "runner-mapping":
                state = {**state, "runner": runner_call("reconcile")}
            component_unknown = any(
                state[key] is not None and state[key].get("unresolved_operations")
                for key in ("guest", "runner")
            )
            terminal = (daemon is not None or group_absent) and not component_unknown
            if state["pending"] == "guest-mapping":
                terminal = (
                    terminal
                    and state["guest"] is not None
                    and (
                        state["guest"].get("ready") is True
                        or state["guest"].get("complete_cleanup") is True
                        or (
                            state["guest"].get("cleanup_eligible") is True
                            and state["guest"].get("observed_partial") is True
                        )
                    )
                )
            if state["pending"] == "runner-mapping":
                terminal = (
                    terminal
                    and state["runner"] is not None
                    and state["runner"].get("mutation_effect") == "applied"
                )
            if state["pending"] == "cleanup":
                terminal = terminal and all(
                    state[key] is None or state[key].get("complete_cleanup") is True
                    for key in ("guest", "runner")
                )
            if state["pending"] == "cleanup" and state.get("relay_stop_attempted") and not group_absent:
                terminal = False
            reconciliation = {
                "state_sha256": sha(read(state_path)),
                "terminal_observed": terminal,
                "original_up_operation_id": state["up_operation_id"],
            }
            if terminal:
                state = {**state, "pending": None}
                if (
                    group_absent
                    and not (root / "data/service-links.json").exists()
                    and all(
                        state[key] is None or state[key].get("complete_cleanup") is True
                        for key in ("guest", "runner")
                    )
                ):
                    state["stage"] = "absent"
                elif (
                    state["guest"] is not None and state["guest"].get("ready") is True and current is not None
                ):
                    state["stage"] = "ready"
                else:
                    state["stage"] = "partial-observed"
        pending = [state["pending"]] if state["pending"] is not None else []
        if original_process is not None and current is None and not group_absent:
            pending.append("original-relay-process-group-not-absent")
        for key in ("guest", "runner"):
            if state[key] is not None:
                pending.extend(state[key].get("unresolved_operations", []))
        resources = [
            {
                "kind": "owned-host-service-relay",
                "identity": daemon["process"] if daemon else state.get("process"),
                "disposition": "remove",
                "state": "present" if current else "absent" if group_absent else "unresolved",
            },
            {
                "kind": "guest-service-forward-config",
                "identity": state.get("guest_forward_sha256"),
                "disposition": "remove",
                "state": "present"
                if (root / "data/service-links.json").exists()
                else "absent"
                if state["stage"] == "absent"
                or state.get("guest_forward_sha256") is None
                and original_process is not None
                else "unresolved",
            },
            {
                "kind": "run-created-link-reconciliation-audit",
                "creation_basis": "run-created",
                "retention": "preserve-audit",
                "identity": root_identity,
                "disposition": "preserve",
                "state": "preserved",
            },
        ]
        for key in ("guest", "runner"):
            if state[key] is not None:
                resources.extend(state[key].get("resources", []))
        complete = (
            state["stage"] == "absent"
            and not pending
            and all(
                r["state"] == ("absent" if r["disposition"] == "remove" else "preserved") for r in resources
            )
        )
        check()
        pins = {
            name: sha(read(links / name))
            for name in ("relay.py", "relay-config.json", "daemon.json", "owner.json", "runner-peer.json")
            if (links / name).exists()
        }
        return {
            "binding": binding,
            "operation_id": operation,
            "root_identity": root_identity,
            "file_pins": pins,
            "reconciliation": reconciliation,
            "complete_cleanup": complete,
            "ready": state["stage"] == "ready" and not pending and current is not None,
            "listeners": daemon["listeners"] if daemon else {},
            "resources": resources,
            "runner_identity": targets["gitlab"]["runner"],
            "unresolved_operations": pending,
            "mutation_effect": "applied" if not pending else "unknown",
        }
    finally:
        os.close(fd)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    _daemon(sys.argv[1])
