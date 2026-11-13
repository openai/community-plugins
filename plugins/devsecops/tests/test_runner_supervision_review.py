"""Independent controlled supervisor audit; no Linux guest/provisioning action."""

from pathlib import PurePosixPath
import signal
import time

import pytest

from test_runner_service_guest import guest as guest, packet as packet, owner


def path_view(children, contents):
    class View:
        def __init__(self, value):
            self.value = str(PurePosixPath(value))

        def __truediv__(self, value):
            return type(self)(PurePosixPath(self.value) / value)

        def iterdir(self):
            return iter(type(self)(value) for value in children[self.value])

        def read_bytes(self):
            value = contents[self.value]
            return value() if callable(value) else value

    return View


def test_child_edges_include_nonleader_thread_children(guest, packet):  # noqa: F811
    supervisor = guest["Supervisor"](packet, time.monotonic() + 5)
    guest["Path"] = path_view(
        {"/proc/120/task": ["/proc/120/task/120", "/proc/120/task/121"]},
        {"/proc/120/task/120/children": b"200 ", "/proc/120/task/121/children": b"201 202 "},
    )
    assert supervisor.child_edges(120, time.monotonic() + 1) == {200, 201, 202}


def test_freeze_waits_for_every_thread_before_returning(guest, packet, monkeypatch):  # noqa: F811
    supervisor = guest["Supervisor"](packet, time.monotonic() + 5)
    original = {"pid": 120}
    supervisor.handles[120] = (17, original)
    nonleader = iter((b"121 (worker) R 1", b"121 (worker) T 1"))
    guest["Path"] = path_view(
        {"/proc/120/task": ["/proc/120/task/120", "/proc/120/task/121"]},
        {"/proc/120/task/120/stat": b"120 (main) T 1", "/proc/120/task/121/stat": lambda: next(nonleader)},
    )
    guest["proc"] = lambda pid: original
    guest["exited"] = lambda fd: False
    signals, waits = [], []
    guest["send"] = lambda fd, sig: signals.append((fd, sig))
    monkeypatch.setattr(guest["time"], "sleep", waits.append)
    supervisor.freeze(120, time.monotonic() + 1)
    assert signals == [(17, signal.SIGSTOP)] and len(waits) == 1
    with pytest.raises(StopIteration):
        next(nonleader)


def test_collect_closes_reparented_edges_after_freezing_parents(guest, packet, tmp_path, monkeypatch):  # noqa: F811
    supervisor = owner(guest, packet, tmp_path, monkeypatch)
    parent = guest["os"].getpid()
    roots = iter(({200}, {200, 400}, {200, 400}, {200, 400}))
    frozen, enrolled = [], []

    def edges(pid, deadline):
        if pid == parent:
            return next(roots)
        assert pid in frozen
        return {300} if pid == 200 else set()

    def enroll(pid, original_parent):
        enrolled.append((pid, original_parent))
        supervisor.handles[pid] = (pid, {"pid": pid})

    monkeypatch.setattr(supervisor, "child_edges", edges)
    monkeypatch.setattr(supervisor, "enroll", enroll)
    monkeypatch.setattr(supervisor, "freeze", lambda pid, deadline: frozen.append(pid))
    guest["exited"] = lambda fd: False
    supervisor.collect(time.monotonic() + 1)
    assert frozen == [200, 300, 400]
    assert enrolled == [(200, parent), (300, 200), (400, parent)]


def test_identity_read_cannot_send_stop_after_deadline(guest, packet, monkeypatch):  # noqa: F811
    supervisor = guest["Supervisor"](packet, time.monotonic() + 5)
    original = {"pid": 120}
    supervisor.handles[120] = (17, original)
    clock = [10]
    signals = []
    monkeypatch.setattr(guest["time"], "monotonic", lambda: clock[0])

    def delayed_identity(pid):
        clock[0] = 12
        return original

    guest["proc"] = delayed_identity
    guest["exited"] = lambda fd: False
    guest["send"] = lambda fd, sig: signals.append((fd, sig))
    with pytest.raises(ValueError, match="deadline"):
        supervisor.freeze(120, 11)
    assert signals == []
