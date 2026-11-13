"""Local journal claim fixtures; no native request or remote delivery success."""
from contextlib import contextmanager
from copy import deepcopy
import json

import pytest

from devsecops.factory import evidence_export
from devsecops.factory.state import RunJournal, StateError, _canonical, _validate
from tests.test_factory_lifecycle import inputs_for
from tests.test_factory_state import Clock, write


@pytest.fixture
def delivery(tmp_path):
    @contextmanager
    def make(*, phase_id='primary-deliver', lifecycle=True):
        inputs, identities = inputs_for(tmp_path)
        journal = RunJournal.create(tmp_path / 'run', identities=identities,
            environment_id=inputs['config']['environment'], clock=Clock())
        if lifecycle:
            journal.initialize_lifecycle(inputs={name: _canonical(value) if isinstance(value, dict)
                else value.read_bytes() for name, value in inputs.items()},
                execution_mode='controlled-test', driver_id='owned-linux')
        evidence = write(tmp_path / 'fixture-observation', b'local fixture observation only\n')
        locks = tmp_path / 'locks'
        with journal.lifecycle_lock(), journal.mutation_lock(locks):
            action = 'deliver'
            if lifecycle:
                for phase in json.loads(inputs['scenario'].read_bytes())['phases']:
                    journal.begin_phase(phase['id'])
                    if phase['id'] == phase_id:
                        action = phase['operation']
                        break
                    journal.finish_phase(phase['id'], 'completed', 'controlled-local-fixture',
                                         receipt_artifacts=[evidence])
            operation = journal.begin_mutation(phase_id, action)
            directory = journal.run_dir / 'operations' / operation / 'git-request-claim'
            directory.mkdir(mode=0o700)
            refs = []
            for name in ('request.body', 'native-request.json', 'claim.json'):
                raw = b'controlled state fixture: ' + name.encode() + b'\n'
                path = write(directory / name, raw)
                refs.append(journal._reference(path, raw))
            yield journal, phase_id, operation, refs, locks, evidence
    return make


def claim(journal, phase_id, operation, refs):
    with journal._run_lock():
        journal._load()
        journal._record_git_request_claim_locked(phase_id, operation, refs)


def test_claim_is_pending_schema_valid_replayable_exported_and_owner_compatible(delivery):
    with delivery() as (journal, phase, operation, refs, locks, _):
        owner = journal._owner_path.read_bytes()
        before = journal.state
        journal._consumed_git_request_operations.add(operation)  # Caller burns before publication.
        claim(journal, phase, operation, refs)
        state = journal.state
        _validate('factory-state', state)
        assert state['journal_seq'] == before['journal_seq'] + 1
        assert state['execution_status'] == 'mutation_pending'
        assert state['operations'][-1]['status'] == 'pending'
        assert state['active_mutation'] == state['operations'][-1]
        assert state['operations'][-1]['receipt_artifacts'] == refs
        assert journal._consumed_git_request_operations == {operation}
        assert journal._owner_path.read_bytes() == owner
        assert state['lifecycle']['final_result'] is None
        _, paths, _ = evidence_export._references(state)
        assert all(paths[ref['path']] == ref for ref in refs)
    reopened = RunJournal.open(journal.run_dir, identities=journal.identities, clock=journal._clock)
    assert reopened.state == state
    assert reopened._consumed_git_request_operations == set()
    with reopened.lifecycle_lock(), reopened.mutation_lock(locks):
        assert reopened.current_mutation(phase, 'deliver', reconciliation=True) == operation
        with pytest.raises(StateError, match='reconciliation'):
            claim(reopened, phase, operation, refs)


def test_duplicate_claim_rejected_by_commit_and_full_replay(delivery):
    with delivery() as (journal, phase, operation, refs, _, _):
        claim(journal, phase, operation, refs)
        before = journal.state
        with pytest.raises(StateError, match='already claimed'):
            claim(journal, phase, operation, refs)
        assert journal.state == before
        event_path = journal.run_dir / 'journal' / f"{before['journal_seq']:08d}.json"
        event = json.loads(event_path.read_bytes())
        event.update(seq=before['journal_seq'] + 1, previous_sha256=before['journal_head_sha256'])
        write(journal.run_dir / 'journal' / f"{event['seq']:08d}.json", _canonical(event))
    with pytest.raises(StateError, match='already claimed'):
        RunJournal.open(journal.run_dir, identities=journal.identities, clock=journal._clock)


@pytest.mark.parametrize('change', [
    'missing', 'duplicate', 'order', 'other-operation', 'alias', 'extra-key',
    'invalid-hash', 'wrong-hash', 'wrong-size', 'bool-size', 'empty', 'oversized',
])
def test_claim_rejects_reference_substitution_without_event(delivery, change):
    with delivery() as (journal, phase, operation, refs, _, _):
        before = journal.state
        selected = deepcopy(refs)
        if change == 'missing':
            selected.pop()
        elif change == 'duplicate':
            selected[1] = selected[0]
        elif change == 'order':
            selected.reverse()
        elif change == 'other-operation':
            selected[0]['path'] = selected[0]['path'].replace(operation, 'another-operation')
        elif change == 'alias':
            selected[0]['path'] = selected[0]['path'].replace('/request.body', '/./request.body')
        elif change == 'extra-key':
            selected[0]['authority'] = True
        elif change in ('invalid-hash', 'wrong-hash'):
            selected[0]['sha256'] = 'invalid' if change == 'invalid-hash' else '0' * 64
        else:
            selected[0]['bytes'] = {'wrong-size': 1, 'bool-size': True, 'empty': 0,
                                    'oversized': 16_000_001}[change]
        with pytest.raises(StateError):
            claim(journal, phase, operation, selected)
        assert journal.state == before


@pytest.mark.parametrize('index', [0, 1, 2])
def test_registered_claim_bytes_remain_audited_on_cached_load_and_replay(delivery, index):
    with delivery() as (journal, phase, operation, refs, _, _):
        claim(journal, phase, operation, refs)
        path = journal.run_dir / refs[index]['path']
        path.write_bytes(b'x' * refs[index]['bytes'])
        with pytest.raises(StateError, match='bound artifact'):
            journal.state
    with pytest.raises(StateError, match='bound artifact'):
        RunJournal.open(journal.run_dir, identities=journal.identities, clock=journal._clock)


@pytest.mark.parametrize('guard', ['lifecycle', 'environment', 'pid', 'owner', 'operation', 'phase'])
def test_claim_requires_current_owned_locked_delivery(delivery, monkeypatch, guard):
    with delivery() as (journal, phase, operation, refs, _, _):
        before = journal.state
        if guard in ('lifecycle', 'environment', 'pid'):
            attr, value = {'lifecycle': ('_lifecycle_locked', False),
                           'environment': ('_environment_locked', False),
                           'pid': ('_process_id', -1)}[guard]
            monkeypatch.setattr(journal, attr, value)
        elif guard == 'owner':
            journal._owned_operations.clear()
        elif guard == 'operation':
            operation = 'different-operation'
        else:
            phase = 'replay-deliver'
        with pytest.raises(StateError):
            claim(journal, phase, operation, refs)
        assert journal.state == before


@pytest.mark.parametrize('lifecycle,phase', [(False, 'primary-deliver'), (True, 'prepare')])
def test_reducer_rejects_claim_without_actual_lifecycle_delivery(delivery, lifecycle, phase):
    with delivery(lifecycle=lifecycle, phase_id=phase) as (journal, _, operation, refs, _, _):
        before = journal.state
        with journal._run_lock():
            journal._load()
            with pytest.raises(StateError, match='pending lifecycle delivery'):
                journal._commit('git_request_claimed', {'operation_id': operation,
                    'phase_id': phase, 'receipt_artifacts': refs})
        assert journal.state == before


def test_finish_retains_claim_references_and_consumed_attempt(delivery):
    with delivery() as (journal, phase, operation, refs, _, evidence):
        journal._consumed_git_request_operations.add(operation)
        claim(journal, phase, operation, refs)
        journal.finish_mutation(operation, receipt_artifacts=[evidence])
        state = journal.state
        assert state['active_mutation'] is None
        assert state['operations'][-1]['status'] == 'applied'
        assert state['operations'][-1]['receipt_artifacts'][:3] == refs
        assert journal._consumed_git_request_operations == {operation}
        assert json.loads(journal._owner_path.read_bytes())['operation'] is None


def test_unknown_effect_preserves_claim_and_reopened_reconciliation(delivery):
    with delivery() as (journal, phase, operation, refs, locks, evidence):
        claim(journal, phase, operation, refs)
        journal.reconcile_mutation(operation, 'unknown', receipt_artifacts=[evidence])
        state = journal.state
        assert state['execution_status'] == 'reconciliation_required'
        assert state['active_mutation'] == state['operations'][-1]
        assert state['active_mutation']['receipt_artifacts'][:3] == refs
        with pytest.raises(StateError, match='reconciliation'):
            claim(journal, phase, operation, refs)
    reopened = RunJournal.open(journal.run_dir, identities=journal.identities, clock=journal._clock)
    with reopened.lifecycle_lock(), reopened.mutation_lock(locks):
        assert reopened.current_mutation(phase, 'deliver', reconciliation=True) == operation
        with pytest.raises(StateError, match='reconciliation'):
            reopened.begin_mutation(phase, 'deliver')
        reopened.reconcile_mutation(operation, 'not_applied', receipt_artifacts=[evidence])
        assert reopened.state['operations'][-1]['receipt_artifacts'][:3] == refs
        assert reopened.state['active_mutation'] is None


def test_claim_event_survives_projection_write_failure(delivery, monkeypatch):
    with delivery() as (journal, phase, operation, refs, locks, _):
        def interrupted_projection():
            raise OSError('controlled projection interruption')
        monkeypatch.setattr(journal, '_write_projection', interrupted_projection)
        with pytest.raises(OSError, match='controlled projection interruption'):
            claim(journal, phase, operation, refs)
    reopened = RunJournal.open(journal.run_dir, identities=journal.identities, clock=journal._clock)
    assert reopened.state['operations'][-1]['receipt_artifacts'] == refs
    with reopened.lifecycle_lock(), reopened.mutation_lock(locks):
        assert reopened.current_mutation(phase, 'deliver', reconciliation=True) == operation
        with pytest.raises(StateError, match='reconciliation'):
            claim(reopened, phase, operation, refs)
