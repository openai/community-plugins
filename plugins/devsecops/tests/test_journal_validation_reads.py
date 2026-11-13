"""A validator may inspect its held journal; it may not reenter writes."""
import threading

import pytest

from devsecops.factory.state import RunJournal, StateError
from test_factory_state import clock, identities, journal, write, native_result

__all__ = ['clock', 'identities', 'journal']


def test_native_validator_reads_current_state_but_cannot_mutate(journal, tmp_path):
    with journal.mutation_lock(tmp_path / 'locks'):
        descriptor = journal.request_native('review', 'review-kubernetes-change', input_artifacts={})
        result, _ = native_result(journal, descriptor)
        def validate(request, result, artifacts):
            observed = journal.state
            assert observed['active_native']['request_id'] == request['request_id']
            observed['run_id'] = 'changed-copy'
            assert journal.state['run_id'] != 'changed-copy'
            with pytest.raises(StateError, match='read-only validation'):
                journal.cancel_native('not-allowed')
            with pytest.raises(StateError, match='read-only validation'):
                journal._commit('native_cancelled', {})
            return ['original-locked-state-read']
        journal.accept_native(result, identities=journal.identities, validator=validate)
    assert journal.state['active_native'] is None
    assert journal._validation_read_owner is None and journal._run_lock_owner is None


def test_cleanup_validator_reads_original_operation_and_blocks_other_reader(journal, tmp_path):
    receipt = write(tmp_path / 'receipt', b'controlled cleanup')
    with journal.mutation_lock(tmp_path / 'locks'):
        operation = journal.begin_mutation('down', 'down')
        def validate(receipts):
            assert journal.current_mutation('down', 'down') == operation
            errors = []
            def competing():
                try:
                    journal.state
                except StateError as error:
                    errors.append(str(error))
            thread = threading.Thread(target=competing)
            thread.start()
            thread.join(timeout=2)
            assert not thread.is_alive() and len(errors) == 1
            assert 'Another operation' in errors[0]
            with pytest.raises(StateError, match='Another operation'):
                RunJournal.open(journal.run_dir, identities=journal.identities)
            assert journal.current_mutation('down', 'down') == operation
            return ['original-operation-observed']
        journal.complete_cleanup(operation, receipt_artifacts=[receipt], validator=validate)
    assert journal.state['execution_status'] == 'released'


def test_validator_failure_clears_read_scope_and_preserves_pending_request(journal, tmp_path):
    with journal.mutation_lock(tmp_path / 'locks'):
        descriptor = journal.request_native('review', 'review-kubernetes-change', input_artifacts={})
        result, _ = native_result(journal, descriptor)
        def fail(*_):
            journal.state
            raise RuntimeError('fixture rejection')
        with pytest.raises(RuntimeError, match='fixture rejection'):
            journal.accept_native(result, identities=journal.identities, validator=fail)
        assert journal._validation_read_owner is None and journal._run_lock_owner is None
        assert journal.state['active_native'] is not None
        journal.cancel_native('operator-stopped')
    assert journal.state['active_native'] is None


def test_validation_read_scope_is_not_available_outside_original_lock(journal):
    with pytest.raises(StateError, match='original journal lock owner'):
        with journal._validation_reads():
            pass
