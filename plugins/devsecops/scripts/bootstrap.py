#!/usr/bin/env python3
"""Explicit provisioning step. Reviews never download tools or schemas."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from devsecops.schema_catalog import select_schema_set


def verified_download(item):
    request = urllib.request.Request(item['url'], headers={'User-Agent': 'devsecops-plugin/0.1'})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(80_000_001)
    if len(data) > 80_000_000 or hashlib.sha256(data).hexdigest() != item['sha256']:
        raise ValueError('Download checksum or size verification failed')
    return data


def atomic(path: Path, data: bytes, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('Refusing symlink in tool cache')
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.download-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.chmod(temp, 0o700 if executable else 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=Path.home()/'.cache/devsecops/v0.1')
    parser.add_argument('--kubernetes-version', help='Exact packaged schema set; defaults to the legacy 1.34.0 set')
    args = parser.parse_args()
    lock = json.loads((Path(__file__).resolve().parents[1]/'toolchain.lock.json').read_text())
    try:
        schema_source, selected_schemas = select_schema_set(lock, args.kubernetes_version)
    except ValueError as error:
        parser.error(str(error))
    arch = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine(), platform.machine())
    target = platform.system().lower() + '-' + arch
    if target not in lock['tools']:
        parser.error('This release provisions macOS and Linux on arm64/amd64 only')
    for parent in (args.cache, *args.cache.parents):
        if parent.is_symlink():
            parser.error('Use a real cache path without symlink components')
    cache = args.cache.resolve()
    def schema(item):
        path = cache/'schemas'/item['path']
        if path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']:
            return
        atomic(path, verified_download(item))
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(schema, selected_schemas))
    receipts = {}
    for name, item in lock['tools'][target].items():
        archive = verified_download(item)
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
            member = tar.getmember(item['member'])
            if not member.isfile() or member.size > 150_000_000:
                raise ValueError('Unexpected tool archive member')
            content = tar.extractfile(member).read()
        atomic(cache/'bin'/name, content, executable=True)
        receipts[name] = {**item, 'binary_sha256': hashlib.sha256(content).hexdigest()}
    atomic(cache/'provisioning.json', (json.dumps({'platform': target, 'tools': receipts,
        'schema_source': schema_source}, indent=2)+'\n').encode())
    print(json.dumps({'helm': str(cache/'bin/helm'), 'kubeconform': str(cache/'bin/kubeconform'),
                      'schemas': str(cache/'schemas')}, indent=2))


if __name__ == '__main__':
    main()
