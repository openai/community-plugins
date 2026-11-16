"""Strict, versioned operator inputs for the Software Factory lifecycle.

These contracts describe inputs, not evidence of successful execution. A valid
lock is never treated as proof that an image was authenticated or deployed.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
from datetime import datetime
import re
import stat
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from .fresh_foundation_recipe import schema as _fresh_foundation_schema


SCENARIO_ID = "ironbank-mission-recovery-v1"
PROFILES = ["bigbang-live", "bigbang-disconnected", "partybus-hosted", "openshift-app",
            "adapter-qualification", "replay"]
DIGEST = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
NAME = {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,62}$"}
ENV_REF = {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{0,127}$"}
ABS_PATH = {"type": "string", "pattern": "^/[^\\u0000\\r\\n]+$", "maxLength": 4096}
TEXT = {"type": "string", "minLength": 1, "maxLength": 256}
TOOL_NAMES = ["git", "helm", "kustomize", "kubeconform", "kubectl", "cosign", "syft", "grype", "codex"]
SERVICE_NAMES = ["gitlab", "argocd", "registry", "registry1", "prometheus", "kubernetes", "jenkins"]
MUTATIONS = ["acquire", "provision", "build", "promote", "inject", "deliver", "probe", "reset", "cleanup"]


def object_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties) if required is None else required}


def array(items: dict, *, minimum: int = 0, maximum: int = 128) -> dict:
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum, "uniqueItems": True}


def document(name: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:devsecops:{name}:1", **object_schema(properties, required)}


AUTH = {"oneOf": [
    object_schema({"mode": {"const": "none"}}),
    object_schema({"mode": {"const": "bearer"}, "token_env": ENV_REF}),
    object_schema({"mode": {"const": "basic"}, "username_env": ENV_REF, "password_env": ENV_REF}),
]}
SERVICE = object_schema({
    "id": {"enum": SERVICE_NAMES}, "origin": {"type": "string", "format": "uri", "maxLength": 2048},
    "read_auth": AUTH, "operator_auth": AUTH, "ca_file": ABS_PATH,
    "blob_download_policy": object_schema({"path": ABS_PATH, "sha256": DIGEST}),
    "attestation_size_policy": object_schema({"path": ABS_PATH, "sha256": DIGEST}),
    "attestation_size_policies": array(object_schema({"path": ABS_PATH, "sha256": DIGEST}), minimum=1, maximum=16),
    "scope": object_schema({"project_ids": array({"type": "integer", "minimum": 1}),
                            "namespaces": array(NAME), "applications": array(NAME), "argocd_projects": array(NAME),
                            "repositories": array(TEXT)}, []),
}, ["id", "origin", "read_auth", "scope"])
SERVICE["allOf"] = [{
    "if": {"required": [name]},
    "then": {"properties": {
        "id": {"enum": ["registry", "registry1"]},
        "scope": {"required": ["repositories"], "properties": {"repositories": {"minItems": 1}}},
    }},
} for name in ("blob_download_policy", "attestation_size_policy", "attestation_size_policies")]
SERVICE["allOf"].append({"not": {"required": ["attestation_size_policy", "attestation_size_policies"]}})

PINNED_FILE = object_schema({"path": ABS_PATH, "sha256": DIGEST})
FOUNDATION_TRANSPORT = object_schema({
    **{name: PINNED_FILE for name in ("config", "known_hosts", "certificate", "identity", "proxy")},
    "identity_agent": ABS_PATH, "host_key_sha256": DIGEST,
    "verification_basis": {"enum": ["authenticated-control-plane", "operator-verified"]},
    "agent_protocol": {"enum": ["native", "certificate-view-v1"]},
}, ["config", "known_hosts", "certificate", "identity", "proxy", "identity_agent",
    "host_key_sha256", "verification_basis"])
RETAINED_FOUNDATION = object_schema({
    "kind": {"const": "retained-tcg-v1"}, "host": TEXT, "remote_lab": ABS_PATH,
    "private_state_directory": ABS_PATH,
    "tool_image": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"},
    "provision_run_id": TEXT, "original_ca_sha256": DIGEST,
    "source_receipt": PINNED_FILE, "transport": FOUNDATION_TRANSPORT,
    "retained_dependencies": {"const": "preserve"},
})
FRESH_FOUNDATION = _fresh_foundation_schema()
FOUNDATION = {"oneOf": [RETAINED_FOUNDATION, FRESH_FOUNDATION]}

CONFIG_SCHEMA = document("factory-config", {
    "schema_version": {"const": 1}, "profile": {"enum": PROFILES}, "environment": NAME,
    "owner": TEXT, "expires_at": {"type": "string", "format": "date-time"},
    "allowed_mutations": array({"enum": MUTATIONS}),
    "runtime": object_schema({
        "driver": {"const": "preprovisioned-linux"}, "os": {"const": "linux"},
        "architecture": {"enum": ["amd64", "arm64"]},
        "nodes": array(object_schema({"id": NAME, "address": TEXT,
                                      "roles": array({"enum": ["server", "agent", "runner", "services"]}, minimum=1)}),
                       minimum=1, maximum=32),
        "foundation": FOUNDATION,
    }, ["driver", "os", "architecture", "nodes"]),
    "services": array(SERVICE, minimum=1, maximum=16),
    "tool_paths": object_schema({name: ABS_PATH for name in TOOL_NAMES}, []),
})

IMAGE = object_schema({
    "repository": {"type": "string", "pattern": "^registry1\\.dso\\.mil/ironbank/[a-z0-9._/-]+$"},
    "manifest_sha256": DIGEST, "platform_manifest_sha256": DIGEST, "config_sha256": DIGEST,
    "os": {"const": "linux"}, "architecture": {"enum": ["amd64", "arm64"]},
    "required_predicates": array(TEXT, minimum=1), "trust_sha256": DIGEST,
})
LOCK_SCHEMA = document("factory-lock", {
    "schema_version": {"const": 1}, "profile": {"enum": PROFILES}, "scenario_id": {"const": SCENARIO_ID},
    "scenario_sha256": DIGEST, "assertions_sha256": DIGEST,
    "platform": object_schema({"os": {"const": "linux"}, "architecture": {"enum": ["amd64", "arm64"]},
                               "distribution": TEXT, "distribution_version": TEXT,
                               "kubernetes_version": TEXT, "bigbang_version": TEXT,
                               "flux_version": TEXT, "argo_version": TEXT, "cni": TEXT, "mesh_mode": TEXT}),
    "tools": array(object_schema({"name": {"enum": TOOL_NAMES}, "version": TEXT, "sha256": DIGEST}), minimum=1),
    "images": object_schema({"reference_web": IMAGE, "mission_base": IMAGE}),
    "freshness_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
    "observation_window_seconds": {"type": "integer", "minimum": 2, "maximum": 3600},
    "scrape_interval_seconds": {"type": "integer", "minimum": 1, "maximum": 300},
    "required_fresh_scrapes": {"type": "integer", "minimum": 3, "maximum": 20},
})

PHASE = object_schema({
    "id": NAME, "operation": {"enum": ["preflight", "prepare", "up", "build-promote", "baseline", "inject",
        "confirm-fault", "native", "validate-correction", "deliver", "probe", "verify", "case", "reset", "export", "down", "finalize"]},
    "depends_on": array(NAME, maximum=8), "attempt": {"enum": ["primary", "replay", "shared"]},
    "case_ids": array({"enum": ["D1", "D2", "D3", "D4", "D5", "D6"]}),
    "workflow": {"enum": ["review-kubernetes-change", "diagnose-gitops-deployment", "prepare-gitops-correction", "verify-gitops-recovery"]},
    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 7200},
}, ["id", "operation", "depends_on", "attempt", "case_ids", "timeout_seconds"])
SCENARIO_SCHEMA = document("factory-scenario", {
    "schema_version": {"const": 1}, "scenario_id": {"const": SCENARIO_ID},
    "primary_profile": {"const": "bigbang-live"}, "required_cases": {"const": ["D1", "D2", "D3", "D4", "D5", "D6"]},
    "phases": array(PHASE, minimum=1, maximum=100),
})
ASSERTIONS_SCHEMA = document("scenario-assertions", {
    "schema_version": {"const": 1}, "scenario_id": {"const": SCENARIO_ID},
    "acceptance": {"type": "string", "minLength": 1, "maxLength": 1024},
    "cases": object_schema({case: array({"type": "string", "pattern": "^[a-z][a-z0-9-]{0,127}$"}, minimum=1)
                            for case in ("D1", "D2", "D3", "D4", "D5", "D6")}),
})
SCHEMAS = {"factory-config": CONFIG_SCHEMA, "factory-lock": LOCK_SCHEMA,
           "factory-scenario": SCENARIO_SCHEMA, "scenario-assertions": ASSERTIONS_SCHEMA}


def validate_document(value: dict, kind: str) -> None:
    schema = SCHEMAS[kind]
    if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(value):
        raise ValueError(f"Input does not satisfy the {kind} schema version 1")
    if kind == "factory-scenario":
        validate_graph(value)
    elif kind == "factory-config":
        foundation = value["runtime"].get("foundation")
        if foundation is not None and foundation.get("kind") == "fresh-owned-tcg-v1":
            from .fresh_foundation_recipe import validate as validate_fresh_foundation
            validate_fresh_foundation(foundation)
            if value["runtime"]["architecture"] != "amd64":
                raise ValueError("Fresh owned foundation requires the pinned amd64 assets")
        expiry = value["expires_at"]
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", expiry):
                raise ValueError
            if datetime.fromisoformat(expiry.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError
        except ValueError:
            raise ValueError("Environment expiry must be a valid timezone-aware timestamp") from None
        for items in (value["services"], value["runtime"]["nodes"]):
            if len({item["id"] for item in items}) != len(items):
                raise ValueError("Configuration contains duplicate identities")
        for service in value["services"]:
            for name in ("blob_download_policy", "attestation_size_policy"):
                if name not in service:
                    continue
                if service["id"] not in {"registry", "registry1"} or not service["scope"].get("repositories"):
                    raise ValueError("Registry policy is restricted to explicitly scoped registries")
                if ".." in Path(service[name]["path"]).parts:
                    raise ValueError("Registry policy path cannot traverse parents")
            policies = service.get("attestation_size_policies", [])
            if (any(".." in Path(ref["path"]).parts for ref in policies)
                    or len({ref["path"] for ref in policies}) != len(policies)):
                raise ValueError("Registry attestation policies require distinct canonical paths")
            try:
                origin = urlsplit(service["origin"])
                port = origin.port
            except ValueError:
                raise ValueError("Service origin is invalid") from None
            if (origin.scheme != "https" or not origin.hostname
                    or origin.username is not None or origin.password is not None
                    or origin.path not in {"", "/"} or origin.query or origin.fragment
                    or port == 0 or any(c.isspace() for c in service["origin"])):
                raise ValueError("Services require credential-free HTTPS origins without queries or paths")
            host = origin.hostname
            try:
                address = ipaddress.ip_address(host)
                authority = f"[{host}]" if address.version == 6 else host
            except ValueError:
                if (len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                             for label in host.rstrip(".").split("."))
                        or all(part.isdigit() for part in host.rstrip(".").split("."))):
                    raise ValueError("Service hostname is invalid") from None
                authority = host
            if origin.netloc.lower() != authority + (f":{port}" if port is not None else ""):
                raise ValueError("Service authority or port is invalid")
    elif kind == "factory-lock":
        window, interval = value['observation_window_seconds'], value['scrape_interval_seconds']
        points = window // interval + 1
        if (window % interval or window < 2 * interval or points > 241
                or value['required_fresh_scrapes'] > points or interval > value['freshness_seconds']):
            raise ValueError('Lock observation window, scrape interval and freshness bounds are inconsistent')
        if len({tool["name"] for tool in value["tools"]}) != len(value["tools"]):
            raise ValueError("Lock contains duplicate tool identities")
        if value["images"]["reference_web"]["repository"] != "registry1.dso.mil/ironbank/opensource/nginx/nginx":
            raise ValueError("Canonical scenario requires the unchanged Iron Bank NGINX repository")
        for image in value["images"].values():
            if image["architecture"] != value["platform"]["architecture"]:
                raise ValueError("Image and platform architecture differ")
        versions = [tool["version"] for tool in value["tools"]]
        versions += [v for k, v in value["platform"].items() if k.endswith("version")]
        if any(v.lower() in {"latest", "main", "master", "unknown", "todo", "tbd"} for v in versions):
            raise ValueError("Live lock contains an unresolved version")


def validate_graph(scenario: dict) -> None:
    seen = set()
    previous = None
    for phase in scenario["phases"]:
        if phase["id"] in seen or not set(phase["depends_on"]).issubset(seen):
            raise ValueError("Scenario phases must have unique identities and earlier dependencies")
        if phase["depends_on"] != ([previous] if previous else []):
            raise ValueError("Canonical phases must preserve the ordered predecessor chain")
        if (phase["operation"] == "native") != ("workflow" in phase):
            raise ValueError("Only native phases must name a workflow")
        if phase["attempt"] == "replay" and not set(phase["case_ids"]).issubset({"D1", "D2"}):
            raise ValueError("Replay may execute only the D1 and D2 cases")
        seen.add(phase["id"])
        previous = phase["id"]
    operations = [phase["operation"] for phase in scenario["phases"]]
    if operations[0] != "preflight" or operations[-3:] != ["export", "down", "finalize"]:
        raise ValueError("Scenario must preflight first and export, clean up, then finalize")
    if operations.count("reset") != 1 or operations.count("finalize") != 1:
        raise ValueError("Canonical scenario requires one bounded reset and one finalization")
    app_operations = ["baseline", "inject", "confirm-fault", "native", "native", "native",
                      "validate-correction", "deliver", "probe", "native", "verify"]
    workflows = ["review-kubernetes-change", "diagnose-gitops-deployment",
                 "prepare-gitops-correction", "verify-gitops-recovery"]
    for attempt in ("primary", "replay"):
        selected = [phase for phase in scenario["phases"] if phase["attempt"] == attempt]
        if ([p["operation"] for p in selected] != app_operations
                or [p["workflow"] for p in selected if p["operation"] == "native"] != workflows):
            raise ValueError("Each application attempt must preserve every required native and deterministic phase")
        for phase in selected:
            expected_cases = (["D1"] if phase["operation"] == "baseline" else
                              ["D1", "D2"] if phase["operation"] == "verify" else ["D2"])
            if phase["case_ids"] != expected_cases:
                raise ValueError("Application phases must retain their exact D1 and D2 assertion bindings")
    expected = (["preflight", "prepare", "up", "build-promote"] + app_operations
                + ["case", "case", "case", "reset"] + app_operations + ["export", "down", "finalize"])
    if operations != expected:
        raise ValueError("Canonical graph is missing, reordering, or duplicating a required phase")
    cases = [p["case_ids"] for p in scenario["phases"] if p["operation"] == "case"]
    if cases != [["D3"], ["D4"], ["D5"]]:
        raise ValueError("Canonical graph must execute the isolated D3, D4 and D5 cases")
    for phase in scenario["phases"]:
        if phase["attempt"] != "shared" or phase["operation"] == "case":
            continue
        expected_cases = (["D1", "D2", "D3", "D4", "D5", "D6"] if phase["operation"] == "finalize" else
                          ["D6"] if phase["operation"] in {"reset", "export", "down"} else [])
        if phase["case_ids"] != expected_cases:
            raise ValueError("Shared phases must retain their exact lifecycle assertion bindings")


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON input contains duplicate keys")
        value[key] = item
    return value


def _invalid_constant(_value):
    raise ValueError("JSON input contains a non-finite number")


def read_json(path: Path, *, limit: int = 1_000_000) -> tuple[dict, str]:
    """Read bounded ordinary files without following symlinks or echoing content."""
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Input paths must not contain symlinks")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
                raise ValueError("Input must be a bounded regular file")
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("Input size limit exceeded")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_invalid_constant)
        if not isinstance(value, dict):
            raise ValueError("Input must be a JSON object")
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("JSON input is unavailable or invalid") from None
    return value, hashlib.sha256(raw).hexdigest()


def load_document(path: Path, kind: str) -> tuple[dict, str]:
    value, digest = read_json(path)
    validate_document(value, kind)
    return value, digest
