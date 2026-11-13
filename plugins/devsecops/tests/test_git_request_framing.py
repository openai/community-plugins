"""Local framing adversaries; no network or remote authority."""
import hashlib
import io
import os
import subprocess

import pytest

from devsecops.factory import git_request_framing as framing
from tests.native_git import selected_git

BASE = 'a' * 40
NEW = 'b' * 40
BASE_REF = 'refs/heads/main'
TARGET = 'refs/heads/demo-review'
CAPS = b'report-status report-status-v2 delete-refs side-band-64k quiet atomic ofs-delta object-format=sha1 agent=git/2.54.0-Darwin'
COMMAND_CAPS = b' report-status-v2 side-band-64k quiet object-format=sha1 agent=git/2.54.0-Darwin'
PACK = b'PACK\0\0\0\2\0\0\0\1' + b'not-a-validated-pack!'  # Parser does not verify pack objects.


def pkt(raw):
    return f'{len(raw) + 4:04x}'.encode() + raw


def advert(*, caps=CAPS, rows=None, service=b'# service=git-receive-pack\n'):
    if rows is None:
        rows = [BASE.encode() + b' refs/heads/main\0' + caps + b'\n']
    return pkt(service) + b'0000' + b''.join(pkt(row) for row in rows) + b'0000'


def command(*, old='0' * 40, new=NEW, target=TARGET, caps=COMMAND_CAPS, pack=PACK):
    return pkt((old + ' ' + new + ' ' + target).encode() + b'\0' + caps) + b'0000' + pack


def receive(raw):
    return framing.receive_advertisement(raw, base_ref=BASE_REF, base_commit=BASE, target_ref=TARGET)


def unpack(raw):
    return framing.command_pack(raw, new_commit=NEW, target_ref=TARGET)


def test_advertisement_returns_unchanged_native_refs():
    raw = advert(rows=[BASE.encode() + b' HEAD\0' + CAPS + b'\n',
                       BASE.encode() + b' refs/heads/main\n', b'c' * 40 + b' refs/tags/release\n'])
    assert receive(raw) == raw[len(pkt(b'# service=git-receive-pack\n')) + 4:]


@pytest.mark.parametrize('caps', [b'', b'quiet', b'report-status report-status',
    b'report-status agent=one agent=two', b'report-status object-format=sha256',
    b'report-status object-format=sha1 object-format=sha1', b'report-status unknown',
    b'report-status\x00quiet', b'report-status\nquiet', b'report-status\tquiet',
    b' report-status', b'report-status ', b'report-status  quiet',
    b'report-status agent=git;unsafe', b'report-status agent=' + b'a' * 129])
def test_advertisement_rejects_capability_ambiguity(caps):
    with pytest.raises(ValueError):
        receive(advert(caps=caps))


@pytest.mark.parametrize('extra', [BASE.encode() + b' refs/heads/main\n',
    BASE.encode() + b' refs/heads/demo-review\n', BASE.encode() + b' refs/heads/other\0quiet\n',
    BASE.encode() + b' refs/heads/../escape\n', BASE.encode() + b' refs/heads/a.lock\n',
    BASE.encode() + b' refs/heads/a b\n', BASE.encode() + b' refs/heads/a\r\n',
    b'0' * 40 + b' refs/heads/empty\n', b'c' * 40 + b' refs/heads/other',
    b'C' * 40 + b' refs/heads/other\n'])
def test_advertisement_rejects_duplicate_target_and_malformed_refs(extra):
    with pytest.raises(ValueError):
        receive(advert(rows=[BASE.encode() + b' refs/heads/main\0' + CAPS + b'\n', extra]))


@pytest.mark.parametrize('raw', [advert() + b'0000', advert()[:-1], advert()[:-4],
    b'0001', b'0002', b'0003', b'0004', b'ffffabc', b'gggg', b'', bytearray(advert()),
    advert(service=b'# service=git-upload-pack\n'),
    pkt(b'# service=git-receive-pack\n') + pkt(b'wrong') + b'0000',
    pkt(b'# service=git-receive-pack\n') + b'00000000',
    advert(rows=[b'c' * 40 + b' refs/heads/main\0' + CAPS + b'\n']),
    advert(rows=[BASE.encode() + b' refs/heads/main\n'])])
def test_advertisement_rejects_framing_and_base_mismatch(raw):
    with pytest.raises(ValueError):
        receive(raw)


def test_advertisement_ref_and_byte_limits(monkeypatch):
    raw = advert()
    monkeypatch.setattr(framing, 'MAX_ADVERTISEMENT', len(raw))
    assert receive(raw)
    with pytest.raises(ValueError):
        receive(raw + b'x')
    monkeypatch.setattr(framing, 'MAX_ADVERTISEMENT', 2 * 1024 * 1024)
    monkeypatch.setattr(framing, 'MAX_REFS', 1)
    assert receive(raw)
    with pytest.raises(ValueError):
        receive(advert(rows=[BASE.encode() + b' refs/heads/main\0' + CAPS + b'\n',
                            BASE.encode() + b' refs/heads/other\n']))


@pytest.mark.parametrize('caps', [COMMAND_CAPS, COMMAND_CAPS[1:], COMMAND_CAPS + b'\n',
                                b'report-status object-format=sha1'])
def test_exact_command_returns_pack_for_separate_validation(caps):
    assert unpack(command(caps=caps)) == PACK


@pytest.mark.parametrize('change', [dict(old=BASE), dict(new=BASE), dict(target=BASE_REF),
    dict(caps=b'quiet'), dict(caps=b'report-status report-status-v2'),
    dict(caps=b'report-status report-status'), dict(caps=b'report-status delete-refs'),
    dict(caps=b'report-status atomic'), dict(caps=b'report-status push-options'),
    dict(caps=b'report-status object-format=sha256'), dict(caps=b'  report-status'),
    dict(caps=b'report-status\0quiet'), dict(caps=b'report-status\n\n'),
    dict(pack=b'PACK\0\0\0\2\0\0\0\0' + b'x' * 20),
    dict(pack=b'PACK\0\0\0\2\0\0\0\x13' + b'x' * 20),
    dict(pack=b'PACK\0\0\0\3\0\0\0\1' + b'x' * 20), dict(pack=PACK[:31])])
def test_command_rejects_wrong_scope_capabilities_and_pack_shape(change):
    with pytest.raises(ValueError):
        unpack(command(**change))


def test_command_rejects_additional_command_and_missing_separator():
    row = pkt(('0' * 40 + ' ' + NEW + ' ' + TARGET).encode() + b'\0' + COMMAND_CAPS)
    for body in (row + row + b'0000' + PACK, row + PACK, b'0000' + PACK):
        with pytest.raises(ValueError):
            unpack(body)


@pytest.mark.parametrize('field,value', [('base_ref', TARGET), ('base_commit', '0' * 40),
    ('base_commit', 'A' * 40), ('target_ref', 'refs/tags/tag'), ('target_ref', 'refs/heads/a..b'),
    ('target_ref', 'refs/heads/.hidden'), ('target_ref', 'refs/heads/a@{b'),
    ('target_ref', 'refs/heads/a.'), ('target_ref', 'refs/heads/a//b')])
def test_selected_scope_rejected(field, value):
    args = dict(base_ref=BASE_REF, base_commit=BASE, target_ref=TARGET)
    args[field] = value
    with pytest.raises(ValueError):
        framing.receive_advertisement(advert(), **args)


def test_native_body_unwrap_preserves_all_binary_bytes(monkeypatch):
    body = b'\x00\xffabcPACK\n' * 100
    stream = io.BytesIO(pkt(body[:400]) + pkt(body[400:]) + b'0000')
    monkeypatch.setattr(framing, 'MAX_BODY', len(body))
    assert framing.read_body(stream) == body
    with pytest.raises(ValueError):
        framing.read_body(io.BytesIO(pkt(body + b'x') + b'0000'))
    with pytest.raises(ValueError):
        unpack(command()) if len(command()) > framing.MAX_BODY else framing.command_pack(
            b'x' * (framing.MAX_BODY + 1), new_commit=NEW, target_ref=TARGET)


@pytest.mark.parametrize('raw', [b'', b'0000', b'0001', b'0002', b'0003', b'0004',
    b'ffff', b'0008abc', pkt(b'payload'), b'zzzz'])
def test_native_body_rejects_empty_truncated_and_special_packets(raw):
    with pytest.raises(ValueError):
        framing.read_body(io.BytesIO(raw))


def test_actual_pinned_git_receive_advertisement(tmp_path):
    tool = selected_git()
    git = tool.path
    assert hashlib.sha256(git.read_bytes()).hexdigest() == tool.sha256
    env = {'PATH': '', 'LANG': 'C', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}

    def native(*args, data=None):
        tool.verify()
        result = subprocess.run([str(git), '-c', 'core.hooksPath=/dev/null', *args],
                                cwd=tmp_path, env=env, input=data, capture_output=True, timeout=5)
        assert result.returncode == 0
        tool.verify()
        return result.stdout

    native('init', '--bare', '--quiet', '--template=', '--object-format=sha1', '.')
    tree = native('hash-object', '-w', '-t', 'tree', '--stdin', data=b'').strip()
    commit = native('hash-object', '-w', '-t', 'commit', '--stdin', data=b'tree ' + tree +
        b'\nauthor Test <test@example.invalid> 0 +0000\ncommitter Test <test@example.invalid> 0 +0000\n\nbase\n').strip()
    native('update-ref', BASE_REF, commit.decode())
    refs = native('receive-pack', '--stateless-rpc', '--advertise-refs', '.')
    raw = pkt(b'# service=git-receive-pack\n') + b'0000' + refs
    assert framing.receive_advertisement(raw, base_ref=BASE_REF, base_commit=commit.decode(),
                                         target_ref=TARGET) == refs
