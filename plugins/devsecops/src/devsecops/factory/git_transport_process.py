"""Pinned local subprocesses using held directory descriptors, never scratch paths."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import struct
import subprocess
import sys
import time
import threading
from uuid import uuid4

from devsecops.process import ToolError


MAX_EXECUTABLE = 200_000_000
MAX_STDIN = 8 * 1024 * 1024
MAX_OUTPUT = 32 * 1024 * 1024

# A fixed isolated interpreter program, not a caller callback or preexec_fn.
# The verified executable still has to occupy its selected pathname at exec.
# No /dev/fd executable-path support is assumed.
_SHIM = r'''
import hashlib, os, select, signal, stat, struct, sys
cwd, executable = int(sys.argv[1]), int(sys.argv[2])
expected, status, owner, argv = sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6:]
try:
    try:
        child = os.fork()
    except BaseException:
        os.write(2, b'Pinned native invocation rejected\n')
        code = 126
    else:
        if child == 0:
            os.close(status)
            os.close(owner)
            try:
                if not stat.S_ISDIR(os.fstat(cwd).st_mode) or not argv:
                    raise ValueError()
                before = os.fstat(executable)
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or not 0 < before.st_size <= 200_000_000):
                    raise ValueError()
                digest, offset = hashlib.sha256(), 0
                while offset < before.st_size:
                    raw = os.pread(executable, min(65536, before.st_size-offset), offset)
                    if not raw:
                        raise ValueError()
                    digest.update(raw)
                    offset += len(raw)
                fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_nlink')
                identity = lambda info: tuple(getattr(info, name) for name in fields)
                if digest.hexdigest() != expected or identity(before) != identity(os.fstat(executable)):
                    raise ValueError()
                os.fchdir(cwd)
                os.set_inheritable(cwd, False)
                os.set_inheritable(executable, False)
                selected = os.stat(argv[0], follow_symlinks=False)
                if identity(selected) != identity(before):
                    raise ValueError()
                os.execve(argv[0], argv, dict(os.environ))
            except BaseException:
                os.write(2, b'Pinned native invocation rejected\n')
                os._exit(126)
        while True:
            observed, result = os.waitpid(child, os.WNOHANG)
            if observed == child:
                break
            if select.select([owner], [], [], .02)[0]:
                os.read(owner, 1)
                os.killpg(os.getpgrp(), signal.SIGKILL)
        code = os.waitstatus_to_exitcode(result)
    os.close(cwd)
    os.close(executable)
    # Keep this session leader alive after the exact target exits. Its PID cannot
    # be reused before group cancellation, including on Darwin zombie-only groups.
    os.write(status, struct.pack('!i', code))
    # EOF means the caller has gone away. No other process inherits this channel.
    os.read(owner, 1)
finally:
    # Status publication can lose its reader during caller death. No failure
    # after fork may bypass cancellation of our still-owned process group.
    os.killpg(os.getpgrp(), signal.SIGKILL)
'''



def _descriptor(value, kind):
    if type(value) is not int or value < 0:
        raise ToolError('An open local descriptor is required')
    try:
        info = os.fstat(value)
        accepted = stat.S_ISDIR(info.st_mode) if kind == 'directory' else stat.S_ISREG(info.st_mode)
        if not accepted:
            raise ToolError('Unsupported local descriptor type')
        if kind == 'executable':
            if info.st_nlink != 1 or not 0 < info.st_size <= MAX_EXECUTABLE:
                raise ToolError('Pinned executable exceeds its byte bound')
            os.pread(value, 1, 0)  # Reject unreadable/path-only executable descriptors before writes.
        return info
    except OSError:
        raise ToolError('A required local descriptor is unavailable') from None


def _same(left, right):
    return (left.st_dev, left.st_ino, left.st_mode) == (right.st_dev, right.st_ino, right.st_mode)


def _temporary(directory, stack):
    name = 'git-native-' + uuid4().hex
    descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
    try:
        os.fchmod(descriptor, 0o600)
        os.unlink(name, dir_fd=directory)
        stream = os.fdopen(descriptor, 'w+b')
    except BaseException:
        os.close(descriptor)
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass
        raise
    return stack.enter_context(stream)


def _pipe(stack):
    reader, writer = os.pipe()
    return (stack.enter_context(os.fdopen(reader, 'rb', buffering=0)),
            stack.enter_context(os.fdopen(writer, 'wb', buffering=0)))


class _OwnedProcess:
    """Keep the live session leader until its group has been cancelled."""
    def __init__(self, process, status):
        self.process, self.status = process, status
        self.record = b''
        self.code = None
        self.stopped = False
        self.cleanup_failed = False

    def completed(self):
        if self.process.returncode is not None:
            raise ToolError('Native child was reaped outside its owner')
        try:
            chunk = os.read(self.status, 5 - len(self.record))
        except (BlockingIOError, InterruptedError):
            return False
        if not chunk:
            raise ToolError('Native supervisor exited without a complete result')
        self.record += chunk
        if len(self.record) > 4:
            raise ToolError('Native supervisor result is invalid')
        if len(self.record) < 4:
            return False
        self.code = struct.unpack('!i', self.record)[0]
        if not -127 <= self.code <= 255:
            raise ToolError('Native supervisor result is invalid')
        return True

    def stop(self):
        # Neither a second exception path nor a completed wait may signal the
        # old group number again: it can belong to an unrelated new session.
        if self.stopped:
            if self.cleanup_failed:
                raise ToolError('Owned native process cleanup could not be confirmed')
            return
        self.stopped = True
        if self.process.returncode is not None:
            self.cleanup_failed = True
            raise ToolError('Native child ownership was lost before cleanup')
        try:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # The direct child is still unreaped, so its PID remains ours.
                # Reap it, but do not claim the inaccessible group was stopped.
                self.cleanup_failed = True
                os.kill(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.cleanup_failed = True
        if self.cleanup_failed:
            raise ToolError('Owned native process cleanup could not be confirmed') from None


def run(argv, *, cwd_fd, executable_fd, executable_sha256, scratch_fd,
        timeout, limit, env, stdin_bytes=None):
    """Run one pinned argv with no shell, ambient environment or path-based scratch.

    The fixed supervisor receives cwd/executable descriptors and two private
    control channels; only cwd/executable reach its target before exec closes
    them. Scratch remains here; stdio names already unlinked private files.
    A rejected executable never reaches its command.
    """
    if (os.name != 'posix' or sys.platform not in ('darwin', 'linux')
            or not all(hasattr(os, name) for name in ('fchdir', 'pread', 'killpg', 'fork', 'waitstatus_to_exitcode', 'set_blocking', 'O_NOFOLLOW'))
            or os.open not in os.supports_dir_fd or os.unlink not in os.supports_dir_fd):
        raise ToolError('Descriptor-bound local execution is unsupported')
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise ToolError('Native child requires the default SIGCHLD disposition')
    if (not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= 128
            or any(type(value) is not str or '\0' in value or len(value.encode()) > 8192 for value in argv)
            or sum(len(value.encode()) for value in argv) > 65536
            or not Path(argv[0]).is_absolute() or '..' in Path(argv[0]).parts
            or not isinstance(executable_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', executable_sha256)
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120
            or type(limit) is not int or not 0 < limit <= MAX_OUTPUT
            or stdin_bytes is not None and (type(stdin_bytes) is not bytes or len(stdin_bytes) > MAX_STDIN)):
        raise ToolError('Invalid bounded local invocation')
    if (type(env) is not dict or len(env) > 128
            or any(type(key) is not str or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key)
                   or type(value) is not str or '\0' in value for key, value in env.items())
            or sum(len(key.encode()) + len(value.encode()) for key, value in env.items()) > 65536):
        raise ToolError('Invalid explicit local process environment')
    argv, selected_env = list(argv), dict(env)
    started = time.monotonic()
    originals = [(cwd_fd, 'directory'), (executable_fd, 'executable'), (scratch_fd, 'directory')]
    expected = [_descriptor(value, kind) for value, kind in originals]
    try:
        selected = os.stat(argv[0], follow_symlinks=False)
        if not _same(expected[1], selected):
            raise ToolError('Selected executable path differs from its descriptor')
    except OSError:
        raise ToolError('Selected executable path is unavailable') from None
    with ExitStack() as stack:
        held = []
        for (value, kind), identity in zip(originals, expected):
            try:
                descriptor = os.dup(value)
            except OSError:
                raise ToolError('A required local descriptor changed') from None
            stack.callback(os.close, descriptor)
            if not _same(identity, _descriptor(descriptor, kind)):
                raise ToolError('A required local descriptor changed')
            held.append(descriptor)
        cwd, executable, scratch = held
        if time.monotonic() - started >= timeout:
            raise ToolError('Tool time limit exceeded')
        stdout, stderr = _temporary(scratch, stack), _temporary(scratch, stack)
        stdin = subprocess.DEVNULL
        if stdin_bytes is not None:
            stdin = _temporary(scratch, stack)
            stdin.write(stdin_bytes)
            stdin.flush()
            stdin.seek(0)
        if time.monotonic() - started >= timeout:
            raise ToolError('Tool time limit exceeded')
        status_read, status_write = _pipe(stack)
        owner_read, owner_write = _pipe(stack)
        os.set_blocking(status_read.fileno(), False)
        invocation = [sys.executable, '-I', '-S', '-B', '-c', _SHIM,
                      str(cwd), str(executable), executable_sha256,
                      str(status_write.fileno()), str(owner_read.fileno()), *argv]
        try:
            process = subprocess.Popen(invocation, stdin=stdin, stdout=stdout, stderr=stderr,
                env=selected_env, pass_fds=(cwd, executable, status_write.fileno(), owner_read.fileno()), close_fds=True, start_new_session=True)
        except OSError:
            raise ToolError('Pinned native invocation is unavailable') from None
        owned = _OwnedProcess(process, status_read.fileno())
        try:
            status_write.close()
            owner_read.close()
            while True:
                if time.monotonic() - started >= timeout:
                    raise ToolError('Tool time limit exceeded')
                if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > limit:
                    raise ToolError('Tool output limit exceeded')
                if owned.completed():
                    break
                time.sleep(.01)
            # A direct child can exit while its owned process group still has
            # descendants holding stdio. Stop those before trusting final bytes.
            owned.stop()
            stdout.seek(0)
            stderr.seek(0)
            out = stdout.read(limit + 1)
            err = stderr.read(max(1, limit + 1 - len(out)))
            if len(out) + len(err) > limit:
                raise ToolError('Tool output limit exceeded')
            if time.monotonic() - started >= timeout:
                raise ToolError('Tool time limit exceeded')
        except BaseException:
            owned.stop()
            raise
        finally:
            owner_write.close()
    receipt = {'tool': Path(argv[0]).name, 'exit_code': owned.code,
        'duration_ms': round((time.monotonic() - started) * 1000),
        'executable_sha256': executable_sha256,
        'stdout_sha256': hashlib.sha256(out).hexdigest(), 'stdout_bytes': len(out),
        'stderr_sha256': hashlib.sha256(err).hexdigest(), 'stderr_bytes': len(err)}
    if stdin_bytes is not None:
        receipt.update(stdin_sha256=hashlib.sha256(stdin_bytes).hexdigest(), stdin_bytes=len(stdin_bytes))
    return owned.code, out, err, receipt


class _DuplexSession:
    """One issuing-thread/process exchange, with cumulative pipe byte bounds."""
    def __init__(self, *, owned, stack, selector, argv, pin, started, wall_started,
                 timeout, input_limit, stdout_limit, stderr_limit, combined_output_limit):
        self._owned, self._stack, self._selector = owned, stack, selector
        self._argv, self._pin = argv, pin
        self._started, self._wall_started, self._timeout = started, wall_started, timeout
        self._input_limit, self._stdout_limit, self._stderr_limit = input_limit, stdout_limit, stderr_limit
        self._combined_limit = combined_output_limit
        self._pid, self._thread = os.getpid(), threading.get_ident()
        self._state, self._closed, self._close_error = 'open', False, None
        self._out, self._err = bytearray(), bytearray()
        self._input_bytes = self._stdout_bytes = self._stderr_bytes = 0
        self._input_hash, self._stdout_hash, self._stderr_hash = hashlib.sha256(), hashlib.sha256(), hashlib.sha256()
        self._stdout_eof = self._stderr_eof = False

    @property
    def process(self):
        """Supervisor for post-close diagnostics only; callers must never reap it."""
        return self._owned.process

    @property
    def stderr(self):
        return bytes(self._err)

    def __reduce__(self):
        raise TypeError('A live duplex process cannot be serialized')

    def _scope(self):
        if self._pid != os.getpid() or self._thread != threading.get_ident():
            raise ToolError('Duplex process belongs to its issuing process and thread')

    def _check(self):
        mono = time.monotonic() - self._started
        wall = time.time() - self._wall_started
        if min(mono, wall) < 0 or max(mono, wall) >= self._timeout or abs(mono-wall) > 2:
            raise ToolError('Duplex tool time limit or clock continuity exceeded')
        return min(self._timeout-mono, self._timeout-wall)

    def _guard(self, operation):
        self._scope()
        if self._state != 'open' or self._closed:
            raise ToolError('Duplex exchange is no longer open')
        try:
            self._check()
            return operation()
        except BaseException as error:
            self._state = 'failed'
            try:
                self.close()
            except BaseException as cleanup:
                error.add_note('Duplex cleanup failed: '+type(cleanup).__name__)
            if isinstance(error, OSError):
                replacement = ToolError('Duplex pipe operation failed')
                for note in getattr(error, '__notes__', ()):
                    replacement.add_note(note)
                raise replacement from None
            raise

    def _pump(self, pending=None):
        remaining = self._check()
        proc = self.process
        writing = pending is not None
        if writing:
            self._selector.register(proc.stdin, selectors.EVENT_WRITE)
        written = 0
        try:
            for key, _ in self._selector.select(min(.02, remaining)):
                if key.fileobj is proc.stdin:
                    try:
                        written = os.write(proc.stdin.fileno(), pending[:16384])
                    except (BlockingIOError, InterruptedError):
                        continue
                    continue
                try:
                    raw = os.read(key.fileobj.fileno(), 16384)
                except (BlockingIOError, InterruptedError):
                    continue
                is_out = key.fileobj is proc.stdout
                if not raw:
                    self._selector.unregister(key.fileobj)
                    if is_out:
                        self._stdout_eof = True
                    else:
                        self._stderr_eof = True
                    continue
                if is_out:
                    self._stdout_bytes += len(raw)
                    if self._stdout_bytes > self._stdout_limit:
                        raise ToolError('Duplex stdout limit exceeded')
                    self._stdout_hash.update(raw)
                    self._out.extend(raw)
                else:
                    self._stderr_bytes += len(raw)
                    if self._stderr_bytes > self._stderr_limit:
                        raise ToolError('Duplex stderr limit exceeded')
                    self._stderr_hash.update(raw)
                    self._err.extend(raw)
                if self._combined_limit is not None and self._stdout_bytes+self._stderr_bytes > self._combined_limit:
                    raise ToolError('Duplex combined output limit exceeded')
            if self._owned.code is None:
                self._owned.completed()
            # A completed target can leave descendants holding pipes. Cancel
            # before draining, while the unreaped supervisor still owns its PID.
            if self._owned.code is not None and not self._owned.stopped:
                self._owned.stop()
            self._check()
            return written
        finally:
            if writing:
                self._selector.unregister(proc.stdin)

    def write(self, raw):
        """Write one explicit bytes value while draining stdout and stderr."""
        def operation():
            if type(raw) is not bytes or self._input_bytes+len(raw) > self._input_limit:
                raise ToolError('Duplex cumulative input limit exceeded')
            self._input_bytes += len(raw)
            self._input_hash.update(raw)
            offset = 0
            while offset < len(raw):
                if self._owned.code is not None:
                    raise ToolError('Duplex target completed before input was consumed')
                offset += self._pump(memoryview(raw)[offset:])
            self._check()
        return self._guard(operation)

    def read(self, count):
        """Return exactly count stdout bytes, failing closed on early EOF."""
        def operation():
            if type(count) is not int or not 0 <= count <= self._stdout_limit:
                raise ToolError('Invalid bounded duplex read')
            while len(self._out) < count:
                if self._stdout_eof:
                    raise ToolError('Duplex stdout ended before the requested bytes')
                self._pump()
            result = bytes(self._out[:count])
            del self._out[:count]
            self._check()
            return result
        return self._guard(operation)

    def finish(self):
        """Close stdin, stop the owned group, drain and return the remaining stdout.

        Receipt stdout bytes/hash cover the entire stream, including prior reads.
        A successful process result is not an interpretation of remote effects.
        """
        def operation():
            self._state = 'finishing'
            self.process.stdin.close()
            while self._owned.code is None or not (self._stdout_eof and self._stderr_eof):
                self._pump()
            self._owned.stop()
            self._check()
            out, err = bytes(self._out), bytes(self._err)
            receipt = {'tool': Path(self._argv[0]).name, 'exit_code': self._owned.code,
                       'duration_ms': round((time.monotonic()-self._started)*1000),
                       'executable_sha256': self._pin,
                       'stdin_sha256': self._input_hash.hexdigest(), 'stdin_bytes': self._input_bytes,
                       'stdout_sha256': self._stdout_hash.hexdigest(), 'stdout_bytes': self._stdout_bytes,
                       'stderr_sha256': self._stderr_hash.hexdigest(), 'stderr_bytes': self._stderr_bytes,
                       'owned_group_cleanup': True}
            self.close()
            self._check()
            self._state = 'finished'
            return self._owned.code, out, err, receipt
        return self._guard(operation)

    def close(self):
        """Idempotent cancellation; never signal a reaped/reused group number."""
        self._scope()
        if self._closed:
            # Preserve a known failed cleanup on subsequent close calls.
            if self._close_error is not None:
                raise self._close_error
            return
        self._closed = True
        self._state = 'closed' if self._state != 'failed' else 'failed'
        try:
            self._owned.stop()
        except BaseException as error:
            self._close_error = error
        try:
            self._stack.close()
        except BaseException as error:
            if self._close_error is None:
                self._close_error = error
            else:
                self._close_error.add_note('Duplex descriptor close failed: '+type(error).__name__)
        if self._close_error is not None:
            raise self._close_error


@contextmanager
def open_duplex(argv, *, cwd_fd, executable_fd, executable_sha256, timeout,
                stdout_limit, stderr_limit, input_limit, env, combined_output_limit=None):
    """Open a pinned local duplex child with held cwd and executable descriptors.

    The returned session is single-thread/process and one-shot. Only the supplied
    argv/environment are passed. It has no protocol, credential or network policy;
    its caller owns those choices. No callbacks, shell or global signal changes
    are used. Cleanup retains the finite runner's five-second bounded group wait.
    """
    started, wall_started = time.monotonic(), time.time()
    if (os.name != 'posix' or sys.platform not in ('darwin', 'linux')
            or not all(hasattr(os, name) for name in ('fchdir', 'pread', 'killpg', 'fork', 'waitstatus_to_exitcode', 'set_blocking', 'O_NOFOLLOW'))
            or os.open not in os.supports_dir_fd or os.unlink not in os.supports_dir_fd):
        raise ToolError('Descriptor-bound local execution is unsupported')
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise ToolError('Native child requires the default SIGCHLD disposition')
    if (not isinstance(argv, (list, tuple)) or not 1 <= len(argv) <= 128
            or any(type(value) is not str or '\0' in value or len(value.encode()) > 8192 for value in argv)
            or sum(len(value.encode()) for value in argv) > 65536
            or not Path(argv[0]).is_absolute() or '..' in Path(argv[0]).parts
            or not isinstance(executable_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', executable_sha256)
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120
            or any(type(value) is not int or not 0 < value <= maximum for value, maximum in
                   ((stdout_limit, MAX_OUTPUT), (stderr_limit, MAX_OUTPUT), (input_limit, MAX_STDIN)))
            or combined_output_limit is not None and
               (type(combined_output_limit) is not int or not 0 < combined_output_limit <= MAX_OUTPUT)):
        raise ToolError('Invalid bounded duplex invocation')
    if (type(env) is not dict or len(env) > 128
            or any(type(key) is not str or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key)
                   or type(value) is not str or '\0' in value for key, value in env.items())
            or sum(len(key.encode()) + len(value.encode()) for key, value in env.items()) > 65536):
        raise ToolError('Invalid explicit local process environment')
    argv, selected_env = list(argv), dict(env)
    originals = [(cwd_fd, 'directory'), (executable_fd, 'executable')]
    expected = [_descriptor(value, kind) for value, kind in originals]
    try:
        if not _same(expected[1], os.stat(argv[0], follow_symlinks=False)):
            raise ToolError('Selected executable path differs from its descriptor')
    except OSError:
        raise ToolError('Selected executable path is unavailable') from None
    stack, session, owned, failure = ExitStack(), None, None, None
    try:
        held = []
        for (value, kind), identity in zip(originals, expected):
            descriptor = os.dup(value)
            stack.callback(os.close, descriptor)
            if not _same(identity, _descriptor(descriptor, kind)):
                raise ToolError('A required local descriptor changed')
            held.append(descriptor)
        cwd, executable = held
        sr, sw = _pipe(stack)
        owner_read, owner_write = _pipe(stack)
        os.set_blocking(sr.fileno(), False)
        selector = stack.enter_context(selectors.DefaultSelector())
        now, wall_now = time.monotonic(), time.time()
        if min(now-started, wall_now-wall_started) < 0 or max(now-started, wall_now-wall_started) >= timeout or abs((now-started)-(wall_now-wall_started)) > 2:
            raise ToolError('Duplex tool time limit or clock continuity exceeded')
        invocation = [sys.executable, '-I', '-S', '-B', '-c', _SHIM,
                      str(cwd), str(executable), executable_sha256,
                      str(sw.fileno()), str(owner_read.fileno()), *argv]
        try:
            process = subprocess.Popen(invocation, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=selected_env, pass_fds=(cwd, executable, sw.fileno(), owner_read.fileno()),
                close_fds=True, start_new_session=True, bufsize=0)
        except OSError:
            raise ToolError('Pinned native invocation is unavailable') from None
        owned = _OwnedProcess(process, sr.fileno())
        # Register all returned streams before any fallible setup operation so
        # partial initialization cannot leave an untracked pipe descriptor.
        for pipe in (process.stdin, process.stdout, process.stderr):
            stack.callback(pipe.close)
        sw.close()
        owner_read.close()
        for pipe in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(pipe.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        selector.register(process.stderr, selectors.EVENT_READ)
        session = _DuplexSession(owned=owned, stack=stack, selector=selector, argv=argv, pin=executable_sha256,
            started=started, wall_started=wall_started, timeout=timeout, input_limit=input_limit,
            stdout_limit=stdout_limit, stderr_limit=stderr_limit, combined_output_limit=combined_output_limit)
        session._check()
        yield session
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            try:
                if session is not None:
                    session.close()
                elif owned is not None:
                    owned.stop()
            finally:
                stack.close()
        except BaseException as cleanup:
            if failure is None:
                raise
            failure.add_note('Duplex cleanup failed: '+type(cleanup).__name__)
