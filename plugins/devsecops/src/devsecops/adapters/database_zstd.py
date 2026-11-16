"""Bounded Zstandard decoding for the official Grype database archive format."""
from __future__ import annotations

from contextlib import contextmanager
import os
import stat
import tempfile
import time

import zstandard

from .signatures import _Invalid

MAGIC = b'\x28\xb5\x2f\xfd'
MAX_COMPRESSED = 1_000_000_000
MAX_DECODED = 3_010_000_000
MAX_WINDOW = 128 * 1024**2
MAX_FRAMES = 16
MAX_BLOCKS = 65536


def _checkpoint(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise _Invalid('database-decompression-deadline')


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _frames(stream, size, deadline):
    """Check complete frame boundaries before the streaming decoder.

python-zstandard's reader can return EOF for a truncated frame. This bounded
structural pass rejects truncation, trailing garbage, dictionaries/skippable
frames and oversized windows; the decoder still verifies block/checksum data.
    """
    frames = blocks = 0
    declared = 0
    while stream.tell() < size:
        _checkpoint(deadline)
        start = stream.tell()
        header = stream.read(min(18, size-start))
        if not header.startswith(MAGIC) or len(header) < 6:
            raise _Invalid('database-zstd-frame-unsupported')
        parameters = zstandard.get_frame_parameters(header)
        length = zstandard.frame_header_size(header)
        frames += 1
        if (frames > MAX_FRAMES or parameters.dict_id or parameters.window_size > MAX_WINDOW
                or start + length > size):
            raise _Invalid('database-zstd-frame-limit')
        if parameters.content_size != zstandard.CONTENTSIZE_UNKNOWN:
            declared += parameters.content_size
            if declared > MAX_DECODED:
                raise _Invalid('database-decompression-limit')
        stream.seek(start + length)
        last = False
        while not last:
            _checkpoint(deadline)
            raw = stream.read(3)
            if len(raw) != 3:
                raise _Invalid('database-zstd-truncated')
            value = int.from_bytes(raw, 'little')
            last, kind, length = bool(value & 1), (value >> 1) & 3, value >> 3
            blocks += 1
            if kind == 3 or length > 131072 or blocks > MAX_BLOCKS:
                raise _Invalid('database-zstd-block-limit')
            count = 1 if kind == 1 else length
            end = stream.tell() + count + (4 if last and parameters.has_checksum else 0)
            if end > size:
                raise _Invalid('database-zstd-truncated')
            stream.seek(end)
    if not frames:
        raise _Invalid('database-zstd-frame-unsupported')
    stream.seek(0)


@contextmanager
def decoded_database_tar(path, *, deadline=None):
    """Yield a private temporary tar stream; no member is extracted to a path."""
    if zstandard.__version__ != '0.25.0':
        raise _Invalid('database-zstd-runtime-unqualified')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        source = os.fdopen(fd, 'rb')
        fd = None
        with source, tempfile.TemporaryFile(mode='w+b', dir=path.parent) as decoded:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= MAX_COMPRESSED:
                raise _Invalid('database-zstd-input-limit')
            _frames(source, before.st_size, deadline)
            class Source:
                def read(self, count):
                    _checkpoint(deadline)
                    return source.read(min(count, 65536))
            total = 0
            # The pinned C backend enforces this argument in bytes. Header
            # window_size is independently checked in bytes before allocation.
            with zstandard.ZstdDecompressor(max_window_size=MAX_WINDOW).stream_reader(
                    Source(), read_size=65536, read_across_frames=True) as reader:
                while True:
                    _checkpoint(deadline)
                    chunk = reader.read(min(65536, MAX_DECODED + 1 - total))
                    _checkpoint(deadline)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_DECODED:
                        raise _Invalid('database-decompression-limit')
                    decoded.write(chunk)
            if _identity(before) != _identity(os.fstat(source.fileno())):
                raise _Invalid('database-zstd-input-changed')
            decoded.flush()
            decoded.seek(0)
            _checkpoint(deadline)
            yield decoded
            _checkpoint(deadline)
    except zstandard.ZstdError:
        raise _Invalid('database-zstd-invalid') from None
    finally:
        if fd is not None:
            os.close(fd)
