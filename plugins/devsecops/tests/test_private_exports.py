"""Native filesystem contracts for private BuildKit outputs; no live-build claim."""
import os
import stat
import subprocess
import sys
import time

import pytest

from devsecops.factory import qualify_builder as q


@pytest.fixture
def output(tmp_path):
    root = tmp_path.resolve() / 'output'
    root.mkdir(mode=0o700)
    return root


@pytest.mark.parametrize('launcher_umask', [0, 0o077, 0o777])
def test_native_child_umask_reset_preserves_reserved_image_and_metadata_is_sealed(output, launcher_umask):
    script = """import os, pathlib
os.umask(0)
pathlib.Path('image.oci.tar').write_bytes(b'export bytes')
pathlib.Path('metadata.tmp').write_bytes(b'{"containerimage.digest":"sha256:test"}')
os.replace('metadata.tmp', 'build-metadata.json')
"""
    with q._private_image_export(output):
        original = (output / 'image.oci.tar').stat()
        subprocess.run([sys.executable, '-I', '-B', '-c', script], cwd=output,
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
                       umask=launcher_umask, check=True)
    image = output / 'image.oci.tar'
    assert (image.stat().st_dev, image.stat().st_ino) == (original.st_dev, original.st_ino)
    assert stat.S_IMODE(image.stat().st_mode) == 0o600
    assert q._image_receipt(image, 64, time.monotonic() + 10)['archive_bytes'] == 12
    metadata = output / 'build-metadata.json'
    assert stat.S_IMODE(metadata.stat().st_mode) == 0o666
    assert q._private_export_metadata(output) == b'{"containerimage.digest":"sha256:test"}'
    assert stat.S_IMODE(metadata.stat().st_mode) == 0o600


@pytest.mark.parametrize('existing', ['regular', 'symlink'])
def test_image_reservation_never_overwrites_existing_files(output, existing):
    outside = output.parent / 'outside'
    outside.write_bytes(b'preserve')
    target = output / 'image.oci.tar'
    if existing == 'regular':
        target.write_bytes(b'preserve')
    else:
        target.symlink_to(outside)
    with pytest.raises(FileExistsError), q._private_image_export(output):
        pytest.fail('A preexisting output was admitted')
    assert outside.read_bytes() == b'preserve' and target.read_bytes() == b'preserve'


def test_replaced_image_inode_is_rejected_even_when_replacement_is_private(output):
    with pytest.raises(q.QualificationError, match='output-image-replaced'):
        with q._private_image_export(output):
            replacement = output / 'replacement'
            replacement.write_bytes(b'unrelated')
            replacement.chmod(0o600)
            os.replace(replacement, output / 'image.oci.tar')


def test_image_permission_tampering_still_fails_the_existing_validator(output):
    with q._private_image_export(output):
        target = output / 'image.oci.tar'
        target.write_bytes(b'export bytes')
        target.chmod(0o666)
    with pytest.raises(q.QualificationError, match='output-image-authority'):
        q._image_receipt(target, 64, time.monotonic() + 10)


def test_image_descriptor_closes_when_the_producer_raises(output, monkeypatch):
    native_open, descriptors = os.open, []
    def track(path, *args, **kwargs):
        descriptor = native_open(path, *args, **kwargs)
        if path == 'image.oci.tar':
            descriptors.append(descriptor)
        return descriptor
    monkeypatch.setattr(q.os, 'open', track)
    with pytest.raises(RuntimeError, match='producer failure'):
        with q._private_image_export(output):
            raise RuntimeError('producer failure')
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'directory', 'empty', 'oversized'])
def test_invalid_metadata_is_rejected_without_changing_other_file_permissions(output, kind):
    outside = output.parent / 'outside'
    outside.write_bytes(b'preserve')
    outside.chmod(0o644)
    target = output / 'build-metadata.json'
    if kind == 'symlink':
        target.symlink_to(outside)
    elif kind == 'hardlink':
        os.link(outside, target)
    elif kind == 'fifo':
        os.mkfifo(target, 0o600)
    elif kind == 'directory':
        target.mkdir()
    else:
        target.write_bytes(b'' if kind == 'empty' else b'x' * 65)
    with pytest.raises((OSError, q.QualificationError)):
        q._private_export_metadata(output, limit=64)
    assert outside.read_bytes() == b'preserve'
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644


def test_shared_parent_is_rejected_before_metadata_is_modified(output):
    target = output / 'build-metadata.json'
    target.write_bytes(b'{}')
    target.chmod(0o666)
    output.chmod(0o755)
    try:
        with pytest.raises(q.QualificationError, match='run-directory-authority'):
            q._private_export_metadata(output)
        with pytest.raises(q.QualificationError, match='run-directory-authority'), q._private_image_export(output):
            pytest.fail('Shared parent admitted')
        assert stat.S_IMODE(target.stat().st_mode) == 0o666
        assert not (output / 'image.oci.tar').exists()
    finally:
        output.chmod(0o700)


def test_foreign_metadata_owner_is_rejected_before_chmod(output, monkeypatch):
    target = output / 'build-metadata.json'
    target.write_bytes(b'{}')
    target.chmod(0o666)
    native_fstat = os.fstat
    def foreign_file(descriptor):
        value = native_fstat(descriptor)
        if stat.S_ISREG(value.st_mode):
            fields = list(value)
            fields[4] = os.getuid() + 1
            return os.stat_result(fields)
        return value
    monkeypatch.setattr(q.os, 'fstat', foreign_file)
    with pytest.raises(q.QualificationError, match='output-metadata-authority'):
        q._private_export_metadata(output)
    assert stat.S_IMODE(target.stat().st_mode) == 0o666


def test_replaced_metadata_path_is_rejected_during_read(output, monkeypatch):
    target = output / 'build-metadata.json'
    target.write_bytes(b'{}')
    native_read = os.read
    def replace_during_read(descriptor, limit):
        raw = native_read(descriptor, limit)
        if raw:
            replacement = output / 'replacement'
            replacement.write_bytes(b'{}')
            replacement.chmod(0o600)
            os.replace(replacement, target)
        return raw
    monkeypatch.setattr(q.os, 'read', replace_during_read)
    with pytest.raises(q.QualificationError, match='output-metadata-changed'):
        q._private_export_metadata(output)


@pytest.mark.parametrize('operation', ['image', 'metadata'])
def test_output_directory_route_replacement_is_rejected(output, operation, monkeypatch):
    target = output / 'build-metadata.json'
    target.write_bytes(b'{}')
    if operation == 'image':
        with pytest.raises(q.QualificationError, match='run-directory-identity-changed'):
            with q._private_image_export(output):
                output.rename(output.with_name('retained'))
                output.mkdir(mode=0o700)
    else:
        native_fchmod = os.fchmod
        def swap_after_chmod(descriptor, mode):
            native_fchmod(descriptor, mode)
            output.rename(output.with_name('retained'))
            output.mkdir(mode=0o700)
        monkeypatch.setattr(q.os, 'fchmod', swap_after_chmod)
        with pytest.raises(q.QualificationError, match='run-directory-identity-changed'):
            q._private_export_metadata(output)
    assert not (output / 'image.oci.tar').exists()


@pytest.mark.parametrize('kind', ['directory', 'fifo', 'empty', 'read-error'])
def test_metadata_descriptor_closes_on_invalid_input_and_read_failure(output, monkeypatch, kind):
    target = output / 'build-metadata.json'
    if kind == 'directory':
        target.mkdir()
    elif kind == 'fifo':
        os.mkfifo(target, 0o600)
    else:
        target.write_bytes(b'' if kind == 'empty' else b'{}')
    native_open, descriptors = os.open, []
    def track(path, *args, **kwargs):
        descriptor = native_open(path, *args, **kwargs)
        if path == 'build-metadata.json':
            descriptors.append(descriptor)
        return descriptor
    monkeypatch.setattr(q.os, 'open', track)
    if kind == 'read-error':
        def fail_read(*args):
            raise OSError('controlled read failure')
        monkeypatch.setattr(q.os, 'read', fail_read)
    with pytest.raises((OSError, q.QualificationError)):
        q._private_export_metadata(output)
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


def test_metadata_link_change_while_sealing_is_rejected(output, monkeypatch):
    target = output / 'build-metadata.json'
    target.write_bytes(b'{}')
    native_fchmod = os.fchmod
    def add_link(descriptor, mode):
        native_fchmod(descriptor, mode)
        os.link(target, output / 'unexpected-link')
    monkeypatch.setattr(q.os, 'fchmod', add_link)
    with pytest.raises(q.QualificationError, match='output-metadata-changed'):
        q._private_export_metadata(output)
