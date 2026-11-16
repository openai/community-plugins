#!/usr/bin/env python3
"""Explicit, checksum-pinned Kustomize provisioning; reviews never download."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 80_000_000


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def download(url, expected):
    request = urllib.request.Request(url, headers={'User-Agent': 'devsecops-kustomize-bootstrap/1'})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(LIMIT+1)
    if len(data) > LIMIT or digest(data) != expected:
        raise ValueError('Published Kustomize checksum or download size verification failed')
    return data


def atomic(path, data, executable=False):
    for entry in (path, *path.parents):
        if entry.is_symlink():
            raise ValueError('Kustomize cache paths must not contain symlinks')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.kustomize-')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o700 if executable else 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def executable(archive):
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as handle:
        entries, declared_bytes = [], 0
        for entry in handle:
            entries.append(entry)
            declared_bytes += entry.size
            if (len(entries) > 16 or declared_bytes > LIMIT
                    or not (entry.isfile() or entry.isdir()) or entry.name.startswith('/')
                    or '..' in Path(entry.name).parts):
                raise ValueError('Unsafe Kustomize tool archive')
        members = [entry for entry in entries if entry.name == 'kustomize']
        if len(members) != 1 or not members[0].isfile() or members[0].size > LIMIT:
            raise ValueError('Unexpected Kustomize archive executable')
        content = handle.extractfile(members[0]).read(LIMIT+1)
        if len(content) > LIMIT:
            raise ValueError('Kustomize executable exceeds size limit')
        return content


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=Path.home()/'.cache/devsecops/kustomize-v5.8.3')
    parser.add_argument('--offline', action='store_true', help='Use only a previously staged, checksum-verified tool archive')
    args = parser.parse_args(argv)
    try:
        raw_lock = (ROOT/'kustomize.lock.json').read_bytes()
        lock = json.loads(raw_lock)
        arch = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine(), platform.machine())
        target = platform.system().lower()+'-'+arch
        if target not in lock['tools']:
            raise ValueError('This lock supports macOS and Linux on arm64/amd64 only')
        for entry in (args.cache, *args.cache.parents):
            if entry.is_symlink():
                raise ValueError('Kustomize cache paths must not contain symlinks')
        cache = args.cache.resolve()
        item = lock['tools'][target]
        archive_path = cache/item['asset']
        if archive_path.is_symlink():
            raise ValueError('Kustomize archive cache must not be a symlink')
        archive = (archive_path.read_bytes() if archive_path.is_file()
                   and archive_path.stat().st_size <= LIMIT else b'')
        if digest(archive) != item['sha256']:
            if args.offline:
                raise ValueError('Offline Kustomize archive is missing or differs from the pinned checksum')
            checksums = download(lock['checksums']['url'], lock['checksums']['sha256'])
            published = dict((line.split()[1], line.split()[0])
                             for line in checksums.decode('ascii').splitlines())
            if published.get(item['asset']) != item['sha256']:
                raise ValueError('Kustomize lock differs from official release checksums')
            url = lock['checksums']['url'].rsplit('/', 1)[0]+'/'+item['asset']
            archive = download(url, item['sha256'])
            atomic(archive_path, archive)
        content = executable(archive)
        atomic(cache/'bin/kustomize', content, executable=True)
        receipt = {'schema_version': 1, 'version': lock['version'], 'platform': target,
                   'lock_sha256': digest(raw_lock), 'release': lock['release'],
                   'checksums_sha256': lock['checksums']['sha256'],
                   'archive_sha256': digest(archive), 'binary_sha256': digest(content)}
        atomic(cache/'provisioning.json', (json.dumps(receipt, indent=2)+'\n').encode())
        print(json.dumps({'kustomize': str(cache/'bin/kustomize'), 'provisioning': str(cache/'provisioning.json')}))
        return 0
    except (OSError, ValueError, KeyError, tarfile.TarError):
        print(json.dumps({'status': 'error', 'message': 'Kustomize provisioning failed; verify platform, trusted lock, cache, and download availability'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
