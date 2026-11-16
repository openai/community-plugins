"""Canonical retained operation coordinator; fixed producers, no callbacks.

All effectful entry points require the current canonical journal operation.
Downloaded reports, source selections and recipe hashes do not grant effects.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import time
from weakref import WeakKeyDictionary

from . import deployment_commit, deployment_proposal, git_base_possession, git_transport
from .ci_publication import verify_ci_publication
from .gitlab_bootstrap import BootstrapScope
from .lifecycle import PhaseContext, PhaseOutcome
from .mission_operation import _configuration as build_configuration
from .mission_security_operation import _configuration as security_configuration
from .operations_recipe import operations_configuration
from .owned_ci import OwnedCIPipeline, PipelineSelection, require_ci_origin
from .state import MAX_RESULT_ARTIFACTS, RunJournal, StateError, _json, _read, _sha

_GIT = WeakKeyDictionary()

# Every canonical ordinary operation has a named owner/required direct input.
# These are gates, not fabricated success receipts for missing implementation.
PHASE_ROUTES = {
    "preflight": "bootstrap",
    "prepare": "bootstrap",
    "provision": "bootstrap",
    "build-promote": "ci-publication-and-git",
    "primary-baseline": "runtime-baseline",
    "primary-inject": "git-fault",
    "primary-confirm-fault": "runtime-fault",
    "primary-review": "native-review",
    "primary-diagnose": "native-diagnose",
    "primary-correction": "native-correction",
    "primary-validate-correction": "validated-native-patch",
    "primary-deliver": "reviewed-git-delivery",
    "primary-probes": "probe-actors",
    "primary-recovery": "native-recovery",
    "primary-verify": "runtime-verification",
    "platform-dependency": "platform-fault-and-git-repair",
    "supply-chain-controls": "actual-subject-and-attestation-rejection",
    "evidence-gaps": "unknown-evidence-and-fresh-native-review",
    "reset": "git-baseline-restoration",
    "replay-baseline": "runtime-baseline",
    "replay-inject": "git-fault",
    "replay-confirm-fault": "runtime-fault",
    "replay-review": "native-review",
    "replay-diagnose": "native-diagnose",
    "replay-correction": "native-correction",
    "replay-validate-correction": "validated-native-patch",
    "replay-deliver": "reviewed-git-delivery",
    "replay-probes": "probe-actors",
    "replay-recovery": "native-recovery",
    "replay-verify": "runtime-verification",
    "export": "export",
    "cleanup": "cleanup",
    "finalize": "finalize",
}


class PreparedOperationGit:
    """Actual same-run CI publication and newly prepared direct Git objects."""

    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Operation Git preparation requires the current coordinator")

    def __reduce__(self):
        raise TypeError("Operation Git preparation cannot be serialized")


def require_operation_git(value, *, journal):
    record = _GIT.get(value) if type(value) is PreparedOperationGit else None
    if record is None or record["pid"] != os.getpid() or record["journal"] is not journal:
        raise StateError("Direct current operation Git preparation required")
    require_ci_origin(record["ci"], run_id=journal.state["run_id"], source_sha=record["source_commit"])
    scope = record["scope"]
    git_transport.require_prepared_git_transport(record["transport"], **scope)
    git_base_possession.require_observed_git_base(
        record["base"],
        transport=record["transport"],
        **scope,
        expected_config_sha256=record["destination_sha256"],
    )
    return record["transport"], record["base"], deepcopy(scope), record["destination_sha256"]


def require_operation_git_sources(value, *, journal):
    require_operation_git(value, journal=journal)
    record = _GIT[value]
    source = record["proposal"].describe()
    deployment_proposal.require_deployment_proposal(
        record["proposal"],
        **{
            name: source[name]
            for name in (
                "run_scope",
                "source_commit",
                "origin",
                "repository",
                "manifest_digest",
                "deployment_repository",
                "deployment_commit",
                "configuration_sha256",
            )
        },
    )
    deployment_commit.require_prepared_deployment_commit(record["commit"], **record["scope"])
    record["publication"].describe()
    return record["proposal"], record["commit"], record["publication"]


class OwnedOperationCoordinator:
    def __init__(self, journal, *, bootstrap, runtime=None, plugin_id=None, plugin_version=None):
        if type(journal) is not RunJournal:
            raise StateError("Current direct run journal required")
        # Import lazily to avoid a cycle through the root production coordinator.
        from .canonical_bootstrap import OwnedBootstrapCoordinator

        if type(bootstrap) is not OwnedBootstrapCoordinator or bootstrap.journal is not journal:
            raise StateError("Direct same-journal bootstrap coordinator required")
        self.journal, self.bootstrap, self.runtime = journal, bootstrap, runtime
        self.pid, self._thread = os.getpid(), threading.get_ident()
        self.plugin_id, self.plugin_version = plugin_id, plugin_version
        self._ci = self._prepared = self._publication = None
        self._deliveries = {}
        self._source_checkouts = {}
        self._correction_contracts = {}
        self._cases = None
        self._runtime_inputs = None
        self._runtime_image = self._locked_images = None
        self._image_reads = 0
        self._derived_evidence = {}

    def _admit(self, context, action=None):
        from .owned_linux import _plain

        journal = self.journal
        if (
            type(context) is not PhaseContext
            or self.pid != os.getpid()
            or self._thread != threading.get_ident()
            or not journal._lifecycle_locked
            or journal._process_id != os.getpid()
            or context.run_dir != journal.run_dir
            or context.remaining_seconds() <= 0
        ):
            raise StateError("Current bounded canonical operation context required")
        state = journal.state
        lifecycle = state.get("lifecycle")
        phase = context.phase
        if (
            lifecycle is None
            or lifecycle["driver_id"] != "owned-linux"
            or lifecycle["abort"] is not None
            or lifecycle["final_result"] is not None
            or state["run_id"] != context.run_id
            or dict(context.identities) != journal.identities
            or state["identities"] != journal.identities
            or (lifecycle["active_phase"] or {}).get("phase_id") != phase["id"]
            or phase["id"] not in PHASE_ROUTES
            or next((item for item in lifecycle["phases"] if item["id"] == phase["id"]), None)
            != _plain(phase)
            or context.execution_mode != lifecycle["execution_mode"]
        ):
            raise StateError("Canonical operation differs from active lifecycle")
        ref = lifecycle["inputs"]["config"]
        raw = _read(journal._artifact_path(ref["path"]), 64000)
        if _sha(raw) != ref["sha256"] or len(raw) != ref["bytes"]:
            raise StateError("Canonical configuration bytes changed")
        selected = _json(raw)
        if selected != _plain(context.inputs["config"]):
            raise StateError("Context configuration differs from frozen canonical inputs")
        expires = datetime.fromisoformat(selected["expires_at"].replace("Z", "+00:00"))
        if expires.tzinfo is None or datetime.now(timezone.utc) >= expires:
            raise StateError("Operation configuration expired")
        if action is not None:
            grant = {
                "build-promote": "build",
                "up": "provision",
                "prepare": "provision",
                "down": "cleanup",
            }.get(action, action)
            if context.execution_mode != "operator" or grant not in selected["allowed_mutations"]:
                raise StateError("Current operator mutation selection required")
            operation = journal.current_mutation(phase["id"], action)
        else:
            operation = None
        return operation

    def _auxiliary(self, name):
        selected = self.journal.state["lifecycle"].get("auxiliary_inputs", {}).get(name)
        if selected is None:
            raise StateError("Required operation input missing: " + name)
        path = self.journal._artifact_path(selected["path"])
        raw = _read(path, 64000)
        if _sha(raw) != selected["sha256"] or len(raw) != selected["bytes"]:
            raise StateError("Frozen operation input changed")
        return path, selected["sha256"], raw

    def _recipe(self):
        _, _, raw = self._auxiliary("owned_operations_recipe")
        value = operations_configuration(raw)
        state = self.journal.state
        if (
            value["run_scope"] != self.journal.identities
            or value["environment_id"] != state["environment_id"]
            or self.plugin_id is not None
            and value["plugin_id"] != self.plugin_id
            or self.plugin_version is not None
            and value["plugin_version"] != self.plugin_version
        ):
            raise StateError("Operation recipe differs from current run")
        return value

    def mutation_intent_artifacts(self, context):
        self._admit(context)
        self._recipe()
        return (self._auxiliary("owned_operations_recipe")[0],)

    def _host_configurations(self, context, ci_origin, root):
        """Derive only fresh owned project/source fields from retained origins."""
        from .canonical_bootstrap import require_bootstrap_authority
        from .owned_checkout import materialize_owned_repository, require_owned_checkout
        from .owned_git_delivery import delivery_writer_configuration
        from .git_destination import destination_configuration
        from devsecops.runtime.cli import _new_directory, _write
        from devsecops.runtime.context import canonical_bytes

        recipe = self._recipe()
        writer_path, writer_sha, writer_raw = self._auxiliary("owned_git_writer")
        if recipe["writer"]["sha256"] != writer_sha:
            raise StateError("Writer differs from separately frozen selection")
        writer = delivery_writer_configuration(writer_raw)
        if (
            writer["run_scope"] != self.journal.identities
            or writer["environment_id"] != self.journal.state["environment_id"]
        ):
            raise StateError("Writer differs from current owned run")
        authority, _ = self.bootstrap.gitlab(context)
        facts = require_bootstrap_authority(authority, self.journal)
        origin = require_ci_origin(ci_origin, run_id=context.run_id)
        if (
            origin["project_id"] != facts["projects"]["source"]["id"]
            or origin["source_sha"] != facts["projects"]["source"]["seed_commit"]
        ):
            raise StateError("CI execution differs from actual owned source seed")
        inputs = {
            name: self.selected_component(name)
            for name in (
                "build",
                "staging",
                "security",
                "signing",
                "publication",
                "proposal",
                "commit",
                "destination",
            )
        }
        selected = {name: _json(item[2]) for name, item in inputs.items()}
        commit = deployment_commit.commit_configuration(inputs["commit"][2])
        checkout = materialize_owned_repository(
            context,
            bootstrap=self.bootstrap,
            role="deployment",
            git_path=Path(commit["git_path"]),
            git_sha256=commit["git_sha256"],
            output=root / "checkout",
        )
        repository = require_owned_checkout(checkout, journal=self.journal)
        selected["build"]["source"]["revision"] = origin["source_sha"]
        for name in ("proposal", "commit"):
            selected[name]["deployment_repository"] = repository["repository"]
            selected[name]["deployment_commit"] = repository["revision"]
        project = facts["projects"]["deployment"]
        selected["destination"].update(
            origin=facts["origin"],
            project_id=project["id"],
            kind="owned-git-destination-observation-configuration",
            api_read_credential={"kind": "owned-reader", "transport": "api"},
            git_read_credential={"kind": "owned-reader", "transport": "git"},
            repository_path="/" + project["path_with_namespace"] + ".git",
            path_with_namespace=project["path_with_namespace"],
            base_ref="refs/heads/main",
            base_commit=repository["revision"],
            target_ref=writer["target_ref_prefix"] + context.run_id + "/" + context.phase["id"],
        )
        selected["commit"]["proposal_configuration_sha256"] = _sha(canonical_bytes(selected["proposal"]))
        # Revalidate exact concrete schemas before deriving any downstream handle.
        build_configuration(canonical_bytes(selected["build"]))
        deployment_proposal.proposal_configuration(canonical_bytes(selected["proposal"]))
        deployment_commit.commit_configuration(canonical_bytes(selected["commit"]))
        destination_configuration(canonical_bytes(selected["destination"]))
        pairs = {}
        directory = root / "derived-inputs"
        with _new_directory(directory) as fd:
            for name in inputs:
                raw = (
                    canonical_bytes(selected[name])
                    if name in ("build", "proposal", "commit", "destination")
                    else inputs[name][2]
                )
                ref = _write(fd, name + ".json", raw.decode())
                pairs[name] = (directory / (name + ".json"), ref["sha256"], raw)
                self._derived_evidence[directory / (name + ".json")] = ref
            derivation = _write(
                fd,
                "derivation.json",
                {
                    "kind": "owned-host-input-derivation",
                    "run_id": context.run_id,
                    "source_sha": origin["source_sha"],
                    "deployment_project_id": project["id"],
                    "deployment_seed_commit": repository["revision"],
                    "original_sha256": {name: item[1] for name, item in inputs.items()},
                    "derived_sha256": {name: item[1] for name, item in pairs.items()},
                    "authority": "not-serialized",
                    "local_checkout_only": True,
                },
            )
            self._derived_evidence[directory / "derivation.json"] = derivation
            os.fsync(fd)
        self._checkout = checkout
        return pairs, authority, (writer_path, writer_sha)

    def _prepare_git(self, context, ci_origin, selector_raw, root):
        """Recompute local proposal/commit/pack from freshly verified CI image."""
        self._admit(context, "build-promote")
        pairs, bootstrap_authority, writer = self._host_configurations(context, ci_origin, root)
        configurations = {name: {"path": str(item[0]), "sha256": item[1]} for name, item in pairs.items()}
        raw = {name: item[2] for name, item in pairs.items()}
        build = build_configuration(raw["build"])
        security = security_configuration(raw["security"])
        proposed = deployment_proposal.proposal_configuration(raw["proposal"])
        source = require_ci_origin(ci_origin)["source_sha"]
        if build["source"]["revision"] != source or build["run_scope"] != self.journal.identities:
            raise StateError("Actual CI source differs from pinned invocation")
        public_path, public_sha, _ = pairs["publication"]
        from .canonical_bootstrap import require_bootstrap_authority

        bootstrap_facts = require_bootstrap_authority(bootstrap_authority, self.journal)
        publication = verify_ci_publication(
            ci_origin=ci_origin,
            selector_raw=selector_raw,
            run_scope=self.journal.identities,
            source_commit=source,
            architecture=context.inputs["lock"]["platform"]["architecture"],
            policy_sha256=security["policy_sha256"],
            config=public_path,
            expected_config_sha256=public_sha,
            expected_ci_publication_config_sha256=bootstrap_facts["runner_input_config_sha256"][
                "publication"
            ],
            output=root / "publication",
            timeout=min(300, context.remaining_seconds() - 4),
            service_route=self.bootstrap.service_route(context, "registry"),
        )
        self._publication = publication
        bound = publication.describe()
        publication_scope = {
            name: bound[name]
            for name in (
                "run_scope",
                "source_commit",
                "architecture",
                "origin",
                "repository",
                "policy_sha256",
                "trust_policy_sha256",
                "publication_config_sha256",
            )
        }
        self._admit(context, "build-promote")
        report, proposal = deployment_proposal.propose_deployment_change(
            published=publication,
            **publication_scope,
            config=pairs["proposal"][0],
            expected_config_sha256=configurations["proposal"]["sha256"],
            output=root / "proposal",
            timeout=min(300, context.remaining_seconds() - 3),
        )
        if proposal is None or report["decision"] != "pass":
            raise StateError("Fresh CI image proposal incomplete")
        proposal_scope = {
            name: bound[name]
            for name in ("run_scope", "source_commit", "origin", "repository", "manifest_digest")
        }
        proposal_scope.update(
            deployment_repository=proposed["deployment_repository"],
            deployment_commit=proposed["deployment_commit"],
        )
        report, commit = deployment_commit.prepare_deployment_commit(
            proposal=proposal,
            **proposal_scope,
            proposal_configuration_sha256=configurations["proposal"]["sha256"],
            config=pairs["commit"][0],
            expected_config_sha256=configurations["commit"]["sha256"],
            output=root / "commit",
            timeout=min(120, context.remaining_seconds() - 2),
        )
        if commit is None or report["decision"] != "pass":
            raise StateError("Fresh CI deployment commit incomplete")
        scope = dict(
            run_scope=self.journal.identities,
            source_commit=source,
            deployment_commit=proposed["deployment_commit"],
            proposal_configuration_sha256=configurations["proposal"]["sha256"],
            configuration_sha256=configurations["commit"]["sha256"],
        )
        report, transport = git_transport.prepare_git_transport(
            prepared=commit,
            **scope,
            output=root / "transport",
            timeout=min(120, context.remaining_seconds() - 1),
        )
        if transport is None or report["decision"] != "pass":
            raise StateError("Fresh CI Git transport incomplete")
        destination_path, destination_sha, _ = pairs["destination"]
        reader_authority, reader_origin = self.bootstrap.reader(context)
        report, base = git_base_possession.verify_git_base_possession(
            transport=transport,
            **scope,
            config=destination_path,
            expected_config_sha256=destination_sha,
            output=root / "base",
            timeout=min(120, context.remaining_seconds()),
            reader_origin=reader_origin,
            bootstrap_authority=reader_authority,
            journal=self.journal,
            service_route=self.bootstrap.service_route(context, "gitlab"),
        )
        if base is None or report["decision"] != "pass":
            raise StateError("Fresh exact Git base observation incomplete")
        self._admit(context, "build-promote")
        value = object.__new__(PreparedOperationGit)
        _GIT[value] = {
            "pid": self.pid,
            "journal": self.journal,
            "ci": ci_origin,
            "source_commit": source,
            "scope": scope,
            "transport": transport,
            "base": base,
            "destination_sha256": destination_sha,
            "publication": publication,
            "proposal": proposal,
            "commit": commit,
            "root": root,
            "bootstrap": bootstrap_authority,
            "destination": destination_path,
            "writer": writer,
        }
        self._prepared = value
        require_operation_git(value, journal=self.journal)
        return value

    def build_promote(self, context):
        operation = self._admit(context, "build-promote")
        if context.phase["id"] != "build-promote" or not {"promote", "deliver", "provision", "probe"} <= set(
            context.inputs["config"]["allowed_mutations"]
        ):
            raise StateError("Current build, promotion, delivery, provisioning and probe selections required")
        recipe = self._recipe()
        # Validate the fixed runtime composition before the first CI/write.
        self._runtime_input_coordinator()
        from .canonical_bootstrap import require_bootstrap_authority

        authority, session = self.bootstrap.gitlab(context)
        observed = require_bootstrap_authority(authority, self.journal)
        self.journal.state  # Preserve the original journal read and its validation.
        scope = BootstrapScope(**dict(observed["scope"]))
        project = observed["projects"]["source"]
        root = self.journal.run_dir / "owned-operations"
        if not root.exists():
            root.mkdir(mode=0o700)
        selected = PipelineSelection(
            project["id"],
            project["seed_commit"],
            observed["runner_id"],
            observed["runner_manager_system_identity"],
            tuple(recipe["ci"]["required_jobs"]),
            recipe["ci"]["publication_job"],
            operation,
            context.run_id,
        )
        if self._ci is not None:
            raise StateError("Existing pipeline attempt requires reconciliation")
        self._ci = OwnedCIPipeline(
            session,
            scope,
            selected,
            journal_root=root,
            deadline=context.deadline,
            runner_origin=self.bootstrap.runner_origin(context),
        )
        self._ci.start()
        for index in range(recipe["ci"]["max_observations"]):
            self._admit(context, "build-promote")
            result = self._ci.observe()
            if result["state"] == "success":
                raw = self._ci.publication_report(result["origin"])
                candidate_root = root / "ci-delivery"
                candidate_root.mkdir(mode=0o700)
                prepared = self._prepare_git(context, result["origin"], raw, candidate_root)
                from .owned_git_delivery import deliver_prepared, merge_reviewed, require_delivery

                stage_authority, writer_origin = self.bootstrap.writer(context)
                record = _GIT[prepared]
                writer_path, writer_sha = record["writer"]
                delivered = deliver_prepared(
                    context,
                    journal=self.journal,
                    prepared=prepared,
                    bootstrap_authority=stage_authority,
                    writer_origin=writer_origin,
                    destination=record["destination"],
                    destination_sha256=record["destination_sha256"],
                    writer=writer_path,
                    writer_sha256=writer_sha,
                    plugin_id=recipe["plugin_id"],
                    plugin_version=recipe["plugin_version"],
                    service_route=self.bootstrap.service_route(context, "gitlab"),
                )
                # The owner session is independently refreshed and re-admitted
                # after the create-only Developer writer has returned readback.
                _, owner_session = self.bootstrap.gitlab(context)
                delivered = merge_reviewed(
                    context,
                    journal=self.journal,
                    delivery=delivered,
                    session=owner_session,
                    required_jobs=tuple(recipe["ci"]["delivery_required_jobs"]),
                    poll_seconds=recipe["ci"]["poll_seconds"],
                    max_observations=recipe["ci"]["max_observations"],
                )
                bound = require_delivery(delivered, journal=self.journal)
                if (
                    bound["delivery_stage"] != "merged-watched-branch"
                    or bound["watched_revision"] != bound["deployment_commit"]
                ):
                    raise StateError("Actual baseline Git delivery remains unobserved")
                self._retain_delivery(context, delivered, attempt="primary", stage="baseline")
                self.runtime.bind_mission_publication(context, self.publication())
                self.runtime.bind_runtime_image(context, self.runtime_image(context))
                self.runtime.bind_locked_images(context, self.locked_images(context))
                # Root composition supplies this fixed production method for
                # original F0 qualification and baseline actor preparation.
                # Its full result determines whether this phase can complete.
                return self._with_evidence(context, self.prepare_runtime(context))
            if result["state"] in ("failed", "canceled", "skipped", "manual"):
                return PhaseOutcome(
                    "incomplete",
                    "owned-ci-did-not-succeed",
                    artifacts=self.evidence_artifacts(context),
                    mutation_effect="unknown",
                )
            delay = min(recipe["ci"]["poll_seconds"], context.remaining_seconds())
            if delay <= 0 or index + 1 == recipe["ci"]["max_observations"]:
                break
            time.sleep(delay)
        return PhaseOutcome(
            "incomplete",
            "owned-ci-execution-unresolved",
            artifacts=self.evidence_artifacts(context),
            mutation_effect="unknown",
        )

    def evidence_artifacts(self, context):
        """Exact original public ledgers for this phase, never a directory scan."""
        self._admit(context)
        paths = set(self.bootstrap.evidence_artifacts(context))
        if self._ci is not None:
            paths.update(self._ci.evidence_artifacts(run_id=context.run_id))
        from .ci_publication import publication_evidence_artifacts, runtime_image_artifacts

        if self._publication is not None:
            paths.update(publication_evidence_artifacts(self._publication, run_scope=self.journal.identities))
        if self._runtime_image is not None:
            paths.update(runtime_image_artifacts(self._runtime_image, run_scope=self.journal.identities))
        if self._locked_images is not None:
            from .locked_images import locked_image_evidence_artifacts

            paths.update(locked_image_evidence_artifacts(self._locked_images, journal=self.journal))
        for path, ref in self._derived_evidence.items():
            raw = _read(path, ref["bytes"] + 1)
            if len(raw) != ref["bytes"] or _sha(raw) != ref["sha256"]:
                raise StateError("Original derived input evidence changed")
            paths.add(path)
        from .owned_checkout import checkout_evidence_artifacts

        if getattr(self, "_checkout", None) is not None:
            paths.update(checkout_evidence_artifacts(self._checkout, journal=self.journal))
        operation = context.phase["operation"]
        attempt = (
            "primary"
            if operation == "build-promote"
            else "replay"
            if operation == "reset"
            else context.phase["attempt"]
        )
        stage = {
            "build-promote": "baseline",
            "reset": "baseline",
            "inject": "fault",
            "deliver": "recovery",
        }.get(operation)
        key = (attempt, stage)
        if key in self._deliveries:
            from .owned_git_delivery import delivery_artifacts

            paths.update(delivery_artifacts(self._deliveries[key], journal=self.journal))
        if key in self._source_checkouts:
            paths.update(checkout_evidence_artifacts(self._source_checkouts[key], journal=self.journal))
        return self._bounded_artifacts(paths)

    @staticmethod
    def _bounded_artifacts(paths):
        result = tuple(sorted(set(paths), key=str))
        if len(result) >= MAX_RESULT_ARTIFACTS or any(
            not isinstance(path, Path) or not path.is_absolute() for path in result
        ):
            raise StateError("Operation evidence path closure exceeds its bound")
        total = 0
        for path in result:
            raw = _read(path, 16_000_000)
            total += len(raw)
            if total > 64_000_000:
                raise StateError("Operation evidence byte closure exceeds its bound")
        return result

    def _with_evidence(self, context, outcome):
        if type(outcome) is not PhaseOutcome:
            raise StateError("Original fixed runtime phase outcome required")
        paths = self._bounded_artifacts((*outcome.artifacts, *self.evidence_artifacts(context)))
        return replace(outcome, artifacts=paths)

    def prepared_git(self):
        require_operation_git(self._prepared, journal=self.journal)
        return self._prepared

    def publication(self):
        if self._publication is None:
            raise StateError("Actual CI publication has not been verified")
        self._publication.describe()
        return self._publication

    def runtime_image(self, context):
        from .runtime_image import refresh_runtime_image, require_runtime_image

        self._admit(context)
        if self._publication is None:
            raise StateError("Original actual CI publication is required")
        if self._runtime_image is not None:
            try:
                require_runtime_image(self._runtime_image, run_scope=self.journal.identities)
                return self._runtime_image
            except ValueError:
                pass  # A fresh read is permitted; the old authority is not extended.
        security = security_configuration(self.selected_component("security")[2])
        self._image_reads += 1
        root = self.journal.run_dir / "owned-operations" / ("runtime-image-" + str(self._image_reads))
        self._runtime_image = refresh_runtime_image(
            publication=self._publication,
            policy=Path(security["policy_path"]),
            expected_policy_sha256=security["policy_sha256"],
            listing=Path(security["listing_path"]),
            output=root,
            timeout=min(180, context.remaining_seconds() - 1),
            service_route=self.bootstrap.service_route(context, "registry"),
            gitlab_service_route=self.bootstrap.service_route(context, "gitlab"),
        )
        self._admit(context)
        require_runtime_image(self._runtime_image, run_scope=self.journal.identities)
        return self._runtime_image

    def locked_images(self, context):
        from .locked_images import qualify_locked_images, require_locked_images

        self._admit(context)
        if self._locked_images is not None:
            try:
                require_locked_images(self._locked_images, journal=self.journal, context=context)
                return self._locked_images
            except ValueError:
                pass
        self._image_reads += 1
        root = self.journal.run_dir / "owned-operations" / ("locked-images-" + str(self._image_reads))
        self._locked_images = qualify_locked_images(context, operations=self, output=root)
        self._admit(context)
        require_locked_images(self._locked_images, journal=self.journal, context=context)
        return self._locked_images

    def selected_component(self, name):
        if name == "owned_git_writer":
            return self._auxiliary(name)
        if name == "runtime":
            selected = self._auxiliary("owned_runtime_recipe")
            if selected[1] != self._recipe()["runtime"]["sha256"]:
                raise StateError("Runtime selection differs from separately frozen input")
            return selected
        if name == "owned_cases_recipe":
            return self._auxiliary(name)
        if name not in {
            "build",
            "staging",
            "security",
            "signing",
            "publication",
            "proposal",
            "commit",
            "destination",
        }:
            raise StateError("Fixed mission component name required")
        return self._auxiliary("mission_git_" + name)

    def _runtime_input_coordinator(self):
        from .owned_runtime_inputs import OwnedRuntimeInputCoordinator

        if self._runtime_inputs is None:
            self._runtime_inputs = OwnedRuntimeInputCoordinator(
                self, runtime=self.runtime, bootstrap=self.bootstrap
            )
        return self._runtime_inputs

    def prepare_runtime(self, context):
        self._admit(context)
        return self._runtime_input_coordinator().prepare(context)

    def source_selection(self, attempt):
        # Only an actual authenticated fault delivery may select review source.
        receipt = self.delivery_binding(attempt, "fault")
        checkout = self.source_checkout(attempt, "fault")
        from .native_phase import NativeSourceSelection

        selected = self._recipe()["native"]
        from .owned_correction_contract import require_correction_contract

        contract, contract_sha = require_correction_contract(
            self._correction_contracts.get(attempt), operations=self, attempt=attempt
        )
        return NativeSourceSelection(
            repository=Path(checkout["repository"]),
            base_commit=receipt["parent"],
            fault_commit=receipt["deployment_commit"],
            profile=Path(selected["profile"]["path"]),
            profile_sha256=selected["profile"]["sha256"],
            correction_contract=contract,
            correction_contract_sha256=contract_sha,
            tools={name: (Path(item["path"]), item["sha256"]) for name, item in selected["tools"].items()},
            schemas=Path(selected["schemas"]["path"]),
            schemas_sha256=selected["schemas"]["sha256"],
        )

    def delivery(self, attempt, stage):
        if attempt not in ("primary", "replay") or stage not in ("baseline", "fault", "recovery"):
            raise StateError("Fixed delivery attempt and stage required")
        # An eventual writer owns the sole issuer of direct delivery receipts;
        # there is deliberately no public JSON/callback enrollment method.
        value = self._deliveries.get((attempt, stage))
        if value is None:
            raise StateError("Actual same-run Git delivery origin is not yet available")
        from .owned_git_delivery import require_delivery

        binding = require_delivery(value, journal=self.journal)
        if (
            binding["delivery_stage"] != "merged-watched-branch"
            or binding["watched_revision"] != binding["deployment_commit"]
        ):
            raise StateError("Watched Git revision has not been independently observed")
        return value

    def delivery_binding(self, attempt, stage):
        from .owned_git_delivery import require_delivery

        return require_delivery(self.delivery(attempt, stage), journal=self.journal)

    def _retain_delivery(self, context, value, *, attempt, stage):
        from .owned_git_delivery import require_delivery
        from .owned_checkout import materialize_delivered_repository, require_owned_checkout

        self._admit(context)
        key = (attempt, stage)
        if key in self._deliveries or key in self._source_checkouts:
            raise StateError("Existing delivery must not be repeated")
        binding = require_delivery(value, journal=self.journal)
        if (
            binding["run_id"] != context.run_id
            or binding["delivery_stage"] != "merged-watched-branch"
            or binding["watched_revision"] != binding["deployment_commit"]
        ):
            raise StateError("Original watched delivery required")
        self._deliveries[key] = value  # Preserve observed external effect before local materialization.
        selected = deployment_commit.commit_configuration(self.selected_component("commit")[2])
        checkout = materialize_delivered_repository(
            context,
            bootstrap=self.bootstrap,
            delivery=value,
            git_path=Path(selected["git_path"]),
            git_sha256=selected["git_sha256"],
            output=self.journal.run_dir / "owned-operations" / ("source-" + attempt + "-" + stage),
        )
        observed = require_owned_checkout(checkout, journal=self.journal)
        if observed["revision"] != binding["watched_revision"]:
            raise StateError("Materialized source differs from actual delivered revision")
        self._source_checkouts[key] = checkout
        if stage == "fault":
            from .owned_correction_contract import derive_correction_contract

            derive_correction_contract(self, context, attempt=attempt)

    def source_checkout(self, attempt, stage):
        from .owned_checkout import require_owned_checkout

        binding = self.delivery_binding(attempt, stage)
        value = self._source_checkouts.get((attempt, stage))
        if value is None:
            raise StateError("Actual delivered source has not been materialized")
        checkout = require_owned_checkout(value, journal=self.journal)
        if checkout["revision"] != binding["watched_revision"]:
            raise StateError("Original source checkout differs from delivered revision")
        return checkout

    def dispatch(self, context):
        self._admit(context)
        if context.phase["id"] == "build-promote":
            return self.build_promote(context)
        if context.phase["operation"] in ("inject", "deliver", "reset"):
            operation = context.phase["operation"]
            self._admit(context, operation)
            attempt = "replay" if operation == "reset" else context.phase["attempt"]
            stage = {"inject": "fault", "deliver": "recovery", "reset": "baseline"}[operation]
            if (attempt, stage) in self._deliveries:
                raise StateError("Existing operation delivery requires reconciliation")
            self._runtime_input_coordinator()
            from .owned_policy_delivery import execute_policy_delivery

            delivered = execute_policy_delivery(context, operations=self)
            self._retain_delivery(context, delivered, attempt=attempt, stage=stage)
            self.runtime.bind_runtime_image(context, self.runtime_image(context))
            self.runtime.bind_locked_images(context, self.locked_images(context))
            return self._with_evidence(context, self.prepare_runtime(context))
        if context.phase["operation"] == "probe":
            # Actors were created under the preceding delivery mutation.
            # This fixed route only observes their original results.
            self._admit(context)
            return self.runtime.dispatch(context)
        if context.phase["id"] in ("platform-dependency", "supply-chain-controls", "evidence-gaps"):
            from .owned_cases import OwnedCanonicalCases

            if self._cases is None:
                self._cases = OwnedCanonicalCases(
                    self.journal, operations=self, runtime=self.runtime, driver=self.bootstrap.driver
                )
            return self._cases.execute(context)
        raise StateError("Required production route not yet linked: " + PHASE_ROUTES[context.phase["id"]])
