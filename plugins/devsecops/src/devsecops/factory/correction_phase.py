"""Canonical local correction revalidation from a direct native handoff.

Retained requests lack trusted repository/tool/schema runtime selections. They
cannot recreate a CorrectionScope or NativeExecutionOrigin after a restart.
This component never prepares a Git write or grants delivery authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
import re
import time
from types import MappingProxyType
from uuid import uuid4

from . import native_handoff, native_provenance
from devsecops.runtime.cli import _new_directory, _verify_output_path
from .lifecycle import PhaseOutcome
from .mission_git_operation import _installed
from .state import (MAX_ARTIFACT_BYTES, MAX_DOCUMENT_BYTES, StateError, _canonical,
                    _exclusive, _json, _read, _sha)

_STEPS = {'primary-correction': 'primary', 'replay-correction': 'replay'}
_OUTPUTS = ('report.json', 'report.html', 'report.md', 'run-manifest.json', 'proposal.patch',
            'correction.json', 'correction.html', 'correction.md', 'rendered-before.json',
            'rendered-after.json', 'correction-manifest.json')


def _guard(driver, deadline, *, allow_mutation=False):
    from .owned_linux import OwnedLinuxDriver
    journal = driver.journal
    state = journal.state
    lifecycle = state.get('lifecycle', {})
    if (type(driver) is not OwnedLinuxDriver or driver._pid != os.getpid()
            or not journal._lifecycle_locked or time.monotonic() >= deadline
            or not state['environment_reserved'] or (state['active_mutation'] is not None and not allow_mutation)
            or state['active_native'] is not None or journal.needs_reconciliation
            or lifecycle.get('driver_id') != 'owned-linux' or lifecycle.get('abort') is not None
            or lifecycle.get('final_result') is not None):
        raise StateError('Correction requires the current idle owned lifecycle')
    config = _json(_checked(journal, lifecycle['inputs']['config'], MAX_DOCUMENT_BYTES))
    expiry = datetime.fromisoformat(config['expires_at'].replace('Z', '+00:00'))
    if datetime.now(timezone.utc) >= expiry:
        raise StateError('Correction operator window expired')
    _installed(journal.identities['package_sha256'], driver.plugin_id, driver.plugin_version, deadline)
    if datetime.now(timezone.utc) >= expiry:
        raise StateError('Correction operator window expired during package verification')
    return state


def _checked(journal, reference, limit=MAX_ARTIFACT_BYTES):
    raw = _read(journal._artifact_path(reference['path']), limit)
    if len(raw) != reference['bytes'] or _sha(raw) != reference['sha256']:
        raise StateError('Accepted correction artifact changed')
    return raw


def _accepted(driver, step, request_sha256, deadline, *, allow_mutation=False):
    state = _guard(driver, deadline, allow_mutation=allow_mutation)
    requests = [row for row in state['native_requests'] if row['step_id'] == step]
    results = [row for row in state['native_results'] if row['step_id'] == step]
    if (step not in _STEPS or len(requests) != 1 or len(results) != 1
            or requests[0]['request']['sha256'] != request_sha256
            or results[0]['status'] != 'completed'
            or requests[0]['request_id'] != results[0]['request_id']):
        raise StateError('Exactly one accepted correction from this attempt is required')
    requested, accepted = requests[0], results[0]
    request_raw = _checked(driver.journal, requested['request'], MAX_DOCUMENT_BYTES)
    result_raw = _checked(driver.journal, accepted['result'], MAX_DOCUMENT_BYTES)
    validation_raw = _checked(driver.journal, accepted['validation'], MAX_DOCUMENT_BYTES)
    request, result, validation = map(_json, (request_raw, result_raw, validation_raw))
    if (request['workflow'] != 'prepare-gitops-correction' or request['step_id'] != step
            or request['run_id'] != state['run_id'] or request['identities'] != driver.journal.identities
            or result['status'] != 'completed' or result['request_sha256'] != request_sha256
            or any(result[key] != request[key] for key in ('run_id', 'step_id', 'request_id'))
            or result['artifacts'] != accepted['artifacts']
            or validation['request_sha256'] != request_sha256
            or validation['result_sha256'] != _sha(result_raw)):
        raise StateError('Accepted correction differs from its current request')
    patches = [row for row in result['artifacts'] if row['kind'] == 'patch']
    if len(result['artifacts']) != 1 or len(patches) != 1:
        raise StateError('Correction requires exactly the accepted patch')
    patch = _checked(driver.journal, patches[0], 256_000)
    inputs = {row['name']: _checked(driver.journal, row) for row in request['input_artifacts']}
    return request, result, inputs, patches[0]['name'], {
        'native_request': request_raw, 'native_result': result_raw,
        'native_validation': validation_raw, 'patch': patch}


def _audit_binding(driver, binding, deadline, *, allow_mutation=False):
    selected = native_handoff._scope(binding['scope'])
    native_handoff._audit(selected, deadline)
    current = _accepted(driver, binding['step'], binding['request_sha256'], deadline, allow_mutation=allow_mutation)
    request, result, inputs, patch_name, artifacts = current
    if inputs != selected['raw'] or artifacts != binding['artifacts']:
        raise StateError('Correction scope or accepted native bytes changed')
    origin = binding['origin']
    try:
        document = origin.document
    except (KeyError, TypeError):
        raise StateError('Original native origin is unavailable') from None
    artifacts = dict(artifacts, native_execution=_canonical(document))
    proof = native_provenance.verify_native_execution_origin(origin, artifacts=artifacts)
    expected_mode = ('controlled-test' if driver.journal.state['lifecycle']['execution_mode']
                     == 'controlled-test' else 'direct')
    if (not proof['bindings_match'] or proof['execution_mode'] != expected_mode
            or (expected_mode == 'direct' and not proof['verified'])
            or document.get('run_id') != request['run_id']
            or document.get('attempt_id') != _STEPS[binding['step']]
            or document.get('request_sha256') != binding['request_sha256']):
        raise StateError('Direct native correction origin changed')
    return selected, request, result, patch_name, artifacts


def bind(driver, scope, *, request_sha256, origin):
    """Retain only a directly prepared scope and accepted same-process origin."""
    if (not isinstance(request_sha256, str) or not re.fullmatch('[a-f0-9]{64}', request_sha256)
            or type(origin) is not native_provenance.NativeExecutionOrigin):
        raise StateError('Direct correction scope, request and origin are required')
    deadline = time.monotonic() + 120
    with driver.journal.lifecycle_lock():
        state = _guard(driver, deadline)
        step = (state['lifecycle'].get('active_phase') or {}).get('phase_id')
        if step not in _STEPS or request_sha256 in driver._correction_bindings:
            raise StateError('Correction binding is stale or already retained')
        _, _, _, _, artifacts = _accepted(driver, step, request_sha256, deadline)
        binding = {'scope': scope, 'origin': origin, 'step': step, 'request_sha256': request_sha256,
                   'artifacts': artifacts, 'consumed': False}
        _audit_binding(driver, binding, deadline)
        driver._correction_bindings[request_sha256] = binding


def execute(driver, context, deadline):
    """Revalidate the exact accepted patch; all external actions remain absent."""
    state = _guard(driver, deadline)
    phase = dict(context.phase)
    attempt = phase.get('attempt')
    step = str(attempt) + '-correction'
    if (attempt not in ('primary', 'replay') or phase.get('operation') != 'validate-correction'
            or phase.get('id') != str(attempt) + '-validate-correction'
            or tuple(phase.get('depends_on', ())) != (step,)
            or (state['lifecycle'].get('active_phase') or {}).get('phase_id') != phase['id']
            or not any(row['phase_id'] == step and row['outcome'] == 'completed'
                       for row in state['lifecycle']['phase_records'])):
        raise StateError('Correction validation does not match its accepted native phase')
    matches = [value for value in driver._correction_bindings.values() if value['step'] == step]
    binding = matches[0] if len(matches) == 1 else None
    if binding is not None and binding['consumed']:
        raise StateError('Correction validation already claimed; inspect retained evidence')
    output = driver.journal.run_dir / ('correction-phase-' + uuid4().hex)
    with _new_directory(output) as directory:
        report = {'schema_version': 1, 'kind': 'owned-linux-correction-phase', 'run_id': context.run_id,
            'phase_id': phase['id'], 'identities': dict(driver.journal.identities),
            'execution_mode': context.execution_mode, 'status': 'incomplete',
            'delivery_authority': 'not-issued', 'delivery_status': 'not-performed',
            'runtime_recovery': 'not-evaluated', 'canonical_success': False, 'checks': [], 'gaps': []}
        artifacts = []
        if binding is None:
            report['gaps'].append('direct-correction-runtime-selection-unavailable')
        else:
            # A failure cannot cause automatic repeated validation or revive origin.
            binding['consumed'] = True
            _exclusive(output / 'intent.json', _canonical({'phase_id': phase['id'],
                'request_sha256': binding['request_sha256'], 'scope': 'local-validation-only'}))
            artifacts.append(output / 'intent.json')
            try:
                selected, request, result, patch_name, native = _audit_binding(driver, binding, deadline)
                validator = native_handoff._CorrectionValidator(selected, request, output, deadline)
                checks = validator(request, result, MappingProxyType({patch_name: native['patch']}))
                _audit_binding(driver, binding, deadline)
                files = [output / 'validated.patch', *(output / 'validation' / name for name in _OUTPUTS)]
                refs = {}
                for path in files:
                    raw = _read(path, MAX_ARTIFACT_BYTES)
                    refs[str(path.relative_to(output))] = {'sha256': _sha(raw), 'bytes': len(raw)}
                report.update(status='completed', checks=list(checks), artifacts=refs,
                    request_sha256=binding['request_sha256'], patch_sha256=_sha(native['patch']))
                artifacts.extend(files)
            except (StateError, OSError, ValueError, TypeError, KeyError):
                report['gaps'].append('accepted-correction-revalidation-incomplete-or-rejected')
        driver._admit(context, deadline)
        _guard(driver, deadline)
        from .owned_linux import _write_terminal, _withdraw_terminal
        _verify_output_path(output, directory)
        identity = None
        try:
            identity = _write_terminal(directory, 'diagnostic.json', report)
            os.fsync(directory)
            _verify_output_path(output, directory)
            driver._admit(context, deadline)
            _guard(driver, deadline)
            if report['status'] == 'completed':
                _audit_binding(driver, binding, deadline)
                for path in files:
                    raw = _read(path, MAX_ARTIFACT_BYTES)
                    if refs[str(path.relative_to(output))] != {'sha256': _sha(raw), 'bytes': len(raw)}:
                        raise StateError('Correction output changed during publication')
                # This original in-process result is issued only after terminal
                # publication and all original scope/native/current-byte checks.
                # A consumed claim or reloaded diagnostic cannot recreate it.
                binding['validated_artifacts'] = {
                    'patch': (output/'validated.patch', refs['validated.patch']['sha256']),
                    'correction': (output/'validation/correction.json', refs['validation/correction.json']['sha256']),
                    'rendered_before': (output/'validation/rendered-before.json', refs['validation/rendered-before.json']['sha256']),
                    'rendered_after': (output/'validation/rendered-after.json', refs['validation/rendered-after.json']['sha256']),
                }
        except BaseException as error:
            if identity is not None:
                _withdraw_terminal(directory, 'diagnostic.json', identity, error)
            raise
        return PhaseOutcome(report['status'], 'owned-linux-correction-validated' if report['status'] == 'completed'
            else 'owned-linux-correction-incomplete', tuple([output / 'diagnostic.json', *artifacts]),
            assertions=(), mutation_effect='not_applied')


def require_validated_correction(driver, context, *, attempt):
    """Audit original success and return exact artifact selections plus scope.

    This is a read-only admission seam for the fixed delivery/runtime callers;
    it does not execute a correction again or issue permission to write Git.
    """
    if attempt not in {'primary', 'replay'} or context.phase['attempt'] != attempt or context.phase['operation'] not in {
            'deliver', 'probe', 'native', 'verify', 'reset'}:
        raise StateError('Validated correction is outside its canonical consumer phase')
    driver._admit(context, context.deadline)
    matches = [value for value in driver._correction_bindings.values() if value['step'] == attempt+'-correction']
    if len(matches) != 1 or not matches[0].get('validated_artifacts'):
        raise StateError('Original successful correction validation is unavailable')
    binding = matches[0]
    selected, request, result, patch_name, native = _audit_binding(driver, binding, context.deadline, allow_mutation=True)
    artifacts = dict(binding['validated_artifacts'])
    for name, (path, sha) in artifacts.items():
        raw = _read(path, MAX_ARTIFACT_BYTES)
        if _sha(raw) != sha or (name == 'patch' and raw != native['patch']):
            raise StateError('Original validated correction artifact bytes changed')
    contract_path = selected['paths']['correction-contract']
    artifacts['correction_contract'] = (contract_path, _sha(_read(contract_path, MAX_ARTIFACT_BYTES)))
    return MappingProxyType({'artifacts': MappingProxyType(artifacts), 'scope': binding['scope'],
        'native_artifacts': MappingProxyType(dict(native)),
        'origin': binding['origin'], 'request_sha256': binding['request_sha256'],
        'base_commit': selected['base_commit'], 'base_tree': deepcopy(selected['manifest']),
        'execution_mode': driver.journal.state['lifecycle']['execution_mode']})
