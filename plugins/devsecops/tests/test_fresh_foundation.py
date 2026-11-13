"""Controlled local filesystem/transport cases; no real guest or E2E qualification."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time
from uuid import uuid4

import pytest

from devsecops.factory import fresh_foundation_recipe as recipes
from devsecops.factory import fresh_foundation_remote as remote
from devsecops.factory import registry_remote, tcg


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def lab(tmp_path, monkeypatch):
    source = tmp_path / "supplied"
    source.mkdir(mode=0o700)

    def pin(name, raw):
        p = source / name
        p.write_bytes(raw)
        p.chmod(0o600)
        return {"path": str(p), "bytes": len(raw), "sha256": digest(raw)}

    assets = {name: pin(name, ("controlled-" + name).encode()) for name in recipes.ASSETS}
    monkeypatch.setattr(recipes, "ASSETS", {name: (v["bytes"], v["sha256"]) for name, v in assets.items()})
    boot = pin("boot.img", b"controlled-empty-cloud-image")
    monkeypatch.setattr(recipes, "BOOT_SHA256", boot["sha256"])
    pki = {}
    for name in recipes.PKI_NAMES:
        raw = (
            b"-----BEGIN OPENSSH PRIVATE KEY-----\ncontrolled\n-----END OPENSSH PRIVATE KEY-----\n"
            if name.endswith("private")
            else b"ssh-ed25519 Y29udHJvbGxlZA==\n"
        )
        pki[name] = pin(name, raw)
    transport = {
        name: {k: v for k, v in pin("transport-" + name, b"controlled-transport").items() if k != "bytes"}
        for name in ("config", "known_hosts", "certificate", "identity", "proxy")
    }
    transport.update(
        identity_agent=str(source / "agent"),
        host_key_sha256="b" * 64,
        verification_basis="operator-verified",
        agent_protocol="native",
    )
    recipe = {
        "kind": "fresh-owned-tcg-v1",
        "host": "controlled-host",
        "remote_parent": "/home/operator/.local/share/devsecops/fresh",
        "private_state_directory": str(tmp_path / "local-private"),
        "tool_image": "sha256:" + "a" * 64,
        "boot_image": boot,
        "rke2_assets": assets,
        "ssh_pki": pki,
        "transport": transport,
        "cleanup": "remove-owned-generation-preserve-inputs",
    }
    parent = tmp_path / "remote-private"
    parent.mkdir(mode=0o700)
    mem = tmp_path / "meminfo"
    mem.write_text("MemAvailable: 999999999 kB\n")
    original_path = Path

    def mapped(value):
        if str(value) == "/proc/meminfo":
            return mem
        candidate = original_path(value)
        selected = original_path(recipe["remote_parent"])
        return parent / candidate.relative_to(selected) if candidate.is_relative_to(selected) else candidate

    monkeypatch.setattr(remote, "Path", mapped)
    monkeypatch.setattr(remote.shutil, "disk_usage", lambda p: SimpleNamespace(free=400 * 1024**3))
    run = str(uuid4())
    identities = {
        name + "_sha256": "c" * 64
        for name in ("scenario", "assertions", "config", "lock", "package", "environment")
    }
    modules = {"fresh_foundation_remote": "d" * 64}
    packet = {
        "recipe": recipe,
        "action": "up",
        "operation_id": str(uuid4()),
        "run_id": run,
        "environment_id": "controlled",
        "identities": identities,
        "modules": modules,
        "timeout": 90,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "expected_root_identity": None,
    }
    packet["binding"] = recipes.binding(
        recipe, run_id=run, environment_id="controlled", identities=identities, modules=modules
    )
    fake = FakeDocker(packet, parent / run)
    monkeypatch.setattr(tcg, "_run", fake.run)
    monkeypatch.setattr(remote, "_install_guest", fake.install)
    return SimpleNamespace(
        packet=packet, recipe=recipe, parent=parent, root=parent / run, fake=fake, source=source
    )


class FakeDocker:
    """No subprocess/network; exact Docker responses for fixed worker commands."""

    def __init__(self, packet, root):
        self.packet, self.root = packet, root
        self.objects = {}
        self.calls = []
        self.fail_after = None
        self.fail_before = None
        self.counter = 0

    def result(self, raw=b"", code=0, err=b""):
        return SimpleNamespace(stdout=raw, stderr=err, returncode=code)

    def emit(self, value):
        return self.result(json.dumps(value).encode())

    def run(self, args, deadline, check=True, data=None):
        self.calls.append(list(args))
        assert time.monotonic() < deadline
        if args[0] == "ssh-keygen":
            return self.result(b"ssh-ed25519 Y29udHJvbGxlZA==\n")
        if args[0] == "ssh":
            command = args[-1]
            if command.startswith("test -f "):
                return self.result()
            if "fresh-rke2-absent" in command:
                return self.result(b"fresh-rke2-absent\n")
            if tcg.GUEST_REPLY_ROUTE in command or "ephemeral" in command:
                return self.emit({"ephemeral": True})
            raise AssertionError(args)
        assert args[0] == "docker"
        if args[1:3] == ["image", "inspect"]:
            return self.emit([{"Id": self.packet["recipe"]["tool_image"]}])
        if len(args) > 2 and args[2] == "inspect":
            item = next(
                (v for v in self.objects.values() if args[3] in (v["Id"], v["Name"].lstrip("/"))), None
            )
            return self.emit([item]) if item else self.result(code=1, err=b"Error: No such object")
        if len(args) > 2 and args[2] == "ls":
            filters = [a.removeprefix("label=").split("=", 1) for a in args if a.startswith("label=")]
            values = []
            for item in self.objects.values():
                labels = item.get("Labels", item.get("Config", {}).get("Labels", {}))
                if all(labels.get(k) == v for k, v in filters):
                    values.append(item["Id"])
            return self.result(("\n".join(values)).encode())
        role = "network" if args[1:3] == ["network", "create"] else None
        if args[1] == "create" or role:
            labels = dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "--label")
            role = labels[remote.ROLE]
            if self.fail_before == ("create", role):
                raise TimeoutError("controlled unobserved request")
            self.counter += 1
            identity = f"{self.counter:064x}"
            if role == "network":
                item = {
                    "Id": identity,
                    "Name": args[-1],
                    "Labels": labels,
                    "Internal": True,
                    "Driver": "bridge",
                    "Containers": {},
                    "IPAM": {"Config": [{"Subnet": "172.19.0.0/16", "Gateway": "172.19.0.1"}]},
                }
            else:

                def pick(key):
                    return args[args.index(key) + 1]

                image = self.packet["recipe"]["tool_image"]
                network = pick("--network")
                net = next((v for v in self.objects.values() if v["Name"] == network), None)
                item = {
                    "Id": identity,
                    "Name": "/" + pick("--name"),
                    "Image": image,
                    "Config": {
                        "Labels": labels,
                        "User": pick("--user"),
                        "Entrypoint": None,
                        "Cmd": args[args.index(image) + 1 :],
                        "Healthcheck": {"Test": ["NONE"]},
                    },
                    "HostConfig": {
                        "Privileged": False,
                        "ReadonlyRootfs": True,
                        "CapDrop": ["ALL"],
                        "CapAdd": None,
                        "Devices": [dict(zip(("PathOnHost", "PathInContainer", "CgroupPermissions"), args[i + 1].split(":")))
                                    for i, arg in enumerate(args) if arg == "--device"],
                        "GroupAdd": [args[i + 1] for i, arg in enumerate(args) if arg == "--group-add"],
                        "SecurityOpt": ["no-new-privileges"],
                        "NetworkMode": network,
                        "Binds": None,
                        "PidMode": "",
                        "IpcMode": "private",
                        "PidsLimit": 256,
                        "Memory": int(pick("--memory")),
                        "MemorySwap": int(pick("--memory-swap")),
                        "NanoCpus": int(pick("--cpus")) * 10**9,
                        "PortBindings": {},
                        "RestartPolicy": {"Name": "no"},
                    },
                    "Mounts": [
                        {"Type": "bind", "Source": str(self.root / "data"), "Destination": "/lab", "RW": True}
                    ],
                    "State": {"Status": "created", "Running": False, "ExitCode": 0},
                    "NetworkSettings": {
                        "Networks": {}
                        if role == "seed"
                        else {network: {"NetworkID": net["Id"], "IPAddress": "172.19.0.2"}}
                    },
                }
            self.objects[role] = item
            if self.fail_after == ("create", role):
                raise TimeoutError("controlled ambiguous creation")
            return self.result(identity.encode())
        if args[1] in ("start", "stop", "rm") or args[1:3] == ["network", "rm"]:
            verb = "rm" if args[1] == "network" else args[1]
            role, item = next((r, v) for r, v in self.objects.items() if v["Id"] == args[-1])
            if verb == "start":
                item["State"].update(Running=role != "seed", Status="exited" if role == "seed" else "running")
                if role == "seed":
                    data = self.root / "data"
                    tcg._write(data / "probe.qcow2", b"controlled-derived-root")
                    tcg._write(data / "seed.iso", b"controlled-derived-seed")
                    tcg._write(
                        data / "service-forward-python.json",
                        recipes.canonical(
                            {
                                "kind": "fixed-service-forward-python",
                                "version": [3, 12, 3],
                                "modules": ["json", "os", "select", "socket", "ssl", "stat"],
                            }
                        ),
                    )
                    tcg._write(
                        data / "disk-info.json",
                        recipes.canonical(
                            {
                                "format": "qcow2",
                                "virtual-size": 160 * 1024**3,
                                "backing-filename": "/lab/ubuntu.img",
                            }
                        ),
                    )
                    tcg._write(
                        data / "tool-packages.tsv",
                        b"qemu-system-x86\t1:8.2.2+ds-0ubuntu1.18\nqemu-utils\t1:8.2.2+ds-0ubuntu1.18\ncloud-image-utils\t0.33-1\n",
                    )
            elif verb == "stop":
                item["State"].update(Running=False, Status="exited")
            else:
                del self.objects[role]
            if self.fail_after == (verb, role):
                raise TimeoutError("controlled ambiguous mutation")
            return self.result()
        raise AssertionError(args)

    def install(self, command, ssh, data, state, save, recipes, deadline):
        tcg._write(data / "operator/kubeconfig.yaml", b"controlled-private-kubeconfig")
        state["api"] = {
            "ca_sha256": "e" * 64,
            "fingerprint": {
                "namespaces": {"default": "new-default", "kube-system": "new-system"},
                "nodes": {"devsecops-fresh": "new-node"},
            },
            "consecutive_ready_observations": 3,
        }
        save()


def execute(lab, action="up", *, operation=None, expected=None, accelerator=None):
    packet = deepcopy(lab.packet)
    packet["action"] = action
    if operation:
        packet["operation_id"] = operation
    if action != "up":
        info = lab.root.stat()
        packet["expected_root_identity"] = (
            {"device": info.st_dev, "inode": info.st_ino} if expected is None else expected
        )
    return remote.execute(packet, tcg, recipes, registry_remote, accelerator=accelerator)


def select_kvm(lab):
    lab.recipe["acceleration"] = {"kind": "kvm", "device_group": 108}
    lab.packet["binding"] = recipes.binding(
        lab.recipe, run_id=lab.packet["run_id"], environment_id=lab.packet["environment_id"],
        identities=lab.packet["identities"], modules=lab.packet["modules"],
    )


def test_explicit_kvm_is_runtime_only_and_cleanup_needs_no_new_kernel_probe(lab, monkeypatch):
    from devsecops.factory import qemu_acceleration
    select_kvm(lab)
    probes = []
    def admit(value):
        probes.append(deepcopy(value))
        return {"kind": "controlled-kernel-admission", "api_version": 12}
    monkeypatch.setattr(qemu_acceleration, "preflight", admit)
    up = execute(lab, accelerator=qemu_acceleration)
    assert up["state"] == "ready" and len(probes) == 1
    assert up["acceleration_admission"]["kind"] == "controlled-kernel-admission"
    creation = [argv for argv in lab.fake.calls if argv[:2] == ["docker", "create"]]
    assert len(creation) == 2
    seed, runtime = creation
    assert "--device" not in seed and "--group-add" not in seed
    assert runtime[runtime.index("--device") + 1] == "/dev/kvm:/dev/kvm:rw"
    assert runtime[runtime.index("--group-add") + 1] == "108"
    assert runtime[runtime.index("-accel") + 1] == "kvm"
    assert runtime[runtime.index("-cpu") + 1] == "host"
    assert runtime[runtime.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in runtime and "--privileged" not in runtime
    def unavailable(value):
        raise OSError("controlled unavailable device after admission")
    monkeypatch.setattr(qemu_acceleration, "preflight", unavailable)
    down = execute(lab, "down", operation=str(uuid4()), accelerator=qemu_acceleration)
    assert down["complete_cleanup"] and not lab.fake.objects
    assert down["source_assets_preserved"] and down["tool_image_preserved"]


@pytest.mark.parametrize("failure", ["missing-helper", "unavailable-kernel"])
def test_failed_kvm_admission_has_no_persistent_effect(lab, monkeypatch, failure):
    from devsecops.factory import qemu_acceleration
    select_kvm(lab)
    def unavailable(value):
        raise OSError("controlled unavailable KVM")
    monkeypatch.setattr(qemu_acceleration, "preflight", unavailable)
    with pytest.raises((OSError, ValueError)):
        execute(lab, accelerator=None if failure == "missing-helper" else qemu_acceleration)
    assert not lab.root.exists() and not lab.fake.calls and not list(lab.parent.iterdir())


@pytest.mark.parametrize("change", ["device", "mknod", "extra-group", "device_rules", "device_requests", "fallback"])
def test_changed_kvm_runtime_blocks_reconciliation_before_new_effects(lab, monkeypatch, change):
    from devsecops.factory import qemu_acceleration
    select_kvm(lab)
    monkeypatch.setattr(qemu_acceleration, "preflight", lambda value: {"kind": "controlled-kernel-admission"})
    execute(lab, accelerator=qemu_acceleration)
    runtime = lab.fake.objects["runtime"]
    if change == "device":
        runtime["HostConfig"]["Devices"][0]["PathOnHost"] = "/dev/mem"
    elif change == "mknod":
        runtime["HostConfig"]["Devices"][0]["CgroupPermissions"] = "rwm"
    elif change == "extra-group":
        runtime["HostConfig"]["GroupAdd"].append("0")
    elif change == "device_rules":
        runtime["HostConfig"]["DeviceCgroupRules"] = ["c *:* rwm"]
    elif change == "device_requests":
        runtime["HostConfig"]["DeviceRequests"] = [{"Driver": "nvidia", "Count": -1}]
    else:
        argv = runtime["Config"]["Cmd"]
        argv[argv.index("-accel") + 1] = "tcg,thread=multi"
    before = len(lab.fake.calls)
    with pytest.raises(ValueError, match="confinement"):
        execute(lab, "reconcile", operation=str(uuid4()), accelerator=qemu_acceleration)
    assert all(argv[1] not in ("create", "start", "stop", "rm") for argv in lab.fake.calls[before:])


@pytest.mark.parametrize("value", [None, {}, {"kind": "tcg", "device_group": 108},
    {"kind": "kvm", "device_group": True}, {"kind": "kvm", "device_group": 0},
    {"kind": "kvm", "device_group": 2**31}, {"kind": "kvm", "device_group": 108, "device": "/dev/mem"}])
def test_acceleration_recipe_and_exported_contract_reject_ambiguity(lab, value):
    import jsonschema
    selected = deepcopy(lab.recipe)
    selected["acceleration"] = value
    with pytest.raises(ValueError):
        recipes.validate(selected)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(selected, recipes.schema())


def test_complete_controlled_generation_and_preserved_inputs(lab):
    before = {p.name: p.read_bytes() for p in lab.source.iterdir()}
    up = execute(lab)
    assert up["state"] == "ready" and not up["complete_cleanup"]
    assert up["root_disk_creation"]["initial_source_sha256"] == lab.recipe["boot_image"]["sha256"]
    assert up["guest_freshness"]["before_install"] == "rke2-config-and-data-absent"
    assert up["ssh_pki_basis"] == "operator-supplied-pins-with-run-created-copies"
    assert up["api"]["fingerprint"]["namespaces"]["kube-system"] == "new-system"
    operation = str(uuid4())
    down = execute(lab, "down", operation=operation)
    assert down["complete_cleanup"] and down["source_assets_preserved"]
    assert not (lab.root / "data").exists() and (lab.root / "owner.json").exists()
    assert {p.name: p.read_bytes() for p in lab.source.iterdir()} == before
    assert all(row["state"] in ("absent", "preserved") for row in down["resources"])
    assert not lab.fake.objects
    recon = execute(lab, "reconcile", operation=operation)
    assert recon["complete_cleanup"]
    commands = json.dumps(lab.fake.calls)
    assert "docker pull" not in commands and "curl" not in commands and "wget" not in commands
    assert "controlled-private-kubeconfig" not in json.dumps(down)


def test_existing_generation_is_never_adopted(lab):
    lab.root.mkdir(mode=0o700)
    sentinel = lab.root / "operator-data"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        execute(lab)
    assert sentinel.read_bytes() == b"preserve" and lab.fake.calls == []


def test_same_creation_and_cleanup_are_not_repeated(lab):
    execute(lab)
    calls = len(lab.fake.calls)
    with pytest.raises(FileExistsError):
        execute(lab)
    assert len(lab.fake.calls) == calls
    op = str(uuid4())
    execute(lab, "down", operation=op)
    calls = len([x for x in lab.fake.calls if x[1] in ("rm", "stop")])
    with pytest.raises(ValueError, match="already attempted"):
        execute(lab, "down", operation=op)
    assert len([x for x in lab.fake.calls if x[1] in ("rm", "stop")]) == calls


@pytest.mark.parametrize("role", ["seed", "network", "runtime"])
def test_unknown_creation_resolves_by_exact_observation_without_retry(lab, role):
    lab.fake.fail_after = ("create", role)
    with pytest.raises(TimeoutError):
        execute(lab)
    recon = execute(lab, "reconcile")
    assert recon["unresolved_operations"] == [] and recon["mutation_effect"] == "applied"
    lab.fake.fail_after = None
    down = execute(lab, "down", operation=str(uuid4()))
    assert down["complete_cleanup"]
    creates = [a for a in lab.fake.calls if a[1] == "create" or a[1:3] == ["network", "create"]]
    assert sum(any(remote.ROLE + "=" + role == part for part in a) for a in creates) == 1


def test_absent_unknown_creation_does_not_prove_nonexecution(lab):
    lab.fake.fail_before = ("create", "seed")
    with pytest.raises(TimeoutError):
        execute(lab)
    recon = execute(lab, "reconcile")
    assert recon["unresolved_operations"] and not recon["complete_cleanup"]
    with pytest.raises(ValueError, match="outcome remains unresolved"):
        execute(lab, "down", operation=str(uuid4()))
    assert (lab.root / "data/ubuntu.img").exists()


@pytest.mark.parametrize("change", ["disk", "owner", "label", "source", "private-key-mode"])
def test_changed_ownership_or_input_stops_cleanup(lab, change):
    execute(lab)
    if change == "disk":
        p = lab.root / "data/probe.qcow2"
        p.rename(p.with_suffix(".original"))
        p.write_bytes(b"replacement")
    elif change == "owner":
        p = lab.root / "owner.json"
        value = json.loads(p.read_bytes())
        value["binding"] = "0" * 64
        p.write_bytes(recipes.canonical(value))
    elif change == "label":
        lab.fake.objects["runtime"]["Config"]["Labels"][remote.LABEL] = "0" * 64
    elif change == "source":
        Path(lab.recipe["boot_image"]["path"]).write_bytes(b"changed")
    else:
        Path(lab.recipe["ssh_pki"]["operator_private"]["path"]).chmod(0o644)
    with pytest.raises((ValueError, tcg.TCGError)):
        execute(lab, "down", operation=str(uuid4()))
    assert (lab.root / "data").exists()
    assert lab.fake.objects["runtime"]["State"]["Running"]


def test_direct_original_root_identity_required(lab):
    execute(lab)
    with pytest.raises(ValueError, match="direct observation"):
        execute(lab, "down", operation=str(uuid4()), expected={"device": 0, "inode": 0})
    assert lab.fake.objects["runtime"]["State"]["Running"]


def test_interrupted_purge_reconciles_without_duplicate_mutation(lab, monkeypatch):
    execute(lab)
    original = registry_remote._purge_owned_payload

    def interrupted(*args):
        original(*args)
        raise TimeoutError("reply lost after payload removed")

    monkeypatch.setattr(registry_remote, "_purge_owned_payload", interrupted)
    op = str(uuid4())
    with pytest.raises(TimeoutError):
        execute(lab, "down", operation=op)
    before = len(lab.fake.calls)
    result = execute(lab, "reconcile", operation=op)
    assert result["complete_cleanup"]
    assert all(a[2] in ("inspect", "ls") for a in lab.fake.calls[before:])


@pytest.mark.parametrize(
    "field,value",
    [("tool_image", None), ("remote_parent", None), ("kind", "retained-tcg-v1"), ("cleanup", "preserve")],
)
def test_closed_recipe_rejects_changed_contract(lab, field, value):
    recipe = deepcopy(lab.recipe)
    recipe[field] = value
    with pytest.raises(ValueError):
        recipes.validate(recipe)


def test_no_preexisting_disk_or_operator_command_field(lab):
    for key in ("disk", "command", "original_ca_sha256", "source_receipt"):
        recipe = deepcopy(lab.recipe)
        recipe[key] = "unapproved"
        with pytest.raises(ValueError):
            recipes.validate(recipe)
    recipe = deepcopy(lab.recipe)
    recipe["ssh_pki"]["operator_private"]["path"] = recipe["private_state_directory"] + "/key"
    with pytest.raises(ValueError, match="outside"):
        recipes.validate(recipe)


def test_schema_is_exact_generated_contract():
    root = Path(recipes.__file__).resolve().parents[3]
    assert json.loads((root / "schemas/fresh-owned-foundation.schema.json").read_bytes()) == {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **recipes.schema(),
    }


@pytest.mark.parametrize("verb,role", [("stop", "runtime"), ("rm", "runtime"), ("rm", "network")])
def test_interrupted_cleanup_only_continues_under_new_explicit_operation(lab, verb, role):
    execute(lab)
    wanted_id = lab.fake.objects[role]["Id"]
    lab.fake.fail_after = (verb, role)
    old = str(uuid4())
    with pytest.raises(TimeoutError):
        execute(lab, "down", operation=old)
    before = len(lab.fake.calls)
    observed = execute(lab, "reconcile", operation=old)
    assert not observed["complete_cleanup"] and not observed["unresolved_operations"]
    assert all(a[2] in ("inspect", "ls") for a in lab.fake.calls[before:])
    lab.fake.fail_after = None
    final = execute(lab, "down", operation=str(uuid4()))
    assert final["complete_cleanup"]
    selected = [
        a
        for a in lab.fake.calls
        if a[-1] == wanted_id and (a[1:3] == ["network", "rm"] if role == "network" else a[1] == verb)
    ]
    assert len(selected) == 1


def test_same_role_late_create_wrong_operation_cannot_resolve(lab):
    lab.fake.fail_after = ("create", "seed")
    with pytest.raises(TimeoutError):
        execute(lab)
    state_path = lab.root / "state.json"
    state = json.loads(state_path.read_bytes())
    other = str(uuid4())
    state["history"].append(other)
    state_path.write_bytes(recipes.canonical(state))
    lab.fake.objects["seed"]["Config"]["Labels"][remote.OPERATION] = other
    observed = execute(lab, "reconcile")
    assert observed["unresolved_operations"] and not observed["complete_cleanup"]
    with pytest.raises(ValueError, match="unresolved"):
        execute(lab, "down", operation=str(uuid4()))


def test_asset_rejects_links_before_read_or_delete(lab):
    pin = lab.recipe["ssh_pki"]["operator_private"]
    source = Path(pin["path"])
    alias = source.with_suffix(".alias")
    alias.symlink_to(source)
    with pytest.raises(ValueError, match="link"):
        remote._asset({**pin, "path": str(alias)}, deadline=time.monotonic() + 5, secret=True)
    source.unlink()
    source.symlink_to(Path(lab.recipe["ssh_pki"]["host_private"]["path"]))
    with pytest.raises(ValueError, match="link"):
        execute(lab)
    assert not lab.fake.objects


def test_fixed_guest_install_checks_artifacts_and_independent_fingerprint(lab, monkeypatch):
    # Invoke the production installer separately from the lifecycle fake's bounded seam.
    import importlib.util
    import base64
    import ast
    import shlex

    spec = importlib.util.spec_from_file_location("controlled_installer_source", Path(remote.__file__))
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    data = lab.parent / "installer"
    (data / "operator").mkdir(parents=True, mode=0o700)
    calls = []
    state = {}
    saves = []
    ca = b"-----BEGIN CERTIFICATE-----\ncontrolled\n-----END CERTIFICATE-----\n"
    raw = b"clusters:\n- cluster:\n    certificate-authority-data: " + base64.b64encode(ca) + b"\n"

    def command(args, seconds=30, **kwargs):
        calls.append(args)
        text = args[-1]
        if "print('verified')" in text or "verified" in text:
            return SimpleNamespace(returncode=0, stdout=b"verified\n")
        if "server_version" in text:
            value = {
                "readyz": "ok",
                "server_version": recipes.VERSION,
                "nodes": [
                    {
                        "name": "devsecops-fresh",
                        "uid": "fresh-node",
                        "ready": True,
                        "kubelet_version": recipes.VERSION,
                    }
                ],
                "namespaces": {"default": "fresh-default", "kube-system": "fresh-system"},
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode())
        if text == "sudo cat /etc/rancher/rke2/rke2.yaml":
            return SimpleNamespace(returncode=0, stdout=raw)
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(original.time, "sleep", lambda duration: None)
    original._install_guest(
        command,
        ["ssh", "-F", "/dev/null", "-p", "2222", "demo@172.19.0.2"],
        data,
        state,
        lambda: saves.append(deepcopy(state)),
        recipes,
        time.monotonic() + 60,
    )
    assert state["api"]["fingerprint"]["namespaces"]["kube-system"] == "fresh-system"
    assert state["api"]["ca_sha256"] == digest(ca)
    assert len(state["readiness_observations"]) == 3
    assert state["guest_install_intent"]["effect"] == "applied"
    assert any(row.get("guest_install_intent", {}).get("effect") == "unknown" for row in saves)
    writer = next(a[-1] for a in calls if "as f:f.write(" in a[-1])
    tree = ast.parse(shlex.split(writer)[-1])
    write_call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Attribute) and n.func.attr == "write")
    configuration_bytes = ast.literal_eval(write_call.args[0]).encode()
    configuration = json.loads(configuration_bytes)
    assert configuration["cni"] == "canal"
    assert configuration["kubelet-arg"] == ["runtime-request-timeout=10m"]
    assert state["guest_install_intent"]["configuration_sha256"] == digest(configuration_bytes)
    install = next(a for a in calls if "INSTALL_RKE2_ARTIFACT_PATH=" in a[-1])
    assert "INSTALL_RKE2_METHOD=tar" in install[-1] and "curl" not in install[-1]
    assert sum(a[0] == "scp" for a in calls) == len(recipes.ASSETS)


def test_missing_supplied_tool_image_stops_cleanup_before_deletion(lab, monkeypatch):
    execute(lab)
    original = lab.fake.run

    def no_image(args, *a, **kw):
        if args[:3] == ["docker", "image", "inspect"]:
            return lab.fake.result(code=1, err=b"No such image")
        return original(args, *a, **kw)

    monkeypatch.setattr(tcg, "_run", no_image)
    with pytest.raises(ValueError, match="tool image"):
        execute(lab, "down", operation=str(uuid4()))
    assert lab.fake.objects["runtime"]["State"]["Running"]


def test_missing_tool_python_stops_before_qemu_guest(lab, monkeypatch):
    original = tcg._run

    def observed(argv, *args, **kwargs):
        result = original(argv, *args, **kwargs)
        if argv[:2] == ["docker", "start"] and "seed" in lab.fake.objects:
            seed = lab.fake.objects["seed"]
            if argv[-1] == seed["Id"]:
                seed["State"]["ExitCode"] = 73
        return result

    monkeypatch.setattr(tcg, "_run", observed)
    with pytest.raises(ValueError, match="lacks Python"):
        remote.execute(lab.packet, tcg, recipes, registry_remote)
    assert "runtime" not in lab.fake.objects
