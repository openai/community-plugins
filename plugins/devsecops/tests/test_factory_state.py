"""Unit fixtures for lifecycle safety, never receipts for a live platform."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from devsecops.factory import state as state_module
from devsecops.factory.state import IDENTITY_KEYS, RunJournal, StateError


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


@pytest.fixture
def identities():
    return {key: hashlib.sha256(key.encode()).hexdigest() for key in IDENTITY_KEYS}


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def journal(tmp_path, identities, clock):
    return RunJournal.create(tmp_path.resolve() / 'run with spaces', identities=identities,
                             environment_id='unit-fixture-environment', clock=clock)


def write(path, value):
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
    path.chmod(0o600)
    return path


def native_result(journal, descriptor, *, status='completed'):
    request = json.loads(Path(descriptor['request_path']).read_text())
    artifacts = []
    if status == 'completed':
        path = write(journal.run_dir / request['output_directory'] / 'unit-proposal.txt', b'unit fixture only\n')
        artifacts = [{'name': 'proposal', 'kind': request['required_artifact_kind'],
                      'path': str(path.relative_to(journal.run_dir)), 'bytes': path.stat().st_size,
                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
    value = {'schema_version': 1, 'request_id': request['request_id'], 'run_id': request['run_id'],
             'step_id': request['step_id'], 'request_sha256': descriptor['request_sha256'],
             'status': status, 'artifacts': artifacts,
             'gaps': [] if status == 'completed' else ['unit-fixture-missing-evidence']}
    return write(Path(descriptor['result_path']), value), value


def validate_fixture(request, result, artifacts):
    assert request['workflow'] != 'run-software-factory-demo'
    assert 'decision' not in result
    assert not artifacts or artifacts['proposal'] == b'unit fixture only\n'
    return ['unit-fixture-artifact-checked']


def test_private_initial_state_has_no_platform_pass(journal):
    state = journal.state
    assert state['execution_status'] == 'ready'
    assert state['cleanup'] == 'always'
    assert not state['environment_reserved']
    assert 'decision' not in state and 'scenario_assertion_status' not in state
    for path in [journal.run_dir, *journal.run_dir.rglob('*')]:
        assert path.stat().st_mode & 0o077 == 0


def test_cached_journal_validation_still_rehashes_every_event(journal):
    journal.state
    event = journal.run_dir / 'journal' / '00000001.json'
    value = json.loads(event.read_bytes())
    value['payload']['environment_id'] = 'changed-fixture'
    event.write_text(json.dumps(value))
    with pytest.raises(StateError, match='projection'):
        journal.state


def test_cached_journal_validation_rechecks_bound_artifact_bytes(journal, tmp_path):
    receipt = write(tmp_path / 'receipt.json', b'fixture initial')
    with journal.mutation_lock(tmp_path / 'locks'):
        operation = journal.begin_mutation('up', 'fixture-create')
        journal.finish_mutation(operation, receipt_artifacts=[receipt])
    state = journal.state
    frozen = journal.run_dir / state['operations'][0]['receipt_artifacts'][0]['path']
    before = frozen.stat()
    frozen.write_bytes(b'fixture changed')
    os.utime(frozen, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(StateError, match='bound artifact'):
        journal.state


def test_cached_journal_validation_rechecks_current_identity_binding(journal):
    journal.state
    journal.identities['package_sha256'] = 'e' * 64
    with pytest.raises(StateError, match='identity changed'):
        journal.state


@pytest.mark.parametrize('key', sorted(IDENTITY_KEYS))
def test_resume_rejects_every_changed_binding(journal, identities, key):
    changed = {**identities, key: 'f' * 64}
    with pytest.raises(StateError, match='identity changed'):
        RunJournal.open(journal.run_dir, identities=changed)


def test_identity_schema_rejects_unknown_fields(tmp_path, identities):
    with pytest.raises(StateError, match='immutable'):
        RunJournal.create(tmp_path / 'bad', identities={**identities, 'live_pass': 'a' * 64}, environment_id='fixture')


def test_mutation_requires_lock_and_observation(journal, tmp_path):
    with pytest.raises(StateError, match='environment lock'):
        journal.begin_mutation('up', 'fixture-create')
    receipt = write(tmp_path / 'observation.json', {'fixture': True, 'observed': 'applied'})
    with journal.mutation_lock(tmp_path / 'locks'):
        operation = journal.begin_mutation('up', 'fixture-create')
        with pytest.raises(StateError, match='observation artifacts'):
            journal.finish_mutation(operation)
        journal.finish_mutation(operation, receipt_artifacts=[receipt])
    assert journal.state['operations'][0]['status'] == 'applied'
    assert journal.state['environment_reserved']


def test_process_lock_and_persistent_owner_block_other_run(journal, tmp_path, identities):
    other = RunJournal.create(tmp_path / 'other-run', identities=identities, environment_id='same-fixture')
    with journal.mutation_lock(tmp_path / 'locks'):
        with pytest.raises(StateError, match='holds the environment'):
            with other.mutation_lock(tmp_path / 'locks'):
                pytest.fail('Concurrent mutation lock acquired')
    with pytest.raises(StateError, match='belongs to another run'):
        with other.mutation_lock(tmp_path / 'locks'):
            pytest.fail('Persistent environment ownership stolen')


def test_crash_after_journal_append_repairs_projection_and_requires_reconciliation(
        journal, tmp_path, identities, clock, monkeypatch):
    with journal.mutation_lock(tmp_path / 'locks'):
        prior = json.loads((journal.run_dir / 'state.json').read_text())
        monkeypatch.setattr(journal, '_write_projection', lambda: (_ for _ in ()).throw(OSError('unit crash')))
        with pytest.raises(OSError, match='unit crash'):
            journal.begin_mutation('deliver', 'fixture-push')
    assert json.loads((journal.run_dir / 'state.json').read_text()) == prior
    recovered = RunJournal.open(journal.run_dir, identities=identities, clock=clock)
    assert recovered.needs_reconciliation
    operation = recovered.state['active_mutation']['operation_id']
    observation = write(tmp_path / 'not-applied.json', {'fixture': True, 'observed': 'not_applied'})
    with recovered.mutation_lock(tmp_path / 'locks'):
        with pytest.raises(StateError, match='reconciliation'):
            recovered.finish_mutation(operation, receipt_artifacts=[observation])
        with pytest.raises(StateError, match='reconciliation'):
            recovered.begin_mutation('deliver', 'fixture-push')
        recovered.reconcile_mutation(operation, 'not_applied', receipt_artifacts=[observation])
        replacement = recovered.begin_mutation('deliver', 'fixture-push')
        assert replacement != operation


def test_crash_after_owner_intent_before_journal_is_recoverable(
        journal, tmp_path, identities, clock, monkeypatch):
    original = journal._commit
    def fail_before_append(kind, payload):
        if kind == 'mutation_started':
            raise OSError('unit crash before append')
        original(kind, payload)
    with journal.mutation_lock(tmp_path / 'locks'):
        monkeypatch.setattr(journal, '_commit', fail_before_append)
        with pytest.raises(OSError):
            journal.begin_mutation('deliver', 'fixture-push')
    recovered = RunJournal.open(journal.run_dir, identities=identities, clock=clock)
    with recovered.mutation_lock(tmp_path / 'locks'):
        assert recovered.needs_reconciliation
        with pytest.raises(StateError, match='reconciliation'):
            recovered.begin_mutation('deliver', 'fixture-push')


def test_unknown_observation_cannot_enable_retry(journal, tmp_path, identities):
    receipt = write(tmp_path / 'unknown.json', {'fixture': True, 'observed': 'unknown'})
    with journal.mutation_lock(tmp_path / 'locks'):
        operation = journal.begin_mutation('deliver', 'fixture-push')
        journal.reconcile_mutation(operation, 'unknown', receipt_artifacts=[receipt])
        assert journal.state['execution_status'] == 'reconciliation_required'
        with pytest.raises(StateError, match='reconciliation'):
            journal.begin_mutation('deliver', 'fixture-push')
        with pytest.raises(StateError, match='Outstanding work'):
            journal.release_environment(receipt_artifacts=[receipt], validator=lambda _: ['fixture-check'])


def test_native_handoff_retains_cleanup_and_environment_owner(journal, tmp_path, identities):
    with journal.mutation_lock(tmp_path / 'locks'):
        descriptor = journal.request_native('D2.correction', 'prepare-gitops-correction')
    assert descriptor['exit_code'] == 3 and descriptor['terminal'] is False
    assert journal.state['execution_status'] == 'awaiting_native_step'
    assert journal.state['cleanup'] == 'always' and journal.state['environment_reserved']
    other = RunJournal.create(tmp_path / 'other', identities=identities, environment_id='fixture')
    with pytest.raises(StateError, match='belongs to another run'):
        with other.mutation_lock(tmp_path / 'locks'):
            pass


def test_native_expiry_cannot_exceed_exact_operator_authorization(journal, clock):
    cap = clock.value + timedelta(seconds=0.5)
    descriptor = journal.request_native('bounded-review', 'review-kubernetes-change', expires_at=cap.isoformat())
    request = json.loads(Path(descriptor['request_path']).read_bytes())
    assert datetime.fromisoformat(request['expires_at']) == cap


def test_native_validator_consumes_bound_bytes_but_does_not_create_live_pass(journal, identities):
    descriptor = journal.request_native('D2.correction', 'prepare-gitops-correction')
    result, _ = native_result(journal, descriptor)
    accepted = journal.accept_native(result, identities=identities, validator=validate_fixture)
    assert accepted['scenario_assertion_status'] == 'not_evaluated'
    assert accepted['terminal'] is False
    validation = json.loads((journal.run_dir / accepted['validation']['path']).read_text())
    assert validation['coverage'] == 'artifact_validation_only'
    assert validation['scenario_assertion_status'] == 'not_evaluated'
    with pytest.raises(StateError, match='No native result'):
        journal.accept_native(result, identities=identities, validator=validate_fixture)


@pytest.mark.parametrize('key', ['decision', 'live_pass', 'next_phase', 'cleanup'])
def test_native_result_cannot_assert_pass_or_choose_control_flow(journal, identities, key):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path, value = native_result(journal, descriptor)
    value[key] = 'pass'
    write(path, value)
    with pytest.raises(StateError, match='native-result schema'):
        journal.accept_native(path, identities=identities, validator=validate_fixture)


def test_native_result_requires_validator(journal, identities):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path, _ = native_result(journal, descriptor)
    with pytest.raises(StateError, match='deterministic'):
        journal.accept_native(path, identities=identities, validator=None)


def test_validator_rejection_does_not_advance(journal, identities):
    descriptor = journal.request_native('correction', 'prepare-gitops-correction')
    path, _ = native_result(journal, descriptor)
    def reject(*_):
        raise StateError('unit fixture invalid patch')
    with pytest.raises(StateError, match='invalid patch'):
        journal.accept_native(path, identities=identities, validator=reject)
    assert journal.state['execution_status'] == 'awaiting_native_step'


@pytest.mark.parametrize('field', ['request_id', 'run_id', 'step_id', 'request_sha256'])
def test_cross_request_result_rejected(journal, identities, field):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path, value = native_result(journal, descriptor)
    value[field] = 'f' * 64 if field == 'request_sha256' else 'other' if field == 'step_id' else '00000000-0000-0000-0000-000000000000'
    write(path, value)
    with pytest.raises(StateError, match='another request'):
        journal.accept_native(path, identities=identities, validator=validate_fixture)


def test_cross_run_artifact_and_result_paths_rejected(journal, tmp_path, identities):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path, value = native_result(journal, descriptor)
    outside = write(tmp_path / 'foreign-result.json', value)
    with pytest.raises(StateError, match='pending run and step'):
        journal.accept_native(outside, identities=identities, validator=validate_fixture)
    value['artifacts'][0]['path'] = '../foreign-result.json'
    write(path, value)
    with pytest.raises(StateError, match='relative to its run'):
        journal.accept_native(path, identities=identities, validator=validate_fixture)


def test_stale_handoff_can_be_cancelled_and_cleaned_up(journal, tmp_path, identities, clock):
    with journal.mutation_lock(tmp_path / 'locks'):
        descriptor = journal.request_native('review', 'review-kubernetes-change', ttl_seconds=1)
    path, _ = native_result(journal, descriptor)
    original_request = Path(descriptor['request_path']).read_bytes()
    clock.value += timedelta(seconds=1)
    with pytest.raises(StateError, match='expired'):
        journal.accept_native(path, identities=identities, validator=validate_fixture)
    journal.cancel_native('operator-abort')
    cleanup = write(tmp_path / 'cleanup.json', {'fixture': True, 'owned_resources_remaining': []})
    with journal.mutation_lock(tmp_path / 'locks'):
        journal.release_environment(receipt_artifacts=[cleanup], validator=lambda _: ['unit-cleanup-observed'])
    assert journal.state['execution_status'] == 'released'
    assert Path(descriptor['request_path']).read_bytes() == original_request
    assert journal.state['native_cancellations'][0]['reason_code'] == 'operator-abort'
    other = RunJournal.create(tmp_path / 'fresh', identities=identities, environment_id='fixture')
    with other.mutation_lock(tmp_path / 'locks'):
        assert other.state['environment_reserved']


def test_expiry_during_validation_rejects_result(journal, identities, clock):
    descriptor = journal.request_native('review', 'review-kubernetes-change', ttl_seconds=1)
    path, _ = native_result(journal, descriptor)
    def delayed(*args):
        clock.value += timedelta(seconds=2)
        return validate_fixture(*args)
    with pytest.raises(StateError, match='expired during'):
        journal.accept_native(path, identities=identities, validator=delayed)


def test_incomplete_native_result_never_advances_mutation(journal, tmp_path, identities):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path, _ = native_result(journal, descriptor, status='incomplete')
    journal.accept_native(path, identities=identities, validator=validate_fixture)
    assert journal.state['execution_status'] == 'native_incomplete'
    with journal.mutation_lock(tmp_path / 'locks'):
        with pytest.raises(StateError, match='Pending work'):
            journal.begin_mutation('deliver', 'fixture-push')


def test_artifact_tampering_before_and_during_validation(journal, identities):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path, value = native_result(journal, descriptor)
    artifact = journal.run_dir / value['artifacts'][0]['path']
    write(artifact, b'altered')
    with pytest.raises(StateError, match='digest or size'):
        journal.accept_native(path, identities=identities, validator=validate_fixture)
    write(artifact, b'unit fixture only\n')
    def mutate(*_):
        write(artifact, b'changed in validation')
        return ['fixture-check']
    with pytest.raises(StateError, match='changed during validation'):
        journal.accept_native(path, identities=identities, validator=mutate)


def test_accepted_artifacts_remain_hash_bound_on_resume(journal, identities):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path, value = native_result(journal, descriptor)
    journal.accept_native(path, identities=identities, validator=validate_fixture)
    write(journal.run_dir / value['artifacts'][0]['path'], b'tampered after acceptance')
    with pytest.raises(StateError, match='bound artifact was changed'):
        RunJournal.open(journal.run_dir, identities=identities)


def test_frozen_inputs_are_immutable_and_do_not_follow_source_edits(journal, tmp_path, identities):
    source = write(tmp_path / 'source.json', {'fixture': True})
    descriptor = journal.request_native('review', 'review-kubernetes-change', input_artifacts={'source': source})
    request = json.loads(Path(descriptor['request_path']).read_text())
    frozen = journal.run_dir / request['input_artifacts'][0]['path']
    write(source, {'fixture': 'changed outside snapshot'})
    assert json.loads(frozen.read_text()) == {'fixture': True}
    write(frozen, b'altered frozen input')
    with pytest.raises(StateError, match='bound artifact was changed'):
        RunJournal.open(journal.run_dir, identities=identities)


def test_duplicate_key_and_oversized_native_json_rejected(journal, identities):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path = write(Path(descriptor['result_path']), b'{"schema_version":1,"schema_version":1}')
    with pytest.raises(StateError, match='Duplicate JSON'):
        journal.accept_native(path, identities=identities, validator=validate_fixture)
    write(path, b' ' * (state_module.MAX_DOCUMENT_BYTES + 1))
    with pytest.raises(StateError, match='bounded regular file'):
        journal.accept_native(path, identities=identities, validator=validate_fixture)


def test_symlink_run_and_artifacts_rejected(journal, tmp_path, identities):
    link = tmp_path / 'run-link'
    link.symlink_to(journal.run_dir)
    with pytest.raises(StateError, match='Symlink'):
        RunJournal.open(link, identities=identities)
    real = write(tmp_path / 'real.txt', b'unit fixture')
    linked = tmp_path / 'source-link'
    linked.symlink_to(real)
    with pytest.raises(StateError, match='Symlink'):
        journal.request_native('review', 'review-kubernetes-change', input_artifacts={'source': linked})


def test_symlink_projection_is_not_followed_or_replaced(journal, tmp_path, identities):
    target = write(tmp_path / 'do-not-touch', b'original')
    (journal.run_dir / 'state.json').unlink()
    (journal.run_dir / 'state.json').symlink_to(target)
    with pytest.raises(StateError, match='Symlink'):
        RunJournal.open(journal.run_dir, identities=identities)
    assert target.read_bytes() == b'original'


def test_fifo_artifact_is_rejected_without_hanging(tmp_path):
    fifo = tmp_path / 'fifo'
    os.mkfifo(fifo)
    code = ('from pathlib import Path\nfrom devsecops.factory.state import _read, StateError\n'
            'import sys\ntry: _read(Path(sys.argv[1]), 100)\n'
            'except StateError: sys.exit(0)\nelse: sys.exit(1)\n')
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    result = subprocess.run([sys.executable, '-c', code, str(fifo)], env=env,
                            capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()


def test_journal_hash_chain_and_projection_corruption_rejected(journal, identities):
    path = journal.run_dir / 'state.json'
    state = json.loads(path.read_text())
    state['environment_id'] = 'tampered'
    write(path, state)
    with pytest.raises(StateError, match='projection'):
        RunJournal.open(journal.run_dir, identities=identities)


def test_incomplete_unpublished_event_temp_does_not_poison_resume(journal, identities):
    write(journal.run_dir / 'journal' / ('.00000002.json.' + 'a' * 32 + '.tmp'), b'{incomplete')
    opened = RunJournal.open(journal.run_dir, identities=identities)
    assert opened.state['journal_seq'] == 1


def test_recursive_native_demo_workflow_is_not_supported(journal):
    with pytest.raises(StateError, match='Unsupported native workflow'):
        journal.request_native('recursive', 'run-software-factory-demo')


def test_cleanup_validation_failure_retains_ownership(journal, tmp_path, identities):
    receipt = write(tmp_path / 'residue.json', {'fixture': True, 'owned_resources_remaining': ['fixture-volume']})
    with journal.mutation_lock(tmp_path / 'locks'):
        def reject(_):
            raise StateError('unit fixture residual resource')
        with pytest.raises(StateError, match='residual resource'):
            journal.release_environment(receipt_artifacts=[receipt], validator=reject)
    assert journal.state['environment_reserved']
    other = RunJournal.create(tmp_path / 'other', identities=identities, environment_id='fixture')
    with pytest.raises(StateError, match='belongs to another run'):
        with other.mutation_lock(tmp_path / 'locks'):
            pass


def test_native_validation_crash_before_journal_can_retry_without_overwrite(journal, identities, monkeypatch):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path, _ = native_result(journal, descriptor)
    original = journal._commit
    def crash(kind, payload):
        if kind == 'native_validated':
            raise OSError('unit crash before validation event')
        original(kind, payload)
    monkeypatch.setattr(journal, '_commit', crash)
    with pytest.raises(OSError):
        journal.accept_native(path, identities=identities, validator=validate_fixture)
    orphan = next(path.parent.glob('validation-*.json'))
    original_bytes = orphan.read_bytes()
    monkeypatch.setattr(journal, '_commit', original)
    journal.accept_native(path, identities=identities, validator=validate_fixture)
    assert orphan.read_bytes() == original_bytes
    assert len(list(path.parent.glob('validation-*.json'))) == 2
    assert len(journal.state['native_results']) == 1


def test_environment_release_crash_recovers_without_new_cleanup(journal, tmp_path, identities, clock, monkeypatch):
    receipt = write(tmp_path / 'cleanup.json', {'fixture': True, 'owned_resources_remaining': []})
    with journal.mutation_lock(tmp_path / 'locks'):
        monkeypatch.setattr(journal, '_write_projection', lambda: (_ for _ in ()).throw(OSError('unit crash')))
        with pytest.raises(OSError):
            journal.release_environment(receipt_artifacts=[receipt], validator=lambda _: ['fixture-cleanup'])
    reopened = RunJournal.open(journal.run_dir, identities=identities, clock=clock)
    assert reopened.state['execution_status'] == 'released'
    with reopened.mutation_lock(tmp_path / 'locks'):
        assert not (tmp_path / 'locks' / (identities['environment_sha256'] + '.owner.json')).exists()
    other = RunJournal.create(tmp_path / 'other', identities=identities, environment_id='fixture')
    with other.mutation_lock(tmp_path / 'locks'):
        assert other.state['environment_reserved']


def test_explicit_cancel_of_missing_native_result_preserves_request(journal):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    request = Path(descriptor['request_path']).read_bytes()
    journal.cancel_native('operator-abort-before-result')
    assert journal.state['execution_status'] == 'ready'
    assert journal.state['native_results'] == []
    assert Path(descriptor['request_path']).read_bytes() == request


def test_cancel_cannot_bypass_unresolved_mutation(journal, tmp_path):
    with journal.mutation_lock(tmp_path / 'locks'):
        journal.begin_mutation('up', 'fixture-create')
        with pytest.raises(StateError, match='No native request'):
            journal.cancel_native('operator-abort')
        assert journal.state['execution_status'] == 'mutation_pending'


def test_duplicate_artifacts_wrong_kind_and_validation_checks_rejected(journal, identities):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path, value = native_result(journal, descriptor)
    value['artifacts'].append(value['artifacts'][0].copy())
    write(path, value)
    with pytest.raises(StateError, match='unique'):
        journal.accept_native(path, identities=identities, validator=validate_fixture)
    value['artifacts'].pop()
    value['artifacts'][0]['kind'] = 'explanation'
    write(path, value)
    with pytest.raises(StateError, match='required native artifact'):
        journal.accept_native(path, identities=identities, validator=validate_fixture)
    value['artifacts'][0]['kind'] = 'review-report'
    write(path, value)
    with pytest.raises(StateError, match='completed check identifiers'):
        journal.accept_native(path, identities=identities, validator=lambda *_: [])


def test_lock_file_rejects_fifo_and_shared_permissions(journal, tmp_path):
    locks = tmp_path / 'locks'
    locks.mkdir(mode=0o700)
    path = locks / (journal.identities['environment_sha256'] + '.lock')
    os.mkfifo(path)
    with pytest.raises(StateError, match='private regular file'):
        with journal.mutation_lock(locks):
            pass
    path.unlink()
    write(path, b'')
    path.chmod(0o644)
    with pytest.raises(StateError, match='private regular file'):
        with journal.mutation_lock(locks):
            pass


def test_lexical_parent_traversal_is_rejected_before_normalization(journal, identities):
    with pytest.raises(StateError, match='Parent traversal'):
        RunJournal.open(journal.run_dir / '..' / journal.run_dir.name, identities=identities)


def test_backward_clock_cannot_refresh_native_request(journal, identities, clock):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path, _ = native_result(journal, descriptor)
    clock.value -= timedelta(minutes=1)
    with pytest.raises(StateError, match='predates'):
        journal.accept_native(path, identities=identities, validator=validate_fixture)


def test_environment_owner_tampering_is_rejected(journal, tmp_path):
    with journal.mutation_lock(tmp_path / 'locks'):
        journal.begin_mutation('up', 'fixture-create')
    owner_path = tmp_path / 'locks' / (journal.identities['environment_sha256'] + '.owner.json')
    owner = json.loads(owner_path.read_text())
    owner['operation']['action'] = 'different-action'
    write(owner_path, owner)
    with pytest.raises(StateError, match='conflicts with the journal'):
        with journal.mutation_lock(tmp_path / 'locks'):
            pass


def test_accepted_native_event_survives_projection_crash(journal, identities, clock, monkeypatch):
    descriptor = journal.request_native('review', 'review-kubernetes-change')
    path, _ = native_result(journal, descriptor)
    monkeypatch.setattr(journal, '_write_projection', lambda: (_ for _ in ()).throw(OSError('unit crash')))
    with pytest.raises(OSError):
        journal.accept_native(path, identities=identities, validator=validate_fixture)
    recovered = RunJournal.open(journal.run_dir, identities=identities, clock=clock)
    assert len(recovered.state['native_results']) == 1
    assert recovered.state['execution_status'] == 'ready'


def test_bounded_journal_is_rejected_before_publishing_an_unreadable_event(journal, monkeypatch):
    monkeypatch.setattr(state_module, 'MAX_JOURNAL_BYTES', journal._journal_bytes + 1)
    with pytest.raises(StateError, match='aggregate size limit'):
        journal.request_native('review', 'review-kubernetes-change')
    assert journal.state['journal_seq'] == 1


def test_complete_cleanup_is_one_validated_journal_transition(journal, tmp_path, identities, clock):
    receipt = write(tmp_path / 'complete-cleanup.json', {'fixture': True, 'remaining': []})
    with journal.mutation_lock(tmp_path / 'locks'):
        operation = journal.begin_mutation('cleanup', 'down')
        before = journal.state['journal_seq']
        observed = []
        def validate(raw):
            current = json.loads((journal.run_dir / 'state.json').read_bytes())
            assert current['active_mutation']['operation_id'] == operation
            assert current['environment_reserved']
            observed.append(raw)
            return ['fixture-complete-cleanup-observed']
        journal.complete_cleanup(operation, receipt_artifacts=[receipt], validator=validate)
        after = journal.state
        assert after['journal_seq'] == before + 1
        assert after['execution_status'] == 'released' and after['active_mutation'] is None
        assert after['operations'][0]['status'] == 'applied'
        assert not after['environment_reserved']
        assert len(observed) == 1
        event = json.loads((journal.run_dir / 'journal' / f"{after['journal_seq']:08d}.json").read_bytes())
        assert event['type'] == 'cleanup_completed'
    reopened = RunJournal.open(journal.run_dir, identities=identities, clock=clock)
    assert reopened.state == after


@pytest.mark.parametrize('failure', ['validator', 'empty-checks', 'altered-frozen-receipt', 'publication'])
def test_complete_cleanup_failure_preserves_pending_operation(journal, tmp_path, monkeypatch, failure):
    receipt = write(tmp_path / 'complete-cleanup.json', {'fixture': True})
    with journal.mutation_lock(tmp_path / 'locks'):
        operation = journal.begin_mutation('cleanup', 'down')
        before = journal.state
        def validate(raw):
            if failure == 'validator':
                raise ValueError('fixture rejected')
            if failure == 'empty-checks':
                return []
            if failure == 'altered-frozen-receipt':
                next((journal.run_dir / 'cleanup').glob('*/0')).write_bytes(b'changed fixture')
            return ['fixture-check']
        original = state_module._exclusive
        def publish(path, raw):
            if failure == 'publication' and Path(path).name == 'validation.json':
                raise OSError('fixture publication failure')
            return original(path, raw)
        monkeypatch.setattr(state_module, '_exclusive', publish)
        with pytest.raises((StateError, ValueError, OSError)):
            journal.complete_cleanup(operation, receipt_artifacts=[receipt], validator=validate)
        assert journal.state == before
        assert journal.state['active_mutation']['operation_id'] == operation


def test_reopened_cleanup_requires_explicit_reconciliation(journal, tmp_path, identities, clock):
    receipt = write(tmp_path / 'complete-cleanup.json', {'fixture': True})
    with journal.mutation_lock(tmp_path / 'locks'):
        operation = journal.begin_mutation('cleanup', 'down')
        journal.reconcile_mutation(operation, 'unknown', receipt_artifacts=[receipt])
    reopened = RunJournal.open(journal.run_dir, identities=identities, clock=clock)
    with reopened.mutation_lock(tmp_path / 'locks'):
        with pytest.raises(StateError, match='requires reconciliation'):
            reopened.complete_cleanup(operation, receipt_artifacts=[receipt], validator=lambda raw: ['fixture-check'])
        reopened.complete_cleanup(operation, receipt_artifacts=[receipt], validator=lambda raw: ['fixture-check'],
                                  reconciliation=True)
        assert reopened.state['execution_status'] == 'released'


def test_complete_cleanup_cannot_release_another_mutation(journal, tmp_path):
    receipt = write(tmp_path / 'complete-cleanup.json', {'fixture': True})
    with journal.mutation_lock(tmp_path / 'locks'):
        operation = journal.begin_mutation('deliver', 'fixture-write')
        before = journal.state
        with pytest.raises(StateError, match='outstanding down'):
            journal.complete_cleanup(operation, receipt_artifacts=[receipt], validator=lambda raw: ['fixture-check'])
        assert journal.state == before
