"""Pinned offline compilation and original readiness guards, controlled tools."""

import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace
import pytest
from devsecops.factory import platform_bootstrap as p
from devsecops.factory.state import StateError, _sha
from tests.test_canonical_bootstrap import selected


def fixture(tmp_path, monkeypatch, *, template="kind: ConfigMap\n", helm_bytes=b"controlled-helm"):
    selection = selected(tmp_path)["platform"]
    stream = io.BytesIO()
    chart = b"apiVersion: v2\nname: bigbang\nversion: 1.2.3\n"
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, raw in [
            ("bigbang/Chart.yaml", chart),
            ("bigbang/templates/example.yaml", template.encode()),
        ]:
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    raw = stream.getvalue()
    Path(selection["chart"]["path"]).write_bytes(raw)
    selection["chart"]["sha256"] = _sha(raw)
    selection["chart_metadata_sha256"] = _sha(chart)
    Path(selection["values"]["path"]).write_bytes(b"{}")
    selection["values"]["sha256"] = _sha(b"{}")
    Path(selection["helm"]["path"]).write_bytes(helm_bytes)
    selection["helm"]["sha256"] = _sha(helm_bytes)
    flux = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"namespace": "flux-system", "name": "source-controller"},
        "spec": {
            "template": {"spec": {"containers": [{"name": "manager", "image": selection["images"][0]}]}}
        },
    }
    raw = json.dumps(flux).encode()
    Path(selection["flux_manifest"]["path"]).write_bytes(raw)
    selection["flux_manifest"]["sha256"] = _sha(raw)
    rendered = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {"namespace": "bigbang", "name": "bigbang"},
        "spec": {},
    }
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert args[1:3] == ["template", "bigbang"] and "--include-crds" in args
        assert "--dependency-update" not in args and "HELM_PLUGINS" in kwargs["env"]
        return 0, json.dumps(rendered).encode(), b"", {"exit_code": 0}

    monkeypatch.setattr(p, "run", run)
    monkeypatch.setattr(
        p, "tool_identity", lambda *a, **kw: {"version": "v1.2.3", "sha256": selection["helm"]["sha256"]}
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    journal = SimpleNamespace(run_dir=run_dir, identities={"package_sha256": "a" * 64})
    driver = SimpleNamespace(journal=journal, _admit=lambda *a: None, _package=lambda *a: None)
    context = SimpleNamespace(
        deadline=__import__("time").monotonic() + 30,
        inputs={
            "lock": {
                "platform": {
                    "flux_version": "1.2.3",
                    "bigbang_version": "1.2.3",
                    "kubernetes_version": "1.36.5",
                },
                "tools": [{"name": "helm", "sha256": selection["helm"]["sha256"], "version": "1.2.3"}],
            },
            "config": {
                "tool_paths": {"helm": selection["helm"]["path"]},
                "services": [{"id": "kubernetes", "scope": {"namespaces": ["flux-system", "bigbang"]}}],
            },
        },
    )
    return p.OwnedPlatformBootstrap(driver, selection), context, selection, calls


def test_offline_compilation_cannot_be_changed_through_returned_dict(tmp_path, monkeypatch):
    producer, ctx, selection, calls = fixture(tmp_path, monkeypatch)
    compiled = producer.prepare(ctx)
    compiled[0][0]["spec"]["template"]["spec"]["containers"][0]["image"] = "injected"
    assert (
        producer.prepare(ctx)[0][0]["spec"]["template"]["spec"]["containers"][0]["image"]
        == selection["images"][0]
    )
    assert len(calls) == 1
    producer._compiled[0][0]["spec"]["template"]["spec"]["containers"][0]["image"] = "injected"
    with pytest.raises(StateError, match="in-memory"):
        producer.prepare(ctx)


def test_realistic_binary_larger_than_manifest_limit_is_supported(tmp_path, monkeypatch):
    producer, ctx, selection, calls = fixture(tmp_path, monkeypatch, helm_bytes=b"h" * 9000000)
    assert producer.prepare(ctx) and len(calls) == 1


def test_lookup_chart_is_rejected_before_helm(tmp_path, monkeypatch):
    producer, ctx, selection, calls = fixture(
        tmp_path, monkeypatch, template='{{ lookup "v1" "Secret" "" "" }}'
    )
    with pytest.raises(StateError, match="lookup"):
        producer.prepare(ctx)
    assert not calls


def test_pinned_material_change_stops_before_tool(tmp_path, monkeypatch):
    producer, ctx, selection, calls = fixture(tmp_path, monkeypatch)
    Path(selection["values"]["path"]).write_text("changed: true")
    with pytest.raises(StateError, match="material changed"):
        producer.prepare(ctx)
    assert not calls


def test_unselected_controller_image_is_not_compiled(tmp_path, monkeypatch):
    producer, ctx, selection, calls = fixture(tmp_path, monkeypatch)
    path = Path(selection["flux_manifest"]["path"])
    value = json.loads(path.read_bytes())
    value["spec"]["template"]["spec"]["containers"][0]["image"] = (
        "registry.example.test/other@sha256:" + "9" * 64
    )
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    producer.recipe["flux_manifest"]["sha256"] = _sha(raw)
    with pytest.raises(StateError, match="selection changed"):
        producer.prepare(ctx)


def test_frozen_compilation_file_change_is_detected(tmp_path, monkeypatch):
    producer, ctx, selection, calls = fixture(tmp_path, monkeypatch)
    producer.prepare(ctx)
    next(iter(producer._files)).write_text("{}")
    with pytest.raises(StateError, match="compilation bytes"):
        producer.prepare(ctx)


@pytest.mark.parametrize("namespace_owned", [True, False])
def test_argo_readiness_requires_original_created_destination_namespace(
    tmp_path, monkeypatch, namespace_owned
):
    producer, ctx, selection, calls = fixture(tmp_path, monkeypatch)
    producer._git_selection = ("https://gitlab.example.test/root/deploy.git", "a" * 40)
    app = {
        "metadata": {"uid": "app-uid"},
        "spec": {
            "source": {
                "repoURL": producer._git_selection[0],
                "targetRevision": "main",
                "path": "charts/mission",
            },
            "destination": {"server": "https://cluster.example.test", "namespace": "mission-demo"},
        },
        "status": {"sync": {"status": "Synced", "revision": "a" * 40}, "health": {"status": "Healthy"}},
    }
    observed = []

    def read(context, descriptor):
        observed.append(descriptor["kind"])
        if descriptor["kind"] == "Application":
            return app
        if not namespace_owned:
            raise StateError("Readiness requires original directly owned resource")
        return {"metadata": {"uid": "owned-namespace-uid"}}

    resources = SimpleNamespace(
        namespaces=("mission-demo",),
        session=SimpleNamespace(origin="https://cluster.example.test"),
        read_owned=read,
    )
    descriptor = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"namespace": "argocd", "name": "mission"},
    }
    if namespace_owned:
        assert producer._wait(ctx, resources, descriptor, "ArgoHealthy") == app
        assert producer._argo_namespace_uid == "owned-namespace-uid"
    else:
        with pytest.raises(StateError, match="original directly owned"):
            producer._wait(ctx, resources, descriptor, "ArgoHealthy")
    assert observed == ["Application", "Namespace"]


def test_bootstrap_application_admission_does_not_claim_first_image_health(tmp_path, monkeypatch):
    producer, ctx, selection, calls = fixture(tmp_path, monkeypatch)
    producer._git_selection = ('https://gitlab.example.test/root/deploy.git', 'a'*40)
    application = {'metadata':{'uid':'original-app'}, 'spec':{
        'source':{'repoURL':producer._git_selection[0], 'targetRevision':'main','path':'charts/mission'},
        'destination':{'server':'https://cluster.example.test','namespace':'mission-demo'}},
        'status':{'sync':{'status':'Unknown'},'health':{'status':'Missing'}}}
    reads = []
    def read(context, descriptor):
        reads.append(descriptor['kind'])
        return application if descriptor['kind']=='Application' else {'metadata':{'uid':'original-namespace'}}
    resources = SimpleNamespace(namespaces=('mission-demo',),
        session=SimpleNamespace(origin='https://cluster.example.test'),read_owned=read)
    descriptor={'apiVersion':'argoproj.io/v1alpha1','kind':'Application',
        'metadata':{'namespace':'argocd','name':'mission'}}
    assert producer._wait(ctx,resources,descriptor,'ArgoAdmitted') is application
    assert producer._argo_namespace_uid=='original-namespace'
    ctx.deadline=__import__('time').monotonic()+0.01
    with pytest.raises(StateError,match='readiness'):
        producer._wait(ctx,resources,descriptor,'ArgoHealthy')
    ctx.deadline=__import__('time').monotonic()+0.1
    application['spec']['source']['repoURL']='https://foreign.example.test/project.git'
    with pytest.raises(StateError,match='readiness'):
        producer._wait(ctx,resources,descriptor,'ArgoAdmitted')
