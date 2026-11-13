"""Closed disposable nonroot runner VM selection; no acquisition or commands.

This module is standalone so the exact packaged source can validate the fixed
remote request. A selection is data, never a grant or a restored VM capability.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID

KIND = "owned-runner-vm-v1"
USER = "gitlab-runner"
HOME = "/home/gitlab-runner"
UID = GID = 10001
PKI_NAMES = ("operator_private", "operator_public", "host_private", "host_public")
EXTERNAL_ORIGINS = ("https://registry1.dso.mil", "https://p1docs.dso.mil")
STORAGE_ORIGIN = "https://ib-prod-harbor-storage.s3.us-gov-west-1.amazonaws.com"
STORAGE_ROLE = "registry1-storage"
STORAGE_ADDRESS = "10.0.2.104"
GUEST = {
    "os": "linux",
    "architecture": "amd64",
    "control_user": USER,
    "runner_user": USER,
    "home": HOME,
    "runner_uid": UID,
    "runner_gid": GID,
    "runner_version": "19.5.1",
    "python_major": 3,
    "python_min_minor": 11,
    "subid_count": 65536,
}


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _shape(value, fields):
    if type(value) is not dict or set(value) != set(fields.split()):
        raise ValueError("Unsupported closed runner-VM fields")


def _path(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"/[^\x00-\x20,]{1,4000}", value)
        or any(part in (".", "..", "") for part in value.split("/")[1:])
    ):
        raise ValueError("Runner-VM paths must be explicit and canonical")
    return Path(value)


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value):
        raise ValueError("Runner-VM requires exact material hashes")


def _pin(value, *, maximum=4 * 1024**3, size=True):
    _shape(value, "path sha256 bytes" if size else "path sha256")
    _path(value["path"])
    _digest(value["sha256"])
    if size and (type(value["bytes"]) is not int or not 0 < value["bytes"] <= maximum):
        raise ValueError("Runner-VM material exceeds its bounded selection")


def _overlap(left, right):
    return left.is_relative_to(right) or right.is_relative_to(left)


def blob_policy_document(raw, expected_sha256):
    """Validate exact policy bytes for the closed Platform One VM profile."""
    _digest(expected_sha256)
    if type(raw) is not bytes or not 1 <= len(raw) <= 16384 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Runner blob policy differs from its explicit pin")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate runner blob policy field")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    _shape(value, "schema_version kind registry_origin repositories storage_origins")
    repositories = value["repositories"]
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["kind"] != "registry-blob-download-policy"
            or value["registry_origin"] != EXTERNAL_ORIGINS[0]
            or value["storage_origins"] != [STORAGE_ORIGIN]
            or type(repositories) is not list or not 1 <= len(repositories) <= 32
            or any(type(name) is not str or len(name) > 255 or not re.fullmatch(
                r'[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*(?:/[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*)*', name)
                for name in repositories)
            or len(set(repositories)) != len(repositories)):
        raise ValueError("Runner blob policy differs from its closed Platform One profile")
    return value


def validate(value):
    optional = " acceleration" if type(value) is dict and "acceleration" in value else ""
    _shape(
        value,
        "schema_version kind host remote_parent private_state_directory tool_image boot_image ssh_pki transport resources guest egress cleanup" + optional,
    )
    if optional:
        acceleration = value["acceleration"]
        _shape(acceleration, "kind device_group")
        if (acceleration["kind"] != "kvm" or type(acceleration["device_group"]) is not int
                or not 1 <= acceleration["device_group"] <= 2**31 - 1):
            raise ValueError("Runner KVM selection requires its explicit nonroot device group")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != KIND
        or value["cleanup"] != "remove-owned-runner-generation-preserve-inputs"
    ):
        raise ValueError("Runner-VM recipe or cleanup version is unsupported")
    if not isinstance(value["host"], str) or not re.fullmatch(
        "[A-Za-z0-9][A-Za-z0-9._-]{0,100}", value["host"]
    ):
        raise ValueError("Runner-VM host identity is unsupported")
    if not isinstance(value["remote_parent"], str) or not re.fullmatch(
        r"/home/[A-Za-z0-9_-]+/\.local/share/devsecops/runner-vm", value["remote_parent"]
    ):
        raise ValueError("Runner VM requires its separate disposable parent")
    private = _path(value["private_state_directory"])
    parent = Path(value["remote_parent"])
    if _overlap(private, parent):
        raise ValueError("Runner local state and remote generation must differ")
    if not isinstance(value["tool_image"], str) or not re.fullmatch(
        "sha256:[a-f0-9]{64}", value["tool_image"]
    ):
        raise ValueError("Runner QEMU tool image requires an existing exact image ID")
    _pin(value["boot_image"], maximum=64 * 1024**3)
    _shape(value["ssh_pki"], " ".join(PKI_NAMES))
    for pin in value["ssh_pki"].values():
        _pin(pin, maximum=16384)
    transport = value["transport"]
    _shape(
        transport,
        "config known_hosts certificate identity proxy identity_agent host_key_sha256 verification_basis agent_protocol",
    )
    if transport["verification_basis"] not in (
        "operator-verified",
        "authenticated-control-plane",
    ) or transport["agent_protocol"] not in ("native", "certificate-view-v1"):
        raise ValueError("Runner host transport needs independently verified original identity")
    _digest(transport["host_key_sha256"])
    _path(transport["identity_agent"])
    for name in ("config", "known_hosts", "certificate", "identity", "proxy"):
        _pin(transport[name], size=False)
        if _overlap(_path(transport[name]["path"]), private) or _overlap(
            _path(transport[name]["path"]), parent
        ):
            raise ValueError("Runner transport inputs must be preserved outside generated state")
    _shape(value["resources"], "cpus memory_mib disk_gib")
    for name, bounds in {"cpus": (2, 16), "memory_mib": (4096, 65536), "disk_gib": (32, 512)}.items():
        item = value["resources"][name]
        if type(item) is not int or not bounds[0] <= item <= bounds[1]:
            raise ValueError("Runner resources require an explicit bounded selection")
    _shape(value["guest"], " ".join(GUEST))
    if any(
        type(value["guest"][name]) is not type(expected) or value["guest"][name] != expected
        for name, expected in GUEST.items()
    ):
        raise ValueError("Only the fixed separate nonroot runner account contract is supported")
    egress_fields = "external_origins external_ca_bundle"
    if type(value["egress"]) is dict and "blob_download_policy" in value["egress"]:
        egress_fields += " blob_download_policy"
    _shape(value["egress"], egress_fields)
    policy = value["egress"].get("blob_download_policy")
    if policy is not None:
        _pin(policy, size=False)
    elif "blob_download_policy" in value["egress"]:
        raise ValueError("Absent blob policy must not create a storage selection")
    origins = value["egress"]["external_origins"]
    allowed_origins = (*EXTERNAL_ORIGINS, *([STORAGE_ORIGIN] if policy else []))
    if (
        type(origins) is not list
        or any(type(item) is not str for item in origins)
        or origins != [item for item in allowed_origins if item in origins]
        or policy is not None and (STORAGE_ORIGIN not in origins or EXTERNAL_ORIGINS[0] not in origins)
    ):
        raise ValueError("Runner external origin selection must be exact, unique and closed")
    external = value["egress"]["external_ca_bundle"]
    if origins:
        _pin(external, size=False)
    elif external is not None:
        raise ValueError("Absent external routes cannot carry an unbound trust selection")
    pins = [value["boot_image"], *value["ssh_pki"].values(), *([external] if external else []), *([policy] if policy else [])]
    if len({pin["path"] for pin in pins}) != len(pins):
        raise ValueError("Runner asset paths cannot alias")
    if len({pin["sha256"] for pin in value["ssh_pki"].values()}) != len(PKI_NAMES):
        raise ValueError("Runner operator and host keys must be independently selected")
    for pin in pins:
        selected = _path(pin["path"])
        if _overlap(selected, parent) or _overlap(selected, private):
            raise ValueError("Supplied runner materials must remain outside disposable state")
    return deepcopy(value)


def validate_against_foundation(value, foundation):
    """Reject reuse of the RKE2 guest; retain only its original host transport."""
    selected = validate(value)
    if not isinstance(foundation, dict) or foundation.get("kind") != "fresh-owned-tcg-v1":
        raise ValueError("Dedicated runner VM requires the original fresh owned host boundary")
    if any(selected[name] != foundation.get(name) for name in ("host", "transport", "tool_image")):
        raise ValueError("Runner must use the same original owned host transport and QEMU tool image")
    if selected.get("acceleration") != foundation.get("acceleration"):
        raise ValueError("Runner and foundation must select the same explicit acceleration boundary")
    for field in ("remote_parent", "private_state_directory"):
        for original in ("remote_parent", "private_state_directory"):
            if _overlap(_path(selected[field]), _path(foundation[original])):
                raise ValueError("Runner VM and RKE2 disposable directories must be distinct")
    original_pins = [foundation["boot_image"], *foundation["ssh_pki"].values()]
    for pin in [selected["boot_image"], *selected["ssh_pki"].values()]:
        if any(
            pin["path"] == original["path"] or pin["sha256"] == original["sha256"]
            for original in original_pins
        ):
            raise ValueError("Runner image and SSH identities must be distinct from RKE2")
        for field in ("remote_parent", "private_state_directory"):
            if _overlap(_path(pin["path"]), _path(foundation[field])):
                raise ValueError("Runner inputs cannot live in a disposable RKE2 generation")
    return selected


def _run_id(run_id):
    if not isinstance(run_id, str) or str(UUID(run_id)) != run_id:
        raise ValueError("Runner VM requires the original canonical UUID")
    return run_id


def generation(value, run_id):
    validate(value)
    return str(Path(value["remote_parent"]) / _run_id(run_id))


def guest_paths(run_id):
    identity = UUID(_run_id(run_id)).hex
    private = HOME + "/.local/share/devsecops/" + identity
    return {
        "private": private,
        "inputs": private + "/inputs",
        "output": HOME + "/builds/factory-" + run_id + "/publication",
        "runtime": HOME + "/builds/" + identity,
    }


def binding(value, *, run_id, environment_id, identities, modules):
    validate(value)
    _run_id(run_id)
    keys = {
        name + "_sha256" for name in ("scenario", "assertions", "config", "lock", "package", "environment")
    }
    if (
        not isinstance(environment_id, str)
        or not re.fullmatch("[a-z][a-z0-9-]{0,62}", environment_id)
        or type(identities) is not dict
        or set(identities) != keys
        or type(modules) is not dict
        or not modules
    ):
        raise ValueError("Runner binding needs exact environment, run and packaged module identities")
    for digest in (*identities.values(), *modules.values()):
        _digest(digest)
    if any(not isinstance(name, str) or not re.fullmatch("[A-Za-z0-9_.-]{1,100}", name) for name in modules):
        raise ValueError("Runner package module identity is invalid")
    return hashlib.sha256(
        canonical(
            {
                "recipe": value,
                "run_id": run_id,
                "environment_id": environment_id,
                "identities": identities,
                "modules": modules,
            }
        )
    ).hexdigest()


def schema():
    def obj(properties):
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    path = {"type": "string", "pattern": r"^/[^\x00-\x20,]{1,4000}$"}
    digest = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
    pin = obj({"path": path, "sha256": digest})

    def sized(maximum):
        return obj(
            {"path": path, "sha256": digest, "bytes": {"type": "integer", "minimum": 1, "maximum": maximum}}
        )

    transport = obj(
        {
            **{name: pin for name in ("config", "known_hosts", "certificate", "identity", "proxy")},
            "identity_agent": path,
            "host_key_sha256": digest,
            "verification_basis": {"enum": ["operator-verified", "authenticated-control-plane"]},
            "agent_protocol": {"enum": ["native", "certificate-view-v1"]},
        }
    )
    egress = obj({
        "external_origins": {"type": "array", "items": {"enum": [*EXTERNAL_ORIGINS, STORAGE_ORIGIN]},
                             "uniqueItems": True, "maxItems": 3},
        "external_ca_bundle": {"oneOf": [{"type": "null"}, pin]},
    })
    egress["properties"]["blob_download_policy"] = pin
    egress["allOf"] = [{
        "if": {"required": ["blob_download_policy"]},
        "then": {"properties": {"external_origins": {"allOf": [
            {"contains": {"const": EXTERNAL_ORIGINS[0]}}, {"contains": {"const": STORAGE_ORIGIN}}]}}},
        "else": {"properties": {"external_origins": {"items": {"enum": list(EXTERNAL_ORIGINS)}}}},
    }]
    result = obj(
        {
            "schema_version": {"const": 1},
            "kind": {"const": KIND},
            "host": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$"},
            "remote_parent": {
                "type": "string",
                "pattern": r"^/home/[A-Za-z0-9_-]+/\.local/share/devsecops/runner-vm$",
            },
            "private_state_directory": path,
            "tool_image": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"},
            "boot_image": sized(64 * 1024**3),
            "ssh_pki": obj({name: sized(16384) for name in PKI_NAMES}),
            "transport": transport,
            "resources": obj(
                {
                    name: {"type": "integer", "minimum": bounds[0], "maximum": bounds[1]}
                    for name, bounds in {
                        "cpus": (2, 16),
                        "memory_mib": (4096, 65536),
                        "disk_gib": (32, 512),
                    }.items()
                }
            ),
            "guest": obj({name: {"const": value} for name, value in GUEST.items()}),
            "egress": egress,
            "cleanup": {"const": "remove-owned-runner-generation-preserve-inputs"},
        }
    )
    result["properties"]["acceleration"] = obj({
        "kind": {"const": "kvm"},
        "device_group": {"type": "integer", "minimum": 1, "maximum": 2**31 - 1},
    })
    return result
