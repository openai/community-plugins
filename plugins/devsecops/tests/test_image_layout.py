"""Real authenticated localhost TLS and real tar/gzip bytes; no registry claims."""
from copy import deepcopy
import gzip
import hashlib
import io
import json
import os
import tarfile
import time

import pytest

from test_argocd import ca, server
from test_registry_evidence import registry, REPOSITORY, TOKEN, SECRET
from test_oci import digest, encode
from test_artifacts import tools
from devsecops.adapters import image_layout as adapter
from devsecops.adapters.http import APIError, HTTPSReadClient
from devsecops.adapters.http_blob import HTTPSBlobReadClient
from devsecops.adapters.oci import CONFIG, INDEX, MANIFEST, DOCKER_CONFIG, DOCKER_MANIFEST
from devsecops.runtime.context import verify_seal

__all__ = ['ca', 'server', 'registry', 'tools']


def layer_bytes(payload=b'real local fixture\n', name='hello.txt'):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w') as archive:
        member = tarfile.TarInfo(name)
        member.size, member.mode = len(payload), 0o644
        archive.addfile(member, io.BytesIO(payload))
    return result.getvalue()


def install(registry, *, docker=False, compressed=True, mutate_manifest=None, mutate_config=None,
            payload=b'real local fixture\n', name='hello.txt', copies=1):
    decoded = layer_bytes(payload, name)
    raw = gzip.compress(decoded, mtime=0) if compressed else decoded
    media = adapter.DOCKER_GZIP if docker else adapter.GZIP if compressed else adapter.TAR
    descriptor = {'mediaType': media, 'digest': digest(raw), 'size': len(raw)}
    config = {'os': 'linux', 'architecture': 'amd64', 'rootfs': {'type': 'layers', 'diff_ids': [digest(decoded)] * copies}}
    if mutate_config:
        mutate_config(config)
    config_raw = encode(config)
    manifest = {'schemaVersion': 2, 'mediaType': DOCKER_MANIFEST if docker else MANIFEST,
        'config': {'mediaType': DOCKER_CONFIG if docker else CONFIG, 'digest': digest(config_raw), 'size': len(config_raw)},
        'layers': [deepcopy(descriptor) for _ in range(copies)]}
    if mutate_manifest:
        mutate_manifest(manifest)
    manifest_raw = encode(manifest)
    index = encode({'schemaVersion': 2, 'mediaType': INDEX, 'manifests': [{
        'mediaType': manifest['mediaType'], 'digest': digest(manifest_raw), 'size': len(manifest_raw),
        'platform': {'os': 'linux', 'architecture': 'amd64'}}]})
    for body, content_type, blob in ((raw, media, True), (config_raw, manifest['config']['mediaType'], True),
                                   (manifest_raw, manifest['mediaType'], False), (index, INDEX, False)):
        registry['routes']['/v2/' + REPOSITORY + ('/blobs/' if blob else '/manifests/') + digest(body)] = {
            'raw': body, 'media': content_type, 'headers': {'Docker-Content-Digest': digest(body)}}
    return {'requested': digest(index), 'manifest': digest(manifest_raw), 'config': digest(config_raw),
            'layer': digest(raw), 'raw': raw, 'decoded': decoded, 'index': index}


def acquire(registry, image, tmp_path, **kwargs):
    return adapter.acquire_image_layout(registry['origin'], repository=REPOSITORY,
        allowed_repositories=[REPOSITORY], requested_digest=image['requested'], architecture='amd64',
        output=tmp_path.resolve() / 'result', token_env=kwargs.pop('token_env', 'REGISTRY_TEST_TOKEN'),
        ca_bundle=registry['ca'], **kwargs)


def codes(result):
    return {gap['code'] for gap in result['gaps']}


@pytest.mark.parametrize('docker,compressed', [(False, False), (False, True), (True, True)])
def test_real_complete_oci_layout_and_authenticated_content(registry, tmp_path, docker, compressed):
    image = install(registry, docker=docker, compressed=compressed)
    result = acquire(registry, image, tmp_path)
    verify_seal(result, 'manifest_sha256')
    assert result['decision'] == 'unknown' and result['build_authority'] == 'not-issued'
    assert result['observations']['acquisition']['decision'] == 'pass'
    assert result['execution_status'] == 'completed'
    layout = tmp_path / 'result/layout'
    assert json.loads((layout / 'oci-layout').read_bytes()) == {'imageLayoutVersion': '1.0.0'}
    assert json.loads((layout / 'index.json').read_bytes())['manifests'][0]['digest'] == image['manifest']
    for name, receipt in result['artifacts'].items():
        raw = (layout / 'blobs/sha256' / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == receipt['sha256']
        assert len(raw) == receipt['bytes']
    assert all(r['authorization'] == 'Bearer ' + TOKEN for r in registry['requests'])
    assert TOKEN not in json.dumps(result) and SECRET not in json.dumps(result)
    assert result['observations']['layers'][0]['diff_id'] == digest(image['decoded'])
    assert len(registry['requests']) == 4


def test_real_basic_to_scoped_bearer_exchange(registry, tmp_path):
    registry['mode'] = 'basic-exchange'
    image = install(registry)
    result = acquire(registry, image, tmp_path, token_env=None,
        basic_username_env='REGISTRY_TEST_USER', basic_password_env='REGISTRY_TEST_PASSWORD',
        token_path='/service/token', token_service='registry')
    assert result['observations']['acquisition']['decision'] == 'pass'
    assert result['observations']['authentication']['token_exchange'] == 'completed'
    assert len([r for r in registry['requests'] if r['path'].startswith('/service/token')]) == 1


@pytest.mark.parametrize('alter,code', [
    (lambda m: m['layers'][0].update(urls=['https://outside.invalid/private']), 'image-external-descriptor-unsupported'),
    (lambda m: m['layers'][0].update(mediaType='application/vnd.oci.image.layer.v1.tar+zstd'), 'image-layer-media-unsupported'),
    (lambda m: m['layers'][0].update(size=adapter.MAX_LAYER + 1), 'image-layer-size-limit'),
    (lambda m: m['layers'][0].update(size=0), 'image-layer-size-limit'),
])
def test_rejects_unsupported_layers_before_fetch(registry, tmp_path, alter, code):
    image = install(registry, mutate_manifest=alter)
    result = acquire(registry, image, tmp_path)
    assert code in codes(result)
    assert len(registry['requests']) == 3
    assert not (tmp_path / 'result/layout/oci-layout').exists()


@pytest.mark.parametrize('change,code', [
    ({'raw': b'X'}, 'image-descriptor-size-mismatch'),
    ({'headers': {'Docker-Content-Digest': 'sha256:' + '0' * 64}}, 'image-header-digest-mismatch'),
    ({'status': 302, 'headers': {'Location': 'https://outside.invalid/layer'}}, 'image-redirect'),
    ({'status': 403}, 'image-denied'),
])
def test_layer_transport_failure_never_completes_layout(registry, tmp_path, change, code):
    image = install(registry)
    registry['routes']['/v2/' + REPOSITORY + '/blobs/' + image['layer']].update(change)
    result = acquire(registry, image, tmp_path)
    assert code in codes(result)
    assert result['observations']['acquisition']['decision'] != 'pass'
    assert not (tmp_path / 'result/layout/oci-layout').exists()
    assert all(not r['path'].startswith('https://') for r in registry['requests'])


def test_wrong_layer_digest_is_failure(registry, tmp_path):
    image = install(registry)
    route = registry['routes']['/v2/' + REPOSITORY + '/blobs/' + image['layer']]
    route['raw'] = bytes([route['raw'][0] ^ 1]) + route['raw'][1:]
    result = acquire(registry, image, tmp_path)
    assert 'image-digest-mismatch' in codes(result)
    assert result['observations']['acquisition']['decision'] == 'fail'


def test_wrong_decoded_diffid_is_failure(registry, tmp_path):
    image = install(registry, mutate_config=lambda c: c['rootfs'].update(diff_ids=['sha256:' + '0' * 64]))
    result = acquire(registry, image, tmp_path)
    assert 'image-rootfs-diffid-mismatch' in codes(result)
    assert result['observations']['acquisition']['decision'] == 'fail'


def test_decoded_budget_prevents_gzip_expansion(registry, tmp_path):
    image = install(registry, payload=b'0' * 1_000_000)
    result = acquire(registry, image, tmp_path, max_decoded_bytes=32768)
    assert codes(result) & {'image-layer-decompression-limit', 'image-layer-content-unsupported'}
    assert not (tmp_path / 'result/layout/oci-layout').exists()


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'back\\slash'])
def test_tar_paths_never_extracted(registry, tmp_path, name):
    image = install(registry, name=name)
    result = acquire(registry, image, tmp_path)
    assert 'image-layer-content-unsupported' in codes(result)
    assert not (tmp_path / 'escape').exists()


def test_duplicate_layer_content_retains_each_diffid(registry, tmp_path):
    image = install(registry, copies=2)
    result = acquire(registry, image, tmp_path)
    assert result['observations']['acquisition']['decision'] == 'pass'
    assert len(result['observations']['layers']) == 2
    assert len([r for r in result['artifacts'].values() if r['kind'] == 'layer']) == 1


def test_slow_body_has_absolute_deadline(registry, tmp_path):
    image = install(registry)
    registry['routes']['/v2/' + REPOSITORY + '/blobs/' + image['layer']]['trickle'] = .03
    started = time.monotonic()
    result = acquire(registry, image, tmp_path, total_timeout=.3, timeout=.3)
    assert time.monotonic() - started < 1.5
    assert 'image-deadline' in codes(result)
    assert not (tmp_path / 'result/layout/oci-layout').exists()


def test_metadata_cap_is_not_expanded(server):
    with pytest.raises(APIError, match='invalid-configuration'):
        HTTPSReadClient(server['origin'], max_body_bytes=8_000_001)
    client = HTTPSBlobReadClient(server['origin'], ca_bundle=server['ca'])
    assert client._max_body == 2_000_000
    server['responses'] = [{'length': 2_000_001}]
    with pytest.raises(APIError, match='body-limit'):
        client.get_bytes('/metadata')


@pytest.mark.parametrize('mode', ['populated', 'public', 'readonly', 'pipe', 'hardlink', 'append'])
def test_blob_requires_new_private_readwrite_regular_fd(server, tmp_path, mode):
    client = HTTPSBlobReadClient(server['origin'], ca_bundle=server['ca'])
    target = tmp_path / 'target'
    target.touch(mode=0o600)
    if mode == 'populated':
        target.write_bytes(b'keep')
    if mode == 'public':
        target.chmod(0o644)
    if mode == 'hardlink':
        os.link(target, tmp_path / 'another')
    if mode == 'pipe':
        fd, other = os.pipe()
        os.close(other)
    else:
        fd = os.open(target, (os.O_RDONLY if mode == 'readonly' else os.O_RDWR) | (os.O_APPEND if mode == 'append' else 0))
    try:
        with pytest.raises(APIError, match='blob-output-unavailable'):
            client.get_blob('/blob', fd=fd, expected_size=3, expected_digest=digest(b'abc'), accept=('application/octet-stream',))
    finally:
        os.close(fd)
    assert not server['requests']


def test_stream_larger_than_metadata_cap_uses_file(server, tmp_path, monkeypatch):
    raw = b'x' * 8_000_001
    server['responses'] = [{'raw': raw, 'content_type': 'application/octet-stream'}]
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    client = HTTPSBlobReadClient(server['origin'], ca_bundle=server['ca'])
    path = tmp_path / 'blob'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        result = client.get_blob('/blob', fd=fd, expected_size=len(raw), expected_digest=digest(raw), accept=('application/octet-stream',))
    finally:
        os.close(fd)
    assert result.size_bytes == len(raw)
    assert not hasattr(result, 'body')
    assert path.read_bytes() == raw


def test_cumulative_transport_budget_is_not_reset(server, tmp_path):
    raw = b'abcdefgh'
    server['responses'] = [{'raw': raw, 'content_type': 'application/octet-stream'}] * 2
    client = HTTPSBlobReadClient(server['origin'], ca_bundle=server['ca'], max_total_bytes=12)
    for index in range(2):
        fd = os.open(tmp_path / str(index), os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            if index:
                with pytest.raises(APIError, match='body-limit'):
                    client.get_blob('/blob', fd=fd, expected_size=8, expected_digest=digest(raw), accept=('application/octet-stream',))
            else:
                client.get_blob('/blob', fd=fd, expected_size=8, expected_digest=digest(raw), accept=('application/octet-stream',))
        finally:
            os.close(fd)


def test_layout_replaced_before_publish_is_rejected(registry, tmp_path, monkeypatch):
    image = install(registry)
    original = adapter._audit_layout
    def swap(*args):
        layout = tmp_path / 'result/layout'
        layout.rename(tmp_path / 'retained-layout')
        layout.mkdir(mode=0o700)
        (layout / 'blobs/sha256').mkdir(mode=0o700, parents=True)
        return original(*args)
    monkeypatch.setattr(adapter, '_audit_layout', swap)
    result = acquire(registry, image, tmp_path)
    assert 'image-blob-output-changed' in codes(result)
    assert not (tmp_path / 'result/layout/oci-layout').exists()


def test_real_pinned_syft_consumes_acquired_image(registry, tmp_path, tools):
    from devsecops.adapters.artifacts import scan_local_artifact
    image = install(registry, payload=b'Metadata-Version: 2.1\nName: controlled-example\nVersion: 1.0\n',
        name='usr/local/lib/python3.11/site-packages/controlled_example-1.0.dist-info/METADATA')
    result = acquire(registry, image, tmp_path)
    assert result['observations']['acquisition']['decision'] == 'pass'
    archive_path = tmp_path / 'acquired-image.tar'
    with tarfile.open(archive_path, 'w') as archive:
        layout = tmp_path / 'result/layout'
        for path in sorted(layout.rglob('*')):
            if path.is_file():
                archive.add(path, arcname=path.relative_to(layout).as_posix())
    scanned = scan_local_artifact(source=archive_path, source_kind='oci-archive',
        expected_sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        expected_image_digest=image['manifest'], architecture='amd64',
        output=tmp_path / 'scan', **tools)
    assert scanned['observations']['sbom']['decision'] == 'pass'
    assert scanned['observations']['sbom']['packages'] >= 1
    assert scanned['decision'] == 'unknown'


def test_cumulative_size_rejected_before_first_layer(registry, tmp_path):
    image = install(registry, compressed=False, payload=b'x' * 65536, copies=2)
    result = acquire(registry, image, tmp_path, max_total_bytes=100_000)
    assert 'image-body-limit' in codes(result)
    assert len(registry['requests']) == 3


def test_modified_frozen_blob_fails_final_readback(registry, tmp_path, monkeypatch):
    image = install(registry)
    original = adapter._audit_layout
    def corrupt(*args):
        path = tmp_path / 'result/layout/blobs/sha256' / image['layer'][7:]
        path.chmod(0o600)
        path.write_bytes(b'corrupt')
        path.chmod(0o400)
        return original(*args)
    monkeypatch.setattr(adapter, '_audit_layout', corrupt)
    result = acquire(registry, image, tmp_path)
    assert 'image-blob-output-changed' in codes(result)
    assert not (tmp_path / 'result/layout/oci-layout').exists()


def test_late_deadline_removes_completion_markers(registry, tmp_path, monkeypatch):
    image = install(registry)
    original = adapter._write
    def expire_after_markers(directory, name, value):
        result = original(directory, name, value)
        if name == 'oci-layout':
            monkeypatch.setattr(adapter.time, 'monotonic', lambda: 10**12)
        return result
    monkeypatch.setattr(adapter, '_write', expire_after_markers)
    result = acquire(registry, image, tmp_path)
    assert 'image-deadline' in codes(result)
    assert not (tmp_path / 'result/layout/index.json').exists()
    assert not (tmp_path / 'result/layout/oci-layout').exists()


def test_gzip_optional_header_scan_has_deadline(tmp_path):
    path = tmp_path / 'long-gzip-name'
    path.write_bytes(b'\x1f\x8b\x08\x08' + b'\0' * 6 + b'x' * 2_000_000)
    started = time.monotonic()
    with path.open('rb') as stream, pytest.raises(APIError, match='deadline'):
        adapter._layer_identity(stream.fileno(), compressed=True, expected_diff='sha256:' + '0' * 64,
            budget={'bytes': 0, 'declared_bytes': 0, 'entries': 0, 'limit': 65536}, deadline=started + .05)
    assert time.monotonic() - started < 1


def test_terminal_receipt_expiry_withdraws_receipt_and_markers(registry, tmp_path, monkeypatch):
    image = install(registry)
    original = adapter._write
    def expire(directory, name, value):
        result = original(directory, name, value)
        if name == 'image-layout.json':
            monkeypatch.setattr(adapter.time, 'monotonic', lambda: 10**12)
        return result
    monkeypatch.setattr(adapter, '_write', expire)
    with pytest.raises(APIError, match='deadline'):
        acquire(registry, image, tmp_path)
    for name in ('image-layout.json', 'layout/index.json', 'layout/oci-layout'):
        assert not (tmp_path / 'result' / name).exists()


def test_terminal_receipt_inner_swap_withdraws_owned_markers(registry, tmp_path, monkeypatch):
    image = install(registry)
    original = adapter._write
    def swap(directory, name, value):
        result = original(directory, name, value)
        if name == 'image-layout.json':
            layout = tmp_path / 'result/layout'
            layout.rename(tmp_path / 'original-layout')
            (layout / 'blobs/sha256').mkdir(mode=0o700, parents=True)
        return result
    monkeypatch.setattr(adapter, '_write', swap)
    with pytest.raises(APIError, match='blob-output-changed'):
        acquire(registry, image, tmp_path)
    assert not (tmp_path / 'result/image-layout.json').exists()
    assert not (tmp_path / 'original-layout/oci-layout').exists()
    assert not (tmp_path / 'original-layout/index.json').exists()


def test_terminal_receipt_marker_change_withdraws_output(registry, tmp_path, monkeypatch):
    image = install(registry)
    original = adapter._write
    def change(directory, name, value):
        result = original(directory, name, value)
        if name == 'image-layout.json':
            (tmp_path / 'result/layout/index.json').write_text('{}')
        return result
    monkeypatch.setattr(adapter, '_write', change)
    with pytest.raises(APIError, match='blob-output-changed'):
        acquire(registry, image, tmp_path)
    for name in ('image-layout.json', 'layout/index.json', 'layout/oci-layout'):
        assert not (tmp_path / 'result' / name).exists()
