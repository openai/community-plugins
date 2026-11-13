"""Bounded offline recovery checks; origins require a separate authenticated driver.

No embedded path is opened and no JSON verdict is promoted to recovery. A future
closed provenance adapter verifies the exact frozen bytes consumed here. Tests
can exercise that interface, but cannot produce a live qualification claim.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import ipaddress
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol

from devsecops.correction import CONTRACT_SCHEMA as CORRECTION_SCHEMA, ScopeValidator
from devsecops.factory.contracts import DIGEST, NAME, LOCK_SCHEMA, array, document, object_schema, validate_document
from devsecops.factory.state import _read, _pairs
from devsecops.profiles import relative_path
from .context import (DATE, IDENTIFIER, IDENTITIES, INTENT_SCHEMA, DELIVERY_SCHEMA, MAX_BUNDLE,
                      REVISION, canonical_bytes, clock, digest, parse_time, seal, validate, validate_intent, verify_seal)
from .collection import _image_comparison
from .diagnosis import _load as _collection, _kube, _argo, _metrics, _scope, _samples

PHASES = ("baseline", "fault", "recovery")
KINDS = ("mission-health", "reference-health", "metrics-allowed-before", "metrics-allowed-after",
         "metrics-denied-wrong-namespace", "metrics-denied-wrong-label")
ASSURANCES = ("operator-intent", "collection-origin", "cluster-destination", "probe-origin-and-source",
              "image-content-mapping", "native-execution", "correction-execution", "git-delivery", "network-policy-path")
BASE_ARTIFACTS = ("inventory", "delivery", "git_delivery", "native_request", "native_result", "native_validation",
                  "native_execution", "patch", "correction", "correction_contract", "rendered_before", "rendered_after")
# Only these collection limitations have explicit replacement checks below.
REASSESSED_COLLECTION_GAPS = {"context-authentication-unverified", "pending-authentication-unverified",
    "environment-inventory-authentication-unverified", "source-intent-authentication-unverified",
    "image-mapping-authentication-unverified", "native-delivery-authentication-unverified",
    "telemetry-target-binding-unverified", "controlled-test-evidence", "recovery-contract-not-evaluated"}
FAULT_COUNTER_ABSENCE_GAPS = {'prometheus-missing-or-multiple-series', 'prometheus-missing-or-excessive-samples',
    'prometheus-insufficient-fresh-samples', 'prometheus-stale-source-sample',
    'prometheus-source-window-mismatch', 'runtime-prometheus-source-time-unavailable',
    'runtime-prometheus-stale-at-collection'}
PROBE_CASE = object_schema({"id": NAME, "phase": {"enum": list(PHASES)}, "kind": {"enum": list(KINDS)},
    "target_id": NAME, "source_id": NAME, "port": {"type": "integer", "minimum": 1, "maximum": 65535},
    "path": {"enum": ["/", "/health", "/healthz", "/metrics"]},
    "body_sha256": {"anyOf": [DIGEST, {"type": "null"}]}})
LABELS = {"type": "object", "maxProperties": 64,
          "propertyNames": {"type": "string", "minLength": 1, "maxLength": 253},
          "additionalProperties": {"type": "string", "maxLength": 63}}
IMAGE_ID = {"type": "string", "maxLength": 1024,
            "pattern": r"^(?:[a-z][a-z0-9+.-]*://)?(?:[A-Za-z0-9._:/-]+@)?sha256:[a-f0-9]{64}$"}
EXPECTED_IMAGE = object_schema({"target_id": NAME, "container": NAME, "container_type": {"enum": ["regular", "init"]},
    "platform_manifest_sha256": DIGEST, "config_sha256": DIGEST, "mapping_receipt_sha256": DIGEST})
RECOVERY_CONTRACT_SCHEMA = document("runtime-recovery-contract", {
    "schema_version": {"const": 1}, "id": NAME, "run_id": IDENTIFIER, "attempt_id": IDENTIFIER,
    "environment": NAME, "identities": IDENTITIES, "factory_lock": LOCK_SCHEMA,
    "intents": object_schema({p: INTENT_SCHEMA for p in PHASES}),
    "mission_target": NAME, "reference_target": NAME,
    "mission_application_container": NAME, "reference_application_container": NAME,
    "image_expectations": array(EXPECTED_IMAGE, minimum=2, maximum=64),
    "delivery_target": NAME, "delivery_source_position": {"type": "integer", "minimum": 1, "maximum": 32},
    "correction_contract_sha256": DIGEST,
    "probe_cases": array(PROBE_CASE, minimum=14, maximum=32),
    "metrics_authorization": object_schema({"namespace_labels": LABELS, "pod_labels": LABELS}),
})
SOURCE = object_schema({"id": NAME, "namespace": NAME, "name": IDENTIFIER, "uid": IDENTIFIER,
    "namespace_uid": IDENTIFIER, "pod_labels": LABELS, "namespace_labels": LABELS,
    "image_id": IMAGE_ID,
    "observed_before": DATE, "observed_after": DATE})
SERVICE = object_schema({"target_id": NAME, "uid": IDENTIFIER,
    "cluster_ips": array({"type": "string", "maxLength": 45}, minimum=1, maximum=2),
    "cluster_domain": {"type": "string", "maxLength": 253},
    "observed_before": DATE, "observed_after": DATE})
RECOVERY_INVENTORY_SCHEMA = document("runtime-recovery-inventory", {
    "schema_version": {"const": 1}, "run_id": IDENTIFIER, "attempt_id": IDENTIFIER,
    "environment": NAME, "identities": IDENTITIES,
    "cluster_uid": IDENTIFIER, "kubernetes_origin_sha256": DIGEST,
    "argo_destination_sha256": DIGEST,
    "services": array(SERVICE, minimum=2, maximum=8), "probe_sources": array(SOURCE, minimum=3, maximum=32),
    "images": array(object_schema({"target_id": NAME, "container": NAME,
        "container_type": {"enum": ["regular", "init"]},
        "runtime_image_id": IMAGE_ID,
        "manifest_digest": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"},
        "config_digest": {"type": "string", "pattern": "^sha256:[a-f0-9]{64}$"},
        "mapping_receipt_sha256": DIGEST}), minimum=2, maximum=64),
    "telemetry": object_schema({"target_id": NAME, "service_uid": IDENTIFIER, "pod_uid": IDENTIFIER,
        "job": {"type": "string", "maxLength": 128}, "instance": {"type": "string", "maxLength": 253}}),
    "topology": object_schema({"cni": {"type": "string", "maxLength": 256},
        "mesh_mode": {"type": "string", "maxLength": 256},
        "probe_transport": {"enum": ["plain-http-networkpolicy", "unsupported"]}, "qualification_sha256": DIGEST}),
})
TREE = object_schema({"sha256": DIGEST, "file_count": {"type": "integer", "minimum": 1, "maximum": 5000},
    "bytes": {"type": "integer", "minimum": 0, "maximum": 64_000_000},
    "files": array(object_schema({"path": {"type": "string", "minLength": 1, "maxLength": 1024},
        "sha256": DIGEST, "bytes": {"type": "integer", "minimum": 0, "maximum": 64_000_000}}), minimum=1, maximum=5000)})
RUN = {"schema_version": {"const": 1}, "run_id": IDENTIFIER, "attempt_id": IDENTIFIER,
       "environment": NAME, "identities": IDENTITIES}
GIT_DELIVERY_SCHEMA = document("runtime-git-delivery", {**RUN, "kind": {"const": "runtime-git-delivery"},
    "repository_url": {"type": "string", "maxLength": 2048}, "revision": REVISION,
    "source_path": {"type": "string", "maxLength": 1024}, "selected_tree": TREE,
    "rendered_artifact_sha256": DIGEST, "observed_at": DATE})
NATIVE_EXECUTION_V1_SCHEMA = document("runtime-native-execution", {**RUN, "kind": {"const": "runtime-native-execution"},
    "request_sha256": DIGEST, "result_sha256": DIGEST, "patch_sha256": DIGEST, "package_sha256": DIGEST,
    "host_id": IDENTIFIER, "turn_id": IDENTIFIER, "trace_sha256": DIGEST, "started_at": DATE, "finished_at": DATE})
NATIVE_EXECUTION_V2_SCHEMA = document("runtime-native-execution", {
    **RUN, "schema_version": {"const": 2}, "kind": {"const": "runtime-native-execution"},
    "driver": {"const": "codex-cli-single-turn"},
    "execution_id": {"type": "string", "pattern": "^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$"},
    "execution_mode": {"enum": ["direct", "controlled-test"]},
    "authority_scope": {"const": "native-execution-and-artifact-validation-only"},
    "host_id": IDENTIFIER, "thread_id": IDENTIFIER,
    "turn_ordinal": {"const": 1}, "server_turn_id": {"type": "null"},
    "request_id": IDENTIFIER, "step_id": IDENTIFIER,
    "workflow": {"const": "prepare-gitops-correction"},
    "configured_model": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$"},
    "configured_reasoning_effort": {"enum": ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]},
    "client_version": {"type": "string", "minLength": 1, "maxLength": 128},
    "executable_sha256": DIGEST, "engine_python_identity_sha256": DIGEST,
    "request_sha256": DIGEST, "result_sha256": DIGEST, "patch_sha256": DIGEST, "package_sha256": DIGEST,
    "trace_sha256": DIGEST, "invocation_sha256": DIGEST, "driver_execution_sha256": DIGEST,
    "execution_manifest_sha256": DIGEST, "validator_identity_sha256": DIGEST,
    "validation_receipt_sha256": DIGEST, "started_at": DATE, "finished_at": DATE, "validated_at": DATE,
})
NATIVE_EXECUTION_V2_SCHEMA['$id'] = 'urn:devsecops:runtime-native-execution:2'
NATIVE_EXECUTION_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    '$id': 'urn:devsecops:runtime-native-execution:compatible',
    'oneOf': [NATIVE_EXECUTION_V1_SCHEMA, NATIVE_EXECUTION_V2_SCHEMA],
}
EVIDENCE_REF = object_schema({"input": {"type": "string", "maxLength": 128},
    "pointer": {"type": "string", "maxLength": 2048}, "sha256": DIGEST})
CONDITION = object_schema({"id": NAME, "phase": {"enum": [*PHASES, "chain", "all"]},
    "target_id": NAME, "observed_state": {"enum": ["recovered", "not-recovered", "unknown"]},
    "code": NAME, "authenticated": {"type": "boolean"}, "evidence": array(EVIDENCE_REF, maximum=20)},
    ["id", "phase", "observed_state", "code", "authenticated", "evidence"])
INPUT = object_schema({"state": {"enum": ["valid", "invalid", "missing"]}, "sha256": DIGEST,
                       "bytes": {"type": "integer", "minimum": 0, "maximum": MAX_BUNDLE}}, ["state"])
GAP = object_schema({"code": NAME, "required": {"const": True}, "input": {"type": "string", "maxLength": 128}},
                    ["code", "required"])
RECOVERY_SCHEMA = document("runtime-recovery", {
    "schema_version": {"const": 1}, "kind": {"const": "runtime-recovery"},
    "observation_kind": {"const": "independent-recovery-conditions"}, "observed_at": DATE,
    "execution_mode": {"enum": ["direct", "controlled-test"]},
    "execution_status": {"enum": ["complete", "partial"]}, "decision": {"enum": ["pass", "fail", "unknown"]},
    "recovery": {"enum": ["verified", "not-recovered", "unknown"]}, "causality": {"const": "unproven"},
    "confirmed_failure": {"type": "boolean"}, "identities": IDENTITIES,
    "inputs": {"type": "object", "maxProperties": 50, "additionalProperties": INPUT},
    "conditions": array(CONDITION, maximum=100), "retained_failures": array(CONDITION, maximum=100),
    "provenance": object_schema({"adapter": {"type": ["string", "null"], "maxLength": 64},
        "verified": {"type": "boolean"}, "receipt_sha256": {"anyOf": [DIGEST, {"type": "null"}]},
        "assurances": array({"enum": list(ASSURANCES)}, maximum=len(ASSURANCES))}),
    "gaps": array(GAP, maximum=200), "recovery_sha256": DIGEST,
})
SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"


@dataclass(frozen=True)
class OriginVerification:
    """Only a trusted code adapter may produce this after verifying external origins."""
    receipt_sha256: str
    artifact_sha256: Mapping[str, str]
    assurances: tuple[str, ...]


class _Provenance(Protocol):
    def verify(self, *, artifacts: Mapping[str, bytes]) -> OriginVerification:
        """Authenticate every exact byte input including contract, without mutation."""
        ...


class _CanonicalRuntime:
    """Fixed in-process admission, never a plugin name or saved receipt loader."""
    def verify(self, *, artifacts, origin):
        from devsecops.factory.runtime_observations import verify_runtime_observation_origin
        return verify_runtime_observation_origin(origin, artifacts=artifacts)


# This adapter authenticates only capabilities actually held by the canonical
# process. Partial assurance is reported as a gap, never promoted to full trust.
_PROVENANCE = {"canonical-runtime-v1": _CanonicalRuntime()}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _parse(raw):
    def nonfinite(_):
        raise ValueError
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=nonfinite)
    if not isinstance(value, dict):
        raise ValueError
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 150_000 or depth > 64:
            raise ValueError
        if isinstance(item, dict):
            pending.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            pending.extend((v, depth + 1) for v in item)
    canonical_bytes(value)
    return value


class _Inputs:
    def __init__(self):
        self.raw, self.docs, self.metadata = {}, {}, {}
        self.total = 0

    def read(self, name, value, *, binary=False, schema=None):
        self.metadata[name] = {"state": "missing" if value is None else "invalid"}
        if value is None:
            return None
        try:
            raw = (_read(value, 256_000 if binary else MAX_BUNDLE) if isinstance(value, Path)
                   else value if binary and isinstance(value, bytes) else canonical_bytes(value))
            self.total += len(raw)
            if self.total > 64_000_000 or len(raw) > (256_000 if binary else MAX_BUNDLE):
                raise ValueError
            result = raw if binary else _parse(raw)
            if binary and (not raw or b"\0" in raw):
                raise ValueError
            if schema:
                validate(result, schema)
            self.raw[name], self.docs[name] = raw, result
            self.metadata[name] = {"state": "valid", "sha256": _sha(raw), "bytes": len(raw)}
            return result
        except (ValueError, OSError, TypeError, RecursionError, OverflowError):
            return None

    def ref(self, name, pointer=""):
        if name not in self.raw:
            return []
        return [{"input": name, "pointer": pointer, "sha256": _sha(self.raw[name])}]


def _gap(result, code, name=None):
    item = {"code": code, "required": True}
    if name:
        item["input"] = name
    if item not in result["gaps"]:
        result["gaps"].append(item)


def _condition(result, inputs, name, value, code, evidence, *, phase="chain", target=None, retain=False):
    item = {"id": name, "phase": phase, "observed_state": value, "code": code,
            "authenticated": result["provenance"]["verified"],
            "evidence": [ref for source, pointer in evidence for ref in inputs.ref(source, pointer)]}
    if target:
        item["target_id"] = target
    result["conditions"].append(item)
    if value == "unknown":
        _gap(result, code)
    if value == "not-recovered" and (retain or phase != "fault"):
        result["retained_failures"].append(deepcopy(item))


def _state(condition, *, known=True):
    return "unknown" if not known else "recovered" if condition else "not-recovered"


def _approved(contract):
    validate(contract, RECOVERY_CONTRACT_SCHEMA)
    validate_document(contract["factory_lock"], "factory-lock")
    identities = contract["identities"]
    lock = contract["factory_lock"]
    if any(identities[k] != lock[k] for k in ("scenario_sha256", "assertions_sha256")):
        raise ValueError
    targets = {}
    for phase, intent in contract["intents"].items():
        validate_intent(intent)
        if (intent["phase"] != phase or intent["scenario_id"] != "ironbank-mission-recovery-v1"
                or intent["profile"] != lock["profile"] or any(intent[k] != contract[k]
                for k in ("identities", "run_id", "attempt_id", "environment"))):
            raise ValueError
        targets[phase] = {t["id"]: t for t in intent["targets"]}
        if set(targets[phase]) != {contract["mission_target"], contract["reference_target"]}:
            raise ValueError
        if "mission" not in targets[phase][contract["mission_target"]]["roles"] or "reference-web" not in targets[phase][contract["reference_target"]]["roles"]:
            raise ValueError
    if any(_scope(targets[p][t]) != _scope(targets["baseline"][t]) for p in PHASES for t in targets[p]):
        raise ValueError
    reference = contract["reference_target"]
    if any(targets[p][reference] != targets["baseline"][reference] for p in PHASES):
        raise ValueError
    expectations = {(i["target_id"], i["container_type"], i["container"]): i for i in contract["image_expectations"]}
    if len(expectations) != len(contract["image_expectations"]):
        raise ValueError
    for role, target in (("mission", contract["mission_target"]), ("reference", reference)):
        image = expectations[(target, "regular", contract[role + "_application_container"])]
        if role == "reference" and any(image[k] != lock["images"]["reference_web"][k] for k in ("platform_manifest_sha256", "config_sha256")):
            raise ValueError
    if any(i["target_id"] not in targets[p] or i["mapping_receipt_sha256"] != targets[p][i["target_id"]].get("image_mapping_receipt", {}).get("sha256") for p in PHASES for i in expectations.values()):
        raise ValueError
    cases = contract["probe_cases"]
    required = {(p, k) for p in PHASES for k in KINDS if p != "fault" or k.endswith("health")}
    if ({(c["phase"], c["kind"]) for c in cases} != required or len(cases) != len(required)
            or len({c["id"] for c in cases}) != len(cases)):
        raise ValueError
    for case in cases:
        expected_target = contract["reference_target"] if case["kind"] == "reference-health" else contract["mission_target"]
        if case["target_id"] != expected_target or (case["kind"].startswith("metrics-") and case["path"] != "/metrics"):
            raise ValueError
        if case["kind"] == "reference-health" and case["path"] != "/":
            raise ValueError
        if case["kind"] == "mission-health" and case["path"] != "/health":
            raise ValueError
    if not all(contract["metrics_authorization"].values()) or contract["delivery_target"] != contract["mission_target"]:
        raise ValueError
    return targets


def _authenticate(result, inputs, provenance, test, runtime_origin=None):
    adapter = test if test is not None else _PROVENANCE.get(provenance)
    if adapter is None:
        _gap(result, "evidence-origin-authentication-unavailable")
        return
    try:
        arguments = {"artifacts": MappingProxyType(dict(inputs.raw))}
        if test is None:
            arguments['origin'] = runtime_origin
        answer = adapter.verify(**arguments)
        if (not isinstance(answer, OriginVerification) or not set(answer.assurances) <= set(ASSURANCES)
                or len(set(answer.assurances)) != len(answer.assurances)
                or dict(answer.artifact_sha256) != {k: _sha(v) for k, v in inputs.raw.items()}):
            raise ValueError
        validate(answer.receipt_sha256, DIGEST)
        complete = set(answer.assurances) == set(ASSURANCES)
        result["provenance"].update(verified=complete, receipt_sha256=answer.receipt_sha256, assurances=list(answer.assurances))
        for missing in sorted(set(ASSURANCES)-set(answer.assurances)):
            _gap(result, 'origin-'+missing+'-unverified')
    except Exception:
        # A driver exception or raw remote message is not user-visible evidence.
        _gap(result, "evidence-origin-authentication-failed")


def _manifest(value):
    if not isinstance(value, dict) or not isinstance(value.get("files"), list) or not 1 <= len(value["files"]) <= 5000:
        raise ValueError
    files, total, digester = {}, 0, hashlib.sha256()
    for item in value["files"]:
        if set(item) != {"path", "sha256", "bytes"}:
            raise ValueError
        name = relative_path(item["path"])
        validate(item["sha256"], DIGEST)
        if name in files or type(item["bytes"]) is not int or not 0 <= item["bytes"] <= 64_000_000:
            raise ValueError
        files[name] = item
        total += item["bytes"]
    for name, item in sorted(files.items()):
        digester.update(name.encode() + b"\0" + item["sha256"].encode() + b"\n")
    if value.get("sha256") != digester.hexdigest() or value.get("file_count") != len(files) or value.get("bytes") != total or total > 64_000_000:
        raise ValueError
    return files


def _delivery(result, inputs, contract, targets):
    names = ("delivery", "git_delivery", "native_request", "native_result", "native_validation", "native_execution", "patch", "correction", "correction_contract")
    docs = inputs.docs
    evidence = [(n, "") for n in names]
    try:
        if any(n not in docs for n in names):
            raise ValueError
        delivery, request, native = docs["delivery"], docs["native_request"], docs["native_result"]
        correction, reviewed, git, execution = (docs[n] for n in ("correction", "correction_contract", "git_delivery", "native_execution"))
        validate(delivery, DELIVERY_SCHEMA)
        validate(request, json.loads((SCHEMAS / "native-request.schema.json").read_bytes()))
        validate(native, json.loads((SCHEMAS / "native-result.schema.json").read_bytes()))
        validate(reviewed, CORRECTION_SCHEMA)
        validate(git, GIT_DELIVERY_SCHEMA)
        validate(execution, NATIVE_EXECUTION_SCHEMA)
        base_files = _manifest(correction["base_tree"])
        candidate_files = _manifest(correction["patched_tree"])
        delivered_files = _manifest(git["selected_tree"])
        exported = correction['rendered_export']
        render_refs = {filename: {'sha256': _sha(inputs.raw[name]), 'bytes': len(inputs.raw[name])}
                       for filename, name in (('rendered-before.json', 'rendered_before'),
                                              ('rendered-after.json', 'rendered_after'))}
        if (exported.get('status') != 'complete'
                or exported.get('encoding') != 'devsecops-runtime-json-sha256-v1'
                or exported.get('artifacts') != render_refs
                or exported.get('input_binding') != {
                    'base_commit': correction['source']['base_commit'],
                    'base_tree_sha256': correction['base_tree']['sha256'],
                    'candidate_tree_sha256': correction['patched_tree']['sha256'],
                    'profile_sha256': correction['source']['profile_sha256'],
                    'contract_sha256': correction['contract_sha256'],
                    'patch_sha256': correction['patch_sha256']}):
            raise ValueError
        target, position = contract["delivery_target"], contract["delivery_source_position"]
        selected = {s["position"]: s for s in targets["recovery"][target]["argocd"]["sources"]}[position]
        fault = {s["position"]: s for s in targets["fault"][target]["argocd"]["sources"]}[position]
        known = (all(delivery[k] == contract[k] for k in ("run_id", "attempt_id", "environment", "identities"))
            and delivery["target_id"] == target and delivery["source_position"] == position
            and delivery["delivered_revision"] == selected["revision"] != fault["revision"]
            and request["run_id"] == contract["run_id"] and request["identities"] == contract["identities"]
            and request["workflow"] == "prepare-gitops-correction" and request["required_artifact_kind"] == "patch"
            and all(native[k] == request[k] for k in ("request_id", "run_id", "step_id"))
            and native["request_sha256"] == _sha(inputs.raw["native_request"])
            and native["status"] == "completed" and not native["gaps"]
            and delivery["native_handoff_sha256"] == _sha(inputs.raw["native_request"])
            and delivery["validation_receipt_sha256"] == _sha(inputs.raw["correction"])
            and delivery["patch_sha256"] == _sha(inputs.raw["patch"]) == correction["patch_sha256"]
            and correction["patch_bytes"] == len(inputs.raw["patch"])
            and delivery["candidate_tree_sha256"] == correction["patched_tree"]["sha256"] == git["selected_tree"]["sha256"]
            and candidate_files == delivered_files
            and set(base_files) == set(candidate_files)
            and {p for p in base_files if base_files[p] != candidate_files[p]} == set(correction["changed_files"])
            and git["repository_url"] == selected["repository_url"] and git["revision"] == delivery["delivered_revision"]
            and git["rendered_artifact_sha256"] == selected.get("rendered_artifact_sha256") == _sha(inputs.raw["rendered_after"])
            and fault.get("rendered_artifact_sha256") == _sha(inputs.raw["rendered_before"])
            and git["source_path"] == selected["path"]
            and all(git[k] == contract[k] for k in ("run_id", "attempt_id", "environment", "identities"))
            and parse_time(git["observed_at"]) >= parse_time(delivery["delivered_at"])
            and _sha(inputs.raw["correction_contract"]) == contract["correction_contract_sha256"] == correction["contract_sha256"]
            and correction["source"]["base_commit"] == reviewed["base_commit"] == fault["revision"]
            and correction["source"]["profile_sha256"] == reviewed["profile_sha256"]
            and correction.get("execution_status") == "complete" and correction.get("gaps") == []
            and correction.get("static_review_decision") == "pass"
            and set(correction["changed_files"]) <= set(reviewed["allowed_files"]) and bool(correction["changed_files"])
            and execution["request_sha256"] == _sha(inputs.raw["native_request"])
            and execution["result_sha256"] == _sha(inputs.raw["native_result"])
            and execution["patch_sha256"] == _sha(inputs.raw["patch"])
            and execution["package_sha256"] == contract["identities"]["package_sha256"]
            and all(execution[k] == contract[k] for k in ("run_id", "attempt_id", "environment", "identities"))
            and any(a["sha256"] == _sha(inputs.raw["correction_contract"]) and a["bytes"] == len(inputs.raw["correction_contract"])
                    for a in request["input_artifacts"])
            and parse_time(request["created_at"]) <= parse_time(execution["started_at"])
            <= parse_time(execution["finished_at"]) <= parse_time(request["expires_at"])
            and parse_time(execution["finished_at"]) <= parse_time(correction["observed_at"]) <= parse_time(delivery["delivered_at"]))
        patches = [a for a in native["artifacts"] if a["kind"] == "patch"]
        valid = docs["native_validation"]
        known = (known and len(patches) == 1 and patches[0]["sha256"] == _sha(inputs.raw["patch"])
                 and patches[0]["bytes"] == len(inputs.raw["patch"])
                 and valid["request_sha256"] == _sha(inputs.raw["native_request"])
                 and valid["result_sha256"] == _sha(inputs.raw["native_result"])
                 and valid["coverage"] == "artifact_validation_only"
                 and parse_time(execution["finished_at"]) <= parse_time(valid["validated_at"]) <= parse_time(delivery["delivered_at"]))
        if execution['schema_version'] == 2:
            known = (known and execution['request_id'] == request['request_id']
                     and execution['step_id'] == request['step_id']
                     and execution['workflow'] == request['workflow']
                     and execution['validation_receipt_sha256'] == _sha(inputs.raw['native_validation'])
                     and execution['validated_at'] == valid['validated_at'])
        _condition(result, inputs, "native-patch-delivery-chain", _state(known, known=known),
                   "delivery-linked" if known else "delivery-link-missing-or-mismatch", evidence)
        return delivery if known else None
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        _condition(result, inputs, "native-patch-delivery-chain", "unknown", "delivery-link-missing-or-mismatch", evidence)
        return None


def _render_scope(result, inputs, contract):
    names = ("correction_contract", "rendered_before", "rendered_after")
    try:
        reviewed, before, after = [inputs.docs[n] for n in names]
        if _sha(inputs.raw["correction_contract"]) != contract["correction_contract_sha256"]:
            raise ValueError
        validate(reviewed, CORRECTION_SCHEMA)
        records = []
        for value in (before, after):
            if set(value) != {"resources"} or not isinstance(value["resources"], list) or not 1 <= len(value["resources"]) <= 1000:
                raise ValueError
            seen = set()
            for row in value["resources"]:
                if set(row) != {"id", "body"} or row["id"] in seen or not isinstance(row["body"], dict):
                    raise ValueError
                body = row["body"]
                parts = row["id"].split("/")
                group = body["apiVersion"].split("/")[0] if "/" in body["apiVersion"] else "core"
                if (len(parts) != 4 or parts[0] != group or parts[1] != body["kind"]
                        or parts[3] != body["metadata"]["name"] or (body["metadata"].get("namespace")
                        and parts[2] != body["metadata"]["namespace"])):
                    raise ValueError
                seen.add(row["id"])
            records.append(value["resources"])
        checker = ScopeValidator(reviewed["allowed_rendered_changes"])
        checker({"name": "recovery"}, *records)
        if set(checker.allowed) - checker.seen:
            raise ValueError
        failures = checker.receipts[0]["violations"]
        state = _state(checker.changed and not failures)
        _condition(result, inputs, "rendered-change-scope", state,
            "only-approved-rendered-fields-changed" if state == "recovered" else "rendered-change-contract-failed", [(n, "") for n in names])
    except (ValueError, TypeError, KeyError, AttributeError):
        _condition(result, inputs, "rendered-change-scope", "unknown", "rendered-scope-evidence-incomplete", [(n, "") for n in names])


def _fault_scrape_evidence(report, target, window):
    """Fresh up=0 is independent of the counter hidden by the tested outage.

    Reassess only counter-absence limitations after checking exact labels, raw
    source timestamps, locked sample count and actual completion freshness.
    This is never used to relax baseline or post-repair functional evidence.
    """
    try:
        expected = {k: v for k, v in target['prometheus'].items() if k != 'service_id'}
        obs = report['observations']
        samples = _samples(obs.get('scrape', {}).get('samples'), window)
        finish = parse_time(report['transport']['finished_at']).timestamp()
        return (report['provider'] == 'prometheus' and report['schema_version'] == 1
            and report['scope'] == expected and samples is not None
            and all(value == 0 for _, value in samples)
            and all(obs['window'].get(k) == window[k] for k in ('start', 'end', 'step_seconds', 'max_sample_age_seconds', 'minimum_fresh_samples'))
            and window['end'] <= finish <= window['end']+window['max_sample_age_seconds']
            and 0 <= finish-samples[-1][0] <= window['max_sample_age_seconds']
            and bool(report['transport']['receipts'])
            and all(g['code'] in FAULT_COUNTER_ABSENCE_GAPS for g in report.get('gaps', [])))
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return False


def _bundles(result, inputs, contract, targets, delivered, observed):
    bundles, states = {}, {}
    for phase in PHASES:
        bundle, _ = _collection(inputs.docs.get(phase))
        bundles[phase], states[phase] = bundle, {}
        if bundle is None:
            _gap(result, "collection-missing-or-invalid", phase)
            continue
        context = bundle["context_snapshot"]
        bound = (context["intent"] == contract["intents"][phase] and context["factory_lock"] == contract["factory_lock"]
                 and bundle["identities"] == contract["identities"] and bundle["stage"] == "measured"
                 and bool(bundle["pending_sha256"]) and bundle["window"] is not None)
        _condition(result, inputs, "collection-scope", _state(bound, known=bound),
            "approved-collection-scope" if bound else "collection-scope-mismatch", [(phase, "/context_snapshot")], phase=phase)
        if not bound:
            continue
        if bundle["execution_mode"] != "direct":
            result["execution_mode"] = "controlled-test"
        fault_targets = {name for name, target in targets[phase].items()
            if phase == 'fault' and _fault_scrape_evidence(bundle['targets'][name].get('prometheus'), target, bundle['window'])}
        if any(g["code"] not in REASSESSED_COLLECTION_GAPS
               and not (g.get('target_id') in fault_targets and g['code'] in FAULT_COUNTER_ABSENCE_GAPS)
               for g in bundle["gaps"] if g["required"]):
            _gap(result, "unresolved-collection-evidence-gap", phase)
        window = bundle["window"]
        for target_id, target in targets[phase].items():
            row = bundle["targets"][target_id]
            info = {"kubernetes": {}, "argocd": {}, "metrics": None}
            states[phase][target_id] = info
            for side in ("before", "after"):
                for provider in ("kubernetes", "argocd"):
                    report = row[side].get(provider, {})
                    if provider == "argocd":
                        origin = report.get("scope", {}).get("origin") if isinstance(report.get("scope"), dict) else None
                        wanted_origin = next((s["origin"].rstrip("/") for s in context["factory_config"]["services"] if s["id"] == target[provider]["service_id"]), None)
                        if not isinstance(origin, str) or origin.rstrip("/") != wanted_origin:
                            report = {}
                            _gap(result, "provider-origin-mismatch", phase)
                    info[provider][side] = (_kube(report, target) if provider == "kubernetes"
                                           else _argo(report, target, window["max_sample_age_seconds"]))
                    if _incomplete(report):
                        _gap(result, "provider-coverage-incomplete", phase)
                    try:
                        start = parse_time(report["transport"]["started_at"]).timestamp()
                        finish = parse_time(report["transport"]["finished_at"]).timestamp()
                        provider_time = parse_time(report["observed_at"]).timestamp()
                        endpoint = window["start"] if side == "before" else window["end"]
                        if (not start <= provider_time <= finish or finish > parse_time(context["factory_config"]["expires_at"]).timestamp()
                                or (side == "before" and finish > window["start"]) or (side == "after" and start < window["end"])
                                or abs(endpoint - finish) > window["max_sample_age_seconds"]
                                or not isinstance(report["transport"].get("receipts"), list) or not report["transport"]["receipts"]):
                            raise ValueError
                        if phase == "recovery" and side == "after" and (observed.timestamp() - finish > window["max_sample_age_seconds"] or finish > observed.timestamp()):
                            raise ValueError
                    except (ValueError, TypeError, KeyError):
                        _gap(result, "provider-time-evidence-stale-or-invalid", phase)
            kubes = list(info["kubernetes"].values())
            argos = list(info["argocd"].values())
            _condition(result, inputs, "workload-readiness", _state(all(k["readiness"] == "ready" for k in kubes), known=all(k["readiness"] != "unknown" for k in kubes)),
                "readiness-is-not-http-health", [(phase, f"/targets/{target_id}/before/kubernetes"), (phase, f"/targets/{target_id}/after/kubernetes")], phase=phase, target=target_id)
            _condition(result, inputs, "controller-convergence", _state(all(a["sync"] == "Synced" and a["health"] == "Healthy" for a in argos),
                known=all(a["sync"] != "Unknown" and a["health"] != "Unknown" for a in argos)), "controller-revision-and-health",
                [(phase, f"/targets/{target_id}/before/argocd"), (phase, f"/targets/{target_id}/after/argocd")], phase=phase, target=target_id)
            if "prometheus" in target:
                report = row["prometheus"] or {}
                fault_scrape = target_id in fault_targets
                if _incomplete(report) and not fault_scrape:
                    _gap(result, "telemetry-provider-coverage-incomplete", phase)
                try:
                    start = parse_time(report["transport"]["started_at"]).timestamp()
                    finish = parse_time(report["transport"]["finished_at"]).timestamp()
                    if (not window["end"] <= start <= finish <= window["end"] + window["max_sample_age_seconds"]
                            or parse_time(report["observed_at"]).timestamp() != finish
                            or finish > parse_time(context["factory_config"]["expires_at"]).timestamp()
                            or (phase == "recovery" and finish > observed.timestamp())):
                        raise ValueError
                except (ValueError, TypeError, KeyError):
                    _gap(result, "telemetry-time-evidence-stale-or-invalid", phase)
                scratch = {"gaps": []}
                info["metrics"] = _metrics(row["prometheus"], target, window, scratch, phase)
                for g in scratch["gaps"]:
                    if not (fault_scrape and g['code'] in {'telemetry-samples-incomplete', 'telemetry-source-window-mismatch', 'telemetry-stale-at-collection'}):
                        _gap(result, g["code"], phase)
                metrics = info["metrics"]
                expected = (metrics["scrape"] == "down" or metrics["counter"] == "flat") if phase == "fault" else (metrics["scrape"] == "up" and metrics["counter"] == "progressing")
                known = fault_scrape or (metrics["scrape"] != "unknown" and metrics["counter"] not in {"unknown", "reset"})
                _condition(result, inputs, "telemetry-outage" if phase == "fault" else "functional-telemetry", _state(expected, known=known),
                    "source-scrapes-and-counter" if known else "source-scrapes-incomplete", [(phase, f"/targets/{target_id}/prometheus")], phase=phase, target=target_id)
                for key, failure in (("window_scrape", "down"), ("window_counter", "flat")):
                    if metrics[key] == failure:
                        result["retained_failures"].append({"id": "historical-" + failure, "phase": phase, "target_id": target_id,
                            "observed_state": "not-recovered", "code": "historical-window-observation", "authenticated": result["provenance"]["verified"],
                            "evidence": inputs.ref(phase, f"/targets/{target_id}/prometheus")})
                if phase == "recovery":
                    try:
                        obs = row["prometheus"]["observations"]
                        samples = [obs[k]["samples"] for k in ("scrape", "functional")]
                        earliest = min(s["source_timestamp"] for seq in samples for s in seq)
                        last_times = [seq[-1]["source_timestamp"] for seq in samples]
                        if (delivered is None or earliest <= parse_time(delivered["delivered_at"]).timestamp()
                                or any(t > observed.timestamp() or observed.timestamp() - t > window["max_sample_age_seconds"] for t in last_times)):
                            raise ValueError
                    except (ValueError, TypeError, KeyError, IndexError):
                        _gap(result, "recovery-samples-not-fresh-after-delivery", phase)
                        _condition(result, inputs, "post-delivery-sample-freshness", "unknown", "recovery-samples-not-fresh-after-delivery",
                            [(phase, f"/targets/{target_id}/prometheus"), ("delivery", "/delivered_at")], phase=phase, target=target_id)
                    else:
                        _condition(result, inputs, "post-delivery-sample-freshness", "recovered", "all-source-samples-after-delivery-and-current",
                            [(phase, f"/targets/{target_id}/prometheus"), ("delivery", "/delivered_at")], phase=phase, target=target_id)
    complete = all(bundles[p] is not None and bundles[p]["window"] for p in PHASES)
    ordered = complete and all(bundles[a]["window"]["end"] < bundles[b]["window"]["start"]
                               and parse_time(bundles[a]["observed_at"]).timestamp() < bundles[b]["window"]["start"]
                               for a, b in zip(PHASES, PHASES[1:]))
    if ordered and delivered:
        ordered = bundles["fault"]["window"]["end"] < parse_time(delivered["delivered_at"]).timestamp() < bundles["recovery"]["window"]["start"]
        binding = bundles["recovery"].get("delivery")
        ordered = ordered and binding is not None and binding.get("binding") == delivered and binding.get("artifact", {}).get("sha256") == _sha(inputs.raw["delivery"])
    else:
        ordered = False
    _condition(result, inputs, "ordered-measured-windows", _state(ordered, known=ordered), "measured-windows-ordered" if ordered else "window-or-delivery-order-unverified",
        [(p, "/window") for p in PHASES])
    for target_id in targets["recovery"]:
        try:
            values, snapshots, references = [], [], []
            for phase in PHASES:
                row, info = bundles[phase]["targets"][target_id], states[phase][target_id]
                for side in ("before", "after"):
                    values.append(info["kubernetes"][side]["identity"])
                    snapshots.append(row[side]["kubernetes"]["observations"]["image_snapshot"])
                    references.append((phase, f"/targets/{target_id}/{side}/kubernetes/observations/image_snapshot"))
                comparison = _image_comparison(*snapshots[-2:], bundles[phase]["window"])
                if not comparison["intervals_valid"]:
                    raise ValueError
            known = all(values)
            stable = known and all(v == values[0] for v in values)
            _condition(result, inputs, "runtime-images-and-ownership", _state(stable, known=known), "same-pods-containers-images-and-restarts" if stable else "runtime-identity-changed-or-incomplete", references, phase="all", target=target_id)
        except (ValueError, TypeError, KeyError, AttributeError):
            _condition(result, inputs, "runtime-images-and-ownership", "unknown", "runtime-image-bracketing-incomplete", [(p, f"/targets/{target_id}") for p in PHASES], phase="all", target=target_id)
    return bundles, states


def _incomplete(report):
    gaps = report.get("gaps")
    transport = report.get("transport")
    return (report.get("execution_status") != "complete" or not isinstance(gaps, list)
            or any(not isinstance(g, dict) or type(g.get("required")) is not bool or g["required"] for g in gaps)
            or not isinstance(transport, dict) or not isinstance(transport.get("receipts"), list) or not transport["receipts"])


def _inventory(result, inputs, contract, targets, bundles, states):
    inventory = inputs.docs.get("inventory")
    try:
        validate(inventory, RECOVERY_INVENTORY_SCHEMA)
        if any(inventory[k] != contract[k] for k in ("run_id", "attempt_id", "environment", "identities")):
            raise ValueError
        services = {s["target_id"]: s for s in inventory["services"]}
        sources = {s["id"]: s for s in inventory["probe_sources"]}
        if len(services) != len(inventory["services"]) or len(sources) != len(inventory["probe_sources"]) or set(services) != set(targets["recovery"]):
            raise ValueError
        expected_images = {(i["target_id"], i["container_type"], i["container"]): i["runtime_image_id"] for i in inventory["images"]}
        if len(expected_images) != len(inventory["images"]):
            raise ValueError
        approved_images = {(i["target_id"], i["container_type"], i["container"]): i for i in contract["image_expectations"]}
        if set(approved_images) != set(expected_images):
            raise ValueError
        for image in inventory["images"]:
            expected = approved_images[(image["target_id"], image["container_type"], image["container"])]
            if (image["manifest_digest"] != "sha256:" + expected["platform_manifest_sha256"]
                    or image["config_digest"] != "sha256:" + expected["config_sha256"]
                    or image["mapping_receipt_sha256"] != expected["mapping_receipt_sha256"]):
                raise ValueError
        probe_image = expected_images[(contract["mission_target"], "regular", contract["mission_application_container"])]
        if any(source["image_id"] != probe_image for source in sources.values()):
            raise ValueError
        used_images = set()
        for phase in PHASES:
            context = bundles[phase]["context_snapshot"]
            origins = {s["id"]: s["origin"] for s in context["factory_config"]["services"]}
            if digest(origins["kubernetes"]) != inventory["kubernetes_origin_sha256"]:
                raise ValueError
            for target_id, target in targets[phase].items():
                if digest(target["argocd"]["destination"]) != inventory["argo_destination_sha256"]:
                    raise ValueError
                service = services[target_id]
                if service["uid"] != target["kubernetes"]["service_uid"]:
                    raise ValueError
                for address in service["cluster_ips"]:
                    ip = ipaddress.ip_address(address)
                    if str(ip) != address or any((ip.is_loopback, ip.is_link_local, ip.is_multicast, ip.is_unspecified, ip.is_reserved)) or getattr(ip, "ipv4_mapped", None):
                        raise ValueError
                for side in ("before", "after"):
                    identity = states[phase][target_id]["kubernetes"][side]["identity"]
                    for container in identity["containers"]:
                        key = (target_id, container["container_type"], container["container"])
                        if expected_images.get(key) != container["image_id"]:
                            raise ValueError
                        used_images.add(key)
        if used_images != set(expected_images):
            raise ValueError
        telemetry = inventory["telemetry"]
        mission = targets["recovery"][contract["mission_target"]]
        if (telemetry["target_id"] != mission["id"] or telemetry["service_uid"] != mission["kubernetes"]["service_uid"]
                or any(telemetry[k] != mission["prometheus"][k] for k in ("job", "instance"))):
            raise ValueError
        metric_ports = {c["port"] for c in contract["probe_cases"] if c["kind"].startswith("metrics-")}
        if len(metric_ports) != 1:
            raise ValueError
        metric_port = next(iter(metric_ports))
        for phase in PHASES:
            for side in ("before", "after"):
                obs = bundles[phase]["targets"][mission["id"]][side]["kubernetes"]["observations"]
                pods = [p for p in obs["pods"] if p["uid"] == telemetry["pod_uid"]]
                if len(pods) != 1 or not any(telemetry["instance"] == ("[" + ip + "]" if ":" in ip else ip) + ":" + str(metric_port) for ip in pods[0]["pod_ips"]):
                    raise ValueError
                if not any(any(e["pod_uid"] == telemetry["pod_uid"] for e in s["endpoints"])
                           and any(p.get("port") == metric_port for p in s["ports"]) for s in obs["endpoint_slices"]):
                    raise ValueError
        _condition(result, inputs, "independent-inventory-association", "recovered", "inventory-matches-scoped-observations", [("inventory", "")], phase="all")
        topology = inventory["topology"]
        supported = (topology["cni"] == contract["factory_lock"]["platform"]["cni"]
                     and topology["mesh_mode"] == contract["factory_lock"]["platform"]["mesh_mode"]
                     and topology["probe_transport"] == "plain-http-networkpolicy")
        _condition(result, inputs, "qualified-probe-transport", _state(supported, known=supported),
            "qualified-topology-claims-match" if supported else "topology-or-transport-unsupported", [("inventory", "/topology")])
        return inventory
    except (ValueError, TypeError, KeyError, AttributeError):
        _condition(result, inputs, "independent-inventory-association", "unknown", "independent-inventory-missing-or-mismatch", [("inventory", "")], phase="all")
        return None


def _subset(labels, actual):
    return all(actual.get(k) == v for k, v in labels.items())


def _probes(result, inputs, contract, targets, bundles, inventory, delivered, observed):
    records = {}
    for case in contract["probe_cases"]:
        name, phase, target_id = "http:" + case["id"], case["phase"], case["target_id"]
        receipt = inputs.docs.get(name)
        state, code = "unknown", "http-evidence-missing-or-invalid"
        try:
            if inventory is None or receipt is None or bundles[phase] is None:
                raise ValueError
            verify_seal(receipt, "receipt_sha256")
            request, obs = receipt["request"], receipt["observation"]
            if receipt["execution_mode"] != "operator":
                result["execution_mode"] = "controlled-test"
            source = next(s for s in inventory["probe_sources"] if s["id"] == case["source_id"])
            service = next(s for s in inventory["services"] if s["target_id"] == target_id)
            target = targets[phase][target_id]
            source_identity = {k: source[k] for k in ("namespace", "name", "uid")}
            destination = request["destination"]
            start, end = parse_time(receipt["started_at"]), parse_time(receipt["finished_at"])
            expected_host = f"{target['kubernetes']['service']}.{target['kubernetes']['namespace']}.svc.{service['cluster_domain']}"
            if (request["run_id"] != contract["run_id"] or request["attempt_id"] != contract["attempt_id"]
                    or request["identities"] != contract["identities"] or request["phase"] != phase or request["case_id"] != case["id"]
                    or request["source"]["expected"] != source_identity or receipt["source_identity"]["claims"] != source_identity
                    or receipt["request_artifact"]["canonical_sha256"] != digest(request)
                    or destination["namespace"] != target["kubernetes"]["namespace"] or destination["service"] != target["kubernetes"]["service"]
                    or destination["cluster_domain"] != service["cluster_domain"] or destination["scheme"] != "http"
                    or any(destination[k] != case[k] for k in ("port", "path"))
                    or set(destination["expected_addresses"]) != set(service["cluster_ips"])
                    or request["expectation"]["body_sha256"] != case["body_sha256"]
                    or receipt["network"]["host"] != expected_host or set(receipt["network"]["dns_addresses"]) != set(service["cluster_ips"])
                    or not parse_time(source["observed_before"]) <= start <= end <= parse_time(source["observed_after"])
                    or not parse_time(service["observed_before"]) <= start <= end <= parse_time(service["observed_after"])
                    or end.timestamp() - start.timestamp() > request["limits"]["total_seconds"] + 1
                    or receipt["elapsed_seconds"] > request["limits"]["total_seconds"] + 1
                    or not bundles[phase]["window"]["start"] <= start.timestamp() <= end.timestamp() <= bundles[phase]["window"]["end"] + bundles[phase]["window"]["max_sample_age_seconds"]):
                raise ValueError
            for side in ("before", "after"):
                ports = bundles[phase]["targets"][target_id][side]["kubernetes"]["observations"]["service"]["ports"]
                if not any(p["port"] == case["port"] and p.get("protocol", "TCP") == "TCP" for p in ports):
                    raise ValueError
            if phase == "recovery" and (delivered is None or start <= parse_time(delivered["delivered_at"])
                    or end > observed or (observed - end).total_seconds() > contract["factory_lock"]["freshness_seconds"]):
                raise ValueError
            role = case["kind"]
            if role.startswith("metrics-"):
                auth = contract["metrics_authorization"]
                ns, pod = _subset(auth["namespace_labels"], source["namespace_labels"]), _subset(auth["pod_labels"], source["pod_labels"])
                wanted = (False, True) if role == "metrics-denied-wrong-namespace" else (True, False) if role == "metrics-denied-wrong-label" else (True, True)
                if (ns, pod) != wanted:
                    raise ValueError
            is_negative = role.startswith("metrics-denied-")
            if is_negative:
                if obs["status"] is not None:
                    state, code = "not-recovered", "unexpected-http-reachability-from-denied-source"
                elif obs["outcome"] in {"connect-timeout", "connection-refused"} and receipt["network"]["connected_peer"] is None:
                    state, code = "recovered", "scoped-connection-failure-observed-cause-unproven"
            else:
                if obs["status"] is not None and obs["status"] != 200:
                    state, code = "not-recovered", "http-status-mismatch"
                elif (receipt["execution_status"] == "complete" and obs["status"] == 200
                        and obs["outcome"] in {"http-response", "body-mismatch"}
                        and receipt["network"]["connected_peer"] in service["cluster_ips"]
                        and type(obs["body_bytes"]) is int and 0 <= obs["body_bytes"] <= request["limits"]["body_bytes"]
                        and obs["body_sha256"] is not None):
                    state = _state(case["body_sha256"] is None or obs["body_sha256"] == case["body_sha256"])
                    code = "complete-http-response" if state == "recovered" else "http-body-mismatch"
            records[(phase, role)] = {"state": state, "start": start, "end": end, "source": source, "case": case}
        except (ValueError, TypeError, KeyError, AttributeError, StopIteration):
            pass
        _condition(result, inputs, case["kind"], state, code, [(name, "")], phase=phase, target=target_id)
    for phase in ("baseline", "recovery"):
        try:
            before, after = (records[(phase, "metrics-allowed-" + side)] for side in ("before", "after"))
            negatives = [records[(phase, "metrics-denied-" + side)] for side in ("wrong-namespace", "wrong-label")]
            valid = (before["state"] == after["state"] == "recovered" and before["source"]["uid"] == after["source"]["uid"]
                     and all(before["end"] < n["start"] <= n["end"] < after["start"] for n in negatives)
                     and negatives[0]["source"]["namespace_uid"] != before["source"]["namespace_uid"]
                     and negatives[1]["source"]["namespace_uid"] == before["source"]["namespace_uid"]
                     and len({before["source"]["uid"], *(n["source"]["uid"] for n in negatives)}) == 3
                     and all(n["case"]["port"] == before["case"]["port"] == after["case"]["port"] for n in negatives))
            _condition(result, inputs, "scoped-control-bracketing", _state(valid, known=valid),
                "positive-controls-bracket-both-negative-sources" if valid else "independent-probe-controls-incomplete",
                [("http:" + r["case"]["id"], "") for r in [before, *negatives, after]], phase=phase)
        except KeyError:
            _condition(result, inputs, "scoped-control-bracketing", "unknown", "independent-probe-controls-incomplete", [], phase=phase)


def verify_recovery(baseline, fault, recovered, *, contract, evidence, provenance=None,
                    native_execution_origin=None, runtime_origin=None, _test_provenance=None, now=None):
    """Verify frozen evidence offline; absent authentication stays explicitly unknown.

    Evidence uses only the documented closed names and ``http:<case-id>``. A Path
    is read once without symlinks; embedded paths/commands/URLs are inert data.
    ``now`` and ``_test_provenance`` are test hooks, never live qualification.
    """
    if provenance is not None and (not isinstance(provenance, str) or provenance not in _PROVENANCE):
        raise ValueError("Recovery provenance adapter is unavailable")
    inputs = _Inputs()
    approved = inputs.read("contract", contract, schema=RECOVERY_CONTRACT_SCHEMA)
    try:
        targets = _approved(approved)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Recovery contract is invalid or unsupported") from None
    cases = {"http:" + c["id"] for c in approved["probe_cases"]}
    if not isinstance(evidence, dict) or not set(evidence) <= set(BASE_ARTIFACTS) | cases:
        raise ValueError("Recovery evidence names are invalid")
    probe_schema = json.loads((SCHEMAS / "http-probe-receipt.schema.json").read_bytes())
    for name, value in (("baseline", baseline), ("fault", fault), ("recovery", recovered)):
        inputs.read(name, value)
    for name in (*BASE_ARTIFACTS, *sorted(cases)):
        inputs.read(name, evidence.get(name), binary=name == "patch", schema=probe_schema if name in cases else None)
    observed = clock(now)
    result = {"schema_version": 1, "kind": "runtime-recovery", "observation_kind": "independent-recovery-conditions",
        "observed_at": observed.isoformat(), "execution_mode": "controlled-test" if now is not None or _test_provenance is not None else "direct",
        "execution_status": "partial", "decision": "unknown", "recovery": "unknown", "causality": "unproven",
        "confirmed_failure": False, "identities": deepcopy(approved["identities"]), "inputs": inputs.metadata,
        "conditions": [], "retained_failures": [], "gaps": [],
        "provenance": {"adapter": "controlled-test" if _test_provenance is not None else provenance, "verified": False, "receipt_sha256": None, "assurances": []}}
    for name, meta in inputs.metadata.items():
        if meta["state"] != "valid":
            _gap(result, "evidence-" + meta["state"], name)
    _authenticate(result, inputs, provenance, _test_provenance, runtime_origin)
    execution = inputs.docs.get('native_execution', {})
    if isinstance(execution, dict) and execution.get('execution_mode') == 'controlled-test':
        result['execution_mode'] = 'controlled-test'
    if native_execution_origin is not None:
        # This consumes only an opaque object issued by the direct native caller.
        # No JSON/file argument or test factory can reconstruct that authority.
        from devsecops.factory.native_provenance import verify_native_execution_origin
        origin = verify_native_execution_origin(native_execution_origin, artifacts=MappingProxyType(inputs.raw))
        if origin['execution_mode'] == 'controlled-test':
            result['execution_mode'] = 'controlled-test'
        native_known = origin['verified'] and origin['execution_mode'] == 'direct'
        _condition(result, inputs, 'native-execution-origin', 'recovered' if native_known else 'unknown',
                   'native-execution-directly-bound' if native_known else 'native-execution-origin-unverified',
                   [(name, '') for name in ('native_execution', 'native_request', 'native_result', 'native_validation', 'patch')])
        result['conditions'][-1]['authenticated'] = native_known
        if native_known and 'native-execution' not in result['provenance']['assurances']:
            result['provenance']['assurances'].append('native-execution')
    delivered = _delivery(result, inputs, approved, targets)
    _render_scope(result, inputs, approved)
    bundles, states = _bundles(result, inputs, approved, targets, delivered, observed)
    inventory = _inventory(result, inputs, approved, targets, bundles, states)
    _policy_controls(result, inputs, approved, targets, bundles, states, inventory)
    _probes(result, inputs, approved, targets, bundles, inventory, delivered, observed)
    if result["execution_mode"] != "direct":
        _gap(result, "controlled-test-evidence")
    failures = any(c["observed_state"] == "not-recovered" for c in result["conditions"])
    result["confirmed_failure"] = any(c["authenticated"] and c["phase"] != "fault" for c in result["retained_failures"])
    if not result["gaps"]:
        result.update(execution_status="complete", decision="fail" if failures else "pass", recovery="not-recovered" if failures else "verified")
    result = seal(result, "recovery_sha256")
    validate(result, RECOVERY_SCHEMA)
    return result


def _selector_matches(selector, labels):
    if not isinstance(selector, dict) or set(selector) - {"matchLabels", "matchExpressions"} or selector.get("matchExpressions"):
        raise ValueError
    return _subset(selector.get("matchLabels", {}), labels)


def _policy_controls(result, inputs, contract, targets, bundles, states, inventory):
    """Only the qualified simple ingress selector case; no inference from timeouts."""
    mission = contract["mission_target"]
    for phase in ("baseline", "recovery"):
        refs = [(phase, f"/targets/{mission}/after/kubernetes/observations/network_policies"), ("inventory", "/probe_sources")]
        try:
            if inventory is None:
                raise ValueError
            relevant = [c for c in contract["probe_cases"] if c["phase"] == phase and c["kind"].startswith("metrics-")]
            sources = {s["id"]: s for s in inventory["probe_sources"]}
            outcomes = []
            for side in ("before", "after"):
                labels = states[phase][mission]["kubernetes"][side]["labels"]
                policies = bundles[phase]["targets"][mission][side]["kubernetes"]["observations"]["network_policies"]
                # Deployment matchLabels are only a known subset of Pod labels;
                # a policy using an additional key cannot safely be excluded.
                if any(set(p["pod_selector"].get("matchLabels", {})) - set(labels) for p in policies if "Ingress" in p["policy_types"]):
                    raise ValueError
                selected = [p for p in policies if "Ingress" in p["policy_types"] and _selector_matches(p["pod_selector"], labels)]
                for case in relevant:
                    source, permitted = sources[case["source_id"]], not selected
                    for policy in selected:
                        for rule in policy["ingress"]:
                            applies = not rule.get("ports")
                            for port in rule.get("ports", []):
                                if type(port.get("port")) is not int or "endPort" in port:
                                    raise ValueError
                                applies |= port.get("protocol", "TCP") == "TCP" and port["port"] == case["port"]
                            if not applies:
                                continue
                            peers = rule.get("from", [])
                            if not peers:
                                permitted = True
                            for peer in peers:
                                if set(peer) - {"namespaceSelector", "podSelector"}:
                                    raise ValueError
                                ns = (_selector_matches(peer["namespaceSelector"], source["namespace_labels"]) if "namespaceSelector" in peer
                                      else source["namespace"] == targets[phase][mission]["kubernetes"]["namespace"] if "podSelector" in peer else True)
                                pod = _selector_matches(peer.get("podSelector", {}), source["pod_labels"])
                                permitted |= ns and pod
                    outcomes.append(permitted == case["kind"].startswith("metrics-allowed-"))
            state = _state(all(outcomes))
            _condition(result, inputs, "metrics-policy-selector-controls", state,
                "observed-ingress-selectors-match-control-matrix" if state == "recovered" else "observed-ingress-policy-allows-wrong-scope",
                refs, phase=phase, target=mission)
        except (ValueError, TypeError, KeyError, AttributeError):
            _condition(result, inputs, "metrics-policy-selector-controls", "unknown", "policy-control-semantics-unsupported-or-missing", refs, phase=phase, target=mission)
