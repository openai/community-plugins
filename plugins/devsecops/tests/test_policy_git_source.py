"""Actual Git/Helm/schema policy producer with explicit controlled origin seams.

F0, lifecycle admission, delivery history and native invocation are controlled
upstream boundaries. These tests do not establish live runtime qualification.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import shutil
from time import monotonic
from types import SimpleNamespace

import pytest
import yaml

from test_correction import correction, POLICY, git, require_tool, patch_text
from devsecops.correction import validate
from devsecops.engine import _schema_receipt
from devsecops.factory import policy_git_source as module, owned_git_delivery
from devsecops.factory.network_policy_qualification import _candidate
from devsecops.factory.owned_operations import OwnedOperationCoordinator
from devsecops.factory.lifecycle import PhaseContext
from devsecops.factory.state import IDENTITY_KEYS, StateError, _canonical, _sha

__all__ = ["correction"]


@pytest.fixture
def policy_case(correction, tmp_path, monkeypatch, request):
    action = getattr(request, "param", "inject")
    args, _ = correction
    require_tool(args)
    if not Path(args.helm).is_file():
        pytest.fail("Pinned actual Helm required for policy source qualification")
    chart = args.repo / "deploy"
    (chart / "templates").mkdir()
    (chart / "policy.yaml").unlink()
    (chart / "templates/policy.yaml").write_text(
        POLICY.replace("app: prometheus", "app: {{ .Values.metricsLabel }}")
    )
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: mission\nversion: 0.1.0\n")
    baseline = b"metricsLabel: prometheus\n"
    values = chart / "values.yaml"
    values.write_bytes(baseline)
    git(args.repo, "add", "--all")
    git(args.repo, "commit", "-qm", "actual Helm baseline")
    baseline_revision = git(args.repo, "rev-parse", "HEAD")
    plan = _candidate(
        yaml.safe_load(POLICY),
        source_path="deploy/values.yaml",
        source_commit=baseline_revision,
        metrics_port=8080,
        namespace_labels={"kubernetes.io/metadata.name": "monitoring"},
        pod_labels={"app": "prometheus"},
    )
    profile = json.loads(args.profile.read_bytes())
    profile["targets"][0].update(type="helm", release="mission")
    args.profile.write_bytes(_canonical(profile))
    accepted = None
    if action == "deliver":
        values.write_text("metricsLabel: devsecops-f0-denied\n")
        git(args.repo, "add", "--all")
        git(args.repo, "commit", "-qm", "controlled qualified fault")
        args.base = git(args.repo, "rev-parse", "HEAD")
        contract = json.loads(args.contract.read_bytes())
        contract.update(
            base_commit=args.base,
            profile_sha256=_sha(args.profile.read_bytes()),
            allowed_files=["deploy/values.yaml"],
            allowed_rendered_changes=[{"resource": plan.resource_id, "pointers": [plan.pointer]}],
        )
        args.contract.write_bytes(_canonical(contract))
        args.patch.write_text(patch_text(values.read_text(), baseline.decode(), "deploy/values.yaml"))
        renders = {}
        report, review, patch = validate(args, rendered_artifacts=renders)
        assert report["decision"] == review["decision"] == "pass"
        output = tmp_path / "accepted"
        output.mkdir()
        paths = {}
        for name, raw in {
            "patch": patch,
            "correction": _canonical(report),
            "rendered_before": renders["rendered-before.json"],
            "rendered_after": renders["rendered-after.json"],
        }.items():
            p = output / name
            p.write_bytes(raw)
            paths[name] = (p, _sha(raw))
        accepted = dict(
            artifacts=paths,
            scope=object(),
            origin=object(),
            request_sha256="e" * 64,
            base_commit=args.base,
            base_tree=report["base_tree"],
            execution_mode="operator",
        )
        from devsecops.factory import correction_phase

        monkeypatch.setattr(correction_phase, "require_validated_correction", lambda *a, **k: accepted)
    revision = git(args.repo, "rev-parse", "HEAD")
    attempt = "shared" if action == "reset" else "primary"
    phase = {
        "id": "reset" if action == "reset" else "primary-" + action,
        "operation": action,
        "attempt": attempt,
    }
    identities = {key: "1" * 64 for key in IDENTITY_KEYS}
    inputs = {
        "config": {
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "allowed_mutations": ["inject", "deliver", "reset"],
        }
    }
    context = PhaseContext(
        "controlled-run", phase, identities, inputs, {}, tmp_path / "journal", monotonic() + 180, "operator"
    )
    journal = SimpleNamespace(identities=identities, run_dir=context.run_dir)
    ops = object.__new__(OwnedOperationCoordinator)
    ops.journal = journal
    ops.bootstrap = SimpleNamespace(driver=SimpleNamespace(journal=journal))
    ops._admit = lambda ctx, action=None: "controlled-operation"
    previous = object()
    baseline_delivery = object()
    binding = {
        "deployment_commit": revision,
        "source_commit": "a" * 40,
        "delivery_stage": "merged-watched-branch",
    }
    checkout = {"revision": revision, "repository": str(args.repo)}
    selected = {
        "plan": plan,
        "source_kind": "helm-values",
        "source_path": "deploy/values.yaml",
        "source_pointer": "/metricsLabel",
        "resource_id": plan.resource_id,
        "render_resource_id": plan.resource_id,
        "qualification": {"qualification_sha256": "f" * 64},
    }
    ops.runtime = SimpleNamespace(qualified_policy_plan=lambda context: selected)
    ops.delivery = (
        lambda attempt, stage: baseline_delivery if stage == "baseline" and action == "reset" else previous
    )
    ops.source_checkout = lambda *key: deepcopy(checkout)
    history = tmp_path / "historical"
    history.mkdir()
    historical = {
        "origin": "https://registry.example.invalid",
        "repository": "mission",
        "manifest_digest": "sha256:" + "b" * 64,
        "published_evidence": {"signer": {"authority": "explicit-controlled-test-upstream"}},
    }
    (history / "historical-source.json").write_bytes(_canonical({"proposal": historical}))
    monkeypatch.setattr(
        owned_git_delivery,
        "require_delivery",
        lambda value, **kw: deepcopy(
            {**binding, "deployment_commit": baseline_revision if value is baseline_delivery else revision}
        ),
    )
    monkeypatch.setattr(owned_git_delivery, "_record", lambda *a: {"output": history})
    binary = Path(shutil.which("git")).resolve()
    commit = {
        "schema_version": 1,
        "kind": "mission-local-deployment-commit-configuration",
        "deployment_repository": str(args.repo),
        "deployment_commit": revision,
        "values_path": "deploy/values.yaml",
        "proposal_configuration_sha256": "d" * 64,
        "git_path": str(binary),
        "git_sha256": _sha(binary.read_bytes()),
        "author_name": "Controlled Policy",
        "author_email": "policy@example.invalid",
    }
    ops.selected_component = lambda name: (tmp_path / "unused", "0" * 64, _canonical(commit))
    native = {
        "profile": {"path": str(args.profile), "sha256": _sha(args.profile.read_bytes())},
        "schemas": {
            "path": str(args.schema_location),
            "sha256": _schema_receipt(Path(args.schema_location))["sha256"],
        },
        "tools": {
            name: {"path": str(getattr(args, name)), "sha256": _sha(Path(getattr(args, name)).read_bytes())}
            for name in ("helm", "kubeconform")
        },
    }
    ops._recipe = lambda: {"native": native}
    return SimpleNamespace(
        ops=ops,
        context=context,
        output=tmp_path / "policy",
        plan=plan,
        selection=selected,
        args=args,
        baseline=baseline,
        accepted=accepted,
        commit=commit,
    )


@pytest.mark.parametrize("policy_case", ["inject", "deliver", "reset"], indirect=True)
def test_direct_producer_preserves_exact_render_and_builds_real_native_commit(policy_case, tmp_path):
    from devsecops.factory import deployment_commit, git_transport

    c = policy_case
    source = module.prepare_policy_source(c.context, operations=c.ops, output=c.output)
    bound = source.describe()
    after = json.loads((c.output / "rendered-after.json").read_bytes())["resources"][0]["body"]
    assert after == json.loads(c.plan.fault if c.context.phase["operation"] == "inject" else c.plan.baseline)
    selected = {**c.commit, "proposal_configuration_sha256": bound["configuration_sha256"]}
    raw = _canonical(selected)
    path = tmp_path / "commit-config.json"
    path.write_bytes(raw)
    report, commit = deployment_commit.prepare_deployment_commit(
        proposal=source,
        **{
            name: bound[name]
            for name in (
                "run_scope",
                "source_commit",
                "origin",
                "repository",
                "manifest_digest",
                "deployment_repository",
                "deployment_commit",
            )
        },
        proposal_configuration_sha256=bound["configuration_sha256"],
        config=path,
        expected_config_sha256=_sha(raw),
        output=tmp_path / "prepared",
    )
    assert report["decision"] == "pass" and commit is not None
    scope = {name: bound[name] for name in ("run_scope", "source_commit", "deployment_commit")}
    scope.update(proposal_configuration_sha256=bound["configuration_sha256"], configuration_sha256=_sha(raw))
    report, transport = git_transport.prepare_git_transport(
        prepared=commit, **scope, output=tmp_path / "transport"
    )
    assert report["decision"] == "pass" and transport is not None
    assert commit.describe()["prepared"]["content_already_baseline"] is (
        c.context.phase["operation"] == "reset"
    )
    assert git(c.args.repo, "rev-parse", "HEAD") == bound["deployment_commit"]


@pytest.mark.parametrize("change", ["pointer", "baseline", "unrelated", "image"])
def test_changed_or_unmapped_policy_source_rejected(policy_case, change, monkeypatch):
    c = policy_case
    if change == "pointer":
        c.selection["source_pointer"] = "/missing"
    elif change == "baseline":
        c.selection["plan"] = deepcopy(c.plan)
        object.__setattr__(c.selection["plan"], "baseline", b"{}")
    elif change == "unrelated":
        c.selection["source_pointer"] = "/metricsLabel"
        old = yaml.safe_dump
        monkeypatch.setattr(yaml, "safe_dump", lambda value, **kw: old(dict(value, extra="unselected"), **kw))
    else:
        c.selection["source_pointer"] = "/metricsLabel"
        old = module._replace_pointer

        def replace(value, *args):
            old(value, *args)
            value["image"] = "unselected"

        monkeypatch.setattr(module, "_replace_pointer", replace)
    with pytest.raises((StateError, ValueError)):
        module.prepare_policy_source(c.context, operations=c.ops, output=c.output)
