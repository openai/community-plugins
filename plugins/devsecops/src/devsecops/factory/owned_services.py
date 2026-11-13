"""Same-process owned GitLab service transport and direct cleanup observations."""

from __future__ import annotations
import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shlex
import time
from types import MappingProxyType
from uuid import uuid4

from . import bootstrap_recipe, services_remote, tcg
from .lifecycle import PhaseContext, PhaseOutcome, _load_inputs
from .native_codex import _package
from .owned_linux import _plain
from .state import RunJournal, StateError, _json, _read, _safe_path, _sha

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
MODULES = (
    "owned_services.py",
    "bootstrap_recipe.py",
    "services_remote.py",
    "tcg.py",
    "runner_install.py",
    "platform_bootstrap.py",
)
BOOTSTRAP = """import base64,json,sys,types
p=json.loads(sys.stdin.buffer.read(3000001))
def module(name,raw):
 m=types.ModuleType(name);sys.modules[name]=m
 exec(compile(base64.b64decode(raw,validate=True),name,'exec'),m.__dict__)
 return m
try:
 s=module('devsecops_services_support',p.pop('support'))
 r=module('devsecops_services_recipe',p.pop('recipes'))
 d=module('devsecops_services_driver',p.pop('driver'))
 result=d.execute(p,s,r)
 print(json.dumps({'binding':p['binding'],'operation_id':p['operation_id'],'observation':result},sort_keys=True))
except Exception:
 print('{"error":"owned-service-operation-unknown-reconcile-required"}')
 sys.exit(2)
"""


@dataclass(frozen=True)
class ServiceTransport:
    transport: dict


def _freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze(child) for child in value)
    return value


class OwnedServiceStack:
    def __init__(self, driver, *, recipe_path, recipe_sha256):
        if type(driver.journal) is not RunJournal or not re.fullmatch("[a-f0-9]{64}", recipe_sha256):
            raise StateError("Owned services require a current journal and pinned recipe")
        self.driver, self.journal = driver, driver.journal
        self.recipe_path, self.recipe_sha256 = Path(recipe_path), recipe_sha256
        self._pid, self._outcomes, self._started = os.getpid(), [], False
        self._principal, self._runner, self._unknown = None, None, False
        self._foundation_transport = None
        self._service_binding = None
        self._evidence = {}

    def bind_foundation_transport(self, foundation):
        from .owned_fresh_foundation import OwnedFreshRKE2Foundation

        if (
            type(foundation) is not OwnedFreshRKE2Foundation
            or foundation.journal is not self.journal
            or self._foundation_transport is not None
            or self._started
        ):
            raise StateError("Service transport requires the original fresh foundation exactly once")
        self._foundation_transport = foundation

    def _admit(self, context, action, operation_id=None):
        if (
            type(context) is not PhaseContext
            or self._pid != os.getpid()
            or not self.journal._lifecycle_locked
            or time.monotonic() >= context.deadline
        ):
            raise StateError("Owned services require the original bounded lifecycle context")
        state = self.journal.state
        lifecycle, phase = state["lifecycle"], _plain(context.phase)
        selected = phase["operation"] if action == "reconcile" else "up" if action == "runner" else action
        if (
            selected not in ("up", "down")
            or phase["operation"] != selected
            or context.run_id != state["run_id"]
            or context.run_dir != self.journal.run_dir
            or _plain(context.identities) != self.journal.identities
            or _plain(context.input_artifacts) != lifecycle["inputs"]
            or context.execution_mode != lifecycle["execution_mode"]
            or lifecycle["driver_id"] != "owned-linux"
            or phase not in lifecycle["phases"]
            or lifecycle["final_result"] is not None
        ):
            raise StateError("Owned service operation differs from the exact current lifecycle")
        operation = self.journal.current_mutation(phase["id"], selected, reconciliation=action == "reconcile")
        if operation_id is not None and operation != operation_id:
            raise StateError("Owned service reconciliation operation differs")
        paths = {name: self.journal._artifact_path(ref["path"]) for name, ref in lifecycle["inputs"].items()}
        _, docs = _load_inputs(paths, self.journal.identities, state["environment_id"])
        if docs != _plain(context.inputs) or docs["config"]["profile"] != "bigbang-live":
            raise StateError("Owned service selected inputs differ")
        config = docs["config"]
        if (
            "cleanup" not in config["allowed_mutations"]
            or selected == "up"
            and "provision" not in config["allowed_mutations"]
        ):
            raise StateError("Owned service operation lacks exact provision/cleanup scope")
        if selected == "up" and datetime.now(timezone.utc) >= datetime.fromisoformat(
            config["expires_at"].replace("Z", "+00:00")
        ):
            raise StateError("Owned service provisioning window expired")
        raw = _read(_safe_path(self.recipe_path), 256000)
        if _sha(raw) != self.recipe_sha256 or not any(
            ref["sha256"] == self.recipe_sha256 for ref in state["active_mutation"]["intent_artifacts"]
        ):
            raise StateError("Owned service recipe must match the current mutation intent")
        recipe = bootstrap_recipe.validate(_json(raw))
        service = [row for row in config["services"] if row["id"] == "gitlab"]
        if (
            recipe["environment"] != state["environment_id"]
            or len(service) != 1
            or service[0]["origin"] != recipe["services"]["origin"]
            or service[0].get("ca_file") != recipe["services"]["materials"]["ca"]["path"]
            or service[0].get("operator_auth", {}).get("token_env")
            != recipe["services"]["credentials"]["operator_token_env"]
        ):
            raise StateError("Owned GitLab recipe differs from the approved service/credential scope")
        _, paths = _package(
            PACKAGE_ROOT,
            self.journal.identities["package_sha256"],
            self.driver.plugin_id,
            self.driver.plugin_version,
            context.deadline,
        )
        hashes = {}
        for name in MODULES:
            path = PACKAGE_ROOT / "src/devsecops/factory" / name
            hashes["factory/" + name] = _sha(_read(path, 300000))
            if paths.get("src/devsecops/factory/" + name) != hashes["factory/" + name]:
                raise StateError("Owned service module differs from the selected installed package")
        return operation, recipe, hashes

    def _dispatch(self, recipe, packet, audit, deadline):
        captured = {}
        for field, module in (("support", tcg), ("recipes", bootstrap_recipe), ("driver", services_remote)):
            file = Path(module.__file__).resolve()
            raw = _read(file, 300000)
            if file.parent != PACKAGE_ROOT / "src/devsecops/factory" or _sha(raw) != packet[
                "package_modules"
            ].get("factory/" + file.name):
                raise StateError("Owned service transport module differs from admitted package")
            captured[field] = base64.b64encode(raw).decode()
        raw = bootstrap_recipe.canonical({**packet, **captured})
        if len(raw) > 3000000:
            raise StateError("Owned service transport input exceeds its bound")
        selected_transport = recipe["services"]["transport"]
        connection = (
            tcg._ssh_context(ServiceTransport(selected_transport), deadline, audit)
            if self._foundation_transport is None
            else self._foundation_transport.command_context(selected_transport, self.journal, deadline, audit)
        )
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
            raise StateError("Owned service operation has uncertain effects; reconciliation is required")
        return response["observation"]

    def _perform(self, context, action, operation_id=None, *, runner=None):
        operation, recipe, hashes = self._admit(context, action, operation_id)
        if self._unknown and action not in ("reconcile",):
            raise StateError("Owned service unknown effects must be reconciled before further writes")
        if action == "up":
            if self._started:
                raise StateError("Owned service bootstrap is one-shot")
            self._started = True
        private = tcg._private(_safe_path(Path(recipe["services"]["private_state_directory"])), create=True)
        if (
            private == self.journal.run_dir
            or private in self.journal.run_dir.parents
            or self.journal.run_dir in private.parents
            or PACKAGE_ROOT in private.parents
        ):
            raise StateError("Owned service private state must be separate from the run/package")
        audit = tcg._private(private / "transport", create=True)
        binding = bootstrap_recipe.binding(
            recipe, run_id=context.run_id, identities=dict(self.journal.identities), package_modules=hashes
        )
        self._service_binding = binding
        packet = {
            "action": action,
            "operation_id": operation,
            "run_id": context.run_id,
            "identities": dict(self.journal.identities),
            "package_modules": hashes,
            "binding": binding,
            "recipe": recipe,
            "timeout": min(1495, max(0.001, context.deadline - time.monotonic() - 5)),
            "expires_at": context.inputs["config"]["expires_at"],
        }
        if action in ("up", "runner"):
            materials = {}
            for name, ref in recipe["services"]["materials"].items():
                path = _safe_path(Path(ref["path"]))
                raw = _read(path, 1000000)
                if _sha(raw) != ref["sha256"] or name == "private_key" and path.stat().st_mode & 0o077:
                    raise StateError("Owned service private material differs from the exact selected pin")
                materials[name] = base64.b64encode(raw).decode()
            packet["materials"] = materials
        if action == "up":
            credentials = recipe["services"]["credentials"]
            packet["root_password"] = os.environ.get(credentials["root_password_env"])
            packet["operator_token"] = os.environ.get(credentials["operator_token_env"])
            if any(
                not isinstance(packet[key], str) or not 20 <= len(packet[key]) <= 512
                for key in ("root_password", "operator_token")
            ):
                raise StateError("Owned service selected private credentials are unavailable")
        elif action == "runner":
            packet.update(runner)
        output = self.journal.run_dir / ("services-" + uuid4().hex)
        output.mkdir(mode=0o700)
        tcg._write(
            output / "intent.json",
            bootstrap_recipe.canonical(
                {
                    "action": action,
                    "operation_id": operation,
                    "binding": binding,
                    "recipe_sha256": self.recipe_sha256,
                    "package_modules": hashes,
                }
            ),
        )
        self._evidence[output / "intent.json"] = _sha(_read(output / "intent.json", 1000000))
        self._admit(context, action, operation_id)
        try:
            observed = self._dispatch(recipe, packet, audit, context.deadline)
        except BaseException:
            self._unknown = True
            tcg._write(
                output / "unknown.json",
                bootstrap_recipe.canonical(
                    {
                        "operation_id": operation,
                        "action": action,
                        "mutation_effect": "unknown",
                        "reconciliation_required": True,
                    }
                ),
            )
            self._evidence[output / "unknown.json"] = _sha(_read(output / "unknown.json", 1000000))
            raise
        if (
            type(observed) is not dict
            or type(observed.get("resources")) is not list
            or type(observed.get("unresolved_operations")) is not list
        ):
            self._unknown = True
            raise StateError("Owned service response does not establish resource ownership")
        self._unknown = bool(observed["unresolved_operations"])
        if action == "up" and observed.get("bootstrap") == "server-ready" and not self._unknown:
            self._principal = deepcopy(observed["principal"])
        if action == "runner" and not self._unknown:
            self._runner = {
                "runner_id": observed["runner_id"],
                "runner_manager_system_identity": observed["runner_system_id"],
            }
        value = {
            "kind": "owned-service-stack",
            "run_id": context.run_id,
            "operation_id": operation,
            "action": action,
            "identities": dict(self.journal.identities),
            **observed,
            "service_ids": ["gitlab"],
        }
        # Supplied source material remains operator-owned, even after owned volume deletion.
        value["resources"].extend(
            {
                "identity": {"kind": "operator-input", "path": ref["path"], "sha256": ref["sha256"]},
                "disposition": "preserve",
                "state": "preserved",
            }
            for ref in recipe["services"]["materials"].values()
        )
        value["resources"].append(
            {
                "identity": {
                    "kind": "operator-credential-references",
                    "references": list(recipe["services"]["credentials"].values()),
                },
                "disposition": "preserve",
                "state": "preserved",
            }
        )
        if observed.get("principal"):
            value["resources"].append(
                {
                    "identity": {
                        "kind": "server-seeded-root-account",
                        "user_id": observed["principal"]["owner_user_id"],
                    },
                    "disposition": "remove",
                    "state": "absent" if observed["complete_cleanup"] else "present",
                    "observation": "destroyed-with-owned-server"
                    if observed["complete_cleanup"]
                    else "owned-server-account-observed",
                }
            )
        artifact = output / "observation.json"
        tcg._write(artifact, bootstrap_recipe.canonical(value))
        self._evidence[artifact] = _sha(_read(artifact, 1000000))
        outcome = PhaseOutcome(
            "incomplete",
            "owned-service-scope-observed",
            (output / "intent.json", artifact),
            mutation_effect=value["mutation_effect"],
        )
        self._outcomes.append((outcome, _freeze(deepcopy(value)), _sha(_read(artifact, 1000000))))
        return outcome

    def up(self, context):
        return self._perform(context, "up")

    def start_runner(self, context, *, runner_id, runner_token, description, install_plan):
        if (
            self._principal is None
            or self._runner is not None
            or type(runner_id) is not int
            or runner_id <= 0
        ):
            raise StateError("Owned runner requires the direct current initialized service")
        from .runner_install import require_runner_install

        selected = require_runner_install(install_plan, self, context)
        if selected.get("runner_profile") == "owned-runner-vm-v1":
            raise StateError("Dedicated runner VM cannot use the legacy Docker runner path")
        packet = {
            "runner_id": runner_id,
            "runner_token": runner_token,
            "runner_description": description,
            "runner_inputs": {
                path: {
                    "content": base64.b64encode(row["content"]).decode(),
                    "sha256": _sha(row["content"]),
                    "private": row["private"],
                }
                for path, row in selected["entries"].items()
            },
            "runner_environment": selected["environment"],
            "runner_secrets": selected["secrets"],
        }
        outcome = self._perform(context, "runner", runner=packet)
        self._runner.update(
            runner_input_config_sha256=selected["config_sha256"],
            original_template_sha256=selected["original_template_sha256"],
            service_trust=selected["service_trust"],
        )
        return outcome

    def principal(self, context):
        self._admit(context, "up")
        if self._principal is None or self._unknown:
            raise StateError("Owned GitLab principal has not been directly observed")
        return _freeze(deepcopy(self._principal))

    def runner(self, context):
        self._admit(context, "up")
        if self._runner is None or self._unknown:
            raise StateError("Owned GitLab runner has not been directly observed")
        return _freeze(deepcopy(self._runner))

    def down(self, context):
        return self._perform(context, "down")

    def reconcile(self, context, operation_id):
        return self._perform(context, "reconcile", operation_id)

    def cleanup_observation(self, outcome):
        if self._pid != os.getpid():
            raise StateError("Owned service observation belongs to another process")
        for original, value, digest in self._outcomes:
            if original is outcome:
                if _sha(_read(original.artifacts[-1], 1000000)) != digest:
                    raise StateError("Owned service observation artifact changed")
                return value
        raise StateError("Owned service observation requires the original direct outcome")

    def evidence_artifacts(self, context):
        if (
            self._pid != os.getpid()
            or context.run_id != self.journal.state["run_id"]
            or dict(context.identities) != self.journal.identities
        ):
            raise StateError("Service evidence requires its original run")
        for path, digest in self._evidence.items():
            if _sha(_read(path, 1000000)) != digest:
                raise StateError("Original service public evidence changed")
        return tuple(self._evidence)

    phase_artifacts = evidence_artifacts

    def _endpoint_read_admission(self, context, operation_id):
        """Exact readonly unknown-operation scope, separate from service effects."""
        from .owned_service_links import _endpoint_context

        _endpoint_context(self.journal, context, reconciliation_operation=operation_id)
        if self._pid != os.getpid():
            raise StateError("Service endpoint belongs to another process")
        state = self.journal.state
        lifecycle = state["lifecycle"]
        if lifecycle["driver_id"] != "owned-linux":
            raise StateError("Service endpoint requires the fixed original driver")
        paths = {name: self.journal._artifact_path(ref["path"]) for name, ref in lifecycle["inputs"].items()}
        _, docs = _load_inputs(paths, self.journal.identities, state["environment_id"])
        if docs != _plain(context.inputs) or docs["config"]["profile"] != "bigbang-live":
            raise StateError("Service endpoint selected inputs differ")
        self.driver._auxiliary_artifacts()
        _, members = _package(
            PACKAGE_ROOT,
            self.journal.identities["package_sha256"],
            self.driver.plugin_id,
            self.driver.plugin_version,
            context.deadline,
        )
        hashes = {}
        for name in MODULES:
            path = PACKAGE_ROOT / "src/devsecops/factory" / name
            hashes["factory/" + name] = _sha(_read(path, 300000))
            if members.get("src/devsecops/factory/" + name) != hashes["factory/" + name]:
                raise StateError("Service endpoint module differs from its original package")
        raw = _read(_safe_path(self.recipe_path), 256000)
        if _sha(raw) != self.recipe_sha256:
            raise StateError("Service endpoint selected recipe changed")
        recipe = bootstrap_recipe.validate(_json(raw))
        services = [row for row in docs["config"]["services"] if row["id"] == "gitlab"]
        if (
            recipe["environment"] != state["environment_id"]
            or len(services) != 1
            or services[0]["origin"] != recipe["services"]["origin"]
            or services[0].get("ca_file") != recipe["services"]["materials"]["ca"]["path"]
            or services[0].get("operator_auth", {}).get("token_env")
            != recipe["services"]["credentials"]["operator_token_env"]
            or bootstrap_recipe.binding(
                recipe,
                run_id=context.run_id,
                identities=dict(self.journal.identities),
                package_modules=hashes,
            )
            != self._service_binding
        ):
            raise StateError("Service endpoint differs from its original owned instance binding")

    def service_endpoint(self, context, *, reconciliation_operation=None):
        """Original public identities for fixed network routing, never a saved receipt grant."""
        if reconciliation_operation is None:
            self.driver._admit(context, context.deadline)
        else:
            self._endpoint_read_admission(context, reconciliation_operation)
        self.evidence_artifacts(context)
        if self._unknown or self._service_binding is None or not self._outcomes:
            raise StateError("Owned service endpoint has not been directly observed")
        raw = _read(_safe_path(self.recipe_path), 256000)
        if _sha(raw) != self.recipe_sha256:
            raise StateError("Owned service endpoint recipe changed")
        recipe = bootstrap_recipe.validate(_json(raw))
        report = self.cleanup_observation(self._outcomes[-1][0])
        targets = {}
        for role in ("server", "network", "runner"):
            rows = [
                row
                for row in report["resources"]
                if row["identity"].get("role") == role and row["state"] == "present"
            ]
            if not rows and role == "runner":
                targets[role] = None
                continue
            if len(rows) != 1:
                raise StateError("Owned service endpoint resource identity is unavailable")
            identity = dict(rows[0]["identity"])
            identity["labels"] = {services_remote.LABEL: self._service_binding, services_remote.ROLE: role}
            targets[role] = identity
        ca = recipe["services"]["materials"]["ca"]
        if _sha(_read(Path(ca["path"]), 1000000)) != ca["sha256"]:
            raise StateError("Owned service endpoint public trust changed")
        return _freeze(
            {
                "run_id": context.run_id,
                "identities": dict(context.identities),
                "binding": self._service_binding,
                "origin": recipe["services"]["origin"],
                "port": 8444,
                "ca_path": ca["path"],
                "ca_sha256": ca["sha256"],
                **targets,
            }
        )

    def inspect_preconditions(self, context):
        """Read-only image availability, through the exact selected SSH transport."""
        self.driver._admit(context, context.deadline)
        if context.phase["operation"] != "preflight":
            raise StateError("Owned service preconditions require canonical preflight")
        raw = _read(_safe_path(self.recipe_path), 256000)
        if _sha(raw) != self.recipe_sha256:
            raise StateError("Owned service selected recipe changed")
        recipe = bootstrap_recipe.validate(_json(raw))
        _, paths = _package(
            PACKAGE_ROOT,
            self.journal.identities["package_sha256"],
            self.driver.plugin_id,
            self.driver.plugin_version,
            context.deadline,
        )
        hashes = {}
        for name in MODULES:
            hashes["factory/" + name] = _sha(_read(PACKAGE_ROOT / "src/devsecops/factory" / name, 300000))
            if paths.get("src/devsecops/factory/" + name) != hashes["factory/" + name]:
                raise StateError("Owned service preflight module differs from selected package")
        private = tcg._private(_safe_path(Path(recipe["services"]["private_state_directory"])), create=True)
        audit = tcg._private(private / "transport", create=True)
        packet = {
            "action": "preflight",
            "operation_id": context.run_id,
            "run_id": context.run_id,
            "identities": dict(self.journal.identities),
            "package_modules": hashes,
            "binding": bootstrap_recipe.binding(
                recipe,
                run_id=context.run_id,
                identities=dict(self.journal.identities),
                package_modules=hashes,
            ),
            "recipe": recipe,
            "timeout": min(90, max(0.001, context.deadline - time.monotonic() - 5)),
            "expires_at": context.inputs["config"]["expires_at"],
        }
        observed = self._dispatch(recipe, packet, audit, context.deadline)
        if (
            observed.get("kind") != "owned-service-preconditions"
            or observed.get("effects") != "none"
            or observed.get("run_id") != context.run_id
        ):
            raise StateError("Owned service precondition observation differs")
        return _freeze(deepcopy(observed))

    def token_status(self, context, *, token_id, owner_user_id):
        """Actual read-only owned-server DB status; HTTP401 alone is insufficient."""
        operation, recipe, hashes = self._admit(context, "reconcile")
        if (
            self._principal is None
            or type(token_id) is not int
            or type(owner_user_id) is not int
            or self._principal.get("token_id") != token_id
            or self._principal.get("owner_user_id") != owner_user_id
        ):
            raise StateError("Token status requires this exact directly created token")
        private = tcg._private(_safe_path(Path(recipe["services"]["private_state_directory"])))
        audit = tcg._private(private / "transport")
        binding = bootstrap_recipe.binding(
            recipe, run_id=context.run_id, identities=dict(self.journal.identities), package_modules=hashes
        )
        packet = {
            "action": "token-status",
            "operation_id": operation,
            "run_id": context.run_id,
            "identities": dict(self.journal.identities),
            "package_modules": hashes,
            "binding": binding,
            "recipe": recipe,
            "timeout": min(110, max(0.001, context.deadline - time.monotonic() - 5)),
            "expires_at": context.inputs["config"]["expires_at"],
            "token_selection": {"token_id": token_id, "owner_user_id": owner_user_id},
        }
        observed = self._dispatch(recipe, packet, audit, context.deadline)
        if (
            observed.get("instance_binding") != binding
            or observed.get("token_id") != token_id
            or observed.get("owner_user_id") != owner_user_id
            or observed.get("token_name") != "factory-owned"
            or type(observed.get("revoked")) is not bool
        ):
            raise StateError("Owned token status observation differs from original instance/identity")
        return _freeze(deepcopy(observed))
