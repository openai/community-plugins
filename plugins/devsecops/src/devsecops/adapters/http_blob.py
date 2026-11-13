"""Explicit bounded file streaming, separate from the small metadata API."""
from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import math
import os
import re
import stat
import time

from .http import APIError, HTTPSReadClient

MAX_LAYER = 512 * 1024**2
MAX_TOTAL = 1024**3


@dataclass(frozen=True)
class BlobResponse:
    status: int
    size_bytes: int
    sha256: str
    media_type: str


class _FileTarget:
    def __init__(self, fd, size, digest, deadline):
        self.fd, self.expected_size, self.expected_digest = fd, size, digest
        self.deadline, self.size, self.checksum = deadline, 0, hashlib.sha256()

    def check(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise APIError('deadline')

    def write(self, chunk):
        self.check()
        if self.size + len(chunk) > self.expected_size:
            raise APIError('body-limit')
        offset = 0
        while offset < len(chunk):
            self.check()
            count = os.pwrite(self.fd, chunk[offset:], self.size + offset)
            if count <= 0:
                raise APIError('blob-output-unavailable')
            offset += count
        self.size += len(chunk)
        self.checksum.update(chunk)

    def finish(self, size, media, header_digest):
        self.check()
        actual = 'sha256:' + self.checksum.hexdigest()
        if size != self.expected_size:
            raise APIError('descriptor-size-mismatch')
        if actual != self.expected_digest:
            raise APIError('digest-mismatch')
        if header_digest is not None and header_digest != actual:
            raise APIError('header-digest-mismatch')
        os.fsync(self.fd)
        before = os.fstat(self.fd)
        checksum, offset = hashlib.sha256(), 0
        while offset < size:
            self.check()
            chunk = os.pread(self.fd, min(65536, size-offset), offset)
            if not chunk:
                raise APIError('blob-output-changed')
            checksum.update(chunk)
            offset += len(chunk)
        after = os.fstat(self.fd)
        def identity(s):
            return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
                    s.st_uid, s.st_mode, s.st_nlink)
        if (identity(before) != identity(after) or after.st_size != size
                or after.st_nlink != 1 or after.st_mode & 0o077
                or checksum.hexdigest() != self.checksum.hexdigest()):
            raise APIError('blob-output-changed')
        self.check()
        return BlobResponse(200, size, checksum.hexdigest(), media)


class HTTPSBlobReadClient(HTTPSReadClient):
    """Same TLS/route transport, explicit larger file-only body budget.

    Metadata reads retain the normal 2 MB per-response ceiling. There is no
    redirect, proxy, decompression, range request, or alternate credential route.
    A failed stream leaves only the caller's private incomplete file.
    """
    def __init__(self, origin, *, timeout=30, total_timeout=300,
                 max_layer_bytes=MAX_LAYER, max_total_bytes=MAX_TOTAL,
                 max_requests=136, **kwargs):
        if (any(type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= cap
                for value, cap in ((timeout, 120), (total_timeout, 600)))
                or any(type(value) is not int or not 1 <= value <= cap for value, cap in (
                    (max_layer_bytes, MAX_LAYER), (max_total_bytes, MAX_TOTAL), (max_requests, 160)))
                or 'max_body_bytes' in kwargs):
            raise APIError('invalid-configuration')
        super().__init__(origin, timeout=min(timeout, 30), total_timeout=min(total_timeout, 120),
                         max_total_bytes=min(max_total_bytes, 64_000_000),
                         max_requests=min(max_requests, 64), **kwargs)
        self._timeout, self._total_timeout = float(timeout), float(total_timeout)
        self._max_layer, self._max_total, self._max_requests = max_layer_bytes, max_total_bytes, max_requests

    def get_blob(self, path, *, fd, expected_size, expected_digest, accept, allow_unauthorized=False,
                 _allow_blob_redirect=False, _params=None):
        if (type(fd) is not int or type(expected_size) is not int or not 1 <= expected_size <= self._max_layer
                or not isinstance(expected_digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', expected_digest)
                or type(allow_unauthorized) is not bool or type(_allow_blob_redirect) is not bool
                or not isinstance(accept, (tuple, list))
                or not 1 <= len(accept) <= 8 or any(not isinstance(item, str) or len(item) > 128
                    or not re.fullmatch(r'[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+', item) for item in accept)):
            raise APIError('invalid-request')
        owned = None
        try:
            owned = os.dup(fd)
            info = os.fstat(owned)
            flags = fcntl.fcntl(owned, fcntl.F_GETFL)
            if (not stat.S_ISREG(info.st_mode) or info.st_size != 0 or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or info.st_mode & 0o077
                    or flags & os.O_ACCMODE != os.O_RDWR or flags & os.O_APPEND):
                raise APIError('blob-output-unavailable')
            if self._deadline is None:
                self._deadline = time.monotonic() + self._total_timeout
            target = _FileTarget(owned, expected_size, expected_digest, self._deadline)
            return self._request(path, _params, as_json=False, accept=tuple(accept),
                                 allow_unauthorized=allow_unauthorized, _stream=target,
                                 _allow_blob_redirect=_allow_blob_redirect)
        except OSError:
            raise APIError('blob-output-unavailable') from None
        finally:
            if owned is not None:
                os.close(owned)
