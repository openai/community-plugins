"""Offline locked Flux/Big Bang compilation and directly observed installation.

No download, repository update, image acquisition or generic post-render command
is supported. The actual selected chart, values, manifests and Helm executable
must already exist. Applying them is separate from observing controller readiness.
"""

from __future__ import annotations
from copy import deepcopy
import io
import os
from pathlib import Path
import re
import tarfile
import time
from uuid import uuid4

from devsecops.process import run
from devsecops.render import inspect_chart, tool_identity
from devsecops.resources import strict_load_yaml
from .bootstrap_recipe import canonical, pin, shape
from .kubernetes_operations import OwnedKubernetesResources, _route
from .state import StateError, _read, _safe_path, _sha


def validate(value):
    shape(
        value,
        "flux_version bigbang_version flux_manifest chart chart_metadata_sha256 values helm images namespaces cluster_resources readiness"
        + (" controller_cleanup" if "controller_cleanup" in value else ""),
    )
    if "controller_cleanup" in value:
        from .controller_cleanup import POLICY

        if value["controller_cleanup"] != POLICY:
            raise StateError("Unsupported selected controller cleanup policy")
    for key in ("flux_version", "bigbang_version"):
        if not isinstance(value[key], str) or not re.fullmatch(
            r"v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", value[key]
        ):
            raise StateError("Selected platform version is unsupported")
    for key in ("flux_manifest", "chart", "values", "helm"):
        pin(value[key])
    if not re.fullmatch("[a-f0-9]{64}", value["chart_metadata_sha256"]):
        raise StateError("Big Bang chart metadata requires its independently pinned digest")
    if (
        type(value["images"]) is not list
        or not value["images"]
        or len(value["images"]) > 128
        or not all(
            isinstance(s, str) and re.fullmatch(r"[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}", s)
            for s in value["images"]
        )
    ):
        raise StateError("Platform controllers require selected immutable image references")
    if type(value["namespaces"]) is not list or not {"flux-system", "bigbang"} <= set(value["namespaces"]):
        raise StateError("Platform namespaces must include fixed Flux and Big Bang scopes")
    if type(value["cluster_resources"]) is not list or len(value["cluster_resources"]) > 128:
        raise StateError("Platform cluster resource scope is bounded")
    for item in value["cluster_resources"]:
        shape(item, "apiVersion kind name")
        _route({"apiVersion": item["apiVersion"], "kind": item["kind"], "metadata": {"name": item["name"]}})
    shape(value["readiness"], "flux_deployments helm_releases argo_application")
    for field in ("flux_deployments", "helm_releases"):
        if (
            type(value["readiness"][field]) is not list
            or not value["readiness"][field]
            or len(value["readiness"][field]) > 64
        ):
            raise StateError("Platform readiness requires a bounded explicit component inventory")
        for item in value["readiness"][field]:
            shape(item, "namespace name")
    shape(value["readiness"]["argo_application"], "namespace name")
    return deepcopy(value)


class OwnedPlatformBootstrap:
    def __init__(self, driver, recipe):
        self.driver, self.journal = driver, driver.journal
        self.recipe = validate(recipe)
        self._recipe_digest = _sha(canonical(self.recipe))
        self._pid, self._compiled, self._observation = os.getpid(), None, None
        self._files = {}
        self._compiled_digest = None
        self._owned_inputs = None

    def _admit(self, context):
        if self._pid != os.getpid():
            raise StateError("Platform producer belongs to another process")
        if _sha(canonical(self.recipe)) != self._recipe_digest:
            raise StateError("Original platform selection changed")
        self.driver._admit(context, context.deadline)
        self.driver._package(self.journal.identities["package_sha256"], context.deadline)
        lock = context.inputs["lock"]
        if self.recipe["flux_version"].removeprefix("v") != lock["platform"]["flux_version"].removeprefix(
            "v"
        ) or self.recipe["bigbang_version"].removeprefix("v") != lock["platform"][
            "bigbang_version"
        ].removeprefix("v"):
            raise StateError("Platform selection differs from the exact factory version lock")
        tools = [row for row in lock["tools"] if row["name"] == "helm"]
        if (
            len(tools) != 1
            or tools[0]["sha256"] != self.recipe["helm"]["sha256"]
            or context.inputs["config"]["tool_paths"].get("helm") != self.recipe["helm"]["path"]
        ):
            raise StateError("Platform Helm executable differs from the selected tool lock")
        namespaces = next(
            row["scope"]["namespaces"]
            for row in context.inputs["config"]["services"]
            if row["id"] == "kubernetes"
        )
        if not set(self.recipe["namespaces"]) <= set(namespaces):
            raise StateError("Platform namespaces exceed the operator Kubernetes scope")
        for name in ("flux_manifest", "chart", "values", "helm"):
            ref = self.recipe[name]
            raw = _read(
                _safe_path(Path(ref["path"])),
                128000000 if name == "helm" else 64000000 if name == "chart" else 8000000,
            )
            if _sha(raw) != ref["sha256"]:
                raise StateError("Selected platform material changed")
        return lock

    def prepare(self, context):
        lock = self._admit(context)
        if self._compiled is not None:
            self._audit_compiled()
            return deepcopy(self._compiled)
        root = self.journal.run_dir / ("platform-compile-" + uuid4().hex)
        root.mkdir(mode=0o700)
        chart = root / "chart"
        chart.mkdir(mode=0o700)
        raw = _read(Path(self.recipe["chart"]["path"]), 64000000)
        total = 0
        names = set()
        metadata = []
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > 5000:
                raise StateError("Platform chart expansion exceeds bound")
            for member in members:
                path = Path(member.name)
                if member.isdir():
                    continue
                if (
                    not member.isfile()
                    or path.is_absolute()
                    or ".." in path.parts
                    or len(path.parts) < 2
                    or member.name in names
                    or member.size > 64000000 - total
                ):
                    raise StateError("Platform chart archive contains an unsafe entry")
                names.add(member.name)
                total += member.size
                content = archive.extractfile(member).read(member.size + 1)
                if len(content) != member.size:
                    raise StateError("Platform chart archive changed")
                target = chart.joinpath(*path.parts[1:])
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                if path.parts[1:] == ("Chart.yaml",):
                    metadata.append(content)
        if len(metadata) != 1 or _sha(metadata[0]) != self.recipe["chart_metadata_sha256"]:
            raise StateError("Big Bang selected chart metadata differs from its independent pin")
        chart_info = strict_load_yaml(metadata[0].decode())[0]
        if chart_info.get("name") != "bigbang" or chart_info.get("version") != self.recipe[
            "bigbang_version"
        ].removeprefix("v"):
            raise StateError("Selected chart is not the locked Big Bang release")
        if inspect_chart(chart):
            raise StateError("Offline platform chart rendering forbids lookup")
        values = root / "values.yaml"
        values.write_bytes(_read(Path(self.recipe["values"]["path"]), 8000000))
        values.chmod(0o600)
        strict_load_yaml(values.read_text())
        helm = Path(self.recipe["helm"]["path"])
        identity = tool_identity(helm, ["version", "--short"], root, deadline=context.deadline)
        expected = next(row for row in lock["tools"] if row["name"] == "helm")
        if identity["sha256"] != expected["sha256"] or identity["version"].removeprefix("v") != expected[
            "version"
        ].removeprefix("v"):
            raise StateError("Actual Helm executable identity differs from lock")
        empty = root / "empty-kubeconfig"
        empty.write_text("")
        empty.chmod(0o600)
        code, out, _, receipt = run(
            [
                str(helm),
                "template",
                "bigbang",
                str(chart),
                "--namespace",
                "bigbang",
                "--include-crds",
                "--values",
                str(values),
                "--kube-version",
                lock["platform"]["kubernetes_version"],
            ],
            cwd=root,
            timeout=min(120, max(0.001, context.deadline - time.monotonic())),
            limit=8000000,
            env={
                "HELM_PLUGINS": str(root / "no-plugins"),
                "HELM_CONFIG_HOME": str(root / "helm-config"),
                "HELM_CACHE_HOME": str(root / "helm-cache"),
                "HELM_DATA_HOME": str(root / "helm-data"),
                "KUBECONFIG": str(empty),
            },
        )
        if code:
            raise StateError("Locked offline Big Bang render failed")
        flux_raw = _read(Path(self.recipe["flux_manifest"]["path"]), 8000000)
        groups = []
        for label, raw in (("flux", flux_raw), ("bigbang", out)):
            documents = [row for row in strict_load_yaml(raw.decode()) if row is not None]
            if not documents or len(documents) > 512:
                raise StateError("Platform rendered resource count is unsupported")
            for row in documents:
                if type(row) is not dict:
                    raise StateError("Platform manifest is not an object")
                _route(row)
                spec = row.get("spec", {})
                podspec = spec.get("template", {}).get("spec", {})
                for container in podspec.get("containers", []) + podspec.get("initContainers", []):
                    if container.get("image") not in self.recipe["images"]:
                        raise StateError("Platform workload image is absent from exact immutable selection")
                if row["kind"] == "GitRepository" and not (
                    re.fullmatch("[a-f0-9]{40}", spec.get("ref", {}).get("commit", ""))
                    or spec.get("url") == "owned://deployment"
                    and spec.get("ref") == {"commit": "owned-deployment-seed"}
                ):
                    raise StateError("Platform Git source requires an immutable commit")
                if row["kind"] == "ConfigMap":
                    row["immutable"] = True
            if "controller_cleanup" in self.recipe:
                from .controller_cleanup import compile_policy

                documents = [compile_policy(row, self.recipe["controller_cleanup"]) for row in documents]
            groups.append(tuple(documents))
        compiled = root / "compiled.json"
        from .platform_inputs import redact

        compiled.write_bytes(
            canonical(
                {
                    "flux": [redact(row) for row in groups[0]],
                    "bigbang": [redact(row) for row in groups[1]],
                    "helm": receipt,
                    "controller_cleanup": self.recipe.get("controller_cleanup"),
                }
            )
        )
        compiled.chmod(0o600)
        self._files = {compiled: _sha(compiled.read_bytes())}
        self._compiled = tuple(groups)
        self._compiled_digest = _sha(canonical(self._compiled))
        self._audit_compiled()
        self._admit(context)
        return deepcopy(self._compiled)

    def _audit_compiled(self):
        if self._compiled is not None and _sha(canonical(self._compiled)) != self._compiled_digest:
            raise StateError("Original in-memory platform compilation changed")
        for path, digest in self._files.items():
            if _sha(_read(path, 16000000)) != digest:
                raise StateError("Original platform compilation bytes changed")

    def bind_owned_inputs(self, context, *, bootstrap_authority, reader_origin, additional):
        from .platform_inputs import prepare_owned_inputs, require_owned_inputs, public_derivation

        if self._owned_inputs is not None:
            raise StateError("Owned platform input derivation is one-shot")
        self._owned_inputs = prepare_owned_inputs(
            self,
            context,
            bootstrap_authority=bootstrap_authority,
            reader_origin=reader_origin,
            groups=self.prepare(context),
            additional=additional,
        )
        record = require_owned_inputs(self._owned_inputs, context, platform=self)
        path = self.journal.run_dir / ("platform-owned-derivation-" + uuid4().hex + ".json")
        raw = canonical(public_derivation(self._owned_inputs, context, platform=self))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        self._files[path] = _sha(raw)
        return tuple(deepcopy(row) for group in record["groups"] for row in group)

    def private_secret_hashes(self, context):
        from .platform_inputs import require_owned_inputs

        if self._owned_inputs is None:
            return ()
        record = require_owned_inputs(self._owned_inputs, context, platform=self)
        return tuple(
            _sha(canonical(row))
            for group in record["groups"]
            for row in group
            if _route(row)[1] in record["private_paths"]
        )

    def manifests(self, context):
        if self._owned_inputs is not None:
            from .platform_inputs import require_owned_inputs

            groups = require_owned_inputs(self._owned_inputs, context, platform=self)["groups"]
        else:
            groups = self.prepare(context)
        return tuple(deepcopy(row) for group in groups for row in group)

    def install(
        self, context, resources, *, additional_manifests=(), expected_git_url, expected_git_revision
    ):
        self._admit(context)
        self._audit_compiled()
        if (
            type(resources) is not OwnedKubernetesResources
            or resources.journal is not self.journal
            or self._observation is not None
        ):
            raise StateError("Platform install requires original current owned Kubernetes resources")
        if context.phase["operation"] != "up":
            raise StateError("Platform install requires provision/up")
        self.journal.current_mutation(context.phase["id"], "up")
        private_paths, early_paths = set(), set()
        if self._owned_inputs is not None:
            from .platform_inputs import require_owned_inputs

            original = require_owned_inputs(self._owned_inputs, context, platform=self, resources=resources)
            if (expected_git_url, expected_git_revision) != (original["url"], original["seed_commit"]):
                raise StateError("Platform desired source differs from original owned derivation")
            groups, additional_manifests = original["groups"][:-1], original["groups"][-1]
            private_paths = original["private_paths"]
            early_paths = original["early_paths"]
        else:
            groups = self.prepare(context)
        observed = []
        rank = {
            "Namespace": 0,
            "CustomResourceDefinition": 1,
            "ServiceAccount": 2,
            "ClusterRole": 3,
            "Role": 3,
            "ClusterRoleBinding": 4,
            "RoleBinding": 4,
        }
        precreated = set()
        # Explicit owned namespaces precede credentials; credentials precede any
        # source/controller that needs the private repository. No namespace adoption.
        all_manifests = [row for group in (*groups, additional_manifests) for row in group]
        from .controller_cleanup import OwnedControllerInventory, POLICY

        if self.recipe.get("controller_cleanup") != POLICY:
            raise StateError("Canonical platform provisioning requires selected controller cleanup policy")
        inventory = OwnedControllerInventory(
            resources,
            context,
            tuple(all_manifests),
            policy=POLICY,
            platform=self if self._owned_inputs is not None else None,
        )
        namespace_bytes = {}
        for manifest in all_manifests:
            if manifest["kind"] != "Namespace":
                continue
            path = _route(manifest)[1]
            if path in namespace_bytes:
                if namespace_bytes[path] != canonical(manifest):
                    raise StateError("Selected namespace manifests collide")
                continue
            namespace_bytes[path] = canonical(manifest)
            observed.append(dict(resources.create(context, manifest)))
            precreated.add(path)
        for manifest in all_manifests:
            path = _route(manifest)[1]
            if path not in early_paths:
                continue
            if path in private_paths:
                identity = resources.create_platform_secret(
                    context,
                    inputs=self._owned_inputs,
                    descriptor={key: deepcopy(manifest[key]) for key in ("apiVersion", "kind", "metadata")},
                )
            else:
                identity = resources.create(context, manifest)
            observed.append(dict(identity))
            precreated.add(path)
        # These definitions are owned directly because Helm's selected cleanup
        # policy skips CRD installation. Establish them before any release can
        # create its custom resources; a failed definition stops provisioning.
        for manifest in additional_manifests:
            path = _route(manifest)[1]
            if manifest["kind"] != "CustomResourceDefinition" or path in precreated:
                continue
            observed.append(dict(resources.create(context, manifest)))
            precreated.add(path)
            self._wait(context, resources, manifest, "Established")
        for group_index, group in enumerate(groups):
            for manifest in sorted(group, key=lambda row: rank.get(row["kind"], 5)):
                if _route(manifest)[1] in precreated:
                    continue
                identity = resources.create(context, manifest)
                observed.append(dict(identity))
                if manifest["kind"] == "CustomResourceDefinition":
                    self._wait(context, resources, manifest, "Established")
            if group_index == 0:
                for manifest in additional_manifests:
                    if manifest["kind"] in (
                        "GitRepository",
                        "Kustomization",
                        "HelmRepository",
                        "HelmRelease",
                    ):
                        path = _route(manifest)[1]
                        if path not in precreated:
                            observed.append(dict(resources.create(context, manifest)))
                            precreated.add(path)
                for selected in self.recipe["readiness"]["flux_deployments"]:
                    self._wait(
                        context,
                        resources,
                        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": selected},
                        "Available",
                    )
        for selected in self.recipe["readiness"]["helm_releases"]:
            self._wait(
                context,
                resources,
                {"apiVersion": "helm.toolkit.fluxcd.io/v2", "kind": "HelmRelease", "metadata": selected},
                "Ready",
            )
        for manifest in sorted(additional_manifests, key=lambda row: rank.get(row["kind"], 5)):
            if _route(manifest)[1] in precreated:
                continue
            identity = resources.create(context, manifest)
            observed.append(dict(identity))
        self._git_selection = (expected_git_url, expected_git_revision)
        argo = self.recipe["readiness"]["argo_application"]
        application = self._wait(
            context,
            resources,
            {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "metadata": argo},
            "ArgoAdmitted",
        )
        inventory.capture(context, defer_unready_application=True)
        self._observation = {
            "kind": "owned-platform-bootstrap",
            "controller_cleanup_policy": POLICY,
            "run_id": context.run_id,
            "identities": dict(self.journal.identities),
            "cluster_uid": resources.cluster_uid,
            "origin": resources.session.origin,
            "resources": observed,
            "versions": {name: self.recipe[name] for name in ("flux_version", "bigbang_version")},
            "argo_destination": deepcopy(application["spec"]["destination"]),
            "argo_application_identity": {
                key: application["metadata"][key] for key in ("namespace", "name", "uid")
            },
            "argo_revision": application.get("status", {}).get("sync", {}).get("revision"),
            "workload_readiness": "pending-first-authenticated-delivery",
            "argo_destination_namespace_uid": self._argo_namespace_uid,
            "readiness": "controllers-and-releases-independently-observed",
            "f0_control_path": "not-observed",
            "fresh_cluster_qualification": False,
        }
        return deepcopy(self._observation)

    def _wait(self, context, resources, manifest, condition):
        _, path = _route(manifest)
        for _ in range(120):
            value = resources.read_owned(context, manifest)
            if value:
                meta, status = value.get("metadata", {}), value.get("status", {})
                if condition in ("ArgoHealthy", "ArgoAdmitted"):
                    source = value.get("spec", {}).get("source", {})
                    destination = value.get("spec", {}).get("destination", {})
                    selected_url, selected_revision = self._git_selection
                    namespace = destination.get("namespace")
                    if namespace not in resources.namespaces:
                        raise StateError("Argo destination differs from the selected owned namespace scope")
                    observed_namespace = resources.read_owned(
                        context, {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}
                    )
                    self._argo_namespace_uid = observed_namespace["metadata"]["uid"]
                    ready = (
                        source.get("repoURL") == selected_url
                        and source.get("targetRevision") == "main"
                        and isinstance(source.get("path"), str)
                        and source["path"] not in ("", ".")
                        and destination.get("namespace") in resources.namespaces
                        and destination.get("server")
                        in (resources.session.origin, "https://kubernetes.default.svc")
                        and (
                            condition == "ArgoAdmitted"
                            or (
                                status.get("sync", {}).get("status") == "Synced"
                                and status.get("sync", {}).get("revision") == selected_revision
                                and status.get("health", {}).get("status") == "Healthy"
                            )
                        )
                    )
                else:
                    ready = any(
                        row.get("type") == condition
                        and row.get("status") == "True"
                        and (
                            condition == "Established"
                            or row.get("observedGeneration", status.get("observedGeneration"))
                            == meta.get("generation")
                        )
                        for row in status.get("conditions", [])
                    )
                if ready:
                    return value
            if time.monotonic() + 2 >= context.deadline:
                break
            time.sleep(2)
        raise StateError("Selected platform component readiness was not independently observed")

    def evidence_artifacts(self, context):
        if self._pid != os.getpid() or context.run_dir != self.journal.run_dir:
            raise StateError("Platform evidence requires its original run")
        self._audit_compiled()
        return tuple(self._files)

    phase_artifacts = evidence_artifacts

    def observation(self, context):
        self._admit(context)
        self._audit_compiled()
        if self._observation is None:
            raise StateError("Platform readiness has not been observed")
        return deepcopy(self._observation)
