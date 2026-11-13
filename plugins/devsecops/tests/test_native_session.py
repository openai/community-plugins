"""Resident orchestration tests: controlled native boundary, real patch validator.

These fixtures never call a model or issue production native authority. Git,
kubeconform, journal acceptance and the fixed correction validator remain real.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
import os
from pathlib import Path
import pickle
import sys
import threading
import time

import pytest

from test_native_handoff import (controlled_dispatch, correction_case, pending, prepared)
from test_factory_lifecycle import finish_native
from devsecops.factory import native_codex, native_provenance, native_session as module
from devsecops.factory.native_session import NativeCodexSelection, NativeCorrectionSession
from devsecops.factory.state import StateError

__all__ = ['controlled_dispatch', 'correction_case', 'pending', 'prepared']


def selection(package='a' * 64):
    return NativeCodexSelection(installed_root=Path('/explicit/installed/plugin'),
        package_sha256=package, plugin_id='devsecops@devsecops-local',
        executable=Path('/explicit/codex'), expected_client_sha256='b' * 64,
        expected_client_version=native_codex.CLIENT_VERSION,
        model='gpt-6-astra', reasoning_effort='ultra',
        engine_python=Path('/explicit/venv/bin/python'), total_timeout=120)


def resident(pending):
    engine = pending[0].engine
    return NativeCorrectionSession(engine, selection=selection(engine.journal.identities['package_sha256']))


@pytest.mark.parametrize('change', [
    {'installed_root': Path('relative')}, {'engine_python': '/path/string'},
    {'package_sha256': 'not-a-hash'}, {'expected_client_sha256': 'c' * 63},
    {'expected_client_version': 'latest'}, {'model': ''}, {'model': '--fallback'},
    {'reasoning_effort': 'automatic'}, {'plugin_id': 'devsecops'},
    {'total_timeout': float('nan')}, {'total_timeout': 901}, {'total_timeout': True},
    {'max_trace_bytes': 1023}, {'max_trace_bytes': 64_000_001},
])
def test_closed_selection_rejects_unpinned_or_unbounded_inputs(change):
    with pytest.raises(StateError):
        replace(selection(), **change)


def test_selection_is_frozen_and_preserves_venv_invocation_path(tmp_path):
    target = tmp_path / 'actual-python'
    target.write_bytes(b'not executed')
    invocation = tmp_path / 'venv-python'
    invocation.symlink_to(target)
    chosen = replace(selection(), engine_python=invocation)
    event = threading.Event()
    assert chosen._options(event)['engine_python'] == invocation
    assert chosen._options(event)['cancel'] is event
    with pytest.raises(FrozenInstanceError):
        chosen.model = 'other-model'


def test_primary_and_replay_keep_distinct_originals_while_engine_advances(pending, controlled_dispatch):
    session = resident(pending)
    coordinator, scope, args, _, _, driver = pending
    controlled_dispatch['patch'] = args.patch.read_bytes()
    primary = session.execute_pending(scope)
    assert session.origin(attempt='primary') is primary
    proof = native_provenance.verify_native_execution_origin(primary,
        artifacts=controlled_dispatch['origin_artifacts'])
    assert proof['bindings_match'] and not proof['verified']
    assert proof['execution_mode'] == 'controlled-test'
    assert 'primary-deliver' not in driver.calls
    result = session.advance()
    assert coordinator.engine.journal.state['active_native']['step_id'] == 'primary-recovery'
    assert session.origin(attempt='primary') is primary
    # Other native workflows remain external controlled fixtures; the session
    # does not implement or authenticate them. No full-demo claim is possible.
    for _ in range(3):
        result = finish_native(coordinator.engine, result)
    assert coordinator.engine.journal.state['active_native']['step_id'] == 'replay-correction'
    replay = session.execute_pending(scope)
    assert replay is not primary and session.origin(attempt='replay') is replay
    assert session.origin(attempt='primary') is primary
    assert controlled_dispatch['calls'] == 2
    assert 'replay-deliver' not in driver.calls
    session.advance()
    records = coordinator.engine.journal.state['lifecycle']['phase_records']
    for step in ('primary-correction', 'replay-correction'):
        assert sum(row['phase_id'] == step for row in records) == 1
        assert next(row for row in records if row['phase_id'] == step)['code'] == 'native-validation-recovered'


def test_exact_selection_and_cancel_event_reach_fixed_native_boundary(pending, monkeypatch):
    session = resident(pending)
    observed = {}
    def dispatch(journal, **kwargs):
        observed.update(kwargs)
        raise StateError('controlled stop before native invocation')
    monkeypatch.setattr(native_provenance, 'execute_validated_correction', dispatch)
    with pytest.raises(StateError, match='controlled stop'):
        session.execute_pending(pending[1])
    options = observed['native_options']
    chosen = selection(pending[0].engine.journal.identities['package_sha256'])
    assert {key: options[key] for key in options if key not in ('cancel', 'total_timeout')} == {
        key: value for key, value in chosen._options(None).items() if key not in ('cancel', 'total_timeout')}
    assert 0 < options['total_timeout'] <= 120
    assert type(options['cancel']) is threading.Event
    assert observed['expected_client_sha256'] == chosen.expected_client_sha256
    assert observed['request_sha256'] == pending[4]['request_sha256']
    assert observed['_controlled_test'] is True


def test_new_session_cannot_restore_accepted_origin_from_journal(pending, controlled_dispatch):
    session = resident(pending)
    controlled_dispatch['patch'] = pending[2].patch.read_bytes()
    session.execute_pending(pending[1])
    other = resident(pending)
    before = list(pending[5].calls)
    for operation in (lambda: other.origin(attempt='primary'),
                      lambda: other.execute_pending(pending[1]), other.advance):
        with pytest.raises(StateError, match='saved evidence cannot restore'):
            operation()
    assert pending[5].calls == before
    assert controlled_dispatch['calls'] == 1
    with pytest.raises(StateError, match='cannot be serialized'):
        pickle.dumps(session)


def test_foreign_acceptance_at_lock_entry_cannot_race_origin_guard(pending, controlled_dispatch, monkeypatch):
    session = resident(pending)
    outsider, scope, args, _, call, driver = pending
    controlled_dispatch['patch'] = args.patch.read_bytes()
    journal = outsider.engine.journal
    original = journal.lifecycle_lock
    entered = []
    @contextmanager
    def acceptance_before_lock():
        # Model another normal runner's acceptance immediately before this
        # runner acquires the lifecycle lock. It owns the origin, not session.
        if not entered:
            entered.append(True)
            outsider.execute(scope, **call)
        with original():
            yield journal
    monkeypatch.setattr(journal, 'lifecycle_lock', acceptance_before_lock)
    with pytest.raises(StateError, match='saved evidence cannot restore'):
        session.advance()
    assert journal.state['active_native'] is None
    assert controlled_dispatch['calls'] == 1 and 'primary-deliver' not in driver.calls


def test_lost_origin_after_acceptance_stops_advance_and_retry_but_allows_cleanup(pending, controlled_dispatch):
    session = resident(pending)
    controlled_dispatch.update(patch=pending[2].patch.read_bytes(), after_accept_error=True)
    with pytest.raises(StateError, match='publication failure'):
        session.execute_pending(pending[1])
    assert pending[0].engine.journal.state['active_native'] is None
    for operation in (session.advance, lambda: session.execute_pending(pending[1]),
                      lambda: session.origin(attempt='primary')):
        with pytest.raises(StateError, match='Resident session stopped'):
            operation()
    assert controlled_dispatch['calls'] == 1 and 'primary-deliver' not in pending[5].calls
    session.interrupt()
    assert 'cleanup' in pending[5].calls and 'primary-deliver' not in pending[5].calls
    assert not pending[0].engine.journal.state['environment_reserved']
    with pytest.raises(StateError, match='already attempted'):
        session.interrupt()


def test_invalid_patch_claim_cannot_be_retried_even_in_new_session(pending, controlled_dispatch):
    session = resident(pending)
    controlled_dispatch['patch'] = pending[2].patch.read_bytes().replace(b'deploy/policy.yaml', b'deploy/foreign.yaml')
    with pytest.raises((StateError, ValueError)):
        session.execute_pending(pending[1])
    assert pending[0].engine.journal.state['active_native'] is not None
    with pytest.raises(StateError, match='stopped'):
        session.execute_pending(pending[1])
    with pytest.raises(StateError, match='already claimed'):
        resident(pending).execute_pending(pending[1])
    assert controlled_dispatch['calls'] == 1


@pytest.mark.parametrize('artifact', ['request', 'result', 'validation', 'patch'])
def test_changed_accepted_bytes_block_origin_and_later_advance(pending, controlled_dispatch, artifact):
    session = resident(pending)
    controlled_dispatch['patch'] = pending[2].patch.read_bytes()
    session.execute_pending(pending[1])
    journal = pending[0].engine.journal
    state = journal.state
    accepted = state['native_results'][-1]
    reference = (state['native_requests'][-1]['request'] if artifact == 'request' else
                 accepted['artifacts'][0] if artifact == 'patch' else accepted[artifact])
    (journal.run_dir / reference['path']).write_bytes(b'changed retained evidence')
    before = list(pending[5].calls)
    with pytest.raises(StateError):
        session.origin(attempt='primary')
    with pytest.raises(StateError):
        session.advance()
    assert pending[5].calls == before


def test_unsupported_native_yield_is_untouched_and_never_dispatched(pending, controlled_dispatch):
    session = resident(pending)
    controlled_dispatch['patch'] = pending[2].patch.read_bytes()
    session.execute_pending(pending[1])
    result = session.advance()
    journal = pending[0].engine.journal
    before = journal.state
    with pytest.raises(StateError, match='Only a pending primary or replay'):
        session.execute_pending(pending[1])
    again = session.advance()
    assert again['execution_status'] == result['execution_status'] == 'awaiting_native_step'
    for name in ('request_path', 'request_sha256', 'result_path'):
        assert again['native_request'][name] == result['native_request'][name]
    assert journal.state == before and controlled_dispatch['calls'] == 1


def test_cancellation_before_dispatch_is_irreversible_without_model_or_cleanup(pending, controlled_dispatch):
    session = resident(pending)
    thread = threading.Thread(target=session.cancel)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()
    with pytest.raises(StateError, match='stopped'):
        session.execute_pending(pending[1])
    assert controlled_dispatch['calls'] == 0 and 'cleanup' not in pending[5].calls
    session.interrupt()
    assert 'cleanup' in pending[5].calls and 'primary-deliver' not in pending[5].calls


def test_cancel_during_dispatch_reaches_child_and_prevents_retry(pending, monkeypatch):
    session = resident(pending)
    calls = []
    def dispatch(journal, **kwargs):
        calls.append(kwargs)
        cancel = kwargs['native_options']['cancel']
        worker = threading.Thread(target=session.cancel)
        worker.start()
        worker.join(timeout=1)
        assert cancel.is_set()
        raise StateError('controlled child cancellation')
    monkeypatch.setattr(native_provenance, 'execute_validated_correction', dispatch)
    with pytest.raises(StateError, match='child cancellation'):
        session.execute_pending(pending[1])
    with pytest.raises(StateError, match='stopped'):
        session.advance()
    assert len(calls) == 1 and 'cleanup' not in pending[5].calls


def test_cancel_racing_successful_acceptance_never_advances(pending, controlled_dispatch):
    session = resident(pending)
    controlled_dispatch.update(patch=pending[2].patch.read_bytes(), before_validate=session.cancel)
    with pytest.raises(StateError, match='stopped'):
        session.execute_pending(pending[1])
    assert pending[0].engine.journal.state['active_native'] is None
    with pytest.raises(StateError, match='stopped'):
        session.advance()
    assert 'primary-deliver' not in pending[5].calls


def test_session_cancel_stops_and_reaps_actual_synthetic_child(pending, monkeypatch, tmp_path):
    session = resident(pending)
    started = tmp_path / 'synthetic-child.pid'
    observed = []
    def dispatch(journal, **kwargs):
        # A real local subprocess exercises cancellation plumbing; this is not
        # a Codex/model process, native execution receipt or production origin.
        stop_waiting = threading.Event()
        def stop_when_started():
            until = time.monotonic() + 3
            while not stop_waiting.wait(.01) and time.monotonic() < until:
                if started.is_file():
                    session.cancel()
                    return
        worker = threading.Thread(target=stop_when_started)
        worker.start()
        try:
            outcome = native_codex._process([sys.executable, '-I', '-B', '-c',
                'import os, pathlib, sys, time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)',
                str(started)], cwd=tmp_path, stdin=b'', deadline=time.monotonic() + 5,
                limit=4096, environ={}, cancel=kwargs['native_options']['cancel'])
            observed.append(outcome)
        finally:
            stop_waiting.set()
            worker.join(timeout=1)
        raise StateError('controlled synthetic child cancelled')
    monkeypatch.setattr(native_provenance, 'execute_validated_correction', dispatch)
    begin = time.monotonic()
    with pytest.raises(StateError, match='synthetic child cancelled'):
        session.execute_pending(pending[1])
    assert time.monotonic() - begin < 5
    assert observed[0]['spawned'] and observed[0]['reason'] == 'cancelled'
    with pytest.raises(ProcessLookupError):
        os.kill(int(started.read_text()), 0)
    assert pending[0].engine.journal.state['active_native'] is not None
    assert 'primary-deliver' not in pending[5].calls and 'cleanup' not in pending[5].calls
    with pytest.raises(StateError, match='stopped'):
        session.advance()


def test_keyboard_interrupt_never_retries_or_implicitly_cleans_up(pending, monkeypatch):
    session = resident(pending)
    calls = []
    def dispatch(*args, **kwargs):
        calls.append(True)
        raise KeyboardInterrupt
    monkeypatch.setattr(native_provenance, 'execute_validated_correction', dispatch)
    with pytest.raises(KeyboardInterrupt):
        session.execute_pending(pending[1])
    with pytest.raises(StateError, match='stopped'):
        session.execute_pending(pending[1])
    assert len(calls) == 1 and 'cleanup' not in pending[5].calls


def test_reentrant_execution_never_launches_second_child(pending, monkeypatch):
    session = resident(pending)
    calls = []
    def dispatch(*args, **kwargs):
        calls.append(True)
        with pytest.raises(StateError, match='already in progress'):
            session.execute_pending(pending[1])
        raise StateError('controlled terminal failure')
    monkeypatch.setattr(native_provenance, 'execute_validated_correction', dispatch)
    with pytest.raises(StateError, match='terminal failure'):
        session.execute_pending(pending[1])
    assert len(calls) == 1


def test_process_identity_and_package_mismatch_reject_before_dispatch(pending, monkeypatch):
    with pytest.raises(StateError, match='package differs'):
        NativeCorrectionSession(pending[0].engine, selection=selection('f' * 64))
    session = resident(pending)
    pid = os.getpid()
    monkeypatch.setattr(module.os, 'getpid', lambda: pid + 1)
    for operation in (session.advance, session.cancel, session.interrupt):
        with pytest.raises(StateError, match='invoking process'):
            operation()


def test_replacing_bound_driver_refuses_continuation(pending):
    session = resident(pending)
    pending[0].engine.driver = object()
    with pytest.raises(StateError, match='identity changed'):
        session.advance()
