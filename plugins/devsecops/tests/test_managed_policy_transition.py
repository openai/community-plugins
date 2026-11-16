"""Controlled direct runtime authority; real selector CAS and ownership ledger."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import pytest
from devsecops.factory import kubernetes_operations as k
from devsecops.factory.owned_runtime_inputs import OwnedRuntimeInputCoordinator
from devsecops.factory.state import StateError, _sha
from tests.test_kubernetes_operations import api, component
from tests.test_owned_foundation import invocation
from tests import test_owned_foundation as foundation_fixture

setup = foundation_fixture.setup


def policy():
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "namespace": "mission-demo",
            "name": "mission",
            "uid": "policy-uid",
            "resourceVersion": "1",
        },
        "spec": {
            "podSelector": {"matchLabels": {"app": "mission"}},
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"podSelector": {"matchLabels": {"role": "allowed"}}}]}],
        },
    }


@pytest.mark.parametrize("change", [None, "namespace", "selfheal", "hash", "unknown"])
def test_original_git_argo_policy_allows_only_bound_scalar_without_adoption(setup, monkeypatch, change):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resources = component(journal, session)
        ctx = replace(ctx, phase={"id": "build-promote", "operation": "build-promote"})
        # Production lifecycle grant admission is separately qualified by root;
        # exercise the exact runtime producer type + real current UID/spec CAS.
        monkeypatch.setattr(resources, "_admit", lambda *a, **kw: operation)
        path = "/apis/networking.k8s.io/v1/namespaces/mission-demo/networkpolicies/mission"
        baseline = policy()
        fault = deepcopy(baseline)
        fault["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"]["role"] = "denied"
        state[path] = deepcopy(baseline)
        state["/api/v1/namespaces/mission-demo"] = {"metadata": {"uid": "namespace-uid"}}
        runtime = object.__new__(OwnedRuntimeInputCoordinator)
        runtime.journal = journal
        runtime.bootstrap = SimpleNamespace(kubernetes=lambda c: resources)

        def target(context):
            return {
                "run_id": ctx.run_id,
                "identities": dict(ctx.identities),
                "cluster_uid": "cluster-uid",
                "origin": session.origin,
                "namespace": "mission-demo",
                "namespace_uid": "changed" if change == "namespace" else "namespace-uid",
                "application": {"uid": "application-uid", "self_heal": change == "selfheal"},
                "policy_path": path,
                "policy": deepcopy(state[path]),
                "baseline_spec_sha256": _sha(k._bytes(baseline["spec"])),
                "fault_spec_sha256": "f" * 64 if change == "hash" else _sha(k._bytes(fault["spec"])),
            }

        monkeypatch.setattr(runtime, "require_policy_target", target)
        original = k.KubernetesSession.request

        def request(self, method, request_path, *, payload=None, deadline):
            if method == "PUT":
                calls.append((method, request_path, deepcopy(payload)))
                assert payload["metadata"]["resourceVersion"] == state[path]["metadata"]["resourceVersion"]
                if change == "unknown":
                    raise k.KubernetesOperationError("unknown-put")
                state[path] = deepcopy(payload)
                state[path]["metadata"]["resourceVersion"] = str(
                    int(state[path]["metadata"]["resourceVersion"]) + 1
                )
                return deepcopy(state[path])
            return original(self, method, request_path, payload=payload, deadline=deadline)

        monkeypatch.setattr(k.KubernetesSession, "request", request)
        if change:
            with pytest.raises(StateError):
                resources.replace_network_policy(
                    ctx,
                    original_uid="policy-uid",
                    expected_spec_sha256=_sha(k._bytes(baseline["spec"])),
                    manifest=fault,
                    runtime_inputs=runtime,
                )
            assert bool(resources._unresolved) == (change == "unknown")
        else:
            first = resources.replace_network_policy(
                ctx,
                original_uid="policy-uid",
                expected_spec_sha256=_sha(k._bytes(baseline["spec"])),
                manifest=fault,
                runtime_inputs=runtime,
            )
            assert first["managed_by"] == "original-owned-argo-delivery"
            resources.replace_network_policy(
                ctx,
                original_uid="policy-uid",
                expected_spec_sha256=_sha(k._bytes(fault["spec"])),
                manifest=baseline,
                runtime_inputs=runtime,
            )
            assert resources._managed_policies[path]["state"] == "baseline"
        assert resources._resources == []  # Never invent create/delete ownership.
