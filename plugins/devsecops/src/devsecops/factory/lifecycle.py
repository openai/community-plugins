"""Canonical factory progression; operation adapters supply independent evidence.

Only the installed 33-phase graph is accepted. The fixed owned Linux backend
requires original component observations for production outcomes. An injected
Python driver remains controlled test execution and cannot establish canonical success. Config never supplies
commands, import paths, validators, or executable callbacks.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import signal
import threading
from time import monotonic
from types import MappingProxyType
from typing import Mapping, Protocol
from uuid import uuid4

from .contracts import validate_document
from .state import (IDENTITY_KEYS, MAX_ARTIFACT_BYTES, MAX_ARTIFACT_TOTAL_BYTES, MAX_DOCUMENT_BYTES, MAX_RESULT_ARTIFACTS, RunJournal,
                    StateError, PARENT_DISPOSAL_MODE, _canonical, _exclusive, _identifier, _json, _private_directory, _read, _sha)


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_ROOT = PACKAGE_ROOT / 'demo/software-factory/scenarios/ironbank-mission-recovery-v1'
MUTATIONS = {'prepare': ('prepare', 'provision'), 'up': ('up', 'provision'),
             'build-promote': ('build-promote', 'build'), 'inject': ('inject', 'inject'),
             'deliver': ('deliver', 'deliver'), 'probe': ('probe', 'probe'),
             'reset': ('reset', 'reset'), 'down': ('down', 'cleanup')}
OUTCOMES = ('completed', 'incomplete', 'error', 'assertion-failure')


class _PhaseExpired(BaseException):
    """A driver must not catch this cancellation boundary."""


@contextmanager
def _deadline(deadline):
    # No detached worker can keep mutating after the caller begins cleanup.
    # Trusted drivers must also cancel/reap subprocesses and bound remote I/O;
    # interrupting the caller cannot prove a remote write did not happen.
    if threading.current_thread() is not threading.main_thread() or signal.getitimer(signal.ITIMER_REAL)[0]:
        raise StateError('A bounded operation requires an available main-thread deadline')
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise _PhaseExpired
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise _PhaseExpired
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@dataclass(frozen=True)
class AssertionResult:
    case_id: str
    assertion_id: str
    status: str


@dataclass(frozen=True)
class PhaseOutcome:
    status: str
    code: str
    artifacts: tuple[Path, ...] = ()
    assertions: tuple[AssertionResult, ...] = ()
    mutation_effect: str = 'unknown'


@dataclass(frozen=True)
class PhaseContext:
    run_id: str
    phase: Mapping
    identities: Mapping
    inputs: Mapping
    input_artifacts: Mapping
    run_dir: Path
    deadline: float
    execution_mode: str

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - monotonic())


class OperationDriver(Protocol):
    """Closed phase dispatch; each concrete handler must bound and cancel its I/O.

    A timeout/exception after mutation intent is an unknown external effect.
    Return applied/not_applied only with actual observations, never an exit code
    or JSON success field alone. Validators inspect the frozen supplied bytes.
    """
    def execute(self, context: PhaseContext) -> PhaseOutcome: ...
    def native_inputs(self, context: PhaseContext) -> Mapping[str, Path]: ...
    def validate_native(self, request: dict, result: dict, artifacts: Mapping[str, bytes]) -> list[str]: ...
    def validate_cleanup(self, receipts: tuple[bytes, ...]) -> list[str]: ...


class _Unavailable:
    def execute(self, context):
        return PhaseOutcome('incomplete', 'operation-driver-unavailable', mutation_effect='not_applied')


def _readonly(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _readonly(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(map(_readonly, value))
    return value


def _load_inputs(inputs, identities, environment_id):
    names = {key.removesuffix('_sha256') for key in IDENTITY_KEYS}
    if not isinstance(inputs, dict) or set(inputs) != names or not isinstance(inputs['package'], (str, Path)):
        raise StateError('Six explicit lifecycle inputs including the package content manifest are required')
    raw, docs = {}, {}
    for name, value in inputs.items():
        try:
            raw[name] = _canonical(deepcopy(value)) if isinstance(value, dict) else _read(
                Path(value), MAX_ARTIFACT_BYTES if name == 'package' else MAX_DOCUMENT_BYTES)
        except (TypeError, ValueError, RecursionError) as error:
            raise StateError('Invalid bounded lifecycle input') from error
        if _sha(raw[name]) != identities[name + '_sha256']:
            raise StateError('Lifecycle input identity changed')
        if name != 'package':
            docs[name] = _json(raw[name])
    for name, kind in (('scenario', 'factory-scenario'), ('assertions', 'scenario-assertions'),
                       ('config', 'factory-config'), ('lock', 'factory-lock')):
        validate_document(docs[name], kind)
    for name in ('scenario', 'assertions'):
        if docs[name] != _json(_read(SCENARIO_ROOT / (name + '.json'), MAX_DOCUMENT_BYTES)):
            raise StateError('Only the exact installed canonical scenario and assertions are supported')
    if (docs['lock']['scenario_sha256'] != identities['scenario_sha256']
            or docs['lock']['assertions_sha256'] != identities['assertions_sha256']
            or docs['lock']['profile'] != docs['config']['profile']
            or docs['config']['environment'] != environment_id
            or not isinstance(docs['environment'], dict)
            or docs['environment'].get('environment_id') != environment_id):
        raise StateError('Lifecycle inputs disagree on immutable scenario or environment scope')
    return raw, docs


class LifecycleEngine:
    """One canonical attempt and one declared replay, with explicit native yield.

    ``inputs`` are supplied again by the operator on every resume, never obtained
    from embedded paths. ``package`` means exact installed SHA256SUMS bytes;
    checking package membership/content is a separate preflight obligation.
    No driver chosen by user-controlled JSON can execute code.
    """

    def __init__(self, journal: RunJournal, *, inputs: dict, shared_lock_dir: Path,
                 driver_id: str = 'unavailable', plugin_id: str | None = None,
                 plugin_version: str | None = None, auxiliary_inputs: dict | None = None,
                 _test_driver: OperationDriver | None = None):
        if driver_id not in ('unavailable', 'owned-linux'):
            raise StateError('No production operation driver is registered for that ID')
        if auxiliary_inputs and (driver_id != 'owned-linux' or _test_driver is not None):
            raise StateError('Auxiliary selection requires the fixed owned Linux backend')
        if driver_id == 'owned-linux':
            if _test_driver is not None:
                raise StateError('A registered backend cannot accept an injected driver')
            from .owned_linux import OwnedLinuxDriver
            driver = OwnedLinuxDriver(journal, plugin_id=plugin_id, plugin_version=plugin_version,
                                      auxiliary_inputs=auxiliary_inputs)
        else:
            if plugin_id is not None or plugin_version is not None:
                raise StateError('Installed plugin selection requires the owned-linux backend')
            driver = _test_driver if _test_driver is not None else _Unavailable()
        self.journal = journal
        self.input_sources = deepcopy(inputs)
        self.shared_lock_dir = Path(shared_lock_dir)
        self.mode = 'controlled-test' if _test_driver is not None else 'operator'
        self.driver_id = 'controlled-test' if _test_driver is not None else driver_id
        self.driver = driver
        self.auxiliary_sources = deepcopy(auxiliary_inputs)
        self.plugin_id, self.plugin_version = plugin_id, plugin_version
        self.raw, self.docs = _load_inputs(self.input_sources, journal.identities, journal.state['environment_id'])
        self.auxiliary_raw = self._load_auxiliary()
        journal.initialize_lifecycle(inputs=self.raw, execution_mode=self.mode, driver_id=self.driver_id,
                                     auxiliary_inputs=self.auxiliary_raw)

    def _load_auxiliary(self):
        from .auxiliary_inputs import load_selection, validate_temporal_scope
        raw = load_selection(self.auxiliary_sources, identities=self.journal.identities,
                             environment_id=self.journal.state['environment_id'],
                             plugin_id=self.plugin_id, plugin_version=self.plugin_version)
        validate_temporal_scope(raw, self.docs['config'])
        return raw

    def _check_inputs(self):
        raw, docs = _load_inputs(self.input_sources, self.journal.identities, self.journal.state['environment_id'])
        if raw != self.raw or docs != self.docs:
            raise StateError('Lifecycle input changed during execution')
        if self._load_auxiliary() != self.auxiliary_raw:
            raise StateError('Auxiliary lifecycle input changed during execution')

    def _context(self, phase):
        budget = phase['timeout_seconds']
        if self.driver_id == 'owned-linux' and phase['operation'] == 'preflight':
            # Keep the outer interrupt within this fixed handler's smaller cap,
            # including a local filesystem read that cannot reach a checkpoint.
            from .owned_linux import MAX_TIMEOUT
            budget = min(budget, MAX_TIMEOUT)
        if phase['operation'] not in ('export', 'down', 'finalize'):
            budget = min(budget, self._authorization_remaining())
        state = self.journal.state
        return PhaseContext(state['run_id'], _readonly(phase), _readonly(self.journal.identities),
                            _readonly(self.docs), _readonly(state['lifecycle']['inputs']), self.journal.run_dir,
                            monotonic() + max(0, budget), self.mode)

    def _authorization_remaining(self):
        # Only controlled fixtures use the injected journal clock. Operator
        # authorization is always evaluated against actual UTC at the call.
        now = (datetime.fromisoformat(self.journal._now()) if self.mode == 'controlled-test'
               else datetime.now(timezone.utc))
        return (datetime.fromisoformat(self.docs['config']['expires_at'].replace('Z', '+00:00')) - now).total_seconds()

    def _receipt(self, phase, code, outcome):
        path = self.journal.run_dir / ('lifecycle-observation-' + uuid4().hex + '.json')
        _exclusive(path, _canonical({'schema_version': 1, 'run_id': self.journal.state['run_id'],
                   'phase_id': phase['id'], 'code': code, 'outcome': outcome,
                   'execution_mode': self.mode, 'coverage': 'runner-observation-only'}))
        return path

    def _snapshot(self, status='incomplete', *, handoff=None):
        state = self.journal.state
        lifecycle = state['lifecycle']
        if lifecycle['final_result'] is not None:
            return self._publish_result()
        result = {'execution_status': status, 'scenario_assertion_status': 'unknown',
                  'terminal': False, 'exit_code': 3 if status == 'awaiting_native_step' else 2,
                  'canonical_success': False, 'execution_mode': self.mode,
                  'run_id': state['run_id'], 'identities': deepcopy(state['identities']),
                  'original_failure': deepcopy(lifecycle['abort']),
                  'active_phase': deepcopy(lifecycle['active_phase']),
                  'cleanup_required': state['environment_reserved'],
                  'completed_phase_count': len(lifecycle['phase_records'])}
        if handoff is not None:
            result['native_request'] = handoff
        return result

    def _outcome(self, outcome, phase):
        if (not isinstance(outcome, PhaseOutcome) or outcome.status not in OUTCOMES
                or outcome.mutation_effect not in ('applied', 'not_applied', 'unknown')
                or not isinstance(outcome.artifacts, tuple) or len(outcome.artifacts) >= MAX_RESULT_ARTIFACTS
                or not isinstance(outcome.assertions, tuple) or len(outcome.assertions) > 100):
            raise StateError('Invalid bounded operation outcome')
        _identifier(outcome.code)
        assertions, seen = [], set()
        for item in outcome.assertions:
            if (not isinstance(item, AssertionResult) or item.case_id not in phase['case_ids']
                    or item.assertion_id not in self.docs['assertions']['cases'][item.case_id]
                    or item.status not in ('pass', 'fail', 'unknown')
                    or (item.case_id, item.assertion_id) in seen):
                raise StateError('Operation assertion is outside the declared phase contract')
            seen.add((item.case_id, item.assertion_id))
            assertions.append({'case_id': item.case_id, 'assertion_id': item.assertion_id, 'status': item.status})
        status = outcome.status
        if any(a['status'] == 'fail' for a in assertions):
            status = 'assertion-failure'
        elif any(a['status'] == 'unknown' for a in assertions) and status == 'completed':
            status = 'incomplete'
        if status == 'completed' and not outcome.artifacts:
            raise StateError('Completed operations require independent evidence artifacts')
        return status, assertions

    def _abort(self, phase, status, code):
        self.journal.abort_lifecycle(phase['id'], status, code)

    def _record(self, phase, status, code, artifacts=(), assertions=()):
        receipt = self._receipt(phase, code, status)
        self.journal.finish_phase(phase['id'], status, code,
                                  receipt_artifacts=(*artifacts, receipt), assertions=assertions)
        if status != 'completed':
            self._abort(phase, status, code)

    def _phase(self, phase):
        """Execute one bounded adapter call; ambiguous writes remain reserved."""
        context = self._context(phase)
        operation = None
        mutation = MUTATIONS.get(phase['operation'])
        owned_probe_observation = (self.driver_id == 'owned-linux'
            and 'owned_runtime_recipe' in self.auxiliary_raw and phase['operation'] == 'probe')
        if owned_probe_observation:
            # The exact runtime producer created these scheduled actors under
            # the preceding delivery intent. This phase reads their real logs
            # and measurements; it neither recreates nor relabels their effect.
            mutation = None
            if 'probe' not in self.docs['config']['allowed_mutations']:
                self._record(phase, 'incomplete', 'runtime-controls-not-authorized')
                return False
        if phase['id'] == 'platform-dependency':
            mutation = ('platform-dependency', 'inject')
        if phase['operation'] not in ('export', 'down', 'finalize') and self._authorization_remaining() <= 0:
            self._record(phase, 'incomplete', 'authorization-expired')
            return False
        if phase['operation'] == 'preflight' and 'cleanup' not in self.docs['config']['allowed_mutations']:
            self._record(phase, 'incomplete', 'cleanup-not-authorized')
            return False
        # The unavailable driver is identified before any external mutation intent.
        if isinstance(self.driver, _Unavailable):
            self._record(phase, 'incomplete', 'operation-driver-unavailable')
            return False
        if mutation and mutation[1] not in self.docs['config']['allowed_mutations']:
            self._record(phase, 'incomplete', 'mutation-not-authorized')
            return False
        if phase['operation'] == 'build-promote' and 'promote' not in self.docs['config']['allowed_mutations']:
            self._record(phase, 'incomplete', 'mutation-not-authorized')
            return False
        if (self.driver_id == 'owned-linux'
                and 'owned_operations_recipe' in self.auxiliary_raw
                and phase['operation'] in ('build-promote', 'reset')
                and not ({'provision', 'probe', 'deliver'} if phase['operation'] == 'build-promote'
                         else {'provision', 'probe'}) <= set(self.docs['config']['allowed_mutations'])):
            self._record(phase, 'incomplete', 'runtime-controls-not-authorized')
            return False
        try:
            if phase['operation'] == 'native':
                if self.driver_id == 'owned-linux':
                    # The fixed coordinator owns the single deadline, including
                    # correction preparation which already uses this guard.
                    inputs = self.driver.native_inputs(context)
                else:
                    with _deadline(context.deadline):
                        inputs = self.driver.native_inputs(context)
                if context.remaining_seconds() <= 0:
                    raise TimeoutError
                return self.journal.request_native(phase['id'], phase['workflow'],
                    input_artifacts=inputs, ttl_seconds=max(1, int(min(3600, context.remaining_seconds()))),
                    expires_at=self.docs['config']['expires_at'])
            # Refuse before journaling or invoking a mutation when a bounded
            # synchronous execution context cannot be established.
            with _deadline(context.deadline):
                pass
            if mutation:
                selected = ()
                if self.driver_id == 'owned-linux':
                    with _deadline(context.deadline):
                        selected = self.driver.mutation_intent_artifacts(context)
                intent = self._receipt(phase, 'mutation-intent', 'pending')
                operation = self.journal.begin_mutation(phase['id'], mutation[0],
                                                       intent_artifacts=[intent, *selected])
            with _deadline(context.deadline):
                outcome = self.driver.execute(context)
            status, assertions = self._outcome(outcome, phase)
            if owned_probe_observation and outcome.mutation_effect != 'not_applied':
                raise StateError('Owned runtime probe observation cannot claim a new mutation')
            if context.remaining_seconds() <= 0:
                raise TimeoutError
            if operation:
                if not outcome.artifacts or outcome.mutation_effect == 'unknown':
                    raise StateError('Operation effect requires independent reconciliation')
                if phase['operation'] == 'down' and status != 'completed' and outcome.mutation_effect == 'applied':
                    # Component deletion can be applied while complete factory
                    # cleanup remains unproved. Retain its evidence and the
                    # original operation for observation, never redispatch it.
                    self._abort(phase, status, outcome.code)
                    receipt = self._receipt(phase, 'cleanup-incomplete', status)
                    self.journal.reconcile_mutation(operation, 'unknown',
                        receipt_artifacts=(*outcome.artifacts, receipt))
                    return False
                if phase['operation'] == 'down' and status == 'completed' and outcome.mutation_effect == 'applied':
                    with _deadline(context.deadline):
                        self.journal.complete_cleanup(operation, receipt_artifacts=outcome.artifacts,
                                                      validator=self.driver.validate_cleanup)
                elif outcome.mutation_effect == 'applied':
                    self.journal.finish_mutation(operation, receipt_artifacts=outcome.artifacts)
                else:
                    self.journal.reconcile_mutation(operation, outcome.mutation_effect,
                                                     receipt_artifacts=outcome.artifacts)
                operation = None
                if status == 'completed' and outcome.mutation_effect != 'applied':
                    status = 'incomplete'
            self._record(phase, status, outcome.code, outcome.artifacts, assertions)
            return status == 'completed'
        except (Exception, KeyboardInterrupt, SystemExit, _PhaseExpired) as error:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
            status = 'interrupted' if interrupted else 'error'
            code = 'phase-interrupted' if interrupted else 'phase-timeout' if isinstance(error, (TimeoutError, _PhaseExpired)) else 'phase-error'
            self._abort(phase, status, code)
            if operation is not None:
                state = self.journal.state
                if (phase['operation'] == 'down' and state['execution_status'] == 'released'
                        and state['active_mutation'] is None
                        and any(o['operation_id'] == operation and o['status'] == 'applied'
                                for o in state['operations'])):
                    # The atomic completion was durable before its projection or
                    # owner-marker publication failed. Preserve the failure, but
                    # do not invent a new unknown operation or repeat deletion.
                    self.journal.finish_cleanup_release(operation)
                    self._record(phase, 'completed', 'cleanup-observation-recovered',
                                 [self.journal.run_dir / state['cleanup_validation']['path']])
                    return True
                receipt = self._receipt(phase, code, status)
                self.journal.reconcile_mutation(operation, 'unknown', receipt_artifacts=[receipt])
                return False
            self._record(phase, status, code)
            return False

    def _native_completion_artifacts(self, step_id, status, receipt):
        artifacts = [receipt]
        if self.driver_id == 'owned-linux' and status == 'completed':
            artifacts.extend(self.driver.completed_native_artifacts(step_id))
        return tuple(dict.fromkeys(artifacts))

    def _native_resume(self, path):
        state = self.journal.state
        active = state['active_native']
        if active is None:
            raise StateError('No native result is pending')
        phase = next(p for p in self.docs['scenario']['phases'] if p['id'] == active['step_id'])
        try:
            with _deadline(self._context(phase).deadline):
                result = self.journal.accept_native(Path(path), identities=self.journal.identities,
                                                    validator=self.driver.validate_native)
        except StateError:
            raise
        except (Exception, _PhaseExpired) as error:
            raise StateError('Native artifact validation failed or exceeded its deadline') from error
        accepted = self.journal.state['native_results'][-1]
        status = 'completed' if accepted['status'] == 'completed' else 'incomplete'
        receipt = self.journal.run_dir / result['validation']['path']
        self._record(phase, status, 'native-result-validated',
                     self._native_completion_artifacts(phase['id'], status, receipt))

    def _publish_result(self):
        from .terminal_evidence import publish_terminal
        identity = None
        if self.driver_id == 'owned-linux':
            identity = self.driver.terminal_native_identity()
        return publish_terminal(self.journal, native_identity=identity)['result']

    def _finish_result(self):
        self.journal.finalize_lifecycle()

    def advance(self, *, native_result: Path | None = None):
        """Advance until native yield, reconciliation, terminal tail, or completion."""
        self._check_inputs()
        with self.journal.lifecycle_lock():
            return self._advance_locked(native_result=native_result)

    def _advance_locked(self, *, native_result=None):
        """Continue under the caller's existing lifecycle guard."""
        if not self.journal._lifecycle_locked:
            raise StateError('Lifecycle continuation requires its execution lock')
        state = self.journal.state
        if isinstance(self.driver, _Unavailable):
            return {**self._snapshot(), 'gaps': ['operation-driver-unavailable']}
        if state['lifecycle']['final_result'] is not None:
            if native_result is not None:
                raise StateError('Finalized lifecycle cannot consume another native result')
            return self._snapshot()
        # Preflight is a closed read-only operation. Its failure never
        # reserves infrastructure and never fabricates a cleanup receipt.
        preflight = self.docs['scenario']['phases'][0]
        if not state['environment_reserved'] and not state['operations']:
            lifecycle = state['lifecycle']
            if not lifecycle['phase_records'] and lifecycle['active_phase'] is None:
                self.journal.begin_phase(preflight['id'])
                if not self._phase(preflight):
                    return self._snapshot()
            elif lifecycle['active_phase'] is not None and lifecycle['active_phase']['phase_id'] == preflight['id']:
                self._record(preflight, 'interrupted', 'unfinished-phase-not-retried')
                return self._snapshot()
            state = self.journal.state
            if any(record['phase_id'] == preflight['id'] and record['outcome'] != 'completed'
                   for record in state['lifecycle']['phase_records']):
                return self._snapshot()
        return self._advance_reserved(native_result=native_result)

    def _advance_reserved(self, *, native_result=None):
        with self.journal.mutation_lock(self.shared_lock_dir):
            state = self.journal.state
            if state['lifecycle']['final_result'] is not None:
                if native_result is not None:
                    raise StateError('Finalized lifecycle cannot consume another native result')
                return self._snapshot()
            if state['active_mutation'] is not None:
                return self._snapshot('reconciliation_required')
            last_down = next((o for o in reversed(state['operations']) if o['action'] == 'down'), None)
            if last_down is not None and last_down['status'] == 'partial':
                return self._snapshot('cleanup_continuation_required')
            if state['active_native'] is not None and self._authorization_remaining() <= 0:
                phase = next(p for p in self.docs['scenario']['phases'] if p['id'] == state['active_native']['step_id'])
                self.journal.cancel_native('authorization-expired')
                self._record(phase, 'incomplete', 'authorization-expired')
                native_result = None
                state = self.journal.state
            if native_result is not None:
                self._native_resume(native_result)
            elif state['active_native'] is not None:
                active = state['active_native']
                request_path = self.journal.run_dir / active['request']['path']
                request = _json(_read(request_path, MAX_DOCUMENT_BYTES))
                return self._snapshot('awaiting_native_step', handoff={
                    'request_path': str(request_path), 'request_sha256': active['request']['sha256'],
                    'result_path': str(self.journal.run_dir / request['result_path'])})
            state = self.journal.state
            if state['lifecycle']['abort'] is None:
                failed = next((r for r in state['lifecycle']['phase_records'] if r['outcome'] != 'completed'), None)
                if failed:
                    phase = next(p for p in self.docs['scenario']['phases'] if p['id'] == failed['phase_id'])
                    self._abort(phase, failed['outcome'], failed['code'])
            active = state['lifecycle']['active_phase']
            if active is not None:
                phase = next(p for p in self.docs['scenario']['phases'] if p['id'] == active['phase_id'])
                if phase['operation'] == 'down' and state['execution_status'] == 'released':
                    self._record(phase, 'completed', 'cleanup-observation-recovered',
                                 [self.journal.run_dir / state['cleanup_validation']['path']])
                elif phase['operation'] == 'native' and any(r['step_id'] == phase['id'] for r in state['native_results']):
                    prior = next(r for r in state['native_results'] if r['step_id'] == phase['id'])
                    self._record(phase, 'completed' if prior['status'] == 'completed' else 'incomplete',
                                 'native-validation-recovered', self._native_completion_artifacts(
                                     phase['id'], prior['status'],
                                     self.journal.run_dir / prior['validation']['path']))
                else:
                    self._record(phase, 'interrupted', 'unfinished-phase-not-retried')
            for phase in self.docs['scenario']['phases']:
                state = self.journal.state
                lifecycle = state['lifecycle']
                if state['active_mutation'] is not None:
                    return self._snapshot('reconciliation_required')
                if any(r['phase_id'] == phase['id'] for r in lifecycle['phase_records']):
                    continue
                if (self.driver_id == 'owned-linux' and self.driver.native_cancelled
                        and lifecycle['abort'] is None and phase['operation'] not in ('export', 'down', 'finalize')):
                    self._abort(phase, 'interrupted', 'native-session-cancelled')
                    lifecycle = self.journal.state['lifecycle']
                if lifecycle['abort'] and phase['operation'] not in ('export', 'down', 'finalize'):
                    continue
                if phase['operation'] == 'finalize':
                    tail = [r for r in lifecycle['phase_records'] if r['phase_id'] in ('export', 'cleanup')]
                    if state['execution_status'] != 'released' or any(r['outcome'] != 'completed' for r in tail):
                        return self._snapshot('cleanup_required' if state['environment_reserved'] else 'incomplete')
                self.journal.begin_phase(phase['id'])
                outcome = self._phase(phase)
                if isinstance(outcome, dict):
                    return self._snapshot('awaiting_native_step', handoff=outcome)
            state = self.journal.state
            if state['active_mutation'] is not None:
                return self._snapshot('reconciliation_required')
            tail = [r for r in state['lifecycle']['phase_records'] if r['phase_id'] in ('export', 'cleanup', 'finalize')]
            if len(tail) == 3 and all(r['outcome'] == 'completed' for r in tail):
                self._finish_result()
            return self._snapshot()

    def reconcile_cleanup(self, *, operation_id: str):
        """Read original resident cleanup effects; never dispatch another deletion."""
        self._check_inputs()
        if self.driver_id != 'owned-linux' and self.mode != 'controlled-test':
            raise StateError('Cleanup reconciliation requires the fixed resident owned driver')
        with self.journal.lifecycle_lock(), self.journal.mutation_lock(self.shared_lock_dir):
            state = self.journal.state
            active = state['active_mutation']
            if active is None or active['operation_id'] != operation_id or active['action'] != 'down':
                raise StateError('Cleanup reconciliation requires the exact outstanding down operation')
            phase = next(p for p in self.docs['scenario']['phases'] if p['id'] == active['phase_id'])
            context = self._context(phase)
            with _deadline(context.deadline):
                outcome = self.driver.reconcile_cleanup(context, operation_id)
            self._outcome(outcome, phase)
            if not outcome.artifacts:
                raise StateError('Cleanup reconciliation requires original observed evidence')
            if outcome.status == 'completed' and outcome.mutation_effect == 'applied':
                try:
                    with _deadline(context.deadline):
                        self.journal.complete_cleanup(operation_id, receipt_artifacts=outcome.artifacts,
                            validator=self.driver.validate_cleanup, reconciliation=True)
                    self._record(phase, 'completed', 'cleanup-reconciled', outcome.artifacts)
                except (Exception, KeyboardInterrupt, SystemExit, _PhaseExpired):
                    state = self.journal.state
                    if (state['execution_status'] != 'released' or state['active_mutation'] is not None
                            or not any(o['operation_id'] == operation_id and o['action'] == 'down'
                                       and o['status'] == 'applied' for o in state['operations'])):
                        raise
                    self.journal.finish_cleanup_release(operation_id)
                    if state['lifecycle']['active_phase'] is not None:
                        self._record(phase, 'completed', 'cleanup-observation-recovered',
                            [self.journal._artifact_path(state['cleanup_validation']['path'])])
                return self._snapshot('cleanup_completed')
            # Only the fixed original validator may classify a partial effect as
            # terminal. Failure leaves the exact old mutation unknown/reserved.
            with _deadline(context.deadline):
                self.journal.reconcile_partial_cleanup(operation_id, receipt_artifacts=outcome.artifacts,
                    validator=self.driver.validate_partial_cleanup)
            return self._snapshot('cleanup_continuation_required')

    def continue_cleanup(self, *, prior_operation_id: str, cleanup_mode="normal"):
        """Explicit fresh authorization for the remaining original owned scopes.

        This resident API is intentionally unavailable as a saved-JSON resume.
        It retains the same phase and origin objects, but journals a new down ID.
        """
        self._check_inputs()
        if type(cleanup_mode) is not str or cleanup_mode not in ('normal', PARENT_DISPOSAL_MODE):
            raise StateError('Unsupported explicit cleanup continuation mode')
        if self.driver_id != 'owned-linux' and self.mode != 'controlled-test':
            raise StateError('Cleanup continuation requires the fixed resident owned driver')
        with self.journal.lifecycle_lock(), self.journal.mutation_lock(self.shared_lock_dir):
            state = self.journal.state
            last = next((o for o in reversed(state['operations']) if o['action'] == 'down'), None)
            if (state['active_mutation'] is not None or state['active_native'] is not None
                    or last is None or last['operation_id'] != prior_operation_id or last['status'] != 'partial'
                    or not state['environment_reserved'] or state['lifecycle']['final_result'] is not None
                    or (state['lifecycle']['active_phase'] or {}).get('phase_id') != last['phase_id']):
                raise StateError('Cleanup continuation requires the latest terminal partial cleanup phase')
            if cleanup_mode not in last.get('cleanup_continuation_modes', ['normal']):
                raise StateError('Cleanup mode is not admitted by the latest partial observation')
            if self._authorization_remaining() <= 0 or 'cleanup' not in self.docs['config']['allowed_mutations']:
                raise StateError('Cleanup continuation requires a fresh unexpired explicit cleanup grant')
            phase = next(p for p in self.docs['scenario']['phases'] if p['id'] == last['phase_id'])
            context = self._context(phase)
            context = PhaseContext(context.run_id, context.phase, context.identities, context.inputs,
                context.input_artifacts, context.run_dir,
                min(context.deadline, monotonic() + self._authorization_remaining()), context.execution_mode)
            with _deadline(context.deadline):
                selected = (self.driver.prepare_cleanup_continuation(context, prior_operation_id)
                    if cleanup_mode == 'normal' else self.driver.prepare_cleanup_continuation(
                        context, prior_operation_id, cleanup_mode=cleanup_mode))
            intent = self._receipt(phase, 'cleanup-continuation-intent', 'pending')
            operation = self.journal.begin_mutation(phase['id'], 'down',
                intent_artifacts=[intent, *selected], continues_operation=prior_operation_id,
                cleanup_mode=None if cleanup_mode == 'normal' else cleanup_mode)
            try:
                with _deadline(context.deadline):
                    outcome = (self.driver.continue_cleanup(context, prior_operation_id)
                        if cleanup_mode == 'normal' else self.driver.continue_cleanup(
                            context, prior_operation_id, cleanup_mode=cleanup_mode))
                status, assertions = self._outcome(outcome, phase)
                if (context.remaining_seconds() <= 0 or not outcome.artifacts
                        or status != 'completed' or outcome.mutation_effect != 'applied'):
                    receipts = outcome.artifacts or (self._receipt(phase, 'cleanup-continuation-unresolved', 'incomplete'),)
                    self.journal.reconcile_mutation(operation, 'unknown', receipt_artifacts=receipts)
                    return self._snapshot('reconciliation_required')
                with _deadline(context.deadline):
                    self.journal.complete_cleanup(operation, receipt_artifacts=outcome.artifacts,
                        validator=self.driver.validate_cleanup)
                self._record(phase, 'completed', 'cleanup-continuation-complete', outcome.artifacts, assertions)
                return self._snapshot('cleanup_completed')
            except (Exception, KeyboardInterrupt, SystemExit, _PhaseExpired):
                state = self.journal.state
                if state['execution_status'] == 'released' and state['active_mutation'] is None:
                    self.journal.finish_cleanup_release(operation)
                    if state['lifecycle']['active_phase'] is not None:
                        self._record(phase, 'completed', 'cleanup-observation-recovered',
                            [self.journal._artifact_path(state['cleanup_validation']['path'])])
                    return self._snapshot('cleanup_completed')
                if state['active_mutation'] is not None:
                    receipt = self._receipt(phase, 'cleanup-continuation-unresolved', 'error')
                    self.journal.reconcile_mutation(operation, 'unknown', receipt_artifacts=[receipt])
                raise

    def reconcile(self, *, operation_id: str, outcome: str, receipt_artifacts, validator):
        """Observe an unresolved effect; do not re-execute its operation."""
        self._check_inputs()
        if outcome not in ('applied', 'not_applied') or not callable(validator):
            raise StateError('Explicit reconciliation needs a resolved outcome and trusted validator')
        with self.journal.mutation_lock(self.shared_lock_dir):
            state = self.journal.state
            active = state['active_mutation']
            if active is None or active['operation_id'] != operation_id:
                raise StateError('Reconciliation must match the unresolved operation')
            if not receipt_artifacts or len(receipt_artifacts) >= MAX_RESULT_ARTIFACTS:
                raise StateError('Reconciliation requires bounded independent receipts')
            raw, frozen, total = [], [], 0
            directory = self.journal.run_dir / ('reconciliation-' + uuid4().hex)
            _private_directory(directory, new=True)
            for index, source in enumerate(receipt_artifacts):
                item = _read(Path(source), min(MAX_ARTIFACT_BYTES, MAX_ARTIFACT_TOTAL_BYTES - total))
                total += len(item)
                path = directory / str(index)
                _exclusive(path, item)
                raw.append(item)
                frozen.append(path)
            with _deadline(monotonic() + 30):
                checks = validator(outcome, tuple(raw))
            if not isinstance(checks, (list, tuple)) or not checks or len(checks) > 100:
                raise StateError('Reconciliation validator must return completed checks')
            for check in checks:
                _identifier(check)
            if raw != [_read(path, MAX_ARTIFACT_BYTES) for path in frozen]:
                raise StateError('Reconciliation evidence changed during validation')
            validation = directory / 'validation.json'
            _exclusive(validation, _canonical({'operation_id': operation_id, 'outcome': outcome,
                'checks': list(checks), 'coverage': 'reconciliation-artifact-validation',
                'execution_mode': self.mode, 'artifacts': [{'sha256': _sha(item), 'bytes': len(item)} for item in raw]}))
            phase = next(p for p in self.docs['scenario']['phases'] if p['id'] == active['phase_id'])
            self._abort(phase, 'interrupted', 'mutation-required-reconciliation')
            if phase['operation'] == 'down' and outcome == 'applied':
                with _deadline(monotonic() + 30):
                    self.journal.complete_cleanup(operation_id, receipt_artifacts=frozen,
                        validator=self.driver.validate_cleanup, reconciliation=True, audit_artifacts=[validation])
                self._record(phase, 'completed', 'cleanup-reconciled', frozen)
            else:
                self.journal.reconcile_mutation(operation_id, outcome, receipt_artifacts=[*frozen, validation])
                self._record(phase, 'interrupted', 'mutation-reconciled-not-retried', frozen)
        return self.advance()

    def interrupt(self):
        """Explicit operator cancellation; native pauses themselves do not clean up."""
        self._check_inputs()
        # A native coordinator holds this guard while its child and validator
        # run. Refuse contention before cancelling its request or changing state.
        with self.journal.lifecycle_lock():
            state = self.journal.state
            if state['lifecycle']['final_result'] is not None:
                return self._snapshot()
            active = state['lifecycle']['active_phase']
            if active is None:
                raise StateError('No lifecycle phase is active to interrupt')
            phase = next(p for p in self.docs['scenario']['phases'] if p['id'] == active['phase_id'])
            if not state['environment_reserved'] and not state['operations']:
                # An unfinished read-only preflight has no resources to own or
                # clean up. Never acquire a reservation merely to cancel it.
                if phase['operation'] != 'preflight':
                    raise StateError('Unreserved interruption requires an active preflight')
                self._abort(phase, 'interrupted', 'operator-interrupted')
                self._record(phase, 'interrupted', 'operator-interrupted')
            else:
                with self.journal.mutation_lock(self.shared_lock_dir):
                    state = self.journal.state
                    self._abort(phase, 'interrupted', 'operator-interrupted')
                    if state['active_mutation'] is not None:
                        return self._snapshot('reconciliation_required')
                    if state['active_native'] is not None:
                        self.journal.cancel_native('operator-interrupted')
                    self._record(phase, 'interrupted', 'operator-interrupted')
            return self._advance_locked()
