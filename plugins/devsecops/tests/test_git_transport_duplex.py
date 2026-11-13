"""Actual local duplex children; no network, credentials or delivery authority."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import pickle
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from devsecops.factory import git_transport_process as process
from devsecops.process import ToolError


@pytest.fixture
def invocation(tmp_path):
    root = tmp_path/'output'
    root.mkdir(mode=0o700)
    cwd = root/'repository'
    cwd.mkdir(mode=0o700)
    with ExitStack() as stack:
        descriptors = []
        def opened(path, flags):
            descriptor = os.open(path, flags)
            descriptors.append(descriptor)
            return descriptor
        def closed():
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        stack.callback(closed)
        binary = Path(sys.executable).resolve()
        args = {'argv': [str(binary), '-I', '-S', '-B', '-c', 'print("OK")'],
            'cwd_fd': opened(cwd, os.O_RDONLY | os.O_DIRECTORY),
            'executable_fd': opened(binary, os.O_RDONLY),
            'executable_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'timeout': 5, 'stdout_limit': 1_000_000, 'stderr_limit': 1_000_000,
            'input_limit': 1_000_000, 'env': {}}
        yield {'args': args, 'root': root, 'cwd': cwd, 'open': opened}


def program(invocation, source, **updates):
    return {**invocation['args'], 'argv': [*invocation['args']['argv'][:-1], source], **updates}


def track_processes(monkeypatch):
    children, signals = [], []
    actual_popen, actual_killpg = subprocess.Popen, os.killpg
    def popen(*args, **kwargs):
        assert kwargs['close_fds'] and kwargs['start_new_session']
        assert len(kwargs['pass_fds']) == 4
        child = actual_popen(*args, **kwargs)
        children.append(child)
        return child
    def killpg(pid, sig):
        signals.append((pid, sig))
        return actual_killpg(pid, sig)
    monkeypatch.setattr(process.subprocess, 'Popen', popen)
    monkeypatch.setattr(process.os, 'killpg', killpg)
    return children, signals


def test_actual_multiple_rounds_receipt_covers_consumed_stdout(invocation):
    source = '''import sys
for _ in range(2):
    value = sys.stdin.buffer.readline()
    sys.stdout.buffer.write(value.upper()); sys.stdout.buffer.flush()
tail = sys.stdin.buffer.read()
sys.stdout.buffer.write(tail + b'TAIL')
sys.stderr.buffer.write(b'PRIVATE-DIAGNOSTIC')
'''
    with process.open_duplex(**program(invocation, source)) as session:
        session.write(b'first\n')
        assert session.read(6) == b'FIRST\n'
        session.write(b'second\n')
        assert session.read(7) == b'SECOND\n'
        session.write(b'end')
        code, out, err, receipt = session.finish()
        assert session.process.returncode is not None
        assert session.stderr == err
    assert code == 0 and out == b'endTAIL' and err == b'PRIVATE-DIAGNOSTIC'
    assert receipt['stdin_bytes'] == 16
    assert receipt['stdin_sha256'] == hashlib.sha256(b'first\nsecond\nend').hexdigest()
    assert receipt['stdout_bytes'] == 20
    assert receipt['stdout_sha256'] == hashlib.sha256(b'FIRST\nSECOND\nendTAIL').hexdigest()
    assert receipt['stderr_bytes'] == len(err)
    assert receipt['stderr_sha256'] == hashlib.sha256(err).hexdigest()
    assert receipt['owned_group_cleanup'] is True
    assert 'PRIVATE-DIAGNOSTIC' not in json.dumps(receipt)


def test_write_drains_both_output_pipes_before_target_consumes_large_input(invocation):
    # Every stream exceeds a typical OS pipe buffer. A serial write/read deadlocks.
    source = '''import sys
sys.stdout.buffer.write(b'o'*262144); sys.stdout.buffer.flush()
sys.stderr.buffer.write(b'e'*262144); sys.stderr.buffer.flush()
value = sys.stdin.buffer.read()
sys.stdout.buffer.write(value)
'''
    raw = b'i'*262144
    with process.open_duplex(**program(invocation, source)) as session:
        session.write(raw)
        assert session.read(262144) == b'o'*262144
        code, out, err, receipt = session.finish()
    assert code == 0 and out == raw and err == b'e'*262144
    assert receipt['stdout_bytes'] == 524288 and receipt['stdin_bytes'] == len(raw)


@pytest.mark.parametrize('code', [0, 7])
def test_exact_native_exit_and_remaining_stdout(invocation, code):
    source = f'import sys; sys.stdout.write("hello"); sys.exit({code})'
    with process.open_duplex(**program(invocation, source)) as session:
        assert session.read(2) == b'he'
        actual, out, err, receipt = session.finish()
    assert actual == receipt['exit_code'] == code and out == b'llo' and not err
    assert receipt['stdout_bytes'] == 5


def test_cumulative_input_cap_after_multiple_successful_rounds(invocation):
    source = 'import sys\nfor line in sys.stdin.buffer:\n sys.stdout.buffer.write(line); sys.stdout.buffer.flush()'
    with process.open_duplex(**program(invocation, source, input_limit=4)) as session:
        session.write(b'a\n')
        assert session.read(2) == b'a\n'
        session.write(b'b\n')
        assert session.read(2) == b'b\n'
        with pytest.raises(ToolError, match='cumulative input'):
            session.write(b'c')
        assert session.process.returncode is not None
        with pytest.raises(ToolError, match='no longer open'):
            session.finish()


def test_cumulative_stdout_cap_does_not_reset_after_read(invocation):
    source = 'import sys\nfor line in sys.stdin.buffer:\n sys.stdout.buffer.write(line); sys.stdout.buffer.flush()'
    with process.open_duplex(**program(invocation, source, stdout_limit=4)) as session:
        session.write(b'a\n')
        assert session.read(2) == b'a\n'
        session.write(b'b\n')
        assert session.read(2) == b'b\n'
        session.write(b'c\n')
        with pytest.raises(ToolError, match='stdout limit'):
            session.read(2)
        assert session.process.returncode is not None


@pytest.mark.parametrize('updates,expected', [
    ({'stderr_limit': 4}, 'stderr limit'),
    ({'combined_output_limit': 9}, 'combined output limit'),
])
def test_stderr_and_aggregate_output_limits_stop_owned_group(invocation, monkeypatch, updates, expected):
    children, signals = track_processes(monkeypatch)
    source = 'import os,time; os.write(1,b"12345"); os.write(2,b"67890"); time.sleep(60)'
    with pytest.raises(ToolError, match=expected):
        with process.open_duplex(**program(invocation, source, **updates)) as session:
            session.finish()
    assert len(children) == 1 and children[0].returncode is not None
    assert signals.count((children[0].pid, signal.SIGKILL)) == 1


def test_combined_cap_exact_boundary_allows_success(invocation):
    source = 'import os; os.write(1,b"12345"); os.write(2,b"67890")'
    with process.open_duplex(**program(invocation, source, combined_output_limit=10)) as session:
        code, out, err, receipt = session.finish()
    assert code == 0 and out == b'12345' and err == b'67890'
    assert receipt['stdout_bytes']+receipt['stderr_bytes'] == 10


def test_early_eof_is_not_a_short_successful_read(invocation):
    with process.open_duplex(**program(invocation, 'print("x",end="")')) as session:
        with pytest.raises(ToolError, match='ended before'):
            session.read(2)
        assert session.process.returncode is not None


@pytest.mark.parametrize('operation', [lambda s: s.write('text'), lambda s: s.read(-1), lambda s: s.read(True)])
def test_invalid_exchange_operation_closes_before_error(invocation, operation):
    with process.open_duplex(**program(invocation, 'import time; time.sleep(60)')) as session:
        with pytest.raises(ToolError):
            operation(session)
        assert session.process.returncode is not None


def test_finished_exchange_and_explicit_close_are_one_shot(invocation, monkeypatch):
    children, signals = track_processes(monkeypatch)
    with process.open_duplex(**invocation['args']) as session:
        assert session.read(0) == b''
        assert session.finish()[0] == 0
        session.close()
        session.close()
        for operation in (lambda: session.read(0), lambda: session.write(b''), session.finish):
            with pytest.raises(ToolError, match='no longer open'):
                operation()
    assert len(children) == 1 and signals.count((children[0].pid, signal.SIGKILL)) == 1


def test_context_abandonment_and_keyboard_interrupt_stop_without_replacing_error(invocation, monkeypatch):
    children, signals = track_processes(monkeypatch)
    interruption = KeyboardInterrupt('intentional local test')
    with pytest.raises(KeyboardInterrupt) as captured:
        with process.open_duplex(**program(invocation, 'import time; time.sleep(60)')):
            raise interruption
    assert captured.value is interruption
    assert children[0].returncode is not None
    assert signals.count((children[0].pid, signal.SIGKILL)) == 1


@pytest.mark.parametrize('failure_index', [0, 1, 2])
def test_partial_pipe_setup_closes_every_stream_after_actual_spawn(invocation, monkeypatch, failure_index):
    children, signals = track_processes(monkeypatch)
    actual_blocking = os.set_blocking
    original = OSError('intentional local pipe setup failure')
    def set_blocking(descriptor, blocking):
        if children:
            streams = (children[0].stdin, children[0].stdout, children[0].stderr)
            if descriptor == streams[failure_index].fileno():
                raise original
        return actual_blocking(descriptor, blocking)
    monkeypatch.setattr(process.os, 'set_blocking', set_blocking)
    with pytest.raises(OSError) as captured:
        with process.open_duplex(**program(invocation, 'import time; time.sleep(60)')):
            pytest.fail('Partially initialized exchange yielded')
    assert captured.value is original
    assert len(children) == 1 and children[0].returncode is not None
    assert signals.count((children[0].pid, signal.SIGKILL)) == 1
    assert all(stream.closed for stream in (children[0].stdin, children[0].stdout, children[0].stderr))


def test_cleanup_failure_is_not_success_and_preserves_original_exception(invocation, monkeypatch):
    with process.open_duplex(**program(invocation, 'import time; time.sleep(60)')) as session:
        actual_stop = session._owned.stop
        def failed_stop():
            actual_stop()
            raise ToolError('intentional cleanup report failure')
        monkeypatch.setattr(session._owned, 'stop', failed_stop)
        with pytest.raises(ToolError, match='intentional cleanup'):
            session.close()
        # A known cleanup failure must not become a clean later close.
        with pytest.raises(ToolError, match='intentional cleanup'):
            session.close()
        # Let context exit verify propagation too, without leaving a real child.
        monkeypatch.setattr(session, '_close_error', None)
    assert session.process.returncode is not None
    original = RuntimeError('original body failure')
    with pytest.raises(RuntimeError) as captured:
        with process.open_duplex(**program(invocation, 'import time; time.sleep(60)')) as session:
            actual_stop = session._owned.stop
            def failed_stop_again():
                actual_stop()
                raise ToolError('intentional cleanup report failure')
            monkeypatch.setattr(session._owned, 'stop', failed_stop_again)
            raise original
    assert captured.value is original
    assert any('Duplex cleanup failed' in note for note in original.__notes__)
    assert session.process.returncode is not None


def test_native_hash_mismatch_never_executes_target(invocation):
    args = program(invocation, 'open("UNEXPECTED-WRITE", "w").write("bad")', executable_sha256='0'*64)
    with process.open_duplex(**args) as session:
        code, out, err, receipt = session.finish()
    assert code == receipt['exit_code'] == 126 and not out
    assert err == b'Pinned native invocation rejected\n'
    assert not (invocation['cwd']/'UNEXPECTED-WRITE').exists()


@pytest.mark.parametrize('updates', [
    {'timeout': 0}, {'timeout': 121}, {'timeout': float('nan')}, {'timeout': True},
    {'stdout_limit': 0}, {'stdout_limit': 32*1024*1024+1}, {'stderr_limit': True},
    {'input_limit': 8*1024*1024+1}, {'input_limit': -1},
    {'combined_output_limit': 0}, {'combined_output_limit': True},
    {'combined_output_limit': 32*1024*1024+1},
    {'argv': ['relative-command']}, {'executable_sha256': 'bad'},
    {'env': {'INVALID-KEY': 'value'}}, {'env': {'KEY': 'a\0b'}}, {'env': None},
])
def test_invalid_admission_does_not_spawn(invocation, monkeypatch, updates):
    monkeypatch.setattr(process.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Invalid invocation spawned'))
    with pytest.raises(ToolError):
        with process.open_duplex(**{**invocation['args'], **updates}):
            pytest.fail('Invalid invocation yielded')


@pytest.mark.parametrize('name', ['cwd_fd', 'executable_fd'])
def test_closed_descriptor_is_rejected_before_spawn(invocation, monkeypatch, name):
    os.close(invocation['args'][name])
    monkeypatch.setattr(process.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Closed descriptor spawned'))
    with pytest.raises(ToolError):
        with process.open_duplex(**invocation['args']):
            pytest.fail('Closed descriptor yielded')


@pytest.mark.parametrize('name', ['cwd_fd', 'executable_fd'])
def test_wrong_descriptor_type_is_rejected_before_spawn(invocation, monkeypatch, name):
    args = dict(invocation['args'])
    args[name] = args['executable_fd' if name == 'cwd_fd' else 'cwd_fd']
    monkeypatch.setattr(process.subprocess, 'Popen', lambda *a, **kw: pytest.fail('Wrong descriptor spawned'))
    with pytest.raises(ToolError):
        with process.open_duplex(**args):
            pytest.fail('Wrong descriptor yielded')


def test_held_cwd_survives_ancestor_replacement_and_caller_fd_close(invocation, tmp_path):
    (invocation['cwd']/'value').write_bytes(b'HELD-OWNED')
    source = 'import sys; sys.stdin.buffer.read(1); sys.stdout.buffer.write(open("value","rb").read())'
    with process.open_duplex(**program(invocation, source)) as session:
        moved, external = tmp_path/'moved', tmp_path/'external'
        (external/'repository').mkdir(parents=True)
        (external/'repository'/'value').write_bytes(b'EXTERNAL')
        invocation['root'].rename(moved)
        invocation['root'].symlink_to(external, target_is_directory=True)
        os.close(invocation['args']['cwd_fd'])
        os.close(invocation['args']['executable_fd'])
        session.write(b'x')
        code, out, err, _ = session.finish()
    assert code == 0 and out == b'HELD-OWNED' and not err
    assert (external/'repository'/'value').read_bytes() == b'EXTERNAL'


def test_no_ambient_secret_or_unrequested_descriptor_inheritance(invocation, monkeypatch):
    monkeypatch.setenv('DUPLEX_NATIVE_SECRET', 'DO-NOT-INHERIT')
    other = invocation['open'](invocation['root']/'outside-descriptor', os.O_RDWR | os.O_CREAT)
    os.set_inheritable(other, True)
    source = f'''import os
assert 'DUPLEX_NATIVE_SECRET' not in os.environ
assert os.environ['EXPLICIT_TEST_VALUE'] == 'selected'
try:
    os.fstat({other})
except OSError:
    pass
else:
    raise AssertionError('Unrequested descriptor inherited')
print('isolated')
'''
    with process.open_duplex(**program(invocation, source, env={'EXPLICIT_TEST_VALUE': 'selected'})) as session:
        code, out, err, _ = session.finish()
    assert code == 0 and out == b'isolated\n' and not err


def test_timeout_stops_owned_descendant_heartbeat(invocation, monkeypatch):
    children, signals = track_processes(monkeypatch)
    source = '''import os,time
if os.fork() == 0:
    with open('heartbeat', 'ab', buffering=0) as stream:
        while True:
            stream.write(b'x'); time.sleep(.01)
else:
    time.sleep(60)
'''
    with pytest.raises(ToolError, match='time limit'):
        with process.open_duplex(**program(invocation, source, timeout=.5)) as session:
            session.read(1)
    assert children[0].returncode is not None
    assert signals.count((children[0].pid, signal.SIGKILL)) == 1
    heartbeat = invocation['cwd']/'heartbeat'
    assert heartbeat.exists()
    size = heartbeat.stat().st_size
    time.sleep(.1)
    assert heartbeat.stat().st_size == size


def test_normal_target_exit_stops_descendant_holding_output_pipes(invocation, monkeypatch):
    children, signals = track_processes(monkeypatch)
    source = '''import os,time
if os.fork() == 0:
    with open('heartbeat', 'ab', buffering=0) as stream:
        while True:
            stream.write(b'x'); time.sleep(.01)
else:
    while not os.path.exists('heartbeat'):
        time.sleep(.005)
    os.write(1,b'DONE')
'''
    with process.open_duplex(**program(invocation, source)) as session:
        code, out, err, _ = session.finish()
    assert code == 0 and out == b'DONE' and not err
    assert children[0].returncode is not None
    assert signals.count((children[0].pid, signal.SIGKILL)) == 1
    heartbeat = invocation['cwd']/'heartbeat'
    size = heartbeat.stat().st_size
    time.sleep(.1)
    assert heartbeat.stat().st_size == size


@pytest.mark.parametrize('delta', [-10, 10])
def test_wall_clock_discontinuity_fails_closed(invocation, monkeypatch, delta):
    with process.open_duplex(**program(invocation, 'import time; time.sleep(60)', timeout=30)) as session:
        wall = time.time
        monkeypatch.setattr(process, 'time', SimpleNamespace(monotonic=time.monotonic, time=lambda: wall()+delta))
        with pytest.raises(ToolError, match='clock continuity'):
            session.read(1)
        assert session.process.returncode is not None


def test_foreign_thread_and_serialization_cannot_transfer_session(invocation):
    with process.open_duplex(**invocation['args']) as session:
        errors = []
        def foreign():
            try:
                session.read(0)
            except ToolError as error:
                errors.append(str(error))
        worker = threading.Thread(target=foreign)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive() and errors == ['Duplex process belongs to its issuing process and thread']
        with pytest.raises(TypeError, match='cannot be serialized'):
            pickle.dumps(session)
        # A foreign caller may not cancel the issuing thread's owned process.
        assert session.finish()[0] == 0
