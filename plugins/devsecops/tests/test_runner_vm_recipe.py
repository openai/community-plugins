"""Explicit separate VM selections; these fixtures create no guest or service."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import UUID

import jsonschema
import pytest

from devsecops.factory import runner_vm_recipe as r
from devsecops.factory.state import IDENTITY_KEYS


def selection(tmp_path):
    def pin(name):
        raw = ("controlled-" + name).encode()
        return {
            "path": str(tmp_path / "supplied" / name),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    transport = {
        name: {k: v for k, v in pin("transport-" + name).items() if k != "bytes"}
        for name in ("config", "known_hosts", "certificate", "identity", "proxy")
    }
    transport.update(
        identity_agent=str(tmp_path / "agent"),
        host_key_sha256="a" * 64,
        verification_basis="operator-verified",
        agent_protocol="native",
    )
    value = {
        "schema_version": 1,
        "kind": r.KIND,
        "host": "owned-host",
        "remote_parent": "/home/operator/.local/share/devsecops/runner-vm",
        "private_state_directory": str(tmp_path / "runner-private"),
        "tool_image": "sha256:" + "b" * 64,
        "boot_image": pin("runner.qcow2"),
        "ssh_pki": {name: pin("runner-" + name) for name in r.PKI_NAMES},
        "transport": transport,
        "resources": {"cpus": 2, "memory_mib": 8192, "disk_gib": 64},
        "guest": deepcopy(r.GUEST),
        "egress": {"external_origins": [], "external_ca_bundle": None},
        "cleanup": "remove-owned-runner-generation-preserve-inputs",
    }
    foundation = {
        "kind": "fresh-owned-tcg-v1",
        "host": value["host"],
        "transport": deepcopy(transport),
        "tool_image": value["tool_image"],
        "remote_parent": "/home/operator/.local/share/devsecops/fresh",
        "private_state_directory": str(tmp_path / "foundation-private"),
        "boot_image": pin("foundation.qcow2"),
        "ssh_pki": {name: pin("foundation-" + name) for name in r.PKI_NAMES},
    }
    return value, foundation


def test_closed_recipe_schema_and_complete_original_identity(tmp_path):
    value, foundation = selection(tmp_path)
    jsonschema.validate(value, r.schema())
    assert r.validate_against_foundation(value, foundation) == value
    run = "12345678-1234-1234-1234-123456789012"
    scope = {name: "c" * 64 for name in IDENTITY_KEYS}
    args = dict(
        run_id=run, environment_id="owned-demo", identities=scope, modules={"runner_vm_remote": "d" * 64}
    )
    first = r.binding(value, **args)
    assert len(first) == 64 and first == r.binding(deepcopy(value), **args)
    args["run_id"] = "12345678-1234-1234-1234-123456789013"
    assert first != r.binding(value, **args)
    paths = r.guest_paths(run)
    assert UUID(hex=Path(paths["runtime"]).name) == UUID(run)
    assert len(paths["runtime"].encode()) == 59
    assert paths["inputs"].startswith(r.HOME + "/.local/share/devsecops/")
    assert r.generation(value, run) == value["remote_parent"] + "/" + run


@pytest.mark.parametrize(
    "damage",
    [
        "schema-bool",
        "sudo",
        "root-user",
        "other-home",
        "uid",
        "resources-bool",
        "alias-pki",
        "relative-image",
        "mutable-tool",
        "private-asset",
        "origin",
        "duplicate-origin",
        "missing-ca",
        "unselected-ca",
        "unknown-field",
    ],
)
def test_unsupported_or_ambiguous_selections_fail_before_effects(tmp_path, damage):
    value, _ = selection(tmp_path)
    if damage == "schema-bool":
        value["schema_version"] = True
    elif damage == "sudo":
        value["guest"]["sudo"] = True
    elif damage == "root-user":
        value["guest"]["control_user"] = "root"
    elif damage == "other-home":
        value["guest"]["home"] = "/home/runner"
    elif damage == "uid":
        value["guest"]["runner_uid"] = 0
    elif damage == "resources-bool":
        value["resources"]["cpus"] = True
    elif damage == "alias-pki":
        value["ssh_pki"]["host_public"] = deepcopy(value["ssh_pki"]["operator_public"])
    elif damage == "relative-image":
        value["boot_image"]["path"] = "runner.qcow2"
    elif damage == "mutable-tool":
        value["tool_image"] = "tcg:latest"
    elif damage == "private-asset":
        value["boot_image"]["path"] = value["private_state_directory"] + "/image"
    elif damage == "origin":
        value["egress"]["external_origins"] = ["https://arbitrary.example"]
    elif damage == "duplicate-origin":
        value["egress"]["external_origins"] = [r.EXTERNAL_ORIGINS[0]] * 2
    elif damage == "missing-ca":
        value["egress"]["external_origins"] = [r.EXTERNAL_ORIGINS[0]]
    elif damage == "unselected-ca":
        value["egress"]["external_ca_bundle"] = {"path": "/supplied/ca.pem", "sha256": "e" * 64}
    else:
        value["qemu_args"] = ["--arbitrary"]
    with pytest.raises((ValueError, TypeError)):
        r.validate(value)


@pytest.mark.parametrize(
    "damage",
    [
        "image-path",
        "image-content",
        "operator-key",
        "host-key",
        "state",
        "generation",
        "transport",
        "host",
        "retained",
    ],
)
def test_rke2_identity_or_ownership_reuse_is_rejected(tmp_path, damage):
    value, foundation = selection(tmp_path)
    if damage == "image-path":
        value["boot_image"]["path"] = foundation["boot_image"]["path"]
    elif damage == "image-content":
        value["boot_image"]["sha256"] = foundation["boot_image"]["sha256"]
    elif damage == "operator-key":
        value["ssh_pki"]["operator_public"] = deepcopy(foundation["ssh_pki"]["operator_public"])
    elif damage == "host-key":
        value["ssh_pki"]["host_public"] = deepcopy(foundation["ssh_pki"]["host_public"])
    elif damage == "state":
        value["private_state_directory"] = foundation["private_state_directory"]
    elif damage == "generation":
        foundation["remote_parent"] = value["remote_parent"]
    elif damage == "transport":
        value["transport"]["host_key_sha256"] = "f" * 64
    elif damage == "host":
        value["host"] = "other-host"
    else:
        foundation["kind"] = "retained-tcg-v1"
    with pytest.raises(ValueError):
        r.validate_against_foundation(value, foundation)


def test_explicit_external_selection_has_no_system_trust_or_implicit_route(tmp_path):
    value, foundation = selection(tmp_path)
    value["egress"] = {
        "external_origins": list(r.EXTERNAL_ORIGINS),
        "external_ca_bundle": {"path": "/supplied/external-ca.pem", "sha256": "e" * 64},
    }
    assert r.validate_against_foundation(value, foundation)["egress"] == value["egress"]
    value["egress"]["external_origins"].reverse()
    with pytest.raises(ValueError):
        r.validate(value)


def test_packaged_schema_is_exact_generated_closed_contract():
    path = Path(r.__file__).resolve().parents[3] / "schemas/owned-runner-vm.schema.json"
    actual = json.loads(path.read_bytes())
    assert actual == {"$schema": "https://json-schema.org/draft/2020-12/schema", **r.schema()}
