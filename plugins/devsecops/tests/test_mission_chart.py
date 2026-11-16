"""Real Helm rendering proves the deployable fault boundary, not live enforcement."""
from copy import deepcopy
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from devsecops.cli import build_parser
from devsecops.correction import validate
from test_correction import git


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / 'demo/software-factory/charts/mission-status'
CACHE = Path.home() / '.cache/devsecops/v0.1'
HELM = os.environ.get('DEVSECOPS_HELM', str(CACHE / 'bin/helm'))
KUBECONFORM = os.environ.get('DEVSECOPS_KUBECONFORM', str(CACHE / 'bin/kubeconform'))
SCHEMAS = os.environ.get('DEVSECOPS_SCHEMAS', str(CACHE / 'schemas'))
# Synthetic render-only identities. No pull or live trust claim is made.
VALUES = {
    'environment': 'render-only-test', 'architecture': 'amd64',
    'image': {'repository': 'registry.example.invalid/demo/mission', 'digest': 'sha256:' + 'a' * 64},
    'imagePullSecrets': [{'name': 'mission-reader'}],
    'networkPolicy': {'healthPeer': {'namespace': 'probe', 'podLabels': {'app': 'health-probe'}},
                      'metricsPeer': {'namespace': 'monitoring', 'podLabels': {'app.kubernetes.io/name': 'prometheus'}},
                      'metricsPort': 9090},
}


@pytest.fixture(autouse=True)
def real_tools():
    if not Path(HELM).is_file() or not Path(KUBECONFORM).is_file() or not Path(SCHEMAS).is_dir():
        pytest.skip('Pinned real Helm, kubeconform and local schemas are required')


def render(tmp_path, values, release='mission-status'):
    path = tmp_path / 'render-values.json'
    path.write_text(json.dumps(values))
    return subprocess.run([HELM, 'template', release, str(CHART), '--namespace', 'mission-demo',
                           '--kube-version', '1.34.0', '--values', str(path)], capture_output=True, timeout=20)


def objects(raw):
    return {(v['kind'], v['metadata']['name']): v for v in yaml.safe_load_all(raw) if v}


def test_real_render_schema_and_fault_hold_workload_identity_constant(tmp_path):
    baseline = render(tmp_path, VALUES)
    assert baseline.returncode == 0, baseline.stderr.decode()
    values = deepcopy(VALUES)
    values['networkPolicy']['metricsPort'] = 9091
    fault = render(tmp_path, values)
    assert fault.returncode == 0, fault.stderr.decode()
    before, after = objects(baseline.stdout), objects(fault.stdout)
    changed = [key for key in before if before[key] != after[key]]
    assert changed == [('NetworkPolicy', 'mission-status-metrics')]
    for raw in (baseline.stdout, fault.stdout):
        manifests = tmp_path / 'rendered.yaml'
        manifests.write_bytes(raw)
        checked = subprocess.run([KUBECONFORM, '-strict', '-summary', '-kubernetes-version', '1.34.0',
            '-schema-location', str(Path(SCHEMAS) / '{{ .NormalizedKubernetesVersion }}-standalone-strict/{{ .ResourceKind }}{{ .KindSuffix }}.json'),
            str(manifests)], capture_output=True, timeout=20)
        assert checked.returncode == 0, checked.stdout.decode() + checked.stderr.decode()
    deployment = before['Deployment', 'mission-status']['spec']['template']['spec']
    assert deployment['automountServiceAccountToken'] is False
    assert deployment['containers'][0]['image'] == VALUES['image']['repository'] + '@' + VALUES['image']['digest']
    assert deployment['containers'][0]['securityContext']['readOnlyRootFilesystem'] is True
    assert deployment['containers'][0]['ports'] == after['Deployment', 'mission-status']['spec']['template']['spec']['containers'][0]['ports']
    deny = before['NetworkPolicy', 'mission-status-default-deny']['spec']
    assert deny['policyTypes'] == ['Ingress', 'Egress'] and deny['ingress'] == deny['egress'] == []
    peer = before['NetworkPolicy', 'mission-status-metrics']['spec']['ingress'][0]['from']
    assert len(peer) == 1 and set(peer[0]) == {'namespaceSelector', 'podSelector'}


def test_rendered_probes_match_running_application_and_listener_defaults(tmp_path):
    from test_mission_app import APP, request
    rendered = render(tmp_path, VALUES)
    assert rendered.returncode == 0, rendered.stderr.decode()
    resources = objects(rendered.stdout)
    container = resources['Deployment', 'mission-status']['spec']['template']['spec']['containers'][0]
    ports = {port['name']: port['containerPort'] for port in container['ports']}
    defaults = APP.MissionService()
    assert ports == {'health': defaults.health_port, 'metrics': defaults.metrics_port}
    with APP.MissionService(health_port=0, metrics_port=0) as application:
        for probe_name in ('startupProbe', 'readinessProbe', 'livenessProbe'):
            probe = container[probe_name]['httpGet']
            assert probe['port'] == 'health'
            status, _, body = request(application.health_address, probe['path'])
            assert status == 200 and json.loads(body)['status'] == 'ok'
        assert request(application.metrics_address, '/metrics')[0] == 200
    service = resources['Service', 'mission-status']['spec']
    assert {port['targetPort']: port['port'] for port in service['ports']} == ports


@pytest.mark.parametrize('invalid', ['defaults', 'tag', 'wide-peer', 'typo', 'port', 'architecture', 'release-length'])
def test_chart_rejects_unresolved_or_accidentally_widened_input(tmp_path, invalid):
    values, release = deepcopy(VALUES), 'mission-status'
    if invalid == 'defaults':
        values = {}
    elif invalid == 'tag':
        values['image']['digest'] = 'latest'
    elif invalid == 'wide-peer':
        values['networkPolicy']['metricsPeer']['podLabels'] = {}
    elif invalid == 'typo':
        values['networkPolicy']['metricsPorts'] = 9090
    elif invalid == 'port':
        values['networkPolicy']['metricsPort'] = 0
    elif invalid == 'architecture':
        values['architecture'] = 'unknown'
    else:
        release = 'x' * 51
    assert render(tmp_path, values, release).returncode != 0


def test_actual_chart_patch_validates_one_port_and_rejects_peer_widening(tmp_path):
    tmp_path = tmp_path.resolve()
    repo = tmp_path / 'repository'
    shutil.copytree(CHART, repo / 'deploy/chart')
    values = deepcopy(VALUES)
    values['networkPolicy']['metricsPort'] = 9091
    baseline = json.dumps(values, indent=2) + '\n'
    source = repo / 'deploy/values.json'
    source.write_text(baseline)
    git(repo, 'init', '-q', '--template=')
    git(repo, 'add', '--all')
    git(repo, 'commit', '-qm', 'Synthetic fault')
    base = git(repo, 'rev-parse', 'HEAD')
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'schema_version': 1, 'id': 'mission-demo', 'revision': '1', 'policy': {},
        'targets': [{'name': 'mission', 'type': 'helm', 'path': 'deploy/chart', 'values': ['deploy/values.json'],
                     'release': 'mission-status', 'namespace': 'mission-demo', 'kubernetes_version': '1.34.0'}]}))
    contract = tmp_path / 'contract.json'
    contract.write_text(json.dumps({'schema_version': 1, 'id': 'mission-one-port', 'base_commit': base,
        'profile_sha256': hashlib.sha256(profile.read_bytes()).hexdigest(), 'allowed_files': ['deploy/values.json'],
        'allowed_rendered_changes': [{'resource': 'networking.k8s.io/NetworkPolicy/mission-demo/mission-status-metrics',
                                     'pointers': ['/spec/ingress/0/ports/0/port']}]}))
    patch = tmp_path / 'actual.diff'
    args = build_parser().parse_args(['validate-correction', '--repo', str(repo), '--base', base, '--profile', str(profile),
        '--contract', str(contract), '--patch', str(patch), '--output', str(tmp_path / 'output'),
        '--helm', HELM, '--kubeconform', KUBECONFORM, '--schema-location', SCHEMAS])
    for widen, expected in [(False, 'pass'), (True, 'fail')]:
        candidate = deepcopy(values)
        candidate['networkPolicy']['metricsPort'] = 9090
        if widen:
            candidate['networkPolicy']['metricsPeer']['podLabels'] = {'app': 'unrelated'}
        changed = json.dumps(candidate, indent=2) + '\n'
        patch.write_text('diff --git a/deploy/values.json b/deploy/values.json\n' + ''.join(difflib.unified_diff(
            baseline.splitlines(keepends=True), changed.splitlines(keepends=True),
            fromfile='a/deploy/values.json', tofile='b/deploy/values.json')))
        result, _, _ = validate(args)
        assert result['decision'] == expected
        assert result['has_rendered_change']
        assert source.read_text() == baseline
