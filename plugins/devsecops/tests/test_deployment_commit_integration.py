"""Actual controlled publication and Helm validation hand off to native Git."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from devsecops.factory import deployment_commit as prepared
from devsecops.factory import deployment_proposal as proposal
from test_deployment_proposal import case as rendered_case, published, tools, scanned, lab, ca

__all__ = ['published', 'tools', 'scanned', 'lab', 'ca']


def test_actual_published_proposal_prepares_native_pack_without_delivery_authority(tmp_path, published, tools):
    original = rendered_case.__wrapped__(tmp_path, published, tools)
    reviewed, authority = proposal.propose_deployment_change(**original['args'])
    assert authority is None and set(reviewed['checks'].values()) == {'pass'}
    root = original['args']['output']
    selected = original['config']
    artifacts = reviewed['artifacts']
    # Test-only typed record from this direct controlled execution, never an
    # imported report or a production issuer. The signer remains controlled.
    binding = {**{key: original['args'][key] for key in ('run_scope', 'source_commit', 'origin', 'repository')},
        'manifest_digest': reviewed['image']['digest'], 'deployment_repository': selected['deployment_repository'],
        'deployment_commit': selected['deployment_commit'], 'configuration_sha256': original['args']['expected_config_sha256'],
        'values_path': selected['values_path'], 'patch': {'path': str(root/'proposal.patch'), **artifacts['proposal.patch']},
        'before': artifacts['values-before.yaml'], 'after': artifacts['values-after.yaml'],
        'rendered_before': artifacts['rendered-before.json'], 'rendered_after': artifacts['rendered-after.json'],
        'published_evidence': deepcopy(reviewed['published_evidence']), 'change_required': reviewed['change_required'],
        'expires_at': original['binding']['expires_at']}
    controlled = object.__new__(proposal.DeploymentProposal)
    proposal._PROPOSALS[controlled] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic()+300, 'binding': binding}
    binary = Path(shutil.which('git')).resolve()
    config = {'schema_version': 1, 'kind': 'mission-local-deployment-commit-configuration',
        'deployment_repository': selected['deployment_repository'], 'deployment_commit': selected['deployment_commit'],
        'values_path': selected['values_path'], 'proposal_configuration_sha256': binding['configuration_sha256'],
        'git_path': str(binary), 'git_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'author_name': 'Controlled Integration', 'author_email': 'integration@example.invalid'}
    config_path = tmp_path/'local-commit-config.json'
    config_path.write_text(json.dumps(config))
    report, cap = prepared.prepare_deployment_commit(proposal=controlled,
        **{name: binding[name] for name in ('run_scope', 'source_commit', 'origin', 'repository',
            'manifest_digest', 'deployment_repository', 'deployment_commit')},
        proposal_configuration_sha256=binding['configuration_sha256'], config=config_path,
        expected_config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(), output=tmp_path/'prepared-commit')
    assert cap is None and report['decision'] == 'unknown'
    assert set(report['checks'].values()) == {'pass'}, report
    assert report['gaps'] == ['controlled-proposal-cannot-issue-prepared-commit-authority']
    assert report['prepared']['parent'] == selected['deployment_commit']
    assert report['prepared']['values_path'] == selected['values_path']
    assert set(report['downstream'].values()) == {'not-performed'}
