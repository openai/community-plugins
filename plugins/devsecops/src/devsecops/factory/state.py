"""Private, resumable factory bookkeeping; this module does not execute platforms.

``RunJournal`` binds a run to six immutable SHA-256 identities. The event journal
is authoritative; ``state.json`` is a checked, recoverable projection. A local
POSIX advisory lock coordinates callers using the same shared lock directory. It
is not a distributed lease across unrelated hosts or filesystems.

Mutation callers must hold ``mutation_lock`` and supply sanitized observation
artifacts. A pending write reopened by another invocation requires explicit
reconciliation, never an automatic retry. Native results only reference local
artifacts. ``accept_native`` requires a trusted deterministic validator accepting
``(request, result, artifact_bytes_by_name)`` and returning nonempty check IDs.
Its receipt establishes artifact validation, never live scenario success.

Concrete drivers own actual authorization, platform observations, cleanup, and
independent assertion checks. Optional lifecycle events enforce the canonical
graph and derive a result from retained evidence; they do not authenticate a
platform or accept a caller-supplied success flag.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from types import MappingProxyType
from typing import Callable, Mapping
from uuid import uuid4

from jsonschema import Draft202012Validator


MAX_DOCUMENT_BYTES = 1_000_000
MAX_ARTIFACT_BYTES = 16_000_000
MAX_ARTIFACT_TOTAL_BYTES = 64_000_000
MAX_RESULT_ARTIFACTS = 256
MAX_EVENTS = 10_000
MAX_JOURNAL_BYTES = 16_000_000
IDENTITY_KEYS = frozenset({
    'scenario_sha256', 'assertions_sha256', 'package_sha256',
    'lock_sha256', 'config_sha256', 'environment_sha256',
})
AUXILIARY_MISSION_NAMES = frozenset({
    'mission_git_invocation', *(f'mission_git_{name}' for name in
        ('build', 'staging', 'security', 'signing', 'publication', 'proposal', 'commit', 'destination')),
})
AUXILIARY_NAMES = AUXILIARY_MISSION_NAMES | {
    'owned_registry_recipe', 'native_selection', 'owned_operations_recipe', 'owned_bootstrap_recipe',
    'owned_cases_recipe', 'owned_git_writer', 'owned_runtime_recipe'}
WORKFLOWS = {
    'review-kubernetes-change': 'review-report',
    'diagnose-gitops-deployment': 'diagnosis',
    'prepare-gitops-correction': 'patch',
    'verify-gitops-recovery': 'recovery-report',
}
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,119}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_SCHEMAS = Path(__file__).resolve().parents[3] / 'schemas'


PARENT_DISPOSAL_MODE = 'dispose-owned-runner-guest'
CONTAINED_GUEST_CHECK = 'owned-guest-contained-residual-parent-disposal-only'
CONTAINED_GUEST_CHECKS = frozenset((CONTAINED_GUEST_CHECK,
    'original-runner-parent-ownership-observed',
    'original-runner-registration-revoked-observed'))


def _cleanup_modes(checks):
    if CONTAINED_GUEST_CHECK not in checks:
        return ['normal']
    if any(checks.count(check) != 1 for check in CONTAINED_GUEST_CHECKS):
        raise StateError('Contained guest residual requires original parent and revocation checks')
    return [PARENT_DISPOSAL_MODE]


class StateError(ValueError):
    """A sanitized state, identity, path or handoff error."""


def _canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StateError('Duplicate JSON object key')
        result[key] = value
    return result


def _json(raw: bytes):
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise StateError('JSON document exceeds the size limit')
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(StateError('Nonfinite JSON number')))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise StateError('Invalid bounded JSON document') from error


def _safe_path(path: Path) -> Path:
    if '..' in Path(path).parts:
        raise StateError('Parent traversal is not permitted in file paths')
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise StateError('Symlink paths are not permitted')
    return path


def _read(path: Path, limit: int) -> bytes:
    path = _safe_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise StateError('Artifact is not a bounded regular file')
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if len(raw) > limit or (before.st_size, before.st_mtime_ns, before.st_ino) != (
                    after.st_size, after.st_mtime_ns, after.st_ino):
                raise StateError('Artifact changed while being read')
            return raw
    except OSError as error:
        raise StateError('A required file is unavailable') from error


def _private_directory(path: Path, *, new: bool = False):
    path = _safe_path(path)
    if new:
        path.mkdir(mode=0o700)
    elif not path.exists():
        path.mkdir(mode=0o700, parents=True)
    if not path.is_dir() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
        raise StateError('Run and lock directories must be private and owned by the operator')


def _fsync_directory(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _lock_file(path: Path) -> int:
    fd = os.open(_safe_path(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        os.close(fd)
        raise StateError('Lock file must be a private regular file owned by the operator')
    return fd


def _exclusive(path: Path, raw: bytes):
    path = _safe_path(path)
    temporary = path.parent / ('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        # link publishes the completed file atomically and refuses replacement.
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    except FileExistsError as error:
        raise StateError('An immutable artifact already exists') from error
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic(path: Path, raw: bytes):
    path = _safe_path(path)
    temporary = path.parent / ('.' + path.name + '.' + uuid4().hex + '.tmp')
    _exclusive(temporary, raw)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _validate(name: str, value):
    schema = _json(_read(_SCHEMAS / (name + '.schema.json'), MAX_DOCUMENT_BYTES))
    if next(Draft202012Validator(schema).iter_errors(value), None) is not None:
        raise StateError('Invalid ' + name + ' schema')


def _identities(value):
    if (not isinstance(value, dict) or set(value) != IDENTITY_KEYS
            or any(not isinstance(v, str) or not _HASH.fullmatch(v) for v in value.values())):
        raise StateError('All immutable run identities must be SHA-256 digests')
    return deepcopy(value)


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise StateError('Invalid phase, environment or artifact identifier')
    return value


def _time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() != timedelta(0):
            raise ValueError
        return result
    except (TypeError, ValueError) as error:
        raise StateError('A UTC timestamp is required') from error


GIT_REQUEST_ACTIONS = {'build-promote': 'build-promote', 'primary-inject': 'inject',
    'primary-deliver': 'deliver', 'reset': 'reset', 'replay-inject': 'inject', 'replay-deliver': 'deliver'}

class RunJournal:
    """Bounded journal API for the shared runner and both native drivers."""

    def __init__(self, run_dir: Path, identities: dict, clock=None):
        self.run_dir = _safe_path(run_dir)
        self.identities = _identities(identities)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._owned_operations = set()
        self._consumed_git_request_operations = set()
        self._environment_locked = False
        self._owner_path = None
        self._state = None
        self._journal_bytes = 0
        self._process_id = os.getpid()
        self._lifecycle_locked = False
        self._cached_journal_hashes = []
        self._projection_hashes = {}
        self._run_lock_owner = None
        self._validation_read_owner = None

    @classmethod
    def create(cls, run_dir: Path, *, identities: dict, environment_id: str,
               cleanup: str = 'always', clock=None) -> 'RunJournal':
        if cleanup != 'always':
            raise StateError('This lifecycle slice requires cleanup always')
        _identifier(environment_id)
        store = cls(run_dir, identities, clock)
        if store.run_dir.exists():
            raise StateError('A new run directory is required')
        _private_directory(store.run_dir, new=True)
        for name in ('journal', 'native', 'operations', 'cleanup'):
            _private_directory(store.run_dir / name, new=True)
        with store._run_lock():
            store._commit('created', {'run_id': str(uuid4()), 'environment_id': environment_id,
                                      'identities': store.identities, 'cleanup': cleanup})
        return store

    @classmethod
    def open(cls, run_dir: Path, *, identities: dict, clock=None) -> 'RunJournal':
        store = cls(run_dir, identities, clock)
        if not store.run_dir.is_dir():
            raise StateError('An existing run directory is required')
        _private_directory(store.run_dir)
        with store._run_lock():
            store._load()
        return store

    @property
    def state(self) -> dict:
        if self._has_validation_read_lock():
            self._load()
            return deepcopy(self._state)
        with self._run_lock():
            self._load()
            return deepcopy(self._state)

    def _has_validation_read_lock(self):
        owner = self._validation_read_owner
        return (owner is not None and owner == self._run_lock_owner
                and owner[:2] == (os.getpid(), threading.get_ident()))

    @contextmanager
    def _validation_reads(self):
        """Let a trusted validator inspect this thread's already-locked journal.

        Only state/current-operation reads use this scope. All mutation methods
        still require a new exclusive lock and cannot reenter during validation.
        """
        owner = self._run_lock_owner
        if (owner is None or owner[:2] != (os.getpid(), threading.get_ident())
                or self._validation_read_owner is not None):
            raise StateError('Validation reads require the original journal lock owner')
        before = deepcopy(self._state)
        self._validation_read_owner = owner
        try:
            yield
            self._load()
            if self._state != before:
                raise StateError('Journal changed during read-only validation')
        finally:
            self._validation_read_owner = None

    @property
    def needs_reconciliation(self) -> bool:
        active = self.state['active_mutation']
        return active is not None and active['operation_id'] not in self._owned_operations

    @contextmanager
    def _run_lock(self):
        if self._has_validation_read_lock():
            raise StateError('Journal mutation is unavailable during read-only validation')
        _private_directory(self.run_dir)
        path = _safe_path(self.run_dir / '.run.lock')
        fd = _lock_file(path)
        acquired = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StateError('Another operation holds the run journal lock') from error
            acquired = True
            self._run_lock_owner = (os.getpid(), threading.get_ident(), fd)
            yield
        finally:
            if acquired:
                self._run_lock_owner = None
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @contextmanager
    def lifecycle_lock(self):
        """Serialize the local runner without claiming any infrastructure."""
        if self._lifecycle_locked:
            raise StateError('The lifecycle execution lock is already held')
        fd = _lock_file(self.run_dir / '.lifecycle.lock')
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StateError('Another runner holds the lifecycle execution lock') from error
            self._lifecycle_locked = True
            yield self
        finally:
            self._lifecycle_locked = False
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @contextmanager
    def mutation_lock(self, shared_lock_dir: Path):
        """Hold this around the journal intent, external effect, and observation.

        All processes controlling this environment must use the same trusted
        directory. The run's environment reservation persists after releasing
        the process lock, including native handoff and crash recovery. Only
        ``release_environment`` with trusted cleanup observations releases it.
        """
        if self._environment_locked:
            raise StateError('The environment mutation lock is already held')
        shared_lock_dir = _safe_path(shared_lock_dir)
        _private_directory(shared_lock_dir)
        path = shared_lock_dir / (self.identities['environment_sha256'] + '.lock')
        fd = _lock_file(path)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise StateError('Another operation holds the environment mutation lock') from error
            self._environment_locked = True
            self._owner_path = shared_lock_dir / (self.identities['environment_sha256'] + '.owner.json')
            with self._run_lock():
                self._load()
                if self._owner_path.exists():
                    owner = _json(_read(self._owner_path, MAX_DOCUMENT_BYTES))
                    if (not isinstance(owner, dict) or set(owner) != {
                            'schema_version', 'run_id', 'run_dir', 'identities', 'operation'}
                            or owner['schema_version'] != 1 or owner['identities'] != self.identities
                            or owner['run_id'] != self._state['run_id'] or owner['run_dir'] != str(self.run_dir)):
                        raise StateError('Environment belongs to another run; resume its owner before reuse')
                    if self._state['execution_status'] == 'released':
                        self._owner_path.unlink()
                        _fsync_directory(shared_lock_dir)
                    else:
                        if not self._state['environment_reserved']:
                            self._commit('environment_reserved', {})
                        operation = owner['operation']
                        if operation is not None:
                            if not isinstance(operation, dict) or 'operation_id' not in operation:
                                raise StateError('Environment mutation owner record is invalid')
                            existing = next((o for o in self._state['operations']
                                             if o['operation_id'] == operation['operation_id']), None)
                            if existing is None:
                                self._verify_references(operation)
                                self._commit('mutation_started', operation)
                            else:
                                expected = {**existing, 'status': 'pending', 'receipt_artifacts': []}
                                if operation != expected:
                                    raise StateError('Environment mutation owner record conflicts with the journal')
                                if existing['status'] in ('applied', 'not_applied', 'partial'):
                                    if self._state['active_mutation'] is not None:
                                        raise StateError('Environment mutation owner record conflicts with the journal')
                                    self._write_owner(None)
                                elif self._state['active_mutation'] is None:
                                    raise StateError('Environment mutation owner record conflicts with the journal')
                        elif self._state['active_mutation'] is not None:
                            raise StateError('Environment mutation owner record conflicts with the journal')
                elif self._state['execution_status'] != 'released':
                    if self._state['environment_reserved']:
                        raise StateError('Reserved environment owner record is missing')
                    self._write_owner(None)
                    self._commit('environment_reserved', {})
            yield self
        finally:
            self._environment_locked = False
            self._owner_path = None
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _write_owner(self, operation):
        if not self._environment_locked or self._owner_path is None:
            raise StateError('Environment ownership requires the mutation lock')
        _atomic(self._owner_path, _canonical({'schema_version': 1, 'run_id': self._state['run_id'],
                'run_dir': str(self.run_dir), 'identities': self.identities, 'operation': operation}))

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise StateError('The run clock must return an aware timestamp')
        return value.astimezone(timezone.utc).isoformat()

    def _artifact_path(self, relative: str) -> Path:
        p = Path(relative)
        if p.is_absolute() or not p.parts or any(x in ('..', '.') for x in p.parts):
            raise StateError('Artifact path must be relative to its run')
        if '\\' in relative or str(p) != relative:
            raise StateError('Artifact path is not canonical')
        return _safe_path(self.run_dir / p)

    def _reference(self, path: Path, raw: bytes) -> dict:
        return {'path': str(path.relative_to(self.run_dir)), 'sha256': _sha(raw), 'bytes': len(raw)}

    def _freeze(self, path: Path, destination: Path, *, budget=MAX_ARTIFACT_BYTES) -> dict:
        raw = _read(path, min(MAX_ARTIFACT_BYTES, budget))
        _exclusive(destination, raw)
        return self._reference(destination, raw)

    def _freeze_many(self, sources, directory: Path, *, max_artifacts=64) -> list[dict]:
        if len(sources) > max_artifacts:
            raise StateError('Too many artifacts')
        _private_directory(directory, new=True)
        refs, total = [], 0
        for i, source in enumerate(sources):
            item = self._freeze(Path(source), directory / str(i), budget=MAX_ARTIFACT_TOTAL_BYTES - total)
            refs.append(item)
            total += item['bytes']
        return refs

    def _verify_references(self, value):
        if isinstance(value, dict):
            if {'path', 'sha256', 'bytes'} <= set(value):
                raw = _read(self._artifact_path(value['path']), MAX_ARTIFACT_BYTES)
                if len(raw) != value['bytes'] or _sha(raw) != value['sha256']:
                    raise StateError('A bound artifact was changed')
            for item in value.values():
                self._verify_references(item)
        elif isinstance(value, list):
            for item in value:
                self._verify_references(item)

    def _load(self):
        journal = _safe_path(self.run_dir / 'journal')
        if not journal.is_dir():
            raise StateError('Run journal is unavailable')
        entries = list(journal.iterdir())
        paths = sorted(p for p in entries if not re.fullmatch(r'\.\d{8}\.json\.[0-9a-f]{32}\.tmp', p.name))
        if not paths or len(entries) > MAX_EVENTS * 2:
            raise StateError('Run journal is empty or exceeds the event limit')
        projection = self.run_dir / 'state.json'
        if projection.exists():
            previous = _json(_read(projection, MAX_DOCUMENT_BYTES))
            _validate('factory-state', previous)
        else:
            previous = None
        # Re-hash every immutable event on every load. Reuse semantic validation
        # only for the exact already-validated byte sequence, never stat metadata.
        # Full replay remains necessary after another instance appends an event.
        if self._state is not None and len(paths) == len(self._cached_journal_hashes):
            hashes = []
            for number, path in enumerate(paths, 1):
                if path.name != f'{number:08d}.json':
                    raise StateError('Run journal is not a contiguous event sequence')
                hashes.append(_sha(_read(path, MAX_DOCUMENT_BYTES)))
            if hashes == self._cached_journal_hashes:
                if self._projection_hashes.get(self._state['journal_seq']) != _sha(_canonical(self._state)):
                    raise StateError('Cached journal state identity changed')
                if self._state['identities'] != self.identities:
                    raise StateError('Run input, package, lock, config or environment identity changed')
                if previous is not None and self._projection_hashes.get(previous['journal_seq']) != _sha(_canonical(previous)):
                    raise StateError('State projection does not match its journal')
                self._verify_references(self._state)
                if previous != self._state:
                    self._write_projection()
                return
        projection_matched = previous is None
        journal_bytes = 0
        self._state = None
        self._cached_journal_hashes = []
        self._projection_hashes = {}
        for number, path in enumerate(paths, 1):
            if path.name != f'{number:08d}.json':
                raise StateError('Run journal is not a contiguous event sequence')
            raw = _read(path, MAX_DOCUMENT_BYTES)
            journal_bytes += len(raw)
            if journal_bytes > MAX_JOURNAL_BYTES:
                raise StateError('Run journal exceeds the aggregate size limit')
            event = _json(raw)
            if (not isinstance(event, dict) or set(event) != {
                    'schema_version', 'seq', 'previous_sha256', 'type', 'time', 'payload'}
                    or event['schema_version'] != 1 or event['seq'] != number
                    or event['previous_sha256'] != (self._state['journal_head_sha256'] if self._state else None)):
                raise StateError('Run journal identity or hash chain is invalid')
            _time(event['time'])
            if self._state and _time(event['time']) < _time(self._state['updated_at']):
                raise StateError('Run journal timestamp moved backwards')
            self._state = self._reduce(event, _sha(raw))
            _validate('factory-state', self._state)
            self._cached_journal_hashes.append(_sha(raw))
            self._projection_hashes[number] = _sha(_canonical(self._state))
            if previous and previous['journal_seq'] == number:
                projection_matched = previous == self._state
        if self._state['identities'] != self.identities:
            raise StateError('Run input, package, lock, config or environment identity changed')
        self._journal_bytes = journal_bytes
        self._verify_references(self._state)
        if not projection_matched:
            raise StateError('State projection does not match its journal')
        if previous != self._state:
            self._write_projection()

    def _write_projection(self):
        _atomic(self.run_dir / 'state.json', _canonical(self._state))

    def _commit(self, kind: str, payload: dict):
        if self._validation_read_owner is not None:
            raise StateError('Journal mutation is unavailable during read-only validation')
        seq = self._state['journal_seq'] + 1 if self._state else 1
        if seq > MAX_EVENTS:
            raise StateError('Run journal event limit reached')
        event = {'schema_version': 1, 'seq': seq,
                 'previous_sha256': self._state['journal_head_sha256'] if self._state else None,
                 'type': kind, 'time': self._now(), 'payload': deepcopy(payload)}
        if self._state and _time(event['time']) < _time(self._state['updated_at']):
            raise StateError('Run clock moved backwards')
        raw = _canonical(event)
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise StateError('Journal event exceeds the size limit')
        if self._journal_bytes + len(raw) > MAX_JOURNAL_BYTES:
            raise StateError('Run journal exceeds the aggregate size limit')
        new_state = self._reduce(event, _sha(raw))
        _validate('factory-state', new_state)
        if len(_canonical(new_state)) > MAX_DOCUMENT_BYTES:
            raise StateError('Run state exceeds the size limit')
        _exclusive(self.run_dir / 'journal' / f'{seq:08d}.json', raw)
        self._journal_bytes += len(raw)
        self._state = new_state
        self._cached_journal_hashes.append(_sha(raw))
        self._projection_hashes[seq] = _sha(_canonical(new_state))
        self._write_projection()

    def _reduce(self, event: dict, event_hash: str) -> dict:
        kind, p = event['type'], event['payload']
        fields = {
            'created': {'run_id', 'environment_id', 'identities', 'cleanup'},
            'environment_reserved': set(),
            'environment_released': {'receipt_artifacts', 'validation'},
            'cleanup_completed': {'operation_id', 'receipt_artifacts', 'audit_artifacts', 'validation'},
            'cleanup_partially_reconciled': {'operation_id', 'receipt_artifacts', 'validation'},
            'mutation_started': {'operation_id', 'phase_id', 'action', 'status',
                                 'intent_artifacts', 'receipt_artifacts'},
            'git_request_claimed': {'operation_id', 'phase_id', 'receipt_artifacts'},
            'mutation_observed': {'operation_id', 'outcome', 'receipt_artifacts'},
            'native_requested': {'request_id', 'step_id', 'workflow', 'expires_at', 'request', 'input_artifacts'},
            'native_validated': {'request_id', 'step_id', 'status', 'result', 'artifacts', 'validation'},
            'native_cancelled': {'request_id', 'step_id', 'reason_code'},
            'lifecycle_initialized': {'inputs', 'phases', 'execution_mode', 'driver_id'},
            'phase_started': {'phase_id'},
            'phase_finished': {'phase_id', 'outcome', 'code', 'artifacts', 'assertions'},
            'lifecycle_aborted': {'phase_id', 'outcome', 'code'},
            'lifecycle_finalized': {'result'},
        }
        permitted = fields.get(kind)
        if kind == 'mutation_started' and isinstance(p, dict) and 'continues_operation' in p:
            permitted = permitted | {'continues_operation'}
        if kind == 'mutation_started' and isinstance(p, dict) and 'cleanup_mode' in p:
            permitted = permitted | {'cleanup_mode'}
        if kind == 'cleanup_partially_reconciled' and isinstance(p, dict) and 'cleanup_continuation_modes' in p:
            permitted = permitted | {'cleanup_continuation_modes'}
        if kind == 'lifecycle_initialized' and isinstance(p, dict) and 'auxiliary_inputs' in p:
            permitted = permitted | {'auxiliary_inputs'}
        if not isinstance(p, dict) or kind not in fields or set(p) != permitted:
            raise StateError('Unsupported journal event payload')
        s = deepcopy(self._state)
        if kind == 'created':
            if s is not None or set(p) != {'run_id', 'environment_id', 'identities', 'cleanup'}:
                raise StateError('Invalid journal creation event')
            s = {'schema_version': 1, **p, 'created_at': event['time'], 'execution_status': 'ready',
                 'active_mutation': None, 'active_native': None, 'operations': [],
                 'native_requests': [], 'native_results': [], 'native_cancellations': [], 'environment_reserved': False,
                 'cleanup_artifacts': [], 'cleanup_validation': None}
        elif s is None:
            raise StateError('Run creation event is required first')
        elif kind.startswith('lifecycle_') or kind.startswith('phase_'):
            self._reduce_lifecycle(s, kind, p, event['time'])
        elif kind == 'environment_reserved':
            if s['environment_reserved'] or s['execution_status'] == 'released':
                raise StateError('Environment cannot be reserved again')
            s['environment_reserved'] = True
        elif kind == 'environment_released':
            if not s['environment_reserved'] or s['execution_status'] not in ('ready', 'native_incomplete'):
                raise StateError('Outstanding work prevents environment release')
            s['environment_reserved'] = False
            s['execution_status'] = 'released'
            s['cleanup_artifacts'] = p['receipt_artifacts']
            s['cleanup_validation'] = p['validation']
        elif kind == 'cleanup_completed':
            active = s['active_mutation']
            lifecycle = s.get('lifecycle')
            if (not s['environment_reserved'] or s['active_native'] is not None
                    or s['execution_status'] not in ('mutation_pending', 'reconciliation_required')
                    or active is None or active['operation_id'] != p['operation_id']
                    or active['action'] != 'down' or not p['receipt_artifacts']
                    or not p['validation']):
                raise StateError('Cleanup completion must resolve the exact outstanding down operation')
            if lifecycle is not None:
                phase = next((phase for phase in lifecycle['phases']
                              if phase['id'] == active['phase_id']), None)
                if (phase is None or phase['operation'] != 'down'
                        or (lifecycle['active_phase'] or {}).get('phase_id') != active['phase_id']):
                    raise StateError('Cleanup completion differs from the current cleanup phase')
            operation = next(o for o in s['operations'] if o['operation_id'] == p['operation_id'])
            operation['status'] = 'applied'
            operation['receipt_artifacts'].extend([*p['receipt_artifacts'], *p['audit_artifacts'], p['validation']])
            s['active_mutation'] = None
            s['environment_reserved'] = False
            s['execution_status'] = 'released'
            s['cleanup_artifacts'] = p['receipt_artifacts']
            s['cleanup_validation'] = p['validation']
        elif kind == 'cleanup_partially_reconciled':
            active, life = s['active_mutation'], s.get('lifecycle')
            if (not s['environment_reserved'] or s['active_native'] is not None
                    or active is None or active['operation_id'] != p['operation_id']
                    or active['action'] != 'down' or not p['receipt_artifacts'] or not p['validation']
                    or life is None or life['final_result'] is not None
                    or (life['active_phase'] or {}).get('phase_id') != active['phase_id']
                    or s['execution_status'] not in ('mutation_pending', 'reconciliation_required')):
                raise StateError('Partial cleanup requires the exact unresolved resident cleanup phase')
            operation = next(o for o in s['operations'] if o['operation_id'] == p['operation_id'])
            self._verify_references([*p['receipt_artifacts'], p['validation']])
            proof = _json(_read(self._artifact_path(p['validation']['path']), MAX_ARTIFACT_BYTES))
            if (proof.get('operation_id') != p['operation_id'] or proof.get('run_id') != s['run_id']
                    or not isinstance(proof.get('checks'), list)):
                raise StateError('Partial cleanup validation differs from its original operation')
            modes = _cleanup_modes(proof['checks'])
            if p.get('cleanup_continuation_modes', ['normal']) != modes:
                raise StateError('Partial cleanup continuation mode differs from its validation')
            if modes == ['normal'] and 'cleanup_continuation_modes' in p:
                raise StateError('Ordinary partial cleanup cannot declare a restricted mode')
            expected_coverage = ('original-resident-terminal-partial-cleanup' if modes == ['normal']
                else 'original-owned-parent-contained-guest-residual')
            if proof.get('coverage') != expected_coverage:
                raise StateError('Partial cleanup validation coverage differs from its admitted mode')
            if modes != ['normal']:
                operation['cleanup_continuation_modes'] = modes
            operation['status'] = 'partial'
            operation['receipt_artifacts'].extend([*p['receipt_artifacts'], p['validation']])
            s['active_mutation'] = None
            s['execution_status'] = 'ready'
        elif kind == 'mutation_started':
            prior = next((o for o in reversed(s['operations']) if o['action'] == 'down'), None)
            continuation = p.get('continues_operation')
            mode = p.get('cleanup_mode', 'normal')
            if ('cleanup_mode' in p and (mode != PARENT_DISPOSAL_MODE or continuation is None)
                    or continuation is not None and prior is not None
                    and mode not in prior.get('cleanup_continuation_modes', ['normal'])):
                raise StateError('Cleanup mode is not admitted by the partial predecessor')
            if continuation is not None:
                if (p['action'] != 'down' or prior is None or prior['operation_id'] != continuation
                        or prior['status'] != 'partial' or prior['phase_id'] != p['phase_id']
                        or p['operation_id'] == continuation):
                    raise StateError('Cleanup continuation requires the exact terminal partial predecessor')
            elif p['action'] == 'down' and prior is not None:
                raise StateError('Another cleanup requires explicit terminal partial continuation')
            if 'lifecycle' in s:
                active_phase = s['lifecycle']['active_phase']
                if active_phase is None or active_phase['phase_id'] != p['phase_id']:
                    raise StateError('Mutation must belong to the active lifecycle phase')
                phase = next(x for x in s['lifecycle']['phases'] if x['id'] == p['phase_id'])
                action = 'platform-dependency' if phase['id'] == 'platform-dependency' else phase['operation']
                if (action not in ('prepare', 'up', 'build-promote', 'inject', 'deliver', 'probe',
                                   'platform-dependency', 'reset', 'down') or p['action'] != action):
                    raise StateError('Read-only lifecycle phases cannot record external mutations')
            if (not s['environment_reserved'] or p['status'] != 'pending' or p['receipt_artifacts']
                    or s['execution_status'] != 'ready'
                    or any(o['operation_id'] == p['operation_id'] for o in s['operations'])):
                raise StateError('A pending operation prevents another mutation')
            s['active_mutation'] = p
            s['operations'].append(p)
            s['execution_status'] = 'mutation_pending'
        elif kind == 'git_request_claimed':
            refs = self._git_request_claim_refs(p['operation_id'], p['receipt_artifacts'])
            active, lifecycle = s['active_mutation'], s.get('lifecycle')
            phase = next((phase for phase in lifecycle['phases']
                          if phase['id'] == p['phase_id']), None) if lifecycle else None
            operation = next((operation for operation in s['operations']
                              if operation['operation_id'] == p['operation_id']), None)
            if (not s['environment_reserved'] or s['execution_status'] != 'mutation_pending'
                    or lifecycle is None or lifecycle['abort'] is not None
                    or lifecycle['final_result'] is not None
                    or (lifecycle['active_phase'] or {}).get('phase_id') != p['phase_id']
                    or phase is None or GIT_REQUEST_ACTIONS.get(p['phase_id']) != phase['operation']
                    or active is None or operation is None or active != operation
                    or operation['phase_id'] != p['phase_id']
                    or operation['action'] != GIT_REQUEST_ACTIONS.get(p['phase_id'])
                    or operation['status'] != 'pending'):
                raise StateError('Git request claim requires the current pending lifecycle delivery')
            prefix = f"operations/{p['operation_id']}/git-request-claim/"
            if any(ref['path'].startswith(prefix) for ref in operation['receipt_artifacts']):
                raise StateError('This delivery operation already claimed a Git request')
            operation['receipt_artifacts'].extend(deepcopy(refs))
            s['active_mutation'] = deepcopy(operation)
        elif kind == 'mutation_observed':
            if p['outcome'] not in ('applied', 'not_applied', 'unknown'):
                raise StateError('Generic mutation observation cannot create a cleanup partial state')
            active = s['active_mutation']
            if active is None or active['operation_id'] != p['operation_id']:
                raise StateError('Mutation observation does not match the pending operation')
            operation = next(o for o in s['operations'] if o['operation_id'] == p['operation_id'])
            operation['status'] = p['outcome']
            operation['receipt_artifacts'].extend(p['receipt_artifacts'])
            if p['outcome'] == 'unknown':
                s['execution_status'] = 'reconciliation_required'
                s['active_mutation'] = deepcopy(operation)
            else:
                s['active_mutation'] = None
                s['execution_status'] = 'ready'
        elif kind == 'native_requested':
            if 'lifecycle' in s:
                active_phase = s['lifecycle']['active_phase']
                phase = next((x for x in s['lifecycle']['phases'] if x['id'] == p['step_id']), None)
                if (active_phase is None or active_phase['phase_id'] != p['step_id'] or phase is None
                        or phase['operation'] != 'native' or phase['workflow'] != p['workflow']):
                    raise StateError('Native request must match the active lifecycle phase')
            if s['execution_status'] != 'ready' or any(r['step_id'] == p['step_id'] for r in s['native_requests']):
                raise StateError('Native step is not available or has already been requested')
            s['active_native'] = p
            s['native_requests'].append(p)
            s['execution_status'] = 'awaiting_native_step'
        elif kind == 'native_validated':
            if (s['active_native'] is None or p['request_id'] != s['active_native']['request_id']
                    or p['step_id'] != s['active_native']['step_id']):
                raise StateError('Native result does not match the pending request')
            s['native_results'].append(p)
            s['active_native'] = None
            s['execution_status'] = 'ready' if p['status'] == 'completed' else 'native_incomplete'
        elif kind == 'native_cancelled':
            if (s['active_native'] is None or p['request_id'] != s['active_native']['request_id']
                    or p['step_id'] != s['active_native']['step_id']):
                raise StateError('Native cancellation does not match the pending request')
            s['native_cancellations'].append({**p, 'cancelled_at': event['time']})
            s['active_native'] = None
            s['execution_status'] = 'ready'
        else:
            raise StateError('Unsupported journal event type')
        s['updated_at'] = event['time']
        s['journal_seq'] = event['seq']
        s['journal_head_sha256'] = event_hash
        return s

    def _reduce_lifecycle(self, state, kind, payload, timestamp):
        """Phase progress shares the authoritative journal with effect ownership."""
        if kind == 'lifecycle_initialized':
            if ('lifecycle' in state or state['operations'] or state['native_requests']
                    or state['execution_status'] != 'ready'):
                raise StateError('Lifecycle initialization requires a fresh run')
            if set(payload['inputs']) != {key.removesuffix('_sha256') for key in IDENTITY_KEYS}:
                raise StateError('Lifecycle requires all six frozen inputs')
            if any(ref['sha256'] != self.identities[name + '_sha256']
                   for name, ref in payload['inputs'].items()):
                raise StateError('Lifecycle input identity changed')
            from .contracts import validate_document
            scenario = _json(_read(self._artifact_path(payload['inputs']['scenario']['path']), MAX_DOCUMENT_BYTES))
            validate_document(scenario, 'factory-scenario')
            canonical_root = Path(__file__).resolve().parents[3] / 'demo/software-factory/scenarios/ironbank-mission-recovery-v1'
            assertions = _json(_read(self._artifact_path(payload['inputs']['assertions']['path']), MAX_DOCUMENT_BYTES))
            if (scenario != _json(_read(canonical_root / 'scenario.json', MAX_DOCUMENT_BYTES))
                    or assertions != _json(_read(canonical_root / 'assertions.json', MAX_DOCUMENT_BYTES))):
                raise StateError('Lifecycle requires the installed canonical scenario and assertions')
            if payload['phases'] != scenario['phases']:
                raise StateError('Lifecycle phase graph differs from its frozen scenario')
            if 'auxiliary_inputs' in payload:
                if type(payload['auxiliary_inputs']) is not dict:
                    raise StateError('Invalid auxiliary reference mapping')
                names = set(payload['auxiliary_inputs'])
                if (not names or not names <= AUXILIARY_NAMES
                        or (names & AUXILIARY_MISSION_NAMES and not AUXILIARY_MISSION_NAMES <= names)
                        or payload['driver_id'] != 'owned-linux' or payload['execution_mode'] != 'operator'):
                    raise StateError('Invalid closed owned Linux auxiliary selection')
            state['lifecycle'] = {**payload, 'active_phase': None, 'phase_records': [],
                                  'abort': None, 'final_result': None}
            return
        lifecycle = state.get('lifecycle')
        if lifecycle is None or lifecycle['final_result'] is not None:
            raise StateError('Lifecycle is unavailable or already finalized')
        phases, records = lifecycle['phases'], lifecycle['phase_records']
        if kind == 'phase_started':
            done = {r['phase_id'] for r in records}
            remaining = [p for p in phases if p['id'] not in done]
            if lifecycle['abort']:
                remaining = [p for p in remaining if p['operation'] in ('export', 'down', 'finalize')]
            if (lifecycle['active_phase'] is not None or not remaining
                    or remaining[0]['id'] != payload['phase_id']
                    or state['active_mutation'] is not None or state['active_native'] is not None):
                raise StateError('Lifecycle predecessor or outstanding work prevents phase start')
            if lifecycle['abort'] is None and any(r['outcome'] != 'completed' for r in records):
                raise StateError('Failed phase must be retained before advancing the lifecycle')
            if remaining[0]['operation'] == 'finalize' and state['execution_status'] != 'released':
                raise StateError('Observed cleanup is required before finalization')
            lifecycle['active_phase'] = {'phase_id': payload['phase_id'], 'started_at': timestamp}
        elif kind == 'phase_finished':
            active = lifecycle['active_phase']
            if (active is None or active['phase_id'] != payload['phase_id']
                    or state['active_mutation'] is not None or state['active_native'] is not None):
                raise StateError('Phase completion requires resolved native and mutation work')
            if not payload['artifacts']:
                raise StateError('Phase completion requires immutable evidence')
            phase = next(p for p in phases if p['id'] == payload['phase_id'])
            assertion_doc = _json(_read(self._artifact_path(lifecycle['inputs']['assertions']['path']), MAX_DOCUMENT_BYTES))
            seen = set()
            for check in payload['assertions']:
                key = (check['case_id'], check['assertion_id'])
                if (check['case_id'] not in phase['case_ids']
                        or check['assertion_id'] not in assertion_doc['cases'][check['case_id']] or key in seen):
                    raise StateError('Phase assertion is outside its canonical contract')
                seen.add(key)
            if payload['outcome'] == 'completed' and any(c['status'] != 'pass' for c in payload['assertions']):
                raise StateError('A completed phase cannot contain failed or unknown assertions')
            records.append({**active, **payload, 'finished_at': timestamp})
            lifecycle['active_phase'] = None
        elif kind == 'lifecycle_aborted':
            if lifecycle['abort'] is None:
                if payload['phase_id'] not in {p['id'] for p in phases}:
                    raise StateError('Abort must identify a declared phase')
                lifecycle['abort'] = payload
                if state['execution_status'] == 'native_incomplete':
                    state['execution_status'] = 'ready'
            elif lifecycle['abort'] != payload:
                raise StateError('The original lifecycle failure is immutable')
        elif kind == 'lifecycle_finalized':
            by_operation = {p['operation']: next((r for r in records if r['phase_id'] == p['id']), None)
                            for p in phases if p['operation'] in ('export', 'down', 'finalize')}
            if (state['execution_status'] != 'released' or not state['cleanup_validation']
                    or lifecycle['active_phase'] is not None
                    or any(record is None or record['outcome'] != 'completed'
                           for record in by_operation.values())):
                raise StateError('Export and observed cleanup must complete before a terminal result')
            if payload['result']['canonical_success'] and (
                    lifecycle['execution_mode'] != 'operator' or lifecycle['abort'] is not None
                    or len(records) != len(phases)
                    or any(record['outcome'] != 'completed' for record in records)):
                raise StateError('Incomplete or controlled execution cannot claim canonical success')
            if payload['result'] != self._derive_lifecycle_result(lifecycle):
                raise StateError('Terminal result must be derived from retained phase evidence')
            lifecycle['final_result'] = payload['result']

    def _derive_lifecycle_result(self, lifecycle):
        assertions = _json(_read(self._artifact_path(lifecycle['inputs']['assertions']['path']), MAX_DOCUMENT_BYTES))
        phases = {p['id']: p for p in lifecycle['phases']}
        observed, failed = set(), False
        for record in lifecycle['phase_records']:
            attempt = phases[record['phase_id']]['attempt']
            for check in record['assertions']:
                if check['status'] == 'pass':
                    observed.add((attempt, check['case_id'], check['assertion_id']))
                failed |= check['status'] == 'fail'
        required = {(attempt, case, check) for case, checks in assertions['cases'].items()
                    for attempt in (('primary', 'replay') if case in ('D1', 'D2') else ('shared',)) for check in checks}
        missing = sorted(':'.join(item) for item in required - observed)
        abort = lifecycle['abort']
        assertion_failed = failed or abort is not None and abort['outcome'] == 'assertion-failure'
        status = 'error' if abort and abort['outcome'] in ('error', 'interrupted') else 'incomplete'
        assertion_status = 'fail' if assertion_failed else 'unknown'
        if assertion_failed:
            status = 'complete'
        canonical_success = (not missing and not failed and abort is None
                             and lifecycle['execution_mode'] == 'operator'
                             and len(lifecycle['phase_records']) == 33
                             and all(r['outcome'] == 'completed' for r in lifecycle['phase_records']))
        if canonical_success:
            status, assertion_status = 'complete', 'pass'
        return {'execution_status': status, 'scenario_assertion_status': assertion_status,
                'canonical_success': canonical_success, 'execution_mode': lifecycle['execution_mode'],
                'terminal': True, 'exit_code': 0 if canonical_success else 1 if assertion_failed else 2,
                'original_failure': deepcopy(abort), 'missing_assertions': missing}

    def initialize_lifecycle(self, *, inputs: Mapping[str, bytes], execution_mode: str, driver_id: str,
                             auxiliary_inputs: Mapping[str, bytes] | None = None):
        """Freeze independently supplied bytes; callers validate canonical contracts first."""
        _identifier(driver_id)
        if execution_mode not in ('operator', 'controlled-test'):
            raise StateError('Unsupported lifecycle execution mode')
        auxiliary = {} if auxiliary_inputs is None else auxiliary_inputs
        if (type(auxiliary) is not dict or not set(auxiliary) <= AUXILIARY_NAMES
                or (set(auxiliary) & AUXILIARY_MISSION_NAMES and not AUXILIARY_MISSION_NAMES <= set(auxiliary))
                or any(not isinstance(raw, bytes) or len(raw) > 64000 for raw in auxiliary.values())
                or (auxiliary and (driver_id != 'owned-linux' or execution_mode != 'operator'))):
            raise StateError('Explicit closed bounded owned Linux auxiliary inputs are required')
        expected = {key.removesuffix('_sha256') for key in IDENTITY_KEYS}
        if (set(inputs) != expected or any(not isinstance(value, bytes) for value in inputs.values())
                or any(len(value) > MAX_ARTIFACT_BYTES for value in inputs.values())
                or sum(map(len, inputs.values())) > MAX_ARTIFACT_TOTAL_BYTES
                or any(_sha(raw) != self.identities[name + '_sha256'] for name, raw in inputs.items())):
            raise StateError('Lifecycle inputs do not match the six bounded identities')
        with self._run_lock():
            self._load()
            existing = self._state.get('lifecycle')
            if existing is not None:
                if existing['execution_mode'] != execution_mode or existing['driver_id'] != driver_id:
                    raise StateError('Lifecycle driver or execution mode changed')
                retained = existing.get('auxiliary_inputs', {})
                if (set(retained) != set(auxiliary) or any(
                        _read(self._artifact_path(retained[name]['path']), 64000) != raw
                        for name, raw in auxiliary.items())):
                    raise StateError('Explicit lifecycle auxiliary selection changed or is missing')
                return
            directory = self.run_dir / ('lifecycle-' + uuid4().hex)
            _private_directory(directory, new=True)
            refs = {}
            for name, raw in sorted(inputs.items()):
                path = directory / name
                _exclusive(path, raw)
                refs[name] = self._reference(path, raw)
            auxiliary_refs = {}
            for name, raw in sorted(auxiliary.items()):
                path = directory / name
                _exclusive(path, raw)
                auxiliary_refs[name] = self._reference(path, raw)
            scenario = _json(inputs['scenario'])
            payload = {'inputs': refs, 'phases': scenario['phases'],
                       'execution_mode': execution_mode, 'driver_id': driver_id}
            if auxiliary_refs:
                payload['auxiliary_inputs'] = auxiliary_refs
            self._commit('lifecycle_initialized', payload)

    def begin_phase(self, phase_id: str):
        if not self._environment_locked and not self._lifecycle_locked:
            raise StateError('Lifecycle phase progress requires the environment lock')
        _identifier(phase_id)
        with self._run_lock():
            self._load()
            self._commit('phase_started', {'phase_id': phase_id})

    def finish_phase(self, phase_id: str, outcome: str, code: str, *, receipt_artifacts=(), assertions=()):
        if not self._environment_locked and not self._lifecycle_locked:
            raise StateError('Lifecycle phase progress requires the environment lock')
        _identifier(phase_id)
        _identifier(code)
        if outcome not in ('completed', 'incomplete', 'error', 'assertion-failure', 'interrupted'):
            raise StateError('Unsupported phase outcome')
        with self._run_lock():
            self._load()
            directory = self.run_dir / ('phase-' + uuid4().hex)
            refs = self._freeze_many(receipt_artifacts, directory, max_artifacts=MAX_RESULT_ARTIFACTS)
            self._commit('phase_finished', {'phase_id': phase_id, 'outcome': outcome, 'code': code,
                                           'artifacts': refs, 'assertions': deepcopy(list(assertions))})

    def abort_lifecycle(self, phase_id: str, outcome: str, code: str):
        if not self._environment_locked and not self._lifecycle_locked:
            raise StateError('Lifecycle phase progress requires the environment lock')
        _identifier(phase_id)
        _identifier(code)
        if outcome not in ('incomplete', 'error', 'assertion-failure', 'interrupted'):
            raise StateError('Unsupported lifecycle abort outcome')
        with self._run_lock():
            self._load()
            if self._state.get('lifecycle', {}).get('abort') is not None:
                return  # Never replace the original failure with a cleanup error.
            self._commit('lifecycle_aborted', {'phase_id': phase_id, 'outcome': outcome, 'code': code})

    def finalize_lifecycle(self):
        if not self._environment_locked:
            raise StateError('Lifecycle finalization requires the environment lock')
        with self._run_lock():
            self._load()
            self._commit('lifecycle_finalized', {'result': self._derive_lifecycle_result(self._state['lifecycle'])})

    def current_mutation(self, phase_id: str, action: str, *, reconciliation=False) -> str:
        """Return the locked current operation, never a caller-supplied receipt ID.

        A normal dispatch must be owned by this exact journal object and PID.
        Read-only reconciliation may observe a reopened pending operation; it
        does not resolve it, authorize another write, or release the environment.
        """
        if (not self._environment_locked or os.getpid() != self._process_id
                or type(reconciliation) is not bool):
            raise StateError('Current operation requires this process and environment lock')
        if self._has_validation_read_lock():
            self._load()
            return self._current_mutation_locked(phase_id, action, reconciliation=reconciliation)
        with self._run_lock():
            self._load()
            return self._current_mutation_locked(phase_id, action, reconciliation=reconciliation)

    def _current_mutation_locked(self, phase_id: str, action: str, *, reconciliation=False) -> str:
        """Check the current operation after the caller locks and reloads this journal."""
        if (not self._environment_locked or os.getpid() != self._process_id
                or type(reconciliation) is not bool):
            raise StateError('Current operation requires this process and environment lock')
        active = self._state['active_mutation']
        lifecycle = self._state.get('lifecycle')
        if (active is None or active['phase_id'] != phase_id or active['action'] != action
                or (lifecycle is not None and
                    (lifecycle['active_phase'] or {}).get('phase_id') != phase_id)):
            raise StateError('Current operation does not match the active phase and action')
        if not reconciliation and (active['operation_id'] not in self._owned_operations
                or self._state['execution_status'] != 'mutation_pending'):
            raise StateError('A reopened or ambiguous mutation requires reconciliation')
        return active['operation_id']

    @staticmethod
    def _git_request_claim_refs(operation_id: str, refs: list) -> list:
        """Closed reference slots for local claim evidence, never write authority."""
        _identifier(operation_id)
        names = ('request.body', 'native-request.json', 'claim.json')
        prefix = f'operations/{operation_id}/git-request-claim/'
        if (type(refs) is not list or len(refs) != len(names)
                or any(type(ref) is not dict or set(ref) != {'path', 'sha256', 'bytes'}
                       or ref['path'] != prefix + name
                       or not isinstance(ref['sha256'], str) or not _HASH.fullmatch(ref['sha256'])
                       or type(ref['bytes']) is not int or not 0 < ref['bytes'] <= MAX_ARTIFACT_BYTES
                       for name, ref in zip(names, refs))):
            raise StateError('Git request claim requires exactly three fixed artifact references')
        return refs

    def _record_git_request_claim_locked(self, phase_id: str, operation_id: str, refs: list):
        """Register already published claim evidence under the caller's run lock.

        The caller reserves and burns the attempt before any publication. This
        records no remote observation and keeps the delivery pending.
        """
        if not self._lifecycle_locked:
            raise StateError('Git request claim requires the lifecycle execution lock')
        if (phase_id not in GIT_REQUEST_ACTIONS
                or self._current_mutation_locked(phase_id, GIT_REQUEST_ACTIONS[phase_id]) != operation_id):
            raise StateError('Git request claim does not match the owned delivery operation')
        refs = self._git_request_claim_refs(operation_id, refs)
        self._verify_references(refs)
        self._commit('git_request_claimed', {'operation_id': operation_id, 'phase_id': phase_id,
                                           'receipt_artifacts': deepcopy(refs)})

    def begin_mutation(self, phase_id: str, action: str, *, intent_artifacts=(), continues_operation=None, cleanup_mode=None) -> str:
        if not self._environment_locked:
            raise StateError('Mutation requires the environment lock')
        _identifier(phase_id)
        _identifier(action)
        if cleanup_mode is not None and (cleanup_mode != PARENT_DISPOSAL_MODE
                or action != 'down' or continues_operation is None):
            raise StateError('Explicit parent disposal requires a new cleanup continuation')
        with self._run_lock():
            self._load()
            if self._state['execution_status'] != 'ready':
                raise StateError('Pending work requires reconciliation before another mutation')
            lifecycle = self._state.get('lifecycle')
            if lifecycle is not None:
                active = lifecycle['active_phase']
                phase = next((p for p in lifecycle['phases'] if p['id'] == phase_id), None)
                if (phase is not None and phase['operation'] == 'probe'
                        and lifecycle['driver_id'] == 'owned-linux'
                        and 'owned_runtime_recipe' in lifecycle.get('auxiliary_inputs', {})):
                    raise StateError('Owned runtime probe phase only observes previously admitted actors')
                expected_action = ('platform-dependency' if phase_id == 'platform-dependency'
                                   else phase['operation'] if phase is not None else None)
                if (active is None or active['phase_id'] != phase_id
                        or expected_action not in ('prepare', 'up', 'build-promote', 'inject', 'deliver',
                                                   'probe', 'platform-dependency', 'reset', 'down')
                        or action != expected_action):
                    raise StateError('Read-only or inactive lifecycle phases cannot record external mutations')
            operation_id = str(uuid4())
            directory = self.run_dir / 'operations' / operation_id
            _private_directory(directory, new=True)
            refs = self._freeze_many(intent_artifacts, directory / 'intent')
            operation = {'operation_id': operation_id, 'phase_id': phase_id, 'action': action,
                         'status': 'pending', 'intent_artifacts': refs, 'receipt_artifacts': []}
            if continues_operation is not None:
                prior = next((o for o in reversed(self._state['operations']) if o['action'] == 'down'), None)
                if (action != 'down' or prior is None or prior['operation_id'] != continues_operation
                        or prior['status'] != 'partial' or prior['phase_id'] != phase_id):
                    raise StateError('Cleanup continuation requires the latest terminal partial operation')
                mode = 'normal' if cleanup_mode is None else cleanup_mode
                if mode not in prior.get('cleanup_continuation_modes', ['normal']):
                    raise StateError('Cleanup mode is not admitted by the partial predecessor')
                operation['continues_operation'] = continues_operation
                if cleanup_mode is not None:
                    operation['cleanup_mode'] = cleanup_mode
            elif action == 'down'  and any(o['action'] == 'down' for o in self._state['operations']):
                raise StateError('Repeated cleanup requires explicit continuation')
            self._write_owner(operation)
            self._commit('mutation_started', operation)
            self._owned_operations.add(operation_id)
            return operation_id

    def _observe_mutation(self, operation_id: str, outcome: str, receipt_artifacts):
        if not self._environment_locked:
            raise StateError('Mutation observation requires the environment lock')
        if outcome not in ('applied', 'not_applied', 'unknown') or not receipt_artifacts:
            raise StateError('Reconciliation requires an explicit outcome and observation artifacts')
        active = self._state['active_mutation']
        if active is None or active['operation_id'] != operation_id:
            raise StateError('Mutation observation does not match the pending operation')
        refs = self._freeze_many(receipt_artifacts,
                                self.run_dir / 'operations' / operation_id / ('observation-' + uuid4().hex),
                                max_artifacts=MAX_RESULT_ARTIFACTS)
        self._commit('mutation_observed', {'operation_id': operation_id, 'outcome': outcome,
                                          'receipt_artifacts': refs})
        if outcome != 'unknown':
            self._write_owner(None)
        self._owned_operations.discard(operation_id)

    def finish_mutation(self, operation_id: str, *, receipt_artifacts=()):
        with self._run_lock():
            self._load()
            if (os.getpid() != self._process_id or operation_id not in self._owned_operations
                    or self._state['execution_status'] != 'mutation_pending'):
                raise StateError('A reopened or ambiguous mutation requires reconciliation')
            self._observe_mutation(operation_id, 'applied', receipt_artifacts)

    def reconcile_mutation(self, operation_id: str, outcome: str, *, receipt_artifacts=()):
        with self._run_lock():
            self._load()
            self._observe_mutation(operation_id, outcome, receipt_artifacts)

    def reconcile_partial_cleanup(self, operation_id: str, *, receipt_artifacts=(), validator: Callable):
        """Record terminal partial effects, retaining reservation and cleanup phase.

        The fixed resident validator proves terminal effects, or bounds only guest-local
        uncertainty under original parent ownership and revoked registration. The
        latter permits only explicit parent disposal; neither case releases the environment.
        """
        if (not self._environment_locked or not self._lifecycle_locked
                or os.getpid() != self._process_id or not callable(validator)):
            raise StateError('Partial cleanup requires original process, locks and resident validation')
        with self._run_lock():
            self._load()
            active = self._state['active_mutation']
            if (active is None or active['operation_id'] != operation_id or active['action'] != 'down'
                    or not self._state['environment_reserved'] or not receipt_artifacts):
                raise StateError('Partial cleanup requires the exact outstanding down operation')
            directory = self.run_dir / ('cleanup-partial-' + uuid4().hex)
            refs = self._freeze_many(receipt_artifacts, directory, max_artifacts=MAX_RESULT_ARTIFACTS)
            with self._validation_reads():
                checks = validator(tuple(_read(self._artifact_path(ref['path']), MAX_ARTIFACT_BYTES)
                                         for ref in refs))
            if not isinstance(checks, (list, tuple)) or not checks or len(checks) > 100:
                raise StateError('Partial cleanup validator must return completed checks')
            for check in checks:
                _identifier(check)
            self._verify_references(refs)
            modes = _cleanup_modes(list(checks))
            raw = _canonical({'schema_version': 1, 'run_id': self._state['run_id'],
                'operation_id': operation_id, 'effect': 'partial', 'checks': list(checks),
                'coverage': ('original-resident-terminal-partial-cleanup' if modes == ['normal']
                    else 'original-owned-parent-contained-guest-residual'), 'validated_at': self._now()})
            path = directory / 'validation.json'
            _exclusive(path, raw)
            payload = {'operation_id': operation_id,
                'receipt_artifacts': refs, 'validation': self._reference(path, raw)}
            if modes != ['normal']:
                payload['cleanup_continuation_modes'] = modes
            self._commit('cleanup_partially_reconciled', payload)
            self._owned_operations.discard(operation_id)
            self._write_owner(None)

    def complete_cleanup(self, operation_id: str, *, receipt_artifacts=(), validator: Callable,
                         reconciliation=False, audit_artifacts=()):
        """Validate complete cleanup before atomically resolving down and releasing.

        A failed validator, frozen receipt check or pre-append publication leaves
        the original down outstanding. A durable completion event recovers both
        transitions together, never a resolved down with an unreleased owner.
        This observes trusted cleanup evidence; it performs no resource deletion.
        """
        if (not self._environment_locked or os.getpid() != self._process_id
                or not callable(validator) or type(reconciliation) is not bool):
            raise StateError('Cleanup completion requires this process, lock and trusted validator')
        with self._run_lock():
            self._load()
            state = self._state
            active = state['active_mutation']
            if (active is None or active['operation_id'] != operation_id or active['action'] != 'down'
                    or not state['environment_reserved'] or state['active_native'] is not None
                    or state['execution_status'] not in ('mutation_pending', 'reconciliation_required')
                    or not receipt_artifacts):
                raise StateError('Cleanup completion requires the exact outstanding down operation and receipts')
            if not reconciliation and (operation_id not in self._owned_operations
                                      or state['execution_status'] != 'mutation_pending'):
                raise StateError('A reopened or ambiguous cleanup requires reconciliation')
            directory = self.run_dir / 'cleanup' / uuid4().hex
            refs = self._freeze_many([*receipt_artifacts, *audit_artifacts], directory,
                                     max_artifacts=MAX_RESULT_ARTIFACTS)
            selected = refs[:len(receipt_artifacts)]
            with self._validation_reads():
                checks = validator(tuple(_read(self._artifact_path(r['path']), MAX_ARTIFACT_BYTES)
                                         for r in selected))
            if not isinstance(checks, (list, tuple)) or not checks or len(checks) > 100:
                raise StateError('The cleanup validator must return completed check identifiers')
            for check in checks:
                _identifier(check)
            self._verify_references(refs)
            raw = _canonical({'schema_version': 1, 'run_id': state['run_id'],
                              'environment_sha256': self.identities['environment_sha256'],
                              'operation_id': operation_id, 'coverage': 'cleanup_receipt_validation',
                              'checks': list(checks), 'validated_at': self._now()})
            path = directory / 'validation.json'
            _exclusive(path, raw)
            self._commit('cleanup_completed', {'operation_id': operation_id,
                         'receipt_artifacts': selected, 'audit_artifacts': refs[len(selected):],
                         'validation': self._reference(path, raw)})
            self._owned_operations.discard(operation_id)
            self._finish_cleanup_release(operation_id)

    def _finish_cleanup_release(self, operation_id):
        # Called only with the run and environment locks held. Revalidate the
        # precise durable transition and owner envelope before touching a marker.
        state = self._state
        operation = next((o for o in state['operations'] if o['operation_id'] == operation_id), None)
        if (not self._environment_locked or os.getpid() != self._process_id
                or self._owner_path is None or state['execution_status'] != 'released'
                or state['environment_reserved'] or state['active_mutation'] is not None
                or state['cleanup_validation'] is None or operation is None
                or operation['action'] != 'down' or operation['status'] != 'applied'):
            raise StateError('Cleanup owner release requires the exact durable completed down')
        validation = _json(_read(self._artifact_path(state['cleanup_validation']['path']), MAX_ARTIFACT_BYTES))
        if validation.get('operation_id') != operation_id:
            raise StateError('Cleanup validation belongs to another operation')
        expected = {'schema_version': 1, 'run_id': state['run_id'], 'run_dir': str(self.run_dir),
                    'identities': self.identities,
                    'operation': {**operation, 'status': 'pending', 'receipt_artifacts': []}}
        try:
            owner = _json(_read(_safe_path(self._owner_path), MAX_DOCUMENT_BYTES))
        except StateError as error:
            if not isinstance(error.__cause__, FileNotFoundError):
                raise
            # A prior release may have unlinked it before fsync failed.
        else:
            if owner != expected:
                raise StateError('Cleanup owner marker differs from the completed operation')
            self._owner_path.unlink()
        _fsync_directory(self._owner_path.parent)

    def finish_cleanup_release(self, operation_id):
        """Finish only the same owner's marker publication after durable cleanup."""
        with self._run_lock():
            self._load()
            self._finish_cleanup_release(operation_id)

    def release_environment(self, *, receipt_artifacts=(), validator: Callable):
        """Release the run's reservation after a trusted cleanup adapter validates
        actual ownership/removal observations. This does not execute cleanup.
        ``validator`` receives a tuple of frozen receipt bytes and returns check IDs.
        A native handoff or unresolved write cannot use this path.
        """
        if not self._environment_locked or not callable(validator):
            raise StateError('Environment release requires the lock and a cleanup validator')
        with self._run_lock():
            self._load()
            if self._state['execution_status'] == 'released':
                return  # Recovery of a previously recorded cleanup is idempotent.
            if (not self._state['environment_reserved'] or not receipt_artifacts
                    or self._state['execution_status'] not in ('ready', 'native_incomplete')):
                raise StateError('Outstanding work or missing observations prevent environment release')
            directory = self.run_dir / 'cleanup' / uuid4().hex
            refs = self._freeze_many(receipt_artifacts, directory, max_artifacts=MAX_RESULT_ARTIFACTS)
            with self._validation_reads():
                checks = validator(tuple(_read(self._artifact_path(r['path']), MAX_ARTIFACT_BYTES) for r in refs))
            if not isinstance(checks, (list, tuple)) or not checks or len(checks) > 100:
                raise StateError('The cleanup validator must return completed check identifiers')
            for check in checks:
                _identifier(check)
            self._verify_references(refs)
            raw = _canonical({'schema_version': 1, 'run_id': self._state['run_id'],
                              'environment_sha256': self.identities['environment_sha256'],
                              'coverage': 'cleanup_receipt_validation', 'checks': list(checks),
                              'validated_at': self._now()})
            path = directory / 'validation.json'
            _exclusive(path, raw)
            self._commit('environment_released', {'receipt_artifacts': refs,
                                                 'validation': self._reference(path, raw)})
            self._owner_path.unlink()
            _fsync_directory(self._owner_path.parent)

    def request_native(self, step_id: str, workflow: str, *, input_artifacts: Mapping[str, Path] | None = None,
                       ttl_seconds: int = 900, expires_at: str | None = None) -> dict:
        _identifier(step_id)
        if workflow not in WORKFLOWS or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise StateError('Unsupported native workflow or expiry interval')
        input_artifacts = input_artifacts or {}
        if len(input_artifacts) > 64:
            raise StateError('Too many native inputs')
        with self._run_lock():
            self._load()
            if self._state['execution_status'] != 'ready':
                raise StateError('Pending work prevents a native request')
            directory = self.run_dir / 'native' / step_id
            _private_directory(directory, new=True)
            for name in ('inputs', 'output'):
                _private_directory(directory / name, new=True)
            inputs, total = [], 0
            for name, source in sorted(input_artifacts.items()):
                _identifier(name)
                item = self._freeze(Path(source), directory / 'inputs' / name,
                                    budget=MAX_ARTIFACT_TOTAL_BYTES - total)
                inputs.append({'name': name, **item})
                total += item['bytes']
            created = self._now()
            expiry = _time(created) + timedelta(seconds=ttl_seconds)
            if expires_at is not None:
                expiry = min(expiry, _time(expires_at))
            if expiry <= _time(created):
                raise StateError('Native request authorization already expired')
            request = {'schema_version': 1, 'request_id': str(uuid4()), 'run_id': self._state['run_id'],
                       'step_id': step_id, 'workflow': workflow, 'identities': self.identities,
                       'created_at': created, 'expires_at': expiry.isoformat(),
                       'required_artifact_kind': WORKFLOWS[workflow], 'input_artifacts': inputs,
                       'result_path': str((directory / 'result.json').relative_to(self.run_dir)),
                       'output_directory': str((directory / 'output').relative_to(self.run_dir))}
            _validate('native-request', request)
            raw = _canonical(request)
            _exclusive(directory / 'request.json', raw)
            reference = self._reference(directory / 'request.json', raw)
            self._commit('native_requested', {'request_id': request['request_id'], 'step_id': step_id,
                         'workflow': workflow, 'expires_at': request['expires_at'], 'request': reference,
                         'input_artifacts': inputs})
            return {'execution_status': 'awaiting_native_step', 'terminal': False, 'exit_code': 3,
                    'request_path': str(directory / 'request.json'), 'request_sha256': reference['sha256'],
                    'result_path': str(directory / 'result.json')}

    def cancel_native(self, reason_code: str):
        """Cancel a suspended/expired request without deleting it or asserting success.
        This enables separately authorized cleanup. Mutation reconciliation is
        unaffected; there is no cancellation path for an unresolved remote write.
        """
        _identifier(reason_code)
        with self._run_lock():
            self._load()
            active = self._state['active_native']
            if active is None:
                raise StateError('No native request is pending')
            self._commit('native_cancelled', {'request_id': active['request_id'], 'step_id': active['step_id'],
                                             'reason_code': reason_code})

    def accept_native(self, result_path: Path, *, identities: dict, validator: Callable) -> dict:
        """Validate the pending result and frozen output bytes without trusting prose.

        A validator exception leaves the request pending. Incomplete results are
        recorded as ``native_incomplete`` and cannot advance mutation phases.
        The same accepted result is never consumed twice.
        """
        if _identities(identities) != self.identities:
            raise StateError('Run input, package, lock, config or environment identity changed')
        if not callable(validator):
            raise StateError('A deterministic native artifact validator is required')
        with self._run_lock():
            self._load()
            active = self._state['active_native']
            if active is None:
                raise StateError('No native result is pending')
            request = _json(_read(self._artifact_path(active['request']['path']), MAX_DOCUMENT_BYTES))
            _validate('native-request', request)
            if _time(self._now()) >= _time(request['expires_at']):
                raise StateError('Native request expired; a stale result cannot resume it')
            if _time(self._now()) < _time(request['created_at']):
                raise StateError('Run clock predates the native request')
            expected_result = self._artifact_path(request['result_path'])
            if _safe_path(result_path) != expected_result:
                raise StateError('Native result must belong to the pending run and step')
            raw = _read(expected_result, MAX_DOCUMENT_BYTES)
            result = _json(raw)
            _validate('native-result', result)
            if (any(result[k] != request[k] for k in ('request_id', 'run_id', 'step_id'))
                    or result['request_sha256'] != active['request']['sha256']):
                raise StateError('Native result is stale or belongs to another request')
            names, paths, output_bytes, refs, total = set(), set(), {}, [], 0
            output_dir = self._artifact_path(request['output_directory'])
            for item in result['artifacts']:
                artifact = self._artifact_path(item['path'])
                if (not artifact.is_relative_to(output_dir) or item['name'] in names
                        or item['path'] in paths):
                    raise StateError('Native artifacts must be unique and belong to this step')
                names.add(item['name'])
                paths.add(item['path'])
                content = _read(artifact, min(MAX_ARTIFACT_BYTES, MAX_ARTIFACT_TOTAL_BYTES - total))
                total += len(content)
                if len(content) != item['bytes'] or _sha(content) != item['sha256']:
                    raise StateError('Native artifact digest or size does not match')
                output_bytes[item['name']] = content
                refs.append(dict(item))
            if sum(map(len, output_bytes.values())) > MAX_ARTIFACT_TOTAL_BYTES:
                raise StateError('Native output exceeds the aggregate size limit')
            if result['status'] == 'completed' and request['required_artifact_kind'] not in {
                    item['kind'] for item in result['artifacts']}:
                raise StateError('The required native artifact is missing')
            with self._validation_reads():
                checks = validator(deepcopy(request), deepcopy(result), MappingProxyType(output_bytes))
            if not isinstance(checks, (list, tuple)) or not checks or len(checks) > 100:
                raise StateError('The validator must return its completed check identifiers')
            for check in checks:
                _identifier(check)
            if _time(self._now()) >= _time(request['expires_at']):
                raise StateError('Native request expired during validation')
            # Re-read after validation: the receipt must describe the exact bytes
            # the trusted validator consumed, not a replacement written meanwhile.
            if _read(expected_result, MAX_DOCUMENT_BYTES) != raw:
                raise StateError('Native result changed during validation')
            for item in refs:
                if _read(self._artifact_path(item['path']), MAX_ARTIFACT_BYTES) != output_bytes[item['name']]:
                    raise StateError('Native artifact changed during validation')
            validation = {'schema_version': 1, 'request_sha256': active['request']['sha256'],
                          'result_sha256': _sha(raw), 'checks': list(checks),
                          'coverage': 'artifact_validation_only', 'scenario_assertion_status': 'not_evaluated',
                          'validated_at': self._now()}
            # A crash before the journal append may leave an unreferenced
            # validation file. Retrying never replaces that immutable attempt.
            validation_path = expected_result.parent / ('validation-' + uuid4().hex + '.json')
            validation_raw = _canonical(validation)
            _exclusive(validation_path, validation_raw)
            accepted = {'request_id': request['request_id'], 'step_id': request['step_id'],
                        'status': result['status'], 'result': self._reference(expected_result, raw),
                        'artifacts': refs, 'validation': self._reference(validation_path, validation_raw)}
            self._commit('native_validated', accepted)
            return {'execution_status': self._state['execution_status'], 'terminal': False,
                    'scenario_assertion_status': 'not_evaluated', 'validation': deepcopy(accepted['validation'])}
