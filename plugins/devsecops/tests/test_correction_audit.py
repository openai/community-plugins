"""Independent regressions for reproduced false passes in correction validation."""
import hashlib
import json
from pathlib import Path

import pytest

from devsecops.correction import validate
import devsecops.correction as correction_module
from test_correction import POLICY, correction as _source_fixture, git, patch_text, require_tool


@pytest.fixture
def correction_case(tmp_path):
    return _source_fixture.__wrapped__(tmp_path)


def test_shared_values_cannot_hide_other_target_changes_by_narrowing_profile(correction_case):
    args, _ = correction_case
    require_tool(args)
    if not Path(args.helm).is_file():
        pytest.skip('Real approved Helm is required')
    chart = args.repo / 'deploy'
    (chart / 'templates').mkdir()
    (chart / 'policy.yaml').unlink()
    template = POLICY.replace('namespace: demo', 'namespace: {{ .Release.Namespace }}')
    template = template.replace('port: 8080', 'port: {{ .Values.metricsPort }}')
    (chart / 'templates/policy.yaml').write_text(template)
    (chart / 'Chart.yaml').write_text('apiVersion: v2\nname: mission\nversion: 0.1.0\n')
    (chart / 'values.yaml').write_text('metricsPort: 8080\n')
    git(args.repo, 'add', '--all')
    git(args.repo, 'commit', '-qm', 'Two targets consume the same editable values')
    args.base = git(args.repo, 'rev-parse', 'HEAD')
    profile = json.loads(args.profile.read_text())
    profile['targets'][0].update(type='helm', release='mission')
    profile['targets'].append(dict(profile['targets'][0], name='other', namespace='other'))
    args.profile.write_text(json.dumps(profile))
    contract = json.loads(args.contract.read_text())
    contract.update(base_commit=args.base, profile_sha256=hashlib.sha256(args.profile.read_bytes()).hexdigest(),
                    allowed_files=['deploy/values.yaml'])
    args.contract.write_text(json.dumps(contract))
    args.patch.write_text(patch_text('metricsPort: 8080\n', 'metricsPort: 9090\n', 'deploy/values.yaml'))

    # The second target changes outside the contracted resource. Supplying an
    # unbound CLI subset must not convert this exact patch/profile into a pass.
    complete, _, _ = validate(args)
    assert complete['decision'] == 'fail'
    assert any(item['target'] == 'other' and item['decision'] == 'fail' for item in complete['scope_receipts'])
    args.target = ['mission', 'other']
    explicit, _, _ = validate(args)
    assert explicit['decision'] == 'fail'
    assert explicit['contract_sha256'] == complete['contract_sha256']
    assert explicit['patch_sha256'] == complete['patch_sha256']
    args.target = ['mission']
    with pytest.raises(ValueError):
        validate(args)


@pytest.mark.parametrize(('attribute', 'line_ending'), [
    ('ident', '\n'),
    ('text eol=crlf', '\n'),
    ('text eol=lf', '\r\n'),
    ('working-tree-encoding=UTF-16LE', '\n'),
    ('working-tree-encoding=ISO-8859-1', '\n'),
])
def test_candidate_git_attributes_cannot_invent_a_semantic_correction(
        correction_case, monkeypatch, attribute, line_ending):
    args, _ = correction_case
    require_tool(args)
    manifest = ('apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: identity\n  namespace: demo\n'
                'data:\n  source: "$Id: original $"\n  label: café\n').replace('\n', line_ending)
    (args.repo / 'deploy/policy.yaml').write_bytes(manifest.encode())
    git(args.repo, 'add', '--all')
    git(args.repo, 'commit', '-qm', 'Commit literal content before candidate attributes')
    (args.repo / 'deploy/.gitattributes').write_text('*.yaml '+attribute+'\n')
    git(args.repo, 'add', '--', 'deploy/.gitattributes')
    git(args.repo, 'commit', '-qm', 'Candidate requests built-in Git normalization')
    args.base = git(args.repo, 'rev-parse', 'HEAD')
    assert '$Id: original $' in git(args.repo, 'show', args.base+':deploy/policy.yaml')
    contract = json.loads(args.contract.read_text())
    contract.update(base_commit=args.base, allowed_rendered_changes=[{
        'resource': 'core/ConfigMap/demo/identity', 'pointers': ['/data']}])
    args.contract.write_text(json.dumps(contract))
    expected = '# comment only\n'+manifest
    args.patch.write_bytes(patch_text(manifest, expected).encode())

    # Even a line-ending transformation with no YAML semantic change violates
    # the frozen patch boundary. Inspect the isolated candidate's exact bytes.
    freeze_worktree = correction_module.freeze_worktree
    captured = {}
    def capture_candidate(repo, roots, destination):
        captured['bytes'] = (repo / 'deploy/policy.yaml').read_bytes()
        return freeze_worktree(repo, roots, destination)
    monkeypatch.setattr(correction_module, 'freeze_worktree', capture_candidate)

    result, review, actual_patch = validate(args)
    assert review['decision'] == 'pass'
    assert result['decision'] == 'fail' and result['has_rendered_change'] is False
    assert actual_patch == args.patch.read_bytes()
    assert review['targets'][0]['inputs']['base']['sha256'] == result['base_tree']['sha256']
    assert result['scope_receipts'][0]['changes'] == []
    assert captured['bytes'] == expected.encode()
