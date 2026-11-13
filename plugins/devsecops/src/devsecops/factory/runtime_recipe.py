"""Closed mission-Python probe recipe; this module issues no base authority."""
from __future__ import annotations

from copy import deepcopy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import time

from devsecops.adapters.artifacts import _BoundedTarInfo, freeze_directory
from devsecops.adapters.http import _json
from devsecops.adapters.image_layout import _audit_layout
from devsecops.adapters.oci import _digest
from devsecops.adapters.signatures import _snapshot
from devsecops.factory.build import MISSION, _base
from devsecops.runtime.context import canonical_bytes


def descriptor(value):
    if (not isinstance(value, dict) or set(value) != {'kind', 'repository', 'requested_digest', 'manifest_digest', 'architecture', 'nonce'}
            or value['kind'] != 'mission-python-runtime-v1' or not _digest(value['manifest_digest'])
            or not isinstance(value['nonce'], str) or not re.fullmatch(r'[a-f0-9]{32}', value['nonce'])):
        raise ValueError('Unsupported fixed runtime recipe')
    _base(value['repository'], value['requested_digest'], value['architecture'])
    return deepcopy(value)


def client_arguments(root, recipe):
    recipe = descriptor(recipe)
    reference = recipe['repository'] + '@' + recipe['requested_digest']
    return ['--oci-layout', 'qualified-base=' + str(root / 'base/layout'),
            '--opt', 'context:' + reference + '=oci-layout://qualified-base@' + recipe['manifest_digest'],
            '--opt', 'platform=linux/' + recipe['architecture']]


def freeze_layout(root, *, acquired, manifest_digest, deadline):
    """Freeze one acquired OCI layout and revalidate its full byte inventory."""
    def checkpoint():
        if time.monotonic() >= deadline:
            raise ValueError('Runtime recipe deadline')
    checkpoint()
    if (not isinstance(acquired, tuple) or len(acquired) != 2 or not isinstance(acquired[0], Path)
            or not acquired[0].is_absolute() or not isinstance(acquired[1], dict)):
        raise ValueError('Direct acquisition path and inventory are required')
    source, observed = acquired
    if not _digest(manifest_digest) or observed['manifest_digest'] != manifest_digest:
        raise ValueError('Runtime image identity differs from acquisition')
    (root / 'base').mkdir(mode=0o700)
    frozen = freeze_directory(source / 'layout', root / 'base/layout', deadline=deadline)
    fds = []
    try:
        for path in (root / 'base', root / 'base/layout', root / 'base/layout/blobs/sha256'):
            fds.append(os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        _audit_layout(*fds, observed['blobs'], deadline)
        for name, expected in (('index.json', observed['index_sha256']), ('oci-layout', observed['layout_version_sha256'])):
            raw = (root / 'base/layout' / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('Runtime OCI completion marker differs')
    finally:
        for fd in reversed(fds):
            os.close(fd)
    index = _json((root / 'base/layout/index.json').read_bytes())
    if len(index.get('manifests', [])) != 1 or index['manifests'][0]['digest'] != manifest_digest:
        raise ValueError('Runtime OCI platform selection differs')
    inventory = {key: observed[key] for key in ('blobs', 'index_sha256', 'layout_version_sha256', 'manifest_digest')}
    if hashlib.sha256(canonical_bytes(inventory)).hexdigest() != observed['sha256']:
        raise ValueError('Runtime OCI inventory identity differs')
    checkpoint()
    return frozen


def prepare(root, recipe, *, acquired, wheel, deadline):
    """Freeze and validate directly acquired layout bytes plus the locked wheel."""
    recipe = descriptor(recipe)
    def checkpoint():
        if time.monotonic() >= deadline:
            raise ValueError('Runtime recipe deadline')
    frozen = freeze_layout(root, acquired=acquired, manifest_digest=recipe['manifest_digest'], deadline=deadline)
    dependency_ref = _snapshot(MISSION / 'dependency-lock.json', root / 'dependency-lock.json', 64_000, deadline=deadline)
    dependency = _json((root / 'dependency-lock.json').read_bytes())
    _snapshot(MISSION / 'requirements.txt', root / 'fixture/requirements.txt', 64_000, deadline=deadline)
    (root / 'fixture/wheelhouse').mkdir(mode=0o700)
    filename = dependency['filename']
    if not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.whl', filename):
        raise ValueError('Unsupported locked wheel filename')
    wheel_ref = _snapshot(wheel, root / 'fixture/wheelhouse' / filename, 1_000_000, deadline=deadline)
    if wheel_ref != {key: dependency[key] for key in ('sha256', 'bytes')}:
        raise ValueError('Runtime wheel differs from installed dependency lock')
    root_code = '''import ensurepip, json, os, pathlib, platform, subprocess, sys, venv
assert sys.version_info >= (3, 11), "Python 3.11+ required"
assert os.getuid() == 0 and os.getgid() == 0
venv.EnvBuilder(with_pip=True).create("/opt/mission-status/venv")
python = "/opt/mission-status/venv/bin/python"
subprocess.run([python, "-I", "-m", "pip", "install", "--isolated", "--no-index", "--find-links=/devsecops-probe/wheelhouse", "--require-hashes", "--only-binary=:all:", "--no-deps", "-r", "/devsecops-probe/requirements.txt"], check=True)
proof = {"contract":"mission-python-runtime-v1", "nonce":NONCE, "python":list(sys.version_info[:3]), "machine":platform.machine(), "root_uid":os.getuid(), "root_gid":os.getgid(), "venv":True, "ensurepip":True}
path = pathlib.Path("/tmp/devsecops-runtime-proof.json")
path.write_text(json.dumps(proof, sort_keys=True))
path.chmod(0o600)
os.chown(path, 10001, 10001)
'''.replace('NONCE', json.dumps(recipe['nonce']))
    uid_code = '''import importlib.metadata, json, os, pathlib
assert os.getuid() == 10001 and os.getgid() == 10001
import prometheus_client
version = importlib.metadata.version("prometheus-client")
assert version == VERSION
path = pathlib.Path("/tmp/devsecops-runtime-proof.json")
proof = json.loads(path.read_text())
assert proof["nonce"] == NONCE
proof.update(uid=os.getuid(), gid=os.getgid(), dependency={"name":"prometheus-client", "version":version})
path.write_text(json.dumps(proof, sort_keys=True))
print("DEVSECOPS_RUNTIME_PROBE " + NONCE, flush=True)
'''.replace('VERSION', json.dumps(dependency['version'])).replace('NONCE', json.dumps(recipe['nonce']))
    dockerfile = ('FROM ' + recipe['repository'] + '@' + recipe['requested_digest'] + ' AS probe\n'
        'USER 0:0\nWORKDIR /opt/mission-status\n'
        'COPY --chmod=0444 root.py uid.py requirements.txt /devsecops-probe/\n'
        'COPY wheelhouse/ /devsecops-probe/wheelhouse/\n'
        'RUN --network=none ["python3", "-I", "/devsecops-probe/root.py"]\n'
        'USER 10001:10001\n'
        'RUN --network=none ["/opt/mission-status/venv/bin/python", "-I", "/devsecops-probe/uid.py"]\n'
        'FROM scratch\nCOPY --from=probe /tmp/devsecops-runtime-proof.json /proof.json\n').encode()
    for name, raw in (('root.py', root_code.encode()), ('uid.py', uid_code.encode()), ('Dockerfile', dockerfile)):
        checkpoint()
        path = root / 'fixture' / name
        with path.open('xb') as stream:
            stream.write(raw)
        path.chmod(0o400)
    checkpoint()
    return {'recipe': recipe, 'layout': acquired[1]['sha256'], 'frozen_tree': frozen,
            'dependency_lock': dependency_ref, 'wheel': wheel_ref, 'dependency_version': dependency['version'],
            'dockerfile_sha256': hashlib.sha256(dockerfile).hexdigest(),
            'root_script_sha256': hashlib.sha256(root_code.encode()).hexdigest(),
            'uid_script_sha256': hashlib.sha256(uid_code.encode()).hexdigest()}


def proof(path, manifest_digest, recipe, dependency_version, deadline):
    """Read only the sole proof file from the previously validated OCI artifact."""
    descriptor(recipe)
    def checkpoint():
        if time.monotonic() >= deadline:
            raise ValueError('Runtime proof deadline')
    checkpoint()
    with tarfile.open(path, 'r:', tarinfo=_BoundedTarInfo) as outer:
        manifest_member = outer.getmember('blobs/sha256/' + manifest_digest[7:])
        if not manifest_member.isfile() or manifest_member.size > 64_000:
            raise ValueError('Runtime artifact manifest unsupported')
        manifest_raw = outer.extractfile(manifest_member).read(64_001)
        if 'sha256:' + hashlib.sha256(manifest_raw).hexdigest() != manifest_digest:
            raise ValueError('Runtime artifact manifest changed')
        manifest = _json(manifest_raw)
        if len(manifest.get('layers', [])) != 1:
            raise ValueError('Runtime proof artifact must contain one layer')
        layer = manifest['layers'][0]
        member = outer.getmember('blobs/sha256/' + layer['digest'][7:])
        if not member.isfile() or member.size > 64_000:
            raise ValueError('Runtime proof layer unsupported')
        encoded = outer.extractfile(member).read(64_001)
        if (len(encoded) != layer['size'] or len(encoded) != member.size
                or 'sha256:' + hashlib.sha256(encoded).hexdigest() != layer['digest']):
            raise ValueError('Runtime artifact layer changed')
        stream = io.BytesIO(encoded)
        compressed = layer['mediaType'].endswith('+gzip')
        decoded = gzip.GzipFile(fileobj=stream) if compressed else stream
        try:
            raw = decoded.read(64_001)
            if len(raw) > 64_000:
                raise ValueError('Runtime proof decoded layer too large')
        finally:
            decoded.close()
            stream.close()
    checkpoint()
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:', tarinfo=_BoundedTarInfo) as archive:
        files = [member for member in archive if not member.isdir()]
        if len(files) != 1 or files[0].name != 'proof.json' or not files[0].isfile() or files[0].size > 16_384:
            raise ValueError('Runtime proof file identity unsupported')
        document = _json(archive.extractfile(files[0]).read(16_385))
    expected = {'contract', 'nonce', 'python', 'machine', 'root_uid', 'root_gid', 'uid', 'gid', 'venv', 'ensurepip', 'dependency'}
    if (set(document) != expected or document['contract'] != recipe['kind'] or document['nonce'] != recipe['nonce']
            or not isinstance(document['python'], list) or len(document['python']) != 3
            or any(type(n) is not int or not 0 <= n <= 999 for n in document['python'])
            or document['python'][:2] < [3, 11]
            or document['machine'] != {'amd64': 'x86_64', 'arm64': 'aarch64'}[recipe['architecture']]
            or any(type(document[k]) is not int or document[k] != v for k, v in
                   (('root_uid', 0), ('root_gid', 0), ('uid', 10001), ('gid', 10001)))
            or document['venv'] is not True or document['ensurepip'] is not True
            or document['dependency'] != {'name': 'prometheus-client', 'version': dependency_version}):
        raise ValueError('Actual base runtime proof did not satisfy the fixed contract')
    checkpoint()
    return document
