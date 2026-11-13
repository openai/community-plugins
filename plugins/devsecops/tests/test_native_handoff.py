"""Controlled orchestration plus real local validation; no native/model call."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import pickle
import shutil
import subprocess
import sys
from types import MappingProxyType

import pytest

from test_correction import correction as correction_case, require_tool
from test_factory_lifecycle import FixtureDriver, finish_native, inputs_for
from devsecops.engine import _schema_receipt
from devsecops.factory import native_handoff as handoff, native_provenance
from devsecops.factory.lifecycle import LifecycleEngine
from devsecops.factory.state import RunJournal, StateError, _canonical, _read, _sha

__all__ = ['correction_case']


@pytest.fixture
def prepared(correction_case, tmp_path):
    args, _ = correction_case
    require_tool(args)
    # Executable mutation tests can touch only this owned copy.
    tool = tmp_path / 'kubeconform'
    shutil.copy2(args.kubeconform, tool)
    selected = dict(repository=args.repo, base_commit=args.base, profile=args.profile,
        profile_sha256=_sha(args.profile.read_bytes()), contract=args.contract,
        contract_sha256=_sha(args.contract.read_bytes()),
        tools={'kubeconform': (tool, _sha(tool.read_bytes()))}, schemas=args.schema_location,
        schemas_sha256=_schema_receipt(args.schema_location)['sha256'], output=tmp_path / 'scope')
    scope = handoff.prepare_correction_scope(**selected)
    return scope, args, selected


class Driver(FixtureDriver):
    def __init__(self, scope):
        super().__init__()
        self.scope = scope

    def native_inputs(self, context):
        super().native_inputs(context)
        return self.scope.input_artifacts if context.phase['workflow'] == 'prepare-gitops-correction' else {}


@pytest.fixture
def pending(prepared, tmp_path):
    scope, args, selected = prepared
    inputs, identities = inputs_for(tmp_path)
    inputs['config']['expires_at'] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    identities['config_sha256'] = _sha(_canonical(inputs['config']))
    journal = RunJournal.create(tmp_path / 'journal', identities=identities,
        environment_id=inputs['config']['environment'])
    driver = Driver(scope)
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path / 'locks', _test_driver=driver)
    result = engine.advance()
    result = finish_native(engine, result)
    result = finish_native(engine, result)
    assert journal.state['active_native']['step_id'] == 'primary-correction'
    descriptor = result['native_request']
    options = {name: 'unused-controlled-option' for name in native_provenance._REQUIRED_OPTIONS}
    options.update(package_sha256=identities['package_sha256'], total_timeout=120)
    call = dict(request_path=Path(descriptor['request_path']), request_sha256=descriptor['request_sha256'],
        expected_client_sha256='a' * 64, native_options=options)
    return handoff.NativeCorrectionCoordinator(engine), scope, args, selected, call, driver


@pytest.fixture
def controlled_dispatch(monkeypatch):
    """Replace the native boundary, always issue visibly controlled test origin.

    This does not claim to exercise Codex. The fixed independent validator,
    journal acceptance, source freezes, Git, and kubeconform remain real.
    """
    observed = {'calls': 0, 'after_accept_error': False, 'patch': None, 'before_validate': None}
    def dispatch(journal, **kwargs):
        observed['calls'] += 1
        assert kwargs['_controlled_test'] is True
        assert type(kwargs['validator']) is handoff._CorrectionValidator
        request_raw = _read(kwargs['request_path'], 1_000_000)
        request = json.loads(request_raw)
        patch = observed['patch']
        target = journal.run_dir / request['output_directory'] / 'model-proposal.patch'
        target.write_bytes(patch)
        result = {'schema_version': 1, 'request_id': request['request_id'], 'run_id': request['run_id'],
            'step_id': request['step_id'], 'request_sha256': kwargs['request_sha256'],
            'status': 'completed', 'gaps': [], 'artifacts': [{'name': 'proposal', 'kind': 'patch',
                'path': str(target.relative_to(journal.run_dir)), 'sha256': _sha(patch), 'bytes': len(patch)}]}
        result_path = journal.run_dir / request['result_path']
        result_path.write_bytes(_canonical(result))
        if observed['before_validate'] is not None:
            observed['before_validate']()
        accepted = journal.accept_native(result_path, identities=journal.identities, validator=kwargs['validator'])
        if observed['after_accept_error']:
            raise StateError('Controlled provenance publication failure')
        # A deliberately limited fixture. It can never verify production origin.
        document = {'execution_mode': 'controlled-test', 'run_id': request['run_id'],
            'attempt_id': kwargs['attempt_id'], 'request_sha256': kwargs['request_sha256']}
        raw = {'native_execution': _canonical(document), 'native_request': request_raw,
            'native_result': _canonical(result), 'native_validation':
                _read(journal.run_dir / accepted['validation']['path'], 1_000_000), 'patch': patch}
        origin = native_provenance.NativeExecutionOrigin(native_provenance._ISSUER)
        native_provenance._ISSUED[origin] = {'pid': os.getpid(), 'execution_mode': 'controlled-test',
            'artifacts': MappingProxyType(raw)}
        observed['origin_artifacts'] = raw
        return origin
    monkeypatch.setattr(native_provenance, 'execute_validated_correction', dispatch)
    return observed


def test_fixed_validator_accepts_exact_real_patch_without_advancing_or_delivering(pending, controlled_dispatch):
    coordinator, scope, args, _, call, driver = pending
    before = args.repo.joinpath('deploy/policy.yaml').read_bytes()
    controlled_dispatch['patch'] = args.patch.read_bytes()
    origin = coordinator.execute(scope, **call)
    assert coordinator.origin(attempt='primary', request_sha256=call['request_sha256']) is origin
    proof = native_provenance.verify_native_execution_origin(origin, artifacts=controlled_dispatch['origin_artifacts'])
    assert proof['bindings_match'] and not proof['verified'] and proof['execution_mode'] == 'controlled-test'
    state = coordinator.engine.journal.state
    assert state['active_native'] is None and state['lifecycle']['active_phase']['phase_id'] == 'primary-correction'
    assert state['environment_reserved'] and 'primary-deliver' not in driver.calls and 'cleanup' not in driver.calls
    assert args.repo.joinpath('deploy/policy.yaml').read_bytes() == before
    output = coordinator.engine.journal.run_dir / ('native-correction-' + call['request_sha256'])
    report = json.loads((output / 'validation/correction.json').read_bytes())
    assert report['decision'] == 'pass' and report['delivery_status'] == 'not_performed'
    assert report['rendered_export']['status'] == 'complete'
    assert (output / 'intent.json').exists()
    # Existing engine already handles accepted handoffs; do not accept twice.
    coordinator.engine.advance()
    record = next(r for r in coordinator.engine.journal.state['lifecycle']['phase_records']
                  if r['phase_id'] == 'primary-correction')
    assert record['code'] == 'native-validation-recovered'


def test_new_coordinator_or_reopened_journal_cannot_restore_origin(pending, controlled_dispatch, tmp_path):
    coordinator, scope, args, _, call, _ = pending
    controlled_dispatch['patch'] = args.patch.read_bytes()
    coordinator.execute(scope, **call)
    for other in [handoff.NativeCorrectionCoordinator(coordinator.engine),
                  handoff.NativeCorrectionCoordinator(LifecycleEngine(
                    RunJournal.open(coordinator.engine.journal.run_dir, identities=coordinator.engine.journal.identities),
                    inputs=coordinator.engine.input_sources, shared_lock_dir=tmp_path / 'locks', _test_driver=Driver(scope)))]:
        with pytest.raises(StateError, match='cannot restore'):
            other.origin(attempt='primary', request_sha256=call['request_sha256'])
    with pytest.raises(StateError, match='cannot be restored'):
        pickle.dumps(scope)


def test_publication_failure_after_acceptance_never_grants_origin_or_retries(pending, controlled_dispatch):
    coordinator, scope, args, _, call, driver = pending
    controlled_dispatch.update(patch=args.patch.read_bytes(), after_accept_error=True)
    with pytest.raises(StateError, match='publication failure'):
        coordinator.execute(scope, **call)
    assert coordinator.engine.journal.state['active_native'] is None
    with pytest.raises(StateError, match='cannot restore'):
        coordinator.origin(attempt='primary', request_sha256=call['request_sha256'])
    with pytest.raises(StateError):
        coordinator.execute(scope, **call)
    assert controlled_dispatch['calls'] == 1 and 'primary-deliver' not in driver.calls


def test_rejected_patch_preserves_pending_handoff_and_durable_no_retry_claim(pending, controlled_dispatch):
    coordinator, scope, args, _, call, driver = pending
    controlled_dispatch['patch'] = args.patch.read_bytes().replace(b'+          port: 9090', b'+          port: invalid')
    # An exact scope violation, independently rejected even if syntax is valid.
    controlled_dispatch['patch'] = controlled_dispatch['patch'].replace(b'deploy/policy.yaml', b'deploy/foreign.yaml')
    with pytest.raises((StateError, ValueError)):
        coordinator.execute(scope, **call)
    assert coordinator.engine.journal.state['active_native'] is not None
    with pytest.raises(StateError, match='already claimed'):
        coordinator.execute(scope, **call)
    assert controlled_dispatch['calls'] == 1 and 'cleanup' not in driver.calls


@pytest.mark.parametrize('change', ['scope-file', 'tool', 'different-scope', 'package', 'client-pin', 'timeout', 'options'])
def test_changed_host_or_request_selection_stops_before_native_call(pending, controlled_dispatch, change, tmp_path):
    coordinator, scope, args, selected, call, _ = pending
    controlled_dispatch['patch'] = args.patch.read_bytes()
    if change == 'scope-file':
        scope.input_artifacts['correction-profile'].write_bytes(b'{}')
    elif change == 'tool':
        selected['tools']['kubeconform'][0].write_bytes(b'changed executable')
    elif change == 'different-scope':
        # A fresh scope with an independently selected different contract cannot
        # replace the already frozen handoff even if both selections are valid.
        contract = json.loads(args.contract.read_bytes())
        contract['id'] = 'other-correction'
        other = tmp_path / 'other-contract.json'
        other.write_bytes(_canonical(contract))
        scope = handoff.prepare_correction_scope(**{**selected, 'contract': other,
            'contract_sha256': _sha(other.read_bytes()), 'output': tmp_path / 'other-scope'})
    elif change == 'package':
        call['native_options']['package_sha256'] = 'f' * 64
    elif change == 'client-pin':
        call['expected_client_sha256'] = 'not-a-pin'
    elif change == 'timeout':
        call['native_options']['total_timeout'] = float('nan')
    else:
        call['native_options']['validator'] = 'untrusted.module:callback'
    with pytest.raises((StateError, ValueError)):
        coordinator.execute(scope, **call)
    assert controlled_dispatch['calls'] == 0


def test_changed_inputs_during_native_work_fail_independent_validation(pending, controlled_dispatch):
    coordinator, scope, args, _, call, _ = pending
    controlled_dispatch['patch'] = args.patch.read_bytes()
    controlled_dispatch['before_validate'] = lambda: scope.input_artifacts['source-0000'].write_bytes(b'changed')
    with pytest.raises(StateError, match='input changed'):
        coordinator.execute(scope, **call)
    assert coordinator.engine.journal.state['active_native'] is not None


def test_expiry_no_origin_cross_attempt_and_process_fork(pending, controlled_dispatch, monkeypatch):
    coordinator, scope, args, _, call, _ = pending
    controlled_dispatch['patch'] = args.patch.read_bytes()
    coordinator.execute(scope, **call)
    with pytest.raises(StateError, match='cannot restore'):
        coordinator.origin(attempt='replay', request_sha256=call['request_sha256'])
    pid = os.getpid()
    monkeypatch.setattr(handoff.os, 'getpid', lambda: pid + 1)
    with pytest.raises(StateError, match='invoking process'):
        coordinator.origin(attempt='primary', request_sha256=call['request_sha256'])


def test_expired_request_never_dispatches(pending, controlled_dispatch, monkeypatch):
    coordinator, scope, _, _, call, _ = pending
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) + timedelta(hours=2)
    monkeypatch.setattr(handoff, 'datetime', Later)
    with pytest.raises(StateError, match='authorization expired'):
        coordinator.execute(scope, **call)
    assert controlled_dispatch['calls'] == 0


def test_controlled_journal_cannot_be_upgraded_by_mutating_engine_mode(pending, controlled_dispatch):
    coordinator, scope, _, _, call, _ = pending
    coordinator.engine.mode = 'operator'
    with pytest.raises(StateError, match='execution mode changed'):
        coordinator.execute(scope, **call)
    assert controlled_dispatch['calls'] == 0


def test_deadline_after_claim_preserves_attempt_and_blocks_retry(pending, monkeypatch):
    coordinator, scope, _, _, call, driver = pending
    calls = []
    def expires(*args, **kwargs):
        calls.append(True)
        raise handoff._PhaseExpired
    monkeypatch.setattr(native_provenance, 'execute_validated_correction', expires)
    with pytest.raises(StateError, match='deadline expired'):
        coordinator.execute(scope, **call)
    with pytest.raises(StateError, match='already claimed'):
        coordinator.execute(scope, **call)
    assert len(calls) == 1 and 'cleanup' not in driver.calls
    assert coordinator.engine.journal.state['environment_reserved']


def test_unresolved_mutation_or_abort_never_dispatches(pending, controlled_dispatch):
    coordinator, scope, _, _, call, _ = pending
    with coordinator.engine.journal.lifecycle_lock():
        coordinator.engine.journal.abort_lifecycle('primary-correction', 'interrupted', 'operator-interrupted')
    with pytest.raises(StateError, match='unresolved canonical'):
        coordinator.execute(scope, **call)
    assert controlled_dispatch['calls'] == 0


def test_lifecycle_lock_blocks_concurrent_dispatch(pending, controlled_dispatch):
    coordinator, scope, _, _, call, _ = pending
    with coordinator.engine.journal.lifecycle_lock():
        with pytest.raises(StateError, match='already held'):
            coordinator.execute(scope, **call)
    assert controlled_dispatch['calls'] == 0


def test_no_public_scope_import_callback_or_production_registration(prepared, tmp_path):
    scope, _, _ = prepared
    with pytest.raises(StateError):
        handoff.CorrectionScope()
    with pytest.raises(StateError):
        handoff._scope({'input_artifacts': scope.input_artifacts})
    inputs, identities = inputs_for(tmp_path)
    journal = RunJournal.create(tmp_path / 'unavailable', identities=identities,
        environment_id=inputs['config']['environment'])
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path / 'unavailable-locks')
    result = engine.advance()
    assert result['gaps'] == ['operation-driver-unavailable'] and not result['canonical_success']
    assert not journal.state['environment_reserved']


def test_component_is_in_the_existing_package_allowlist():
    from scripts.build_plugin import package_files
    files = package_files()
    assert 'src/devsecops/factory/native_handoff.py' in files
    assert 'docs/interfaces/native-handoff-coordinator.md' in files


def test_nested_repository_cannot_make_input_preparation_write_into_original_source(prepared):
    _, args, selected = prepared
    output = args.repo / 'review-output'
    before = {p.relative_to(args.repo): p.read_bytes() for p in args.repo.rglob('*') if p.is_file()}
    with pytest.raises(StateError, match='ordinary in-worktree Git metadata'):
        handoff.prepare_correction_scope(**{**selected, 'repository': args.repo / 'deploy', 'output': output})
    assert not output.exists()
    assert before == {p.relative_to(args.repo): p.read_bytes() for p in args.repo.rglob('*') if p.is_file()}


def test_external_git_metadata_cannot_be_selected_as_output(prepared):
    _, args, selected = prepared
    external = args.repo.parent / 'external-git'
    (args.repo / '.git').rename(external)
    (args.repo / '.git').write_text('gitdir: ' + str(external) + '\n')
    output = external / 'review-output'
    before = {p.relative_to(external): p.read_bytes() for p in external.rglob('*') if p.is_file()}
    with pytest.raises(StateError, match='ordinary in-worktree Git metadata'):
        handoff.prepare_correction_scope(**{**selected, 'output': output})
    assert not output.exists()
    assert before == {p.relative_to(external): p.read_bytes() for p in external.rglob('*') if p.is_file()}


@pytest.mark.parametrize('storage', ['commondir', 'alternates', 'object-symlink'])
def test_external_git_storage_is_rejected_before_output(prepared, storage, tmp_path):
    _, args, selected = prepared
    metadata = args.repo / '.git'
    if storage == 'commondir':
        (metadata / 'commondir').write_text(str(tmp_path / 'foreign'))
    elif storage == 'alternates':
        (metadata / 'objects/info/alternates').write_text(str(tmp_path / 'foreign'))
    else:
        (metadata / 'objects/ff').symlink_to(tmp_path / 'foreign', target_is_directory=True)
    output = tmp_path / 'rejected-scope'
    with pytest.raises(StateError):
        handoff.prepare_correction_scope(**{**selected, 'output': output})
    assert not output.exists()


@pytest.mark.parametrize('selection', ['environment', 'cached-root'])
def test_source_contained_temporary_storage_rejected_before_git_or_output(prepared, monkeypatch, selection, tmp_path):
    _, args, selected = prepared
    if selection == 'environment':
        monkeypatch.setenv('TMPDIR', str(args.repo))
    else:
        monkeypatch.setattr(handoff.tempfile, 'tempdir', str(args.repo))
    def forbidden(*args, **kwargs):
        raise AssertionError('Git must not run when scratch is in source')
    monkeypatch.setattr(handoff, 'git', forbidden)
    output = tmp_path / 'rejected-temp-scope'
    with pytest.raises(StateError, match='temporary storage must be outside'):
        handoff.prepare_correction_scope(**{**selected, 'output': output})
    assert not output.exists()


@pytest.mark.parametrize('selection', ['default-root', 'explicit-external-root'])
def test_fresh_process_prepares_from_repository_cwd_with_cold_temp_cache(prepared, tmp_path, selection):
    """Real public preparation must work before pytest has warmed tempfile's cache."""
    _, args, selected = prepared
    output = tmp_path / 'cold-process-scope'
    payload = {**selected, 'output': output}
    payload = {name: str(value) if isinstance(value, Path) else value for name, value in payload.items()}
    payload['tools'] = {name: [str(path), pin] for name, (path, pin) in selected['tools'].items()}
    env = {'PATH': os.environ['PATH'], 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': str(Path(handoff.__file__).resolve().parents[2])}
    external = tmp_path / 'explicit-temporary-root'
    if selection == 'explicit-external-root':
        external.mkdir(mode=0o700)
        env['TMPDIR'] = str(external)
    before = {p.relative_to(args.repo): p.read_bytes() for p in args.repo.rglob('*') if p.is_file()}
    program = r'''
import hashlib, json, os, sys, tempfile
from pathlib import Path
assert tempfile.tempdir is None
from devsecops.factory.native_handoff import prepare_correction_scope
assert tempfile.tempdir is None, 'Import must not hide the cold-start regression'
selected = json.load(sys.stdin)
for name in ('repository', 'profile', 'contract', 'schemas', 'output'):
    selected[name] = Path(selected[name])
selected['tools'] = {name: (Path(path), pin) for name, (path, pin) in selected['tools'].items()}
repository = selected['repository'].resolve()
assert Path.cwd() == repository
writable_opens = []
def observe(event, arguments):
    if event != 'open' or not isinstance(arguments[0], (str, bytes)):
        return
    flags = arguments[2]
    if isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
        path = Path(os.fsdecode(arguments[0])).resolve()
        assert not path.is_relative_to(repository), 'Transient write into selected source: ' + str(path)
        writable_opens.append(str(path))
sys.addaudithook(observe)
scope = prepare_correction_scope(**selected)
paths = scope.input_artifacts
print(json.dumps({'temporary_root': str(Path(tempfile.gettempdir()).resolve()),
    'selection': json.loads(paths['correction-selection'].read_bytes()),
    'source_sha256': hashlib.sha256(paths['source-0000'].read_bytes()).hexdigest(),
    'writable_opens': writable_opens}))
'''
    child = subprocess.run([sys.executable, '-B', '-c', program], cwd=args.repo, env=env,
        input=json.dumps(payload), capture_output=True, text=True, timeout=45, check=False)
    assert child.returncode == 0, child.stderr
    observed = json.loads(child.stdout)
    temporary_root = Path(observed['temporary_root'])
    if selection == 'explicit-external-root':
        assert temporary_root == external.resolve()
    else:
        assert temporary_root in {Path(path).resolve() for path in ('/tmp', '/var/tmp', '/usr/tmp')}
    assert not temporary_root.is_relative_to(args.repo)
    assert observed['selection']['base_commit'] == args.base
    assert observed['selection']['source_files'] == {'source-0000': 'deploy/policy.yaml'}
    assert observed['source_sha256'] == _sha((args.repo / 'deploy/policy.yaml').read_bytes())
    assert observed['writable_opens']
    assert before == {p.relative_to(args.repo): p.read_bytes() for p in args.repo.rglob('*') if p.is_file()}
    assert (output / 'source/deploy/policy.yaml').read_bytes() == (args.repo / 'deploy/policy.yaml').read_bytes()


def test_case_alias_output_cannot_write_into_source(prepared):
    _, args, selected = prepared
    alias = args.repo.with_name(args.repo.name.upper())
    if not alias.exists() or not alias.samefile(args.repo):
        pytest.skip('Case-insensitive alias regression requires a supporting filesystem')
    output = alias / 'review-output'
    with pytest.raises(StateError, match='external trusted correction inputs'):
        handoff.prepare_correction_scope(**{**selected, 'output': output})
    assert not output.exists()


def test_case_alias_temporary_storage_is_source_contained(prepared, monkeypatch, tmp_path):
    _, args, selected = prepared
    alias = args.repo.with_name(args.repo.name.upper())
    if not alias.exists() or not alias.samefile(args.repo):
        pytest.skip('Case-insensitive alias regression requires a supporting filesystem')
    monkeypatch.setenv('TMPDIR', str(alias))
    output = tmp_path / 'rejected-alias-temp'
    with pytest.raises(StateError, match='temporary storage must be outside'):
        handoff.prepare_correction_scope(**{**selected, 'output': output})
    assert not output.exists()
