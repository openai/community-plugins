"""Freeze explicit operator inputs without treating integrity as authorization.

Paths are explicit Python arguments, never followed from context or receipt JSON. In-memory
documents have canonical byte identities, distinct from file-byte identities.
The unkeyed seals detect changed inputs; they are not signatures or trust grants.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

from devsecops.factory.contracts import (
    CONFIG_SCHEMA, LOCK_SCHEMA, DIGEST, NAME, array, document, object_schema,
    read_json, validate_document,
)

UTC = timezone.utc
MAX_DOCUMENT = 1_000_000
MAX_BUNDLE = 16_000_000
CANONICALIZATION = "devsecops-runtime-json-sha256-v1"
SPEC_DIGEST_FORMAT = "devsecops-json-sha256-v1"
DATE = {"type": "string", "format": "date-time"}
IDENTIFIER = {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}
REVISION = {"type": "string", "pattern": "^(?:[a-f0-9]{40}|[a-f0-9]{64})$"}
REF = object_schema({"id": NAME, "sha256": DIGEST})
IDENTITIES = object_schema({key: DIGEST for key in (
    "scenario_sha256", "assertions_sha256", "package_sha256", "lock_sha256", "config_sha256", "environment_sha256")})
SOURCE = object_schema({
    "position": {"type": "integer", "minimum": 1, "maximum": 32},
    "repository_url": {"type": "string", "maxLength": 2048}, "revision": REVISION,
    "path": {"type": "string", "maxLength": 1024},
    "renderer": {"enum": ["helm", "kustomize", "manifests", "values"]},
    "source_spec_sha256": DIGEST, "rendered_artifact_sha256": DIGEST, "intent_receipt": REF,
}, ["position", "repository_url", "revision", "path", "renderer"])
TARGET = object_schema({
    "id": NAME, "roles": array(NAME, minimum=1, maximum=8),
    "kubernetes": object_schema({
        "service_id": {"const": "kubernetes"}, "namespace": NAME, "deployment": NAME, "service": NAME,
        "deployment_uid": IDENTIFIER, "generation": {"type": "integer", "minimum": 1}, "service_uid": IDENTIFIER,
    }, ["service_id", "namespace", "deployment", "service"]),
    "argocd": object_schema({
        "service_id": {"const": "argocd"}, "project": NAME, "application_namespace": NAME,
        "application": NAME, "sources": array(SOURCE, minimum=1, maximum=32),
        "destination": object_schema({"server": {"type": "string", "maxLength": 2048}, "namespace": NAME}),
        "destination_spec_sha256": DIGEST, "intent_receipt": REF,
    }, ["service_id", "project", "application_namespace", "application", "sources", "destination"]),
    "image_mapping_receipt": REF,
    "prometheus": object_schema({
        "service_id": {"const": "prometheus"}, "namespace": NAME, "service": NAME,
        "job": {"type": "string", "pattern": "^[A-Za-z0-9_.:/-]{1,128}$"},
        "instance": {"type": "string", "pattern": "^[A-Za-z0-9_.:\\[\\]-]{1,253}$"},
    }),
}, ["id", "roles", "kubernetes", "argocd"])
INTENT_SCHEMA = document("runtime-intent", {
    "schema_version": {"const": 1}, "run_id": IDENTIFIER, "attempt_id": IDENTIFIER,
    "phase": {"enum": ["baseline", "fault", "recovery", "observation"]},
    "environment": NAME, "profile": {"type": "string", "maxLength": 64}, "scenario_id": NAME,
    "identities": IDENTITIES, "environment_inventory_receipt": REF,
    "targets": array(TARGET, minimum=1, maximum=8),
}, ["schema_version", "run_id", "attempt_id", "phase", "environment", "profile", "scenario_id", "identities", "targets"])
GAP = object_schema({"code": NAME, "required": {"const": True}, "target_id": NAME}, ["code", "required"])
ARTIFACT = object_schema({
    "sha256": DIGEST, "canonical_sha256": DIGEST,
    "encoding": {"enum": ["raw-json", CANONICALIZATION]}, "path": {"type": ["string", "null"], "maxLength": 4096},
})
RECEIPT = object_schema({
    "id": NAME, "expected_sha256": DIGEST, "artifact": ARTIFACT,
    "integrity": {"enum": ["matched", "missing", "mismatch", "unreadable"]},
    "authentication": {"const": "not-verified"},
}, ["id", "expected_sha256", "integrity", "authentication"])
CA = object_schema({"state": {"enum": ["system-trust", "frozen", "unreadable"]}, "sha256": DIGEST}, ["state"])
CONTEXT_SCHEMA = document("runtime-context", {
    "schema_version": {"const": 1}, "kind": {"const": "runtime-context"}, "frozen_at": DATE,
    "factory_config": CONFIG_SCHEMA, "factory_lock": LOCK_SCHEMA, "intent": INTENT_SCHEMA,
    "input_artifacts": object_schema({key: ARTIFACT for key in ("config", "lock", "intent")}),
    "receipt_artifacts": array(RECEIPT, maximum=300),
    "ca_artifacts": {"type": "object", "additionalProperties": CA},
    "gaps": array(GAP, maximum=1000), "context_sha256": DIGEST,
})
DELIVERY_SCHEMA = document("runtime-delivery-binding", {
    "schema_version": {"const": 1}, "kind": {"const": "runtime-delivery-binding"},
    "run_id": IDENTIFIER, "attempt_id": IDENTIFIER, "environment": NAME, "identities": IDENTITIES,
    "target_id": NAME, "source_position": {"type": "integer", "minimum": 1, "maximum": 32},
    "delivered_revision": REVISION, "delivered_at": DATE,
    "native_handoff_sha256": DIGEST, "patch_sha256": DIGEST,
    "candidate_tree_sha256": DIGEST, "validation_receipt_sha256": DIGEST,
})


def canonical_bytes(value):
    """A named local encoding with a trailing newline, not RFC 8785."""
    try:
        data = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n").encode()
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise ValueError("Runtime document is not bounded JSON") from None
    if len(data) > MAX_BUNDLE:
        raise ValueError("Runtime document exceeds its byte bound")
    return data


def digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def clock(value=None):
    value = datetime.now(UTC) if value is None else value
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("Runtime observation time must be timezone aware")
    return value.astimezone(UTC)


def parse_time(value):
    try:
        return clock(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise ValueError("Runtime timestamp is invalid") from None


def validate(value, schema):
    canonical_bytes(value)
    if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(value):
        raise ValueError("Runtime document does not satisfy its versioned schema")


def gap(code, target_id=None):
    result = {"code": code, "required": True}
    if target_id is not None:
        result["target_id"] = target_id
    return result


def unique_gaps(items):
    return list({(g["code"], g.get("target_id")): g for g in items}.values())


def seal(value, field):
    result = deepcopy(value)
    result.pop(field, None)
    result[field] = digest(result)
    return result


def verify_seal(value, field):
    body = deepcopy(value)
    expected = body.pop(field, None)
    if expected != digest(body):
        raise ValueError("Runtime document integrity check failed")


def load_input(value):
    """Return a private copy and both raw/canonical identities, without payload logging."""
    if isinstance(value, Path):
        resolved = value.absolute()
        try:
            before = resolved.stat()
            data, sha = read_json(resolved, limit=MAX_DOCUMENT)
            after = resolved.stat()
        except (OSError, ValueError):
            raise ValueError("Runtime input is unreadable or invalid") from None
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("Runtime input changed while reading")
        artifact = {"sha256": sha, "encoding": "raw-json", "path": str(resolved)}
    elif isinstance(value, dict):
        data = deepcopy(value)
        raw = canonical_bytes(data)
        if len(raw) > MAX_DOCUMENT:
            raise ValueError("Runtime input exceeds its byte bound")
        artifact = {"sha256": hashlib.sha256(raw).hexdigest(), "encoding": CANONICALIZATION, "path": None}
    else:
        raise ValueError("Runtime inputs must be explicit Paths or JSON objects")
    artifact["canonical_sha256"] = digest(data)
    return data, artifact


def file_hash(path):
    """Bounded regular certificate bytes; do not follow symlinks or return content."""
    path = Path(path).absolute()
    try:
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_DOCUMENT:
                raise ValueError
            raw = stream.read(MAX_DOCUMENT + 1)
            after = os.fstat(stream.fileno())
        def identity(s):
            return (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (len(raw) > MAX_DOCUMENT or identity(before) != identity(after) or identity(path.stat()) != identity(after)):
            raise ValueError
        return hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError):
        raise ValueError("Runtime certificate input is unreadable or changed") from None


def _https(value, *, origin=False):
    try:
        url = urlsplit(value)
        invalid = (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
                   or url.query or url.fragment or url.port == 0 or any(c.isspace() for c in value) or "\\" in value)
        if origin and url.path not in ("", "/"):
            invalid = True
        if invalid:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Runtime intent URL is not credential-free HTTPS") from None


def validate_intent(intent):
    validate(intent, INTENT_SCHEMA)
    targets = intent["targets"]
    if len({t["id"] for t in targets}) != len(targets):
        raise ValueError("Runtime target identities must be unique")
    workload_keys, service_keys, argo_keys, prom_keys = [], [], [], []
    for target in targets:
        kube, argo = target["kubernetes"], target["argocd"]
        workload_keys.append((kube["namespace"], kube["deployment"]))
        service_keys.append((kube["namespace"], kube["service"]))
        argo_keys.append((argo["project"], argo["application_namespace"], argo["application"]))
        if kube["namespace"] != argo["destination"]["namespace"]:
            raise ValueError("Runtime target destination namespaces differ")
        _https(argo["destination"]["server"], origin=True)
        positions = [s["position"] for s in argo["sources"]]
        if sorted(positions) != list(range(1, len(positions) + 1)):
            raise ValueError("Runtime source positions must be complete and unique")
        for source in argo["sources"]:
            _https(source["repository_url"])
            path = source["path"]
            if (path.startswith("/") or "\\" in path or any(c in path for c in "\r\n\x00")
                    or any(p in (".", "..") for p in path.split("/"))
                    or (not path and source["renderer"] != "values")):
                raise ValueError("Runtime source path is invalid")
        if "prometheus" in target:
            prom = target["prometheus"]
            if (prom["namespace"], prom["service"]) != (kube["namespace"], kube["service"]):
                raise ValueError("Runtime metric and workload scope differ")
            prom_keys.append(tuple(prom[k] for k in ("namespace", "service", "job", "instance")))
    if any(len(set(keys)) != len(keys) for keys in (workload_keys, service_keys, argo_keys, prom_keys)):
        raise ValueError("Runtime targets overlap")
    if intent["scenario_id"] == "ironbank-mission-recovery-v1":
        roles = {role: [t for t in targets if role in t["roles"]] for role in ("mission", "reference-web")}
        if (any(len(v) != 1 for v in roles.values()) or roles["mission"][0]["id"] == roles["reference-web"][0]["id"]
                or "prometheus" not in roles["mission"][0]):
            raise ValueError("Canonical scenario requires distinct mission and reference-web targets and mission telemetry")


def _references(intent):
    result = []
    if "environment_inventory_receipt" in intent:
        result.append(intent["environment_inventory_receipt"])
    for target in intent["targets"]:
        if "image_mapping_receipt" in target:
            result.append(target["image_mapping_receipt"])
        argo = target["argocd"]
        if "intent_receipt" in argo:
            result.append(argo["intent_receipt"])
        result.extend(s["intent_receipt"] for s in argo["sources"] if "intent_receipt" in s)
    refs = {}
    for ref in result:
        if ref["id"] in refs and refs[ref["id"]] != ref["sha256"]:
            raise ValueError("Runtime receipt identity has conflicting hashes")
        refs[ref["id"]] = ref["sha256"]
    return refs


def freeze_runtime_context(factory_config, factory_lock, phase_intent, *, receipt_documents=None, now=None):
    """Freeze validated inputs and receipt integrity; never authenticate receipt claims."""
    now = clock(now)
    config, config_art = load_input(factory_config)
    lock, lock_art = load_input(factory_lock)
    intent, intent_art = load_input(phase_intent)
    validate_document(config, "factory-config")
    validate_document(lock, "factory-lock")
    validate_intent(intent)
    identities = intent["identities"]
    if (config["profile"] != lock["profile"] or intent["profile"] != config["profile"]
            or intent["environment"] != config["environment"] or intent["scenario_id"] != lock["scenario_id"]
            or config["runtime"]["architecture"] != lock["platform"]["architecture"]
            or identities["config_sha256"] != config_art["sha256"] or identities["lock_sha256"] != lock_art["sha256"]
            or any(identities[k] != lock[k] for k in ("scenario_sha256", "assertions_sha256"))):
        raise ValueError("Runtime input identities or environments differ")
    gaps = [gap("environment-inventory-authentication-unverified"), gap("source-intent-authentication-unverified")]
    if now >= parse_time(config["expires_at"]):
        gaps.append(gap("environment-expired"))
    if "environment_inventory_receipt" not in intent:
        gaps.append(gap("environment-inventory-missing"))
    refs = _references(intent)
    supplied = {} if receipt_documents is None else receipt_documents
    if not isinstance(supplied, dict) or set(supplied) - set(refs):
        raise ValueError("Runtime receipts must match explicitly referenced identities")
    receipts = []
    for ref_id, expected in refs.items():
        receipt = {"id": ref_id, "expected_sha256": expected, "integrity": "missing", "authentication": "not-verified"}
        if ref_id in supplied:
            try:
                _, artifact = load_input(supplied[ref_id])
                receipt.update(artifact=artifact, integrity="matched" if artifact["sha256"] == expected else "mismatch")
            except ValueError:
                receipt["integrity"] = "unreadable"
        if receipt["integrity"] != "matched":
            gaps.append(gap("referenced-receipt-" + receipt["integrity"]))
        receipts.append(receipt)
    for target in intent["targets"]:
        target_id, kube, argo = target["id"], target["kubernetes"], target["argocd"]
        gaps.append(gap("image-mapping-authentication-unverified", target_id))
        for key, code in (("deployment_uid", "deployment-uid-intent-missing"), ("generation", "deployment-generation-intent-missing"),
                          ("service_uid", "service-uid-intent-missing")):
            if key not in kube:
                gaps.append(gap(code, target_id))
        if "image_mapping_receipt" not in target:
            gaps.append(gap("image-mapping-missing", target_id))
        if "prometheus" in target:
            gaps.append(gap("telemetry-target-binding-unverified", target_id))
        if ("destination_spec_sha256" not in argo or "intent_receipt" not in argo
                or any("source_spec_sha256" not in s or "rendered_artifact_sha256" not in s or "intent_receipt" not in s
                       for s in argo["sources"])):
            gaps.append(gap("complete-source-intent-missing", target_id))
    ca_artifacts = {}
    for service in config["services"]:
        if service["id"] not in {"kubernetes", "argocd", "prometheus"}:
            continue
        ca = {"state": "system-trust"}
        if "ca_file" in service:
            try:
                ca = {"state": "frozen", "sha256": file_hash(service["ca_file"])}
            except ValueError:
                ca = {"state": "unreadable"}
                gaps.append(gap("ca-input-unreadable"))
        ca_artifacts[service["id"]] = ca
    result = seal({"schema_version": 1, "kind": "runtime-context", "frozen_at": now.isoformat(),
        "factory_config": config, "factory_lock": lock, "intent": intent,
        "input_artifacts": {"config": config_art, "lock": lock_art, "intent": intent_art},
        "receipt_artifacts": receipts, "ca_artifacts": ca_artifacts, "gaps": unique_gaps(gaps)}, "context_sha256")
    validate(result, CONTEXT_SCHEMA)
    return result


def verify_context(context):
    """Validate recorded structure/integrity only; embedded paths are inert.

    This is not read authorization. Network entry points additionally require
    verify_runtime_authority with independently supplied operator inputs.
    """
    validate(context, CONTEXT_SCHEMA)
    verify_seal(context, "context_sha256")
    validate_document(context["factory_config"], "factory-config")
    validate_document(context["factory_lock"], "factory-lock")
    validate_intent(context["intent"])
    for name, document_key in (("config", "factory_config"), ("lock", "factory_lock"), ("intent", "intent")):
        artifact = context["input_artifacts"][name]
        if digest(context[document_key]) != artifact["canonical_sha256"]:
            raise ValueError("Frozen runtime document content changed")


def verify_runtime_authority(context, authority):
    """Bind recorded scope to separate trusted inputs before any provider read.

    Callers must obtain these values from an operator-controlled source, never
    extract them from the context being checked. Paths recorded in evidence
    cannot authorize file reads, credentials, origins, CAs or resource scope.
    """
    if not isinstance(authority, dict) or set(authority) != {"config", "lock", "intent"}:
        raise ValueError("Explicit trusted config, lock and intent authority is required")
    verify_context(context)
    # Re-freezing validates every config/lock/intent cross-invariant, and derives
    # CA identities only from the independently supplied trusted configuration.
    current = freeze_runtime_context(authority["config"], authority["lock"], authority["intent"])
    for name, key in (("config", "factory_config"), ("lock", "factory_lock"), ("intent", "intent")):
        recorded, supplied = context["input_artifacts"][name], current["input_artifacts"][name]
        if (context[key] != current[key]
                or any(recorded[field] != supplied[field] for field in ("sha256", "canonical_sha256"))):
            raise ValueError("Frozen runtime input bytes differ from trusted authority")
    if context["ca_artifacts"] != current["ca_artifacts"]:
        raise ValueError("Frozen runtime certificate bytes differ from trusted authority")
