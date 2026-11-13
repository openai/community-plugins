"""Actual child pipes preserve bytes, deadlines and cleanup across transient readiness."""
import errno
import hashlib
import os
from pathlib import Path
import sys
import time

import pytest

from test_git_transport_duplex import invocation, program
from devsecops.factory import git_transport_process as duplex, tcg
from devsecops.factory import service_link_remote, runner_vm_routes_remote
from devsecops.factory import service_link_guest, runner_vm_guest_routes
from devsecops.process import ToolError

__all__ = ['invocation']
ERRORS = [errno.EAGAIN, errno.EINTR]
PAYLOAD = bytes(range(256)) * 720
CHILD = '''import sys
sys.stdout.buffer.write(b'o'*65536);sys.stdout.buffer.flush()
sys.stderr.buffer.write(b'e'*32768);sys.stderr.buffer.flush()
raw=sys.stdin.buffer.read();sys.stdout.buffer.write(raw)
'''


def inject(monkeypatch, module, stream, code, *, persistent=False):
    children = []
    actual_popen = module.subprocess.Popen
    actual_read, actual_write = os.read, os.write
    hits = []
    def popen(*args, **kwargs):
        child = actual_popen(*args, **kwargs)
        children.append(child)
        return child
    def eligible(fd):
        if not children:
            return False
        pipe = getattr(children[-1], stream)
        return pipe is not None and not pipe.closed and fd == pipe.fileno()
    def failure(fd):
        if eligible(fd) and (persistent or len(hits) < 3):
            hits.append(fd)
            raise OSError(code, 'injected transient readiness')
    def read(fd, size):
        if stream != 'stdin':
            failure(fd)
        return actual_read(fd, size)
    def write(fd, raw):
        if stream == 'stdin':
            failure(fd)
        return actual_write(fd, raw)
    monkeypatch.setattr(module.subprocess, 'Popen', popen)
    monkeypatch.setattr(os, 'read', read)
    monkeypatch.setattr(os, 'write', write)
    return children, hits


@pytest.mark.parametrize('stream', ['stdin', 'stdout', 'stderr'])
@pytest.mark.parametrize('code', ERRORS, ids=['would-block', 'interrupted'])
def test_tcg_transient_readiness_preserves_both_outputs_and_input(monkeypatch, stream, code):
    children, hits = inject(monkeypatch, tcg, stream, code)
    result = tcg._run([str(Path(sys.executable).resolve()), '-I', '-B', '-c', CHILD],
        time.monotonic()+5, data=PAYLOAD)
    assert len(hits) == 3 and result.returncode == 0
    assert result.stdout == b'o'*65536+PAYLOAD and result.stderr == b'e'*32768
    assert children[0].poll() is not None


@pytest.mark.parametrize('stream', ['stdin', 'stdout', 'stderr'])
@pytest.mark.parametrize('code', ERRORS, ids=['would-block', 'interrupted'])
def test_duplex_transient_readiness_preserves_receipt_and_input(invocation, monkeypatch, stream, code):
    children, hits = inject(monkeypatch, duplex, stream, code)
    with duplex.open_duplex(**program(invocation, CHILD)) as session:
        session.write(PAYLOAD)
        rc, out, err, receipt = session.finish()
    assert len(hits) == 3 and rc == 0
    assert out == b'o'*65536+PAYLOAD and err == b'e'*32768
    assert receipt['stdin_sha256'] == hashlib.sha256(PAYLOAD).hexdigest()
    assert receipt['stdout_sha256'] == hashlib.sha256(out).hexdigest()
    assert receipt['stderr_sha256'] == hashlib.sha256(err).hexdigest()
    assert receipt['owned_group_cleanup'] is True and children[0].poll() is not None


@pytest.mark.parametrize('module', [service_link_remote, runner_vm_routes_remote,
                                   service_link_guest, runner_vm_guest_routes])
@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
@pytest.mark.parametrize('code', ERRORS, ids=['would-block', 'interrupted'])
def test_read_only_route_inspection_retains_output_across_transient_readiness(
        monkeypatch, module, stream, code):
    children, hits = inject(monkeypatch, module, stream, code)
    argv = [str(Path(sys.executable).resolve()), '-I', '-B', '-c',
            'import sys;sys.stdout.buffer.write(b"o"*65536);sys.stderr.buffer.write(b"e"*32768)']
    if hasattr(module, '_docker_inspection'):
        result = module._docker_inspection(argv)
    else:
        result = module._run(argv, deadline=time.monotonic()+5)
    assert result == b'o'*65536 and len(hits) == 3
    assert children[0].poll() is not None


def test_tcg_persistent_backpressure_keeps_original_deadline_and_reaps_child(monkeypatch):
    children, hits = inject(monkeypatch, tcg, 'stdin', errno.EAGAIN, persistent=True)
    started = time.monotonic()
    result = tcg._run([str(Path(sys.executable).resolve()), '-I', '-B', '-c',
        'import sys;sys.stdin.buffer.read()'], started+.25, data=PAYLOAD, timeout_ok=True)
    assert result.returncode == 124 and hits
    assert time.monotonic()-started < 2 and children[0].poll() is not None


def test_duplex_persistent_backpressure_keeps_original_deadline_and_reaps_child(invocation, monkeypatch):
    children, hits = inject(monkeypatch, duplex, 'stdin', errno.EAGAIN, persistent=True)
    with pytest.raises(ToolError, match='time limit'):
        with duplex.open_duplex(**program(invocation, 'import sys;sys.stdin.buffer.read()', timeout=.25)) as session:
            session.write(PAYLOAD)
    assert hits and children[0].poll() is not None


def test_duplex_status_interruption_does_not_discard_native_exit(invocation, monkeypatch):
    with duplex.open_duplex(**program(invocation, 'print("complete")')) as session:
        actual = os.read
        hits = []
        def read(fd, size):
            if fd == session._owned.status and not hits:
                hits.append(fd)
                raise InterruptedError(errno.EINTR, 'interrupted status')
            return actual(fd, size)
        monkeypatch.setattr(os, 'read', read)
        code, out, _, receipt = session.finish()
    assert hits and code == 0 and out == b'complete\n' and receipt['owned_group_cleanup'] is True
