"""Fixed dispatcher orchestration with inert components, never a live cleanup."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from devsecops.factory import owned_cleanup as cleanup
from devsecops.factory.cleanup_observation import ComponentObservations
from devsecops.factory.lifecycle import PhaseContext, PhaseOutcome
from devsecops.factory.owned_linux import OwnedLinuxDriver
from devsecops.factory.owned_foundation import OwnedRKE2Foundation
from devsecops.factory.owned_registry import OwnedOCIRegistry
from devsecops.factory.state import StateError


@pytest.fixture
def composed(tmp_path, monkeypatch):
    journal = SimpleNamespace(run_dir=tmp_path, identities={'package_sha256': 'a'*64},
        state={'operations': [], 'active_mutation': None})
    driver = object.__new__(OwnedLinuxDriver)
    driver.journal = journal
    coordinator = cleanup.OwnedCleanupCoordinator(driver)
    monkeypatch.setattr(coordinator, '_admit', lambda ctx, action, **kwargs: 'up-op' if action == 'up' else 'down-op')
    up = PhaseContext('run', {'id': 'provision', 'operation': 'up'}, journal.identities, {}, {}, tmp_path, 1e30, 'controlled-test')
    down = replace(up, phase={'id': 'cleanup', 'operation': 'down'})
    calls, controls = [], {'failure': None}
    for kind, cls in [('foundation', OwnedRKE2Foundation), ('registry', OwnedOCIRegistry)]:
        component = object.__new__(cls)
        component.journal = journal
        store = ComponentObservations()
        def perform(self, ctx, *, action, selected=kind, cache=store):
            calls.append((selected, action))
            if controls['failure'] == (selected, action):
                raise KeyboardInterrupt()
            report = {'run_id': ctx.run_id, 'operation_id': 'down-op' if action != 'up' else 'up-op',
                'identities': dict(ctx.identities), 'action': action,
                'resources': [{'kind': 'retained-baseline', 'disposition': 'preserve', 'state': 'preserved'}],
                'runtime_cleanup': 'observed-absent', 'observation': {
                    'inflight': None, 'cleanup': 'owned-runtime-and-private-material-absent'}}
            if controls['failure'] == (selected, 'unknown'):
                report['observation']['cleanup'] = 'unknown'
            path = tmp_path/(selected+'-'+action+'.json')
            path.write_text(json.dumps(report))
            result = PhaseOutcome('incomplete', 'component-only', (path,), mutation_effect='unknown')
            return cache.retain(result, report)
        monkeypatch.setattr(cls, 'up', lambda self, ctx, fn=perform: fn(self, ctx, action='up'))
        monkeypatch.setattr(cls, 'down', lambda self, ctx, fn=perform: fn(self, ctx, action='down'))
        monkeypatch.setattr(cls, 'reconcile', lambda self, ctx, op, fn=perform: fn(self, ctx, action='reconcile'))
        monkeypatch.setattr(cls, 'cleanup_observation', lambda self, result, cache=store: cache.observe(result))
        getattr(coordinator, 'register_'+kind)(component)
    # Service and Kubernetes have not been dispatched: a closed original
    # inventory is the only reason the early rollback may call them absent.
    coordinator.components['services'] = SimpleNamespace(journal=journal)
    coordinator.components['service-links'] = SimpleNamespace(journal=journal)
    coordinator.declare_deferred_scope(up, 'kubernetes')
    coordinator.declare_deferred_scope(up, 'gitlab')
    coordinator.declare_deferred_scope(up, 'writer')
    coordinator.declare_deferred_scope(up, 'reader')
    coordinator.seal_inventory(up)
    coordinator.foundation_up(up)
    coordinator.registry_up(up)
    return coordinator, up, down, calls, controls


def test_fixed_reverse_cleanup_and_exact_frozen_receipt_validation(composed):
    coordinator, _, down, calls, _ = composed
    result = coordinator.execute(down)
    assert calls[-2:] == [('registry', 'down'), ('foundation', 'down')]
    assert result.status == 'completed' and result.mutation_effect == 'applied'
    raw = tuple(path.read_bytes() for path in result.artifacts)
    report = json.loads(raw[0])
    assert report['never_dispatched_scopes'] == ['services', 'service-links', 'kubernetes', 'writer', 'reader', 'gitlab']
    assert coordinator.validate_cleanup(raw)
    with pytest.raises(StateError):
        coordinator.validate_cleanup((raw[0]+b' ',))
    with pytest.raises(StateError, match='already attempted'):
        coordinator.execute(down)


def test_unknown_child_effect_stops_before_foundation_and_readonly_reconciliation(composed):
    coordinator, _, down, calls, controls = composed
    controls['failure'] = ('registry', 'unknown')
    result = coordinator.execute(down)
    assert result.status == 'incomplete' and ('foundation', 'down') not in calls
    controls['failure'] = None
    observed = coordinator.reconcile(down, 'down-op')
    assert calls[-1] == ('registry', 'reconcile')
    assert ('foundation', 'down') not in calls
    assert observed.status == 'incomplete'  # Reconciliation never silently runs the remaining deletions.
    with pytest.raises(StateError):
        coordinator.validate_cleanup(tuple(path.read_bytes() for path in observed.artifacts))


def test_cancellation_retains_attempt_and_reconcile_does_not_repeat(composed):
    coordinator, _, down, calls, controls = composed
    controls['failure'] = ('registry', 'down')
    with pytest.raises(KeyboardInterrupt):
        coordinator.execute(down)
    assert coordinator.attempts['registry']['down_attempted'] is True
    controls['failure'] = None
    result = coordinator.reconcile(down, 'down-op')
    assert calls.count(('registry', 'down')) == 1
    assert ('foundation', 'down') not in calls and result.status == 'incomplete'


def test_changed_child_receipt_prevents_environment_release(composed):
    coordinator, _, down, _, _ = composed
    result = coordinator.execute(down)
    path = coordinator.attempts['registry']['outcome'].artifacts[0]
    path.write_bytes(b'{}')
    with pytest.raises(StateError, match='evidence changed'):
        coordinator.validate_cleanup(tuple(path.read_bytes() for path in result.artifacts))


def test_saved_json_and_copied_outcome_cannot_restore_observation(composed):
    coordinator, _, down, _, _ = composed
    result = coordinator.execute(down)
    with pytest.raises(StateError, match='original resident'):
        coordinator._observations.observe(replace(result))
    fresh = cleanup.OwnedCleanupCoordinator(coordinator.driver)
    with pytest.raises(StateError, match='resident'):
        fresh.validate_cleanup(tuple(path.read_bytes() for path in result.artifacts))


def test_no_arbitrary_callback_registration(composed):
    coordinator, _, _, _, _ = composed
    with pytest.raises(StateError, match='exact journal-bound'):
        coordinator.register_foundation(SimpleNamespace(journal=coordinator.journal, down=lambda _: None))


def test_service_links_need_exact_disposition_and_observed_absence():
    report = {'kind': 'owned-service-links', 'complete_cleanup': True,
        'mutation_effect': 'applied', 'unresolved_operations': [],
        'resources': [{'disposition': 'remove', 'state': 'absent'},
                      {'disposition': 'preserve', 'state': 'preserved'}]}
    assert cleanup.OwnedCleanupCoordinator._resolved('service-links', report)
    report['resources'][0]['state'] = 'preserved'
    assert not cleanup.OwnedCleanupCoordinator._resolved('service-links', report)
    report['resources'] = []
    assert not cleanup.OwnedCleanupCoordinator._resolved('service-links', report)


def test_service_links_close_before_the_original_registry_and_foundation(composed, monkeypatch):
    coordinator, up, down, calls, _ = composed
    observations = ComponentObservations()
    component = coordinator.components['service-links']
    def result(context, action):
        calls.append(('service-links', action))
        report = {'kind': 'owned-service-links', 'run_id': context.run_id,
            'operation_id': 'up-op' if action == 'up' else 'down-op',
            'identities': dict(context.identities), 'action': action,
            'complete_cleanup': True, 'mutation_effect': 'applied', 'unresolved_operations': [],
            'resources': [{'disposition': 'remove', 'state': 'absent'}]}
        path = context.run_dir / ('service-links-' + action + '.json')
        path.write_text(json.dumps(report))
        outcome = PhaseOutcome('completed', 'controlled-link-observation', (path,), mutation_effect='applied')
        return observations.retain(outcome, report)
    component.up = lambda context: result(context, 'up')
    component.down = lambda context: result(context, 'down')
    component.cleanup_observation = observations.observe
    coordinator.service_links_up(up)
    outcome = coordinator.execute(down)
    assert outcome.status == 'completed'
    assert calls[-3:] == [('service-links', 'down'), ('registry', 'down'), ('foundation', 'down')]


def test_prepare_failure_before_first_effect_has_original_resident_no_effects_closure(composed, monkeypatch):
    existing, up, down, _, _ = composed
    fresh = cleanup.OwnedCleanupCoordinator(existing.driver)
    monkeypatch.setattr(fresh, '_admit', lambda *args, **kwargs: 'down-op')
    result = fresh.execute(down)
    assert result.status == 'completed'
    value = json.loads(result.artifacts[0].read_bytes())
    assert value['resident_no_effects_closure'] is True
    assert len(value['never_dispatched_scopes']) == 8 and value['components'] == {}
    assert fresh.validate_cleanup(tuple(path.read_bytes() for path in result.artifacts))


def test_new_coordinator_after_prior_operation_cannot_infer_no_effects(composed, monkeypatch):
    existing, _, down, _, _ = composed
    existing.driver.journal.state['operations'] = [{'operation_id': 'prior-up', 'outcome': 'unknown'}]
    fresh = cleanup.OwnedCleanupCoordinator(existing.driver)
    monkeypatch.setattr(fresh, '_admit', lambda *args, **kwargs: 'down-op')
    result = fresh.execute(down)
    assert result.status == 'incomplete'
    assert json.loads(result.artifacts[0].read_bytes())['resident_no_effects_closure'] is False


def test_attempted_service_init_without_direct_token_blocks_teardown(composed):
    coordinator, _, down, calls, _ = composed
    coordinator.attempts['services'] = {'up_operation': 'up-op', 'up_outcome': None,
        'down_attempted': False, 'outcome': None, 'observation': None}
    result = coordinator.execute(down)
    assert result.status == 'incomplete'
    assert ('registry', 'down') not in calls and ('foundation', 'down') not in calls
    assert 'service-token-ownership-unreconciled' in json.loads(result.artifacts[0].read_bytes())['gaps']
