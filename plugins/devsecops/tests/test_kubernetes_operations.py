"""Real private journals and controlled API transport; not live qualification."""

from copy import deepcopy
import pytest
from devsecops.factory import kubernetes_operations as k
from devsecops.factory.owned_linux import OwnedLinuxDriver
from devsecops.factory.state import StateError, _sha
from tests.test_owned_foundation import invocation
from tests import test_owned_foundation as foundation_fixture

setup = foundation_fixture.setup


def api(monkeypatch, fail=False):
    session = object.__new__(k.KubernetesSession)
    session.origin = "https://cluster.example.test"
    state = {"/api/v1/namespaces/kube-system": {"metadata": {"uid": "cluster-uid"}}}
    calls = []

    def request(self, method, path, *, payload=None, deadline):
        calls.append((method, path, deepcopy(payload)))
        if method == "GET":
            return deepcopy(state.get(path))
        if method == "POST":
            if fail:
                raise k.KubernetesOperationError("lost-ack")
            value = deepcopy(payload)
            value["metadata"].update(uid="uid-" + value["metadata"]["name"], resourceVersion="1")
            state[path + "/" + value["metadata"]["name"]] = value
            return deepcopy(value)
        if method == "DELETE":
            row = state[path]
            assert payload["preconditions"] == {
                "uid": row["metadata"]["uid"],
                "resourceVersion": row["metadata"]["resourceVersion"],
            }
            del state[path]
            return {"kind": "Status", "status": "Success"}
        raise AssertionError(method)

    monkeypatch.setattr(k.KubernetesSession, "request", request)
    return session, state, calls


def manifest():
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "probe-config", "namespace": "mission-demo"},
        "immutable": True,
        "data": {"runner.py": "controlled-test"},
    }


def component(journal, session):
    driver = OwnedLinuxDriver(journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0")
    return k.OwnedKubernetesResources(
        driver,
        session=session,
        cluster_uid="cluster-uid",
        namespaces=("mission-demo",),
        manifest_sha256=(_sha(k._bytes(manifest())),),
    )


def test_create_intent_precedes_post_and_exact_uid_is_retained(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        original = k.KubernetesSession.request

        def check(self, method, path, **kwargs):
            if method == "POST":
                assert len(list((journal.run_dir / "kubernetes-resources").glob("*create-intent.json"))) == 1
            return original(self, method, path, **kwargs)

        monkeypatch.setattr(k.KubernetesSession, "request", check)
        actual = resource.create(ctx, manifest())
        assert (
            actual["uid"] == "uid-probe-config" and len(resource.owned(ctx)) == 1 and not resource._unresolved
        )
        with pytest.raises(TypeError):
            actual["uid"] = "replacement"
        with pytest.raises(StateError, match="already been attempted"):
            resource.create(ctx, manifest())


@pytest.mark.parametrize(
    "change", [lambda m: m.update(immutable=False), lambda m: m["data"].update(extra="unreviewed")]
)
def test_changed_manifest_rejected_before_post(setup, monkeypatch, change):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        changed = manifest()
        change(changed)
        with pytest.raises(StateError, match="selected manifest pin"):
            resource.create(ctx, changed)
        assert not any(method == "POST" for method, _, _ in calls)


def test_same_name_object_is_never_adopted(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    state["/api/v1/namespaces/mission-demo/configmaps/probe-config"] = manifest()
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        with pytest.raises(StateError, match="adoption is forbidden"):
            resource.create(ctx, manifest())
        assert resource._resources == [] and not any(method == "POST" for method, _, _ in calls)


def test_post_lost_ack_preserves_unknown_and_prevents_retry(setup, monkeypatch):
    session, state, calls = api(monkeypatch, True)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        with pytest.raises(k.KubernetesOperationError):
            resource.create(ctx, manifest())
        assert len(resource._unresolved) == 1
        with pytest.raises(StateError, match="already been attempted"):
            resource.create(ctx, manifest())
        assert sum(method == "POST" for method, _, _ in calls) == 1


def test_cluster_swap_stops_before_mutation(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    state["/api/v1/namespaces/kube-system"]["metadata"]["uid"] = "foreign"
    with invocation(setup) as (journal, _, ctx, _):
        with pytest.raises(StateError, match="cluster identity"):
            component(journal, session).create(ctx, manifest())
        assert [row[0] for row in calls] == ["GET"]


@pytest.mark.parametrize("replacement", [False, True])
def test_cleanup_uses_exact_uid_and_resource_version_and_audits_artifact(setup, monkeypatch, replacement):
    session, state, calls = api(monkeypatch)
    with invocation(setup, "down") as (journal, _, ctx, operation):
        resource = component(journal, session)
        desired = manifest()
        desired["metadata"]["labels"] = {k.LABEL: ctx.run_id, k.BINDING: resource._binding}
        path = "/api/v1/namespaces/mission-demo/configmaps/probe-config"
        state[path] = {
            **desired,
            "metadata": {
                **desired["metadata"],
                "uid": "replacement" if replacement else "original",
                "resourceVersion": "19",
            },
        }
        resource._resources.append(
            {
                "path": path,
                "uid": "original",
                "resourceVersion": "19",
                "manifest": desired,
                "operation_id": "create",
                "state": "present",
                "disposition": "remove",
                "identity": {
                    "kind": "ConfigMap",
                    "namespace": "mission-demo",
                    "name": "probe-config",
                    "uid": "original",
                },
            }
        )
        if replacement:
            with pytest.raises(StateError, match="ownership identity changed"):
                resource.down(ctx)
            assert not any(method == "DELETE" for method, _, _ in calls)
        else:
            result = resource.down(ctx)
            observed = resource.cleanup_observation(result)
            assert observed["complete_cleanup"] and observed["resources"][0]["state"] == "absent"
            result.artifacts[0].write_text("{}")
            with pytest.raises(StateError, match="artifact changed"):
                resource.cleanup_observation(result)


def test_same_uid_content_mutation_cannot_pass_readiness(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        resource.create(ctx, manifest())
        state["/api/v1/namespaces/mission-demo/configmaps/probe-config"]["data"]["runner.py"] = (
            "changed-same-uid"
        )
        with pytest.raises(StateError, match="same UID"):
            resource.read_owned(ctx, manifest())


def test_original_public_ledger_bundle_preserves_every_byte_and_rejects_tampering(setup, monkeypatch):
    import base64
    import json

    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        for index in range(101):
            resource._record("controlled", {"operation_id": "controlled", "index": index})
        artifacts = resource.evidence_artifacts(ctx)
        assert len(artifacts) == 1
        doc = json.loads(artifacts[0].read_bytes())
        assert len(doc["entries"]) == 101
        for row in doc["entries"]:
            raw = base64.b64decode(row["content_base64"])
            assert _sha(raw) == row["sha256"] and len(raw) == row["bytes"]
            assert (journal.run_dir / row["path"]).read_bytes() == raw
        next(iter(resource._evidence)).write_bytes(b"changed")
        with pytest.raises(StateError, match="public evidence"):
            resource.evidence_artifacts(ctx)


@pytest.mark.parametrize("change", ["none", "uid", "content", "metadata"])
def test_private_read_ownership_audit_has_no_administrative_io(setup, monkeypatch, change):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        resource.create(ctx, manifest())
        observed = deepcopy(state["/api/v1/namespaces/mission-demo/configmaps/probe-config"])
        if change == "uid":
            observed["metadata"]["uid"] = "replacement"
        elif change == "content":
            observed["data"]["runner.py"] = "changed"
        elif change == "metadata":
            observed["metadata"]["labels"][k.LABEL] = "other-run"
        previous = len(calls)
        if change == "none":
            assert resource.audit_owned_read(ctx, manifest(), observed) == observed
        else:
            with pytest.raises(StateError):
                resource.audit_owned_read(ctx, manifest(), observed)
        assert len(calls) == previous


@pytest.mark.parametrize(
    "finalizers",
    [
        None,
        ["resources-finalizer.argocd.argoproj.io/background"],
        ["unapproved"],
        [k.ARGO_FOREGROUND_FINALIZER],
    ],
)
def test_application_accepts_only_exact_foreground_cleanup_finalizer(setup, monkeypatch, finalizers):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        doc = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {"namespace": "mission-demo", "name": "application"},
            "spec": {},
        }
        if finalizers is not None:
            doc["metadata"]["finalizers"] = finalizers
        resource.manifest_sha256 = (_sha(k._bytes(doc)),)
        if finalizers != [k.ARGO_FOREGROUND_FINALIZER]:
            with pytest.raises(StateError, match="foreground"):
                resource.create(ctx, doc)
            assert not any(method == "POST" for method, _, _ in calls)
        else:
            resource.create(ctx, doc)
            path = "/apis/argoproj.io/v1alpha1/namespaces/mission-demo/applications/application"
            state[path]["metadata"]["finalizers"] = []
            with pytest.raises(StateError, match="finalizer changed"):
                resource.read_owned(ctx, doc)


@pytest.mark.parametrize("finalizer_preserved", [True, False])
def test_cleanup_requires_original_foreground_finalizer_before_application_delete(
    setup, monkeypatch, finalizer_preserved
):
    from dataclasses import replace
    from tests.test_controller_cleanup import controller, namespace, selected_inventory, populated
    from devsecops.factory import controller_cleanup as c

    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, operation):
        resource = component(journal, session)
        selected = controller("Application")
        inventory = selected_inventory(resource, ctx, (namespace(), selected), policy=c.POLICY)
        parent, child = populated(resource, state, ctx, selected)
        inventory.capture(ctx)
        if not finalizer_preserved:
            state[parent]["metadata"]["finalizers"] = []
        original = k.KubernetesSession.request

        def observe(self, method, path, **kwargs):
            if method == "DELETE" and path == parent:
                state.pop(child, None)
            return original(self, method, path, **kwargs)

        monkeypatch.setattr(k.KubernetesSession, "request", observe)
        monkeypatch.setattr(resource, "_admit", lambda *a, **kw: operation)
        down = replace(ctx, phase={"id": "down", "operation": "down"})
        if finalizer_preserved:
            outcome = resource.down(down)
            assert resource.cleanup_observation(outcome)["complete_cleanup"]
            deletion = next(payload for method, _, payload in calls if method == "DELETE")
            assert deletion["propagationPolicy"] == "Foreground"
        else:
            with pytest.raises(StateError, match="finalizer"):
                resource.down(down)
            assert not any(method == "DELETE" for method, _, _ in calls)
