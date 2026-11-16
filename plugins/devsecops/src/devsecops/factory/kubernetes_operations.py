"""Fixed Kubernetes resource ownership, bounded transport and guarded teardown.

Only direct objects retained by this process confer authority. JSON observations
are evidence, not permission to adopt a resource or repeat an uncertain create.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import base64
import http.client
import json
import os
import re
import socket
import threading
import time
from types import MappingProxyType

from devsecops.adapters.http import HTTPSReadClient, ReadResponse, _json
from .lifecycle import PhaseContext, PhaseOutcome, _load_inputs
from .state import RunJournal, StateError, _read, _sha
from .owned_linux import _plain

LABEL = "devsecops.openai.com/run"
BINDING = "devsecops.openai.com/binding"
ARGO_FOREGROUND_FINALIZER = "resources-finalizer.argocd.argoproj.io"
KINDS = {
    ("apps/v1", "Deployment"): ("/apis/apps/v1", "deployments", True),
    ("apps/v1", "StatefulSet"): ("/apis/apps/v1", "statefulsets", True),
    ("apps/v1", "DaemonSet"): ("/apis/apps/v1", "daemonsets", True),
    ("autoscaling/v2", "HorizontalPodAutoscaler"): ("/apis/autoscaling/v2", "horizontalpodautoscalers", True),
    ("policy/v1", "PodDisruptionBudget"): ("/apis/policy/v1", "poddisruptionbudgets", True),
    ("admissionregistration.k8s.io/v1", "ValidatingWebhookConfiguration"): (
        "/apis/admissionregistration.k8s.io/v1", "validatingwebhookconfigurations", False,
    ),
    ("admissionregistration.k8s.io/v1", "MutatingWebhookConfiguration"): (
        "/apis/admissionregistration.k8s.io/v1", "mutatingwebhookconfigurations", False,
    ),
    ("v1", "Service"): ("/api/v1", "services", True),
    ("v1", "ServiceAccount"): ("/api/v1", "serviceaccounts", True),
    ("v1", "Secret"): ("/api/v1", "secrets", True),
    ("v1", "PersistentVolumeClaim"): ("/api/v1", "persistentvolumeclaims", True),
    ("rbac.authorization.k8s.io/v1", "Role"): ("/apis/rbac.authorization.k8s.io/v1", "roles", True),
    ("rbac.authorization.k8s.io/v1", "RoleBinding"): (
        "/apis/rbac.authorization.k8s.io/v1",
        "rolebindings",
        True,
    ),
    ("rbac.authorization.k8s.io/v1", "ClusterRole"): (
        "/apis/rbac.authorization.k8s.io/v1",
        "clusterroles",
        False,
    ),
    ("rbac.authorization.k8s.io/v1", "ClusterRoleBinding"): (
        "/apis/rbac.authorization.k8s.io/v1",
        "clusterrolebindings",
        False,
    ),
    ("apiextensions.k8s.io/v1", "CustomResourceDefinition"): (
        "/apis/apiextensions.k8s.io/v1",
        "customresourcedefinitions",
        False,
    ),
    ("source.toolkit.fluxcd.io/v1", "HelmRepository"): (
        "/apis/source.toolkit.fluxcd.io/v1",
        "helmrepositories",
        True,
    ),
    ("v1", "Namespace"): ("/api/v1", "namespaces", False),
    ("v1", "ConfigMap"): ("/api/v1", "configmaps", True),
    ("v1", "ResourceQuota"): ("/api/v1", "resourcequotas", True),
    ("v1", "Pod"): ("/api/v1", "pods", True),
    ("networking.k8s.io/v1", "NetworkPolicy"): ("/apis/networking.k8s.io/v1", "networkpolicies", True),
    ("argoproj.io/v1alpha1", "Application"): ("/apis/argoproj.io/v1alpha1", "applications", True),
    ("source.toolkit.fluxcd.io/v1", "GitRepository"): (
        "/apis/source.toolkit.fluxcd.io/v1",
        "gitrepositories",
        True,
    ),
    ("kustomize.toolkit.fluxcd.io/v1", "Kustomization"): (
        "/apis/kustomize.toolkit.fluxcd.io/v1",
        "kustomizations",
        True,
    ),
    ("helm.toolkit.fluxcd.io/v2", "HelmRelease"): ("/apis/helm.toolkit.fluxcd.io/v2", "helmreleases", True),
}


def _bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    return value


def _name(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value)


class KubernetesOperationError(StateError):
    def __init__(self, code, *, status=None, effect="unknown"):
        self.code, self.status, self.effect = code, status, effect
        super().__init__("Kubernetes operation could not complete (" + code + ")")


class KubernetesSession:
    """One selected HTTPS origin and frozen credential; no redirects or retries."""

    def __init__(self, origin, *, ca_file, token_env=None, client_certificate=None):
        self._client = HTTPSReadClient(
            origin,
            ca_bundle=ca_file,
            token_env=token_env,
            client_certificate=client_certificate,
            total_timeout=120,
            max_requests=64,
        )
        self.origin = self._client.origin
        self.ca_file = ca_file
        self.ca_sha256 = _sha(_read(ca_file, 2_000_000))
        self._authorization = self._client._authorization()
        self._pid, self._poisoned = os.getpid(), False
        self._receipts = []

    @property
    def receipts(self):
        return deepcopy(self._receipts)

    def request(self, method, path, *, payload=None, deadline, _health=False):
        health_route = bool(
            method == "GET"
            and isinstance(path, str)
            and re.fullmatch(
                r"/api/v1/namespaces/[a-z0-9-]+/services/[a-z0-9-]+:[1-9][0-9]{0,4}/proxy/healthz?", path
            )
        )
        if (
            _health
            and not health_route
            or self._pid != os.getpid()
            or method not in ("GET", "POST", "PUT", "DELETE")
            or not isinstance(path, str)
            or not (re.fullmatch(r"/(?:api|apis)/[A-Za-z0-9_:./-]+", path) or _health and health_route)
            or any(part in ("", ".", "..") for part in path.split("/")[1:])
            or method != "GET"
            and self._poisoned
        ):
            raise KubernetesOperationError("request-admission", effect="none")
        raw = None if payload is None else _bytes(payload)
        if raw is not None and len(raw) > 1000000:
            raise KubernetesOperationError("request-size", effect="none")
        budget = min(20, deadline - time.monotonic())
        if budget <= 0:
            raise KubernetesOperationError("deadline", effect="none")
        client = self._client
        conn = http.client.HTTPSConnection(
            client._host, client._port, context=client._context, timeout=budget
        )
        sockets = []
        end = time.monotonic() + budget

        def connect():
            conn.sock = client._connect((client._host, client._port), max(0.001, end - time.monotonic()))
            sockets.append(conn.sock)
            conn.sock = client._context.wrap_socket(conn.sock, server_hostname=client._host)
            sockets.append(conn.sock)

        conn.connect = connect

        def abort():
            for sock in sockets:
                try:
                    socket.socket.shutdown(sock, socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()

        timer = threading.Timer(budget, abort)
        timer.daemon = True
        headers = {"Accept": "application/json", "Accept-Encoding": "identity", "Connection": "close"}
        if raw is not None:
            headers["Content-Type"] = "application/json"
        if self._authorization:
            headers["Authorization"] = self._authorization
        receipt = {
            "method": method,
            "path_sha256": hashlib.sha256(path.encode()).hexdigest(),
            "status": None,
            "effect": "unknown",
            "body_sha256": None,
        }
        self._receipts.append(receipt)
        sent = False
        try:
            timer.start()
            sent = True
            conn.request(method, path, body=raw, headers=headers)
            response = conn.getresponse()
            receipt["status"] = response.status
            if response.status not in (200, 201, 202, 404):
                raise KubernetesOperationError(
                    "denied" if response.status in (401, 403) else "http-status",
                    status=response.status,
                    effect="unknown" if method != "GET" else "none",
                )
            body = response.read(1000001)
            if len(body) > 1000000 or time.monotonic() >= end:
                raise KubernetesOperationError("response-bound")
            receipt["body_sha256"] = hashlib.sha256(body).hexdigest()
            if response.status == 404:
                receipt["effect"] = "none"
                return None
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise KubernetesOperationError("response-encoding")
            result = (
                ReadResponse(status=response.status, body=body, media_type=response.getheader("Content-Type"))
                if _health
                else _json(body)
            )
            receipt["effect"] = "observed" if method == "GET" else "applied"
            return result
        except BaseException as error:
            if method != "GET" and sent:
                self._poisoned = True
            if isinstance(error, KubernetesOperationError):
                raise
            raise KubernetesOperationError(
                "transport-or-response", effect="unknown" if sent and method != "GET" else "none"
            ) from None
        finally:
            timer.cancel()
            conn.close()

    def read_service_health(self, namespace, name, port, *, deadline, health_path="/health"):
        if (
            not _name(namespace)
            or not _name(name)
            or type(port) is not int
            or not 1 <= port <= 65535
            or health_path not in ("/health", "/healthz")
        ):
            raise KubernetesOperationError("health-route", effect="none")
        return self.request(
            "GET",
            f"/api/v1/namespaces/{namespace}/services/{name}:{port}/proxy{health_path}",
            deadline=deadline,
            _health=True,
        )


def _route(document):
    try:
        base, plural, namespaced = KINDS[(document["apiVersion"], document["kind"])]
        metadata = document["metadata"]
        name = metadata["name"]
    except (KeyError, TypeError):
        raise StateError("Unsupported owned Kubernetes resource kind") from None
    namespace = metadata.get("namespace")
    if (
        not isinstance(name, str)
        or len(name) > 253
        or not all(_name(part) for part in name.split("."))
        or namespaced
        and not _name(namespace)
        or not namespaced
        and namespace is not None
    ):
        raise StateError("Owned Kubernetes resource requires exact DNS names")
    collection = base + ("/namespaces/" + namespace if namespaced else "") + "/" + plural
    return collection, collection + "/" + name


def _identity(value, desired, *, uid=None):
    if type(value) is not dict:
        raise StateError("Kubernetes identity readback is absent")
    _route(value)
    actual, expected = value["metadata"], desired["metadata"]
    if (
        value["apiVersion"] != desired["apiVersion"]
        or value["kind"] != desired["kind"]
        or actual.get("name") != expected["name"]
        or actual.get("namespace") != expected.get("namespace")
        or not isinstance(actual.get("uid"), str)
        or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", actual["uid"])
        or uid is not None
        and actual["uid"] != uid
        or not isinstance(actual.get("resourceVersion"), str)
        or not actual["resourceVersion"].isdigit()
        or any(actual.get("labels", {}).get(key) != expected["labels"][key] for key in (LABEL, BINDING))
    ):
        raise StateError("Kubernetes resource ownership identity changed")
    return actual["uid"], actual["resourceVersion"]


class OwnedKubernetesResources:
    """Tracked creates and fixed cleanup over one live, scoped Kubernetes origin.

    The bootstrap coordinator supplies exact installed manifest pins before any
    write. A same-name object is never adopted, including after an uncertain POST.
    """

    def __init__(
        self,
        driver,
        *,
        session,
        cluster_uid,
        namespaces,
        manifest_sha256,
        cluster_resources=(),
        reader_selections=(),
        private_secret_sha256=(),
    ):
        if (
            type(session) is not KubernetesSession
            or type(driver.journal) is not RunJournal
            or not isinstance(cluster_uid, str)
            or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", cluster_uid)
            or type(namespaces) is not tuple
            or not namespaces
            or not all(_name(n) for n in namespaces)
            or type(manifest_sha256) is not tuple
            or not all(re.fullmatch("[a-f0-9]{64}", n) for n in manifest_sha256)
        ):
            raise StateError("Kubernetes resource ownership requires exact direct bootstrap inputs")
        self.driver, self.journal, self.session = driver, driver.journal, session
        self.cluster_uid, self.namespaces, self.manifest_sha256 = cluster_uid, namespaces, manifest_sha256
        if type(cluster_resources) is not tuple or any(
            type(row) is not tuple or len(row) != 3 for row in cluster_resources
        ):
            raise StateError("Exact approved cluster resource identities are required")
        self.cluster_resources = cluster_resources
        self._reader_selections = tuple(_bytes(row) for row in reader_selections)
        self._private_secret_sha256 = frozenset(private_secret_sha256)
        self._pid, self._resources, self._unresolved, self._outcomes = os.getpid(), [], [], []
        self._binding = hashlib.sha256(
            _bytes(
                {
                    "run_id": self.journal.state["run_id"],
                    "identities": self.journal.identities,
                    "cluster_uid": cluster_uid,
                    "origin": session.origin,
                }
            )
        ).hexdigest()[:63]
        self._sequence = 0
        self._evidence, self._evidence_bundles = {}, {}
        self._managed_policies = {}
        self._reader_issuances = {}
        self._controller_inventory = None

    def _admit(self, context, *, write=False, reconciliation=False, probe=False):
        if self._pid != os.getpid() or type(context) is not PhaseContext:
            raise StateError("Kubernetes resource operation requires its original process context")
        if reconciliation:
            state = self.journal.state
            lifecycle = state.get("lifecycle", {})
            if (
                not self.journal._lifecycle_locked
                or context.run_id != state["run_id"]
                or context.run_dir != self.journal.run_dir
                or _plain(context.identities) != self.journal.identities
                or _plain(context.input_artifacts) != lifecycle.get("inputs")
                or _plain(context.phase) not in lifecycle.get("phases", [])
                or lifecycle.get("driver_id") != "owned-linux"
                or context.execution_mode != lifecycle.get("execution_mode")
                or lifecycle.get("final_result") is not None
            ):
                raise StateError("Kubernetes reconciliation context differs from current journal")
            paths = {
                name: self.journal._artifact_path(ref["path"]) for name, ref in lifecycle["inputs"].items()
            }
            _, docs = _load_inputs(paths, self.journal.identities, state["environment_id"])
            if docs != _plain(context.inputs):
                raise StateError("Kubernetes reconciliation inputs changed")
        else:
            self.driver._admit(context, context.deadline)
        operation = context.phase["operation"]
        allowed = ("up", "inject", "deliver", "probe", "down") + (("build-promote", "reset") if probe else ())
        if write and operation not in allowed:
            raise StateError("Kubernetes mutations are forbidden in this phase")
        scope = {
            "up": "provision",
            "inject": "inject",
            "deliver": "deliver",
            "probe": "probe",
            "down": "cleanup",
            "build-promote": "build",
            "reset": "reset",
        }.get(operation)
        if write:
            if (
                scope not in context.inputs["config"]["allowed_mutations"]
                or probe
                and "probe" not in context.inputs["config"]["allowed_mutations"]
            ):
                raise StateError("Kubernetes mutation lacks the selected action grant")
            if (
                probe
                and operation in ("build-promote", "reset")
                and not (
                    {"provision", "probe"}
                    | ({"build", "promote"} if operation == "build-promote" else {"reset"})
                )
                <= set(context.inputs["config"]["allowed_mutations"])
            ):
                raise StateError("Probe creation lacks the complete phase/provision/probe grant set")
            current = self.journal.current_mutation(
                context.phase["id"], operation, reconciliation=reconciliation
            )
            if operation != "down" and datetime.now(timezone.utc) >= datetime.fromisoformat(
                context.inputs["config"]["expires_at"].replace("Z", "+00:00")
            ):
                raise StateError("Kubernetes mutation window expired")
        else:
            current = None
        if time.monotonic() >= context.deadline:
            raise StateError("Kubernetes operation deadline expired")
        # Identity is independently observed before every boundary, including cleanup.
        cluster = self.session.request("GET", "/api/v1/namespaces/kube-system", deadline=context.deadline)
        if not cluster or cluster.get("metadata", {}).get("uid") != self.cluster_uid:
            raise StateError("Kubernetes cluster identity differs from bootstrap")
        return current

    def _record(self, kind, value):
        self._sequence += 1
        root = self.journal.run_dir / "kubernetes-resources"
        root.mkdir(mode=0o700, exist_ok=True)
        if root.is_symlink():
            raise StateError("Kubernetes ownership journal is not a private directory")
        path = root / f"{self._sequence:05d}-{kind}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        self._evidence[path] = _sha(_read(path, 16000000))
        return path

    def evidence_artifacts(self, context):
        if (
            self._pid != os.getpid()
            or context.run_id != self.journal.state["run_id"]
            or dict(context.identities) != self.journal.identities
        ):
            raise StateError("Kubernetes evidence requires its original run")
        entries = []
        for path, digest in self._evidence.items():
            raw = _read(path, 16000000)
            if _sha(raw) != digest:
                raise StateError("Original Kubernetes public evidence changed")
            entries.append(
                {
                    "path": str(path.relative_to(self.journal.run_dir)),
                    "sha256": digest,
                    "bytes": len(raw),
                    "content_base64": base64.b64encode(raw).decode(),
                }
            )
        if len(entries) <= 96:
            return tuple(self._evidence)
        key = _sha(_bytes([{key: row[key] for key in ("path", "sha256", "bytes")} for row in entries]))
        if key not in self._evidence_bundles:
            groups, current, size = [], [], 0
            for row in entries:
                bound = len(_bytes(row))
                if current and size + bound > 14000000:
                    groups.append(current)
                    current, size = [], 0
                if bound > 14000000:
                    raise StateError("Kubernetes evidence entry exceeds the frozen aggregate bound")
                current.append(row)
                size += bound
            if current:
                groups.append(current)
            paths = []
            for index, group in enumerate(groups):
                raw = _bytes(
                    {
                        "kind": "original-kubernetes-ledger-bundle",
                        "run_id": context.run_id,
                        "identities": dict(context.identities),
                        "entries": group,
                    }
                )
                path = self.journal.run_dir / ("kubernetes-evidence-" + key + "-" + str(index) + ".json")
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                paths.append((path, _sha(raw)))
            self._evidence_bundles[key] = tuple(paths)
        for path, digest in self._evidence_bundles[key]:
            if _sha(_read(path, 16000000)) != digest:
                raise StateError("Original Kubernetes evidence bundle changed")
        return tuple(path for path, _ in self._evidence_bundles[key])

    phase_artifacts = evidence_artifacts

    def create(self, context, manifest):
        return self._create(context, manifest)

    def _create(self, context, manifest, *, probe=False, private_secret=False):
        operation = self._admit(context, write=True, probe=probe)
        if (
            type(manifest) is not dict
            or not probe
            and not private_secret
            and hashlib.sha256(_bytes(manifest)).hexdigest() not in self.manifest_sha256
        ):
            raise StateError("Kubernetes create must use an exact selected manifest pin")
        if not private_secret and _sha(_bytes(manifest)) in self._private_secret_sha256:
            raise StateError("Derived repository secrets require their original private input capability")
        desired = deepcopy(manifest)
        collection, path = _route(desired)
        meta = desired["metadata"]
        namespace = meta.get("namespace", meta["name"])
        scope_ok = (
            namespace in self.namespaces
            if desired["kind"] == "Namespace" or "namespace" in meta
            else (desired["apiVersion"], desired["kind"], meta["name"]) in self.cluster_resources
        )
        if not scope_ok or any(
            k in meta for k in ("uid", "resourceVersion", "generateName", "ownerReferences")
        ):
            raise StateError("Kubernetes create exceeds exact owned namespace scope")
        if (
            desired["kind"] == "Application"
            and meta.get("finalizers") != [ARGO_FOREGROUND_FINALIZER]
            or desired["kind"] != "Application"
            and meta.get("finalizers")
        ):
            raise StateError("Only the exact selected Argo foreground resource finalizer is admitted")
        if desired["kind"] == "ConfigMap" and desired.get("immutable") is not True:
            raise StateError("Probe configuration must be immutable")
        if desired["kind"] == "Pod":
            spec = desired.get("spec", {})
            if (
                set(spec)
                - {
                    "containers",
                    "volumes",
                    "restartPolicy",
                    "automountServiceAccountToken",
                    "securityContext",
                    "serviceAccountName",
                    "enableServiceLinks",
                    "activeDeadlineSeconds",
                    "terminationGracePeriodSeconds",
                }
                or spec.get("automountServiceAccountToken") is not False
                or spec.get("restartPolicy") != "Never"
                or any(
                    "hostPath" in volume or set(volume) - {"name", "configMap", "emptyDir"}
                    for volume in spec.get("volumes", [])
                )
                or any(
                    not isinstance(container.get("image"), str)
                    or not re.fullmatch(r"[a-z0-9./:_-]+@sha256:[a-f0-9]{64}", container["image"])
                    or container.get("securityContext", {}).get("allowPrivilegeEscalation") is not False
                    or container.get("securityContext", {}).get("capabilities", {}).get("drop") != ["ALL"]
                    or container.get("securityContext", {}).get("privileged", False)
                    for container in spec.get("containers", [])
                )
            ):
                raise StateError("Probe pod isolation or immutable image contract differs")
        if any(row["path"] == path for row in self._resources) or any(
            row["path"] == path for row in self._unresolved
        ):
            raise StateError("Owned Kubernetes create has already been attempted")
        meta.setdefault("labels", {}).update({LABEL: context.run_id, BINDING: self._binding})
        if self.session.request("GET", path, deadline=context.deadline) is not None:
            raise StateError("Kubernetes destination already exists; adoption is forbidden")
        intent = {
            "verb": "create",
            "operation_id": operation,
            "path": path,
            "manifest_sha256": hashlib.sha256(_bytes(desired)).hexdigest(),
        }
        self._record("create-intent", intent)
        self._unresolved.append(intent)
        created = self.session.request("POST", collection, payload=desired, deadline=context.deadline)
        uid, _ = _identity(created, desired)
        # Retain the response identity before the independent GET; failure is unresolved.
        intent["observed_uid"] = uid
        self._record("create-response", intent)
        fresh = self.session.request("GET", path, deadline=context.deadline)
        uid, version = _identity(fresh, desired, uid=uid)

        def contains(actual, expected):
            if type(expected) is dict:
                return type(actual) is dict and all(
                    key in actual and contains(actual[key], child) for key, child in expected.items()
                )
            if type(expected) is list:
                return (
                    type(actual) is list
                    and len(actual) == len(expected)
                    and all(contains(a, b) for a, b in zip(actual, expected))
                )
            return type(actual) is type(expected) and actual == expected

        for key in ("spec", "data", "binaryData", "immutable", "rules", "subjects", "roleRef", "type"):
            if key in desired and not contains(fresh.get(key), desired[key]):
                raise StateError("Kubernetes created content differs from selected manifest")
        record = {
            "path": path,
            "uid": uid,
            "resourceVersion": version,
            "manifest": desired,
            "operation_id": operation,
            "state": "present",
            "disposition": "remove",
            "observed_content": {
                key: deepcopy(value) for key, value in fresh.items() if key not in ("metadata", "status")
            },
            "identity": {
                "kind": desired["kind"],
                "namespace": meta.get("namespace"),
                "name": meta["name"],
                "uid": uid,
            },
        }
        self._record(
            "create-observed",
            {key: value for key, value in record.items() if key not in ("manifest", "observed_content")},
        )
        self._resources.append(record)
        self._unresolved.remove(intent)
        return _freeze(deepcopy(record["identity"]))

    def create_probe(self, context, *, runtime, plan, publication):
        from .runtime_observations import RuntimeObservationSession
        from devsecops.runtime.probe_observations import ProbeActorPlan, validate_probe_plan
        from .ci_publication import VerifiedCIPublication
        from .runtime_image import RuntimeImageProvenance, require_runtime_image

        if (
            type(runtime) is not RuntimeObservationSession
            or runtime.driver.journal is not self.journal
            or type(plan) is not ProbeActorPlan
            or type(publication) not in (VerifiedCIPublication, RuntimeImageProvenance)
        ):
            raise StateError("Probe actors require original runtime plan and verified CI publication")
        frozen = runtime.require_probe_plan(plan, context)
        pod, config = validate_probe_plan(plan, frozen)

        def audit_image():
            return (
                require_runtime_image(publication, run_scope=self.journal.identities)
                if type(publication) is RuntimeImageProvenance
                else publication.describe()
            )

        signed = audit_image()
        image = (
            signed["origin"].removeprefix("https://")
            + "/"
            + signed["repository"]
            + "@"
            + signed["manifest_digest"]
        )
        if (
            signed["run_scope"] != self.journal.identities
            or pod["spec"]["containers"][0]["image"] != image
            or frozen["intent"]["run_id"] != context.run_id
        ):
            raise StateError("Probe image or current run differs from authenticated CI publication")
        self._admit(context, write=True, probe=True)
        config_identity = self._create(context, config, probe=True)
        # Re-audit original capabilities between the two external writes.
        runtime.require_probe_plan(plan, context)
        audit_image()
        pod_identity = self._create(context, pod, probe=True)
        return _freeze({"pod": dict(pod_identity), "configmap": dict(config_identity)})

    def create_platform_secret(self, context, *, inputs, descriptor):
        from .platform_inputs import OwnedPlatformInputs, require_owned_inputs

        if type(inputs) is not OwnedPlatformInputs:
            raise StateError("Repository secret requires original private platform inputs")
        self._admit(context, write=True)
        if context.phase["operation"] != "up":
            raise StateError("Repository credential creation requires provisioning")
        record = require_owned_inputs(inputs, context, resources=self)
        path = _route(descriptor)[1]
        rows = [row for group in record["groups"] for row in group if _route(row)[1] == path]
        if (
            path not in record["private_paths"]
            or len(rows) != 1
            or rows[0]["kind"] != "Secret"
            or rows[0]["type"] != "Opaque"
        ):
            raise StateError("Repository credential is not an original selected reader secret")
        return self._create(context, deepcopy(rows[0]), private_secret=True)

    def _reader_rbac_inventory(self, context, *, namespace, name, selected_uids):
        """Close all applicable API RBAC bindings before issuing a workload reader."""
        principal = "system:serviceaccount:" + namespace + ":" + name
        groups = {"system:authenticated", "system:serviceaccounts", "system:serviceaccounts:" + namespace}
        snapshots = []
        discovery = {
            "/api",
            "/api/*",
            "/apis",
            "/apis/*",
            "/healthz",
            "/livez",
            "/readyz",
            "/healthz/*",
            "/livez/*",
            "/readyz/*",
            "/version",
            "/version/",
            "/openapi",
            "/openapi/*",
            "/.well-known/openid-configuration",
            "/openid/v1/jwks",
        }
        for kind, route in (("RoleBinding", "rolebindings"), ("ClusterRoleBinding", "clusterrolebindings")):
            result = self.session.request(
                "GET", "/apis/rbac.authorization.k8s.io/v1/" + route, deadline=context.deadline
            )
            if (
                type(result) is not dict
                or type(result.get("items")) is not list
                or len(result["items"]) > 1024
                or result.get("metadata", {}).get("continue")
            ):
                raise StateError("Complete bounded effective RBAC inventory is unavailable")
            for binding in result["items"]:
                if type(binding) is not dict or binding.get("kind") != kind:
                    raise StateError("Effective RBAC inventory shape differs")
                subjects = binding.get("subjects", [])
                applies = any(
                    (
                        subject.get("kind") == "ServiceAccount"
                        and subject.get("name") == name
                        and subject.get("namespace") == namespace
                    )
                    or (subject.get("kind") == "User" and subject.get("name") == principal)
                    or (subject.get("kind") == "Group" and subject.get("name") in groups)
                    for subject in subjects
                )
                if not applies:
                    continue
                metadata, ref = binding.get("metadata", {}), binding.get("roleRef", {})
                if (
                    not isinstance(metadata.get("uid"), str)
                    or not metadata["uid"]
                    or ref.get("apiGroup") != "rbac.authorization.k8s.io"
                    or ref.get("kind") not in ("Role", "ClusterRole")
                    or not _name(ref.get("name"))
                    and not isinstance(ref.get("name"), str)
                ):
                    raise StateError("Applicable RBAC binding identity is incomplete")
                # RBAC built-ins may contain colons, but never URL syntax/traversal.
                if not re.fullmatch(r"[A-Za-z0-9:_.-]{1,253}", ref["name"]):
                    raise StateError("Applicable RBAC role name is unsupported")
                if ref["kind"] == "Role":
                    if kind != "RoleBinding" or not _name(metadata.get("namespace")):
                        raise StateError("Namespaced RBAC role lacks its exact scope")
                    path = (
                        "/apis/rbac.authorization.k8s.io/v1/namespaces/"
                        + metadata["namespace"]
                        + "/roles/"
                        + ref["name"]
                    )
                else:
                    path = "/apis/rbac.authorization.k8s.io/v1/clusterroles/" + ref["name"]
                role = self.session.request("GET", path, deadline=context.deadline)
                if (
                    type(role) is not dict
                    or role.get("kind") != ref["kind"]
                    or role.get("metadata", {}).get("name") != ref["name"]
                    or not isinstance(role.get("metadata", {}).get("uid"), str)
                    or type(role.get("rules")) is not list
                    or role.get("aggregationRule")
                ):
                    raise StateError("Applicable RBAC role readback is incomplete or aggregated")
                if metadata["uid"] in selected_uids:
                    original = self.read_owned(
                        context, {key: deepcopy(binding[key]) for key in ("apiVersion", "kind", "metadata")}
                    )
                    original_role = self.read_owned(
                        context, {key: deepcopy(role[key]) for key in ("apiVersion", "kind", "metadata")}
                    )
                    if original != binding or original_role != role:
                        raise StateError("Selected RBAC differs between inventory and fresh readback")
                else:
                    for rule in role["rules"]:
                        if type(rule) is not dict:
                            raise StateError("Applicable RBAC rule is not a bounded object")
                        urls = rule.get("nonResourceURLs") if type(rule) is dict else None
                        discovery_only = (
                            set(rule) == {"nonResourceURLs", "verbs"}
                            and rule.get("verbs") == ["get"]
                            and type(urls) is list
                            and urls
                            and set(urls) <= discovery
                        )
                        introspection_only = (
                            set(rule) == {"apiGroups", "resources", "verbs"}
                            and rule.get("verbs") == ["create"]
                            and (
                                (
                                    rule.get("apiGroups") == ["authorization.k8s.io"]
                                    and bool(rule.get("resources"))
                                    and set(rule["resources"])
                                    <= {"selfsubjectaccessreviews", "selfsubjectrulesreviews"}
                                )
                                or (
                                    rule.get("apiGroups") == ["authentication.k8s.io"]
                                    and rule.get("resources") == ["selfsubjectreviews"]
                                )
                            )
                        )
                        if not discovery_only and not introspection_only:
                            raise StateError(
                                "Unselected effective RBAC grants workload access to this reader"
                            )
                snapshots.append({"binding": binding, "role": role})
        seen = {row["binding"]["metadata"]["uid"] for row in snapshots}
        if not set(selected_uids) <= seen:
            raise StateError("Selected reader bindings are absent from complete RBAC inventory")
        return _sha(_bytes(sorted(snapshots, key=lambda row: row["binding"]["metadata"]["uid"])))

    def issue_read_token(self, context, *, selection):
        """Issue only to a selected, directly owned SA and read-only RBAC set."""
        from .owned_runtime_reader import _issue
        from .bootstrap_recipe import runtime_reader_selection

        selection = runtime_reader_selection(selection)
        if _bytes(selection) not in self._reader_selections:
            raise StateError("Runtime reader selection was not frozen by owned bootstrap")
        operation = self._admit(context, write=True)
        if (
            context.phase["operation"] != "up"
            or "provision" not in context.inputs["config"]["allowed_mutations"]
        ):
            raise StateError("Runtime reader issuance requires provisioning authority")
        sa_descriptor = selection["serviceaccount"]
        sa = self.read_owned(context, sa_descriptor)
        if sa.get("automountServiceAccountToken") is not False:
            raise StateError("Runtime reader service account must disable automatic token mounts")
        uid, ns, name = sa["metadata"]["uid"], sa["metadata"]["namespace"], sa["metadata"]["name"]
        if uid in self._reader_issuances:
            raise StateError("Runtime reader TokenRequest was already attempted")
        rights = []
        selected_bindings = set()
        for descriptor in selection["bindings"]:
            binding = self.read_owned(context, descriptor)
            selected_bindings.add(binding["metadata"]["uid"])
            if binding.get("subjects") != [{"kind": "ServiceAccount", "name": name, "namespace": ns}]:
                raise StateError("Reader binding must name only the original service account")
            ref = binding.get("roleRef", {})
            expected_kind = "ClusterRole" if binding["kind"] == "ClusterRoleBinding" else "Role"
            if ref.get("apiGroup") != "rbac.authorization.k8s.io" or ref.get("kind") != expected_kind:
                raise StateError("Reader binding role reference is unsupported")
            role_descriptor = {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": expected_kind,
                "metadata": {"name": ref.get("name")},
            }
            if expected_kind == "Role":
                role_descriptor["metadata"]["namespace"] = binding["metadata"]["namespace"]
            role = self.read_owned(context, role_descriptor)
            if role.get("aggregationRule"):
                raise StateError("Aggregated roles cannot establish fixed reader rights")
            rules = role.get("rules")
            if type(rules) is not list or not rules:
                raise StateError("Reader role must contain bounded explicit read rules")
            for rule in rules:
                if (
                    type(rule) is not dict
                    or set(rule) - {"apiGroups", "resources", "resourceNames", "verbs"}
                    or not rule.get("verbs")
                    or not set(rule["verbs"]) <= {"get", "list"}
                    or not rule.get("apiGroups")
                    or "*" in rule["apiGroups"]
                    or not rule.get("resources")
                    or "*" in rule["resources"]
                    or any(
                        not isinstance(value, str) or value == "*" for value in rule.get("resourceNames", [])
                    )
                ):
                    raise StateError("Reader RBAC must be explicit get/list only")
                allowed = {
                    "": {
                        "namespaces",
                        "nodes",
                        "pods",
                        "pods/log",
                        "services",
                        "services/proxy",
                        "configmaps",
                        "events",
                    },
                    "apps": {"deployments", "statefulsets", "daemonsets", "replicasets"},
                    "discovery.k8s.io": {"endpointslices"},
                    "networking.k8s.io": {"networkpolicies"},
                    "argoproj.io": {"applications"},
                    "source.toolkit.fluxcd.io": {"gitrepositories", "helmrepositories"},
                    "kustomize.toolkit.fluxcd.io": {"kustomizations"},
                    "helm.toolkit.fluxcd.io": {"helmreleases"},
                }
                if any(
                    group not in allowed or not set(rule["resources"]) <= allowed[group]
                    for group in rule["apiGroups"]
                ):
                    raise StateError("Reader RBAC selects unsupported resources")
                if "services/proxy" in rule["resources"] and (
                    rule["verbs"] != ["get"] or not rule.get("resourceNames")
                ):
                    raise StateError("Service proxy read must select exact service names")
                rights.append(
                    {
                        "namespace": binding["metadata"].get("namespace"),
                        "role_uid": role["metadata"]["uid"],
                        "binding_uid": binding["metadata"]["uid"],
                        **deepcopy(rule),
                    }
                )
        # Reject extra current-run grants to this SA, including group subjects.
        for row in self._resources:
            doc = row["manifest"]
            if row["state"] != "present" or doc["kind"] not in ("RoleBinding", "ClusterRoleBinding"):
                continue
            subjects = doc.get("subjects", [])
            matches = any(
                (
                    subject.get("kind") == "ServiceAccount"
                    and subject.get("name") == name
                    and subject.get("namespace") == ns
                )
                or (
                    subject.get("kind") == "Group"
                    and subject.get("name")
                    in ("system:authenticated", "system:serviceaccounts", "system:serviceaccounts:" + ns)
                )
                for subject in subjects
            )
            if matches and row["uid"] not in selected_bindings:
                raise StateError("Unselected owned RBAC also grants this reader access")
        if (selection["role"] == "denied" and rights) or (selection["role"] == "runtime" and not rights):
            raise StateError("Runtime and denied reader scopes differ")
        rbac_digest = self._reader_rbac_inventory(
            context, namespace=ns, name=name, selected_uids=selected_bindings
        )
        now = datetime.now(timezone.utc)
        expires = datetime.fromisoformat(context.inputs["config"]["expires_at"].replace("Z", "+00:00"))
        duration = selection["duration_seconds"]
        if duration > int((expires - now).total_seconds()):
            raise StateError("Reader lifetime exceeds the current provisioning grant")
        path = "/api/v1/namespaces/" + ns + "/serviceaccounts/" + name + "/token"
        payload = {
            "apiVersion": "authentication.k8s.io/v1",
            "kind": "TokenRequest",
            "spec": {"audiences": [selection["audience"]], "expirationSeconds": duration},
        }
        intent = {
            "verb": "issue-reader",
            "operation_id": operation,
            "path": path,
            "uid": uid,
            "role": selection["role"],
            "audience": selection["audience"],
            "duration_seconds": duration,
        }
        self._record("reader-intent", intent)
        self._reader_issuances[uid] = {"state": "unknown", "intent": intent}
        self._unresolved.append(intent)
        value = self.session.request("POST", path, payload=payload, deadline=context.deadline)
        status = value.get("status", {}) if type(value) is dict else {}
        token, expiration = status.get("token"), status.get("expirationTimestamp")
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 32768
            or any(ord(c) < 33 or ord(c) > 126 for c in token)
            or not isinstance(expiration, str)
        ):
            raise StateError("Original TokenRequest response is incomplete")
        actual_expiry = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
        if (
            actual_expiry.tzinfo is None
            or not now < actual_expiry <= expires
            or (actual_expiry - now).total_seconds() > duration + 5
        ):
            raise StateError("Actual TokenRequest lifetime exceeds its selected bound")
        fresh = self.read_owned(context, sa_descriptor)
        if fresh["metadata"]["uid"] != uid:
            raise StateError("Service account changed during TokenRequest")
        if (
            self._reader_rbac_inventory(context, namespace=ns, name=name, selected_uids=selected_bindings)
            != rbac_digest
        ):
            raise StateError("Effective reader RBAC changed during TokenRequest")
        reader = _issue(
            self,
            context,
            selection=selection,
            serviceaccount=fresh,
            rights=rights,
            token=token,
            expiration=expiration,
            ca_path=self.session.ca_file,
            rbac_sha256=rbac_digest,
        )
        self._reader_issuances[uid].update(state="issued", capability=reader)
        self._record(
            "reader-observed", {**intent, "token_expires_at": expiration, "selected_resource_grants": rights}
        )
        self._unresolved.remove(intent)
        return reader

    def replace_network_policy(
        self, context, *, original_uid, expected_spec_sha256, manifest, runtime_inputs=None
    ):
        """One selector-value transition on an original owned policy, with CAS."""
        operation = self._admit(context, write=True, probe=True)
        if context.phase["operation"] not in ("build-promote", "reset", "inject", "deliver", "probe"):
            raise StateError("NetworkPolicy control transition is outside an admitted mutation phase")
        if (
            type(manifest) is not dict
            or manifest.get("kind") != "NetworkPolicy"
            or manifest.get("apiVersion") != "networking.k8s.io/v1"
        ):
            raise StateError("Only fixed NetworkPolicy selector transitions are supported")
        _, path = _route(manifest)
        if self._unresolved:
            raise StateError("Policy transition requires no unresolved effects")
        managed = runtime_inputs is not None

        def identity(value, desired, expected_uid):
            if not managed:
                return _identity(value, desired, uid=expected_uid)
            metadata = value.get("metadata", {}) if type(value) is dict else {}
            expected = desired["metadata"]
            if (
                not value
                or _route(value) != _route(desired)
                or metadata.get("uid") != expected_uid
                or not isinstance(metadata.get("resourceVersion"), str)
                or not metadata["resourceVersion"].isdigit()
                or metadata.get("labels", {}) != expected.get("labels", {})
                or metadata.get("annotations", {}) != expected.get("annotations", {})
            ):
                raise StateError("Managed policy original metadata identity changed")
            return metadata["uid"], metadata["resourceVersion"]

        if managed:
            from .owned_runtime_inputs import OwnedRuntimeInputCoordinator

            if (
                type(runtime_inputs) is not OwnedRuntimeInputCoordinator
                or runtime_inputs.journal is not self.journal
                or runtime_inputs.bootstrap.kubernetes(context) is not self
            ):
                raise StateError("Managed policy requires the original runtime input producer")
            authority = runtime_inputs.require_policy_target(context)
            if (
                authority["run_id"] != context.run_id
                or _plain(authority["identities"]) != self.journal.identities
                or authority["cluster_uid"] != self.cluster_uid
                or authority["origin"] != self.session.origin
                or authority["application"].get("self_heal") is not False
                or authority["policy_path"] != path
                or authority["policy"]["metadata"].get("uid") != original_uid
            ):
                raise StateError("Managed policy authority differs from original delivery/cluster")
            namespace = self.session.request(
                "GET", "/api/v1/namespaces/" + authority["namespace"], deadline=context.deadline
            )
            if not namespace or namespace.get("metadata", {}).get("uid") != authority["namespace_uid"]:
                raise StateError("Managed policy namespace identity changed")
            permitted = {authority["baseline_spec_sha256"], authority["fault_spec_sha256"]}
            if {expected_spec_sha256, hashlib.sha256(_bytes(manifest.get("spec"))).hexdigest()} != permitted:
                raise StateError("Managed policy transition differs from rendered baseline/fault")
            current = self.session.request("GET", path, deadline=context.deadline)
            desired = authority["policy"]
            uid, version = identity(current, desired, original_uid)
            if current.get("spec") != desired.get("spec") or version != desired["metadata"].get(
                "resourceVersion"
            ):
                raise StateError("Managed policy changed after original runtime observation")
            retained = self._managed_policies.get(path)
            if retained and (
                retained["uid"] != uid or retained["namespace_uid"] != authority["namespace_uid"]
            ):
                raise StateError("Managed policy original identity changed")
            row = {"manifest": deepcopy(desired), "uid": uid, "resourceVersion": version}
        else:
            rows = [
                row
                for row in self._resources
                if row["path"] == path and row["uid"] == original_uid and row["state"] == "present"
            ]
            if len(rows) != 1:
                raise StateError("Policy transition requires an original owned UID")
            row = rows[0]
            current = self.session.request("GET", path, deadline=context.deadline)
            uid, version = _identity(current, row["manifest"], uid=original_uid)
        if hashlib.sha256(_bytes(current.get("spec"))).hexdigest() != expected_spec_sha256:
            raise StateError("NetworkPolicy control base spec changed")

        def differences(a, b, path=()):
            if type(a) is not type(b):
                return [path]
            if type(a) is dict:
                if set(a) != set(b):
                    return [path]
                return [p for key in a for p in differences(a[key], b[key], (*path, key))]
            if type(a) is list:
                if len(a) != len(b):
                    return [path]
                return [p for n, (x, y) in enumerate(zip(a, b)) for p in differences(x, y, (*path, n))]
            return [] if a == b else [path]

        target = deepcopy(manifest.get("spec"))
        delta = differences(current.get("spec"), target)
        if (
            len(delta) != 1
            or len(delta[0]) != 7
            or delta[0][0] != "ingress"
            or type(delta[0][1]) is not int
            or delta[0][2] != "from"
            or type(delta[0][3]) is not int
            or delta[0][4:6] != ("podSelector", "matchLabels")
            or not isinstance(delta[0][6], str)
        ):
            raise StateError("NetworkPolicy control may change only one ingress pod-selector label value")
        before, after = current["spec"], target
        for key in delta[0]:
            before, after = before[key], after[key]
        if not all(
            isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", value)
            for value in (before, after)
        ):
            raise StateError("NetworkPolicy control selector value is unsupported")
        body = {key: deepcopy(current[key]) for key in ("apiVersion", "kind", "metadata", "spec")}
        body["metadata"].pop("managedFields", None)
        body["spec"] = target
        target_sha = hashlib.sha256(_bytes(target)).hexdigest()
        intent = {
            "verb": "replace-policy",
            "operation_id": operation,
            "path": path,
            "uid": uid,
            "resourceVersion": version,
            "before_spec_sha256": expected_spec_sha256,
            "after_spec_sha256": target_sha,
        }
        if managed:
            intent["managed_by"] = "original-owned-argo-delivery"
            intent["application_uid"] = authority["application"]["uid"]
            self._managed_policies[path] = {
                "uid": uid,
                "namespace_uid": authority["namespace_uid"],
                "baseline_spec_sha256": authority["baseline_spec_sha256"],
                "application_uid": authority["application"]["uid"],
                "state": "transition-unknown",
            }
        self._record("policy-intent", intent)
        self._unresolved.append(intent)
        result = self.session.request("PUT", path, payload=body, deadline=context.deadline)
        identity(result, row["manifest"], uid)
        fresh = self.session.request("GET", path, deadline=context.deadline)
        _, new_version = identity(fresh, row["manifest"], uid)
        if fresh.get("spec") != target:
            raise StateError("NetworkPolicy control transition has uncertain effect")
        self._record("policy-observed", {**intent, "observed_resourceVersion": new_version})
        self._unresolved.remove(intent)
        row["resourceVersion"] = new_version
        row["manifest"]["spec"] = target
        row["observed_content"] = {
            key: deepcopy(value) for key, value in fresh.items() if key not in ("metadata", "status")
        }
        if managed:
            self._managed_policies[path].update(
                state="baseline" if target_sha == authority["baseline_spec_sha256"] else "fault",
                observed_spec_sha256=target_sha,
            )
        return _freeze({**intent, "observed_resourceVersion": new_version, "mutation_effect": "applied"})

    def select_platform_commit(self, context, *, transition):
        from .case_platform_git import PlatformGitTransition, require_platform_transition

        if type(transition) is not PlatformGitTransition:
            raise StateError("Platform source selection requires original direct Git transition")
        selected = require_platform_transition(transition, journal=self.journal, context=context)
        self.driver._admit(context, context.deadline)
        if (
            context.phase["id"] != "platform-dependency"
            or context.phase["operation"] != "case"
            or not {"inject", "deliver", "probe"} <= set(context.inputs["config"]["allowed_mutations"])
        ):
            raise StateError("Platform commit selection is outside its exact D3 phase and grants")
        operation = self.journal.current_mutation(context.phase["id"], "platform-dependency")
        if selected["operation_id"] != operation or self._unresolved:
            raise StateError("Platform transition differs from current original operation")
        manifest = {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "GitRepository",
            "metadata": {"namespace": selected["namespace"], "name": selected["name"]},
        }
        _, path = _route(manifest)
        rows = [
            row
            for row in self._resources
            if row["path"] == path and row["uid"] == selected["uid"] and row["state"] == "present"
        ]
        if len(rows) != 1:
            raise StateError("Platform source is not an original owned GitRepository")
        row = rows[0]
        fresh = self.read_owned(context, manifest)
        uid, version = _identity(fresh, row["manifest"], uid=selected["uid"])
        if fresh.get("spec", {}).get("url") != selected["source_url"] or fresh["spec"].get("ref") != {
            "commit": selected["previous_commit"]
        }:
            raise StateError("Owned platform source changed before exact commit selection")
        body = {key: deepcopy(fresh[key]) for key in ("apiVersion", "kind", "metadata", "spec")}
        body["metadata"].pop("managedFields", None)
        body["spec"]["ref"] = {"commit": selected["commit"]}
        intent = {
            "verb": "platform-commit",
            "operation_id": operation,
            "path": path,
            "uid": uid,
            "resourceVersion": version,
            "previous_commit": selected["previous_commit"],
            "commit": selected["commit"],
        }
        self._record("platform-intent", intent)
        self._unresolved.append(intent)
        require_platform_transition(transition, journal=self.journal, context=context)
        result = self.session.request("PUT", path, payload=body, deadline=context.deadline)
        _identity(result, row["manifest"], uid=uid)
        readback = self.session.request("GET", path, deadline=context.deadline)
        _, observed_version = _identity(readback, row["manifest"], uid=uid)
        if readback.get("spec") != body["spec"]:
            raise StateError("Platform source selection has uncertain effects")
        self._record("platform-observed", {**intent, "observed_resourceVersion": observed_version})
        self._unresolved.remove(intent)
        row["manifest"]["spec"] = body["spec"]
        row["resourceVersion"] = observed_version
        row["observed_content"] = {
            key: deepcopy(value) for key, value in readback.items() if key not in ("metadata", "status")
        }
        return _freeze({**intent, "observed_resourceVersion": observed_version, "mutation_effect": "applied"})

    def read_platform_extension(self, context, *, transition, descriptor):
        from .case_platform_git import PlatformGitTransition, require_platform_transition

        if type(transition) is not PlatformGitTransition:
            raise StateError("Platform extension read requires original direct Git transition")
        selected = require_platform_transition(transition, journal=self.journal, context=context)
        self._admit(context)
        extension = selected["extension"]
        if (
            descriptor.get("apiVersion") != "kustomize.toolkit.fluxcd.io/v1"
            or descriptor.get("kind") != "Kustomization"
            or descriptor.get("metadata") != {"namespace": extension["namespace"], "name": extension["name"]}
        ):
            raise StateError("Platform extension descriptor differs from the exact Git transition")
        source = self.read_owned(
            context,
            {
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository",
                "metadata": {"namespace": selected["namespace"], "name": selected["name"]},
            },
        )
        if (
            source["metadata"]["uid"] != selected["uid"]
            or source["spec"].get("url") != selected["source_url"]
            or source["spec"].get("ref") != {"commit": selected["commit"]}
        ):
            raise StateError("Platform extension read differs from the selected original source commit")
        _, path = _route(descriptor)
        rows = [
            row
            for row in self._resources
            if row["path"] == path and row["uid"] == extension["uid"] and row["state"] == "present"
        ]
        if len(rows) != 1:
            raise StateError("Platform extension is not an original directly owned resource")
        row = rows[0]
        observed = self.session.request("GET", path, deadline=context.deadline)
        _identity(observed, row["manifest"], uid=extension["uid"])
        actual = {key: value for key, value in observed.items() if key not in ("metadata", "status")}
        allowed = []
        for depends_on in (selected["previous_depends_on"], selected["expected_depends_on"]):
            candidate = deepcopy(row["observed_content"])
            candidate["spec"]["dependsOn"] = deepcopy(depends_on)
            allowed.append(candidate)
        if actual not in allowed:
            raise StateError(
                "Platform extension changed outside its exact pending or applied dependency transition"
            )
        return deepcopy(observed)

    def read_owned(self, context, manifest):
        self._admit(context)
        _, path = _route(manifest)
        rows = [row for row in self._resources if row["path"] == path and row["state"] == "present"]
        if len(rows) != 1:
            raise StateError("Readiness requires an original directly owned resource")
        value = self.session.request("GET", path, deadline=context.deadline)
        return self._audit_owned_observation(rows[0], value)

    def audit_owned_read(self, context, manifest, value):
        """Check a private-reader result against retained ownership without I/O.

        This is a consistency check, not a credential or observation issuer. The
        caller must obtain ``value`` through its original admitted reader.
        """
        if self._pid != os.getpid() or type(context) is not PhaseContext:
            raise StateError("Owned read audit requires the original process context")
        self.driver._admit(context, context.deadline)
        if time.monotonic() >= context.deadline:
            raise StateError("Owned read audit deadline expired")
        _, path = _route(manifest)
        rows = [row for row in self._resources if row["path"] == path and row["state"] == "present"]
        if len(rows) != 1:
            raise StateError("Read audit requires an original directly owned resource")
        return self._audit_owned_observation(rows[0], value)

    def _audit_owned_observation(self, row, value):
        _identity(value, row["manifest"], uid=row["uid"])
        if row["manifest"]["kind"] == "Application" and value["metadata"].get("finalizers") != [
            ARGO_FOREGROUND_FINALIZER
        ]:
            raise StateError("Original Argo foreground cleanup finalizer changed")
        actual = {key: child for key, child in value.items() if key not in ("metadata", "status")}
        if actual != row.get("observed_content"):
            raise StateError("Original owned Kubernetes content changed with the same UID")
        for name in ("labels", "annotations"):
            if any(
                value["metadata"].get(name, {}).get(key) != child
                for key, child in row["manifest"]["metadata"].get(name, {}).items()
            ):
                raise StateError("Original Kubernetes metadata binding changed")
        return deepcopy(value)

    def owned(self, context):
        self._admit(context)
        return tuple(
            _freeze(deepcopy(row["identity"])) for row in self._resources if row["state"] == "present"
        )

    def _cleanup(self, context, *, reconcile=False, operation_id=None, continuation=False):
        operation = self._admit(context, write=True, reconciliation=reconcile)
        if context.phase["operation"] != "down" or operation_id is not None and operation != operation_id:
            raise StateError("Kubernetes cleanup requires the exact down operation")
        # Managed policies remain controller-owned: independently verify restoration
        # or actual namespace/resource absence; never adopt them for deletion.
        managed_unresolved = []
        for path, record in self._managed_policies.items():
            observed = self.session.request("GET", path, deadline=context.deadline)
            namespace = path.split("/namespaces/", 1)[1].split("/", 1)[0]
            ns = self.session.request("GET", "/api/v1/namespaces/" + namespace, deadline=context.deadline)
            if observed is None and ns is None:
                record["state"] = "absent-with-owned-namespace"
            elif (
                observed
                and observed.get("metadata", {}).get("uid") == record["uid"]
                and ns
                and ns.get("metadata", {}).get("uid") == record["namespace_uid"]
                and hashlib.sha256(_bytes(observed.get("spec"))).hexdigest() == record["baseline_spec_sha256"]
            ):
                record["state"] = "baseline"
                if reconcile:
                    self._unresolved[:] = [
                        intent
                        for intent in self._unresolved
                        if not (
                            intent.get("verb") == "replace-policy"
                            and intent.get("path") == path
                            and intent.get("uid") == record["uid"]
                            and intent.get("after_spec_sha256") == record["baseline_spec_sha256"]
                        )
                    ]
            else:
                managed_unresolved.append(path)
        if managed_unresolved:
            raise StateError("Managed policy restoration remains unresolved before owned teardown")
        # Finalizers and uninstallers need their original controllers alive.
        from .controller_cleanup import CONTROLLERS, OwnedControllerInventory, ControllerObservationChanged

        inventory = self._controller_inventory
        has_controllers = any(row["manifest"]["kind"] in CONTROLLERS for row in self._resources)
        if has_controllers and type(inventory) is not OwnedControllerInventory:
            raise StateError("Controller cleanup requires its original pre-effect inventory")
        roots = (
            inventory.start_cleanup(context, reconcile=reconcile, continuation=continuation)
            if inventory is not None
            else ()
        )
        root_rows = [row for path in roots for row in self._resources if row["path"] == path]
        remainder = [row for row in reversed(self._resources) if row["path"] not in roots]
        children_complete = inventory is None
        deferred_controllers = []
        for resource in root_rows + remainder:
            if inventory is not None and resource["path"] not in roots and not children_complete:
                # No new mutation while retained children or an uncertain root remain.
                attempts = 1 if reconcile else 20
                for _ in range(attempts):
                    children_complete = inventory.children_absent(context)
                    if children_complete or time.monotonic() + 0.25 >= context.deadline:
                        break
                    time.sleep(0.25)
                if not children_complete and not reconcile:
                    break
            current = self.session.request("GET", resource["path"], deadline=context.deadline)
            if current is None:
                resource["state"] = "absent"
                if reconcile:
                    self._unresolved[:] = [
                        intent
                        for intent in self._unresolved
                        if not (
                            intent.get("verb") == "delete"
                            and intent.get("path") == resource["path"]
                            and intent.get("uid") == resource["uid"]
                            and intent.get("operation_id") == operation
                        )
                    ]
                continue
            uid, version = _identity(current, resource["manifest"], uid=resource["uid"])
            if reconcile:
                if inventory is not None:
                    self._audit_owned_observation(resource, current)
                continue
            if inventory is not None and resource["path"] in roots:
                try:
                    inventory.authorize_root_delete(context, resource, current)
                except ControllerObservationChanged:
                    deferred_controllers.append(resource["path"])
                    break
            if resource["manifest"]["kind"] == "Application" and current["metadata"].get("finalizers") != [
                ARGO_FOREGROUND_FINALIZER
            ]:
                raise StateError("Argo foreground cleanup finalizer changed before deletion")
            intent = {
                "verb": "delete",
                "operation_id": operation,
                "path": resource["path"],
                "uid": uid,
                "resourceVersion": version,
            }
            self._record("delete-intent", intent)
            self._unresolved.append(intent)
            self.session.request(
                "DELETE",
                resource["path"],
                payload={
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "propagationPolicy": "Foreground",
                    "preconditions": {"uid": uid, "resourceVersion": version},
                },
                deadline=context.deadline,
            )
            for _ in range(20):
                fresh = self.session.request("GET", resource["path"], deadline=context.deadline)
                if fresh is None:
                    resource["state"] = "absent"
                    self._unresolved.remove(intent)
                    self._record("delete-observed", intent)
                    break
                _identity(fresh, resource["manifest"], uid=uid)
                if time.monotonic() + 0.25 >= context.deadline:
                    break
                time.sleep(0.25)
            if intent in self._unresolved:
                break
        for resource in self._resources:
            issuance = self._reader_issuances.get(resource["uid"])
            if resource["state"] == "absent" and issuance:
                issuance["state"] = "serviceaccount-absent"
                # TokenRequest cannot be individually revoked. The actual original
                # ServiceAccount UID's removal invalidates its bound credentials.
                self._unresolved[:] = [
                    intent
                    for intent in self._unresolved
                    if not (intent.get("verb") == "issue-reader" and intent.get("uid") == resource["uid"])
                ]
        if inventory is not None and not children_complete:
            children_complete = inventory.children_absent(context)
        complete = (
            children_complete
            and not self._unresolved
            and all(row["state"] == "absent" for row in self._resources)
        )
        value = {
            "kind": "owned-kubernetes-cleanup",
            "run_id": context.run_id,
            "operation_id": operation,
            "action": "reconcile" if reconcile else "down",
            "identities": dict(self.journal.identities),
            "cluster_uid": self.cluster_uid,
            "origin": self.session.origin,
            "resources": [
                {key: row[key] for key in ("identity", "disposition", "state")} for row in self._resources
            ],
            "controller_inventory": inventory.observation() if inventory is not None else None,
            "deferred_controller_paths": deferred_controllers,
            "managed_policy_observations": deepcopy(self._managed_policies),
            "reader_credentials": [
                {"serviceaccount_uid": uid, "state": row["state"]}
                for uid, row in self._reader_issuances.items()
            ],
            "unresolved_operations": deepcopy(self._unresolved),
            "complete_cleanup": complete,
            "cleanup_eligible": reconcile
            and not self._unresolved
            and (inventory is None or inventory.continuation_ready()),
            "mutation_effect": "applied" if not self._unresolved else "unknown",
            "service_ids": [],
        }
        artifact = self._record("cleanup", value)
        outcome = PhaseOutcome(
            "incomplete",
            "owned-kubernetes-scope-observed",
            (artifact,),
            mutation_effect=value["mutation_effect"],
        )
        self._outcomes.append((outcome, _freeze(deepcopy(value)), _sha(_read(artifact, 1000000))))
        return outcome

    def down(self, context):
        return self._cleanup(context)

    def reconcile(self, context, operation_id):
        return self._cleanup(context, reconcile=True, operation_id=operation_id)

    def continue_cleanup(self, context, prior_operation_id):
        """Fresh explicitly chained action after terminal original reconciliation."""
        operation = self._admit(context, write=True)
        active = self.journal.state.get("active_mutation")
        prior = self.cleanup_observation(self._outcomes[-1][0]) if self._outcomes else None
        if (
            operation == prior_operation_id
            or not active
            or active.get("continues_operation") != prior_operation_id
            or prior is None
            or prior["action"] != "reconcile"
            or prior["operation_id"] != prior_operation_id
            or prior.get("cleanup_eligible") is not True
            or prior["unresolved_operations"]
            or self._unresolved
        ):
            raise StateError(
                "Kubernetes cleanup continuation lacks terminal original reconciliation and new scoped operation"
            )
        for row in self._resources:
            if row["state"] == "present":
                observed = self.session.request("GET", row["path"], deadline=context.deadline)
                self._audit_owned_observation(row, observed)
        self._record(
            "cleanup-continuation", {"operation_id": operation, "prior_operation_id": prior_operation_id}
        )
        return self._cleanup(context, continuation=True)

    def cleanup_observation(self, outcome):
        if self._pid != os.getpid():
            raise StateError("Kubernetes cleanup observation belongs to another process")
        for original, observation, digest in self._outcomes:
            if original is outcome:
                if _sha(_read(original.artifacts[0], 1000000)) != digest:
                    raise StateError("Kubernetes cleanup artifact changed")
                return observation
        raise StateError("Kubernetes cleanup requires the original direct outcome")
