"""Fixed creation/observation/removal of one separate nonroot runner guest.

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

LABEL = "io.devsecops.runner-vm.binding"
ROLE = "io.devsecops.runner-vm.role"
OPERATION = "io.devsecops.runner-vm.operation"
PAYLOAD_FILES = ("ubuntu.img", "probe.qcow2", "seed.iso", "user-data", "meta-data")


def _asset_pins(recipe):
    pins = {"boot": recipe["boot_image"], **recipe["ssh_pki"]}
    external = recipe["egress"]["external_ca_bundle"]
    if external is not None:
        info = os.lstat(external["path"])
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 256000:
            raise ValueError("Runner external CA input must be a bounded regular public bundle")
        pins["external_ca"] = {**external, "bytes": info.st_size}
    return pins


def _asset(pin, *, deadline, destination=None, secret=False):
    source = Path(pin["path"])
    if any(p.is_symlink() for p in (source, *source.parents)):
        raise ValueError("Runner VM source asset path contains a link")
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
            raise ValueError("Runner VM source asset identity or mode is unsupported")
        if destination is not None:
            out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        digest, count = hashlib.sha256(), 0
        while True:
            if time.monotonic() >= deadline:
                raise ValueError("Runner VM source snapshot exceeded its deadline")
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            count += len(block)
            if count > pin["bytes"]:
                raise ValueError("Runner VM source grew during snapshot")
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
            raise ValueError("Runner VM supplied asset differs from its immutable pin")
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


def _host_entries(environment, recipe):
    if not re.fullmatch("[a-z][a-z0-9-]{0,62}", environment):
        raise ValueError("Runner guest environment is unsupported")
    entries = [
        ("10.0.2.100", "gitlab." + environment + ".test"),
        ("10.0.2.101", "registry." + environment + ".test"),
    ]
    for origin, address in (
        ("https://registry1.dso.mil", "10.0.2.102"),
        ("https://p1docs.dso.mil", "10.0.2.103"),
        ("https://ib-prod-harbor-storage.s3.us-gov-west-1.amazonaws.com", "10.0.2.104"),
    ):
        if origin in recipe["egress"]["external_origins"]:
            entries.append((address, origin.removeprefix("https://")))
    return entries


def _cloud_seed(operator_public, host_private, host_public, run_id, environment, recipe):
    if (
        not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]*)?\n?", operator_public)
        or not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]*)?\n?", host_public)
        or not host_private.startswith("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    ):
        raise ValueError("Runner VM guest requires pinned Ed25519 SSH material")
    return (
        "#cloud-config\n"
        + json.dumps(
            {
                "users": [
                    {
                        "name": "gitlab-runner",
                        "uid": 10001,
                        "primary_group": "gitlab-runner",
                        "groups": [],
                        "homedir": "/home/gitlab-runner",
                        "shell": "/bin/bash",
                        "lock_passwd": True,
                        "sudo": False,
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
                "manage_etc_hosts": False,
                "write_files": [
                    {
                        "path": "/etc/hosts",
                        "owner": "root:root",
                        "permissions": "0644",
                        "append": True,
                        "content": "\n"
                        + "\n".join(
                            address + " " + host for address, host in _host_entries(environment, recipe)
                        )
                        + "\n",
                    }
                ],
                "runcmd": [
                    [
                        "install",
                        "-d",
                        "-m",
                        "0700",
                        "-o",
                        "10001",
                        "-g",
                        "10001",
                        "/home/gitlab-runner/.local",
                        "/home/gitlab-runner/.local/share",
                        "/home/gitlab-runner/.local/share/devsecops",
                        "/home/gitlab-runner/.local/share/devsecops/" + run_id.replace("-", ""),
                        "/home/gitlab-runner/.local/share/devsecops/" + run_id.replace("-", "") + "/inputs",
                        "/home/gitlab-runner/builds",
                    ],
                    ["touch", "/run/devsecops-runner-vm-" + run_id],
                ],
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()


GUEST_SERVICE_FORWARD = """import ipaddress,json,os,select,socket,stat,sys
p='/lab/service-links.json'
f=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);i=os.fstat(f)
if not stat.S_ISREG(i.st_mode) or i.st_uid!=os.getuid() or i.st_nlink!=1 or i.st_mode&0o077:raise SystemExit(2)
r=os.read(f,16385);os.close(f)
if len(r)>16384:raise SystemExit(2)
v=json.loads(r);role=sys.argv[1]
roles={'gitlab','registry','registry1','p1docs','registry1-storage'}
if set(v)-{'schema_version','binding','gateway',*roles} or not {'schema_version','binding','gateway','gitlab','registry'}<=set(v) or v['schema_version']!=1 or role not in roles or role not in v:raise SystemExit(2)
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


def _netdev(recipe):
    roles = [("10.0.2.100", 8444, "gitlab"), ("10.0.2.101", 8443, "registry")]
    for origin, address, role in (
        ("https://registry1.dso.mil", "10.0.2.102", "registry1"),
        ("https://p1docs.dso.mil", "10.0.2.103", "p1docs"),
        ("https://ib-prod-harbor-storage.s3.us-gov-west-1.amazonaws.com", "10.0.2.104", "registry1-storage"),
    ):
        if origin in recipe["egress"]["external_origins"]:
            roles.append((address, 443, role))
    return "user,id=net0,restrict=on,hostfwd=tcp:0.0.0.0:2222-:22," + ",".join(
        f"guestfwd=tcp:{address}:{port}-cmd:python3 -I /lab/service-link-forward.py {role}"
        for address, port, role in roles
    )


def _guest_ssh(support, data, address, root, runtime, *, write_hosts=False):
    argv = support._guest_ssh(data, address, root, runtime, write_hosts=write_hosts)
    if argv[-1] != "demo@" + address:
        raise ValueError("Reviewed guest SSH template changed")
    return [*argv[:-1], "gitlab-runner@" + address]


GUEST_OBSERVE = """import grp,json,os,pathlib,platform,pwd,re,sys
u=pwd.getpwuid(os.getuid())
assert os.getuid()==os.geteuid()==10001 and os.getgid()==os.getegid()==10001
assert u.pw_name=='gitlab-runner' and u.pw_dir=='/home/gitlab-runner' and set(os.getgroups())<={10001}
assert not pathlib.Path('/etc/rancher/rke2').exists() and not pathlib.Path('/var/lib/rancher/rke2').exists()
run=sys.argv[1];assert re.fullmatch('[a-f0-9-]{36}',run)
assert pathlib.Path('/run/devsecops-runner-vm-'+run).is_file()
status=pathlib.Path('/proc/self/status').read_bytes();assert len(status)<64000
flags=[l.split(b':',1)[1].strip() for l in status.splitlines() if l.startswith(b'NoNewPrivs:')]
assert flags==[b'0']
assert platform.system()=='Linux' and platform.machine()=='x86_64'
boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip();assert re.fullmatch('[a-f0-9-]{36}',boot)
print(json.dumps({'guest_boot_id':boot,'guest_uid':10001,'guest_gid':10001,'guest_user':'gitlab-runner','guest_os':'linux','guest_architecture':'amd64','no_new_privs':0,'rke2_state':'absent'}))
"""


class RunnerVMRemoteContext:
    """Only the installed fixed child helpers receive this current original scope."""

    def __init__(self, packet, recipe, support, root, data, resources, state, command):
        self.packet, self.recipe, self.support = packet, recipe, support
        self.root, self.data, self.resources, self.state = root, data, resources, state
        self._command = command
        runtime, network = resources["runtime"], resources["network"]
        if runtime is None or network is None or runtime["State"].get("Running") is not True:
            raise ValueError("Runner guest requires its original running VM")
        selected = runtime["NetworkSettings"]["Networks"][network["Name"]]
        self.address = str(ipaddress.IPv4Address(selected["IPAddress"]))
        if not ipaddress.IPv4Address(self.address).is_private:
            raise ValueError("Runner guest endpoint is not private")
        self.gateway = support._reply_route_target(network, self.address)

    def command(self, argv, seconds=30, *, input_bytes=None, allow_failure=False):
        return self._command(argv, seconds, data_bytes=input_bytes, allow_failure=allow_failure)

    def ssh_argv(self):
        return _guest_ssh(self.support, self.data, self.address, self.root, self.resources["runtime"]["Id"])

    def observe_guest(self):
        result = self.command(
            [
                *self.ssh_argv(),
                shlex.join(["python3", "-I", "-B", "-c", GUEST_OBSERVE, self.packet["run_id"]]),
            ]
        )
        value = self.support._json(result.stdout)
        if (
            set(value)
            != {
                "guest_boot_id",
                "guest_uid",
                "guest_gid",
                "guest_user",
                "guest_os",
                "guest_architecture",
                "no_new_privs",
                "rke2_state",
            }
            or value["guest_uid"] != 10001
            or value["guest_gid"] != 10001
            or value["guest_user"] != "gitlab-runner"
            or value["guest_os"] != "linux"
            or value["guest_architecture"] != "amd64"
            or value["no_new_privs"] != 0
            or value["rke2_state"] != "absent"
            or not re.fullmatch("[a-f0-9-]{36}", value["guest_boot_id"])
        ):
            raise ValueError("Runner guest actual nonroot identity differs")
        if self.state["guest_identity"] is not None and value != self.state["guest_identity"]:
            raise ValueError("Runner guest boot or account changed")
        return value

    def identity(self):
        guest = self.state["guest_identity"]
        if guest is None:
            raise ValueError("Runner guest identity has not been observed")
        owner = self.support._json(self.support._read(self.root / "owner.json"))
        return {
            "binding": self.packet["binding"],
            "generation": "runner-vm-" + self.packet["run_id"],
            "guest_boot_id": guest["guest_boot_id"],
            "guest_uid": 10001,
            "guest_gid": 10001,
            "root_identity": owner["root_identity"],
            "disk_identity": self.state["disk_identity"],
            "runtime_id": self.resources["runtime"]["Id"],
            "network_id": self.resources["network"]["Id"],
            "ssh_host_key_sha256": self.recipe["ssh_pki"]["host_public"]["sha256"],
        }

    def endpoint(self):
        names = {
            "operator_private": "operator",
            "operator_public": "operator.pub",
            "host_private": "host",
            "host_public": "host.pub",
        }
        for name, filename in names.items():
            pin = {**self.recipe["ssh_pki"][name], "path": str(self.data / "ssh" / filename)}
            _asset(
                pin,
                deadline=time.monotonic() + min(30, self.packet["timeout"]),
                secret=name.endswith("private"),
            )
        external_ca = None
        if self.recipe["egress"]["external_ca_bundle"] is not None:
            pin = {**_asset_pins(self.recipe)["external_ca"], "path": str(self.data / "ssh/external-ca.pem")}
            _asset(pin, deadline=time.monotonic() + min(30, self.packet["timeout"]))
            raw = self.support._read(self.data / "ssh/external-ca.pem")
            external_ca = {
                "sha256": pin["sha256"],
                "bytes": len(raw),
                "ca_b64": base64.b64encode(raw).decode(),
            }
        return {
            "runner_vm": self.identity(),
            "run_id": self.packet["run_id"],
            "environment_id": self.packet["environment_id"],
            "address": self.address,
            "gateway": self.gateway,
            "port": 2222,
            "remote_directory": str(self.root),
            "data_directory": str(self.data),
            "network": {
                "id": self.resources["network"]["Id"],
                "name": self.resources["network"]["Name"],
                "gateway": self.gateway,
            },
            "runtime": {
                "id": self.resources["runtime"]["Id"],
                "name": self.resources["runtime"]["Name"].removeprefix("/"),
                "image_id": self.recipe["tool_image"],
            },
            "egress": self.recipe["egress"],
            "external_ca": external_ca,
        }


def _qemu(recipe):
    kvm = recipe.get("acceleration") is not None
    return [
        "qemu-system-x86_64",
        "-machine",
        "q35,dump-guest-core=off",
        "-accel",
        "kvm" if kvm else "tcg,thread=multi",
        "-cpu",
        "host" if kvm else "max",
        "-smp",
        str(recipe["resources"]["cpus"]),
        "-m",
        str(recipe["resources"]["memory_mib"]),
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
        _netdev(recipe),
        "-device",
        "virtio-net-pci,netdev=net0,romfile=",
    ]


RUNTIME_PYTHON_CHECK = "import json,os,select,socket,ssl,stat,sys; assert sys.version_info >= (3,10); print(json.dumps({'kind':'fixed-service-forward-python','version':list(sys.version_info[:3]),'modules':['json','os','select','socket','ssl','stat']}))"


def _seed_command(recipe):
    return (
        "umask 077; command -v python3 >/dev/null || exit 73; "
        + "python3 -I -c "
        + shlex.quote(RUNTIME_PYTHON_CHECK)
        + " > /lab/service-forward-python.json || exit 73; "
        + "test ! -e /lab/probe.qcow2 && test ! -e /lab/seed.iso && "
        "cloud-localds /lab/seed.iso /lab/user-data /lab/meta-data && "
        "qemu-img create -f qcow2 -F qcow2 -b /lab/ubuntu.img /lab/probe.qcow2 "
        + str(recipe["resources"]["disk_gib"])
        + "G && "
        "qemu-img info --output=json /lab/probe.qcow2 > /lab/disk-info.json && "
        "cp /tool-packages.tsv /lab/tool-packages.tsv"
    )


def execute(packet, support, recipes, purge, runner=None, routes=None, *, accelerator=None):
    recipe = recipes.validate(packet["recipe"])
    acceleration = recipe.get("acceleration")
    if acceleration is not None and accelerator is None:
        raise ValueError("Runner KVM execution requires its exact packaged admission helper")
    action, operation = packet["action"], packet["operation_id"]
    if action not in (
        "up",
        "down",
        "reconcile",
        "observe",
        "host-observe",
        "runner",
        "routes",
    ) or not re.fullmatch("[a-f0-9-]{36}", operation):
        raise ValueError("Unsupported runner-vm operation")
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
        raise ValueError("Runner VM operation binding or deadline differs")
    expiry = datetime.fromisoformat(packet["expires_at"].replace("Z", "+00:00"))
    provisioning = (
        action == "up"
        or action in ("runner", "routes")
        and packet.get("child", {}).get("action") in ("up", "install")
    )
    if expiry.tzinfo is None or provisioning and datetime.now(timezone.utc) >= expiry:
        raise ValueError("Runner VM provisioning authorization expired")
    deadline = time.monotonic() + packet["timeout"]
    parent = support._private(Path(recipe["remote_parent"]))
    root = Path(recipes.generation(recipe, packet["run_id"]))
    parent_identity = parent.stat()
    intent = parent / (packet["run_id"] + ".intent.json")
    initial = {
        "binding": expected,
        "run_id": packet["run_id"],
        "up_operation_id": operation,
        "source": "owned-runner-vm-v1",
        "parent_identity": {"device": parent_identity.st_dev, "inode": parent_identity.st_ino},
    }
    if action == "up":
        if os.getuid() == 0 or os.geteuid() == 0:
            raise ValueError("Runner VM guest requires a nonroot operator")
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
        raise ValueError("Runner VM generation outcome is unobserved; absence is not nonexecution")
    support._private(root)
    owner = support._json(support._read(root / "owner.json"))
    root_info = root.stat()
    if (
        owner.get("binding") != expected
        or owner.get("run_id") != packet["run_id"]
        or owner.get("root_identity") != {"device": root_info.st_dev, "inode": root_info.st_ino}
        or owner.get("parent_identity") != {"device": parent_identity.st_dev, "inode": parent_identity.st_ino}
    ):
        raise ValueError("Runner VM generation ownership or inode changed")
    observed_root = {"device": root_info.st_dev, "inode": root_info.st_ino}
    if packet.get("expected_root_identity") is not None and packet["expected_root_identity"] != observed_root:
        raise ValueError("Runner VM generation differs from its original direct observation")
    if (
        action in ("down", "runner", "routes", "observe", "host-observe")
        and packet.get("expected_root_identity") is None
    ):
        raise ValueError("Runner VM mutation requires its original observed generation")
    retained_intent = support._json(support._read(intent))
    if retained_intent != {key: value for key, value in owner.items() if key != "root_identity"}:
        raise ValueError("Runner VM creation intent differs from its original generation")
    locked = support._lock_descriptor(root / "lock")
    fcntl.flock(
        locked,
        (fcntl.LOCK_SH if action in ("reconcile", "observe", "host-observe") else fcntl.LOCK_EX)
        | fcntl.LOCK_NB,
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
            "guest_identity": None,
            "endpoint": None,
            "purge": None,
        }
    )
    data = root / "data"
    names = {
        role: "devsecops-runner-vm-" + expected[:20] + "-" + role for role in ("network", "seed", "runtime")
    }

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
        raise ValueError("Runner VM retained operation inventory is malformed")
    inflight = state.get("inflight")
    if inflight is not None and (
        type(inflight) is not dict
        or set(inflight) != {"operation_id", "role", "verb"}
        or inflight["operation_id"] not in state["history"]
        or inflight["role"] not in names
        or inflight["verb"] not in ("create", "start", "stop", "remove")
    ):
        os.close(locked)
        raise ValueError("Runner VM unresolved operation identity is malformed")
    if state.get("purge") is not None and (
        state["purge"].get("root_identity") != observed_root
        or state["purge"].get("operation_id") not in state["history"]
    ):
        os.close(locked)
        raise ValueError("Runner VM purge generation differs from original creation")
    original_vm = packet.get("expected_runner_vm")
    if original_vm is not None and (
        type(original_vm) is not dict
        or original_vm.get("binding") != expected
        or original_vm.get("generation") != "runner-vm-" + packet["run_id"]
        or original_vm.get("root_identity") != owner["root_identity"]
        or original_vm.get("disk_identity") != state["disk_identity"]
        or original_vm.get("runtime_id") != state["ids"].get("runtime")
        or original_vm.get("network_id") != state["ids"].get("network")
        or original_vm.get("guest_boot_id") != (state["guest_identity"] or {}).get("guest_boot_id")
        or original_vm.get("ssh_host_key_sha256") != recipe["ssh_pki"]["host_public"]["sha256"]
        or original_vm.get("guest_uid") != 10001
        or original_vm.get("guest_gid") != 10001
    ):
        os.close(locked)
        raise ValueError("Runner VM state differs from its original process-bound identity")

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
            raise ValueError("Runner VM operation deadline expired")
        if provisioning and datetime.now(timezone.utc) >= expiry:
            raise ValueError("Runner VM provisioning authorization expired")
        support._private(root)
        support._private(parent)
        if (
            root.stat().st_ino != owner["root_identity"]["inode"]
            or root.stat().st_dev != owner["root_identity"]["device"]
            or parent.stat().st_dev != owner["parent_identity"]["device"]
            or parent.stat().st_ino != owner["parent_identity"]["inode"]
        ):
            raise ValueError("Runner VM generation was replaced")
        if support._json(support._read(root / "owner.json")) != owner:
            raise ValueError("Runner VM ownership changed")
        if state["disk_identity"] is not None and state["purge"] is None:
            info = support._path(data / "probe.qcow2").stat()
            if state["disk_identity"] != {"device": info.st_dev, "inode": info.st_ino}:
                raise ValueError("Runner VM root disk was replaced")
            if support._read(data / "service-link-forward.py") != GUEST_SERVICE_FORWARD.encode():
                raise ValueError("Runner VM original fixed forwarder source changed")

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
            raise ValueError("Runner VM resource inspection failed")
        values = support._json(result.stdout)
        if type(values) is not list or len(values) != 1:
            raise ValueError("Runner VM resource inspection is ambiguous")
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
                    raise ValueError("Runner VM resource ID or ownership differs")
                if role == "network":
                    if item.get("Internal") is not True or item.get("Driver") != "bridge":
                        raise ValueError("Runner VM network isolation differs")
                else:
                    config, host = item.get("Config", {}), item.get("HostConfig", {})
                    wanted = ["sh", "-c", _seed_command(recipe)] if role == "seed" else _qemu(recipe)
                    selected_acceleration = acceleration if role == "runtime" else None
                    device_contract = (accelerator.runtime_matches(host, selected_acceleration)
                                       if accelerator is not None else not host.get("Devices") and not host.get("GroupAdd"))
                    memory = (
                        512 * 1024**2
                        if role == "seed"
                        else (recipe["resources"]["memory_mib"] + 2048) * 1024**2
                    )
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
                        or host.get("NanoCpus")
                        != (10**9 if role == "seed" else recipe["resources"]["cpus"] * 10**9)
                        or host.get("PortBindings")
                        or host.get("RestartPolicy", {}).get("Name") not in ("no", "")
                        or len(mounts) != 1
                        or mounts[0].get("Type") != "bind"
                        or mounts[0].get("Source") != str(data)
                        or mounts[0].get("Destination") != "/lab"
                        or mounts[0].get("RW") is not True
                    ):
                        raise ValueError("Runner VM container confinement differs")
                    if role == "runtime":
                        attached = item.get("NetworkSettings", {}).get("Networks", {})
                        if set(attached) != {names["network"]} or found["network"] is None:
                            raise ValueError("Runner VM runtime network attachment differs")
                        nid = attached[names["network"]].get("NetworkID")
                        created = item.get("State", {}).get("Status") == "created"
                        if nid != found["network"]["Id"] and not (created and nid in ("", None)):
                            raise ValueError("Runner VM runtime network ID changed")
                found[role] = item
            elif matches:
                raise ValueError("Runner VM owned resource inventory is incomplete")
            else:
                found[role] = None
        network, runtime = found["network"], found["runtime"]
        if network is not None:
            members = set(network.get("Containers", {}))
            allowed = {runtime["Id"]} if runtime is not None else set()
            if (
                members - allowed
                or runtime is not None
                and runtime["State"].get("Running")
                and members != allowed
            ):
                raise ValueError("Runner VM network membership differs from its original isolated runtime")
        return found

    def mutate(argv, role, verb, seconds=30):
        if state["inflight"] is not None:
            raise ValueError("An unresolved runner-vm mutation must be reconciled first")
        state["inflight"] = {"operation_id": operation, "role": role, "verb": verb}
        save()
        return command(argv, seconds)

    def acknowledge(role, verb, identifier=None):
        values = observed()
        item = values[role]
        if verb == "create":
            if item is None or item["Id"] != identifier:
                raise ValueError("Runner VM creation acknowledgement not independently observed")
            state["ids"][role] = identifier
        elif verb == "remove" and item is not None:
            raise ValueError("Runner VM resource absence not observed")
        elif verb == "start" and item is None:
            raise ValueError("Runner VM started resource missing")
        state["inflight"] = None
        save()
        return values

    def container(role):
        memory = 512 * 1024**2 if role == "seed" else (recipe["resources"]["memory_mib"] + 2048) * 1024**2
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
            "1" if role == "seed" else str(recipe["resources"]["cpus"]),
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
        args += ["sh", "-c", _seed_command(recipe)] if role == "seed" else _qemu(recipe)
        identifier = mutate(args, role, "create").stdout.decode().strip()
        acknowledge(role, "create", identifier)
        return identifier

    def assets_snapshot(copy=False):
        selected = _asset_pins(recipe)
        destinations = {
            "boot": "ubuntu.img",
            "operator_private": "ssh/operator",
            "operator_public": "ssh/operator.pub",
            "host_private": "ssh/host",
            "host_public": "ssh/host.pub",
            "external_ca": "ssh/external-ca.pem",
        }
        result = {}
        for name, pin in selected.items():
            original = _asset(pin, deadline=deadline, secret=name in ("operator_private", "host_private"))
            result[name] = original
            if copy:
                state["assets"][name] = original
                state["asset_copy_pending"] = name
                save()
                copied = _asset(
                    pin,
                    deadline=deadline,
                    destination=data / destinations[name],
                    secret=name in ("operator_private", "host_private"),
                )
                if copied != original:
                    raise ValueError("Runner supplied source changed between original intent and copy")
                state["asset_copy_pending"] = None
                save()
        expected_assets = packet.get("expected_source_assets")
        if expected_assets is not None and expected_assets != result:
            raise ValueError("Runner sources differ from the original process-bound observations")
        return result

    try:
        resources = observed()
        if action == "up":
            if any(resources.values()) or state["stage"] != "exclusive-generation-created":
                raise ValueError("Runner VM provisioning cannot adopt or repeat an existing effect")
            tool = inspect("image", recipe["tool_image"])
            if tool is None or tool.get("Id") != recipe["tool_image"]:
                raise ValueError("Runner VM cached tool image unavailable; acquisition is separate")
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
            if (
                memory < (recipe["resources"]["memory_mib"] + 4096) * 1024**2
                or shutil.disk_usage(parent).free < 2 * recipe["resources"]["disk_gib"] * 1024**3
            ):
                raise ValueError("Runner VM guest reference memory/disk headroom is unavailable")
            support._private(data, create=True)
            for name in ("ssh",):
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
                packet["environment_id"],
                recipe,
            )
            support._write(data / "user-data", seed)
            support._write(
                data / "meta-data",
                recipes.canonical({"instance-id": packet["run_id"], "local-hostname": "devsecops-runner-vm"}),
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
                raise ValueError("Runner VM disk and seed helper did not finish successfully")
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
                or disk_info.get("virtual-size") != recipe["resources"]["disk_gib"] * 1024**3
                or disk_info.get("backing-filename") != "/lab/ubuntu.img"
            ):
                raise ValueError("Runner VM derived root disk geometry or backing changed")
            packages = dict(
                line.split("\t") for line in support._read(data / "tool-packages.tsv").decode().splitlines()
            )
            required = {
                "qemu-system-x86": "1:8.2.2+ds-0ubuntu1.18",
                "qemu-utils": "1:8.2.2+ds-0ubuntu1.18",
                "cloud-image-utils": "0.33-1",
            }
            if any(packages.get(name) != version for name, version in required.items()):
                raise ValueError("Runner VM tool package inventory differs from the reviewed versions")
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
                "virtual_bytes": recipe["resources"]["disk_gib"] * 1024**3,
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
                raise ValueError("Runner VM API address is not private")
            ssh = _guest_ssh(support, data, address, root, runtime, write_hosts=True)
            boot_end = min(deadline, time.monotonic() + 600)
            while time.monotonic() < boot_end:
                result = command(
                    [*ssh, "test -f /run/devsecops-runner-vm-" + packet["run_id"]], 20, allow_failure=True
                )
                if result.returncode == 0:
                    break
                if result.returncode != 1 and not support._ssh_pending(result):
                    raise ValueError("Runner VM boot observation failed")
                time.sleep(min(3, max(0, boot_end - time.monotonic())))
            else:
                raise ValueError("Runner VM guest did not reach its pinned seed boot marker")
            scope = RunnerVMRemoteContext(packet, recipe, support, root, data, resources, state, command)
            guest = scope.observe_guest()
            state["guest_identity"] = guest
            state["stage"] = "ready"
            state["address"] = address
            state["endpoint"] = scope.endpoint()
            save()
        elif action in ("runner", "routes", "observe", "host-observe"):
            if state["stage"] != "ready" or state["inflight"] is not None or not resources["runtime"]:
                raise ValueError("Runner VM child action requires its original ready generation")
            preserved = assets_snapshot()
            if any(preserved.get(name) != original for name, original in state["assets"].items()):
                raise ValueError("Runner sources changed before original child dispatch")
            scope = RunnerVMRemoteContext(packet, recipe, support, root, data, resources, state, command)
            host_only = action == "host-observe" or (
                action == "routes"
                and packet.get("child", {}).get("action") in ("down-host", "reconcile-host")
            )
            if scope.endpoint() != state["endpoint"]:
                raise ValueError("Runner VM original endpoint changed")
            if not host_only:
                scope.observe_guest()
            if action not in ("observe", "host-observe"):
                handler = runner if action == "runner" else routes
                if handler is None:
                    raise ValueError("Runner VM fixed child handler unavailable")
                child = handler.execute(packet["child"], scope=scope)
                if not host_only:
                    scope.observe_guest()
                return {
                    "binding": expected,
                    "operation_id": operation,
                    "runner_vm": scope.identity(),
                    "child": child,
                    "endpoint": scope.endpoint(),
                }
        elif action == "down":
            tool = inspect("image", recipe["tool_image"])
            if tool is None or tool.get("Id") != recipe["tool_image"]:
                raise ValueError("Supplied runner-vm tool image is no longer preserved")
            preserved = assets_snapshot()
            if any(preserved.get(name) != original for name, original in state["assets"].items()):
                raise ValueError("Supplied runner-vm source assets changed before cleanup")
            if operation in state["history"]:
                raise ValueError("Runner VM cleanup already attempted; reconcile without retry")
            if not pending_resolved(resources):
                raise ValueError("Runner VM mutation outcome remains unresolved; cleanup cannot replace it")
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
                        raise ValueError("Runner VM stop not observed")
                    state["inflight"] = None
                    save()
                mutate(["docker", "rm", item["Id"]], role, "remove")
                resources = acknowledge(role, "remove")
            if resources["network"]:
                if resources["network"].get("Containers"):
                    raise ValueError("Runner VM network still has members")
                mutate(["docker", "network", "rm", resources["network"]["Id"]], "network", "remove")
                resources = acknowledge("network", "remove")
            preserved = assets_snapshot()
            if any(preserved.get(name) != original for name, original in state["assets"].items()):
                raise ValueError("Supplied runner-vm source assets changed")
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
        source_preserved = all(preserved.get(name) == original for name, original in state["assets"].items())
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
            "generation": "runner-vm-" + packet["run_id"],
            "root_identity": owner["root_identity"],
            "up_operation_id": owner["up_operation_id"],
            "state": state["stage"],
            "root_disk_creation": state.get("root_disk_creation"),
            "guest_identity": state["guest_identity"],
            "service_forward_python": state.get("service_forward_python"),
            "endpoint": state["endpoint"],
            "address": state.get("address"),
            "source_assets": preserved,
            "source_assets_preserved": source_preserved,
            "tool_image_preserved": tool_preserved,
            "ssh_pki_basis": "operator-supplied-pins-with-run-created-copies",
            "unresolved_operations": unresolved,
            "complete_cleanup": complete,
            "observation_effect": "not_applied"
            if action in ("reconcile", "observe", "host-observe")
            else "applied",
            "mutation_effect": "applied"
            if not unresolved
            and (complete or data.exists() or state["disk_identity"] is not None or any(resources.values()))
            else "unknown",
            "cleanup_eligible": not unresolved,
            "guest_observation": "not-requeried" if action == "host-observe" else "original",
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
        if state["endpoint"] is not None:
            result["runner_vm"] = state["endpoint"]["runner_vm"]
        if acceleration is not None:
            result["acceleration_admission"] = owner["acceleration_admission"]
        check()
        return result
    finally:
        os.close(locked)
