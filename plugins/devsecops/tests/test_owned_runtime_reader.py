"""Original owned Kubernetes creates and TokenRequest under a real run journal.

Only the HTTPS boundary is scripted. No actual token or live call is used.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import ModuleType
import sys
import pytest

from devsecops.factory import kubernetes_operations as k, owned_runtime_reader as r
from devsecops.factory.state import StateError, _sha
from devsecops.factory.owned_linux import OwnedLinuxDriver
from tests.test_kubernetes_operations import api
from tests.test_owned_foundation import invocation
from tests import test_owned_foundation as foundation_fixture

setup = foundation_fixture.setup


def materials(role="runtime", verbs=None):
    sa = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"namespace": "mission-demo", "name": role + "-reader"},
        "automountServiceAccountToken": False,
    }
    roles = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {"namespace": "mission-demo", "name": "runtime-role"},
        "rules": [{"apiGroups": ["apps"], "resources": ["deployments"], "verbs": verbs or ["get", "list"]}],
    }
    binding = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"namespace": "mission-demo", "name": "runtime-read"},
        "subjects": [{"kind": "ServiceAccount", "namespace": "mission-demo", "name": sa["metadata"]["name"]}],
        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "runtime-role"},
    }

    def descriptor(d):
        return {key: deepcopy(d[key]) for key in ("apiVersion", "kind", "metadata")}

    selected = {
        "role": role,
        "serviceaccount": descriptor(sa),
        "bindings": [descriptor(binding)] if role == "runtime" else [],
        "audience": "https://kubernetes.default.svc",
        "duration_seconds": 600,
    }
    return ([sa, roles, binding] if role == "runtime" else [sa]), selected


def ready(journal, ctx, tmp, monkeypatch, *, role="runtime", verbs=None, fault=False):
    session, state, calls = api(monkeypatch)
    session.ca_file = tmp / "ca.crt"
    session.ca_file.write_bytes(b"controlled-ca")
    docs, selection = materials(role, verbs)
    driver = OwnedLinuxDriver(journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0")
    resources = k.OwnedKubernetesResources(
        driver,
        session=session,
        cluster_uid="cluster-uid",
        namespaces=("mission-demo",),
        manifest_sha256=tuple(_sha(k._bytes(d)) for d in docs),
        reader_selections=(selection,),
    )
    original = k.KubernetesSession.request

    def request(self, method, path, *, payload=None, deadline):
        if path.endswith("/token"):
            calls.append((method, path, deepcopy(payload)))
            assert method == "POST" and payload["spec"]["audiences"] == [selection["audience"]]
            assert list((journal.run_dir / "kubernetes-resources").glob("*reader-intent.json"))
            if fault:
                raise k.KubernetesOperationError("unknown-token-ack")
            return {
                "status": {
                    "token": "SYNTHETIC_RUNTIME_TOKEN_CANARY",
                    "expirationTimestamp": (datetime.now(timezone.utc) + timedelta(seconds=599)).isoformat(),
                }
            }
        return original(self, method, path, payload=payload, deadline=deadline)

    monkeypatch.setattr(k.KubernetesSession, "request", request)
    for doc in docs:
        resources.create(ctx, doc)
    state["/apis/rbac.authorization.k8s.io/v1/rolebindings"] = {
        "items": [deepcopy(row) for row in state.values() if row.get("kind") == "RoleBinding"]
    }
    state["/apis/rbac.authorization.k8s.io/v1/clusterrolebindings"] = {"items": []}
    return resources, selection, state, calls


def bind(monkeypatch, resources, value):
    authority = object()
    module = ModuleType("devsecops.factory.canonical_bootstrap")
    module.require_bootstrap_authority = lambda a, j: {
        "kubernetes": {"cluster_uid": "cluster-uid", "origin": resources.session.origin}
    }
    monkeypatch.setitem(sys.modules, "devsecops.factory.canonical_bootstrap", module)
    r._bind(value, bootstrap_authority=authority, resources=resources)
    return {"journal": resources.journal, "bootstrap_authority": authority}


@pytest.mark.parametrize("role", ["runtime", "denied"])
def test_original_owned_reader_issues_scoped_token_without_secret_evidence(setup, monkeypatch, role):
    with invocation(setup) as (journal, _, ctx, _):
        resources, selection, state, calls = ready(journal, ctx, setup["tmp"], monkeypatch, role=role)
        value = resources.issue_read_token(ctx, selection=selection)
        scope = bind(monkeypatch, resources, value)
        observed = r.require_runtime_reader(value, **scope)
        assert observed["role"] == role and bool(observed["selected_resource_grants"]) == (role == "runtime")
        assert r.private_token(value, **scope) == "SYNTHETIC_RUNTIME_TOKEN_CANARY"
        assert all(
            b"SYNTHETIC_RUNTIME_TOKEN_CANARY" not in p.read_bytes()
            for p in (journal.run_dir / "kubernetes-resources").iterdir()
        )
        with pytest.raises(StateError, match="already attempted"):
            resources.issue_read_token(ctx, selection=selection)
        assert sum(path.endswith("/token") for _, path, _ in calls) == 1


def test_read_rule_with_write_or_secret_access_cannot_issue(setup, monkeypatch):
    with invocation(setup) as (journal, _, ctx, _):
        resources, selection, state, calls = ready(
            journal, ctx, setup["tmp"], monkeypatch, verbs=["get", "update"]
        )
        with pytest.raises(StateError, match="get/list"):
            resources.issue_read_token(ctx, selection=selection)
        assert not any(path.endswith("/token") for _, path, _ in calls)


def test_unknown_token_creation_is_not_retried_or_exposed(setup, monkeypatch):
    with invocation(setup) as (journal, _, ctx, _):
        resources, selection, state, calls = ready(journal, ctx, setup["tmp"], monkeypatch, fault=True)
        with pytest.raises(k.KubernetesOperationError):
            resources.issue_read_token(ctx, selection=selection)
        assert resources._unresolved[0]["verb"] == "issue-reader"
        with pytest.raises(StateError, match="already attempted"):
            resources.issue_read_token(ctx, selection=selection)
        assert sum(path.endswith("/token") for _, path, _ in calls) == 1


@pytest.mark.parametrize("change", ["expiry", "ca", "rbac", "scope"])
def test_active_reader_rejects_changed_identity_while_history_never_exposes_token(setup, monkeypatch, change):
    with invocation(setup) as (journal, _, ctx, _):
        resources, selection, state, calls = ready(journal, ctx, setup["tmp"], monkeypatch)
        value = resources.issue_read_token(ctx, selection=selection)
        scope = bind(monkeypatch, resources, value)
        if change == "expiry":

            class Future(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime.now(timezone.utc) + timedelta(hours=1)

            monkeypatch.setattr(r, "datetime", Future)
            assert r.require_runtime_reader_history(value, **scope)["role"] == "runtime"
        elif change == "ca":
            resources.session.ca_file.write_bytes(b"replaced")
        elif change == "rbac":
            resources._resources[1]["manifest"]["rules"][0]["verbs"].append("update")
        else:
            scope["bootstrap_authority"] = object()
        with pytest.raises(StateError):
            r.private_token(value, **scope)


def test_selection_must_match_original_bootstrap_and_same_uid_rbac_readback(setup, monkeypatch):
    with invocation(setup) as (journal, _, ctx, _):
        resources, selection, state, calls = ready(journal, ctx, setup["tmp"], monkeypatch)
        changed = deepcopy(selection)
        changed["audience"] = "https://unapproved.example"
        with pytest.raises(StateError, match="not frozen"):
            resources.issue_read_token(ctx, selection=changed)
        state["/apis/rbac.authorization.k8s.io/v1/namespaces/mission-demo/roles/runtime-role"]["rules"][0][
            "verbs"
        ].append("delete")
        with pytest.raises(StateError, match="same UID"):
            resources.issue_read_token(ctx, selection=selection)
        assert not any(path.endswith("/token") for _, path, _ in calls)


@pytest.mark.parametrize("subject", ["User", "Group", "ServiceAccount"])
def test_unowned_effective_write_grant_is_rejected_before_token(setup, monkeypatch, subject):
    with invocation(setup) as (journal, _, ctx, _):
        resources, selection, state, calls = ready(journal, ctx, setup["tmp"], monkeypatch)
        principal = {
            "kind": subject,
            "name": (
                "system:serviceaccount:mission-demo:runtime-reader"
                if subject == "User"
                else "system:serviceaccounts:mission-demo"
                if subject == "Group"
                else "runtime-reader"
            ),
        }
        if subject == "ServiceAccount":
            principal["namespace"] = "mission-demo"
        binding = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": "external-broad", "uid": "external-binding"},
            "subjects": [principal],
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "external-role",
            },
        }
        state["/apis/rbac.authorization.k8s.io/v1/clusterrolebindings"]["items"].append(binding)
        state["/apis/rbac.authorization.k8s.io/v1/clusterroles/external-role"] = {
            "kind": "ClusterRole",
            "metadata": {"name": "external-role", "uid": "external-role"},
            "rules": [{"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["update"]}],
        }
        with pytest.raises(StateError, match="Unselected effective"):
            resources.issue_read_token(ctx, selection=selection)
        assert not any(path.endswith("/token") for _, path, _ in calls)


def test_incomplete_global_rbac_inventory_cannot_issue_denied_token(setup, monkeypatch):
    with invocation(setup) as (journal, _, ctx, _):
        resources, selection, state, calls = ready(journal, ctx, setup["tmp"], monkeypatch, role="denied")
        state["/apis/rbac.authorization.k8s.io/v1/rolebindings"]["metadata"] = {"continue": "more"}
        with pytest.raises(StateError, match="Complete bounded"):
            resources.issue_read_token(ctx, selection=selection)
        assert not any(path.endswith("/token") for _, path, _ in calls)


def test_canonical_events_and_endpoint_slice_reads_are_explicitly_supported(setup, monkeypatch):
    with invocation(setup) as (journal, _, ctx, _):
        session, state, calls = api(monkeypatch)
        docs, selection = materials()
        docs[1]["rules"] = [
            {"apiGroups": [""], "resources": ["events"], "verbs": ["get", "list"]},
            {"apiGroups": ["discovery.k8s.io"], "resources": ["endpointslices"], "verbs": ["get", "list"]},
        ]
        driver = OwnedLinuxDriver(journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0")
        resources = k.OwnedKubernetesResources(
            driver,
            session=session,
            cluster_uid="cluster-uid",
            namespaces=("mission-demo",),
            manifest_sha256=tuple(_sha(k._bytes(doc)) for doc in docs),
            reader_selections=(selection,),
        )
        for doc in docs:
            resources.create(ctx, doc)
        # Stop at the subsequent global inventory boundary: validates actual role
        # rules without issuing a token through this focused transport fixture.
        monkeypatch.setattr(
            resources,
            "_reader_rbac_inventory",
            lambda *a, **kw: (_ for _ in ()).throw(StateError("inventory boundary")),
        )
        with pytest.raises(StateError, match="inventory boundary"):
            resources.issue_read_token(ctx, selection=selection)


def test_only_fixed_standard_discovery_baseline_is_accepted(setup, monkeypatch):
    with invocation(setup) as (journal, _, ctx, _):
        resources, selection, state, calls = ready(journal, ctx, setup["tmp"], monkeypatch, role="denied")
        state["/apis/rbac.authorization.k8s.io/v1/clusterrolebindings"]["items"] = [
            {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRoleBinding",
                "metadata": {"name": "system:discovery", "uid": "system-discovery-binding"},
                "subjects": [{"kind": "Group", "name": "system:authenticated"}],
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "ClusterRole",
                    "name": "system:discovery",
                },
            }
        ]
        state["/apis/rbac.authorization.k8s.io/v1/clusterroles/system:discovery"] = {
            "kind": "ClusterRole",
            "metadata": {"name": "system:discovery", "uid": "system-discovery-role"},
            "rules": [{"nonResourceURLs": ["/api", "/apis", "/version"], "verbs": ["get"]}],
        }
        value = resources.issue_read_token(ctx, selection=selection)
        scope = bind(monkeypatch, resources, value)
        assert r.require_runtime_reader(value, **scope)["selected_resource_grants"] == []
