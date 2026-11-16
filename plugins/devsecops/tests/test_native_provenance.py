"""Controlled real subprocess + journal integration; never a live native proof."""
from copy import deepcopy
import json
from pathlib import Path
import pickle
import threading

import pytest

from test_native_codex import lab
from devsecops.factory import native_provenance as provenance
from devsecops.factory.state import RunJournal, StateError, _sha
from devsecops.runtime.context import canonical_bytes, validate
from devsecops.runtime.recovery import NATIVE_EXECUTION_SCHEMA, NATIVE_EXECUTION_V2_SCHEMA

__all__ = ['lab']


def controlled_validator(request, result, artifacts):
    assert request['workflow'] == 'prepare-gitops-correction'
    assert len(result['artifacts']) == 1
    assert json.loads(artifacts['report']) == {'controlled_test_only': True}
    return ['controlled-patch-artifact-inspected']


@pytest.fixture
def correction(lab, tmp_path):
    ids = lab['request']['identities']
    journal = RunJournal.create(tmp_path / 'journal-run', identities=ids, environment_id='controlled-lab')
    descriptor = journal.request_native('primary-correction', 'prepare-gitops-correction',
                                       input_artifacts={'frozen-input': lab['root'] / 'input.json'})
    options = {key: value for key, value in lab['args'].items()
               if key in provenance._REQUIRED_OPTIONS | provenance._OPTIONAL_OPTIONS}
    args = dict(journal=journal, request_path=Path(descriptor['request_path']),
        request_sha256=descriptor['request_sha256'], attempt_id='primary', environment='controlled-lab',
        expected_client_sha256=_sha(lab['args']['executable'].read_bytes()), validator=controlled_validator,
        provenance_output=tmp_path / 'native-origin', native_options=options, _controlled_test=True)
    return lab, journal, args


def frozen(args):
    root = args['provenance_output']
    return {name: (root / (name + ('.patch' if name == 'patch' else '.json'))).read_bytes()
            for name in provenance._BINDINGS}


def test_concrete_driver_and_independent_journal_validation_issue_only_controlled_origin(correction):
    _, journal, args = correction
    origin = provenance.execute_validated_correction(**args)
    document = origin.document
    validate(document, NATIVE_EXECUTION_V2_SCHEMA)
    validate(document, NATIVE_EXECUTION_SCHEMA)
    assert document['execution_mode'] == 'controlled-test'
    assert document['authority_scope'] == 'native-execution-and-artifact-validation-only'
    assert document['driver'] == 'codex-cli-single-turn'
    assert document['turn_ordinal'] == 1 and document['server_turn_id'] is None
    assert document['configured_model'] == 'exact-test-model'
    assert document['configured_reasoning_effort'] == 'ultra'
    assert document['executable_sha256'] == args['expected_client_sha256']
    assert document['request_sha256'] == args['request_sha256']
    assert journal.state['execution_status'] == 'ready'
    artifacts = frozen(args)
    assert document['validation_receipt_sha256'] == _sha(artifacts['native_validation'])
    assert document['patch_sha256'] == _sha(artifacts['patch'])
    assert origin.artifact_sha256 == {name: _sha(raw) for name, raw in artifacts.items()}
    checked = provenance.verify_native_execution_origin(origin, artifacts=artifacts)
    assert checked['bindings_match'] is True and checked['verified'] is False
    assert checked['execution_mode'] == 'controlled-test'
    assert (args['provenance_output'] / 'origin-manifest.json').exists()


@pytest.mark.parametrize('name', provenance._BINDINGS)
def test_origin_binds_every_native_byte_input(correction, name):
    _, _, args = correction
    origin = provenance.execute_validated_correction(**args)
    artifacts = frozen(args)
    artifacts[name] += b' '
    result = provenance.verify_native_execution_origin(origin, artifacts=artifacts)
    assert result['bindings_match'] is False and result['verified'] is False


def test_imported_forged_or_copied_documents_cannot_become_an_origin(correction):
    _, _, args = correction
    origin = provenance.execute_validated_correction(**args)
    document = origin.document
    document['execution_mode'] = 'direct'
    for supplied in (document, canonical_bytes(document), args['provenance_output'] / 'native_execution.json',
                     object.__new__(provenance.NativeExecutionOrigin)):
        with pytest.raises(StateError, match='directly issued'):
            provenance.verify_native_execution_origin(supplied, artifacts=frozen(args))
    with pytest.raises(StateError, match='direct execution'):
        provenance.NativeExecutionOrigin()
    with pytest.raises(StateError, match='serialized'):
        pickle.dumps(origin)
    assert origin.document['execution_mode'] == 'controlled-test'


def test_origin_rejects_inherited_capability_in_a_different_process(correction, monkeypatch):
    _, _, args = correction
    origin = provenance.execute_validated_correction(**args)
    artifacts = frozen(args)
    issuer_pid = provenance.os.getpid()
    monkeypatch.setattr(provenance.os, 'getpid', lambda: issuer_pid + 1)
    with pytest.raises(StateError, match='issuing process'):
        provenance.verify_native_execution_origin(origin, artifacts=artifacts)


def test_pinned_client_mismatch_stops_before_native_subprocess(correction):
    _, journal, args = correction
    args['expected_client_sha256'] = 'f' * 64
    with pytest.raises(StateError, match='pinned binary'):
        provenance.execute_validated_correction(**args)
    assert journal.state['execution_status'] == 'awaiting_native_step'
    assert not args['provenance_output'].exists()


def test_untrusted_json_cannot_select_a_validator(correction):
    _, journal, args = correction
    args['validator'] = 'module:pretend_validate'
    with pytest.raises(StateError, match='trusted-host artifact validator'):
        provenance.execute_validated_correction(**args)
    assert journal.state['execution_status'] == 'awaiting_native_step'


def reject_validator(request, result, artifacts):
    raise ValueError('private-raw-validator-diagnostic')


def test_validator_rejection_leaves_request_pending_and_no_origin(correction):
    _, journal, args = correction
    args['validator'] = reject_validator
    with pytest.raises(StateError, match='Independent native artifact validation failed') as error:
        provenance.execute_validated_correction(**args)
    assert 'private-raw-validator-diagnostic' not in str(error.value)
    assert journal.state['execution_status'] == 'awaiting_native_step'
    assert not (args['provenance_output'] / 'origin-manifest.json').exists()


def test_controlled_origin_survives_no_json_upgrade(correction):
    _, _, args = correction
    origin = provenance.execute_validated_correction(**args)
    artifacts = frozen(args)
    changed = deepcopy(origin.document)
    changed['execution_mode'] = 'direct'
    artifacts['native_execution'] = canonical_bytes(changed)
    result = provenance.verify_native_execution_origin(origin, artifacts=artifacts)
    assert result['execution_mode'] == 'controlled-test'
    assert not result['bindings_match'] and not result['verified']


def test_origin_publication_failure_never_issues_authority_even_after_handoff_validation(correction, monkeypatch):
    _, journal, args = correction
    original = provenance.native_codex._write_private
    def failed(directory, name, raw):
        if name == 'origin-manifest.json':
            raise OSError('controlled-publication-error')
        return original(directory, name, raw)
    monkeypatch.setattr(provenance.native_codex, '_write_private', failed)
    with pytest.raises(OSError):
        provenance.execute_validated_correction(**args)
    assert journal.state['execution_status'] == 'ready'
    assert not (args['provenance_output'] / 'origin-manifest.json').exists()


def test_current_request_and_environment_are_checked_before_execution(correction):
    _, journal, args = correction
    args['environment'] = 'different-lab'
    with pytest.raises(StateError, match='exact pending'):
        provenance.execute_validated_correction(**args)
    assert journal.state['execution_status'] == 'awaiting_native_step'
    assert not args['provenance_output'].exists()


def test_validator_cannot_change_frozen_inputs_and_leave_an_accepted_handoff(correction):
    _, journal, args = correction
    def changing_validator(request, result, artifacts):
        path = journal.run_dir / request['input_artifacts'][0]['path']
        path.write_bytes(b'changed-controlled-input')
        return ['controlled-invalid-change']
    args['validator'] = changing_validator
    with pytest.raises(StateError, match='Frozen native inputs changed'):
        provenance.execute_validated_correction(**args)
    assert not (args['provenance_output'] / 'origin-manifest.json').exists()
    # The append-only journal did not accept the altered handoff.
    events = [json.loads(path.read_bytes()) for path in (journal.run_dir / 'journal').glob('*.json')]
    assert events and not any(event['type'] == 'native_validated' for event in events)


def test_cancellation_during_independent_validation_cannot_issue_origin(correction):
    _, journal, args = correction
    cancel = threading.Event()
    args['native_options']['cancel'] = cancel
    def cancelling_validator(request, result, artifacts):
        cancel.set()
        return ['controlled-cancelled-validation']
    args['validator'] = cancelling_validator
    with pytest.raises(StateError, match='cancelled'):
        provenance.execute_validated_correction(**args)
    assert journal.state['execution_status'] == 'awaiting_native_step'
    assert not (args['provenance_output'] / 'origin-manifest.json').exists()


def test_recovery_consumes_opaque_origin_without_upgrading_controlled_evidence(correction):
    from test_runtime_recovery import fixture, NOW
    from devsecops.runtime.recovery import verify_recovery
    _, _, args = correction
    origin = provenance.execute_validated_correction(**args)
    bundles, contract, evidence = fixture()
    for name in provenance._BINDINGS:
        evidence[name] = args['provenance_output'] / (name + ('.patch' if name == 'patch' else '.json'))
    result = verify_recovery(bundles['baseline'], bundles['fault'], bundles['recovery'],
        contract=contract, evidence=evidence, native_execution_origin=origin, now=NOW)
    assert result['execution_mode'] == 'controlled-test' and result['decision'] == 'unknown'
    native = next(c for c in result['conditions'] if c['id'] == 'native-execution-origin')
    assert native['observed_state'] == 'unknown' and not native['authenticated']
    assert result['provenance']['verified'] is False
