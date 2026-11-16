"""Private pre-cleanup journal checkpoints, never terminal or imported authority.

Only schema-defined journal references are members. Opaque report paths and
unregistered traces are deliberately not followed. The fixed driver keeps this
context open through its installed-package and phase checks before returning.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import math
import os
from pathlib import Path
import stat
import time
from types import MappingProxyType
from uuid import uuid4
import zipfile

from .lifecycle import PhaseContext
from .state import (AUXILIARY_NAMES, MAX_ARTIFACT_BYTES, MAX_DOCUMENT_BYTES, MAX_EVENTS,
                    MAX_JOURNAL_BYTES, RunJournal, StateError, _canonical, _json, _sha)


MAX_REFERENCE_OCCURRENCES = 4096
MAX_EXPORT_BYTES = 60_000_000
PART_BYTES = 8_000_000
MAX_METADATA_BYTES = 1_000_000
_PREFIX = 'export-checkpoint-'
_MANIFEST = 'checkpoint-manifest.json'
_FIELDS = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')
# These slots are intentionally explicit; schema coverage is checked by tests.
REFERENCE_SLOTS = (
    'operations.[].intent_artifacts.[]', 'operations.[].receipt_artifacts.[]',
    'native_requests.[].request', 'native_requests.[].input_artifacts.[]',
    'native_results.[].result', 'native_results.[].artifacts.[]', 'native_results.[].validation',
    'cleanup_artifacts.[]', 'cleanup_validation',
    *(f'lifecycle.inputs.{name}' for name in ('scenario', 'assertions', 'config', 'lock', 'package', 'environment')),
    *(f'lifecycle.auxiliary_inputs.{name}' for name in sorted(AUXILIARY_NAMES)),
    'lifecycle.phase_records.[].artifacts.[]',
)


def _checkpoint(deadline):
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        raise StateError('Export requires a finite phase deadline')
    if time.monotonic() >= deadline:
        raise TimeoutError('Evidence export exceeded its phase budget')


def _plain(value):
    if type(value) in (dict, MappingProxyType):
        return {key: _plain(child) for key, child in value.items()}
    if type(value) in (tuple, list):
        return [_plain(child) for child in value]
    return value


def _identity(info):
    return info.st_dev, info.st_ino


def _close_fds(descriptors, *, failure=None):
    """Attempt each owned close once, without masking an active interruption."""
    first = None
    for fd in descriptors:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as error:
                if failure is not None:
                    failure.add_note('Export source descriptor cleanup also failed: ' + type(error).__name__)
                elif first is None:
                    first = error
                else:
                    first.add_note('Another export descriptor cleanup failed: ' + type(error).__name__)
    if failure is None and first is not None:
        raise first


class _Route:
    """Hold every directory component; writes never reopen an admitted path."""
    def __init__(self, path, deadline):
        self.nodes = []
        self.deadline = deadline
        self.path = Path(path)
        if (not self.path.is_absolute() or '..' in self.path.parts
                or str(self.path) != os.path.abspath(self.path) or len(self.path.parts) > 128):
            raise StateError('Export directory route is unsupported')
        try:
            self.nodes.append((None, os.open(self.path.anchor, self.flags)))
            for name in self.path.parts[1:]:
                _checkpoint(deadline)
                self.nodes.append((name, os.open(name, self.flags, dir_fd=self.fd)))
            self.audit()
        except BaseException as error:
            self.close(failure=error)
            raise

    @property
    def flags(self):
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    @property
    def fd(self):
        return self.nodes[-1][1]

    def audit(self):
        _checkpoint(self.deadline)
        for index, (name, fd) in enumerate(self.nodes):
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode):
                raise StateError('Export directory descriptor changed')
            observed = (os.stat(self.path.anchor, follow_symlinks=False) if index == 0 else
                        os.stat(name, dir_fd=self.nodes[index-1][1], follow_symlinks=False))
            if not stat.S_ISDIR(observed.st_mode) or _identity(observed) != _identity(info):
                raise StateError('Export directory route changed')

    def close(self, *, failure=None):
        descriptors = [fd for _, fd in reversed(self.nodes)]
        self.nodes = []
        _close_fds(descriptors, failure=failure)


def _relative(path):
    if (not isinstance(path, str) or not 1 <= len(path) <= 500 or '\\' in path
            or Path(path).is_absolute() or str(Path(path)) != path
            or any(part in ('.', '..') for part in Path(path).parts)
            or not Path(path).parts or len(Path(path).parts) > 64
            or Path(path).parts[0].startswith(_PREFIX)):
        raise StateError('Export member path is not a supported journal reference')
    return Path(path).parts


@contextmanager
def _source(root, path, deadline):
    parts = _relative(path)
    nodes, fd, failure = [], None, None
    try:
        parent = root.fd
        for name in parts[:-1]:
            _checkpoint(deadline)
            child = os.open(name, root.flags, dir_fd=parent)
            nodes.append((parent, name, child))
            parent = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)

        def audit():
            root.audit()
            for parent_fd, name, child in nodes:
                observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISDIR(observed.st_mode) or _identity(observed) != _identity(os.fstat(child)):
                    raise StateError('Export source directory route changed')
            info = os.fstat(fd)
            observed = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1
                    or _identity(info) != _identity(observed)):
                raise StateError('Export source must be an unaliased regular file')
            return info

        before = audit()
        yield fd, before
        after = audit()
        if any(getattr(before, key) != getattr(after, key) for key in _FIELDS):
            raise StateError('Export source changed during its bounded read')
    except BaseException as error:
        failure = error
        raise
    finally:
        _close_fds([fd, *(child for _, _, child in reversed(nodes))], failure=failure)


def _regular(root, path, reference, deadline, sink=None):
    _checkpoint(deadline)
    expected = reference['bytes']
    if type(expected) is not int or not 0 <= expected <= MAX_ARTIFACT_BYTES:
        raise StateError('Export source length exceeds its bound')
    with _source(root, path, deadline) as (fd, before):
        if before.st_size != expected:
            raise StateError('Export source length differs from its journal reference')
        digest, total = hashlib.sha256(), 0
        while True:
            _checkpoint(deadline)
            raw = os.read(fd, min(65536, expected-total+1))
            if not raw:
                break
            total += len(raw)
            if total > expected:
                raise StateError('Export source grew beyond its journal reference')
            digest.update(raw)
            if sink is not None:
                sink(raw)
        if total != expected or digest.hexdigest() != reference['sha256']:
            raise StateError('Export source bytes differ from their journal reference')
    _checkpoint(deadline)


def _references(state):
    result = []

    def visit(value, parts, location):
        if not parts:
            if value is not None:
                result.append(dict(value) | {'location': '.'.join(location)})
                if len(result) > MAX_REFERENCE_OCCURRENCES:
                    raise StateError('Export reference membership exceeds its bound')
            return
        name, *rest = parts
        if name == '[]':
            for index, item in enumerate(value):
                visit(item, rest, location+[str(index)])
        else:
            visit(value[name], rest, location+[name])

    for slot in REFERENCE_SLOTS:
        if (slot.startswith('lifecycle.auxiliary_inputs.')
                and slot.rsplit('.', 1)[1] not in state['lifecycle'].get('auxiliary_inputs', {})):
            continue
        visit(state, slot.split('.'), [])
    paths, blobs = {}, {}
    for ref in result:
        _relative(ref['path'])
        selected = {'path': ref['path'], 'bytes': ref['bytes'], 'sha256': ref['sha256']}
        if (ref['path'] in paths and paths[ref['path']] != selected
                or ref['sha256'] in blobs and blobs[ref['sha256']]['bytes'] != ref['bytes']):
            raise StateError('Export reference bindings conflict')
        paths[ref['path']] = selected
        blobs.setdefault(ref['sha256'], selected)
    return result, paths, blobs


def _capture(journal, context, deadline):
    _checkpoint(deadline)
    if (type(journal) is not RunJournal or journal._process_id != os.getpid()
            or type(context) is not PhaseContext or not journal._lifecycle_locked
            or not journal._environment_locked):
        raise StateError('Export requires its current locked resident lifecycle')
    state = journal.state
    lifecycle = state.get('lifecycle', {})
    phase = _plain(context.phase)
    if (not state['environment_reserved'] or state['active_mutation'] is not None
            or state['active_native'] is not None or lifecycle.get('final_result') is not None
            or lifecycle.get('driver_id') != 'owned-linux' or phase.get('id') != 'export'
            or phase.get('operation') != 'export' or phase not in lifecycle.get('phases', [])
            or (lifecycle.get('active_phase') or {}).get('phase_id') != 'export'
            or context.run_id != state['run_id'] or context.run_dir != journal.run_dir
            or _plain(context.identities) != journal.identities
            or _plain(context.input_artifacts) != lifecycle.get('inputs')
            or context.execution_mode != lifecycle.get('execution_mode')):
        raise StateError('Export scope differs from its active journal phase')
    raw = _canonical(state)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise StateError('Export state exceeds its metadata bound')
    refs, paths, blobs = _references(state)
    if sum(ref['bytes'] for ref in blobs.values()) > MAX_EXPORT_BYTES:
        raise StateError('Export sources exceed the aggregate output bound')
    _checkpoint(deadline)
    return {'state': state, 'state_raw': raw, 'references': refs, 'paths': paths, 'blobs': blobs}


def _events(capture, root, deadline):
    state = capture['state']
    if not 1 <= state['journal_seq'] <= MAX_EVENTS:
        raise StateError('Export journal sequence is unsupported')
    previous, total, events = None, 0, []
    for number in range(1, state['journal_seq']+1):
        path = f'journal/{number:08d}.json'
        with _source(root, path, deadline) as (fd, before):
            if not 0 < before.st_size <= MAX_DOCUMENT_BYTES:
                raise StateError('Export event exceeds its document bound')
            chunks, size = [], 0
            while True:
                _checkpoint(deadline)
                chunk = os.read(fd, min(65536, before.st_size-size+1))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > before.st_size:
                    raise StateError('Export journal event changed')
            raw = b''.join(chunks)
        total += len(raw)
        if len(raw) != before.st_size or total > MAX_JOURNAL_BYTES:
            raise StateError('Export journal prefix exceeds its bound or changed')
        event = _json(raw)
        if (event.get('seq') != number or event.get('previous_sha256') != previous
                or event.get('schema_version') != 1):
            raise StateError('Export event prefix differs from its authoritative chain')
        previous = _sha(raw)
        events.append((path, raw))
    if previous != state['journal_head_sha256']:
        raise StateError('Export event head differs from its captured journal')
    if (event.get('type') != 'phase_started' or event.get('payload') != {'phase_id': 'export'}
            or event.get('time') != state['lifecycle']['active_phase']['started_at']):
        raise StateError('Export checkpoint must end at its active phase start event')
    return events


def _audit_snapshot(journal, context, capture, root, deadline):
    root.audit()
    current = _capture(journal, context, deadline)
    if current['state_raw'] != capture['state_raw']:
        raise StateError('Export journal advanced or changed during its checkpoint')
    _events(capture, root, deadline)
    for path, ref in capture['paths'].items():
        _regular(root, path, ref, deadline)
    if _capture(journal, context, deadline)['state_raw'] != capture['state_raw']:
        raise StateError('Export journal advanced during its final source audit')
    root.audit()


class _Output:
    def __init__(self, root, deadline):
        self.root, self.deadline, self.fd = root, deadline, None
        self.manifest_fd = None
        self.name = _PREFIX + uuid4().hex
        self.files = {}
        root.audit()
        os.mkdir(self.name, 0o700, dir_fd=root.fd)
        try:
            self.fd = os.open(self.name, root.flags, dir_fd=root.fd)
            self.audit()
        except BaseException as error:
            self.close(failure=error)
            raise

    def audit(self):
        self.root.audit()
        observed = os.stat(self.name, dir_fd=self.root.fd, follow_symlinks=False)
        held = os.fstat(self.fd)
        if (not stat.S_ISDIR(observed.st_mode) or _identity(observed) != _identity(held)
                or held.st_uid != os.geteuid() or stat.S_IMODE(held.st_mode) & 0o077):
            raise StateError('Private export output route changed')

    def create(self, name):
        self.audit()
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        self.files[name] = _identity(os.fstat(fd))
        if name == _MANIFEST:
            # Keep the inode allocated through withdrawal; a saved dev/ino alone
            # could match an unrelated replacement after immediate inode reuse.
            try:
                self.manifest_fd = os.dup(fd)
            except BaseException as error:
                _close_fds([fd], failure=error)
                raise
        return fd

    def withdraw_manifest(self):
        owned = self.files.get(_MANIFEST)
        if owned is not None and self.fd is not None and self.manifest_fd is not None:
            try:
                if _identity(os.stat(_MANIFEST, dir_fd=self.fd, follow_symlinks=False)) == owned:
                    os.unlink(_MANIFEST, dir_fd=self.fd)
                    os.fsync(self.fd)
            except FileNotFoundError:
                pass

    def check_files(self, references):
        for ref in references:
            self.audit()
            name = ref['path']
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
            failure = None
            try:
                before = os.fstat(fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) & 0o077
                        or _identity(before) != self.files[name] or before.st_size != ref['bytes']):
                    raise StateError('Export output identity or length changed')
                digest, size = hashlib.sha256(), 0
                while True:
                    _checkpoint(self.deadline)
                    raw = os.read(fd, min(65536, ref['bytes']-size+1))
                    if not raw:
                        break
                    size += len(raw)
                    if size > ref['bytes']:
                        raise StateError('Export output exceeded its frozen length')
                    digest.update(raw)
                after = os.fstat(fd)
                if (size != ref['bytes'] or digest.hexdigest() != ref['sha256']
                        or any(getattr(before, key) != getattr(after, key) for key in _FIELDS)
                        or _identity(os.stat(name, dir_fd=self.fd, follow_symlinks=False)) != self.files[name]):
                    raise StateError('Export output bytes changed')
            except BaseException as error:
                failure = error
                raise
            finally:
                _close_fds([fd], failure=failure)
        self.audit()

    def close(self, *, failure=None):
        descriptors = (self.manifest_fd, self.fd)
        self.manifest_fd = self.fd = None
        _close_fds(descriptors, failure=failure)


class _Parts:
    def __init__(self, output):
        self.output, self.fd, self.total = output, None, 0
        self.parts = []
        self.failed = False

    def tell(self):
        return self.total

    def flush(self):
        self.output.audit()

    def write(self, raw):
        if self.failed:
            raise StateError('Failed export archive cannot accept further writes')
        if self.total + len(raw) > MAX_EXPORT_BYTES:
            raise StateError('Export archive exceeds its aggregate output bound')
        original = len(raw)
        while raw:
            self.output.audit()
            if self.fd is None:
                self.name = f'checkpoint-{len(self.parts):04d}.zip.part'
                self.fd = self.output.create(self.name)
                self.size, self.digest = 0, hashlib.sha256()
            chunk = raw[:min(65536, PART_BYTES-self.size)]
            written = os.write(self.fd, chunk)
            if written <= 0:
                raise StateError('Export archive write did not advance')
            self.digest.update(chunk[:written])
            self.size += written
            self.total += written
            raw = raw[written:]
            if self.size == PART_BYTES:
                self.finish_part()
        return original

    def finish_part(self):
        if self.fd is not None:
            os.fsync(self.fd)
            fd, self.fd = self.fd, None
            os.close(fd)
            self.parts.append({'path': self.name, 'bytes': self.size, 'sha256': self.digest.hexdigest()})

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)


def _zip_member(archive, name, size):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.compress_type = zipfile.ZIP_STORED
    info.file_size = size
    return archive.open(info, 'w')


@contextmanager
def _zip_guard(resource, parts):
    """Latch failure before ZIP close can append descriptors or a directory.

    Closing still releases the standard-library resources, but the failed
    writer refuses every byte. Preserve the original interruption/source error
    if those close operations themselves fail while unwinding.
    """
    try:
        yield resource
    except BaseException:
        parts.failed = True
        try:
            resource.close()
        except BaseException:
            pass
        raise
    else:
        try:
            resource.close()
        except BaseException:
            parts.failed = True
            raise


@contextmanager
def export_checkpoint(journal, context, *, deadline):
    """Yield bounded artifact paths while retaining the final-marker guard.

    The fixed driver performs its post-admission/package checks inside this
    context. A failed body or final audit withdraws only our manifest inode.
    The synchronous lifecycle alarm remains the hard phase deadline; these
    chunk checkpoints also respect the driver's narrower cooperative budget.
    """
    _checkpoint(deadline)
    if type(context) is not PhaseContext:
        raise StateError('Export requires a concrete phase context')
    _checkpoint(context.deadline)
    deadline = min(deadline, context.deadline)
    if (os.name != 'posix' or not all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY'))
            or not {os.open, os.stat, os.mkdir, os.unlink} <= os.supports_dir_fd):
        raise StateError('Descriptor-anchored export is unsupported on this platform')
    capture = _capture(journal, context, deadline)
    root, output, parts, failure = None, None, None, None
    try:
        root = _Route(journal.run_dir, deadline)
        info = os.fstat(root.fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise StateError('Export requires its private run directory')
        events = _events(capture, root, deadline)
        # Audit all aliases, hashes and bounds before creating an output directory.
        for path, ref in capture['paths'].items():
            _regular(root, path, ref, deadline)
        checkpoint = _canonical({'schema_version': 1, 'kind': 'factory-private-journal-checkpoint',
                                 'state': capture['state']})
        if (len(checkpoint) > MAX_METADATA_BYTES or len(checkpoint) + sum(len(raw) for _, raw in events)
                + sum(ref['bytes'] for ref in capture['blobs'].values()) > MAX_EXPORT_BYTES):
            raise StateError('Export checkpoint exceeds its bounded snapshot envelope')
        output = _Output(root, deadline)
        parts, members = _Parts(output), []
        with _zip_guard(zipfile.ZipFile(parts, 'w', compression=zipfile.ZIP_STORED,
                                       allowZip64=False), parts) as archive:
            for name, raw in [('checkpoint.json', checkpoint), *events]:
                with _zip_guard(_zip_member(archive, name, len(raw)), parts) as target:
                    target.write(raw)
                members.append({'name': name, 'bytes': len(raw), 'sha256': _sha(raw)})
            for digest, ref in sorted(capture['blobs'].items()):
                name = 'artifacts/' + digest
                with _zip_guard(_zip_member(archive, name, ref['bytes']), parts) as target:
                    _regular(root, ref['path'], ref, deadline, target.write)
                members.append({'name': name, 'bytes': ref['bytes'], 'sha256': digest})
        parts.finish_part()
        references = [{key: ref[key] for key in ('location', 'path', 'bytes', 'sha256')}
                      | {'member': 'artifacts/'+ref['sha256']} for ref in capture['references']]
        manifest = {'schema_version': 1, 'kind': 'factory-private-journal-export',
            'snapshot_status': 'complete', 'terminal': False, 'canonical_success': False,
            'membership': 'journal-registered-artifacts-only',
            'run_id': context.run_id, 'identities': dict(journal.identities),
            'execution_mode': context.execution_mode,
            'checkpoint': {'journal_seq': capture['state']['journal_seq'],
                           'journal_head_sha256': capture['state']['journal_head_sha256'],
                           'state_sha256': _sha(capture['state_raw'])},
            'archive': {'format': 'zip-stored-parts', 'bytes': parts.total, 'parts': parts.parts},
            'members': members, 'references': references,
            'exclusions': ['this-manifest', 'subsequent-export-completion', 'subsequent-cleanup',
                           'final-result', 'video-review', 'unregistered-files', 'report-embedded-paths']}
        raw = _canonical(manifest)
        if len(raw) > MAX_METADATA_BYTES or parts.total + len(raw) > MAX_EXPORT_BYTES:
            raise StateError('Export manifest exceeds its bounded snapshot envelope')
        _audit_snapshot(journal, context, capture, root, deadline)
        output.check_files(parts.parts)
        fd = output.create(_MANIFEST)
        write_failure = None
        try:
            for offset in range(0, len(raw), 65536):
                block = raw[offset:offset+65536]
                while block:
                    output.audit()
                    written = os.write(fd, block)
                    if written <= 0:
                        raise StateError('Export manifest write did not advance')
                    block = block[written:]
            os.fsync(fd)
        except BaseException as error:
            write_failure = error
            raise
        finally:
            _close_fds([fd], failure=write_failure)
        os.fsync(output.fd)
        retained = parts.parts + [{'path': _MANIFEST, 'bytes': len(raw), 'sha256': _sha(raw)}]
        output.check_files(retained)
        yield tuple(journal.run_dir/output.name/ref['path'] for ref in retained)
        _audit_snapshot(journal, context, capture, root, deadline)
        output.check_files(retained)
        _checkpoint(deadline)
    except BaseException as error:
        failure = error
        if output is not None:
            try:
                output.withdraw_manifest()
            except BaseException as cleanup_error:
                error.add_note('Export manifest withdrawal also failed: ' + type(cleanup_error).__name__)
        raise
    finally:
        close_error = None
        for resource in (parts, output, root):
            if resource is not None:
                try:
                    resource.close()
                except BaseException as error:
                    if failure is not None:
                        failure.add_note('Export descriptor cleanup also failed: ' + type(error).__name__)
                    elif close_error is None:
                        close_error = error
        if failure is None and close_error is not None:
            raise close_error
