"""Controlled original-owner transports; no live cleanup qualification."""

import base64
from copy import deepcopy
from dataclasses import replace
import gzip
import json
import os
import pytest

from devsecops.factory import controller_cleanup as c
from devsecops.factory import kubernetes_operations as k
from devsecops.factory.state import StateError, _sha
from tests.test_owned_foundation import invocation
from tests.test_kubernetes_operations import api, component
from tests import test_owned_foundation as fixture

setup = fixture.setup


def namespace():
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "mission-demo"}}


def controller(kind="HelmRelease"):
    api = {
        "HelmRelease": "helm.toolkit.fluxcd.io/v2",
        "Kustomization": "kustomize.toolkit.fluxcd.io/v1",
        "Application": "argoproj.io/v1alpha1",
    }[kind]
    row = {
        "apiVersion": api,
        "kind": kind,
        "metadata": {"name": "mission", "namespace": "mission-demo"},
        "spec": {},
    }
    if kind == "Application":
        row["metadata"]["finalizers"] = ["resources-finalizer.argocd.argoproj.io"]
    return c.compile_policy(row, c.POLICY)


def direct(resource, state, ctx, document):
    row = deepcopy(document)
    row["metadata"].setdefault("labels", {}).update({k.LABEL: ctx.run_id, k.BINDING: resource._binding})
    desired = deepcopy(row)
    row["metadata"].update(uid="uid-" + row["kind"].lower(), resourceVersion="1", generation=1)
    path = k._route(row)[1]
    state[path] = row
    resource._resources.append(
        {
            "path": path,
            "uid": row["metadata"]["uid"],
            "resourceVersion": "1",
            "manifest": desired,
            "operation_id": "original-create",
            "state": "present",
            "disposition": "remove",
            "observed_content": {
                key: deepcopy(value) for key, value in row.items() if key not in ("metadata", "status")
            },
            "identity": {
                "kind": row["kind"],
                "name": row["metadata"]["name"],
                "namespace": row["metadata"].get("namespace"),
                "uid": row["metadata"]["uid"],
            },
        }
    )
    return path


def release_secret(manifests, *, release="mission", version=1, payload_extra=None):
    payload = {
        "name": release,
        "namespace": "mission-demo",
        "version": version,
        "info": {"status": "deployed"},
        "manifest": "\n---\n".join(json.dumps(row) for row in manifests),
    }
    payload.update(payload_extra or {})
    raw = json.dumps(payload).encode()
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": f"sh.helm.release.v1.{release}.v{version}",
            "namespace": "mission-demo",
            "uid": "storage-uid",
            "resourceVersion": "1",
            "labels": {"owner": "helm", "name": release, "status": "deployed", "version": str(version)},
        },
        "type": "helm.sh/release.v1",
        "data": {"release": base64.b64encode(base64.b64encode(gzip.compress(raw))).decode()},
    }
    return secret


def selected_inventory(resource, ctx, manifests, **kwargs):
    resource.manifest_sha256 = tuple(_sha(k._bytes(row)) for row in manifests)
    return c.OwnedControllerInventory(resource, ctx, manifests, **kwargs)


def populated(resource, state, ctx, root):
    direct(resource, state, ctx, namespace())
    parent = direct(resource, state, ctx, root)
    state[parent]["status"] = {
        "observedGeneration": 1,
        "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 1}],
    }
    child = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "child",
            "namespace": "mission-demo",
            "uid": "child-uid",
            "resourceVersion": "1",
            "labels": {},
            "annotations": {},
        },
        "data": {"private": "never-export-this"},
    }
    if root["kind"] == "HelmRelease":
        child["metadata"]["labels"] = {"app.kubernetes.io/managed-by": "Helm"}
        child["metadata"]["annotations"] = {
            "meta.helm.sh/release-name": "mission",
            "meta.helm.sh/release-namespace": "mission-demo",
        }
        state[parent]["status"]["history"] = [
            {"name": "mission", "namespace": "mission-demo", "version": 1, "status": "deployed"}
        ]
        selected_child = {key: deepcopy(value) for key, value in child.items() if key != "metadata"}
        selected_child["metadata"] = {"name": "child", "namespace": "mission-demo"}
        secret = release_secret([selected_child])
        state[k._route(secret)[1]] = secret
    elif root["kind"] == "Kustomization":
        child["metadata"]["labels"] = {
            "kustomize.toolkit.fluxcd.io/name": "mission",
            "kustomize.toolkit.fluxcd.io/namespace": "mission-demo",
        }
        state[parent]["status"].update(
            lastAppliedRevision="main@sha1:" + "a" * 40,
            inventory={"entries": [{"id": "mission-demo_child__ConfigMap", "v": "v1"}]},
        )
    else:
        child["metadata"]["labels"] = {"app.kubernetes.io/instance": "mission"}
        state[parent]["status"].update(
            sync={"status": "Synced", "revision": "a" * 40},
            health={"status": "Healthy"},
            resources=[
                {
                    "group": "",
                    "kind": "ConfigMap",
                    "name": "child",
                    "namespace": "mission-demo",
                    "version": "v1",
                }
            ],
        )
    state[k._route(child)[1]] = child
    return parent, k._route(child)[1]


@pytest.mark.parametrize("kind", ["HelmRelease", "Kustomization", "Application"])
def test_original_inventory_and_actual_absence_never_grant_child_delete(setup, monkeypatch, kind):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        root = controller(kind)
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        _, child = populated(resource, state, ctx, root)
        inventory.capture(ctx)
        inventory.start_cleanup(ctx, reconcile=False)
        assert not inventory.children_absent(ctx)
        for path in list(inventory._children):
            del state[path]
        assert inventory.children_absent(ctx)
        evidence = json.dumps(inventory.observation())
        assert "never-export-this" not in evidence and child in evidence
        assert all(method == "GET" for method, _, _ in calls)
        with pytest.raises(StateError, match="already dispatched"):
            inventory.start_cleanup(ctx, reconcile=False)


@pytest.mark.parametrize("kind", ["HelmRelease", "Kustomization", "Application"])
def test_replacement_child_uid_blocks_absence_even_same_name(setup, monkeypatch, kind):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        root = controller(kind)
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        _, child = populated(resource, state, ctx, root)
        inventory.start_cleanup(ctx, reconcile=False)
        state[child]["metadata"]["uid"] = "foreign-replacement"
        with pytest.raises(StateError, match="UID changed"):
            inventory.children_absent(ctx)


def test_preexisting_selected_cluster_resource_rejects_before_any_write(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        resource.cluster_resources = (("rbac.authorization.k8s.io/v1", "ClusterRole", "child-role"),)
        state["/apis/rbac.authorization.k8s.io/v1/clusterroles/child-role"] = {"metadata": {"uid": "foreign"}}
        with pytest.raises(StateError, match="already exists"):
            selected_inventory(resource, ctx, (controller(),), policy=c.POLICY)
        assert all(method == "GET" for method, _, _ in calls)


def test_unselected_cluster_child_is_not_adopted_from_flux_inventory(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        root = controller("Kustomization")
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        parent, _ = populated(resource, state, ctx, root)
        state[parent]["status"]["inventory"]["entries"] = [
            {"id": "_foreign_rbac.authorization.k8s.io_ClusterRole", "v": "v1"}
        ]
        with pytest.raises(StateError, match="absence proof"):
            inventory.capture(ctx)
        assert all(method == "GET" for method, _, _ in calls)


@pytest.mark.parametrize(
    "change", ["keep", "namespace-swap", "wrong-release", "stale-generation", "nested-unsafe"]
)
def test_actual_inventory_rejects_unsupported_or_foreign_scope(setup, monkeypatch, change):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        root = controller()
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        parent, child = populated(resource, state, ctx, root)
        if change == "keep":
            state[child]["metadata"]["annotations"]["helm.sh/resource-policy"] = "keep"
        if change == "namespace-swap":
            state["/api/v1/namespaces/mission-demo"]["metadata"]["uid"] = "foreign"
        if change == "wrong-release":
            state[child]["metadata"]["annotations"]["meta.helm.sh/release-name"] = "foreign"
        if change == "stale-generation":
            state[parent]["status"]["observedGeneration"] = 0
        if change == "nested-unsafe":
            nested = controller()
            nested["metadata"]["name"] = "nested"
            nested["spec"]["install"]["crds"] = "Create"
            nested["metadata"].update(
                uid="nested-uid",
                resourceVersion="1",
                generation=1,
                labels={"app.kubernetes.io/managed-by": "Helm"},
                annotations={
                    "meta.helm.sh/release-name": "mission",
                    "meta.helm.sh/release-namespace": "mission-demo",
                },
            )
            state[k._route(nested)[1]] = nested
            secret = release_secret([nested])
            state[k._route(secret)[1]] = secret
        with pytest.raises(StateError):
            inventory.capture(ctx)
        assert all(method == "GET" for method, _, _ in calls)


def test_named_policy_is_explicit_and_preserves_original_manifest():
    original = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"name": "example", "namespace": "bigbang"},
        "spec": {"chart": {"spec": {"chart": "original"}}},
    }
    before = deepcopy(original)
    compiled = c.compile_policy(original, c.POLICY)
    assert original == before and compiled["spec"]["chart"] == original["spec"]["chart"]
    c.require_policy(compiled)
    for key in ("install", "upgrade", "rollback", "uninstall"):
        assert compiled["spec"][key]["disableHooks"] is True
    with pytest.raises(StateError):
        c.compile_policy(original, "unselected")


def test_release_decoder_bounded_and_rejects_non_gzip(monkeypatch):
    secret = release_secret([])
    assert c.decode_release(secret)[0]["name"] == "mission"
    monkeypatch.setattr(c, "MAX_RELEASE_BYTES", 20)
    with pytest.raises(StateError, match="bounds"):
        c.decode_release(secret)
    secret["data"]["release"] = base64.b64encode(base64.b64encode(b"{}")).decode()
    with pytest.raises(StateError):
        c.decode_release(secret)


@pytest.mark.parametrize("leave_child", [False, True])
def test_actual_cleanup_caller_waits_for_children_before_controller_teardown(setup, monkeypatch, leave_child):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resource = component(journal, session)
        root = controller()
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        parent, child = populated(resource, state, ctx, root)
        daemon = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "helm-controller", "namespace": "mission-demo"},
            "spec": {},
        }
        controller_path = direct(resource, state, ctx, daemon)
        inventory.capture(ctx)
        original = k.KubernetesSession.request

        def observe(self, method, path, **kwargs):
            if method == "DELETE" and path == parent and not leave_child:
                for item in list(inventory._children):
                    state.pop(item, None)
            if method == "DELETE" and path == controller_path:
                assert not any(path in state for path in inventory._children)
            return original(self, method, path, **kwargs)

        monkeypatch.setattr(k.KubernetesSession, "request", observe)
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: operation)
        monkeypatch.setattr(k.time, "sleep", lambda _: None)
        down = replace(ctx, phase={"id": "down", "operation": "down"})
        result = resource.down(down)
        report = resource.cleanup_observation(result)
        assert report["complete_cleanup"] is (not leave_child)
        deletes = [path for method, path, _ in calls if method == "DELETE"]
        assert deletes[0] == parent
        assert (controller_path in deletes) is (not leave_child)
        assert child not in deletes
        if leave_child:
            count = len(deletes)
            resource.reconcile(down, operation)
            assert sum(method == "DELETE" for method, _, _ in calls) == count


def test_child_inventory_is_not_reconstructed_from_report(setup, monkeypatch):
    session, state, _ = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        inventory = selected_inventory(resource, ctx, (namespace(),), policy=c.POLICY)
        resource._controller_inventory = deepcopy(inventory.observation())
        with pytest.raises(StateError, match="capability changed"):
            inventory.capture(ctx)
        resource._controller_inventory = inventory
        other_pid = os.getpid() + 1
        monkeypatch.setattr(c.os, "getpid", lambda: other_pid)
        with pytest.raises(StateError, match="capability changed"):
            inventory.capture(ctx)


def test_inventory_cannot_omit_selected_manifest_or_created_controller(setup, monkeypatch):
    session, state, _ = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        with pytest.raises(StateError, match="every originally selected"):
            c.OwnedControllerInventory(resource, ctx, (controller(),), policy=c.POLICY)
        inventory = selected_inventory(resource, ctx, (namespace(),), policy=c.POLICY)
        direct(resource, state, ctx, controller())
        with pytest.raises(StateError, match="absent from selected"):
            inventory.capture(ctx)


def test_parent_change_during_actual_child_read_rejects_snapshot(setup, monkeypatch):
    session, state, _ = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        root = controller("Kustomization")
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        parent, child = populated(resource, state, ctx, root)
        original = k.KubernetesSession.request

        def race(self, method, path, **kwargs):
            value = original(self, method, path, **kwargs)
            if method == "GET" and path == child:
                state[parent]["status"]["lastAppliedRevision"] = "main@sha1:" + "b" * 40
            return value

        monkeypatch.setattr(k.KubernetesSession, "request", race)
        with pytest.raises(StateError, match="changed during"):
            inventory.capture(ctx)


def test_helm_rendered_content_mismatch_rejects_same_uid(setup, monkeypatch):
    session, state, _ = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        root = controller()
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        _, child = populated(resource, state, ctx, root)
        state[child]["data"]["private"] = "different-content"
        with pytest.raises(StateError, match="content differs"):
            inventory.capture(ctx)


def test_actual_argo_cluster_child_needs_preflight_scope_and_absence(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        resource.cluster_resources = (("rbac.authorization.k8s.io/v1", "ClusterRole", "child-role"),)
        root = controller("Application")
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        parent, _ = populated(resource, state, ctx, root)
        role = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {
                "name": "child-role",
                "uid": "role-uid",
                "resourceVersion": "1",
                "labels": {"app.kubernetes.io/instance": "mission"},
            },
            "rules": [],
        }
        path = k._route(role)[1]
        state[path] = role
        state[parent]["status"]["resources"].append(
            {
                "group": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "child-role",
                "version": "v1",
            }
        )
        inventory.start_cleanup(ctx, reconcile=False)
        assert inventory._children[path]["uid"] == "role-uid"
        for child in list(inventory._children):
            state.pop(child)
        assert inventory.children_absent(ctx)
        assert all(method == "GET" for method, _, _ in calls)


def test_cleanup_continuation_requires_original_terminal_reconcile_and_new_operation(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resource = component(journal, session)
        root = controller()
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        parent, _ = populated(resource, state, ctx, root)
        worker = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "helm-controller", "namespace": "mission-demo"},
            "spec": {},
        }
        worker_path = direct(resource, state, ctx, worker)
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: operation)
        monkeypatch.setattr(k.time, "sleep", lambda _: None)
        down = replace(ctx, phase={"id": "down", "operation": "down"})
        resource.down(down)
        assert worker_path in state and parent not in state
        with pytest.raises(StateError, match="continuation lacks"):
            resource.continue_cleanup(down, operation)
        for path in list(inventory._children):
            state.pop(path, None)
        outcome = resource.reconcile(down, operation)
        assert resource.cleanup_observation(outcome)["cleanup_eligible"] is True
        # The caller boundary uses a controlled current journal view; the separate
        # journal continuation suite verifies durable continues_operation replay.
        view = deepcopy(journal.state)
        view["active_mutation"] = {"operation_id": "new-operation", "continues_operation": operation}
        original_state = type(journal).state
        monkeypatch.setattr(
            type(journal),
            "state",
            property(lambda self: deepcopy(view) if self is journal else original_state.fget(self)),
        )
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: "new-operation")
        result = resource.continue_cleanup(down, operation)
        assert resource.cleanup_observation(result)["complete_cleanup"] is True
        assert worker_path not in state
        assert sum(method == "DELETE" and path == parent for method, path, _ in calls) == 1


def test_policy_is_applied_by_actual_offline_compiler(tmp_path, monkeypatch):
    from tests.test_platform_bootstrap import fixture as platform_fixture

    producer, ctx, _, calls = platform_fixture(tmp_path, monkeypatch)
    groups = producer.prepare(ctx)
    release = next(row for group in groups for row in group if row["kind"] == "HelmRelease")
    c.require_policy(release)
    assert calls and producer.recipe["controller_cleanup"] == c.POLICY
    compiled = next(iter(producer._files))
    assert json.loads(compiled.read_bytes())["controller_cleanup"] == c.POLICY


@pytest.mark.parametrize("replacement", ["crd", "namespace"])
def test_custom_child_route_comes_only_from_pinned_original_crd(setup, monkeypatch, replacement):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        root = controller("Kustomization")
        crd = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "servicemonitors.monitoring.coreos.com"},
            "spec": {
                "group": "monitoring.coreos.com",
                "scope": "Namespaced",
                "names": {"kind": "ServiceMonitor", "plural": "servicemonitors"},
                "versions": [{"name": "v1", "served": True, "storage": True}],
            },
        }
        resource.cluster_resources = ((crd["apiVersion"], crd["kind"], crd["metadata"]["name"]),)
        inventory = selected_inventory(resource, ctx, (namespace(), root, crd), policy=c.POLICY)
        parent, _ = populated(resource, state, ctx, root)
        crd_path = direct(resource, state, ctx, crd)
        child = {
            "apiVersion": "monitoring.coreos.com/v1",
            "kind": "ServiceMonitor",
            "metadata": {
                "name": "monitor",
                "namespace": "mission-demo",
                "uid": "monitor-uid",
                "resourceVersion": "1",
                "labels": {
                    "kustomize.toolkit.fluxcd.io/name": "mission",
                    "kustomize.toolkit.fluxcd.io/namespace": "mission-demo",
                },
            },
            "spec": {"selector": {"matchLabels": {"app": "mission"}}},
        }
        path = "/apis/monitoring.coreos.com/v1/namespaces/mission-demo/servicemonitors/monitor"
        state[path] = child
        state[parent]["status"]["inventory"]["entries"] = [
            {"id": "mission-demo_monitor_monitoring.coreos.com_ServiceMonitor", "v": "v1"}
        ]
        inventory.start_cleanup(ctx, reconcile=False)
        assert inventory._children[path]["uid"] == "monitor-uid"
        del state[path]
        assert inventory.children_absent(ctx)
        if replacement == "crd":
            state[crd_path]["metadata"]["uid"] = "foreign-crd"
        else:
            del state[crd_path]
            state["/api/v1/namespaces/mission-demo"]["metadata"]["uid"] = "foreign-namespace"
        with pytest.raises(StateError, match="identity"):
            inventory.children_absent(ctx)
        assert all(method == "GET" for method, _, _ in calls)


@pytest.mark.parametrize("selected_but_never_created", [False, True])
def test_unused_selected_cluster_child_appearing_after_capture_blocks_teardown(
    setup, monkeypatch, selected_but_never_created
):
    session, state, _ = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        resource.cluster_resources = (("rbac.authorization.k8s.io/v1", "ClusterRole", "late-role"),)
        root = controller("Kustomization")
        planned = (namespace(), root)
        if selected_but_never_created:
            planned += (
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "ClusterRole",
                    "metadata": {"name": "late-role"},
                    "rules": [],
                },
            )
        inventory = selected_inventory(resource, ctx, planned, policy=c.POLICY)
        populated(resource, state, ctx, root)
        inventory.start_cleanup(ctx, reconcile=False)
        for path in list(inventory._children):
            state.pop(path)
        path = "/apis/rbac.authorization.k8s.io/v1/clusterroles/late-role"
        state[path] = {"metadata": {"uid": "untracked-role"}}
        assert inventory.children_absent(ctx) is False
        assert inventory.observation()["selected_cluster_scope_observations"] == [
            {"path": path, "state": "untracked-present"}
        ]


def test_actual_delete_rejects_new_resource_version_after_inventory(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resource = component(journal, session)
        root = controller("Kustomization")
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        parent, _ = populated(resource, state, ctx, root)
        original = inventory.capture

        def raced(context):
            original(context)
            state[parent]["metadata"]["resourceVersion"] = "2"

        monkeypatch.setattr(inventory, "capture", raced)
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: operation)
        down = replace(ctx, phase={"id": "down", "operation": "down"})
        result = resource.down(down)
        report = resource.cleanup_observation(result)
        assert report["deferred_controller_paths"] == (parent,)
        assert report["complete_cleanup"] is False
        assert not any(method == "DELETE" for method, _, _ in calls)


def test_status_refresh_can_continue_only_with_new_action_preserving_old_child_union(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resource = component(journal, session)
        root = controller("Kustomization")
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        parent, first_child = populated(resource, state, ctx, root)
        original_capture = inventory.capture
        second = deepcopy(state[first_child])
        second["metadata"].update(name="second", uid="second-child-uid")
        second_path = k._route(second)[1]

        def raced(context):
            original_capture(context)
            state[parent]["metadata"]["resourceVersion"] = "2"
            state[parent]["status"]["inventory"]["entries"].append(
                {"id": "mission-demo_second__ConfigMap", "v": "v1"}
            )
            state[second_path] = second

        monkeypatch.setattr(inventory, "capture", raced)
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: operation)
        monkeypatch.setattr(k.time, "sleep", lambda _: None)
        down = replace(ctx, phase={"id": "down", "operation": "down"})
        result = resource.down(down)
        assert resource.cleanup_observation(result)["deferred_controller_paths"] == (parent,)
        assert first_child in inventory._children and second_path not in inventory._children
        monkeypatch.setattr(inventory, "capture", original_capture)
        reconciled = resource.reconcile(down, operation)
        assert resource.cleanup_observation(reconciled)["cleanup_eligible"]
        assert not any(method == "DELETE" for method, _, _ in calls)
        view = deepcopy(journal.state)
        view["active_mutation"] = {"operation_id": "new-operation", "continues_operation": operation}
        original_state = type(journal).state
        monkeypatch.setattr(
            type(journal),
            "state",
            property(lambda self: deepcopy(view) if self is journal else original_state.fget(self)),
        )
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: "new-operation")
        original_request = k.KubernetesSession.request

        def finalize(self, method, path, **kwargs):
            if method == "DELETE" and path == parent:
                assert {first_child, second_path} <= inventory._children.keys()
                for child in list(inventory._children):
                    state.pop(child, None)
            return original_request(self, method, path, **kwargs)

        monkeypatch.setattr(k.KubernetesSession, "request", finalize)
        final = resource.continue_cleanup(down, operation)
        assert resource.cleanup_observation(final)["complete_cleanup"]
        assert sum(method == "DELETE" and path == parent for method, path, _ in calls) == 1


def test_inventory_cannot_be_bound_after_existing_resource_effects(setup, monkeypatch):
    session, state, _ = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        direct(resource, state, ctx, namespace())
        with pytest.raises(StateError, match="before any original resource effect"):
            selected_inventory(resource, ctx, (namespace(), controller()), policy=c.POLICY)


def test_completed_custom_cleanup_with_lost_local_receipt_reconciles_without_removed_api(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resource = component(journal, session)
        root = controller("Kustomization")
        crd = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "servicemonitors.monitoring.coreos.com"},
            "spec": {
                "group": "monitoring.coreos.com",
                "scope": "Namespaced",
                "names": {"kind": "ServiceMonitor", "plural": "servicemonitors"},
                "versions": [{"name": "v1", "served": True, "storage": True}],
            },
        }
        resource.cluster_resources = ((crd["apiVersion"], crd["kind"], crd["metadata"]["name"]),)
        selected_inventory(resource, ctx, (namespace(), root, crd), policy=c.POLICY)
        parent, _ = populated(resource, state, ctx, root)
        crd_path = direct(resource, state, ctx, crd)
        path = "/apis/monitoring.coreos.com/v1/namespaces/mission-demo/servicemonitors/monitor"
        state[path] = {
            "apiVersion": "monitoring.coreos.com/v1",
            "kind": "ServiceMonitor",
            "metadata": {
                "name": "monitor",
                "namespace": "mission-demo",
                "uid": "monitor-uid",
                "resourceVersion": "1",
                "labels": {
                    "kustomize.toolkit.fluxcd.io/name": "mission",
                    "kustomize.toolkit.fluxcd.io/namespace": "mission-demo",
                },
            },
            "spec": {},
        }
        state[parent]["status"]["inventory"]["entries"] = [
            {"id": "mission-demo_monitor_monitoring.coreos.com_ServiceMonitor", "v": "v1"}
        ]
        original_request, original_record = k.KubernetesSession.request, resource._record

        def finalize(self, method, target, **kwargs):
            if method == "DELETE" and target == parent:
                state.pop(path)
            return original_request(self, method, target, **kwargs)

        def lost(kind, value):
            if kind == "cleanup":
                raise OSError("controlled lost local receipt")
            return original_record(kind, value)

        monkeypatch.setattr(k.KubernetesSession, "request", finalize)
        monkeypatch.setattr(resource, "_record", lost)
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: operation)
        down = replace(ctx, phase={"id": "down", "operation": "down"})
        with pytest.raises(OSError, match="controlled lost"):
            resource.down(down)
        assert crd_path not in state and "/api/v1/namespaces/mission-demo" not in state
        marker = len(calls)
        monkeypatch.setattr(resource, "_record", original_record)
        result = resource.reconcile(down, operation)
        observed = resource.cleanup_observation(result)
        assert observed["complete_cleanup"] is True
        assert observed["controller_inventory"]["namespace_closure"][path]["namespace_state"] == "absent"
        assert not any(method != "GET" or target == path for method, target, _ in calls[marker:])


@pytest.mark.parametrize("missing_child", [False, True])
def test_actual_failed_helm_storage_allows_scoped_cleanup_without_success_claim(
    setup, monkeypatch, missing_child
):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resource = component(journal, session)
        root = controller()
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        parent, child = populated(resource, state, ctx, root)
        state[parent]["status"]["conditions"][0]["status"] = "False"
        state[parent]["status"]["history"][0]["status"] = "failed"
        secret_path = next(path for path in state if "/secrets/sh.helm.release" in path)
        payload, _ = c.decode_release(state[secret_path])
        payload["info"]["status"] = "failed"
        state[secret_path]["metadata"]["labels"]["status"] = "failed"
        state[secret_path]["data"]["release"] = base64.b64encode(
            base64.b64encode(gzip.compress(json.dumps(payload).encode()))
        ).decode()
        if missing_child:
            del state[child]
        original = k.KubernetesSession.request

        def uninstall(self, method, path, **kwargs):
            if method == "DELETE" and path == parent:
                for item in list(inventory._children):
                    state.pop(item, None)
            return original(self, method, path, **kwargs)

        monkeypatch.setattr(k.KubernetesSession, "request", uninstall)
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: operation)
        down = replace(ctx, phase={"id": "down", "operation": "down"})
        result = resource.down(down)
        observed = resource.cleanup_observation(result)
        assert observed["complete_cleanup"]
        assert observed["controller_inventory"]["controllers"][0]["release_status"] == "failed"
        assert all(target != child for method, target, _ in calls if method == "DELETE")


def test_first_failed_install_without_actual_versioned_inventory_stays_incomplete(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resource = component(journal, session)
        root = controller()
        selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        parent, _ = populated(resource, state, ctx, root)
        state[parent]["status"] = {
            "observedGeneration": 1,
            "conditions": [{"type": "Ready", "status": "False", "observedGeneration": 1}],
        }
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: operation)
        down = replace(ctx, phase={"id": "down", "operation": "down"})
        with pytest.raises(StateError, match="usable current versioned history/storage inventory"):
            resource.down(down)
        assert not any(method == "DELETE" for method, _, _ in calls)


def test_original_pending_application_defers_child_scope_until_actual_ready_delivery(setup, monkeypatch):
    session, state, _ = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        root = controller("Application")
        inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
        parent, child = populated(resource, state, ctx, root)
        state[parent]["status"]["health"]["status"] = "Progressing"
        inventory.capture(ctx, defer_unready_application=True)
        assert not inventory.observation()["captured"]
        assert (
            inventory.observation()["pending_application_inventory"][parent]["uid"]
            == state[parent]["metadata"]["uid"]
        )
        assert child not in inventory._children
        with pytest.raises(StateError, match="Healthy/Synced"):
            inventory.capture(replace(ctx, phase={"id": "cleanup", "operation": "down"}))
        state[parent]["status"]["health"]["status"] = "Healthy"
        inventory.capture(replace(ctx, phase={"id": "build", "operation": "build-promote"}))
        assert inventory.observation()["captured"] and child in inventory._children
        assert not inventory.observation()["pending_application_inventory"]


def failed_argo_fixture(journal, ctx, resource, state, monkeypatch, *, bind_seed=True):
    """Actual opaque input derivation with controlled original GitLab reader transport."""
    from devsecops.factory import platform_inputs as inputs_module
    from tests.test_platform_inputs import producer, templates

    platform, authority, reader = producer(journal, ctx, monkeypatch)
    ns, source, secret, argo_secret = templates()
    root = controller("Application")
    root["spec"] = {
        "source": {"repoURL": inputs_module.URL, "targetRevision": "main", "path": "charts/mission"},
        "destination": {"server": resource.session.origin, "namespace": "mission-demo"},
    }
    trust = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "immutable": True,
        "metadata": {
            "namespace": "mission-demo",
            "name": "argocd-tls-certs-cm",
            "labels": {"app.kubernetes.io/part-of": "argocd"},
        },
        "data": {inputs_module.HOST: inputs_module.CA},
    }
    inputs = inputs_module.prepare_owned_inputs(
        platform,
        ctx,
        bootstrap_authority=authority,
        reader_origin=reader,
        groups=((ns,), (source,)),
        additional=(secret, argo_secret, trust, root),
    )
    record = inputs_module.require_owned_inputs(inputs, ctx, platform=platform)
    docs = tuple(row for group in record["groups"] for row in group)
    root = next(row for row in docs if row["kind"] == "Application")
    platform._owned_inputs = inputs
    authority._owner._kubernetes = resource
    inventory = selected_inventory(
        resource,
        ctx,
        docs,
        policy=c.POLICY,
        platform=platform if bind_seed else None,
    )
    parent, child = populated(resource, state, ctx, root)
    app = state[parent]
    app["metadata"]["creationTimestamp"] = "2026-01-01T00:00:00Z"
    app["status"].update(
        health={"status": "Missing"},
        sync={
            "status": "OutOfSync",
            "revision": "b" * 40,
            "comparedTo": {"source": deepcopy(root["spec"]["source"])},
        },
        operationState={
            "phase": "Failed",
            "startedAt": "2026-01-01T00:00:01Z",
            "finishedAt": "2026-01-01T00:00:02Z",
            "operation": {"sync": {"revision": "b" * 40}},
            "syncResult": {
                "revision": "b" * 40,
                "source": deepcopy(root["spec"]["source"]),
                "resources": [
                    {**deepcopy(app["status"]["resources"][0]), "status": "SyncFailed", "syncPhase": "Sync"}
                ],
            },
        },
    )
    return inventory, platform, parent, child


@pytest.mark.parametrize("phase", ["Failed", "Error"])
@pytest.mark.parametrize("missing_child", [False, True])
def test_terminal_failed_seed_argo_actual_inventory_allows_only_original_parent_cleanup(
    setup, monkeypatch, phase, missing_child
):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resource = component(journal, session)
        inventory, _, parent, child = failed_argo_fixture(journal, ctx, resource, state, monkeypatch)
        state[parent]["status"]["operationState"]["phase"] = phase
        if missing_child:
            del state[child]
        original = k.KubernetesSession.request

        def finalize(self, method, path, **kwargs):
            if method == "DELETE" and path == parent:
                state.pop(child, None)
            return original(self, method, path, **kwargs)

        monkeypatch.setattr(k.KubernetesSession, "request", finalize)
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: operation)
        down = replace(ctx, phase={"id": "down", "operation": "down"})
        result = resource.down(down)
        observed = resource.cleanup_observation(result)
        assert observed["complete_cleanup"]
        proof = observed["controller_inventory"]["controllers"][0]["terminal_failure"]
        assert proof["phase"] == phase and proof["seed_revision"] == "b" * 40
        assert proof["seed_project_id"] == 37
        assert all(
            target in (parent, "/api/v1/namespaces/mission-demo")
            for method, target, _ in calls
            if method == "DELETE"
        )
        if missing_child:
            assert inventory._children[child]["uid"] is None
            assert (
                inventory._children[child]["absence_basis"]
                == "actual-terminal-argo-inventory-and-current-get"
            )


@pytest.mark.parametrize(
    "change",
    [
        "no-seed",
        "not-down",
        "seed-binding-changed",
        "seed-changed",
        "source-changed",
        "running",
        "active-operation",
        "finished-missing",
        "result-revision",
        "request-revision",
        "sync-revision",
        "result-source",
        "comparison-source",
        "unknown-sync",
        "comparison-error",
        "empty-inventory",
        "missing-inventory",
        "extra-result",
        "duplicate-result",
        "duplicate-status",
        "result-subset",
        "request-subset",
        "hook-type",
        "hook-phase",
        "status-hook",
        "live-argo-hook",
        "live-helm-hook",
        "replaced-parent",
        "malformed-state",
        "malformed-request",
        "malformed-result",
        "malformed-sync",
        "nonterminal-result",
        "future-finish",
        "missing-nested-controller",
        "namespace-replaced",
    ],
)
def test_failed_argo_incomplete_or_changed_evidence_blocks_before_delete(setup, monkeypatch, change):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resource = component(journal, session)
        inventory, platform, parent, child = failed_argo_fixture(
            journal,
            ctx,
            resource,
            state,
            monkeypatch,
            bind_seed=change != "no-seed",
        )
        app = state[parent]
        status = app["status"]
        failed = status["operationState"]
        result = failed["syncResult"]
        changes = {
            "seed-binding-changed": lambda: setattr(platform, "_owned_inputs", object()),
            "seed-changed": lambda: inventory._seed_source.update(revision="c" * 40),
            "source-changed": lambda: app["spec"]["source"].update(path="unselected"),
            "running": lambda: failed.update(phase="Running"),
            "active-operation": lambda: app.update(operation={"sync": {"revision": "b" * 40}}),
            "finished-missing": lambda: failed.pop("finishedAt"),
            "result-revision": lambda: result.update(revision="c" * 40),
            "request-revision": lambda: failed["operation"]["sync"].update(revision="c" * 40),
            "sync-revision": lambda: status["sync"].update(revision="c" * 40),
            "result-source": lambda: result["source"].update(path="unselected"),
            "comparison-source": lambda: status["sync"]["comparedTo"]["source"].update(path="unselected"),
            "unknown-sync": lambda: status["sync"].update(status="Unknown"),
            "comparison-error": lambda: status.update(conditions=[{"type": "ComparisonError"}]),
            "empty-inventory": lambda: status.update(resources=[]),
            "missing-inventory": lambda: status.pop("resources"),
            "extra-result": lambda: result["resources"].append({**result["resources"][0], "name": "extra"}),
            "duplicate-result": lambda: result["resources"].append(deepcopy(result["resources"][0])),
            "duplicate-status": lambda: status["resources"].append(deepcopy(status["resources"][0])),
            "result-subset": lambda: result.update(resources=[]),
            "request-subset": lambda: failed["operation"]["sync"].update(resources=[{"name": "child"}]),
            "hook-type": lambda: result["resources"][0].update(hookType="Sync"),
            "hook-phase": lambda: result["resources"][0].update(hookPhase="Succeeded"),
            "status-hook": lambda: status["resources"][0].update(hook=True),
            "live-argo-hook": lambda: state[child]["metadata"]["annotations"].update(
                {"argocd.argoproj.io/hook": "Sync"}
            ),
            "live-helm-hook": lambda: state[child]["metadata"]["annotations"].update(
                {"helm.sh/hook": "post-install"}
            ),
            "replaced-parent": lambda: app["metadata"].update(uid="replacement"),
            "malformed-state": lambda: status.update(operationState=[]),
            "malformed-request": lambda: failed.update(operation=None),
            "malformed-result": lambda: failed.update(syncResult=None),
            "malformed-sync": lambda: status.update(sync=[]),
            "nonterminal-result": lambda: result["resources"][0].update(status=""),
            "future-finish": lambda: failed.update(finishedAt="2999-01-01T00:00:02Z"),
            "namespace-replaced": lambda: state["/api/v1/namespaces/mission-demo"]["metadata"].update(
                uid="replacement"
            ),
        }
        if change == "missing-nested-controller":
            entry = {
                "group": "helm.toolkit.fluxcd.io",
                "version": "v2",
                "kind": "HelmRelease",
                "namespace": "mission-demo",
                "name": "missing",
            }
            status["resources"] = [entry]
            result["resources"] = [{**entry, "status": "SyncFailed", "syncPhase": "Sync"}]
        elif change in changes:
            changes[change]()
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: operation)
        down = replace(ctx, phase={"id": "down", "operation": "down"})
        with pytest.raises(StateError):
            inventory.capture(ctx if change == "not-down" else down)
        assert not any(method == "DELETE" for method, _, _ in calls)


@pytest.mark.parametrize("change", ["operation", "operationState", "conditions"])
def test_failed_argo_retry_during_child_observation_rejected_before_delete(setup, monkeypatch, change):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resource = component(journal, session)
        _, _, parent, child = failed_argo_fixture(journal, ctx, resource, state, monkeypatch)
        original = k.KubernetesSession.request

        def retry(self, method, path, **kwargs):
            observed = original(self, method, path, **kwargs)
            if method == "GET" and path == child:
                if change == "operation":
                    state[parent]["operation"] = {"sync": {"revision": "b" * 40}}
                elif change == "operationState":
                    state[parent]["status"]["operationState"]["phase"] = "Running"
                else:
                    state[parent]["status"]["conditions"] = [{"type": "ComparisonError"}]
            return observed

        monkeypatch.setattr(k.KubernetesSession, "request", retry)
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: operation)
        down = replace(ctx, phase={"id": "down", "operation": "down"})
        with pytest.raises(StateError, match="changed during inventory"):
            resource.down(down)
        assert not any(method == "DELETE" for method, _, _ in calls)
