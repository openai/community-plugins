"""Real chart resource shapes through controlled original-owner inventories."""

from copy import deepcopy
import pytest

from devsecops.factory import controller_cleanup as c
from devsecops.factory import kubernetes_operations as k
from devsecops.factory.state import StateError
from devsecops.factory.owned_linux import OwnedLinuxDriver
from devsecops.factory.platform_bootstrap import OwnedPlatformBootstrap
from tests.test_kubernetes_operations import api, component
from tests.test_controller_cleanup import controller, namespace, populated, release_secret, selected_inventory
from tests.test_owned_foundation import invocation
from tests import test_owned_foundation as fixture

setup = fixture.setup


@pytest.mark.parametrize("established", [True, False])
def test_additional_crds_must_be_established_before_helm_provisioning(setup, monkeypatch, established):
    session, state, calls = api(monkeypatch)
    original_request = k.KubernetesSession.request

    def response(self, method, path, **kwargs):
        result = original_request(self, method, path, **kwargs)
        if result and result.get("kind") == "CustomResourceDefinition":
            result["metadata"]["uid"] = "uid-selected-crd"
            if method == "POST":
                state[k._route(result)[1]]["metadata"]["uid"] = "uid-selected-crd"
        return result

    monkeypatch.setattr(k.KubernetesSession, "request", response)
    namespace_doc, release = namespace(), controller()
    definition = {
        "apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
        "metadata": {"name": "prometheuses.monitoring.coreos.com"},
        "spec": {"group": "monitoring.coreos.com", "scope": "Namespaced",
                 "names": {"kind": "Prometheus", "plural": "prometheuses"},
                 "versions": [{"name": "v1", "served": True, "storage": True}]},
    }
    observed = []

    class ReleaseReadyReached(Exception):
        pass

    with invocation(setup) as (journal, _, ctx, _):
        driver = OwnedLinuxDriver(journal, plugin_id="devsecops@devsecops-local", plugin_version="0.1.0")
        resources = k.OwnedKubernetesResources(
            driver, session=session, cluster_uid="cluster-uid", namespaces=("mission-demo",),
            manifest_sha256=tuple(c._sha(k._bytes(x)) for x in (namespace_doc, release, definition)),
            cluster_resources=((definition["apiVersion"], definition["kind"], definition["metadata"]["name"]),),
        )
        platform = object.__new__(OwnedPlatformBootstrap)
        platform.driver, platform.journal = driver, journal
        platform._owned_inputs, platform._observation = None, None
        platform.recipe = {"controller_cleanup": c.POLICY,
                           "readiness": {"flux_deployments": [],
                                         "helm_releases": [{"namespace": "mission-demo", "name": "mission"}],
                                         "argo_application": {"namespace": "mission-demo", "name": "mission"}}}
        monkeypatch.setattr(platform, "_admit", lambda context: None)
        monkeypatch.setattr(platform, "_audit_compiled", lambda: None)
        monkeypatch.setattr(platform, "prepare", lambda context: ((namespace_doc,), (release,)))

        def wait(context, original, document, condition):
            assert original is resources
            if condition == "Established":
                actual = resources.read_owned(context, document)
                assert actual["metadata"]["name"] == definition["metadata"]["name"]
                observed.append(condition)
                if not established:
                    raise StateError("Selected CRD did not become Established")
                return actual
            assert condition == "Ready"
            assert observed == ["Established"], "Helm provisioning reached before CRD establishment"
            raise ReleaseReadyReached

        monkeypatch.setattr(platform, "_wait", wait)
        expected = ReleaseReadyReached if established else StateError
        with pytest.raises(expected, match=None if established else "Selected CRD did not become Established"):
            platform.install(ctx, resources, additional_manifests=(definition,),
                             expected_git_url="https://gitlab.example.test/root/deploy.git",
                             expected_git_revision="b" * 40)
        posted = [payload["kind"] for method, path, payload in calls if method == "POST"]
        assert posted == ["Namespace", "CustomResourceDefinition"] + (["HelmRelease"] if established else [])

REGISTERED = [
    ("source.toolkit.fluxcd.io", "v1", "GitRepository", "gitrepositories"),
    ("source.toolkit.fluxcd.io", "v1", "HelmRepository", "helmrepositories"),
    ("kustomize.toolkit.fluxcd.io", "v1", "Kustomization", "kustomizations"),
    ("helm.toolkit.fluxcd.io", "v2", "HelmRelease", "helmreleases"),
    ("argoproj.io", "v1alpha1", "Application", "applications"),
]
STANDARD = [
    ("admissionregistration.k8s.io/v1", "ValidatingWebhookConfiguration", "validatingwebhookconfigurations", False),
    ("admissionregistration.k8s.io/v1", "MutatingWebhookConfiguration", "mutatingwebhookconfigurations", False),
    ("autoscaling/v2", "HorizontalPodAutoscaler", "horizontalpodautoscalers", True),
    ("policy/v1", "PodDisruptionBudget", "poddisruptionbudgets", True),
    ("v1", "ResourceQuota", "resourcequotas", True),
]


def crd(group, version, kind, plural):
    return {"apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
            "metadata": {"name": plural + "." + group},
            "spec": {"group": group, "scope": "Namespaced", "names": {"kind": kind, "plural": plural},
                     "versions": [{"name": version, "served": True, "storage": True,
                                   "schema": {"openAPIV3Schema": {"type": "object"}}}]}}


@pytest.mark.parametrize("selection", REGISTERED)
def test_exact_installed_flux_and_argo_crds_reuse_registered_routes(setup, monkeypatch, selection):
    session, _, calls = api(monkeypatch)
    group, version, kind, _ = selection
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        inventory = selected_inventory(resource, ctx, (namespace(), controller(), crd(*selection)), policy=c.POLICY)
        descriptor = {"apiVersion": group + "/" + version, "kind": kind,
                      "metadata": {"name": "selected", "namespace": "mission-demo"}}
        assert inventory._route(descriptor) == k._route(descriptor)
        assert all(method == "GET" for method, _, _ in calls)


@pytest.mark.parametrize("change", ["plural", "cluster-scope", "builtin-shadow"])
def test_crd_cannot_change_a_registered_api_route(setup, monkeypatch, change):
    session, _, calls = api(monkeypatch)
    row = crd(*REGISTERED[0])
    if change == "plural":
        row["spec"]["names"]["plural"] = "otherobjects"
    elif change == "cluster-scope":
        row["spec"]["scope"] = "Cluster"
    else:
        row = crd("apps", "v1", "Deployment", "deployments")
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        with pytest.raises(StateError, match="route|scope"):
            selected_inventory(resource, ctx, (namespace(), controller(), row), policy=c.POLICY)
        assert all(method == "GET" and path == "/api/v1/namespaces/kube-system" for method, path, _ in calls)


def standard_child(selection):
    version, kind, plural, namespaced = selection
    row = {"apiVersion": version, "kind": kind, "metadata": {"name": "platform-child"}}
    if namespaced:
        row["metadata"]["namespace"] = "mission-demo"
    if kind.endswith("WebhookConfiguration"):
        row["webhooks"] = [{"name": "validation.istio.io", "failurePolicy": "Ignore",
                            "admissionReviewVersions": ["v1"], "sideEffects": "None",
                            "clientConfig": {"service": {"name": "istiod", "namespace": "mission-demo", "path": "/validate"}}}]
    elif kind == "HorizontalPodAutoscaler":
        row["spec"] = {"scaleTargetRef": {"apiVersion": "apps/v1", "kind": "Deployment", "name": "mission"},
                       "minReplicas": 1, "maxReplicas": 2}
    elif kind == "ResourceQuota":
        row["spec"] = {"hard": {"pods": "1000"}, "scopeSelector": {"matchExpressions": [
            {"scopeName": "PriorityClass", "operator": "In", "values": ["system-cluster-critical"]}]}}
    else:
        row["spec"] = {"minAvailable": 1, "selector": {"matchLabels": {"app": "mission"}}}
    path = ("/api/v1" if version == "v1" else "/apis/" + version) + ("/namespaces/mission-demo" if namespaced else "") + "/" + plural + "/platform-child"
    return row, path


def child_inventory(resource, state, ctx, selection, *, select_cluster=True, mutate=None):
    desired, path = standard_child(selection)
    if not selection[3] and select_cluster:
        resource.cluster_resources = ((selection[0], selection[1], desired["metadata"]["name"]),)
    root = controller()
    inventory = selected_inventory(resource, ctx, (namespace(), root), policy=c.POLICY)
    _, old = populated(resource, state, ctx, root)
    prior = state.pop(old)
    actual = deepcopy(desired)
    actual["metadata"].update({key: prior["metadata"][key] for key in ("uid", "resourceVersion", "labels", "annotations")})
    if mutate:
        mutate(actual)
    state[path] = actual
    secret = release_secret([desired])
    state[k._route(secret)[1]] = secret
    return inventory, path


@pytest.mark.parametrize("selection", STANDARD)
def test_standard_platform_inventory_proves_absence_without_deleting_children(setup, monkeypatch, selection):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        inventory, path = child_inventory(resource, state, ctx, selection)
        inventory.capture(ctx)
        assert path in inventory._children
        inventory.start_cleanup(ctx, reconcile=False)
        assert not inventory.children_absent(ctx)
        for child in list(inventory._children):
            del state[child]
        assert inventory.children_absent(ctx)
        assert all(method == "GET" for method, _, _ in calls)


@pytest.mark.parametrize("selection", STANDARD[:2])
def test_unselected_admission_configuration_is_never_adopted(setup, monkeypatch, selection):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        inventory, path = child_inventory(resource, state, ctx, selection, select_cluster=False)
        with pytest.raises(StateError, match="absence proof"):
            inventory.capture(ctx)
        assert not any(route == path for _, route, _ in calls)


@pytest.mark.parametrize("selection", STANDARD[:2])
def test_preexisting_admission_configuration_stops_before_provisioning(setup, monkeypatch, selection):
    session, state, calls = api(monkeypatch)
    row, path = standard_child(selection)
    state[path] = row
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        resource.cluster_resources = ((selection[0], selection[1], row["metadata"]["name"]),)
        with pytest.raises(StateError, match="already exists"):
            selected_inventory(resource, ctx, (namespace(), controller()), policy=c.POLICY)
        assert all(method == "GET" for method, _, _ in calls)


def test_istio_validation_policy_can_strengthen_after_controller_startup(setup, monkeypatch):
    session, state, calls = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        inventory, path = child_inventory(resource, state, ctx, STANDARD[0],
            mutate=lambda row: row["webhooks"][0].update(failurePolicy="Fail"))
        inventory.capture(ctx)
        assert path in inventory._children
        assert inventory._children[path]["controller_mutations"] == [
            {"path": "/webhooks/0/failurePolicy", "before": "Ignore", "after": "Fail"}]
        assert all(method == "GET" for method, _, _ in calls)


def test_validation_policy_must_never_weaken(setup, monkeypatch):
    session, state, _ = api(monkeypatch)
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        inventory, _ = child_inventory(resource, state, ctx, STANDARD[0])
        selected, _ = standard_child(STANDARD[0])
        selected["webhooks"][0]["failurePolicy"] = "Fail"
        secret = release_secret([selected])
        state[k._route(secret)[1]] = secret
        with pytest.raises(StateError, match="content differs"):
            inventory.capture(ctx)


@pytest.mark.parametrize("change", ["callback", "namespace", "review-version", "mutating-policy"])
def test_webhook_inventory_still_rejects_unselected_controller_changes(setup, monkeypatch, change):
    session, state, _ = api(monkeypatch)
    def mutate(row):
        hook = row["webhooks"][0]
        if change == "callback":
            hook["clientConfig"]["service"]["name"] = "foreign"
        elif change == "namespace":
            hook["clientConfig"]["service"]["namespace"] = "foreign"
        elif change == "review-version":
            hook["admissionReviewVersions"] = ["v1beta1"]
        else:
            hook["failurePolicy"] = "Fail"
    with invocation(setup) as (journal, _, ctx, _):
        resource = component(journal, session)
        inventory, _ = child_inventory(resource, state, ctx, STANDARD[1] if change == "mutating-policy" else STANDARD[0], mutate=mutate)
        with pytest.raises(StateError, match="content differs"):
            inventory.capture(ctx)
