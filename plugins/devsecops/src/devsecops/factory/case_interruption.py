"""D6 separate read-only client turn cancelled after its actual start event.

Only the original resident caller can issue the proof. Saved receipts remain
historical evidence; they cannot restore a native or mutation capability.
"""
from copy import deepcopy
import os
from pathlib import Path
import time

from . import native_codex, native_reports
from .native_factory_session import NativeFactorySession
from .native_phase import NativeSourceSelection
from .state import (MAX_ARTIFACT_BYTES, MAX_ARTIFACT_TOTAL_BYTES, RunJournal, StateError,
                    _canonical, _exclusive, _json, _read, _sha)

_PROOFS = {}
_STEP = 'replay-interrupted-review'
_WORKFLOW = 'review-kubernetes-change'


class InterruptedReview:
    def __new__(cls):
        raise StateError('Only the original interrupted native process can issue this proof')

    def __reduce__(self):
        raise StateError('Interrupted native proof cannot be restored from saved evidence')


def _admit(context, production):
    from .owned_production import OwnedProductionCoordinator
    if type(production) is not OwnedProductionCoordinator:
        raise StateError('D6 interruption requires the original production coordinator')
    production._current()
    driver, journal = production.driver, production.journal
    session = driver._native_session
    if type(session) is not NativeFactorySession or session._journal is not journal:
        raise StateError('D6 interruption requires the original resident native session')
    driver._admit(context, context.deadline)
    driver._package(context.identities['package_sha256'], context.deadline)
    return session, journal, session.selection_for_interrupted_review(context)


def _capture(paths):
    # Only producer-declared paths. No traversal of child-writable directories.
    paths = tuple(sorted(set(paths)))
    if len(paths) > 192:
        raise StateError('Interrupted native evidence exceeds its bounded file count')
    files, total = {}, 0
    for path in paths:
        raw = _read(path, MAX_ARTIFACT_BYTES)
        total += len(raw)
        if total > MAX_ARTIFACT_TOTAL_BYTES:
            raise StateError('Interrupted native evidence exceeds its aggregate byte bound')
        files[path] = _sha(raw)
    return files


def _child_cancelled(child, request):
    state = child.state
    if (state['run_id'] != request['run_id'] or state['active_native'] is not None
            or state['native_results'] != [] or len(state['native_requests']) != 1
            or state['native_requests'][0]['request_id'] != request['request_id']
            or len(state['native_cancellations']) != 1
            or state['native_cancellations'][0]['request_id'] != request['request_id']
            or state['native_cancellations'][0]['reason_code'] != 'd6-interrupted-review'
            or state['journal_seq'] != 3 or state['active_mutation'] is not None
            or state['environment_reserved'] or state.get('lifecycle') is not None):
        raise StateError('Interrupted child journal acquired progress or lost its cancellation')
    return state


def execute_interrupted_review(context, *, production, output):
    """One real invocation; no retry, supplied trace or main result acceptance."""
    session, journal, selection = _admit(context, production)
    before = journal.state
    output = Path(output)
    if not output.is_absolute() or not output.is_relative_to(journal.run_dir) or output.exists():
        raise StateError('D6 needs a new private output within its original run')
    maximum = min(120, selection.total_timeout, context.remaining_seconds())
    if maximum < 5:
        raise StateError('D6 has insufficient bounded time to start and reap a review')
    output.mkdir(mode=0o700)
    source = production.operations.source_selection('primary')
    if type(source) is not NativeSourceSelection:
        raise StateError('D6 requires the original actual primary fault Git source')
    scope = native_reports.prepare_review_scope(repository=source.repository,
        base_commit=source.base_commit, head_commit=source.fault_commit,
        profile=source.profile, profile_sha256=source.profile_sha256,
        tools=dict(source.tools), schemas=source.schemas, schemas_sha256=source.schemas_sha256,
        output=output / 'scope', deadline=context.deadline)
    prepared = native_reports._scope(scope)
    child = RunJournal.create(output / 'request-run', identities=dict(context.identities),
        environment_id=before['environment_id'])
    info = child.request_native(_STEP, _WORKFLOW, input_artifacts=scope.input_artifacts,
        ttl_seconds=int(maximum), expires_at=context.inputs['config']['expires_at'])
    request_path = Path(info['request_path'])
    request_raw = _read(request_path, 1_000_000)
    request = _json(request_raw)
    if (request['run_id'] == context.run_id or request['request_id'] in
            {row['request_id'] for row in before['native_requests']}):
        raise StateError('Interrupted review must have an independent child identity')
    try:
        deadline = min(context.deadline, time.monotonic() + maximum)
        native_reports._audit(prepared, deadline)
        if {row['name']: _read(child.run_dir / row['path'], MAX_ARTIFACT_BYTES)
                for row in request['input_artifacts']} != prepared['raw']:
            raise StateError('Interrupted child input differs from original prepared review')
        if native_codex._file_digest(selection.executable.resolve(strict=True), deadline) != selection.expected_client_sha256:
            raise StateError('Interrupted review client differs from original selection')
        watcher = native_codex._TurnInterruption()
        options = selection._options(session._cancel)
        options['total_timeout'] = (deadline - time.monotonic()) * .8
        if options['total_timeout'] < 1:
            raise StateError('Interrupted review budget expired during scope preparation')
        execution = native_codex.execute_native_codex(request_path, run_root=child.run_dir,
            request_sha256=_sha(request_raw), output=output / 'execution', _interruption=watcher, **options)
    finally:
        # Cancellation is durable even when preflight or publication fails.
        # No child result is ever submitted to accept_native by this executor.
        if child.state['active_native'] is not None:
            child.cancel_native('d6-interrupted-review')
    _admit(context, production)
    native_reports._audit(prepared, deadline)
    if (journal.state != before or not watcher.spawned or not watcher.turn_started
            or not watcher.interrupted_alive or not watcher.reaped
            or execution['execution_status'] != 'incomplete' or execution['result_sha256'] is not None
            or execution['artifacts'] != [] or execution['process_started'] is not True
            or 'cancelled-after-turn-start' not in execution['gaps']
            or any(execution[key] != request[key] for key in ('request_id', 'run_id', 'step_id'))):
        raise StateError('D6 did not observe an actual interrupted and reaped independent native turn')
    _child_cancelled(child, request)
    manifest_path = output / 'execution' / 'manifest.json'
    manifest = _json(_read(manifest_path, 1_000_000))
    allowed = {'request.json', 'prompt.txt', 'package.SHA256SUMS', 'result-schema.json',
               'startup-config.json', 'invocation.json', 'trace.jsonl', 'stderr.log', 'execution.json'}
    if (set(manifest) != {'schema_version', 'files'} or manifest['schema_version'] != 1
            or not set(manifest['files']) <= allowed
            or not allowed - {'startup-config.json'} <= set(manifest['files'])):
        raise StateError('Interrupted execution has unexpected or missing retained files')
    paths = [manifest_path]
    for name, ref in manifest['files'].items():
        raw = _read(output / 'execution' / name, MAX_ARTIFACT_BYTES)
        if ref != {'sha256': _sha(raw), 'bytes': len(raw)}:
            raise StateError('Interrupted native manifest differs from original bytes')
        paths.append(output / 'execution' / name)
    invocation = _json(_read(output / 'execution' / 'invocation.json', 1_000_000))
    if (invocation['executable_sha256'] != selection.expected_client_sha256
            or invocation['client_version'] != selection.expected_client_version
            or invocation['model_requested'] != selection.model
            or invocation['reasoning_effort_requested'] != selection.reasoning_effort
            or invocation['package_sha256'] != selection.package_sha256
            or _read(output / 'execution' / 'request.json', 1_000_000) != request_raw
            or _json(_read(output / 'execution' / 'execution.json', 1_000_000)) != execution
            or native_codex._file_digest(selection.executable.resolve(strict=True), deadline) != selection.expected_client_sha256):
        raise StateError('Interrupted native invocation differs from the original selected client and package')
    plugin_version = _json(_read(selection.installed_root / '.codex-plugin/plugin.json', 1_000_000))['version']
    native_codex._package(selection.installed_root, selection.package_sha256,
                         selection.plugin_id, plugin_version, deadline)
    if native_codex._engine_identity(selection.engine_python, cwd=output, deadline=deadline,
            environ=native_reports._environment(output), cancel=session._cancel) != invocation['engine_python']:
        raise StateError('Interrupted native interpreter identity changed')
    binding = {'parent_run_id': context.run_id, 'child_run_id': request['run_id'],
        'request_id': request['request_id'], 'request_sha256': _sha(request_raw),
        'status': 'cancelled', 'spawned': True, 'reaped': True, 'turn_started': True,
        'accepted_results': 0, 'usable_success_receipt': False, 'main_native_steps_advanced': False,
        'scope': 'separate-read-only-primary-fault-review', 'runtime_authority': 'not-issued',
        'detached_descendant_cleanup': 'unverified', 'client_path': str(selection.executable),
        'client_sha256': selection.expected_client_sha256, 'client_version': selection.expected_client_version,
        'model': selection.model, 'reasoning_effort': selection.reasoning_effort,
        'package_sha256': selection.package_sha256, 'base_commit': source.base_commit,
        'fault_commit': source.fault_commit, 'thread_id': watcher.thread_id}
    result = output / 'interruption.json'
    _exclusive(result, _canonical(binding))
    paths += [result, request_path, child.run_dir / 'state.json', *prepared['paths'].values()]
    paths += [child.run_dir / row['path'] for row in request['input_artifacts']]
    paths += [child.run_dir / 'journal' / f'{index:08d}.json' for index in range(1, 4)]
    proof = object.__new__(InterruptedReview)
    _PROOFS[proof] = {'pid': os.getpid(), 'journal': journal, 'child': child, 'request': request,
        'watcher': watcher, 'binding': binding, 'files': _capture(paths), 'identities': dict(context.identities)}
    require_interruption(proof, journal=journal)
    return proof


def require_interruption(proof, *, journal):
    """Historical admission only; no timeout, mutation grant or native restart."""
    record = _PROOFS.get(proof) if type(proof) is InterruptedReview else None
    if (record is None or record['pid'] != os.getpid() or record['journal'] is not journal
            or journal.identities != record['identities']
            or journal.state['run_id'] != record['binding']['parent_run_id']):
        raise StateError('An original same-process interrupted native proof is required')
    watcher = record['watcher']
    if (watcher.pid != os.getpid() or watcher.thread_id != record['binding']['thread_id']
            or not all((watcher.spawned, watcher.turn_started, watcher.interrupted_alive, watcher.reaped))):
        raise StateError('Original native process interruption observations changed')
    _child_cancelled(record['child'], record['request'])
    for path, pin in record['files'].items():
        if _sha(_read(path, MAX_ARTIFACT_BYTES)) != pin:
            raise StateError('Original interrupted native evidence changed')
    return deepcopy(record['binding'])


def interruption_artifacts(proof, *, journal):
    require_interruption(proof, journal=journal)
    return tuple(sorted(_PROOFS[proof]['files']))
