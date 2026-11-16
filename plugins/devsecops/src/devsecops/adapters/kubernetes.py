"""Bounded Kubernetes observations for one explicitly named application.

No kubeconfig, exec helper, Secret, log, watch, proxy, or mutation API is used.
"""
from __future__ import annotations

import ipaddress
import re
from datetime import UTC, datetime

from .http import APIError


class _Invalid(ValueError):
    def __init__(self, message, code="invalid-or-unsupported"):
        self.code = code
        super().__init__(message)


def _text(value, limit=253):
    if not isinstance(value, str) or not value or len(value) > limit or re.search(r"[\x00-\x20\x7f]", value):
        raise _Invalid("Invalid structured identity")
    return value


def _name(value):
    value = _text(value)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value):
        raise _Invalid("Invalid Kubernetes name")
    return value


def _number(value, default=0):
    value = default if value is None else value
    if type(value) is not int or not 0 <= value <= 2**53:
        raise _Invalid("Invalid resource count")
    return value


def _timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise _Invalid("Invalid event timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise _Invalid("Event timestamp has no timezone")
    return parsed.astimezone(UTC).isoformat()


def _items(value, limit=100):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(v, dict) for v in value):
        raise _Invalid("Invalid or excessive collection")
    return value


def _labels(value):
    if not isinstance(value, dict) or len(value) > 32:
        raise _Invalid("Invalid label selector")
    output = {}
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 253 or not re.fullmatch(r"[A-Za-z0-9_.\-/]+", key):
            raise _Invalid("Invalid label key")
        if not isinstance(item, str) or len(item) > 63 or not re.fullmatch(r"[A-Za-z0-9_.-]*", item):
            raise _Invalid("Invalid label value")
        output[key] = item
    return output


def _selector(value):
    if not isinstance(value, dict) or set(value) - {"matchLabels", "matchExpressions"}:
        raise _Invalid("Unsupported selector")
    result = {"matchLabels": _labels(value.get("matchLabels", {})), "matchExpressions": []}
    for expression in _items(value.get("matchExpressions", []), 32):
        key = expression.get("key")
        _labels({key: ""})
        operator = expression.get("operator")
        values = expression.get("values", [])
        if operator not in {"In", "NotIn", "Exists", "DoesNotExist"} or not isinstance(values, list) or len(values) > 32:
            raise _Invalid("Unsupported selector expression")
        for label_value in values:
            _labels({key: label_value})
        if (operator in {"Exists", "DoesNotExist"} and values) or (operator in {"In", "NotIn"} and not values):
            raise _Invalid("Invalid selector expression values")
        result["matchExpressions"].append({"key": key, "operator": operator, "values": values})
    return result


def _metadata(obj, kind, namespace, name=None):
    if not isinstance(obj, dict) or obj.get("kind", kind) != kind:
        raise _Invalid("Unexpected resource kind")
    meta = obj.get("metadata", {})
    version = {"Deployment": "apps/v1", "ReplicaSet": "apps/v1", "EndpointSlice": "discovery.k8s.io/v1",
               "NetworkPolicy": "networking.k8s.io/v1"}.get(kind, "v1")
    if obj.get("apiVersion", version) != version:
        raise _Invalid("Unsupported API version")
    if not isinstance(meta, dict) or meta.get("namespace") != namespace:
        raise _Invalid("Resource outside namespace scope")
    actual_name = _name(meta.get("name"))
    if name is not None and actual_name != name:
        raise _Invalid("Resource identity mismatch")
    owners = []
    for owner in _items(meta.get("ownerReferences", []), 16):
        owners.append({"kind": _text(owner.get("kind"), 64), "name": _name(owner.get("name")),
                       "uid": _text(owner.get("uid"), 128), "controller": owner.get("controller") is True})
    return {"kind": kind, "api_version": version, "name": actual_name, "namespace": namespace, "uid": _text(meta.get("uid"), 128),
            "resource_version": _text(meta.get("resourceVersion"), 128),
            "generation": _number(meta.get("generation")), "owners": owners,
            "deleting": bool(meta.get("deletionTimestamp"))}


def _conditions(status, allowed):
    result = []
    seen = set()
    for item in _items(status.get("conditions", []), 32):
        if item.get("type") in allowed:
            if item.get("status") not in {"True", "False", "Unknown"} or item["type"] in seen:
                raise _Invalid("Invalid condition status")
            seen.add(item["type"])
            result.append({"type": item["type"], "status": item["status"]})
    return result


def _owned(obj, kind, uid):
    controllers = [owner for owner in obj["owners"] if owner["controller"]]
    return len(controllers) == 1 and controllers[0]["kind"] == kind and controllers[0]["uid"] == uid


def _ports(value):
    result = []
    for port in _items(value, 32):
        protocol = port.get("protocol", "TCP")
        if protocol not in {"TCP", "UDP", "SCTP"}:
            raise _Invalid("Unsupported port protocol")
        entry = {"protocol": protocol}
        for key in ("name", "port", "targetPort", "endPort"):
            if key in port and port[key] is not None:
                item = port[key]
                if type(item) is int:
                    if not 1 <= item <= 65535:
                        raise _Invalid("Invalid network port")
                elif not isinstance(item, str) or not re.fullmatch(r"[a-z0-9-]{1,63}", item):
                    raise _Invalid("Invalid named port")
                entry[key] = item
        result.append(entry)
    return result


def _policy(obj, namespace):
    result = _metadata(obj, "NetworkPolicy", namespace)
    spec = obj.get("spec", {})
    if not isinstance(spec, dict) or set(spec) - {"podSelector", "policyTypes", "ingress", "egress"}:
        raise _Invalid("Unsupported NetworkPolicy fields")
    result["pod_selector"] = _selector(spec.get("podSelector", {}))
    types = spec.get("policyTypes", ["Ingress"] + (["Egress"] if spec.get("egress") else []))
    if not isinstance(types, list) or any(t not in {"Ingress", "Egress"} for t in types):
        raise _Invalid("Unsupported policy type")
    result["policy_types"] = types
    for direction, peer_key in (("ingress", "from"), ("egress", "to")):
        result[direction] = []
        for rule in _items(spec.get(direction, []), 50):
            if set(rule) - {peer_key, "ports"}:
                raise _Invalid("Unsupported NetworkPolicy rule")
            peers = []
            for peer in _items(rule.get(peer_key, []), 50):
                normalized = {}
                if set(peer) - {"podSelector", "namespaceSelector", "ipBlock"}:
                    raise _Invalid("Unsupported policy peer")
                for key in ("podSelector", "namespaceSelector"):
                    if key in peer:
                        normalized[key] = _selector(peer[key])
                if "ipBlock" in peer:
                    block = peer["ipBlock"]
                    exclusions = block.get("except", [])
                    if not isinstance(exclusions, list) or len(exclusions) > 32:
                        raise _Invalid("Invalid IP block")
                    normalized["ipBlock"] = {"cidr": str(ipaddress.ip_network(block["cidr"])),
                                             "except": [str(ipaddress.ip_network(v)) for v in exclusions]}
                peers.append(normalized)
            result[direction].append({peer_key: peers, "ports": _ports(rule.get("ports", []))})
    return result


class KubernetesCollector:
    """Collect one Deployment, one Service, their owner chain and namespaced policy."""

    def __init__(self, client, *, max_pages=5, max_items=100):
        if type(max_pages) is not int or not 1 <= max_pages <= 20 or type(max_items) is not int or not 1 <= max_items <= 500:
            raise ValueError("Invalid Kubernetes collection limits")
        self.client, self.max_pages, self.max_items = client, max_pages, max_items

    def _list(self, path, kind, params=None):
        query = dict(params or {}, limit=min(self.max_items, 100))
        result, seen, tokens, resource_version = [], set(), set(), None
        for _ in range(self.max_pages):
            page = self.client.get_json(path, query)
            if page.get("kind") != kind + "List":
                raise _Invalid("Unexpected list kind")
            meta = page.get("metadata", {})
            version = _text(meta.get("resourceVersion"), 128)
            if resource_version is not None and version != resource_version:
                raise _Invalid("List snapshot changed between pages", "list-race")
            resource_version = version
            for item in _items(page.get("items"), self.max_items):
                uid = _text(item.get("metadata", {}).get("uid"), 128)
                if uid in seen:
                    raise _Invalid("Duplicate resource in list")
                seen.add(uid)
                result.append(item)
            if len(result) > self.max_items:
                raise _Invalid("Resource collection limit reached", "collection-limit")
            continuation = meta.get("continue", "")
            if not continuation:
                return result
            continuation = _text(continuation, 4096)
            if continuation in tokens:
                raise _Invalid("Repeated continuation token")
            tokens.add(continuation)
            query["continue"] = continuation
        raise _Invalid("Resource page limit reached", "collection-limit")

    def collect(self, namespace, deployment, service, *, now=None, expected_deployment_uid=None,
                expected_generation=None):
        for value in (namespace, deployment, service):
            _name(value)
        if expected_deployment_uid is not None:
            _text(expected_deployment_uid, 128)
        if expected_generation is not None:
            _number(expected_generation)
        clock = now or datetime.now(UTC)
        if not isinstance(clock, datetime) or clock.tzinfo is None:
            raise ValueError("An aware observation time is required")
        report = {"schema_version": 1, "provider": "kubernetes", "execution_status": "partial",
                  "decision": "unknown", "confirmed_failure": False,
                  "observation_kind": "scoped_runtime_snapshot",
                  "scope": {"namespace": namespace, "deployment": deployment, "service": service},
                  "observed_at": clock.astimezone(UTC).isoformat(), "observations": {}, "gaps": []}
        observations = report["observations"]

        def gap(code, message):
            report["gaps"].append({"code": code, "message": message, "required": True})

        def attempt(label, function):
            try:
                return function()
            except APIError as exc:
                category = ("denied" if exc.status in (401, 403) else "missing" if exc.status == 404
                            else "expired-snapshot" if exc.status == 410 else "api-unavailable")
                gap("kubernetes-" + category, f"The scoped {label} read was unavailable")
            except _Invalid as exc:
                gap("kubernetes-" + exc.code, f"The scoped {label} evidence was invalid, changed, or exceeded bounds")
            except (ValueError, TypeError, KeyError, AttributeError):
                gap("kubernetes-invalid-or-unsupported", f"The scoped {label} evidence was invalid, changed, or exceeded bounds")
            return None

        dep_path = f"/apis/apps/v1/namespaces/{namespace}/deployments/{deployment}"
        svc_path = f"/api/v1/namespaces/{namespace}/services/{service}"

        def deployment_read():
            raw = self.client.get_json(dep_path)
            result = _metadata(raw, "Deployment", namespace, deployment)
            if raw.get("apiVersion") != "apps/v1" or result["generation"] < 1:
                raise _Invalid("Unsupported Deployment version")
            spec, status = raw.get("spec", {}), raw.get("status", {})
            result.update({"selector": _selector(spec.get("selector")),
                           "desired_replicas": _number(spec.get("replicas"), 1),
                           "observed_generation": _number(status.get("observedGeneration")),
                           "conditions": _conditions(status, {"Available", "Progressing", "ReplicaFailure"})})
            for key in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas", "unavailableReplicas"):
                result[key] = _number(status.get(key))
            return result

        def service_read():
            raw = self.client.get_json(svc_path)
            result = _metadata(raw, "Service", namespace, service)
            if raw.get("apiVersion") != "v1":
                raise _Invalid("Unsupported Service version")
            spec = raw.get("spec", {})
            result.update({"selector": _labels(spec.get("selector", {})), "ports": _ports(spec.get("ports", [])),
                           "type": spec.get("type", "ClusterIP")})
            if result["type"] not in {"ClusterIP", "NodePort", "LoadBalancer"}:
                raise _Invalid("Unsupported Service type")
            return result

        dep = attempt("Deployment", deployment_read)
        svc = attempt("Service", service_read)
        if dep is not None:
            observations["deployment"] = dep
        if svc is not None:
            observations["service"] = svc
        if dep is None or svc is None:
            return report
        if expected_deployment_uid is not None and dep["uid"] != expected_deployment_uid:
            gap("kubernetes-identity-mismatch", "Deployment UID differs from the intended deployment")
        if expected_generation is not None and dep["generation"] != expected_generation:
            gap("kubernetes-generation-mismatch", "Deployment generation differs from the intended generation")
        selector = dep["selector"]["matchLabels"]
        if not selector or dep["selector"]["matchExpressions"]:
            gap("kubernetes-unsupported-selector", "Deployment collection requires a nonempty exact matchLabels selector")
            return report
        if not svc["selector"] or svc["selector"] != selector:
            gap("kubernetes-service-selector-mismatch", "Service and Deployment exact selectors differ; route ownership is unproven")
        labels_query = ",".join(f"{key}={value}" for key, value in sorted(selector.items()))
        sets = attempt("ReplicaSets", lambda: self._list(
            f"/apis/apps/v1/namespaces/{namespace}/replicasets", "ReplicaSet", {"labelSelector": labels_query}))
        replicasets = []
        if sets is not None:
            for raw in sets:
                def set_read(raw=raw):
                    item = _metadata(raw, "ReplicaSet", namespace)
                    if any(raw.get("metadata", {}).get("labels", {}).get(k) != v for k, v in selector.items()):
                        raise _Invalid("ReplicaSet selector mismatch", "owner-mismatch")
                    status = raw.get("status", {})
                    item.update({"desired_replicas": _number(raw.get("spec", {}).get("replicas"), 1),
                                 "observed_generation": _number(status.get("observedGeneration")),
                                 "ready_replicas": _number(status.get("readyReplicas")),
                                 "available_replicas": _number(status.get("availableReplicas"))})
                    return item
                item = attempt("ReplicaSet", set_read)
                if item and _owned(item, "Deployment", dep["uid"]):
                    replicasets.append(item)
                    if item["desired_replicas"] > 0 and item["observed_generation"] != item["generation"]:
                        gap("kubernetes-stale-replicaset", "An active ReplicaSet has not observed its selected generation")
                elif item:
                    gap("kubernetes-owner-mismatch", "A selected ReplicaSet has a different controlling owner")
        observations["replicasets"] = replicasets
        set_uids = {item["uid"] for item in replicasets}
        pods_raw = attempt("Pods", lambda: self._list(
            f"/api/v1/namespaces/{namespace}/pods", "Pod", {"labelSelector": labels_query}))
        pods = []

        def pod_read(raw):
            item = _metadata(raw, "Pod", namespace)
            status = raw.get("status", {})
            actual_labels = _labels({key: raw.get("metadata", {}).get("labels", {}).get(key) for key in selector})
            if actual_labels != selector or not any(_owned(item, "ReplicaSet", uid) for uid in set_uids):
                raise _Invalid("Pod owner or selector mismatch", "owner-mismatch")
            phase = status.get("phase")
            if phase not in {"Pending", "Running", "Succeeded", "Failed", "Unknown"}:
                raise _Invalid("Unsupported Pod phase")
            item.update({"phase": phase, "conditions": _conditions(status, {"Ready", "ContainersReady", "Initialized", "PodScheduled"}),
                         "containers": []})
            addresses = status.get("podIPs", [{"ip": status["podIP"]}] if status.get("podIP") else [])
            item["pod_ips"] = [str(ipaddress.ip_address(address["ip"])) for address in _items(addresses, 16)]
            if raw.get("spec", {}).get("ephemeralContainers") or status.get("ephemeralContainerStatuses"):
                raise _Invalid("Ephemeral containers are outside this evidence contract")
            for key, container_type in (("containerStatuses", "regular"), ("initContainerStatuses", "init")):
                spec_key = "containers" if container_type == "regular" else "initContainers"
                expected_names = [_name(c.get("name")) for c in _items(raw.get("spec", {}).get(spec_key, []), 32)]
                actual_names = []
                for container in _items(status.get(key, []), 32):
                    image_id = container.get("imageID")
                    if not isinstance(image_id, str) or len(image_id) > 1024 or not re.fullmatch(
                            r"(?:[a-z][a-z0-9+.-]*://)?(?:[A-Za-z0-9._:/-]+@)?sha256:[a-f0-9]{64}", image_id):
                        gap("kubernetes-missing-image", "A selected container has no runtime image identity")
                        image_id = None
                    state = container.get("state", {})
                    state_names = [s for s in ("running", "waiting", "terminated") if s in state]
                    state_name = state_names[0] if len(state_names) == 1 else "unknown"
                    reason = state.get(state_name, {}).get("reason")
                    name = _name(container.get("name"))
                    actual_names.append(name)
                    item["containers"].append({"name": name, "container_type": container_type,
                        "image_id": _text(image_id, 1024) if image_id else None, "ready": container.get("ready") is True,
                        "restart_count": _number(container["restartCount"]), "state": state_name,
                        "reason": reason if reason in {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "ContainerCreating", "CreateContainerConfigError", "OOMKilled", "Completed", "Error"} else None})
                if len(actual_names) != len(set(actual_names)) or set(actual_names) != set(expected_names):
                    raise _Invalid("Container status coverage differs from Pod specification", "container-coverage")
            if not item["containers"]:
                raise _Invalid("Pod runtime image identity is unavailable")
            if {"type": "Ready", "status": "True"} in item["conditions"] and any(
                    not c["ready"] for c in item["containers"] if c["container_type"] == "regular"):
                raise _Invalid("Pod and container readiness disagree", "status-race")
            return item

        for raw in pods_raw or []:
            item = attempt("Pod", lambda raw=raw: pod_read(raw))
            if item:
                pods.append(item)
        observations["pods"] = pods
        pod_names = {item["uid"]: item["name"] for item in pods}
        pod_ips = {item["uid"]: item["pod_ips"] for item in pods}
        slices_raw = attempt("EndpointSlices", lambda: self._list(
            f"/apis/discovery.k8s.io/v1/namespaces/{namespace}/endpointslices", "EndpointSlice",
            {"labelSelector": f"kubernetes.io/service-name={service}"}))
        slices = []

        def slice_read(raw):
            item = _metadata(raw, "EndpointSlice", namespace)
            item["address_type"] = raw.get("addressType")
            if item["address_type"] not in {"IPv4", "IPv6"}:
                raise _Invalid("Unsupported endpoint address type")
            if not _owned(item, "Service", svc["uid"]) or raw.get("metadata", {}).get("labels", {}).get("kubernetes.io/service-name") != service:
                raise _Invalid("EndpointSlice service identity mismatch")
            item["ports"], item["endpoints"] = _ports(raw.get("ports", [])), []
            for endpoint in _items(raw.get("endpoints", []), 100):
                ref = endpoint.get("targetRef", {})
                if (ref.get("kind") != "Pod" or ref.get("uid") not in pod_names or ref.get("namespace") != namespace
                        or ref.get("name") != pod_names[ref["uid"]]):
                    raise _Invalid("EndpointSlice points outside the selected workload")
                addresses = endpoint.get("addresses", [])
                if not isinstance(addresses, list) or not 1 <= len(addresses) <= 16:
                    raise _Invalid("Invalid endpoint addresses")
                conditions = endpoint.get("conditions", {})
                if type(conditions.get("ready")) is not bool:
                    raise _Invalid("Endpoint readiness is unknown")
                addresses = [str(ipaddress.ip_address(value)) for value in addresses]
                if not set(addresses).issubset(pod_ips[ref["uid"]]) or any(
                        ipaddress.ip_address(value).version != (4 if item["address_type"] == "IPv4" else 6) for value in addresses):
                    raise _Invalid("Endpoint address and Pod identity disagree", "endpoint-identity-mismatch")
                item["endpoints"].append({"pod_uid": ref["uid"], "pod_name": _name(ref.get("name")),
                    "addresses": addresses, "ready": conditions["ready"],
                    "serving": conditions.get("serving") if type(conditions.get("serving")) is bool else None,
                    "terminating": conditions.get("terminating") is True})
            return item

        for raw in slices_raw or []:
            item = attempt("EndpointSlice", lambda raw=raw: slice_read(raw))
            if item:
                slices.append(item)
        observations["endpoint_slices"] = slices
        policies = attempt("NetworkPolicies", lambda: self._list(
            f"/apis/networking.k8s.io/v1/namespaces/{namespace}/networkpolicies", "NetworkPolicy"))
        observations["network_policies"] = []
        for raw in policies or []:
            policy = attempt("NetworkPolicy", lambda raw=raw: _policy(raw, namespace))
            if policy:
                observations["network_policies"].append(policy)
        events = attempt("Deployment events", lambda: self._list(
            f"/api/v1/namespaces/{namespace}/events", "Event", {"fieldSelector": f"involvedObject.uid={dep['uid']}"}))
        observations["deployment_events"] = []
        for raw in events or []:
            def event_read(raw=raw):
                item = _metadata(raw, "Event", namespace)
                ref = raw.get("involvedObject", {})
                if ref.get("uid") != dep["uid"] or ref.get("kind") != "Deployment" or ref.get("namespace") != namespace:
                    raise _Invalid("Event object identity mismatch")
                reason = raw.get("reason")
                item.update({"object_uid": dep["uid"], "type": raw.get("type") if raw.get("type") in {"Normal", "Warning"} else "Other",
                             "count": _number(raw.get("count")),
                             "first_observed_at": _timestamp(raw.get("firstTimestamp")),
                             "last_observed_at": _timestamp(raw.get("series", {}).get("lastObservedTime") or raw.get("lastTimestamp")),
                             "event_time": _timestamp(raw.get("eventTime")),
                             "created_at": _timestamp(raw.get("metadata", {}).get("creationTimestamp")),
                             "reason": reason if reason in {"ScalingReplicaSet", "ProgressDeadlineExceeded", "FailedCreate", "SuccessfulCreate", "FailedDelete", "SuccessfulDelete"} else "Other"})
                return item
            item = attempt("Event", event_read)
            if item:
                observations["deployment_events"].append(item)
        dep_after, svc_after = attempt("Deployment recheck", deployment_read), attempt("Service recheck", service_read)
        if (dep_after is not None and dep_after != dep) or (svc_after is not None and svc_after != svc):
            gap("kubernetes-race", "Deployment or Service changed during evidence collection")
        if dep["observed_generation"] != dep["generation"]:
            gap("kubernetes-stale-generation", "Controller status has not observed the selected generation")
        else:
            desired = dep["desired_replicas"]
            ready_pods = [p for p in pods if p["phase"] == "Running" and not p["deleting"]
                          and {"type": "Ready", "status": "True"} in p["conditions"]]
            ready_endpoints = [e for s in slices for e in s["endpoints"] if e["ready"] and not e["terminating"]]
            deployment_ready = (desired > 0 and not dep["deleting"] and dep["updatedReplicas"] == desired
                                and dep["availableReplicas"] >= desired and dep["readyReplicas"] >= desired
                                and not any(c in dep["conditions"] for c in (
                                    {"type": "ReplicaFailure", "status": "True"},
                                    {"type": "Progressing", "status": "False"},
                                    {"type": "Available", "status": "False"})))
            ready = deployment_ready and len(ready_pods) >= desired and bool(ready_endpoints)
            observations["workload_ready"] = ready if not report["gaps"] or not deployment_ready else None
            report["confirmed_failure"] = not deployment_ready or (not report["gaps"] and not ready)
        started = report["observed_at"]
        report["observed_at"] = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        observations["image_snapshot"] = {"namespace": namespace, "service": service, "deployment_uid": dep["uid"],
            "observed_at": report["observed_at"], "observed_from": started, "observed_through": report["observed_at"], "containers": [
                {"pod_uid": p["uid"], "pod_name": p["name"], "container": c["name"], "container_type": c["container_type"],
                 "image_id": c["image_id"], "restart_count": c["restart_count"]}
                for p in pods for c in p["containers"]]}
        if not report["gaps"]:
            report["execution_status"] = "complete"
            report["decision"] = "fail" if report["confirmed_failure"] else "pass"
        return report
