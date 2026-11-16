"""Controlled boundaries only; no guest, network, or live CI qualification."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import json
import os
from pathlib import Path
import pickle
import ssl
import subprocess
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from devsecops.factory import runner_vm_guest_routes as guest
from devsecops.factory import runner_vm_routes_remote as remote
from devsecops.factory import runner_vm_routes as owner
from devsecops.factory.state import StateError


@pytest.fixture(autouse=True)
def deny_uncontrolled_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError(
            "This controlled test must not open a socket, resolve DNS, or run a network command"
        )

    monkeypatch.setattr(guest.socket, "socket", denied)
    monkeypatch.setattr(guest.socket, "create_connection", denied)
    monkeypatch.setattr(guest.socket, "getaddrinfo", denied)
    monkeypatch.setattr(guest, "_run", denied)
    monkeypatch.setattr(remote, "_docker_inspection", denied)


@pytest.fixture(scope="module")
def ca(tmp_path_factory):
    path = tmp_path_factory.mktemp("route-ca")
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(path / "key"),
            "-out",
            str(path / "ca.pem"),
            "-days",
            "1",
            "-subj",
            "/CN=Controlled Route Test",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=15,
    )
    return (path / "ca.pem").read_bytes()


@pytest.fixture
def packet(ca):
    run = str(uuid4())
    return {
        "schema_version": 1,
        "action": "up",
        "run_id": run,
        "operation_id": str(uuid4()),
        "binding": "a" * 64,
        "runner_vm": {
            "binding": "b" * 64,
            "generation": "runner-vm-" + run,
            "guest_boot_id": str(uuid4()),
            "guest_uid": 10001,
            "guest_gid": 10001,
            "root_identity": {"device": 1, "inode": 2},
            "disk_identity": {"device": 1, "inode": 3},
            "runtime_id": "c" * 64,
            "network_id": "d" * 64,
            "ssh_host_key_sha256": "e" * 64,
        },
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "timeout": 20,
        "services": {
            role: {
                "origin": f"https://{role}.controlled.test:{port}",
                "address": address,
                "port": port,
                "ca_b64": base64.b64encode(ca).decode(),
                "ca_sha256": guest._sha(ca),
            }
            for role, address, port in (("gitlab", "10.0.2.100", 8444), ("registry", "10.0.2.101", 8443))
        },
    }


@pytest.fixture
def guest_scene(packet, tmp_path, monkeypatch):
    monkeypatch.setattr(guest, "UID", os.getuid())
    monkeypatch.setattr(guest, "GID", os.getgid())
    packet["runner_vm"]["guest_uid"] = os.getuid()
    packet["runner_vm"]["guest_gid"] = os.getgid()
    monkeypatch.setattr(guest, "ROOT", tmp_path)
    boot = tmp_path / "boot"
    boot.write_text(packet["runner_vm"]["guest_boot_id"])
    monkeypatch.setattr(guest, "BOOT_ID", boot)
    root = tmp_path / UUID(packet["run_id"]).hex
    root.mkdir(mode=0o700)
    # macOS /private/tmp may inherit group 0; model the seeded guest owner,
    # whose private directory group must equal its effective process group.
    os.chown(root, -1, os.getegid())
    (root / "inputs").mkdir(mode=0o700)
    calls = []
    monkeypatch.setattr(
        guest,
        "_tls_probe",
        lambda row, role, deadline: calls.append(role) or {"host": row["origin"], "verified": True},
    )
    return packet, root, calls


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(callback="arbitrary"),
        lambda p: p["services"]["registry"].update(origin="https://registry.other.test:8443"),
        lambda p: p["services"]["gitlab"].update(origin="https://gitlab.controlled.test:8444/"),
        lambda p: p["services"]["gitlab"].update(address="10.0.2.200"),
        lambda p: p["services"]["registry"].update(ca_sha256="f" * 64),
        lambda p: p["services"]["registry"].update(port=True),
        lambda p: p.update(timeout=float("nan")),
        lambda p: p["runner_vm"].update(guest_uid=0),
        lambda p: p["runner_vm"].update(generation="other"),
        lambda p: p.update(expires_at="2001-01-01T00:00:00+00:00"),
    ],
)
def test_closed_guest_selection_rejects_changed_scope(packet, change):
    change(packet)
    with pytest.raises((ValueError, TypeError)):
        guest.validate_packet(packet)


def test_private_trust_round_trip_and_expired_read_no_network(guest_scene):
    packet, root, calls = guest_scene
    up = guest.execute(packet)
    assert up["ready"] and not up["complete_cleanup"] and calls == ["gitlab", "registry"]
    trust = Path(up["trust_directory"])
    assert up["environment"]["SSL_CERT_DIR"] == str(trust / "empty-ca-directory")
    assert all(v == "" for k, v in up["environment"].items() if "proxy" in k.lower())
    for name, row in up["files"].items():
        assert (trust / name).stat().st_ino == row["inode"] and row["mode"] == 0o600
    packet.update(action="observe", expires_at="2001-01-01T00:00:00+00:00")
    read = guest.execute(packet)
    assert not read["ready"] and read["mutation_effect"] == "not_applied" and len(calls) == 2
    packet.update(action="down", operation_id=str(uuid4()))
    down = guest.execute(packet)
    assert down["complete_cleanup"] and down["unresolved"] == [] and not trust.exists()
    assert (root / "inputs").is_dir() and (root / "route-state.json").is_file()


def test_external_alias_and_closed_bundle(packet, ca):
    for role in ("registry1", "p1docs"):
        address, port = guest.ROLE[role]
        packet["services"][role] = {
            "origin": guest.EXTERNAL[role],
            "address": address,
            "port": port,
            "ca_b64": base64.b64encode(ca).decode(),
            "ca_sha256": guest._sha(ca),
        }
    files = guest.planned_files(guest.validate_packet(packet))
    assert files["external-ca.pem"] == files["registry1-ca.pem"] == files["p1docs-ca.pem"] == ca
    assert files["ca-bundle.pem"] == ca


def test_guest_duplicate_up_preserves_original(guest_scene):
    packet, root, _ = guest_scene
    guest.execute(packet)
    before = (root / "route-state.json").read_bytes()
    with pytest.raises(FileExistsError):
        guest.execute(packet)
    assert (root / "route-state.json").read_bytes() == before


@pytest.mark.parametrize("tamper", ["inode", "symlink", "foreign", "boot"])
def test_guest_tamper_refuses_removal(guest_scene, tamper):
    packet, root, _ = guest_scene
    up = guest.execute(packet)
    trust = Path(up["trust_directory"])
    path = trust / "gitlab-ca.pem"
    if tamper == "inode":
        raw = path.read_bytes()
        replacement = trust / "replacement"
        replacement.write_bytes(raw)
        replacement.chmod(0o600)
        os.replace(replacement, path)
    elif tamper == "symlink":
        path.unlink()
        path.symlink_to(trust / "registry-ca.pem")
    elif tamper == "foreign":
        (trust / "foreign").write_text("preserve")
    else:
        guest.BOOT_ID.write_text(str(uuid4()))
    packet.update(action="down")
    with pytest.raises(ValueError):
        guest.execute(packet)
    assert trust.exists()


def test_interrupted_guest_write_stays_unknown_without_adoption(guest_scene, monkeypatch):
    packet, root, _ = guest_scene
    real = guest._exclusive

    def lost(path, raw):
        result = real(path, raw)
        if path.name == "gitlab-ca.pem":
            raise OSError("controlled lost acknowledgement")
        return result

    monkeypatch.setattr(guest, "_exclusive", lost)
    with pytest.raises(OSError):
        guest.execute(packet)
    monkeypatch.setattr(guest, "_exclusive", real)
    packet["action"] = "reconcile"
    observed = guest.execute(packet)
    assert "gitlab-ca.pem" in observed["unresolved"] and not observed["ready"]
    packet["action"] = "down"
    with pytest.raises(ValueError, match="Uncertain"):
        guest.execute(packet)
    assert (root / "inputs/service-trust/gitlab-ca.pem").is_file()


def client_hello(host):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    obj = context.wrap_bio(incoming, outgoing, server_side=False, server_hostname=host)
    with pytest.raises(ssl.SSLWantReadError):
        obj.do_handshake()
    return outgoing.read()


def test_real_tls_clienthello_requires_exact_sni():
    raw = client_hello("registry1.dso.mil")
    assert remote.client_hello_origin(raw) == "registry1.dso.mil"

    class Client:
        def __init__(self):
            self.data = raw

        def settimeout(self, value):
            assert 0 < value <= 5

        def recv(self, count):
            part = self.data[:count]
            self.data = self.data[count:]
            return part

    assert (
        remote.receive_client_hello(
            Client(), expected_host="registry1.dso.mil", deadline=time.monotonic() + 5
        )
        == raw
    )
    with pytest.raises(ValueError):
        remote.receive_client_hello(Client(), expected_host="p1docs.dso.mil", deadline=time.monotonic() + 5)


@pytest.mark.parametrize(
    "raw",
    [b"CONNECT registry1.dso.mil:443 HTTP/1.1\r\n", b"", b"\x16\x03\x03\xff\xff", b"\x17\x03\x03\x00\x01x"],
)
def test_non_tls_and_malformed_clienthello_refused(raw):
    with pytest.raises(ValueError):
        remote.client_hello_origin(raw)


def test_external_dns_only_exact_global_ipv4(monkeypatch):
    calls = []
    monkeypatch.setattr(
        remote, "_docker_inspection", lambda argv: calls.append(argv) or b"8.8.8.8 STREAM registry1.dso.mil\n"
    )
    assert remote.external_addresses("registry1") == ["8.8.8.8"]
    assert calls == [["getent", "ahostsv4", "registry1.dso.mil"]]
    monkeypatch.setattr(remote, "_docker_inspection", lambda argv: b"127.0.0.1 STREAM registry1.dso.mil\n")
    with pytest.raises(ValueError):
        remote.external_addresses("registry1")
    with pytest.raises(ValueError):
        remote.external_addresses("other")


@pytest.fixture
def remote_scene(packet, tmp_path, monkeypatch):
    root = tmp_path / "owned-vm"
    root.mkdir(mode=0o700)
    data = root / "data"
    data.mkdir(mode=0o700)
    disk = data / "probe.qcow2"
    remote.write(disk, b"controlled VM disk")
    remote.write(data / "service-link-forward.py", b"fixed original forwarder")
    packet["runner_vm"]["root_identity"] = {"device": root.stat().st_dev, "inode": root.stat().st_ino}
    packet["runner_vm"]["disk_identity"] = {"device": disk.stat().st_dev, "inode": disk.stat().st_ino}
    remote.write(
        root / "owner.json",
        remote.canonical({"binding": packet["runner_vm"]["binding"], "run_id": packet["run_id"]}),
    )
    resources = {}

    def resources_for(name, runtime_id, network_id, subnet):
        network = {
            "Id": network_id,
            "Driver": "bridge",
            "Labels": {"owner": name},
            "IPAM": {"Config": [{"Subnet": subnet + ".0/24", "Gateway": subnet + ".1"}]},
        }
        runtime = {
            "Id": runtime_id,
            "State": {"Running": True},
            "Config": {"Labels": {"owner": name}},
            "NetworkSettings": {"Networks": {name: {"NetworkID": network_id, "IPAddress": subnet + ".2"}}},
        }
        resources[runtime_id] = runtime
        resources[network_id] = network
        return runtime, network

    runtime, network = resources_for("vm", "c" * 64, "d" * 64, "172.20.0")
    targets = {}
    for index, role in enumerate(("gitlab", "registry")):
        rt, nt = resources_for(role, str(index + 1) * 64, str(index + 3) * 64, "172.21." + str(index))
        targets[role] = {
            "origin": packet["services"][role]["origin"],
            "port": packet["services"][role]["port"],
            "container": {"kind": "container", "id": rt["Id"], "labels": rt["Config"]["Labels"]},
            "network": {"kind": "network", "id": nt["Id"], "labels": nt["Labels"]},
        }

    class Scope:
        def identity(self):
            return deepcopy(packet["runner_vm"])

        def endpoint(self):
            return {"runner_vm": self.identity()}

        def observe_guest(self):
            self.guest_reads += 1
            return {"guest_boot_id": packet["runner_vm"]["guest_boot_id"]}

    scope = Scope()
    scope.root = root
    scope.data = data
    scope.packet = packet
    scope.guest_reads = 0
    scope.resources = {"runtime": runtime, "network": network}
    scope.recipe = {"egress": {"external_origins": [], "external_ca_bundle": None}}
    monkeypatch.setattr(remote, "CONTEXT_TYPE", Scope)
    monkeypatch.setattr(remote, "GUEST_SOURCE_BYTES", Path(guest.__file__).read_bytes())
    monkeypatch.setattr(remote, "SOURCE_BYTES", Path(remote.__file__).read_bytes(), raising=False)
    monkeypatch.setattr(remote, "inspect", lambda target: deepcopy(resources[target["id"]]))
    child = {
        "action": "up",
        "guest": packet,
        "targets": targets,
        "guest_source_sha256": remote.sha(remote.GUEST_SOURCE_BYTES),
    }
    process = {"alive": False, "identity": None}
    guest_calls = []

    class Popen:
        def __init__(self, argv, **kwargs):
            assert kwargs["start_new_session"] and kwargs["env"] == {
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
            }
            self.pid = 987654
            config = json.loads(remote.read(root / "runner-routes/config.json"))
            process["identity"] = {
                "pid": self.pid,
                "process_group": self.pid,
                "session": self.pid,
                "start_time": 123,
                "command_sha256": remote.sha(b"\0".join(x.encode() for x in argv) + b"\0"),
            }
            process["alive"] = True
            remote.write(
                root / "runner-routes/daemon.json",
                remote.canonical(
                    {
                        "binding": packet["binding"],
                        "runner_vm": scope.identity(),
                        "process": process["identity"],
                        "listeners": {
                            role: {"host": "172.20.0.1", "port": 30000 + i}
                            for i, role in enumerate(packet["services"])
                        },
                        "config_sha256": remote.sha(remote.read(root / "runner-routes/config.json")),
                        "script_sha256": config["script_sha256"],
                    }
                ),
            )

        def poll(self):
            return None if process["alive"] else 0

    monkeypatch.setattr(remote.subprocess, "Popen", Popen)
    monkeypatch.setattr(
        remote, "process_identity", lambda pid: deepcopy(process["identity"]) if process["alive"] else None
    )
    monkeypatch.setattr(remote, "process_group_absent", lambda original: not process["alive"])
    monkeypatch.setattr(remote.os, "killpg", lambda pid, sig: process.update(alive=False))

    def guest_action(scope, p):
        guest_calls.append(p["action"])
        return {
            "ready": p["action"] != "down",
            "complete_cleanup": p["action"] == "down",
            "unresolved": [],
            "runner_vm": scope.identity(),
        }

    monkeypatch.setattr(remote, "_guest_action", guest_action)
    return child, scope, process, guest_calls


def action(scene, name):
    child, scope, _, _ = scene
    child["action"] = name
    child["guest"]["action"] = {"reconcile-host": "reconcile", "down-host": "down"}.get(name, name)
    return remote.execute(child, scope=scope)


def test_real_remote_coordinator_normal_cleanup(remote_scene):
    up = action(remote_scene, "up")
    assert up["ready"] and up["unresolved"] == []
    read = action(remote_scene, "observe")
    assert read["ready"] and read["mutation_effect"] == "not_applied"
    down = action(remote_scene, "down")
    assert (
        down["complete_cleanup"]
        and down["host_cleanup"]
        and down["guest_cleanup"]
        and down["unresolved"] == []
    )
    assert remote_scene[3] == ["up", "observe", "down"]
    assert not (remote_scene[1].data / "service-links.json").exists()


def test_remote_lost_guest_reply_host_reconcile_and_explicit_host_cleanup(remote_scene, monkeypatch):
    real = remote._guest_action

    def lost(scope, p):
        real(scope, p)
        raise OSError("controlled guest reply loss")

    monkeypatch.setattr(remote, "_guest_action", lost)
    with pytest.raises(OSError):
        action(remote_scene, "up")
    old_reads = remote_scene[1].guest_reads
    report = action(remote_scene, "reconcile-host")
    assert report["host_disposal_eligible"] and report["host_unresolved"] == [] and report["guest_unresolved"]
    assert (
        not report["ready"] and not report["complete_cleanup"] and report["mutation_effect"] == "not_applied"
    )
    assert remote_scene[1].guest_reads == old_reads
    down = action(remote_scene, "down-host")
    assert down["host_cleanup"] and not down["guest_cleanup"] and not down["complete_cleanup"]
    assert remote_scene[1].guest_reads == old_reads


def test_unknown_host_write_never_becomes_guest_contained(remote_scene, monkeypatch):
    real = remote.write

    def lost(path, value):
        real(path, value)
        if path.name == "service-links.json":
            raise OSError("controlled mapping reply loss")

    monkeypatch.setattr(remote, "write", lost)
    with pytest.raises(OSError):
        action(remote_scene, "up")
    report = action(remote_scene, "reconcile-host")
    assert not report["host_disposal_eligible"] and report["host_unresolved"]
    with pytest.raises(ValueError, match="host write"):
        action(remote_scene, "down-host")
    assert remote_scene[2]["alive"]


def test_changed_original_forwarder_preserves_resources(remote_scene):
    action(remote_scene, "up")
    (remote_scene[1].data / "service-link-forward.py").write_bytes(b"changed")
    with pytest.raises(ValueError, match="forwarder source"):
        action(remote_scene, "down")
    assert remote_scene[2]["alive"] and (remote_scene[1].data / "service-links.json").exists()


def test_relay_pid_reuse_never_signalled(remote_scene, monkeypatch):
    action(remote_scene, "up")
    original = deepcopy(remote_scene[2]["identity"])
    original["start_time"] += 1
    monkeypatch.setattr(remote, "process_identity", lambda pid: original)
    with pytest.raises(ValueError, match="PID was reused"):
        action(remote_scene, "down-host")
    assert remote_scene[2]["alive"]


@pytest.fixture
def resident_scene(packet, ca, tmp_path, monkeypatch):
    journal = SimpleNamespace(
        run_dir=tmp_path,
        state={
            "run_id": packet["run_id"],
            "active_mutation": {"operation_id": packet["operation_id"], "action": "up"},
        },
        identities={"package_sha256": "f" * 64},
    )
    driver = object.__new__(owner.OwnedLinuxDriver)
    driver.journal = journal
    vm = object.__new__(owner.OwnedRunnerVM)
    vm.journal = journal
    vm._selected = {"egress": {"external_origins": [], "external_ca_bundle": None}}
    vm._children = {"runner": None, "routes": None}
    vm._child_attempts = {"runner": False, "routes": False}
    registry = object.__new__(owner.OwnedOCIRegistry)
    registry.journal = journal
    services = object.__new__(owner.OwnedServiceStack)
    services.journal = journal
    ca_path = tmp_path / "selected-ca.pem"
    ca_path.write_bytes(ca)
    modules = {
        name: owner._sha((owner.PACKAGE_ROOT / f"src/devsecops/factory/{name}.py").read_bytes())
        for name in ("runner_vm_routes", "runner_vm_routes_remote", "runner_vm_guest_routes")
    }
    calls = []
    dispatches = []
    mode = {"unknown_guest": False}

    def admit(self, context, *, action=None, reconciliation_operation=None):
        op = journal.state["active_mutation"]["operation_id"]
        if reconciliation_operation is not None and reconciliation_operation != op:
            raise StateError("different old operation")
        return op, vm._selected, modules, "b" * 64

    monkeypatch.setattr(owner.OwnedRunnerVM, "_admit", admit)
    endpoint = {"runner_vm": packet["runner_vm"], "external_ca": None}
    monkeypatch.setattr(owner.OwnedRunnerVM, "endpoint", lambda *args, **kwargs: deepcopy(endpoint))
    monkeypatch.setattr(
        owner.OwnedRunnerVM, "host_endpoint", lambda *args, **kwargs: deepcopy(endpoint), raising=False
    )

    def target(role):
        return {
            "run_id": packet["run_id"],
            "identities": journal.identities,
            "binding": role + "-original",
            "ca_path": str(ca_path),
            "ca_sha256": guest._sha(ca),
            "origin": packet["services"][role]["origin"],
            "port": packet["services"][role]["port"],
            "network": {"id": role + "network"},
            ("server" if role == "gitlab" else "registry"): {"id": role + "container"},
        }

    monkeypatch.setattr(owner.OwnedServiceStack, "service_endpoint", lambda *args, **kwargs: target("gitlab"))
    monkeypatch.setattr(
        owner.OwnedOCIRegistry, "service_endpoint", lambda *args, **kwargs: target("registry")
    )

    def dispatch(self, context, *, action, packet, reconciliation_operation=None):
        calls.append(action)
        if mode.get("raise"):
            raise OSError("controlled original child reply loss")
        trust = f"/home/gitlab-runner/.local/share/devsecops/{UUID(packet['guest']['run_id']).hex}/inputs/service-trust"
        observed_guest = {
            "trust_directory": trust,
            "files": {
                "ca-bundle.pem": {
                    "sha256": guest._sha(ca),
                    "device": 1,
                    "inode": 2,
                    "mode": 0o600,
                    "uid": 10001,
                    "gid": 10001,
                    "bytes": len(ca),
                }
            },
            "empty_identity": {"device": 1, "inode": 3, "mode": 0o700, "uid": 10001, "gid": 10001},
            "environment": {"SSL_CERT_DIR": trust + "/empty-ca-directory"},
            "verification": {"controlled": True},
        }
        guest_unknown = ["guest-local-state-unobserved"] if mode["unknown_guest"] else []
        public = {
            "binding": packet["guest"]["binding"],
            "runner_vm": packet["guest"]["runner_vm"],
            "operation_id": packet["guest"]["operation_id"],
            "unresolved": guest_unknown,
            "guest_unresolved": guest_unknown,
            "host_unresolved": [],
            "ready": action in {"up", "observe"} and not guest_unknown,
            "guest_cleanup": action == "down",
            "host_cleanup": action in {"down", "down-host"},
            "complete_cleanup": action == "down",
            "host_disposal_eligible": action == "reconcile-host",
            "guest": observed_guest,
            "relay": {"original": True},
            "mutation_effect": "not_applied"
            if action in {"observe", "reconcile", "reconcile-host"}
            else "applied",
        }
        artifact = tmp_path / ("transport-" + uuid4().hex + ".json")
        artifact.write_bytes(guest._bytes(public))
        result = {
            "observation": public,
            "runner_vm": packet["guest"]["runner_vm"],
            "transport_receipt": {
                "artifact": str(artifact),
                "sha256": owner._sha(artifact.read_bytes()),
                "operation_id": packet["guest"]["operation_id"],
                "action": action,
            },
        }
        dispatches.append(result)
        return result

    def require(self, original, *args, **kwargs):
        if not any(original is d for d in dispatches):
            raise StateError("not original dispatch")
        path = Path(original["transport_receipt"]["artifact"])
        if owner._sha(path.read_bytes()) != original["transport_receipt"]["sha256"]:
            raise StateError("changed original transport")
        return original

    monkeypatch.setattr(owner.OwnedRunnerVM, "dispatch_routes", dispatch)
    monkeypatch.setattr(owner.OwnedRunnerVM, "require_dispatch", require)
    component = owner.OwnedRunnerVMRoutes(driver, vm=vm, registry=registry, services=services)
    context = SimpleNamespace(
        run_id=packet["run_id"],
        identities=journal.identities,
        inputs={"config": {"expires_at": packet["expires_at"]}},
        deadline=time.monotonic() + 60,
    )
    return component, context, journal, calls, mode, endpoint


def test_original_resident_origin_and_trust_shape(resident_scene):
    component, context, _, calls, _, _ = resident_scene
    up = component.up(context)
    origin = component.origin(context)
    facts = owner.require_runner_vm_routes(origin, vm=component.vm, context=context)
    assert calls == ["up", "observe"] and facts["external_origins"] == ()
    assert set(facts["route_trust"]) == {"files", "environment", "empty_directory"}
    assert facts["route_trust"]["empty_directory"]["path"].endswith("/empty-ca-directory")
    with pytest.raises(TypeError):
        pickle.dumps(origin)
    with pytest.raises(TypeError):
        owner.RunnerVMRouteOrigin()
    with pytest.raises(StateError):
        owner.require_runner_vm_routes(dict(facts), vm=component.vm, context=context)
    with pytest.raises(StateError):
        component.cleanup_observation(deepcopy(up))
    component.origin(context)
    with pytest.raises(StateError, match="superseded"):
        owner.require_runner_vm_routes(origin, vm=component.vm, context=context)


def test_historical_original_cleanup_audit_checks_bytes_without_old_operation(resident_scene):
    component, context, journal, _, _, _ = resident_scene
    up = component.up(context)
    journal.state["active_mutation"] = {"operation_id": str(uuid4()), "action": "down"}
    report = component.cleanup_observation(up)
    assert report["action"] == "up"
    up.artifacts[-1].write_bytes(b"changed")
    with pytest.raises(StateError, match="evidence changed"):
        component.cleanup_observation(up)


@pytest.mark.parametrize("reconciled", [False, True])
def test_explicit_resident_disposal_preserves_guest_unknown_until_kernel_proof(
    resident_scene, monkeypatch, reconciled
):
    component, context, journal, calls, mode, _ = resident_scene
    component.up(context)
    old = str(uuid4())
    journal.state["active_mutation"] = {"operation_id": old, "action": "down"}
    component._unknown = True
    mode["unknown_guest"] = True
    basis = component.prepare_disposal(context, old)
    report = component.cleanup_observation(basis)
    assert (
        report["host_disposal_eligible"]
        and report["guest_unresolved"]
        and basis.mutation_effect == "not_applied"
    )
    with pytest.raises(StateError):
        component.down_host(context)
    fresh = str(uuid4())
    journal.state["active_mutation"] = {
        "operation_id": fresh,
        "action": "down",
        "cleanup_mode": "dispose-owned-runner-guest",
        "continues_operation": old,
    }
    partial = component.prepare_host_disposal(context, old)
    assert component.host_cleanup_ready(context) and not component.vm_cleanup_ready(context)
    assert component.cleanup_observation(partial)["guest_unresolved"]
    proof = object()

    def require(value, *, vm, context, reconciliation_operation=None):
        if value is not proof or vm is not component.vm:
            raise StateError("typed original proof required")
        if reconciliation_operation != (fresh if reconciled else None):
            raise StateError("Original cessation observation mode differs")
        return {
            "runner_vm": component._runner_vm,
            "operation_id": fresh,
            "guest_kernel": "ceased",
            "scope": "guest-process-and-private-disk-only",
            "ci_effects": "not-reconciled",
        }

    monkeypatch.setattr(owner, "require_guest_kernel_cessation", require)
    proof_artifact = journal.run_dir / "original-kernel-cessation.json"
    proof_artifact.write_bytes(b"controlled original kernel cessation boundary")
    monkeypatch.setattr(
        owner.OwnedRunnerVM,
        "guest_cessation_artifacts",
        lambda self, value: (proof_artifact,) if value is proof else (),
        raising=False,
    )
    with pytest.raises(StateError):
        component.accept_guest_cessation({}, context)
    final = component.accept_guest_cessation(
        proof, context, **({"reconciliation_operation": fresh} if reconciled else {})
    )
    assert component.vm_cleanup_ready(context)
    final_report = component.cleanup_observation(final)
    assert final_report["unresolved_operations"] == [] and all(
        row["state"] == "absent" for row in final_report["resources"]
    )
    assert final_report["guest_cessation_basis"]["ci_effects"] == "not-reconciled"
    assert final_report["mutation_effect"] == "applied"
    assert final_report["observation_effect"] == ("not_applied" if reconciled else "applied")
    assert calls == ["up", "reconcile-host", "down-host"]


@pytest.mark.parametrize("change", ["mode", "prior", "same-operation", "action"])
def test_disposal_continuation_requires_exact_original_basis(resident_scene, change):
    component, context, journal, calls, mode, _ = resident_scene
    component.up(context)
    old = str(uuid4())
    journal.state["active_mutation"] = {"operation_id": old, "action": "down"}
    component._unknown = True
    mode["unknown_guest"] = True
    component.prepare_disposal(context, old)
    current = {
        "operation_id": str(uuid4()),
        "action": "down",
        "cleanup_mode": "dispose-owned-runner-guest",
        "continues_operation": old,
    }
    if change == "mode":
        current["cleanup_mode"] = "continue"
    elif change == "prior":
        current["continues_operation"] = str(uuid4())
    elif change == "same-operation":
        current["operation_id"] = old
    else:
        current["action"] = "up"
    journal.state["active_mutation"] = current
    with pytest.raises(StateError):
        component.prepare_host_disposal(context, old)
    assert calls == ["up", "reconcile-host"]


def test_remote_external_ca_observation_never_reads_selected_remote_path(resident_scene, ca):
    component, context, _, _, _, endpoint = resident_scene
    component.vm._selected["egress"] = {
        "external_origins": ["https://registry1.dso.mil"],
        "external_ca_bundle": {"path": "/remote/only/explicit-ca.pem", "sha256": guest._sha(ca)},
    }
    endpoint["external_ca"] = {
        "sha256": guest._sha(ca),
        "bytes": len(ca),
        "ca_b64": base64.b64encode(ca).decode(),
    }
    packet = component._packet(context, "up")
    assert packet["guest"]["services"]["registry1"]["ca_sha256"] == guest._sha(ca)
    endpoint["external_ca"]["bytes"] += 1
    with pytest.raises(StateError, match="CA selection"):
        component._packet(context, "up")


def test_failed_or_incomplete_refresh_invalidates_old_readiness(resident_scene):
    component, context, _, _, mode, _ = resident_scene
    component.up(context)
    origin = component.origin(context)
    mode["unknown_guest"] = True
    with pytest.raises(StateError, match="readiness is unavailable"):
        component.origin(context)
    with pytest.raises(StateError, match="superseded"):
        owner.require_runner_vm_routes(origin, vm=component.vm, context=context)


def test_expired_original_origin_cannot_grant_readiness(resident_scene):
    component, context, _, _, _, _ = resident_scene
    component.up(context)
    origin = component.origin(context)
    context.inputs["config"]["expires_at"] = "2001-01-01T00:00:00+00:00"
    with pytest.raises(StateError, match="expired"):
        owner.require_runner_vm_routes(origin, vm=component.vm, context=context)


def test_historical_component_observation_pins_original_transport_bytes(resident_scene):
    component, context, _, _, _, _ = resident_scene
    outcome = component.up(context)
    report = component.cleanup_observation(outcome)
    Path(report["transport_receipt"]["artifact"]).write_bytes(b"changed transport")
    with pytest.raises(StateError, match="evidence changed"):
        component.cleanup_observation(outcome)


@pytest.mark.parametrize("changed", ["time", "operation"])
def test_ready_origin_requires_same_current_operation_and_fresh_read(resident_scene, changed):
    component, context, journal, _, _, _ = resident_scene
    component.up(context)
    origin = component.origin(context)
    if changed == "time":
        component._ready_at -= 31
    else:
        journal.state["active_mutation"] = {"operation_id": str(uuid4()), "action": "build-promote"}
    with pytest.raises(StateError, match="another or expired observation"):
        owner.require_runner_vm_routes(origin, vm=component.vm, context=context)


def test_tls_probe_eof_is_bounded_and_uses_exact_explicit_ca(packet, monkeypatch):
    row = packet["services"]["registry"]
    calls = []

    class TLS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, value):
            assert 0 < value <= 5

        def connect(self, destination):
            calls.append(("destination", destination))

        def do_handshake(self):
            calls.append(("handshake", True))

        def sendall(self, raw):
            calls.append(("request", raw))

        def recv(self, count):
            return b""

    class Context:
        def __init__(self, protocol):
            assert protocol == ssl.PROTOCOL_TLS_CLIENT

        def load_verify_locations(self, **kw):
            calls.append(("ca", kw))

        def wrap_socket(self, raw, *, server_hostname, do_handshake_on_connect):
            assert do_handshake_on_connect is False
            calls.append(("sni", server_hostname))
            return TLS()

    monkeypatch.setattr(guest.ssl, "SSLContext", Context)
    monkeypatch.setattr(guest, "_run", lambda argv, **kw: b"10.0.2.101 STREAM registry.controlled.test\n")
    monkeypatch.setattr(guest.socket, "socket", lambda *args, **kw: TLS())
    with pytest.raises(
        ValueError,
        match="readiness failed",
    ):
        guest._tls_probe(row, "registry", time.monotonic() + 5)
    assert ("destination", ("10.0.2.101", 8443)) in calls
    assert ("sni", "registry.controlled.test") in calls
    assert next(value for kind, value in calls if kind == "ca") == {
        "cadata": base64.b64decode(row["ca_b64"]).decode("ascii")
    }


def test_no_endpoint_hostname_fallback(packet, monkeypatch):
    monkeypatch.setattr(guest, "_run", lambda *args, **kw: b"10.0.2.102 STREAM registry.controlled.test\n")
    monkeypatch.setattr(
        guest.socket, "socket", lambda *args, **kw: pytest.fail("must not open changed destination")
    )
    with pytest.raises(ValueError, match="Seeded hostname"):
        guest._tls_probe(packet["services"]["registry"], "registry", time.monotonic() + 5)


def test_slow_validation_cannot_extend_absolute_route_budget(remote_scene, monkeypatch):
    clock = [1000.0]
    real = remote._validate
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])

    def delayed(packet, scope):
        result = real(packet, scope)
        clock[0] += 21
        return result

    monkeypatch.setattr(remote, "_validate", delayed)
    with pytest.raises(ValueError, match="deadline"):
        action(remote_scene, "up")
    assert not (remote_scene[1].root / "runner-routes-intent.json").exists()
    assert not remote_scene[2]["alive"]


def test_delayed_original_audit_refuses_relay_spawn_preserves_intent(remote_scene, monkeypatch):
    clock = [1000.0]
    real = remote._root_guard
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])

    def delayed(config):
        result = real(config)
        state = remote_scene[1].root / "runner-routes/state.json"
        if state.exists() and json.loads(state.read_bytes())["pending"] == "start-relay":
            clock[0] += 21
        return result

    monkeypatch.setattr(remote, "_root_guard", delayed)
    with pytest.raises(ValueError, match="deadline"):
        action(remote_scene, "up")
    assert not remote_scene[2]["alive"] and not remote_scene[3]
    assert (
        json.loads((remote_scene[1].root / "runner-routes/state.json").read_bytes())["pending"]
        == "start-relay"
    )


def test_stop_budget_never_dispatches_late_sigkill(remote_scene, monkeypatch):
    action(remote_scene, "up")
    clock = [1000.0]
    signals = []
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])

    def signal(pid, sig):
        signals.append(sig)
        clock[0] += 21

    monkeypatch.setattr(remote.os, "killpg", signal)
    with pytest.raises(ValueError, match="deadline"):
        action(remote_scene, "down")
    assert signals == [remote.signal.SIGTERM]
    assert remote_scene[2]["alive"] and (remote_scene[1].data / "service-links.json").exists()
    assert (
        json.loads((remote_scene[1].root / "runner-routes/state.json").read_bytes())["pending"]
        == "stop-relay"
    )


def test_mapping_removal_rechecks_budget_after_durable_intent(remote_scene, monkeypatch):
    action(remote_scene, "up")
    clock = [1000.0]
    real = remote._replace_state
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])

    def delayed(path, value, previous):
        real(path, value, previous)
        if value["pending"] == "remove-forwarder":
            clock[0] += 21

    monkeypatch.setattr(remote, "_replace_state", delayed)
    with pytest.raises(ValueError, match="deadline"):
        action(remote_scene, "down")
    assert not remote_scene[2]["alive"] and (remote_scene[1].data / "service-links.json").exists()
    assert (
        json.loads((remote_scene[1].root / "runner-routes/state.json").read_bytes())["pending"]
        == "remove-forwarder"
    )


def test_guest_payload_creation_rechecks_after_slow_journal(guest_scene, monkeypatch):
    packet, root, _ = guest_scene
    clock = [1000.0]
    real = guest.os.replace
    monkeypatch.setattr(guest.time, "monotonic", lambda: clock[0])

    def delayed(source, target):
        real(source, target)
        if (
            Path(target).name == "route-state.json"
            and json.loads(Path(target).read_bytes())["pending"] == "gitlab-ca.pem"
        ):
            clock[0] += 21

    monkeypatch.setattr(guest.os, "replace", delayed)
    with pytest.raises(ValueError, match="deadline"):
        guest.execute(packet)
    assert not (root / "inputs/service-trust/gitlab-ca.pem").exists()
    assert json.loads((root / "route-state.json").read_bytes())["pending"] == "gitlab-ca.pem"


def test_historical_evidence_export_after_cleanup_needs_no_live_admission(resident_scene, monkeypatch):
    component, context, _, _, _, _ = resident_scene
    up = component.up(context)
    monkeypatch.setattr(
        component,
        "_admit",
        lambda *args, **kwargs: pytest.fail("historical export must not admit new action"),
    )
    paths = component.evidence_artifacts(context)
    assert set(up.artifacts) <= set(paths)
    up.artifacts[1].write_bytes(b"changed transport")
    with pytest.raises(StateError, match="evidence changed"):
        component.evidence_artifacts(context)


def test_unattempted_route_cleanup_uses_original_resident_ledger_without_guest_endpoint(
    resident_scene, monkeypatch
):
    component, context, journal, calls, _, _ = resident_scene
    journal.state["active_mutation"] = {"operation_id": str(uuid4()), "action": "down"}
    monkeypatch.setattr(
        owner.OwnedRunnerVM,
        "endpoint",
        lambda *args, **kwargs: pytest.fail("unattempted route must not inspect guest"),
    )
    result = component.down(context)
    report = component.cleanup_observation(result)
    assert report["never_dispatched"] and report["complete_cleanup"] and not calls
    assert result.mutation_effect == "not_applied" and component.vm_cleanup_ready(context)


def test_daemon_cache_is_local_bounded_and_new_connection_always_reaudits(remote_scene, monkeypatch):
    action(remote_scene, "up")
    root = remote_scene[1].root / "runner-routes"
    clock = [1000.0]
    calls = []
    real = remote._root_guard
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(remote, "_root_guard", lambda config: calls.append(clock[0]) or real(config))
    guard = remote._RelayGuard(
        root / "config.json", remote.read(root / "config.json"), remote.read(root / "relay.py")
    )
    guard()
    clock[0] += 0.5
    guard()
    assert calls == [1000.0]
    guard(force=True)
    assert calls == [1000.0, 1000.5]
    clock[0] += 1
    guard()
    assert calls == [1000.0, 1000.5, 1001.5]
    sibling = remote._RelayGuard(
        root / "config.json", remote.read(root / "config.json"), remote.read(root / "relay.py")
    )
    sibling()
    assert calls[-2:] == [1001.5, 1001.5]
    (root / "relay.py").write_bytes(b"source changed")
    with pytest.raises(ValueError, match="identity/source/config"):
        guard()
    assert len(calls) == 4


def test_failed_or_delayed_full_audit_never_leaves_cached_authority(remote_scene, monkeypatch):
    action(remote_scene, "up")
    root = remote_scene[1].root / "runner-routes"
    clock = [1000.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: clock[0])
    guard = remote._RelayGuard(
        root / "config.json", remote.read(root / "config.json"), remote.read(root / "relay.py")
    )
    guard()

    def fail(config):
        raise ValueError("controlled original observation unavailable")

    monkeypatch.setattr(remote, "_root_guard", fail)
    with pytest.raises(ValueError):
        guard(force=True)
    assert guard.last is None
    with pytest.raises(ValueError):
        guard()

    def delayed(config):
        clock[0] += 11

    monkeypatch.setattr(remote, "_root_guard", delayed)
    with pytest.raises(ValueError, match="deadline"):
        guard(force=True)
    assert guard.last is None and not hasattr(remote._AUDIT_BUDGET, "deadline")
