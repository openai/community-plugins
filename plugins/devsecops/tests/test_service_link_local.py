"""Original subprocess lifecycle modeled without sockets, SSH, or signals."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import os
import stat
import subprocess
import tempfile
import pytest
from devsecops.factory import service_link_local as local, tcg
from devsecops.factory.state import StateError
from tests.test_owned_service_links import linked, start  # noqa: F401
from tests.test_owned_fresh_foundation import component  # noqa: F401
from tests.test_fresh_foundation import lab  # noqa: F401
from tests.test_factory_tcg import transport_inputs, issued_certificate  # noqa: F401

_REAL_TUNNEL = local.OwnedLinkTunnel


@pytest.fixture
def local_boundary(linked, monkeypatch):  # noqa: F811
    controls = {}
    calls = []
    active = {}
    control_paths = set()

    class Reservation:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def bind(self, value):
            assert value == ("127.0.0.1", 0)

        def getsockname(self):
            return ("127.0.0.1", 43001)

    monkeypatch.setattr(local.socket, "socket", Reservation)

    @contextmanager
    def ssh(spec, deadline, audit, *, control=None, forwarding=False, control_only=False):
        if control is not None:
            control_paths.add(control)
        yield ["controlled-ssh", "retained"]

    monkeypatch.setattr(tcg, "_ssh_context", ssh)
    original_lstat = Path.lstat

    def lstat(path):
        item = original_lstat(path)
        if path in control_paths:
            return SimpleNamespace(
                st_mode=stat.S_IFSOCK | 0o600, st_uid=os.getuid(), st_dev=item.st_dev, st_ino=item.st_ino
            )
        return item

    monkeypatch.setattr(Path, "lstat", lstat)

    class Process:
        def __init__(self, argv, **kw):
            self.pid = 4242
            self.code = None
            active[self.pid] = self
            calls.append(("spawn", argv))
            for path in control_paths:
                path.write_bytes(b"controlled-socket-identity")

        def poll(self):
            return self.code

        def terminate(self):
            calls.append(("terminate", self.pid))
            if not controls.get("stop_unknown"):
                self.code = 0

        def wait(self, timeout):
            if self.code is None:
                raise subprocess.TimeoutExpired("controlled", timeout)
            return self.code

    monkeypatch.setattr(local.subprocess, "Popen", Process)

    def run(argv, deadline, **kw):
        if argv[0] == "ps":
            return SimpleNamespace(
                returncode=0,
                stdout=b"original-process" if not controls.get("replaced") else b"replaced-process",
            )
        calls.append(("check", argv))
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(tcg, "_run", run)
    with tempfile.TemporaryDirectory(prefix="dlink-", dir="/tmp") as temporary:
        yield SimpleNamespace(
            linked=linked, calls=calls, controls=controls, private=Path(temporary).resolve()
        )


def prepare(boundary, context):
    selected = boundary.linked
    foundation, outcome = start(selected, context)
    selected.owner._private = boundary.private / "links"
    selected.owner._private.mkdir(mode=0o700)
    (selected.owner._private / "receipts").mkdir(mode=0o700)
    selected.owner.foundation._private = boundary.private
    # Original frozen transport continuity is covered by foundation tests. Here
    # only local process transitions are controlled, with real original journal.
    selected.owner.foundation.current_transport_for = lambda selected, journal: selected
    tunnel = _REAL_TUNNEL(selected.owner, "registry")
    tunnel.start(context, 30001)
    return tunnel, foundation, outcome


def test_only_original_process_can_be_closed(local_boundary):
    b = local_boundary
    with b.linked.base.invocation() as (context, op):
        tunnel, foundation, outcome = prepare(b, context)
        argv = b.calls[0][1]
        assert "127.0.0.1:43001:127.0.0.1:30001" in argv and "ExitOnForwardFailure=yes" in argv
        b.controls["replaced"] = True
        with pytest.raises(StateError, match="changed"):
            tunnel.audit(context)
        down = b.linked.base.down_context(context, op, [*foundation.artifacts, *outcome.artifacts])
        with pytest.raises(StateError, match="replaced"):
            tunnel.close(down)
        assert not any(row[0] == "terminate" for row in b.calls)


def test_unknown_stop_is_not_repeated_and_readonly_reconcile_observes_eventual_exit(local_boundary):
    b = local_boundary
    with b.linked.base.invocation() as (context, op):
        tunnel, foundation, outcome = prepare(b, context)
        down = b.linked.base.down_context(context, op, [*foundation.artifacts, *outcome.artifacts])
        b.controls["stop_unknown"] = True
        with pytest.raises(StateError, match="unresolved"):
            tunnel.close(down)
        with pytest.raises(StateError, match="no signal retry"):
            tunnel.reconcile(down)
        assert [r[0] for r in b.calls].count("terminate") == 1
        tunnel.process.code = 0
        tunnel.control.unlink()  # model original SSH removing its socket on exit
        tunnel.reconcile(down)
        assert tunnel.observation(down)["state"] == "absent"
        assert [r[0] for r in b.calls].count("terminate") == 1


def test_close_removes_original_residual_socket_and_retains_public_audit(local_boundary):
    b = local_boundary
    with b.linked.base.invocation() as (context, op):
        tunnel, foundation, outcome = prepare(b, context)
        down = b.linked.base.down_context(context, op, [*foundation.artifacts, *outcome.artifacts])
        tunnel.close(down)
        assert tunnel.observation(down)["state"] == "absent" and not tunnel.control.exists()
        assert any(p.name.startswith("closed-") for p in tunnel.artifacts())
        assert all(p.name != "ssh.log" for p in tunnel.artifacts())
        tunnel.close(down)
        assert [r[0] for r in b.calls].count("terminate") == 1
