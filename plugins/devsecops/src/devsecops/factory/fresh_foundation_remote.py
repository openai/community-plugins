"""Fixed remote creation/observation/removal of one fresh offline RKE2 guest.

All source assets must already exist locally. No image pull, download, host sudo,
shared disk adoption, command selection or implicit retry is implemented here.
"""

from datetime import datetime, timezone
import base64
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import time

LABEL = "io.devsecops.fresh.binding"
ROLE = "io.devsecops.fresh.role"
OPERATION = "io.devsecops.fresh.operation"
PAYLOAD_FILES = ("ubuntu.img", "probe.qcow2", "seed.iso", "user-data", "meta-data")


def _asset(pin, *, deadline, destination=None, secret=False):
    source = Path(pin["path"])
    if any(p.is_symlink() for p in (source, *source.parents)):
        raise ValueError("Fresh source asset path contains a link")
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    out = None
    try:
        original = os.fstat(fd)
        if (
            not stat.S_ISREG(original.st_mode)
            or original.st_size != pin["bytes"]
            or original.st_uid != os.getuid()
            or original.st_nlink != 1
            or original.st_mode & (0o077 if secret else 0o022)
        ):
            raise ValueError("Fresh source asset identity or mode is unsupported")
        if destination is not None:
            out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        digest, count = hashlib.sha256(), 0
        while True:
            if time.monotonic() >= deadline:
                raise ValueError("Fresh source snapshot exceeded its deadline")
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            count += len(block)
            if count > pin["bytes"]:
                raise ValueError("Fresh source grew during snapshot")
            digest.update(block)
            if out is not None:
                view = memoryview(block)
                while view:
                    written = os.write(out, view)
                    if written <= 0:
                        raise OSError("Snapshot write did not advance")
                    view = view[written:]
        after = os.fstat(fd)

        def fields(item):
            return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)

        if fields(original) != fields(after) or count != pin["bytes"] or digest.hexdigest() != pin["sha256"]:
            raise ValueError("Fresh supplied asset differs from its immutable pin")
        if out is not None:
            os.fsync(out)
        return {
            "path": str(source),
            "device": original.st_dev,
            "inode": original.st_ino,
            "bytes": count,
            "sha256": digest.hexdigest(),
        }
    finally:
        os.close(fd)
        if out is not None:
            os.close(out)


def _cloud_seed(operator_public, host_private, host_public, run_id):
    if (
        not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]*)?\n?", operator_public)
        or not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]*)?\n?", host_public)
        or not host_private.startswith("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    ):
        raise ValueError("Fresh guest requires pinned Ed25519 SSH material")
    return (
        "#cloud-config\n"
        + json.dumps(
            {
                "users": [
                    {
                        "name": "demo",
                        "groups": ["sudo"],
                        "shell": "/bin/bash",
                        "lock_passwd": True,
                        "sudo": ["ALL=(ALL) NOPASSWD:ALL"],
                        "ssh_authorized_keys": [operator_public.strip()],
                    }
                ],
                "ssh_pwauth": False,
                "disable_root": True,
                "package_update": False,
                "package_upgrade": False,
                "ssh_deletekeys": True,
                "ssh_genkeytypes": [],
                "ssh_keys": {"ed25519_private": host_private, "ed25519_public": host_public.strip()},
                "runcmd": [["touch", "/run/devsecops-fresh-" + run_id]],
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()


GUEST_SERVICE_FORWARD = """import ipaddress,json,os,select,socket,stat,sys
p='/lab/service-links.json'
f=os.open(p,os.O_RDONLY|os.O_NOFOLLOW);i=os.fstat(f)
if not stat.S_ISREG(i.st_mode) or i.st_uid!=os.getuid() or i.st_nlink!=1 or i.st_mode&0o077:raise SystemExit(2)
r=os.read(f,16385);os.close(f)
if len(r)>16384:raise SystemExit(2)
v=json.loads(r);role=sys.argv[1]
if set(v)!={'schema_version','binding','gateway','gitlab','registry'} or v['schema_version']!=1 or role not in ('gitlab','registry'):raise SystemExit(2)
a=ipaddress.IPv4Address(v['gateway']);port=v[role]
if not a.is_private or a.is_loopback or type(port)is not int or not 1024<port<65536:raise SystemExit(2)
s=socket.create_connection((str(a),port),timeout=15);s.settimeout(60)
try:
 while True:
  ready,_,_=select.select([0,s],[],[],60)
  if not ready:break
  if 0 in ready:
   b=os.read(0,65536)
   if not b:break
   s.sendall(b)
  if s in ready:
   b=s.recv(65536)
   if not b:break
   while b:
    n=os.write(1,b);b=b[n:]
finally:s.close()
"""


def _qemu(recipe=None):
    kvm = recipe is not None and recipe.get("acceleration") is not None
    return [
        "qemu-system-x86_64",
        "-machine",
        "q35,dump-guest-core=off",
        "-accel",
        "kvm" if kvm else "tcg,thread=multi",
        "-cpu",
        "host" if kvm else "max",
        "-smp",
        "8",
        "-m",
        "32768",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        "file:/lab/serial.log",
        "-drive",
        "file=/lab/probe.qcow2,if=virtio,format=qcow2",
        "-drive",
        "file=/lab/seed.iso,format=raw,media=cdrom,readonly=on",
        "-netdev",
        "user,id=net0,restrict=on,hostfwd=tcp:0.0.0.0:2222-:22,hostfwd=tcp:0.0.0.0:6443-:6443,guestfwd=tcp:10.0.2.100:8444-cmd:python3 -I /lab/service-link-forward.py gitlab,guestfwd=tcp:10.0.2.101:8443-cmd:python3 -I /lab/service-link-forward.py registry",
        "-device",
        "virtio-net-pci,netdev=net0,romfile=",
    ]


RUNTIME_PYTHON_CHECK = "import json,os,select,socket,ssl,stat,sys; assert sys.version_info >= (3,10); print(json.dumps({'kind':'fixed-service-forward-python','version':list(sys.version_info[:3]),'modules':['json','os','select','socket','ssl','stat']}))"

SEED_COMMAND = (
    "umask 077; command -v python3 >/dev/null || exit 73; "
    + "python3 -I -c "
    + shlex.quote(RUNTIME_PYTHON_CHECK)
    + " > /lab/service-forward-python.json || exit 73; "
    + "test ! -e /lab/probe.qcow2 && test ! -e /lab/seed.iso && "
    "cloud-localds /lab/seed.iso /lab/user-data /lab/meta-data && "
    "qemu-img create -f qcow2 -F qcow2 -b /lab/ubuntu.img /lab/probe.qcow2 160G && "
    "qemu-img info --output=json /lab/probe.qcow2 > /lab/disk-info.json && "
    "cp /tool-packages.tsv /lab/tool-packages.tsv"
)


def execute(packet, support, recipes, purge, *, accelerator=None):
    recipe = recipes.validate(packet["recipe"])
    acceleration = recipe.get("acceleration")
    if acceleration is not None and accelerator is None:
        raise ValueError("Fresh KVM execution requires its exact packaged admission helper")
    action, operation = packet["action"], packet["operation_id"]
    if action not in ("up", "down", "reconcile", "credentials") or not re.fullmatch(
        "[a-f0-9-]{36}", operation
    ):
        raise ValueError("Unsupported fresh operation")
    expected = recipes.binding(
        recipe,
        run_id=packet["run_id"],
        environment_id=packet["environment_id"],
        identities=packet["identities"],
        modules=packet["modules"],
    )
    if (
        packet["binding"] != expected
        or type(packet["timeout"]) not in (float, int)
        or not 0 < packet["timeout"] <= 3600
    ):
        raise ValueError("Fresh operation binding or deadline differs")
    expiry = datetime.fromisoformat(packet["expires_at"].replace("Z", "+00:00"))
    if expiry.tzinfo is None or action == "up" and datetime.now(timezone.utc) >= expiry:
        raise ValueError("Fresh provisioning authorization expired")
    deadline = time.monotonic() + packet["timeout"]
    parent = support._private(Path(recipe["remote_parent"]))
    root = Path(recipes.generation(recipe, packet["run_id"]))
    parent_identity = parent.stat()
    intent = parent / (packet["run_id"] + ".intent.json")
    initial = {
        "binding": expected,
        "run_id": packet["run_id"],
        "up_operation_id": operation,
        "source": "fresh-owned-tcg-v1",
        "parent_identity": {"device": parent_identity.st_dev, "inode": parent_identity.st_ino},
    }
    if action == "up":
        if os.getuid() == 0 or os.geteuid() == 0:
            raise ValueError("Fresh guest requires a nonroot operator")
        if acceleration is not None:
            initial["acceleration_admission"] = accelerator.preflight(acceleration)
        # Admission descriptors are already closed. Exclusive durable intent
        # precedes persistent resources; an existing attempt is never adopted.
        support._write(intent, recipes.canonical(initial))
        os.mkdir(root, 0o700)
        root_info = root.stat()
        initial["root_identity"] = {"device": root_info.st_dev, "inode": root_info.st_ino}
        support._write(root / "owner.json", recipes.canonical(initial))
        support._write(root / "lock", b"")
    if not root.exists():
        raise ValueError("Fresh generation outcome is unobserved; absence is not nonexecution")
    support._private(root)
    owner = support._json(support._read(root / "owner.json"))
    root_info = root.stat()
    if (
        owner.get("binding") != expected
        or owner.get("run_id") != packet["run_id"]
        or owner.get("root_identity") != {"device": root_info.st_dev, "inode": root_info.st_ino}
        or owner.get("parent_identity") != {"device": parent_identity.st_dev, "inode": parent_identity.st_ino}
    ):
        raise ValueError("Fresh generation ownership or inode changed")
    observed_root = {"device": root_info.st_dev, "inode": root_info.st_ino}
    if packet.get("expected_root_identity") is not None and packet["expected_root_identity"] != observed_root:
        raise ValueError("Fresh generation differs from its original direct observation")
    if action in ("down", "credentials") and packet.get("expected_root_identity") is None:
        raise ValueError("Fresh mutation requires its original observed generation")
    retained_intent = support._json(support._read(intent))
    if retained_intent != {key: value for key, value in owner.items() if key != "root_identity"}:
        raise ValueError("Fresh creation intent differs from its original generation")
    locked = support._lock_descriptor(root / "lock")
    fcntl.flock(
        locked, (fcntl.LOCK_SH if action in ("reconcile", "credentials") else fcntl.LOCK_EX) | fcntl.LOCK_NB
    )
    state_path = root / "state.json"
    state = (
        support._json(support._read(state_path))
        if state_path.exists()
        else {
            "ids": {},
            "history": [owner["up_operation_id"]],
            "inflight": None,
            "assets": {},
            "disk_identity": None,
            "stage": "exclusive-generation-created",
            "guest_freshness": None,
            "api": None,
            "purge": None,
        }
    )
    data = root / "data"
    names = {role: "devsecops-fresh-" + expected[:20] + "-" + role for role in ("network", "seed", "runtime")}

    if (
        type(state) is not dict
        or type(state.get("ids")) is not dict
        or set(state["ids"]) - set(names)
        or any(not re.fullmatch("[a-f0-9]{64}", value) for value in state["ids"].values())
        or type(state.get("history")) is not list
        or len(state["history"]) > 32
        or owner["up_operation_id"] not in state["history"]
        or type(state.get("assets")) is not dict
    ):
        os.close(locked)
        raise ValueError("Fresh retained operation inventory is malformed")
    inflight = state.get("inflight")
    if inflight is not None and (
        type(inflight) is not dict
        or set(inflight) != {"operation_id", "role", "verb"}
        or inflight["operation_id"] not in state["history"]
        or inflight["role"] not in names
        or inflight["verb"] not in ("create", "start", "stop", "remove")
    ):
        os.close(locked)
        raise ValueError("Fresh unresolved operation identity is malformed")
    if state.get("purge") is not None and (
        state["purge"].get("root_identity") != observed_root
        or state["purge"].get("operation_id") not in state["history"]
    ):
        os.close(locked)
        raise ValueError("Fresh purge generation differs from original creation")

    def pending_resolved(resources):
        pending = state["inflight"]
        if pending is None:
            return True
        item = resources[pending["role"]]
        return (
            (
                pending["verb"] == "create"
                and item is not None
                and (
                    item.get("Labels", {})
                    if pending["role"] == "network"
                    else item.get("Config", {}).get("Labels", {})
                ).get(OPERATION)
                == pending["operation_id"]
            )
            or (pending["verb"] == "remove" and item is None)
            or (pending["verb"] == "stop" and item is not None and not item["State"].get("Running"))
            or (
                pending["verb"] == "start"
                and item is not None
                and (item["State"].get("Running") or item["State"].get("Status") == "exited")
            )
        )

    def check():
        if time.monotonic() >= deadline:
            raise ValueError("Fresh operation deadline expired")
        if action == "up" and datetime.now(timezone.utc) >= expiry:
            raise ValueError("Fresh provisioning authorization expired")
        support._private(root)
        support._private(parent)
        if (
            root.stat().st_ino != owner["root_identity"]["inode"]
            or root.stat().st_dev != owner["root_identity"]["device"]
            or parent.stat().st_dev != owner["parent_identity"]["device"]
            or parent.stat().st_ino != owner["parent_identity"]["inode"]
        ):
            raise ValueError("Fresh generation was replaced")
        if support._json(support._read(root / "owner.json")) != owner:
            raise ValueError("Fresh ownership changed")
        if state["disk_identity"] is not None and state["purge"] is None:
            info = support._path(data / "probe.qcow2").stat()
            if state["disk_identity"] != {"device": info.st_dev, "inode": info.st_ino}:
                raise ValueError("Fresh root disk was replaced")

    def save():
        check()
        support._write(state_path, recipes.canonical(state), replace=state_path.exists())

    def command(argv, seconds=30, *, allow_failure=False, data_bytes=None):
        check()
        result = support._run(
            argv, min(deadline, time.monotonic() + seconds), check=not allow_failure, data=data_bytes
        )
        check()
        return result

    def inspect(kind, identifier):
        result = command(["docker", kind, "inspect", identifier], allow_failure=True)
        if result.returncode:
            if b"No such" in result.stderr or b"not found" in result.stderr:
                return None
            raise ValueError("Fresh resource inspection failed")
        values = support._json(result.stdout)
        if type(values) is not list or len(values) != 1:
            raise ValueError("Fresh resource inspection is ambiguous")
        return values[0]

    def observed():
        found = {}
        for role in names:
            kind = "network" if role == "network" else "container"
            item = inspect(kind, state["ids"].get(role, names[role]))
            matches = (
                command(
                    [
                        "docker",
                        kind,
                        "ls",
                        *(["-a"] if kind == "container" else []),
                        "-q",
                        "--no-trunc",
                        "--filter",
                        "label=" + LABEL + "=" + expected,
                        "--filter",
                        "label=" + ROLE + "=" + role,
                    ]
                )
                .stdout.decode()
                .split()
            )
            if item is not None:
                labels = (
                    item.get("Labels", {}) if role == "network" else item.get("Config", {}).get("Labels", {})
                )
                if (
                    not re.fullmatch("[a-f0-9]{64}", item.get("Id", ""))
                    or labels.get(LABEL) != expected
                    or labels.get(ROLE) != role
                    or labels.get(OPERATION) not in state["history"]
                    or matches != [item["Id"]]
                    or state["ids"].get(role, item["Id"]) != item["Id"]
                    or item.get("Name", "").removeprefix("/") != names[role]
                ):
                    raise ValueError("Fresh resource ID or ownership differs")
                if role == "network":
                    if item.get("Internal") is not True or item.get("Driver") != "bridge":
                        raise ValueError("Fresh network isolation differs")
                else:
                    config, host = item.get("Config", {}), item.get("HostConfig", {})
                    wanted = ["sh", "-c", SEED_COMMAND] if role == "seed" else _qemu(recipe)
                    selected_acceleration = acceleration if role == "runtime" else None
                    device_contract = (accelerator.runtime_matches(host, selected_acceleration)
                                       if accelerator is not None else not host.get("Devices") and not host.get("GroupAdd"))
                    memory = 512 * 1024**2 if role == "seed" else 34 * 1024**3
                    network = "none" if role == "seed" else names["network"]
                    mounts = item.get("Mounts", [])
                    if (
                        item.get("Image") != recipe["tool_image"]
                        or config.get("User") != f"{os.getuid()}:{os.getgid()}"
                        or config.get("Entrypoint") not in (None, [])
                        or config.get("Cmd") != wanted
                        or config.get("Healthcheck", {}).get("Test") != ["NONE"]
                        or host.get("Privileged") is not False
                        or host.get("ReadonlyRootfs") is not True
                        or host.get("CapDrop") != ["ALL"]
                        or host.get("CapAdd")
                        or not device_contract
                        or host.get("SecurityOpt") not in (["no-new-privileges"], ["no-new-privileges=true"])
                        or host.get("NetworkMode") != network
                        or host.get("Binds")
                        or host.get("PidMode") not in ("", None)
                        or host.get("IpcMode") not in ("private", "")
                        or host.get("PidsLimit") != 256
                        or host.get("Memory") != memory
                        or host.get("MemorySwap") != memory
                        or host.get("NanoCpus") != (10**9 if role == "seed" else 8 * 10**9)
                        or host.get("PortBindings")
                        or host.get("RestartPolicy", {}).get("Name") not in ("no", "")
                        or len(mounts) != 1
                        or mounts[0].get("Type") != "bind"
                        or mounts[0].get("Source") != str(data)
                        or mounts[0].get("Destination") != "/lab"
                        or mounts[0].get("RW") is not True
                    ):
                        raise ValueError("Fresh container confinement differs")
                    if role == "runtime":
                        attached = item.get("NetworkSettings", {}).get("Networks", {})
                        if set(attached) != {names["network"]} or found["network"] is None:
                            raise ValueError("Fresh runtime network attachment differs")
                        nid = attached[names["network"]].get("NetworkID")
                        created = item.get("State", {}).get("Status") == "created"
                        if nid != found["network"]["Id"] and not (created and nid in ("", None)):
                            raise ValueError("Fresh runtime network ID changed")
                found[role] = item
            elif matches:
                raise ValueError("Fresh owned resource inventory is incomplete")
            else:
                found[role] = None
        return found

    def mutate(argv, role, verb, seconds=30):
        if state["inflight"] is not None:
            raise ValueError("An unresolved fresh mutation must be reconciled first")
        state["inflight"] = {"operation_id": operation, "role": role, "verb": verb}
        save()
        return command(argv, seconds)

    def acknowledge(role, verb, identifier=None):
        values = observed()
        item = values[role]
        if verb == "create":
            if item is None or item["Id"] != identifier:
                raise ValueError("Fresh creation acknowledgement not independently observed")
            state["ids"][role] = identifier
        elif verb == "remove" and item is not None:
            raise ValueError("Fresh resource absence not observed")
        elif verb == "start" and item is None:
            raise ValueError("Fresh started resource missing")
        state["inflight"] = None
        save()
        return values

    def container(role):
        memory = 512 * 1024**2 if role == "seed" else 34 * 1024**3
        args = [
            "docker",
            "create",
            "--pull=never",
            "--name",
            names[role],
            "--label",
            LABEL + "=" + expected,
            "--label",
            ROLE + "=" + role,
            "--label",
            OPERATION + "=" + operation,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--pids-limit",
            "256",
            "--ulimit",
            "core=0",
            "--mount",
            f"type=bind,src={data},dst=/lab",
            "--network",
            "none" if role == "seed" else names["network"],
            "--cpus",
            "1" if role == "seed" else "8",
            "--memory",
            str(memory),
            "--memory-swap",
            str(memory),
            "--no-healthcheck",
            "--entrypoint",
            "",
        ]
        if role == "runtime" and accelerator is not None:
            args += accelerator.docker_arguments(acceleration)
        args += [recipe["tool_image"]]
        args += ["sh", "-c", SEED_COMMAND] if role == "seed" else _qemu(recipe)
        identifier = mutate(args, role, "create").stdout.decode().strip()
        acknowledge(role, "create", identifier)
        return identifier

    def assets_snapshot(copy=False):
        selected = {"boot": recipe["boot_image"], **recipe["rke2_assets"], **recipe["ssh_pki"]}
        destinations = {
            "boot": "ubuntu.img",
            **{name: "rke2-artifacts/" + name for name in recipe["rke2_assets"]},
            "operator_private": "ssh/operator",
            "operator_public": "ssh/operator.pub",
            "host_private": "ssh/host",
            "host_public": "ssh/host.pub",
        }
        result = {}
        for name, pin in selected.items():
            result[name] = _asset(
                pin,
                deadline=deadline,
                destination=data / destinations[name] if copy else None,
                secret=name in ("operator_private", "host_private"),
            )
        return result

    try:
        resources = observed()
        if action == "up":
            if any(resources.values()) or state["stage"] != "exclusive-generation-created":
                raise ValueError("Fresh provisioning cannot adopt or repeat an existing effect")
            tool = inspect("image", recipe["tool_image"])
            if tool is None or tool.get("Id") != recipe["tool_image"]:
                raise ValueError("Fresh cached tool image unavailable; acquisition is separate")
            memory = (
                int(
                    next(
                        line.split()[1]
                        for line in Path("/proc/meminfo").read_text().splitlines()
                        if line.startswith("MemAvailable:")
                    )
                )
                * 1024
            )
            if memory < 44 * 1024**3 or shutil.disk_usage(parent).free < 310 * 1024**3:
                raise ValueError("Fresh guest reference memory/disk headroom is unavailable")
            support._private(data, create=True)
            for name in ("ssh", "rke2-artifacts", "operator"):
                support._private(data / name, create=True)
            state["stage"] = "copy-source-assets"
            save()
            state["assets"] = assets_snapshot(copy=True)
            support._write(data / "service-link-forward.py", GUEST_SERVICE_FORWARD.encode())
            save()
            for name in ("operator", "host"):
                public = command(["ssh-keygen", "-y", "-f", str(data / "ssh" / name)]).stdout.strip()
                selected = support._read(data / "ssh" / (name + ".pub")).strip().split()
                if public.split() != selected[:2]:
                    raise ValueError("Pinned guest SSH key pair does not match")
            seed = _cloud_seed(
                support._read(data / "ssh/operator.pub").decode(),
                support._read(data / "ssh/host").decode(),
                support._read(data / "ssh/host.pub").decode(),
                packet["run_id"],
            )
            support._write(data / "user-data", seed)
            support._write(
                data / "meta-data",
                recipes.canonical({"instance-id": packet["run_id"], "local-hostname": "devsecops-fresh"}),
            )
            state["seed_sha256"] = hashlib.sha256(seed).hexdigest()
            state["stage"] = "derive-new-root-disk-and-seed"
            save()
            seed_id = container("seed")
            mutate(["docker", "start", "-a", seed_id], "seed", "start", 120)
            resources = observed()
            if resources["seed"]["State"].get("ExitCode") == 73:
                raise ValueError(
                    "Pinned QEMU tool image lacks Python 3.10+ and required fixed service-forward stdlib modules"
                )
            if (
                resources["seed"]["State"].get("Status") != "exited"
                or resources["seed"]["State"].get("ExitCode") != 0
            ):
                raise ValueError("Fresh disk and seed helper did not finish successfully")
            runtime_python = support._json(support._read(data / "service-forward-python.json"))
            if (
                runtime_python.get("kind") != "fixed-service-forward-python"
                or runtime_python.get("modules") != ["json", "os", "select", "socket", "ssl", "stat"]
                or type(runtime_python.get("version")) is not list
                or len(runtime_python["version"]) != 3
                or any(type(v) is not int for v in runtime_python["version"])
                or tuple(runtime_python["version"]) < (3, 10, 0)
            ):
                raise ValueError("Pinned tool runtime Python prerequisite observation differs")
            state["service_forward_python"] = runtime_python
            save()
            disk_info = support._json(support._read(data / "disk-info.json"))
            if (
                disk_info.get("format") != "qcow2"
                or disk_info.get("virtual-size") != 160 * 1024**3
                or disk_info.get("backing-filename") != "/lab/ubuntu.img"
            ):
                raise ValueError("Fresh derived root disk geometry or backing changed")
            packages = dict(
                line.split("\t") for line in support._read(data / "tool-packages.tsv").decode().splitlines()
            )
            required = {
                "qemu-system-x86": "1:8.2.2+ds-0ubuntu1.18",
                "qemu-utils": "1:8.2.2+ds-0ubuntu1.18",
                "cloud-image-utils": "0.33-1",
            }
            if any(packages.get(name) != version for name, version in required.items()):
                raise ValueError("Fresh tool package inventory differs from the reviewed versions")
            state["tool_package_inventory_sha256"] = hashlib.sha256(
                support._read(data / "tool-packages.tsv")
            ).hexdigest()
            disk = support._path(data / "probe.qcow2").stat()
            state["disk_identity"] = {"device": disk.st_dev, "inode": disk.st_ino}
            state["root_disk_creation"] = {
                "exclusive_generation": owner["root_identity"],
                "initial_source_sha256": recipe["boot_image"]["sha256"],
                "helper_id": seed_id,
                "helper_exit_code": 0,
                "disk_identity": state["disk_identity"],
                "virtual_bytes": 160 * 1024**3,
            }
            state["inflight"] = None
            state["stage"] = "new-root-disk-observed"
            save()
            mutate(["docker", "rm", seed_id], "seed", "remove")
            acknowledge("seed", "remove")
            net = (
                mutate(
                    [
                        "docker",
                        "network",
                        "create",
                        "--internal",
                        "--label",
                        LABEL + "=" + expected,
                        "--label",
                        ROLE + "=network",
                        "--label",
                        OPERATION + "=" + operation,
                        names["network"],
                    ],
                    "network",
                    "create",
                )
                .stdout.decode()
                .strip()
            )
            acknowledge("network", "create", net)
            runtime = container("runtime")
            mutate(["docker", "start", runtime], "runtime", "start")
            resources = acknowledge("runtime", "start")
            network = resources["runtime"]["NetworkSettings"]["Networks"][names["network"]]
            address = str(ipaddress.IPv4Address(network["IPAddress"]))
            if not ipaddress.IPv4Address(address).is_private:
                raise ValueError("Fresh API address is not private")
            ssh = support._guest_ssh(data, address, root, runtime, write_hosts=True)
            boot_end = min(deadline, time.monotonic() + 600)
            while time.monotonic() < boot_end:
                result = command(
                    [*ssh, "test -f /run/devsecops-fresh-" + packet["run_id"]], 20, allow_failure=True
                )
                if result.returncode == 0:
                    break
                if result.returncode != 1 and not support._ssh_pending(result):
                    raise ValueError("Fresh boot observation failed")
                time.sleep(min(3, max(0, boot_end - time.monotonic())))
            else:
                raise ValueError("Fresh guest did not reach its pinned seed boot marker")
            gateway = support._reply_route_target(resources["network"], address)
            route = command(
                [*ssh, shlex.join(["sudo", "python3", "-I", "-c", support.GUEST_REPLY_ROUTE, gateway])], 30
            )
            state["guest_reply_route"] = support._json(route.stdout)
            state["stage"] = "verify-pristine-guest"
            save()
            pristine = command(
                [
                    *ssh,
                    shlex.join(
                        [
                            "sudo",
                            "python3",
                            "-I",
                            "-c",
                            "import pathlib;assert not pathlib.Path('/etc/rancher/rke2').exists();assert not pathlib.Path('/var/lib/rancher/rke2').exists();print('fresh-rke2-absent')",
                        ]
                    ),
                ],
                30,
            )
            if pristine.stdout.strip() != b"fresh-rke2-absent":
                raise ValueError("Guest already contains RKE2 state")
            state["guest_freshness"] = {
                "before_install": "rke2-config-and-data-absent",
                "seed_sha256": state["seed_sha256"],
            }
            save()
            _install_guest(command, ssh, data, state, save, recipes, deadline)
            state["stage"] = "ready"
            state["address"] = address
            save()
        elif action == "credentials":
            if state["stage"] != "ready" or state["inflight"] is not None or not resources["runtime"]:
                raise ValueError("Fresh credentials require the directly observed running generation")
            return {
                "binding": expected,
                "operation_id": operation,
                "private_kubeconfig_b64": base64.b64encode(
                    support._read(data / "operator/kubeconfig.yaml")
                ).decode(),
                "ca_sha256": state["api"]["ca_sha256"],
            }
        elif action == "down":
            tool = inspect("image", recipe["tool_image"])
            if tool is None or tool.get("Id") != recipe["tool_image"]:
                raise ValueError("Supplied fresh tool image is no longer preserved")
            preserved = assets_snapshot()
            if state["assets"] and preserved != state["assets"]:
                raise ValueError("Supplied fresh source assets changed before cleanup")
            if operation in state["history"]:
                raise ValueError("Fresh cleanup already attempted; reconcile without retry")
            if not pending_resolved(resources):
                raise ValueError("Fresh mutation outcome remains unresolved; cleanup cannot replace it")
            if state["inflight"] is not None:
                state.setdefault("reconciled_effects", []).append(state["inflight"])
                state["inflight"] = None
            state["history"].append(operation)
            state["stage"] = "cleanup"
            save()
            for role in ("runtime", "seed"):
                item = resources[role]
                if item is None:
                    continue
                if item["State"].get("Running"):
                    mutate(["docker", "stop", "--time", "10", item["Id"]], role, "stop", 30)
                    resources = observed()
                    if resources[role]["State"].get("Running"):
                        raise ValueError("Fresh stop not observed")
                    state["inflight"] = None
                    save()
                mutate(["docker", "rm", item["Id"]], role, "remove")
                resources = acknowledge(role, "remove")
            if resources["network"]:
                if resources["network"].get("Containers"):
                    raise ValueError("Fresh network still has members")
                mutate(["docker", "network", "rm", resources["network"]["Id"]], "network", "remove")
                resources = acknowledge("network", "remove")
            preserved = assets_snapshot()
            if state["assets"] and preserved != state["assets"]:
                raise ValueError("Supplied fresh source assets changed")
            state["purge"] = {
                "operation_id": operation,
                "state": "unknown",
                "root_identity": owner["root_identity"],
            }
            save()
            purge._purge_owned_payload(root, (root_info.st_dev, root_info.st_ino), deadline)
            state["purge"]["state"] = "absent"
            state["stage"] = "absent"
            save()
        # Reconciliation only observes; absent delayed creates stay unknown.
        resources = observed()
        unresolved = [] if pending_resolved(resources) else [state["inflight"]]
        removed = (
            not data.exists() and not data.is_symlink() and not any(resources.values()) and not unresolved
        )
        preserved = assets_snapshot()
        tool = inspect("image", recipe["tool_image"])
        tool_preserved = tool is not None and tool.get("Id") == recipe["tool_image"]
        source_preserved = not state["assets"] or preserved == state["assets"]
        complete = (
            removed
            and source_preserved
            and tool_preserved
            and (state.get("purge") or {}).get("operation_id") == operation
        )
        result = {
            "binding": expected,
            "operation_id": operation,
            "run_id": packet["run_id"],
            "generation": packet["run_id"],
            "root_identity": owner["root_identity"],
            "up_operation_id": owner["up_operation_id"],
            "state": state["stage"],
            "root_disk_creation": state.get("root_disk_creation"),
            "guest_freshness": state["guest_freshness"],
            "service_forward_python": state.get("service_forward_python"),
            "api": state["api"],
            "address": state.get("address"),
            "source_assets": preserved,
            "source_assets_preserved": source_preserved,
            "tool_image_preserved": tool_preserved,
            "ssh_pki_basis": "operator-supplied-pins-with-run-created-copies",
            "unresolved_operations": unresolved,
            "complete_cleanup": complete,
            "mutation_effect": "applied"
            if not unresolved and (complete or state["disk_identity"] is not None or any(resources.values()))
            else "unknown",
            "resources": [
                {
                    "kind": role,
                    "identity": item["Id"] if item else state["ids"].get(role),
                    "disposition": "remove",
                    "state": "present" if item else "absent",
                }
                for role, item in resources.items()
            ]
            + [
                {
                    "kind": "supplied-tool-image",
                    "identity": recipe["tool_image"],
                    "disposition": "preserve",
                    "state": "preserved" if tool_preserved else "changed",
                },
                {
                    "kind": "disposable-disk-seed-pki",
                    "identity": state["disk_identity"],
                    "path": str(data),
                    "disposition": "remove",
                    "state": "absent" if removed else "present",
                },
                {
                    "kind": "supplied-assets",
                    "identity": preserved,
                    "disposition": "preserve",
                    "state": "preserved" if source_preserved else "changed",
                },
                {
                    "kind": "ownership-reconciliation-evidence",
                    "identity": owner["root_identity"],
                    "disposition": "preserve",
                    "state": "preserved",
                },
            ],
        }
        if acceleration is not None:
            result["acceleration_admission"] = owner["acceleration_admission"]
        check()
        return result
    finally:
        os.close(locked)


def _install_guest(command, ssh, data, state, save, recipes, deadline):
    destination = "/home/demo/rke2-artifacts"
    state["stage"] = "stage-pinned-rke2-assets"
    save()
    command([*ssh, "mkdir -m 700 " + destination], 30)
    scp = ["scp", "-O", *ssh[1:-3], "-P", ssh[-2]]
    for name in recipes.ASSETS:
        command([*scp, str(data / "rke2-artifacts" / name), ssh[-1] + ":" + destination + "/"], 600)
    verification = """import hashlib,json,pathlib
assets=json.loads(%r)
for name,(size,digest) in assets.items():
 p=pathlib.Path('/home/demo/rke2-artifacts')/name;h=hashlib.sha256()
 with p.open('rb') as f:
  while b:=f.read(1048576):h.update(b)
 assert p.stat().st_size==size and h.hexdigest()==digest
print('verified')
""" % json.dumps(recipes.ASSETS)
    if command([*ssh, shlex.join(["python3", "-I", "-c", verification])], 300).stdout.strip() != b"verified":
        raise ValueError("Guest RKE2 assets differ from their exact source pins")
    configuration = {
        "write-kubeconfig-mode": "0600",
        "cni": "canal",
        "kubelet-arg": ["runtime-request-timeout=10m"],
        "node-name": "devsecops-fresh",
        "node-ip": "10.0.2.15",
        "disable": ["rke2-traefik", "rke2-ingress-nginx"],
    }
    code = """import os,pathlib
p=pathlib.Path('/etc/rancher/rke2');p.mkdir(parents=True,mode=0o700,exist_ok=False)
fd=os.open(p/'config.yaml',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:f.write(%r)
""" % (json.dumps(configuration) + "\n")
    state["stage"] = "install-rke2"
    state["guest_install_intent"] = {
        "configuration_sha256": hashlib.sha256((json.dumps(configuration) + "\n").encode()).hexdigest(),
        "version": recipes.VERSION,
        "effect": "unknown",
    }
    save()
    command([*ssh, shlex.join(["sudo", "python3", "-I", "-c", code])], 30)
    command(
        [
            *ssh,
            shlex.join(
                [
                    "sudo",
                    "env",
                    "INSTALL_RKE2_METHOD=tar",
                    "INSTALL_RKE2_ARTIFACT_PATH=" + destination,
                    "INSTALL_RKE2_VERSION=" + recipes.VERSION,
                    "sh",
                    destination + "/install.sh",
                ]
            ),
        ],
        180,
    )
    command(
        [
            *ssh,
            "sudo systemctl enable rke2-server.service && sudo systemctl start --no-block rke2-server.service",
        ],
        30,
    )
    probe = """import json,pathlib,subprocess
k=['/var/lib/rancher/rke2/bin/kubectl','--kubeconfig=/etc/rancher/rke2/rke2.yaml','--request-timeout=5s']
try:
 def get(a):return subprocess.check_output(k+a,stderr=subprocess.DEVNULL,timeout=10).decode()
 ready=get(['get','--raw=/readyz']).strip();version=json.loads(get(['version','-o','json']))['serverVersion']['gitVersion']
 nodes=json.loads(get(['get','nodes','-o','json']))['items']
 result={'readyz':ready,'server_version':version,'nodes':[{'name':n['metadata']['name'],'uid':n['metadata']['uid'],
  'kubelet_version':n['status']['nodeInfo']['kubeletVersion'],'ready':any(c['type']=='Ready' and c['status']=='True' for c in n['status']['conditions'])} for n in nodes],
  'namespaces':{name:json.loads(get(['get','namespace',name,'-o','json']))['metadata']['uid'] for name in ('default','kube-system')}}
 print(json.dumps(result))
except (OSError,ValueError,KeyError,subprocess.SubprocessError):print('{"readyz":"not-ready","nodes":[]}')
"""
    gate = min(deadline, time.monotonic() + 1200)
    observations = []
    consecutive = 0
    fingerprint = None
    while time.monotonic() < gate:
        result = command(
            [*ssh, shlex.join(["sudo", "python3", "-I", "-c", probe])],
            min(50, max(0.1, gate - time.monotonic())),
            allow_failure=True,
        )
        if result.returncode != 0:
            raise ValueError("Fresh authenticated readiness probe failed")
        value = json.loads(result.stdout)
        nodes = value.get("nodes", [])
        namespaces = value.get("namespaces", {})
        ready = (
            value.get("readyz") == "ok"
            and value.get("server_version") == recipes.VERSION
            and len(nodes) == 1
            and nodes[0].get("ready") is True
            and nodes[0].get("kubelet_version") == recipes.VERSION
            and all(
                isinstance(v, str) and re.fullmatch("[a-zA-Z0-9._:-]{1,128}", v)
                for v in [nodes[0].get("uid"), namespaces.get("default"), namespaces.get("kube-system")]
            )
        )
        current = {"nodes": {n["name"]: n["uid"] for n in nodes}, "namespaces": namespaces} if ready else None
        if ready and fingerprint is not None and current != fingerprint:
            raise ValueError("Fresh cluster identity changed during readiness")
        if ready:
            fingerprint = current
        consecutive = consecutive + 1 if ready else 0
        observations.append(value)
        if len(observations) > 100:
            raise ValueError("Fresh readiness observation bound exceeded")
        state["readiness_observations"] = observations
        save()
        if consecutive == 3:
            break
        time.sleep(min(15, max(0, gate - time.monotonic())))
    if consecutive != 3:
        raise ValueError("Fresh RKE2 did not pass three consecutive ready observations")
    raw = command([*ssh, "sudo cat /etc/rancher/rke2/rke2.yaml"], 30).stdout
    if not 0 < len(raw) < 1024 * 1024:
        raise ValueError("Fresh kubeconfig exceeds its private bound")
    match = re.search(rb"^\s+certificate-authority-data:\s*([A-Za-z0-9+/=]+)\s*$", raw, re.MULTILINE)
    if match is None:
        raise ValueError("Fresh kubeconfig contains no issued CA")
    ca = base64.b64decode(match[1], validate=True)
    if not ca.startswith(b"-----BEGIN CERTIFICATE-----"):
        raise ValueError("Fresh issued API CA is not PEM")
    for name, value in [("kubeconfig.yaml", raw), ("api-ca.crt", ca)]:
        target = data / "operator" / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    state["api"] = {
        "ca_sha256": hashlib.sha256(ca).hexdigest(),
        "fingerprint": fingerprint,
        "credential_basis": "issued-by-newly-installed-observed-guest",
        "consecutive_ready_observations": 3,
    }
    state["guest_install_intent"]["effect"] = "applied"
    save()
