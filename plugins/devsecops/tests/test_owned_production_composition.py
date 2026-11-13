"""Real 33-phase journal/dispatch composition with controlled effect boundaries.

This deliberately supplies no production capabilities. CI, lab, collectors,
native execution and exports are controlled leaves. The actual lifecycle,
OwnedLinuxDriver, production dispatcher, operation dispatcher, runtime-input
dispatcher and runtime phase dispatcher run. A complete graph remains unknown
and cannot claim live qualification, recovery or a recording.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
import traceback
from types import SimpleNamespace

import pytest

from devsecops.factory.lifecycle import AssertionResult, LifecycleEngine, PhaseOutcome
from devsecops.factory.state import RunJournal, _canonical, _sha
from devsecops.factory.owned_linux import OwnedLinuxDriver
from devsecops.factory.owned_production import OwnedProductionCoordinator, REQUIRED_SELECTIONS
from devsecops.factory.owned_operations import OwnedOperationCoordinator
from devsecops.factory.owned_runtime_inputs import OwnedRuntimeInputCoordinator
from devsecops.factory.runtime_observations import RuntimeObservationSession
from devsecops.factory.native_factory_session import NativeFactorySession
from test_factory_lifecycle import inputs_for, finish_native


@pytest.fixture
def composition(tmp_path, monkeypatch, request):
    inputs, identities = inputs_for(tmp_path)
    inputs['config']['expires_at'] = (datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()
    if getattr(request, 'param', {}).get('without_probe'):
        inputs['config']['allowed_mutations'].remove('probe')
    identities['config_sha256'] = _sha(_canonical(inputs['config']))
    journal = RunJournal.create(tmp_path/'journal', identities=identities, environment_id=inputs['config']['environment'])
    driver = OwnedLinuxDriver(journal, plugin_id='devsecops@local', plugin_version='controlled',
                               auxiliary_inputs={name: ('controlled-only', 'a'*64) for name in REQUIRED_SELECTIONS})
    trace, states, snapshots, leaves = [], {}, [], []
    fault = {'missing_baseline': False, 'errors': [], 'owned_probe': True, 'probe_effect': None}
    assertion_doc = json.loads(inputs['assertions'].read_bytes())

    @contextmanager
    def controlled_inner_deadline(deadline):
        # The injected controlled driver is already inside LifecycleEngine's
        # real deadline. Registered production calls own this inner deadline.
        yield
    monkeypatch.setattr('devsecops.factory.owned_linux._deadline', controlled_inner_deadline)

    def admit(context, *args, **kwargs):
        assert context.execution_mode == 'controlled-test'
        assert dict(context.identities) == journal.identities
        assert journal.state['lifecycle']['active_phase']['phase_id'] == context.phase['id']
        return {}, deepcopy(inputs)

    monkeypatch.setattr(driver, '_admit', admit)
    monkeypatch.setattr(driver, '_package', lambda *a: {'controlled_boundary': True})
    monkeypatch.setattr(driver, '_auxiliary_artifacts', lambda: {})

    def artifact(context, kind):
        path = context.run_dir/(context.phase['id']+'-'+kind+'.json')
        path.write_bytes(_canonical({'fixture_only': True, 'phase': context.phase['id'], 'boundary': kind}))
        return path

    def outcome(context, kind, *, applied=True):
        leaves.append((context.phase['id'], kind))
        assertions = tuple(AssertionResult(case, check, 'pass') for case in context.phase['case_ids']
                           for check in assertion_doc['cases'][case])
        return PhaseOutcome('completed', 'controlled-'+kind, (artifact(context, kind),), assertions,
                            'applied' if applied else 'not_applied')

    runtime = object.__new__(RuntimeObservationSession)
    runtime._driver, runtime._journal, runtime._pid = driver, journal, os.getpid()
    runtime._run, runtime._identities = journal.state['run_id'], dict(journal.identities)
    runtime._records, runtime._recovery, runtime._input_producer = states, {}, None
    monkeypatch.setattr(runtime, '_admit', admit)
    monkeypatch.setattr(runtime, '_retained', lambda attempt, phase: states[(attempt, phase)])

    operations = object.__new__(OwnedOperationCoordinator)
    operations.journal, operations.runtime = journal, runtime
    operations._deliveries, operations._runtime_inputs = {}, None
    monkeypatch.setattr(operations, '_admit', admit)
    monkeypatch.setattr(operations, 'evidence_artifacts', lambda context: (artifact(context, 'controlled-origin-closure'),))
    monkeypatch.setattr(operations, 'runtime_image', lambda context: object())
    monkeypatch.setattr(operations, 'locked_images', lambda context: object())
    monkeypatch.setattr(runtime, 'bind_runtime_image', lambda *a: None)
    monkeypatch.setattr(runtime, 'bind_locked_images', lambda *a: None)

    producer = object.__new__(OwnedRuntimeInputCoordinator)
    producer.operations, producer.runtime, producer.journal = operations, runtime, journal
    producer._records, producer._f0_evidence = {}, []
    monkeypatch.setattr(producer, '_admit', admit)
    monkeypatch.setattr(producer, 'refresh_images', lambda context: admit(context))
    monkeypatch.setattr(producer, 'require_cni_selection', lambda context: {'controlled_boundary': True})
    operations._runtime_inputs = producer

    def retain(context, value, *, attempt, stage):
        assert (attempt, stage) not in operations._deliveries
        operations._deliveries[(attempt, stage)] = value

    monkeypatch.setattr(operations, '_retain_delivery', retain)
    monkeypatch.setattr('devsecops.factory.owned_policy_delivery.execute_policy_delivery',
                        lambda context, **kwargs: {'controlled_boundary': True, 'phase': context.phase['id']})

    def snapshot(context, *, attempt, phase):
        assert (attempt, phase) in operations._deliveries
        snapshots.append((attempt, phase))
        return {'controlled_boundary': True}

    monkeypatch.setattr(producer, '_snapshot', snapshot)

    def begin(context, *, attempt, phase, snapshot, experiment=None):
        assert (attempt, phase) not in states
        assert context.phase['operation'] in {'build-promote', 'reset', 'inject', 'deliver'}
        if not (fault['missing_baseline'] and (attempt, phase) == ('primary', 'baseline')):
            states[(attempt, phase)] = {'probes': [object()], 'bundle': {}, 'context': context, 'finished': False}
        path = artifact(context, attempt+'-'+phase+'-pending')
        return SimpleNamespace(context=path, pending=path)

    monkeypatch.setattr(producer, '_begin', begin)
    monkeypatch.setattr(producer, 'phase_artifacts', lambda context, **kw: (artifact(context, 'runtime-prepared'),))
    experiment = SimpleNamespace(_observed={})
    experiment.require_transition = lambda plan, context, state: {'controlled_boundary': True, 'state': state}
    experiment.observe = lambda context, state: experiment._observed.__setitem__(state, {'controlled_boundary': True})
    experiment.qualify = lambda context: {'controlled_boundary': True}
    producer._experiment = experiment
    monkeypatch.setattr(producer, '_prepare_policy', lambda context, snapshot: object())
    monkeypatch.setattr(producer, 'require_policy_target', lambda context: {
        'policy': {'metadata': {'uid': 'controlled-policy'}, 'spec': {'fixture_only': True}}})
    monkeypatch.setattr(runtime, 'bind_network_policy_path', lambda *a: None)
    monkeypatch.setattr(runtime, 'qualified_fault', lambda context: {'controlled_boundary': True})

    def finish(context, *, attempt=None, phase=None):
        attempt = attempt or context.phase['attempt']
        phase = phase or {'baseline': 'baseline', 'confirm-fault': 'fault', 'probe': 'recovery'}[context.phase['operation']]
        record = states[(attempt, phase)]
        assert not record['finished']
        record['finished'], record['bundle'] = True, {'stage': 'measured'}
        path = artifact(context, attempt+'-'+phase+'-collection')
        return SimpleNamespace(context=path, pending=path, collection=path)

    monkeypatch.setattr(runtime, 'finish', finish)
    monkeypatch.setattr(runtime, 'finish_experiment', lambda context, phase: finish(context, attempt='f0-network-policy', phase=phase))
    monkeypatch.setattr(runtime, 'finish_probes', lambda context: {'controlled': artifact(context, 'observed-probes')})
    monkeypatch.setattr(runtime, 'finish_experiment_probes', lambda context, phase: {})

    def native_input(attempt, kind):
        needed = ('baseline', 'fault') if kind == 'diagnose' else ('baseline', 'fault', 'recovery')
        assert all(states[(attempt, phase)]['finished'] for phase in needed)
        return {'controlled_boundary': True, 'attempt': attempt, 'kind': kind}

    monkeypatch.setattr(runtime, 'diagnosis_inputs', lambda attempt: native_input(attempt, 'diagnose'))
    monkeypatch.setattr(runtime, 'recovery_inputs', lambda attempt: native_input(attempt, 'recovery'))
    monkeypatch.setattr(operations, 'source_selection', lambda attempt: {
        'controlled_boundary': True, 'attempt': attempt, 'delivery': operations._deliveries[(attempt, 'fault')]})

    native_session = object.__new__(NativeFactorySession)
    monkeypatch.setattr(native_session, 'origin', lambda attempt: {'controlled_boundary': True, 'attempt': attempt})
    driver._native_session = native_session
    monkeypatch.setattr(runtime, 'verify', lambda context, **kw: {
        'execution_status': 'complete', 'recovery': 'verified', 'fixture_only': True, 'execution_mode': 'controlled-test'})

    # The additional original CI/F0/native acceptance producers are controlled
    # leaves here, like the original runtime verifier. No live capability is issued.
    from devsecops.factory.runtime_acceptance import CHECKS
    monkeypatch.setattr('devsecops.factory.runtime_acceptance.assertions_for_verification',
        lambda session, context, report: (tuple(AssertionResult(case, name, 'pass') for case, name in CHECKS),
            {'controlled_boundary': True, 'execution_mode': 'controlled-test'}, ()))

    def capture_inventory(context):
        assert snapshots == [('primary', 'baseline')]
        assert not states  # Before any F0 mutation actor/baseline preparation.
        fault['inventory_captures'] = fault.get('inventory_captures', 0) + 1
        return {'captured': not fault.get('missing_inventory'), 'pending_application_inventory': {}}

    bootstrap = SimpleNamespace(execute=lambda context: outcome(context, 'bootstrap'),
        capture_controller_inventory=capture_inventory,
        kubernetes=lambda context: SimpleNamespace(replace_network_policy=lambda *a, **kw: None))
    operations.bootstrap = producer.bootstrap = runtime._bootstrap = bootstrap
    production = object.__new__(OwnedProductionCoordinator)
    production.driver, production.journal, production.pid = driver, journal, os.getpid()
    production.run_id, production.identities = journal.state['run_id'], dict(journal.identities)
    production.bootstrap, production.runtime, production.operations = bootstrap, runtime, operations
    production.cases = SimpleNamespace(execute=lambda context: outcome(context, 'case'))
    production.cleanup = SimpleNamespace(execute=lambda context: outcome(context, 'cleanup'),
        validate_cleanup=lambda receipts: ['controlled-cleanup-boundary'])
    production.native = SimpleNamespace(prepare=lambda context, selection: {},
        validate_native=lambda request, result, artifacts: ['controlled-native-boundary'])
    production.replay_acceptance = SimpleNamespace(begin_reset=lambda context: None,
        retain_reset=lambda context, result: result,
        finalize=lambda context: outcome(context, 'replay-acceptance', applied=False))
    driver._production_instance = production

    def build(context):
        retain(context, {'controlled_boundary': True}, attempt='primary', stage='baseline')
        return operations.prepare_runtime(context)

    monkeypatch.setattr(operations, 'build_promote', build)
    monkeypatch.setattr('devsecops.factory.correction_phase.execute', lambda driver, context, deadline: outcome(context, 'correction', applied=False))
    @contextmanager
    def export(journal, context, **kwargs):
        yield (artifact(context, 'export'),)
    monkeypatch.setattr('devsecops.factory.evidence_export.export_checkpoint', export)
    execute, native_inputs = driver.execute, driver.native_inputs
    def traced(context):
        trace.append(context.phase['id'])
        try:
            result = execute(context)
            if context.phase['operation'] == 'probe' and fault['probe_effect'] is not None:
                result = replace(result, mutation_effect=fault['probe_effect'])
            return result
        except Exception:
            fault['errors'].append(traceback.format_exc())
            raise
    def traced_native(context):
        trace.append(context.phase['id'])
        return native_inputs(context)
    monkeypatch.setattr(driver, 'execute', traced)
    monkeypatch.setattr(driver, 'native_inputs', traced_native)
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path/'locks', _test_driver=driver)
    # Keep the journal and all evidence explicitly controlled. Only the probe
    # scheduler's fixed selection branch is activated here; no production
    # capability is issued. Recipe loading/admission has separate component
    # coverage and is deliberately outside this controlled graph fixture.
    original_phase = engine._phase
    def selected_probe_phase(phase):
        if phase['operation'] != 'probe' or not fault['owned_probe']:
            return original_phase(phase)
        driver_id, auxiliary = engine.driver_id, engine.auxiliary_raw
        try:
            engine.driver_id = 'owned-linux'
            engine.auxiliary_raw = {**auxiliary, 'owned_runtime_recipe': b'controlled-selection-branch-only'}
            return original_phase(phase)
        finally:
            engine.driver_id, engine.auxiliary_raw = driver_id, auxiliary
    monkeypatch.setattr(engine, '_phase', selected_probe_phase)
    return engine, trace, snapshots, states, fault, producer


def test_all_33_phases_use_fixed_combined_routes_and_one_replay(composition):
    engine, trace, snapshots, states, fault, _ = composition
    result = engine.advance()
    native_steps = []
    while result['exit_code'] == 3:
        native_steps.append(engine.journal.state['active_native']['step_id'])
        result = finish_native(engine, result)
    assert not fault['errors'], fault['errors']
    assert trace == [p['id'] for p in engine.docs['scenario']['phases']], result
    assert native_steps == [attempt+'-'+kind for attempt in ('primary', 'replay') for kind in ('review', 'diagnose', 'correction', 'recovery')]
    assert set(states) == {(a, p) for a in ('f0-network-policy', 'primary', 'replay') for p in ('baseline', 'fault', 'recovery')}
    assert all(record['finished'] for record in states.values())
    assert snapshots == [('primary', 'baseline'), ('primary', 'baseline'), ('primary', 'fault'),
                          ('primary', 'recovery'), ('replay', 'baseline'), ('replay', 'fault'), ('replay', 'recovery')]
    assert len(engine.journal.state['lifecycle']['phase_records']) == 33
    assert engine.journal.state['execution_status'] == 'released'
    assert result['terminal'] and result['execution_mode'] == 'controlled-test'
    assert result['canonical_success'] is False and result['scenario_assertion_status'] == 'unknown'
    assert not any(row['action'] == 'probe' for row in engine.journal.state['operations'])


def test_missing_original_baseline_stops_incident_and_replay(composition):
    engine, trace, _, _, fault, _ = composition
    fault['missing_baseline'] = True
    result = engine.advance()
    assert 'primary-baseline' in trace
    assert 'primary-inject' not in trace and 'reset' not in trace and 'replay-baseline' not in trace
    assert result['canonical_success'] is False
    assert result['original_failure']['phase_id'] == 'primary-baseline'


def test_probe_dispatch_never_starts_new_actors_or_replaces_delivery_window(composition):
    engine, _, _, states, _, producer = composition
    result = engine.advance()
    while result['exit_code'] == 3 and engine.journal.state['active_native']['step_id'] != 'primary-recovery':
        result = finish_native(engine, result)
    assert engine.journal.state['active_native']['step_id'] == 'primary-recovery'
    assert states[('primary', 'recovery')]['finished']
    assert ('primary', 'probe') not in states
    assert sum(k == ('primary', 'recovery') for k in states) == 1


def test_original_production_validator_wrapper_is_usable_inside_journal_acceptance(composition, monkeypatch):
    engine, *_ = composition
    production = engine.driver._production()
    monkeypatch.setattr(production, 'validate_native',
        lambda request, result, artifacts: OwnedProductionCoordinator.validate_native(production, request, result, artifacts))
    pending = engine.advance()
    assert pending['exit_code'] == 3
    # RunJournal already owns the exact original run lock here. A validator
    # may audit its current snapshot but must not attempt a competing flock.
    result = finish_native(engine, pending)
    assert result['exit_code'] == 3
    assert engine.journal.state['active_native']['step_id'] == 'primary-diagnose'


@pytest.mark.parametrize('effect', ['applied', 'unknown'])
def test_owned_probe_observation_rejects_claimed_new_mutation(composition, effect):
    engine, trace, _, _, fault, _ = composition
    fault['probe_effect'] = effect
    result = engine.advance()
    while result['exit_code'] == 3:
        result = finish_native(engine, result)
    assert result['original_failure']['phase_id'] == 'primary-probes'
    assert 'primary-recovery' not in trace
    assert not any(row['action'] == 'probe' for row in engine.journal.state['operations'])


def test_legacy_probe_still_requires_its_own_applied_mutation(composition):
    engine, _, _, _, fault, _ = composition
    fault.update(owned_probe=False, probe_effect='applied')
    result = engine.advance()
    while result['exit_code'] == 3:
        result = finish_native(engine, result)
    probes = [row for row in engine.journal.state['operations'] if row['action'] == 'probe']
    assert len(probes) == 2 and all(row['status'] == 'applied' for row in probes)
    assert result['terminal'] and not result['canonical_success']


@pytest.mark.parametrize('composition', [{'without_probe': True}], indirect=True)
def test_owned_probe_observation_still_requires_explicit_probe_grant(composition):
    engine, trace, _, _, _, _ = composition
    result = engine.advance()
    while result['exit_code'] == 3:
        result = finish_native(engine, result)
    assert result['original_failure']['phase_id'] == 'primary-probes'
    assert result['original_failure']['code'] == 'runtime-controls-not-authorized'
    assert 'primary-probes' not in trace and 'primary-recovery' not in trace
    assert not any(row['action'] == 'probe' for row in engine.journal.state['operations'])


def test_first_ready_delivery_must_close_original_child_inventory_before_f0(composition):
    engine, trace, snapshots, states, fault, _ = composition
    fault['missing_inventory'] = True
    result = engine.advance()
    assert result['original_failure']['phase_id'] == 'build-promote'
    assert fault['inventory_captures'] == 1
    assert snapshots == [('primary', 'baseline')]
    assert not states
    assert 'primary-baseline' not in trace
