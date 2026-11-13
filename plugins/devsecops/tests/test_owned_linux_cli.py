"""Registered backend over real loopback TLS; no live lab or canonical authority."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import zipfile

import pytest

from devsecops.factory import cli, owned_linux
from devsecops.factory.lifecycle import LifecycleEngine, SCENARIO_ROOT
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha
from tests.test_owned_linux import ca, server, setup, PRIVATE, READ_TOKEN, OPERATOR_TOKEN  # noqa: F401

__all__ = ['setup']


def selections(case, tmp_path):
    # Explicit controlled cleanup authorization satisfies the existing canonical
    # preflight contract; the fixed read-only handler still performs no cleanup.
    case['values']['config']['allowed_mutations'] = ['cleanup']
    inputs = {name: SCENARIO_ROOT / (name + '.json') for name in ('scenario', 'assertions')}
    for name in ('config', 'lock', 'environment'):
        path = tmp_path / (name + '.json')
        path.write_bytes(_canonical(case['values'][name]))
        inputs[name] = path
    inputs['package'] = case['package'] / 'SHA256SUMS'
    identities = {name + '_sha256': _sha(path.read_bytes()) for name, path in inputs.items()}
    args = ['e2e', '--profile', 'bigbang-live', '--config', str(inputs['config']),
        '--lock', str(inputs['lock']), '--environment', case['values']['config']['environment'],
        '--environment-inventory', str(inputs['environment']), '--package-manifest', str(inputs['package']),
        '--shared-lock-dir', str(tmp_path / 'locks'), '--operation-backend', 'owned-linux',
        '--plugin-id', 'devsecops@devsecops-local', '--plugin-version', '0.1.0']
    return inputs, identities, args


def contents(directory):
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob('*') if path.is_file()}


def incomplete(run, report):
    assert report['exit_code'] == 2 and report['execution_status'] == 'incomplete'
    assert report['scenario_assertion_status'] == 'unknown' and not report['canonical_success']
    assert report['execution_mode'] == 'operator' and not report['terminal'] and not report['cleanup_required']
    journal = RunJournal.open(run, identities=report['identities'])
    state = journal.state
    assert not state['environment_reserved'] and state['operations'] == []
    assert state['active_mutation'] is None and state['active_native'] is None
    assert state['lifecycle']['driver_id'] == 'owned-linux'
    phase, = state['lifecycle']['phase_records']
    assert phase['phase_id'] == 'preflight' and phase['outcome'] == 'incomplete'
    assert phase['code'] == 'owned-linux-preflight-incomplete' and phase['assertions'] == []
    path, = run.glob('owned-linux-*/diagnostic.json')
    diagnostic = json.loads(path.read_bytes())
    assert diagnostic['canonical_success'] is False and diagnostic['created_resources'] == []
    assert diagnostic['external_mutations_performed'] is False and diagnostic['cleanup'] == 'not-performed'
    assert not (run.parent / 'locks').exists()
    return diagnostic


def exact_requests(case):
    requests = case['server']['requests']
    assert [row['path'] for row in requests] == [
        '/api/version', '/api/v4/projects/7', '/version', '/readyz',
        '/api/v1/namespaces/mission-demo', '/api/v1/status/buildinfo']
    assert all(row['authorization'] == 'Bearer ' + READ_TOKEN for row in requests)


def test_registered_lifecycle_uses_fixed_handler_and_real_tls_without_an_injected_driver(setup, tmp_path):
    inputs, identities, _ = selections(setup, tmp_path)
    run = tmp_path / 'registered-run'
    journal = RunJournal.create(run, identities=identities,
        environment_id=setup['values']['config']['environment'])
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path / 'locks',
        driver_id='owned-linux', plugin_id='devsecops@devsecops-local', plugin_version='0.1.0')
    assert type(engine.driver) is owned_linux.OwnedLinuxDriver and engine.mode == 'operator'
    report = engine.advance()
    diagnostic = incomplete(run, report)
    exact_requests(setup)
    assert diagnostic['package']['content_verified']
    assert all(row['authentication'] == row['read_grants'] == 'unverified' for row in diagnostic['services'])


def test_cli_preflight_is_incomplete_and_resume_never_retries_service_reads(setup, tmp_path, capsys):
    _, _, args = selections(setup, tmp_path)
    run = tmp_path / 'cli-run'
    assert cli.main([*args, '--output', str(run)]) == 2
    captured = capsys.readouterr()
    assert not captured.err
    report = json.loads(captured.out)
    assert report['operation_backend'] == 'owned-linux'
    incomplete(run, report)
    exact_requests(setup)
    before = contents(run)
    assert cli.main([*args, '--resume', str(run)]) == 2
    resumed = capsys.readouterr()
    assert not resumed.err and json.loads(resumed.out) == report
    assert contents(run) == before
    exact_requests(setup)
    assert all(value not in captured.out + resumed.out for value in (PRIVATE, READ_TOKEN, OPERATOR_TOKEN))


@pytest.mark.parametrize('option,value', [
    ('--plugin-id', 'different@devsecops-local'), ('--plugin-version', '9.9.9'),
])
def test_cli_mismatched_installed_identity_never_contacts_services(setup, tmp_path, capsys, option, value):
    _, _, args = selections(setup, tmp_path)
    args[args.index(option) + 1] = value
    run = tmp_path / 'wrong-installation'
    assert cli.main([*args, '--output', str(run)]) == 2
    captured = capsys.readouterr()
    assert not captured.err
    diagnostic = incomplete(run, json.loads(captured.out))
    assert diagnostic['preflight_stage'] == 'package-validation' and diagnostic['services'] == []
    assert 'preflight-input-package-or-budget-unavailable' in diagnostic['gaps']
    assert setup['server']['requests'] == []


def test_cli_resume_cannot_switch_the_registered_operation_backend(setup, tmp_path, capsys):
    _, _, args = selections(setup, tmp_path)
    run = tmp_path / 'bound-backend'
    assert cli.main([*args, '--output', str(run)]) == 2
    capsys.readouterr()
    before = contents(run)
    for flag in ('--plugin-id', '--plugin-version'):
        position = args.index(flag)
        del args[position:position + 2]
    args[args.index('--operation-backend') + 1] = 'unavailable'
    assert cli.main([*args, '--resume', str(run)]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)['message'] == 'Lifecycle driver or execution mode changed'
    assert contents(run) == before
    exact_requests(setup)


def test_registered_backend_rejects_injected_driver_before_journal_initialization(setup, tmp_path):
    inputs, identities, _ = selections(setup, tmp_path)
    journal = RunJournal.create(tmp_path / 'no-injection', identities=identities,
        environment_id=setup['values']['config']['environment'])
    before = contents(journal.run_dir)
    with pytest.raises(StateError, match='cannot accept an injected driver'):
        LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path / 'locks',
            driver_id='owned-linux', plugin_id='devsecops@devsecops-local', plugin_version='0.1.0',
            _test_driver=object())
    assert contents(journal.run_dir) == before and setup['server']['requests'] == []


def test_registered_phase_alarm_bounds_a_blocking_local_inspection(setup, tmp_path, monkeypatch):
    inputs, identities, _ = selections(setup, tmp_path)
    journal = RunJournal.create(tmp_path / 'bounded-inspection', identities=identities,
        environment_id=setup['values']['config']['environment'])
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path / 'locks',
        driver_id='owned-linux', plugin_id='devsecops@devsecops-local', plugin_version='0.1.0')
    entered = []
    def blocked(*args, **kwargs):
        entered.append(True)
        time.sleep(1)
        raise AssertionError('The registered phase alarm must interrupt local inspection')
    monkeypatch.setattr(owned_linux, 'inspect', blocked)
    monkeypatch.setattr(owned_linux, 'MAX_TIMEOUT', .15)
    started = time.monotonic()
    report = engine.advance()
    assert time.monotonic() - started < .75 and entered == [True]
    assert report['exit_code'] == 2 and not report['canonical_success']
    phase, = journal.state['lifecycle']['phase_records']
    assert phase['outcome'] == 'error' and phase['code'] == 'phase-timeout'
    assert not journal.state['environment_reserved'] and journal.state['operations'] == []
    assert setup['server']['requests'] == [] and not list(journal.run_dir.rglob('diagnostic.json'))


@pytest.mark.parametrize('wrapper_origin', ['installed-cache', 'source-checkout'])
def test_real_cli_package_entrypoint_uses_only_selected_installation(setup, tmp_path, wrapper_origin):
    from scripts import build_plugin
    _, _, args = selections(setup, tmp_path)
    extracted = tmp_path / 'extracted'
    # The release builder deliberately refuses archives inside its source tree,
    # including when the caller keeps pytest's retained basetemp under work/.
    with tempfile.TemporaryDirectory(prefix='devsecops-owned-cli-package-') as directory:
        archive = Path(directory) / 'plugin.zip'
        package_info = build_plugin.build(archive)
        with zipfile.ZipFile(archive) as package:
            package.extractall(extracted)
    home = tmp_path / 'selected codex home'
    installed = home / 'plugins/cache/devsecops-local/devsecops' / package_info['version']
    installed.parent.mkdir(parents=True)
    (extracted / 'devsecops').rename(installed)
    args[args.index('--package-manifest') + 1] = str(installed / 'SHA256SUMS')
    args[args.index('--plugin-version') + 1] = package_info['version']
    root = installed if wrapper_origin == 'installed-cache' else Path(__file__).resolve().parents[1]
    wrapper = root / 'scripts/factory_demo.py'
    env = {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME')}
    env.update(CODEX_HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
    run = tmp_path / 'isolated-cli-run'
    child = subprocess.run([sys.executable, '-I', '-B', str(wrapper), *args, '--output', str(run)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=45)
    assert child.returncode == 2 and not child.stderr, child.stderr
    report = json.loads(child.stdout)
    diagnostic = incomplete(run, report)
    if wrapper_origin == 'installed-cache':
        assert diagnostic['package']['content_verified'] and diagnostic['package']['sha256'] == _sha(
            (installed / 'SHA256SUMS').read_bytes())
        exact_requests(setup)
        before = contents(run)
        resumed = subprocess.run([sys.executable, '-I', '-B', str(wrapper), *args, '--resume', str(run)],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=45)
        assert resumed.returncode == 2 and not resumed.stderr and json.loads(resumed.stdout) == report
        assert contents(run) == before
        exact_requests(setup)
    else:
        assert diagnostic['preflight_stage'] == 'package-validation' and diagnostic['services'] == []
        assert 'preflight-input-package-or-budget-unavailable' in diagnostic['gaps']
        assert setup['server']['requests'] == []
    assert all(value not in child.stdout + child.stderr for value in (PRIVATE, READ_TOKEN, OPERATOR_TOKEN))
