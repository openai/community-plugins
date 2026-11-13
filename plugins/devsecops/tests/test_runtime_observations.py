"""Controlled session composition and origin rejection, never live qualification."""
from copy import deepcopy
from datetime import timedelta
import os
from types import SimpleNamespace
import time

import pytest

from devsecops.factory import runtime_observations as production
from devsecops.factory.state import StateError
from devsecops.runtime.context import canonical_bytes, digest
from devsecops.runtime.recovery import ASSURANCES, verify_recovery
from test_runtime_recovery import fixture, NOW
from test_runtime_collection import clients_for


@pytest.fixture
def retained(tmp_path, monkeypatch):
    bundles, contract, evidence = fixture()
    # This controlled harness deliberately supplies no bootstrap/driver authority.
    # It exercises data retention and admission logic below the concrete constructor.
    session = object.__new__(production.RuntimeObservationSession)
    session._pid = os.getpid()
    session._run = contract['run_id']
    session._identities = deepcopy(contract['identities'])
    session._journal = SimpleNamespace(identities=deepcopy(contract['identities']),
        state={'run_id': contract['run_id'], 'environment_id': contract['environment']})
    session._records, session._recovery = {}, {}
    for phase, bundle in bundles.items():
        context = bundle['context_snapshot']
        authority = {'config': deepcopy(context['factory_config']), 'lock': deepcopy(context['factory_lock']),
                     'intent': deepcopy(context['intent'])}
        paths, pins = {}, {}
        for name, doc in [('context', context), ('pending', bundle), ('collection', bundle)]:
            path = tmp_path/(phase+'-'+name+'.json')
            raw = canonical_bytes(doc)
            path.write_bytes(raw)
            paths[name], pins[path] = path, raw
        session._records[('primary', phase)] = {'context': context, 'authority': authority,
            'pins': pins, 'paths': paths, 'bundle': bundle, 'pending': bundle, 'mode': 'controlled-test'}
    phase_context = SimpleNamespace(phase={'attempt': 'primary', 'operation': 'probe'},
                                    run_dir=tmp_path, deadline=time.monotonic()+300, execution_mode='controlled-test')
    monkeypatch.setattr(session, '_admit', lambda context: None)
    contract_path = tmp_path/'contract.json'
    contract_path.write_bytes(canonical_bytes(contract))
    paths = {}
    for index, (name, value) in enumerate(evidence.items()):
        path = tmp_path/('evidence-'+str(index)+'.json')
        path.write_bytes(value if isinstance(value, bytes) else canonical_bytes(value))
        paths[name] = path
    return session, phase_context, contract_path, paths, bundles, contract, evidence


def bind(retained):
    session, context, contract, evidence, *_ = retained
    return session.bind_recovery_inputs(context, contract=contract,
        contract_sha256=production._sha(contract.read_bytes()), evidence=evidence)


def test_native_diagnosis_and_recovery_select_original_exact_bytes(retained):
    session = retained[0]
    diagnosis = session.diagnosis_inputs('primary')
    selected = bind(retained)
    assert selected.baseline == diagnosis.baseline and selected.fault == diagnosis.fault
    assert selected.baseline_sha256 == production._sha(selected.baseline.read_bytes())
    assert selected.recovered_sha256 == production._sha(selected.recovered.read_bytes())
    assert len(selected.evidence) == 26
    with pytest.raises(StateError):
        session.diagnosis_inputs('replay')


@pytest.mark.parametrize('damage', ['baseline', 'fault', 'recovery', 'contract', 'probe', 'patch', 'process', 'identity'])
def test_selected_bytes_and_scope_cannot_change_after_binding(retained, damage):
    session, _, contract, evidence, *_ = retained
    selected = bind(retained)
    if damage in ('baseline', 'fault', 'recovery'):
        path = getattr(selected, 'recovered' if damage == 'recovery' else damage)
        path.write_bytes(path.read_bytes()+b' ')
    elif damage == 'contract':
        contract.write_bytes(contract.read_bytes()+b' ')
    elif damage in ('probe', 'patch'):
        path = evidence['http:baseline-mission-health' if damage == 'probe' else 'patch']
        path.write_bytes(path.read_bytes()+b' ')
    elif damage == 'process':
        session._pid = -1
    else:
        session._journal.identities['package_sha256'] = '0'*64
    with pytest.raises((StateError, ValueError)):
        session.recovery_inputs('primary')


@pytest.mark.parametrize('damage', ['attempt', 'run', 'intent', 'missing-evidence', 'extra-evidence', 'hash'])
def test_contract_or_artifact_selection_cannot_change_canonical_scope(retained, damage):
    session, context, contract_path, evidence, _, contract, _ = retained
    expected = production._sha(contract_path.read_bytes())
    if damage == 'attempt':
        contract['attempt_id'] = 'replay'
    elif damage == 'run':
        contract['run_id'] = 'other-run'
    elif damage == 'intent':
        contract['intents']['fault']['targets'][0]['argocd']['sources'][0]['revision'] = 'f'*40
    elif damage == 'missing-evidence':
        del evidence['patch']
    elif damage == 'extra-evidence':
        evidence['untrusted-script'] = contract_path
    else:
        expected = '0'*64
    if damage in ('attempt', 'run', 'intent'):
        contract_path.write_bytes(canonical_bytes(contract))
        expected = production._sha(contract_path.read_bytes())
    with pytest.raises((StateError, ValueError)):
        session.bind_recovery_inputs(context, contract=contract_path, contract_sha256=expected, evidence=evidence)


def test_controlled_retained_origin_never_authenticates_live_recovery(retained):
    session, context, *_ = retained
    bind(retained)
    context.phase['operation'] = 'verify'
    result = session.verify(context)
    assert result['provenance']['assurances'] == []
    assert result['provenance']['verified'] is False
    assert result['execution_mode'] == 'controlled-test'
    assert result['recovery'] == 'unknown'
    codes = {row['code'] for row in result['gaps']}
    assert {'origin-'+assurance+'-unverified' for assurance in ASSURANCES} <= codes


def test_serialized_origins_and_other_runtime_backend_names_are_not_loaded(retained):
    _, _, _, _, bundles, contract, evidence = retained
    with pytest.raises(StateError):
        production.RuntimeObservationOrigin()
    result = verify_recovery(bundles['baseline'], bundles['fault'], bundles['recovery'],
        contract=contract, evidence=evidence, provenance='canonical-runtime-v1',
        runtime_origin={'assurances': list(ASSURANCES)}, now=NOW)
    assert result['provenance']['verified'] is False
    assert 'evidence-origin-authentication-failed' in {g['code'] for g in result['gaps']}
    with pytest.raises(ValueError):
        verify_recovery(bundles['baseline'], bundles['fault'], bundles['recovery'],
                        contract=contract, evidence=evidence, provenance='external-command')


def test_finish_rejects_insufficient_budget_without_reading_or_restarting(retained, monkeypatch):
    session, context, *_ = retained
    record = session._records[('primary', 'baseline')]
    record.pop('bundle')
    record['bootstrap'] = None
    context.execution_mode = 'controlled-test'
    context.phase['operation'] = 'baseline'
    context.deadline = time.monotonic()+1
    monkeypatch.setattr(production, 'finish_runtime_collection', lambda *a, **k: pytest.fail('No provider read expected'))
    with pytest.raises(StateError, match='budget'):
        session.finish(context)


def test_duplicate_collection_or_new_inputs_are_not_silently_replayed(retained):
    session, context, contract, *_ = retained
    context.phase['operation'] = 'baseline'
    with pytest.raises(StateError, match='replacement'):
        session.collect(context, intent=contract, intent_sha256=digest({}))


def test_untrusted_constructor_arguments_do_not_create_production_session():
    # The final combined candidate supplies concrete bootstrap; isolated tests
    # need not substitute a permissive constructor when that module is absent.
    try:
        import devsecops.factory.canonical_bootstrap  # noqa: F401
    except ImportError:
        pytest.skip('Concrete bootstrap is authored in its separate isolated lane')
    with pytest.raises(StateError):
        production.RuntimeObservationSession({}, bootstrap={})


@pytest.mark.parametrize('attempt', ['primary', 'replay'])
def test_canonical_begin_finish_calls_actual_collectors_with_fixed_window_and_exact_paths(retained, monkeypatch, attempt):
    session, context, _, _, bundles, *_ = retained
    fixture_context = deepcopy(bundles['baseline']['context_snapshot'])
    intent = deepcopy(fixture_context['intent'])
    intent['attempt_id'] = attempt
    paths = {}
    for name, value in [('config', fixture_context['factory_config']), ('lock', fixture_context['factory_lock']), ('intent', intent)]:
        path = context.run_dir/(attempt+'-'+name+'.json')
        path.write_bytes(canonical_bytes(value))
        paths[name] = path
    session._journal.state['lifecycle'] = {'inputs': {k: {'path': str(v)} for k, v in paths.items() if k != 'intent'}}
    session._journal._artifact_path = lambda path: __import__('pathlib').Path(path)
    session._records = {}
    context.phase.update(operation='up', attempt='shared')
    context.execution_mode = 'controlled-test'
    real_start, real_finish = production.start_runtime_collection, production.finish_runtime_collection
    calls = []

    def start(value, **kwargs):
        calls.append(('start', deepcopy(value['intent']), dict(kwargs['authority'])))
        return real_start(value, clients=clients_for(value, NOW), now=NOW, **kwargs)

    def finish(value, pending, **kwargs):
        calls.append(('finish', deepcopy(pending['window']), dict(kwargs['authority'])))
        return real_finish(value, pending, clients=clients_for(value, NOW+timedelta(seconds=30)),
                           now=NOW+timedelta(seconds=30), **kwargs)

    monkeypatch.setattr(production, 'start_runtime_collection', start)
    monkeypatch.setattr(production, 'finish_runtime_collection', finish)
    monkeypatch.setattr(production, 'clock', lambda: NOW+timedelta(seconds=30))
    begun = session.begin(context, intent=paths['intent'], intent_sha256=production._sha(paths['intent'].read_bytes()))
    assert begun.collection is None
    with pytest.raises(StateError, match='already exists'):
        session.begin(context, intent=paths['intent'], intent_sha256=production._sha(paths['intent'].read_bytes()))
    context.phase.update(operation='baseline', attempt=attempt)
    finished = session.finish(context)
    assert finished.window == begun.window == calls[1][1]
    assert calls[0][2] == calls[1][2] == paths
    assert session._retained(attempt, 'baseline')['bundle']['execution_mode'] == 'controlled-test'
    assert finished.collection is not None and finished.collection_sha256 == production._sha(finished.collection.read_bytes())
    with pytest.raises(StateError, match='already finished'):
        session.finish(context)
    assert len(calls) == 2
