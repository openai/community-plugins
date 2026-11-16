"""Native component composition, not a live factory run.

Native boundaries and operational phases are controlled. Fixed report replay,
patch validation, journals, retained-byte audits and the resident session are
real. No model, service credential, remote call or lab operation is performed.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import pickle
import threading
from time import monotonic

import pytest

from test_correction import correction as correction_case, require_tool
from test_factory_lifecycle import FixtureDriver, inputs_for
from test_native_handoff import controlled_dispatch
from test_native_reports import installed, recovery_scope, controlled_native, bind
from test_runtime_diagnosis import measured
from devsecops.engine import _schema_receipt
from devsecops.factory import native_handoff, native_reports
from devsecops.factory.lifecycle import LifecycleEngine
from devsecops.factory.native_factory_session import NativeFactorySession
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha

__all__ = ['correction_case', 'controlled_dispatch', 'installed', 'recovery_scope', 'controlled_native']


class Driver(FixtureDriver):
    def __init__(self, scopes):
        super().__init__()
        self.scopes = scopes

    def native_inputs(self, context):
        self.calls.append(context.phase['id'])
        return self.scopes[context.phase['workflow']].input_artifacts


@pytest.fixture
def correction_inputs(correction_case, tmp_path):
    # The recovery fixture also writes contract.json in this temporary root.
    args, _ = correction_case
    raw = args.contract.read_bytes()
    args.contract = tmp_path/'correction-contract.json'
    args.contract.write_bytes(raw)
    return correction_case


@pytest.fixture
def pending(correction_inputs, recovery_scope, installed, tmp_path):
    args, _ = correction_inputs
    require_tool(args)
    common = dict(repository=args.repo, base_commit=args.base, profile=args.profile,
        profile_sha256=_sha(args.profile.read_bytes()),
        tools={'kubeconform': (Path(args.kubeconform), _sha(Path(args.kubeconform).read_bytes()))},
        schemas=args.schema_location, schemas_sha256=_schema_receipt(args.schema_location)['sha256'])
    scopes = {
        'review-kubernetes-change': native_reports.prepare_review_scope(**common, head_commit=args.base,
            output=tmp_path/'review-scope', deadline=monotonic()+30),
        'prepare-gitops-correction': native_handoff.prepare_correction_scope(**common,
            contract=args.contract, contract_sha256=_sha(args.contract.read_bytes()),
            output=tmp_path/'correction-scope'),
        'verify-gitops-recovery': recovery_scope,
    }
    paths = {}
    for name, data in (('baseline', measured()[0]), ('fault', measured('fault', 60, up=0)[0])):
        paths[name] = tmp_path/('diagnosis-'+name+'.json')
        paths[name].write_bytes(_canonical(data))
    scopes['diagnose-gitops-deployment'] = native_reports.prepare_diagnosis_scope(**paths,
        output=tmp_path/'diagnosis-scope', deadline=monotonic()+30)
    inputs, identities = inputs_for(tmp_path)
    inputs['package'] = installed.installed_root/'SHA256SUMS'
    inputs['config']['expires_at'] = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    identities.update(package_sha256=installed.package_sha256,
                      config_sha256=_sha(_canonical(inputs['config'])))
    journal = RunJournal.create(tmp_path/'journal', identities=identities,
                                environment_id=inputs['config']['environment'])
    driver = Driver(scopes)
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path/'locks', _test_driver=driver)
    result = engine.advance()
    assert result['exit_code'] == 3 and journal.state['active_native']['step_id'] == 'primary-review'
    return NativeFactorySession(engine, selection=installed), engine, driver, scopes, args


def execute_report(pending, controlled_native, installed):
    session, engine, _, scopes, _ = pending
    active = engine.journal.state['active_native']
    scope = scopes[active['workflow']]
    bind(controlled_native, scope, installed)
    return session.execute_pending(scope)


def test_all_eight_steps_keep_distinct_direct_origins(pending, controlled_native, controlled_dispatch, installed):
    session, engine, driver, scopes, args = pending
    controlled_dispatch['patch'] = args.patch.read_bytes()
    originals = {}
    for attempt in ('primary', 'replay'):
        for suffix in ('review', 'diagnose', 'correction', 'recovery'):
            active = engine.journal.state['active_native']
            step = attempt+'-'+suffix
            assert active['step_id'] == step
            before = list(driver.calls)
            if suffix == 'correction':
                value = session.execute_pending(scopes[active['workflow']])
                assert session.origin(attempt=attempt) is value
                assert value.document['execution_mode'] == 'controlled-test'
            else:
                value = execute_report(pending, controlled_native, installed)
                assert session.report(step_id=step) is value
                assert value.document['execution_mode'] == 'controlled-test'
                assert value.document['runtime_authority'] == 'not-issued'
                assert value.document['scenario_assertion_status'] == 'not_evaluated'
                if suffix != 'review':
                    assert value.document['domain_decision'] == 'unknown'
            originals[step] = value
            assert driver.calls == before  # Acceptance never advances a mutation phase.
            result = session.advance()
    assert len({id(value) for value in originals.values()}) == 8
    assert controlled_native['calls'] == 6 and controlled_dispatch['calls'] == 2
    assert engine.mode == 'controlled-test' and result['canonical_success'] is False
    records = engine.journal.state['lifecycle']['phase_records']
    for step, value in originals.items():
        assert sum(row['phase_id'] == step for row in records) == 1
        if not step.endswith('correction'):
            assert session.report(step_id=step) is value


def test_saved_reports_cannot_restore_a_second_session(pending, controlled_native, installed):
    session, engine, driver, _, _ = pending
    execute_report(pending, controlled_native, installed)
    other = NativeFactorySession(engine, selection=installed)
    before = list(driver.calls)
    for operation in (lambda: other.report(step_id='primary-review'), other.advance):
        with pytest.raises(StateError, match='cannot restore'):
            operation()
    assert driver.calls == before and controlled_native['calls'] == 1
    with pytest.raises(StateError, match='serialized'):
        pickle.dumps(session)


@pytest.mark.parametrize('target', ['request', 'result', 'validation', 'report', 'replay'])
def test_changed_accepted_report_evidence_blocks_dependent_advance(pending, controlled_native, installed, target):
    session, engine, driver, _, _ = pending
    value = execute_report(pending, controlled_native, installed)
    state = engine.journal.state
    accepted = state['native_results'][-1]
    reference = (state['native_requests'][-1]['request'] if target == 'request' else
                 accepted['artifacts'][0] if target == 'report' else accepted.get(target))
    path = (engine.journal.run_dir/reference['path'] if reference else
            engine.journal.run_dir/('native-report-'+value.document['request_sha256'])/'independent/replay-invocation.json')
    path.write_bytes(b'changed accepted evidence')
    before = list(driver.calls)
    with pytest.raises(StateError):
        session.advance()
    assert driver.calls == before


def test_foreign_acceptance_at_lock_entry_is_rejected(pending, controlled_native, installed, monkeypatch):
    session, engine, driver, scopes, _ = pending
    outsider = native_reports.NativeReportCoordinator(engine, installed)
    scope = scopes['review-kubernetes-change']
    bind(controlled_native, scope, installed)
    original = engine.journal.lifecycle_lock
    entered = []
    @contextmanager
    def accept_before_lock():
        if not entered:
            entered.append(True)
            outsider.execute(scope)
        with original():
            yield engine.journal
    monkeypatch.setattr(engine.journal, 'lifecycle_lock', accept_before_lock)
    before = list(driver.calls)
    with pytest.raises(StateError, match='cannot restore'):
        session.advance()
    assert driver.calls == before and engine.journal.state['active_native'] is None


def test_incomplete_native_result_is_retained_without_advance_or_retry(pending, controlled_native, installed):
    session, engine, driver, scopes, _ = pending
    controlled_native['result_change'] = lambda result: result.update(status='incomplete', gaps=['controlled-native-gap'])
    active = engine.journal.state['active_native']
    with pytest.raises(StateError, match='invocation identity'):
        execute_report(pending, controlled_native, installed)
    execution = engine.journal.run_dir/('native-report-'+active['request']['sha256'])/'execution/native-result.json'
    retained = json.loads(execution.read_bytes())
    assert retained['status'] == 'incomplete' and retained['gaps'] == ['controlled-native-gap']
    assert engine.journal.state['active_native'] is not None
    with pytest.raises(StateError, match='stopped'):
        session.execute_pending(scopes['review-kubernetes-change'])
    assert controlled_native['calls'] == 1 and 'primary-deliver' not in driver.calls
    session.interrupt()
    assert 'cleanup' in driver.calls and 'primary-deliver' not in driver.calls


def test_report_cancellation_is_shared_and_stops_continuation(pending, monkeypatch):
    session, _, driver, scopes, _ = pending
    calls = []
    def stopped(scope, *, cancel):
        calls.append(scope)
        worker = threading.Thread(target=session.cancel)
        worker.start()
        worker.join(timeout=1)
        assert not worker.is_alive() and cancel.is_set()
        raise StateError('controlled report cancellation')
    monkeypatch.setattr(session._report_coordinator, 'execute', stopped)
    with pytest.raises(StateError, match='controlled report cancellation'):
        session.execute_pending(scopes['review-kubernetes-change'])
    with pytest.raises(StateError, match='stopped'):
        session.advance()
    assert len(calls) == 1 and 'cleanup' not in driver.calls
    session.interrupt()
    assert 'cleanup' in driver.calls


def test_wrong_thread_and_replaced_coordinator_are_refused(pending, monkeypatch):
    session, engine, driver, scopes, _ = pending
    errors = []
    def foreign_thread():
        try:
            session.execute_pending(scopes['review-kubernetes-change'])
        except StateError as error:
            errors.append(str(error))
    worker = threading.Thread(target=foreign_thread)
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive() and len(errors) == 1 and 'invoking process' in errors[0]
    monkeypatch.setattr(session._report_coordinator, 'engine', object())
    before = list(driver.calls)
    with pytest.raises(StateError, match='coordinator identity'):
        session.advance()
    assert driver.calls == before and engine.journal.state['active_native']['step_id'] == 'primary-review'


def test_process_identity_is_checked_before_native_dispatch(pending, monkeypatch):
    session, _, driver, scopes, _ = pending
    current = os.getpid()
    monkeypatch.setattr(os, 'getpid', lambda: current+1)
    before = list(driver.calls)
    for action in (session.advance, session.cancel,
                   lambda: session.execute_pending(scopes['review-kubernetes-change'])):
        with pytest.raises(StateError, match='invoking process'):
            action()
    assert driver.calls == before


@pytest.mark.parametrize('binding_fails', [False, True])
def test_owned_scope_handoff_follows_acceptance_and_precedes_advance(
        pending, controlled_native, controlled_dispatch, installed, monkeypatch, binding_fails):
    from devsecops import __version__
    from devsecops.factory.owned_linux import OwnedLinuxDriver
    _, engine, fixture_driver, scopes, args = pending
    # Exact production type, with operational handlers modeled explicitly. The
    # combined driver tests exercise its actual binding method separately.
    owned = OwnedLinuxDriver(engine.journal, plugin_id='devsecops@devsecops-local',
                             plugin_version=__version__)
    monkeypatch.setattr(owned, 'execute', fixture_driver.execute)
    monkeypatch.setattr(owned, 'native_inputs', fixture_driver.native_inputs)
    monkeypatch.setattr(owned, 'validate_cleanup', fixture_driver.validate_cleanup)
    engine.driver = owned
    session = NativeFactorySession(engine, selection=installed)
    updated = (session, engine, fixture_driver, scopes, args)
    for _ in range(2):
        execute_report(updated, controlled_native, installed)
        session.advance()
    active = engine.journal.state['active_native']
    assert active['step_id'] == 'primary-correction'
    controlled_dispatch['patch'] = args.patch.read_bytes()
    calls = []
    def binding(scope, *, request_sha256, origin):
        assert engine.journal.state['active_native'] is None
        assert engine.journal.state['lifecycle']['active_phase']['phase_id'] == 'primary-correction'
        assert scope is scopes['prepare-gitops-correction']
        assert origin is session.origin(attempt='primary')
        assert request_sha256 == active['request']['sha256']
        assert 'primary-validate-correction' not in fixture_driver.calls
        calls.append(origin)
        if binding_fails:
            raise StateError('controlled owned binding failure')
    monkeypatch.setattr(owned, 'bind_correction_scope', binding, raising=False)
    if binding_fails:
        with pytest.raises(StateError, match='controlled owned binding failure'):
            session.execute_pending(scopes['prepare-gitops-correction'])
        with pytest.raises(StateError, match='stopped'):
            session.advance()
        assert 'primary-validate-correction' not in fixture_driver.calls
        session.interrupt()
        assert 'cleanup' in fixture_driver.calls and 'primary-deliver' not in fixture_driver.calls
    else:
        origin = session.execute_pending(scopes['prepare-gitops-correction'])
        assert calls == [origin]
        assert 'primary-validate-correction' not in fixture_driver.calls
        session.advance()
        assert 'primary-validate-correction' in fixture_driver.calls
    assert len(calls) == 1 and controlled_dispatch['calls'] == 1
