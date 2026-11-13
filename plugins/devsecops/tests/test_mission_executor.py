"""Actual Git/wheel/OCI mechanics; Linux execution stays explicitly controlled."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import pickle
import socket
import subprocess
import tarfile
import tempfile
import time

import pytest

from devsecops.factory import base_qualification as base
from devsecops.factory import mission_executor as executor
from devsecops.factory import mission_recipe as recipe
from devsecops.factory import qualify_builder as q
from devsecops.factory.build import prepare_mission_build
from devsecops.factory.state import IDENTITY_KEYS
from devsecops.adapters.artifacts import _oci_archive
from devsecops.runtime.context import canonical_bytes, verify_seal
from test_build_mission import inputs
from test_qualify_builder import _install_pidfds

__all__ = ['inputs']
_REAL_POPEN = subprocess.Popen


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(raw):
    return 'sha256:' + sha(raw)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def layer(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, raw, uid, gid in files:
            info = tarfile.TarInfo(name)
            info.size, info.uid, info.gid, info.mode = len(raw), uid, gid, 0o600
            archive.addfile(info, io.BytesIO(raw))
    return stream.getvalue()


def image(config, layers):
    blobs = {}
    config_raw = encode(config)
    blobs[sha(config_raw)] = config_raw
    manifest = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
        'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': digest(config_raw), 'size': len(config_raw)},
        'layers': []}
    for raw in layers:
        compressed = gzip.compress(raw, mtime=0)
        blobs[sha(compressed)] = compressed
        manifest['layers'].append({'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip',
            'digest': digest(compressed), 'size': len(compressed)})
    raw = encode(manifest)
    blobs[sha(raw)] = raw
    index = encode({'schemaVersion': 2, 'manifests': [{'mediaType': manifest['mediaType'], 'digest': digest(raw), 'size': len(raw)}]})
    return {'blobs': blobs, 'manifest': digest(raw), 'config': digest(config_raw), 'index': index,
            'marker': encode({'imageLayoutVersion': '1.0.0'})}


@pytest.fixture
def build_case(inputs, tmp_path):
    if not inputs['wheel'].is_file():
        pytest.skip('Real pinned offline wheel has not been staged')
    initial = layer([('base-fixture.txt', b'controlled base bytes', 0, 0)])
    config = {'os': 'linux', 'architecture': 'amd64', 'config': {},
        'rootfs': {'type': 'layers', 'diff_ids': [digest(initial)]}}
    base_image = image(config, [initial])
    base_root = tmp_path / 'base/layout'
    (base_root / 'blobs/sha256').mkdir(mode=0o700, parents=True)
    inventory = {}
    for name, raw in base_image['blobs'].items():
        target = base_root / 'blobs/sha256' / name
        target.write_bytes(raw)
        target.chmod(0o400)
        inventory[name] = {'sha256': name, 'bytes': len(raw), 'kind': 'metadata' if raw.startswith(b'{') else 'layer'}
    for name, raw in [('index.json', base_image['index']), ('oci-layout', base_image['marker'])]:
        (base_root / name).write_bytes(raw)
        (base_root / name).chmod(0o400)
    layout = {'blobs': inventory, 'index_sha256': sha(base_image['index']),
        'layout_version_sha256': sha(base_image['marker']), 'manifest_digest': base_image['manifest']}
    layout['sha256'] = sha(canonical_bytes(layout))
    layout.update(directory='layout', hash_scheme='devsecops-image-layout-v1')
    scope = {name: sha(name.encode()) for name in IDENTITY_KEYS}
    # Consumer guard fixture only. Never a production issuer or live P1 claim.
    authority = object.__new__(base.VerifiedBase)
    binding = {'run_scope': scope, 'repository': inputs['base_repository'], 'requested_digest': inputs['base_digest'],
        'architecture': 'amd64', 'platform_manifest_digest': base_image['manifest'], 'config_digest': base_image['config'],
        'layout_path': str(base_root), 'layout_inventory': layout, 'layout_sha256': layout['sha256'],
        'verification_sha256': '1' * 64, 'publisher_trust_sha256': '2' * 64,
        'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()}
    base._ISSUED[authority] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic() + 900, 'binding': binding}
    prepared = prepare_mission_build(**inputs)
    files = [(name, (inputs['output'] / 'context' / name).read_bytes(), 10001, 10001) for name in ('app.py', 'probe.py')]
    application = layer([('opt/mission-status/' + name, raw, uid, gid) for name, raw, uid, gid in files])
    config = deepcopy(config)
    config['rootfs']['diff_ids'].append(digest(application))
    config['config'] = {'User': '10001:10001', 'WorkingDir': '/opt/mission-status',
        'Entrypoint': ['/opt/mission-status/venv/bin/python', '/opt/mission-status/app.py'],
        'Cmd': ['--host', '0.0.0.0', '--health-port', '8080', '--metrics-port', '9090'],
        'Env': ['PYTHONDONTWRITEBYTECODE=1', 'PYTHONUNBUFFERED=1'],
        'ExposedPorts': {'8080/tcp': {}, '9090/tcp': {}}}
    selected = {'kind': recipe.KIND, 'repository': binding['repository'], 'requested_digest': binding['requested_digest'],
        'manifest_digest': binding['platform_manifest_digest'], 'architecture': 'amd64', 'source_commit': inputs['revision'],
        'context_sha256': prepared['context']['sha256']}
    yield {'arguments': inputs, 'authority': authority, 'binding': binding, 'scope': scope,
        'prepared': prepared, 'recipe': selected, 'config': config, 'layers': [initial, application], 'root': tmp_path}
    del base._ISSUED[authority]


def export(case, *, config=None, layers=None, name='image.oci.tar'):
    fixture = image(config or case['config'], layers or case['layers'])
    path = case['root'] / name
    with tarfile.open(path, 'w') as archive:
        for name, raw in {'index.json': fixture['index'], 'oci-layout': fixture['marker'],
                **{'blobs/sha256/' + name: raw for name, raw in fixture['blobs'].items()}}.items():
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    path.chmod(0o600)
    return path, fixture


def bundle(case):
    return {'recipe': case['recipe'], 'prepared': (case['arguments']['output'], case['prepared']),
        'base_authority': case['authority'], 'run_scope': case['scope']}


def test_closed_recipe_prepares_exact_actual_git_and_locked_wheel(build_case):
    case = build_case
    root = case['root'] / 'runtime'
    (root / 'fixture').mkdir(mode=0o700, parents=True)
    result = recipe.prepare(root, bundle(case), time.monotonic() + 5)
    assert result['context'] == case['prepared']['context']
    assert (root / 'fixture/Dockerfile').read_bytes() == (case['arguments']['output'] / 'context/Dockerfile').read_bytes()
    argv = q._commands(root, case['recipe'])['buildctl']
    assert 'build-arg:IRONBANK_PYTHON_REPOSITORY=example/python' in argv
    assert 'build-arg:IRONBANK_PYTHON_SHA256=' + 'a' * 64 in argv
    assert '--no-cache' in argv and not any('push=' in value for value in argv)


@pytest.mark.parametrize('damage', ['context', 'extra-file', 'manifest', 'expiry'])
def test_context_and_authority_changes_stop_before_build(build_case, damage):
    case = build_case
    root = case['root'] / 'runtime'
    (root / 'fixture').mkdir(mode=0o700, parents=True)
    if damage == 'context':
        (case['arguments']['output'] / 'context/app.py').write_text('changed bytes')
    elif damage == 'extra-file':
        (case['arguments']['output'] / 'context/extra.py').write_text('unselected bytes')
    elif damage == 'manifest':
        case['recipe']['manifest_digest'] = 'sha256:' + '0' * 64
    else:
        base._ISSUED[case['authority']]['expires_monotonic'] = 0
    with pytest.raises(ValueError):
        recipe.prepare(root, bundle(case), time.monotonic() + 5)


def inspect(case, path, fixture):
    _oci_archive(path, fixture['manifest'], 'amd64', deadline=time.monotonic() + 5)
    return recipe.inspect_output(path, fixture['manifest'], base_layout=Path(case['binding']['layout_path']),
        base_manifest_digest=case['binding']['platform_manifest_digest'], context=case['prepared']['context'],
        deadline=time.monotonic() + 5)


def test_full_actual_oci_bytes_bind_base_source_files_and_runtime(build_case):
    path, fixture = export(build_case)
    result = inspect(build_case, path, fixture)
    assert result['inherited_layer_count'] == 1
    assert set(result['application_files']) == {'opt/mission-status/app.py', 'opt/mission-status/probe.py'}
    assert all(value['uid'] == value['gid'] == 10001 for value in result['application_files'].values())


@pytest.mark.parametrize('damage', ['base', 'uid', 'command', 'environment', 'content', 'whiteout', 'root-whiteout', 'ancestor'])
def test_valid_but_wrong_image_cannot_satisfy_mission_lineage(build_case, damage):
    case = build_case
    config, layers = deepcopy(case['config']), list(case['layers'])
    if damage == 'base':
        layers[0] = layer([('other-base.txt', b'different base', 0, 0)])
    elif damage == 'uid':
        config['config']['User'] = '0:0'
    elif damage == 'command':
        config['config']['Cmd'] = ['wrong']
    elif damage == 'environment':
        config['config']['Env'].append('PYTHONUNBUFFERED=0')
    elif damage == 'content':
        layers[1] = layer([('opt/mission-status/app.py', b'wrong source', 10001, 10001)])
    elif damage == 'whiteout':
        layers.append(layer([('opt/mission-status/.wh.app.py', b'', 0, 0)]))
    elif damage == 'root-whiteout':
        layers.append(layer([('.wh..wh..opq', b'', 0, 0)]))
    else:
        layers.append(layer([('opt/mission-status', b'not a directory', 0, 0)]))
    config['rootfs']['diff_ids'] = [digest(value) for value in layers]
    path, fixture = export(case, config=config, layers=layers)
    with pytest.raises(ValueError):
        inspect(case, path, fixture)


def test_opaque_whiteout_keeps_files_introduced_in_its_own_layer(build_case):
    case = build_case
    copied = [('opt/mission-status/' + name, (case['arguments']['output'] / 'context' / name).read_bytes(), 10001, 10001)
              for name in ('app.py', 'probe.py')]
    copied.append(('opt/mission-status/.wh..wh..opq', b'', 0, 0))
    layers = [case['layers'][0], layer(copied)]
    config = deepcopy(case['config'])
    config['rootfs']['diff_ids'] = [digest(value) for value in layers]
    path, fixture = export(case, config=config, layers=layers)
    assert len(inspect(case, path, fixture)['application_files']) == 2


@pytest.fixture
def controlled_runner(build_case, monkeypatch):
    case = build_case
    source = case['root'] / 'tools'
    source.mkdir()
    pins = {}
    for name in ('buildctl', 'buildkitd', 'buildkit-runc', 'rootlesskit'):
        raw = ('controlled-never-executed-' + name).encode()
        (source / name).write_bytes(raw)
        pins[name] = {'sha256': sha(raw), 'bytes': len(raw)}
    (case['root'] / 'supplychain.lock.json').write_bytes(encode({
        'builder': {'version': '0.34.1', 'platforms': {'linux-amd64': {'binaries': {k: v for k, v in pins.items() if k != 'rootlesskit'}}}},
        'rootlesskit': {'version': '3.3.0', 'platforms': {'linux-amd64': {'binary_sha256': pins['rootlesskit']['sha256'], 'binary_bytes': pins['rootlesskit']['bytes']}}}}))
    monkeypatch.setattr(q, 'ROOT', case['root'])
    monkeypatch.setattr(q.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(q, 'prerequisites', lambda uid: {'uid': uid, 'controlled_test': True})
    monkeypatch.setattr(q, '_boot_id', lambda: 'd836be49-595f-4713-a4a7-fbc6a74150ee')
    monkeypatch.setattr(q, '_untracked_processes', lambda *args: [])
    identities, processes, listeners = {}, {}, []
    path, fixture = export(case)
    class Process:
        def __init__(self, argv, **kwargs):
            role, root = Path(argv[0]).name, kwargs['cwd']
            self.pid, self.returncode = (43001, None) if role == 'rootlesskit' else (43003, 0)
            for item, pid in ((('rootlesskit', 43001), ('buildkitd', 43002)) if role == 'rootlesskit' else (('buildctl', 43003),)):
                command = q._commands(root, case['recipe'])[item]
                identities[pid] = {'pid': pid, 'uid': os.getuid(), 'start_ticks': 12, 'executable_device': 1, 'executable_inode': 2,
                    'argv_sha256': sha(b'\0'.join(os.fsencode(arg) for arg in command) + b'\0')}
            processes[self.pid] = self
            if role == 'rootlesskit':
                endpoint = socket.socket(socket.AF_UNIX)
                endpoint.bind(str(root / 'buildkit.sock'))
                listeners.append(endpoint)
            else:
                (root / 'image.oci.tar').write_bytes(path.read_bytes())
                (root / 'image.oci.tar').chmod(0o600)
                (root / 'build-metadata.json').write_bytes(encode({'containerimage.digest': fixture['manifest']}))
        def poll(self):
            return self.returncode
        def wait(self, timeout):
            self.returncode = 0
            return 0
        def kill(self):
            self.returncode = -9
    monkeypatch.setattr(q.subprocess, 'Popen', Process)
    monkeypatch.setattr(q, '_proc', lambda pid: identities.get(pid))
    def kill(pid, sig):
        identities.pop(pid, None)
        if pid in processes:
            processes[pid].returncode = 0
    _install_pidfds(monkeypatch, kill)
    monkeypatch.setattr(q, '_remember_daemon_peer', lambda root, state, argv, deadline: q._remember(root, state, 'buildkitd', 43002, argv))
    monkeypatch.setattr(q, 'inspect_rootless_worker', lambda **kwargs: {'peer': {'pid': 43002}, 'worker': {'id': 'controlled'}})
    original = q.qualify
    def controlled(**kwargs):
        result = original(**kwargs)
        result['execution_mode'] = 'controlled-test'
        q._atomic(kwargs['output'], 'qualification.json', result)
        return result
    monkeypatch.setattr(q, 'qualify', controlled)
    yield source, identities
    for listener in listeners:
        listener.close()


def test_direct_consumer_runs_preparation_and_closed_lifecycle_but_controlled_run_never_issues(build_case, controlled_runner, monkeypatch):
    case = build_case
    # Git commands must use the real runner; only lifecycle Popen is controlled.
    # q.subprocess and process.subprocess share the same module. Restore the
    # real implementation around source capture, then return to controlled Linux.
    from unittest.mock import patch
    original_prepare = executor.prepare_mission_build
    def prepared(**kwargs):
        with patch('subprocess.Popen', _REAL_POPEN):
            return original_prepare(**kwargs)
    monkeypatch.setattr(executor, 'prepare_mission_build', prepared)
    args = {key: value for key, value in case['arguments'].items() if key != 'output'}
    with tempfile.TemporaryDirectory(prefix='dso-m-', dir='/tmp') as temporary:
        result, artifact = executor.build_mission(**args, verified_base=case['authority'], run_scope=case['scope'],
            tools=controlled_runner[0], expected_uid=os.getuid(), output=case['root'] / 'built',
            runtime_output=Path(temporary).resolve() / 'run')
    verify_seal(result, 'build_sha256')
    assert result['checks'] == dict.fromkeys(('base', 'source', 'executor', 'artifact'), 'pass')
    assert result['execution_mode'] == 'controlled-test' and result['decision'] == 'unknown'
    assert artifact is None and result['build_authority'] == 'not-issued'
    assert result['gaps'] == ['controlled-execution-cannot-issue-build-authority']
    assert not controlled_runner[1]


def test_report_cannot_authorize_build_or_staging(inputs, tmp_path):
    with pytest.raises(ValueError, match='Unissued'):
        executor.build_mission(**inputs, verified_base={'decision': 'pass'}, run_scope={}, tools=tmp_path,
            expected_uid=os.getuid(), runtime_output=tmp_path / 'runtime')
    assert not inputs['output'].exists()
    for value in ({'decision': 'pass'}, object.__new__(executor.BuiltMission)):
        with pytest.raises(ValueError, match='Unissued'):
            executor.require_built_mission(value, run_scope={}, source_commit='0' * 40, architecture='amd64')
    with pytest.raises(TypeError):
        executor.BuiltMission()
    with pytest.raises(TypeError):
        pickle.dumps(object.__new__(executor.BuiltMission))


def test_preparation_deadline_crossing_withdraws_receipt(inputs, monkeypatch):
    import devsecops.factory.build as build
    original = build._write
    def elapsed(fd, name, raw):
        result = original(fd, name, raw)
        if name == 'prepared.json':
            monkeypatch.setattr(build.time, 'monotonic', lambda: 10**12)
        return result
    monkeypatch.setattr(build, '_write', elapsed)
    with pytest.raises(ValueError):
        prepare_mission_build(**inputs, timeout=30)
    assert not (inputs['output'] / 'prepared.json').exists()


def test_snapshot_deadline_crossing_stops_before_git_process(inputs, monkeypatch):
    import devsecops.snapshot as snapshot
    monkeypatch.setattr(snapshot, 'run', lambda *args, **kwargs: pytest.fail('Expired capture cannot start Git'))
    with pytest.raises(ValueError, match='deadline'):
        snapshot.freeze_commit(inputs['repo'], inputs['revision'], ['mission'], inputs['output'], deadline=0)
