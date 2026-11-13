"""Controlled journal/native/package boundary; real local correction validation.

No provider, Git writer, registry or lab is called. Native origin is explicitly
controlled-test; prior phase receipts and package verification are modeled.
"""
from datetime import datetime, timedelta, timezone
import json
import os
import time
from types import MappingProxyType

import pytest

from test_native_handoff import prepared, correction_case  # noqa: F401
from test_factory_lifecycle import inputs_for
from devsecops.factory import correction_phase as module, native_provenance, owned_linux
from devsecops.factory.lifecycle import PhaseContext, _readonly, _load_inputs
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha


@pytest.fixture
def accepted(prepared, tmp_path, monkeypatch):  # noqa: F811
    scope, args, selected = prepared
    inputs, identities = inputs_for(tmp_path)
    inputs['config']['expires_at'] = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    identities['config_sha256'] = _sha(_canonical(inputs['config']))
    raw, docs = _load_inputs(inputs, identities, inputs['config']['environment'])
    journal = RunJournal.create(tmp_path/'run', identities=identities,
        environment_id=inputs['config']['environment'])
    journal.initialize_lifecycle(inputs=raw, execution_mode='controlled-test', driver_id='owned-linux')
    package_calls = []
    monkeypatch.setattr(module, '_installed', lambda *values: package_calls.append(values))
    driver = owned_linux.OwnedLinuxDriver(journal, plugin_id='devsecops@devsecops-local', plugin_version='0.2.0')
    evidence = tmp_path/'modeled-prior-phase.json'
    evidence.write_text('{"controlled_test_prior_phase":true}')
    with journal.lifecycle_lock(), journal.mutation_lock(tmp_path/'locks'):
        for phase in docs['scenario']['phases']:
            journal.begin_phase(phase['id'])
            if phase['id'] == 'primary-correction':
                break
            journal.finish_phase(phase['id'], 'completed', 'controlled-prior-phase', receipt_artifacts=[evidence])
        request_info = journal.request_native('primary-correction', 'prepare-gitops-correction',
            input_artifacts=scope.input_artifacts)
    request_path = journal.run_dir/'native/primary-correction/request.json'
    request_raw = request_path.read_bytes()
    request = json.loads(request_raw)
    patch = args.patch.read_bytes()
    artifact = journal.run_dir/request['output_directory']/'proposal.patch'
    artifact.write_bytes(patch)
    result = {'schema_version': 1, 'request_id': request['request_id'], 'run_id': request['run_id'],
        'step_id': request['step_id'], 'request_sha256': request_info['request_sha256'],
        'status': 'completed', 'gaps': [], 'artifacts': [{'name': 'proposal', 'kind': 'patch',
            'path': str(artifact.relative_to(journal.run_dir)), 'sha256': _sha(patch), 'bytes': len(patch)}]}
    result_path = journal.run_dir/request['result_path']
    result_path.write_bytes(_canonical(result))
    # Only acceptance/provenance are modeled. The new handler calls the actual
    # fixed deterministic validator on real Git, schemas and kubeconform below.
    receipt = journal.accept_native(result_path, identities=identities,
        validator=lambda *_: ['controlled-accepted-native-artifact'])
    origin = native_provenance.NativeExecutionOrigin(native_provenance._ISSUER)
    document = {'execution_mode': 'controlled-test', 'run_id': request['run_id'],
        'attempt_id': 'primary', 'request_sha256': request_info['request_sha256']}
    native = {'native_execution': _canonical(document), 'native_request': request_raw,
        'native_result': _canonical(result), 'native_validation':
            (journal.run_dir/receipt['validation']['path']).read_bytes(), 'patch': patch}
    native_provenance._ISSUED[origin] = {'pid': os.getpid(), 'execution_mode': 'controlled-test',
        'artifacts': MappingProxyType(native)}
    return {'journal': journal, 'driver': driver, 'docs': docs, 'scope': scope, 'args': args,
        'selected': selected, 'request_sha256': request_info['request_sha256'], 'origin': origin,
        'artifact': artifact, 'package_calls': package_calls}


def bind(value):
    value['driver'].bind_correction_scope(value['scope'],
        request_sha256=value['request_sha256'], origin=value['origin'])


def begin_validation(value):
    journal = value['journal']
    with journal.lifecycle_lock():
        reference = journal.state['native_results'][0]['validation']
        journal.finish_phase('primary-correction', 'completed', 'native-validation-recovered',
            receipt_artifacts=[journal.run_dir/reference['path']])
        journal.begin_phase('primary-validate-correction')
    phase = next(row for row in value['docs']['scenario']['phases'] if row['id'] == 'primary-validate-correction')
    return PhaseContext(journal.state['run_id'], _readonly(phase), _readonly(journal.identities),
        _readonly(value['docs']), _readonly(journal.state['lifecycle']['inputs']), journal.run_dir,
        time.monotonic()+60, 'controlled-test')


def execute(value, context):
    with value['journal'].lifecycle_lock():
        return value['driver'].execute(context)


def test_actual_phase_revalidates_exact_accepted_patch_locally(accepted):
    source = accepted['args'].repo/'deploy/policy.yaml'
    before = source.read_bytes()
    bind(accepted)
    outcome = execute(accepted, begin_validation(accepted))
    assert outcome.status == 'completed' and outcome.mutation_effect == 'not_applied'
    assert outcome.assertions == ()
    report = json.loads(outcome.artifacts[0].read_bytes())
    assert report['patch_sha256'] == _sha(accepted['args'].patch.read_bytes())
    assert report['delivery_authority'] == 'not-issued' and report['canonical_success'] is False
    assert report['runtime_recovery'] == 'not-evaluated' and report['gaps'] == []
    correction = next(path for path in outcome.artifacts if path.name == 'correction.json')
    validated = json.loads(correction.read_bytes())
    assert validated['decision'] == 'pass' and validated['delivery_status'] == 'not_performed'
    assert source.read_bytes() == before
    assert accepted['journal'].state['active_mutation'] is None
    assert accepted['package_calls']


def test_missing_direct_scope_is_explicit_incomplete_not_restored(accepted):
    outcome = execute(accepted, begin_validation(accepted))
    report = json.loads(outcome.artifacts[0].read_bytes())
    assert outcome.status == 'incomplete'
    assert report['gaps'] == ['direct-correction-runtime-selection-unavailable']
    assert report['delivery_authority'] == 'not-issued'
    assert len(outcome.artifacts) == 1


@pytest.mark.parametrize('change', ['request', 'origin', 'scope', 'pid', 'package'])
def test_binding_rejects_substitution_before_retaining(accepted, change, monkeypatch):
    if change == 'request':
        accepted['request_sha256'] = 'f'*64
    elif change == 'origin':
        accepted['origin'] = object()
    elif change == 'scope':
        accepted['scope'] = object()
    elif change == 'pid':
        accepted['driver']._pid += 1
    else:
        def reject(*_):
            raise StateError('Selected package changed')
        monkeypatch.setattr(module, '_installed', reject)
    with pytest.raises(StateError):
        bind(accepted)
    assert accepted['driver']._correction_bindings == {}


def test_binding_cannot_be_replaced_or_claimed_twice(accepted):
    bind(accepted)
    with pytest.raises(StateError, match='already retained'):
        bind(accepted)
    context = begin_validation(accepted)
    assert execute(accepted, context).status == 'completed'
    count = len(list(accepted['journal'].run_dir.glob('correction-phase-*')))
    with pytest.raises(StateError, match='already claimed'):
        execute(accepted, context)
    assert len(list(accepted['journal'].run_dir.glob('correction-phase-*'))) == count


@pytest.mark.parametrize('change', ['tool', 'scope'])
def test_later_runtime_selection_changes_remain_incomplete(accepted, change):
    bind(accepted)
    if change == 'tool':
        accepted['selected']['tools']['kubeconform'][0].write_bytes(b'changed')
    else:
        accepted['scope'].input_artifacts['correction-contract'].write_bytes(b'{}')
    outcome = execute(accepted, begin_validation(accepted))
    report = json.loads(outcome.artifacts[0].read_bytes())
    assert outcome.status == 'incomplete'
    assert report['gaps'] == ['accepted-correction-revalidation-incomplete-or-rejected']
    assert report['delivery_authority'] == 'not-issued'


def test_changed_accepted_patch_fails_journal_reconciliation(accepted):
    bind(accepted)
    context = begin_validation(accepted)
    accepted['artifact'].write_bytes(b'replaced')
    with pytest.raises(StateError):
        execute(accepted, context)


def test_new_driver_cannot_restore_binding_from_journal(accepted):
    bind(accepted)
    accepted['driver'] = owned_linux.OwnedLinuxDriver(accepted['journal'],
        plugin_id='devsecops@devsecops-local', plugin_version='0.2.0')
    assert execute(accepted, begin_validation(accepted)).status == 'incomplete'


def test_operator_window_expiry_prevents_local_phase_work(accepted, monkeypatch):
    bind(accepted)
    context = begin_validation(accepted)
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc)+timedelta(hours=2)
    monkeypatch.setattr(module, 'datetime', Later)
    with pytest.raises(StateError, match='window expired'):
        execute(accepted, context)
    assert not list(accepted['journal'].run_dir.glob('correction-phase-*'))


def test_controlled_origin_cannot_be_substituted_with_different_mode(accepted):
    native_provenance._ISSUED[accepted['origin']]['execution_mode'] = 'direct'
    with pytest.raises(StateError, match='origin changed'):
        bind(accepted)
    assert accepted['driver']._correction_bindings == {}


def test_publication_recheck_failure_withdraws_completed_diagnostic(accepted, monkeypatch):
    bind(accepted)
    context = begin_validation(accepted)
    original = owned_linux._write_terminal
    def drift(directory, name, report):
        identity = original(directory, name, report)
        accepted['scope'].input_artifacts['correction-contract'].write_bytes(b'changed after terminal')
        return identity
    monkeypatch.setattr(owned_linux, '_write_terminal', drift)
    with pytest.raises(StateError, match='input changed'):
        execute(accepted, context)
    directory, = accepted['journal'].run_dir.glob('correction-phase-*')
    assert not (directory/'diagnostic.json').exists()
