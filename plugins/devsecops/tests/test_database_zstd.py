"""Real bounded zstd archive decoding and native Grype import on a fake DB."""
import gzip
import io
import tarfile
import time

import pytest
import zstandard

from devsecops.adapters import artifacts as a
from devsecops.adapters import database_zstd as decoder
from test_artifacts import database, source, tools, tree_hash

__all__ = ['source', 'tools']


def tar_bytes(name='vulnerability.db', payload=b'SQLite format 3\x00controlled', kind=tarfile.REGTYPE):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        member = tarfile.TarInfo(name)
        member.type, member.size = kind, len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return stream.getvalue()


def write(tmp_path, raw):
    path = tmp_path / 'database.tar.zst'
    path.write_bytes(raw)
    return path


@pytest.mark.parametrize('known_size,checksum', [(True, True), (True, False), (False, True), (False, False)])
def test_complete_frames_and_exact_sqlite_member(tmp_path, known_size, checksum):
    data = tar_bytes()
    compressed = zstandard.ZstdCompressor(write_content_size=known_size, write_checksum=checksum).compress(data)
    assert a._database_archive(write(tmp_path, compressed)) == 'zstd'


def test_complete_bounded_multiframe_tar(tmp_path):
    data = tar_bytes()
    compressor = zstandard.ZstdCompressor(write_content_size=False)
    raw = compressor.compress(data[:1024]) + compressor.compress(data[1024:])
    assert a._database_archive(write(tmp_path, raw)) == 'zstd'


@pytest.mark.parametrize('cut', [1, 2, 4, 7, 10])
def test_truncated_frame_never_passes_even_if_tar_body_decoded(tmp_path, cut):
    compressed = zstandard.ZstdCompressor(write_checksum=True).compress(tar_bytes())
    with pytest.raises(ValueError):
        a._database_archive(write(tmp_path, compressed[:-cut]))


@pytest.mark.parametrize('tail', [b'garbage', b'\x00', b'\x50\x2a\x4d\x18\x00\x00\x00\x00'])
def test_reject_trailing_garbage_and_skippable_frame(tmp_path, tail):
    raw = zstandard.ZstdCompressor().compress(tar_bytes())
    with pytest.raises(ValueError):
        a._database_archive(write(tmp_path, raw + tail))


@pytest.mark.parametrize('name', ['../escape', '/vulnerability.db', 'other.db', 'nested/vulnerability.db'])
def test_zstd_archive_cannot_extract_another_path(tmp_path, name):
    raw = zstandard.ZstdCompressor().compress(tar_bytes(name=name))
    with pytest.raises(ValueError):
        a._database_archive(write(tmp_path, raw))
    assert not (tmp_path / 'escape').exists()


def test_second_tar_after_padding_is_not_hidden(tmp_path):
    raw = zstandard.ZstdCompressor().compress(tar_bytes() + tar_bytes(name='other.db'))
    with pytest.raises(ValueError):
        a._database_archive(write(tmp_path, raw))


def test_damaged_frame_checksum_rejected(tmp_path):
    raw = zstandard.ZstdCompressor(write_checksum=True).compress(tar_bytes())
    with pytest.raises(ValueError):
        a._database_archive(write(tmp_path, raw[:-1] + bytes([raw[-1] ^ 1])))


def test_decoded_limit_applies_without_declared_content_size(tmp_path, monkeypatch):
    monkeypatch.setattr(decoder, 'MAX_DECODED', 20000)
    raw = zstandard.ZstdCompressor(write_content_size=False).compress(tar_bytes(payload=b'SQLite format 3\x00' + b'0' * 100000))
    with pytest.raises(a._Invalid) as error:
        a._database_archive(write(tmp_path, raw))
    assert error.value.code == 'database-decompression-limit'


def test_declared_content_size_limit_checked_before_decode(tmp_path, monkeypatch):
    monkeypatch.setattr(decoder, 'MAX_DECODED', 1000)
    raw = zstandard.ZstdCompressor().compress(tar_bytes())
    with pytest.raises(a._Invalid) as error:
        a._database_archive(write(tmp_path, raw))
    assert error.value.code == 'database-decompression-limit'


def test_window_limit_checked_before_decoder_allocation(tmp_path, monkeypatch):
    # Standard frame, unknown content size, 256 MiB window, empty final block.
    raw = decoder.MAGIC + b'\x00\x90\x01\x00\x00'
    assert zstandard.get_frame_parameters(raw).window_size == 256 * 1024**2
    monkeypatch.setattr(zstandard, 'ZstdDecompressor', lambda **kwargs: pytest.fail('oversized window reached decoder'))
    with pytest.raises(a._Invalid) as error:
        a._database_archive(write(tmp_path, raw))
    assert error.value.code == 'database-zstd-frame-limit'


def test_current_official_header_window_accepted_by_pinned_backend(tmp_path):
    raw = zstandard.ZstdCompressor(write_content_size=False, write_checksum=True).compress(tar_bytes())
    # Original six-byte header from the SHA-pinned 2026-10-27 official package.
    # Raising this controlled frame's declared window leaves its blocks/checksum
    # unchanged. It proves the real decoder accepts the publisher's 128MiB form.
    header = bytes.fromhex('28b52ffd0488')
    assert zstandard.frame_header_size(raw) == len(header)
    raw = header + raw[len(header):]
    assert zstandard.get_frame_parameters(raw).window_size == decoder.MAX_WINDOW == 128 * 1024**2
    # The pinned C backend uses bytes here. A KiB-scaled argument must not
    # accidentally become a 128MiB native allowance beneath our header guard.
    with pytest.raises(zstandard.ZstdError, match='too much memory'):
        with zstandard.ZstdDecompressor(max_window_size=128 * 1024).stream_reader(io.BytesIO(raw)) as stream:
            stream.read()
    assert a._database_archive(write(tmp_path, raw)) == 'zstd'


def test_pinned_backend_accepts_actual_one_megabyte_window(tmp_path):
    raw = zstandard.ZstdCompressor(write_content_size=False).compress(tar_bytes(payload=b'SQLite format 3\x00' + b'0' * 1_000_000))
    assert zstandard.get_frame_parameters(raw).window_size >= 1_000_000
    assert a._database_archive(write(tmp_path, raw)) == 'zstd'


def test_frame_count_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(decoder, 'MAX_FRAMES', 2)
    empty = zstandard.ZstdCompressor().compress(b'')
    raw = empty * 3 + zstandard.ZstdCompressor().compress(tar_bytes())
    with pytest.raises(a._Invalid) as error:
        a._database_archive(write(tmp_path, raw))
    assert error.value.code == 'database-zstd-frame-limit'


def test_block_count_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(decoder, 'MAX_BLOCKS', 2)
    # Three legal empty raw blocks, with the last-block flag on the final one.
    raw = decoder.MAGIC + b'\x00\x00' + b'\x00\x00\x00' * 2 + b'\x01\x00\x00'
    with pytest.raises(a._Invalid) as error:
        a._database_archive(write(tmp_path, raw))
    assert error.value.code == 'database-zstd-block-limit'


def test_decoder_shares_absolute_deadline(tmp_path):
    raw = zstandard.ZstdCompressor().compress(tar_bytes())
    with pytest.raises(a._Invalid) as error:
        a._database_archive(write(tmp_path, raw), deadline=time.monotonic() - 1)
    assert 'deadline' in error.value.code


def test_gzip_path_stays_supported(tmp_path):
    assert a._database_archive(write(tmp_path, gzip.compress(tar_bytes()))) == 'gzip'


def test_decoded_temp_stays_beside_owned_frozen_archive(tmp_path, monkeypatch):
    original = decoder.tempfile.TemporaryFile
    selected = []
    def temporary(*args, **kwargs):
        selected.append(kwargs['dir'])
        return original(*args, **kwargs)
    monkeypatch.setattr(decoder.tempfile, 'TemporaryFile', temporary)
    raw = zstandard.ZstdCompressor().compress(tar_bytes())
    assert a._database_archive(write(tmp_path, raw)) == 'zstd'
    assert selected == [tmp_path]


def test_real_pinned_grype_imports_zstd_database(source, tmp_path, tools):
    fixture = database(tmp_path)
    unpacked_tar = gzip.decompress(fixture['database_snapshot'].read_bytes())
    compressed = zstandard.ZstdCompressor(write_content_size=False, write_checksum=True).compress(unpacked_tar)
    path = write(tmp_path, compressed)
    result = a.scan_local_artifact(source=source, source_kind='directory',
        expected_sha256=tree_hash(source, tmp_path), output=tmp_path / 'scan', **tools,
        database_snapshot=path, database_sha256=a._hash(compressed))
    obs = result['observations']
    assert obs['database']['decision'] == 'pass' and obs['database']['compression'] == 'zstd'
    assert obs['database']['sha256'] == a._hash(compressed)
    assert obs['vulnerabilities']['decision'] == 'pass'
    assert result['decision'] == 'unknown'  # Fake DB mechanics never establish genuine CVE coverage.
