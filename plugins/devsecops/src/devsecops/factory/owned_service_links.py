"""Fixed owned-service routes. Serialized observations cannot issue route authority."""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import shlex
import time
from types import MappingProxyType
from uuid import uuid4
import weakref

from devsecops.runtime.cli import _new_directory, _write
from . import service_link_remote, service_link_local, tcg
from .cleanup_observation import ComponentObservations
from .lifecycle import PhaseContext, PhaseOutcome, _load_inputs
from .owned_fresh_foundation import OwnedFreshRKE2Foundation
from .owned_registry import OwnedOCIRegistry
from .owned_services import OwnedServiceStack, _freeze
from .owned_linux import OwnedLinuxDriver, _plain
from .state import StateError, _json, _read, _sha

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_BOOTSTRAP = """import base64,json,sys,types
p=json.loads(sys.stdin.buffer.read(3000001))
def load(name,raw):
 m=types.ModuleType(name);sys.modules[name]=m;b=base64.b64decode(raw,validate=True);exec(compile(b,name,'exec'),m.__dict__);m.SOURCE_BYTES=b;return m
try:
 s=load('link_support',p.pop('support_source'));g=load('link_guest',p.pop('guest_source'));r=load('link_remote',p.pop('remote_source'))
 v=r.execute(p,s,g);print(json.dumps({'binding':p['binding'],'operation_id':p['operation_id'],'observation':v},sort_keys=True))
except Exception:
 print('{"error":"owned-service-links-outcome-unobserved"}');sys.exit(2)
"""
_ISSUER = object()
_ROUTES = weakref.WeakKeyDictionary()


def _mutation_action(phase):
    # This is the single canonical case with a dedicated mutation action. Other
    # case/read phases cannot obtain its route merely by supplying an action.
    return (
        "platform-dependency"
        if phase["id"] == "platform-dependency" and phase["operation"] == "case"
        else phase["operation"]
    )


def _endpoint_context(journal, context, *, reconciliation_operation=None):
    """Read-only original endpoint admission; never an effect dispatcher."""
    if (
        type(context) is not PhaseContext
        or not journal._lifecycle_locked
        or type(context.deadline) not in (int, float)
        or not math.isfinite(context.deadline)
        or time.monotonic() >= context.deadline
        or context.run_id != journal.state["run_id"]
        or context.run_dir != journal.run_dir
        or dict(context.identities) != journal.identities
    ):
        raise StateError("Service endpoint requires its current original context")
    life = journal.state["lifecycle"]
    phase = _plain(context.phase)
    if (
        phase not in life["phases"]
        or (life.get("active_phase") or {}).get("phase_id") != phase["id"]
        or life.get("final_result") is not None
        or _plain(context.input_artifacts) != life["inputs"]
        or context.execution_mode != life["execution_mode"]
    ):
        raise StateError("Service endpoint differs from the current lifecycle")
    if reconciliation_operation is None:
        if journal.needs_reconciliation:
            raise StateError("Normal service endpoint cannot bypass unknown operation")
    else:
        current = journal.current_mutation(phase["id"], _mutation_action(phase), reconciliation=True)
        if not journal.needs_reconciliation or current != reconciliation_operation:
            raise StateError("Service endpoint reconciliation differs from its existing unknown operation")


class OwnedServiceRoute:
    __slots__ = ("__weakref__",)

    def __init__(self, *, _issuer=None):
        if _issuer is not _ISSUER:
            raise StateError("Only the original service-link owner can issue a route")

    def __copy__(self):
        raise StateError("Owned routes cannot be copied")

    def __deepcopy__(self, memo):
        raise StateError("Owned routes cannot be copied")

    def __reduce__(self):
        raise StateError("Owned routes cannot be serialized")


def require_service_route(value, *, origin):
    if type(value) is not OwnedServiceRoute or value not in _ROUTES:
        raise StateError("An original resident service route is required")
    owner, context, service, reconciliation = _ROUTES[value]
    facts = owner._route_facts(context, service, reconciliation_operation=reconciliation)
    if type(origin) is not str or facts["origin"] != origin:
        raise StateError("Service route origin differs from the selected TLS origin")
    return facts


def renew_service_route(value, *, context, operation_id=None):
    if type(value) is not OwnedServiceRoute or value not in _ROUTES:
        raise StateError("Only an original service route may be renewed")
    owner, _, service, reconciliation = _ROUTES[value]
    selected = operation_id if operation_id is not None else reconciliation
    return (
        owner.reconciliation_route(context, service, selected)
        if selected is not None
        else owner.route(context, service)
    )


class OwnedServiceLinks:
    def __init__(self, driver, *, foundation, registry, services):
        if (
            type(driver) is not OwnedLinuxDriver
            or type(foundation) is not OwnedFreshRKE2Foundation
            or type(registry) is not OwnedOCIRegistry
            or type(services) is not OwnedServiceStack
            or any(c.journal is not driver.journal for c in (foundation, registry, services))
        ):
            raise StateError(
                "Service links require exact original fresh/service/registry owners and one journal"
            )
        self.driver, self.journal = driver, driver.journal
        self.foundation, self.registry, self.services = foundation, registry, services
        self._pid, self._run_id, self._identities = (
            os.getpid(),
            driver.journal.state["run_id"],
            dict(driver.journal.identities),
        )
        self._attempts, self._outcomes, self._tunnels = [], [], {}
        self._observations = ComponentObservations()
        self._binding = self._remote_identity = self._selected = self._remote = None
        self._unknown, self._down, self._runner_started = False, False, False
        self._private = None
        self._continuation = None

    def _admit(self, context, *, action=None, operation_id=None):
        if (
            type(context) is not PhaseContext
            or self._pid != os.getpid()
            or type(context.deadline) not in (int, float)
            or not math.isfinite(context.deadline)
            or time.monotonic() >= context.deadline
            or not self.journal._lifecycle_locked
        ):
            raise StateError("Service links require their current bounded process context")
        life = self.journal.state["lifecycle"]
        phase = _plain(context.phase)
        if (
            context.run_id != self._run_id
            or context.run_dir != self.journal.run_dir
            or dict(context.identities) != self._identities
            or self.journal.identities != self._identities
            or _plain(context.input_artifacts) != life["inputs"]
            or context.execution_mode != life["execution_mode"]
            or life["driver_id"] != "owned-linux"
            or phase not in life["phases"]
            or (life.get("active_phase") or {}).get("phase_id") != phase["id"]
            or life.get("final_result") is not None
        ):
            raise StateError("Service-link context differs from the original active lifecycle")
        paths = {n: self.journal._artifact_path(v["path"]) for n, v in life["inputs"].items()}
        _, docs = _load_inputs(paths, self._identities, self.journal.state["environment_id"])
        if docs != _plain(context.inputs):
            raise StateError("Service-link frozen inputs changed")
        self.driver._package(self._identities["package_sha256"], context.deadline)
        if action != "reconcile" and self.journal.needs_reconciliation:
            raise StateError("Existing unknown mutation must be reconciled before service routes")
        if action is not None:
            expected = (
                "up"
                if action in ("up", "runner")
                else "down"
                if action == "down"
                else _mutation_action(phase)
            )
            if (action != "reconcile" and expected not in ("up", "down")) or (
                _mutation_action(phase) if action == "reconcile" else phase["operation"]
            ) != expected:
                raise StateError("Service-link mutation belongs to a different phase")
            current = self.journal.current_mutation(
                phase["id"], expected, reconciliation=action == "reconcile"
            )
            if operation_id is not None and current != operation_id:
                raise StateError("Service-link reconciliation differs from the original operation")
            if action in ("up", "runner"):
                expiry = datetime.fromisoformat(docs["config"]["expires_at"].replace("Z", "+00:00"))
                if (
                    "provision" not in docs["config"]["allowed_mutations"]
                    or datetime.now(timezone.utc) >= expiry
                ):
                    raise StateError("Service-link provisioning grant expired")
            if "cleanup" not in docs["config"]["allowed_mutations"]:
                raise StateError("Service-link cleanup scope is absent")
            return current
        return None

    def _targets(self, context, *, reconciliation_operation=None):
        fresh = _plain(
            self.foundation.service_link_origin(context, reconciliation_operation=reconciliation_operation)
        )
        gitlab = (
            _plain(self.services.service_endpoint(context, reconciliation_operation=reconciliation_operation))
            if reconciliation_operation is not None
            else _plain(self.services.service_endpoint(context))
        )
        registry = _plain(
            self.registry.service_endpoint(context, reconciliation_operation=reconciliation_operation)
        )
        for target in (fresh, gitlab, registry):
            if target["run_id"] != self._run_id or target["identities"] != self._identities:
                raise StateError("Service-link endpoint owner differs")
        selected = {"foundation": fresh, "gitlab": gitlab, "registry": registry}
        # Runner starts after the first API bootstrap. It is the only additional
        # endpoint allowed later, with a direct original service observation.
        static = deepcopy(selected)
        static["gitlab"]["runner"] = None
        if self._selected is not None and static != self._selected:
            raise StateError("Service-link original endpoint identity changed")
        return selected, static

    def _dispatch(self, context, action, operation, targets):
        from . import service_link_guest

        sources = {}
        for field, module in (
            ("support_source", tcg),
            ("remote_source", service_link_remote),
            ("guest_source", service_link_guest),
        ):
            path = Path(module.__file__).resolve()
            if path.parent != PACKAGE_ROOT / "src/devsecops/factory":
                raise StateError("Service-link transported source is outside its installed package")
            sources[field] = base64.b64encode(_read(path, 500000)).decode()
        materials = {}
        for role in ("gitlab", "registry"):
            item = targets[role]
            raw = _read(Path(item["ca_path"]), 1000000)
            if _sha(raw) != item["ca_sha256"]:
                raise StateError("Service-link public CA changed before dispatch")
            materials[role] = base64.b64encode(raw).decode()
        packet = {
            "schema_version": 1,
            "action": action,
            "operation_id": operation,
            "run_id": self._run_id,
            "identities": self._identities,
            "binding": self._binding,
            "targets": targets,
            "expected_root_identity": self._remote_identity,
            "expected_observation": self._remote,
            "timeout": min(600, max(0.001, context.deadline - time.monotonic() - 5)),
            "expires_at": context.inputs["config"]["expires_at"],
            "materials": materials,
            **sources,
        }
        raw = (__import__("json").dumps(packet, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(raw) > 3_000_000:
            raise StateError("Service-link packet exceeds its fixed budget")
        self._attempts[-1]["dispatched"] = True
        with self.foundation.command_context(
            targets["foundation"]["recipe"]["transport"], self.journal,
            context.deadline, self._private / "receipts"
        ) as argv:
            result = tcg._run(
                [*argv, shlex.join(["python3", "-I", "-B", "-c", _BOOTSTRAP])],
                context.deadline,
                data=raw,
                check=False,
            )
        value = _json(result.stdout)
        if (
            result.returncode
            or value.get("binding") != self._binding
            or value.get("operation_id") != operation
            or "error" in value
        ):
            raise StateError("Service-link remote outcome is unobserved; reconcile before another mutation")
        actual = value["observation"]
        if actual.get("binding") != self._binding or actual.get("operation_id") != operation:
            raise StateError("Service-link direct remote observation differs")
        identity = actual.get("root_identity")
        if not isinstance(identity, dict) or set(identity) != {"device", "inode"}:
            raise StateError("Service-link original remote generation was not observed")
        if self._remote_identity is not None and identity != self._remote_identity:
            raise StateError("Service-link remote generation was replaced")
        self._remote_identity = identity
        return actual

    def _perform(self, context, action, operation_id=None):
        operation = self._admit(context, action=action, operation_id=operation_id)
        if self._unknown and action != "reconcile":
            raise StateError("Service-link unknown effects must be reconciled first")
        if action == "up" and self._attempts or action == "down" and self._down:
            raise StateError("Service-link effect already attempted; use original reconciliation")
        if action == "runner" and self._runner_started:
            raise StateError("Runner mapping was already attempted")
        if action == "reconcile" and not any(r["operation_id"] == operation for r in self._attempts):
            raise StateError("No original service-link operation matches this reconciliation")
        # No-effects closure is backed by this original object, never missing files.
        never_dispatched = action != "up" and not any(a["dispatched"] for a in self._attempts)
        if action == "up":
            targets, static = self._targets(context)
            self._selected = static
            self._binding = _sha(
                __import__("json").dumps(static, sort_keys=True, separators=(",", ":")).encode()
            )
            self._private = self.foundation._private / "service-links"
            self._private.mkdir(mode=0o700)
            (self._private / "receipts").mkdir(mode=0o700)
        elif never_dispatched:
            targets = None
        elif action == "reconcile":
            # Unknown effects never gain authority from new endpoint JSON. The
            # exact original retained owners/pins remain independently audited.
            self.foundation.current_transport_for(
                self._selected["foundation"]["recipe"]["transport"], self.journal
            )
            targets, _ = self._targets(context, reconciliation_operation=operation)
        else:
            targets, _ = self._targets(context)
        if action == "runner":
            if targets["gitlab"]["runner"] is None:
                raise StateError("Runner mapping requires the actual owned runner ID")
            self._runner_started = True
        if action == "down":
            self._down = True
        output = self.journal.run_dir / ("service-links-" + uuid4().hex)
        attempt = {"action": action, "operation_id": operation, "dispatched": False}
        self._attempts.append(attempt)
        with _new_directory(output) as directory:
            _write(
                directory,
                "intent.json",
                {
                    "kind": "owned-service-links-intent",
                    "binding": self._binding,
                    "run_id": self._run_id,
                    "operation_id": operation,
                    "action": action,
                    "identities": self._identities,
                },
            )
            os.fsync(directory)
            try:
                if never_dispatched:
                    actual = {
                        "complete_cleanup": True,
                        "unresolved_operations": [],
                        "mutation_effect": "applied",
                        "resources": [
                            {
                                "kind": "resident-link-inventory",
                                "disposition": "preserve",
                                "state": "preserved",
                            }
                        ],
                        "basis": "original-resident-never-dispatched",
                    }
                else:
                    dispatch_action = (
                        "reconcile"
                        if action == "down"
                        and self._continuation is not None
                        and self._remote
                        and self._remote.get("complete_cleanup") is True
                        else action
                    )
                    actual = self._dispatch(context, dispatch_action, operation, targets)
                    self._remote = deepcopy(actual)
                    self._unknown = bool(actual.get("unresolved_operations"))
                    if action == "down" and actual.get("complete_cleanup") is True and not self._unknown:
                        for tunnel in self._tunnels.values():
                            tunnel.close(context)
                    if action == "up" and not self._unknown and actual.get("ready") is True:
                        for role in ("gitlab", "registry"):
                            tunnel = service_link_local.OwnedLinkTunnel(self, role)
                            self._tunnels[role] = tunnel  # retain intent owner before spawn
                            tunnel.start(context, actual["listeners"]["desktop-" + role]["port"])
                    if action == "reconcile":
                        for tunnel in self._tunnels.values():
                            tunnel.reconcile(context)
                        self._unknown = self._unknown or any(t.unresolved for t in self._tunnels.values())
                local = [t.observation(context) for t in self._tunnels.values()]
                resources = [*actual.get("resources", []), *local]
                complete = actual.get("complete_cleanup") is True and all(
                    r["state"] == "absent" for r in local
                )
                report = {
                    "kind": "owned-service-links",
                    "schema_version": 1,
                    "binding": self._binding,
                    "run_id": self._run_id,
                    "identities": self._identities,
                    "operation_id": operation,
                    "action": action,
                    "complete_cleanup": complete,
                    "ready": actual.get("ready") is True,
                    "resources": resources,
                    "mutation_effect": "applied" if not self._unknown else "unknown",
                    "unresolved_operations": actual.get("unresolved_operations", [])
                    + (["local-tunnel"] if self._unknown and not actual.get("unresolved_operations") else []),
                    "cleanup_eligible": action == "reconcile" and not self._unknown,
                    "canonical_success": False,
                }
                _write(directory, "observation.json", actual)
                _write(directory, "links.json", report)
                os.fsync(directory)
            except BaseException:
                self._unknown = any(a["dispatched"] for a in self._attempts)
                raise
        outcome = PhaseOutcome(
            "incomplete",
            "owned-service-links-component-only",
            (
                output / "intent.json",
                output / "observation.json",
                output / "links.json",
                *(path for t in self._tunnels.values() for path in t.artifacts()),
            ),
            mutation_effect=report["mutation_effect"],
        )
        self._outcomes.append(outcome)
        return self._observations.retain(outcome, report)

    def up(self, context):
        return self._perform(context, "up")

    def configure_runner(self, context):
        return self._perform(context, "runner")

    def down(self, context):
        return self._perform(context, "down")

    def reconcile(self, context, operation_id):
        return self._perform(context, "reconcile", operation_id)

    def continue_cleanup(self, context, prior_operation_id):
        """Explicit fresh action only after original read-only reconciliation.

        The aggregate caller must journal a NEW down mutation. This method does
        not reopen a spent grant or infer effects from absent files.
        """
        operation = self._admit(context, action="down")
        if not self._down or operation == prior_operation_id or not self._outcomes:
            raise StateError("Link cleanup continuation requires a new scoped down operation")
        prior = self._observations.observe(self._outcomes[-1])
        if (
            prior["action"] != "reconcile"
            or prior["operation_id"] != prior_operation_id
            or prior.get("cleanup_eligible") is not True
            or prior["unresolved_operations"]
        ):
            raise StateError("Original link cleanup has not reached terminal reconciliation")
        self._continuation = {"prior_operation_id": prior_operation_id, "operation_id": operation}
        self._down = False
        return self._perform(context, "down")

    def cleanup_observation(self, outcome):
        return self._observations.observe(outcome)

    def evidence_artifacts(self, context, *, reconciliation_operation=None):
        self._admit(
            context,
            action="reconcile" if reconciliation_operation is not None else None,
            operation_id=reconciliation_operation,
        )
        for outcome in self._outcomes:
            self._observations.observe(outcome)
        return tuple(path for outcome in self._outcomes for path in outcome.artifacts)

    def _route_facts(self, context, service, *, reconciliation_operation=None):
        self._admit(
            context,
            action="reconcile" if reconciliation_operation is not None else None,
            operation_id=reconciliation_operation,
        )
        if (
            service not in ("gitlab", "registry")
            or self._down
            or self._unknown
            or service not in self._tunnels
        ):
            raise StateError("Service route is unavailable or has unresolved effects")
        self.evidence_artifacts(context, reconciliation_operation=reconciliation_operation)
        expiry = datetime.fromisoformat(context.inputs["config"]["expires_at"].replace("Z", "+00:00"))
        remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise StateError("Original service route grant expired; fresh scoped authorization is required")
        targets, _ = self._targets(context, reconciliation_operation=reconciliation_operation)
        tunnel = self._tunnels[service]
        tunnel.audit(context)
        target = targets[service]
        return MappingProxyType(
            {
                "origin": target["origin"],
                "connect_host": "127.0.0.1",
                "connect_port": tunnel.port,
                "ca_path": target["ca_path"],
                "ca_sha256": target["ca_sha256"],
                "run_id": self._run_id,
                "identities": _freeze(self._identities),
                "service": service,
                "deadline": min(context.deadline, time.monotonic() + remaining),
                "read_only": reconciliation_operation is not None,
            }
        )

    def route(self, context, service):
        self._route_facts(context, service)
        result = OwnedServiceRoute(_issuer=_ISSUER)
        _ROUTES[result] = (self, context, service, None)
        return result

    def reconciliation_route(self, context, service, operation_id):
        if not self.journal.needs_reconciliation or not isinstance(operation_id, str):
            raise StateError("Read-only reconciliation route requires its existing unknown operation")
        self._route_facts(context, service, reconciliation_operation=operation_id)
        result = OwnedServiceRoute(_issuer=_ISSUER)
        _ROUTES[result] = (self, context, service, operation_id)
        return result
