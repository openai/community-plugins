"""Current local Git request claims; native Git, controlled reads, zero dispatch."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import pickle
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

from devsecops.factory import git_base_possession as base
from devsecops.factory import git_request_claim as claim
from devsecops.factory import evidence_export as export
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha
from test_deployment_commit import case
from test_factory_lifecycle import inputs_for
from test_factory_evidence_export import _context, _archive
from test_git_base_possession import subject
from test_git_native_request import advert
from test_git_transport import SCOPE

__all__ = ['case']


@pytest.fixture
def ready(case, tmp_path, monkeypatch):
    inputs, _ = inputs_for(tmp_path)
    inputs['config']['expires_at'] = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    raw = {name: _canonical(value) if isinstance(value, dict) else value.read_bytes()
           for name, value in inputs.items()}
    identities = {name+'_sha256': _sha(value) for name, value in raw.items()}
    journal = RunJournal.create(tmp_path/'run', identities=identities,
                                environment_id=inputs['config']['environment'])
    journal.initialize_lifecycle(inputs=raw, execution_mode='controlled-test', driver_id='owned-linux')
    values = {name: json.loads(value) for name, value in raw.items() if name != 'package'}
    evidence = journal.run_dir/'controlled-local-evidence'
    evidence.write_bytes(b'controlled local fixture, no live effect\n')
    evidence.chmod(0o600)
    case['args']['run_scope'] = identities
    case['binding']['run_scope'] = identities
    state = subject.__wrapped__(case, tmp_path, monkeypatch)
    report, observed = base.verify_git_base_possession(**state.args)
    assert report['decision'] == 'pass'
    with ExitStack() as stack:
        stack.enter_context(journal.lifecycle_lock())
        stack.enter_context(journal.mutation_lock(tmp_path/'locks'))
        for phase in values['scenario']['phases']:
            journal.begin_phase(phase['id'])
            if phase['operation'] == 'deliver':
                break
            journal.finish_phase(phase['id'], 'completed', 'controlled-local-fixture',
                                 receipt_artifacts=[evidence])
        operation = journal.begin_mutation(phase['id'], 'deliver', intent_artifacts=[evidence])
        request = claim.prepare_operation_request(journal, phase['id'], transport=state.plan,
            observed_base=observed, **{name: state.args[name] for name in SCOPE},
            expected_destination_sha256=state.args['expected_config_sha256'], advertisement=advert(state))
        yield SimpleNamespace(journal=journal, phase=phase['id'], operation=operation,
            request=request, state=state, observed=observed, evidence=evidence, values=values,
            marker=journal.run_dir/'operations'/operation/'git-request-claim')


def take(ready):
    return claim.claim_operation_request(ready.journal, ready.phase, ready.request)


def test_claim_reconcile_export_reopen_preserves_all_three_refs(ready):
    handle = take(ready)
    description = handle.describe()
    assert description['claim_status'] == 'consumed'
    assert description['writer_grant'] == 'missing'
    assert description['remote_effect'] == 'not-established'
    state = ready.journal.state
    assert state['execution_status'] == 'mutation_pending'
    assert state['active_mutation']['status'] == 'pending'
    refs = state['active_mutation']['receipt_artifacts']
    assert [r['path'].rsplit('/', 1)[1] for r in refs] == list(claim._NAMES)
    assert refs == state['operations'][-1]['receipt_artifacts']
    assert json.loads((ready.marker/'claim.json').read_bytes()) == description
    report = json.loads((ready.marker/'native-request.json').read_bytes())
    assert report['native_delivery_exit'] is None
    assert report['write_attempts'] == report['network_requests'] == 0
    with pytest.raises(StateError):
        take(ready)
    ready.journal.reconcile_mutation(ready.operation, 'unknown', receipt_artifacts=[ready.evidence])
    assert ready.journal.state['active_mutation']['receipt_artifacts'][:3] == refs
    with pytest.raises(StateError):
        handle.describe()
    ready.journal.reconcile_mutation(ready.operation, 'not_applied', receipt_artifacts=[ready.evidence])
    assert ready.journal.state['operations'][-1]['receipt_artifacts'][:3] == refs
    ready.journal.finish_phase(ready.phase, 'completed', 'controlled-local-fixture',
                               receipt_artifacts=[ready.evidence])
    phases = ready.values['scenario']['phases']
    start = next(i for i, p in enumerate(phases) if p['id'] == ready.phase)+1
    for phase in phases[start:]:
        ready.journal.begin_phase(phase['id'])
        if phase['id'] == 'export':
            break
        ready.journal.finish_phase(phase['id'], 'completed', 'controlled-local-fixture',
                                   receipt_artifacts=[ready.evidence])
    context = _context(ready.journal, ready.values)
    with export.export_checkpoint(ready.journal, context, deadline=context.deadline) as artifacts:
        _, members = _archive(artifacts)
        for ref in refs:
            assert members['artifacts/'+ref['sha256']] == (ready.journal.run_dir/ref['path']).read_bytes()
        assert any(b'"type":"git_request_claimed"' in raw for name, raw in members.items()
                   if name.startswith('journal/'))
    reopened = RunJournal.open(ready.journal.run_dir, identities=ready.journal.identities)
    assert reopened.state['operations'][-1]['receipt_artifacts'][:3] == refs
    with pytest.raises(StateError):
        claim.claim_operation_request(reopened, ready.phase, ready.request)


def test_direct_handle_scope_cancellation_and_expiry_reject_before_reservation(ready, monkeypatch):
    for fake in ({}, ready.request.describe(), object.__new__(claim.PreparedNativeRequest)):
        with pytest.raises(StateError):
            claim.claim_operation_request(ready.journal, ready.phase, fake)
    with pytest.raises(TypeError):
        pickle.dumps(ready.request)
    with pytest.raises(StateError):
        claim.claim_operation_request(ready.journal, 'unselected', ready.request)
    reopened = RunJournal.open(ready.journal.run_dir, identities=ready.journal.identities)
    with pytest.raises(StateError):
        claim.claim_operation_request(reopened, ready.phase, ready.request)
    record = claim._REQUESTS[ready.request]
    pid = record['pid']
    record['pid'] = pid+1
    with pytest.raises(StateError):
        take(ready)
    record['pid'] = pid
    errors = []
    def other_thread():
        try:
            take(ready)
        except StateError as error:
            errors.append(error)
    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join(timeout=5)
    assert len(errors) == 1 and not thread.is_alive()
    original = record['started_mono']
    record['started_mono'] = time.monotonic()-121
    with pytest.raises(StateError):
        take(ready)
    record['started_mono'] = original
    ready.request.cancel()
    with pytest.raises(StateError):
        take(ready)
    assert not ready.marker.exists()
    assert ready.operation not in ready.journal._consumed_git_request_operations


def test_current_native_bytes_and_base_are_not_replaceable(ready):
    record = claim._REQUESTS[ready.request]
    for name in ('request.body', 'native-request.json'):
        target = record['output']/name
        original = target.read_bytes()
        target.write_bytes(original+b' ')
        with pytest.raises(ValueError):
            take(ready)
        target.write_bytes(original)
    observation = base._OBSERVATIONS[ready.observed]
    observation['expires_monotonic'] = time.monotonic()-1
    with pytest.raises(ValueError):
        take(ready)
    assert not ready.marker.exists()


@pytest.mark.parametrize('failure', ['parent-fsync', 'partial-file', 'before-event', 'after-event'])
def test_interrupted_publication_is_spent_even_if_marker_removed(ready, monkeypatch, failure):
    original_publish = claim._publish
    original_record = ready.journal._record_git_request_claim_locked
    if failure == 'parent-fsync':
        monkeypatch.setattr(claim.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('controlled fsync')))
    elif failure == 'partial-file':
        def publish(directory, name, raw):
            original_publish(directory, name, raw[:1])
            raise OSError('controlled partial publication')
        monkeypatch.setattr(claim, '_publish', publish)
    else:
        def record(*args):
            if failure == 'after-event':
                original_record(*args)
            raise OSError('controlled event interruption')
        monkeypatch.setattr(ready.journal, '_record_git_request_claim_locked', record)
    with pytest.raises(OSError):
        take(ready)
    monkeypatch.undo()
    assert ready.operation in ready.journal._consumed_git_request_operations
    assert ready.marker.is_dir()
    state = ready.journal.state
    assert state['execution_status'] == 'mutation_pending'
    assert state['active_mutation']['status'] == 'pending'
    assert bool(state['active_mutation']['receipt_artifacts']) == (failure == 'after-event')
    with pytest.raises(StateError):
        take(ready)
    reopened = RunJournal.open(ready.journal.run_dir, identities=ready.journal.identities)
    with pytest.raises(StateError):
        claim.claim_operation_request(reopened, ready.phase, ready.request)
    shutil.rmtree(ready.marker)
    with pytest.raises((StateError, OSError)):
        take(ready)
    assert not ready.marker.exists()


def test_claim_marker_substitution_and_final_drift_never_restore_attempt(ready, monkeypatch):
    original = ready.journal._record_git_request_claim_locked
    def publish(*args):
        original(*args)
        ready.request.cancel()
    monkeypatch.setattr(ready.journal, '_record_git_request_claim_locked', publish)
    with pytest.raises(StateError):
        take(ready)
    assert ready.operation in ready.journal._consumed_git_request_operations
    assert len(ready.journal.state['active_mutation']['receipt_artifacts']) == 3
    assert ready.journal.state['execution_status'] == 'mutation_pending'


def test_live_claim_checks_marker_inode_bytes_and_membership(ready):
    handle = take(ready)
    target = ready.marker/'claim.json'
    original = target.read_bytes()
    target.write_bytes(original+b' ')
    with pytest.raises(ValueError):
        handle.describe()
    target.write_bytes(original)
    extra = ready.marker/'extra'
    extra.write_bytes(b'extra')
    with pytest.raises(StateError):
        handle.describe()
    extra.unlink()
    moved = ready.marker.with_name('old-claim')
    ready.marker.rename(moved)
    shutil.copytree(moved, ready.marker)
    with pytest.raises(StateError):
        handle.describe()
    with pytest.raises(StateError):
        take(ready)


def test_exact_config_reread_is_bound_before_parsing(ready, monkeypatch):
    original = claim._read
    configuration = ready.journal.state['lifecycle']['inputs']['config']['path']
    def changed(path, maximum):
        raw = original(path, maximum)
        if path == ready.journal.run_dir/configuration:
            value = json.loads(raw)
            value['expires_at'] = (datetime.now(timezone.utc)+timedelta(days=3)).isoformat()
            return _canonical(value)
        return raw
    monkeypatch.setattr(claim, '_read', changed)
    with pytest.raises(StateError, match='Exact current lifecycle configuration bytes'):
        take(ready)
    assert not ready.marker.exists()


def test_operation_replaced_before_reservation_receives_no_marker(ready, monkeypatch):
    original = claim._open_directory
    operation_path = ready.marker.parent
    saved = operation_path.with_name('saved-original-operation')
    calls = []
    def replaced(path):
        if path == operation_path:
            calls.append(path)
            if len(calls) == 3:
                operation_path.rename(saved)
                operation_path.mkdir(mode=0o700)
        return original(path)
    monkeypatch.setattr(claim, '_open_directory', replaced)
    try:
        with pytest.raises(StateError, match='changed before claim reservation'):
            take(ready)
        assert len(calls) == 3
        assert list(operation_path.iterdir()) == []
        assert ready.operation not in ready.journal._consumed_git_request_operations
    finally:
        if saved.exists():
            operation_path.rmdir()
            saved.rename(operation_path)
    monkeypatch.setattr(claim, '_open_directory', original)
    assert take(ready).describe()['claim_status'] == 'consumed'


@pytest.mark.parametrize('issued_claim', [False, True])
def test_real_lifecycle_abort_revokes_prepared_and_claim_handles(ready, issued_claim):
    handle = take(ready) if issued_claim else None
    ready.journal.abort_lifecycle(ready.phase, 'incomplete', 'controlled-local-cancellation')
    with pytest.raises(StateError, match='Current exact owned Linux deliver phase'):
        ready.request.describe()
    with pytest.raises(StateError, match='Current exact owned Linux deliver phase'):
        take(ready)
    if handle is not None:
        with pytest.raises(StateError, match='Current exact owned Linux deliver phase'):
            handle.describe()
        assert ready.marker.is_dir()
        assert ready.operation in ready.journal._consumed_git_request_operations
    else:
        assert not ready.marker.exists()
        assert ready.operation not in ready.journal._consumed_git_request_operations
    # The local evidence guard must leave generic reconciliation available.
    assert ready.journal.current_mutation(ready.phase, 'deliver', reconciliation=True) == ready.operation
    assert ready.journal.state['execution_status'] == 'mutation_pending'


@pytest.mark.parametrize('drift', ['base-during-final-output-read', 'config-during-final-source-audit'])
def test_final_authority_drift_withdraws_handle_but_keeps_spent_evidence(ready, monkeypatch, drift):
    request = claim._REQUESTS[ready.request]
    original_read = claim._read_retained
    original_record = ready.journal._record_git_request_claim_locked
    original_observed = claim.require_observed_git_base
    state = {'published': False, 'output_reads': 0, 'base_audits': 0, 'future': False}
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz)+(timedelta(days=2) if state['future'] else timedelta())
    def record(*args):
        original_record(*args)
        state['published'] = True
    def retained(directory, name, expected, deadline, **kwargs):
        result = original_read(directory, name, expected, deadline, **kwargs)
        if (state['published'] and name == 'native-request.json'
                and claim._identity(directory) == request['output_identity']):
            state['output_reads'] += 1
            if state['output_reads'] == 2 and drift == 'base-during-final-output-read':
                base._OBSERVATIONS[ready.observed]['expires_monotonic'] = time.monotonic()-1
        return result
    def observed(*args, **kwargs):
        result = original_observed(*args, **kwargs)
        if state['published']:
            state['base_audits'] += 1
            if state['base_audits'] == 4 and drift == 'config-during-final-source-audit':
                state['future'] = True
        return result
    monkeypatch.setattr(ready.journal, '_record_git_request_claim_locked', record)
    monkeypatch.setattr(claim, '_read_retained', retained)
    monkeypatch.setattr(claim, 'require_observed_git_base', observed)
    monkeypatch.setattr(claim, 'datetime', Clock)
    with pytest.raises(ValueError, match='expired'):
        take(ready)
    assert state['output_reads'] == 2
    assert ready.operation in ready.journal._consumed_git_request_operations
    assert ready.journal.state['execution_status'] == 'mutation_pending'
    assert len(ready.journal.state['active_mutation']['receipt_artifacts']) == 3
    assert ready.marker.is_dir()
