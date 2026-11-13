"""Bounded private stdin retains cancellation and the existing receipt contract."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

from devsecops import process


def test_real_child_reads_exact_binary_input_without_receipt_content(tmp_path):
    raw = b'PRIVATE-INPUT-CANARY\0\xff\n' * 100
    script = 'import sys,hashlib; b=sys.stdin.buffer.read(); print(hashlib.sha256(b).hexdigest()); print(len(b))'
    code, out, err, receipt = process.run([sys.executable, '-c', script], cwd=tmp_path, stdin_bytes=raw)
    assert code == 0 and not err
    assert out.decode().splitlines() == [hashlib.sha256(raw).hexdigest(), str(len(raw))]
    assert receipt['stdin_sha256'] == hashlib.sha256(raw).hexdigest() and receipt['stdin_bytes'] == len(raw)
    assert 'PRIVATE-INPUT-CANARY' not in json.dumps(receipt)


def test_default_receipt_and_devnull_behavior_unchanged(tmp_path):
    code, out, err, receipt = process.run([sys.executable, '-c', 'import sys; print(len(sys.stdin.buffer.read()))'], cwd=tmp_path)
    assert (code, out, err) == (0, b'0\n', b'')
    assert set(receipt) == {'tool', 'exit_code', 'duration_ms', 'stdout_sha256', 'stderr_sha256', 'stdout_bytes', 'stderr_bytes'}


@pytest.mark.parametrize('value', ['PRIVATE', bytearray(b'PRIVATE'), memoryview(b'PRIVATE'), b'x'*(8*1024*1024+1)])
def test_input_rejected_before_child_creation(tmp_path, monkeypatch, value):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid input reached process creation')
    monkeypatch.setattr(process.subprocess, 'Popen', forbidden)
    with pytest.raises(process.ToolError, match='input exceeds') as error:
        process.run(['not-called'], cwd=tmp_path, stdin_bytes=value)
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.parametrize('raw', [b'', b'x'*(8*1024*1024)])
def test_exact_input_bounds(tmp_path, raw):
    code, out, _, receipt = process.run([sys.executable, '-c', 'import sys; print(len(sys.stdin.buffer.read()))'],
        cwd=tmp_path, stdin_bytes=raw)
    assert code == 0 and int(out) == len(raw) and receipt['stdin_bytes'] == len(raw)


def test_stdin_is_private_regular_file_not_a_blocking_pipe(tmp_path):
    script = 'import os,stat; s=os.fstat(0); print(stat.S_ISREG(s.st_mode)); print(s.st_mode & 0o077)'
    code, out, _, _ = process.run([sys.executable, '-c', script], cwd=tmp_path, stdin_bytes=b'x')
    assert code == 0 and out == b'True\n0\n'


def test_timeout_still_cancels_child_with_input(tmp_path):
    with pytest.raises(process.ToolError, match='time limit'):
        process.run([sys.executable, '-c', 'import sys,time; sys.stdin.buffer.read(); time.sleep(5)'],
            cwd=tmp_path, stdin_bytes=b'x', timeout=.05)


def test_explicit_temporary_directory_covers_all_streams(tmp_path, monkeypatch):
    selected = tmp_path / 'private'
    selected.mkdir(mode=0o700)
    ambient = tmp_path / 'ambient'
    ambient.mkdir()
    actual = process.tempfile.TemporaryFile
    calls = []
    def recorded(*args, **kwargs):
        assert kwargs['dir'] == selected
        calls.append(kwargs['dir'])
        return actual(*args, **kwargs)
    monkeypatch.setattr(process.tempfile, 'tempdir', str(ambient))
    monkeypatch.setattr(process.tempfile, 'TemporaryFile', recorded)
    code, out, err, receipt = process.run([sys.executable, '-c', 'import sys; print(len(sys.stdin.buffer.read()))'],
        cwd=tmp_path, stdin_bytes=b'PRIVATE', temp_directory=selected)
    assert (code, out, err) == (0, b'7\n', b'') and len(calls) == 3
    assert not list(ambient.iterdir()) and not list(selected.iterdir())
    assert str(selected) not in json.dumps(receipt)


@pytest.mark.parametrize('selected', ['PRIVATE', Path('relative'), Path('/tmp/../PRIVATE')])
def test_invalid_temporary_directory_rejected_before_file_or_child_creation(tmp_path, monkeypatch, selected):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid directory reached file or process creation')
    monkeypatch.setattr(process.tempfile, 'TemporaryFile', forbidden)
    monkeypatch.setattr(process.subprocess, 'Popen', forbidden)
    with pytest.raises(process.ToolError, match='temporary directory must be an absolute path'):
        process.run(['not-called'], cwd=tmp_path, temp_directory=selected)


def test_expired_deadline_after_staging_never_launches_child(tmp_path, monkeypatch):
    actual = process.tempfile.TemporaryFile
    files = []
    def record(*args, **kwargs):
        stream = actual(*args, **kwargs)
        files.append(stream)
        return stream
    def clock():
        if not files:
            return 0
        assert len(files) == 3 and files[-1].read() == b'PRIVATE-STAGED-INPUT'
        return 1
    def forbidden(*args, **kwargs):
        pytest.fail('Expired deadline reached child creation')
    monkeypatch.setattr(process.tempfile, 'TemporaryFile', record)
    monkeypatch.setattr(process.time, 'monotonic', clock)
    monkeypatch.setattr(process.subprocess, 'Popen', forbidden)
    with pytest.raises(process.ToolError, match='time limit'):
        process.run(['not-called'], cwd=tmp_path, stdin_bytes=b'PRIVATE-STAGED-INPUT', timeout=.5)
    assert all(stream.closed for stream in files)


def test_stdin_staging_at_deadline_never_starts_child(tmp_path, monkeypatch):
    clock = [0.0]
    actual = process.tempfile.TemporaryFile
    opened = []
    class SlowInput:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def __getattr__(self, name):
            return getattr(self.stream, name)
        def flush(self):
            self.stream.flush()
            clock[0] = 1.0
    def temporary(*args, **kwargs):
        stream = actual(*args, **kwargs)
        opened.append(stream)
        return SlowInput(stream) if len(opened) == 3 else stream
    monkeypatch.setattr(process.tempfile, 'TemporaryFile', temporary)
    monkeypatch.setattr(process.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(process.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Expired staging started a child'))
    with pytest.raises(process.ToolError, match='time limit'):
        process.run(['never-started'], cwd=tmp_path, stdin_bytes=b'PRIVATE-INPUT-CANARY', timeout=1)
    assert len(opened) == 3 and all(stream.closed for stream in opened)


def test_completed_child_cannot_bypass_timeout_polling(tmp_path, monkeypatch):
    clock, stopped = [0.0], []
    class CompletedChild:
        pid, returncode = 123456, 0
        def poll(self):
            return self.returncode
        def wait(self):
            stopped.append('wait')
            return self.returncode
    def completed(*args, **kwargs):
        clock[0] = 1.0
        return CompletedChild()
    monkeypatch.setattr(process.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(process.subprocess, 'Popen', completed)
    monkeypatch.setattr(process.os, 'killpg', lambda pid, signum: stopped.append((pid, signum)))
    with pytest.raises(process.ToolError, match='time limit'):
        process.run(['completed'], cwd=tmp_path, stdin_bytes=b'x', timeout=1)
    assert stopped == [(123456, process.signal.SIGKILL), 'wait']


def test_receipt_work_cannot_publish_success_after_deadline(tmp_path, monkeypatch):
    clock = [0.0]
    actual = process.hashlib.sha256
    class CompletedChild:
        pid, returncode = 123456, 0
        def poll(self):
            return self.returncode
    def digest(raw):
        if raw == b'PRIVATE-INPUT-CANARY':
            clock[0] = 1.0
        return actual(raw)
    monkeypatch.setattr(process.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(process.subprocess, 'Popen', lambda *args, **kwargs: CompletedChild())
    monkeypatch.setattr(process.hashlib, 'sha256', digest)
    with pytest.raises(process.ToolError, match='time limit') as error:
        process.run(['completed'], cwd=tmp_path, stdin_bytes=b'PRIVATE-INPUT-CANARY', timeout=1)
    assert 'PRIVATE' not in str(error.value)
