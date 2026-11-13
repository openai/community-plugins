"""Canonical headless native driver; current-host yields remain separate.

The selection is explicit, byte-bound operator input. It cannot restore native
origins or select callbacks. One resident session owns all eight native calls.
"""
from __future__ import annotations

from contextlib import contextmanager
import signal
import threading
from pathlib import Path

from .lifecycle import LifecycleEngine, PACKAGE_ROOT
from .native_factory_session import NativeFactorySession
from .native_session import NativeCodexSelection
from .state import MAX_ARTIFACT_BYTES, MAX_DOCUMENT_BYTES, StateError, _json, _read, _sha

_KIND = 'canonical-native-driver-selection'
_FIELDS = {'schema_version', 'kind', 'run_scope', 'environment_id', 'plugin_id', 'plugin_version',
           'installed_root', 'executable', 'expected_client_sha256', 'expected_client_version',
           'model', 'reasoning_effort', 'engine_python', 'total_timeout', 'max_trace_bytes'}
_STEPS = tuple(f'{attempt}-{kind}' for attempt in ('primary', 'replay')
               for kind in ('review', 'diagnose', 'correction', 'recovery'))


def validate_selection(raw, *, identities, environment_id, plugin_id, plugin_version):
    """Validate frozen selection bytes without executing a client or a model."""
    value = _json(raw)
    if (type(value) is not dict or set(value) != _FIELDS
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['kind'] != _KIND or value['run_scope'] != dict(identities)
            or value['environment_id'] != environment_id or value['plugin_id'] != plugin_id
            or value['plugin_version'] != plugin_version):
        raise StateError('Native selection differs from the explicit lifecycle scope')
    for field in ('installed_root', 'executable', 'engine_python'):
        path = value[field]
        if (not isinstance(path, str) or '\x00' in path or '\r' in path or '\n' in path
                or not Path(path).is_absolute() or '..' in Path(path).parts):
            raise StateError('Native selection requires explicit absolute host paths')
    selected = NativeCodexSelection(
        installed_root=Path(value['installed_root']), package_sha256=identities['package_sha256'],
        plugin_id=value['plugin_id'], executable=Path(value['executable']),
        expected_client_sha256=value['expected_client_sha256'],
        expected_client_version=value['expected_client_version'], model=value['model'],
        reasoning_effort=value['reasoning_effort'], engine_python=Path(value['engine_python']),
        total_timeout=value['total_timeout'], max_trace_bytes=value['max_trace_bytes'])
    if selected.max_trace_bytes > MAX_ARTIFACT_BYTES:
        raise StateError('Canonical native trace exceeds the journal artifact byte bound')
    return selected


def load_selection(path, pin, *, identities, environment_id, plugin_id, plugin_version,
                   config, lock, package_manifest):
    """Resolve only current explicit input and the installed, locked client."""
    if (not isinstance(path, Path) or not path.is_absolute() or '..' in path.parts
            or not isinstance(pin, str) or len(pin) != 64):
        raise StateError('Native selection needs an explicit absolute path and SHA256')
    raw = _read(path, MAX_DOCUMENT_BYTES)
    if _sha(raw) != pin:
        raise StateError('Native selection differs from its explicit SHA256')
    selection = validate_selection(raw, identities=identities, environment_id=environment_id,
                                   plugin_id=plugin_id, plugin_version=plugin_version)
    locked = [row for row in lock['tools'] if row['name'] == 'codex']
    if (selection.installed_root != PACKAGE_ROOT or package_manifest != PACKAGE_ROOT / 'SHA256SUMS'
            or config['tool_paths'].get('codex') != str(selection.executable)
            or len(locked) != 1 or locked[0]['sha256'] != selection.expected_client_sha256
            or locked[0]['version'] != selection.expected_client_version):
        raise StateError('Native driver differs from the actual installed package or locked client')
    return selection


@contextmanager
def _signals(session):
    if threading.current_thread() is not threading.main_thread():
        raise StateError('Canonical native execution requires the main thread')
    previous = {}
    try:
        for number in (signal.SIGINT, signal.SIGTERM):
            previous[number] = signal.signal(number, lambda _number, _frame: session.cancel())
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def run(engine, *, selection, cleanup_controls=False):
    """Drive the canonical graph through its actual eight native handoffs.

    Controlled engines are allowed only as their existing explicit test mode.
    A resumed pending first handoff can be freshly prepared by the fixed backend;
    accepted native history cannot recreate the originating resident session.
    """
    if type(engine) is not LifecycleEngine or type(selection) is not NativeCodexSelection:
        raise StateError('Canonical native execution requires a concrete engine and selection')
    selection.__post_init__()
    from .resident_cleanup_control import require_terminal, wait_for_cleanup
    if type(cleanup_controls) is not bool:
        raise StateError('Resident cleanup controls require explicit selection')
    if cleanup_controls:
        require_terminal()  # Reject unavailable control before the first effect.
    if selection.max_trace_bytes > MAX_ARTIFACT_BYTES:
        raise StateError('Canonical native trace exceeds the journal artifact byte bound')
    if engine.mode != 'controlled-test':
        from .owned_linux import OwnedLinuxDriver
        if engine.driver_id != 'owned-linux' or type(engine.driver) is not OwnedLinuxDriver:
            raise StateError('Headless native execution requires the fixed owned Linux backend')
    state = engine.journal.state
    if state['lifecycle']['final_result'] is not None:
        return engine.advance()
    if state['native_results']:
        raise StateError('Accepted native history requires its original process; use explicit abort or fresh qualification')
    result = engine.advance()
    if result['exit_code'] != 3:
        return wait_for_cleanup(engine, result, enabled=cleanup_controls)
    session = None
    seen = []
    try:
        session = NativeFactorySession(engine, selection=selection)
        engine.driver.bind_native_session(session)
        with _signals(session):
            while result['exit_code'] == 3:
                active = engine.journal.state['active_native']
                if (active is None or len(seen) >= len(_STEPS)
                        or active['step_id'] != _STEPS[len(seen)]):
                    raise StateError('Canonical native handoff order changed')
                if session.cancelled:
                    raise StateError('Canonical native execution was cancelled')
                scope = engine.driver.native_scope(step_id=active['step_id'])
                session.execute_pending(scope)
                seen.append(active['step_id'])
                result = session.advance()
    except (Exception, KeyboardInterrupt, SystemExit):
        # Cleanup also covers session construction and fixed-driver binding.
        # Unknown mutation effects stay reserved for reconciliation.
        try:
            if session is None:
                result = engine.interrupt()
            else:
                session.cancel()
                result = session.interrupt()
        except BaseException as error:
            raise StateError('Native execution stopped; cleanup requires inspection of the retained run') from error
        result = {**result, 'exit_code': 2, 'canonical_success': False,
                'native_driver_error': 'native-execution-stopped',
                'native_steps_completed_in_process': len(seen)}

    return wait_for_cleanup(engine, result, enabled=cleanup_controls)
