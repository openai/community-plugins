"""Real TLS/Cosign and Helm composition, with explicit test-only trust handoff.

Controlled publication cannot issue PublishedMission. The first test exercises
that production boundary. The second injects a private in-process test record
after real publication to reach Helm; it keeps the controlled markers and the
original unknown receipts. It is component integration, never canonical trust.
"""
from copy import deepcopy
import json
import os
import shutil
import time

from devsecops.factory import deployment_proposal as proposal
from devsecops.factory import mission_proposal_operation as operation
from devsecops.factory import mission_publication as publication
from devsecops.runtime.context import verify_seal
from test_deployment_proposal import git, tools
from test_mission_publication import case, registry, scanned, lab, ca, encode, sha
from test_mission_publication_operation import inputs, signed_result

__all__ = ['case', 'registry', 'scanned', 'lab', 'ca', 'tools']


def proposal_inputs(case, tmp_path, tools):
    arguments = inputs(case, tmp_path)
    repo = tmp_path / 'deployment repository'
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
  digest: "sha256:''' + 'b' * 64 + '''"
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
    git(repo, 'commit', '--quiet', '-m', 'Controlled deployment baseline')
    selected = {'schema_version': 1, 'kind': 'mission-deployment-proposal-configuration',
        'deployment_repository': str(repo), 'deployment_commit': git(repo, 'rev-parse', 'HEAD'),
        'chart_path': 'charts/mission', 'values_path': 'environments/lab.yaml',
        'image_keys': deepcopy(proposal._KEYS), 'release': 'mission', 'namespace': 'mission',
        'kubernetes_version': '1.34.0', **tools}
    path = tmp_path / 'proposal-configuration.json'
    path.write_bytes(encode(selected))
    arguments.update(proposal_config=path, proposal_config_sha256=sha(path.read_bytes()))
    return arguments, repo, values, selected


def actual_publication(arguments, case):
    root = arguments['output'] / 'publication'
    aggregate = json.loads((root / 'mission-publication-operation.json').read_bytes())
    report = json.loads((root / 'publication/mission-publication.json').read_bytes())
    verify_seal(aggregate, 'publication_operation_sha256')
    verify_seal(report, 'publication_sha256')
    assert aggregate['decision'] == report['decision'] == 'unknown'
    assert aggregate['publication_sha256'] == report['publication_sha256']
    assert report['gaps'] == ['controlled-evidence-cannot-issue-publication-authority']
    assert report['checks'] == {**dict.fromkeys(report['checks'], 'pass'), 'trust': 'unknown'}
    assert all(row['state'] == 'content-confirmed-by-immutable-readback' for row in report['mutations'])
    assert {'POST', 'PUT', 'GET'} <= {row['method'] for row in case['registry']['requests']}
    artifact = json.loads(case['registry']['manifests'][report['artifact']['digest']])
    assert artifact['subject']['digest'] == case['binding']['manifest_digest']
    for name, reference in report['artifacts'].items():
        raw = (root / 'publication' / name).read_bytes()
        assert sha(raw) == reference['sha256'] and len(raw) == reference['bytes']
    return aggregate, report, root / 'publication'


def test_real_controlled_publication_stops_outer_composition_before_helm(case, tmp_path, monkeypatch, tools):
    calls = signed_result(case, monkeypatch)  # Unavailable build/base issuance boundary only.
    arguments, repo, values, selected = proposal_inputs(case, tmp_path, tools)
    before = values.read_bytes()
    report, capability = operation.qualify_publish_and_propose(**arguments)
    aggregate, _, _ = actual_publication(arguments, case)
    assert len(calls) == 1
    assert calls[0]['expected_source_revision'] == case['binding']['source_commit']
    assert calls[0]['expected_source_repository'] == arguments['expected_source_repository']
    assert capability is None and report['decision'] == report['factory_decision'] == 'unknown'
    assert report['checks'] == {'publication': 'unknown', 'proposal': 'not-started'}
    assert report['gaps'] == ['publication-incomplete-or-failed']
    assert report['publication_operation_sha256'] == aggregate['publication_operation_sha256']
    assert not (arguments['output'] / 'proposal').exists()
    assert values.read_bytes() == before and git(repo, 'rev-parse', 'HEAD') == selected['deployment_commit']
    assert git(repo, 'status', '--porcelain') == ''
    assert set(report['downstream'].values()) == {'not-performed'}
    verify_seal(report, 'proposal_operation_sha256')


def test_test_only_controlled_handoff_reaches_real_helm_without_issuing_proposal(case, tmp_path, monkeypatch, tools):
    calls = signed_result(case, monkeypatch)
    arguments, repo, values, selected = proposal_inputs(case, tmp_path, tools)
    before = values.read_bytes()
    publish = operation.qualify_sign_and_publish
    handed_off = []

    def controlled_internal_handoff(**kwargs):
        # Execute the actual five-config composition, TLS upload/readback and
        # Cosign verification. No saved report is accepted by a production API.
        result, capability = publish(**kwargs)
        assert capability is None and result['decision'] == 'unknown'
        aggregate, retained, root = actual_publication(arguments, case)
        assert result == aggregate
        binding = {**deepcopy(case['binding']),
            'publication_config_sha256': kwargs['publication_config_sha256'],
            'artifact_manifest': retained['artifact'], 'publication_readback': retained['artifacts'],
            'publication_path': str(root), 'scope': 'registry-evidence-publication-only'}
        assert binding['execution_mode'] == binding['signer']['authority'] == 'controlled-test'
        # This private record is test-only plumbing to exercise the downstream
        # real tools. The controlled markers still prohibit proposal authority.
        capability = object.__new__(publication.PublishedMission)
        publication._PUBLISHED[capability] = {'pid': os.getpid(),
            'expires_monotonic': time.monotonic() + 600, 'binding': binding}
        handed_off.append(capability)
        # Only the test call's decision changes; retained sealed bytes stay unknown.
        return {**result, 'decision': 'pass'}, capability

    monkeypatch.setattr(operation, 'qualify_sign_and_publish', controlled_internal_handoff)
    report, capability = operation.qualify_publish_and_propose(**arguments)
    assert len(calls) == len(handed_off) == 1
    assert handed_off[0].describe()['execution_mode'] == 'controlled-test'
    aggregate, _, _ = actual_publication(arguments, case)
    assert report['publication_operation_sha256'] == aggregate['publication_operation_sha256']
    assert capability is None and report['decision'] == report['factory_decision'] == 'unknown'
    assert report['checks'] == {'publication': 'pass', 'proposal': 'unknown'}
    assert report['gaps'] == ['deployment-proposal-incomplete-or-failed']
    assert 'binding' not in report and set(report['downstream'].values()) == {'not-performed'}
    root = arguments['output'] / 'proposal'
    rendered = json.loads((root / 'deployment-proposal.json').read_bytes())
    assert rendered['decision'] == rendered['factory_decision'] == 'unknown'
    assert rendered['gaps'] == ['controlled-publication-cannot-issue-deployment-proposal-authority']
    assert set(rendered['checks'].values()) == {'pass'}
    assert rendered['rendered_changes'] == [{'resource': 'apps/Deployment/mission/mission',
        'pointer': '/spec/template/spec/containers/0/image'}]
    assert rendered['image']['repository'] == case['binding']['origin'][8:] + '/' + case['binding']['repository']
    assert rendered['image']['digest'] == case['binding']['manifest_digest']
    after = json.loads((root / 'rendered-after.json').read_bytes())['resources']
    expected_image = rendered['image']['repository'] + '@' + case['binding']['manifest_digest']
    assert after['apps/Deployment/mission/mission']['spec']['template']['spec']['containers'][0]['image'] == expected_image
    assert report['proposal_sha256'] == rendered['proposal_sha256']
    verify_seal(rendered, 'proposal_sha256')
    verify_seal(report, 'proposal_operation_sha256')
    assert values.read_bytes() == before and git(repo, 'rev-parse', 'HEAD') == selected['deployment_commit']
    assert git(repo, 'status', '--porcelain') == ''
    replica = tmp_path / 'independent-patch-application'
    shutil.copytree(repo, replica)
    git(replica, 'apply', '--check', str(root / 'proposal.patch'))
    git(replica, 'apply', str(root / 'proposal.patch'))
    assert (replica / selected['values_path']).read_bytes() == (root / 'values-after.yaml').read_bytes()
    assert git(replica, 'diff', '--name-only') == selected['values_path']
