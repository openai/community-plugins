"""Real pinned Helm/Kubeconform over actual Git and controlled published evidence."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from devsecops.factory import deployment_proposal as proposal
from devsecops.factory import mission_publication as publication
from devsecops.runtime.context import verify_seal
from test_mission_publication import case as publication_case, registry as registry_fixture, scanned, lab, ca

__all__ = ['scanned', 'lab', 'ca']
ROOT = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(repo, *args):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false',
        '-c', 'user.name=Controlled Test', '-c', 'user.email=test@example.invalid', *args], cwd=repo,
        env={'PATH': os.environ['PATH'], 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}, timeout=10).decode().strip()


@pytest.fixture(scope='module')
def published(tmp_path_factory, scanned, lab, ca):
    root = tmp_path_factory.mktemp('published-for-proposal').resolve()
    with pytest.MonkeyPatch.context() as monkeypatch:
        server = registry_fixture.__wrapped__(ca, monkeypatch)
        registry = next(server)
        try:
            case = publication_case.__wrapped__(scanned, lab, root, monkeypatch, registry)
            result, capability = publication.publish_signed_mission(**case['args'])
            assert capability is None and result['checks']['discovery'] == 'pass'
            binding = {**deepcopy(case['binding']), 'publication_config_sha256': case['args']['expected_config_sha256'],
                'artifact_manifest': result['artifact'], 'publication_readback': result['artifacts'],
                'publication_path': str(case['args']['output']), 'scope': 'registry-evidence-publication-only'}
        finally:
            server.close()
    return binding


@pytest.fixture(scope='module')
def tools():
    cache = Path.home() / '.cache/devsecops/v0.1'
    paths = {name + '_path': os.environ.get('DEVSECOPS_' + name.upper(), str(cache / 'bin' / name))
             for name in ('helm', 'kubeconform')}
    if not all(Path(path).is_file() for path in paths.values()):
        pytest.skip('Explicit pinned Helm/Kubeconform bootstrap required')
    return {**paths, **{name + '_sha256': sha(Path(paths[name + '_path']).read_bytes()) for name in ('helm', 'kubeconform')},
        'schemas_path': os.environ.get('DEVSECOPS_SCHEMAS', str(cache / 'schemas'))}


@pytest.fixture
def case(tmp_path, published, tools):
    root = tmp_path.resolve()
    retained = root / 'published'
    shutil.copytree(published['publication_path'], retained)
    binding = {**deepcopy(published), 'publication_path': str(retained)}
    capability = object.__new__(publication.PublishedMission)
    publication._PUBLISHED[capability] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic()+600, 'binding': binding}
    repo = root / 'deployment repository'
    chart = repo / 'charts/mission'
    chart.parent.mkdir(parents=True)
    shutil.copytree(proposal.CHART, chart)
    values = repo / 'environments/lab.yaml'
    values.parent.mkdir()
    values.write_text('''# Keep customer settings and comments — unchanged.
environment: lab
architecture: amd64
image:
  repository: registry.previous.example/demo/mission # previous approved image
  digest: "sha256:''' + 'b'*64 + '''"
imagePullSecrets: []
networkPolicy:
  healthPeer:
    namespace: probes
    podLabels: {app: probe}
  metricsPeer:
    namespace: monitoring
    podLabels: {app: prometheus}
  metricsPort: 9090
''')
    git(repo, 'init', '--quiet', '--template=')
    git(repo, 'add', '--all')
    git(repo, 'commit', '--quiet', '-m', 'Controlled baseline')
    commit = git(repo, 'rev-parse', 'HEAD')
    config = {'schema_version': 1, 'kind': 'mission-deployment-proposal-configuration',
        'deployment_repository': str(repo), 'deployment_commit': commit, 'chart_path': 'charts/mission',
        'values_path': 'environments/lab.yaml', 'image_keys': deepcopy(proposal._KEYS), 'release': 'mission',
        'namespace': 'mission', 'kubernetes_version': '1.34.0', **tools}
    config_path = root / 'proposal.json'
    config_path.write_text(json.dumps(config))
    args = {'published': capability, **{key: binding[key] for key in ('run_scope', 'source_commit', 'architecture',
        'origin', 'repository', 'policy_sha256', 'trust_policy_sha256', 'publication_config_sha256')},
        'config': config_path, 'expected_config_sha256': sha(config_path.read_bytes()), 'output': root / 'proposal'}
    return {'args': args, 'config': config, 'binding': binding, 'repo': repo, 'values': values}


def update(case):
    case['args']['config'].write_text(json.dumps(case['config']))
    case['args']['expected_config_sha256'] = sha(case['args']['config'].read_bytes())


def commit(case):
    git(case['repo'], 'add', '--all')
    git(case['repo'], 'commit', '--quiet', '-m', 'Controlled candidate baseline')
    case['config']['deployment_commit'] = git(case['repo'], 'rev-parse', 'HEAD')
    update(case)


def test_actual_helm_schema_and_git_patch_preserve_other_fields(case, tmp_path):
    before = case['values'].read_bytes()
    before_head = git(case['repo'], 'rev-parse', 'HEAD')
    result, capability = proposal.propose_deployment_change(**case['args'])
    assert capability is None and result['decision'] == result['factory_decision'] == 'unknown'
    assert result['gaps'] == ['controlled-publication-cannot-issue-deployment-proposal-authority']
    assert set(result['checks'].values()) == {'pass'}
    assert result['rendered_changes'] == [{'resource': 'apps/Deployment/mission/mission', 'pointer': '/spec/template/spec/containers/0/image'}]
    assert result['image']['runtime_architecture'] == 'not-observed'
    assert set(result['downstream'].values()) == {'not-performed'}
    assert case['values'].read_bytes() == before and git(case['repo'], 'rev-parse', 'HEAD') == before_head
    assert git(case['repo'], 'status', '--porcelain') == ''
    verify_seal(result, 'proposal_sha256')
    root = case['args']['output']
    assert '# previous approved image' in (root / 'values-after.yaml').read_text()
    # Independent real Git application proves exported bytes reproduce the file.
    replica = tmp_path / 'patch-check'
    shutil.copytree(case['repo'], replica)
    git(replica, 'apply', '--check', str(root / 'proposal.patch'))
    git(replica, 'apply', str(root / 'proposal.patch'))
    assert (replica / 'environments/lab.yaml').read_bytes() == (root / 'values-after.yaml').read_bytes()
    assert git(replica, 'diff', '--name-only') == 'environments/lab.yaml'


def schemas_1365():
    path = Path(os.environ.get('DEVSECOPS_SCHEMAS_1365', Path.home() / '.cache/devsecops/kubernetes-1.36.5/schemas'))
    if not (path / 'v1.36.5-standalone-strict').is_dir():
        pytest.skip('Explicit Kubernetes 1.36.5 schema bootstrap required')
    return path


def test_actual_1365_helm_and_kubeconform_use_exact_selected_schema_pins(case):
    case['config']['kubernetes_version'] = '1.36.5'
    case['config']['schemas_path'] = str(schemas_1365())
    update(case)
    result, capability = proposal.propose_deployment_change(**case['args'])
    assert capability is None and result['decision'] == 'unknown'
    assert set(result['checks'].values()) == {'pass'}
    assert result['schema_source']['kubernetes_version'] == '1.36.5'
    assert result['schema_source']['commit'] == 'a6f9a32d2ccb64b6e4f5b41419b9c2e8ee0cce18'
    assert len(result['schemas']) == 24 and all(name.startswith('v1.36.5-standalone-strict/') for name in result['schemas'])
    assert result['gaps'] == ['controlled-publication-cannot-issue-deployment-proposal-authority']
    assert all(side['schema']['decision'] == 'pass' for side in result['validation'].values())


@pytest.mark.parametrize('wrong', ['old-set', 'modified-pin'])
def test_selected_1365_cannot_validate_with_134_or_changed_schema_bytes(case, tmp_path, wrong):
    case['config']['kubernetes_version'] = '1.36.5'
    if wrong == 'modified-pin':
        schemas = tmp_path / 'changed-schemas'
        shutil.copytree(schemas_1365(), schemas)
        (schemas / 'v1.36.5-standalone-strict/deployment-apps-v1.json').write_text('{}')
        case['config']['schemas_path'] = str(schemas)
    update(case)
    result, capability = proposal.propose_deployment_change(**case['args'])
    assert capability is None and result['decision'] == 'unknown'
    assert result['checks']['schema'] != 'pass'
    assert 'deployment-proposal-incomplete-or-changed' in result['gaps']


@pytest.mark.parametrize('mutation', ['json', 'expired', 'foreign', 'source'])
def test_publication_admission_precedes_any_output(case, mutation):
    if mutation == 'json':
        case['args']['published'] = case['binding']
    elif mutation == 'expired':
        publication._PUBLISHED[case['args']['published']]['expires_monotonic'] = 0
    elif mutation == 'foreign':
        publication._PUBLISHED[case['args']['published']]['pid'] = 0
    else:
        case['args']['source_commit'] = 'f'*40
    with pytest.raises(ValueError):
        proposal.propose_deployment_change(**case['args'])
    assert not case['args']['output'].exists()


@pytest.mark.parametrize('mutation', ['config-pin', 'helm-pin', 'schema-version', 'chart', 'extra-chart-file', 'symlink', 'architecture', 'bad-peer', 'publication-bytes'])
def test_invalid_or_changed_selected_inputs_never_issue_proposal(case, mutation):
    if mutation == 'config-pin':
        case['args']['expected_config_sha256'] = '0'*64
    elif mutation == 'helm-pin':
        case['config']['helm_sha256'] = '0'*64
        update(case)
    elif mutation == 'schema-version':
        case['config']['kubernetes_version'] = '1.33.0'
        update(case)
    elif mutation in ('chart', 'extra-chart-file'):
        target = case['repo'] / 'charts/mission' / ('templates/deployment.yaml' if mutation == 'chart' else 'templates/extra.yaml')
        target.write_text('apiVersion: v1\nkind: Pod\n')
        commit(case)
    elif mutation == 'symlink':
        case['values'].unlink()
        case['values'].symlink_to('/etc/passwd')
        commit(case)
    elif mutation in ('architecture', 'bad-peer'):
        raw = case['values'].read_text()
        case['values'].write_text(raw.replace('architecture: amd64', 'architecture: arm64') if mutation == 'architecture' else raw.replace('namespace: probes', 'namespace: ""'))
        commit(case)
    else:
        path = Path(case['binding']['publication_path']) / 'readback-payload.json'
        path.chmod(0o600)
        path.write_bytes(b'changed')
    result, capability = proposal.propose_deployment_change(**case['args'])
    assert capability is None and result['decision'] == 'unknown'
    assert 'deployment-proposal-incomplete-or-changed' in result['gaps']


def test_noop_is_explicit_and_retains_exact_existing_bytes(case):
    raw = case['values'].read_text().replace('registry.previous.example/demo/mission',
        case['binding']['origin'].removeprefix('https://') + '/' + case['binding']['repository']).replace('sha256:'+'b'*64, case['binding']['manifest_digest'])
    case['values'].write_text(raw)
    commit(case)
    result, capability = proposal.propose_deployment_change(**case['args'])
    assert capability is None and result['checks']['image_only'] == 'pass' and result['change_required'] is False
    assert result['rendered_changes'] == [] and (case['args']['output'] / 'proposal.patch').read_bytes() == b''
    assert (case['args']['output'] / 'values-after.yaml').read_bytes() == raw.encode()


def test_rendered_comparison_rejects_extra_value_change_even_if_helper_regresses(case, monkeypatch):
    actual = proposal.replace_mission_image
    def incorrect(raw, **kwargs):
        result, changes = actual(raw, **kwargs)
        return result.replace(b'environment: lab', b'environment: changed'), changes
    monkeypatch.setattr(proposal, 'replace_mission_image', incorrect)
    report, capability = proposal.propose_deployment_change(**case['args'])
    assert capability is None and report['decision'] == 'unknown'
    assert report['checks']['render'] == report['checks']['schema'] == 'pass'
    assert report['checks']['image_only'] != 'pass'


def test_patch_and_semantic_result_are_deterministic_for_exact_inputs(case):
    first, _ = proposal.propose_deployment_change(**case['args'])
    initial = case['args']['output']
    case['args']['output'] = initial.with_name('second-proposal')
    second, _ = proposal.propose_deployment_change(**case['args'])
    for name in ('proposal.patch', 'values-after.yaml', 'rendered-before.json', 'rendered-after.json'):
        assert (initial / name).read_bytes() == (case['args']['output'] / name).read_bytes()
    assert first['checks'] == second['checks'] and first['rendered_changes'] == second['rendered_changes']


@pytest.mark.parametrize('ending', ['\r\n', ''])
def test_exported_patch_applies_with_crlf_or_missing_final_newline(tmp_path, ending):
    before = ('image: {repository: old.example/app, digest: "sha256:'+'a'*64+'"}'+ending).encode()
    after = before.replace(b'old.example/app', b'new.example/app').replace(b'a'*64, b'b'*64)
    path = tmp_path / 'values.yaml'
    path.write_bytes(before)
    patch = tmp_path / 'proposal.patch'
    patch.write_bytes(proposal._patch(before, after, 'values.yaml'))
    git(tmp_path, 'init', '--quiet', '--template=')
    git(tmp_path, 'apply', '--check', str(patch))
    git(tmp_path, 'apply', str(patch))
    assert path.read_bytes() == after


@pytest.mark.parametrize('mutation', ['expiry', 'evidence', 'route'])
def test_final_receipt_reaudits_and_withdraws_on_change(case, monkeypatch, mutation):
    actual = proposal._write
    root = case['args']['output']
    def write(directory, name, value):
        result = actual(directory, name, value)
        if name == 'deployment-proposal.json':
            if mutation == 'expiry':
                publication._PUBLISHED[case['args']['published']]['expires_monotonic'] = 0
            elif mutation == 'evidence':
                (root / 'proposal.patch').write_text('changed')
            else:
                root.rename(root.with_name('moved'))
                root.mkdir(mode=0o700)
        return result
    monkeypatch.setattr(proposal, '_write', write)
    with pytest.raises(ValueError):
        proposal.propose_deployment_change(**case['args'])
    retained = root.with_name('moved') if mutation == 'route' else root
    assert not (retained / 'deployment-proposal.json').exists()


def test_first_image_initialization_validates_after_without_inventing_a_prior_workload(case):
    before = (
        case["values"]
        .read_text()
        .replace("registry.previous.example/demo/mission", '""')
        .replace("sha256:" + "b" * 64, "")
    )
    case["values"].write_text(before)
    commit(case)
    report, capability = proposal.propose_deployment_change(**case["args"])
    assert capability is None  # The fixture publication remains explicitly controlled.
    assert report["gaps"] == ["controlled-publication-cannot-issue-deployment-proposal-authority"]
    assert report["before_state"] == "uninitialized-image"
    assert report["validation"]["before"] == {"decision": "not-performed", "reason": "uninitialized-image"}
    assert report["validation"]["after"]["schema"]["decision"] == "pass"
    assert json.loads((case["args"]["output"] / "rendered-before.json").read_bytes()) == {
        "resources": {},
        "state": "uninitialized-image",
        "rendered": False,
    }
    assert report["rendered_changes"] == [
        {
            "resource": "apps/Deployment/mission/mission",
            "pointer": "/spec/template/spec/containers/0/image",
            "kind": "first-image-initialization",
        }
    ]
    assert case["values"].read_text() == before


@pytest.mark.parametrize("empty", ["repository", "digest"])
def test_first_image_rejects_a_partially_empty_selection(case, empty):
    before = case["values"].read_text()
    before = (
        before.replace("registry.previous.example/demo/mission", '""')
        if empty == "repository"
        else before.replace("sha256:" + "b" * 64, "")
    )
    case["values"].write_text(before)
    commit(case)
    report, capability = proposal.propose_deployment_change(**case["args"])
    assert capability is None and "deployment-proposal-incomplete-or-changed" in report["gaps"]
    assert report["checks"]["image_only"] != "pass"


def test_first_image_rejects_changed_chart_before_initialization(case):
    case["values"].write_text(
        case["values"]
        .read_text()
        .replace("registry.previous.example/demo/mission", '""')
        .replace("sha256:" + "b" * 64, "")
    )
    chart = case["repo"] / "charts/mission/templates/deployment.yaml"
    chart.write_text(chart.read_text().replace("replicas: 1", "replicas: 2"))
    commit(case)
    report, capability = proposal.propose_deployment_change(**case["args"])
    assert capability is None and report["checks"]["image_only"] != "pass"


def test_first_image_rejects_unrelated_proposed_values_delta(case, monkeypatch):
    case['values'].write_text(case['values'].read_text().replace('registry.previous.example/demo/mission', '""').replace('sha256:'+'b'*64, ''))
    commit(case)
    original = proposal.replace_mission_image
    def corrupted(raw, **kwargs):
        after, changes = original(raw, **kwargs)
        return after.replace(b'metricsPort: 9090', b'metricsPort: 9091'), changes
    monkeypatch.setattr(proposal, 'replace_mission_image', corrupted)
    report, capability = proposal.propose_deployment_change(**case['args'])
    assert capability is None and report['checks']['image_only'] != 'pass'
