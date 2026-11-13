from copy import deepcopy
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

from devsecops.cli import build_parser, main
from devsecops.correction import CONTRACT_SCHEMA, ScopeValidator, _patch_bytes, _pointers, validate


POLICY = """apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: mission-metrics
  namespace: demo
spec:
  podSelector:
    matchLabels:
      app: mission
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
          podSelector:
            matchLabels:
              app: prometheus
      ports:
        - protocol: TCP
          port: 8080
"""


def git(repo, *args):
    return subprocess.check_output(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgSign=false", *args], cwd=repo,
        env={"PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}).decode().strip()


def patch_text(before=POLICY, after=None, name="deploy/policy.yaml"):
    after = after if after is not None else before.replace("port: 8080", "port: 9090")
    return "diff --git a/" + name + " b/" + name + "\n" + "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile="a/" + name, tofile="b/" + name))


@pytest.fixture
def correction(tmp_path):
    tmp_path = tmp_path.resolve()
    repo = tmp_path / "repository with spaces"
    (repo / "deploy").mkdir(parents=True)
    (repo / "deploy/policy.yaml").write_text(POLICY)
    git(repo, "init", "-q", "--template=")
    git(repo, "add", "--all")
    git(repo, "commit", "-qm", "baseline")
    sha = git(repo, "rev-parse", "HEAD")
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"schema_version": 1, "id": "test-review", "revision": "1", "policy": {},
        "targets": [{"name": "mission", "type": "manifests", "path": "deploy", "namespace": "demo", "kubernetes_version": "1.34.0"}]}))
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({"schema_version": 1, "id": "mission-correction", "base_commit": sha,
        "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(), "allowed_files": ["deploy/policy.yaml"],
        "allowed_rendered_changes": [{"resource": "networking.k8s.io/NetworkPolicy/demo/mission-metrics", "pointers": ["/spec/ingress"]}]}))
    patch = tmp_path / "proposal.diff"
    patch.write_text(patch_text())
    cache = Path.home() / ".cache/devsecops/v0.1"
    argv = ["validate-correction", "--repo", str(repo), "--base", sha, "--profile", str(profile),
            "--contract", str(contract), "--patch", str(patch), "--output", str(tmp_path / "out"),
            "--kubeconform", os.environ.get("DEVSECOPS_KUBECONFORM", str(cache / "bin/kubeconform")),
            "--schema-location", os.environ.get("DEVSECOPS_SCHEMAS", str(cache / "schemas")),
            "--helm", os.environ.get("DEVSECOPS_HELM", str(cache / "bin/helm"))]
    return build_parser().parse_args(argv), argv


def require_tool(args):
    if not Path(args.kubeconform).is_file() or not Path(args.schema_location).is_dir():
        pytest.skip("Real kubeconform and approved local schemas are required")


def test_real_patch_validation_preserves_source_and_binds_exact_bytes(correction):
    args, _ = correction
    require_tool(args)
    before = {p.relative_to(args.repo): p.read_bytes() for p in args.repo.rglob("*") if p.is_file()}
    result, review, patch = validate(args)
    assert result["decision"] == review["decision"] == "pass"
    assert result["patch_sha256"] == hashlib.sha256(patch).hexdigest()
    assert patch == args.patch.read_bytes()
    assert result["base_tree"]["sha256"] != result["patched_tree"]["sha256"]
    assert result["source"]["base_commit"] == args.base
    assert result["runtime_recovery"] == "not_evaluated"
    assert result["delivery_status"] == "not_performed"
    after = {p.relative_to(args.repo): p.read_bytes() for p in args.repo.rglob("*") if p.is_file()}
    assert before == after


def test_cli_writes_bound_terminal_manifest_without_raw_patch_stdout(correction, capsys):
    args, argv = correction
    require_tool(args)
    assert main(argv) == 0
    stdout = capsys.readouterr().out
    assert "namespaceSelector" not in stdout
    manifest = json.loads((args.output / "correction-manifest.json").read_text())
    assert manifest["finished"] and manifest["decision"] == "pass"
    for name, reference in manifest["artifacts"].items():
        assert hashlib.sha256((args.output / name).read_bytes()).hexdigest() == reference["sha256"]
    assert (args.output / "proposal.patch").read_bytes() == args.patch.read_bytes()


def test_cli_exports_the_exact_validated_render_pair(correction, capsys):
    args, argv = correction
    require_tool(args)
    assert main(argv) == 0
    assert "namespaceSelector" not in capsys.readouterr().out
    result = json.loads((args.output / "correction.json").read_bytes())
    manifest = json.loads((args.output / "correction-manifest.json").read_bytes())
    exported = result["rendered_export"]
    assert exported["status"] == "complete"
    assert exported["input_binding"] == {
        "base_commit": args.base,
        "base_tree_sha256": result["base_tree"]["sha256"],
        "candidate_tree_sha256": result["patched_tree"]["sha256"],
        "profile_sha256": hashlib.sha256(args.profile.read_bytes()).hexdigest(),
        "contract_sha256": hashlib.sha256(args.contract.read_bytes()).hexdigest(),
        "patch_sha256": hashlib.sha256(args.patch.read_bytes()).hexdigest(),
    }
    for name, port in (("rendered-before.json", 8080), ("rendered-after.json", 9090)):
        raw = (args.output / name).read_bytes()
        reference = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        assert manifest["artifacts"][name] == exported["artifacts"][name] == reference
        assert (args.output / name).stat().st_mode & 0o077 == 0
        document = json.loads(raw)
        assert set(document) == {"resources"}
        row, = document["resources"]
        assert set(row) == {"id", "body"}
        assert row["id"] == "networking.k8s.io/NetworkPolicy/demo/mission-metrics"
        assert row["body"]["spec"]["ingress"][0]["ports"][0]["port"] == port


def test_exact_render_export_is_optional_for_existing_python_callers(correction):
    args, _ = correction
    require_tool(args)
    result, report, patch = validate(args)
    assert "rendered_export" not in result
    assert result["decision"] == report["decision"] == "pass"
    assert patch == args.patch.read_bytes()


def test_export_writer_rejects_a_substituted_render_before_creating_output(correction):
    from devsecops.correction import write_outputs
    args, _ = correction
    require_tool(args)
    artifacts = {}
    result, report, patch = validate(args, rendered_artifacts=artifacts)
    artifacts["rendered-after.json"] = artifacts["rendered-before.json"]
    with pytest.raises(ValueError, match="render"):
        write_outputs(result, report, patch, args.output, rendered_artifacts=artifacts)
    assert not args.output.exists()


def test_actual_correction_exports_are_consumed_by_recovery_without_synthesized_rendering(correction, capsys):
    from test_runtime_recovery import fixture, condition
    from devsecops.runtime.recovery import verify_recovery
    args, argv = correction
    require_tool(args)
    assert main(argv) == 0
    capsys.readouterr()
    bundles, contract, evidence = fixture()
    for name, path in {
        'correction': args.output / 'correction.json',
        'correction_contract': args.contract,
        'patch': args.output / 'proposal.patch',
        'rendered_before': args.output / 'rendered-before.json',
        'rendered_after': args.output / 'rendered-after.json',
    }.items():
        evidence[name] = path
    contract['correction_contract_sha256'] = hashlib.sha256(args.contract.read_bytes()).hexdigest()
    report = verify_recovery(bundles['baseline'], bundles['fault'], bundles['recovery'],
                             contract=contract, evidence=evidence)
    assert condition(report, 'rendered-change-scope')['observed_state'] == 'recovered'
    for name in ('correction', 'patch', 'rendered_before', 'rendered_after'):
        assert report['inputs'][name]['sha256'] == hashlib.sha256(evidence[name].read_bytes()).hexdigest()
    # These local exports do not forge a native turn, Git delivery or live origin.
    assert condition(report, 'native-patch-delivery-chain')['observed_state'] == 'unknown'
    assert report['decision'] == 'unknown' and report['provenance']['verified'] is False


def test_duplicate_cross_target_render_inventory_is_not_exported(correction):
    args, _ = correction
    require_tool(args)
    profile = json.loads(args.profile.read_bytes())
    profile['targets'].append({**profile['targets'][0], 'name': 'duplicate'})
    args.profile.write_text(json.dumps(profile))
    contract = json.loads(args.contract.read_bytes())
    contract['profile_sha256'] = hashlib.sha256(args.profile.read_bytes()).hexdigest()
    args.contract.write_text(json.dumps(contract))
    artifacts = {}
    result, _, _ = validate(args, rendered_artifacts=artifacts)
    assert result['decision'] == 'unknown'
    assert result['rendered_export']['status'] == 'incomplete'
    assert artifacts == {}


def test_output_path_swap_cannot_redirect_private_render_bodies(correction, monkeypatch):
    import devsecops.runtime.cli as runtime_cli
    from devsecops.correction import write_outputs
    args, _ = correction
    require_tool(args)
    artifacts = {}
    result, review, patch = validate(args, rendered_artifacts=artifacts)
    retained, decoy = args.output.with_name('retained'), args.output.with_name('decoy')
    decoy.mkdir()
    original_write = runtime_cli._write
    def swap(directory, name, value):
        if name == 'rendered-before.json':
            args.output.rename(retained)
            args.output.symlink_to(decoy, target_is_directory=True)
        return original_write(directory, name, value)
    monkeypatch.setattr(runtime_cli, '_write', swap)
    with pytest.raises((OSError, ValueError)):
        write_outputs(result, review, patch, args.output, rendered_artifacts=artifacts)
    assert not list(decoy.iterdir())
    assert (retained / 'rendered-before.json').read_bytes() == artifacts['rendered-before.json']
    assert not (retained / 'correction-manifest.json').exists()


def test_full_render_values_remain_private_and_outside_readable_reports(correction, capsys):
    args, argv = correction
    require_tool(args)
    marker = 'private-render-value-keep-out-of-report'
    (args.repo / 'deploy/secret.yaml').write_text(
        'apiVersion: v1\nkind: Secret\nmetadata:\n  name: source-token\n  namespace: demo\n'
        'type: Opaque\nstringData:\n  token: ' + marker + '\n')
    git(args.repo, 'add', '--all')
    git(args.repo, 'commit', '-qm', 'include unchanged sensitive resource')
    args.base = git(args.repo, 'rev-parse', 'HEAD')
    argv[argv.index('--base') + 1] = args.base
    contract = json.loads(args.contract.read_bytes())
    contract['base_commit'] = args.base
    args.contract.write_text(json.dumps(contract))
    assert main(argv) == 0
    assert marker not in capsys.readouterr().out
    for name in ('rendered-before.json', 'rendered-after.json'):
        path = args.output / name
        assert marker in path.read_text()
        assert path.stat().st_mode & 0o077 == 0
    assert args.output.stat().st_mode & 0o077 == 0
    for name in ('correction.json', 'correction.html', 'correction.md',
                 'report.json', 'report.html', 'report.md', 'correction-manifest.json'):
        assert marker not in (args.output / name).read_text()


def test_actual_helm_values_correction_reuses_the_review_engine(correction):
    args, _ = correction
    require_tool(args)
    if not Path(args.helm).is_file():
        pytest.skip("Real approved Helm is required")
    chart = args.repo / "deploy"
    (chart / "templates").mkdir()
    (chart / "policy.yaml").rename(chart / "templates/policy.yaml")
    (chart / "templates/policy.yaml").write_text(POLICY.replace("port: 8080", "port: {{ .Values.metricsPort }}"))
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: mission\nversion: 0.1.0\n")
    (chart / "values.yaml").write_text("metricsPort: 8080\n")
    git(args.repo, "add", "--all")
    git(args.repo, "commit", "-qm", "Helm baseline")
    args.base = git(args.repo, "rev-parse", "HEAD")
    profile = json.loads(args.profile.read_text())
    profile["targets"][0].update(type="helm", release="mission")
    args.profile.write_text(json.dumps(profile))
    contract = json.loads(args.contract.read_text())
    contract.update(base_commit=args.base, profile_sha256=hashlib.sha256(args.profile.read_bytes()).hexdigest(), allowed_files=["deploy/values.yaml"])
    args.contract.write_text(json.dumps(contract))
    args.patch.write_text(patch_text("metricsPort: 8080\n", "metricsPort: 9090\n", "deploy/values.yaml"))
    artifacts = {}
    result, review, _ = validate(args, rendered_artifacts=artifacts)
    assert result["decision"] == review["decision"] == "pass"
    assert review["toolchain"]["helm"]["version"]
    assert json.loads(artifacts['rendered-before.json'])['resources'][0]['body']['spec']['ingress'][0]['ports'][0]['port'] == 8080
    assert json.loads(artifacts['rendered-after.json'])['resources'][0]['body']['spec']['ingress'][0]['ports'][0]['port'] == 9090
    assert result["scope_receipts"][0]["changes"] == [{
        "resource": "networking.k8s.io/NetworkPolicy/demo/mission-metrics", "pointer": "/spec/ingress/0/ports/0/port", "permitted": True}]


def test_actual_kustomize_correction_reuses_frozen_source_boundary(correction):
    args, _ = correction
    require_tool(args)
    args.kustomize = os.environ.get("DEVSECOPS_KUSTOMIZE", str(Path.home() / ".cache/devsecops/kustomize-v5.8.3/bin/kustomize"))
    if not Path(args.kustomize).is_file():
        pytest.skip("Real approved Kustomize is required")
    (args.repo / "deploy/kustomization.yaml").write_text("apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: [policy.yaml]\n")
    git(args.repo, "add", "--all")
    git(args.repo, "commit", "-qm", "Kustomize baseline")
    args.base = git(args.repo, "rev-parse", "HEAD")
    profile = json.loads(args.profile.read_text())
    profile["schema_version"] = 2
    profile["targets"][0].update(type="kustomize", source_root="deploy")
    args.profile.write_text(json.dumps(profile))
    contract = json.loads(args.contract.read_text())
    contract.update(base_commit=args.base, profile_sha256=hashlib.sha256(args.profile.read_bytes()).hexdigest())
    args.contract.write_text(json.dumps(contract))
    artifacts = {}
    result, review, _ = validate(args, rendered_artifacts=artifacts)
    assert result["decision"] == review["decision"] == "pass"
    assert review["schema_version"] == 2 and review["toolchain"]["kustomize"]["version"]
    assert json.loads(artifacts['rendered-after.json'])['resources'][0]['body']['spec']['ingress'][0]['ports'][0]['port'] == 9090


@pytest.mark.parametrize("change", ["outside-file", "base", "profile", "duplicate-key", "movable-base", "creation", "mode"])
def test_reject_unbound_or_unsupported_proposal(correction, change):
    args, _ = correction
    if change == "outside-file":
        args.patch.write_text(patch_text(name="deploy/other.yaml"))
    elif change in {"base", "profile"}:
        value = json.loads(args.contract.read_text())
        value["base_commit" if change == "base" else "profile_sha256"] = "0" * (40 if change == "base" else 64)
        args.contract.write_text(json.dumps(value))
    elif change == "duplicate-key":
        args.contract.write_text('{"schema_version":1,"schema_version":1}')
    elif change == "movable-base":
        args.base = "HEAD"
    elif change == "creation":
        args.patch.write_text("new file mode 100644\n" + patch_text())
    else:
        args.patch.write_text("old mode 100644\nnew mode 100755\n" + patch_text())
    with pytest.raises(ValueError):
        validate(args)


def test_semantic_change_outside_contract_is_completed_failure(correction):
    args, _ = correction
    require_tool(args)
    args.patch.write_text(patch_text(after=POLICY.replace("app: mission", "app: other")))
    result, review, _ = validate(args)
    assert result["decision"] == review["decision"] == "fail"
    assert result["execution_status"] == "complete"
    assert result["scope_receipts"][0]["violations"][0]["code"] == "rendered-change-outside-contract"


def test_comment_only_patch_is_not_a_functional_correction(correction):
    args, _ = correction
    require_tool(args)
    args.patch.write_text(patch_text(after="# unchanged behavior\n" + POLICY))
    result, review, _ = validate(args)
    assert review["decision"] == "pass"
    assert result["decision"] == "fail" and not result["has_rendered_change"]


def test_missing_schema_preserves_unknown(correction, tmp_path):
    args, _ = correction
    args.schema_location = tmp_path / "missing"
    result, review, _ = validate(args)
    assert result["decision"] == review["decision"] == "unknown"
    assert result["execution_status"] == "partial"


def test_images_cannot_change_even_under_an_allowed_spec():
    record = {"id": "apps/Deployment/demo/mission", "body": {"spec": {"containers": [{"image": "registry/image@sha256:old"}]}}}
    candidate = deepcopy(record)
    candidate["body"]["spec"]["containers"][0]["image"] = "registry/image@sha256:new"
    check = ScopeValidator([{"resource": record["id"], "pointers": ["/spec"]}])
    assert check({"name": "mission"}, [record], [candidate])["decision"] == "fail"


def test_inventory_change_cannot_be_hidden_by_field_scope():
    check = ScopeValidator([{"resource": "core/Service/demo/mission", "pointers": ["/spec"]}])
    result = check({"name": "mission"}, [{"id": "core/Service/demo/mission", "body": {}}], [])
    assert result["violations"] == [{"code": "resource-inventory-changed"}]


@pytest.mark.parametrize("pointer", ["", "/", "/spec//ingress", "/spec/~2", "/spec/\n"])
def test_invalid_scope_pointer(pointer):
    with pytest.raises(ValueError):
        ScopeValidator([{"resource": "core/Service/demo/a", "pointers": [pointer]}])


def test_type_changes_are_not_hidden_by_python_equality():
    assert _pointers({"value": True}, {"value": 1}) == ["/value"]
    assert _pointers({"value": [True]}, {"value": [1]}) == ["/value/0"]


@pytest.mark.parametrize('change', ['port-only', 'peer-widening', 'extra-peer', 'extra-port', 'reorder'])
def test_one_port_contract_preserves_policy_peers_and_other_rules(change):
    body = yaml.safe_load(POLICY)
    body['spec']['ingress'].append({'from': [{'podSelector': {'matchLabels': {'app': 'health-probe'}}}],
                                   'ports': [{'protocol': 'TCP', 'port': 8081}]})
    identity = 'networking.k8s.io/NetworkPolicy/demo/mission-metrics'
    before = {'id': identity, 'body': body}
    after = deepcopy(before)
    rules = after['body']['spec']['ingress']
    rules[0]['ports'][0]['port'] = 9090
    if change == 'peer-widening':
        del rules[0]['from'][0]['namespaceSelector']
    elif change == 'extra-peer':
        rules[0]['from'].append({})
    elif change == 'extra-port':
        rules[0]['ports'].append({'protocol': 'TCP', 'port': 80})
    elif change == 'reorder':
        rules.reverse()
    check = ScopeValidator([{'resource': identity, 'pointers': ['/spec/ingress/0/ports/0/port']}])
    result = check({'name': 'mission'}, [before], [after])
    assert result['decision'] == ('pass' if change == 'port-only' else 'fail')


def test_symlink_patch_is_rejected(tmp_path):
    real = tmp_path.resolve() / "real.patch"
    real.write_text(patch_text())
    link = tmp_path.resolve() / "link.patch"
    link.symlink_to(real)
    with pytest.raises(ValueError):
        _patch_bytes(link)


def test_schema_matches_packaged_definition():
    assert CONTRACT_SCHEMA == json.loads((Path(__file__).resolve().parents[1] / "schemas/correction-contract.schema.json").read_text())
