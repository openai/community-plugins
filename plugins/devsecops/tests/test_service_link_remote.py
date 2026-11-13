"""Run real remote coordinator against controlled Docker/process/SSH boundaries."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from devsecops.factory import service_link_remote as remote, fresh_foundation_remote as fresh, tcg


@pytest.fixture
def scene(tmp_path, monkeypatch):
    run = str(uuid4())
    op = str(uuid4())
    parent = tmp_path / "fresh"
    parent.mkdir(mode=0o700)
    root = parent / run
    root.mkdir(mode=0o700)
    data = root / "data"
    data.mkdir(mode=0o700)
    (data / "probe.qcow2").write_bytes(b"controlled-disk")
    (data / "service-link-forward.py").write_bytes(fresh.GUEST_SERVICE_FORWARD.encode())
    image = tmp_path / "images"
    image.write_bytes(b"rancher/coredns:controlled\n")
    identities = {
        n + "_sha256": str(i) * 64
        for i, n in enumerate(("scenario", "assertions", "config", "lock", "package", "environment"))
    }
    fi = {"device": root.stat().st_dev, "inode": root.stat().st_ino}
    di = {"device": (data / "probe.qcow2").stat().st_dev, "inode": (data / "probe.qcow2").stat().st_ino}
    foundation = {
        "run_id": run,
        "identities": identities,
        "generation": run,
        "root": str(root),
        "root_identity": fi,
        "up_operation_id": op,
        "binding": "f" * 64,
        "runtime_id": "1" * 64,
        "network_id": "2" * 64,
        "address": "172.21.0.2",
        "cluster_uid": "original-system-uid",
        "root_disk_creation": {"disk_identity": di},
        "source_assets": {},
        "guest_forward_source_sha256": remote.sha(fresh.GUEST_SERVICE_FORWARD.encode()),
        "qemu_argv": fresh._qemu(),
        "recipe": {
            "remote_parent": str(parent),
            "rke2_assets": {
                "rke2-images.linux-amd64.txt": {
                    "path": str(image),
                    "bytes": image.stat().st_size,
                    "sha256": remote.sha(image.read_bytes()),
                }
            },
        },
    }
    (root / "owner.json").write_bytes(
        remote.canonical({"run_id": run, "binding": "f" * 64, "root_identity": fi, "up_operation_id": op})
    )

    def target(role, identifier, kind="container"):
        return {"id": identifier * 64, "kind": kind, "role": role, "labels": {"owned": role}}

    def service(role):
        return {
            "run_id": run,
            "identities": identities,
            "binding": role,
            "origin": f"https://{role}.demo.test:" + ("8444" if role == "gitlab" else "8443"),
            "port": 8444 if role == "gitlab" else 8443,
            "ca_path": "/unused/public.pem",
            "ca_sha256": remote.sha(b"CA"),
            "network": target(role + "-network", "4" if role == "gitlab" else "6", "network"),
            **(
                {"server": target("server", "3"), "runner": None}
                if role == "gitlab"
                else {"registry": target("registry", "5")}
            ),
        }

    targets = {"foundation": foundation, "gitlab": service("gitlab"), "registry": service("registry")}
    binding = remote.sha(json.dumps(targets, sort_keys=True, separators=(",", ":")).encode())
    packet = {
        "schema_version": 1,
        "action": "up",
        "operation_id": op,
        "run_id": run,
        "identities": identities,
        "binding": binding,
        "targets": targets,
        "timeout": 90,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
        "materials": {"gitlab": "Q0E=", "registry": "Q0E="},
        "expected_root_identity": None,
        "expected_observation": None,
    }
    resources = {}
    for digit, subnet in [("2", 21), ("4", 22), ("6", 23)]:
        resources[digit * 64] = {
            "Id": digit * 64,
            "Driver": "bridge",
            "Internal": digit != "4",
            "IPAM": {"Config": [{"Subnet": f"172.{subnet}.0.0/24", "Gateway": f"172.{subnet}.0.1"}]},
        }
    for digit, net, subnet, role in [
        ("1", "2", 21, "runtime"),
        ("3", "4", 22, "server"),
        ("5", "6", 23, "registry"),
        ("7", "4", 22, "runner"),
    ]:
        resources[digit * 64] = {
            "Id": digit * 64,
            "State": {"Running": True},
            "Config": {"Cmd": fresh._qemu() if role == "runtime" else []},
            "NetworkSettings": {
                "Networks": {
                    "fixed": {
                        "NetworkID": net * 64,
                        "IPAddress": f"172.{subnet}.0." + ("3" if role == "runner" else "2"),
                    }
                }
            },
        }
    commands = []
    controls = {}
    live = {}
    guest_state = {"ready": False, "restored": False}
    runner_state = {"ready": False}

    def inspect(target):
        commands.append(("inspect", target["id"]))
        if controls.get("replacement") == target["id"]:
            raise ValueError("controlled ID replacement")
        return deepcopy(resources[target["id"]])

    monkeypatch.setattr(remote, "inspect", inspect)

    def identity(pid):
        return deepcopy(live.get(pid))

    monkeypatch.setattr(remote, "process_identity", identity)

    class Process:
        def __init__(self, argv, **kwargs):
            commands.append(("spawn", argv))
            self.pid = 4242
            live[self.pid] = {
                "pid": 4242,
                "start": "original",
                "command_sha256": "a" * 64,
                "process_group": 4242,
                "session": 4242,
            }
            if controls.get("daemon_failed"):
                return
            config = json.loads(Path(argv[-1]).read_bytes())
            path = Path(argv[-1]).parent
            listeners = {
                n: {**row, "port": row["port"] or 30000 + i}
                for i, (n, row) in enumerate(config["listeners"].items())
            }
            (path / "daemon.json").write_bytes(
                remote.canonical(
                    {
                        "binding": binding,
                        "process": live[self.pid],
                        "listeners": listeners,
                        "config_sha256": remote.sha(Path(argv[-1]).read_bytes()),
                        "script_sha256": config["script_sha256"],
                    }
                )
            )

        def poll(self):
            if controls.get("daemon_failed"):
                live.pop(self.pid, None)
            return None if self.pid in live else 0

    monkeypatch.setattr(remote.subprocess, "Popen", Process)

    def kill(pid, sig):
        if sig == 0:
            if pid not in live:
                raise ProcessLookupError
            return
        commands.append(("signal", pid))
        live.pop(pid, None)

    monkeypatch.setattr(remote.os, "killpg", kill)

    def run(argv, deadline, *, data=None, **kw):
        selected = json.loads(data)
        action = selected["action"]
        kind = "runner" if argv[0] == "docker" else "guest"
        commands.append((kind, action))
        state = runner_state if kind == "runner" else guest_state
        if action == "up":
            state["ready"] = True
            state["restored"] = False
        if action == "down":
            state["ready"] = False
            state["restored"] = True
        unknown = controls.get("guest_unknown") and kind == "guest"
        result = {
            "binding": binding,
            "operation_id": selected.get("operation_id", op),
            "stage": "restored" if state.get("restored") else "ready",
            "ready": state["ready"] and not unknown and not controls.get("guest_partial"),
            "cleanup_eligible": not unknown,
            "observed_partial": bool(controls.get("guest_partial")) and not state.get("restored"),
            "complete_cleanup": state.get("restored", False) and not unknown,
            "unresolved_operations": ["controlled-guest-unknown"] if unknown else [],
            "mutation_effect": "not_applied"
            if action == "reconcile"
            else "unknown"
            if unknown
            else "applied",
            "resources": [
                {
                    "kind": kind + "-mapping",
                    "disposition": "remove",
                    "state": "absent" if state.get("restored") else "present",
                }
            ],
        }
        if controls.get("lost_guest_up") and kind == "guest" and action == "up":
            raise ValueError("controlled lost guest response")
        return SimpleNamespace(returncode=0, stdout=remote.canonical(result), stderr=b"")

    support = SimpleNamespace(
        _private=tcg._private,
        _lock_descriptor=tcg._lock_descriptor,
        _write=tcg._write,
        _run=run,
        _guest_ssh=lambda *a: ["controlled-guest-ssh"],
    )
    guest = SimpleNamespace(SOURCE_BYTES=b"# controlled guest boundary")

    def call(action, previous=None):
        selected = deepcopy(packet)
        selected["action"] = action
        if action == "down":
            selected["operation_id"] = str(uuid4())
        if previous:
            selected["expected_root_identity"] = previous["root_identity"]
            selected["expected_observation"] = previous
        return remote.execute(selected, support, guest)

    return SimpleNamespace(
        packet=packet,
        root=root,
        call=call,
        commands=commands,
        controls=controls,
        live=live,
        guest=guest_state,
        target=target,
        resources=resources,
    )


def test_complete_owned_path_restores_before_stopping_and_never_joins_networks(scene):
    up = scene.call("up")
    assert up["ready"] and not up["complete_cleanup"]
    scene.packet["targets"]["gitlab"]["runner"] = scene.target("runner", "7")
    runner = scene.call("runner", up)
    assert runner["ready"]
    down = scene.call("down", runner)
    assert down["complete_cleanup"] and not down["unresolved_operations"]
    effects = [x[0:2] for x in scene.commands if x[0] != "inspect"]
    assert effects[-3:] == [("runner", "down"), ("guest", "down"), ("signal", 4242)]
    assert not (scene.root / "data/service-links.json").exists()
    assert all(
        r["state"] == ("absent" if r["disposition"] == "remove" else "preserved") for r in down["resources"]
    )


def test_unready_guest_never_promoted_and_no_duplicate_on_reconcile(scene):
    scene.controls["guest_unknown"] = True
    with pytest.raises(ValueError, match="unready"):
        scene.call("up")
    with pytest.raises(FileExistsError):
        scene.call("up")
    observed = scene.call("reconcile")
    assert not observed["ready"] and observed["unresolved_operations"]
    assert [r for r in scene.commands if r == ("guest", "up")] == [("guest", "up")]
    assert not any(r[0] == "signal" for r in scene.commands)


def test_lost_guest_result_reconciles_readonly_then_new_cleanup(scene):
    scene.controls["lost_guest_up"] = True
    with pytest.raises(ValueError, match="lost guest"):
        scene.call("up")
    observed = scene.call("reconcile")
    assert observed["reconciliation"]["terminal_observed"] is True and observed["ready"]
    assert not observed["unresolved_operations"]
    down = scene.call("down", observed)
    assert down["complete_cleanup"]
    assert len([r for r in scene.commands if r == ("guest", "up")]) == 1


def test_guest_cleanup_uncertainty_preserves_relays(scene):
    up = scene.call("up")
    scene.controls["guest_unknown"] = True
    with pytest.raises(ValueError, match="preserve relay"):
        scene.call("down", up)
    assert scene.live and not any(r[0] == "signal" for r in scene.commands)
    observed = scene.call("reconcile", up)
    assert not observed["complete_cleanup"] and observed["unresolved_operations"]


def test_original_file_drift_prevents_cleanup_and_signal(scene):
    up = scene.call("up")
    (scene.root / "service-links/relay-config.json").write_text("{}")
    with pytest.raises(ValueError, match="configuration changed"):
        scene.call("down", up)
    assert scene.live and not any(r[0] == "signal" for r in scene.commands)


def test_original_disk_replacement_prevents_all_service_effects(scene):
    path = scene.root / "data/probe.qcow2"
    replacement = path.with_name("replacement")
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    with pytest.raises(ValueError, match="disk was replaced"):
        scene.call("up")
    assert not scene.commands and not (scene.root / "service-links").exists()


def test_expired_provision_grant_does_not_create_intent(scene):
    scene.packet["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="expired"):
        scene.call("up")
    assert not (scene.root / "service-links-intent.json").exists()


def test_runtime_isolation_and_fixed_command_are_revalidated(scene):
    scene.resources["1" * 64]["Config"]["Cmd"] = ["sh"]
    with pytest.raises(ValueError, match="command changed"):
        scene.call("up")
    assert not (scene.root / "service-links-intent.json").exists()


def test_endpoint_substitution_cannot_preserve_binding(scene):
    scene.packet["targets"]["registry"]["origin"] = "https://registry.other.test:8443"
    with pytest.raises(ValueError, match="binding differs"):
        scene.call("up")
    assert not scene.commands


def test_original_target_replacement_cannot_be_cleaned(scene):
    up = scene.call("up")
    scene.controls["replacement"] = "5" * 64
    with pytest.raises(ValueError, match="ID replacement"):
        scene.call("down", up)
    assert scene.live and not any(r[0] == "signal" for r in scene.commands)


def test_peer_admission_uses_original_container_ip_for_every_connection(scene):
    scene.call("up")
    root = scene.root / "service-links"
    config = json.loads((root / "relay-config.json").read_bytes())
    for listener, good, bad in [
        ("guest-registry", "172.21.0.2", "172.21.0.3"),
        ("desktop-registry", "127.0.0.1", "172.21.0.2"),
    ]:
        spec = config["listeners"][listener]
        assert remote.resolve_connection(config, root, spec, good) == ("172.23.0.2", 8443)
        with pytest.raises(ValueError, match="peer"):
            remote.resolve_connection(config, root, spec, bad)
    with pytest.raises(FileNotFoundError):
        remote.resolve_connection(config, root, config["listeners"]["runner-registry"], "172.22.0.3")
    (root / "runner-peer.json").write_bytes(
        remote.canonical({"binding": config["binding"], "identity": scene.target("runner", "7")})
    )
    assert remote.resolve_connection(config, root, config["listeners"]["runner-registry"], "172.22.0.3") == (
        "172.23.0.2",
        8443,
    )
    with pytest.raises(ValueError, match="peer"):
        remote.resolve_connection(config, root, config["listeners"]["runner-registry"], "172.22.0.4")
    scene.controls["replacement"] = "5" * 64
    with pytest.raises(ValueError):
        remote.resolve_connection(config, root, config["listeners"]["guest-registry"], "172.21.0.2")


def test_added_network_attachment_is_rejected_before_forwarding(scene):
    scene.call("up")
    root = scene.root / "service-links"
    config = json.loads((root / "relay-config.json").read_bytes())
    scene.resources["5" * 64]["NetworkSettings"]["Networks"]["unapproved"] = {
        "NetworkID": "e" * 64,
        "IPAddress": "172.24.0.2",
    }
    with pytest.raises(ValueError, match="exact owned network"):
        remote.resolve_connection(config, root, config["listeners"]["guest-registry"], "172.21.0.2")


def test_fresh_qemu_keeps_restriction_and_only_two_fixed_guest_routes():
    args = fresh._qemu()
    network = args[args.index("-netdev") + 1]
    assert "restrict=on" in network and network.count("guestfwd=") == 2
    assert "guestfwd=tcp:10.0.2.100:8444-cmd:python3 -I /lab/service-link-forward.py gitlab" in network
    assert "guestfwd=tcp:10.0.2.101:8443-cmd:python3 -I /lab/service-link-forward.py registry" in network
    assert not any(v in network for v in ("restrict=off", "tap", "host=", "smb="))


def test_known_partial_guest_is_cleanup_eligible_without_becoming_ready(scene):
    scene.controls["guest_partial"] = True
    with pytest.raises(ValueError, match="unready"):
        scene.call("up")
    observed = scene.call("reconcile")
    assert observed["reconciliation"]["terminal_observed"] is True
    assert observed["ready"] is False and not observed["unresolved_operations"]
    down = scene.call("down", observed)
    assert down["complete_cleanup"] is True
    assert [r for r in scene.commands if r == ("guest", "up")] == [("guest", "up")]


def test_read_rejects_fifo_without_blocking(tmp_path):
    path = tmp_path / "fifo"
    os.mkfifo(path)
    with pytest.raises(ValueError, match="type"):
        remote.read(path)


def test_original_source_in_place_change_rejects_before_effects(scene, tmp_path):
    source = tmp_path / "original-source"
    source.write_bytes(b"original")
    info = source.stat()
    scene.packet["targets"]["foundation"]["source_assets"] = {
        "source": {
            "path": str(source),
            "device": info.st_dev,
            "inode": info.st_ino,
            "bytes": 8,
            "sha256": remote.sha(b"original"),
        }
    }
    scene.packet["binding"] = remote.sha(
        json.dumps(scene.packet["targets"], sort_keys=True, separators=(",", ":")).encode()
    )
    source.write_bytes(b"replaced")
    with pytest.raises(ValueError, match="source bytes changed"):
        scene.call("up")
    assert not scene.commands


def test_observed_failed_daemon_process_is_cleanup_eligible_without_spawn_retry(scene):
    scene.controls["daemon_failed"] = True
    with pytest.raises(ValueError, match="before listener"):
        scene.call("up")
    observed = scene.call("reconcile")
    assert observed["reconciliation"]["terminal_observed"] is True and not observed["ready"]
    assert not observed["unresolved_operations"]
    down = scene.call("down", observed)
    assert down["complete_cleanup"] is True
    assert len([r for r in scene.commands if r[0] == "spawn"]) == 1
    assert not any(r[0] in ("guest", "signal") for r in scene.commands)
