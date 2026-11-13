"""Canonical composition and fixed transport under controlled test boundaries."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import json
import os
import pytest
from devsecops.factory import canonical_bootstrap as b, bootstrap_recipe as recipe, services_remote
from devsecops.factory.lifecycle import PhaseOutcome
from devsecops.factory.owned_linux import OwnedLinuxDriver
from devsecops.factory.state import StateError, _sha
from tests.test_owned_foundation import context
from tests import test_owned_foundation as foundation_fixture

setup = foundation_fixture.setup


def selected(tmp):
    def pin(name, raw=b"controlled"):
        p = tmp / name
        p.write_bytes(raw)
        p.chmod(0o600)
        return {"path": str(p), "sha256": _sha(raw)}

    t = {n: pin("ssh-" + n) for n in ("config", "known_hosts", "certificate", "identity", "proxy")}
    t.update(
        identity_agent=str(tmp / "agent"),
        host_key_sha256="a" * 64,
        verification_basis="operator-verified",
        agent_protocol="native",
    )
    return {
        "schema_version": 1,
        "kind": "owned-bootstrap-v1",
        "environment": "devsecops-demo",
        "registry": pin("registry.json", b"{}"),
        "writer_policy": pin("writer-policy.json", b"{}"),
        "services": {
            "origin": "https://gitlab.devsecops-demo.test:8444",
            "version": "19.5.3",
            "remote_directory": "/home/operator/.local/share/devsecops/services/devsecops-demo",
            "private_state_directory": str(tmp / "private-services"),
            "transport": t,
            "materials": {n: pin(n) for n in ("ca", "certificate", "private_key")},
            "credentials": {
                "root_password_env": "TEST_ROOT_PASSWORD",
                "operator_token_env": "TEST_OPERATOR_TOKEN",
            },
            "server_image": {
                "reference": "docker.io/gitlab/gitlab-ce@sha256:" + "d" * 64,
                "image_id": "sha256:" + "e" * 64,
            },
            "runner_image": {
                "reference": "registry.example.test/runner@sha256:" + "f" * 64,
                "image_id": "sha256:" + "1" * 64,
            },
            "runner_environment": {
                "DEVSECOPS_GIT_INVOCATION": "/opt/devsecops/runner-inputs/invocation.json",
                "DEVSECOPS_GIT_INVOCATION_SHA256": "2" * 64,
                "DEVSECOPS_PYTHON": "/opt/devsecops/venv/bin/python",
                "DEVSECOPS_PLUGIN_ROOT": "/opt/devsecops/plugin",
            },
            "runner_validation": {
                "helm": {"path": "/opt/devsecops/tools/helm", "sha256": "3" * 64},
                "kubeconform": {"path": "/opt/devsecops/tools/kubeconform", "sha256": "4" * 64},
                "schemas_path": "/opt/devsecops/schemas",
            },
            "runner_materials": [],
            "runner_secret_environment": [],
        },
        "seeds": {
            r: [{"repository_path": "README.md", "file": pin(r, b"controlled seed")}]
            for r in ("source", "deployment")
        },
        "kubernetes": {"manifests": []},
        "platform": {
            "flux_version": "1.2.3",
            "bigbang_version": "1.2.3",
            "flux_manifest": pin("flux"),
            "chart": pin("chart"),
            "chart_metadata_sha256": "b" * 64,
            "values": pin("values"),
            "helm": pin("helm"),
            "images": ["registry.example.test/controller@sha256:" + "c" * 64],
            "namespaces": ["flux-system", "bigbang"],
            "cluster_resources": [],
            "controller_cleanup": "owned-controller-inventory-v1",
            "readiness": {
                "flux_deployments": [{"namespace": "flux-system", "name": "source-controller"}],
                "helm_releases": [{"namespace": "bigbang", "name": "bigbang"}],
                "argo_application": {"namespace": "argocd", "name": "mission"},
            },
        },
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x.update(command="arbitrary"),
        lambda x: x["services"].update(version="latest"),
        lambda x: x["services"]["server_image"].update(reference="docker.io/gitlab/gitlab-ce:latest"),
        lambda x: x["services"]["runner_environment"].update(BASH_ENV="/tmp/injected"),
        lambda x: x["services"]["transport"].update(verification_basis="self-asserted"),
        lambda x: x["seeds"]["source"][0].update(repository_path="../escape"),
        lambda x: x["platform"]["images"].append("controller:latest"),
    ],
)
def test_recipe_rejects_unpinned_or_executable_extension(tmp_path, mutation):
    value = selected(tmp_path)
    recipe.validate(value)
    mutation(value)
    with pytest.raises(ValueError):
        recipe.validate(value)


def test_remote_preflight_only_reads_exact_images(tmp_path):
    value = selected(tmp_path)
    calls = []

    def run(argv, deadline, **kwargs):
        calls.append(argv)
        assert argv[:3] == ["docker", "image", "inspect"]
        image = next(
            value["services"][n]
            for n in ("server_image", "runner_image")
            if value["services"][n]["image_id"] == argv[-1]
        )
        return SimpleNamespace(
            stdout=json.dumps(
                [
                    {
                        "Id": image["image_id"],
                        "Os": "linux",
                        "Architecture": "amd64",
                        "RepoDigests": [image["reference"]],
                    }
                ]
            ).encode()
        )

    p = {
        "recipe": value,
        "action": "preflight",
        "operation_id": "12345678-1234-1234-1234-123456789012",
        "run_id": "12345678-1234-1234-1234-123456789012",
        "identities": {},
        "package_modules": {},
        "timeout": 30,
        "expires_at": "2099-01-01T00:00:00Z",
    }
    p["binding"] = recipe.binding(value, run_id=p["run_id"], identities={}, package_modules={})
    result = services_remote.execute(p, SimpleNamespace(_run=run, _json=json.loads), recipe)
    assert result["effects"] == "none" and len(calls) == 2


@contextmanager
def current(setup, paths):
    journal, _ = setup["start"]()
    proof = setup["tmp"] / "proof.json"
    proof.write_text("{}")
    with journal.lifecycle_lock(), journal.mutation_lock(setup["tmp"] / "locks"):
        for name in ("preflight", "prepare"):
            journal.begin_phase(name)
            journal.finish_phase(name, "completed", "controlled", receipt_artifacts=[proof])
        ctx = context(journal, setup["values"], "up")
        journal.begin_phase(ctx.phase["id"])
        journal.begin_mutation(ctx.phase["id"], "up", intent_artifacts=paths)
        yield journal, ctx


@pytest.mark.parametrize("runner_profile", ["legacy", "vm"])
def test_canonical_dag_seals_cleanup_before_effects_and_issues_original_authority(
    setup, monkeypatch, runner_profile
):
    value = selected(setup["tmp"])
    if runner_profile == "vm":
        value.update(schema_version=2, kind="owned-bootstrap-v2")
        value["services"].pop("runner_image")
        value["services"]["runner_vm"] = value["registry"].copy()
        from devsecops.factory.owned_runner_vm import OwnedRunnerVM
        from devsecops.factory.runner_vm_routes import OwnedRunnerVMRoutes
        from devsecops.factory.owned_runner_service import OwnedRunnerService

        # These are controlled guest/host transport boundaries, not VM proof.
        def component_init(self, driver, **kwargs):
            self.driver, self.journal = driver, driver.journal
            for name, component in kwargs.items():
                setattr(self, name, component)

        for cls in (OwnedRunnerVM, OwnedRunnerVMRoutes, OwnedRunnerService):
            monkeypatch.setattr(cls, "__init__", component_init)
            monkeypatch.setattr(cls, "evidence_artifacts", lambda *a: ())
        monkeypatch.setattr(OwnedRunnerVM, "prepare", lambda *a: events.append("vm-prepare"))
        monkeypatch.setattr(
            OwnedRunnerService, "bind_registration", lambda *a, **kw: events.append("vm-registration")
        )
        monkeypatch.setattr(
            OwnedRunnerService,
            "runner",
            lambda *a: {"runner_id": 3, "runner_manager_system_identity": "s_original"},
        )
    path = setup["tmp"] / "bootstrap.json"
    path.write_bytes(recipe.canonical(value))
    events = []

    class Cleanup:
        def evidence_artifacts(self, context):
            return ()

        def register_foundation(self, c):
            events.append("register-foundation")

        def register_registry(self, c):
            events.append("register-registry")

        def register_services(self, c):
            events.append("register-services")

        def register_runner_vm(self, c):
            events.append("register-runner-vm")

        def register_runner_routes(self, c):
            events.append("register-runner-routes")

        def register_runner_service(self, c):
            events.append("register-runner-service")

        def runner_vm_up(self, c):
            events.append("runner-vm")
            return PhaseOutcome("incomplete", "controlled-vm")

        def runner_routes_up(self, c):
            events.append("runner-routes")
            return PhaseOutcome("incomplete", "controlled-routes")

        def runner_service_up(self, c):
            events.append("runner-start")
            return PhaseOutcome("incomplete", "controlled-guest-service")

        def declare_deferred_scope(self, c, n):
            events.append("declare-" + n)

        def seal_inventory(self, c, *, inventory_contract):
            assert inventory_contract == (
                "owned-runner-vm-v1" if runner_profile == "vm" else "owned-container-runner-v1"
            )
            events.append("seal")

        def foundation_up(self, c):
            events.append("foundation")
            return PhaseOutcome("incomplete", "controlled")

        def registry_up(self, c):
            events.append("registry")
            return PhaseOutcome("incomplete", "controlled")

        def services_up(self, c):
            events.append("services")

        def bind_gitlab(self, *a):
            events.append("bind-gitlab")
            return self

        def accept_service_token(self, s):
            events.append("token")

        def runner_intent(self, *a):
            events.append("runner-intent")
            return object()

        def runner_created(self, *a):
            events.append("runner-created")

        def register_kubernetes(self, c):
            events.append("register-kubernetes")

    class API:
        writes_blocked = False

        def request(self, method, path, **kw):
            if method == "POST":
                events.append("runner-post")
                return {"id": 3, "token": "glrt-controlled-token"}
            return {"id": 3, "runner_type": "project_type", "description": marker}

        def get_collection(self, *a, **kw):
            return [
                {"system_id": "s_original", "status": "online", "platform": "linux", "architecture": "amd64"}
            ]

    with current(setup, [path, Path(value["registry"]["path"])]) as (journal, ctx):
        driver = OwnedLinuxDriver(journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0")
        driver.auxiliary_sources = {"owned_bootstrap_recipe": (path, _sha(path.read_bytes()))}
        monkeypatch.setattr(driver, "_admit", lambda c, d: None)
        monkeypatch.setattr(driver, "_package", lambda h, d: None)
        coordinator = b.OwnedBootstrapCoordinator(driver, cleanup=Cleanup())
        coordinator._prepared = True
        marker = "factory-owned:devsecops-demo:" + journal.identities["environment_sha256"]
        monkeypatch.setattr(coordinator, "_seeds", lambda r: ())
        monkeypatch.setattr(coordinator, "_issue_writer", lambda *a: events.append("writer-up"))
        monkeypatch.setattr(coordinator, "_issue_reader", lambda *a: events.append("reader-up"))
        monkeypatch.setattr(coordinator, "_assign_runner", lambda *a: events.append("runner-assigned"))
        monkeypatch.setattr(
            coordinator.foundation,
            "bootstrap_observation",
            lambda o: {
                "report": {
                    "foundation_readiness": "retained-rke2-ready",
                    "cluster_fingerprint": {
                        "namespaces": {"kube-system": "cluster-uid"},
                        "ca_sha256": "a" * 64,
                    },
                },
                "actual": {"endpoint": "https://cluster.example.test"},
                "private_state_directory": str(setup["tmp"]),
            },
        )
        monkeypatch.setattr(
            coordinator.registry,
            "component_observation",
            lambda o: {"observation": {"storage_qualification": "pass"}},
            raising=False,
        )
        monkeypatch.setattr(
            coordinator.services,
            "principal",
            lambda c: {"owner_user_id": 1, "namespace_id": 2, "namespace_path": "root", "token_id": 9},
        )
        monkeypatch.setattr(coordinator, "_new_session", lambda r, p, **kw: API())
        monkeypatch.setattr(
            b,
            "bootstrap_owned_projects",
            lambda *a, **kw: {
                "resources": [
                    {
                        "role": r,
                        "project_id": n,
                        "path_with_namespace": "root/factory-devsecops-demo-" + r,
                        "seed_commit": str(n) * 40,
                    }
                    for r, n in (("source", 4), ("deployment", 5))
                ]
            },
        )
        monkeypatch.setattr(
            coordinator.services, "start_runner", lambda *a, **kw: events.append("runner-start")
        )
        monkeypatch.setattr(
            coordinator.services,
            "runner",
            lambda c: {"runner_id": 3, "runner_manager_system_identity": "s_original"},
        )
        monkeypatch.setattr(b.KubernetesSession, "__init__", lambda s, o, **kw: setattr(s, "origin", o))
        monkeypatch.setattr(
            b.KubernetesSession, "request", lambda *a, **kw: {"metadata": {"uid": "cluster-uid"}}
        )
        monkeypatch.setattr(coordinator.platform, "manifests", lambda c: ())
        monkeypatch.setattr(coordinator, "_resolve_platform_inputs", lambda *a: ())
        monkeypatch.setattr(
            coordinator.platform,
            "install",
            lambda *a, **kw: {
                "argo_destination": {"server": "https://kubernetes.default.svc", "namespace": "mission-demo"}
            },
        )
        result = coordinator.up(ctx)
        authority = coordinator.require_authority(ctx)
        facts = b.require_bootstrap_authority(authority, journal)
        assert result.status == "completed" and facts["kubernetes"]["cluster_origin"] == "retained"
        assert facts["projects"]["deployment"]["id"] == 5 and coordinator.require_authority(ctx) is authority
        assert (
            events.index("seal")
            < events.index("foundation")
            < events.index("registry")
            < events.index("services")
        )
        assert (
            events.index("runner-intent")
            < events.index("runner-post")
            < events.index("runner-created")
            < events.index("runner-start")
        )
        if runner_profile == "vm":
            assert events.index("register-runner-service") < events.index("seal")
            assert events.index("runner-vm") < events.index("runner-routes") < events.index("runner-intent")
            assert (
                events.index("runner-created")
                < events.index("vm-registration")
                < events.index("runner-start")
            )
        with pytest.raises(TypeError):
            facts["projects"]["deployment"]["id"] = 99
        with pytest.raises(StateError, match="one-shot"):
            coordinator.up(ctx)
        import time

        coordinator._authority_deadline = time.monotonic() - 1
        with pytest.raises(StateError, match="audit deadline"):
            b.require_bootstrap_authority(authority, journal)
        assert (
            b.require_bootstrap_authority(authority, journal, deadline=time.monotonic() + 2)["run_id"]
            == ctx.run_id
        )
        coordinator.revoke()
        with pytest.raises(StateError):
            b.require_bootstrap_authority(authority, journal)


@pytest.mark.parametrize("foreign_project", [False, True])
def test_runner_assigns_exact_two_owned_projects_before_starting_execution(
    setup, monkeypatch, foreign_project
):
    from copy import deepcopy
    from tests.test_owned_foundation import invocation

    with invocation(setup) as (journal, _, ctx, _):
        coordinator = object.__new__(b.OwnedBootstrapCoordinator)
        coordinator.journal = journal
        monkeypatch.setattr(coordinator, "_admit", lambda context: None)
        events = []
        monkeypatch.setattr(coordinator, "_artifact", lambda name, value: events.append(name))
        scope = SimpleNamespace(marker="owned-marker")
        projects = {"source": {"id": 4}, "deployment": {"id": 5}}
        state = {
            "id": 3,
            "runner_type": "project_type",
            "is_shared": False,
            "description": "owned-marker",
            "locked": False,
            "paused": True,
            "projects": [{"id": 4}],
        }

        class Resources:
            def runner_assignment_intent(self, ticket, project):
                events.append("assignment-intent")

            def runner_assigned(self, ticket, project):
                events.append("assignment-observed")

        class API:
            def request(self, method, path, payload=None, expected=(200,)):
                if method == "GET":
                    return deepcopy(state)
                if method == "POST":
                    assert path == "/api/v4/projects/5/runners" and payload == {"runner_id": 3}
                    assert events[-1] == "assignment-intent"
                    state["projects"].append({"id": 99 if foreign_project else 5})
                    return {"id": 3, "runner_type": "project_type"}
                assert method == "PUT" and events[-1] == "runner-execution-intent"
                assert payload == {"locked": True, "paused": False}
                state.update(payload)
                events.append("enable-runner")
                return deepcopy(state)

            def get_collection(self, path, **kw):
                return [{"id": 3}]

            def stop_writes(self):
                events.append("stop-writes")

        if foreign_project:
            with pytest.raises(StateError, match="assignment"):
                coordinator._assign_runner(ctx, API(), Resources(), object(), 3, scope, projects)
            assert "enable-runner" not in events
        else:
            coordinator._assign_runner(ctx, API(), Resources(), object(), 3, scope, projects)
            assert state["locked"] is True and state["paused"] is False
            assert events.index("assignment-observed") < events.index("enable-runner")


def test_reconciliation_route_uses_exact_readonly_operation_gate(monkeypatch):
    from devsecops.factory.owned_services import OwnedServiceStack

    coordinator = object.__new__(b.OwnedBootstrapCoordinator)
    coordinator._pid, coordinator._revoked = os.getpid(), False
    coordinator.journal = object()
    coordinator.services = object.__new__(OwnedServiceStack)
    coordinator.services.journal = coordinator.journal
    calls = []
    monkeypatch.setattr(
        OwnedServiceStack,
        "service_endpoint",
        lambda self, ctx, reconciliation_operation: calls.append(
            (ctx.phase["operation"], reconciliation_operation)
        ),
    )
    coordinator._service_links = SimpleNamespace(
        route=lambda *a: pytest.fail("ordinary route cannot replace reconciliation"),
        reconciliation_route=lambda ctx, service, operation: ("readonly", service, operation),
    )
    monkeypatch.setattr(
        coordinator,
        "_admit",
        lambda ctx: pytest.fail("ordinary action admission cannot replace reconciliation"),
    )
    monkeypatch.setattr(coordinator, "_recipe", lambda: {})
    context = SimpleNamespace(phase={"operation": "deliver"})
    assert coordinator.service_route(context, "gitlab", reconciliation_operation="original-delivery") == (
        "readonly",
        "gitlab",
        "original-delivery",
    )
    assert calls == [("deliver", "original-delivery")]


def test_full_production_rejects_legacy_runner_before_any_component_effect(setup, monkeypatch):
    from devsecops.factory.owned_production import OwnedProductionCoordinator, REQUIRED_SELECTIONS

    value = selected(setup["tmp"])
    path = setup["tmp"] / "legacy-bootstrap.json"
    path.write_bytes(recipe.canonical(value))
    with current(setup, [path, Path(value["registry"]["path"])]) as (journal, ctx):
        driver = OwnedLinuxDriver(journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0")
        driver.auxiliary_sources = {name: (path, _sha(path.read_bytes())) for name in REQUIRED_SELECTIONS}
        monkeypatch.setattr(driver, "_auxiliary_artifacts", lambda: ())
        monkeypatch.setattr(
            b.OwnedBootstrapCoordinator, "up", lambda *a: pytest.fail("No legacy provision allowed")
        )
        with pytest.raises(StateError, match="dedicated runner VM profile before effects"):
            OwnedProductionCoordinator(driver)
