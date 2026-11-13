"""State-machine fixtures only: no live platform, cluster, or success receipt."""
from datetime import timedelta
import json
import signal
import time
from pathlib import Path
from uuid import uuid4

import pytest

from devsecops.factory import lifecycle as module
from devsecops.factory.lifecycle import AssertionResult, LifecycleEngine, PhaseOutcome
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha
from tests.test_factory_contracts import config, factory_lock
from tests.test_factory_state import Clock, native_result


def inputs_for(tmp_path):
    configuration = config()
    configuration['allowed_mutations'] = ['acquire', 'provision', 'build', 'promote', 'inject',
                                           'deliver', 'probe', 'reset', 'cleanup']
    scenario = module.SCENARIO_ROOT / 'scenario.json'
    assertions = module.SCENARIO_ROOT / 'assertions.json'
    lock = factory_lock()
    lock.update(profile=configuration['profile'], scenario_sha256=_sha(scenario.read_bytes()),
                assertions_sha256=_sha(assertions.read_bytes()))
    package = tmp_path / 'SHA256SUMS'
    package.write_text('fixture content manifest: not independently verified\n')
    inputs = {'scenario': scenario, 'assertions': assertions, 'config': configuration,
              'lock': lock, 'package': package, 'environment': {'environment_id': configuration['environment'],
                                                               'fixture': True}}
    identities = {key + '_sha256': _sha(_canonical(value) if isinstance(value, dict) else value.read_bytes())
                  for key, value in inputs.items()}
    return inputs, identities


class FixtureDriver:
    def __init__(self):
        self.calls = []
        self.behaviors = {}
        self.cleanup_reject = False
        self.native_reject = False

    def execute(self, context):
        phase = context.phase
        self.calls.append(phase['id'])
        assert context.remaining_seconds() > 0
        if phase['id'] in self.behaviors:
            return self.behaviors[phase['id']](context)
        artifact = context.run_dir / ('fixture-' + uuid4().hex + '.json')
        artifact.write_text(json.dumps({'fixture_only': True, 'phase_id': phase['id']}))
        artifact.chmod(0o600)
        assertions = tuple(AssertionResult(case, check, 'pass') for case in phase['case_ids']
                           for check in context.inputs['assertions']['cases'][case])
        return PhaseOutcome('completed', 'fixture-observed', (artifact,), assertions, 'applied')

    def native_inputs(self, context):
        self.calls.append(context.phase['id'])
        return {}

    def validate_native(self, request, result, artifacts):
        if self.native_reject:
            raise ValueError('SECRET_SENTINEL')
        assert result['step_id'] == request['step_id']
        assert not artifacts or artifacts['proposal'] == b'unit fixture only\n'
        return ['fixture-native-bytes-checked']

    def validate_cleanup(self, receipts):
        if self.cleanup_reject:
            raise ValueError('SECRET_SENTINEL')
        assert json.loads(receipts[0])['fixture_only']
        return ['fixture-cleanup-observations-checked']


@pytest.fixture
def setup(tmp_path):
    inputs, identities = inputs_for(tmp_path)
    clock = Clock()
    journal = RunJournal.create(tmp_path / 'run', identities=identities,
                                environment_id=inputs['config']['environment'], clock=clock)
    driver = FixtureDriver()
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path / 'locks', _test_driver=driver)
    return engine, driver, inputs, clock


def finish_native(engine, result):
    path, _ = native_result(engine.journal, result['native_request'])
    return engine.advance(native_result=path)


def complete(engine):
    result = engine.advance()
    handoffs = 0
    while result['exit_code'] == 3:
        handoffs += 1
        assert handoffs <= 8
        result = finish_native(engine, result)
    return result, handoffs


def fail_read(context):
    path = context.run_dir / ('failure-' + uuid4().hex + '.json')
    path.write_text('{"fixture_only":true,"failure_observed":true}')
    return PhaseOutcome('assertion-failure', 'fixture-assertion-failed', (path,), mutation_effect='not_applied')


def crash(_):
    raise KeyboardInterrupt


def test_unavailable_handler_never_pretends_to_execute(tmp_path):
    inputs, identities = inputs_for(tmp_path)
    journal = RunJournal.create(tmp_path / 'run', identities=identities,
                                environment_id=inputs['config']['environment'])
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path / 'locks')
    result = engine.advance()
    assert result['exit_code'] == 2 and not result['terminal'] and not result['canonical_success']
    assert result['gaps'] == ['operation-driver-unavailable']
    assert result['original_failure'] is None
    assert not journal.state['environment_reserved']
    assert journal.state['operations'] == []
    assert journal.state['lifecycle']['final_result'] is None


def test_current_host_exit3_preserves_environment_and_does_not_block(setup):
    engine, driver, _, _ = setup
    result = engine.advance()
    assert result['exit_code'] == 3 and not result['terminal']
    assert result['execution_status'] == 'awaiting_native_step'
    assert result['execution_mode'] == 'controlled-test'
    assert engine.journal.state['environment_reserved']
    assert 'cleanup' not in driver.calls
    request = json.loads(Path(result['native_request']['request_path']).read_text())
    assert request['step_id'] == 'primary-review'
    assert request['identities'] == engine.journal.identities
    calls = list(driver.calls)
    again = engine.advance()
    assert again['native_request']['request_sha256'] == result['native_request']['request_sha256']
    assert driver.calls == calls


def test_exactly_one_replay_and_controlled_driver_can_never_claim_live_pass(setup):
    engine, driver, _, _ = setup
    result, handoffs = complete(engine)
    assert handoffs == 8
    assert result['terminal'] and result['exit_code'] == 2
    assert result['scenario_assertion_status'] == 'unknown' and not result['canonical_success']
    assert result['missing_assertions'] == []
    assert driver.calls == [p['id'] for p in engine.docs['scenario']['phases']]
    assert driver.calls.count('reset') == 1
    assert len(engine.journal.state['lifecycle']['phase_records']) == 33
    assert engine.journal.state['execution_status'] == 'released'
    calls = list(driver.calls)
    assert engine.advance() == result
    assert driver.calls == calls
    with pytest.raises(StateError, match='Finalized'):
        engine.advance(native_result=engine.journal.run_dir / 'forged-result.json')


@pytest.mark.parametrize('name', ['scenario', 'assertions', 'config', 'lock', 'package', 'environment'])
def test_all_six_explicit_inputs_revalidated_before_resume_reads_or_effects(setup, name):
    engine, driver, _, _ = setup
    engine.advance()
    calls = list(driver.calls)
    if isinstance(engine.input_sources[name], dict):
        engine.input_sources[name]['unexpected'] = True
    else:
        path = engine.journal.run_dir / ('changed-' + name)
        path.write_bytes(engine.input_sources[name].read_bytes() + b' ')
        engine.input_sources[name] = path
    with pytest.raises(StateError, match='identity changed'):
        engine.advance()
    assert driver.calls == calls


def test_resume_must_preserve_controlled_mode(setup, tmp_path):
    engine, _, inputs, _ = setup
    engine.advance()
    with pytest.raises(StateError, match='execution mode changed'):
        LifecycleEngine(engine.journal, inputs=inputs, shared_lock_dir=tmp_path / 'locks')


def test_json_cannot_select_executable_driver(setup, tmp_path):
    engine, _, inputs, _ = setup
    with pytest.raises(StateError, match='No production'):
        LifecycleEngine(engine.journal, inputs=inputs, shared_lock_dir=tmp_path / 'locks', driver_id='evil.module:run')


@pytest.mark.parametrize('which', ['scenario', 'assertions'])
def test_schema_valid_but_modified_canonical_contract_rejected(tmp_path, which):
    inputs, identities = inputs_for(tmp_path)
    doc = json.loads(inputs[which].read_bytes())
    if which == 'scenario':
        doc['phases'][0]['timeout_seconds'] = 301
    else:
        doc['cases']['D1'].pop()
    inputs[which] = doc
    identities[which + '_sha256'] = _sha(_canonical(doc))
    inputs['lock'][which + '_sha256'] = identities[which + '_sha256']
    identities['lock_sha256'] = _sha(_canonical(inputs['lock']))
    journal = RunJournal.create(tmp_path / 'run', identities=identities,
                                environment_id=inputs['config']['environment'])
    with pytest.raises(StateError, match='exact installed canonical'):
        LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path / 'locks')


@pytest.mark.parametrize('which', ['stale', 'foreign-request', 'foreign-path', 'validator-reject'])
def test_bad_native_result_cannot_advance_or_trigger_cleanup(setup, which):
    engine, driver, _, clock = setup
    result = engine.advance()
    path, value = native_result(engine.journal, result['native_request'])
    if which == 'stale':
        clock.value += timedelta(hours=1)
    elif which == 'foreign-request':
        value['request_id'] = str(uuid4())
        path.write_text(json.dumps(value))
    elif which == 'foreign-path':
        other = path.parent / 'other-result.json'
        other.write_text(path.read_text())
        path = other
    else:
        driver.native_reject = True
    calls = list(driver.calls)
    with pytest.raises((StateError, ValueError)):
        engine.advance(native_result=path)
    assert driver.calls == calls and 'cleanup' not in driver.calls
    assert engine.journal.state['active_native'] is not None


def test_incomplete_native_result_is_not_success_and_runs_cleanup(setup):
    engine, driver, _, _ = setup
    result = engine.advance()
    path, _ = native_result(engine.journal, result['native_request'], status='incomplete')
    result = engine.advance(native_result=path)
    assert result['terminal'] and result['exit_code'] == 2
    assert result['original_failure']['outcome'] == 'incomplete'
    assert driver.calls[-3:] == ['export', 'cleanup', 'finalize']


def test_assertion_failure_is_preserved_after_export_and_observed_cleanup(setup):
    engine, driver, _, _ = setup
    driver.behaviors['primary-baseline'] = fail_read
    result = engine.advance()
    assert result['terminal'] and result['exit_code'] == 1
    assert result['scenario_assertion_status'] == 'fail'
    assert result['original_failure']['phase_id'] == 'primary-baseline'
    assert driver.calls[-3:] == ['export', 'cleanup', 'finalize']
    assert 'primary-inject' not in driver.calls
    assert engine.journal.state['cleanup_validation'] is not None


def test_read_interruption_cleans_up_but_retains_original_error(setup):
    engine, driver, _, _ = setup
    driver.behaviors['primary-baseline'] = crash
    result = engine.advance()
    assert result['terminal'] and result['execution_status'] == 'error'
    assert result['original_failure']['outcome'] == 'interrupted'
    assert driver.calls[-3:] == ['export', 'cleanup', 'finalize']


def test_mutation_interruption_requires_reconciliation_and_never_auto_retries(setup):
    engine, driver, _, _ = setup
    driver.behaviors['prepare'] = crash
    result = engine.advance()
    assert result['execution_status'] == 'reconciliation_required' and not result['terminal']
    assert engine.journal.state['environment_reserved']
    assert driver.calls == ['preflight', 'prepare']
    assert engine.advance()['execution_status'] == 'reconciliation_required'
    assert driver.calls == ['preflight', 'prepare']
    receipt = engine.journal.run_dir / 'reconciliation-observation.json'
    receipt.write_text('{"fixture_only":true,"observed":"not_applied"}')
    op = engine.journal.state['active_mutation']['operation_id']
    result = engine.reconcile(operation_id=op, outcome='not_applied', receipt_artifacts=[receipt],
                               validator=lambda outcome, raw: ['fixture-effect-observed'])
    assert result['terminal'] and result['execution_status'] == 'error'
    assert driver.calls == ['preflight', 'prepare', 'export', 'cleanup', 'finalize']
    assert result['original_failure']['code'] == 'phase-interrupted'


def test_operator_interrupt_of_native_pause_cleans_up_without_native_success(setup):
    engine, driver, _, _ = setup
    engine.advance()
    result = engine.interrupt()
    assert result['terminal'] and result['original_failure']['code'] == 'operator-interrupted'
    assert driver.calls[-3:] == ['export', 'cleanup', 'finalize']
    assert engine.journal.state['native_results'] == []
    assert len(engine.journal.state['native_cancellations']) == 1
    assert engine.journal.state['cleanup_validation'] is not None
    assert not engine.journal.state['environment_reserved']


def test_interrupt_contending_with_native_runner_leaves_journal_unchanged(setup):
    engine, driver, inputs, clock = setup
    engine.advance()
    other_journal = RunJournal.open(engine.journal.run_dir, identities=engine.journal.identities, clock=clock)
    other = LifecycleEngine(other_journal, inputs=inputs, shared_lock_dir=engine.shared_lock_dir,
                            _test_driver=driver)
    before = engine.journal.state
    files = {path.relative_to(engine.journal.run_dir): path.read_bytes()
             for path in engine.journal.run_dir.rglob('*') if path.is_file()}
    calls = list(driver.calls)
    with engine.journal.lifecycle_lock():
        with pytest.raises(StateError, match='lifecycle execution lock'):
            other.interrupt()
    assert engine.journal.state == before
    assert {path.relative_to(engine.journal.run_dir): path.read_bytes()
            for path in engine.journal.run_dir.rglob('*') if path.is_file()} == files
    assert driver.calls == calls


def test_interrupt_unstarted_run_does_not_reserve_environment(setup):
    engine, driver, _, _ = setup
    before = engine.journal.state
    with pytest.raises(StateError, match='No lifecycle phase'):
        engine.interrupt()
    assert engine.journal.state == before
    assert driver.calls == []
    assert not engine.shared_lock_dir.exists()


def test_interrupt_holds_runner_lock_through_cleanup(setup, monkeypatch):
    engine, driver, inputs, clock = setup
    engine.advance()
    other_journal = RunJournal.open(engine.journal.run_dir, identities=engine.journal.identities, clock=clock)
    other = LifecycleEngine(other_journal, inputs=inputs, shared_lock_dir=engine.shared_lock_dir,
                            _test_driver=driver)
    execute = driver.execute
    observed = []
    def competing_runner(context):
        if context.phase['id'] == 'cleanup':
            before = other_journal.state
            with pytest.raises(StateError, match='lifecycle execution lock'):
                other.interrupt()
            assert other_journal.state == before
            observed.append(True)
        return execute(context)
    monkeypatch.setattr(driver, 'execute', competing_runner)
    result = engine.interrupt()
    assert observed == [True]
    assert result['terminal'] and not engine.journal.state['environment_reserved']
    assert driver.calls[-3:] == ['export', 'cleanup', 'finalize']


def test_interrupt_unfinished_preflight_does_not_reserve_or_run_cleanup(setup):
    engine, driver, _, _ = setup
    with engine.journal.lifecycle_lock():
        engine.journal.begin_phase('preflight')
    result = engine.interrupt()
    state = engine.journal.state
    assert not result['terminal'] and not result['cleanup_required']
    assert result['original_failure']['code'] == 'operator-interrupted'
    assert not state['environment_reserved'] and state['operations'] == []
    assert state['cleanup_validation'] is None
    assert driver.calls == []
    assert not engine.shared_lock_dir.exists()


def test_interrupt_unresolved_mutation_preserves_effect_and_original_failure(setup):
    engine, driver, _, _ = setup
    driver.behaviors['prepare'] = crash
    engine.advance()
    before = engine.journal.state
    calls = list(driver.calls)
    result = engine.interrupt()
    state = engine.journal.state
    assert result['execution_status'] == 'reconciliation_required' and not result['terminal']
    assert state['active_mutation'] == before['active_mutation']
    assert state['operations'] == before['operations']
    assert state['lifecycle']['abort'] == before['lifecycle']['abort']
    assert state['environment_reserved'] and state['cleanup_validation'] is None
    assert driver.calls == calls


def test_interrupt_finalized_run_is_read_only(setup):
    engine, driver, _, _ = setup
    engine.advance()
    finalized = engine.interrupt()
    before = engine.journal.state
    calls = list(driver.calls)
    assert engine.interrupt() == finalized
    assert engine.journal.state == before
    assert driver.calls == calls


def test_cleanup_failure_cannot_emit_terminal_result_or_erase_assertion_failure(setup):
    engine, driver, _, _ = setup
    driver.behaviors['primary-baseline'] = fail_read
    driver.cleanup_reject = True
    result = engine.advance()
    assert not result['terminal'] and result['execution_status'] == 'reconciliation_required'
    assert result['original_failure']['outcome'] == 'assertion-failure'
    assert 'finalize' not in driver.calls
    assert engine.journal.state['lifecycle']['final_result'] is None
    calls = list(driver.calls)
    engine.advance()
    assert driver.calls == calls


def test_export_failure_does_not_prevent_cleanup_but_prevents_terminal_result(setup):
    engine, driver, _, _ = setup
    driver.behaviors['primary-baseline'] = fail_read
    driver.behaviors['export'] = lambda _: PhaseOutcome('incomplete', 'fixture-export-missing')
    result = engine.advance()
    assert 'cleanup' in driver.calls and 'finalize' not in driver.calls
    assert not result['terminal'] and engine.journal.state['execution_status'] == 'released'
    assert result['original_failure']['outcome'] == 'assertion-failure'


def test_phase_timeout_retains_unknown_mutation_effect(setup, monkeypatch):
    engine, driver, _, _ = setup
    current = [1.0]
    monkeypatch.setattr(module, 'monotonic', lambda: current[0])
    original = driver.execute
    def delayed(context):
        driver.behaviors.pop('prepare')
        outcome = original(context)
        current[0] += 1801
        return outcome
    driver.behaviors['prepare'] = delayed
    result = engine.advance()
    assert result['execution_status'] == 'reconciliation_required'
    assert result['original_failure']['code'] == 'phase-timeout'


def test_phase_cannot_jump_or_skip_replay_in_authoritative_journal(setup, tmp_path):
    engine, _, _, _ = setup
    with engine.journal.mutation_lock(tmp_path / 'locks'):
        with pytest.raises(StateError, match='predecessor'):
            engine.journal.begin_phase('replay-baseline')


def test_reopened_active_read_is_not_silently_replayed(setup, tmp_path):
    engine, driver, inputs, clock = setup
    with engine.journal.lifecycle_lock():
        engine.journal.begin_phase('preflight')
    reopened = RunJournal.open(engine.journal.run_dir, identities=engine.journal.identities, clock=clock)
    resumed = LifecycleEngine(reopened, inputs=inputs, shared_lock_dir=tmp_path / 'locks', _test_driver=driver)
    result = resumed.advance()
    assert not result['terminal'] and result['original_failure']['code'] == 'unfinished-phase-not-retried'
    assert driver.calls == [] and not reopened.state['environment_reserved']


def test_arbitrary_json_success_is_not_a_driver_outcome(setup):
    engine, driver, _, _ = setup
    driver.behaviors['preflight'] = lambda _: {'status': 'completed', 'success': True}
    result = engine.advance()
    assert not result['terminal'] and result['original_failure']['outcome'] == 'error'
    assert not result['canonical_success']


def test_native_resume_through_fresh_engine_retains_attempt_identity(setup, tmp_path):
    engine, driver, inputs, clock = setup
    result = engine.advance()
    path, _ = native_result(engine.journal, result['native_request'])
    reopened = RunJournal.open(engine.journal.run_dir, identities=engine.journal.identities, clock=clock)
    resumed = LifecycleEngine(reopened, inputs=inputs, shared_lock_dir=tmp_path / 'locks', _test_driver=driver)
    next_result = resumed.advance(native_result=path)
    request = json.loads(Path(next_result['native_request']['request_path']).read_text())
    assert request['step_id'] == 'primary-diagnose'
    assert len(reopened.state['native_results']) == 1


def test_input_dict_does_not_share_mutable_configuration_with_driver(setup):
    engine, driver, _, _ = setup
    def cannot_mutate(context):
        with pytest.raises(TypeError):
            context.inputs['config']['environment'] = 'other'
        with pytest.raises(TypeError):
            context.phase['operation'] = 'up'
        return PhaseOutcome('incomplete', 'fixture-no-evidence')
    driver.behaviors['preflight'] = cannot_mutate
    result = engine.advance()
    assert result['original_failure']['outcome'] == 'incomplete'


def test_cleanup_interrupt_is_reconciled_from_observations_not_repeated(setup):
    engine, driver, _, _ = setup
    driver.behaviors['primary-baseline'] = fail_read
    driver.behaviors['cleanup'] = crash
    result = engine.advance()
    assert result['execution_status'] == 'reconciliation_required'
    receipt = engine.journal.run_dir / 'cleanup-observation.json'
    receipt.write_text('{"fixture_only":true,"deleted_resources_observed":true}')
    op = engine.journal.state['active_mutation']['operation_id']
    result = engine.reconcile(operation_id=op, outcome='applied', receipt_artifacts=[receipt],
                               validator=lambda outcome, raw: ['fixture-deletions-observed'])
    assert result['terminal'] and result['exit_code'] == 1
    assert driver.calls.count('cleanup') == 1
    assert result['original_failure']['outcome'] == 'assertion-failure'


def test_scope_expiry_after_native_yield_cancels_request_and_only_cleans_up(setup):
    engine, driver, _, clock = setup
    result = engine.advance()
    calls = list(driver.calls)
    path, _ = native_result(engine.journal, result['native_request'])
    clock.value += timedelta(days=2)
    result = engine.advance(native_result=path)
    assert result['terminal'] and result['exit_code'] == 2
    assert result['original_failure']['code'] == 'authorization-expired'
    assert driver.calls == calls + ['export', 'cleanup', 'finalize']
    assert engine.journal.state['native_results'] == []


def test_expired_preflight_performs_no_external_work_or_infrastructure_reservation(setup):
    engine, driver, _, clock = setup
    clock.value += timedelta(days=2)
    result = engine.advance()
    assert not result['terminal'] and result['original_failure']['code'] == 'authorization-expired'
    assert driver.calls == [] and not engine.journal.state['environment_reserved']


def test_synchronous_deadline_interrupts_blocking_call_and_restores_handler():
    prior = signal.getsignal(signal.SIGALRM)
    start = time.monotonic()
    with pytest.raises(module._PhaseExpired):
        with module._deadline(time.monotonic() + 0.03):
            time.sleep(2)
    assert time.monotonic() - start < 0.5
    assert signal.getsignal(signal.SIGALRM) == prior
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_read_only_preflight_cannot_be_disguised_as_a_mutation(setup, tmp_path):
    engine, _, _, _ = setup
    with engine.journal.mutation_lock(tmp_path / 'locks'):
        engine.journal.begin_phase('preflight')
        with pytest.raises(StateError, match='Read-only'):
            engine.journal.begin_mutation('preflight', 'preflight')


def test_reconciliation_validator_receives_frozen_bytes_and_tamper_is_rejected(setup):
    engine, driver, _, _ = setup
    driver.behaviors['prepare'] = crash
    engine.advance()
    op = engine.journal.state['active_mutation']['operation_id']
    receipt = engine.journal.run_dir / 'observation.json'
    receipt.write_bytes(b'fixture original')
    def invalid(outcome, raw):
        assert raw == (b'fixture original',)
        for path in engine.journal.run_dir.glob('reconciliation-*/0'):
            path.write_bytes(b'fixture changed')
        return ['fixture-validation']
    with pytest.raises(StateError, match='changed during validation'):
        engine.reconcile(operation_id=op, outcome='not_applied', receipt_artifacts=[receipt], validator=invalid)
    assert engine.journal.state['active_mutation']['operation_id'] == op


def test_completed_outcome_cannot_supply_unknown_assertion(setup):
    engine, driver, _, _ = setup
    original = driver.execute
    def wrong(context):
        driver.behaviors.pop('primary-baseline')
        normal = original(context)
        return PhaseOutcome('completed', 'fixture-invalid-assertion', normal.artifacts,
                            (AssertionResult('D1', 'invented-check', 'pass'),), 'applied')
    driver.behaviors['primary-baseline'] = wrong
    result = engine.advance()
    assert result['terminal'] and result['execution_status'] == 'error'
    assert result['original_failure']['code'] == 'phase-error'


def test_rejected_cleanup_keeps_exact_down_reconcilable_without_redispatch(setup):
    engine, driver, _, _ = setup
    driver.behaviors['primary-baseline'] = fail_read
    driver.cleanup_reject = True
    result = engine.advance()
    assert result['execution_status'] == 'reconciliation_required'
    state = engine.journal.state
    operation = state['active_mutation']['operation_id']
    assert state['active_mutation']['action'] == 'down'
    assert state['active_mutation']['status'] == 'unknown'
    assert state['environment_reserved'] and state['cleanup_validation'] is None
    assert not any(row['phase_id'] == 'cleanup' for row in state['lifecycle']['phase_records'])
    receipt = engine.journal.run_dir / 'independent-cleanup-observation.json'
    receipt.write_text('{"fixture_only":true,"deleted_resources_observed":true}')
    with pytest.raises(ValueError, match='SECRET_SENTINEL'):
        engine.reconcile(operation_id=operation, outcome='applied', receipt_artifacts=[receipt],
                         validator=lambda outcome, raw: ['fixture-deletions-observed'])
    assert engine.journal.state['active_mutation']['operation_id'] == operation
    assert driver.calls.count('cleanup') == 1
    driver.cleanup_reject = False
    result = engine.reconcile(operation_id=operation, outcome='applied', receipt_artifacts=[receipt],
                              validator=lambda outcome, raw: ['fixture-deletions-observed'])
    assert result['terminal'] and result['exit_code'] == 1
    assert result['original_failure']['outcome'] == 'assertion-failure'
    assert driver.calls.count('cleanup') == 1
    assert engine.journal.state['execution_status'] == 'released'


@pytest.mark.parametrize('failure', ['before-append', 'after-append', 'owner-unlink', 'owner-fsync'])
def test_cleanup_publication_fault_preserves_atomic_effect_or_reconcilable_down(setup, monkeypatch, failure):
    engine, driver, _, _ = setup
    driver.behaviors['primary-baseline'] = fail_read
    journal = engine.journal
    from devsecops.factory import state as journal_module
    original_commit, original_projection = journal._commit, journal._write_projection
    original_unlink, original_fsync = Path.unlink, journal_module._fsync_directory
    failed = []
    def commit(kind, payload):
        if failure == 'before-append' and kind == 'cleanup_completed' and not failed:
            failed.append(True)
            raise OSError('fixture cleanup pre-append failure')
        return original_commit(kind, payload)
    def projection():
        if failure == 'after-append' and journal._state['execution_status'] == 'released' and not failed:
            failed.append(True)
            raise OSError('fixture cleanup post-append failure')
        return original_projection()
    def unlink(path, *args, **kwargs):
        if failure == 'owner-unlink' and path == journal._owner_path and not failed:
            failed.append(True)
            raise OSError('fixture owner unlink failure')
        return original_unlink(path, *args, **kwargs)
    def fsync(path):
        if (failure == 'owner-fsync' and path == engine.shared_lock_dir
                and journal._state['execution_status'] == 'released' and not failed):
            failed.append(True)
            raise OSError('fixture owner fsync failure')
        return original_fsync(path)
    monkeypatch.setattr(journal, '_commit', commit)
    monkeypatch.setattr(journal, '_write_projection', projection)
    monkeypatch.setattr(Path, 'unlink', unlink)
    monkeypatch.setattr(journal_module, '_fsync_directory', fsync)
    result = engine.advance()
    assert failed == [True] and driver.calls.count('cleanup') == 1
    if failure == 'before-append':
        assert result['execution_status'] == 'reconciliation_required'
        assert journal.state['active_mutation']['action'] == 'down'
        assert journal.state['environment_reserved']
        assert journal.state['cleanup_validation'] is None
    else:
        assert journal.state['execution_status'] == 'released'
        assert journal.state['active_mutation'] is None
        assert result['terminal'] and result['exit_code'] == 1
        calls = list(driver.calls)
        assert engine.advance() == result
        assert driver.calls == calls
        assert not list(engine.shared_lock_dir.glob('*.owner.json'))
    assert result['original_failure']['outcome'] == 'assertion-failure'


@pytest.mark.parametrize('status', ['incomplete', 'error', 'assertion-failure'])
def test_partial_applied_cleanup_preserves_observation_and_reconciliation_target(setup, status):
    engine, driver, _, _ = setup
    driver.behaviors['primary-baseline'] = fail_read
    artifact = engine.journal.run_dir / 'partial-cleanup.json'
    artifact.write_text('{"fixture_only":true,"component_removed":true,"complete_cleanup":false}')
    driver.behaviors['cleanup'] = lambda context: PhaseOutcome(status, 'fixture-partial-cleanup',
                                                               (artifact,), mutation_effect='applied')
    result = engine.advance()
    assert result['execution_status'] == 'reconciliation_required'
    state = engine.journal.state
    pending = state['active_mutation']
    assert pending['action'] == 'down' and pending['status'] == 'unknown'
    assert state['environment_reserved'] and state['cleanup_validation'] is None
    assert any((engine.journal.run_dir / ref['path']).read_bytes() == artifact.read_bytes()
               for ref in pending['receipt_artifacts'])
    calls = list(driver.calls)
    engine.advance()
    assert driver.calls == calls and driver.calls.count('cleanup') == 1


def test_foreign_owner_marker_is_never_removed_by_cleanup_recovery(setup, monkeypatch):
    engine, driver, _, _ = setup
    driver.behaviors['primary-baseline'] = fail_read
    original = Path.unlink
    replaced = []
    def replace_marker(path, *args, **kwargs):
        if path == engine.journal._owner_path and not replaced:
            value = json.loads(path.read_bytes())
            value['run_id'] = str(uuid4())
            path.write_text(json.dumps(value))
            replaced.append(path)
            raise OSError('fixture foreign owner replacement')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', replace_marker)
    with pytest.raises(StateError, match='owner marker differs'):
        engine.advance()
    assert len(replaced) == 1 and replaced[0].is_file()
    assert json.loads(replaced[0].read_bytes())['run_id'] != engine.journal.state['run_id']
    assert driver.calls.count('cleanup') == 1
    assert engine.journal.state['execution_status'] == 'released'
    assert engine.journal.state['lifecycle']['final_result'] is None
