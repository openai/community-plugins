"""Resident SSH lifecycle boundaries; modeled host plus a real no-fallback client check."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import copy
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import time

import pytest

from devsecops.factory import owned_fresh_foundation as fresh, tcg
from devsecops.factory.state import StateError
from tests.test_owned_fresh_foundation import component  # noqa: F401
from tests.test_fresh_foundation import lab  # noqa: F401
from tests.test_factory_tcg import transport_inputs, issued_certificate  # noqa: F401

_REAL_FRESH_TUNNEL = fresh._FreshTunnel


@pytest.fixture
def resident(component, monkeypatch):  # noqa: F811 - imported fixture dependency
    component.driver.enable_resident_transport()
    calls, controls, control_paths = [], {}, set()
    with component.invocation() as (context, operation):
        outcome = component.driver.up(context)  # Existing fixture controls all remote effects.
        with tempfile.TemporaryDirectory(prefix="dssh-", dir="/tmp") as temporary:
            private = Path(temporary).resolve()
            old = component.driver._private
            (private / "owner.json").write_bytes((old / "owner.json").read_bytes())
            (private / "owner.json").chmod(0o600)
            (private / "receipts").mkdir(mode=0o700)
            component.driver._private = private
            info = private.stat()
            component.driver._private_identity = (info.st_dev, info.st_ino)

            @contextmanager
            def authenticate(spec, deadline, audit, *, control=None, forwarding=False):
                if controls.get("locked"):
                    raise AssertionError("The protected key must not be consulted again")
                calls.append(("authentication", control))
                assert forwarding and control is not None
                control_paths.add(control)
                yield ["controlled-ssh", "retained"]

            monkeypatch.setattr(tcg, "_ssh_context", authenticate)
            original_lstat = Path.lstat

            def lstat(path):
                value = original_lstat(path)
                if path in control_paths:
                    return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=os.getuid(),
                                           st_dev=value.st_dev, st_ino=value.st_ino)
                return value

            monkeypatch.setattr(Path, "lstat", lstat)

            class Process:
                def __init__(self, argv, **kwargs):
                    self.pid, self.code = 42242, None
                    calls.append(("spawn", list(argv)))
                    for path in control_paths:
                        path.write_bytes(b"controlled-original-socket")
                        path.chmod(0o600)

                def poll(self):
                    return self.code

                def terminate(self):
                    calls.append(("terminate", self.pid))
                    if not controls.get("stop_unknown"):
                        self.code = 0

                def wait(self, timeout):
                    if self.code is None:
                        raise subprocess.TimeoutExpired("controlled-ssh", timeout)
                    return self.code

            monkeypatch.setattr(fresh.subprocess, "Popen", Process)

            def run(argv, deadline, **kwargs):
                if argv[0] == "ps":
                    return SimpleNamespace(returncode=0, stdout=b"changed-pid" if controls.get("replaced") else b"original-pid")
                calls.append(("command", list(argv)))
                if "cancel" in argv and controls.get("cancel_unknown"):
                    raise tcg.TCGError("controlled cancellation outcome unknown")
                if "forward" in argv and controls.get("forward_unknown"):
                    raise tcg.TCGError("controlled forwarding outcome unknown")
                return SimpleNamespace(returncode=0, stdout=b"")

            monkeypatch.setattr(tcg, "_run", run)
            session = fresh._ResidentSSHSession(component.driver)
            component.driver._host_session = session
            yield SimpleNamespace(session=session, component=component, context=context,
                                  operation=operation, calls=calls, controls=controls, outcome=outcome)


def test_one_authentication_survives_key_lock_through_commands_forwards_and_cleanup(resident):
    b, s = resident, resident.session
    deadline = time.monotonic() + 30
    with s.command(deadline) as argv:
        tcg._run([*argv, "first-owned-operation"], deadline)
    b.controls["locked"] = True
    forwards = [s.forward("kubernetes", "172.18.0.2", 6443, deadline),
                s.forward("gitlab", "127.0.0.1", 30001, deadline),
                s.forward("registry", "127.0.0.1", 30002, deadline)]
    for label in ("build", "recovery", "cleanup"):
        with s.command(deadline) as argv:
            assert "IdentityAgent=none" in argv and "PubkeyAuthentication=no" in argv
            assert "ProxyCommand=/usr/bin/false" in argv
            tcg._run([*argv, label], deadline)
    for forward in reversed(forwards):
        forward.audit(deadline)
        forward.close(deadline)
        forward.close(deadline)
    s.close(deadline)
    s.close(deadline)
    assert [row[0] for row in b.calls].count("authentication") == 1
    assert [row[0] for row in b.calls].count("spawn") == 1
    assert [row[0] for row in b.calls].count("terminate") == 1
    assert s.closed and not s.control.exists() and all(f.closed for f in forwards)


@pytest.mark.parametrize("loss", ["exited", "socket", "replaced"])
def test_connection_loss_cannot_authenticate_again(resident, loss):
    b, s = resident, resident.session
    deadline = time.monotonic() + 30
    with s.command(deadline):
        pass
    b.controls["locked"] = True
    if loss == "exited":
        s.process.code = 255
    elif loss == "socket":
        s.control.unlink()
    else:
        b.controls["replaced"] = True
    for _ in range(2):
        with pytest.raises(StateError):
            with s.command(deadline):
                pytest.fail("A lost connection cannot dispatch another operation")
    assert [row[0] for row in b.calls].count("authentication") == 1
    assert [row[0] for row in b.calls].count("spawn") == 1


def test_kubernetes_adapter_cancels_only_its_forward_and_retains_cleanup_connection(resident):
    s, b, deadline = resident.session, resident, time.monotonic() + 30
    with s.command(deadline):
        pass
    tunnel = _REAL_FRESH_TUNNEL(b.component.driver._private, s.transport, "a" * 64, owner=b.component.driver)
    first = tunnel._tunnel("172.18.0.2", b.operation, deadline)
    assert first == tunnel._tunnel("172.18.0.2", b.operation, deadline)
    with pytest.raises(StateError, match="provisioning operation"):
        tunnel._tunnel("172.18.0.2", "different-operation", deadline)
    assert tunnel._close_tunnel(deadline)["owned_tunnel_absent"] is True
    with s.command(deadline) as argv:
        tcg._run([*argv, "remove-owned-vm"], deadline)
    assert s.process.poll() is None and s.forwards["kubernetes"].closed
    s.close(deadline)
    assert [row[0] for row in b.calls].count("authentication") == 1


def test_service_adapter_retains_original_shared_connection_until_finalization(resident):
    from devsecops.factory.owned_service_links import OwnedServiceLinks
    from devsecops.factory.service_link_local import OwnedLinkTunnel

    b, s, deadline = resident, resident.session, time.monotonic() + 30
    with s.command(deadline):
        pass
    # The separate service-link tests cover action admission. This test controls
    # only that admission and exercises the actual shared-forward adapter.
    owner = object.__new__(OwnedServiceLinks)
    owner.foundation, owner.journal = b.component.driver, b.component.journal
    owner._private, owner._binding = s.directory / "links", "b" * 64
    owner._private.mkdir(mode=0o700)
    owner._admit = lambda context, action: None
    tunnel = OwnedLinkTunnel(owner, "registry")
    tunnel.start(b.context, 30002)
    tunnel.audit(b.context)
    assert tunnel.observation(b.context)["state"] == "present"
    down = b.component.down_context(b.context, b.operation, b.outcome.artifacts)
    tunnel.close(down)
    assert tunnel.observation(down)["state"] == "absent" and s.process.poll() is None
    assert tunnel.process is None
    s.close(deadline)
    assert [row[0] for row in b.calls].count("authentication") == 1


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf"), -1, 0])
def test_invalid_deadline_stops_before_authentication(resident, deadline):
    with pytest.raises(StateError, match="deadline"):
        with resident.session.command(deadline):
            pytest.fail("Invalid deadline dispatched")
    assert resident.calls == []


@pytest.mark.parametrize("role,host,port", [
    ("other", "127.0.0.1", 30001), ("gitlab", "172.18.0.2", 30001),
    ("registry", "169.254.169.254", 8080), ("kubernetes", "169.254.169.254", 6443),
    ("kubernetes", "8.8.8.8", 6443), ("kubernetes", "0.0.0.0", 6443),
    ("kubernetes", "172.18.0.2", 6444), ("gitlab", "localhost", 30001),
    ("gitlab", "::1", 30001), ("gitlab", "127.0.0.1", True),
    ("gitlab", "127.0.0.1", 22), ("gitlab", "127.0.0.1", 65536),
])
def test_forward_scope_is_closed_before_effect(resident, role, host, port):
    s, deadline = resident.session, time.monotonic() + 30
    with s.command(deadline):
        pass
    with pytest.raises(StateError):
        s.forward(role, host, port, deadline)
    assert not s.forwards
    assert not any(row[0] == "command" and "forward" in row[1] for row in resident.calls)


def test_changed_pid_cannot_be_signaled(resident):
    s, deadline = resident.session, time.monotonic() + 30
    with s.command(deadline):
        pass
    resident.controls["replaced"] = True
    with pytest.raises(StateError, match="replaced"):
        s.close(deadline)
    assert not any(row[0] == "terminate" for row in resident.calls)


def test_changed_control_socket_is_preserved(resident):
    s, deadline = resident.session, time.monotonic() + 30
    with s.command(deadline):
        pass
    s.control.rename(s.directory / "original-control")
    s.control.write_bytes(b"replacement")
    with pytest.raises(StateError, match="socket changed"):
        s.audit(deadline)
    with pytest.raises(StateError, match="residual socket changed"):
        s.close(deadline)
    assert s.control.read_bytes() == b"replacement"


def test_uncertain_forward_cannot_be_reissued_or_hide_its_pending_origin(resident):
    s, deadline = resident.session, time.monotonic() + 30
    with s.command(deadline):
        pass
    resident.controls["forward_unknown"] = True
    with pytest.raises(tcg.TCGError, match="unknown"):
        s.forward("registry", "127.0.0.1", 30002, deadline)
    assert s.forwards["registry"].unknown
    with pytest.raises(StateError, match="already attempted"):
        s.forward("registry", "127.0.0.1", 30002, deadline)
    with pytest.raises(StateError, match="forwards must be closed"):
        s.close(deadline)
    assert len([row for row in resident.calls if row[0] == "command" and "forward" in row[1]]) == 1


def test_uncertain_forward_cancellation_is_not_repeated(resident):
    s, deadline = resident.session, time.monotonic() + 30
    with s.command(deadline):
        pass
    forward = s.forward("registry", "127.0.0.1", 30002, deadline)
    resident.controls["cancel_unknown"] = True
    with pytest.raises(tcg.TCGError):
        forward.close(deadline)
    with pytest.raises(StateError, match="not repeated"):
        forward.close(deadline)
    assert len([row for row in resident.calls if row[0] == "command" and "cancel" in row[1]]) == 1


def test_original_session_evidence_and_capability_cannot_be_replaced(resident):
    s, deadline = resident.session, time.monotonic() + 30
    with s.command(deadline):
        pass
    with pytest.raises(TypeError):
        copy.copy(s)
    path = next(iter(s.pins))
    path.write_bytes(b"{}")
    before = len(resident.calls)
    with pytest.raises(StateError, match="evidence changed"):
        s.audit(deadline)
    assert len(resident.calls) == before


def test_real_openssh_missing_control_has_no_agent_or_network_fallback():
    executable = shutil.which("ssh")
    assert executable is not None
    with tempfile.TemporaryDirectory(prefix="mux-proof-", dir="/tmp") as temporary:
        root = Path(temporary).resolve()
        agent = root / "agent"
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(str(agent))
            listener.listen(1)
            listener.settimeout(0.1)
            # Only the fixed argv builder is exercised here, not an issued
            # session capability or a protected key. No master is started.
            selected = SimpleNamespace(executable=Path(executable).resolve(), control=root / "missing-control")
            argv = fresh._ResidentSSHSession.argv(selected)
            result = subprocess.run([*argv, "true"], env={"PATH": "/usr/bin:/bin", "HOME": str(root),
                                    "SSH_AUTH_SOCK": str(agent)}, capture_output=True, timeout=5)
            assert result.returncode == 255
            assert b"Operation not permitted" not in result.stderr
            with pytest.raises(TimeoutError):
                listener.accept()
            assert not selected.control.exists()
