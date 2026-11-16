"""Controlled transport boundaries and real journal admission; no live E2E claims."""

from copy import copy, deepcopy
from dataclasses import replace
from types import SimpleNamespace
import time
from uuid import uuid4
import pytest
from devsecops.factory import owned_service_links as links, service_link_local
from devsecops.factory.owned_linux import OwnedLinuxDriver
from devsecops.factory.owned_registry import OwnedOCIRegistry
from devsecops.factory.owned_services import OwnedServiceStack
from devsecops.factory.state import StateError, _sha
from tests.test_owned_fresh_foundation import component  # noqa: F401
from tests.test_fresh_foundation import lab  # noqa: F401
from tests.test_factory_tcg import transport_inputs, issued_certificate  # noqa: F401


@pytest.fixture
def linked(component, monkeypatch, tmp_path):  # noqa: F811
    base = component
    driver = OwnedLinuxDriver(base.journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0")
    monkeypatch.setattr(driver, "_package", lambda *a: {"controlled": True})
    registry = OwnedOCIRegistry(
        base.journal,
        recipe_path=tmp_path / "registry.json",
        recipe_sha256="a" * 64,
        plugin_id="devsecops@devsecops-local",
        plugin_version="0.1.0",
    )
    services = OwnedServiceStack(driver, recipe_path=tmp_path / "bootstrap.json", recipe_sha256="b" * 64)
    owner = links.OwnedServiceLinks(driver, foundation=base.driver, registry=registry, services=services)
    calls, controls = [], {}
    ca = tmp_path / "ca.pem"
    ca.write_bytes(b"controlled-ca")

    def endpoint(role, context, **kw):
        links._endpoint_context(base.journal, context, **kw)
        target = {
            "kind": "container",
            "id": ("a" if role == "gitlab" else "b") * 64,
            "role": "server" if role == "gitlab" else "registry",
            "labels": {"binding": role},
        }
        return {
            "run_id": context.run_id,
            "identities": dict(context.identities),
            "binding": role,
            "origin": f"https://{role}.demo-test.test:" + ("8444" if role == "gitlab" else "8443"),
            "port": 8444 if role == "gitlab" else 8443,
            "ca_path": str(ca),
            "ca_sha256": _sha(ca.read_bytes()),
            "network": {"kind": "network", "id": "c" * 64, "labels": {"binding": role}},
            **(
                {"server": target, "runner": controls.get("runner")}
                if role == "gitlab"
                else {"registry": target}
            ),
        }

    monkeypatch.setattr(services, "service_endpoint", lambda c, **kw: endpoint("gitlab", c, **kw))
    monkeypatch.setattr(registry, "service_endpoint", lambda c, **kw: endpoint("registry", c, **kw))
    base.changes["resources"] = [
        {"kind": role, "identity": "d" * 64, "state": "present"} for role in ("runtime", "network")
    ]

    def dispatch(context, action, operation, targets):
        owner._attempts[-1]["dispatched"] = True
        calls.append((action, operation))
        if controls.get("interrupt"):
            raise StateError("controlled unknown remote effect")
        return {
            "binding": owner._binding,
            "operation_id": operation,
            "root_identity": {"device": 1, "inode": 2},
            "complete_cleanup": action == "down",
            "ready": action != "down",
            "unresolved_operations": [],
            "resources": [
                {
                    "kind": "controlled-relay",
                    "disposition": "remove",
                    "state": "absent" if action == "down" else "present",
                }
            ],
            "listeners": {"desktop-" + r: {"port": 23400 + i} for i, r in enumerate(("gitlab", "registry"))},
            "runner_identity": targets["gitlab"]["runner"],
        }

    monkeypatch.setattr(owner, "_dispatch", dispatch)

    class Tunnel:
        def __init__(self, owner, service):
            self.owner, self.service, self.port, self.unresolved, self.closed = (
                owner,
                service,
                40001,
                False,
                False,
            )

        def start(self, context, remote_port):
            if controls.get("tunnel_interrupt"):
                self.unresolved = True
                raise StateError("controlled tunnel interruption")

        def close(self, context):
            self.closed = True

        def audit(self, context):
            if controls.get("replaced") or self.closed:
                raise StateError("original process replaced")

        def observation(self, context):
            return {
                "kind": "controlled-local-tunnel",
                "disposition": "remove",
                "state": "absent" if self.closed else "present",
            }

        def artifacts(self):
            return ()

    monkeypatch.setattr(service_link_local, "OwnedLinkTunnel", Tunnel)
    return SimpleNamespace(base=base, owner=owner, driver=driver, calls=calls, controls=controls, ca=ca)


def start(linked, context):
    return linked.base.driver.up(context), linked.owner.up(context)


def test_unissued_routes_and_serialization_are_rejected():
    for item in (
        {},
        object.__new__(links.OwnedServiceRoute),
        SimpleNamespace(origin="https://gitlab.demo.test:8444"),
    ):
        with pytest.raises(StateError):
            links.require_service_route(item, origin="https://gitlab.demo.test:8444")
    with pytest.raises(StateError):
        links.OwnedServiceRoute()


def test_route_original_context_ca_origin_and_process_are_reaudited(linked):
    with linked.base.invocation() as (context, op):
        _, outcome = start(linked, context)
        route = linked.owner.route(context, "registry")
        facts = links.require_service_route(route, origin="https://registry.demo-test.test:8443")
        assert (
            facts["connect_host"] == "127.0.0.1"
            and facts["deadline"] == context.deadline
            and facts["read_only"] is False
        )
        with pytest.raises(TypeError):
            facts["connect_port"] = 1
        with pytest.raises(StateError):
            copy(route)
        with pytest.raises(StateError):
            deepcopy(route)
        with pytest.raises(StateError):
            links.require_service_route(route, origin="https://other.test:8443")
        linked.controls["replaced"] = True
        with pytest.raises(StateError):
            links.require_service_route(route, origin=facts["origin"])
        linked.controls.pop("replaced")
        outcome.artifacts[1].write_bytes(b"{}")
        with pytest.raises(StateError, match="evidence changed"):
            links.require_service_route(route, origin=facts["origin"])


def test_expired_context_and_wrong_driver_or_endpoint_owner_fail(linked):
    with pytest.raises(StateError):
        links.OwnedServiceLinks(
            SimpleNamespace(journal=linked.base.journal),
            foundation=linked.base.driver,
            registry=linked.owner.registry,
            services=linked.owner.services,
        )
    with linked.base.invocation() as (context, op):
        start(linked, context)
        with pytest.raises(StateError):
            linked.owner.route(replace(context, deadline=time.monotonic() - 1), "gitlab")
        with pytest.raises(StateError):
            linked.owner.route(replace(context, run_id=str(uuid4())), "gitlab")
        linked.ca.write_bytes(b"replaced")
        with pytest.raises(StateError, match="endpoint identity changed"):
            linked.owner.route(context, "gitlab")


def test_same_operation_readonly_renewal_never_upgrades(linked):
    with linked.base.invocation() as (context, op):
        start(linked, context)
        normal = linked.owner.route(context, "gitlab")
        linked.base.journal._owned_operations.remove(op)
        with pytest.raises(StateError):
            links.require_service_route(normal, origin="https://gitlab.demo-test.test:8444")
        with pytest.raises(StateError):
            links.renew_service_route(normal, context=context)
        readonly = links.renew_service_route(normal, context=context, operation_id=op)
        assert (
            links.require_service_route(readonly, origin="https://gitlab.demo-test.test:8444")["read_only"]
            is True
        )
        renewed = links.renew_service_route(readonly, context=context)
        assert (
            links.require_service_route(renewed, origin="https://gitlab.demo-test.test:8444")["read_only"]
            is True
        )
        with pytest.raises(StateError):
            links.renew_service_route(normal, context=context, operation_id=str(uuid4()))


def test_remote_unknown_is_latched_without_duplicate_effect(linked):
    with linked.base.invocation() as (context, op):
        linked.base.driver.up(context)
        linked.controls["interrupt"] = True
        with pytest.raises(StateError):
            linked.owner.up(context)
        with pytest.raises(StateError):
            linked.owner.up(context)
        assert linked.calls == [("up", op)]
        linked.controls.pop("interrupt")
        linked.base.journal._owned_operations.remove(op)
        outcome = linked.owner.reconcile(context, op)
        assert linked.calls == [("up", op), ("reconcile", op)]
        assert linked.owner.cleanup_observation(outcome)["complete_cleanup"] is False
        with pytest.raises(StateError):
            linked.owner.route(context, "registry")


def test_cleanup_closes_original_tunnels_and_rejects_repeated_down(linked):
    with linked.base.invocation() as (context, op):
        foundation, outcome = start(linked, context)
        down = linked.base.down_context(context, op, [*foundation.artifacts, *outcome.artifacts])
        result = linked.owner.down(down)
        observed = linked.owner.cleanup_observation(result)
        assert observed["complete_cleanup"] is True and observed["mutation_effect"] == "applied"
        assert all(r["state"] == "absent" for r in observed["resources"])
        with pytest.raises(StateError):
            linked.owner.down(down)
        assert [r[0] for r in linked.calls] == ["up", "down"]


def test_never_dispatched_cleanup_is_original_resident_evidence(linked):
    with linked.base.invocation() as (context, op):
        proof = linked.base.journal.run_dir / "proof.json"
        proof.write_text("{}")
        down = linked.base.down_context(context, op, [proof])
        result = linked.owner.down(down)
        assert linked.owner.cleanup_observation(result)["complete_cleanup"] is True and linked.calls == []


def test_runner_addition_requires_original_observed_identity_and_is_single_attempt(linked):
    with linked.base.invocation() as (context, op):
        start(linked, context)
        with pytest.raises(StateError, match="actual owned runner"):
            linked.owner.configure_runner(context)
        linked.controls["runner"] = {
            "kind": "container",
            "id": "f" * 64,
            "role": "runner",
            "labels": {"binding": "gitlab"},
        }
        linked.owner.configure_runner(context)
        with pytest.raises(StateError):
            linked.owner.configure_runner(context)
        assert [r[0] for r in linked.calls] == ["up", "runner"]


def test_readonly_route_for_exact_unknown_build_phase_does_not_broaden_component_effects(linked):
    from devsecops.factory.lifecycle import _readonly

    with linked.base.invocation() as (context, op):
        foundation, outcome = start(linked, context)
        original = linked.owner.route(context, "gitlab")
        journal = linked.base.journal
        artifacts = [*foundation.artifacts, *outcome.artifacts]
        journal.finish_mutation(op, receipt_artifacts=artifacts)
        journal.finish_phase(context.phase["id"], "completed", "controlled", receipt_artifacts=artifacts)
        phase = next(p for p in linked.base.values["scenario"]["phases"] if p["id"] == "build-promote")
        journal.begin_phase(phase["id"])
        current = journal.begin_mutation(
            phase["id"], phase["operation"], intent_artifacts=[linked.base.proof]
        )
        next_context = replace(context, phase=_readonly(phase))
        journal._owned_operations.remove(current)
        readonly = links.renew_service_route(original, context=next_context, operation_id=current)
        assert (
            links.require_service_route(readonly, origin="https://gitlab.demo-test.test:8444")["read_only"]
            is True
        )
        with pytest.raises(StateError):
            linked.owner.reconcile(next_context, current)
        with pytest.raises(StateError):
            links.require_service_route(original, origin="https://gitlab.demo-test.test:8444")


def test_route_does_not_extend_original_config_expiry(linked, monkeypatch):
    from datetime import datetime, timedelta

    with linked.base.invocation() as (context, op):
        start(linked, context)
        original = linked.owner.route(context, "registry")
        expiry = datetime.fromisoformat(context.inputs["config"]["expires_at"].replace("Z", "+00:00"))

        class Expired(datetime):
            @classmethod
            def now(cls, tz=None):
                return expiry + timedelta(seconds=1)

        monkeypatch.setattr(links, "datetime", Expired)
        with pytest.raises(StateError, match="grant expired"):
            links.require_service_route(original, origin="https://registry.demo-test.test:8443")


def test_readonly_route_exact_platform_dependency_mutation_action(linked):
    from devsecops.factory.lifecycle import _readonly

    with linked.base.invocation() as (context, op):
        foundation, outcome = start(linked, context)
        original = linked.owner.route(context, "gitlab")
        journal = linked.base.journal
        artifacts = [*foundation.artifacts, *outcome.artifacts]
        journal.finish_mutation(op, receipt_artifacts=artifacts)
        journal.finish_phase(context.phase["id"], "completed", "controlled", receipt_artifacts=artifacts)
        for phase in linked.base.values["scenario"]["phases"][3:]:
            journal.begin_phase(phase["id"])
            if phase["id"] == "platform-dependency":
                break
            journal.finish_phase(
                phase["id"], "completed", "controlled-only", receipt_artifacts=[linked.base.proof]
            )
        assert phase["operation"] == "case"
        current = journal.begin_mutation(
            phase["id"], "platform-dependency", intent_artifacts=[linked.base.proof]
        )
        journal._owned_operations.remove(current)
        next_context = replace(context, phase=_readonly(phase))
        readonly = links.renew_service_route(original, context=next_context, operation_id=current)
        assert (
            links.require_service_route(readonly, origin="https://gitlab.demo-test.test:8444")["read_only"]
            is True
        )
        with pytest.raises(StateError):
            linked.owner.reconcile(next_context, current)
