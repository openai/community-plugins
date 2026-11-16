"""Small, deterministic Kubernetes change checks with value-free evidence.

These checks describe declarations. They do not establish cluster admission,
effective RBAC, network enforcement, runtime identity, or deployment success.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_SPECIALIZED = {
    ("apps/v1", "Deployment"),
    *(("v1", kind) for kind in ("Pod", "Service", "ServiceAccount", "Namespace", "PersistentVolumeClaim")),
    *(("rbac.authorization.k8s.io/v1", kind) for kind in ("Role", "ClusterRole", "RoleBinding", "ClusterRoleBinding")),
    ("networking.k8s.io/v1", "NetworkPolicy"),
}
_SEVERITY = {"low": 0, "medium": 1, "high": 2}
_DIGEST = re.compile(r"@sha256:[a-fA-F0-9]{64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,253}$")
_DANGEROUS_CAPABILITIES = {"ALL", "SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "DAC_READ_SEARCH", "SYS_MODULE", "SYS_RAWIO"}


def _obj(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _array(value: Any) -> list:
    return value if isinstance(value, list) else []


def _strings(value: Any) -> set[str]:
    return {item for item in _array(value) if isinstance(item, str)}


def _ptr(key: Any) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def _signature(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _finding(record: dict, rule: str, severity: str, field: str, message: str, remediation: str, *, anchor: str | None = None, change: str | None = None) -> dict:
    key = f"{rule}|{record['id']}|{anchor or field}"
    result = {
        "rule_id": rule, "severity": severity, "resource": record["id"],
        "field": field, "message": message, "remediation": remediation,
        "evidence": {
            "source": record.get("source", "<input>"),
            "pointer": record.get("source_pointer", "") + field,
            "document": record.get("document_index", 0),
        },
        "source_type": "deterministic",
        "fingerprint": hashlib.sha256(key.encode()).hexdigest()[:24],
    }
    if change is not None:
        result["change"] = change
    return result


def _pod(record: dict) -> tuple[dict, str] | None:
    body = _obj(record.get("body"))
    if (record.get("api_version"), record.get("kind")) == ("v1", "Pod"):
        return _obj(body.get("spec")), "/spec"
    if (record.get("api_version"), record.get("kind")) == ("apps/v1", "Deployment"):
        return _obj(_obj(_obj(body.get("spec")).get("template")).get("spec")), "/spec/template/spec"
    return None


def _containers(pod: dict, path: str) -> list[tuple[dict, str, str]]:
    result = []
    for category in ("containers", "initContainers", "ephemeralContainers"):
        for index, container in enumerate(_array(pod.get(category))):
            if not isinstance(container, dict):
                continue
            name = container.get("name")
            identity = name if isinstance(name, str) and _SAFE_NAME.fullmatch(name) else str(index)
            result.append((container, f"{path}/{category}/{index}", f"{category}:{identity}"))
    return result


def _effective_security(pod: dict, container: dict) -> dict:
    pod_security = _obj(pod.get("securityContext"))
    security = _obj(container.get("securityContext"))
    inherited = {key: pod_security[key] for key in ("runAsNonRoot", "runAsUser", "seccompProfile") if key in pod_security}
    return {**inherited, **security}


def _labels(record: dict) -> dict | None:
    if record.get("namespace") == "?":
        return None
    body = _obj(record.get("body"))
    if (record.get("api_version"), record.get("kind")) == ("v1", "Pod"):
        return _obj(_obj(body.get("metadata")).get("labels"))
    if (record.get("api_version"), record.get("kind")) == ("apps/v1", "Deployment"):
        return _obj(_obj(_obj(_obj(body.get("spec")).get("template")).get("metadata")).get("labels"))
    return None


def _selector_matches(selector: dict, labels: dict) -> bool | None:
    """Support Kubernetes LabelSelector operations; malformed forms stay unknown."""
    match_labels = selector.get("matchLabels", {})
    expressions = selector.get("matchExpressions", [])
    if not isinstance(match_labels, dict) or not isinstance(expressions, list):
        return None
    if not all(isinstance(value, str) for value in labels.values()) or not all(isinstance(value, str) for value in match_labels.values()):
        return None
    matched = all(key in labels and labels[key] == value for key, value in match_labels.items())
    for requirement in expressions:
        if not isinstance(requirement, dict) or not isinstance(requirement.get("key"), str):
            return None
        key, operation = requirement["key"], requirement.get("operator")
        raw_values = requirement.get("values", [])
        if not isinstance(raw_values, list) or not all(isinstance(value, str) for value in raw_values):
            return None
        values = set(raw_values)
        if operation == "In" and values:
            matched = matched and key in labels and labels[key] in values
        elif operation == "NotIn" and values:
            matched = matched and (key not in labels or labels[key] not in values)
        elif operation == "Exists" and not values:
            matched = matched and key in labels
        elif operation == "DoesNotExist" and not values:
            matched = matched and key not in labels
        else:
            return None
    return matched


def _registry(image: str) -> str:
    first = image.split("/", 1)[0]
    if "/" in image and ("." in first or ":" in first or first == "localhost"):
        return first.lower()
    return "docker.io"


def _workload_checks(record: dict, policy: dict) -> list[dict]:
    result: list[dict] = []
    pod_details = _pod(record)
    if pod_details is None:
        return result
    pod, path = pod_details
    for flag in ("hostNetwork", "hostPID", "hostIPC"):
        if pod.get(flag) is True:
            result.append(_finding(record, "K8S-HOST-ACCESS", "high", f"{path}/{flag}", "The workload explicitly shares a host namespace.", "Confirm the host access is required; otherwise disable this setting and validate workload behavior."))
    for index, volume in enumerate(_array(pod.get("volumes"))):
        if isinstance(volume, dict) and isinstance(volume.get("hostPath"), dict):
            volume_name = volume.get("name")
            anchor = f"volume:{volume_name}:hostPath" if isinstance(volume_name, str) and _SAFE_NAME.fullmatch(volume_name) else f"volume:{index}:hostPath"
            result.append(_finding(record, "K8S-HOSTPATH", "high", f"{path}/volumes/{index}/hostPath", "The workload declares a hostPath volume for node filesystem storage.", "Verify the node path and mount permissions are necessary and constrained; prefer an appropriate managed volume where possible.", anchor=anchor))
    for container, container_path, anchor in _containers(pod, path):
        security = _effective_security(pod, container)
        if security.get("privileged") is True:
            result.append(_finding(record, "K8S-PRIVILEGED", "high", f"{container_path}/securityContext/privileged", "A container explicitly requests privileged execution.", "Remove privileged execution unless the platform owner has reviewed the concrete requirement.", anchor=f"{anchor}:privileged"))
        if security.get("allowPrivilegeEscalation") is True:
            result.append(_finding(record, "K8S-ESCALATION", "medium", f"{container_path}/securityContext/allowPrivilegeEscalation", "A container explicitly permits privilege escalation; this is a declared setting, not proof of a new runtime capability.", "Set allowPrivilegeEscalation to false where the workload permits it and validate the application.", anchor=f"{anchor}:allowPrivilegeEscalation"))
        if security.get("runAsUser") == 0 and not isinstance(security.get("runAsUser"), bool):
            own_security = _obj(container.get("securityContext"))
            field = f"{container_path}/securityContext/runAsUser" if "runAsUser" in own_security else f"{path}/securityContext/runAsUser"
            result.append(_finding(record, "K8S-ROOT-USER", "medium", field, "A container's effective manifest setting requests user ID zero.", "Use an appropriate nonzero user when supported; validate image and filesystem requirements.", anchor=f"{anchor}:runAsUser"))
        capabilities = _obj(security.get("capabilities"))
        additions = _strings(capabilities.get("add"))
        if additions:
            result.append(_finding(record, "K8S-CAPABILITIES", "high" if additions & _DANGEROUS_CAPABILITIES else "medium", f"{container_path}/securityContext/capabilities/add", "A container explicitly requests additional Linux capabilities.", "Retain only capabilities demonstrated necessary for the workload and review their effect with the platform owner.", anchor=f"{anchor}:capabilities"))
        image = container.get("image")
        if isinstance(image, str):
            if not _DIGEST.search(image):
                required = policy.get("require_image_digest") is True
                result.append(_finding(record, "POLICY-IMAGE-DIGEST" if required else "K8S-IMAGE-MUTABLE", "high" if required else "low", f"{container_path}/image", "The image reference is not pinned to a supported complete SHA-256 digest.", "Use the verified immutable image digest; the review does not scan the image or establish its provenance.", anchor=f"{anchor}:image-digest"))
            registries = policy.get("allowed_registries", [])
            if isinstance(registries, list) and registries and _registry(image) not in {value.lower() for value in registries if isinstance(value, str)}:
                result.append(_finding(record, "POLICY-IMAGE-REGISTRY", "high", f"{container_path}/image", "The image's registry is outside the configured exact registry allowlist.", "Select an image from an approved registry or request a documented policy exception.", anchor=f"{anchor}:image-registry"))
    if record.get("kind") == "Deployment":
        spec = _obj(_obj(record.get("body")).get("spec"))
        selector = spec.get("selector")
        if isinstance(selector, dict) and selector and _selector_matches(selector, _labels(record) or {}) is False:
            result.append(_finding(record, "K8S-DEPLOYMENT-SELECTOR", "high", "/spec/selector", "The Deployment selector does not match its own Pod template labels.", "Align the selector and template labels; for an existing Deployment, account for selector immutability."))
    return result


def _referenced_role(binding: dict, records: list[dict]) -> dict | None:
    reference = _obj(_obj(binding.get("body")).get("roleRef"))
    if reference.get("apiGroup") != "rbac.authorization.k8s.io" or reference.get("kind") not in ("Role", "ClusterRole"):
        return None
    if binding.get("kind") == "ClusterRoleBinding" and reference.get("kind") != "ClusterRole":
        return None
    for record in records:
        if record.get("api_version") != "rbac.authorization.k8s.io/v1" or record.get("kind") != reference["kind"] or record.get("name") != reference.get("name"):
            continue
        if record["kind"] == "ClusterRole" or record.get("namespace") == binding.get("namespace"):
            return record
    return None


def _rbac_checks(record: dict, all_records: list[dict] | None = None) -> list[dict]:
    result = []
    body = _obj(record.get("body"))
    if record.get("kind") in ("Role", "ClusterRole"):
        for index, rule in enumerate(_array(body.get("rules"))):
            if not isinstance(rule, dict):
                continue
            path = f"/rules/{index}"
            # Rule position is not an authorization identity. A content hash
            # prevents benign reordering from looking like newly added access.
            rule_anchor = hashlib.sha256(_signature({key: sorted(_strings(rule.get(key))) for key in ("apiGroups", "resources", "verbs", "resourceNames", "nonResourceURLs")}).encode()).hexdigest()
            groups, resources, verbs = (_strings(rule.get(key)) for key in ("apiGroups", "resources", "verbs"))
            if any("*" in _strings(rule.get(key)) for key in ("apiGroups", "resources", "verbs")) or any(value.startswith("*/") for value in resources) or any("*" in value for value in _strings(rule.get("nonResourceURLs"))):
                result.append(_finding(record, "K8S-RBAC-WILDCARD", "high", path, "An RBAC rule declares wildcard API, resource, verb, or non-resource URL access.", "Replace wildcards with the permissions required by the workload and review the resulting declaration.", anchor=f"{rule_anchor}:wildcard"))
            if "rbac.authorization.k8s.io" in groups and resources & {"roles", "clusterroles"} and verbs & {"bind", "escalate"}:
                result.append(_finding(record, "K8S-RBAC-DELEGATION", "high", path, "An RBAC rule explicitly grants role binding or escalation privileges.", "Confirm this delegation is necessary and narrowly scoped; this check does not compute effective authorization.", anchor=f"{rule_anchor}:delegation"))
            if "" in groups and "secrets" in resources and verbs & {"get", "list", "watch"}:
                result.append(_finding(record, "K8S-RBAC-SECRETS", "high", path, "An RBAC rule explicitly grants access to Secret contents.", "Confirm the service needs these Secret operations and constrain resource scope where possible.", anchor=f"{rule_anchor}:secrets"))
            impersonation_resource = ("" in groups and bool(resources & {"users", "groups", "serviceaccounts"})) or ("authentication.k8s.io" in groups and any(value == "uids" or value.startswith("userextras/") for value in resources))
            if "impersonate" in verbs and impersonation_resource:
                result.append(_finding(record, "K8S-RBAC-IMPERSONATE", "high", path, "An RBAC rule declares impersonation privileges.", "Review the allowed identities and operations with the authorization owner.", anchor=f"{rule_anchor}:impersonate"))
            if "" in groups and "serviceaccounts/token" in resources and "create" in verbs:
                result.append(_finding(record, "K8S-RBAC-TOKEN", "high", path, "An RBAC rule explicitly permits service-account token requests.", "Confirm which service accounts may be targeted and constrain token-creation permissions.", anchor=f"{rule_anchor}:token"))
    elif record.get("kind") in ("RoleBinding", "ClusterRoleBinding"):
        role = _obj(body.get("roleRef"))
        if role.get("kind") == "ClusterRole" and role.get("name") == "cluster-admin" and role.get("apiGroup") == "rbac.authorization.k8s.io":
            result.append(_finding(record, "K8S-RBAC-CLUSTER-ADMIN", "high", "/roleRef", "The binding references the cluster-admin ClusterRole; the binding's scope and actual role definition still matter.", "Use a narrower role when possible and verify the referenced role and intended subjects."))
        referenced = _referenced_role(record, all_records or [])
        if referenced is not None and any(item["severity"] == "high" for item in _rbac_checks(referenced)):
            for index, subject in enumerate(_array(body.get("subjects"))):
                if not isinstance(subject, dict) or subject.get("kind") not in ("User", "Group", "ServiceAccount") or not isinstance(subject.get("name"), str):
                    continue
                anchor = hashlib.sha256(_signature(subject).encode()).hexdigest()
                item = _finding(record, "K8S-RBAC-SENSITIVE-BINDING", "high", f"/subjects/{index}", "The binding associates a subject with a supplied role declaring broad or sensitive permissions; effective authorization remains unverified.", "Confirm the subject and binding scope need the role's declared permissions, and narrow the role or binding where possible.", anchor=f"subject:{anchor}")
                item["evidence"]["related"] = {"resource": referenced["id"], "source": referenced.get("source", "<input>"), "pointer": referenced.get("source_pointer", "") + "/rules", "document": referenced.get("document_index", 0)}
                result.append(item)
    return result


def _network_checks(record: dict, all_records: list[dict]) -> list[dict]:
    result = []
    body = _obj(record.get("body"))
    spec = _obj(body.get("spec"))
    if record.get("kind") == "Service":
        service_type = spec.get("type", "ClusterIP")
        if service_type in ("LoadBalancer", "NodePort"):
            result.append(_finding(record, "K8S-SERVICE-EXPOSURE", "high", "/spec/type", "The Service requests LoadBalancer or NodePort exposure; public reachability is not established by this declaration.", "Confirm the intended audience, provider settings, and network restrictions for this exposure."))
        if _array(spec.get("externalIPs")):
            result.append(_finding(record, "K8S-SERVICE-EXTERNAL-IPS", "medium", "/spec/externalIPs", "The Service declares external IP addresses; actual routing and reachability are unverified.", "Confirm address ownership and the intended network paths."))
        selector = spec.get("selector")
        if service_type != "ExternalName" and isinstance(selector, dict) and selector and record.get("namespace") != "?":
            matches = []
            for other in all_records:
                if other.get("namespace") != record.get("namespace"):
                    continue
                labels = _labels(other)
                if labels is not None and all(key in labels and labels[key] == value for key, value in selector.items()):
                    matches.append(other)
            if not matches:
                result.append(_finding(record, "K8S-SERVICE-SELECTOR", "medium", "/spec/selector", "No supplied Pod or Deployment in the same namespace matches the Service selector; endpoints may be owned outside this inventory.", "Check the complete workload inventory and EndpointSlices before changing the selector."))
    elif record.get("kind") == "NetworkPolicy":
        policy_types = spec.get("policyTypes")
        if not isinstance(policy_types, list):
            policy_types = ["Ingress"] + (["Egress"] if _array(spec.get("egress")) else [])
        for direction, peer_field in (("ingress", "from"), ("egress", "to")):
            if direction.title() not in policy_types:
                continue
            for index, rule in enumerate(_array(spec.get(direction))):
                if not isinstance(rule, dict):
                    continue
                peers = rule.get(peer_field, [])
                ports = rule.get("ports", [])
                if not isinstance(peers, list) or not isinstance(ports, list):
                    continue
                all_peers = not peers or any(isinstance(peer, dict) and not peer for peer in peers)
                if all_peers and not ports:
                    result.append(_finding(record, "K8S-NETPOL-ALLOW-ALL", "high", f"/spec/{direction}/{index}", f"A NetworkPolicy rule declares unrestricted {direction} for selected pods; runtime enforcement is unverified.", "Constrain peers and ports to the intended traffic and review the union of applicable policies.", anchor=f"{direction}:allow-all"))
    return result


def _snapshot_checks(records: list[dict], policy: dict) -> list[dict]:
    result = []
    for record in records:
        if (record.get("api_version"), record.get("kind")) in _SPECIALIZED:
            result.extend(_workload_checks(record, policy))
            if record.get("api_version") == "rbac.authorization.k8s.io/v1":
                result.extend(_rbac_checks(record, records))
            if (record.get("api_version"), record.get("kind")) in (("v1", "Service"), ("networking.k8s.io/v1", "NetworkPolicy")):
                result.extend(_network_checks(record, records))
        labels = _obj(_obj(_obj(record.get("body")).get("metadata")).get("labels"))
        for label in policy.get("required_labels", []) if isinstance(policy.get("required_labels", []), list) else []:
            if isinstance(label, str) and label not in labels:
                result.append(_finding(record, "POLICY-REQUIRED-LABEL", "high", f"/metadata/labels/{_ptr(label)}", "A configured required metadata label is absent.", "Add the required label using the customer-owned metadata convention."))
    return result


def _diff_fields(base: Any, head: Any, path: str = "", *, sensitive: bool = False) -> list[str]:
    if _signature(base) == _signature(head):
        return []
    if sensitive and path in ("/data", "/stringData", "/binaryData"):
        return [path]
    if isinstance(base, dict) and isinstance(head, dict):
        result = []
        for key in sorted(base.keys() | head.keys()):
            child = f"{path}/{_ptr(key)}"
            if key not in base or key not in head:
                result.append(child)
            else:
                result.extend(_diff_fields(base[key], head[key], child, sensitive=sensitive))
            if len(result) >= 200:
                return result[:200]
        return result
    if isinstance(base, list) and isinstance(head, list):
        result = []
        for index in range(max(len(base), len(head))):
            child = f"{path}/{index}"
            if index >= len(base) or index >= len(head):
                result.append(child)
            else:
                result.extend(_diff_fields(base[index], head[index], child, sensitive=sensitive))
            if len(result) >= 200:
                return result[:200]
        return result
    return [path or "/"]


def _transition_checks(base: dict | None, head: dict | None) -> list[dict]:
    record = head or base
    if record is None:
        return []
    result: list[dict] = []
    before = _obj(base.get("body")) if base else {}
    after = _obj(head.get("body")) if head else {}
    known = (record.get("api_version"), record.get("kind")) in _SPECIALIZED
    if known and base and not head and record.get("kind") in ("Namespace", "PersistentVolumeClaim"):
        result.append(_finding(record, "K8S-DESTRUCTIVE-REMOVAL", "high", "/metadata/name", "The Namespace or PersistentVolumeClaim declaration is removed from the reviewed target; actual deletion and data retention depend on the deployment process.", "Confirm intentional removal, dependent resources, retention settings, and a tested migration or recovery plan.", change="introduced"))
    if known and base and head and record.get("kind") == "Deployment":
        if _obj(before.get("spec")).get("selector") != _obj(after.get("spec")).get("selector"):
            result.append(_finding(record, "K8S-IMMUTABLE-SELECTOR", "high", "/spec/selector", "The apps/v1 Deployment selector changes; Kubernetes does not permit this field to be updated in place.", "Retain the selector or explicitly design and validate a replacement migration.", change="introduced"))
    if known and base and head and record.get("kind") == "PersistentVolumeClaim":
        for field in ("storageClassName", "accessModes", "volumeMode", "selector", "volumeName"):
            if _obj(before.get("spec")).get(field) != _obj(after.get("spec")).get(field):
                result.append(_finding(record, "K8S-PVC-MIGRATION", "high", f"/spec/{field}", "A PVC binding or storage-configuration field changes; supported updates depend on claim state and may require migration.", "Verify the exact API update rules and claim state; review storage migration and data recovery before replacing the claim.", change="introduced"))
    if known and record.get("kind") in ("RoleBinding", "ClusterRoleBinding"):
        if base and head and before.get("roleRef") != after.get("roleRef"):
            result.append(_finding(record, "K8S-IMMUTABLE-ROLE-REF", "high", "/roleRef", "The binding roleRef changes; this field cannot be updated in place.", "Review an intentional binding replacement and verify subject scope and the referenced role.", change="introduced"))
        subjects_before = sorted(_signature(item) for item in _array(before.get("subjects")))
        subjects_after = sorted(_signature(item) for item in _array(after.get("subjects")))
        if subjects_before != subjects_after:
            result.append(_finding(record, "K8S-RBAC-BINDING-CHANGE", "medium", "/subjects", "The binding's declared subjects change; effective authorization has not been computed.", "Confirm the intended identities, binding scope, and referenced role with the authorization owner.", change="introduced"))
    if known and record.get("kind") == "NetworkPolicy" and before.get("spec") != after.get("spec"):
        action = "removed" if head is None else "added" if base is None else "changed"
        result.append(_finding(record, "K8S-NETPOL-CHANGE", "medium", "/spec", f"A NetworkPolicy declaration is {action}; the effect depends on all applicable policies and enforcement.", "Review policy selectors, directions, peers, and ports together; verify intended connectivity in an approved environment.", change="introduced"))
    if record.get("kind") == "CustomResourceDefinition" and before != after:
        result.append(_finding(record, "K8S-CRD-LIFECYCLE", "medium", "/spec", "A CRD declaration changes; schema comparison cannot establish conversion, stored-data, or controller lifecycle safety.", "Review served/storage versions, conversion behavior, stored resources, and the controller's migration procedure.", change="introduced"))
    before_hook = _obj(_obj(before.get("metadata")).get("annotations")).get("helm.sh/hook")
    after_hook = _obj(_obj(after.get("metadata")).get("annotations")).get("helm.sh/hook")
    if (before_hook or after_hook) and before != after:
        result.append(_finding(record, "K8S-HELM-HOOK", "medium", "/metadata/annotations/helm.sh~1hook", "A Helm hook resource changes; static rendering does not execute it or prove lifecycle and rollback behavior.", "Review hook events, order, deletion policy, idempotence, and failure handling separately.", change="introduced"))

    before_pod = _pod(base) if base else None
    after_pod = _pod(head) if head else None
    if before_pod and after_pod:
        old_pod, old_path = before_pod
        new_pod, new_path = after_pod
        old_containers = {anchor: (container, path) for container, path, anchor in _containers(old_pod, old_path)}
        for container, path, anchor in _containers(new_pod, new_path):
            if anchor not in old_containers:
                continue
            old_container, old_container_path = old_containers[anchor]
            old_security = _effective_security(old_pod, old_container)
            new_security = _effective_security(new_pod, container)
            weakens = (
                ("allowPrivilegeEscalation", False, "high", "K8S-ESCALATION", "The container loses its explicit prohibition on privilege escalation."),
                ("runAsNonRoot", True, "high", "K8S-NONROOT-WEAKENING", "The container loses its effective manifest requirement to run as non-root."),
                ("readOnlyRootFilesystem", True, "medium", "K8S-ROOTFS-WEAKENING", "The container loses its explicit read-only root filesystem setting."),
            )
            for field, protected, severity, rule, message in weakens:
                if old_security.get(field) is protected and new_security.get(field) is not protected:
                    old_location = old_container_path if field in _obj(old_container.get("securityContext")) else old_path
                    old_pointer = f"{old_location}/securityContext/{field}"
                    if field in new_security:
                        location = path if field in _obj(container.get("securityContext")) else new_path
                        pointer, side = f"{location}/securityContext/{field}", "head"
                    else:
                        pointer, side = old_pointer, "base"
                    item = _finding(record, rule, severity, pointer, message, "Restore the control or document and validate the workload requirement for relaxing it.", anchor=f"{anchor}:{field}", change="worsened")
                    item["evidence"]["side"] = side
                    item["evidence"]["previous"] = {"source": base.get("source", "<input>"), "pointer": base.get("source_pointer", "") + old_pointer, "document": base.get("document_index", 0), "side": "base"}
                    if side == "base":
                        item["evidence"].update(source=base.get("source", "<input>"), pointer=base.get("source_pointer", "") + pointer, document=base.get("document_index", 0))
                    result.append(item)
            old_seccomp = _obj(old_security.get("seccompProfile")).get("type")
            new_seccomp = _obj(new_security.get("seccompProfile")).get("type")
            if old_seccomp in ("RuntimeDefault", "Localhost") and new_seccomp not in ("RuntimeDefault", "Localhost"):
                old_location = old_container_path if "seccompProfile" in _obj(old_container.get("securityContext")) else old_path
                old_pointer = f"{old_location}/securityContext/seccompProfile"
                if "seccompProfile" in new_security:
                    location = path if "seccompProfile" in _obj(container.get("securityContext")) else new_path
                    evidence_record, pointer, side = record, f"{location}/securityContext/seccompProfile", "head"
                else:
                    evidence_record, pointer, side = base, old_pointer, "base"
                item = _finding(evidence_record, "K8S-SECCOMP-WEAKENING", "high", pointer, "The container loses its effective manifest seccomp profile requirement; node defaults have not been established.", "Retain an appropriate seccomp profile or validate the exact replacement control.", anchor=f"{anchor}:seccompProfile", change="worsened")
                item["evidence"]["side"] = side
                item["evidence"]["previous"] = {"source": base.get("source", "<input>"), "pointer": base.get("source_pointer", "") + old_pointer, "document": base.get("document_index", 0), "side": "base"}
                result.append(item)
        if old_pod.get("automountServiceAccountToken") is False and new_pod.get("automountServiceAccountToken") is not False:
            result.append(_finding(record, "K8S-TOKEN-AUTOMOUNT", "medium", f"{new_path}/automountServiceAccountToken", "The workload removes an explicit prohibition on service-account token automounting; service-account defaults remain unverified.", "Keep automount disabled unless the workload needs Kubernetes API credentials.", change="worsened"))
    return result


def compare_and_check(base_records: list[dict], head_records: list[dict], policy: dict) -> dict:
    """Compare inventories and evaluate supported checks without exposing bodies."""
    gaps: list[dict] = []

    def index(records: list[dict], side: str) -> dict[str, dict]:
        indexed = {}
        duplicates = set()
        for record in records:
            resource_id = record["id"]
            if resource_id in indexed:
                duplicates.add(resource_id)
            else:
                indexed[resource_id] = record
        for resource_id in sorted(duplicates):
            indexed.pop(resource_id, None)
            gaps.append({"code": "duplicate_identity", "required": True, "resource": resource_id, "side": side, "message": "Duplicate identities cannot be compared; ambiguous records are excluded."})
        return indexed

    base, head = index(base_records, "base"), index(head_records, "head")
    # An ambiguous identity on either side cannot become a fabricated deletion
    # or addition merely because the other side happened to be unambiguous.
    for gap in gaps:
        base.pop(gap["resource"], None)
        head.pop(gap["resource"], None)
    changes: list[dict] = []
    for resource_id in sorted(base.keys() | head.keys()):
        before, after = base.get(resource_id), head.get(resource_id)
        record = after or before
        if before is None:
            action, fields = "added", ["/"]
        elif after is None:
            action, fields = "removed", ["/"]
        else:
            fields = _diff_fields(before["body"], after["body"], sensitive=record["kind"] in ("Secret", "ConfigMap"))
            if not fields:
                continue
            action = "modified"
        changes.append({"resource": resource_id, "change": action, "kind": record["kind"], "source": record.get("source", "<input>"), "fields": fields, "fields_truncated": len(fields) >= 200})

    for resource_id in sorted(base.keys() | head.keys()):
        record = head.get(resource_id) or base[resource_id]
        if (record["api_version"], record["kind"]) not in _SPECIALIZED:
            gaps.append({"code": "specialized_rules_unavailable", "required": False, "resource": resource_id, "message": "This kind/version receives inventory, structural comparison, and separately available schema checks; specialized behavior is unvalidated."})
        if record.get("api_version") == "rbac.authorization.k8s.io/v1":
            body = _obj(record.get("body"))
            if record.get("kind") == "ClusterRole" and body.get("aggregationRule") is not None:
                gaps.append({"code": "rbac_aggregation_unvalidated", "required": False, "resource": resource_id, "message": "Aggregated ClusterRole membership is not resolved; declared rules do not establish effective permissions."})
            if record.get("kind") in ("RoleBinding", "ClusterRoleBinding") and _referenced_role(record, list(head.values()) if resource_id in head else list(base.values())) is None:
                gaps.append({"code": "rbac_referenced_role_unavailable", "required": False, "resource": resource_id, "message": "The referenced role is unavailable in the supplied inventory; granted permissions cannot be evaluated from the binding alone."})

    before_findings = {item["fingerprint"]: item for item in _snapshot_checks(list(base.values()), policy)}
    after_findings = {item["fingerprint"]: item for item in _snapshot_checks(list(head.values()), policy)}
    findings: dict[str, dict] = {}
    for key in sorted(before_findings.keys() | after_findings.keys()):
        old, new = before_findings.get(key), after_findings.get(key)
        if new is None:
            item = {**old, "change": "resolved", "evidence": {**old["evidence"], "side": "base"}}
        else:
            classification = "introduced" if old is None else "worsened" if _SEVERITY[new["severity"]] > _SEVERITY[old["severity"]] else "preexisting"
            item = {**new, "change": classification, "evidence": {**new["evidence"], "side": "head"}}
        findings[key] = item
    for resource_id in sorted(base.keys() | head.keys()):
        for item in _transition_checks(base.get(resource_id), head.get(resource_id)):
            # A transition-specific weakening explanation is more precise than
            # a generic static observation at the same anchor.
            item["evidence"].setdefault("side", "head" if resource_id in head else "base")
            findings[item["fingerprint"]] = item
    ordered = sorted(findings.values(), key=lambda item: (-_SEVERITY[item["severity"]], item["resource"], item["rule_id"], item["field"]))
    return {"changes": changes, "findings": ordered, "gaps": gaps}
