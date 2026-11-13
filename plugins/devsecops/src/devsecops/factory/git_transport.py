"""Current prepared commit to bounded local Git transport inputs; no network."""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
from weakref import WeakKeyDictionary

from devsecops.adapters.artifacts import _open_directory
from devsecops.adapters.vulnerability_policy import checkpoint, timestamp
from devsecops.factory.deployment_commit import (
    _entries, _object_id, _oid, _output_outside_repository, _require_ordinary_repository,
    commit_configuration, require_prepared_deployment_commit,
)
from devsecops.factory.mission_security import _read_retained
from devsecops.factory.git_transport_process import run
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal

MAX_CLOSURE_OBJECTS = 256
MAX_CLOSURE_RAW = 8 * 1024 * 1024
MAX_OBJECT_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 16
MAX_TREE_ENTRIES = 20_000
MAX_NATIVE_OUTPUT = 32 * 1024 * 1024
MAX_PREPARED_PACK = 8 * 1024 * 1024
MAX_PACK = 9 * 1024 * 1024
_PLANS = WeakKeyDictionary()
_CONSUMERS = WeakKeyDictionary()
MAX_CONSUMER_FILE = 8 * 1024 * 1024
MAX_CONSUMER_TOTAL = 16 * 1024 * 1024
MAX_CONSUMER_FILES = 64
_INPUTS = {'commit-config.json': ('commit-config.json', 64000),
           'objects.pack': ('prepared.pack', MAX_PREPARED_PACK),
           'objects.idx': ('prepared.idx', 64000), 'commit.txt': ('prepared-commit.txt', 64000)}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(fd):
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _directory_at(directory, *parts):
    current = os.dup(directory)
    try:
        for part in parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        result, current = current, None
        return result
    finally:
        if current is not None:
            os.close(current)


def _snapshot_from(source, source_name, destination, name, limit, deadline):
    """Both directory routes are already held; no destination pathname is reopened."""
    checkpoint(deadline)
    fd = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise ValueError('Snapshot source size or type unsupported')
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=destination)
        total, digest = 0, hashlib.sha256()
        with os.fdopen(out, 'wb') as target:
            while chunk := stream.read(65536):
                checkpoint(deadline)
                total += len(chunk)
                if total > limit:
                    raise ValueError('Snapshot source exceeds bound')
                target.write(chunk)
                digest.update(chunk)
            target.flush()
            os.fsync(target.fileno())
            os.fchmod(target.fileno(), 0o400)
        after = os.fstat(stream.fileno())
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if total != before.st_size or any(getattr(before, key) != getattr(after, key) for key in keys):
            raise ValueError('Snapshot source changed during copy')
    checkpoint(deadline)
    return {'sha256': digest.hexdigest(), 'bytes': total}


def _snapshot_to(source, directory, name, limit, deadline):
    source_dir = _open_directory(source.parent)
    try:
        return _snapshot_from(source_dir, source.name, directory, name, limit, deadline)
    finally:
        os.close(source_dir)


class PreparedGitTransport:
    """Local transport inputs only; never a remote possession or delivery grant."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('Transport plans require a current direct prepared commit')

    def __reduce__(self):
        raise TypeError('Local transport plans cannot be serialized')

    def describe(self):
        record = _PLANS.get(self)
        if record is None:
            raise ValueError('Unissued local transport plan')
        return require_prepared_git_transport(self, **record['scope'])


def _audit_source(prepared, scope, expected, directory, deadline):
    checkpoint(deadline)
    if require_prepared_deployment_commit(prepared, **scope) != expected:
        raise ValueError('Prepared commit binding changed')
    _verify_output_path(Path(expected['output_path']), directory)
    for name, (_, maximum) in _INPUTS.items():
        ref = expected['artifacts'][name]
        if type(ref['bytes']) is not int or not 0 < ref['bytes'] <= maximum:
            raise ValueError('Prepared artifact exceeds local transport bound')
        _read_retained(directory, name, ref, deadline)


def require_prepared_git_transport(value, *, run_scope, source_commit, deployment_commit,
                                   proposal_configuration_sha256, configuration_sha256, deadline=None):
    now = time.monotonic()
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise ValueError('A finite caller audit deadline is required')
    deadline = min(now + 10, deadline) if deadline is not None else now + 10
    checkpoint(deadline)
    record = _PLANS.get(value) if type(value) is PreparedGitTransport else None
    selected = dict(run_scope=run_scope, source_commit=source_commit, deployment_commit=deployment_commit,
                    proposal_configuration_sha256=proposal_configuration_sha256,
                    configuration_sha256=configuration_sha256)
    if (record is None or record['pid'] != os.getpid() or time.monotonic() >= record['expires_monotonic']
            or selected != record['scope']):
        raise ValueError('Unissued, foreign-process, expired or mismatched local transport plan')
    original = record['prepared_binding']
    source_fd = _open_directory(Path(original['output_path']))
    try:
        if _identity(source_fd) != record['source_identity']:
            raise ValueError('Prepared artifact directory changed')
        _audit_source(record['prepared'], selected, original, source_fd, deadline)
        output_fd = _open_directory(Path(record['binding']['output_path']))
        try:
            if _identity(output_fd) != record['output_identity']:
                raise ValueError('Local transport output changed')
            for name, ref in {**record['binding']['artifacts'], 'git-transport.json': record['receipt']}.items():
                _read_retained(output_fd, name, ref, deadline)
            _verify_output_path(Path(record['binding']['output_path']), output_fd)
            _audit_source(record['prepared'], selected, original, source_fd, deadline)
        finally:
            os.close(output_fd)
    finally:
        os.close(source_fd)
    if time.monotonic() >= record['expires_monotonic']:
        raise ValueError('Local transport plan expired during audit')
    checkpoint(deadline)
    return deepcopy(record['binding'])


def _consumer_roots(record):
    """Private issuer provenance; never reconstruct protected roots from public JSON."""
    return ((Path(record['prepared_binding']['deployment_repository']), record['repo_identity'][:2]),
            (Path(record['prepared_binding']['output_path']), record['source_identity']),
            (Path(record['binding']['output_path']), record['output_identity']))


def _outside_held(directory, protected, deadline):
    """Reject any currently observed protected ancestor, including filesystem aliases."""
    current = os.dup(directory)
    try:
        for _ in range(128):
            checkpoint(deadline)
            identity = _identity(current)
            if identity in protected:
                raise ValueError('Consumer output intersects a protected source root')
            parent = os.open('..', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            if _identity(parent) == identity:
                os.close(parent)
                return
            os.close(current)
            current = parent
        raise ValueError('Consumer output ancestry exceeds its bound')
    finally:
        os.close(current)


@contextmanager
def _consumer_directory(path, protected, audit, deadline):
    """Admit existing ancestors read-only, then create only through that held parent."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    current, route = os.open(path.anchor, flags), Path(path.anchor)
    try:
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            checkpoint(deadline)
            _outside_held(current, protected, deadline)
            try:
                child = os.open(part, flags, dir_fd=current)
            except FileNotFoundError:
                break
            os.close(current)
            current, route = child, route / part
        else:
            raise ValueError('Consumer output must be a new directory')
        for part in parts[index:]:
            audit()
            _verify_output_path(route, current)
            _outside_held(current, protected, deadline)
            checkpoint(deadline)
            os.mkdir(part, 0o700, dir_fd=current)
            child = os.open(part, flags, dir_fd=current)
            os.close(current)
            current, route = child, route / part
            _verify_output_path(route, current)
            _outside_held(current, protected, deadline)
            audit()
        os.fchmod(current, 0o700)
        yield current
    finally:
        os.close(current)


def _consumer_record(value):
    record = _CONSUMERS.get(value) if type(value) is TransportConsumerOutput else None
    if record is None or record['pid'] != os.getpid():
        raise ValueError('Consumer output is not active in its issuing process')
    return record


def _audit_consumer(record):
    checkpoint(record['deadline'])
    # Check the borrowed descriptor before reopening anything that might reuse a closed fd.
    if _identity(record['borrowed']) != record['identity']:
        raise ValueError('Borrowed consumer descriptor changed')
    record['audit_roots']()
    if _identity(record['directory']) != record['identity']:
        raise ValueError('Private consumer directory changed')
    _verify_output_path(record['path'], record['directory'])
    _outside_held(record['directory'], record['protected'], record['deadline'])
    names = set()
    with os.scandir(record['directory']) as entries:
        for count, entry in enumerate(entries, 1):
            checkpoint(record['deadline'])
            names.add(entry.name)
            if count > len(record['artifacts']):
                raise ValueError('Consumer output includes unissued artifacts')
    if names != set(record['artifacts']):
        raise ValueError('Consumer output includes unissued artifacts')
    for name, ref in record['artifacts'].items():
        _read_retained(record['directory'], name, ref, record['deadline'])
    checkpoint(record['deadline'])
    if 'additional_audit' in record:
        record['additional_audit']()


def _withdraw_terminals(record):
    # This descriptor is private, separate from the borrowed read descriptor.
    for name, identity in record['terminals'].items():
        try:
            info = os.stat(name, dir_fd=record['directory'], follow_symlinks=False)
            if (info.st_dev, info.st_ino) == identity:
                os.unlink(name, dir_fd=record['directory'])
        except FileNotFoundError:
            pass
    os.fsync(record['directory'])


class TransportConsumerOutput:
    """Active local evidence writer, with no remote or delivery authority."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('Consumer outputs require issuer-owned admission')

    def __reduce__(self):
        raise TypeError('Consumer outputs cannot be serialized')

    def audit(self):
        record = _consumer_record(self)
        try:
            _audit_consumer(record)
        except BaseException:
            _withdraw_terminals(record)
            raise

    @property
    def directory_fd(self):
        """Borrowed descriptor for reads only; do not close, replace or write through it."""
        record = _consumer_record(self)
        self.audit()
        return record['borrowed']

    def terminal_identity(self, name):
        """Return an active terminal's issuer-recorded creation identity only."""
        record = _consumer_record(self)
        if type(name) is not str or name not in record['terminals'] or name not in record['artifacts']:
            raise ValueError('An exact completed terminal name from this consumer is required')
        self.audit()
        issued = record['terminals'][name]
        current = os.stat(name, dir_fd=record['directory'], follow_symlinks=False)
        if (current.st_dev, current.st_ino) != issued:
            raise ValueError('Issued terminal identity changed')
        checkpoint(record['deadline'])
        return issued

    def write_bytes(self, name, raw, *, terminal=False):
        record = _consumer_record(self)
        if (type(name) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name) is None
                or type(raw) is not bytes or type(terminal) is not bool
                or name in record['artifacts']
                or len(raw) > MAX_CONSUMER_FILE or len(record['artifacts']) >= MAX_CONSUMER_FILES
                or sum(ref['bytes'] for ref in record['artifacts'].values()) + len(raw) > MAX_CONSUMER_TOTAL):
            raise ValueError('Bounded basename, exact bytes and consumer limits are required')
        try:
            _audit_consumer(record)
            checkpoint(record['deadline'])
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=record['directory'])
            identity = _identity(fd)
            if terminal:
                record['terminals'][name] = identity
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                for offset in range(0, len(raw), 65536):
                    checkpoint(record['deadline'])
                    stream.write(raw[offset:offset + 65536])
                stream.flush()
                os.fsync(stream.fileno())
            ref = {'sha256': _sha(raw), 'bytes': len(raw)}
            record['artifacts'][name] = ref
            _audit_consumer(record)
            return deepcopy(ref)
        except BaseException:
            _withdraw_terminals(record)
            raise


@contextmanager
def open_transport_consumer_output(value, *, run_scope, source_commit, deployment_commit,
                                   proposal_configuration_sha256, configuration_sha256, output, deadline):
    """Admit local consumer evidence against private source identities and a live plan."""
    now = time.monotonic()
    if (os.name != 'posix' or sys.platform not in ('darwin', 'linux')
            or type(deadline) not in (int, float) or not math.isfinite(deadline)
            or not now < deadline <= now + 120 or not isinstance(output, Path)
            or not output.is_absolute() or '..' in output.parts
            or len(output.parts) > 64 or len(str(output)) > 4096):
        raise ValueError('Supported runtime, new absolute output and bounded future deadline required')
    scope = deepcopy(dict(run_scope=run_scope, source_commit=source_commit, deployment_commit=deployment_commit,
                          proposal_configuration_sha256=proposal_configuration_sha256,
                          configuration_sha256=configuration_sha256))
    require_prepared_git_transport(value, **scope, deadline=deadline)
    plan = _PLANS[value]
    roots = _consumer_roots(plan)
    with ExitStack() as stack:
        held = []
        for path, identity in roots:
            fd = _open_directory(path)
            stack.callback(os.close, fd)
            if _identity(fd) != identity:
                raise ValueError('Protected consumer source root changed')
            held.append((path, fd, identity))

        def audit_roots():
            checkpoint(deadline)
            require_prepared_git_transport(value, **scope, deadline=deadline)
            _require_ordinary_repository(roots[0][0], deadline, plan['repo_identity'])
            for path, fd, identity in held:
                if _identity(fd) != identity:
                    raise ValueError('Protected consumer source descriptor changed')
                _verify_output_path(path, fd)
            checkpoint(deadline)

        audit_roots()
        protected = {identity for _, identity in roots}
        directory = stack.enter_context(_consumer_directory(output, protected, audit_roots, deadline))
        borrowed = os.dup(directory)
        identity = _identity(directory)
        handle = object.__new__(TransportConsumerOutput)
        record = dict(pid=os.getpid(), directory=directory, borrowed=borrowed, identity=identity,
                      path=output, protected=protected, deadline=deadline, audit_roots=audit_roots,
                      artifacts={}, terminals={})
        _CONSUMERS[handle] = record
        try:
            _audit_consumer(record)
            yield handle
            _audit_consumer(record)
            os.fsync(directory)
            _audit_consumer(record)
        except BaseException:
            _withdraw_terminals(record)
            raise
        finally:
            _CONSUMERS.pop(handle, None)
            try:
                if _identity(borrowed) == identity:
                    os.close(borrowed)
            except OSError:
                pass


def prepare_git_transport(*, prepared, run_scope, source_commit, deployment_commit,
                          proposal_configuration_sha256, configuration_sha256, output, timeout=120):
    """Freeze complete local base closure and exact prepared objects, with no remote inputs."""
    if os.name != 'posix' or sys.platform not in ('darwin', 'linux') or not hasattr(os, 'fchdir'):
        raise ValueError('Local transport requires supported POSIX directory descriptor execution')
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 120
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('New absolute output and bounded local preparation deadline required')
    deadline = time.monotonic() + timeout
    scope = deepcopy(dict(run_scope=run_scope, source_commit=source_commit, deployment_commit=deployment_commit,
                 proposal_configuration_sha256=proposal_configuration_sha256,
                 configuration_sha256=configuration_sha256))
    source = require_prepared_deployment_commit(prepared, **scope)  # Before any I/O.
    if source['scope'] != 'local-prepared-commit-only' or set(source['artifacts']) != set(_INPUTS):
        raise ValueError('Unsupported prepared commit scope or artifact set')
    selected = source['prepared']
    if selected['parent'] != deployment_commit or selected['object_format'] != 'sha1':
        raise ValueError('Exact SHA-1 base/new commit binding required')
    repo, source_path = Path(source['deployment_repository']), Path(source['output_path'])
    source_identity = _output_outside_repository(output.resolve(), repo, deadline)
    _output_outside_repository(output.resolve(), source_path, deadline)
    repo_identity = _require_ordinary_repository(repo, deadline, source_identity=source_identity)
    source_fd = _open_directory(source_path)
    try:
        _audit_source(prepared, scope, source, source_fd, deadline)
        source_output_identity = _identity(source_fd)
        with _new_directory(output) as directory:
            retained = {}
            report = {'schema_version': 1, 'kind': 'mission-local-git-transport', 'decision': 'unknown',
                'scope': 'local-transport-preparation-only', 'factory_decision': 'unknown',
                'delivery_authority': 'not-issued', 'preparation_authority': 'not-issued',
                'checks': {'local_complete': False}, 'remote_possession': 'unverified',
                'omission_authorized': False, 'transmitted_pack': None, 'git_destination': None,
                'image_publication': {key: source[key] for key in ('origin', 'repository', 'manifest_digest')},
                'base_commit': deployment_commit, 'new_commit': selected['commit'],
                'started_at': datetime.now(timezone.utc).isoformat(), 'commands': [], 'gaps': []}
            scratch = output / 'scratch'
            os.mkdir('scratch', 0o700, dir_fd=directory)
            scratch_fd = _directory_at(directory, 'scratch')
            opened_fds = []
            tool = None
            total_native = 0

            def audit():
                _audit_source(prepared, scope, source, source_fd, deadline)
                _require_ordinary_repository(repo, deadline, repo_identity)
                _verify_output_path(output, directory)
                for name, ref in retained.items():
                    _read_retained(directory, name, ref, deadline)
                if scratch_fd is not None:
                    _verify_output_path(scratch, scratch_fd)
                    if tool is not None:
                        _read_retained(scratch_fd, 'git', tool, deadline)
                checkpoint(deadline)

            try:
                for original, (name, maximum) in _INPUTS.items():
                    audit()
                    retained[name] = _snapshot_to(source_path / original, directory, name, maximum, deadline)
                    if retained[name] != source['artifacts'][original]:
                        raise ValueError('Prepared artifact changed during freezing')
                config = commit_configuration(_read_retained(directory, 'commit-config.json',
                    retained['commit-config.json'], deadline, materialize=True))
                if (retained['commit-config.json']['sha256'] != configuration_sha256
                        or any(config[name] != expected for name, expected in (
                            ('deployment_repository', str(repo)), ('deployment_commit', deployment_commit),
                            ('proposal_configuration_sha256', proposal_configuration_sha256),
                            ('values_path', selected['values_path']), ('git_sha256', source['git']['sha256'])))):
                    raise ValueError('Local transport configuration differs from prepared authority')
                tool = _snapshot_to(Path(config['git_path']), scratch_fd, 'git', 200_000_000, deadline)
                if tool['sha256'] != config['git_sha256']:
                    raise ValueError('Approved Git binary changed')
                executable_fd = os.open('git', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=scratch_fd)
                opened_fds.append(executable_fd)
                os.fchmod(executable_fd, 0o500)
                repo_fd = _open_directory(repo)
                opened_fds.append(repo_fd)
                private_fd = None
                fixed = ['--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                    '-c', 'core.pager=cat', '-c', 'core.attributesFile=/dev/null', '-c', 'protocol.allow=never',
                    '-c', 'protocol.http.allow=never', '-c', 'protocol.https.allow=never', '-c', 'protocol.ssh.allow=never',
                    '-c', 'protocol.git.allow=never', '-c', 'protocol.file.allow=never', '-c', 'protocol.ext.allow=never',
                    '-c', 'credential.helper=', '-c', 'submodule.recurse=false', '-c', 'commit.gpgSign=false', '-c', 'gc.auto=0']
                env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C',
                    'GIT_NO_LAZY_FETCH': '1', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1'}

                def native(*args, original=False, store=None, limit=64000, data=None):
                    nonlocal total_native
                    audit()
                    limit = min(limit, MAX_NATIVE_OUTPUT - total_native)
                    if limit <= 0:
                        raise ValueError('Native Git aggregate output bound exceeded')
                    code, raw, error, receipt = run([str(scratch / 'git'), *fixed, *args],
                        cwd_fd=repo_fd if original else store if store is not None else scratch_fd if args[0] == 'init' else private_fd,
                        executable_fd=executable_fd, executable_sha256=tool['sha256'], scratch_fd=scratch_fd,
                        timeout=min(30, deadline - time.monotonic()), limit=limit, env=env,
                        stdin_bytes=data)
                    total_native += len(raw) + len(error)
                    report['commands'].append({'operation': args[0], **receipt})
                    audit()
                    if code or len(raw) > limit or total_native > MAX_NATIVE_OUTPUT:
                        raise ValueError('Bounded local Git object preparation failed')
                    return raw

                native('init', '--bare', '--quiet', '--template=', '--object-format=sha1', 'objects.git')
                private_fd = _directory_at(scratch_fd, 'objects.git')
                opened_fds.append(private_fd)
                if native('rev-parse', '--show-object-format', original=True) != b'sha1\n':
                    raise ValueError('Only SHA-1 repositories are supported')
                closure = {'base': deployment_commit, 'objects': {}, 'raw_bytes': 0, 'tree_entries': 0,
                           'ancestor_history_imported': False}

                def copy_object(kind, oid):
                    if oid in closure['objects']:
                        if closure['objects'][oid]['kind'] != kind:
                            raise ValueError('Base object kind collision')
                        return None
                    if len(closure['objects']) >= MAX_CLOSURE_OBJECTS:
                        raise ValueError('Complete base object count exceeds bound')
                    if native('cat-file', '-t', oid, original=True) != kind.encode() + b'\n':
                        raise ValueError('Base object kind differs')
                    size = native('cat-file', '-s', oid, original=True)
                    if not re.fullmatch(rb'[0-9]{1,10}\n', size):
                        raise ValueError('Invalid base object size')
                    size = int(size)
                    maximum = min(MAX_OBJECT_BYTES, 64000) if kind == 'commit' else MAX_OBJECT_BYTES
                    if size > maximum or closure['raw_bytes'] + size > MAX_CLOSURE_RAW:
                        raise ValueError('Complete base raw object bytes exceed bound')
                    raw = native('cat-file', kind, oid, original=True, limit=maximum)
                    if len(raw) != size or _object_id(kind, raw) != oid:
                        raise ValueError('Base object byte identity differs')
                    if _oid(native('hash-object', '-w', '-t', kind, '--stdin', data=raw)) != oid:
                        raise ValueError('Private copied object differs')
                    closure['objects'][oid] = {'kind': kind, 'bytes': size, 'sha256': _sha(raw)}
                    closure['raw_bytes'] += size
                    return raw

                base = copy_object('commit', deployment_commit)
                headers = base.split(b'\n\n', 1)[0].splitlines()
                if not headers or not re.fullmatch(rb'tree [a-f0-9]{40}', headers[0]):
                    raise ValueError('Selected base root tree is invalid')
                parents = [row[7:].decode('ascii') for row in headers if row.startswith(b'parent ')]
                if any(not re.fullmatch(r'[a-f0-9]{40}', oid) for oid in parents):
                    raise ValueError('Selected base parent is invalid')
                tree = headers[0][5:].decode()
                closure.update(base_tree=tree, base_parents_not_imported=parents)

                def visit(oid, depth):
                    if depth > MAX_DEPTH:
                        raise ValueError('Complete base depth exceeds bound')
                    copy_object('tree', oid)
                    listing = native('ls-tree', '-z', oid, limit=MAX_OBJECT_BYTES)
                    entries = _entries(listing, deadline) if listing else []
                    closure['tree_entries'] += len(entries)
                    if closure['tree_entries'] > MAX_TREE_ENTRIES:
                        raise ValueError('Complete base expanded tree entries exceed bound')
                    for mode, kind, child, _ in entries:
                        if mode == b'040000' and kind == b'tree':
                            visit(child.decode(), depth + 1)
                        elif mode in (b'100644', b'100755', b'120000') and kind == b'blob':
                            copy_object('blob', child.decode())
                        else:
                            raise ValueError('Gitlinks or special base tree modes are unsupported')
                visit(tree, 0)
                base_pack = native('pack-objects', '--stdout', '--window=0', '--depth=0', '--threads=1',
                    '--no-reuse-delta', '--no-reuse-object', limit=MAX_PACK,
                    data=('\n'.join(sorted(closure['objects'])) + '\n').encode())
                # Binary artifacts use private exclusive files; no JSON encoding.
                def binary(name, raw, *, target=directory):
                    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    ref = {'sha256': _sha(raw), 'bytes': len(raw)}
                    if target == directory:
                        retained[name] = ref
                    return ref
                binary('base.pack', base_pack)
                binary('base.pack', base_pack, target=private_fd)
                # The selected base intentionally references unimported parents;
                # validate the pack/index here, not ancestor connectivity.
                base_checksum = _oid(native('index-pack', '-o', 'base.idx', 'base.pack'))
                retained['base.idx'] = _snapshot_from(private_fd, 'base.idx', directory, 'base.idx', 64000, deadline)
                inventory = selected['objects']
                if not 1 <= len(inventory) <= 18 or selected['commit'] not in inventory:
                    raise ValueError('Prepared object count or new commit differs')
                if (sum(row['bytes'] for row in inventory.values()) > MAX_CLOSURE_RAW
                        or any(not re.fullmatch(r'[a-f0-9]{40}', oid) or row['kind'] not in ('commit', 'tree', 'blob')
                            or type(row['bytes']) is not int or not 0 <= row['bytes'] <= MAX_OBJECT_BYTES
                            or not re.fullmatch(r'[a-f0-9]{64}', row['sha256']) for oid, row in inventory.items())):
                    raise ValueError('Prepared object record exceeds bounds')
                imported = native('index-pack', '--stdin', '--strict', data=_read_retained(directory,
                    'prepared.pack', retained['prepared.pack'], deadline, materialize=True))
                if not re.fullmatch(rb'pack\t[a-f0-9]{40}\n', imported):
                    raise ValueError('Prepared pack import was incomplete')
                checksum = imported[5:].strip().decode()
                if checksum != selected['pack_git_checksum']:
                    raise ValueError('Prepared native pack checksum differs')
                pack_fd = _directory_at(private_fd, 'objects', 'pack')
                opened_fds.append(pack_fd)
                index_name = 'pack-' + checksum + '.idx'
                # Generated native files are made private before retained-byte auditing.
                index_fd = os.open(index_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pack_fd)
                try:
                    os.fchmod(index_fd, 0o400)
                finally:
                    os.close(index_fd)
                _read_retained(pack_fd, index_name, retained['prepared.idx'], deadline)
                observed = {}
                for row in native('verify-pack', '-v', 'objects/pack/' + index_name).splitlines():
                    fields = row.split()
                    if fields and re.fullmatch(rb'[a-f0-9]{40}', fields[0]):
                        if len(fields) != 5 or fields[0].decode() in observed:
                            raise ValueError('Prepared pack has duplicate or delta objects')
                        observed[fields[0].decode()] = (fields[1].decode(), int(fields[2]))
                if observed != {oid: (row['kind'], row['bytes']) for oid, row in inventory.items()}:
                    raise ValueError('Prepared pack inventory differs')
                # Shared loose base objects must not mask different bytes in the
                # prepared pack. Read it in a store containing only that pack.
                native('init', '--bare', '--quiet', '--template=', '--object-format=sha1', 'prepared-only.git')
                prepared_only = _directory_at(scratch_fd, 'prepared-only.git')
                opened_fds.append(prepared_only)
                prepared_packs = _directory_at(prepared_only, 'objects', 'pack')
                opened_fds.append(prepared_packs)
                _snapshot_from(directory, 'prepared.pack', prepared_packs, 'pack-'+checksum+'.pack', MAX_PREPARED_PACK, deadline)
                _snapshot_from(pack_fd, index_name, prepared_packs, index_name, 64000, deadline)
                if native('cat-file', '--batch-all-objects', '--batch-check=%(objectname)', store=prepared_only).splitlines() != sorted(oid.encode() for oid in inventory):
                    raise ValueError('Prepared-only store has an unexpected object inventory')
                shared = []
                for oid, row in inventory.items():
                    if oid in closure['objects']:
                        if row != closure['objects'][oid]:
                            raise ValueError('Shared prepared/base object identity differs')
                        shared.append(oid)
                    raw = native('cat-file', row['kind'], oid, store=prepared_only, limit=MAX_OBJECT_BYTES)
                    if len(raw) != row['bytes'] or _sha(raw) != row['sha256'] or _object_id(row['kind'], raw) != oid:
                        raise ValueError('Prepared raw object identity differs')
                if selected['commit'] in closure['objects']:
                    raise ValueError('Prepared commit must be new to the selected base closure')
                commit = native('cat-file', 'commit', selected['commit'])
                head = commit.split(b'\n\n', 1)[0].splitlines()
                if (head[:2] != [b'tree '+selected['tree'].encode(), b'parent '+deployment_commit.encode()]
                        or sum(row.startswith(b'parent ') for row in head) != 1
                        or commit != _read_retained(directory, 'prepared-commit.txt',
                            retained['prepared-commit.txt'], deadline, materialize=True)):
                    raise ValueError('Prepared new commit parent, tree or bytes differ')
                mode = selected['mode'].encode()
                expected = (b':'+mode+b' '+mode+b' '+selected['before_blob'].encode()+b' '+
                    selected['after_blob'].encode()+b' M\0'+selected['values_path'].encode()+b'\0')
                if source.get('source_kind') == 'qualified-policy-change' and selected.get('content_already_baseline') is True:
                    if selected['before_blob'] != selected['after_blob'] or selected['tree'] != closure['base_tree']:
                        raise ValueError('Empty reset changed the original baseline tree')
                    expected = b''
                if native('diff-tree', '--no-commit-id', '--raw', '-z', '-r', '--no-renames',
                          deployment_commit, selected['commit']) != expected:
                    raise ValueError('Prepared change exceeds the exact values modification')
                actual = native('cat-file', '--batch-all-objects', '--batch-check=%(objectname)').splitlines()
                expected_ids = set(closure['objects']) | set(inventory)
                if actual != sorted(oid.encode() for oid in expected_ids) or set(parents) & expected_ids:
                    raise ValueError('Private object inventory includes extra objects or ancestor history')
                closure.update(base_pack_checksum=base_checksum, prepared_shared_objects=sorted(shared),
                               scratch_object_ids=sorted(expected_ids))
                report.update(closure=closure, prepared_pack=deepcopy(retained['prepared.pack']))
                report['checks']['local_complete'] = True
                audit()
            finally:
                for fd in reversed(opened_fds):
                    os.close(fd)
                os.close(scratch_fd)
                scratch_fd = None
                shutil.rmtree('scratch', dir_fd=directory)
            audit()
            binding = {**scope, **{name: deepcopy(report[name]) for name in ('scope', 'image_publication',
                'git_destination', 'base_commit', 'new_commit', 'prepared_pack', 'transmitted_pack',
                'remote_possession', 'omission_authorized', 'closure')}, 'artifacts': deepcopy(retained),
                'output_path': str(output), 'expires_at': source['expires_at']}
            controlled = source['published_evidence']['signer']['authority'] == 'controlled-test'
            if controlled:
                report['gaps'].append('controlled-preparation-cannot-issue-transport-plan-authority')
            else:
                report['decision'], report['preparation_authority'] = 'pass', 'issued'
            report.update(artifacts=deepcopy(retained), binding=deepcopy(binding),
                          finished_at=datetime.now(timezone.utc).isoformat())
            report = seal(report, 'preparation_sha256')
            try:
                receipt = _write(directory, 'git-transport.json', report)
                os.fsync(directory)
                audit()
                _read_retained(directory, 'git-transport.json', receipt, deadline)
                audit()
            except BaseException:
                try:
                    os.unlink('git-transport.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            if controlled:
                return report, None
            plan = object.__new__(PreparedGitTransport)
            _PLANS[plan] = {'pid': os.getpid(), 'scope': deepcopy(scope), 'binding': binding,
                'prepared': prepared, 'prepared_binding': source, 'source_identity': source_output_identity,
                'repo_identity': repo_identity,
                'output_identity': _identity(directory), 'receipt': receipt,
                'expires_monotonic': time.monotonic() + (timestamp(source['expires_at']) - datetime.now(timezone.utc)).total_seconds()}
            return report, plan
    finally:
        os.close(source_fd)


@contextmanager
def _open_request_source(value, *, scope, deadline):
    """Internal direct-handle reader; no new public authority or delivery grant."""
    binding = require_prepared_git_transport(value, **scope, deadline=deadline)
    record = _PLANS[value]
    prepared = deepcopy(record['prepared_binding']['prepared'])
    directory = _open_directory(Path(binding['output_path']))
    try:
        identity = _identity(directory)
        if identity != record['output_identity']:
            raise ValueError('Request transport directory changed')

        def audit():
            if (require_prepared_git_transport(value, **scope, deadline=deadline) != binding
                    or _identity(directory) != identity
                    or record['prepared_binding']['prepared'] != prepared):
                raise ValueError('Request input authority changed')
            _verify_output_path(Path(binding['output_path']), directory)

        audit()
        yield binding, prepared, directory, audit
        audit()
    finally:
        os.close(directory)


def _bind_consumer_audit(value, audit):
    """Add one internal current-input guard, including terminal finalization."""
    record = _consumer_record(value)
    if not callable(audit) or 'additional_audit' in record:
        raise ValueError('Exactly one additional consumer audit is permitted')
    record['additional_audit'] = audit
    value.audit()
