"""Actual local Git/Helm/schema MR validation; package identity is controlled."""

from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest
import yaml

from devsecops.factory import deployment_validation as v
from devsecops.factory import deployment_proposal as p
from devsecops.factory.state import IDENTITY_KEYS, _sha
from devsecops.runtime.context import verify_seal
from tests.test_deployment_proposal import git, tools as selected_tools

from tests.test_canonical_bootstrap import selected

# Re-export the existing explicit local tool fixture.
tools = selected_tools

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def deployed(tmp_path, monkeypatch, tools):
    repository = tmp_path / "repository"
    repository.mkdir()
    shutil.copytree(p.CHART, repository / "chart")
    (repository / ".gitlab-ci.yml").write_bytes(v.RECIPE.read_bytes())
    values = repository / "environment.yaml"
    values.write_text(
        """environment: lab
architecture: amd64
image:
  repository: registry.example.test/mission
  digest: sha256:"""
        + "b" * 64
        + """
imagePullSecrets: []
networkPolicy:
  healthPeer:
    namespace: probes
    podLabels: {app: probe}
  metricsPeer:
    namespace: monitoring
    podLabels: {app: prometheus}
  metricsPort: 9090
"""
    )
    git(repository, "init", "--quiet", "--template=")
    git(repository, "add", "--all")
    git(repository, "commit", "--quiet", "-m", "Controlled MR checkout")
    revision = git(repository, "rev-parse", "HEAD")
    config = {
        "schema_version": 1,
        "kind": "mission-deployment-proposal-configuration",
        "deployment_repository": "/old/host/selection",
        "deployment_commit": "a" * 40,
        "chart_path": "chart",
        "values_path": "environment.yaml",
        "image_keys": deepcopy(p._KEYS),
        "release": "mission",
        "namespace": "mission",
        "kubernetes_version": "1.34.0",
        **tools,
    }
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(config))
    invocation = {
        "run_scope": {name: "c" * 64 for name in IDENTITY_KEYS},
        "timeout": 600,
        "plugin_id": "devsecops@devsecops-local",
        "plugin_version": "test",
        "configurations": {"proposal": {"path": str(path), "sha256": _sha(path.read_bytes())}},
    }
    monkeypatch.setattr(v, "load_invocation", lambda *args: deepcopy(invocation))
    package = []
    monkeypatch.setattr(v, "_installed", lambda *args: package.append(args))
    args = dict(
        invocation=tmp_path / "invocation.json",
        invocation_sha256="d" * 64,
        repository=repository,
        revision=revision,
    )
    return args, path, invocation, package


def test_exact_actual_mr_checkout_validates_without_publication(deployed):
    args, path, invocation, package = deployed
    before = path.read_bytes()
    report = v.validate_checkout(**args)
    assert report["decision"] == "pass" and report["delivery_authority"] == "not-issued"
    assert report["source_commit"] == args["revision"]
    assert report["configuration_sha256"] == _sha(before)
    assert len(package) == 2 and path.read_bytes() == before
    verify_seal(report, "validation_sha256")
    assert git(args["repository"], "status", "--porcelain") == ""


@pytest.mark.parametrize("change", ["chart", "pipeline", "values", "wrong-head", "tool-pin"])
def test_mr_gate_rejects_incompatible_exact_inputs(deployed, change):
    args, path, invocation, package = deployed
    repo = args["repository"]
    if change == "wrong-head":
        args["revision"] = "0" * 40
    elif change == "tool-pin":
        config = json.loads(path.read_bytes())
        config["helm_sha256"] = "0" * 64
        path.write_text(json.dumps(config))
        invocation["configurations"]["proposal"]["sha256"] = _sha(path.read_bytes())
    else:
        target = {
            "chart": "chart/templates/deployment.yaml",
            "pipeline": ".gitlab-ci.yml",
            "values": "environment.yaml",
        }[change]
        file = repo / target
        file.write_text(
            file.read_text() + "\n# changed candidate\n"
            if change != "values"
            else file.read_text().replace("architecture: amd64", "architecture: unsupported")
        )
        git(repo, "add", "--all")
        git(repo, "commit", "--quiet", "-m", "Controlled changed input")
        args["revision"] = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError):
        v.validate_checkout(**args)


def test_fixed_mr_recipe_uses_owned_runner_and_no_publication():
    recipe = yaml.safe_load(v.RECIPE.read_bytes())
    assert recipe["workflow"]["rules"] == [
        {"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'},
        {"when": "never"},
    ]
    job = recipe[v.JOB]
    assert job["tags"] == ["devsecops-owned"] and job["retry"] == 0
    assert job["rules"] == recipe["workflow"]["rules"]
    assert "validate_owned_deployment.py" in job["script"][0]
    assert "publish_" not in job["script"][0]
    assert job["artifacts"]["paths"] == ["deployment-validation.json"]


def test_each_owned_seed_requires_the_exact_installed_ci(tmp_path):
    from devsecops.factory.canonical_bootstrap import OwnedBootstrapCoordinator
    from devsecops.factory.state import StateError

    recipe = selected(tmp_path)
    producer = object.__new__(OwnedBootstrapCoordinator)
    for role, filename in [
        ("source", "gitlab-owned-api-v1.yml"),
        ("deployment", "gitlab-owned-deployment-v1.yml"),
    ]:
        path = ROOT / "demo/software-factory/ci" / filename
        recipe["seeds"][role].append(
            {
                "repository_path": ".gitlab-ci.yml",
                "file": {"path": str(path), "sha256": _sha(path.read_bytes())},
            }
        )
    assert len(producer._seeds(recipe)) == 2
    recipe["seeds"]["deployment"].pop()
    with pytest.raises(StateError, match="Each owned project"):
        producer._seeds(recipe)


@pytest.mark.parametrize('material',['helm_path','kubeconform_path','schemas_path'])
def test_derived_actual_checkout_cannot_supply_its_validation_tools(deployed, material):
    args,path,invocation,package=deployed
    config=json.loads(path.read_bytes())
    config[material]=str(args['repository']/'untrusted-material')
    path.write_text(json.dumps(config))
    invocation['configurations']['proposal']['sha256']=_sha(path.read_bytes())
    with pytest.raises(ValueError,match='outside the deployment checkout'):
        v.validate_checkout(**args)


@pytest.mark.parametrize('missing',[
    'src/devsecops/factory/deployment_validation.py',
    'scripts/validate_owned_deployment.py',
    'demo/software-factory/ci/gitlab-owned-deployment-v1.yml',
])
def test_portable_package_requires_the_complete_fixed_deployment_gate(missing):
    from scripts import build_plugin
    files=build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError,match='fixed deployment MR validator'):
        build_plugin.validate(files)


def test_owned_argo_template_cannot_pin_the_seed_forever():
    from devsecops.factory import platform_inputs as inputs
    from devsecops.factory.state import StateError
    app={'apiVersion':'argoproj.io/v1alpha1','kind':'Application',
        'spec':{'source':{'repoURL':inputs.URL,'targetRevision':inputs.COMMIT,'path':'chart'}}}
    with pytest.raises(StateError,match='main branch'):
        inputs._derive(app,url='https://owned.example.test/deploy.git',commit='a'*40,
            reader={},secret='unused',ca='unused')


@pytest.mark.parametrize('damage',['command','path','digest','missing'])
def test_runner_validator_selection_is_closed_and_explicit(tmp_path, damage):
    from devsecops.factory import bootstrap_recipe
    value=selected(tmp_path)
    validation=value['services']['runner_validation']
    if damage=='command':
        validation['command']=['sh','-c','unselected']
    elif damage=='path':
        validation['helm']['path']='/usr/bin/helm'
    elif damage=='digest':
        validation['helm']['sha256']='not-a-pin'
    else:
        value['services'].pop('runner_validation')
    with pytest.raises(ValueError):
        bootstrap_recipe.validate(value)
