"""Journal-owned, fixed Zot bootstrap and storage qualification component.

Every result is incomplete and its whole-phase mutation effect remains unknown.
Only the separate component fields describe bootstrap, uploads and cleanup.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import io
import math
import os
from pathlib import Path
import re
import shlex
import time
from uuid import uuid4
import zipfile

from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from . import service_recipe, registry_remote, registry_qualification, tcg
from .cleanup_observation import ComponentObservations
from .lifecycle import PhaseContext, PhaseOutcome, _load_inputs
from .native_codex import _package
from .owned_foundation import _within
from .owned_linux import _plain
from .state import RunJournal, StateError, _json, _read, _safe_path, _sha

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
MODULES = (
    "factory/owned_registry.py",
    "factory/service_recipe.py",
    "factory/registry_remote.py",
    "factory/registry_qualification.py",
    "factory/tcg.py",
    "adapters/http.py",
    "adapters/oci.py",
    "adapters/staging_registry.py",
)
PROBE_MODULES = (
    "__init__.py",
    "adapters/__init__.py",
    "factory/__init__.py",
    "adapters/http.py",
    "adapters/oci.py",
    "adapters/registry_download_policy.py",
    "adapters/staging_registry.py",
    "factory/registry_qualification.py",
)
BOOTSTRAP = """import base64,json,sys,types
p=json.loads(sys.stdin.buffer.read(2000001))
def module(name,raw):
 m=types.ModuleType(name);sys.modules[name]=m
 exec(compile(base64.b64decode(raw,validate=True),name,'exec'),m.__dict__)
 return m
try:
 s=module('devsecops_registry_support',p.pop('support'))
 r=module('devsecops_registry_recipe',p.pop('recipes'))
 d=module('devsecops_registry_driver',p.pop('driver'))
 result=d.execute(p,s,r)
 print(json.dumps({'binding':p['binding'],'operation_id':p['operation_id'],'observation':result},sort_keys=True))
except Exception:
 print('{"error":"registry-operation-unknown-reconcile-required"}')
 sys.exit(2)
"""


@dataclass(frozen=True)
class RegistryTransport:
    transport: dict


def _probe_zip(module_hashes):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in PROBE_MODULES:
            raw = _read(PACKAGE_ROOT / "src/devsecops" / name, 300000)
            if _sha(raw) != module_hashes.get(name):
                raise StateError("Captured registry probe module differs from admitted package")
            archive.writestr(zipfile.ZipInfo("devsecops/" + name, (2026, 1, 1, 0, 0, 0)), raw)
        archive.writestr(
            zipfile.ZipInfo("__main__.py", (2026, 1, 1, 0, 0, 0)),
            b"from devsecops.factory.registry_qualification import main\nmain()\n",
        )
    raw = stream.getvalue()
    if len(raw) > 600000:
        raise StateError("Registry qualification package exceeds its bound")
    return raw


def _write_terminal(directory, name, value):
    # Capture identity at exclusive creation, before any publication or audit.
    return registry_qualification._write(directory, name, value)


class OwnedOCIRegistry:
    """The caller freezes the recipe as a mutation intent before invoking us."""

    def __init__(self, journal, *, recipe_path, recipe_sha256, plugin_id, plugin_version):
        if (
            type(journal) is not RunJournal
            or not isinstance(plugin_id, str)
            or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}@[a-z][a-z0-9-]{0,62}", plugin_id)
            or not isinstance(plugin_version, str)
            or not re.fullmatch("[A-Za-z0-9][A-Za-z0-9.+-]{0,127}", plugin_version)
            or not isinstance(recipe_sha256, str)
            or not re.fullmatch("[a-f0-9]{64}", recipe_sha256)
        ):
            raise StateError("Registry requires explicit installed identity and pinned recipe")
        self.journal, self.recipe_path, self.recipe_sha256 = journal, Path(recipe_path), recipe_sha256
        self.plugin_id, self.plugin_version, self.pid = plugin_id, plugin_version, os.getpid()
        self._cleanup_observations = ComponentObservations()
        self._link_outcome = None
        self._link_down = False
        self._foundation_transport = None
        self._transport_started = False

    def bind_foundation_transport(self, foundation):
        from .owned_fresh_foundation import OwnedFreshRKE2Foundation

        if (
            type(foundation) is not OwnedFreshRKE2Foundation
            or foundation.journal is not self.journal
            or self._foundation_transport is not None
            or self._transport_started
        ):
            raise StateError("Registry transport requires one original same-journal fresh foundation")
        self._foundation_transport = foundation

    def _admit(self, context, action, deadline, operation_id):
        if type(context) is not PhaseContext or self.pid != os.getpid() or not self.journal._lifecycle_locked:
            raise StateError("Registry requires this process and locked lifecycle context")
        if time.monotonic() >= deadline:
            raise StateError("Registry deadline expired")
        state = self.journal.state
        lifecycle, phase = state.get("lifecycle", {}), _plain(context.phase)
        selected_action = phase.get("operation") if action == "reconcile" else action
        if (
            selected_action not in ("up", "down")
            or phase.get("operation") != selected_action
            or context.run_id != state["run_id"]
            or context.run_dir != self.journal.run_dir
            or _plain(context.identities) != self.journal.identities
            or _plain(context.input_artifacts) != lifecycle.get("inputs")
            or context.execution_mode != lifecycle.get("execution_mode")
            or lifecycle.get("driver_id") != "owned-linux"
            or phase not in lifecycle.get("phases", [])
            or lifecycle.get("final_result") is not None
        ):
            raise StateError("Registry operation differs from the current lifecycle")
        current = self.journal.current_mutation(
            phase["id"], selected_action, reconciliation=action == "reconcile"
        )
        if operation_id is not None and current != operation_id:
            raise StateError("Registry reconciliation operation differs")
        paths = {name: self.journal._artifact_path(ref["path"]) for name, ref in lifecycle["inputs"].items()}
        _, docs = _load_inputs(paths, self.journal.identities, state["environment_id"])
        config = docs["config"]
        if docs != _plain(context.inputs) or config["profile"] != "bigbang-live":
            raise StateError("Registry inputs differ from the selected reference profile")
        if (
            "cleanup" not in config["allowed_mutations"]
            or action == "up"
            and "provision" not in config["allowed_mutations"]
        ):
            raise StateError("Registry operation lacks recorded scope")
        if action == "up" and datetime.now(timezone.utc) >= datetime.fromisoformat(
            config["expires_at"].replace("Z", "+00:00")
        ):
            raise StateError("Registry provisioning window expired")
        raw = _read(_safe_path(self.recipe_path), 64000)
        if _sha(raw) != self.recipe_sha256 or not any(
            ref["sha256"] == self.recipe_sha256 for ref in state["active_mutation"]["intent_artifacts"]
        ):
            raise StateError("Registry recipe must match an exact frozen current mutation intent")
        recipe = service_recipe.validate(_json(raw))
        services = [row for row in config["services"] if row["id"] == "registry"]
        if (
            recipe["environment"] != state["environment_id"]
            or len(services) != 1
            or services[0]["origin"] != recipe["origin"]
            or services[0].get("ca_file") != recipe["materials"]["ca"]["path"]
            or set(services[0]["scope"].get("repositories", []))
            != {recipe["repository"], recipe["repository"] + "/qualification"}
        ):
            raise StateError("Registry recipe differs from approved destination and repository scope")
        for actor, field in (("operator", "operator_auth"), ("reader", "read_auth")):
            auth = services[0].get(field, {})
            if (
                auth.get("mode") != "basic"
                or auth.get("password_env") != recipe["credentials"][actor]["password_env"]
            ):
                raise StateError("Registry credentials differ from selected service references")
            if (
                action == "up"
                and os.environ.get(auth["username_env"]) != recipe["credentials"][actor]["username"]
            ):
                raise StateError("Registry username differs from selected service identity")
        if PACKAGE_ROOT != Path(__file__).resolve().parents[3]:
            raise StateError("Registry must load from its selected installed package")
        for module in (tcg, service_recipe, registry_remote, registry_qualification):
            if Path(module.__file__).resolve().parent != PACKAGE_ROOT / "src/devsecops/factory":
                raise StateError("Registry executing module is outside the installed package")
        manifest, members = _package(
            PACKAGE_ROOT,
            self.journal.identities["package_sha256"],
            self.plugin_id,
            self.plugin_version,
            deadline,
        )
        hashes = {}
        for name in set(MODULES) | set(PROBE_MODULES):
            hashes[name] = _sha(_read(PACKAGE_ROOT / "src/devsecops" / name, 300000))
            if members.get("src/devsecops/" + name) != hashes[name]:
                raise StateError("Registry module differs from selected package")
        return current, recipe, hashes, raw

    def _dispatch(self, recipe, packet, directory, deadline):
        captured = {}
        for field, module, name in (
            ("support", tcg, "factory/tcg.py"),
            ("recipes", service_recipe, "factory/service_recipe.py"),
            ("driver", registry_remote, "factory/registry_remote.py"),
        ):
            raw = _read(Path(module.__file__), 300000)
            if _sha(raw) != packet["package_modules"].get(name):
                raise StateError("Captured registry transport module differs from admitted package")
            captured[field] = base64.b64encode(raw).decode()
        packet = {**packet, **captured}
        raw = service_recipe.canonical(packet)
        if len(raw) > 2000000:
            raise StateError("Registry transport input exceeds its bound")
        connection = (
            tcg._ssh_context(RegistryTransport(recipe["transport"]), deadline, directory)
            if self._foundation_transport is None
            else self._foundation_transport.command_context(recipe["transport"], self.journal, deadline, directory)
        )
        self._transport_started = True
        with connection as argv:
            result = tcg._run(
                [*argv, shlex.join(["python3", "-I", "-B", "-c", BOOTSTRAP])], deadline, data=raw, check=False
            )
        response = _json(result.stdout)
        if (
            result.returncode
            or response.get("binding") != packet["binding"]
            or response.get("operation_id") != packet["operation_id"]
            or "error" in response
        ):
            raise StateError("Registry operation may have effects; read-only reconciliation required")
        return response["observation"]

    def _perform(self, context, action, operation_id=None):
        if (
            type(context) is not PhaseContext
            or type(context.deadline) not in (int, float)
            or not math.isfinite(context.deadline)
        ):
            raise StateError("Registry requires a bounded context")
        deadline = min(context.deadline, time.monotonic() + 240)
        operation, recipe, hashes, raw = self._admit(context, action, deadline, operation_id)
        private = _safe_path(Path(recipe["private_state_directory"]))
        if any(
            _within(private, root) or _within(root, private) for root in (self.journal.run_dir, PACKAGE_ROOT)
        ):
            raise StateError("Registry private state must be separate from package and journal")
        for item in [
            self.recipe_path,
            *[Path(item["path"]) for item in recipe["materials"].values()],
            *[
                Path(recipe["transport"][name]["path"])
                for name in ("config", "known_hosts", "certificate", "identity", "proxy")
            ],
        ]:
            if _within(_safe_path(item), private):
                raise StateError("Registry private state cannot contain operator inputs")
        binding = service_recipe.binding(
            recipe, run_id=context.run_id, identities=dict(self.journal.identities), package_modules=hashes
        )
        packet = {
            "action": action,
            "operation_id": operation,
            "run_id": context.run_id,
            "identities": dict(self.journal.identities),
            "package_modules": hashes,
            "binding": binding,
            "recipe": recipe,
            "timeout": max(0.001, deadline - time.monotonic() - 5),
            "expires_at": context.inputs["config"]["expires_at"],
        }
        probe = _probe_zip(hashes)
        packet["probe_zip_sha256"] = _sha(probe)
        if action == "up":
            materials = {}
            for name, ref in recipe["materials"].items():
                value = _read(_safe_path(Path(ref["path"])), 1000000)
                if _sha(value) != ref["sha256"]:
                    raise StateError("Registry private material differs from its selected pin")
                if name in ("private_key", "htpasswd") and Path(ref["path"]).stat().st_mode & 0o077:
                    raise StateError("Registry secret material must be private")
                materials[name] = base64.b64encode(value).decode()
            settings = {
                "origin": recipe["origin"],
                "repository": recipe["repository"] + "/qualification",
                "binding": binding,
                "expires_at": packet["expires_at"],
            }
            for actor, ref in recipe["credentials"].items():
                password = os.environ.get(ref["password_env"])
                if (
                    not password
                    or not 16 <= len(password) <= 1024
                    or any(ord(c) < 33 or ord(c) > 126 for c in password)
                ):
                    raise StateError("Registry requires existing bounded private credentials")
                settings[actor] = {"username": ref["username"], "password": password}
            packet.update(
                materials=materials,
                probe_settings=settings,
                probe_zip=base64.b64encode(probe).decode(),
                probe_zip_sha256=_sha(probe),
            )
        private = tcg._private(private, create=True)
        audit = tcg._private(private / "transport", create=True)
        owner = private / "owner.json"
        selected = {"binding": binding, "recipe_sha256": self.recipe_sha256}
        if owner.exists():
            if _json(_read(owner, 64000)) != selected:
                raise StateError("Registry local state has another owner")
        else:
            tcg._write(owner, service_recipe.canonical(selected))
        output = self.journal.run_dir / ("registry-" + uuid4().hex)
        with _new_directory(output) as directory:
            _write(
                directory,
                "intent.json",
                {
                    "binding": binding,
                    "action": action,
                    "operation_id": operation,
                    "recipe_sha256": self.recipe_sha256,
                    "module_hashes": hashes,
                },
            )
            self._admit(context, action, deadline, operation_id)
            try:
                observation = self._dispatch(recipe, packet, audit, deadline)
            except (tcg.TCGError, StateError, ValueError, OSError):
                observation = {
                    "bootstrap": "unknown",
                    "storage_qualification": "unknown",
                    "cleanup": "unknown",
                    "mutation_effect": "unknown",
                    "error": "dispatch-interrupted-reconcile-required",
                    "resources": [],
                }
            report = {
                "schema_version": 1,
                "kind": "owned-zot-component",
                "run_id": context.run_id,
                "operation_id": operation,
                "action": action,
                "binding": binding,
                "recipe_sha256": self.recipe_sha256,
                "execution_mode": context.execution_mode,
                "identities": dict(self.journal.identities),
                "canonical_success": False,
                "complete_cleanup": False,
                "whole_phase_mutation_effect": "unknown",
                "observation": observation,
            }

            def audit_output():
                self._admit(context, action, deadline, operation_id)
                _verify_output_path(output, directory)
                if _json(_read(owner, 64000)) != selected:
                    raise StateError("Registry owner changed during operation")

            terminal_identity = None
            try:
                audit_output()
                terminal_identity = _write_terminal(directory, "registry.json", report)
                os.fsync(directory)
                audit_output()
            except BaseException:
                if terminal_identity is not None:
                    registry_qualification._withdraw(directory, "registry.json", terminal_identity)
                raise
        result = self._cleanup_observations.retain(
            PhaseOutcome(
                "incomplete",
                "owned-registry-component-only",
                (output / "intent.json", output / "registry.json"),
                mutation_effect="unknown",
            ),
            report,
        )
        if action == "up":
            self._link_outcome = result
        if action == "down":
            self._link_down = True
        return result

    def up(self, context):
        return self._perform(context, "up")

    def down(self, context):
        return self._perform(context, "down")

    def reconcile(self, context, operation_id):
        return self._perform(context, "reconcile", operation_id)

    def cleanup_observation(self, outcome):
        return self._cleanup_observations.observe(outcome)

    def component_observation(self, outcome):
        return self._cleanup_observations.observe(outcome)

    def validate_cleanup(self, receipts):
        raise StateError("Registry component cannot release the factory environment")

    def service_endpoint(self, context, *, reconciliation_operation=None):
        """Original registry ID and pinned public CA, without reconstructing authority."""
        from .owned_service_links import _endpoint_context
        _endpoint_context(self.journal, context, reconciliation_operation=reconciliation_operation)
        from .owned_services import _freeze

        if self._link_outcome is None or self._link_down or context.run_id != self.journal.state["run_id"]:
            raise StateError("Registry endpoint requires its original live outcome")
        report = self.component_observation(self._link_outcome)
        recipe_raw = _read(_safe_path(self.recipe_path), 64000)
        if _sha(recipe_raw) != self.recipe_sha256:
            raise StateError("Registry route recipe changed")
        recipe = service_recipe.validate(_json(recipe_raw))
        actual = report["observation"]
        if actual.get("bootstrap") != "container-running" or actual.get("storage_qualification") != "pass":
            raise StateError("Registry endpoint is not directly qualified")
        roles = {}
        for role in ("registry", "network"):
            rows = [r for r in actual["resources"] if r.get("role") == role and r.get("state") == "present"]
            if len(rows) != 1 or not re.fullmatch("[a-f0-9]{64}", rows[0].get("id", "")):
                raise StateError("Registry original exact resource identity is unavailable")
            roles[role] = {
                "kind": "network" if role == "network" else "container",
                "id": rows[0]["id"],
                "role": role,
                "labels": {registry_remote.LABEL: report["binding"], "io.devsecops.registry.role": role},
            }
        ca = recipe["materials"]["ca"]
        if _sha(_read(Path(ca["path"]), 1000000)) != ca["sha256"]:
            raise StateError("Registry route CA changed")
        return _freeze(
            {
                "run_id": context.run_id,
                "identities": dict(context.identities),
                "binding": report["binding"],
                "origin": recipe["origin"],
                "port": 8443,
                "ca_path": ca["path"],
                "ca_sha256": ca["sha256"],
                **roles,
            }
        )
