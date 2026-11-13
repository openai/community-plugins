"""Fixed offline runtime recipe and content-bound execution proof handling."""
from copy import deepcopy
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import time

import pytest

from devsecops.factory import runtime_recipe as adapter
from devsecops.factory import qualify_builder
from test_image_layout import install, acquire
from test_registry_evidence import registry, ca

__all__ = ['registry', 'ca']


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def recipe():
    return {'kind': 'mission-python-runtime-v1', 'repository': 'registry1.dso.mil/ironbank/opensource/python/python',
        'requested_digest': 'sha256:' + '1' * 64, 'manifest_digest': 'sha256:' + '2' * 64,
        'architecture': 'amd64', 'nonce': '3' * 32}


def test_fixed_offline_context_binds_requested_and_platform_digests():
    value, root = recipe(), Path('/tmp/owned-runtime')
    commands = qualify_builder._commands(root, value)
    argv = commands['buildctl']
    expected = 'context:' + value['repository'] + '@' + value['requested_digest']
    expected += '=oci-layout://qualified-base@' + value['manifest_digest']
    assert expected in argv and 'qualified-base=/tmp/owned-runtime/base/layout' in argv
    assert '--no-cache' in argv and 'platform=linux/amd64' in argv
    assert '--net=none' in commands['rootlesskit']
    assert commands == qualify_builder._commands(root, deepcopy(value))
    assert qualify_builder._runtime_names({'recipe': value})[-1] == 'base'
    assert 'base' not in qualify_builder._runtime_names({})


@pytest.mark.parametrize('field,value', [('kind', 'custom'), ('repository', 'docker.io/python'),
    ('manifest_digest', 'latest'), ('architecture', 'windows'), ('nonce', '../evil')])
def test_no_arbitrary_probe_or_substitute_base(field, value):
    selected = recipe()
    selected[field] = value
    with pytest.raises((TypeError, ValueError)):
        adapter.descriptor(selected)


def prepared_inputs(registry, tmp_path):
    image = install(registry)
    acquired = acquire(registry, image, tmp_path)
    root = tmp_path / 'runtime'
    (root / 'fixture').mkdir(parents=True, mode=0o700)
    selected = recipe()
    selected.update(requested_digest=image['requested'], manifest_digest=image['manifest'])
    wheel = Path.home() / '.cache/devsecops/wheelhouse/prometheus_client-0.26.0-py3-none-any.whl'
    if not wheel.is_file():
        pytest.skip('Real pinned offline dependency wheel is not present')
    return dict(root=root, recipe=selected, acquired=(tmp_path / 'result', acquired['observations']['layout']),
        wheel=wheel, deadline=time.monotonic() + 10)


def test_freezes_real_acquired_layout_and_locked_wheel_without_candidate_code(registry, tmp_path):
    args = prepared_inputs(registry, tmp_path)
    result = adapter.prepare(**args)
    root = args['root']
    dockerfile = (root / 'fixture/Dockerfile').read_text()
    assert dockerfile.startswith('FROM ' + args['recipe']['repository'] + '@' + args['recipe']['requested_digest'] + ' AS probe\n')
    assert dockerfile.count('RUN --network=none') == 2
    assert 'USER 10001:10001' in dockerfile and 'COPY --chmod=0444' in dockerfile
    assert 'FROM scratch\nCOPY --from=probe /tmp/devsecops-runtime-proof.json /proof.json' in dockerfile
    assert result['layout'] == args['acquired'][1]['sha256']
    assert not (root / 'fixture/app.py').exists()
    for name in ('root.py', 'uid.py'):
        compile((root / 'fixture' / name).read_bytes(), name, 'exec')
    script = (root / 'fixture/root.py').read_text()
    for flag in ('--no-index', '--require-hashes', '--no-deps', '--isolated', '--only-binary=:all:'):
        assert flag in script


@pytest.mark.parametrize('damage', ['wheel', 'blob', 'marker', 'inventory', 'manifest'])
def test_changed_inputs_never_start_a_runtime(registry, tmp_path, damage):
    args = prepared_inputs(registry, tmp_path)
    source, observation = args['acquired']
    if damage == 'wheel':
        fake = tmp_path / 'fake.whl'
        fake.write_bytes(b'not the locked wheel')
        args['wheel'] = fake
    elif damage == 'blob':
        path = next((source / 'layout/blobs/sha256').iterdir())
        path.chmod(0o600)
        path.write_bytes(b'corrupt')
        path.chmod(0o400)
    elif damage == 'marker':
        path = source / 'layout/index.json'
        path.chmod(0o600)
        path.write_bytes(b'{}')
    elif damage == 'inventory':
        observation['sha256'] = '0' * 64
    else:
        args['recipe']['manifest_digest'] = 'sha256:' + '0' * 64
    with pytest.raises((ValueError, OSError)):
        adapter.prepare(**args)


def proof_document():
    return {'contract': 'mission-python-runtime-v1', 'nonce': recipe()['nonce'],
        'python': [3, 11, 11], 'machine': 'x86_64', 'root_uid': 0, 'root_gid': 0,
        'uid': 10001, 'gid': 10001, 'venv': True, 'ensurepip': True,
        'dependency': {'name': 'prometheus-client', 'version': '0.26.0'}}


def archive_file(path, document, *, compressed=True, extra=False, tamper_layer=False, wrong_manifest=False):
    def tar_file(archive, name, raw):
        member = tarfile.TarInfo(name)
        member.mode, member.size = 0o644, len(raw)
        archive.addfile(member, io.BytesIO(raw))
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        tar_file(archive, 'proof.json', encode(document))
        if extra:
            tar_file(archive, 'another.json', b'{}')
    layer = gzip.compress(stream.getvalue(), mtime=0) if compressed else stream.getvalue()
    descriptor = {'digest': digest(layer), 'size': len(layer),
        'mediaType': 'application/vnd.oci.image.layer.v1.tar' + ('+gzip' if compressed else '')}
    manifest = encode({'layers': [descriptor]})
    identity = digest(manifest)
    with tarfile.open(path, 'w') as archive:
        tar_file(archive, 'blobs/sha256/' + identity[7:], b'{}' if wrong_manifest else manifest)
        tar_file(archive, 'blobs/sha256/' + descriptor['digest'][7:], b'x' * len(layer) if tamper_layer else layer)
    return identity


@pytest.mark.parametrize('compressed', [True, False])
def test_reads_content_bound_fixed_runtime_proof(tmp_path, compressed):
    path = tmp_path / 'proof.oci.tar'
    document = proof_document()
    identity = archive_file(path, document, compressed=compressed)
    assert adapter.proof(path, identity, recipe(), '0.26.0', time.monotonic() + 2) == document


@pytest.mark.parametrize('field,value', [('nonce', '0' * 32), ('uid', 0), ('gid', 0), ('root_uid', False),
    ('venv', 1), ('ensurepip', False), ('machine', 'aarch64'), ('python', [3, 10, 9]),
    ('dependency', {'name': 'prometheus-client', 'version': '0.0.0'})])
def test_wrong_runtime_proof_cannot_pass(tmp_path, field, value):
    path = tmp_path / 'proof.oci.tar'
    document = proof_document()
    document[field] = value
    identity = archive_file(path, document)
    with pytest.raises(ValueError, match='fixed contract'):
        adapter.proof(path, identity, recipe(), '0.26.0', time.monotonic() + 2)


@pytest.mark.parametrize('option', ['extra', 'tamper_layer', 'wrong_manifest'])
def test_changed_or_extra_artifact_bytes_fail(tmp_path, option):
    path = tmp_path / 'proof.oci.tar'
    identity = archive_file(path, proof_document(), **{option: True})
    with pytest.raises(ValueError):
        adapter.proof(path, identity, recipe(), '0.26.0', time.monotonic() + 2)


def test_expired_runtime_proof_does_not_open_archive(tmp_path):
    with pytest.raises(ValueError, match='deadline'):
        adapter.proof(tmp_path / 'missing.tar', 'sha256:' + '0' * 64, recipe(), '0.26.0', 0)
