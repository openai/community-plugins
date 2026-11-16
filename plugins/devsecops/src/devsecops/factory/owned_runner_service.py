"""Original dedicated-guest runner registration, process and CI provenance.

The guest observer supplies process facts; only this resident object, its exact
VM transport and the original API registration can issue a CI runner origin.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import os
import time
from uuid import uuid4
from weakref import WeakKeyDictionary

from .cleanup_observation import ComponentObservations
from .lifecycle import PhaseOutcome
from .state import StateError, _json, _read, _sha
from .bootstrap_recipe import canonical
from .owned_linux import _plain

_ORIGINS = WeakKeyDictionary()
_REVOCATIONS = WeakKeyDictionary()


class RunnerExecutionOrigin:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Runner execution origin requires the original guest service")

    def __reduce__(self):
        raise TypeError("Runner execution origin cannot be serialized")


class RunnerRegistrationRevoked:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Runner revocation requires its original API registration")

    def __reduce__(self):
        raise TypeError("Runner revocation cannot be serialized")


def require_runner_execution(value, *, run_id, runner_id, manager, current=False):
    record = _ORIGINS.get(value) if type(value) is RunnerExecutionOrigin else None
    if record is None or record["pid"] != os.getpid():
        raise StateError("Original runner execution capability required")
    owner = record["owner"]
    owner._audit_registration()
    owner._audit_evidence()
    facts = record["facts"]
    if (
        facts["run_id"] != run_id
        or facts["runner_id"] != runner_id
        or facts["runner_manager_system_identity"] != manager
        or current
        and (
            owner._unknown
            or owner._terminal is not None
            or time.monotonic() >= record["deadline"]
            or owner._registration_row()["state"] != "owned-present"
        )
    ):
        raise StateError("Runner execution identity or original lifetime differs")
    return deepcopy(facts)


def runner_execution_binding(value, *, current=False):
    record = _ORIGINS.get(value) if type(value) is RunnerExecutionOrigin else None
    if record is None:
        raise StateError("Original runner execution capability required")
    facts = record["facts"]
    return require_runner_execution(
        value,
        run_id=facts["run_id"],
        runner_id=facts["runner_id"],
        manager=facts["runner_manager_system_identity"],
        current=current,
    )


def require_runner_revocation(value, *, vm, context, reconciliation_operation=None):
    record = _REVOCATIONS.get(value) if type(value) is RunnerRegistrationRevoked else None
    if record is None or record["pid"] != os.getpid() or record["owner"].vm is not vm:
        raise StateError("Original runner registration revocation required")
    owner = record["owner"]
    if reconciliation_operation is None:
        owner.driver._admit(context, context.deadline)
    else:
        owner._registration[0].owner._admit(context, "down", operation_id=reconciliation_operation)
    if context.phase["operation"] != "down":
        raise StateError("Runner revocation disposal proof requires fresh cleanup")
    owner._audit_registration(absent=True)
    if owner._registration_row()["id"] != record["runner_id"]:
        raise StateError("Original revoked runner identity changed")
    return {
        "runner_id": record["runner_id"],
        "run_id": context.run_id,
        "runner_vm": deepcopy(owner._runner_vm),
        "revocation_operation_id": record["operation_id"],
    }


def _admit_external_dependencies(selected, route_facts):
    """Check actual staged build/publisher choices before starting any runner."""
    entries = selected["entries"]
    invocation_path = selected["environment"]["DEVSECOPS_GIT_INVOCATION"]
    invocation = _json(entries[invocation_path]["content"])
    build_ref = invocation["configurations"]["build"]
    raw = entries[build_ref["path"]]["content"]
    if _sha(raw) != build_ref["sha256"]:
        raise StateError("Original staged build configuration changed")
    from .mission_operation import _configuration
    from devsecops.adapters.publisher_trust import _policy

    build = _configuration(raw)
    publisher = build["publisher"]
    if publisher["policy"] not in entries:
        raise StateError("Publisher policy must be the exact staged pinned material")
    raw = entries[publisher["policy"]]["content"]
    if _sha(raw) != publisher["policy_sha256"]:
        raise StateError("Staged publisher policy differs from approved bytes")
    policy = _policy(raw)
    required = {"https://registry1.dso.mil"}
    if policy["source"]["kind"] == "p1-current-document":
        required.add("https://p1docs.dso.mil")
    download_ref = build.get("blob_download_policy")
    if download_ref is not None:
        from . import runner_vm_recipe
        from devsecops.adapters.image_policy import _policy as image_policy

        if download_ref["path"] not in entries:
            raise StateError("Blob policy must be the original staged material")
        blob_policy = runner_vm_recipe.blob_policy_document(
            entries[download_ref["path"]]["content"], download_ref["sha256"]
        )
        image_ref = build["image"]
        if image_ref["policy"] not in entries:
            raise StateError("Storage route requires the exact staged image policy")
        image_raw = entries[image_ref["policy"]]["content"]
        if _sha(image_raw) != image_ref["policy_sha256"]:
            raise StateError("Staged image policy changed before storage admission")
        subject = image_policy(image_raw)["subject"]
        if (blob_policy["registry_origin"] != subject["origin"]
                or subject["repository"] not in blob_policy["repositories"]
                or route_facts.get("blob_download_policy_sha256") != download_ref["sha256"]):
            raise StateError("Storage route differs from the selected image or original policy identity")
        required.update(blob_policy["storage_origins"])
    elif "blob_download_policy_sha256" in route_facts:
        raise StateError("Storage route policy is absent from the actual staged build")
    size_ref = build.get("attestation_size_policy")
    if size_ref is not None:
        from devsecops.adapters.registry_attestation_policy import parse_attestation_size_policy
        from devsecops.adapters.image_policy import _policy as image_policy

        image_ref = build["image"]
        if size_ref["path"] not in entries or image_ref["policy"] not in entries:
            raise StateError("Attestation and image policies must be the original staged materials")
        size_raw = entries[size_ref["path"]]["content"]
        image_raw = entries[image_ref["policy"]]["content"]
        if _sha(size_raw) != size_ref["sha256"] or _sha(image_raw) != image_ref["policy_sha256"]:
            raise StateError("Staged attestation or image policy differs from its original pin")
        try:
            subject = image_policy(image_raw)["subject"]
            parse_attestation_size_policy(size_raw, size_ref["sha256"], origin=subject["origin"],
                repository=subject["repository"], subject_digest=subject["digest"])
        except ValueError:
            raise StateError("Staged attestation policy does not bind the exact selected image") from None
    if set(route_facts.get("external_origins", ())) != required:
        raise StateError("Runner external routes differ from the actual publisher/build dependency set")
    auth = build["registry_auth"]
    credentials = (
        [auth["token_env"]]
        if "token_env" in auth
        else [auth[name] for name in ("basic_username_env", "basic_password_env") if name in auth]
    )
    if (
        not credentials
        or "token_env" not in auth
        and len(credentials) != 2
        or any(name not in selected["secrets"] for name in credentials)
    ):
        raise StateError("Registry1 requires explicitly selected staged private credential references")
    trust = selected["service_trust"].get("external")
    if not trust or auth.get("ca_bundle") != trust["path"]:
        raise StateError("Registry1 requires the exact selected external CA material")
    return tuple(sorted(required))


class OwnedRunnerService:
    def __init__(self, driver, *, vm, services, routes):
        from .owned_linux import OwnedLinuxDriver
        from .owned_runner_vm import OwnedRunnerVM
        from .owned_services import OwnedServiceStack
        from .runner_vm_routes import OwnedRunnerVMRoutes

        if (
            type(driver) is not OwnedLinuxDriver
            or type(vm) is not OwnedRunnerVM
            or type(services) is not OwnedServiceStack
            or type(routes) is not OwnedRunnerVMRoutes
            or any(component.journal is not driver.journal for component in (vm, services, routes))
        ):
            raise StateError("Runner service requires the original fixed VM, routes and services")
        self.driver, self.journal, self.vm, self.services, self.routes = (
            driver,
            driver.journal,
            vm,
            services,
            routes,
        )
        self._pid, self._registration, self._plan = os.getpid(), None, None
        self._started, self._unknown, self._terminal = False, False, None
        self._runner_vm, self._runner, self._revocation = None, None, None
        self._observations, self._evidence, self._dispatches = ComponentObservations(), {}, []
        self._down_operation = None
        self._service_nonce = str(uuid4())
        vm.bind_runner_service(self)

    def _registration_row(self):
        if self._registration is None:
            raise StateError("Runner registration has not been bound")
        resources, ticket = self._registration
        rows = [row for handle, row in resources.runners if handle is ticket]
        if len(rows) != 1:
            raise StateError("Original runner registration ticket changed")
        return rows[0]

    def _audit_registration(self, *, absent=False):
        from .owned_gitlab_cleanup import OwnedGitLabResources

        if self._pid != os.getpid() or self._registration is None:
            raise StateError("Original runner service process required")
        resources, _ = self._registration
        if (
            type(resources) is not OwnedGitLabResources
            or resources.pid != os.getpid()
            or resources.owner.journal is not self.journal
            or resources.context.run_id != self.journal.state["run_id"]
        ):
            raise StateError("Original API runner registration owner changed")
        row = self._registration_row()
        if row["id"] != self._runner_id or row["description"] != self._description:
            raise StateError("Original API runner registration differs")
        if absent and row["state"] != "absent":
            raise StateError("Original runner registration revocation remains unobserved")
        if set(resources.events) != set(resources._event_refs):
            raise StateError("Original API registration evidence changed")
        for path, ref in resources._event_refs.items():
            raw = _read(path, ref["bytes"] + 1)
            if len(raw) != ref["bytes"] or _sha(raw) != ref["sha256"]:
                raise StateError("Original API runner registration evidence changed")

    def bind_registration(
        self, context, *, resources, ticket, runner_id, runner_token, description, install_plan
    ):
        self.driver._admit(context, context.deadline)
        self.journal.current_mutation(context.phase["id"], "up")
        if self._registration is not None or self._started:
            raise StateError("Runner service registration is one-shot")
        if (
            type(runner_id) is not int
            or runner_id <= 0
            or type(runner_token) is not str
            or not 20 <= len(runner_token) <= 8192
        ):
            raise StateError("Exact original runner registration token required")
        self._registration = (resources, ticket)
        self._runner_id, self._description = runner_id, description
        self._token, self._plan = runner_token, install_plan
        self._audit_registration()
        if self._registration_row()["state"] != "owned-present":
            raise StateError("Runner registration has not completed direct API readback")

    def _artifact(self, value):
        path = self.journal.run_dir / ("runner-service-" + uuid4().hex + ".json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        self._evidence[path] = _sha(_read(path, 2_000_000))
        return path

    def _audit_evidence(self):
        if self._pid != os.getpid():
            raise StateError("Runner service belongs to another process")
        for path, digest in self._evidence.items():
            if _sha(_read(path, 2_000_000)) != digest:
                raise StateError("Original runner service evidence changed")

    def _packet(self, context, action):
        endpoint = self.vm.endpoint(context)
        identity = _plain(endpoint["runner_vm"])
        if self._runner_vm is not None and identity != self._runner_vm:
            raise StateError("Original runner VM identity changed")
        self._runner_vm = identity
        operation = (
            self.journal.current_mutation(context.phase["id"], context.phase["operation"])
            if action in ("install", "down")
            else self.journal.current_mutation(context.phase["id"], context.phase["operation"])
        )
        packet = {
            "schema_version": 1,
            "action": action,
            "run_id": context.run_id,
            "operation_id": operation,
            "runner_vm": identity,
            "service_nonce": self._service_nonce,
            "timeout": min(300, max(0.001, context.deadline - time.monotonic() - 2)),
        }
        if action != "install":
            packet["expected_process"] = None if self._runner is None else deepcopy(self._runner["process"])
        return packet

    def _observe(self, context, action, packet, *, reconciliation_operation=None):
        self._audit_evidence()
        # Original intention is retained before transport, including lost replies.
        intent = self._artifact(
            {
                "kind": "owned-runner-service-intent",
                "action": action,
                "run_id": context.run_id,
                "operation_id": packet["operation_id"],
                "runner_vm": packet["runner_vm"],
                "runner_id": getattr(self, "_runner_id", None),
            }
        )
        try:
            result = self.vm.dispatch_runner(
                context, action=action, packet=packet, reconciliation_operation=reconciliation_operation
            )
            self.vm.require_dispatch(result, context, reconciliation_operation=reconciliation_operation)
            observed = _plain(result["observation"])
            if (
                observed.get("runner_vm") != self._runner_vm
                or type(observed.get("resources")) is not list
                or type(observed.get("unresolved_operations")) is not list
            ):
                raise StateError("Runner service response lacks its exact VM ownership")
            self._dispatches.append(result)
        except BaseException:
            self._unknown = True
            self._artifact(
                {
                    "kind": "owned-runner-service-unknown",
                    "action": action,
                    "run_id": context.run_id,
                    "operation_id": packet["operation_id"],
                    "mutation_effect": "unknown",
                }
            )
            raise
        self._unknown = bool(observed["unresolved_operations"])
        if self._runner is not None and action in ("down", "reconcile"):
            for key in ("runner_id", "runner_manager_system_identity"):
                if observed.get(key) != self._runner[key]:
                    self._unknown = True
                    raise StateError("Runner cleanup identity differs from original installed runner")
        report = {
            **observed,
            "kind": "owned-runner-service",
            "run_id": context.run_id,
            "identities": dict(context.identities),
            "operation_id": packet["operation_id"],
            "action": "up" if action == "install" else action,
            "transport_receipt": _plain(result["transport_receipt"]),
        }
        artifact = self._artifact(report)
        outcome = PhaseOutcome(
            "incomplete",
            "owned-runner-service-observed",
            (intent, artifact),
            mutation_effect=report["mutation_effect"],
        )
        self._observations.retain(outcome, report)
        if report.get("complete_cleanup") is True:
            self._terminal = outcome
        if action in ("install", "observe") and not self._unknown:
            if (
                report.get("runner_id") != self._runner_id
                or not isinstance(report.get("runner_manager_system_identity"), str)
                or report.get("service_ready") is not True
            ):
                self._unknown = True
                raise StateError("Original runner process has not been observed ready")
            candidate = {
                "runner_id": self._runner_id,
                "runner_manager_system_identity": report["runner_manager_system_identity"],
                "runner_vm": deepcopy(self._runner_vm),
                "process": deepcopy(report["process"]),
            }
            if self._runner is not None:
                if any(self._runner[key] != value for key, value in candidate.items()):
                    self._unknown = True
                    raise StateError("Original runner process identity cannot be replaced")
            else:
                self._runner = candidate
        return outcome

    def up(self, context):
        self.driver._admit(context, context.deadline)
        self._audit_registration()
        if self._started:
            raise StateError("Runner service install was already attempted")
        from .runner_install import require_runner_install

        selected = require_runner_install(self._plan, self.services, context)
        if selected.get("runner_profile") != "owned-runner-vm-v1":
            raise StateError("Dedicated guest requires the explicit VM runner installation profile")
        route_facts = self.routes.require_ready(context)
        _admit_external_dependencies(selected, route_facts)
        packet = self._packet(context, "install")
        packet["installation"] = {
            "runner_id": self._runner_id,
            "runner_token": self._token,
            "description": self._description,
            "origin": self._registration[0].scope.origin,
            "entries": {
                path: {
                    "content": base64.b64encode(row["content"]).decode(),
                    "sha256": _sha(row["content"]),
                    "private": row["private"],
                }
                for path, row in selected["entries"].items()
            },
            "environment": selected["environment"],
            "secrets": selected["secrets"],
            "service_trust": selected["service_trust"],
            "guest_package": selected["guest_package"],
            "guest_paths": selected["guest_paths"],
            "route_trust": _plain(route_facts["route_trust"]),
        }
        self._started = True
        outcome = self._observe(context, "install", packet)
        self._token = None
        self._runner.update(
            runner_input_config_sha256=selected["config_sha256"],
            original_template_sha256=selected["original_template_sha256"],
        )
        return outcome

    def runner(self, context):
        self.driver._admit(context, context.deadline)
        self._audit_registration()
        self._audit_evidence()
        if self._runner is None or self._unknown or self._terminal is not None:
            raise StateError("Original dedicated runner is not ready")
        return deepcopy(self._runner)

    def execution_origin(self, context):
        self.driver._admit(context, context.deadline)
        self.routes.require_ready(context)
        self._audit_registration()
        if self._unknown or self._terminal is not None or not self._started:
            raise StateError("Original dedicated runner cannot admit CI")
        previous = self.runner(context)
        self._observe(context, "observe", self._packet(context, "observe"))
        current = self.runner(context)
        if any(
            current[key] != previous[key]
            for key in ("runner_id", "runner_manager_system_identity", "runner_vm", "process")
        ):
            raise StateError("Original dedicated runner process identity changed before CI")
        value = object.__new__(RunnerExecutionOrigin)
        _ORIGINS[value] = {
            "pid": os.getpid(),
            "owner": self,
            "deadline": context.deadline,
            "facts": {
                "run_id": context.run_id,
                **current,
                "origin": self._registration[0].scope.origin,
                "project_ids": list(self._registration_row()["project_ids"]),
            },
        }
        return value

    def revocation_proof(self, context, *, reconciliation_operation=None):
        if reconciliation_operation is None:
            self.driver._admit(context, context.deadline)
        else:
            self._registration[0].owner._admit(context, "down", operation_id=reconciliation_operation)
        self._audit_registration(absent=True)
        if self._revocation is None:
            value = object.__new__(RunnerRegistrationRevoked)
            _REVOCATIONS[value] = {
                "pid": os.getpid(),
                "owner": self,
                "runner_id": self._runner_id,
                "operation_id": self._registration[0]._down_operation,
            }
            self._revocation = value
        return self._revocation

    def down(self, context):
        self.driver._admit(context, context.deadline)
        if self._down_operation is not None:
            raise StateError("Runner service cleanup already attempted; reconcile first")
        self._audit_registration(absent=True)
        packet = self._packet(context, "down")
        self._down_operation = packet["operation_id"]
        return self._observe(context, "down", packet)

    def reconcile(self, context, operation_id):
        if self._down_operation != operation_id:
            raise StateError("Runner service reconciliation requires the original operation")
        # VM admission validates this exact unknown journal operation without a
        # normal driver admission or a repeated effect.
        packet = {
            "schema_version": 1,
            "action": "reconcile",
            "run_id": context.run_id,
            "operation_id": operation_id,
            "runner_vm": deepcopy(self._runner_vm),
            "service_nonce": self._service_nonce,
            "timeout": min(300, max(0.001, context.deadline - time.monotonic() - 2)),
            "expected_process": None if self._runner is None else deepcopy(self._runner["process"]),
        }
        return self._observe(context, "reconcile", packet, reconciliation_operation=operation_id)

    def accept_guest_cessation(self, proof, context, *, reconciliation_operation=None):
        from .owned_runner_vm import require_guest_kernel_cessation

        kwargs = (
            {"reconciliation_operation": reconciliation_operation}
            if reconciliation_operation is not None
            else {}
        )
        binding = _plain(require_guest_kernel_cessation(proof, vm=self.vm, context=context, **kwargs))
        if binding["runner_vm"] != self._runner_vm or binding["ci_effects"] != "not-reconciled":
            raise StateError("Original guest cessation identity differs")
        self._audit_registration(absent=True)
        self._audit_evidence()
        report = {
            "kind": "owned-runner-service",
            "run_id": context.run_id,
            "identities": dict(context.identities),
            "operation_id": binding["operation_id"],
            "action": "reconcile" if reconciliation_operation is not None else "down",
            "observation_effect": "not_applied" if reconciliation_operation is not None else "applied",
            "runner_vm": deepcopy(self._runner_vm),
            "runner_id": self._runner_id,
            "runner_manager_system_identity": None
            if self._runner is None
            else self._runner["runner_manager_system_identity"],
            "process": None if self._runner is None else deepcopy(self._runner["process"]),
            "complete_cleanup": True,
            "mutation_effect": "applied",
            "unresolved_operations": [],
            "service_ready": False,
            "cleanup_basis": "original-guest-kernel-and-disk-cessation",
            "prior_guest_effects": "not-reconciled",
            "ci_effects": "not-reconciled",
            "resources": [
                {
                    "kind": "owned-guest-service-and-private-material",
                    "identity": {"runner_vm": deepcopy(self._runner_vm)},
                    "disposition": "remove",
                    "state": "absent",
                }
            ],
        }
        artifact = self._artifact(report)
        outcome = PhaseOutcome(
            "incomplete",
            "runner-service-parent-cessation",
            tuple(dict.fromkeys((artifact, *self.vm.guest_cessation_artifacts(proof)))),
            mutation_effect="applied",
        )
        self._terminal = self._observations.retain(outcome, report)
        self._unknown = False
        return outcome

    def vm_cleanup_ready(self, context):
        if self._terminal is None:
            return False
        report = self.cleanup_observation(self._terminal)
        return (
            report["runner_vm"] == self._runner_vm
            and report.get("complete_cleanup") is True
            and report.get("unresolved_operations") == []
            and bool(report["resources"])
            and all(row.get("disposition") in ("remove", "preserve") for row in report["resources"])
            and all(
                row.get("state") == ("absent" if row["disposition"] == "remove" else "preserved")
                for row in report["resources"]
            )
        )

    def cleanup_observation(self, outcome):
        self._audit_evidence()
        return self._observations.observe(outcome)

    def evidence_artifacts(self, context):
        if context.run_id != self.journal.state["run_id"]:
            raise StateError("Runner service evidence belongs to another run")
        self._audit_evidence()
        return tuple(self._evidence)
