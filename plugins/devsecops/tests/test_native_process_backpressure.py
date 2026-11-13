"""Real local child processes with controlled transient nonblocking pipe errors."""
import errno
import hashlib
import json
import os
import sys
import threading
import time

import pytest

from devsecops.factory import native_codex as native


def pipe_boundary(monkeypatch, direction, error_type, *, failures):
    original_popen = native.subprocess.Popen
    original_io = getattr(native.os, direction)
    descriptors = set()
    observations = {"raised": 0, "process": None}

    def spawn(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        observations["process"] = process
        streams = (process.stdin,) if direction == "write" else (process.stdout, process.stderr)
        descriptors.update(stream.fileno() for stream in streams)
        return process

    def transient(descriptor, argument):
        if descriptor in descriptors and (failures is None or observations["raised"] < failures):
            observations["raised"] += 1
            code = errno.EAGAIN if error_type is BlockingIOError else errno.EINTR
            raise error_type(code, "controlled transient pipe condition")
        return original_io(descriptor, argument)

    monkeypatch.setattr(native.subprocess, "Popen", spawn)
    monkeypatch.setattr(native.os, direction, transient)
    return observations


@pytest.mark.parametrize("direction", ["write", "read"])
@pytest.mark.parametrize("error_type", [BlockingIOError, InterruptedError])
def test_transient_pipe_errors_preserve_complete_native_input(tmp_path, monkeypatch, direction, error_type):
    observed = pipe_boundary(monkeypatch, direction, error_type, failures=2)
    payload = b"native-input-with-backpressure\x00" * 8192
    script = (
        "import hashlib,json,sys; data=sys.stdin.buffer.read(); "
        "print(json.dumps({'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}),flush=True)"
    )
    result = native._process([sys.executable, "-I", "-B", "-c", script], cwd=tmp_path,
        stdin=payload, deadline=time.monotonic() + 10, limit=4096, environ=dict(os.environ))
    assert result["returncode"] == 0 and result["reason"] is None and result["spawned"]
    assert json.loads(result["stdout"]) == {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    assert observed["raised"] == 2
    assert observed["process"].returncode is not None
    assert all(stream.closed for stream in (observed["process"].stdin, observed["process"].stdout,
                                            observed["process"].stderr))


def test_persistent_backpressure_keeps_original_deadline_and_reaps_child(tmp_path, monkeypatch):
    observed = pipe_boundary(monkeypatch, "write", BlockingIOError, failures=None)
    started = time.monotonic()
    result = native._process([sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.buffer.read()"],
        cwd=tmp_path, stdin=b"bounded request", deadline=started + .2, limit=4096, environ=dict(os.environ))
    assert result["reason"] == "deadline-exceeded" and result["returncode"] is None
    assert time.monotonic() - started < 3
    assert observed["raised"] > 0 and observed["process"].returncode is not None


def test_cancellation_remains_effective_during_persistent_backpressure(tmp_path, monkeypatch):
    observed = pipe_boundary(monkeypatch, "write", BlockingIOError, failures=None)
    cancelled = threading.Event()
    timer = threading.Timer(.1, cancelled.set)
    timer.start()
    try:
        result = native._process([sys.executable, "-I", "-B", "-c", "import sys; sys.stdin.buffer.read()"],
            cwd=tmp_path, stdin=b"cancellable request", deadline=time.monotonic() + 3,
            limit=4096, environ=dict(os.environ), cancel=cancelled)
    finally:
        timer.cancel()
        timer.join(timeout=1)
    assert result["reason"] == "cancelled" and result["returncode"] is None
    assert observed["raised"] > 0 and observed["process"].returncode is not None
