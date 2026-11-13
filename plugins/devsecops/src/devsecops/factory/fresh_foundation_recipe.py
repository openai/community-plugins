"""Closed fresh-owned QEMU inputs. No acquisition, disk adoption or commands."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

VERSION = "v1.36.5+rke2r1"
BOOT_SHA256 = "04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94"
ASSETS = {
    "rke2-images.linux-amd64.tar.zst": (
        806932038,
        "5af963b4a2cbb553dbf54bae5d884ce48562af808883e627ecc5d179408949f3",
    ),
    "rke2-images.linux-amd64.txt": (950, "b501891895be418ea65e3d338b0b984f83bb4641a5fbb081ddd7d982ecd6cd96"),
    "rke2.linux-amd64.tar.gz": (41238340, "4fdbe8a8a32517fbcb6e80c71ca393b96828fbb87685102d9058b51527f9278d"),
    "sha256sum-amd64.txt": (3558, "75dd00b1f228c63cf101744738660843468874bbd7b354fa8329fd5f80b053ce"),
    "install.sh": (26731, "42983c86d1da64a92061d83afb57630cedd69241989f1b0673f3db6c3d92ee6b"),
}
PKI_NAMES = ("operator_private", "operator_public", "host_private", "host_public")


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def _shape(value, fields):
    if type(value) is not dict or set(value) != set(fields.split()):
        raise ValueError("Unsupported closed fresh-foundation fields")


def _path(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"/[^\x00-\x20,]{1,4000}", value)
        or any(part in (".", "..", "") for part in value.split("/")[1:])
    ):
        raise ValueError("Fresh-foundation paths must be explicit and canonical")
    return Path(value)


def _pin(value, *, private=False):
    _shape(value, "path sha256 bytes")
    _path(value["path"])
    if (
        not isinstance(value["sha256"], str)
        or not re.fullmatch("[a-f0-9]{64}", value["sha256"])
        or type(value["bytes"]) is not int
        or not 0 < value["bytes"] <= (16384 if private else 4 * 1024**3)
    ):
        raise ValueError("Fresh-foundation asset pin is unsupported")


def validate(value):
    optional = " acceleration" if type(value) is dict and "acceleration" in value else ""
    _shape(
        value,
        "kind host remote_parent private_state_directory tool_image boot_image ssh_pki rke2_assets transport cleanup" + optional,
    )
    if optional:
        acceleration = value["acceleration"]
        _shape(acceleration, "kind device_group")
        if (acceleration["kind"] != "kvm" or type(acceleration["device_group"]) is not int
                or not 1 <= acceleration["device_group"] <= 2**31 - 1):
            raise ValueError("Fresh KVM selection requires its explicit nonroot device group")
    if value["kind"] != "fresh-owned-tcg-v1" or value["cleanup"] != "remove-owned-generation-preserve-inputs":
        raise ValueError("Fresh-foundation version or cleanup disposition is unsupported")
    if not isinstance(value["host"], str) or not re.fullmatch(
        "[A-Za-z0-9][A-Za-z0-9._-]{0,100}", value["host"]
    ):
        raise ValueError("Fresh-foundation host identity is unsupported")
    if not isinstance(value["remote_parent"], str) or not re.fullmatch(
        r"/home/[A-Za-z0-9_-]+/\.local/share/devsecops/fresh", value["remote_parent"]
    ):
        raise ValueError("Fresh-foundation requires its separate private generation parent")
    private = _path(value["private_state_directory"])
    if not isinstance(value["tool_image"], str) or not re.fullmatch(
        "sha256:[a-f0-9]{64}", value["tool_image"]
    ):
        raise ValueError("Fresh-foundation tool image must be an existing immutable image ID")
    _pin(value["boot_image"])
    if value["boot_image"]["sha256"] != BOOT_SHA256:
        raise ValueError("Fresh boot source differs from the reviewed Ubuntu cloud-image pin")
    _shape(value["ssh_pki"], " ".join(PKI_NAMES))
    for pin in value["ssh_pki"].values():
        _pin(pin, private=True)
    _shape(value["rke2_assets"], " ".join(ASSETS))
    for name, (size, digest) in ASSETS.items():
        pin = value["rke2_assets"][name]
        _pin(pin)
        if (pin["bytes"], pin["sha256"]) != (size, digest):
            raise ValueError("Fresh RKE2 input differs from the installed reviewed release")
    transport = value["transport"]
    _shape(
        transport,
        "config known_hosts certificate identity proxy identity_agent host_key_sha256 verification_basis agent_protocol",
    )
    if (
        transport["verification_basis"] not in ("operator-verified", "authenticated-control-plane")
        or transport["agent_protocol"] not in ("native", "certificate-view-v1")
        or not isinstance(transport["host_key_sha256"], str)
        or not re.fullmatch("[a-f0-9]{64}", transport["host_key_sha256"])
    ):
        raise ValueError("Fresh SSH transport requires independently verified pinned identity")
    _path(transport["identity_agent"])
    for name in ("config", "known_hosts", "certificate", "identity", "proxy"):
        ref = transport[name]
        _shape(ref, "path sha256")
        _path(ref["path"])
        if not isinstance(ref["sha256"], str) or not re.fullmatch("[a-f0-9]{64}", ref["sha256"]):
            raise ValueError("Fresh SSH artifact hash is unsupported")
    for name in ("config", "known_hosts", "certificate", "identity", "proxy"):
        if Path(transport[name]["path"]).is_relative_to(private):
            raise ValueError("Transport inputs must remain outside private generated state")
    pins = [value["boot_image"], *value["rke2_assets"].values(), *value["ssh_pki"].values()]
    if len({item["path"] for item in pins}) != len(pins):
        raise ValueError("Fresh input assets cannot alias one another")
    parent = Path(value["remote_parent"])
    for pin in pins:
        selected = Path(pin["path"])
        if (
            selected.is_relative_to(parent)
            or parent.is_relative_to(selected)
            or selected.is_relative_to(private)
        ):
            raise ValueError("Supplied assets must remain outside every disposable generation")
    return deepcopy(value)


def binding(value, *, run_id, environment_id, identities, modules):
    validate(value)
    if (
        not re.fullmatch("[a-f0-9-]{36}", run_id)
        or not re.fullmatch("[a-z][a-z0-9-]{0,62}", environment_id)
        or set(identities)
        != {
            name + "_sha256"
            for name in ("scenario", "assertions", "config", "lock", "package", "environment")
        }
        or any(not re.fullmatch("[a-f0-9]{64}", digest) for digest in identities.values())
    ):
        raise ValueError("Fresh generation requires the complete immutable run identity")
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


def generation(value, run_id):
    validate(value)
    if not re.fullmatch("[a-f0-9-]{36}", run_id):
        raise ValueError("Fresh generation requires a run UUID")
    return str(Path(value["remote_parent"]) / run_id)


def schema():
    """Proposed closed runtime.foundation branch for root-owned schema merge."""

    def obj(properties):
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    path = {"type": "string", "pattern": r"^/[^\x00-\x20,]{1,4000}$"}
    digest = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
    pin = obj(
        {"path": path, "sha256": digest, "bytes": {"type": "integer", "minimum": 1, "maximum": 4 * 1024**3}}
    )
    short_pin = obj({"path": path, "sha256": digest})
    transport = obj(
        {
            **{name: short_pin for name in ("config", "known_hosts", "certificate", "identity", "proxy")},
            "identity_agent": path,
            "host_key_sha256": digest,
            "verification_basis": {"enum": ["operator-verified", "authenticated-control-plane"]},
            "agent_protocol": {"enum": ["native", "certificate-view-v1"]},
        }
    )
    result = obj(
        {
            "kind": {"const": "fresh-owned-tcg-v1"},
            "host": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$"},
            "remote_parent": {
                "type": "string",
                "pattern": r"^/home/[A-Za-z0-9_-]+/\.local/share/devsecops/fresh$",
            },
            "private_state_directory": path,
            "tool_image": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"},
            "boot_image": pin,
            "ssh_pki": obj({name: pin for name in PKI_NAMES}),
            "rke2_assets": obj({name: pin for name in ASSETS}),
            "transport": transport,
            "cleanup": {"const": "remove-owned-generation-preserve-inputs"},
        }
    )
    result["properties"]["acceleration"] = obj({
        "kind": {"const": "kvm"},
        "device_group": {"type": "integer", "minimum": 1, "maximum": 2**31 - 1},
    })
    return result
