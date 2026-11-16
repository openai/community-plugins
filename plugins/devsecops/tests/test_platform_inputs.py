"""Original derivation and secret creation, with controlled GitLab origin seam."""

import base64
from copy import deepcopy
from types import SimpleNamespace, ModuleType
import sys
import pytest
from devsecops.factory import platform_inputs as i, platform_bootstrap as p, kubernetes_operations as k
from devsecops.factory.state import StateError, _sha
from devsecops.factory.owned_linux import OwnedLinuxDriver
from tests.test_kubernetes_operations import api
from tests.test_owned_foundation import invocation
from tests import test_owned_foundation as foundation_fixture

setup = foundation_fixture.setup
CANARY = "SYNTHETIC_PRIVATE_READER_CANARY"


def templates():
    namespace = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "mission-demo"}}
    source = {
        "apiVersion": "source.toolkit.fluxcd.io/v1",
        "kind": "GitRepository",
        "metadata": {"namespace": "mission-demo", "name": "owned-deployment"},
        "spec": {"url": i.URL, "ref": {"commit": i.COMMIT}, "secretRef": {"name": "owned-flux-reader"}},
    }
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"namespace": "mission-demo", "name": "owned-flux-reader"},
        "type": "Opaque",
        "stringData": {"username": i.USERNAME, "password": i.PASSWORD, "ca.crt": i.CA},
    }
    argo = deepcopy(secret)
    argo["metadata"].update(name="owned-argo-reader", labels={"argocd.argoproj.io/secret-type": "repository"})
    argo["stringData"].pop("ca.crt")
    argo["stringData"].update(type="git", url=i.URL)
    return namespace, source, secret, argo


def producer(journal, ctx, monkeypatch):
    value = object.__new__(p.OwnedPlatformBootstrap)
    value.journal = journal
    value.recipe = {"controller_cleanup": "owned-controller-inventory-v1"}
    monkeypatch.setattr(value, "_admit", lambda context: None)
    ca = journal.run_dir / "selected-ca.pem"
    ca.write_bytes(b"controlled-public-ca")
    owner = SimpleNamespace(
        platform=value,
        _kubernetes=None,
        _recipe=lambda: {
            "services": {"materials": {"ca": {"path": str(ca), "sha256": _sha(ca.read_bytes())}}}
        },
    )
    authority = SimpleNamespace(_owner=owner)
    module = ModuleType("devsecops.factory.canonical_bootstrap")
    facts = {
        "origin": "https://gitlab.example.test",
        "projects": {"deployment": {"id": 37, "path_with_namespace": "root/deploy", "seed_commit": "b" * 40}},
    }
    module.require_bootstrap_authority = lambda a, j: facts
    monkeypatch.setitem(sys.modules, "devsecops.factory.canonical_bootstrap", module)
    reader = object()
    module = ModuleType("devsecops.factory.owned_reader_identity")
    reader_facts = {
        "project_id": 37,
        "path_with_namespace": "root/deploy",
        "username": "owned-reader",
        "user_id": 17,
        "token_id": 91,
    }
    module.require_owned_reader_origin = lambda value, **scope: dict(reader_facts)
    module._reader_secret = lambda value, **scope: CANARY
    monkeypatch.setitem(sys.modules, "devsecops.factory.owned_reader_identity", module)
    return value, authority, reader


def test_private_source_derivation_uses_actual_seed_and_only_read_principal(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        platform, authority, reader = producer(journal, ctx, monkeypatch)
        namespace, source, secret, argo = templates()
        inputs = i.prepare_owned_inputs(
            platform,
            ctx,
            bootstrap_authority=authority,
            reader_origin=reader,
            groups=((namespace, source), ()),
            additional=(secret, argo),
        )
        record = i.require_owned_inputs(inputs, ctx, platform=platform)
        actual = record["groups"][0][1]
        assert actual["spec"]["url"] == "https://gitlab.example.test/root/deploy.git"
        assert actual["spec"]["ref"] == {"commit": "b" * 40}
        assert source["spec"]["url"] == i.URL
        driver = OwnedLinuxDriver(journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0")
        hashes = tuple(_sha(k._bytes(row)) for group in record["groups"] for row in group)
        private = tuple(
            _sha(k._bytes(row)) for group in record["groups"] for row in group if row["kind"] == "Secret"
        )
        resources = k.OwnedKubernetesResources(
            driver,
            session=session,
            cluster_uid="cluster-uid",
            namespaces=("mission-demo",),
            manifest_sha256=hashes,
            private_secret_sha256=private,
        )
        authority._owner._kubernetes = resources
        resources.create(ctx, namespace)
        for doc in (secret, argo):
            descriptor = {key: deepcopy(doc[key]) for key in ("apiVersion", "kind", "metadata")}
            resources.create_platform_secret(ctx, inputs=inputs, descriptor=descriptor)
        saved = state["/api/v1/namespaces/mission-demo/secrets/owned-flux-reader"]
        assert base64.b64decode(saved["data"]["password"]).decode() == CANARY
        evidence = i.public_derivation(inputs, ctx, platform=platform)
        encoded = base64.b64encode(CANARY.encode())
        assert CANARY.encode() not in k._bytes(evidence) and encoded not in k._bytes(evidence)
        assert all(
            CANARY.encode() not in path.read_bytes() and encoded not in path.read_bytes()
            for path in (journal.run_dir / "kubernetes-resources").iterdir()
        )
        with pytest.raises(StateError, match="private input capability"):
            resources.create(ctx, record["groups"][-1][0])


@pytest.mark.parametrize("change", ["role", "token", "url", "namespace"])
def test_unapproved_secret_template_or_namespace_is_rejected(setup, monkeypatch, change):
    with invocation(setup) as (journal, _, ctx, _):
        platform, authority, reader = producer(journal, ctx, monkeypatch)
        namespace, source, secret, argo = templates()
        if change == "role":
            argo["metadata"]["labels"]["argocd.argoproj.io/secret-type"] = "repo-creds"
        elif change == "token":
            argo["stringData"]["password"] = "operator-root-token"
        elif change == "url":
            argo["stringData"]["url"] = "https://unapproved.example/foreign.git"
        else:
            namespace["metadata"]["name"] = "different"
        with pytest.raises(StateError):
            i.prepare_owned_inputs(
                platform,
                ctx,
                bootstrap_authority=authority,
                reader_origin=reader,
                groups=((namespace, source), ()),
                additional=(secret, argo),
            )


def test_compile_redaction_does_not_persist_selected_secret_values():
    _, _, secret, _ = templates()
    secret["stringData"]["password"] = CANARY
    redacted = i.redact(secret)
    assert CANARY not in str(redacted) and secret["stringData"]["password"] == CANARY


def test_platform_install_orders_original_namespace_then_private_secret_then_flux_source(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        platform, authority, reader = producer(journal, ctx, monkeypatch)
        namespace, source, secret, argo_secret = templates()
        app = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {
                "namespace": "mission-demo",
                "name": "mission",
                "finalizers": [k.ARGO_FOREGROUND_FINALIZER],
            },
            "spec": {
                "source": {"repoURL": i.URL, "targetRevision": "main", "path": "charts/mission"},
                "destination": {"server": session.origin, "namespace": "mission-demo"},
            },
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
            "data": {i.HOST: i.CA},
        }
        inputs = i.prepare_owned_inputs(
            platform,
            ctx,
            bootstrap_authority=authority,
            reader_origin=reader,
            groups=((namespace,), (source,)),
            additional=(secret, argo_secret, trust, app),
        )
        record = i.require_owned_inputs(inputs, ctx, platform=platform)
        docs = [row for group in record["groups"] for row in group]
        driver = OwnedLinuxDriver(journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0")
        resources = k.OwnedKubernetesResources(
            driver,
            session=session,
            cluster_uid="cluster-uid",
            namespaces=("mission-demo",),
            manifest_sha256=tuple(_sha(k._bytes(d)) for d in docs),
            private_secret_sha256=tuple(_sha(k._bytes(d)) for d in docs if d["kind"] == "Secret"),
        )
        authority._owner._kubernetes = resources
        platform._owned_inputs = inputs
        platform._observation = None
        platform._argo_namespace_uid = "uid-mission-demo"
        platform.recipe = {
            "controller_cleanup": "owned-controller-inventory-v1",
            "flux_version": "1.2.3",
            "bigbang_version": "1.2.3",
            "readiness": {
                "flux_deployments": [],
                "helm_releases": [],
                "argo_application": {"namespace": "mission-demo", "name": "mission"},
            },
        }
        monkeypatch.setattr(platform, "_audit_compiled", lambda: None)
        original_request = k.KubernetesSession.request

        def inventory_response(self, method, path, **kwargs):
            result = original_request(self, method, path, **kwargs)
            if method == "GET" and result and result.get("kind") == "Application":
                result["status"] = {
                    "sync": {"status": "Unknown"},
                    "health": {"status": "Missing"},
                    "resources": [],
                }
            return result

        monkeypatch.setattr(k.KubernetesSession, "request", inventory_response)
        platform.install(
            ctx,
            resources,
            expected_git_url="https://gitlab.example.test/root/deploy.git",
            expected_git_revision="b" * 40,
        )
        assert platform._observation["workload_readiness"] == "pending-first-authenticated-delivery"
        assert platform._observation["argo_revision"] is None
        assert resources._controller_inventory.observation()["captured"] is False
        assert resources._controller_inventory.observation()["pending_application_inventory"]
        kinds = [payload["kind"] for method, path, payload in calls if method == "POST"]
        assert kinds == ["Namespace", "Secret", "Secret", "ConfigMap", "GitRepository", "Application"]
        actual_trust = state["/api/v1/namespaces/mission-demo/configmaps/argocd-tls-certs-cm"]
        assert actual_trust["data"] == {"gitlab.example.test": "controlled-public-ca"}
