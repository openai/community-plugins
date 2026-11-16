"""Recovery command integration preserves measured conditions and unknown origins."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from devsecops.cli import main
from devsecops.runtime.context import canonical_bytes
from devsecops.runtime.presentation import render_html, render_markdown
from test_runtime_cli import assert_manifest
from test_runtime_recovery import fixture, run


def command_inputs(directory):
    bundles, contract, evidence = fixture()
    args = ['verify-gitops-recovery']
    paths = {}
    for name, value in [('baseline', bundles['baseline']), ('fault', bundles['fault']),
                        ('recovered', bundles['recovery']), ('contract', contract)]:
        path = directory / (name + '.json')
        path.write_bytes(canonical_bytes(value))
        paths[name] = path
        args += ['--' + name, str(path)]
    for index, (name, value) in enumerate(evidence.items()):
        path = directory / ('evidence-' + str(index))
        path.write_bytes(value if isinstance(value, bytes) else canonical_bytes(value))
        args += ['--evidence', name + '=' + str(path)]
    return args, paths


def test_real_recovery_command_exports_conditions_private_manifest_and_raw_hashes(tmp_path, capsys, monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'socket', lambda *a, **k: pytest.fail('Offline recovery may not connect'))
    args, paths = command_inputs(tmp_path)
    output = tmp_path / 'new recovery with spaces'
    assert main([*args, '--output', str(output)]) == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary['recovery'] == summary['decision'] == 'unknown'
    assert summary['execution_mode'] == 'controlled-test'
    report = json.loads((output / 'recovery.json').read_bytes())
    assert report['kind'] == 'runtime-recovery' and report['conditions']
    assert not report['provenance']['verified']
    assert any(item['observed_state'] == 'recovered' for item in report['conditions'])
    manifest = assert_manifest(output)
    assert manifest['command'] == 'verify-gitops-recovery'
    assert manifest['inputs']['baseline']['sha256'] == hashlib.sha256(paths['baseline'].read_bytes()).hexdigest()
    html = (output / 'report.html').read_text()
    assert 'RECOVERY UNKNOWN' in html and 'Recovery conditions' in html and 'Source verification' in html
    assert 'Source origin' in html and 'Controlled test evidence' in html
    assert 'Functional recovery</span>' in html and 'Not evaluated</strong>' not in html
    assert all('sensitive-value-must-not-escape' not in path.read_text() for path in output.iterdir())


@pytest.mark.parametrize('evidence', [['patch=missing', 'patch=other'], ['arbitrary=missing'],
                                    ['http:../escape=missing'], ['patch='], ['patch=missing'] * 45])
def test_invalid_evidence_names_or_duplicates_fail_before_input_reads(tmp_path, capsys, monkeypatch, evidence):
    monkeypatch.setattr('devsecops.runtime.recovery.verify_recovery', lambda *a, **k: pytest.fail('Invalid mapping may not load inputs'))
    args = ['verify-gitops-recovery', '--baseline', 'missing', '--fault', 'missing',
            '--recovered', 'missing', '--contract', 'missing', '--output', str(tmp_path / 'output')]
    for value in evidence:
        args += ['--evidence', value]
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)['execution_status'] == 'error'
    assert_manifest(tmp_path / 'output')


def test_unreadable_and_omitted_independent_inputs_stay_explicit_gaps(tmp_path, capsys):
    args, paths = command_inputs(tmp_path)
    first_evidence = args.index('--evidence')
    args = args[:first_evidence]
    paths['recovered'].unlink()
    output = tmp_path / 'missing'
    assert main([*args, '--output', str(output)]) == 2
    capsys.readouterr()
    report = json.loads((output / 'recovery.json').read_bytes())
    assert report['inputs']['recovery']['state'] == 'invalid'
    assert report['inputs']['inventory']['state'] == 'missing'
    assert report['recovery'] == 'unknown' and report['execution_status'] == 'partial'
    assert any(item['observed_state'] == 'unknown' for item in report['conditions'])


def test_wrapper_runs_recovery_from_an_unrelated_working_directory(tmp_path):
    args, _ = command_inputs(tmp_path)
    root = Path(__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    result = subprocess.run([sys.executable, str(root / 'scripts/devsecops_review.py'), *args,
                             '--output', str(tmp_path / 'wrapper output')],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)['recovery'] == 'unknown'


def test_presentation_escapes_observations_and_keeps_positive_conditions_separate_from_recovery():
    result = run(fixture(), auth=False)
    assert all(item['observed_state'] == 'recovered' for item in result['conditions'])
    modified = deepcopy(result)
    modified['conditions'][0]['target_id'] = '<script>alert("unsafe")</script>'
    html = render_html(modified, 'recovery.json')
    markdown = render_markdown(modified, 'recovery.json')
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'RECOVERY UNKNOWN' in html and 'Observed match' in html and 'Unverified' in html
    assert 'Source verification' in html and 'more observed matches</summary>' in html
    assert '**Recovery: unknown.**' in markdown and 'Causality remains unproven.' in markdown
