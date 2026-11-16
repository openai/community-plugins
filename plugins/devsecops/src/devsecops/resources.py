"""Bounded YAML ingestion and Kubernetes object identity.

Only inventory records contain manifest bodies. Gaps deliberately contain no
input snippets: this module is used before evidence is passed to the model.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

import yaml
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent, DocumentStartEvent
from yaml.nodes import MappingNode

MAX_YAML_BYTES = 8 * 1024 * 1024
MAX_DOCUMENTS = 1024
MAX_NODES = 150_000
MAX_DEPTH = 64
MAX_RESOURCES = 5000


class _StrictLoader(yaml.SafeLoader):
    pass


# Keep dates and the legacy YAML value token (=) as JSON strings. Kubernetes
# CRD enums use bare =; PyYAML has an implicit resolver but no safe constructor
# for its obsolete value tag. Explicit value tags remain forbidden below.
# Copy the resolver table rather than mutating PyYAML's global SafeLoader.
_StrictLoader.yaml_implicit_resolvers = {
    key: [(tag, regex) for tag, regex in values
          if tag not in {"tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:value"}]
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _mapping(loader: _StrictLoader, node: MappingNode, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            raise ValueError("YAML merge keys are unsupported.")
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError("YAML mapping keys must be strings.")
        if key in result:
            raise ValueError("YAML contains duplicate mapping keys.")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictLoader.add_constructor("tag:yaml.org,2002:map", _mapping)
_ALLOWED_TAGS = {
    f"tag:yaml.org,2002:{kind}" for kind in ("str", "int", "float", "bool", "null", "seq", "map")
}


def _json_values(value: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ValueError("YAML exceeds the nesting limit.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite YAML numbers are unsupported.")
    if isinstance(value, dict):
        for item in value.values():
            _json_values(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _json_values(item, depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("YAML contains an unsupported scalar type.")


def strict_load_yaml(text: str, source: str = "<input>") -> list[Any]:
    """Load a bounded YAML stream; all errors omit source contents and snippets.

    YAML's ordinary scalar semantics are preserved, except dates and bare = stay
    strings and non-finite numbers are rejected. Values files must still be passed to
    Helm as their original bytes, not serialized from this representation.
    """
    del source  # A parser error must never reflect an untrusted filename/snippet.
    if not isinstance(text, str):
        raise ValueError("YAML input must be text.")
    if len(text.encode("utf-8")) > MAX_YAML_BYTES:
        raise ValueError("YAML exceeds the byte limit.")
    try:
        depth = documents = nodes = 0
        for event in yaml.parse(text, Loader=_StrictLoader):
            nodes += 1
            if nodes > MAX_NODES:
                raise ValueError("YAML exceeds the node limit.")
            if isinstance(event, AliasEvent):
                raise ValueError("YAML aliases are unsupported.")
            tag = getattr(event, "tag", None)
            if tag is not None and tag not in _ALLOWED_TAGS:
                raise ValueError("Custom or unsupported YAML tags are forbidden.")
            if isinstance(event, DocumentStartEvent):
                documents += 1
                if documents > MAX_DOCUMENTS:
                    raise ValueError("YAML exceeds the document limit.")
            if isinstance(event, CollectionStartEvent):
                depth += 1
                if depth > MAX_DEPTH:
                    raise ValueError("YAML exceeds the nesting limit.")
            elif isinstance(event, CollectionEndEvent):
                depth -= 1
        values = list(yaml.load_all(text, Loader=_StrictLoader))
        for value in values:
            _json_values(value)
        return values
    except (yaml.YAMLError, RecursionError, UnicodeError, OverflowError):
        raise ValueError("YAML cannot be parsed within the supported format and limits.") from None


# Explicit metadata, not a guess that every unfamiliar kind is namespaced.
# This registry determines identity only; it does not imply semantic-rule or
# schema support for every listed resource.
_CLUSTER_KINDS = {
    "": {"ComponentStatus", "Namespace", "Node", "PersistentVolume"},
    "apiextensions.k8s.io": {"CustomResourceDefinition"},
    "apiregistration.k8s.io": {"APIService"},
    "rbac.authorization.k8s.io": {"ClusterRole", "ClusterRoleBinding"},
    "networking.k8s.io": {"IngressClass", "IPAddress", "ServiceCIDR"},
    "storage.k8s.io": {"StorageClass", "CSIDriver", "CSINode", "VolumeAttachment", "VolumeAttributesClass"},
    "scheduling.k8s.io": {"PriorityClass"},
    "node.k8s.io": {"RuntimeClass"},
    "certificates.k8s.io": {"CertificateSigningRequest"},
    "admissionregistration.k8s.io": {
        "MutatingWebhookConfiguration", "ValidatingWebhookConfiguration",
        "ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding",
        "MutatingAdmissionPolicy", "MutatingAdmissionPolicyBinding",
    },
    "flowcontrol.apiserver.k8s.io": {"FlowSchema", "PriorityLevelConfiguration"},
}
_NAMESPACED_KINDS = {
    "": {
        "ConfigMap", "Endpoints", "Event", "LimitRange", "PersistentVolumeClaim",
        "Pod", "PodTemplate", "ReplicationController", "ResourceQuota", "Secret",
        "Service", "ServiceAccount",
    },
    "apps": {"ControllerRevision", "DaemonSet", "Deployment", "ReplicaSet", "StatefulSet"},
    "autoscaling": {"HorizontalPodAutoscaler"},
    "batch": {"CronJob", "Job"},
    "networking.k8s.io": {"Ingress", "NetworkPolicy"},
    "policy": {"PodDisruptionBudget"},
    "rbac.authorization.k8s.io": {"Role", "RoleBinding"},
    "storage.k8s.io": {"CSIStorageCapacity"},
    "coordination.k8s.io": {"Lease", "LeaseCandidate"},
    "discovery.k8s.io": {"EndpointSlice"},
    "events.k8s.io": {"Event"},
}
_IDENTITY_PART = re.compile(r"^[A-Za-z0-9_.:-]{1,253}$")
_API_VERSION = re.compile(r"^(?:[a-z0-9][a-z0-9.-]*/)?[a-z0-9][a-z0-9-]{0,62}$")
_KIND = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,127}$")


def _gap(code: str, source: str, message: str, **extra: Any) -> dict:
    return {"code": code, "source": source, "message": message, "required": True, **extra}


def inventory(text: str, namespace: str = "default", source: str = "<input>") -> tuple[list[dict], list[dict]]:
    """Return identified objects and explicit coverage gaps from a YAML stream.

    Ambiguous duplicate identities are all excluded. Unknown resource scopes
    use a provisional identity, never an assumed namespace.
    """
    gaps: list[dict] = []
    if not isinstance(namespace, str) or not _IDENTITY_PART.fullmatch(namespace):
        return [], [_gap("invalid_namespace", source, "The target namespace is invalid.")]
    try:
        documents = strict_load_yaml(text, source)
    except ValueError as exc:
        return [], [_gap("invalid_yaml", source, str(exc))]

    objects: list[tuple[dict, int, str]] = []

    def expand(item: Any, document: int, pointer: str = "", depth: int = 0) -> None:
        if len(objects) >= MAX_RESOURCES:
            raise ValueError("The resource inventory exceeds its limit.")
        if depth > MAX_DEPTH:
            raise ValueError("The resource list exceeds its nesting limit.")
        if not isinstance(item, dict):
            gaps.append(_gap("invalid_resource", source, "A manifest document or list item is not an object.", document=document, pointer=pointer))
            return
        if item.get("kind") == "List":
            if item.get("apiVersion") != "v1" or not isinstance(item.get("items"), list):
                gaps.append(_gap("unsupported_list", source, "Only core v1 List objects with an items array can be expanded.", document=document, pointer=pointer))
                return
            for index, child in enumerate(item["items"]):
                expand(child, document, f"{pointer}/items/{index}", depth + 1)
            return
        objects.append((item, document, pointer))

    try:
        for index, document in enumerate(documents):
            if document is not None:
                expand(document, index)
    except ValueError as exc:
        return [], gaps + [_gap("resource_limit", source, str(exc))]

    crd_scopes: dict[tuple[str, str], str | None] = {}
    for body, document, pointer in objects:
        if body.get("apiVersion") != "apiextensions.k8s.io/v1" or body.get("kind") != "CustomResourceDefinition":
            continue
        spec = body.get("spec")
        if not isinstance(spec, dict):
            continue
        names = spec.get("names")
        group = spec.get("group")
        kind = names.get("kind") if isinstance(names, dict) else None
        scope = spec.get("scope")
        if not isinstance(group, str) or not isinstance(kind, str) or scope not in ("Namespaced", "Cluster"):
            gaps.append(_gap("invalid_crd_scope", source, "A supplied CRD lacks valid scope metadata.", document=document, pointer=f"{pointer}/spec/scope"))
            continue
        key = (group, kind)
        if key in crd_scopes and crd_scopes[key] != scope:
            crd_scopes[key] = None
            gaps.append(_gap("conflicting_crd_scope", source, "Supplied CRDs disagree about a resource scope.", document=document, pointer=f"{pointer}/spec/scope"))
        else:
            crd_scopes[key] = scope

    records: list[dict] = []
    for body, document, pointer in objects:
        api_version = body.get("apiVersion")
        kind = body.get("kind")
        metadata = body.get("metadata")
        name = metadata.get("name") if isinstance(metadata, dict) else None
        if (
            not isinstance(api_version, str) or not _API_VERSION.fullmatch(api_version)
            or not isinstance(kind, str) or not _KIND.fullmatch(kind)
            or not isinstance(name, str) or not _IDENTITY_PART.fullmatch(name)
        ):
            gaps.append(_gap("invalid_identity", source, "A resource lacks a supported apiVersion, kind, or fixed metadata.name; generated names cannot establish identity.", document=document, pointer=pointer))
            continue
        group = api_version.split("/", 1)[0] if "/" in api_version else ""
        declared_namespace = metadata.get("namespace")
        if declared_namespace is not None and (not isinstance(declared_namespace, str) or not _IDENTITY_PART.fullmatch(declared_namespace)):
            gaps.append(_gap("invalid_identity", source, "A resource declares an invalid namespace.", document=document, pointer=f"{pointer}/metadata/namespace"))
            continue
        if kind in _CLUSTER_KINDS.get(group, set()):
            scope = "Cluster"
        elif kind in _NAMESPACED_KINDS.get(group, set()):
            scope = "Namespaced"
        else:
            scope = crd_scopes.get((group, kind))
        if scope == "Cluster":
            effective_namespace = ""
            scope_key = "<cluster>"
            if declared_namespace:
                gaps.append(_gap("cluster_resource_namespace", source, "A cluster-scoped resource declares a namespace; admission behavior has not been validated.", document=document, pointer=f"{pointer}/metadata/namespace"))
        elif scope == "Namespaced":
            effective_namespace = declared_namespace or namespace
            scope_key = effective_namespace
        else:
            effective_namespace = "?"
            # Retain the declared namespace in the provisional key, without
            # treating it as proof of resource scope.
            scope_key = f"<unknown:{declared_namespace or 'unset'}>"
        resource_id = f"{group or 'core'}/{kind}/{scope_key}/{name}"
        if scope is None:
            gaps.append(_gap("unknown_scope", source, "Resource scope is unknown; namespace-dependent conclusions are disabled.", resource=resource_id, document=document, pointer=pointer))
        records.append({
            "id": resource_id, "api_version": api_version, "kind": kind,
            "namespace": effective_namespace, "name": name, "source": source,
            "document_index": document, "source_pointer": pointer,
            "scope": scope or "Unknown", "body": body,
        })

    counts = Counter(record["id"] for record in records)
    duplicates = {resource_id for resource_id, count in counts.items() if count > 1}
    for resource_id in sorted(duplicates):
        gaps.append(_gap("duplicate_identity", source, "Multiple resources have the same identity; all ambiguous occurrences are excluded.", resource=resource_id))
    return [record for record in records if record["id"] not in duplicates], gaps
