"""Canonical fixed owned bootstrap DAG and non-serializable direct capabilities.

Recipe selection is not readiness. Direct component outcomes, authenticated API
readbacks and exact same-process resource ownership precede capability issuance.
"""

from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict
import os
from pathlib import Path
import time
from types import MappingProxyType
from uuid import uuid4

from . import bootstrap_recipe, service_recipe, service_inventory
from .gitlab_api import GitLabSession
from .gitlab_bootstrap import (
    BootstrapScope,
    ProjectSeed,
    SeedFile,
    bootstrap_owned_projects,
    _project_identity,
)
from .kubernetes_operations import KubernetesSession, OwnedKubernetesResources
from .lifecycle import PhaseOutcome, _load_inputs
from .owned_foundation import OwnedRKE2Foundation
from .owned_registry import OwnedOCIRegistry
from .owned_services import OwnedServiceStack
from .platform_bootstrap import OwnedPlatformBootstrap
from .state import StateError, _json, _read, _safe_path, _sha

_ISSUER = object()


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    return value


class BootstrapAuthority:
    __slots__ = ("_owner", "_pid", "_facts", "_scope", "_stage")

    def __init__(self, *, _issuer, owner, facts, scope, stage="owned-platform-admitted"):
        if _issuer is not _ISSUER:
            raise StateError("Bootstrap authority can only be issued by the fixed coordinator")
        for key, value in (
            ("_owner", owner),
            ("_pid", os.getpid()),
            ("_facts", _freeze(deepcopy(facts))),
            ("_scope", scope),
            ("_stage", stage),
        ):
            object.__setattr__(self, key, value)

    def __setattr__(self, name, value):
        raise TypeError("Original bootstrap authority is immutable")

    @property
    def scope(self):
        return self._scope

    def __copy__(self):
        raise TypeError("Bootstrap authority is not transferable")

    def __deepcopy__(self, memo):
        raise TypeError("Bootstrap authority is not transferable")

    def __reduce__(self):
        raise TypeError("Bootstrap authority is not serializable")


def require_bootstrap_authority(authority, journal, *, deadline=None):
    if (
        type(authority) is not BootstrapAuthority
        or authority._pid != os.getpid()
        or type(authority._owner) is not OwnedBootstrapCoordinator
        or authority._owner.journal is not journal
        or authority
        is not (
            authority._owner._gitlab_authority
            if authority._stage == "owned-gitlab-projects"
            else authority._owner._authority
        )
    ):
        raise StateError("A direct original owned bootstrap authority is required")
    authority._owner._audit_authority(stage=authority._stage, deadline=deadline)
    return authority._facts


def runner_origin_for(authority, journal, context):
    """Fixed original bootstrap→guest proof for a canonical delivery job."""
    require_bootstrap_authority(authority, journal, deadline=context.deadline)
    if authority._owner.journal is not journal:
        raise StateError("Runner origin belongs to another bootstrap journal")
    return authority._owner.runner_origin(context)


class OwnedBootstrapCoordinator:
    def __init__(self, driver, *, cleanup):
        self.driver, self.journal, self.cleanup = driver, driver.journal, cleanup
        selection = driver.auxiliary_sources or {}
        pair = selection.get("owned_bootstrap_recipe")
        if type(pair) is not tuple or len(pair) != 2:
            raise StateError("Owned bootstrap requires an explicit pinned auxiliary recipe")
        self.recipe_path, self.recipe_sha256 = pair
        self._pid, self._authority, self._revoked = os.getpid(), None, False
        self._gitlab_authority, self._gitlab_facts, self._writer = None, None, None
        self._prepared, self._dispatched, self._preflight = False, False, None
        self._runner_plan = None
        self._runner_vm = self._runner_routes = self._runner_service = None
        self._evidence = {}
        self._gitlab_resources = None
        self._scope, self._session, self._facts, self._kubernetes = None, None, None, None
        recipe = self._recipe()
        config_ref = self.journal.state["lifecycle"]["inputs"]["config"]
        config = _json(_read(self.journal._artifact_path(config_ref["path"]), 1000000))
        self._foundation_kind = config["runtime"].get("foundation", {}).get("kind")
        if self._foundation_kind == "fresh-owned-tcg-v1":
            from .owned_fresh_foundation import OwnedFreshRKE2Foundation

            self.foundation = OwnedFreshRKE2Foundation(
                self.journal, plugin_id=driver.plugin_id, plugin_version=driver.plugin_version
            )
        elif self._foundation_kind == "retained-tcg-v1":
            self.foundation = OwnedRKE2Foundation(
                self.journal, plugin_id=driver.plugin_id, plugin_version=driver.plugin_version
            )
        else:
            raise StateError("Owned bootstrap requires an explicit supported foundation recipe")
        self.registry = OwnedOCIRegistry(
            self.journal,
            recipe_path=recipe["registry"]["path"],
            recipe_sha256=recipe["registry"]["sha256"],
            plugin_id=driver.plugin_id,
            plugin_version=driver.plugin_version,
        )
        self.services = OwnedServiceStack(
            driver, recipe_path=self.recipe_path, recipe_sha256=self.recipe_sha256
        )
        if self._foundation_kind == "fresh-owned-tcg-v1":
            self.registry.bind_foundation_transport(self.foundation)
            self.services.bind_foundation_transport(self.foundation)
        self.platform = OwnedPlatformBootstrap(driver, recipe["platform"])
        self._service_links = None
        if self._foundation_kind == "fresh-owned-tcg-v1":
            from .owned_service_links import OwnedServiceLinks

            self._service_links = OwnedServiceLinks(
                driver, foundation=self.foundation, registry=self.registry, services=self.services
            )
            cleanup.register_service_links(self._service_links)
        cleanup.register_foundation(self.foundation)
        cleanup.register_registry(self.registry)
        cleanup.register_services(self.services)
        if recipe["kind"] == "owned-bootstrap-v2":
            from .owned_runner_vm import OwnedRunnerVM
            from .runner_vm_routes import OwnedRunnerVMRoutes
            from .owned_runner_service import OwnedRunnerService

            self._runner_vm = OwnedRunnerVM(driver, foundation=self.foundation, services=self.services)
            self._runner_routes = OwnedRunnerVMRoutes(
                driver, vm=self._runner_vm, registry=self.registry, services=self.services
            )
            self._runner_service = OwnedRunnerService(
                driver, vm=self._runner_vm, services=self.services, routes=self._runner_routes
            )
            cleanup.register_runner_vm(self._runner_vm)
            cleanup.register_runner_routes(self._runner_routes)
            cleanup.register_runner_service(self._runner_service)

    def cleanup_inventory_contract(self):
        if self._pid != os.getpid():
            raise StateError("Cleanup inventory selection belongs to another process")
        return (
            "owned-runner-vm-v1"
            if self._recipe()["kind"] == "owned-bootstrap-v2"
            else "owned-container-runner-v1"
        )

    def runner_origin(self, context):
        self._admit(context)
        if self._runner_service is None:
            raise StateError("Canonical full CI requires its original dedicated runner VM")
        return self._runner_service.execution_origin(context)

    def _recipe(self):
        raw = _read(_safe_path(Path(self.recipe_path)), 256000)
        if _sha(raw) != self.recipe_sha256:
            raise StateError("Owned bootstrap recipe differs from exact selected bytes")
        recipe = bootstrap_recipe.validate(_json(raw))
        if recipe["environment"] != self.journal.state["environment_id"]:
            raise StateError("Owned bootstrap environment differs from current journal")
        return recipe

    def _admit(self, context):
        if self._pid != os.getpid() or self._revoked:
            raise StateError("Owned bootstrap coordinator is no longer live in this process")
        self.driver._admit(context, context.deadline)
        self.driver._package(self.journal.identities["package_sha256"], context.deadline)
        self._authority_deadline = context.deadline
        return self._recipe()

    def _artifact(self, kind, value):
        path = self.journal.run_dir / ("bootstrap-" + kind + "-" + uuid4().hex + ".json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(bootstrap_recipe.canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        self._evidence[path] = _sha(_read(path, 16000000))
        return path

    def _retain_outcome(self, outcome):
        if outcome is None:
            return
        if type(outcome) is not PhaseOutcome:
            raise StateError("Bootstrap child outcome must come from its direct fixed component")
        for path in outcome.artifacts:
            self._evidence[Path(path)] = _sha(_read(Path(path), 16000000))

    def evidence_artifacts(self, context):
        if (
            self._pid != os.getpid()
            or context.run_id != self.journal.state["run_id"]
            or dict(context.identities) != self.journal.identities
        ):
            raise StateError("Bootstrap evidence requires the original run")
        for path, digest in self._evidence.items():
            if _sha(_read(path, 16000000)) != digest:
                raise StateError("Original bootstrap public evidence changed")
        paths = list(self._evidence)
        for component in (
            self.platform,
            self.services,
            self._kubernetes,
            self._gitlab_resources,
            self._runner_vm,
            self._runner_routes,
            self._runner_service,
        ):
            if component is not None:
                paths.extend(component.evidence_artifacts(context))
        unique = tuple(dict.fromkeys(paths))
        if len(unique) > 256 or sum(Path(path).stat().st_size for path in unique) > 64000000:
            raise StateError("Complete bootstrap public evidence exceeds the phase result bound")
        return unique

    phase_artifacts = evidence_artifacts

    def _seeds(self, recipe):
        seeds = []
        for role in ("source", "deployment"):
            files = []
            for item in recipe["seeds"][role]:
                raw = _read(_safe_path(Path(item["file"]["path"])), 65536)
                if _sha(raw) != item["file"]["sha256"]:
                    raise StateError("Owned project seed differs from its selected bytes")
                if item["repository_path"] == ".gitlab-ci.yml":
                    installed = (
                        Path(__file__).resolve().parents[3]
                        / "demo/software-factory/ci"
                        / (
                            "gitlab-owned-api-v1.yml"
                            if role == "source"
                            else "gitlab-owned-deployment-v1.yml"
                        )
                    )
                    if raw != _read(installed, 65536):
                        raise StateError(
                            "Owned project CI must use its exact installed source or deployment recipe"
                        )
                files.append(SeedFile(item["repository_path"], raw))
            if ".gitlab-ci.yml" not in [item.path for item in files]:
                raise StateError("Each owned project requires its installed explicit CI workflow")
            seeds.append(ProjectSeed(role, tuple(files)))
        return tuple(seeds)

    def preflight(self, context):
        recipe = self._admit(context)
        from .controller_cleanup import POLICY

        if recipe["platform"].get("controller_cleanup") != POLICY:
            raise StateError("Canonical bootstrap requires selected owned controller cleanup policy")
        inventory = service_inventory.classify_foundation_services(
            context.inputs["environment"], context.inputs["config"]
        )
        dispositions = {row["id"]: row["disposition"] for row in inventory}
        if dispositions.get("gitlab") != "planned-owned" or dispositions.get("registry") != "planned-owned":
            raise StateError("Owned bootstrap requires declared owned GitLab and registry")
        if not {"provision", "cleanup"} <= set(context.inputs["config"]["allowed_mutations"]):
            raise StateError("Owned bootstrap requires provision and cleanup scope")
        self._seeds(recipe)
        registry_raw = _read(_safe_path(Path(recipe["registry"]["path"])), 64000)
        if _sha(registry_raw) != recipe["registry"]["sha256"]:
            raise StateError("Owned bootstrap registry recipe pin changed")
        selected_registry = service_recipe.validate(_json(registry_raw))
        if selected_registry["environment"] != recipe["environment"]:
            raise StateError("Owned bootstrap registry environment differs")
        for ref in [*recipe["services"]["materials"].values(), *recipe["kubernetes"]["manifests"]]:
            if _sha(_read(_safe_path(Path(ref["path"])), 1000000)) != ref["sha256"]:
                raise StateError("Owned bootstrap material differs from selected bytes")
        for env in recipe["services"]["credentials"].values():
            if not isinstance(os.environ.get(env), str) or not 20 <= len(os.environ[env]) <= 512:
                raise StateError("Owned bootstrap selected private credentials are unavailable")
        self.platform.prepare(context)
        images = self.services.inspect_preconditions(context)
        self._preflight = {
            "kind": "owned-bootstrap-preflight",
            "run_id": context.run_id,
            "identities": dict(self.journal.identities),
            "recipe_sha256": self.recipe_sha256,
            "preloaded_service_images": {k: dict(v) for k, v in images["images"].items()},
            "service_dispositions": dispositions,
            "readiness": "bootstrap-inputs-observed",
            "platform_readiness": "not-yet-provisioned",
            "foundation_mode": self._foundation_kind,
            "cluster_origin": "not-yet-provisioned",
            "fresh_cluster_qualification": False,
        }
        self._artifact("preflight", self._preflight)
        return PhaseOutcome(
            "completed",
            "owned-bootstrap-inputs-observed",
            self.evidence_artifacts(context),
            mutation_effect="not_applied",
        )

    def prepare(self, context):
        recipe = self._admit(context)
        if self._preflight is None:
            raise StateError("Owned bootstrap requires its original preflight observation")
        operation = self.journal.current_mutation(context.phase["id"], "prepare")
        seeds = self._seeds(recipe)
        from .runner_install import prepare_runner_install

        self._runner_plan = prepare_runner_install(self.services, context)
        if recipe["kind"] == "owned-bootstrap-v2":
            from .runner_install import inspect_runner_install
            from .owned_runner_service import _admit_external_dependencies
            from .runner_vm_recipe import validate as runner_vm_configuration

            ref = recipe["services"]["runner_vm"]
            raw = _read(_safe_path(Path(ref["path"])), 256000)
            if _sha(raw) != ref["sha256"]:
                raise StateError("Original runner VM dependency selection changed")
            selected_vm = runner_vm_configuration(_json(raw))
            _admit_external_dependencies(
                inspect_runner_install(self._runner_plan, self.services, context),
                {"external_origins": selected_vm["egress"]["external_origins"]},
            )

        staged = {
            "run_id": context.run_id,
            "operation_id": operation,
            "recipe_sha256": self.recipe_sha256,
            "prepared": "local-fixed-owned-bootstrap-dag",
            "seed_files": {
                seed.role: [
                    {"path": item.path, "sha256": _sha(item.content), "bytes": len(item.content)}
                    for item in seed.files
                ]
                for seed in seeds
            },
            "remote_effects": "none",
            "platform_readiness": "not-yet-provisioned",
        }
        artifact = self._artifact("prepared", staged)
        if _read(artifact, 1000000) != bootstrap_recipe.canonical(staged):
            raise StateError("Local bootstrap preparation artifact readback differs")
        self._prepared = True
        return PhaseOutcome(
            "completed",
            "owned-bootstrap-prepared",
            self.evidence_artifacts(context),
            mutation_effect="applied",
        )

    def _new_session(self, recipe, principal, *, context=None):
        selected = recipe["services"]
        route = (
            {}
            if self._service_links is None
            else {"service_route": self._service_links.route(context, "gitlab")}
        )
        session = GitLabSession(
            selected["origin"],
            **route,
            token_env=selected["credentials"]["operator_token_env"],
            trust={"mode": "bundle", **selected["materials"]["ca"]},
            expected_user_id=principal["owner_user_id"],
            total_timeout=900,
            max_requests=512,
            max_total_bytes=64000000,
        )
        session.metadata()
        session.principal()
        return session

    def up(self, context):
        recipe = self._admit(context)
        if not self._prepared or self._dispatched:
            raise StateError("Owned bootstrap requires original preparation and is one-shot")
        self.journal.current_mutation(context.phase["id"], "up")
        intents = self.journal.state["active_mutation"]["intent_artifacts"]
        if not {self.recipe_sha256, recipe["registry"]["sha256"]} <= {row["sha256"] for row in intents}:
            raise StateError("Bootstrap and registry recipes must both be frozen in current mutation intent")
        self.cleanup.declare_deferred_scope(context, "kubernetes")
        self.cleanup.declare_deferred_scope(context, "gitlab")
        self.cleanup.declare_deferred_scope(context, "writer")
        self.cleanup.declare_deferred_scope(context, "reader")
        self.cleanup.seal_inventory(context, inventory_contract=self.cleanup_inventory_contract())
        self._dispatched = True
        foundation_outcome = self.cleanup.foundation_up(context)
        self._retain_outcome(foundation_outcome)
        foundation = self.foundation.bootstrap_observation(foundation_outcome)
        expected_readiness = (
            "fresh-rke2-ready" if self._foundation_kind == "fresh-owned-tcg-v1" else "retained-rke2-ready"
        )
        if foundation["report"].get("foundation_readiness") != expected_readiness:
            raise StateError("Owned bootstrap foundation is not ready")
        fresh_origin = (
            deepcopy(foundation["report"].get("origin"))
            if self._foundation_kind == "fresh-owned-tcg-v1"
            else None
        )
        if self._foundation_kind == "fresh-owned-tcg-v1" and not fresh_origin:
            raise StateError("Fresh foundation lacks its original direct creation provenance")
        registry_outcome = self.cleanup.registry_up(context)
        self._retain_outcome(registry_outcome)
        # Fixed component report is evidence from this exact direct invocation;
        # aggregate readiness still requires its actual storage qualification.
        registry_report = self.registry.component_observation(registry_outcome)
        registry_observation = registry_report.get("observation", {})
        if registry_observation.get("storage_qualification") not in ("pass",):
            raise StateError("Owned bootstrap registry storage qualification is incomplete")
        self._retain_outcome(self.cleanup.services_up(context))
        if self._service_links is not None:
            self._retain_outcome(self.cleanup.service_links_up(context))
        if self._runner_vm is not None:
            self._runner_vm.prepare(context)
            self._retain_outcome(self.cleanup.runner_vm_up(context))
            self._retain_outcome(self.cleanup.runner_routes_up(context))
        principal = self.services.principal(context)
        session = self._new_session(recipe, principal, context=context)
        scope = BootstrapScope(
            recipe["services"]["origin"],
            recipe["environment"],
            self.journal.identities["environment_sha256"],
            principal["owner_user_id"],
            principal["namespace_id"],
            principal["namespace_path"],
        )
        resources = self.cleanup.bind_gitlab(context, session, scope)
        self._gitlab_resources = resources
        resources.accept_service_token(self.services)
        report = bootstrap_owned_projects(
            session, scope, self._seeds(recipe), journal_root=self.journal.run_dir, cleanup=resources
        )
        projects = {
            row["role"]: {
                "id": row["project_id"],
                "path_with_namespace": row["path_with_namespace"],
                "seed_commit": row["seed_commit"],
            }
            for row in report["resources"]
        }
        self._scope, self._session = scope, session
        self._gitlab_facts = {
            "run_id": context.run_id,
            "identities": dict(self.journal.identities),
            **asdict(scope),
            "scope": asdict(scope),
            "projects": deepcopy(projects),
            "authority_stage": "owned-gitlab-projects",
        }
        self._gitlab_authority = BootstrapAuthority(
            _issuer=_ISSUER, owner=self, facts=self._gitlab_facts, scope=scope, stage="owned-gitlab-projects"
        )
        self._issue_writer(context, recipe, session)
        self._issue_reader(context, session)
        ticket = resources.runner_intent(projects["source"]["id"], scope.marker)
        created = session.request(
            "POST",
            "/api/v4/user/runners",
            payload={
                "runner_type": "project_type",
                "project_id": projects["source"]["id"],
                "description": scope.marker,
                "paused": True,
                "locked": False,
                "run_untagged": True,
                "tag_list": "devsecops-owned",
                "access_level": "not_protected",
                "maximum_timeout": 3600,
            },
            expected=(201,),
        )
        if (
            type(created) is not dict
            or type(created.get("id")) is not int
            or created["id"] <= 0
            or not isinstance(created.get("token"), str)
        ):
            session.stop_writes()
            raise StateError("Owned runner creation response is incomplete")
        runner_id = created["id"]
        fresh = session.request("GET", f"/api/v4/runners/{runner_id}")
        if (
            fresh.get("id") != runner_id
            or fresh.get("runner_type") != "project_type"
            or fresh.get("description") != scope.marker
        ):
            session.stop_writes()
            raise StateError("Owned runner identity differs from the created project runner")
        resources.runner_created(ticket, runner_id)
        self._assign_runner(context, session, resources, ticket, runner_id, scope, projects)
        if self._runner_service is not None:
            self._runner_service.bind_registration(
                context,
                resources=resources,
                ticket=ticket,
                runner_id=runner_id,
                runner_token=created["token"],
                description=scope.marker,
                install_plan=self._runner_plan,
            )
            self._retain_outcome(self.cleanup.runner_service_up(context))
            runner = self._runner_service.runner(context)
        else:
            runner_outcome = self.services.start_runner(
                context,
                runner_id=runner_id,
                runner_token=created["token"],
                description=scope.marker,
                install_plan=self._runner_plan,
            )
            self._retain_outcome(runner_outcome)
            if self._service_links is not None:
                self._retain_outcome(self._service_links.configure_runner(context))
            runner = dict(self.services.runner(context))
        online = False
        for _ in range(30):
            managers = session.get_collection(
                f"/api/v4/runners/{runner_id}/managers?per_page=100", max_pages=1, max_items=32
            )
            online = any(
                row.get("system_id") == runner["runner_manager_system_identity"]
                and row.get("status") == "online"
                and row.get("platform") == "linux"
                and row.get("architecture") == "amd64"
                for row in managers
            )
            if online:
                break
            if time.monotonic() + 2 >= context.deadline:
                break
            time.sleep(2)
        if not online:
            raise StateError("Owned runner manager has not been independently observed online")
        actual = foundation["actual"]
        private = Path(foundation["private_state_directory"])
        fingerprint = foundation["report"]["cluster_fingerprint"]
        cluster_uid = fingerprint["namespaces"]["kube-system"]
        ksession = KubernetesSession(
            actual["endpoint"],
            ca_file=private / "ca.crt",
            client_certificate=(private / "client.crt", private / "client.key"),
        )
        service = next(row for row in context.inputs["config"]["services"] if row["id"] == "kubernetes")
        manifests = []
        for ref in recipe["kubernetes"]["manifests"]:
            raw = _read(_safe_path(Path(ref["path"])), 1000000)
            if _sha(raw) != ref["sha256"]:
                raise StateError("Owned Kubernetes manifest bytes changed")
            manifests.append(_json(raw))
        all_manifests = self._resolve_platform_inputs(context, tuple(manifests))
        kresources = OwnedKubernetesResources(
            self.driver,
            session=ksession,
            cluster_uid=cluster_uid,
            namespaces=tuple(service["scope"]["namespaces"]),
            manifest_sha256=tuple(_sha(bootstrap_recipe.canonical(row)) for row in all_manifests),
            reader_selections=tuple(recipe["kubernetes"].get("runtime_readers", [])),
            private_secret_sha256=self.platform.private_secret_hashes(context),
            cluster_resources=tuple(
                (row["apiVersion"], row["kind"], row["name"])
                for row in recipe["platform"]["cluster_resources"]
            ),
        )
        self.cleanup.register_kubernetes(kresources)
        self._kubernetes = kresources
        platform = self.platform.install(
            context,
            kresources,
            additional_manifests=tuple(manifests),
            expected_git_url=scope.origin + "/" + projects["deployment"]["path_with_namespace"] + ".git",
            expected_git_revision=projects["deployment"]["seed_commit"],
        )
        self._runtime_readers = {
            selected["role"]: kresources.issue_read_token(context, selection=selected)
            for selected in recipe["kubernetes"].get("runtime_readers", [])
        }
        facts = {
            "run_id": context.run_id,
            "identities": dict(self.journal.identities),
            **asdict(scope),
            "projects": projects,
            **runner,
            "scope": asdict(scope),
            "kubernetes": {
                "origin": ksession.origin,
                "cluster_uid": cluster_uid,
                "cluster_origin": "fresh" if fresh_origin else "retained",
                "fresh_origin": fresh_origin,
                "fingerprint": fingerprint,
                "ca_file": str(private / "ca.crt"),
                "client_certificate": [str(private / "client.crt"), str(private / "client.key")],
                "owned_namespaces": list(kresources.namespaces),
                "owned_resources": [dict(row) for row in kresources.owned(context)],
                "argo_destination": platform["argo_destination"],
                "argo_application_identity": platform.get("argo_application_identity"),
            },
            "platform": platform,
        }
        self._scope, self._session, self._facts = scope, session, facts
        self._authority = BootstrapAuthority(_issuer=_ISSUER, owner=self, facts=facts, scope=scope)
        from .owned_runtime_reader import _bind

        for reader in self._runtime_readers.values():
            _bind(reader, bootstrap_authority=self._authority, resources=kresources)
        self._artifact(
            "observed",
            {
                "kind": "owned-bootstrap-observed",
                **facts,
                "fresh_cluster_qualification": bool(fresh_origin),
                "platform_qualification": "controllers-and-releases-observed-application-admitted",
                "workload_readiness": "pending-first-authenticated-delivery",
                "canonical_success": False,
            },
        )
        # Up establishes observed controller/release readiness only. The CI image
        # trust and explicit independent F0 experiment belong to build-promote;
        # fresh-cluster qualification remains unavailable on this retained route.
        return PhaseOutcome(
            "completed",
            "owned-bootstrap-controller-readiness-observed",
            self.evidence_artifacts(context),
            mutation_effect="applied",
        )

    def _audit_authority(self, *, stage="owned-platform-admitted", deadline=None):
        authority = self._gitlab_authority if stage == "owned-gitlab-projects" else self._authority
        facts = self._gitlab_facts if stage == "owned-gitlab-projects" else self._facts
        if (
            self._pid != os.getpid()
            or self._revoked
            or authority is None
            or not self.journal._lifecycle_locked
        ):
            raise StateError("Owned bootstrap authority is unavailable or expired")
        self._recipe()
        selected_deadline = self._authority_deadline if deadline is None else deadline
        if time.monotonic() >= selected_deadline:
            raise StateError("Original bootstrap authority audit deadline expired")
        self.driver._package(
            self.journal.identities["package_sha256"], min(selected_deadline, time.monotonic() + 30)
        )
        state = self.journal.state
        paths = {
            name: self.journal._artifact_path(ref["path"])
            for name, ref in state["lifecycle"]["inputs"].items()
        }
        _load_inputs(paths, self.journal.identities, state["environment_id"])
        if facts["run_id"] != self.journal.state["run_id"] or facts["identities"] != self.journal.identities:
            raise StateError("Owned bootstrap authority differs from current journal")

    def require_authority(self, context):
        self._admit(context)
        self._audit_authority(deadline=context.deadline)
        return self._authority

    def gitlab(self, context):
        authority = self.require_authority(context)
        self._recipe()
        # A new bounded transport keeps long demos within per-session limits;
        # original direct ownership remains mandatory and is rechecked remotely.
        if self._session.writes_blocked:
            raise StateError("Uncertain GitLab write blocks session renewal")
        from datetime import datetime, timezone

        if datetime.now(timezone.utc) >= datetime.fromisoformat(
            context.inputs["config"]["expires_at"].replace("Z", "+00:00")
        ):
            raise StateError("Owned GitLab operation window expired")
        route = (
            {} if self._service_links is None else {"service_route": self.service_route(context, "gitlab")}
        )
        session = self._session.renew_for_operation(**route)
        session.metadata()
        session.principal()
        for role, row in self._facts["projects"].items():
            _project_identity(
                session.request("GET", f"/api/v4/projects/{row['id']}"), self._scope, role, row["id"]
            )
        self._session = session
        return authority, session

    def _resolve_platform_inputs(self, context, additional):
        authority, reader = self.reader(context)
        return self.platform.bind_owned_inputs(
            context, bootstrap_authority=authority, reader_origin=reader, additional=additional
        )

    def _assign_runner(self, context, session, resources, ticket, runner_id, scope, projects):
        """Assign only this run's two projects before admitting any runner jobs."""
        self._admit(context)
        self.journal.current_mutation(context.phase["id"], "up")
        expected = {row["id"] for row in projects.values()}
        source, deployment = projects["source"]["id"], projects["deployment"]["id"]
        before = session.request("GET", f"/api/v4/runners/{runner_id}")

        def identity(value, project_ids, *, locked, paused):
            if (
                type(value) is not dict
                or value.get("id") != runner_id
                or value.get("runner_type") != "project_type"
                or value.get("is_shared") is not False
                or value.get("description") != scope.marker
                or value.get("locked") is not locked
                or value.get("paused") is not paused
                or type(value.get("projects")) is not list
                or {row.get("id") for row in value["projects"]} != project_ids
                or len(value["projects"]) != len(project_ids)
            ):
                raise StateError("Original owned runner project assignment or execution scope changed")

        identity(before, {source}, locked=False, paused=True)
        resources.runner_assignment_intent(ticket, deployment)
        try:
            assigned = session.request(
                "POST",
                f"/api/v4/projects/{deployment}/runners",
                payload={"runner_id": runner_id},
                expected=(201,),
            )
            if assigned.get("id") != runner_id or assigned.get("runner_type") != "project_type":
                session.stop_writes()
                raise StateError("Owned deployment runner assignment response is incomplete")
            observed = session.request("GET", f"/api/v4/runners/{runner_id}")
            identity(observed, expected, locked=False, paused=True)
            resources.runner_assigned(ticket, deployment)
            self._admit(context)
            self._artifact(
                "runner-execution-intent",
                {
                    "run_id": context.run_id,
                    "runner_id": runner_id,
                    "project_ids": sorted(expected),
                    "locked": True,
                    "paused": False,
                },
            )
            session.request("PUT", f"/api/v4/runners/{runner_id}", payload={"locked": True, "paused": False})
            identity(
                session.request("GET", f"/api/v4/runners/{runner_id}"), expected, locked=True, paused=False
            )
            for project_id in sorted(expected):
                assigned = session.get_collection(
                    f"/api/v4/projects/{project_id}/runners?type=project_type&per_page=100",
                    max_pages=1,
                    max_items=32,
                )
                if len(assigned) != 1 or assigned[0].get("id") != runner_id:
                    raise StateError(
                        "Owned project runner inventory differs from its assigned original runner"
                    )
            self._artifact(
                "runner-execution-observed",
                {
                    "run_id": context.run_id,
                    "runner_id": runner_id,
                    "project_ids": sorted(expected),
                    "locked": True,
                    "paused": False,
                },
            )
        except BaseException:
            session.stop_writes()
            raise

    def _issue_writer(self, context, recipe, session):
        from .owned_writer_identity import OwnedDeliveryWriter

        selected = recipe["writer_policy"]
        self._writer = OwnedDeliveryWriter(
            self.journal,
            bootstrap_authority=self._gitlab_authority,
            session=session,
            policy_path=Path(selected["path"]),
            policy_sha256=selected["sha256"],
            cleanup=self.cleanup,
        )
        self.cleanup.register_writer(self._writer)
        self._retain_outcome(self.cleanup.writer_up(context))

    def _issue_reader(self, context, session):
        from .owned_reader_identity import OwnedGitLabReader

        self._reader = OwnedGitLabReader(
            self.journal,
            bootstrap_authority=self._gitlab_authority,
            session=session,
            expires_at=context.inputs["config"]["expires_at"],
            cleanup=self.cleanup,
        )
        self.cleanup.register_reader(self._reader)
        self._retain_outcome(self.cleanup.reader_up(context))

    def reader(self, context):
        self._admit(context)
        self._audit_authority(stage="owned-gitlab-projects")
        if not getattr(self, "_reader", None):
            raise StateError("Original owned repository reader is unavailable")
        return self._gitlab_authority, self._reader.origin()

    def writer(self, context):
        self._admit(context)
        self._audit_authority(stage="owned-gitlab-projects")
        if self._writer is None:
            raise StateError("Original owned delivery writer has not been created")
        return self._gitlab_authority, self._writer.origin()

    def service_route(self, context, service_id, *, reconciliation_operation=None):
        if reconciliation_operation is None:
            self._admit(context)
        else:
            if (
                self._pid != os.getpid()
                or self._revoked
                or type(self.services) is not OwnedServiceStack
                or self.services.journal is not self.journal
            ):
                raise StateError("Read-only service route requires its original bootstrap owner")
            # The original service endpoint audits the exact current unknown
            # operation, inputs and source, without acquiring a service effect grant.
            self.services.service_endpoint(context, reconciliation_operation=reconciliation_operation)
            self._recipe()
        if self._service_links is None or service_id not in ("gitlab", "registry"):
            raise StateError("Original owned service route is unavailable")
        if reconciliation_operation is not None:
            return self._service_links.reconciliation_route(context, service_id, reconciliation_operation)
        return self._service_links.route(context, service_id)

    def capture_controller_inventory(self, context):
        """Observe actual first-ready delivery through the original retained owner."""
        from .controller_cleanup import OwnedControllerInventory

        self._admit(context)
        resources = self._kubernetes
        if (
            type(resources) is not OwnedKubernetesResources
            or type(resources._controller_inventory) is not OwnedControllerInventory
        ):
            raise StateError("Controller inventory requires original provisioned Kubernetes resources")
        resources._admit(context)
        resources._controller_inventory.capture(context)
        return resources._controller_inventory.observation()

    def runtime_reader(self, context, role="runtime"):
        from .owned_runtime_reader import require_runtime_reader

        authority = self.require_authority(context)
        reader = getattr(self, "_runtime_readers", {}).get(role)
        if reader is None:
            raise StateError("The owned bootstrap recipe lacks the selected runtime reader identity")
        require_runtime_reader(reader, journal=self.journal, bootstrap_authority=authority)
        return reader

    def kubernetes(self, context):
        self.require_authority(context)
        if self._kubernetes is None:
            raise StateError("Owned Kubernetes capability is unavailable")
        return self._kubernetes

    def revoke(self):
        self._revoked = True

    def execute(self, context):
        operation = context.phase["operation"]
        if operation == "preflight":
            return self.preflight(context)
        if operation == "prepare":
            return self.prepare(context)
        if operation == "up":
            return self.up(context)
        raise StateError("Owned bootstrap does not dispatch this canonical phase")
