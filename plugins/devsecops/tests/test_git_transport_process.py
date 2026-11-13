"""Actual pinned local processes under held-directory and resource boundaries."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import select
import struct
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from devsecops.factory import git_transport_process as process
from devsecops.process import ToolError


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def invocation(tmp_path):
    root = tmp_path/'output'
    root.mkdir(mode=0o700)
    cwd, scratch = root/'repository', root/'scratch'
    cwd.mkdir(mode=0o700)
    scratch.mkdir(mode=0o700)
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
            'scratch_fd': opened(scratch, os.O_RDONLY | os.O_DIRECTORY),
            'executable_fd': opened(binary, os.O_RDONLY), 'executable_sha256': sha(binary),
            'timeout': 5, 'limit': 64000, 'env': {}}
        yield {'args': args, 'root': root, 'cwd': cwd, 'scratch': scratch, 'open': opened}


def program(invocation, source):
    return {**invocation['args'], 'argv': [*invocation['args']['argv'][:-1], source]}


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


def test_actual_git_uses_held_cwd_and_scratch_after_ancestor_symlink_swap(invocation, tmp_path):
    # Preserve argv[0] dispatch for the approved host Git wrapper, just as
    # production preparation freezes that executable under the name "git".
    (tmp_path/'bin').mkdir(mode=0o700)
    binary = tmp_path/'bin/git'
    shutil.copyfile(Path(shutil.which('git')).resolve(), binary)
    binary.chmod(0o500)
    subprocess.run([str(binary), '-c', 'core.hooksPath=/dev/null', 'init', '--quiet', '--template='],
        cwd=invocation['cwd'], env={'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull},
        check=True, capture_output=True, timeout=10)
    moved, external = tmp_path/'moved-owned-output', tmp_path/'external'
    external.mkdir()
    (external/'repository').mkdir()
    (external/'scratch').mkdir()
    (external/'canary').write_bytes(b'UNTOUCHED')
    invocation['root'].rename(moved)
    invocation['root'].symlink_to(external, target_is_directory=True)
    args = {**invocation['args'], 'argv': [str(binary), 'rev-parse', '--show-toplevel'],
            'executable_fd': invocation['open'](binary, os.O_RDONLY), 'executable_sha256': sha(binary),
            'env': {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                    'GIT_NO_REPLACE_OBJECTS': '1'}}
    code, out, err, receipt = process.run(**args)
    assert code == 0 and not err
    assert Path(out.decode().strip()) == moved/'repository'
    assert list((moved/'scratch').iterdir()) == []
    assert list((external/'repository').iterdir()) == list((external/'scratch').iterdir()) == []
    assert (external/'canary').read_bytes() == b'UNTOUCHED'
    assert receipt['stdout_sha256'] == hashlib.sha256(out).hexdigest()


@pytest.mark.parametrize('name', ['cwd_fd', 'scratch_fd', 'executable_fd'])
def test_closed_descriptor_rejected_before_private_writes(invocation, monkeypatch, name):
    args = invocation['args']
    os.close(args[name])
    monkeypatch.setattr(process, '_temporary', lambda *a: pytest.fail('Closed descriptor created output'))
    with pytest.raises(ToolError):
        process.run(**args)
    assert list(invocation['scratch'].iterdir()) == []


@pytest.mark.parametrize('name', ['cwd_fd', 'scratch_fd', 'executable_fd'])
def test_wrong_descriptor_type_rejected_before_private_writes(invocation, monkeypatch, name):
    args = dict(invocation['args'])
    args[name] = args['cwd_fd'] if name == 'executable_fd' else args['executable_fd']
    monkeypatch.setattr(process, '_temporary', lambda *a: pytest.fail('Invalid descriptor created output'))
    with pytest.raises(ToolError):
        process.run(**args)


def test_unsupported_platform_rejected_before_private_writes(invocation, monkeypatch):
    monkeypatch.setattr(process.sys, 'platform', 'unsupported-posix')
    monkeypatch.setattr(process, '_temporary', lambda *a: pytest.fail('Unsupported platform created output'))
    with pytest.raises(ToolError, match='unsupported'):
        process.run(**invocation['args'])


@pytest.mark.parametrize('updates', [
    {'timeout': 121}, {'timeout': float('nan')}, {'timeout': True}, {'limit': 0},
    {'limit': 32*1024*1024+1}, {'stdin_bytes': b'x'*(8*1024*1024+1)},
    {'executable_sha256': 'INVALID'}, {'argv': ['relative-command']}, {'env': {'BAD': 'a\0b'}},
])
def test_invalid_bounds_rejected_before_private_writes(invocation, monkeypatch, updates):
    monkeypatch.setattr(process, '_temporary', lambda *a: pytest.fail('Invalid invocation created output'))
    with pytest.raises(ToolError):
        process.run(**{**invocation['args'], **updates})


def test_no_ambient_secret_or_unrequested_fd_inheritance(invocation, monkeypatch):
    monkeypatch.setenv('PINNED_NATIVE_SECRET', 'DO-NOT-INHERIT')
    other = invocation['open'](invocation['root']/'outside-descriptor', os.O_RDWR | os.O_CREAT)
    os.set_inheritable(other, True)
    source = f'''import os
assert 'PINNED_NATIVE_SECRET' not in os.environ
assert os.environ['EXPLICIT_TEST_VALUE'] == 'selected'
try:
    os.fstat({other})
except OSError:
    pass
else:
    raise AssertionError('Unrequested descriptor inherited')
print('isolated')
'''
    code, out, err, _ = process.run(**{**program(invocation, source), 'env': {'EXPLICIT_TEST_VALUE': 'selected'}})
    assert code == 0 and out == b'isolated\n' and not err


def test_explicit_stdin_and_private_diagnostics_receipt(invocation):
    args = program(invocation, 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.write("PRIVATE-DIAGNOSTIC")')
    code, out, err, receipt = process.run(**{**args, 'stdin_bytes': b'EXPLICIT-INPUT'})
    assert code == 0 and out == b'EXPLICIT-INPUT' and err == b'PRIVATE-DIAGNOSTIC'
    assert receipt['stdin_sha256'] == hashlib.sha256(out).hexdigest()
    assert 'PRIVATE-DIAGNOSTIC' not in json.dumps(receipt)
    assert list(invocation['scratch'].iterdir()) == []


def test_wrong_tool_pin_never_executes_command(invocation):
    args = program(invocation, 'open("UNEXPECTED-WRITE", "w").write("bad")')
    code, out, err, _ = process.run(**{**args, 'executable_sha256': '0'*64})
    assert code == 126 and not out and err == b'Pinned native invocation rejected\n'
    assert not (invocation['cwd']/'UNEXPECTED-WRITE').exists()


def test_mismatched_tool_path_rejected_before_private_writes(invocation, monkeypatch):
    args = {**invocation['args'], 'argv': [str(Path(shutil.which('git')).resolve()), '--version']}
    monkeypatch.setattr(process, '_temporary', lambda *a: pytest.fail('Wrong executable created output'))
    with pytest.raises(ToolError):
        process.run(**args)


def test_hardlinked_executable_rejected_before_private_writes(invocation, tmp_path, monkeypatch):
    binary = tmp_path/'copied-tool'
    shutil.copyfile(Path(sys.executable).resolve(), binary)
    os.link(binary, tmp_path/'substituted-route')
    args = {**invocation['args'], 'argv': [str(binary)],
            'executable_fd': invocation['open'](binary, os.O_RDONLY), 'executable_sha256': sha(binary)}
    monkeypatch.setattr(process, '_temporary', lambda *a: pytest.fail('Hardlinked executable created output'))
    with pytest.raises(ToolError):
        process.run(**args)


def test_timeout_kills_group_reaps_child_and_stops_descendant_writes(invocation, monkeypatch):
    children, signals = track_processes(monkeypatch)
    source = '''import os, time
if os.fork() == 0:
    with open('heartbeat', 'ab', buffering=0) as stream:
        while True:
            stream.write(b'x')
            time.sleep(.01)
else:
    time.sleep(60)
'''
    with pytest.raises(ToolError, match='time limit'):
        process.run(**{**program(invocation, source), 'timeout': .4})
    assert len(children) == 1 and children[0].poll() is not None
    assert (children[0].pid, signal.SIGKILL) in signals
    heartbeat = invocation['cwd']/'heartbeat'
    assert heartbeat.exists()
    size = heartbeat.stat().st_size
    time.sleep(.1)
    assert heartbeat.stat().st_size == size
    assert list(invocation['scratch'].iterdir()) == []


def test_output_overflow_kills_group_and_reaps_child(invocation, monkeypatch):
    children, signals = track_processes(monkeypatch)
    source = "import os, time\nwhile True:\n os.write(1, b'x'*65536)\n time.sleep(.001)"
    with pytest.raises(ToolError, match='output limit'):
        process.run(**{**program(invocation, source), 'limit': 1024})
    assert len(children) == 1 and children[0].poll() is not None
    assert (children[0].pid, signal.SIGKILL) in signals
    assert list(invocation['scratch'].iterdir()) == []


def test_normal_exit_stops_surviving_owned_descendant(invocation, monkeypatch):
    children, signals = track_processes(monkeypatch)
    source = '''import os, time
if os.fork() == 0:
    with open('heartbeat', 'ab', buffering=0) as stream:
        while True:
            stream.write(b'x')
            time.sleep(.01)
else:
    while not os.path.exists('heartbeat'):
        time.sleep(.01)
    os._exit(0)
'''
    code, out, err, _ = process.run(**program(invocation, source))
    assert code == 0 and not out and not err
    assert len(children) == 1 and children[0].poll() is not None
    assert (children[0].pid, signal.SIGKILL) in signals
    heartbeat = invocation['cwd']/'heartbeat'
    size = heartbeat.stat().st_size
    time.sleep(.1)
    assert heartbeat.stat().st_size == size


def test_interrupt_kills_group_and_reaps_child(invocation, monkeypatch):
    children, signals = track_processes(monkeypatch)
    def interrupted(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(process, 'time', SimpleNamespace(monotonic=time.monotonic, sleep=interrupted))
    with pytest.raises(KeyboardInterrupt):
        process.run(**program(invocation, 'import time; time.sleep(60)'))
    assert len(children) == 1 and children[0].poll() is not None
    assert (children[0].pid, signal.SIGKILL) in signals


@pytest.mark.parametrize('code', [0, 17, 126])
def test_completed_target_keeps_live_leader_until_group_stop(invocation, monkeypatch, code):
    children, signals = track_processes(monkeypatch)
    real_stop = process._OwnedProcess.stop

    def checked_stop(owned):
        if not owned.stopped:
            assert owned.code == code
            assert owned.process.returncode is None
            # The result is ready but the supervisor still exists. Never reap
            # its PID to discover completion, even after an immediate exit.
            os.kill(owned.process.pid, 0)
        return real_stop(owned)

    monkeypatch.setattr(process._OwnedProcess, 'stop', checked_stop)
    result, out, err, receipt = process.run(**program(invocation, f'raise SystemExit({code})'))
    assert result == receipt['exit_code'] == code
    assert not out and not err
    assert children[0].returncode == -signal.SIGKILL
    assert signals == [(children[0].pid, signal.SIGKILL)]


def test_post_cleanup_failure_cannot_signal_a_reused_group(invocation, monkeypatch):
    # Deterministic reproduction of the old two-signal exception path. wait()
    # represents reaping and immediate numeric PGID reuse by an unrelated job.
    state = {'reused': False, 'signals': [], 'waits': 0}
    child = SimpleNamespace(pid=424242, returncode=None)
    now = time.monotonic()

    def wait(timeout):
        assert state['signals'] == [(child.pid, signal.SIGKILL)]
        state['waits'] += 1
        state['reused'] = True
        child.returncode = 0
        return 0

    def killpg(pid, sig):
        assert not state['reused'], 'Unrelated replacement process group was signalled'
        state['signals'].append((pid, sig))

    child.wait = wait
    child.poll = lambda: pytest.fail('Normal exit must not reap before group cleanup')
    monkeypatch.setattr(process.os, 'read', lambda *args: struct.pack('!i', 0))
    monkeypatch.setattr(process.os, 'killpg', killpg)
    monkeypatch.setattr(process.subprocess, 'Popen', lambda *a, **kw: child)
    monkeypatch.setattr(process, 'time', SimpleNamespace(
        monotonic=lambda: now + (10 if state['reused'] else 0), sleep=lambda _: None))
    with pytest.raises(ToolError, match='time limit'):
        process.run(**invocation['args'])
    assert state['waits'] == 1 and len(state['signals']) == 1


def test_lost_child_ownership_never_signals_old_group(monkeypatch):
    child = SimpleNamespace(pid=424242, returncode=0)
    owned = process._OwnedProcess(child, 123)
    monkeypatch.setattr(process.os, 'killpg', lambda *args: pytest.fail('Lost child group signalled'))
    with pytest.raises(ToolError, match='reaped outside'):
        owned.completed()
    with pytest.raises(ToolError, match='ownership was lost'):
        owned.stop()


@pytest.mark.parametrize('record', [b'', b'12345', struct.pack('!i', 256), struct.pack('!i', -128)])
def test_malformed_supervisor_result_refused(monkeypatch, record):
    owned = process._OwnedProcess(SimpleNamespace(pid=424242, returncode=None), 123)
    monkeypatch.setattr(process.os, 'read', lambda *args: record)
    with pytest.raises(ToolError, match='supervisor'):
        owned.completed()



def test_nondefault_sigchld_refuses_before_private_files(invocation, monkeypatch):
    monkeypatch.setattr(process.signal, 'getsignal', lambda _: signal.SIG_IGN)
    monkeypatch.setattr(process, '_temporary', lambda *args: pytest.fail('Unsafe child ownership wrote files'))
    with pytest.raises(ToolError, match='SIGCHLD'):
        process.run(**invocation['args'])


def test_permission_failure_is_not_reported_as_group_cleanup(monkeypatch):
    child = SimpleNamespace(pid=424242, returncode=None)
    calls = []
    def denied(*args):
        calls.append('group')
        raise PermissionError()
    def wait(timeout):
        calls.append('wait')
        child.returncode = -signal.SIGKILL
    child.wait = wait
    monkeypatch.setattr(process.os, 'killpg', denied)
    monkeypatch.setattr(process.os, 'kill', lambda *args: calls.append('child'))
    owned = process._OwnedProcess(child, 123)
    for _ in range(2):
        with pytest.raises(ToolError, match='cleanup could not be confirmed'):
            owned.stop()
    assert calls == ['group', 'child', 'wait']


def test_caller_death_stops_supervisor_target_and_descendant(invocation, tmp_path):
    # A separate actual caller disappears while its target and descendant run.
    # The only cleanup trigger is EOF on the supervisor's private owner pipe.
    runner = tmp_path/'caller.py'
    runner.write_text('''import hashlib, os, pathlib, subprocess, sys
sys.path.insert(0, sys.argv[1])
from devsecops.factory import git_transport_process as process
root = pathlib.Path(sys.argv[2])
binary = pathlib.Path(sys.executable).resolve()
original = subprocess.Popen
def record(*args, **kwargs):
    child = original(*args, **kwargs)
    (root/'supervisor-pid').write_text(str(child.pid))
    return child
process.subprocess.Popen = record
source = """import os, time
if os.fork() == 0:
    with open('heartbeat', 'ab', buffering=0) as stream:
        while True:
            stream.write(b'x')
            time.sleep(.01)
else:
    time.sleep(60)
"""
fds = [os.open(root/'repository', os.O_RDONLY | os.O_DIRECTORY),
       os.open(binary, os.O_RDONLY), os.open(root/'scratch', os.O_RDONLY | os.O_DIRECTORY)]
process.run([str(binary), '-I', '-S', '-c', source], cwd_fd=fds[0], executable_fd=fds[1],
    scratch_fd=fds[2], executable_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
    timeout=10, limit=64000, env={})
''')
    caller = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(runner),
                               str(Path(process.__file__).parents[2]), str(invocation['root'])],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    heartbeat = invocation['cwd']/'heartbeat'
    try:
        end = time.monotonic() + 5
        while not heartbeat.exists():
            assert time.monotonic() < end, 'Actual supervised target did not start'
            assert caller.poll() is None, 'Actual caller exited before its target started'
            time.sleep(.01)
        caller.kill()
        caller.wait(timeout=3)
        time.sleep(.1)
        count = heartbeat.stat().st_size
        time.sleep(.15)
        assert heartbeat.stat().st_size == count
    finally:
        if caller.poll() is None:
            caller.kill()
            caller.wait(timeout=3)
        caller.stdout.close()
        caller.stderr.close()


def test_target_cannot_inherit_private_supervisor_channels(invocation):
    source = '''import os
for descriptor in range(3, 256):
    try:
        os.fstat(descriptor)
    except OSError:
        continue
    raise AssertionError('Unexpected inherited descriptor')
'''
    code, out, err, _ = process.run(**program(invocation, source))
    assert code == 0 and not out and not err


def test_target_signal_status_preserved(invocation):
    code, out, err, receipt = process.run(**program(invocation,
        'import os, signal; os.kill(os.getpid(), signal.SIGTERM)'))
    assert code == receipt['exit_code'] == -signal.SIGTERM
    assert not out and not err


def test_lost_result_reader_cancels_descendants_after_target_completes(invocation):
    # The target finishes with a live descendant after its caller's status
    # reader is already closed. An EPIPE during completion publication must
    # still cancel the group; owner EOF cannot explain cleanup in this test.
    source = '''import os, time
os.close(1)
if os.fork() == 0:
    with open('heartbeat', 'ab', buffering=0) as stream:
        while True:
            stream.write(b'x')
            time.sleep(.01)
else:
    while not os.path.exists('heartbeat'):
        time.sleep(.01)
    os._exit(0)
'''
    args = program(invocation, source)
    with ExitStack() as stack:
        status_read, status_write = process._pipe(stack)
        owner_read, owner_write = process._pipe(stack)
        status_read.close()
        child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', process._SHIM,
            str(args['cwd_fd']), str(args['executable_fd']), args['executable_sha256'],
            str(status_write.fileno()), str(owner_read.fileno()), *args['argv']],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            pass_fds=(args['cwd_fd'], args['executable_fd'], status_write.fileno(), owner_read.fileno()),
            start_new_session=True, env={})
        try:
            # EOF from the leader (the target closed stdout before forking)
            # observes exit without freeing its PID before failure cleanup.
            assert select.select([child.stdout], [], [], 5)[0]
            assert os.read(child.stdout.fileno(), 1) == b''
            assert not owner_write.closed
            heartbeat = invocation['cwd']/'heartbeat'
            assert heartbeat.exists()
            time.sleep(.1)
            count = heartbeat.stat().st_size
            time.sleep(.15)
            assert heartbeat.stat().st_size == count
        finally:
            owner_read.close()
            owner_write.close()
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass  # Darwin's already-dead zombie-only group.
            code = child.wait(timeout=5)
            child.stdout.close()
        assert code == -signal.SIGKILL
