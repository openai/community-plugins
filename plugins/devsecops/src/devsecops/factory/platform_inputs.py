"""Fixed platform template resolution from original owned GitLab identities.

Selected templates contain public sentinel values. Actual repository secrets stay
in the resident capability and API request; durable derivation evidence has only
resource identities, approved template hashes and derived content hashes.
"""

from copy import deepcopy
import base64
import os
from pathlib import Path
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

from .bootstrap_recipe import canonical
from .state import StateError, _sha, _read

URL = "owned://deployment"
COMMIT = "owned-deployment-seed"
USERNAME = "owned-reader-username"
PASSWORD = "owned-reader-token"
CA = "owned-gitlab-ca"
HOST = "owned-gitlab-host"
_MARKERS = {URL, COMMIT, USERNAME, PASSWORD, CA, HOST}
_INPUTS = WeakKeyDictionary()


class OwnedPlatformInputs:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Owned platform inputs require original bootstrap derivation")

    def __reduce__(self):
        raise TypeError("Private platform inputs cannot be restored from evidence")


def redact(document):
    value = deepcopy(document)
    if value.get("kind") == "Secret":
        for field in ("data", "stringData"):
            if field in value:
                value[field] = {"redacted_content_sha256": _sha(canonical(value[field]))}
    return value


def _markers(value):
    if type(value) is dict:
        return any(_markers(child) for child in value.values())
    if type(value) in (list, tuple):
        return any(_markers(child) for child in value)
    return isinstance(value, str) and value in _MARKERS


def _derive(document, *, url, commit, reader, secret, ca):
    result = deepcopy(document)
    kind, spec = result.get("kind"), result.get("spec", {})
    private = False
    if kind == "GitRepository" and spec.get("url") == URL:
        if spec.get("ref") != {"commit": COMMIT}:
            raise StateError("Owned Flux source template must select only the actual owned seed commit")
        spec["url"], spec["ref"] = url, {"commit": commit}
    elif kind == "Application" and spec.get("source", {}).get("repoURL") == URL:
        source = spec["source"]
        if source.get("targetRevision") != "main" or "sources" in spec:
            raise StateError("Owned Argo template must watch the owned deployment main branch")
        source["repoURL"] = url
    elif kind == "Secret" and _markers(result):
        values = result.get("stringData", {})
        argo = result.get("metadata", {}).get("labels", {}).get("argocd.argoproj.io/secret-type")
        expected = {"username": USERNAME, "password": PASSWORD}
        if argo is None:
            expected["ca.crt"] = CA
        if argo is not None:
            if argo != "repository":
                raise StateError("Owned Argo credentials must select one repository secret")
            expected.update(type="git", url=URL)
        if values != expected or "data" in result or result.get("type") != "Opaque":
            raise StateError("Owned repository secret differs from the closed reader template")
        values = {"username": reader["username"], "password": secret}
        if argo is None:
            values["ca.crt"] = ca
        if argo is not None:
            values.update(type="git", url=url)
        result.pop("stringData")
        result["data"] = {key: base64.b64encode(value.encode()).decode() for key, value in values.items()}
        private = True
    elif kind == "ConfigMap" and _markers(result):
        metadata = result.get("metadata", {})
        if (
            metadata.get("name") != "argocd-tls-certs-cm"
            or metadata.get("labels", {}).get("app.kubernetes.io/part-of") != "argocd"
            or result.get("data") != {HOST: CA}
            or result.get("immutable") is not True
        ):
            raise StateError("Owned Argo TLS trust must use the exact selected immutable ConfigMap template")
        result["data"] = {urlsplit(url).hostname: ca}
    if _markers(result):
        raise StateError("Unresolved owned platform marker outside its fixed template field")
    return result, private


def prepare_owned_inputs(platform, context, *, bootstrap_authority, reader_origin, groups, additional):
    from .canonical_bootstrap import require_bootstrap_authority
    from .owned_reader_identity import require_owned_reader_origin, _reader_secret
    from .platform_bootstrap import OwnedPlatformBootstrap

    if type(platform) is not OwnedPlatformBootstrap or bootstrap_authority._owner.platform is not platform:
        raise StateError("Platform derivation requires the original same-run producer")
    platform._admit(context)
    if context.phase["operation"] != "up":
        raise StateError("Owned platform derivation requires provisioning")
    operation = platform.journal.current_mutation(context.phase["id"], "up")
    facts = require_bootstrap_authority(bootstrap_authority, platform.journal)
    scope = dict(journal=platform.journal, bootstrap_authority=bootstrap_authority)
    reader = require_owned_reader_origin(reader_origin, **scope)
    deployment = facts["projects"]["deployment"]
    if (
        reader["project_id"] != deployment["id"]
        or reader["path_with_namespace"] != deployment["path_with_namespace"]
    ):
        raise StateError("Platform reader differs from original deployment project")
    url = facts["origin"] + "/" + deployment["path_with_namespace"] + ".git"
    token = _reader_secret(reader_origin, **scope)
    ca_ref = bootstrap_authority._owner._recipe()["services"]["materials"]["ca"]
    ca_raw = _read(Path(ca_ref["path"]), 1000000)
    if _sha(ca_raw) != ca_ref["sha256"]:
        raise StateError("Owned GitLab public CA differs from selected platform trust")
    ca = ca_raw.decode("utf-8")
    bundle, private_paths, early_paths, derivations = [], set(), set(), []
    from .kubernetes_operations import _route

    for group in (*groups, additional):
        derived = []
        for source in group:
            target, private = _derive(
                source, url=url, commit=deployment["seed_commit"], reader=reader, secret=token, ca=ca
            )
            if "controller_cleanup" in platform.recipe:
                from .controller_cleanup import compile_policy

                target = compile_policy(target, platform.recipe["controller_cleanup"])
            _, path = _route(target)
            if private:
                private_paths.add(path)
            if private or target["kind"] == "ConfigMap" and source.get("data") == {HOST: CA}:
                early_paths.add(path)
            derivations.append(
                {
                    "path": path,
                    "template_sha256": _sha(canonical(source)),
                    "derived_sha256": _sha(canonical(target)),
                    "reader_secret": private,
                }
            )
            derived.append(target)
        bundle.append(tuple(derived))
    values = tuple(bundle)
    documents = [row for group in values for row in group]
    by_identity = {}
    for row in documents:
        path = _route(row)[1]
        if path in by_identity and (
            row["kind"] != "Namespace" or canonical(row) != canonical(by_identity[path])
        ):
            raise StateError("Selected platform resource manifests collide")
        by_identity[path] = row
    for row in documents:
        if row["kind"] == "GitRepository" and row.get("spec", {}).get("url") == url:
            spec = row["spec"]
            ref = spec.get("secretRef", {})
            matches = [
                secret
                for secret in documents
                if secret["kind"] == "Secret"
                and secret["metadata"].get("namespace") == row["metadata"]["namespace"]
                and secret["metadata"]["name"] == ref.get("name")
                and _route(secret)[1] in private_paths
                and "ca.crt" in secret.get("data", {})
            ]
            if set(ref) != {"name"} or len(matches) != 1 or spec.get("proxySecretRef"):
                raise StateError(
                    "Owned Flux source must use its exact original read-only repository credential"
                )
        if row["kind"] == "Application" and row.get("spec", {}).get("source", {}).get("repoURL") == url:
            namespace = row["metadata"]["namespace"]
            matches = [
                secret
                for secret in documents
                if secret["kind"] == "Secret"
                and secret["metadata"].get("namespace") == namespace
                and _route(secret)[1] in private_paths
                and secret["metadata"].get("labels", {}).get("argocd.argoproj.io/secret-type") == "repository"
            ]
            trust = [
                config
                for config in documents
                if config["kind"] == "ConfigMap"
                and config["metadata"].get("namespace") == namespace
                and config["metadata"]["name"] == "argocd-tls-certs-cm"
                and _route(config)[1] in early_paths
            ]
            if len(matches) != 1 or len(trust) != 1:
                raise StateError(
                    "Owned Argo application requires its exact reader secret and hostname CA trust"
                )
    namespaces = {row["metadata"]["name"] for group in values for row in group if row["kind"] == "Namespace"}
    if any(
        row["metadata"]["namespace"] not in namespaces
        for group in values
        for row in group
        if _route(row)[1] in private_paths
    ):
        raise StateError("Owned repository secrets require explicitly created namespace manifests")
    value = object.__new__(OwnedPlatformInputs)
    _INPUTS[value] = {
        "platform": platform,
        "authority": bootstrap_authority,
        "reader": reader_origin,
        "pid": os.getpid(),
        "operation": operation,
        "groups": values,
        "digest": _sha(canonical(values)),
        "private_paths": private_paths,
        "early_paths": early_paths,
        "ca_ref": deepcopy(ca_ref),
        "derivations": derivations,
        "project_id": deployment["id"],
        "seed_commit": deployment["seed_commit"],
        "url": url,
        "reader_user_id": reader["user_id"],
        "reader_token_id": reader["token_id"],
    }
    return value


def require_owned_inputs(value, context, *, platform=None, resources=None):
    record = _INPUTS.get(value) if type(value) is OwnedPlatformInputs else None
    if (
        record is None
        or record["pid"] != os.getpid()
        or platform is not None
        and record["platform"] is not platform
    ):
        raise StateError("Original private platform input capability is required")
    owner = record["platform"]
    owner._admit(context)
    from .canonical_bootstrap import require_bootstrap_authority
    from .owned_reader_identity import require_owned_reader_origin, _reader_secret

    facts = require_bootstrap_authority(record["authority"], owner.journal)
    if resources is not None and (
        resources.journal is not owner.journal or record["authority"]._owner._kubernetes is not resources
    ):
        raise StateError("Private platform inputs differ from original Kubernetes owner")
    scope = dict(journal=owner.journal, bootstrap_authority=record["authority"])
    reader = require_owned_reader_origin(record["reader"], **scope)
    if _sha(_read(Path(record["ca_ref"]["path"]), 1000000)) != record["ca_ref"]["sha256"]:
        raise StateError("Original platform public CA changed")
    _reader_secret(record["reader"], **scope)
    if (
        facts["projects"]["deployment"]["id"] != record["project_id"]
        or facts["projects"]["deployment"]["seed_commit"] != record["seed_commit"]
        or reader["user_id"] != record["reader_user_id"]
        or reader["token_id"] != record["reader_token_id"]
        or _sha(canonical(record["groups"])) != record["digest"]
    ):
        raise StateError("Original platform derivation identity changed")
    return record


def public_derivation(value, context, *, platform):
    record = require_owned_inputs(value, context, platform=platform)
    return {
        key: deepcopy(record[key])
        for key in ("project_id", "seed_commit", "url", "reader_user_id", "reader_token_id", "derivations")
    }
