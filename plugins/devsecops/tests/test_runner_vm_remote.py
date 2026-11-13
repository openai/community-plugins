"""Controlled VM lifecycle effects only; no Docker, guest, SSH or network calls."""

from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devsecops.factory import runner_vm_recipe as recipes, runner_vm_remote as remote, tcg
from tests import test_fresh_foundation as shared
from tests.test_fresh_foundation import lab as foundation_lab  # noqa: F401


class RunnerDocker(shared.FakeDocker):
    def run(self, args, deadline, check=True, data=None):
        network, runtime = self.objects.get("network"), self.objects.get("runtime")
        if network is not None:
            members = network["Containers"]
            if runtime is not None and runtime["State"].get("Running"):
                members[runtime["Id"]] = {"Name": runtime["Name"].removeprefix("/")}
            else:
                for identifier in list(members):
                    if identifier != "foreign":
                        members.pop(identifier)
        if args[0] == "ssh" and "guest_boot_id" in args[-1]:
            self.calls.append(list(args))
            assert args[-2].startswith("gitlab-runner@")
            return self.emit(
                {
                    "guest_boot_id": "3f2d83e0-2157-4a7c-9ab9-48f5bd9d3ae2",
                    "guest_uid": 10001,
                    "guest_gid": 10001,
                    "guest_user": "gitlab-runner",
                    "guest_os": "linux",
                    "guest_architecture": "amd64",
                    "no_new_privs": 0,
                    "rke2_state": "absent",
                }
            )
        return super().run(args, deadline, check=check, data=data)


@pytest.fixture
def lab(foundation_lab, monkeypatch):  # noqa: F811
    lab = foundation_lab
    recipe = lab.recipe
    recipe.pop("rke2_assets")
    recipe.update(
        schema_version=1,
        kind="owned-runner-vm-v1",
        remote_parent="/home/operator/.local/share/devsecops/runner-vm",
        resources={"cpus": 4, "memory_mib": 8192, "disk_gib": 160},
        guest=dict(recipes.GUEST),
        egress={"external_origins": [], "external_ca_bundle": None},
        cleanup="remove-owned-runner-generation-preserve-inputs",
    )
    for name, pin in recipe["ssh_pki"].items():
        path = shared.Path(pin["path"])
        raw = (
            path.read_bytes().rstrip() + b" " + name.encode() + b"\n"
            if name.endswith("public")
            else path.read_bytes() + name.encode() + b"\n"
        )
        path.write_bytes(raw)
        pin.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(remote, "Path", shared.remote.Path)
    monkeypatch.setattr(shared, "remote", remote)
    monkeypatch.setattr(shared, "recipes", recipes)
    lab.packet["modules"] = {"runner_vm_remote": "d" * 64}
    lab.packet["binding"] = recipes.binding(
        recipe,
        run_id=lab.packet["run_id"],
        environment_id="controlled",
        identities=lab.packet["identities"],
        modules=lab.packet["modules"],
    )
    lab.fake = RunnerDocker(lab.packet, lab.root)
    monkeypatch.setattr(tcg, "_run", lab.fake.run)
    return lab


def test_complete_guest_lifecycle_and_historical_identity(lab):
    before = {p.name: p.read_bytes() for p in lab.source.iterdir()}
    up = shared.execute(lab)
    assert up["state"] == "ready" and up["runner_vm"]["guest_uid"] == 10001
    assert up["runner_vm"]["generation"] == "runner-vm-" + lab.packet["run_id"]
    assert up["guest_identity"]["rke2_state"] == "absent"
    operation = str(uuid4())
    down = shared.execute(lab, "down", operation=operation)
    assert down["complete_cleanup"] and down["source_assets_preserved"] and down["tool_image_preserved"]
    assert down["runner_vm"] == up["runner_vm"] and not lab.fake.objects
    assert not (lab.root / "data").exists() and (lab.root / "owner.json").exists()
    assert {p.name: p.read_bytes() for p in lab.source.iterdir()} == before
    assert all(row["state"] in ("absent", "preserved") for row in down["resources"])
    before_calls = len(lab.fake.calls)
    reconciled = shared.execute(lab, "reconcile", operation=operation)
    assert reconciled["complete_cleanup"] and reconciled["mutation_effect"] == "applied"
    assert reconciled["observation_effect"] == "not_applied"
    assert all(call[1] not in ("create", "start", "stop", "rm") for call in lab.fake.calls[before_calls:])


def test_seed_and_qemu_have_fixed_nonroot_control_and_closed_network(lab):
    pins = lab.recipe["ssh_pki"]

    def read(name):
        return shared.Path(pins[name]["path"]).read_text()

    seed = json.loads(
        remote._cloud_seed(
            read("operator_public"),
            read("host_private"),
            read("host_public"),
            lab.packet["run_id"],
            "controlled",
            lab.recipe,
        ).split(b"\n", 1)[1]
    )
    assert len(seed["users"]) == 1
    user = seed["users"][0]
    assert user["name"] == "gitlab-runner" and user["uid"] == 10001 and user["sudo"] is False
    assert user["groups"] == [] and seed["disable_root"] and not seed["package_update"]
    net = remote._netdev(lab.recipe)
    assert "restrict=on" in net and "6443" not in net and "registry1" not in net
    assert "10.0.2.100:8444" in net and "10.0.2.101:8443" in net
    selected = deepcopy(lab.recipe)
    selected["egress"]["external_origins"] = ["https://registry1.dso.mil"]
    net = remote._netdev(selected)
    assert "10.0.2.102:443" in net and "p1docs" not in net


def test_explicit_kvm_runtime_and_cleanup(lab, monkeypatch):
    shared.test_explicit_kvm_is_runtime_only_and_cleanup_needs_no_new_kernel_probe(lab, monkeypatch)


@pytest.mark.parametrize("failure", ["missing-helper", "unavailable-kernel"])
def test_failed_kvm_preflight_has_no_persistent_effect(lab, monkeypatch, failure):
    shared.test_failed_kvm_admission_has_no_persistent_effect(lab, monkeypatch, failure)


@pytest.mark.parametrize("change", ["device", "mknod", "extra-group", "device_rules", "device_requests", "fallback"])
def test_changed_kvm_runtime_rejected(lab, monkeypatch, change):
    shared.test_changed_kvm_runtime_blocks_reconciliation_before_new_effects(lab, monkeypatch, change)


@pytest.mark.parametrize("value", [None, {}, {"kind": "tcg", "device_group": 108},
    {"kind": "kvm", "device_group": True}, {"kind": "kvm", "device_group": 0},
    {"kind": "kvm", "device_group": 2**31}, {"kind": "kvm", "device_group": 108, "device": "/dev/mem"}])
def test_acceleration_schema_rejects_ambiguity(lab, value):
    shared.test_acceleration_recipe_and_exported_contract_reject_ambiguity(lab, value)


def test_same_operation_cannot_create_or_delete_twice(lab):
    shared.test_same_creation_and_cleanup_are_not_repeated(lab)


def test_existing_generation_never_adopted(lab):
    shared.test_existing_generation_is_never_adopted(lab)


@pytest.mark.parametrize("role", ["seed", "network", "runtime"])
def test_unknown_create_requires_positive_original_observation(lab, role):
    shared.test_unknown_creation_resolves_by_exact_observation_without_retry(lab, role)


def test_absent_unknown_creation_stays_unknown(lab):
    shared.test_absent_unknown_creation_does_not_prove_nonexecution(lab)


@pytest.mark.parametrize("change", ["owner", "asset", "disk"])
def test_changed_original_identity_blocks_cleanup(lab, change):
    shared.test_changed_ownership_or_input_stops_cleanup(lab, change)


def test_direct_original_generation_required(lab):
    shared.test_direct_original_root_identity_required(lab)


def test_interrupted_purge_only_observes_original_effect(lab, monkeypatch):
    shared.test_interrupted_purge_reconciles_without_duplicate_mutation(lab, monkeypatch)


@pytest.mark.parametrize("verb,role", [("stop", "runtime"), ("rm", "runtime"), ("rm", "network")])
def test_interrupted_cleanup_needs_new_explicit_operation(lab, verb, role):
    shared.test_interrupted_cleanup_only_continues_under_new_explicit_operation(lab, verb, role)


def test_late_create_from_wrong_operation_rejected(lab):
    shared.test_same_role_late_create_wrong_operation_cannot_resolve(lab)


def test_input_links_never_followed(lab):
    shared.test_asset_rejects_links_before_read_or_delete(lab)


def test_source_tool_preservation_required(lab, monkeypatch):
    shared.test_missing_supplied_tool_image_stops_cleanup_before_deletion(lab, monkeypatch)


def test_runtime_python_preflight_before_guest(lab, monkeypatch):
    shared.test_missing_tool_python_stops_before_qemu_guest(lab, monkeypatch)


def _original_packet(lab, up, action, operation=None):
    packet = deepcopy(lab.packet)
    packet.update(
        action=action,
        operation_id=operation or str(uuid4()),
        expected_root_identity=up["root_identity"],
        expected_runner_vm=up["runner_vm"],
    )
    return packet


def test_parent_process_identity_is_checked_before_any_cleanup_effect(lab):
    up = shared.execute(lab)
    packet = _original_packet(lab, up, "down")
    packet["expected_runner_vm"]["runtime_id"] = "f" * 64
    before = len(lab.fake.calls)
    with pytest.raises(ValueError, match="original process-bound identity"):
        remote.execute(packet, tcg, recipes, shared.registry_remote)
    assert len(lab.fake.calls) == before and lab.fake.objects["runtime"]["State"]["Running"]


def test_host_reconciliation_preserves_guest_unknown_without_guest_ssh(lab, monkeypatch):
    up = shared.execute(lab)
    original = lab.fake.run

    def no_guest(args, *a, **kw):
        assert args[0] != "ssh", "Host-only reconciliation must not require unavailable guest SSH"
        return original(args, *a, **kw)

    monkeypatch.setattr(tcg, "_run", no_guest)
    packet = _original_packet(lab, up, "host-observe")
    result = remote.execute(packet, tcg, recipes, shared.registry_remote)
    assert result["runner_vm"] == up["runner_vm"]
    assert result["guest_observation"] == "not-requeried" and not result["complete_cleanup"]
    assert result["observation_effect"] == "not_applied" and result["unresolved_operations"] == []
    packet = _original_packet(lab, up, "routes")
    packet["child"] = {"action": "reconcile-host"}
    seen = []

    def observe(child, *, scope):
        assert type(scope) is remote.RunnerVMRemoteContext
        seen.append(scope.identity())
        return {"complete_cleanup": False, "guest_unresolved": True, "host_disposal_eligible": True}

    result = remote.execute(
        packet, tcg, recipes, shared.registry_remote, routes=SimpleNamespace(execute=observe)
    )
    assert seen == [up["runner_vm"]] and result["child"]["guest_unresolved"]


def test_guest_boot_change_blocks_child_effect(lab, monkeypatch):
    up = shared.execute(lab)
    original = lab.fake.run

    def changed(args, *a, **kw):
        result = original(args, *a, **kw)
        if args[0] == "ssh" and "guest_boot_id" in args[-1]:
            value = json.loads(result.stdout)
            value["guest_boot_id"] = str(uuid4())
            return lab.fake.emit(value)
        return result

    monkeypatch.setattr(tcg, "_run", changed)
    packet = _original_packet(lab, up, "runner")
    packet["child"] = {"action": "install"}
    handler = SimpleNamespace(execute=lambda *_a, **_kw: pytest.fail("Changed boot reached child mutation"))
    with pytest.raises(ValueError, match="boot or account changed"):
        remote.execute(packet, tcg, recipes, shared.registry_remote, runner=handler)


def test_changed_copied_host_key_blocks_child_effect(lab):
    up = shared.execute(lab)
    (lab.root / "data/ssh/host.pub").write_bytes(b"changed-key\n")
    packet = _original_packet(lab, up, "runner")
    packet["child"] = {"action": "install"}
    handler = SimpleNamespace(execute=lambda *_a, **_kw: pytest.fail("Changed copied PKI reached mutation"))
    with pytest.raises(ValueError, match="asset identity|immutable pin"):
        remote.execute(packet, tcg, recipes, shared.registry_remote, runner=handler)


def test_public_external_ca_is_observed_from_remote_source_and_preserved(lab):
    source = lab.source / "external-ca.pem"
    source.write_bytes(b"controlled-public-ca")
    source.chmod(0o600)
    lab.recipe["egress"] = {
        "external_origins": ["https://registry1.dso.mil"],
        "external_ca_bundle": {
            "path": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
    }
    lab.packet["binding"] = recipes.binding(
        lab.recipe,
        run_id=lab.packet["run_id"],
        environment_id="controlled",
        identities=lab.packet["identities"],
        modules=lab.packet["modules"],
    )
    up = shared.execute(lab)
    ca = up["endpoint"]["external_ca"]
    assert ca["sha256"] == lab.recipe["egress"]["external_ca_bundle"]["sha256"]
    assert ca["bytes"] == len(source.read_bytes()) and ca["ca_b64"]
    assert up["source_assets"]["external_ca"]["sha256"] == ca["sha256"]
    down = shared.execute(lab, "down", operation=str(uuid4()))
    assert down["complete_cleanup"] and source.read_bytes() == b"controlled-public-ca"


def test_partial_asset_copy_keeps_original_intent_and_can_clean_owned_payload(lab, monkeypatch):
    original = remote._asset

    def interrupted(pin, *, deadline, destination=None, secret=False):
        if destination is not None and str(destination).endswith("/ssh/operator"):
            raise TimeoutError("controlled copy interruption before credential copy")
        return original(pin, deadline=deadline, destination=destination, secret=secret)

    monkeypatch.setattr(remote, "_asset", interrupted)
    with pytest.raises(TimeoutError):
        shared.execute(lab)
    state = json.loads((lab.root / "state.json").read_bytes())
    assert state["asset_copy_pending"] == "operator_private"
    assert "boot" in state["assets"] and "operator_private" in state["assets"]
    assert not lab.fake.objects
    monkeypatch.setattr(remote, "_asset", original)
    observed = shared.execute(lab, "reconcile")
    assert observed["cleanup_eligible"] and observed["unresolved_operations"] == []
    assert observed["mutation_effect"] == "applied" and "runner_vm" not in observed
    down = shared.execute(lab, "down", operation=str(uuid4()))
    assert down["complete_cleanup"] and not (lab.root / "data").exists()


def test_generated_forwarder_change_blocks_first_child_dispatch(lab):
    up = shared.execute(lab)
    (lab.root / "data/service-link-forward.py").write_text("changed")
    packet = _original_packet(lab, up, "routes")
    packet["child"] = {"action": "up"}
    handler = SimpleNamespace(execute=lambda *_a, **_kw: pytest.fail("Changed forwarder reached child"))
    with pytest.raises(ValueError, match="fixed forwarder source changed"):
        remote.execute(packet, tcg, recipes, shared.registry_remote, routes=handler)


def test_foreign_network_member_blocks_parent_deletion(lab):
    up = shared.execute(lab)
    lab.fake.objects["network"]["Containers"]["foreign"] = {"Name": "unowned"}
    with pytest.raises(ValueError, match="network membership"):
        remote.execute(_original_packet(lab, up, "down"), tcg, recipes, shared.registry_remote)
    assert lab.fake.objects["runtime"]["State"]["Running"]
