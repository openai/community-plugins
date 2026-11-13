"""Useful evidence survives unavailable optional stages; contracts stay portable."""
import json
import subprocess
from pathlib import Path

from devsecops.cli import build_parser
from devsecops.engine import review
from devsecops.contracts import REPORT_SCHEMA, validate_report
from devsecops.profiles import PROFILE_SCHEMA
import pytest

ROOT = Path(__file__).resolve().parents[1]


def make_repo(tmp_path):
    repo = tmp_path/'repo'
    repo.mkdir()
    (repo/'manifests').mkdir()
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'app'},
           'spec': {'containers': [{'name': 'app', 'image': 'nginx:1.27', 'securityContext': {'privileged': False}}]}}
    (repo/'manifests/pod.json').write_text(json.dumps(pod))
    (repo/'chart').mkdir()
    (repo/'chart/Chart.yaml').write_text('apiVersion: v2\nname: fixture\nversion: 0.1.0\n')
    for args in [('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.invalid'),
                 ('add', '.'), ('commit', '-qm', 'baseline')]:
        subprocess.run(['git', *args], cwd=repo, capture_output=True, check=True)
    pod['spec']['containers'][0]['securityContext']['privileged'] = True
    (repo/'manifests/pod.json').write_text(json.dumps(pod))
    profile = {'schema_version': 1, 'id': 'fixture', 'revision': '1', 'policy': {}, 'targets': [
        {'name': 'plain', 'type': 'manifests', 'path': 'manifests', 'namespace': 'demo', 'kubernetes_version': '1.34.0'}]}
    path = tmp_path/'profile.json'
    path.write_text(json.dumps(profile))
    return repo, path, profile


def arguments(repo, path, output):
    return build_parser().parse_args(['review', '--repo', str(repo), '--base', 'HEAD', '--profile', str(path),
        '--output', str(output), '--kubeconform', '/definitely-missing/kubeconform', '--schema-location', '/definitely-missing/schemas'])


def test_missing_schema_tool_preserves_candidate_findings(tmp_path):
    repo, profile, _ = make_repo(tmp_path)
    report = review(arguments(repo, profile, tmp_path/'out'))
    validate_report(report)
    assert report['decision'] == 'unknown'
    assert report['targets'][0]['confirmed_failure'] is True
    assert any(f['rule_id'] == 'K8S-PRIVILEGED' and f['change'] == 'introduced' for f in report['targets'][0]['findings'])
    assert any(g['code'] == 'schema-prerequisite' for g in report['targets'][0]['gaps'])


def test_missing_helm_preserves_independent_manifest_target(tmp_path):
    repo, profile_path, profile = make_repo(tmp_path)
    profile['targets'].append({'name': 'chart', 'type': 'helm', 'path': 'chart', 'release': 'web', 'namespace': 'demo', 'kubernetes_version': '1.34.0'})
    profile_path.write_text(json.dumps(profile))
    args = arguments(repo, profile_path, tmp_path/'out')
    args.helm = '/definitely-missing/helm'
    report = review(args)
    assert report['decision'] == 'unknown'
    assert len(report['targets']) == 2
    assert report['targets'][0]['findings']
    assert report['targets'][1]['decision'] == 'unknown'


def test_public_contracts_match_runtime():
    for name, schema in [('profile', PROFILE_SCHEMA), ('report', REPORT_SCHEMA)]:
        assert json.loads((ROOT/'schemas'/f'{name}.schema.json').read_text()) == schema
    lock = (ROOT/'uv.lock').read_text()
    assert '.internal.' not in lock and 'https://pypi.org/simple' in lock


def test_report_contract_rejects_false_complete_or_invalid_finding():
    with pytest.raises(ValueError, match='report schema'):
        validate_report({'schema_version': 1, 'decision': 'pass'})
