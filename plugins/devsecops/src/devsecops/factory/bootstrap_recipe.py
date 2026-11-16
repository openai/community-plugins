"""Closed selection of fixed owned GitLab/registry bootstrap inputs.

Pins select bytes and destinations; they are not readiness or live resource IDs.
"""

from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def shape(value, fields):
    if type(value) is not dict or set(value) != set(fields.split()):
        raise ValueError("Unsupported closed owned bootstrap fields")


def match(value, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError("Unsupported owned bootstrap identity")


def pin(value):
    shape(value, "path sha256")
    match(value["path"], r"/[^\x00-\x20]{1,4000}")
    if ".." in Path(value["path"]).parts:
        raise ValueError("Owned bootstrap paths must be canonical")
    match(value["sha256"], "[a-f0-9]{64}")


def runtime_reader_selection(value):
    shape(value, "role serviceaccount bindings audience duration_seconds")
    if value["role"] not in ("runtime", "denied"):
        raise ValueError("Unsupported runtime reader role")
    match(value["audience"], r"https://[A-Za-z0-9.-]+(?::[1-9][0-9]{0,4})?(?:/[A-Za-z0-9_./-]*)?")
    if type(value["duration_seconds"]) is not int or not 600 <= value["duration_seconds"] <= 14400:
        raise ValueError("Reader duration must be explicitly bounded between ten minutes and four hours")
    descriptor = value["serviceaccount"]
    shape(descriptor, "apiVersion kind metadata")
    shape(descriptor["metadata"], "namespace name")
    if descriptor["apiVersion"] != "v1" or descriptor["kind"] != "ServiceAccount":
        raise ValueError("Reader must select an owned ServiceAccount")
    for item in descriptor["metadata"].values():
        match(item, r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
    if type(value["bindings"]) is not list or len(value["bindings"]) > 32:
        raise ValueError("Reader bindings must be bounded")
    if (value["role"] == "denied" and value["bindings"]) or (
        value["role"] == "runtime" and not value["bindings"]
    ):
        raise ValueError("Reader role and selected grants differ")
    for binding in value["bindings"]:
        shape(binding, "apiVersion kind metadata")
        if binding["apiVersion"] != "rbac.authorization.k8s.io/v1" or binding["kind"] not in (
            "RoleBinding",
            "ClusterRoleBinding",
        ):
            raise ValueError("Reader binding kind is unsupported")
        shape(binding["metadata"], "namespace name" if binding["kind"] == "RoleBinding" else "name")
        for item in binding["metadata"].values():
            match(item, r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
    return deepcopy(value)


def validate(value):
    shape(value, "schema_version kind environment registry services seeds kubernetes platform writer_policy")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] not in (1, 2)
        or value["kind"] != "owned-bootstrap-v" + str(value["schema_version"])
    ):
        raise ValueError("Unsupported owned bootstrap recipe")
    match(value["environment"], r"[a-z][a-z0-9-]{0,31}")
    pin(value["registry"])
    pin(value["writer_policy"])
    services = value["services"]
    runner_vm = value["schema_version"] == 2
    shape(
        services,
        "origin version remote_directory private_state_directory transport materials credentials server_image "
        + ("runner_vm" if runner_vm else "runner_image")
        + " runner_environment runner_materials runner_secret_environment runner_validation",
    )
    if (
        services["version"] != "19.5.3"
        or services["origin"] != "https://gitlab." + value["environment"] + ".test:8444"
    ):
        raise ValueError("Owned GitLab must use its exact locked private origin/version")
    match(
        services["remote_directory"],
        r"/home/[A-Za-z0-9_-]+/\.local/share/devsecops/services/" + value["environment"],
    )
    match(services["private_state_directory"], r"/[^\x00-\x20,]{1,4000}")
    images = [services["server_image"]]
    if runner_vm:
        pin(services["runner_vm"])
    else:
        images.append(services["runner_image"])
    for image in images:
        shape(image, "reference image_id")
        match(image["reference"], r"[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}")
        match(image["image_id"], r"sha256:[a-f0-9]{64}")
    if not services["server_image"]["reference"].startswith("docker.io/gitlab/gitlab-ce@sha256:"):
        raise ValueError("Owned server must use the selected GitLab CE image")
    transport = services["transport"]
    shape(
        transport,
        "config known_hosts certificate identity proxy identity_agent host_key_sha256 verification_basis agent_protocol",
    )
    if transport["verification_basis"] not in (
        "operator-verified",
        "authenticated-control-plane",
    ) or transport["agent_protocol"] not in ("native", "certificate-view-v1"):
        raise ValueError("Owned services require independently verified SSH destination")
    for name in ("config", "known_hosts", "certificate", "identity", "proxy"):
        pin(transport[name])
    match(transport["identity_agent"], r"/[^\x00-\x20]{1,4000}")
    match(transport["host_key_sha256"], "[a-f0-9]{64}")
    shape(services["materials"], "ca certificate private_key")
    for ref in services["materials"].values():
        pin(ref)
    shape(services["credentials"], "root_password_env operator_token_env")
    for ref in services["credentials"].values():
        match(ref, "[A-Z][A-Z0-9_]{0,127}")
    if len(set(services["credentials"].values())) != 2:
        raise ValueError("GitLab root and API credential references must differ")
    # Host and runner can use different operating systems. Explicit immutable
    # runner validation tools are selected independently; host binary hashes
    # must never be relabeled as Linux executables during staging.
    shape(services["runner_validation"], "helm kubeconform schemas_path")
    for name in ("helm", "kubeconform"):
        pin(services["runner_validation"][name])
    for path in [services["runner_validation"][name]["path"] for name in ("helm", "kubeconform")] + [
        services["runner_validation"]["schemas_path"]
    ]:
        match(path, r"/opt/devsecops/[A-Za-z0-9_./-]+")
        if ".." in Path(path).parts:
            raise ValueError("Runner validation paths must select immutable image material")
    # Runner receives selected invocation bindings only, never arbitrary environment
    # variables that could change shell/interpreter or inject loader options.
    shape(
        services["runner_environment"],
        "DEVSECOPS_GIT_INVOCATION DEVSECOPS_GIT_INVOCATION_SHA256 DEVSECOPS_PYTHON DEVSECOPS_PLUGIN_ROOT",
    )
    for name in ("DEVSECOPS_PYTHON", "DEVSECOPS_PLUGIN_ROOT"):
        match(services["runner_environment"][name], r"/opt/devsecops/[A-Za-z0-9_./-]+")
        if ".." in Path(services["runner_environment"][name]).parts:
            raise ValueError("Runner image paths must be canonical")
    match(
        services["runner_environment"]["DEVSECOPS_GIT_INVOCATION"], r"/opt/devsecops/[A-Za-z0-9_./-]+\.json"
    )
    match(services["runner_environment"]["DEVSECOPS_GIT_INVOCATION_SHA256"], "[a-f0-9]{64}")
    if runner_vm and not re.fullmatch(
        r"/opt/devsecops/runner-inputs/[A-Za-z0-9_.-]+\.json",
        services["runner_environment"]["DEVSECOPS_GIT_INVOCATION"],
    ):
        raise ValueError("Runner VM requires a logical invocation selector under the fixed inputs prefix")
    if type(services["runner_materials"]) is not list or len(services["runner_materials"]) > 64:
        raise ValueError("Runner material set is unsupported")
    targets = set()
    sources = set()
    for item in services["runner_materials"]:
        shape(item, "target file private")
        pin(item["file"])
        match(item["target"], r"/opt/devsecops/runner-inputs/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,3}")
        if (
            ".." in Path(item["target"]).parts
            or item["target"] in targets
            or item["file"]["path"] in sources
            or type(item["private"]) is not bool
        ):
            raise ValueError("Runner material target or source collides")
        targets.add(item["target"])
        sources.add(item["file"]["path"])
    names = services["runner_secret_environment"]
    if type(names) is not list or len(names) > 32 or len(set(names)) != len(names):
        raise ValueError("Runner secret references are unsupported")
    for name in names:
        match(name, r"DEVSECOPS_[A-Z0-9_]{1,100}")
        if name in services["runner_environment"]:
            raise ValueError("Runner private environment cannot replace execution selectors")
    shape(value["seeds"], "source deployment")
    for files in value["seeds"].values():
        if type(files) is not list or not 1 <= len(files) <= 16:
            raise ValueError("Owned projects require bounded selected seed files")
        names = set()
        for item in files:
            shape(item, "repository_path file")
            pin(item["file"])
            match(item["repository_path"], r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,3}")
            if (
                any(n in (".", "..", ".git") for n in item["repository_path"].split("/"))
                or item["repository_path"] in names
            ):
                raise ValueError("Owned project seed paths collide")
            names.add(item["repository_path"])
        if "README.md" not in names:
            raise ValueError("Owned project seeds require README.md")
    shape(
        value["kubernetes"],
        "manifests runtime_readers" if "runtime_readers" in value["kubernetes"] else "manifests",
    )
    readers = value["kubernetes"].get("runtime_readers", [])
    if type(readers) is not list or len(readers) > 2:
        raise ValueError("Runtime readers must be a bounded explicit selection")
    roles, accounts = set(), set()
    for reader in readers:
        runtime_reader_selection(reader)
        account = (
            reader["serviceaccount"]["metadata"]["namespace"],
            reader["serviceaccount"]["metadata"]["name"],
        )
        if reader["role"] in roles or account in accounts:
            raise ValueError("Runtime and denied readers require distinct selected accounts")
        roles.add(reader["role"])
        accounts.add(account)
    manifests = value["kubernetes"]["manifests"]
    if type(manifests) is not list or len(manifests) > 32:
        raise ValueError("Owned Kubernetes manifests exceed the bounded set")
    for ref in manifests:
        pin(ref)
    if __package__:
        from .platform_bootstrap import validate as validate_platform

        validate_platform(value["platform"])
    elif type(value["platform"]) is not dict:
        raise ValueError("Owned platform selection is missing")
    return deepcopy(value)


def binding(recipe, *, run_id, identities, package_modules):
    validate(recipe)
    return hashlib.sha256(
        canonical(
            {"recipe": recipe, "run_id": run_id, "identities": identities, "package_modules": package_modules}
        )
    ).hexdigest()
