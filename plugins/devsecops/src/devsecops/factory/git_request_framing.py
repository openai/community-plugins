"""Bounded local Git framing checks; no encoder, I/O, or delivery authority.

Advertisements remain untrusted input. Matching their selected base and target
does not establish freshness, credentials, permissions, or remote possession.
The returned pack still requires native validation and an exact object audit.
"""
from __future__ import annotations

import re

MAX_ADVERTISEMENT = 2 * 1024 * 1024
MAX_REFS = 10_000
MAX_BODY = 9 * 1024 * 1024
MAX_OBJECTS = 18
_OID = re.compile(rb'[0-9a-f]{40}')
_HEX = re.compile(rb'[0-9a-fA-F]{4}')
_AD_FLAGS = frozenset((b'report-status', b'report-status-v2', b'delete-refs',
                      b'side-band-64k', b'quiet', b'atomic', b'ofs-delta', b'push-options'))
_COMMAND_FLAGS = frozenset((b'report-status', b'report-status-v2', b'side-band-64k', b'quiet'))


def _ref(value, *, head=False):
    if head and value == 'HEAD':
        return value.encode()
    if (type(value) is not str or not 1 <= len(value) <= 1024
            or not value.startswith('refs/') or len(value.split('/')) < 3
            or '..' in value or '@{' in value or value.endswith('.')
            or any(not part or part.startswith('.') or part.endswith('.lock')
                   or not re.fullmatch(r'[A-Za-z0-9_+@=.-]+', part) for part in value.split('/'))):
        raise ValueError('Unsupported Git ref')
    return value.encode('ascii')


def _selected(ref, commit):
    encoded = _ref(ref)
    if (not encoded.startswith(b'refs/heads/') or type(commit) is not str
            or not re.fullmatch(r'[0-9a-f]{40}', commit) or commit == '0' * 40):
        raise ValueError('Exact nonzero SHA-1 and branch required')
    return encoded, commit.encode('ascii')


def _packet(raw, offset):
    header = raw[offset:offset + 4]
    if not _HEX.fullmatch(header):
        raise ValueError('Invalid Git packet header')
    size = int(header, 16)
    offset += 4
    if size == 0:
        return None, offset
    if not 4 < size <= 65520 or offset + size - 4 > len(raw):
        raise ValueError('Truncated or unsupported Git packet')
    return raw[offset:offset + size - 4], offset + size - 4


def _capabilities(raw, *, command=False):
    if not 0 < len(raw) <= 4096:
        raise ValueError('Invalid Git capability size')
    tokens = raw.split(b' ')
    if len(tokens) > 64:
        raise ValueError('Too many Git capabilities')
    result = {}
    flags = _COMMAND_FLAGS if command else _AD_FLAGS
    for token in tokens:
        name, equals, value = token.partition(b'=')
        if not token or len(token) > 1024 or name in result:
            raise ValueError('Malformed or duplicate Git capability')
        if name in flags and not equals:
            result[name] = True
        elif name == b'object-format' and equals and value == b'sha1':
            result[name] = value
        elif name == b'agent' and equals and re.fullmatch(rb'[A-Za-z0-9._/+()-]{1,128}', value):
            result[name] = value
        else:
            raise ValueError('Unsupported Git capability')
    status = {b'report-status', b'report-status-v2'} & result.keys()
    if not status or command and len(status) != 1:
        raise ValueError('Exact report-status capability required')
    return result


def receive_advertisement(raw, *, base_ref, base_commit, target_ref):
    """Validate a bounded receive-pack advertisement and return its native refs."""
    base_name, base_oid = _selected(base_ref, base_commit)
    target = _ref(target_ref)
    if not target.startswith(b'refs/heads/') or target == base_name:
        raise ValueError('Distinct target branch required')
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_ADVERTISEMENT:
        raise ValueError('Git advertisement size or type unsupported')
    service, offset = _packet(raw, 0)
    separator, offset = _packet(raw, offset)
    if service != b'# service=git-receive-pack\n' or separator is not None:
        raise ValueError('Exact receive-pack service required')
    start, refs = offset, {}
    while True:
        row, offset = _packet(raw, offset)
        if row is None:
            break
        if len(refs) >= MAX_REFS or len(row) > 8192 or not row.endswith(b'\n'):
            raise ValueError('Git advertisement ref bound or line shape')
        row = row[:-1]
        if not refs:
            if row.count(b'\0') != 1:
                raise ValueError('First ref requires exact capability separator')
            row, capabilities = row.split(b'\0')
            _capabilities(capabilities)
        elif b'\0' in row:
            raise ValueError('Only first ref may carry capabilities')
        try:
            oid, name = row.split(b' ')
            _ref(name.decode('ascii'), head=True)
        except (ValueError, UnicodeError):
            raise ValueError('Malformed Git advertised ref') from None
        if not _OID.fullmatch(oid) or oid == b'0' * 40 or name in refs:
            raise ValueError('Invalid or duplicate Git advertised ref')
        refs[name] = oid
    if offset != len(raw) or not refs:
        raise ValueError('Trailing or empty Git advertisement')
    if refs.get(base_name) != base_oid or target in refs:
        raise ValueError('Advertised base or target differs from selection')
    return raw[start:]


def command_pack(body, *, new_commit, target_ref):
    """Require one native create-only command and return its unverified pack."""
    target, new_oid = _selected(target_ref, new_commit)
    if type(body) is not bytes or not 0 < len(body) <= MAX_BODY:
        raise ValueError('Git request size or type unsupported')
    row, offset = _packet(body, 0)
    separator, offset = _packet(body, offset)
    if row is None or separator is not None or row.count(b'\0') != 1:
        raise ValueError('Exactly one Git command required')
    command, capabilities = row.split(b'\0')
    if command != b'0' * 40 + b' ' + new_oid + b' ' + target:
        raise ValueError('Exact create-only Git command required')
    # Native send-pack inserts one space after NUL. Protocol producers can omit
    # that space and the optional final LF; neither permits arbitrary whitespace.
    if capabilities.startswith(b' '):
        capabilities = capabilities[1:]
    if capabilities.endswith(b'\n'):
        capabilities = capabilities[:-1]
    _capabilities(capabilities, command=True)
    pack = body[offset:]
    if (len(pack) < 32 or not pack.startswith(b'PACK\0\0\0\2')
            or not 1 <= int.from_bytes(pack[8:12], 'big') <= MAX_OBJECTS):
        raise ValueError('Git pack header or object count unsupported')
    return pack


def read_body(child):
    """Unwrap a bounded native stateless request; child owns time and I/O limits."""
    chunks, size = [], 0
    while True:
        header = child.read(4)
        if type(header) is not bytes or not _HEX.fullmatch(header):
            raise ValueError('Invalid native Git packet header')
        length = int(header, 16)
        if length == 0:
            break
        if not 4 < length <= 65520 or size + length - 4 > MAX_BODY:
            raise ValueError('Native Git request exceeds its bound')
        raw = child.read(length - 4)
        if type(raw) is not bytes or len(raw) != length - 4:
            raise ValueError('Truncated native Git packet')
        chunks.append(raw)
        size += len(raw)
    if not chunks:
        raise ValueError('Native Git request is empty')
    return b''.join(chunks)
