#!/usr/bin/env python3
"""Build a deterministic plugin package from an explicit allowlist."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = (".dockerignore", "plugin.json", "pyproject.toml", "uv.lock", "toolchain.lock.json", "kustomize.lock.json",
             "cosign.lock.json", "supplychain.lock.json", "README.md", "LICENSE", "NOTICE")
COMPONENT_DIRS = (".codex-plugin", "assets", "skills", "src", "scripts", "schemas", "docs", "tests")
PUBLIC_DEMO_DIRS = ("demo/software-factory/apps", "demo/software-factory/charts", "demo/software-factory/scenarios",
                    "demo/software-factory/ci", "demo/software-factory/provision", "demo/software-factory/locks")
PUBLIC_DEMO_FILES = ("demo/software-factory/config/demo-config.example.json",
                     "demo/software-factory/config/registry1-blob-download-policy.example.json",
                     "demo/portable-gitops/README.md",
                     "demo/portable-gitops/versions.json",
                     "demo/portable-gitops/acquire.sh",
                     "demo/portable-gitops/bundle_inputs.py",
                     "demo/portable-gitops/pipeline.py",
                     "demo/portable-gitops/scan_policy.py",
                     "demo/portable-gitops/stack.py",
)
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".venv", ".git", "node_modules"}


def package_files() -> dict[str, bytes]:
    candidates = [ROOT / name for name in TOP_FILES if (ROOT / name).exists()]
    for directory in (*COMPONENT_DIRS, *PUBLIC_DEMO_DIRS):
        location = ROOT / directory
        if location.is_symlink():
            raise ValueError(f"Symlink component is not allowed: {directory}")
        if location.exists():
            candidates.extend(path for path in location.rglob("*") if path.is_file())
    candidates.extend((ROOT / "profiles").glob("*.example.json"))
    candidates.extend((ROOT / "profiles/platform-one").rglob("*.example.json"))
    candidates.extend(ROOT / path for path in PUBLIC_DEMO_FILES if (ROOT / path).is_file())
    result = {}
    for path in sorted(candidates):
        relative = path.relative_to(ROOT)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != ROOT):
            raise ValueError(f"Symlink cannot be packaged: {relative}")
        if path.suffix in {".pyc", ".pyo"} or path.name.startswith(".env"):
            continue
        result[relative.as_posix()] = path.read_bytes()
    return result


def validate(files: dict[str, bytes]) -> tuple[str, str]:
    required = {"plugin.json", "pyproject.toml", "uv.lock", "toolchain.lock.json", "README.md", "scripts/devsecops_review.py", "src/devsecops/cli.py", "schemas/profile.schema.json", "schemas/report.schema.json"}
    missing = sorted(required - files.keys())
    if missing:
        raise ValueError(f"Required runtime files are missing: {', '.join(missing)}")
    if "scripts/bootstrap.py" in files and "src/devsecops/schema_catalog.py" not in files:
        raise ValueError("Schema bootstrap is missing its exact-version selector")
    portable_stack_files = {
        path for path in PUBLIC_DEMO_FILES if path.startswith("demo/portable-gitops/")
    }
    if portable_stack_files & files.keys() or "tests/test_portable_stack.py" in files:
        if portable_stack_files - files.keys():
            raise ValueError("Portable stack is missing its exact public assets")
    if "src/devsecops/portable_runtime.py" in files:
        portable_required = {"scripts/observe_gitops.py", "docs/portable-gitops.md",
                             "docs/examples/portable-runtime.example.json",
                             "schemas/portable-runtime-config.schema.json",
                             "schemas/portable-runtime-observation.schema.json",
                             "schemas/portable-runtime-comparison.schema.json"}
        if portable_required - files.keys():
            raise ValueError("Portable runtime is missing its packaged entry point, guide or schemas")
    if "src/devsecops/factory/cli.py" in files:
        factory_required = {
            "scripts/factory_demo.py", "src/devsecops/factory/contracts.py", "src/devsecops/factory/doctor.py",
            "scripts/qualify_release.py", "src/devsecops/factory/qualification.py",
            "src/devsecops/factory/fresh_qualification.py", "src/devsecops/factory/native_qualification.py",
            "src/devsecops/factory/ci_evidence_qualification.py",
            "src/devsecops/factory/qualification_runner.py", "src/devsecops/factory/qualification_contracts.py",
            "schemas/qualification.schema.json", "schemas/video-review.schema.json", "schemas/video-chapters.schema.json",
            "docs/interfaces/release-qualification.md",
            "docs/demo/ironbank-e2e.md",
            "docs/interfaces/runner-vm-routes.md",
            "docs/interfaces/owned-runner-ci.md",
            "src/devsecops/factory/runner_package.py",
            "docs/interfaces/owned-runner-package.md",
            "docs/demo/two-hour-runbook.md", "docs/demo/recording-cues.csv",
            "docs/demo/canonical-phase-cue-map.json", "docs/demo/preflight.md",
            "docs/owned-cleanup-continuation.md",
            "schemas/factory-config.schema.json", "schemas/factory-lock.schema.json", "schemas/factory-scenario.schema.json",
            "schemas/scenario-assertions.schema.json",
            "src/devsecops/factory/state.py", "schemas/factory-state.schema.json",
            "src/devsecops/factory/lifecycle.py", "docs/interfaces/factory-lifecycle.md",
            "src/devsecops/factory/auxiliary_inputs.py",
            "src/devsecops/factory/terminal_evidence.py", "src/devsecops/factory/controller_cleanup.py", "schemas/controller-cleanup-policy.schema.json", "schemas/scenario-result.schema.json",
            "schemas/terminal-evidence-manifest.schema.json", "docs/interfaces/terminal-evidence.md",
            "src/devsecops/factory/native_driver.py", "src/devsecops/factory/native_phase.py",
            "src/devsecops/factory/native_factory_session.py", "src/devsecops/factory/owned_production.py",
            "skills/run-software-factory-demo/SKILL.md",
            "skills/run-software-factory-demo/agents/openai.yaml",
            "skills/run-software-factory-demo/references/native-handoff.md",
            "schemas/native-request.schema.json", "schemas/native-result.schema.json",
            "demo/software-factory/config/demo-config.example.json",
            "demo/software-factory/scenarios/ironbank-mission-recovery-v1/scenario.json",
            "demo/software-factory/scenarios/ironbank-mission-recovery-v1/assertions.json",
            "demo/software-factory/charts/mission-status/Chart.yaml",
            "demo/software-factory/charts/mission-status/values.schema.json",
            "demo/software-factory/charts/mission-status/templates/deployment.yaml",
            "demo/software-factory/charts/mission-status/templates/networkpolicy.yaml",
            "demo/software-factory/apps/mission-status/app.py",
            "demo/software-factory/apps/mission-status/probe.py",
            "demo/software-factory/apps/mission-status/Dockerfile",
            "demo/software-factory/apps/mission-status/requirements.txt",
            "demo/software-factory/apps/mission-status/dependency-lock.json",
        }
        if factory_required - files.keys():
            raise ValueError("Factory runtime is missing required packaged contracts or public demo assets")
    if "src/devsecops/correction.py" in files:
        correction_required = {"schemas/correction-contract.schema.json", "docs/correction.md", "src/devsecops/correction_presentation.py",
                               "skills/prepare-gitops-correction/SKILL.md",
                               "skills/prepare-gitops-correction/agents/openai.yaml"}
        if correction_required - files.keys():
            raise ValueError("Correction runtime is missing its contract, guide or native skill")
    if "demo/software-factory/apps/mission-status/probe.py" in files:
        probe_required = {
            "schemas/http-probe-request.schema.json", "schemas/http-probe-receipt.schema.json",
            "docs/interfaces/http-probes.md", "demo/software-factory/apps/mission-status/Dockerfile",
        }
        if probe_required - files.keys():
            raise ValueError("HTTP probe is missing its contracts, guide or image build recipe")
    if "src/devsecops/adapters/signatures.py" in files:
        signature_required = {
            "cosign.lock.json", "scripts/bootstrap_cosign.py", "docs/interfaces/signatures.md",
        }
        if signature_required - files.keys():
            raise ValueError("Signature verifier is missing its pinned toolchain, bootstrap or guide")
    if "src/devsecops/adapters/attestations.py" in files:
        if {"src/devsecops/adapters/signatures.py", "docs/interfaces/attestations.md"} - files.keys():
            raise ValueError("Attestation verifier is missing its signature primitives or guide")
    if "src/devsecops/adapters/image_verification.py" in files:
        image_required = {
            "src/devsecops/adapters/registry_evidence.py", "src/devsecops/adapters/attestations.py",
            "scripts/verify_registry_image.py", "docs/interfaces/registry-evidence.md",
            "docs/interfaces/image-verification.md",
        }
        if image_required - files.keys():
            raise ValueError("Image verification is missing its acquisition, cryptography, script or guide")
    if "src/devsecops/adapters/artifacts.py" in files:
        artifacts_required = {
            "supplychain.lock.json", "scripts/bootstrap_supplychain.py", "scripts/build_mission.py",
            "src/devsecops/factory/build.py", "docs/interfaces/build-chain.md",
            "src/devsecops/adapters/database_zstd.py", "docs/interfaces/database-archives.md",
            "src/devsecops/adapters/scanner_catalog.py",
            "demo/software-factory/ci/gitlab-ci.yml", "demo/software-factory/ci/Jenkinsfile",
        }
        if artifacts_required - files.keys():
            raise ValueError("Artifact chain is missing its toolchain, runtime, CI recipe or guide")
    if "src/devsecops/factory/qualify_builder.py" in files:
        if {"scripts/qualify_rootless_builder.py", "docs/interfaces/rootless-executor-qualification.md",
                "src/devsecops/factory/build.py", "supplychain.lock.json"} - files.keys():
            raise ValueError("Executor qualification is missing its entry point, guide or pinned worker")
    if "src/devsecops/factory/native_codex.py" in files:
        if {"scripts/native_codex.py", "docs/interfaces/native-codex.md",
                "schemas/native-request.schema.json", "schemas/native-result.schema.json",
                "src/devsecops/factory/state.py"} - files.keys():
            raise ValueError("Native Codex driver is missing its entry point, guide or handoff contracts")
    if "src/devsecops/factory/native_provenance.py" in files:
        if {"src/devsecops/factory/native_codex.py", "src/devsecops/runtime/recovery.py",
                "schemas/runtime-native-execution.schema.json", "docs/interfaces/native-provenance.md"} - files.keys():
            raise ValueError("Native provenance is missing its direct caller, consumer or guide")
    if "src/devsecops/adapters/image_policy.py" in files:
        if {"scripts/assess_registry_image.py", "docs/interfaces/image-policy.md",
                "src/devsecops/adapters/image_verification.py"} - files.keys():
            raise ValueError("Image policy is missing its entry point, guide or actual verification")
    if "src/devsecops/adapters/publisher_trust.py" in files:
        if {"scripts/assess_publisher_trust.py", "docs/interfaces/publisher-trust.md",
                "src/devsecops/adapters/signatures.py"} - files.keys():
            raise ValueError("Publisher trust is missing its entry point, guide or key verifier")
    if "src/devsecops/factory/base_qualification.py" in files:
        if {"src/devsecops/factory/runtime_recipe.py", "src/devsecops/factory/qualify_builder.py",
                "src/devsecops/adapters/publisher_trust.py", "src/devsecops/adapters/image_policy.py",
                "src/devsecops/adapters/image_layout.py", "docs/interfaces/base-qualification.md"} - files.keys():
            raise ValueError("Base qualification is missing its concrete trust, image or runtime component")
    if "src/devsecops/factory/mission_executor.py" in files:
        if {"src/devsecops/factory/mission_recipe.py", "src/devsecops/factory/mission_operation.py",
                "src/devsecops/factory/base_qualification.py", "docs/interfaces/mission-executor.md",
                "demo/software-factory/ci/mission-build.example.json"} - files.keys():
            raise ValueError("Mission executor is missing its typed base, fixed recipe or operator interface")
    if "src/devsecops/factory/mission_staging.py" in files:
        if {"src/devsecops/adapters/staging_registry.py", "src/devsecops/factory/mission_delivery.py",
                "src/devsecops/factory/mission_executor.py", "src/devsecops/adapters/image_layout.py",
                "scripts/stage_mission.py", "docs/interfaces/mission-staging.md",
                "demo/software-factory/ci/mission-staging.example.json"} - files.keys():
            raise ValueError("Mission staging is missing its upload, readback, caller or operator interface")
    if "src/devsecops/factory/mission_security.py" in files:
        if {"src/devsecops/adapters/vulnerability_policy.py", "src/devsecops/factory/mission_security_operation.py",
                "src/devsecops/factory/mission_staging.py", "src/devsecops/adapters/artifacts.py",
                "scripts/assess_mission.py", "docs/interfaces/mission-security.md",
                "demo/software-factory/ci/mission-security.example.json",
                "demo/software-factory/ci/mission-security-policy.example.json"} - files.keys():
            raise ValueError("Mission security is missing its scanner, policy, caller or operator interface")
    if "src/devsecops/factory/mission_signing.py" in files:
        if {"src/devsecops/adapters/local_signing.py", "src/devsecops/factory/mission_signing_operation.py",
                "src/devsecops/factory/mission_security.py", "src/devsecops/adapters/signatures.py",
                "scripts/sign_mission.py", "scripts/generate_lab_signing_key.py",
                "docs/interfaces/mission-signing.md", "demo/software-factory/ci/mission-signing.example.json",
                "demo/software-factory/ci/mission-signing-trust.example.json"} - files.keys():
            raise ValueError("Mission signing is missing its key helper, verifier, caller or operator interface")
    if "src/devsecops/factory/mission_publication.py" in files:
        if {"src/devsecops/adapters/mission_evidence.py", "src/devsecops/factory/mission_publication_operation.py",
                "src/devsecops/factory/mission_signing.py", "src/devsecops/adapters/staging_registry.py",
                "scripts/publish_mission.py", "docs/interfaces/mission-publication.md",
                "demo/software-factory/ci/mission-publication.example.json"} - files.keys():
            raise ValueError("Mission publication is missing its evidence reader, signer, caller or operator interface")
    if "src/devsecops/factory/deployment_proposal.py" in files:
        if {"src/devsecops/adapters/mission_values.py", "src/devsecops/factory/mission_publication.py",
                "src/devsecops/render.py", "src/devsecops/snapshot.py", "toolchain.lock.json",
                "src/devsecops/factory/mission_proposal_operation.py", "scripts/publish_and_propose_mission.py",
                "docs/interfaces/deployment-proposal.md"} - files.keys():
            raise ValueError("Deployment proposal is missing its scalar editor, publication, renderer or guide")
    proposal_operation = {"src/devsecops/factory/mission_proposal_operation.py", "scripts/publish_and_propose_mission.py"}
    if proposal_operation & files.keys():
        if (proposal_operation | {"src/devsecops/factory/deployment_proposal.py",
                "src/devsecops/factory/mission_publication_operation.py", "docs/interfaces/deployment-proposal.md"}) - files.keys():
            raise ValueError("Publication/proposal operation is missing its composition, caller or guide")
    git_invocation = {"src/devsecops/factory/mission_git_invocation.py", "scripts/publish_and_prepare_mission_git.py",
                      "schemas/mission-git-invocation.schema.json", "docs/interfaces/mission-git-invocation.md",
                      "demo/software-factory/ci/gitlab-git-preparation.yml"}
    if git_invocation & files.keys():
        if (git_invocation | {"src/devsecops/factory/mission_git_operation.py",
                "src/devsecops/factory/deployment_commit.py", "src/devsecops/factory/git_transport.py",
                "src/devsecops/factory/git_base_possession.py", "docs/interfaces/mission-git-composition.md"}) - files.keys():
            raise ValueError("Mission Git invocation is missing its caller, contract, recipe or composition")
    if "src/devsecops/factory/auxiliary_inputs.py" in files:
        if {"src/devsecops/factory/mission_git_invocation.py", "src/devsecops/factory/service_recipe.py",
                "src/devsecops/factory/state.py", "src/devsecops/factory/lifecycle.py",
                "src/devsecops/factory/owned_linux.py", "src/devsecops/factory/evidence_export.py",
                "src/devsecops/factory/cli.py", "schemas/factory-state.schema.json",
                "docs/interfaces/lifecycle-auxiliary-inputs.md"} - files.keys():
            raise ValueError("Lifecycle auxiliary selection is missing its parser, journal, CLI, export or guide")
    if "src/devsecops/factory/owned_production.py" in files:
        if {"src/devsecops/factory/deployment_validation.py",
                "scripts/validate_owned_deployment.py",
                "demo/software-factory/ci/gitlab-owned-deployment-v1.yml"} - files.keys():
            raise ValueError("Canonical production is missing its fixed deployment MR validator")
        production = {"canonical_bootstrap", "bootstrap_recipe", "owned_services", "kubernetes_operations",
                      "platform_bootstrap", "platform_inputs", "owned_operations", "operations_recipe", "owned_cleanup",
                      "cleanup_observation", "owned_gitlab_cleanup", "owned_writer_identity", "owned_git_delivery",
                      "runtime_observations", "network_policy_qualification", "owned_runtime_inputs",
                      "runtime_image", "locked_images", "owned_cases", "cases_recipe",
                      "owned_runtime_transport", "owned_runtime_reader", "owned_reader_identity",
                      "policy_git_source", "owned_policy_delivery", "owned_correction_contract", "runtime_acceptance",
                      "case_platform", "case_platform_git", "case_supply_chain", "case_evidence",
                      "fresh_foundation_recipe", "fresh_foundation_remote", "owned_fresh_foundation",
                      "owned_service_links", "service_link_remote", "service_link_local", "service_link_guest",
                      "replay_acceptance", "case_interruption", "resident_cleanup_control",
                      "ci_publication", "git_native_request", "git_receive_http", "git_request_claim",
                      "git_request_framing", "git_writer_identity", "owned_checkout", "owned_ci",
                      "runner_install", "services_remote", "owned_runner_vm", "runner_vm_recipe",
                      "runner_vm_remote", "owned_runner_service", "runner_service_guest",
                      "runner_vm_routes", "runner_vm_routes_remote", "runner_vm_guest_routes", "qemu_acceleration"}
        if {"src/devsecops/factory/" + name + ".py" for name in production} - files.keys():
            raise ValueError("Canonical production composition is missing a fixed component or input contract")
        if "src/devsecops/adapters/owned_route.py" not in files:
            raise ValueError("Canonical production composition is missing its original service-route transport")
        if "src/devsecops/runtime/probe_observations.py" not in files:
            raise ValueError("Canonical production composition is missing its original probe observation producer")
        if {"schemas/fresh-owned-foundation.schema.json", "schemas/owned-cases-recipe.schema.json",
                    "schemas/owned-runner-vm.schema.json", "docs/interfaces/owned-runner-vm.md",
                    "docs/interfaces/qemu-acceleration.md",
                "docs/interfaces/owned-runner-service.md",
                "demo/software-factory/provision/tcg/FRESH-OWNED.md",
                "docs/interfaces/canonical-cases.md"} - files.keys():
            raise ValueError("Canonical production composition is missing its source contracts or operational guides")
    if "src/devsecops/adapters/gitlab_pipeline.py" in files:
        if {"scripts/collect_gitlab_pipeline.py", "docs/interfaces/gitlab-pipeline.md",
                "src/devsecops/adapters/http.py"} - files.keys():
            raise ValueError("GitLab pipeline collection is missing its command, guide or verified transport")
    if "src/devsecops/adapters/image_layout.py" in files:
        if {"src/devsecops/adapters/http_blob.py", "src/devsecops/adapters/oci.py",
                "docs/interfaces/image-layout-acquisition.md"} - files.keys():
            raise ValueError("Image layout acquisition is missing its bounded transport or guide")
    if "src/devsecops/runtime/cli.py" in files:
        runtime_required = {
            "src/devsecops/runtime/context.py", "src/devsecops/runtime/collection.py",
            "src/devsecops/runtime/diagnosis.py", "src/devsecops/runtime/presentation.py",
            "src/devsecops/adapters/http.py", "src/devsecops/adapters/argocd.py",
            "src/devsecops/adapters/kubernetes.py", "src/devsecops/adapters/prometheus.py",
            "schemas/runtime-intent.schema.json", "schemas/runtime-context.schema.json",
            "schemas/runtime-collection.schema.json", "schemas/runtime-delivery-binding.schema.json",
            "schemas/runtime-diagnosis.schema.json", "docs/runtime.md",
            "skills/diagnose-gitops-deployment/SKILL.md", "skills/diagnose-gitops-deployment/agents/openai.yaml",
        }
        if runtime_required - files.keys():
            raise ValueError("Runtime evidence commands are missing required adapters, contracts, reports or native skill")
    if "src/devsecops/runtime/recovery.py" in files:
        recovery_required = {
            "schemas/runtime-recovery.schema.json", "schemas/runtime-recovery-contract.schema.json",
            "schemas/runtime-recovery-inventory.schema.json", "schemas/runtime-git-delivery.schema.json",
            "schemas/runtime-native-execution.schema.json", "docs/interfaces/runtime-recovery.md",
            "skills/verify-gitops-recovery/SKILL.md", "skills/verify-gitops-recovery/agents/openai.yaml",
        }
        if recovery_required - files.keys():
            raise ValueError("Recovery verifier is missing its contracts, guide or native skill")
    manifest = json.loads(files["plugin.json"])
    if manifest.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
        raise ValueError("Portable plugin schema declaration is invalid")
    name, version = manifest.get("name", ""), manifest.get("version", "")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?", name) or ".." in name or "--" in name:
        raise ValueError("Plugin name is invalid")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?", version):
        raise ValueError("Plugin version must use SemVer")
    # Reports run directly from packaged source, without installed distribution
    # metadata. Their identity must match the release the customer installed.
    module = ast.parse(files.get("src/devsecops/__init__.py", b""))
    versions = [node.value.value for node in module.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
                and isinstance(node.value, ast.Constant)]
    if versions != [version]:
        raise ValueError("Runtime report version differs from plugin release")
    for forbidden in ("skills", "mcpServers", "apps", "interface"):
        if forbidden in manifest:
            raise ValueError(f"Legacy field in portable manifest: {forbidden}")
    interface = manifest["extensions"]["com.openai"]["interface"]
    if not 1 <= len(interface.get("shortDescription", "")) <= 30:
        raise ValueError("Plugin subtitle must contain 1–30 characters")
    for asset in ("logo", "composerIcon", "logoDark", "composerIconDark"):
        if asset in interface and interface[asset].removeprefix("./") not in files:
            raise ValueError(f"Missing interface asset: {asset}")
    overlay_path = ".codex-plugin/plugin.json"
    if overlay_path in files:
        overlay = json.loads(files[overlay_path])
        for field in ("name", "version", "description", "author"):
            if overlay.get(field) != manifest.get(field):
                raise ValueError(f"Compatibility manifest drift: {field}")
        if overlay.get("interface") != interface:
            raise ValueError("Compatibility interface differs from portable interface")
    skills = [path for path in files if re.fullmatch(r"skills/[^/]+/SKILL.md", path)]
    if not skills:
        raise ValueError("The package contains no portable skill")
    for path in skills:
        text = files[path].decode("utf-8")
        if not text.startswith("---\n"):
            raise ValueError(f"Missing skill frontmatter: {path}")
        metadata = yaml.safe_load(text.split("---", 2)[1])
        if metadata.get("name") != path.split("/")[1] or not metadata.get("description"):
            raise ValueError(f"Invalid skill metadata: {path}")
        agent_path = str(Path(path).parent / "agents" / "openai.yaml")
        if agent_path in files:
            agent = yaml.safe_load(files[agent_path])
            agent_interface = agent.get("interface", {})
            if not agent_interface.get("display_name") or not agent_interface.get("short_description"):
                raise ValueError(f"Invalid skill interface: {agent_path}")
    return name, version


def content_manifest(files: dict[str, bytes]) -> bytes:
    """Exact portable content identity, independent of ZIP container bytes."""
    if "SHA256SUMS" in files:
        raise ValueError("The content manifest must not include itself")
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {path}\n"
                   for path, data in sorted(files.items())).encode()


def build(output: Path, *, overwrite: bool = False) -> dict[str, str | int]:
    output = output.expanduser().resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("Write the archive outside the directory being packaged")
    if output.exists() and not overwrite:
        raise ValueError("Output exists; select a new path or explicitly use --overwrite")
    if not output.parent.is_dir():
        raise ValueError("The archive parent directory must already exist")
    files = package_files()
    name, version = validate(files)
    files["SHA256SUMS"] = content_manifest(files)
    with tempfile.NamedTemporaryFile(prefix="devsecops-package-", suffix=".zip", dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative, data in sorted(files.items()):
                entry = zipfile.ZipInfo(f"{name}/{relative}", date_time=(2026, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, data)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("Archive integrity validation failed")
            if set(archive.namelist()) != {f"{name}/{path}" for path in files}:
                raise ValueError("Archive membership validation failed")
        if overwrite:
            temporary.replace(output)
        else:
            os.link(temporary, output)
        return {
            "path": str(output), "name": name, "version": version,
            "files": len(files), "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "package_sha256": hashlib.sha256(files["SHA256SUMS"]).hexdigest(),
        }
    finally:
        temporary.unlink(missing_ok=True)


def stage_marketplace(destination: Path) -> dict[str, str | int]:
    """Prepare a source marketplace; registration/installation is a separate action."""
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ValueError("Marketplace directory exists; choose a new staging directory")
    files = package_files()
    name, version = validate(files)
    files["SHA256SUMS"] = content_manifest(files)
    destination.mkdir(parents=True, exist_ok=False)
    plugin = destination / "plugins" / name
    for relative, data in files.items():
        path = plugin / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    metadata = {
        "name": "devsecops-local",
        "interface": {"displayName": "DevSecOps Local"},
        "plugins": [{
            "name": name,
            "source": {"source": "local", "path": f"./plugins/{name}"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Developer Tools",
        }],
    }
    marketplace_file = destination / ".agents" / "plugins" / "marketplace.json"
    marketplace_file.parent.mkdir(parents=True, exist_ok=True)
    marketplace_file.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return {
        "path": str(destination), "name": "devsecops-local", "version": version,
        "plugin": name, "files": len(files), "status": "staged-not-installed",
        "package_sha256": hashlib.sha256(files["SHA256SUMS"]).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New private ZIP path outside the source tree")
    parser.add_argument("--marketplace-dir", type=Path, help="New local source-marketplace staging directory")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.output is None and args.marketplace_dir is None:
        parser.error("Supply --output or --marketplace-dir")
    try:
        result = {}
        if args.output is not None:
            result["archive"] = build(args.output, overwrite=args.overwrite)
        if args.marketplace_dir is not None:
            result["marketplace"] = stage_marketplace(args.marketplace_dir)
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        parser.exit(2, f"Package failed: {error}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
