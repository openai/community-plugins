"""Bounded journal evidence closure; these are local state-machine fixtures."""
from pathlib import Path

import pytest

from devsecops.factory import state as state_module
from devsecops.factory.state import RunJournal, StateError
from test_factory_state import clock, identities, journal, write
from test_factory_lifecycle import setup, complete

__all__ = ['clock', 'identities', 'journal', 'setup']


def test_large_result_closure_survives_observation_and_independent_replay(journal, tmp_path):
    sources = tuple(write(tmp_path / f'receipt-{i}', str(i).encode()) for i in range(200))
    with journal.mutation_lock(tmp_path / 'locks'):
        operation = journal.begin_mutation('build', 'build')
        journal.finish_mutation(operation, receipt_artifacts=sources)
    reopened = RunJournal.open(journal.run_dir, identities=journal.identities, clock=journal._clock)
    refs = reopened.state['operations'][0]['receipt_artifacts']
    assert len(refs) == 200
    assert [(reopened.run_dir / r['path']).read_bytes() for r in refs] == [p.read_bytes() for p in sources]


def test_result_count_and_aggregate_bounds_leave_mutation_unresolved(journal, tmp_path, monkeypatch):
    source = write(tmp_path / 'receipt', b'123456')
    with journal.mutation_lock(tmp_path / 'locks'):
        operation = journal.begin_mutation('build', 'build')
        with pytest.raises(StateError, match='Too many artifacts'):
            journal.finish_mutation(operation, receipt_artifacts=[source] * 257)
        monkeypatch.setattr(state_module, 'MAX_ARTIFACT_TOTAL_BYTES', 10)
        with pytest.raises(StateError):
            journal.finish_mutation(operation, receipt_artifacts=[source] * 2)
        assert journal.state['active_mutation']['operation_id'] == operation
        assert journal.state['operations'][0]['receipt_artifacts'] == []


def test_mutation_intent_capacity_remains_sixty_four(journal, tmp_path):
    source = write(tmp_path / 'intent', b'intent')
    with journal.mutation_lock(tmp_path / 'locks'):
        with pytest.raises(StateError, match='Too many artifacts'):
            journal.begin_mutation('build', 'build', intent_artifacts=[source] * 65)
        assert journal.state['active_mutation'] is None


def test_phase_result_capacity_preserves_every_file(setup, tmp_path):
    engine, _, _, _ = setup
    phase = engine.docs['scenario']['phases'][0]
    sources = tuple(write(tmp_path / f'phase-{i}', str(i).encode()) for i in range(200))
    with engine.journal.lifecycle_lock():
        engine.journal.begin_phase(phase['id'])
        engine._record(phase, 'completed', 'fixture-observed', sources)
    record = engine.journal.state['lifecycle']['phase_records'][0]
    assert len(record['artifacts']) == 201
    complete(engine)
    from devsecops.factory.terminal_evidence import verify_terminal
    observed = verify_terminal(engine.journal.run_dir)
    assert len(observed['result']['phase_records'][0]['artifacts']) == 201
    exported = {r['path'] for r in observed['manifest']['files']}
    assert {r['path'] for r in record['artifacts']} <= exported


def test_owned_native_completion_uses_fixed_accessor_and_keeps_validation(setup):
    engine, driver, _, _ = setup
    engine.driver_id = 'owned-linux'
    first, second = Path('/fixture/execution'), Path('/fixture/trace')
    calls = []
    driver.completed_native_artifacts = lambda step: calls.append(step) or (first, second)
    receipt = Path('/fixture/validation')
    assert engine._native_completion_artifacts('primary-review', 'completed', receipt) == (receipt, first, second)
    assert calls == ['primary-review']
    assert engine._native_completion_artifacts('primary-review', 'incomplete', receipt) == (receipt,)
    assert calls == ['primary-review']
