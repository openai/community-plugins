"""Real journal/TTY admission with controlled components, never VM authority."""

from copy import deepcopy
from datetime import timedelta
import json

import pytest

from devsecops.factory import resident_cleanup_control as control
from devsecops.factory.lifecycle import PhaseOutcome
from devsecops.factory.state import CONTAINED_GUEST_CHECKS, PARENT_DISPOSAL_MODE, StateError
from tests import test_cleanup_continuation as fixtures
from tests.test_resident_cleanup_control import terminal

resident = fixtures.resident


def contained(resident):
    engine, _, prior, _, _, _ = resident
    validate = engine.driver.validate_partial_cleanup
    # This is an injected controlled driver. The production aggregate separately
    # proves these checks through original typed VM/registration observations.
    engine.driver.validate_partial_cleanup = lambda receipts: [
        *validate(receipts), *sorted(CONTAINED_GUEST_CHECKS)]
    return engine, prior


def controlled_disposal_leaf(engine):
    """Exercise real journal controls with an inert, explicitly controlled leaf.

    The inherited three-component aggregate has no original runner VM. Its
    ordinary cleanup must not be dispatched under a parent-disposal operation.
    Actual original VM/aggregate disposal is tested in runner cleanup composition.
    """
    prepare = engine.driver.prepare_cleanup_continuation
    calls = []
    retained = {}

    def selected(context, prior, *, cleanup_mode):
        calls.append(('prepare', cleanup_mode))
        assert cleanup_mode == PARENT_DISPOSAL_MODE
        assert engine.journal.state['active_mutation'] is None
        return prepare(context, prior)

    def perform(context, prior, *, cleanup_mode):
        active = engine.journal.state['active_mutation']
        assert active['cleanup_mode'] == cleanup_mode == PARENT_DISPOSAL_MODE
        assert active['continues_operation'] == prior and active['operation_id'] != prior
        calls.append(('execute', cleanup_mode))
        raw = json.dumps({'fixture_only': True, 'run_id': context.run_id,
            'operation_id': active['operation_id'], 'continues_operation': prior,
            'cleanup_mode': cleanup_mode}).encode()
        artifact = context.run_dir / ('controlled-parent-disposal-' + active['operation_id'] + '.json')
        with artifact.open('xb') as stream:
            stream.write(raw)
        retained.update(raw=raw, operation=active['operation_id'], prior=prior)
        return PhaseOutcome('completed', 'controlled-disposal-leaf', (artifact,), mutation_effect='applied')

    def validate(receipts):
        assert engine.journal._has_validation_read_lock()
        active = engine.journal.state['active_mutation']
        assert active['operation_id'] == retained['operation']
        assert active['continues_operation'] == retained['prior']
        assert active['cleanup_mode'] == PARENT_DISPOSAL_MODE
        assert type(receipts) is tuple and receipts == (retained['raw'],)
        calls.append(('validate', PARENT_DISPOSAL_MODE))
        return ['controlled-disposal-leaf-bytes-checked']

    engine.driver.prepare_cleanup_continuation = selected
    engine.driver.continue_cleanup = perform
    engine.driver.validate_cleanup = validate
    return calls


def test_contained_residual_seals_restricted_mode_without_dispatch(resident):
    engine, prior = contained(resident)
    engine.reconcile_cleanup(operation_id=prior)
    before = list(resident[3])
    last = engine.journal.state['operations'][-1]
    assert last['status'] == 'partial'
    assert last['cleanup_continuation_modes'] == [PARENT_DISPOSAL_MODE]
    assert engine.journal.state['environment_reserved']
    assert engine.journal.state['active_mutation'] is None
    assert engine.advance()['execution_status'] == 'cleanup_continuation_required'
    with pytest.raises(StateError, match='mode is not admitted'):
        engine.continue_cleanup(prior_operation_id=prior)
    assert resident[3] == before
    assert len(engine.journal.state['operations']) == len(engine.journal._state['operations'])


def test_explicit_mode_is_in_fresh_operation_and_retains_partial_history(resident):
    engine, prior = contained(resident)
    engine.reconcile_cleanup(operation_id=prior)
    before = deepcopy(engine.journal.state['operations'][-1])
    component_calls = list(resident[3])
    calls = controlled_disposal_leaf(engine)
    engine.continue_cleanup(prior_operation_id=prior, cleanup_mode=PARENT_DISPOSAL_MODE)
    state = engine.journal.state
    assert calls == [(step, PARENT_DISPOSAL_MODE) for step in ('prepare', 'execute', 'validate')]
    assert resident[3] == component_calls
    assert state['operations'][-2] == before
    assert state['operations'][-1]['cleanup_mode'] == PARENT_DISPOSAL_MODE
    assert state['execution_status'] == 'released'
    assert engine.advance()['terminal']
    assert not engine.journal.state['lifecycle']['final_result']['canonical_success']


def test_normal_partial_cannot_select_parent_disposal(resident):
    engine, _, prior, calls, _, _ = resident
    engine.reconcile_cleanup(operation_id=prior)
    before = list(calls)
    with pytest.raises(StateError, match='mode is not admitted'):
        engine.continue_cleanup(prior_operation_id=prior, cleanup_mode=PARENT_DISPOSAL_MODE)
    assert calls == before and engine.journal.state['active_mutation'] is None


@pytest.mark.parametrize('missing', sorted(CONTAINED_GUEST_CHECKS - {
    'owned-guest-contained-residual-parent-disposal-only'}))
def test_marker_without_parent_or_revocation_proof_keeps_old_unknown(resident, missing):
    engine, _, prior, _, _, _ = resident
    engine.driver.validate_partial_cleanup = lambda _: sorted(CONTAINED_GUEST_CHECKS - {missing})
    with pytest.raises(StateError, match='parent and revocation'):
        engine.reconcile_cleanup(operation_id=prior)
    assert engine.journal.state['active_mutation']['operation_id'] == prior
    assert engine.journal.state['environment_reserved']


@pytest.mark.parametrize('mode', [None, True, {}, [], 'automatic', 'dispose', 'normal '])
def test_invalid_modes_never_reach_preparation(resident, mode):
    engine, prior = contained(resident)
    engine.reconcile_cleanup(operation_id=prior)
    before = list(resident[3])
    with pytest.raises(StateError, match='Unsupported explicit cleanup'):
        engine.continue_cleanup(prior_operation_id=prior, cleanup_mode=mode)
    assert resident[3] == before and engine.journal.state['active_mutation'] is None


def test_expired_cleanup_grant_cannot_use_disposal_mode(resident):
    engine, prior = contained(resident)
    engine.reconcile_cleanup(operation_id=prior)
    resident[5].value += timedelta(days=31)
    before = list(resident[3])
    with pytest.raises(StateError):
        engine.continue_cleanup(prior_operation_id=prior, cleanup_mode=PARENT_DISPOSAL_MODE)
    assert resident[3] == before and engine.journal.state['active_mutation'] is None


def test_tty_requires_fresh_explicit_disposal_command(resident, monkeypatch):
    engine, _ = contained(resident)
    calls = controlled_disposal_leaf(engine)
    output = terminal(monkeypatch, lambda prompt: prompt['command'])
    result = control.wait_for_cleanup(engine, engine._snapshot('reconciliation_required'), enabled=True)
    prompts = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [p['command'].split()[0] for p in prompts] == ['reconcile', 'dispose-runner-guest']
    assert prompts[-1]['action'] == 'new-authorized-runner-guest-disposal'
    assert prompts[0]['command'].split()[-1] != prompts[1]['command'].split()[-1]
    assert calls == [(step, PARENT_DISPOSAL_MODE) for step in ('prepare', 'execute', 'validate')]
    assert result['terminal'] and not result['canonical_success']


def test_controlled_disposal_leaf_cannot_skip_real_release_validator(resident, monkeypatch):
    engine, prior = contained(resident)
    engine.reconcile_cleanup(operation_id=prior)
    previous = deepcopy(engine.journal.state['operations'][-1])
    component_calls = list(resident[3])
    calls = controlled_disposal_leaf(engine)
    validate = engine.driver.validate_cleanup

    def reject(receipts):
        validate(receipts)
        raise StateError('controlled release rejection')

    monkeypatch.setattr(engine.driver, 'validate_cleanup', reject)
    with pytest.raises(StateError, match='controlled release rejection'):
        engine.continue_cleanup(prior_operation_id=prior, cleanup_mode=PARENT_DISPOSAL_MODE)
    state = engine.journal.state
    assert calls == [(step, PARENT_DISPOSAL_MODE) for step in ('prepare', 'execute', 'validate')]
    assert resident[3] == component_calls
    assert state['operations'][-2] == previous
    assert state['active_mutation']['operation_id'] != prior
    assert state['active_mutation']['continues_operation'] == prior
    assert state['active_mutation']['cleanup_mode'] == PARENT_DISPOSAL_MODE
    assert state['active_mutation']['status'] == 'unknown'
    assert state['environment_reserved'] and state['cleanup_validation'] is None


def test_ordinary_continue_command_cannot_confirm_disposal(resident, monkeypatch):
    engine, prior = contained(resident)
    engine.reconcile_cleanup(operation_id=prior)
    before = list(resident[3])
    terminal(monkeypatch, lambda p: p['command'].replace('dispose-runner-guest ', 'continue ', 1))
    with pytest.raises(StateError, match='fresh terminal challenge'):
        control.wait_for_cleanup(engine, engine._snapshot('cleanup_continuation_required'), enabled=True)
    assert resident[3] == before and engine.journal.state['active_mutation'] is None


def test_begin_mutation_rejects_forged_mode_on_normal_partial(resident):
    engine, _, prior, _, _, _ = resident
    engine.reconcile_cleanup(operation_id=prior)
    with engine.journal.lifecycle_lock(), engine.journal.mutation_lock(engine.shared_lock_dir):
        with pytest.raises(StateError, match='mode is not admitted'):
            engine.journal.begin_mutation('cleanup', 'down', continues_operation=prior,
                cleanup_mode=PARENT_DISPOSAL_MODE)
    assert engine.journal.state['active_mutation'] is None


@pytest.mark.parametrize('contained_partial,forged_mode', [
    (False, PARENT_DISPOSAL_MODE), (True, None), (True, 'normal')])
def test_reducer_independently_rejects_mode_substitution(resident, contained_partial, forged_mode):
    engine, _, prior, _, _, _ = resident
    if contained_partial:
        contained(resident)
    engine.reconcile_cleanup(operation_id=prior)
    before = deepcopy(engine.journal.state)
    payload = {'operation_id': '00000000-1111-2222-3333-444444444444',
        'phase_id': 'cleanup', 'action': 'down', 'status': 'pending',
        'intent_artifacts': [], 'receipt_artifacts': [], 'continues_operation': prior}
    if forged_mode is not None:
        payload['cleanup_mode'] = forged_mode
    with pytest.raises(StateError, match='mode is not admitted'):
        engine.journal._reduce({'type': 'mutation_started', 'payload': payload}, 'a' * 64)
    assert engine.journal.state == before


@pytest.mark.parametrize('duplicate', sorted(CONTAINED_GUEST_CHECKS))
def test_duplicate_containment_check_keeps_original_unknown(resident, duplicate):
    engine, _, prior, _, _, _ = resident
    engine.driver.validate_partial_cleanup = lambda _: [*sorted(CONTAINED_GUEST_CHECKS), duplicate]
    with pytest.raises(StateError, match='parent and revocation'):
        engine.reconcile_cleanup(operation_id=prior)
    assert engine.journal.state['active_mutation']['operation_id'] == prior
    assert engine.journal.state['environment_reserved']


def test_disposal_mode_is_not_an_initial_or_other_operation(resident):
    engine, _, prior, _, _, _ = resident
    with engine.journal.lifecycle_lock(), engine.journal.mutation_lock(engine.shared_lock_dir):
        for action, predecessor in [('up', prior), ('down', None)]:
            with pytest.raises(StateError, match='new cleanup continuation'):
                engine.journal.begin_mutation('cleanup', action,
                    continues_operation=predecessor, cleanup_mode=PARENT_DISPOSAL_MODE)

