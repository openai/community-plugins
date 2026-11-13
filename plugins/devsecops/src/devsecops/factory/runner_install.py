"""Direct, bounded runner-input compilation from the current approved templates."""

from copy import deepcopy
import os
import uuid
from pathlib import Path
from weakref import WeakKeyDictionary
from .bootstrap_recipe import canonical
from .mission_git_invocation import invocation_configuration
from .state import StateError, _json, _read, _safe_path, _sha

_PLANS = WeakKeyDictionary()
_NAMES = ("build", "staging", "security", "signing", "publication", "proposal")
_ROOT = "/opt/devsecops/runner-inputs/"


class RunnerInstallPlan:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Runner installation requires direct current template compilation")

    def __reduce__(self):
        raise TypeError("Runner installation plan cannot be serialized")


def prepare_runner_install(stack, context):
    from .owned_services import OwnedServiceStack

    if type(stack) is not OwnedServiceStack:
        raise StateError("Fixed owned service stack required")
    stack.driver._admit(context, context.deadline)
    raw_recipe = _read(_safe_path(stack.recipe_path), 256000)
    if _sha(raw_recipe) != stack.recipe_sha256:
        raise StateError("Selected runner recipe changed")
    from .bootstrap_recipe import validate

    recipe = validate(_json(raw_recipe))
    vm_recipe = None
    input_root = _ROOT
    extra_refs = {str(stack.recipe_path): (stack.recipe_sha256, 256000)}
    guest = None
    if recipe["schema_version"] == 2:
        from . import runner_vm_recipe
        from .owned_linux import _plain

        vm_ref = recipe["services"]["runner_vm"]
        raw_vm = _read(_safe_path(Path(vm_ref["path"])), 64000)
        if _sha(raw_vm) != vm_ref["sha256"]:
            raise StateError("Selected runner VM recipe changed")
        vm_recipe = runner_vm_recipe.validate_against_foundation(
            _json(raw_vm), _plain(context.inputs["config"]["runtime"]["foundation"])
        )
        extra_refs[vm_ref["path"]] = (vm_ref["sha256"], 64000)
        download_ref = vm_recipe["egress"].get("blob_download_policy")
        if download_ref is not None:
            download_raw = _read(_safe_path(Path(download_ref["path"])), 16384)
            runner_vm_recipe.blob_policy_document(download_raw, download_ref["sha256"])
            extra_refs[download_ref["path"]] = (download_ref["sha256"], 16384)
        guest = runner_vm_recipe.guest_paths(context.run_id)
        input_root = guest["inputs"] + "/"

    def input_target(value):
        if not value.startswith(_ROOT):
            raise StateError("Runner input requires its original logical target prefix")
        return input_root + value.removeprefix(_ROOT)

    source = stack.driver.auxiliary_sources.get("mission_git_invocation")
    if type(source) is not tuple:
        raise StateError("Owned runner requires the original pinned mission invocation")
    raw = _read(_safe_path(source[0]), 64000)
    if _sha(raw) != source[1]:
        raise StateError("Original runner invocation changed")
    selected = invocation_configuration(raw)
    if selected["run_scope"] != stack.journal.identities:
        raise StateError("Runner templates differ from current run")
    if vm_recipe and (
        selected["plugin_id"] != stack.driver.plugin_id
        or selected["plugin_version"] != stack.driver.plugin_version
    ):
        raise StateError("Runner VM must use the actual current installed package identity")
    refs = {**extra_refs, str(source[0]): (source[1], 64000)}
    entries = {}
    mapping = {}
    for material in recipe["services"]["runner_materials"]:
        ref = material["file"]
        path = _safe_path(Path(ref["path"]))
        data = _read(path, 1000000)
        if _sha(data) != ref["sha256"] or material["private"] and path.stat().st_mode & 0o077:
            raise StateError("Runner selected material differs or lacks private permissions")
        refs[str(path)] = (ref["sha256"], 1000000)
        target = input_target(material["target"])
        mapping[str(path)] = target
        entries[target] = {"content": data, "private": material["private"]}

    registry_ref = recipe["registry"]
    registry_raw = _read(_safe_path(Path(registry_ref["path"])), 64000)
    if _sha(registry_raw) != registry_ref["sha256"]:
        raise StateError("Runner registry trust selection changed")
    from .service_recipe import validate as registry_configuration

    registry = registry_configuration(_json(registry_raw))
    refs[registry_ref["path"]] = (registry_ref["sha256"], 64000)
    trust = {}
    trust_refs = [
        ("gitlab", recipe["services"]["materials"]["ca"]),
        ("registry", registry["materials"]["ca"]),
    ]
    if vm_recipe and vm_recipe["egress"]["external_ca_bundle"] is not None:
        trust_refs.append(("external", vm_recipe["egress"]["external_ca_bundle"]))
    for service, ref in trust_refs:
        path = _safe_path(Path(ref["path"]))
        data = _read(path, 1000000)
        if _sha(data) != ref["sha256"]:
            raise StateError("Runner owned service public CA differs from its selected pin")
        target = input_root + "service-trust/" + service + "-ca.pem"
        if target in entries or (
            str(path) in mapping
            and mapping[str(path)] != target
            and mapping[str(path)] not in {row["path"] for row in trust.values()}
        ):
            raise StateError("Runner service trust conflicts with an existing material mapping")
        refs[str(path)] = (ref["sha256"], 1000000)
        mapping.setdefault(str(path), target)
        entries[target] = {"content": data, "private": False}
        trust[service] = {"path": target, "sha256": ref["sha256"]}

    def mapped(value):
        if type(value) is dict:
            return {key: mapped(child) for key, child in value.items()}
        if type(value) is list:
            return [mapped(child) for child in value]
        return mapping.get(value, value) if type(value) is str else value

    from .mission_operation import _configuration as build
    from .mission_staging import _configuration as staging
    from .mission_security_operation import _configuration as security
    from .mission_signing import signing_configuration as signing
    from .mission_publication import publication_configuration as publication

    from .deployment_proposal import proposal_configuration as proposal

    parsers = dict(zip(_NAMES, (build, staging, security, signing, publication, proposal)))
    digests, original = {}, {}
    for name in _NAMES:
        ref = selected["configurations"][name]
        data = _read(_safe_path(Path(ref["path"])), 64000)
        if _sha(data) != ref["sha256"]:
            raise StateError("Runner original component template changed")
        refs[ref["path"]] = (ref["sha256"], 64000)
        original[name] = ref["sha256"]
        original_value = parsers[name](data)
        if vm_recipe and name == "build":
            external = vm_recipe["egress"]["external_ca_bundle"]
            if (
                "https://registry1.dso.mil" not in vm_recipe["egress"]["external_origins"]
                or external is None
                or original_value.get("registry_auth", {}).get("ca_bundle") != external["path"]
            ):
                raise StateError(
                    "Runner VM build requires explicit Registry1 selection and its exact pinned external trust"
                )
            download_ref = vm_recipe["egress"].get("blob_download_policy")
            if original_value.get("blob_download_policy") != download_ref:
                raise StateError("Runner VM route and build blob policies must be the same pinned selection")
            if download_ref is not None and (
                download_ref["path"] not in mapping
                or _sha(entries[mapping[download_ref["path"]]]["content"]) != download_ref["sha256"]
            ):
                raise StateError("Runner blob policy must be an explicitly staged immutable material")
        if name == "build" and "attestation_size_policy" in original_value:
            from devsecops.adapters.registry_attestation_policy import parse_attestation_size_policy
            from devsecops.adapters.image_policy import _policy as image_policy

            size_ref = original_value["attestation_size_policy"]
            image_ref = original_value["image"]
            if size_ref["path"] not in mapping or image_ref["policy"] not in mapping:
                raise StateError("Runner attestation and image policies must be explicitly staged materials")
            size_raw = entries[mapping[size_ref["path"]]]["content"]
            image_raw = entries[mapping[image_ref["policy"]]]["content"]
            if _sha(size_raw) != size_ref["sha256"] or _sha(image_raw) != image_ref["policy_sha256"]:
                raise StateError("Runner attestation or image policy differs from its original pin")
            try:
                subject = image_policy(image_raw)["subject"]
                parse_attestation_size_policy(size_raw, size_ref["sha256"], origin=subject["origin"],
                    repository=subject["repository"], subject_digest=subject["digest"])
            except ValueError:
                raise StateError("Runner attestation policy does not bind the exact selected image") from None
        value = mapped(original_value)
        if name == "build":
            value["owner_uid"] = 10001
        elif name == "proposal":
            validation = recipe["services"]["runner_validation"]
            for tool in ("helm", "kubeconform"):
                value[tool + "_path"] = validation[tool]["path"]
                value[tool + "_sha256"] = validation[tool]["sha256"]
            value["schemas_path"] = validation["schemas_path"]
        encoded = canonical(value)
        parsers[name](encoded)
        target = input_root + name + ".json"
        digests[name] = _sha(encoded)
        if target in entries:
            raise StateError("Runner material collides with a derived configuration")
        entries[target] = {"content": encoded, "private": False}
        selected["configurations"][name] = {"path": target, "sha256": digests[name]}
    selected["output"] = "/home/gitlab-runner/builds/factory-" + context.run_id + "/publication"
    # Keep all 128 run-identity bits while fitting the real build admission's
    # 60-byte runtime-root limit and the existing runner-builds volume.
    run_identity = uuid.UUID(context.run_id)
    if str(run_identity) != context.run_id:
        raise StateError("Runner requires the exact canonical run UUID")
    selected["runtime_root"] = "/home/gitlab-runner/builds/" + run_identity.hex
    target = recipe["services"]["runner_environment"]["DEVSECOPS_GIT_INVOCATION"]
    if vm_recipe:
        target = input_target(target)
    if target in entries:
        raise StateError("Runner material collides with its derived invocation")
    encoded = canonical(selected)
    invocation_configuration(encoded)
    entries[target] = {"content": encoded, "private": False}
    environment = deepcopy(recipe["services"]["runner_environment"])
    environment["DEVSECOPS_GIT_INVOCATION"] = target
    environment["DEVSECOPS_GIT_INVOCATION_SHA256"] = _sha(encoded)
    secrets = {}
    for name in recipe["services"]["runner_secret_environment"]:
        value = os.environ.get(name)
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 8192
            or any(ord(c) < 32 or ord(c) > 126 for c in value)
        ):
            raise StateError("Selected runner private environment is unavailable")
        secrets[name] = value
    if sum(len(row["content"]) for row in entries.values()) > 1500000:
        raise StateError(
            "Runner inline input bundle exceeds bound; large locked assets belong in the selected runner image"
        )
    plan = object.__new__(RunnerInstallPlan)
    _PLANS[plan] = {
        "stack": stack,
        "pid": os.getpid(),
        "run_id": context.run_id,
        "refs": refs,
        "entries": entries,
        "environment": environment,
        "secrets": secrets,
        "config_sha256": digests,
        "original_template_sha256": original,
        "service_trust": trust,
        "runner_profile": "owned-runner-vm-v1" if vm_recipe else "legacy-container-runner-v1",
        "guest_paths": guest,
        "guest_package": None
        if vm_recipe is None
        else {
            "root": environment["DEVSECOPS_PLUGIN_ROOT"],
            "manifest_sha256": stack.journal.identities["package_sha256"],
            "plugin_id": selected["plugin_id"],
            "plugin_version": selected["plugin_version"],
        },
    }
    return plan


def _original_runner_install(plan, stack, context):
    record = _PLANS.get(plan) if type(plan) is RunnerInstallPlan else None
    if (
        record is None
        or record["stack"] is not stack
        or record["pid"] != os.getpid()
        or record["run_id"] != context.run_id
    ):
        raise StateError("Original same-process runner installation plan required")
    return record


def inspect_runner_install(plan, stack, context):
    """Reaudit original compiled data; this does not admit guest installation."""
    record = _original_runner_install(plan, stack, context)
    stack.driver._admit(context, context.deadline)
    for path, (digest, bound) in record["refs"].items():
        if _sha(_read(_safe_path(Path(path)), bound)) != digest:
            raise StateError("Runner selected input changed before staging")
    if any(os.environ.get(name) != value for name, value in record["secrets"].items()):
        raise StateError("Runner selected private environment changed before staging")
    return deepcopy({key: value for key, value in record.items() if key not in ("stack", "pid", "refs")})


def require_runner_install(plan, stack, context):
    _original_runner_install(plan, stack, context)
    stack._admit(context, "up")
    return inspect_runner_install(plan, stack, context)
