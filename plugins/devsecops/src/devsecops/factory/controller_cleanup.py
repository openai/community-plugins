"""Original controller inventory and absence gates; never a child delete issuer.

Only fixed GETs derived from selected controllers, their actual inventories and
preflighted scope are supported. Helm storage payloads stay private in memory.
"""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import gzip
import io
import os
import re
import time

from devsecops.resources import strict_load_yaml
from devsecops.adapters.http import _json, APIError
from .state import StateError, _sha
from .kubernetes_operations import KINDS, OwnedKubernetesResources, _bytes, _route, _name

POLICY = "owned-controller-inventory-v1"
CONTROLLERS = {"Application", "HelmRelease", "Kustomization"}
MAX_RESOURCES = 1024
MAX_RELEASE_BYTES = 8_000_000
REGISTERED_CUSTOM_KINDS = frozenset({
    ("source.toolkit.fluxcd.io/v1", "GitRepository"),
    ("source.toolkit.fluxcd.io/v1", "HelmRepository"),
    ("kustomize.toolkit.fluxcd.io/v1", "Kustomization"),
    ("helm.toolkit.fluxcd.io/v2", "HelmRelease"),
    ("argoproj.io/v1alpha1", "Application"),
})


def _selected_custom_routes(manifests):
    """Validate data-only CRD routes; registration grants no read/write authority."""
    routes = {}
    for row in manifests:
        if row.get("kind") != "CustomResourceDefinition":
            continue
        spec = row.get("spec", {})
        group, names = spec.get("group", ""), spec.get("names", {})
        if spec.get("scope") != "Namespaced":
            if isinstance(group, str) and isinstance(names, dict) and any(
                isinstance(version, dict) and version.get("served") is True
                and (group + "/" + str(version.get("name")), names.get("kind")) in KINDS
                for version in spec.get("versions", [])
            ):
                raise StateError("Selected CRD scope differs from a registered resource route")
            continue  # Cluster custom objects require separately implemented scope admission.
        if (
            not all(_name(part) for part in group.split("."))
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,62}", names.get("kind", ""))
            or not _name(names.get("plural"))
        ):
            raise StateError("Selected custom resource route is unsupported")
        versions = spec.get("versions", [])
        if not isinstance(versions, list) or not 1 <= len(versions) <= 16:
            raise StateError("Selected CRD served versions exceed bounds")
        for version in versions:
            if version.get("served") is not True:
                continue
            if not _name(version.get("name")):
                raise StateError("Selected CRD version is unsupported")
            key = (group + "/" + version["name"], names["kind"])
            route = {"base": "/apis/" + key[0], "plural": names["plural"], "crd_path": _route(row)[1]}
            if key in KINDS:
                if (key not in REGISTERED_CUSTOM_KINDS
                        or KINDS[key] != (route["base"], route["plural"], True)
                        or row["metadata"]["name"] != names["plural"] + "." + group):
                    raise StateError("Selected CRD route collides with an original resource type")
                continue
            if key in routes and routes[key] != route:
                raise StateError("Selected CRD route collides with an original resource type")
            routes[key] = route
    return routes


class ControllerObservationChanged(StateError):
    """Known changed readback before any delete for this original root."""


def compile_policy(manifest, policy):
    """Derive only the named, selected policy; preserve original input pins."""
    if policy != POLICY:
        raise StateError("Original platform must select owned-controller-inventory-v1")
    row = deepcopy(manifest)
    kind = row["kind"]
    if kind == "HelmRelease":
        spec = row.setdefault("spec", {})
        if "kubeConfig" in spec or spec.get("suspend", False):
            raise StateError("Controller cleanup cannot select a remote or suspended HelmRelease")
        for action in ("install", "upgrade"):
            spec.setdefault(action, {}).update(crds="Skip", disableHooks=True)
        spec["install"]["createNamespace"] = False
        spec.setdefault("rollback", {})["disableHooks"] = True
        spec.setdefault("test", {})["enable"] = False
        spec.setdefault("uninstall", {}).update(
            disableHooks=True, keepHistory=False, deletionPropagation="foreground", disableWait=False
        )
        # Explicit fixed release identity avoids version-dependent default interpretation.
        if "releaseName" not in spec:
            name = (spec["targetNamespace"] + "-" if spec.get("targetNamespace") else "") + row["metadata"][
                "name"
            ]
            spec["releaseName"] = name if len(name) <= 53 else name[:40] + "-" + _sha(name.encode())[:12]
    elif kind == "Kustomization":
        spec = row.setdefault("spec", {})
        if "kubeConfig" in spec or spec.get("suspend", False):
            raise StateError("Controller cleanup cannot select a remote or suspended Kustomization")
        spec.update(prune=True, deletionPolicy="Delete")
    return row


def require_policy(row):
    kind, spec = row["kind"], row.get("spec", {})
    if "kubeConfig" in spec or spec.get("suspend", False):
        raise StateError("Controller cleanup requires a current local controller: " + _route(row)[1])
    if kind == "HelmRelease":
        good = all(
            spec.get(a, {}).get("crds") == "Skip" and spec.get(a, {}).get("disableHooks") is True
            for a in ("install", "upgrade")
        )
        good &= spec.get("install", {}).get("createNamespace", False) is False
        good &= spec.get("rollback", {}).get("disableHooks") is True
        good &= spec.get("test", {}).get("enable", False) is False
        uninstall = spec.get("uninstall", {})
        good &= uninstall.get("disableHooks") is True and uninstall.get("keepHistory", False) is False
        good &= (
            uninstall.get("deletionPropagation") == "foreground"
            and uninstall.get("disableWait", False) is False
        )
        good &= isinstance(spec.get("releaseName"), str) and bool(
            re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,52}", spec["releaseName"])
        )
        if not good:
            raise StateError(
                "Helm child inventory needs selected CRD/hook/uninstall policy: " + _route(row)[1]
            )
    elif kind == "Application":
        if row["metadata"].get("finalizers") != ["resources-finalizer.argocd.argoproj.io"]:
            raise StateError("Argo cleanup requires the selected foreground resource finalizer")
    elif kind == "Kustomization":
        if spec.get("prune") is not True or spec.get("deletionPolicy") != "Delete":
            raise StateError("Flux child inventory needs selected prune/delete policy: " + _route(row)[1])


def _identity(value, descriptor, uid=None):
    if (
        not isinstance(value, dict)
        or any(value.get(key) != descriptor.get(key) for key in ("apiVersion", "kind"))
        or any(
            value.get("metadata", {}).get(key) != descriptor.get("metadata", {}).get(key)
            for key in ("name", "namespace")
        )
    ):
        raise StateError("Controller inventory resource identity is missing or changed")
    meta = value.get("metadata", {})
    if (
        not isinstance(meta.get("uid"), str)
        or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", meta["uid"])
        or (uid is not None and meta["uid"] != uid)
    ):
        raise StateError("Controller child UID changed; no replacement may be adopted")
    return meta["uid"]


def decode_release(secret):
    """Two strict base64 layers and bounded gzip; no release/config logging."""
    try:
        encoded = secret["data"]["release"]
        if not isinstance(encoded, str) or len(encoded) > 12_000_000:
            raise ValueError
        compressed = base64.b64decode(base64.b64decode(encoded, validate=True), validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            raw = stream.read(MAX_RELEASE_BYTES + 1)
        if len(raw) > MAX_RELEASE_BYTES:
            raise ValueError
        value = _json(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value, _sha(raw)
    except (KeyError, ValueError, TypeError, OSError, EOFError, APIError):
        raise StateError("Helm storage release encoding is unsupported or exceeds bounds") from None


def _descriptor(entry):
    if type(entry) is not dict or set(entry) != {"id", "v"} or not isinstance(entry["id"], str):
        raise StateError("Flux inventory entry shape is unsupported")
    parts = entry["id"].split("_")
    if len(parts) != 4:
        raise StateError("Flux inventory identity is unsupported")
    namespace, name, group, kind = parts
    result = {
        "apiVersion": (group + "/" if group else "") + entry["v"],
        "kind": kind,
        "metadata": {"name": name},
    }
    if namespace:
        result["metadata"]["namespace"] = namespace
    return result


class OwnedControllerInventory:
    """Original same-process observation closure, bound before controller effects."""

    def __init__(self, resources, context, manifests, *, policy, platform=None):
        if (
            type(resources) is not OwnedKubernetesResources
            or getattr(resources, "_controller_inventory", None) is not None
        ):
            raise StateError("Controller inventory requires one original Kubernetes owner")
        resources._admit(context)
        if resources._resources or resources._unresolved:
            raise StateError("Controller inventory must bind before any original resource effect")
        if context.phase["operation"] != "up" or policy != POLICY:
            raise StateError("Controller inventory must be selected before original provisioning")
        if not isinstance(manifests, tuple) or len(manifests) > MAX_RESOURCES:
            raise StateError("Controller manifest inventory exceeds bounds")
        if {_sha(_bytes(row)) for row in manifests} != set(resources.manifest_sha256):
            raise StateError("Controller inventory must cover every originally selected manifest pin")
        self.resources, self.policy, self._pid = resources, policy, os.getpid()
        self._seed_source, self._seed_binding = None, None
        if platform is not None:
            from .platform_bootstrap import OwnedPlatformBootstrap
            from .platform_inputs import require_owned_inputs

            if type(platform) is not OwnedPlatformBootstrap or platform.journal is not resources.journal:
                raise StateError("Initial Argo source requires its original platform producer")
            original = require_owned_inputs(
                platform._owned_inputs, context, platform=platform, resources=resources
            )
            self._seed_source = {
                "url": original["url"],
                "revision": original["seed_commit"],
                "project_id": original["project_id"],
            }
            self._seed_binding = (platform, platform._owned_inputs)
        self._seed_digest = _sha(_bytes(self._seed_source))
        self._scope_digest = _sha(
            _bytes(
                {
                    "pins": resources.manifest_sha256,
                    "namespaces": resources.namespaces,
                    "cluster_resources": resources.cluster_resources,
                    "origin": resources.session.origin,
                    "cluster_uid": resources.cluster_uid,
                }
            )
        )
        self._custom_routes = _selected_custom_routes(manifests)
        self._route_digest = _sha(
            _bytes([[list(key), value] for key, value in sorted(self._custom_routes.items())])
        )
        self._roots = {self._route(row)[1]: deepcopy(row) for row in manifests if row["kind"] in CONTROLLERS}
        if len(self._roots) != sum(row["kind"] in CONTROLLERS for row in manifests):
            raise StateError("Selected controller manifest paths collide")
        if len(self._roots) > 128:
            raise StateError("Controller inventory is too large")
        for row in self._roots.values():
            require_policy(row)
        self._selection = _sha(_bytes(self._roots))
        self._children, self._controllers, self._cluster_absent = {}, {}, set()
        self._delete_views, self._extra_cluster_observations = {}, []
        self._namespace_closure = {}
        self._deferred_applications = {}
        self._started, self._captured, self._error = False, False, None
        self._absence_observed = False
        self._direct_paths = {self._route(row)[1] for row in manifests}
        # Exact explicit cluster paths only, including unused selected child names.
        for api, kind, name in resources.cluster_resources:
            row = {"apiVersion": api, "kind": kind, "metadata": {"name": name}}
            path = self._route(row)[1]
            if resources.session.request("GET", path, deadline=context.deadline) is not None:
                raise StateError("Selected controller cluster destination already exists: " + path)
            self._cluster_absent.add(path)
        resources._controller_inventory = self
        resources._record(
            "controller-scope",
            {
                "policy": policy,
                "root_paths": sorted(self._roots),
                "preflight_absent_cluster_paths": sorted(self._cluster_absent),
                "selection_sha256": self._selection,
            },
        )

    def _route(self, descriptor):
        key = (descriptor.get("apiVersion"), descriptor.get("kind"))
        if key in KINDS:
            return _route(descriptor)
        route = self._custom_routes.get(key)
        meta = descriptor.get("metadata", {})
        name, namespace = meta.get("name"), meta.get("namespace")
        if (
            route is None
            or not isinstance(name, str)
            or len(name) > 253
            or not all(_name(part) for part in name.split("."))
            or not _name(namespace)
        ):
            raise StateError("Controller custom child needs an exact selected namespaced CRD")
        collection = route["base"] + "/namespaces/" + namespace + "/" + route["plural"]
        return collection, collection + "/" + name

    def _admit(self, context):
        r = self.resources
        if (
            self._seed_digest != _sha(_bytes(self._seed_source))
            or self._seed_binding is not None
            and (
                self._seed_binding[0].journal is not r.journal
                or self._seed_binding[0]._owned_inputs is not self._seed_binding[1]
            )
        ):
            raise StateError("Original initial Argo source binding changed")
        if self._route_digest != _sha(
            _bytes([[list(key), value] for key, value in sorted(self._custom_routes.items())])
        ):
            raise StateError("Original selected custom resource routes changed")
        if (
            self._pid != os.getpid()
            or r._controller_inventory is not self
            or _sha(_bytes(self._roots)) != self._selection
            or _sha(
                _bytes(
                    {
                        "pins": r.manifest_sha256,
                        "namespaces": r.namespaces,
                        "cluster_resources": r.cluster_resources,
                        "origin": r.session.origin,
                        "cluster_uid": r.cluster_uid,
                    }
                )
            )
            != self._scope_digest
        ):
            raise StateError("Original controller inventory capability changed")
        if context.run_id != r.journal.state["run_id"] or dict(context.identities) != dict(
            r.journal.identities
        ):
            raise StateError("Controller inventory scope changed")
        if time.monotonic() >= context.deadline:
            raise StateError("Controller inventory deadline expired")

    def _get(self, context, descriptor):
        self._admit(context)
        route = self._custom_routes.get((descriptor.get("apiVersion"), descriptor.get("kind")))
        if route is not None:
            records = [row for row in self.resources._resources if row["path"] == route["crd_path"]]
            if len(records) != 1:
                raise StateError("Custom child requires its actual directly created original CRD")
            observed = self.resources.session.request("GET", route["crd_path"], deadline=context.deadline)
            if observed is None and self._started:
                namespace_path = "/api/v1/namespaces/" + descriptor["metadata"]["namespace"]
                namespaces = [row for row in self.resources._resources if row["path"] == namespace_path]
                if (
                    len(namespaces) == 1
                    and self.resources.session.request("GET", namespace_path, deadline=context.deadline)
                    is None
                ):
                    self._namespace_closure[self._route(descriptor)[1]] = {
                        "namespace_path": namespace_path,
                        "original_namespace_uid": namespaces[0]["uid"],
                        "crd_path": route["crd_path"],
                        "original_crd_uid": records[0]["uid"],
                        "namespace_state": "absent",
                        "crd_state": "absent",
                    }
                    return None
            self.resources._audit_owned_observation(records[0], observed)
        return self.resources.session.request("GET", self._route(descriptor)[1], deadline=context.deadline)

    def _scope(self, context, descriptor):
        r, path = self.resources, self._route(descriptor)[1]
        namespace = descriptor["metadata"].get("namespace")
        if namespace:
            records = [row for row in r._resources if row["path"] == "/api/v1/namespaces/" + namespace]
            if len(records) != 1:
                raise StateError("Controller child needs an original owned Namespace: " + path)
            ns = self._get(context, records[0]["manifest"])
            _identity(ns, records[0]["manifest"], records[0]["uid"])
        elif path not in self._cluster_absent:
            raise StateError("Controller cluster child lacks original selected absence proof: " + path)
        if descriptor["kind"] == "Namespace":
            raise StateError("Controllers must not create or own the original namespace: " + path)

    def _remember(
        self,
        context,
        controller,
        descriptor,
        *,
        helm=None,
        allow_absent=False,
        absence_basis="actual-failed-release-manifest-and-current-get",
        reject_hooks=False,
    ):
        path = self._route(descriptor)[1]
        if path == self._route(controller)[1]:
            raise StateError("Controller inventory contains a self-cycle")
        self._scope(context, descriptor)
        value = self._get(context, descriptor)
        old = self._children.get(path)
        if value is None and allow_absent:
            if descriptor["kind"] in CONTROLLERS:
                raise StateError("Failed release has an unobserved nested controller scope")
            if len(self._children) >= MAX_RESOURCES and path not in self._children:
                raise StateError("Controller resource inventory exceeds bounds")
            if descriptor.get("metadata", {}).get("annotations", {}).get("helm.sh/resource-policy") == "keep":
                raise StateError("Failed Helm release contains a preservation resource")
            self._children[path] = old or {
                "path": path,
                "uid": None,
                "descriptor": {key: descriptor[key] for key in ("apiVersion", "kind")},
                "metadata": {
                    key: descriptor["metadata"][key]
                    for key in ("name", "namespace")
                    if key in descriptor["metadata"]
                },
                "content_sha256": _sha(_bytes(descriptor)),
                "controller_path": self._route(controller)[1],
                "state": "absent",
                "absence_basis": absence_basis,
            }
            self._children[path]["state"] = "absent"
            return None
        if old is not None and old["uid"] is None and value is not None:
            raise StateError("Previously absent failed-release child appeared without an original UID")
        uid = _identity(value, descriptor, old["uid"] if old else None)
        meta = value["metadata"]
        if reject_hooks and any(
            meta.get("annotations", {}).get(key) for key in ("argocd.argoproj.io/hook", "helm.sh/hook")
        ):
            raise StateError("Failed Argo inventory contains a live hook resource")
        controller_mutations = []
        if helm:

            def contains(actual, desired, location=()):
                # Istio first admits configuration while its validator starts,
                # then strengthens Ignore to Fail. Callback identity and every
                # other declared field remain exact; weakening is never accepted.
                if (descriptor.get("apiVersion") == "admissionregistration.k8s.io/v1"
                        and descriptor.get("kind") == "ValidatingWebhookConfiguration"
                        and len(location) == 3 and location[0] == "webhooks"
                        and type(location[1]) is int and location[2] == "failurePolicy"
                        and desired == "Ignore" and actual == "Fail"):
                    controller_mutations.append({"path": "/webhooks/" + str(location[1]) + "/failurePolicy",
                                                 "before": "Ignore", "after": "Fail"})
                    return True
                if type(desired) is dict:
                    return type(actual) is dict and all(
                        key in actual and contains(actual[key], item, (*location, key)) for key, item in desired.items()
                    )
                if type(desired) is list:
                    return (
                        type(actual) is list
                        and len(actual) == len(desired)
                        and all(contains(a, b, (*location, index)) for index, (a, b) in enumerate(zip(actual, desired)))
                    )
                return type(actual) is type(desired) and actual == desired

            if any(
                not contains(value.get(key), child, (key,))
                for key, child in descriptor.items()
                if key not in ("metadata", "status")
            ):
                raise StateError("Helm child content differs from original release manifest: " + path)
        if meta.get("annotations", {}).get("helm.sh/resource-policy") == "keep" or any(
            meta.get(key, {}).get("kustomize.toolkit.fluxcd.io/prune") == "disabled"
            for key in ("labels", "annotations")
        ):
            raise StateError("Controller child requests preservation: " + path)
        direct = [row for row in self.resources._resources if row["path"] == path]
        if direct:
            _identity(value, direct[0]["manifest"], direct[0]["uid"])
        elif helm:
            if (
                meta.get("annotations", {}).get("meta.helm.sh/release-name") != helm[0]
                or meta.get("annotations", {}).get("meta.helm.sh/release-namespace") != helm[1]
                or meta.get("labels", {}).get("app.kubernetes.io/managed-by") != "Helm"
            ):
                raise StateError("Helm child lacks exact release ownership metadata: " + path)
        elif controller["kind"] == "Application":
            group = descriptor["apiVersion"].split("/")[0] if "/" in descriptor["apiVersion"] else ""
            tracking = f"{controller['metadata']['name']}:{group}/{descriptor['kind']}:{meta.get('namespace', '')}/{meta['name']}"
            if (
                meta.get("annotations", {}).get("argocd.argoproj.io/tracking-id") != tracking
                and meta.get("labels", {}).get("app.kubernetes.io/instance") != controller["metadata"]["name"]
            ):
                raise StateError("Argo child lacks exact application tracking metadata: " + path)
        else:
            labels = meta.get("labels", {})
            if (
                labels.get("kustomize.toolkit.fluxcd.io/name") != controller["metadata"]["name"]
                or labels.get("kustomize.toolkit.fluxcd.io/namespace") != controller["metadata"]["namespace"]
            ):
                raise StateError("Flux child lacks exact original controller labels: " + path)
        if len(self._children) >= MAX_RESOURCES and path not in self._children:
            raise StateError("Controller resource inventory exceeds bounds")
        content = {key: child for key, child in value.items() if key not in ("metadata", "status")}
        # Evidence is identity/hash only, including Secret and ConfigMap contents.
        self._children[path] = {
            "path": path,
            "uid": uid,
            "descriptor": {key: deepcopy(value[key]) for key in ("apiVersion", "kind")},
            "metadata": {key: meta[key] for key in ("name", "namespace") if key in meta},
            "content_sha256": _sha(_bytes(content)),
            "controller_path": self._route(controller)[1],
            "state": "present",
        }
        if controller_mutations:
            self._children[path]["controller_mutations"] = controller_mutations
        return value

    def _helm(self, context, controller, *, allow_failed=False):
        spec, status = controller["spec"], controller.get("status", {})
        history = status.get("history")
        if not isinstance(history, list) or not history or len(history) > 64:
            raise StateError(
                "Original HelmRelease has no bounded successful history: " + self._route(controller)[1]
            )
        target = spec.get("targetNamespace", controller["metadata"]["namespace"])
        storage = spec.get("storageNamespace", controller["metadata"]["namespace"])
        if status.get("storageNamespace", storage) != storage:
            raise StateError("Helm storage namespace differs from selected scope")
        name = spec["releaseName"]
        latest = history[0]
        if not isinstance(latest, dict):
            raise StateError("Helm cleanup lacks a valid current versioned release history")
        if (
            latest.get("name") != name
            or latest.get("namespace") != target
            or latest.get("status") not in (("deployed", "failed") if allow_failed else ("deployed",))
            or type(latest.get("version")) is not int
            or not 1 <= latest["version"] <= 10000
        ):
            raise StateError("Helm cleanup lacks an actual current versioned release inventory")
        secret_descriptor = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": f"sh.helm.release.v1.{name}.v{latest['version']}", "namespace": storage},
        }
        self._scope(context, secret_descriptor)
        secret = self._get(context, secret_descriptor)
        old = self._children.get(self._route(secret_descriptor)[1])
        secret_uid = _identity(secret, secret_descriptor, old["uid"] if old else None)
        if secret.get("type") != "helm.sh/release.v1" or any(
            secret["metadata"].get("labels", {}).get(key) != expected
            for key, expected in {
                "owner": "helm",
                "name": name,
                "status": latest["status"],
                "version": str(latest["version"]),
            }.items()
        ):
            raise StateError("Helm release storage identity differs")
        release, digest = decode_release(secret)
        if (
            any(
                release.get(key) != expected
                for key, expected in {"name": name, "namespace": target, "version": latest["version"]}.items()
            )
            or release.get("info", {}).get("status") != latest["status"]
        ):
            raise StateError("Helm storage payload differs from actual release history")
        raw = release.get("manifest")
        if not isinstance(raw, str) or len(raw.encode()) > MAX_RELEASE_BYTES:
            raise StateError("Helm manifest exceeds supported bounds")
        documents = [row for row in strict_load_yaml(raw) if row is not None]
        if len(documents) > MAX_RESOURCES:
            raise StateError("Helm manifest resource count exceeds bounds")
        children = []
        for document in documents:
            if not isinstance(document, dict):
                raise StateError("Helm manifest resource is invalid")
            key = (document.get("apiVersion"), document.get("kind"))
            if key in self._custom_routes or key in KINDS and KINDS[key][2]:
                document.setdefault("metadata", {}).setdefault("namespace", target)
            children.append(
                self._remember(
                    context,
                    controller,
                    document,
                    helm=(name, target),
                    allow_absent=allow_failed and latest["status"] == "failed",
                )
            )
        # Storage is removed by Helm uninstall, never by this observer.
        self._children[self._route(secret_descriptor)[1]] = {
            "path": self._route(secret_descriptor)[1],
            "uid": secret_uid,
            "descriptor": {"apiVersion": "v1", "kind": "Secret"},
            "metadata": secret_descriptor["metadata"],
            "content_sha256": _sha(_bytes(secret.get("data"))),
            "controller_path": self._route(controller)[1],
            "state": "present",
        }
        return children, {
            "release_status": latest["status"],
            "release_sha256": digest,
            "storage_uid": secret_uid,
            "version": latest["version"],
            "manifest_sha256": _sha(raw.encode()),
        }

    def _flux(self, context, controller):
        status = controller.get("status", {})
        entries = status.get("inventory", {}).get("entries")
        if (
            not isinstance(entries, list)
            or len(entries) > MAX_RESOURCES
            or not isinstance(status.get("lastAppliedRevision"), str)
            or not re.fullmatch(
                r"(?:[A-Za-z0-9._/-]{1,128}@)?sha(?:1:[a-f0-9]{40}|256:[a-f0-9]{64})",
                status["lastAppliedRevision"],
            )
        ):
            raise StateError(
                "Original Flux controller has no bounded applied inventory: " + self._route(controller)[1]
            )
        paths, children = set(), []
        for entry in entries:
            descriptor = _descriptor(entry)
            path = self._route(descriptor)[1]
            if path in paths:
                raise StateError("Flux inventory contains duplicate resources")
            paths.add(path)
            children.append(self._remember(context, controller, descriptor))
        return children, {
            "inventory_sha256": _sha(_bytes(entries)),
            "last_applied_revision": status["lastAppliedRevision"],
        }

    def _argo_descriptor(self, entry):
        if not isinstance(entry, dict):
            raise StateError("Argo child inventory is unsupported")
        group, kind = entry.get("group", ""), entry.get("kind")
        versions = [
            api
            for api, selected_kind in (*KINDS, *self._custom_routes)
            if selected_kind == kind and (api.split("/")[0] if "/" in api else "") == group
        ]
        if (
            len(versions) != 1
            or entry.get("version", versions[0].split("/")[-1]) != versions[0].split("/")[-1]
        ):
            raise StateError("Argo child API version is outside fixed selected support")
        descriptor = {"apiVersion": versions[0], "kind": kind, "metadata": {"name": entry.get("name")}}
        if entry.get("namespace"):
            descriptor["metadata"]["namespace"] = entry["namespace"]
        self._route(descriptor)
        return descriptor

    def _failed_argo(self, context, controller, entries):
        status, source = controller.get("status", {}), controller.get("spec", {}).get("source")
        state = status.get("operationState", {})
        if not isinstance(state, dict) or not isinstance(state.get("operation", {}), dict):
            raise StateError("Failed Argo operation has unsupported state")
        result = state.get("syncResult", {})
        request = state.get("operation", {}).get("sync", {})
        sync, conditions = status.get("sync", {}), status.get("conditions", [])
        if (
            not all(isinstance(row, dict) for row in (result, request, sync))
            or not isinstance(sync.get("comparedTo", {}), dict)
            or not isinstance(conditions, list)
            or len(conditions) > 128
            or any(not isinstance(row, dict) for row in conditions)
        ):
            raise StateError("Failed Argo operation has unsupported comparison or result state")
        seed = self._seed_source
        if (
            context.phase["operation"] != "down"
            or seed is None
            or not entries
            or state.get("phase") not in ("Failed", "Error")
            or controller.get("operation") is not None
            or not isinstance(source, dict)
            or source.get("repoURL") != seed["url"]
            or source.get("targetRevision") not in ("main", seed["revision"])
            or source != result.get("source")
            or result.get("sources")
            or result.get("revisions")
            or result.get("revision") != seed["revision"]
            or request.get("revision") != seed["revision"]
            or request.get("resources")
            or request.get("sources")
            or request.get("revisions")
            or request.get("source", source) != source
            or sync.get("revision") != seed["revision"]
            or sync.get("status") not in ("Synced", "OutOfSync")
            or sync.get("comparedTo", {}).get("source") != source
            or any(
                row.get("type") in ("ComparisonError", "InvalidSpecError", "UnknownError")
                for row in conditions
            )
        ):
            raise StateError(
                "Argo child inventory needs actual Healthy/Synced resources or exact terminal failed seed inventory"
            )
        try:
            dates = [
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                for value in (
                    controller["metadata"]["creationTimestamp"],
                    state["startedAt"],
                    state["finishedAt"],
                )
            ]
            if any(value.tzinfo is None for value in dates) or not dates[0] <= dates[1] <= dates[
                2
            ] <= datetime.now(timezone.utc) + timedelta(seconds=5):
                raise ValueError
        except (KeyError, ValueError, TypeError, AttributeError):
            raise StateError("Failed Argo operation lacks bounded completed timestamps") from None
        results = result.get("resources")
        if not isinstance(results, list) or not results or len(results) > MAX_RESOURCES:
            raise StateError("Failed Argo operation lacks actual nonempty resource results")
        paths = []
        for row in results:
            if (
                not isinstance(row, dict)
                or row.get("hookType")
                or row.get("hookPhase") not in (None, "")
                or row.get("syncPhase", "Sync") != "Sync"
                or row.get("status") not in ("Synced", "SyncFailed")
                or not row.get("version")
            ):
                raise StateError("Failed Argo operation has hooks or unresolved resource results")
            paths.append(self._route(self._argo_descriptor(row))[1])
        if any(not isinstance(row, dict) or row.get("hook") not in (None, False) for row in entries):
            raise StateError("Failed Argo tracked inventory contains hook evidence")
        expected = [self._route(self._argo_descriptor(row))[1] for row in entries]
        if (
            len(paths) != len(set(paths))
            or len(expected) != len(set(expected))
            or set(paths) != set(expected)
        ):
            raise StateError("Failed Argo result and tracked resource inventory differ")
        return {
            "basis": "actual-terminal-failed-seed-inventory",
            "phase": state["phase"],
            "seed_revision": seed["revision"],
            "seed_project_id": seed["project_id"],
            "source_binding_sha256": self._seed_digest,
            "operation_sha256": _sha(_bytes(state)),
        }

    def _argo(self, context, controller):
        status = controller.get("status", {})
        if not isinstance(status, dict) or not all(
            isinstance(status.get(key, {}), dict) for key in ("sync", "health")
        ):
            raise StateError("Argo child inventory needs supported actual status")
        entries = status.get("resources")
        if not isinstance(entries, list) or len(entries) > MAX_RESOURCES:
            raise StateError("Argo child inventory needs actual Healthy/Synced resources")
        healthy = (
            status.get("sync", {}).get("status") == "Synced"
            and status.get("health", {}).get("status") == "Healthy"
            and isinstance(status.get("sync", {}).get("revision"), str)
            and bool(re.fullmatch(r"[a-f0-9]{40}", status["sync"]["revision"]))
        )
        failed = None if healthy else self._failed_argo(context, controller, entries)
        children, seen = [], set()
        for entry in entries:
            descriptor = self._argo_descriptor(entry)
            path = self._route(descriptor)[1]
            if path in seen:
                raise StateError("Argo child inventory contains duplicate resources")
            seen.add(path)
            children.append(
                self._remember(
                    context,
                    controller,
                    descriptor,
                    allow_absent=failed is not None,
                    absence_basis="actual-terminal-argo-inventory-and-current-get",
                    reject_hooks=failed is not None,
                )
            )
        return children, {
            "inventory_sha256": _sha(_bytes(entries)),
            "revision": status.get("sync", {}).get("revision"),
            "terminal_failure": failed,
        }

    def capture(self, context, *, _remaining=False, defer_unready_application=False):
        self._admit(context)
        if defer_unready_application and context.phase["operation"] != "up":
            raise StateError("Deferred first Application inventory is restricted to original provisioning")
        self._deferred_applications = {}
        if _remaining and (not self._started or context.phase["operation"] != "down"):
            raise StateError("Remaining inventory refresh requires original cleanup continuation")
        if self._started and not _remaining:
            raise StateError("Controller cleanup inventory is already frozen")
        if any(
            row["manifest"]["kind"] in CONTROLLERS and row["path"] not in self._roots
            for row in self.resources._resources
        ):
            raise StateError("Original created controller is absent from selected inventory")
        pending, seen = [], set()
        for path, manifest in self._roots.items():
            rows = [row for row in self.resources._resources if row["path"] == path]
            if not rows:
                continue  # Never attempted; no controller effect to invent.
            value = self._get(context, manifest)
            if _remaining and rows[0]["state"] == "absent" and value is None:
                continue
            _identity(value, manifest, rows[0]["uid"])
            self.resources._audit_owned_observation(rows[0], value)
            pending.append(value)
        while pending:
            if len(seen) >= 128:
                raise StateError("Nested controller inventory exceeds bounds")
            controller = pending.pop(0)
            path = self._route(controller)[1]
            if path in seen:
                continue
            seen.add(path)
            require_policy(controller)
            meta, status = controller["metadata"], controller.get("status", {})
            if (
                defer_unready_application
                and controller["kind"] == "Application"
                and (
                    status.get("sync", {}).get("status") != "Synced"
                    or status.get("health", {}).get("status") != "Healthy"
                )
            ):
                self._deferred_applications[path] = {
                    "path": path,
                    "uid": meta["uid"],
                    "spec_sha256": _sha(_bytes(controller["spec"])),
                    "child_scope": "unobserved-until-first-ready-delivery",
                }
                continue
            allow_failed = (
                context.phase["operation"] == "down"
                and controller["kind"] == "HelmRelease"
                and isinstance(status.get("history"), list)
                and bool(status["history"])
                and isinstance(status["history"][0], dict)
                and status["history"][0].get("status") == "failed"
            )
            if controller["kind"] != "Application" and (
                type(meta.get("generation")) is not int
                or status.get("observedGeneration") != meta["generation"]
                or not allow_failed
                and not any(
                    isinstance(row, dict)
                    and row.get("type") == "Ready"
                    and row.get("status") == "True"
                    and row.get("observedGeneration", meta["generation"]) == meta["generation"]
                    for row in status.get("conditions", [])
                )
            ):
                reason = (
                    "Helm cleanup lacks usable current versioned history/storage inventory: "
                    if context.phase["operation"] == "down" and controller["kind"] == "HelmRelease"
                    else "Controller inventory needs actual current Ready observation: "
                )
                raise StateError(reason + path)
            if controller["kind"] == "HelmRelease":
                children, proof = self._helm(context, controller, allow_failed=allow_failed)
            elif controller["kind"] == "Kustomization":
                children, proof = self._flux(context, controller)
            else:
                children, proof = self._argo(context, controller)
            after = self._get(context, controller)
            _identity(after, controller, meta["uid"])
            keys = (
                "observedGeneration",
                "history",
                "inventory",
                "lastAppliedRevision",
                "sync",
                "health",
                "resources",
                "operationState",
                "conditions",
            )
            if (
                after.get("spec") != controller.get("spec")
                or after.get("operation") != controller.get("operation")
                or after["metadata"].get("generation") != meta.get("generation")
                or any(after.get("status", {}).get(key) != status.get(key) for key in keys)
            ):
                raise StateError("Controller changed during inventory observation: " + path)
            if proof.get("terminal_failure") is not None:
                self._failed_argo(context, after, after["status"]["resources"])
            prior = self._controllers.get(path)
            if prior and (
                prior["uid"] != meta["uid"] or prior["spec_sha256"] != _sha(_bytes(controller["spec"]))
            ):
                raise StateError("Original controller UID/spec changed before cleanup: " + path)
            self._delete_views[path] = _sha(_bytes(after))
            self._controllers[path] = {
                "path": path,
                "resource_version": after["metadata"]["resourceVersion"],
                "uid": meta["uid"],
                "spec_sha256": _sha(_bytes(controller["spec"])),
                **proof,
            }
            if any(
                row is not None and row["kind"] == "Application" and self._route(row)[1] not in self._roots
                for row in children
            ):
                raise StateError(
                    "Nested Argo Applications need an independently selected original root for controller-safe teardown"
                )
            pending.extend(row for row in children if row is not None and row["kind"] in CONTROLLERS)
        self._captured = not self._deferred_applications
        self.resources._record("controller-inventory", self.observation())

    def start_cleanup(self, context, *, reconcile, continuation=False):
        self._admit(context)
        if continuation and (not self._started or reconcile):
            raise StateError("Controller cleanup continuation requires its original frozen inventory")
        if self._started and not reconcile and not continuation:
            raise StateError(
                "Controller cleanup already dispatched; only read-only reconciliation is allowed"
            )
        if continuation:
            self.capture(context, _remaining=True)
        if not self._started:
            if reconcile:
                raise StateError("Controller cleanup was never dispatched; reconciliation cannot start it")
            self.capture(context)
            self._started = True
        # All discovered controllers must remain original until their own removal.
        return self.root_paths()

    def root_paths(self):
        return tuple(
            sorted(
                self._roots,
                key=lambda p: (
                    {"Application": 0, "Kustomization": 1, "HelmRelease": 2}[self._roots[p]["kind"]],
                    p,
                ),
            )
        )

    def authorize_root_delete(self, context, resource, current):
        self._admit(context)
        path = resource["path"]
        if not self._started or path not in self._roots or path not in self._delete_views:
            raise StateError("Controller delete lacks its original captured inventory")
        _identity(current, resource["manifest"], resource["uid"])
        if _sha(_bytes(current)) != self._delete_views[path]:
            raise ControllerObservationChanged(
                "Controller changed after inventory capture; deletion is not authorized"
            )

    def children_absent(self, context):
        self._admit(context)
        if not self._started or not self._captured:
            raise StateError("Controller absence requires original captured inventory")
        complete = True
        self._absence_observed = False
        for path, row in self._children.items():
            descriptor = {**row["descriptor"], "metadata": row["metadata"]}
            value = self._get(context, descriptor)
            if value is None:
                row["state"] = "absent"
            else:
                if row["uid"] is None:
                    raise StateError(
                        "Previously absent failed-release child appeared without an original UID"
                    )
                _identity(value, descriptor, row["uid"])
                row["state"] = "present"
                complete = False
        self._extra_cluster_observations = []
        for path in sorted(
            self._cluster_absent - {row["path"] for row in self.resources._resources} - set(self._children)
        ):
            value = self.resources.session.request("GET", path, deadline=context.deadline)
            self._extra_cluster_observations.append(
                {"path": path, "state": "absent" if value is None else "untracked-present"}
            )
            if value is not None:
                complete = False
        self._absence_observed = True
        self.resources._record("controller-absence", self.observation())
        return complete

    def continuation_ready(self):
        return self._absence_observed and all(
            row["state"] == "absent" for row in self._extra_cluster_observations
        )

    def observation(self):
        return {
            "kind": "owned-controller-cleanup-inventory",
            "policy": self.policy,
            "run_id": self.resources.journal.state["run_id"],
            "cluster_uid": self.resources.cluster_uid,
            "selection_sha256": self._selection,
            "captured": self._captured,
            "cleanup_started": self._started,
            "controllers": deepcopy(list(self._controllers.values())),
            "children": deepcopy(list(self._children.values())),
            "selected_cluster_scope_observations": deepcopy(self._extra_cluster_observations),
            "namespace_closure": deepcopy(self._namespace_closure),
            "pending_application_inventory": deepcopy(self._deferred_applications),
            "authority": "original-observation-only-no-child-delete",
        }
