"""Original resident ownership of the separate, disposable nonroot runner VM."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import importlib
import math
import os
from pathlib import Path
import shlex
import time
from types import MappingProxyType
from uuid import uuid4
from weakref import WeakKeyDictionary

from . import bootstrap_recipe, runner_vm_recipe, tcg
from .cleanup_observation import ComponentObservations
from .lifecycle import PhaseContext, PhaseOutcome, _load_inputs
from .native_codex import _package
from .owned_linux import OwnedLinuxDriver, _plain
from .owned_service_links import _endpoint_context, _mutation_action
from .state import RunJournal, StateError, _json, _read, _safe_path, _sha

PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def _admit_blob_download_policy(recipe, auxiliary_sources):
    """Bind requested egress to the actual immutable build before VM dispatch."""
    selected = recipe["egress"].get("blob_download_policy")
    invocation_ref = (auxiliary_sources or {}).get("mission_git_invocation")
    if invocation_ref is None:
        if selected is not None:
            raise StateError("Storage egress requires the actual selected mission build")
        return
    from .mission_git_invocation import invocation_configuration
    from .mission_operation import _configuration
    from devsecops.adapters.image_policy import _policy

    invocation_raw = _read(_safe_path(Path(invocation_ref[0])), 64000)
    if _sha(invocation_raw) != invocation_ref[1]:
        raise StateError("Mission invocation changed before runner VM dispatch")
    build_ref = invocation_configuration(invocation_raw)["configurations"]["build"]
    build_raw = _read(_safe_path(Path(build_ref["path"])), 64000)
    if _sha(build_raw) != build_ref["sha256"]:
        raise StateError("Mission build changed before runner VM dispatch")
    build = _configuration(build_raw)
    if build.get("blob_download_policy") != selected:
        raise StateError("Runner VM storage egress and build policy differ")
    size_ref = build.get("attestation_size_policy")
    if selected is not None or size_ref is not None:
        image = build["image"]
        raw = _read(_safe_path(Path(image["policy"])), 64000)
        if _sha(raw) != image["policy_sha256"]:
            raise StateError("Image policy changed before runner VM dispatch")
        subject = _policy(raw)["subject"]
    if selected is not None:
        raw = _read(_safe_path(Path(selected["path"])), 16384)
        policy = runner_vm_recipe.blob_policy_document(raw, selected["sha256"])
        if policy["registry_origin"] != subject["origin"] or subject["repository"] not in policy["repositories"]:
            raise StateError("Runner VM storage scope differs from the actual selected image")
    if size_ref is not None:
        from devsecops.adapters.registry_attestation_policy import parse_attestation_size_policy

        raw = _read(_safe_path(Path(size_ref["path"])), 64000)
        try:
            parse_attestation_size_policy(raw, size_ref["sha256"], origin=subject["origin"],
                repository=subject["repository"], subject_digest=subject["digest"])
        except ValueError:
            raise StateError("Attestation policy changed or mismatches the image before runner VM dispatch") from None


MODULES = (
    "owned_runner_vm",
    "runner_vm_recipe",
    "runner_vm_remote",
    "tcg",
    "registry_remote",
    "owned_runner_service",
    "runner_service_guest",
    "runner_vm_routes",
    "runner_vm_routes_remote",
    "runner_vm_guest_routes",
    "cleanup_observation",
    "bootstrap_recipe",
    "qemu_acceleration",
)
BOOTSTRAP = """import base64,json,sys,types
p=json.loads(sys.stdin.buffer.read(3000001))
def load(name,field):
 m=types.ModuleType(name);sys.modules[name]=m
 raw=base64.b64decode(p.pop(field),validate=True)
 exec(compile(raw,name,'exec'),m.__dict__);m.SOURCE_BYTES=raw
 return m
try:
 s=load('runner_vm_support','support_source');r=load('runner_vm_recipe','recipe_source')
 c=load('runner_vm_purge','purge_source');d=load('runner_vm_driver','driver_source')
 a=load('devsecops_qemu_acceleration','acceleration_source')
 j=load('runner_service_guest','runner_source');guest_raw=p['guest_routes_source']
 g=load('runner_vm_guest_routes','guest_routes_source')
 n=load('runner_vm_routes_remote','routes_source')
 j.CONTEXT_TYPE=d.RunnerVMRemoteContext
 n.CONTEXT_TYPE=d.RunnerVMRemoteContext
 n.GUEST_SOURCE_BYTES=base64.b64decode(guest_raw,validate=True)
 result=d.execute(p,s,r,c,runner=j,routes=n,accelerator=a)
 print(json.dumps({'binding':p['binding'],'operation_id':p['operation_id'],'observation':result},sort_keys=True))
except Exception:
 print('{"error":"runner-vm-operation-unobserved-reconciliation-required"}')
 sys.exit(2)
"""
_CESSATIONS = WeakKeyDictionary()
_DISPOSALS = WeakKeyDictionary()


class RunnerVMDisposalBasis:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Parent disposal basis requires original read-only reconciliation")

    def __reduce__(self):
        raise TypeError("Parent disposal basis cannot be serialized")


class GuestKernelCessation:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Guest kernel cessation requires original parent disposal")

    def __reduce__(self):
        raise TypeError("Guest kernel cessation cannot be serialized")


def require_guest_kernel_cessation(value, *, vm, context, reconciliation_operation=None):
    if type(value) is not GuestKernelCessation or value not in _CESSATIONS:
        raise StateError("Original runner VM kernel cessation proof required")
    owner, outcome, operation, revocation, read_operation = _CESSATIONS[value]
    if reconciliation_operation != read_operation:
        raise StateError("Guest cessation observation requires its original action admission mode")
    current, _, _, _ = vm._admit(context, action="down", reconciliation_operation=reconciliation_operation)
    if owner is not vm or current != operation:
        raise StateError("Guest kernel cessation differs from current original down")
    from .owned_runner_service import require_runner_revocation

    require_runner_revocation(
        revocation, vm=vm, context=context, reconciliation_operation=reconciliation_operation
    )
    report = vm.cleanup_observation(outcome)
    if not vm._complete(report) or report.get("runner_vm") != vm._origin:
        raise StateError("Guest kernel cessation lost its original parent absence evidence")
    return _freeze(
        {
            "runner_vm": vm._origin,
            "operation_id": operation,
            "guest_kernel": "ceased",
            "scope": "guest-process-and-private-disk-only",
            "ci_effects": "not-reconciled",
        }
    )


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    return value


@dataclass(frozen=True)
class _Transport:
    transport: dict


class OwnedRunnerVM:
    def __init__(self, driver, *, foundation, services):
        from .owned_fresh_foundation import OwnedFreshRKE2Foundation
        from .owned_services import OwnedServiceStack

        if (
            type(driver) is not OwnedLinuxDriver
            or type(driver.journal) is not RunJournal
            or type(foundation) is not OwnedFreshRKE2Foundation
            or type(services) is not OwnedServiceStack
            or services.driver is not driver
            or any(owner.journal is not driver.journal for owner in (foundation, services))
        ):
            raise StateError("Runner VM requires the exact original driver, fresh foundation and services")
        self.driver, self.journal, self.foundation, self.services = (
            driver,
            driver.journal,
            foundation,
            services,
        )
        self._pid, self._run_id, self._identities = (
            os.getpid(),
            self.journal.state["run_id"],
            dict(self.journal.identities),
        )
        self._selected = self._binding = self._remote_identity = self._origin = None
        self._source_assets = None
        self._up_operation = self._private = self._private_identity = None
        self._remote_attempted, self._unknown, self._absent = False, False, False
        self._observations, self._dispatches, self._evidence = ComponentObservations(), [], {}
        self._children = {"runner": None, "routes": None}
        self._child_attempts = {"runner": False, "routes": False}
        self._continuation = None
        self._disposal_attempt = None
        self._disposal_attempt_history = []
        self._last_unknown_operation = None
        self._last_unknown_action = None
        self._outcomes = []

    def __reduce__(self):
        raise TypeError("Runner VM ownership cannot be serialized")

    def bind_runner_service(self, component):
        from .owned_runner_service import OwnedRunnerService

        self._bind("runner", component, OwnedRunnerService)

    def bind_routes(self, component):
        from .runner_vm_routes import OwnedRunnerVMRoutes

        self._bind("routes", component, OwnedRunnerVMRoutes)

    def _bind(self, name, component, expected):
        if (
            type(component) is not expected
            or component.vm is not self
            or component.journal is not self.journal
            or self._children[name] is not None
            or self._child_attempts[name]
        ):
            raise StateError("Runner VM child requires its original fixed owner exactly once")
        self._children[name] = component

    def _admit(self, context, *, action=None, reconciliation_operation=None):
        if (
            self._pid != os.getpid()
            or type(context) is not PhaseContext
            or type(context.deadline) not in (float, int)
            or not math.isfinite(context.deadline)
            or context.run_id != self._run_id
            or dict(context.identities) != self._identities
        ):
            raise StateError("Runner VM belongs to another process or run")
        _endpoint_context(self.journal, context, reconciliation_operation=reconciliation_operation)
        if reconciliation_operation is None:
            self.driver._admit(context, context.deadline)
        else:
            self.driver._auxiliary_artifacts()
        self.driver._package(self._identities["package_sha256"], context.deadline)
        life = self.journal.state["lifecycle"]
        if life.get("driver_id") != "owned-linux":
            raise StateError("Runner VM requires its original owned Linux lifecycle")
        paths = {name: self.journal._artifact_path(ref["path"]) for name, ref in life["inputs"].items()}
        _, docs = _load_inputs(paths, self._identities, self.journal.state["environment_id"])
        if docs != _plain(context.inputs) or docs["config"]["profile"] != "bigbang-live":
            raise StateError("Runner VM frozen inputs differ")
        config = docs["config"]
        phase = _plain(context.phase)
        if action in ("up", "down"):
            if phase["operation"] != action or (
                action == "up" and "provision" not in config["allowed_mutations"]
            ):
                raise StateError("Runner VM mutation is outside its fixed phase")
        if "cleanup" not in config["allowed_mutations"]:
            raise StateError("Runner VM requires explicit owned cleanup scope")
        if action == "up" and datetime.now(timezone.utc) >= datetime.fromisoformat(
            config["expires_at"].replace("Z", "+00:00")
        ):
            raise StateError("Runner VM provisioning authorization expired")
        operation = self.journal.current_mutation(
            phase["id"], _mutation_action(phase), reconciliation=reconciliation_operation is not None
        )
        if reconciliation_operation is not None and operation != reconciliation_operation:
            raise StateError("Runner VM reconciliation differs from its existing operation")
        raw = _read(_safe_path(self.services.recipe_path), 256000)
        original = (self.driver.auxiliary_sources or {}).get("owned_bootstrap_recipe")
        if (
            type(original) is not tuple
            or len(original) != 2
            or Path(original[0]) != self.services.recipe_path
            or original[1] != self.services.recipe_sha256
            or _sha(raw) != self.services.recipe_sha256
        ):
            raise StateError("Runner VM original bootstrap recipe changed")
        bootstrap = bootstrap_recipe.validate(_json(raw))
        if bootstrap["environment"] != self.journal.state["environment_id"]:
            raise StateError("Runner VM selected bootstrap belongs to another environment")
        ref = bootstrap["services"].get("runner_vm")
        if type(ref) is not dict:
            raise StateError("Dedicated runner VM recipe is not selected")
        raw = _read(_safe_path(Path(ref["path"])), 256000)
        if _sha(raw) != ref["sha256"]:
            raise StateError("Runner VM recipe pin changed")
        recipe = runner_vm_recipe.validate(_json(raw))
        runner_vm_recipe.validate_against_foundation(recipe, config["runtime"]["foundation"])
        _admit_blob_download_policy(recipe, self.driver.auxiliary_sources)
        policy_ref = recipe["egress"].get("blob_download_policy")
        if policy_ref is not None:
            policy_raw = _read(_safe_path(Path(policy_ref["path"])), 16384)
            runner_vm_recipe.blob_policy_document(policy_raw, policy_ref["sha256"])
        if self._selected is not None and recipe != self._selected:
            raise StateError("Runner VM original selection changed")
        _, members = _package(
            PACKAGE_ROOT,
            self._identities["package_sha256"],
            self.driver.plugin_id,
            self.driver.plugin_version,
            context.deadline,
        )
        hashes = {}
        for name in MODULES:
            path = PACKAGE_ROOT / "src/devsecops/factory" / (name + ".py")
            module = importlib.import_module("devsecops.factory." + name)
            if Path(module.__file__).resolve() != path:
                raise StateError("Runner VM implementation was imported outside its installed package")
            hashes[name] = _sha(_read(path, 500000))
            if members.get(str(path.relative_to(PACKAGE_ROOT))) != hashes[name]:
                raise StateError("Runner VM implementation differs from its installed package")
        binding = runner_vm_recipe.binding(
            recipe,
            run_id=self._run_id,
            environment_id=self.journal.state["environment_id"],
            identities=self._identities,
            modules=hashes,
        )
        if self._binding is not None and self._binding != binding:
            raise StateError("Runner VM original source or ownership binding changed")
        for path, digest in self._evidence.items():
            if _sha(_read(path, 2_000_000)) != digest:
                raise StateError("Runner VM original evidence changed")
        if self._private is not None:
            info = tcg._private(self._private).stat()
            if (info.st_dev, info.st_ino) != self._private_identity:
                raise StateError("Runner VM private transport directory changed")
        return operation, recipe, hashes, binding

    def prepare(self, context):
        _, recipe, _, binding = self._admit(context, action="up")
        self._selected, self._binding = deepcopy(recipe), binding

    def _dispatch(self, context, action, operation, recipe, hashes, binding, child=None):
        sources = {
            "support_source": "tcg",
            "recipe_source": "runner_vm_recipe",
            "purge_source": "registry_remote",
            "driver_source": "runner_vm_remote",
            "runner_source": "runner_service_guest",
            "routes_source": "runner_vm_routes_remote",
            "guest_routes_source": "runner_vm_guest_routes",
            "acceleration_source": "qemu_acceleration",
        }
        packet = {
            "action": action,
            "operation_id": operation,
            "run_id": self._run_id,
            "environment_id": self.journal.state["environment_id"],
            "identities": self._identities,
            "modules": hashes,
            "binding": binding,
            "recipe": recipe,
            "timeout": min(3600, max(0.001, context.deadline - time.monotonic() - 5)),
            "expires_at": context.inputs["config"]["expires_at"],
            "expected_root_identity": self._remote_identity,
            "expected_runner_vm": self._origin,
            "expected_source_assets": self._source_assets,
        }
        if child is not None:
            packet["child"] = child
        for field, name in sources.items():
            raw = _read(PACKAGE_ROOT / "src/devsecops/factory" / (name + ".py"), 500000)
            if _sha(raw) != hashes.get(name):
                raise StateError("Runner VM transported source changed")
            packet[field] = base64.b64encode(raw).decode()
        raw = runner_vm_recipe.canonical(packet)
        if len(raw) > 3_000_000:
            raise StateError("Runner VM transport packet exceeds its limit")
        with self.foundation.command_context(
            recipe["transport"], self.journal, context.deadline, self._private
        ) as argv:
            if context.deadline <= time.monotonic():
                raise StateError("Runner VM transport deadline expired before dispatch")
            if action == "down" and child and child.get("action") == "explicit-parent-disposal":
                self._mark_parent_dispatch(operation, child)
            self._remote_attempted = True
            result = tcg._run(
                [*argv, shlex.join(["python3", "-I", "-B", "-c", BOOTSTRAP])],
                context.deadline,
                data=raw,
                check=False,
            )
        value = _json(result.stdout)
        if (
            result.returncode
            or value.get("binding") != binding
            or value.get("operation_id") != operation
            or "error" in value
            or type(value.get("observation")) is not dict
        ):
            raise StateError("Runner VM outcome is unobserved; reconcile without repeating the operation")
        observed = value["observation"]
        if observed.get("binding") != binding or observed.get("operation_id") != operation:
            raise StateError("Runner VM returned observation differs from the original dispatch")
        return observed

    def _perform(self, context, action, operation_id=None, *, child=None):
        selected_action = action if action in ("up", "down") else None
        operation, recipe, hashes, binding = self._admit(
            context, action=selected_action, reconciliation_operation=operation_id
        )
        if self._unknown and operation_id is None:
            raise StateError("Runner VM unknown effects require exact reconciliation")
        if action == "up":
            if self._up_operation is not None:
                raise StateError("Runner VM provisioning is one-shot")
            self._up_operation = operation
            self._selected, self._binding = deepcopy(recipe), binding
        elif self._up_operation is None:
            raise StateError("Runner VM was never attempted by this process")
        if self._private is None:
            parent = _safe_path(Path(recipe["private_state_directory"]))
            if (
                parent.is_relative_to(self.journal.run_dir)
                or self.journal.run_dir.is_relative_to(parent)
                or parent.is_relative_to(PACKAGE_ROOT)
            ):
                raise StateError("Runner VM private transport state must be separate")
            tcg._private(parent, create=True)
            private = parent / self._run_id
            private.mkdir(mode=0o700)
            info = private.stat()
            self._private, self._private_identity = private, (info.st_dev, info.st_ino)
        output = self.journal.run_dir / ("runner-vm-" + uuid4().hex)
        output.mkdir(mode=0o700)
        intent = output / "intent.json"
        tcg._write(
            intent,
            runner_vm_recipe.canonical(
                {
                    "action": action,
                    "operation_id": operation,
                    "binding": binding,
                    "run_id": self._run_id,
                    "modules": hashes,
                    "child_action": child.get("action") if child else None,
                }
            ),
        )
        self._evidence[intent] = _sha(_read(intent, 64000))
        try:
            actual = self._dispatch(context, action, operation, recipe, hashes, binding, child)
        except BaseException:
            self._unknown = self._remote_attempted
            if self._unknown:
                self._last_unknown_operation = operation
                self._last_unknown_action = action
            raise
        if actual.get("root_identity") is not None:
            if self._remote_identity is not None and actual["root_identity"] != self._remote_identity:
                self._unknown = True
                raise StateError("Runner VM original generation changed")
            self._remote_identity = deepcopy(actual["root_identity"])
        if actual.get("runner_vm") is not None:
            if self._origin is not None and actual["runner_vm"] != self._origin:
                self._unknown = True
                raise StateError("Runner VM original boot, SSH, disk or runtime identity changed")
            self._origin = deepcopy(actual["runner_vm"])
        if actual.get("source_assets") is not None:
            if self._source_assets is not None and actual["source_assets"] != self._source_assets:
                self._unknown = True
                raise StateError("Runner VM original supplied source identities changed")
            self._source_assets = deepcopy(actual["source_assets"])
        self._unknown = bool(actual.get("unresolved_operations", []))
        self._absent = actual.get("complete_cleanup") is True
        report = {
            "kind": "owned-runner-vm",
            "run_id": self._run_id,
            "identities": self._identities,
            "action": action,
            "operation_id": operation,
            **actual,
        }
        path = output / "observation.json"
        tcg._write(path, runner_vm_recipe.canonical(report))
        self._evidence[path] = _sha(_read(path, 2_000_000))
        outcome = PhaseOutcome(
            "incomplete",
            "owned-runner-vm-scope-observed",
            (intent, path),
            mutation_effect=actual.get(
                "mutation_effect", "not_applied" if action == "observe" else "unknown"
            ),
        )
        self._observations.retain(outcome, report)
        self._outcomes.append(outcome)
        self._dispatches.append((outcome, report))
        return outcome

    def up(self, context):
        return self._perform(context, "up")

    def down(self, context):
        self._admit(context, action="down")
        if not self._remote_attempted:
            return self._no_effects(context)
        for name, component in self._children.items():
            if self._child_attempts[name] and (
                component is None or component.vm_cleanup_ready(context) is not True
            ):
                raise StateError("Runner VM child cleanup remains unobserved: " + name)
        return self._perform(context, "down")

    @staticmethod
    def _complete(report):
        return (
            report.get("complete_cleanup") is True
            and not report.get("unresolved_operations")
            and report.get("source_assets_preserved") is True
            and report.get("tool_image_preserved") is True
            and bool(report.get("resources"))
            and all(
                row.get("state") == ("absent" if row.get("disposition") == "remove" else "preserved")
                and row.get("disposition") in ("remove", "preserve")
                for row in report["resources"]
            )
        )

    def prepare_disposal(self, context, *, prior_operation_id, revocation):
        """Read only: contain guest-local uncertainty without declaring it resolved."""
        from .owned_runner_service import require_runner_revocation

        self._admit(context, reconciliation_operation=prior_operation_id)
        attempt = self._disposal_attempt
        undispatched = (
            type(attempt) is dict
            and attempt["operation_id"] == prior_operation_id
            and attempt["parent_dispatched"] is False
        )
        if undispatched:
            self._disposal_admission(
                context, prior_operation_id, attempt["basis"], revocation, require_dispatched=False
            )
        if (
            context.phase["operation"] != "down"
            or self._origin is None
            or self._absent
            or self._unknown
            and self._last_unknown_action not in ("runner", "routes", "observe")
            and not (undispatched and self._last_unknown_action in ("host-observe", "down"))
        ):
            raise StateError("Parent disposal basis cannot contain an unknown parent operation")
        require_runner_revocation(
            revocation, vm=self, context=context, reconciliation_operation=prior_operation_id
        )
        outcome = self._perform(context, "host-observe", prior_operation_id)
        report = self.cleanup_observation(outcome)
        if (
            report.get("runner_vm") != self._origin
            or report.get("unresolved_operations")
            or report.get("state") != "ready"
            or report.get("guest_observation") != "not-requeried"
            or report.get("source_assets_preserved") is not True
            or report.get("tool_image_preserved") is not True
        ):
            raise StateError("Parent disposal basis lacks exact current host identity and preservation")
        require_runner_revocation(
            revocation, vm=self, context=context, reconciliation_operation=prior_operation_id
        )
        basis = object.__new__(RunnerVMDisposalBasis)
        _DISPOSALS[basis] = (self, outcome, prior_operation_id, revocation)
        if undispatched:
            attempt["successor_basis"] = basis
        return basis

    def disposal_observation(self, basis, context):
        """Audit retained evidence after PARTIAL; this never grants a new action."""
        if type(basis) is not RunnerVMDisposalBasis or basis not in _DISPOSALS:
            raise StateError("Original resident parent disposal basis required")
        owner, outcome, operation, _ = _DISPOSALS[basis]
        if (
            owner is not self
            or self._pid != os.getpid()
            or type(context) is not PhaseContext
            or not self.journal._lifecycle_locked
            or context.run_id != self._run_id
            or context.run_dir != self.journal.run_dir
            or dict(context.identities) != self._identities
            or self.journal.identities != self._identities
        ):
            raise StateError("Parent disposal history differs from its original current process/run")
        _package(
            PACKAGE_ROOT,
            self._identities["package_sha256"],
            self.driver.plugin_id,
            self.driver.plugin_version,
            time.monotonic() + 30,
        )
        report = self.cleanup_observation(outcome)
        if report.get("operation_id") != operation or report.get("runner_vm") != self._origin:
            raise StateError("Parent disposal history changed")
        return _freeze(
            {
                "runner_vm": self._origin,
                "prior_operation_id": operation,
                "observation": report,
                "artifacts": outcome.artifacts,
                "parent_disposal_eligible": True,
                "guest_residual": "unresolved-contained-in-original-VM",
            }
        )

    def disposal_artifacts(self, basis):
        if type(basis) is not RunnerVMDisposalBasis or basis not in _DISPOSALS:
            raise StateError("Original resident parent disposal basis required")
        owner, outcome, _, _ = _DISPOSALS[basis]
        if owner is not self or self._pid != os.getpid() or self.journal.identities != self._identities:
            raise StateError("Parent disposal artifacts belong to another original owner")
        self.cleanup_observation(outcome)
        return outcome.artifacts

    def dispose_guest(self, context, *, revocation, basis):
        """Explicit fresh down path; never an automatic SSH-failure fallback."""
        from .owned_runner_service import require_runner_revocation

        operation, _, _, _ = self._admit(context, action="down")
        previous_attempt = self._disposal_attempt
        if previous_attempt is not None and not (
            previous_attempt["parent_dispatched"] is False
            and previous_attempt["successor_basis"] is basis
            and previous_attempt["operation_id"] != operation
        ):
            raise StateError("Original parent disposal was attempted; reconcile it without redispatch")
        observed_basis = self.disposal_observation(basis, context)
        owner, _, prior, original_revocation = _DISPOSALS[basis]
        mutation = self.journal.state["active_mutation"]
        if (
            type(mutation) is not dict
            or owner is not self
            or original_revocation is not revocation
            or operation == prior
            or mutation.get("cleanup_mode") != "dispose-owned-runner-guest"
            or mutation.get("continues_operation") != prior
            or observed_basis["runner_vm"] != _freeze(self._origin)
        ):
            raise StateError("Parent disposal requires the exact new explicit mode and original basis")
        require_runner_revocation(revocation, vm=self, context=context)
        if (
            self._origin is None
            or self._remote_identity is None
            or self._absent
            or operation == self._last_unknown_operation
            or self._unknown
            and self._last_unknown_action not in ("runner", "routes", "observe")
        ):
            raise StateError("Parent disposal requires a fresh down and exact original live VM identity")
        routes = self._children["routes"]
        if self._child_attempts["routes"] and (
            routes is None or routes.host_cleanup_ready(context) is not True
        ):
            raise StateError("Original runner host relays must be absent before parent disposal")
        # The prior child effect remains explicitly unresolved. This grant only
        # disposes the independently identified parent, never retries that child.
        # Register the exact attempt before even the read-only host probe.
        # A failed probe can then be distinguished from an unknown deletion
        # using this original resident ledger, never an absent receipt.
        output = self.journal.run_dir / ("runner-vm-disposal-" + uuid4().hex)
        output.mkdir(mode=0o700)
        intent = output / "explicit-disposal-intent.json"
        tcg._write(
            intent,
            runner_vm_recipe.canonical(
                {
                    "kind": "original-runner-parent-disposal-intent",
                    "operation_id": operation,
                    "prior_operation_id": prior,
                    "cleanup_mode": "dispose-owned-runner-guest",
                    "run_id": self._run_id,
                    "identities": self._identities,
                    "runner_vm": self._origin,
                    "source_assets": self._source_assets,
                }
            ),
        )
        self._evidence[intent] = _sha(_read(intent, 2_000_000))
        self._disposal_attempt = {
            "operation_id": operation,
            "prior_operation_id": prior,
            "basis": basis,
            "revocation": revocation,
            "host_outcome": None,
            "parent_dispatched": False,
            "successor_basis": None,
            "intent": intent,
            "intent_sha256": self._evidence[intent],
            "runner_vm": deepcopy(self._origin),
            "source_assets": deepcopy(self._source_assets),
            "root_identity": deepcopy(self._remote_identity),
        }
        if previous_attempt is not None:
            self._disposal_attempt_history.append(previous_attempt)
        original_unknown = self._unknown
        self._unknown = False
        try:
            host_outcome = self._perform(context, "host-observe")
            self._disposal_attempt["host_outcome"] = host_outcome
            outcome = self._perform(
                context,
                "down",
                child={
                    "action": "explicit-parent-disposal",
                    "prior_unknown_operation": self._last_unknown_operation,
                },
            )
        except BaseException:
            self._unknown = self._unknown or original_unknown
            raise
        if not self._complete(self.cleanup_observation(outcome)):
            raise StateError("Runner parent absence remains unobserved")
        proof = self._issue_cessation(context, outcome, operation, revocation)
        return outcome, proof

    def _disposal_admission(self, context, operation_id, basis, revocation, *, require_dispatched=True):
        current, _, _, _ = self._admit(context, action="down", reconciliation_operation=operation_id)
        attempt = self._disposal_attempt
        mutation = self.journal.state.get("active_mutation")
        if (
            type(attempt) is not dict
            or type(mutation) is not dict
            or current != operation_id
            or attempt["operation_id"] != operation_id
            or attempt["basis"] is not basis
            or attempt["revocation"] is not revocation
            or mutation.get("cleanup_mode") != "dispose-owned-runner-guest"
            or mutation.get("continues_operation") != attempt["prior_operation_id"]
            or attempt["runner_vm"] != self._origin
            or attempt["root_identity"] != self._remote_identity
            or attempt["source_assets"] != self._source_assets
        ):
            raise StateError("Parent cessation reconciliation requires its exact original disposal attempt")
        if _sha(_read(attempt["intent"], 2_000_000)) != attempt["intent_sha256"]:
            raise StateError("Original parent disposal intent changed")
        historical = self.disposal_observation(basis, context)
        if historical["prior_operation_id"] != attempt["prior_operation_id"]:
            raise StateError("Original parent disposal observations differ")
        if require_dispatched or attempt["parent_dispatched"]:
            if attempt["parent_dispatched"] is not True or attempt["host_outcome"] is None:
                raise StateError("Original parent deletion has not been dispatched")
            host = self.cleanup_observation(attempt["host_outcome"])
            if host.get("operation_id") != operation_id or host.get("runner_vm") != self._origin:
                raise StateError("Original parent disposal observations differ")
        from .owned_runner_service import require_runner_revocation

        require_runner_revocation(revocation, vm=self, context=context, reconciliation_operation=operation_id)

    def _mark_parent_dispatch(self, operation_id, child):
        """Latch only at the final fixed transport invocation, after local prep."""
        attempt = self._disposal_attempt
        if (
            type(attempt) is not dict
            or attempt["operation_id"] != operation_id
            or attempt["host_outcome"] is None
            or attempt["parent_dispatched"] is not False
            or child.get("action") != "explicit-parent-disposal"
        ):
            raise StateError("Parent dispatch requires its original unconsumed disposal attempt")
        self.cleanup_observation(attempt["host_outcome"])
        attempt["parent_dispatched"] = True

    def disposal_was_dispatched(self, context, *, operation_id, revocation, basis):
        """Read only: return a positively retained original dispatch decision."""
        self._disposal_admission(context, operation_id, basis, revocation, require_dispatched=False)
        return self._disposal_attempt["parent_dispatched"]

    def _issue_cessation(self, context, outcome, operation, revocation, *, reconciliation=False):
        report = self.cleanup_observation(outcome)
        attempt = self._disposal_attempt
        if (
            type(attempt) is not dict
            or attempt["operation_id"] != operation
            or attempt["parent_dispatched"] is not True
            or attempt["revocation"] is not revocation
            or not self._complete(report)
            or report.get("runner_vm") != self._origin
            or report.get("operation_id") != operation
            or report.get("action") != ("reconcile" if reconciliation else "down")
        ):
            raise StateError("Original parent cessation requires positive complete absence")
        proof = object.__new__(GuestKernelCessation)
        read_operation = operation if reconciliation else None
        _CESSATIONS[proof] = (self, outcome, operation, revocation, read_operation)
        require_guest_kernel_cessation(
            proof, vm=self, context=context, reconciliation_operation=read_operation
        )
        return proof

    def guest_cessation_artifacts(self, proof):
        """Audit original proof bytes after cleanup without reviving a grant."""
        if type(proof) is not GuestKernelCessation or proof not in _CESSATIONS:
            raise StateError("Original guest cessation evidence required")
        owner, outcome, operation, _, _ = _CESSATIONS[proof]
        attempt = self._disposal_attempt
        if (
            owner is not self
            or self._pid != os.getpid()
            or self.journal.state["run_id"] != self._run_id
            or dict(self.journal.identities) != self._identities
            or type(attempt) is not dict
            or attempt["operation_id"] != operation
        ):
            raise StateError("Guest cessation history differs from its original process/run")
        if _sha(_read(attempt["intent"], 2_000_000)) != attempt["intent_sha256"]:
            raise StateError("Original parent disposal intent changed")
        report = self.cleanup_observation(outcome)
        if not self._complete(report) or report.get("runner_vm") != self._origin:
            raise StateError("Original parent absence evidence changed")
        return (attempt["intent"], *outcome.artifacts)

    def reconcile_disposal(self, context, *, operation_id, revocation, basis):
        """Observe a lost original disposal result; never repeat the deletion."""
        self._disposal_admission(context, operation_id, basis, revocation)
        outcome = self._perform(context, "reconcile", operation_id)
        self._disposal_admission(context, operation_id, basis, revocation)
        proof = self._issue_cessation(context, outcome, operation_id, revocation, reconciliation=True)
        return outcome, proof

    def reconcile(self, context, operation_id):
        if not self._remote_attempted:
            return self._no_effects(context, operation_id)
        return self._perform(context, "reconcile", operation_id)

    def _no_effects(self, context, operation_id=None):
        operation, _, _, binding = self._admit(
            context, action="down" if operation_id is None else None, reconciliation_operation=operation_id
        )
        if self._remote_attempted or any(self._child_attempts.values()):
            raise StateError("Runner no-effects closure requires its original never-dispatched ledger")
        output = self.journal.run_dir / ("runner-vm-" + uuid4().hex)
        output.mkdir(mode=0o700)
        report = {
            "kind": "owned-runner-vm",
            "action": "down" if operation_id is None else "reconcile",
            "run_id": self._run_id,
            "operation_id": operation,
            "identities": self._identities,
            "binding": binding,
            "complete_cleanup": True,
            "never_dispatched": True,
            "mutation_effect": "not_applied",
            "observation_effect": "not_applied",
            "cleanup_eligible": True,
            "unresolved_operations": [],
            "resources": [
                {
                    "kind": "original-runner-vm-inventory",
                    "identity": binding,
                    "disposition": "remove",
                    "state": "absent",
                    "basis": "original-resident-no-dispatch",
                }
            ],
        }
        path = output / "observation.json"
        tcg._write(path, runner_vm_recipe.canonical(report))
        self._evidence[path] = _sha(_read(path, 64000))
        outcome = PhaseOutcome(
            "incomplete", "runner-vm-never-dispatched", (path,), mutation_effect="not_applied"
        )
        self._observations.retain(outcome, report)
        self._outcomes.append(outcome)
        return outcome

    def continue_cleanup(self, context, prior_operation_id):
        operation, _, _, _ = self._admit(context, action="down")
        mutation = self.journal.state.get("active_mutation")
        if (
            not self._outcomes
            or operation == prior_operation_id
            or type(mutation) is not dict
            or mutation.get("continues_operation") != prior_operation_id
            or mutation.get("cleanup_mode") is not None
        ):
            raise StateError(
                "Runner cleanup continuation requires a new down linked to its original operation"
            )
        prior = self.cleanup_observation(self._outcomes[-1])
        if (
            prior.get("action") != "reconcile"
            or prior.get("operation_id") != prior_operation_id
            or prior.get("cleanup_eligible") is not True
            or prior.get("unresolved_operations")
        ):
            raise StateError("Runner cleanup original operation has not reached terminal observation")
        self._continuation = {"operation_id": operation, "prior_operation_id": prior_operation_id}
        return self.down(context)

    def cleanup_observation(self, outcome):
        return self._observations.observe(outcome)

    origin_observation = cleanup_observation

    def evidence_artifacts(self, context):
        """Export original historical evidence; this grants no action authority."""
        if (
            self._pid != os.getpid()
            or type(context) is not PhaseContext
            or context.run_id != self._run_id
            or context.run_dir != self.journal.run_dir
            or dict(context.identities) != self._identities
            or self.journal.state["run_id"] != self._run_id
            or dict(self.journal.identities) != self._identities
        ):
            raise StateError("Runner VM historical evidence belongs to another process or run")
        for path, digest in self._evidence.items():
            if _sha(_read(path, 2_000_000)) != digest:
                raise StateError("Runner VM original historical evidence changed")
        return tuple(self._evidence)

    def endpoint(self, context, *, reconciliation_operation=None):
        if self._origin is None or self._absent:
            raise StateError("Runner VM has no original ready guest endpoint")
        outcome = self._perform(context, "observe", reconciliation_operation)
        return _freeze(self.cleanup_observation(outcome)["endpoint"])

    def host_endpoint(self, context, *, reconciliation_operation):
        if context.phase["operation"] != "down" or self._origin is None or self._absent:
            raise StateError("Host-only runner endpoint is limited to original down reconciliation")
        outcome = self._perform(context, "host-observe", reconciliation_operation)
        return _freeze(self.cleanup_observation(outcome)["endpoint"])

    def _child(self, context, name, action, packet, operation_id):
        if (
            self._children[name] is None
            or action not in ("install", "up", "observe", "down", "reconcile", "down-host", "reconcile-host")
            or action in ("down-host", "reconcile-host")
            and name != "routes"
        ):
            raise StateError("Runner VM requires its fixed original registered child")
        if action in ("install", "up", "down", "down-host"):
            self._admit(context, action="down" if action in ("down", "down-host") else "up")
        if action in ("reconcile", "reconcile-host") and operation_id is None:
            raise StateError("Runner VM child reconciliation requires the exact original operation")
        if action == "reconcile-host" and context.phase["operation"] != "down":
            raise StateError("Host-only route reconciliation requires the original unknown down")
        if type(packet) is not dict or packet.get("action") != action:
            raise StateError("Runner VM child packet differs from its fixed action")
        if action in ("install", "up"):
            self._child_attempts[name] = True
        previous_unknown = self._unknown
        if action == "down-host" and self._unknown:
            current, _, _, _ = self._admit(context, action="down")
            if current == self._last_unknown_operation or self._last_unknown_action not in (
                "runner",
                "routes",
                "observe",
            ):
                raise StateError("Host-only route cleanup cannot replace an unknown VM operation")
            self._unknown = False
        try:
            outcome = self._perform(context, name, operation_id, child=packet)
        except BaseException:
            self._unknown = self._unknown or previous_unknown
            raise
        report = self.cleanup_observation(outcome)
        result = _freeze(
            {
                "observation": report["child"],
                "runner_vm": report["runner_vm"],
                "transport_receipt": {
                    "artifact": str(outcome.artifacts[-1]),
                    "sha256": self._evidence[outcome.artifacts[-1]],
                    "operation_id": report["operation_id"],
                    "action": action,
                    "vm_binding": self._binding,
                    "guest_boot_id": self._origin["guest_boot_id"],
                },
            }
        )
        self._dispatches.append((result, outcome))
        return result

    def dispatch_runner(self, context, *, action, packet, reconciliation_operation=None):
        return self._child(context, "runner", action, packet, reconciliation_operation)

    def dispatch_routes(self, context, *, action, packet, reconciliation_operation=None):
        return self._child(context, "routes", action, packet, reconciliation_operation)

    def require_dispatch(self, original, context, *, reconciliation_operation=None):
        self._admit(context, reconciliation_operation=reconciliation_operation)
        for result, outcome in self._dispatches:
            if result is original:
                self.cleanup_observation(outcome)
                return original
        raise StateError("Only an original resident runner VM child dispatch may be observed")
