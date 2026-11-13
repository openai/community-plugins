"""Bounded, shell-free subprocess execution. Raw output stays in private scratch."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


class ToolError(ValueError):
    pass


def run(argv: list[str], *, cwd: Path, timeout: float = 45, limit: int = 8_000_000,
        env: dict[str, str] | None = None, stdin_bytes: bytes | None = None,
        temp_directory: Path | None = None) -> tuple[int, bytes, bytes, dict]:
    """Kill a whole process group on timeout/overflow; never echo tool diagnostics."""
    if stdin_bytes is not None and (type(stdin_bytes) is not bytes or len(stdin_bytes) > 8 * 1024 * 1024):
        raise ToolError('Tool input exceeds the supported byte contract')
    if temp_directory is not None and (not isinstance(temp_directory, Path)
            or not temp_directory.is_absolute() or '..' in temp_directory.parts):
        raise ToolError('Tool temporary directory must be an absolute path')
    safe_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
                "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1"}
    if env:
        safe_env.update(env)
    start = time.monotonic()
    with tempfile.TemporaryFile(dir=temp_directory) as stdout, tempfile.TemporaryFile(dir=temp_directory) as stderr, ExitStack() as inputs:
        stdin = subprocess.DEVNULL
        if stdin_bytes is not None:
            stdin = inputs.enter_context(tempfile.TemporaryFile(dir=temp_directory))
            stdin.write(stdin_bytes)
            stdin.flush()
            stdin.seek(0)
        if time.monotonic() - start >= timeout:
            raise ToolError("Tool time limit exceeded")
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=safe_env, stdin=stdin,
                                    stdout=stdout, stderr=stderr, start_new_session=True)
        except OSError:
            raise ToolError("Tool unavailable or not executable") from None
        def stop():
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # The process may have exited between poll() and killpg().
                if proc.poll() is None:
                    proc.kill()
            proc.wait()
        try:
            reason = None
            while proc.poll() is None:
                if time.monotonic() - start >= timeout:
                    reason = "Tool time limit exceeded"
                if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > limit:
                    reason = "Tool output limit exceeded"
                if reason:
                    raise ToolError(reason)
                time.sleep(.01)
            if time.monotonic() - start >= timeout:
                raise ToolError("Tool time limit exceeded")
        except BaseException:
            stop()
            raise
        if os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size > limit:
            raise ToolError("Tool output limit exceeded")
        stdout.seek(0)
        stderr.seek(0)
        out, err = stdout.read(), stderr.read()
    receipt = {"tool": Path(argv[0]).name, "exit_code": proc.returncode,
               "duration_ms": round((time.monotonic() - start) * 1000),
               "stdout_sha256": hashlib.sha256(out).hexdigest(),
               "stderr_sha256": hashlib.sha256(err).hexdigest(),
               "stdout_bytes": len(out), "stderr_bytes": len(err)}
    if stdin_bytes is not None:
        receipt.update(stdin_sha256=hashlib.sha256(stdin_bytes).hexdigest(), stdin_bytes=len(stdin_bytes))
    if time.monotonic() - start >= timeout:
        raise ToolError("Tool time limit exceeded")
    return proc.returncode, out, err, receipt
