"""Prepare a private Grype database before starting short-lived image operations.

Only a live context in this process can supply the cache. A saved receipt is
evidence about preparation, never a substitute for the cache or a scan result.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import platform
import re
import stat
import tempfile
import time
from weakref import WeakKeyDictionary

from .artifacts import (
    MAX_DATABASE, ROOT, VERSIONS, _canonical, _checkpoint, _database_archive,
    _json, _output, _snapshot, _timestamp, _write,
)
from .signatures import _Invalid
from devsecops.process import run

_LIVE = WeakKeyDictionary()
MAX_PREPARATION_SECONDS = 1800
MAX_CACHE_BYTES = 6_000_000_000


class PreparedDatabase:
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('PreparedDatabase requires current native preparation')

    def __reduce__(self):
        raise TypeError('A prepared database context cannot be serialized')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _inventory(cache, deadline):
    _checkpoint(deadline)
    directories, files = {}, {}
    for directory, names in ((cache, {'6'}), (cache / '6', {'vulnerability.db', 'import.json'})):
        info = directory.lstat()
        if (directory.resolve() != directory or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o500
                or {entry.name for entry in directory.iterdir()} != names):
            raise _Invalid('prepared-database-directory-changed')
        directories[str(directory)] = _identity(info)
    for name, maximum in (('vulnerability.db', MAX_CACHE_BYTES), ('import.json', 64000)):
        path = cache / '6' / name
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                    or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o400
                    or not 0 < before.st_size <= maximum):
                raise _Invalid('prepared-database-file-changed')
            digest = hashlib.sha256()
            total = 0
            while chunk := stream.read(1_000_000):
                _checkpoint(deadline)
                if total == 0 and name == 'vulnerability.db' and not chunk.startswith(b'SQLite format 3\x00'):
                    raise _Invalid('prepared-database-format-unsupported')
                digest.update(chunk)
                total += len(chunk)
                if total > before.st_size:
                    raise _Invalid('prepared-database-file-changed')
            if total != before.st_size or _identity(before) != _identity(os.fstat(stream.fileno())):
                raise _Invalid('prepared-database-file-changed')
            files[name] = {'identity': _identity(before), 'bytes': total, 'sha256': digest.hexdigest()}
    _checkpoint(deadline)
    if any(_identity(Path(name).lstat()) != expected for name, expected in directories.items()):
        raise _Invalid('prepared-database-directory-changed')
    return {'directories': directories, 'files': files}


def bind_prepared_database(value, *, database_sha256, grype_sha256, max_database_age_hours, deadline):
    record = _LIVE.get(value) if type(value) is PreparedDatabase else None
    if (record is None or record['pid'] != os.getpid() or time.monotonic() >= record['expires_monotonic']
            or record['database']['sha256'] != database_sha256 or record['grype']['sha256'] != grype_sha256
            or record['maximum_age_hours'] != max_database_age_hours):
        raise _Invalid('prepared-database-context-unavailable')
    age = (datetime.now(timezone.utc) - record['built']).total_seconds() / 3600
    if not 0 <= age <= max_database_age_hours:
        raise _Invalid('database-stale-or-future')
    if _inventory(record['cache'], deadline) != record['inventory']:
        raise _Invalid('prepared-database-content-changed')
    return {'cache': str(record['cache']), 'compression': record['compression'],
            'preparation_sha256': record['preparation_sha256']}


@contextmanager
def prepare_database(*, database, database_sha256, grype, output,
                     max_database_age_hours=120, timeout=900):
    """Import pinned bytes offline, then retain an immutable private cache."""
    if (not isinstance(database, Path) or not database.is_absolute()
            or not isinstance(output, Path) or not output.is_absolute()
            or not isinstance(database_sha256, str) or not re.fullmatch('[a-f0-9]{64}', database_sha256)
            or type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 1 <= timeout <= MAX_PREPARATION_SECONDS
            or type(max_database_age_hours) is not int or not 1 <= max_database_age_hours <= 720):
        raise ValueError('A pinned database and bounded preparation policy are required')
    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + timeout
    value = None
    with _output(output) as outdir, tempfile.TemporaryDirectory(prefix='devsecops-prepared-db-') as temporary:
        scratch = Path(temporary).resolve()
        cache = scratch / 'cache'
        cache.mkdir(mode=0o700)
        for name in ('home', 'empty-path', 'tmp'):
            (scratch / name).mkdir(mode=0o700)
        env = {'HOME': str(scratch / 'home'), 'PATH': str(scratch / 'empty-path'),
            'XDG_CONFIG_HOME': str(scratch / 'home'), 'TMPDIR': str(scratch / 'tmp'),
            'GRYPE_CHECK_FOR_APP_UPDATE': 'false', 'GRYPE_DB_CACHE_DIR': str(cache),
            'GRYPE_DB_AUTO_UPDATE': 'false', 'GRYPE_DB_REQUIRE_UPDATE_CHECK': 'false',
            'GRYPE_DB_VALIDATE_AGE': 'true', 'GRYPE_DB_MAX_ALLOWED_BUILT_AGE': f'{max_database_age_hours}h',
            'GRYPE_DB_VALIDATE_BY_HASH_ON_START': 'true'}
        (scratch / 'grype.yaml').write_text('check-for-app-update: false\ndb:\n  auto-update: false\n')
        lock_receipt = _snapshot(ROOT / 'supplychain.lock.json', scratch / 'lock.json', 64000, deadline=deadline)
        lock = _json((scratch / 'lock.json').read_bytes())
        architecture = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine(), platform.machine())
        target = platform.system().lower() + '-' + architecture
        selected = lock.get('tools', {}).get('grype', {})
        pin = selected.get('platforms', {}).get(target, {})
        tool = _snapshot(grype, scratch / 'grype', 200_000_000, deadline=deadline)
        if (lock.get('schema_version') != 1 or selected.get('version') != VERSIONS['grype']
                or tool != {'sha256': pin.get('binary_sha256'), 'bytes': pin.get('binary_bytes')}):
            raise _Invalid('scanner-binary-mismatch')
        (scratch / 'grype').chmod(0o500)
        receipts = []

        def execute(arguments, limit=1_000_000):
            _checkpoint(deadline)
            code, raw, _, receipt = run([str(scratch / 'grype'), '--config', 'grype.yaml', *arguments],
                cwd=scratch, env=env, timeout=deadline-time.monotonic(), limit=limit)
            receipts.append({**receipt, 'operation': arguments[0]})
            if code:
                raise _Invalid('database-preparation-command-failed')
            return raw

        version = _json(execute(['version', '-o', 'json'], 64000))
        if version.get('version') != VERSIONS['grype'] or version.get('application') != 'grype':
            raise _Invalid('scanner-version-mismatch')
        archive = scratch / 'database.tar.gz'
        database_ref = _snapshot(database, archive, MAX_DATABASE, deadline=deadline)
        if database_ref['sha256'] != database_sha256:
            raise _Invalid('database-digest-mismatch')
        compression = _database_archive(archive, deadline=deadline)
        if compression == 'zstd':
            archive = archive.rename(scratch / 'database.tar.zst')
        execute(['db', 'import', archive.name])
        status = _json(execute(['db', 'status', '-o', 'json'], 64000))
        built = _timestamp(status.get('built'))
        age = (datetime.now(timezone.utc)-built).total_seconds()/3600
        if (status.get('valid') is not True or not re.fullmatch(r'v6\.[0-9]+\.[0-9]+', str(status.get('schemaVersion')))
                or not 0 <= age <= max_database_age_hours):
            raise _Invalid('database-status-unverified')
        cache_fd = os.open(cache, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            version_fd = os.open('6', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cache_fd)
            try:
                for name in ('vulnerability.db', 'import.json'):
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=version_fd)
                    try:
                        info = os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                            raise _Invalid('prepared-database-file-changed')
                        os.fchmod(fd, 0o400)
                    finally:
                        os.close(fd)
                os.fchmod(version_fd, 0o500)
            finally:
                os.close(version_fd)
            os.fchmod(cache_fd, 0o500)
        finally:
            os.close(cache_fd)
        inventory = _inventory(cache, deadline)
        report = {'schema_version': 1, 'kind': 'local-database-preparation',
            'scope': 'database-cache-only', 'scan_performed': False, 'promotion_authority': False,
            'database': database_ref, 'compression': compression, 'built_at': built.isoformat(),
            'schema': status['schemaVersion'], 'maximum_age_hours': max_database_age_hours,
            'grype': {**tool, 'version': VERSIONS['grype'], 'lock_sha256': lock_receipt['sha256']},
            'cache_files': {name: {k: row[k] for k in ('sha256', 'bytes')} for name, row in inventory['files'].items()},
            'commands': receipts, 'started_at': started.isoformat(),
            'finished_at': datetime.now(timezone.utc).isoformat()}
        report_sha = hashlib.sha256(_canonical(report)).hexdigest()
        _write(outdir, 'database-preparation.json', _canonical({**report, 'preparation_sha256': report_sha}))
        value = object.__new__(PreparedDatabase)
        _LIVE[value] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic()+3600,
            'database': database_ref, 'grype': tool, 'maximum_age_hours': max_database_age_hours,
            'built': built, 'cache': cache, 'inventory': inventory,
            'compression': compression, 'preparation_sha256': report_sha}
        try:
            yield value
        finally:
            _LIVE.pop(value, None)
