"""Resident, correction-only native orchestration; no receipt-to-authority path."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path
import threading

from . import native_codex
from .lifecycle import LifecycleEngine
from .native_handoff import NativeCorrectionCoordinator
from .state import MAX_ARTIFACT_BYTES, MAX_DOCUMENT_BYTES, StateError, _read, _sha


_STEPS = {'primary-correction': 'primary', 'replay-correction': 'replay'}


@dataclass(frozen=True)
class NativeCodexSelection:
    """Explicit host selection; construction grants no execution authority.

    Paths retain their spelling, including the external venv interpreter symlink.
    The concrete driver audits actual binaries, dependencies and installed bytes.
    """
    installed_root: Path
    package_sha256: str
    plugin_id: str
    executable: Path
    expected_client_sha256: str
    expected_client_version: str
    model: str
    reasoning_effort: str
    engine_python: Path
    total_timeout: float = 900
    max_trace_bytes: int = 16_000_000

    def __post_init__(self):
        if any(not isinstance(value, Path) or not value.is_absolute()
               for value in (self.installed_root, self.executable, self.engine_python)):
            raise StateError('Native selection requires explicit absolute host paths')
        if (any(not isinstance(value, str) or not native_codex._HASH.fullmatch(value)
                for value in (self.package_sha256, self.expected_client_sha256))
                or not isinstance(self.plugin_id, str) or len(self.plugin_id.split('@')) != 2
                or not all(native_codex._SEGMENT.fullmatch(value) for value in self.plugin_id.split('@'))
                or self.expected_client_version not in native_codex.CLIENT_VERSIONS
                or not isinstance(self.model, str) or not native_codex._MODEL.fullmatch(self.model)
                or self.reasoning_effort not in native_codex._EFFORTS
                or type(self.total_timeout) not in (int, float) or not math.isfinite(self.total_timeout)
                or not 1 <= self.total_timeout <= 900
                or type(self.max_trace_bytes) is not int or not 1024 <= self.max_trace_bytes <= 64_000_000):
            raise StateError('Native selection requires pinned identities and bounded execution limits')

    def _options(self, cancel):
        return {name: getattr(self, name) for name in (
            'installed_root', 'package_sha256', 'plugin_id', 'executable', 'expected_client_version',
            'model', 'reasoning_effort', 'engine_python', 'total_timeout', 'max_trace_bytes')} | {'cancel': cancel}


class NativeCorrectionSession:
    """Keep direct origins while the caller advances one concrete lifecycle.

    Only ``cancel`` is callable from another thread. Other methods use the main
    thread, as required by the lifecycle's bounded POSIX deadline handling.
    This class neither implements missing platform phases nor validates other
    native workflows. No callbacks, report imports, or origin restoration exist.
    """
    def __init__(self, engine: LifecycleEngine, *, selection: NativeCodexSelection):
        if type(engine) is not LifecycleEngine or type(selection) is not NativeCodexSelection:
            raise StateError('A concrete lifecycle engine and explicit native selection are required')
        if threading.current_thread() is not threading.main_thread():
            raise StateError('Resident native sessions require the main thread')
        selection.__post_init__()
        state = engine.journal.state
        if not state['environment_reserved'] or state['lifecycle']['final_result'] is not None:
            raise StateError('A resident correction session requires an active reserved lifecycle')
        if selection.package_sha256 != engine.journal.identities['package_sha256']:
            raise StateError('Native selection package differs from the lifecycle input')
        self._engine, self._journal, self._selection = engine, engine.journal, selection
        self._driver = engine.driver
        self._pid, self._run_id = os.getpid(), state['run_id']
        self._identities = dict(engine.journal.identities)
        self._mode, self._driver_id = engine.mode, engine.driver_id
        self._coordinator = NativeCorrectionCoordinator(engine)
        self._cancel = threading.Event()
        self._busy = self._failed = self._interrupted = False

    def __reduce__(self):
        raise StateError('Native sessions cannot be serialized or restored from reports')

    def _bound(self):
        if self._pid != os.getpid() or threading.current_thread() is not threading.main_thread():
            raise StateError('Native session belongs to its invoking process and main thread')
        if (self._engine.journal is not self._journal or self._engine.driver is not self._driver
                or self._engine.mode != self._mode
                or self._engine.driver_id != self._driver_id or self._journal.identities != self._identities):
            raise StateError('Resident lifecycle identity changed')
        self._engine._check_inputs()
        state = self._journal.state
        if (state['run_id'] != self._run_id or state['lifecycle']['execution_mode'] != self._mode
                or state['lifecycle']['driver_id'] != self._driver_id):
            raise StateError('Resident lifecycle identity changed')
        return state

    def _available(self):
        state = self._bound()
        if self._failed or self._cancel.is_set() or self._interrupted:
            raise StateError('Resident session stopped; inspect evidence and explicitly interrupt for cleanup')
        return state

    def _reference(self, reference, limit):
        raw = _read(self._journal._artifact_path(reference['path']), limit)
        if len(raw) != reference['bytes'] or _sha(raw) != reference['sha256']:
            raise StateError('Accepted native evidence changed')
        return reference['sha256']

    def _origins(self, state):
        """Audit retained originals against journal bytes, never reconstruct them."""
        origins = {}
        for accepted in state['native_results']:
            step = accepted['step_id']
            if step not in _STEPS:
                continue
            request = next((row for row in state['native_requests']
                            if row['request_id'] == accepted['request_id'] and row['step_id'] == step), None)
            if request is None or accepted['status'] != 'completed':
                raise StateError('A completed resident correction is required')
            attempt = _STEPS[step]
            origin = self._coordinator.origin(attempt=attempt, request_sha256=request['request']['sha256'])
            document, hashes = origin.document, origin.artifact_sha256
            if (document['run_id'] != self._run_id or document['attempt_id'] != attempt
                    or document['request_sha256'] != request['request']['sha256']
                    or document['execution_mode'] != ('controlled-test' if self._mode == 'controlled-test' else 'direct')
                    or len(accepted['artifacts']) != 1 or accepted['artifacts'][0]['kind'] != 'patch'):
                raise StateError('Resident correction origin identity changed')
            expected = {'native_request': self._reference(request['request'], MAX_DOCUMENT_BYTES),
                        'native_result': self._reference(accepted['result'], MAX_DOCUMENT_BYTES),
                        'native_validation': self._reference(accepted['validation'], MAX_DOCUMENT_BYTES),
                        'patch': self._reference(accepted['artifacts'][0], MAX_ARTIFACT_BYTES)}
            if any(hashes.get(name) != digest for name, digest in expected.items()):
                raise StateError('Resident origin does not bind the accepted correction bytes')
            origins[attempt] = origin
        return origins

    @contextmanager
    def _operation(self):
        if self._busy:
            raise StateError('A resident operation is already in progress')
        self._busy = True
        try:
            yield
        except BaseException:
            # Includes KeyboardInterrupt and a failure after journal acceptance
            # but before origin publication. Neither permits an automatic retry.
            self._failed = True
            raise
        finally:
            self._busy = False

    def execute_pending(self, scope):
        """Invoke exactly the current primary/replay correction; do not advance."""
        state = self._available()
        self._origins(state)
        active = state['active_native']
        if (active is None or active['step_id'] not in _STEPS
                or active['workflow'] != 'prepare-gitops-correction'):
            raise StateError('Only a pending primary or replay correction is supported')
        with self._operation():
            origin = self._coordinator.execute(scope,
                request_path=self._journal.run_dir / active['request']['path'],
                request_sha256=active['request']['sha256'],
                expected_client_sha256=self._selection.expected_client_sha256,
                native_options=self._selection._options(self._cancel))
            self._available()  # Cancellation racing a completed turn never advances.
            origins = self._origins(self._journal.state)
            if origins.get(_STEPS[active['step_id']]) is not origin:
                raise StateError('Resident correction origin is unavailable')
            return origin

    def origin(self, *, attempt):
        """Return the original object for a completed correction in this session."""
        if attempt not in ('primary', 'replay'):
            raise StateError('Only primary or replay correction origins are supported')
        origins = self._origins(self._available())
        if attempt not in origins:
            raise StateError('Direct native origin is unavailable; saved evidence cannot restore it')
        return origins[attempt]

    def advance(self):
        """Advance the existing engine while retaining correction provenance.

        Unsupported native workflows remain ordinary native yields. The caller
        must not use report resume to authenticate a correction outside this
        session; a subsequent advance detects the missing direct origin.
        """
        self._available()
        with self._operation():
            # Keep origin admission and dependent phases within the same lock.
            # Calling public advance() would release this guard or reenter its
            # non-reentrant lock. Bootstrap/preflight are outside this component.
            with self._journal.lifecycle_lock():
                state = self._available()
                self._origins(state)
                if state['lifecycle']['final_result'] is not None:
                    return self._engine._snapshot()
                if not state['environment_reserved']:
                    raise StateError('Resident correction continuation requires its existing reservation')
                return self._engine._advance_reserved()

    def reconcile_cleanup(self, *, operation_id):
        """Explicit read-only cleanup reconciliation on this original process.

        Native cancellation/failure does not block inspection of owned effects.
        """
        self._bound()
        with self._operation():
            return self._engine.reconcile_cleanup(operation_id=operation_id)

    def continue_cleanup(self, *, prior_operation_id, cleanup_mode="normal"):
        """Explicit new cleanup operation; never invoked by ordinary advance."""
        self._bound()
        with self._operation():
            return self._engine.continue_cleanup(prior_operation_id=prior_operation_id, cleanup_mode=cleanup_mode)

    def cancel(self):
        """Signal child cancellation only; this never claims platform cleanup."""
        if self._pid != os.getpid():
            raise StateError('Native session belongs to its invoking process')
        self._cancel.set()

    def interrupt(self):
        """After child termination, explicitly enter the existing mandatory tail.

        Failure or missing native origin cannot prevent cleanup. The unchanged
        lifecycle still requires reconciliation and independent cleanup checks.
        """
        self._bound()
        if self._interrupted:
            raise StateError('Resident interruption was already attempted; inspect retained lifecycle state')
        with self._operation():
            self._cancel.set()
            self._interrupted = True
            return self._engine.interrupt()
