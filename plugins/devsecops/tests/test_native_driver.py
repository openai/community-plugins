"""Canonical resident caller with controlled native and operational boundaries.

Real journals, fixed report replay and correction validation execute. This is
component coverage, never a live E2E repetition or recording qualification.
"""
from dataclasses import asdict
import json
import signal

import pytest

from test_factory_cli import arguments
from test_native_factory_session import (pending, correction_inputs, correction_case,
    installed, recovery_scope, controlled_native, controlled_dispatch, execute_report)
from test_native_reports import bind
from devsecops.factory import native_driver as module
from devsecops.factory.cli import main
from devsecops.factory.state import StateError, _canonical, _sha

__all__ = ['pending', 'correction_inputs', 'correction_case', 'installed', 'recovery_scope',
           'controlled_native', 'controlled_dispatch']


def attach(pending, controlled_native, controlled_dispatch, installed):
    _, engine, driver, scopes, args = pending
    controlled_dispatch['patch'] = args.patch.read_bytes()
    sessions = []
    driver.bind_native_session = sessions.append
    def scope(*, step_id):
        active = engine.journal.state['active_native']
        assert active['step_id'] == step_id
        selected = scopes[active['workflow']]
        if not step_id.endswith('correction'):
            bind(controlled_native, selected, installed)
        return selected
    driver.native_scope = scope
    return engine, driver, sessions


def test_canonical_resident_caller_services_all_eight_handoffs(pending, controlled_native, controlled_dispatch, installed):
    engine, driver, sessions = attach(pending, controlled_native, controlled_dispatch, installed)
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    result = module.run(engine, selection=installed)
    assert result['terminal'] and result['exit_code'] == 2
    assert not result['canonical_success'] and engine.mode == 'controlled-test'
    assert controlled_native['calls'] == 6 and controlled_dispatch['calls'] == 2
    assert len(sessions) == 1 and not sessions[0].cancelled
    identity = sessions[0].terminal_native_identity()
    assert identity['model'] == installed.model and identity['provider_observed_model'] is None
    assert identity['client_sha256'] == installed.expected_client_sha256
    assert driver.calls.count('cleanup') == 1
    assert [row['step_id'] for row in engine.journal.state['native_results']] == list(module._STEPS)
    assert previous == {number: signal.getsignal(number) for number in previous}
    assert module.run(engine, selection=installed) == result  # Terminal readback does not execute again.
    assert driver.calls.count('cleanup') == 1


def test_native_failure_runs_cleanup_once_and_preserves_incomplete_execution(pending, controlled_native, controlled_dispatch, installed):
    engine, driver, sessions = attach(pending, controlled_native, controlled_dispatch, installed)
    controlled_native['result_change'] = lambda result: result.update(status='incomplete', gaps=['controlled-gap'])
    active = engine.journal.state['active_native']
    result = module.run(engine, selection=installed)
    assert result['exit_code'] == 2 and result['native_steps_completed_in_process'] == 0
    assert driver.calls.count('cleanup') == 1 and 'primary-deliver' not in driver.calls
    assert sessions[0].cancelled and controlled_native['calls'] == 1
    path = engine.journal.run_dir/('native-report-'+active['request']['sha256'])/'execution/native-result.json'
    assert json.loads(path.read_bytes())['gaps'] == ['controlled-gap']


def test_binding_failure_still_uses_authorized_cleanup(pending, controlled_native, controlled_dispatch, installed):
    engine, driver, _ = attach(pending, controlled_native, controlled_dispatch, installed)
    def fail(_):
        raise StateError('controlled fixed binding failure')
    driver.bind_native_session = fail
    result = module.run(engine, selection=installed)
    assert result['exit_code'] == 2 and driver.calls.count('cleanup') == 1
    assert controlled_native['calls'] == 0 and controlled_dispatch['calls'] == 0


def test_accepted_history_cannot_restore_resident_authority(pending, controlled_native, installed):
    _, engine, driver, _, _ = pending
    execute_report(pending, controlled_native, installed)
    before = list(driver.calls)
    with pytest.raises(StateError, match='original process'):
        module.run(engine, selection=installed)
    assert driver.calls == before and controlled_native['calls'] == 1


def document(installed):
    value = asdict(installed)
    value.pop('package_sha256')
    for key in ('installed_root', 'executable', 'engine_python'):
        value[key] = str(value[key])
    value.update(schema_version=1, kind='canonical-native-driver-selection',
                 run_scope={'package_sha256': installed.package_sha256}, environment_id='owned-test',
                 plugin_version='test-version')
    return value


def selection_arguments(installed):
    return dict(identities={'package_sha256': installed.package_sha256}, environment_id='owned-test',
                plugin_id=installed.plugin_id, plugin_version='test-version')


def test_selection_binds_exact_explicit_client_package_and_model(installed, tmp_path, monkeypatch):
    value = document(installed)
    path = tmp_path/'selection.json'
    path.write_bytes(_canonical(value))
    config = {'tool_paths': {'codex': str(installed.executable)}}
    lock = {'tools': [{'name': 'codex', 'version': installed.expected_client_version,
                      'sha256': installed.expected_client_sha256}]}
    monkeypatch.setattr(module, 'PACKAGE_ROOT', installed.installed_root)
    args = dict(**selection_arguments(installed), config=config, lock=lock,
                package_manifest=installed.installed_root/'SHA256SUMS')
    assert module.load_selection(path, _sha(path.read_bytes()), **args) == installed
    lock['tools'][0]['sha256'] = '0'*64
    with pytest.raises(StateError, match='locked client'):
        module.load_selection(path, _sha(path.read_bytes()), **args)


@pytest.mark.parametrize('field,value', [('schema_version', True), ('kind', 'different'),
    ('environment_id', 'other'), ('plugin_version', 'other'), ('model', 'arbitrary model'),
    ('executable', '/tmp/../client'), ('installed_root', 'relative'), ('total_timeout', True),
    ('max_trace_bytes', 16_000_001), ('extra', 'not-supported')])
def test_selection_rejects_unbound_or_ambiguous_fields(installed, field, value):
    doc = document(installed)
    doc[field] = value
    with pytest.raises((StateError, ValueError)):
        module.validate_selection(_canonical(doc), **selection_arguments(installed))


@pytest.mark.parametrize('extra', [
    ['--driver', 'codex-cli'],
    ['--native-selection', '/tmp/not-opened'],
    ['--driver', 'codex-cli', '--operation-backend', 'owned-linux', '--plugin-id', 'id', '--plugin-version', 'v'],
    ['--driver', 'codex-cli', '--operation-backend', 'owned-linux', '--plugin-id', 'id', '--plugin-version', 'v',
     '--native-selection', '/tmp/not-opened'],
])
def test_cli_rejects_incomplete_or_wrong_driver_selection_before_journal(tmp_path, capsys, extra):
    run = tmp_path/'not-created'
    assert main([*arguments(tmp_path), '--output', str(run), *extra]) == 2
    assert not run.exists() and not capsys.readouterr().out
