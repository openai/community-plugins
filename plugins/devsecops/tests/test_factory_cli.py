"""Real command surface; unavailable operations never fabricate live success."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from devsecops.factory.cli import main
from devsecops.factory.state import RunJournal
from tests.test_factory_lifecycle import inputs_for


def arguments(tmp_path):
    inputs, _ = inputs_for(tmp_path)
    for key in ('config', 'lock', 'environment'):
        path = tmp_path / (key + '.json')
        path.write_text(json.dumps(inputs[key]))
        inputs[key] = path
    return ['e2e', '--profile', 'bigbang-live', '--config', str(inputs['config']),
            '--lock', str(inputs['lock']), '--environment', json.loads(inputs['config'].read_text())['environment'],
            '--environment-inventory', str(inputs['environment']), '--package-manifest', str(inputs['package']),
            '--shared-lock-dir', str(tmp_path / 'locks'), '--driver', 'current-host', '--cleanup', 'always']


def test_unavailable_backend_creates_bound_journal_without_reserving_environment(tmp_path, capsys):
    args = arguments(tmp_path)
    run = tmp_path / 'new run with spaces'
    assert main([*args, '--output', str(run)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['gaps'] == ['operation-driver-unavailable']
    assert not report['canonical_success'] and not report['terminal'] and not report['cleanup_required']
    assert report['completed_phase_count'] == 0 and report['native_driver'] == 'current-host'
    journal = RunJournal.open(run, identities=report['identities'])
    assert not journal.state['environment_reserved'] and not journal.state['operations']
    assert not (tmp_path / 'locks').exists()
    assert run.stat().st_mode & 0o777 == 0o700
    assert main([*args, '--resume', str(run)]) == 2
    resumed = json.loads(capsys.readouterr().out)
    assert resumed['identities'] == report['identities'] and resumed['run_id'] == report['run_id']


@pytest.mark.parametrize('changed', ['config', 'lock', 'environment', 'package'])
def test_resume_rejects_changed_explicit_bytes_and_leaves_journal_unchanged(tmp_path, capsys, changed):
    args = arguments(tmp_path)
    run = tmp_path / 'run'
    assert main([*args, '--output', str(run)]) == 2
    capsys.readouterr()
    before = {str(path.relative_to(run)): path.read_bytes() for path in run.rglob('*') if path.is_file()}
    flag = {'config': '--config', 'lock': '--lock', 'environment': '--environment-inventory',
            'package': '--package-manifest'}[changed]
    path = Path(args[args.index(flag) + 1])
    path.write_bytes(path.read_bytes() + b'\n')
    assert main([*args, '--resume', str(run)]) == 2
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err)['execution_status'] == 'error'
    after = {str(path.relative_to(run)): path.read_bytes() for path in run.rglob('*') if path.is_file()}
    assert before == after


@pytest.mark.parametrize('option,value', [('--profile', 'openshift-app'), ('--environment', 'different-environment')])
def test_mismatched_requested_scope_fails_before_creating_directory(tmp_path, capsys, option, value):
    args = arguments(tmp_path)
    args[args.index(option) + 1] = value
    run = tmp_path / 'run'
    assert main([*args, '--output', str(run)]) == 2
    assert not run.exists() and not capsys.readouterr().out


def test_wrapper_runs_same_engine_without_author_pythonpath(tmp_path):
    args = arguments(tmp_path)
    root = Path(__file__).resolve().parents[1]
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    result = subprocess.run([sys.executable, str(root / 'scripts/factory_demo.py'), *args,
                             '--output', str(tmp_path / 'wrapper run')],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stdout)['gaps'] == ['operation-driver-unavailable']
    assert not result.stderr


@pytest.mark.parametrize('selection', [
    ['--operation-backend', 'owned-linux'],
    ['--operation-backend', 'owned-linux', '--plugin-id', 'devsecops@devsecops-local'],
    ['--operation-backend', 'owned-linux', '--plugin-version', '0.2.0-alpha.6'],
    ['--plugin-id', 'devsecops@devsecops-local', '--plugin-version', '0.2.0-alpha.6'],
])
def test_backend_identity_selection_fails_before_journal_creation(tmp_path, capsys, selection):
    args = arguments(tmp_path)
    run = tmp_path / 'must-not-exist'
    assert main([*args, '--output', str(run), *selection]) == 2
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err)['execution_status'] == 'error'
    assert not run.exists() and not (tmp_path / 'locks').exists()
