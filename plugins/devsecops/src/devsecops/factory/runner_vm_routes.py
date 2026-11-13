"""Original separate runner-VM route owner; observations cannot restore authority."""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from types import MappingProxyType
from uuid import uuid4
from weakref import WeakKeyDictionary

from . import runner_vm_guest_routes, runner_vm_recipe
from .cleanup_observation import ComponentObservations
from .lifecycle import PhaseOutcome
from .owned_linux import OwnedLinuxDriver, _plain
from .owned_registry import OwnedOCIRegistry
from .owned_runner_vm import OwnedRunnerVM, require_guest_kernel_cessation
from .owned_services import OwnedServiceStack
from .state import StateError, _read, _sha

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_ORIGINS = WeakKeyDictionary()


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({name: _freeze(child) for name, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    return value


class RunnerVMRouteOrigin:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Runner routes require a direct original VM observation")

    def __reduce__(self):
        raise TypeError("Runner route origins cannot be serialized")


def require_runner_vm_routes(value, *, vm, context):
    if type(value) is not RunnerVMRouteOrigin or value not in _ORIGINS:
        raise StateError("Only original resident runner routes may be consumed")
    owner, dispatch = _ORIGINS[value]
    if owner.vm is not vm or owner._ready_dispatch is not dispatch:
        raise StateError("Runner route origin belongs to another or superseded VM observation")
    operation, _, _ = owner._admit(context)
    if (
        dispatch["transport_receipt"]["operation_id"] != operation
        or owner._ready_at is None
        or not 0 <= time.monotonic() - owner._ready_at <= 30
    ):
        raise StateError("Original runner route readiness belongs to another or expired observation")
    if datetime.now(timezone.utc) >= datetime.fromisoformat(
        context.inputs["config"]["expires_at"].replace("Z", "+00:00")
    ):
        raise StateError("Original runner route readiness expired")
    vm.require_dispatch(dispatch, context)
    owner._targets(context)
    return _freeze(owner._ready_facts(dispatch))


class OwnedRunnerVMRoutes:
    def __init__(self, driver, *, vm, registry, services):
        if (
            type(driver) is not OwnedLinuxDriver
            or type(vm) is not OwnedRunnerVM
            or type(registry) is not OwnedOCIRegistry
            or type(services) is not OwnedServiceStack
            or any(owner.journal is not driver.journal for owner in (vm, registry, services))
        ):
            raise StateError("Runner routes require exact original owners in one journal")
        self.driver, self.journal, self.vm = driver, driver.journal, vm
        self.registry, self.services = registry, services
        self._pid = os.getpid()
        self._run_id, self._identities = self.journal.state["run_id"], dict(self.journal.identities)
        self._selected = self._binding = self._runner_vm = None
        self._attempted = self._unknown = self._down = False
        self._ready_dispatch = self._terminal = self._host_terminal = None
        self._ready_at = None
        self._dispatches, self._outcomes, self._artifacts = [], [], {}
        self._observations = ComponentObservations()
        self._cessation = None
        self._disposal_bases = {}
        self._host_disposal_operation = None
        self._external_ca = None
        vm.bind_routes(self)

    def __reduce__(self):
        raise TypeError("Runner route ownership cannot be serialized")

    def _admit(self, context, *, action=None, reconciliation_operation=None):
        if (
            self._pid != os.getpid()
            or context.run_id != self._run_id
            or dict(context.identities) != self._identities
        ):
            raise StateError("Runner route owner belongs to another process/run")
        operation, recipe, modules, _ = self.vm._admit(
            context,
            action="down" if action in {"down", "down-host"} else "up" if action == "up" else None,
            reconciliation_operation=reconciliation_operation,
        )
        for path, pin in self._artifacts.items():
            if _sha(_read(path, 2_000_000)) != pin:
                raise StateError("Original runner route evidence changed")
        if self._unknown and reconciliation_operation is None and operation != self._host_disposal_operation:
            raise StateError("Unknown runner route action requires its original reconciliation")
        if self._down and action == "up":
            raise StateError("Disposed runner routes cannot be restarted")
        return operation, recipe, modules

    def _targets(self, context, *, reconciliation_operation=None):
        selected = {}
        for role, owner, resource in (
            ("gitlab", self.services, "server"),
            ("registry", self.registry, "registry"),
        ):
            observed = _plain(
                owner.service_endpoint(context, reconciliation_operation=reconciliation_operation)
            )
            if observed["run_id"] != self._run_id or observed["identities"] != self._identities:
                raise StateError("Runner service endpoint belongs to another original run")
            raw = _read(Path(observed["ca_path"]), 100000)
            if _sha(raw) != observed["ca_sha256"]:
                raise StateError("Runner service public trust changed")
            selected[role] = {
                "service": {
                    "origin": observed["origin"],
                    "address": runner_vm_guest_routes.ROLE[role][0],
                    "port": observed["port"],
                    "ca_b64": base64.b64encode(raw).decode(),
                    "ca_sha256": _sha(raw),
                },
                "target": {
                    "container": observed[resource],
                    "network": observed["network"],
                    "origin": observed["origin"],
                    "port": observed["port"],
                },
                "owner_binding": observed["binding"],
                "ca_path": observed["ca_path"],
            }
        if self._selected is not None and selected != self._selected:
            raise StateError("Runner route original owned targets changed")
        return selected

    def _packet(self, context, action, reconciliation_operation=None):
        operation, recipe, modules = self._admit(
            context, action=action, reconciliation_operation=reconciliation_operation
        )
        if action in {"down-host", "reconcile-host"}:
            # This explicit branch must remain usable when guest SSH is lost.
            # The VM owner independently audits its original host/disk/outer
            # identities before dispatch; a saved endpoint cannot grant one.
            if self._runner_vm is None:
                raise StateError("Host cleanup needs the original observed VM identity")
            endpoint = _plain(
                self.vm.host_endpoint(context, reconciliation_operation=reconciliation_operation)
            )
            runner = endpoint["runner_vm"]
        else:
            endpoint = _plain(self.vm.endpoint(context, reconciliation_operation=reconciliation_operation))
            runner = endpoint["runner_vm"]
        if self._runner_vm is not None and runner != self._runner_vm:
            raise StateError("Runner routes changed VM generation, boot, disk or SSH identity")
        selected = self._targets(context, reconciliation_operation=reconciliation_operation)
        services = {role: row["service"] for role, row in selected.items()}
        external = recipe["egress"]["external_origins"]
        if external:
            ca = recipe["egress"]["external_ca_bundle"]
            actual_ca = endpoint.get("external_ca")
            if type(actual_ca) is not dict or set(actual_ca) != {"sha256", "bytes", "ca_b64"}:
                raise StateError("Original remote VM public CA observation is unavailable")
            raw = base64.b64decode(actual_ca["ca_b64"], validate=True)
            if (
                _sha(raw) != ca["sha256"]
                or actual_ca["sha256"] != ca["sha256"]
                or actual_ca["bytes"] != len(raw)
            ):
                raise StateError("Original explicit external-route CA selection changed")
            for role, origin in runner_vm_guest_routes.EXTERNAL.items():
                if origin in external:
                    address, port = runner_vm_guest_routes.ROLE[role]
                    services[role] = {
                        "origin": origin,
                        "address": address,
                        "port": port,
                        "ca_b64": base64.b64encode(raw).decode(),
                        "ca_sha256": _sha(raw),
                    }
        source = _read(PACKAGE_ROOT / "src/devsecops/factory/runner_vm_guest_routes.py", 131072)
        if _sha(source) != modules["runner_vm_guest_routes"]:
            raise StateError("Original guest route source differs from installed package")
        binding = _sha(
            runner_vm_guest_routes._bytes(
                {
                    "runner_vm": runner,
                    "selected": selected,
                    "services": services,
                    "identities": self._identities,
                    "modules": {
                        name: modules[name]
                        for name in ("runner_vm_routes", "runner_vm_routes_remote", "runner_vm_guest_routes")
                    },
                }
            )
        )
        if self._binding is not None and self._binding != binding:
            raise StateError("Original runner route/source/trust binding changed")
        packet = {
            "schema_version": 1,
            "action": {"down-host": "down", "reconcile-host": "reconcile"}.get(action, action),
            "run_id": self._run_id,
            "operation_id": operation,
            "binding": binding,
            "runner_vm": runner,
            "expires_at": context.inputs["config"]["expires_at"],
            "timeout": min(300, context.deadline - time.monotonic() - 5),
            "services": services,
        }
        policy_ref = recipe["egress"].get("blob_download_policy")
        if policy_ref is not None:
            policy_raw = _read(Path(policy_ref["path"]), 16384)
            runner_vm_recipe.blob_policy_document(policy_raw, policy_ref["sha256"])
            packet["blob_download_policy"] = {
                "sha256": policy_ref["sha256"], "document_b64": base64.b64encode(policy_raw).decode("ascii")
            }
        runner_vm_guest_routes.validate_packet(packet)
        if self._binding is None:
            self._binding, self._runner_vm, self._selected = binding, deepcopy(runner), deepcopy(selected)
        return {
            "action": action,
            "guest": packet,
            "targets": {role: row["target"] for role, row in selected.items()},
            "guest_source_sha256": _sha(source),
        }

    def _artifact(self, value):
        path = self.journal.run_dir / ("runner-routes-" + uuid4().hex + ".json")
        raw = runner_vm_guest_routes._bytes(value)
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self._artifacts[path] = _sha(raw)
        return path

    def _report(self, context, action, original, observed, intent):
        public = _plain(observed)
        if (
            public.get("binding") != self._binding
            or public.get("runner_vm") != self._runner_vm
            or public.get("operation_id") != original["transport_receipt"]["operation_id"]
            or type(public.get("unresolved")) is not list
        ):
            raise StateError("Runner route response lacks its original operation identity")
        self._unknown = bool(public["unresolved"])
        resources = [
            {
                "kind": "runner-vm-host-relay",
                "identity": {"runner_vm": self._runner_vm, "origin": row["service"]["origin"]},
                "disposition": "remove",
                "state": "absent" if public.get("host_cleanup") is True else "present",
            }
            for row in self._selected.values()
        ]
        for origin in self.vm._selected["egress"]["external_origins"]:
            resources.append(
                {
                    "kind": "runner-vm-external-relay",
                    "identity": {"runner_vm": self._runner_vm, "origin": origin},
                    "disposition": "remove",
                    "state": "absent" if public.get("host_cleanup") is True else "present",
                }
            )
        resources.append(
            {
                "kind": "runner-vm-private-trust",
                "identity": {"runner_vm": self._runner_vm},
                "disposition": "remove",
                "state": "absent" if public.get("guest_cleanup") is True else "present",
            }
        )
        report = {
            **public,
            "kind": "owned-runner-vm-routes",
            "run_id": self._run_id,
            "identities": self._identities,
            "action": action,
            "resources": resources,
            "unresolved_operations": public["unresolved"],
            "cleanup_eligible": action == "reconcile" and not self._unknown,
            "transport_receipt": _plain(original["transport_receipt"]),
            "canonical_success": False,
        }
        artifact = self._artifact(report)
        transport = Path(original["transport_receipt"]["artifact"])
        if _sha(_read(transport, 2_000_000)) != original["transport_receipt"]["sha256"]:
            raise StateError("Original route transport evidence changed before retention")
        outcome = PhaseOutcome(
            "incomplete",
            "owned-runner-vm-routes-observed",
            (intent, transport, artifact),
            mutation_effect=(
                "not_applied"
                if action in {"observe", "reconcile", "reconcile-host"}
                else "unknown"
                if self._unknown
                else public["mutation_effect"]
            ),
        )
        self._observations.retain(outcome, report)
        self._outcomes.append(outcome)
        if public.get("host_cleanup") is True and not public.get("host_unresolved", ["missing"]):
            self._host_terminal = outcome
        if public.get("complete_cleanup") is True and not self._unknown:
            self._terminal = outcome
        if action in {"up", "observe"} and public.get("ready") is True and not self._unknown:
            self._ready_dispatch = original
            self._ready_at = time.monotonic()
        return outcome

    def _perform(self, context, action, operation_id=None):
        if action in {"observe", "reconcile", "reconcile-host", "down", "down-host"}:
            self._ready_dispatch = None
        packet = self._packet(context, action, operation_id)
        if action == "up" and self._attempted:
            raise StateError("Original runner routes have already been attempted")
        intent = self._artifact(
            {
                "kind": "owned-runner-vm-routes-intent",
                "run_id": self._run_id,
                "operation_id": packet["guest"]["operation_id"],
                "binding": self._binding,
                "action": action,
                "runner_vm": self._runner_vm,
                "origins": [r["origin"] for r in packet["guest"]["services"].values()],
            }
        )
        if action == "up":
            self._attempted = True
        if action in {"down", "down-host"}:
            self._down = True
            self._ready_dispatch = None
        try:
            original = self.vm.dispatch_routes(
                context, action=action, packet=packet, reconciliation_operation=operation_id
            )
            self.vm.require_dispatch(original, context, reconciliation_operation=operation_id)
            self._dispatches.append(original)
            return self._report(context, action, original, original["observation"], intent)
        except BaseException:
            self._unknown = True
            self._artifact(
                {
                    "kind": "owned-runner-vm-routes-unknown",
                    "run_id": self._run_id,
                    "binding": self._binding,
                    "action": action,
                    "mutation_effect": "unknown",
                }
            )
            raise

    def up(self, context):
        return self._perform(context, "up")

    configure = up

    def down(self, context):
        if not self._attempted:
            return self._no_effects(context)
        return self._perform(context, "down")

    def down_host(self, context):
        raise StateError("Host-only cleanup requires the explicit original disposal continuation")

    def reconcile(self, context, operation_id):
        if not self._attempted:
            return self._no_effects(context, operation_id)
        return self._perform(context, "reconcile", operation_id)

    def _no_effects(self, context, operation_id=None):
        operation, _, _ = self._admit(
            context, action="down" if operation_id is None else None, reconciliation_operation=operation_id
        )
        if self._attempted or self._dispatches or self.vm._child_attempts["routes"]:
            raise StateError("Route no-effects closure requires the original never-dispatched ledger")
        report = {
            "kind": "owned-runner-vm-routes",
            "action": "down" if operation_id is None else "reconcile",
            "run_id": self._run_id,
            "operation_id": operation,
            "identities": self._identities,
            "runner_vm": deepcopy(self._runner_vm),
            "complete_cleanup": True,
            "host_cleanup": True,
            "guest_cleanup": True,
            "never_dispatched": True,
            "mutation_effect": "not_applied",
            "cleanup_eligible": True,
            "host_unresolved": [],
            "guest_unresolved": [],
            "unresolved": [],
            "unresolved_operations": [],
            "resources": [
                {
                    "kind": "original-runner-route-inventory",
                    "identity": {"run_id": self._run_id, "identities": self._identities},
                    "disposition": "remove",
                    "state": "absent",
                    "basis": "original-resident-no-dispatch",
                }
            ],
        }
        artifact = self._artifact(report)
        outcome = PhaseOutcome(
            "incomplete",
            "original-runner-routes-never-dispatched",
            (artifact,),
            mutation_effect="not_applied",
        )
        self._observations.retain(outcome, report)
        self._outcomes.append(outcome)
        self._down = True
        self._terminal = self._host_terminal = outcome
        return outcome

    def prepare_disposal(self, context, operation_id):
        outcome = self._perform(context, "reconcile-host", operation_id)
        report = self.cleanup_observation(outcome)
        if report.get("host_disposal_eligible") is True and not report.get("host_unresolved"):
            self._disposal_bases[operation_id] = outcome
        return outcome

    def prepare_host_disposal(self, context, operation_id):
        basis = self._disposal_bases.get(operation_id)
        if basis is None:
            raise StateError("Original host disposal observation is unavailable")
        report = self.cleanup_observation(basis)
        mutation = self.journal.state["active_mutation"]
        if (
            report.get("host_disposal_eligible") is not True
            or report.get("host_unresolved")
            or report.get("runner_vm") != self._runner_vm
            or mutation is None
            or mutation.get("action") != "down"
            or mutation.get("operation_id") == operation_id
            or mutation.get("cleanup_mode") != "dispose-owned-runner-guest"
            or mutation.get("continues_operation") != operation_id
        ):
            raise StateError("Only a fresh explicit disposal continuation may remove original host routes")
        self._host_disposal_operation = mutation["operation_id"]
        return self._perform(context, "down-host")

    def _ready_facts(self, original):
        public = _plain(original["observation"])
        if self._down or self._unknown or public.get("ready") is not True or public.get("unresolved"):
            raise StateError("Original runner routes are not currently ready")
        guest = public["guest"]
        trust = Path(guest["trust_directory"])
        files = {name: {**row, "path": str(trust / name)} for name, row in guest["files"].items()}
        route_trust = {
            "files": files,
            "environment": deepcopy(guest["environment"]),
            "empty_directory": {"path": str(trust / "empty-ca-directory"), **guest["empty_identity"]},
        }
        facts = {
            "binding": self._binding,
            "runner_vm": deepcopy(self._runner_vm),
            "external_origins": list(self.vm._selected["egress"]["external_origins"]),
            "trust_files": files,
            "trust_directory": str(trust),
            "route_trust": route_trust,
            "verification": deepcopy(guest["verification"]),
            "relay": deepcopy(public["relay"]),
            "transport_receipt": _plain(original["transport_receipt"]),
        }
        policy_ref = self.vm._selected["egress"].get("blob_download_policy")
        if policy_ref is not None:
            facts["blob_download_policy_sha256"] = policy_ref["sha256"]
        return facts

    def origin(self, context):
        self._perform(context, "observe")
        if self._ready_dispatch is None:
            raise StateError("Original live runner route readiness is unavailable")
        value = object.__new__(RunnerVMRouteOrigin)
        _ORIGINS[value] = (self, self._ready_dispatch)
        return value

    def require_ready(self, context):
        return require_runner_vm_routes(self.origin(context), vm=self.vm, context=context)

    def cleanup_observation(self, outcome):
        return self._observations.observe(outcome)

    def evidence_artifacts(self, context, *, reconciliation_operation=None):
        # Export original evidence after a later phase or completed cleanup;
        # historical bytes grant neither readiness nor a new mutation.
        if (
            self._pid != os.getpid()
            or context.run_id != self._run_id
            or dict(context.identities) != self._identities
            or self.journal.state["run_id"] != self._run_id
            or dict(self.journal.identities) != self._identities
        ):
            raise StateError("Original runner route evidence belongs to another process/run")
        for path, pin in self._artifacts.items():
            if _sha(_read(path, 2_000_000)) != pin:
                raise StateError("Original runner route evidence changed")
        paths = list(self._artifacts)
        for outcome in self._outcomes:
            self.cleanup_observation(outcome)
            paths.extend(outcome.artifacts)
        return tuple(dict.fromkeys(paths))

    def host_cleanup_ready(self, context, *, reconciliation_operation=None):
        self._admit(context, action="down", reconciliation_operation=reconciliation_operation)
        if not self._attempted:
            return True
        if self._host_terminal is None:
            return False
        value = self.cleanup_observation(self._host_terminal)
        return (
            value["host_cleanup"] is True
            and value["runner_vm"] == self._runner_vm
            and not value.get("host_unresolved", ["missing"])
        )

    def vm_cleanup_ready(self, context):
        self._admit(context, action="down")
        if not self._attempted:
            return True
        if self._terminal is None:
            return False
        value = self.cleanup_observation(self._terminal)
        return (
            value["complete_cleanup"] is True
            and value["runner_vm"] == self._runner_vm
            and not value["unresolved_operations"]
        )

    def accept_guest_cessation(self, proof, context, *, reconciliation_operation=None):
        current = _plain(
            require_guest_kernel_cessation(
                proof,
                vm=self.vm,
                context=context,
                **(
                    {"reconciliation_operation": reconciliation_operation}
                    if reconciliation_operation is not None
                    else {}
                ),
            )
        )
        if (
            not self.host_cleanup_ready(context, reconciliation_operation=reconciliation_operation)
            or current["runner_vm"] != self._runner_vm
            or current["guest_kernel"] != "ceased"
            or current["scope"] != "guest-process-and-private-disk-only"
        ):
            raise StateError("Guest cessation cannot replace original host relay cleanup")
        original = self.cleanup_observation(self._host_terminal)
        report = {
            **original,
            "action": "down",
            "operation_id": current["operation_id"],
            "guest_cleanup": True,
            "complete_cleanup": True,
            "ready": False,
            "guest_unresolved": [],
            "host_unresolved": [],
            "unresolved": [],
            "unresolved_operations": [],
            "guest_cessation_basis": current,
            "mutation_effect": "applied",
            "observation_effect": "not_applied" if reconciliation_operation is not None else "applied",
            "resources": [{**r, "state": "absent"} for r in original["resources"]],
        }
        artifact = self._artifact(report)
        artifacts = tuple(
            dict.fromkeys(
                (*self._host_terminal.artifacts, *self.vm.guest_cessation_artifacts(proof), artifact)
            )
        )
        outcome = PhaseOutcome(
            "incomplete",
            "runner-route-guest-disposal-observed",
            artifacts,
            mutation_effect=report["mutation_effect"],
        )
        self._observations.retain(outcome, report)
        self._outcomes.append(outcome)
        self._terminal, self._cessation = outcome, proof
        self._unknown = False
        return outcome
