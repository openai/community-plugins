"""Closed installed mission recipe for the owned, offline BuildKit lifecycle."""
from __future__ import annotations

from copy import deepcopy
import gzip
import hashlib
from pathlib import Path
import re
import tarfile
import time

from devsecops.adapters.artifacts import _BoundedTarInfo, _canonical, freeze_directory
from devsecops.adapters.http import _json
from devsecops.adapters.signatures import _snapshot
from devsecops.factory.build import FILES, MISSION, _base
from devsecops.factory.runtime_recipe import freeze_layout
from devsecops.factory.state import _read

KIND = 'mission-image-build-v1'
MAX_IMAGE = 512_000_000


def checkpoint(deadline):
    if time.monotonic() >= deadline:
        raise ValueError('Mission build deadline exceeded')


def descriptor(value):
    if (not isinstance(value, dict) or set(value) != {'kind', 'repository', 'requested_digest',
            'manifest_digest', 'architecture', 'source_commit', 'context_sha256'} or value['kind'] != KIND
            or not isinstance(value['manifest_digest'], str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', value['manifest_digest'])
            or not isinstance(value['context_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', value['context_sha256'])
            or not isinstance(value['source_commit'], str) or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', value['source_commit'])):
        raise ValueError('Unsupported fixed mission recipe')
    _base(value['repository'], value['requested_digest'], value['architecture'])
    return deepcopy(value)


def arguments(root, recipe):
    recipe = descriptor(recipe)
    reference = recipe['repository'] + '@' + recipe['requested_digest']
    return ['--oci-layout', 'qualified-base=' + str(root / 'base/layout'),
        '--opt', 'context:' + reference + '=oci-layout://qualified-base@' + recipe['manifest_digest'],
        '--opt', 'platform=linux/' + recipe['architecture'],
        '--opt', 'build-arg:IRONBANK_PYTHON_REPOSITORY=' + recipe['repository'].removeprefix('registry1.dso.mil/ironbank/'),
        '--opt', 'build-arg:IRONBANK_PYTHON_SHA256=' + recipe['requested_digest'][7:]]


def authorize(inputs):
    # Import lazily: F0 uses the lifecycle too, but no Python callback or JSON
    # verdict is an admissible substitute for this concrete typed guard.
    from devsecops.factory.base_qualification import require_verified_base
    if not isinstance(inputs, dict) or set(inputs) != {'recipe', 'prepared', 'base_authority', 'run_scope'}:
        raise ValueError('Closed mission inputs and base authority are required')
    recipe = descriptor(inputs['recipe'])
    binding = require_verified_base(inputs['base_authority'], run_scope=inputs['run_scope'],
        repository=recipe['repository'], requested_digest=recipe['requested_digest'], architecture=recipe['architecture'])
    if binding['platform_manifest_digest'] != recipe['manifest_digest']:
        raise ValueError('Mission base platform differs from qualified authority')
    return binding


def prepare(root, inputs, deadline):
    binding = authorize(inputs)
    recipe = descriptor(inputs['recipe'])
    prepared = inputs['prepared']
    if not isinstance(prepared, tuple) or len(prepared) != 2 or not isinstance(prepared[0], Path) or not prepared[0].is_absolute():
        raise ValueError('Direct mission preparation is required')
    source, receipt = prepared
    if (receipt['source']['commit'] != recipe['source_commit'] or receipt['context']['sha256'] != recipe['context_sha256']
            or receipt['base'] != {'repository': recipe['repository'], 'digest': recipe['requested_digest'],
                                  'os': 'linux', 'architecture': recipe['architecture']}):
        raise ValueError('Mission preparation differs from selected recipe')
    layout_path = Path(binding['layout_path'])
    if layout_path.name != 'layout' or not layout_path.is_absolute() or '..' in layout_path.parts:
        raise ValueError('Qualified layout path is unsupported')
    layout = freeze_layout(root, acquired=(layout_path.parent, binding['layout_inventory']),
        manifest_digest=recipe['manifest_digest'], deadline=deadline)
    if binding['layout_inventory']['sha256'] != binding['layout_sha256']:
        raise ValueError('Qualified layout identity differs')
    (root / 'fixture').rmdir()  # The lifecycle created this empty owned directory.
    context = freeze_directory(source / 'context', root / 'fixture', deadline=deadline)
    rows = receipt['context']['files']
    lock = _json(_read(root / 'fixture/dependency-lock.json', 64_000))
    expected = set(FILES) | {'wheelhouse/' + lock['filename']}
    if (not isinstance(rows, list) or len(rows) != len(expected) or {row['path'] for row in rows} != expected
            or receipt['context']['hash_scheme'] != 'devsecops-build-files-sha256-v1'):
        raise ValueError('Mission context has unexpected files')
    actual_paths = {path.relative_to(root / 'fixture').as_posix() for path in (root / 'fixture').rglob('*') if path.is_file()}
    if actual_paths != expected:
        raise ValueError('Mission context contains unselected files')
    observed = []
    for row in sorted(rows, key=lambda row: row['path']):
        checkpoint(deadline)
        raw = _read(root / 'fixture' / row['path'], 64_000_000)
        current = {'path': row['path'], 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        if current != row:
            raise ValueError('Prepared mission file changed')
        observed.append(current)
    if hashlib.sha256(_canonical(observed)).hexdigest() != recipe['context_sha256']:
        raise ValueError('Prepared mission context identity changed')
    for name in ('Dockerfile', 'requirements.txt', 'dependency-lock.json'):
        trusted = _snapshot(MISSION / name, root / ('trusted-' + name), 1_000_000, deadline=deadline)
        if next(row for row in rows if row['path'] == name)['sha256'] != trusted['sha256']:
            raise ValueError('Mission recipe differs from installed bytes')
    if next(row for row in rows if row['path'].startswith('wheelhouse/')) != {
            'path': 'wheelhouse/' + lock['filename'], 'sha256': lock['sha256'], 'bytes': lock['bytes']}:
        raise ValueError('Mission dependency differs from installed lock')
    checkpoint(deadline)
    authorize(inputs)
    return {'source': deepcopy(receipt['source']), 'context': deepcopy(receipt['context']),
        'frozen_context': context, 'layout': binding['layout_sha256'], 'frozen_layout': layout,
        'base_config_digest': binding['config_digest'], 'base_verification_sha256': binding['verification_sha256']}


def inspect_output(path, manifest_digest, *, base_layout, base_manifest_digest, context, deadline):
    """After whole-OCI validation, bind base filesystem and copied app files."""
    checkpoint(deadline)
    base_manifest_raw = _read(base_layout / 'blobs/sha256' / base_manifest_digest[7:], 2_000_000)
    if 'sha256:' + hashlib.sha256(base_manifest_raw).hexdigest() != base_manifest_digest:
        raise ValueError('Base manifest changed before output assessment')
    base_manifest = _json(base_manifest_raw)
    config_digest = base_manifest['config']['digest']
    base_config_raw = _read(base_layout / 'blobs/sha256' / config_digest[7:], 2_000_000)
    if 'sha256:' + hashlib.sha256(base_config_raw).hexdigest() != config_digest:
        raise ValueError('Base config changed before output assessment')
    base_config = _json(base_config_raw)
    wanted = {'opt/mission-status/' + name: next(row for row in context['files'] if row['path'] == name)
              for name in ('app.py', 'probe.py')}
    with tarfile.open(path, 'r:', tarinfo=_BoundedTarInfo) as archive:
        def blob(identity, limit=2_000_000):
            checkpoint(deadline)
            member = archive.getmember('blobs/sha256/' + identity[7:])
            if not member.isfile() or not 0 < member.size <= limit:
                raise ValueError('Mission output metadata is unsupported')
            raw = archive.extractfile(member).read(limit+1)
            if 'sha256:' + hashlib.sha256(raw).hexdigest() != identity:
                raise ValueError('Mission output metadata changed')
            checkpoint(deadline)
            return raw
        manifest = _json(blob(manifest_digest))
        config = _json(blob(manifest['config']['digest']))
        inherited = base_config['rootfs']['diff_ids']
        if (not inherited or config['rootfs']['diff_ids'][:len(inherited)] != inherited
                or len(config['rootfs']['diff_ids']) <= len(inherited)):
            raise ValueError('Mission output did not preserve the qualified base filesystem')
        expected = {'User': '10001:10001', 'WorkingDir': '/opt/mission-status',
            'Entrypoint': ['/opt/mission-status/venv/bin/python', '/opt/mission-status/app.py'],
            'Cmd': ['--host', '0.0.0.0', '--health-port', '8080', '--metrics-port', '9090']}
        runtime = config['config']
        environment = runtime.get('Env')
        if (any(runtime.get(key) != value for key, value in expected.items())
                or not {'8080/tcp', '9090/tcp'} <= set(runtime.get('ExposedPorts', {}))
                or runtime.get('OnBuild') not in (None, []) or not isinstance(environment, list)
                or any(not isinstance(value, str) for value in environment)
                or any([value for value in environment if value.startswith(name + '=')] != [name + '=1']
                       for name in ('PYTHONDONTWRITEBYTECODE', 'PYTHONUNBUFFERED'))):
            raise ValueError('Mission output runtime differs from the fixed recipe')
        expected['Env'] = ['PYTHONDONTWRITEBYTECODE=1', 'PYTHONUNBUFFERED=1']
        found, budget = {}, {'decoded': 0, 'entries': 0}
        class Reader:
            def __init__(self, stream, decoded=False):
                self.stream, self.decoded, self.checksum, self.size = stream, decoded, hashlib.sha256(), 0
            def read(self, size=-1):
                checkpoint(deadline)
                raw = self.stream.read(min(size, 65536) if size >= 0 else 65536)
                self.size += len(raw)
                self.checksum.update(raw)
                if self.decoded:
                    budget['decoded'] += len(raw)
                    if budget['decoded'] > MAX_IMAGE:
                        raise ValueError('Mission layer decode limit')
                elif self.size > MAX_IMAGE:
                    raise ValueError('Mission layer byte limit')
                return raw
        for position, layer in enumerate(manifest['layers'][len(inherited):], len(inherited)):
            checkpoint(deadline)
            layer_files, names = {}, set()
            member = archive.getmember('blobs/sha256/' + layer['digest'][7:])
            stream = archive.extractfile(member)
            encoded = Reader(stream)
            decompressed = gzip.GzipFile(fileobj=encoded) if layer['mediaType'].endswith('+gzip') else encoded
            decoded = Reader(decompressed, decoded=True)
            try:
                with tarfile.open(fileobj=decoded, mode='r|', tarinfo=_BoundedTarInfo) as contents:
                    for item in contents:
                        checkpoint(deadline)
                        budget['entries'] += 1
                        if budget['entries'] > 100_000 or item.size < 0 or item.size > MAX_IMAGE:
                            raise ValueError('Mission layer entry limit')
                        name = Path(item.name).as_posix().removeprefix('./')
                        if name in names:
                            raise ValueError('Repeated mission layer paths are unsupported')
                        names.add(name)
                        parent, leaf = name.rsplit('/', 1) if '/' in name else ('', name)
                        if leaf.startswith('.wh.'):
                            removed = parent if leaf == '.wh..wh..opq' else '/'.join(filter(None, (parent, leaf[4:])))
                            for target in list(found):
                                if not removed or target == removed or target.startswith(removed + '/'):
                                    found.pop(target)
                        elif name in wanted:
                            if not item.isfile() or item.uid != 10001 or item.gid != 10001 or not item.mode & 0o400:
                                raise ValueError('Mission application file is not the expected readable owned regular file')
                            checksum, size = hashlib.sha256(), 0
                            with contents.extractfile(item) as payload:
                                while chunk := payload.read(65536):
                                    checkpoint(deadline)
                                    size += len(chunk)
                                    if size > 64_000_000:
                                        raise ValueError('Mission application file limit')
                                    checksum.update(chunk)
                            ref = wanted[name]
                            if size != ref['bytes'] or checksum.hexdigest() != ref['sha256']:
                                raise ValueError('Mission application file differs from the selected commit')
                            layer_files[name] = {'sha256': ref['sha256'], 'bytes': size, 'uid': item.uid, 'gid': item.gid}
                        elif any(target.startswith(name + '/') for target in wanted) and not item.isdir():
                            raise ValueError('Mission application ancestor is not a directory')
                while decoded.read(65536):
                    pass
                if ('sha256:' + decoded.checksum.hexdigest() != config['rootfs']['diff_ids'][position]
                        or 'sha256:' + encoded.checksum.hexdigest() != layer['digest'] or encoded.size != layer['size']):
                    raise ValueError('Mission output layer changed during assessment')
                # OCI whiteouts hide lower layers regardless of tar entry order;
                # they never remove a file introduced in their own layer.
                found.update(layer_files)
            finally:
                if isinstance(decompressed, gzip.GzipFile):
                    decompressed.close()
                stream.close()
    checkpoint(deadline)
    if set(found) != set(wanted):
        raise ValueError('Mission output lacks selected application files')
    return {'base_manifest_digest': base_manifest_digest, 'base_config_digest': config_digest,
        'inherited_diff_ids_sha256': hashlib.sha256(_canonical(inherited)).hexdigest(),
        'inherited_layer_count': len(inherited), 'application_files': found, 'runtime': expected}
