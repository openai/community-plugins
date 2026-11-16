"""Resident localhost-only SSH forwards for the two fixed owned service endpoints."""

from __future__ import annotations

import os
import socket
import stat
import subprocess
import time
from uuid import uuid4

from . import tcg
from .owned_fresh_foundation import FreshTransport
from .state import StateError, _read, _sha


class OwnedLinkTunnel:
    def __init__(self, owner, service):
        from .owned_service_links import OwnedServiceLinks

        if type(owner) is not OwnedServiceLinks or service not in ("gitlab", "registry"):
            raise StateError("Service tunnel needs its exact original fixed owner")
        self.owner, self.service = owner, service
        self.process = self.port = self.control = self.process_hash = None
        self._pins, self._started, self._closed = {}, False, False
        self.unresolved = False
        self._stop_attempted = False
        self._close_operation = None
        self._shared = None
        self._directory = owner._private / service
        self._directory.mkdir(mode=0o700)
        info = self._directory.stat()
        self._identity = (info.st_dev, info.st_ino)

    def _guard(self):
        info = tcg._private(self._directory).stat()
        if (info.st_dev, info.st_ino) != self._identity:
            raise StateError("Service tunnel private directory changed")
        for path, digest in self._pins.items():
            if _sha(_read(path, 128000)) != digest:
                raise StateError("Service tunnel original evidence changed")

    def _write(self, name, value):
        self._guard()
        path = self._directory / name
        tcg._write(path, tcg._canonical(value))
        self._pins[path] = _sha(_read(path, 128000))

    def _identity_now(self, context):
        if self.process is None or self.process.poll() is not None:
            return None
        result = tcg._run(
            ["ps", "-p", str(self.process.pid), "-o", "lstart=", "-o", "command="],
            context.deadline,
            check=False,
        )
        if result.returncode or not result.stdout.strip():
            raise StateError("Original service tunnel process identity is unobserved")
        return _sha(result.stdout)

    def start(self, context, remote_port):
        self.owner._admit(context, action="up")
        if self._started or type(remote_port) is not int or not 1024 < remote_port < 65536:
            raise StateError("Service tunnel has already been attempted or endpoint is invalid")
        self._started = True
        self.unresolved = True
        if self.owner.foundation.resident_transport_enabled:
            session = self.owner.foundation._host_session
            if session is None:
                raise StateError("Service forwarding requires the original authenticated foundation connection")
            previous = session.forwards.get(self.service)
            try:
                self._shared = session.forward(self.service, "127.0.0.1", remote_port, context.deadline)
            finally:
                if self._shared is None and previous is None:
                    self._shared = session.forwards.get(self.service)
            self.port = self._shared.port
            self._write("shared-forward.json", {
                "service": self.service, "port": self.port, "remote_port": remote_port,
                "connection_pid": session.process.pid, "process_sha256": session.process_hash,
                "shared_authenticated_connection": True,
            })
            self.unresolved = False
            self.audit(context)
            return
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        self.control = self.owner.foundation._private / ("sl-" + self.service[0] + "-" + uuid4().hex[:8])
        if len(str(self.control).encode()) > 100:
            raise StateError("Service tunnel control path exceeds Unix socket bounds")
        selected = self.owner._selected["foundation"]["recipe"]["transport"]
        transport = self.owner.foundation.current_transport_for(selected, self.owner.journal)
        with tcg._ssh_context(
            FreshTransport(transport),
            context.deadline,
            self.owner._private / "receipts",
            control=self.control,
            forwarding=True,
        ) as argv:
            argv[-1:-1] = [
                "-N",
                "-o",
                "ControlPersist=no",
                "-o",
                "ExitOnForwardFailure=yes",
                "-L",
                f"127.0.0.1:{self.port}:127.0.0.1:{remote_port}",
            ]
            self._write(
                "intent.json",
                {
                    "service": self.service,
                    "run_id": context.run_id,
                    "binding": self.owner._binding,
                    "argv": argv,
                    "port": self.port,
                    "remote_port": remote_port,
                },
            )
            log = os.open(
                self._directory / "ssh.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            try:
                self.process = subprocess.Popen(
                    argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True
                )
            finally:
                os.close(log)
            self.process_hash = self._identity_now(context)
            if self.process_hash is None:
                self.unresolved = False
                raise StateError("Original service SSH tunnel exited before establishment")
            self._write("process.json", {"pid": self.process.pid, "process_sha256": self.process_hash})
            while time.monotonic() < context.deadline:
                if self.process.poll() is not None:
                    self.unresolved = False
                    raise StateError("Owned service SSH tunnel failed")
                if self.control.exists():
                    info = self.control.lstat()
                    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                        raise StateError("Service tunnel control socket identity differs")
                    self._control_identity = (info.st_dev, info.st_ino)
                    self.unresolved = False
                    self.audit(context)
                    return
                time.sleep(0.05)
        raise StateError("Service tunnel establishment exceeded its bounded phase")

    def audit(self, context):
        self._guard()
        if self._shared is not None:
            if self._closed or self.unresolved:
                raise StateError("Original shared service forward is closed or unresolved")
            self._shared.audit(context.deadline)
            return
        if (
            self._closed
            or self.unresolved
            or self.process_hash is None
            or self._identity_now(context) != self.process_hash
        ):
            raise StateError("Original service tunnel is absent, changed, or unresolved")
        info = self.control.lstat()
        if not stat.S_ISSOCK(info.st_mode) or (info.st_dev, info.st_ino) != self._control_identity:
            raise StateError("Original service tunnel listener control changed")
        # Local control check cannot initiate new authentication or reach a new host.
        transport = self.owner.foundation.current_transport_for(
            self.owner._selected["foundation"]["recipe"]["transport"], self.owner.journal
        )
        with tcg._ssh_context(
            FreshTransport(transport),
            context.deadline,
            self.owner._private / "receipts",
            control=self.control,
            control_only=True,
        ) as argv:
            argv[-1:-1] = ["-O", "check"]
            tcg._run(argv, context.deadline)

    def reconcile(self, context):
        self._guard()
        if self._shared is not None:
            if self._shared.closed:
                self.unresolved, self._closed = False, True
                return
            self._shared.audit(context.deadline)
            self.unresolved = False
            return
        if not self.unresolved:
            return
        if self.process is None or self.process_hash is None:
            raise StateError("Service tunnel launch has no original process identity")
        current = self._identity_now(context)
        if current is None:
            self.unresolved = False
            self._closed = not self.control.exists() and not self.control.is_symlink()
            return
        if self._stop_attempted:
            raise StateError("Original service tunnel stop remains unresolved; no signal retry")
        if current != self.process_hash or not self.control.exists():
            raise StateError("Original service tunnel establishment remains unobserved")
        info = self.control.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise StateError("Original service tunnel control identity differs")
        self._control_identity = (info.st_dev, info.st_ino)
        self.unresolved = False
        try:
            self.audit(context)
        except BaseException:
            self.unresolved = True
            raise

    def close(self, context):
        self._guard()
        if self._shared is not None:
            if self._closed:
                return
            self.owner.journal.current_mutation(context.phase["id"], "down")
            self.unresolved = True
            self._shared.close(context.deadline)
            self.unresolved, self._closed = False, True
            self._write("shared-forward-closed.json", {"service": self.service, "port": self.port,
                                                       "cancel_acknowledged": True, "shared_connection_retained": True})
            return
        if self._closed:
            if self.process is not None and self.process.poll() is None:
                raise StateError("Closed service tunnel unexpectedly still exists")
            return
        if self.process is None:
            if self._started:
                raise StateError("Service tunnel launch effect remains unobserved")
            self._closed = True
            return
        current = self._identity_now(context)
        if current is not None and current != self.process_hash:
            raise StateError("Refusing to stop a replaced service tunnel process")
        operation = self.owner.journal.current_mutation(context.phase["id"], "down")
        if self._close_operation == operation:
            raise StateError("Tunnel close operation already attempted; reconcile")
        self._close_operation = operation
        self._write(
            "close-intent-" + operation + ".json",
            {
                "pid": self.process.pid,
                "process_sha256": self.process_hash,
                "binding": self.owner._binding,
                "run_id": context.run_id,
            },
        )
        if current is not None:
            # The original live Popen child and independently audited process
            # identity are both required; no reconstructed PID is accepted.
            if self._stop_attempted:
                raise StateError("Original tunnel stop remains unresolved; no duplicate signal")
            self._stop_attempted = True
            self.process.terminate()
            try:
                self.process.wait(timeout=max(0.001, min(15, context.deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                self.unresolved = True
                raise StateError("Service tunnel stop is unresolved") from None
        if self.process.poll() is None:
            raise StateError("Service tunnel final absence was not observed")
        if self.control.exists() or self.control.is_symlink():
            info = self.control.lstat()
            if not stat.S_ISSOCK(info.st_mode) or (info.st_dev, info.st_ino) != self._control_identity:
                raise StateError("Service tunnel residual socket was replaced")
            self.control.unlink()
        self._write(
            "closed-" + operation + ".json",
            {"pid": self.process.pid, "absent": True, "binding": self.owner._binding},
        )
        self.unresolved, self._closed = False, True

    def observation(self, context):
        self._guard()
        if self._shared is not None:
            return {
                "kind": "local-service-tunnel",
                "identity": {"service": self.service, "port": self.port,
                             "pid": self._shared.session.process.pid,
                             "process_sha256": self._shared.session.process_hash,
                             "shared_authenticated_connection": True},
                "disposition": "remove",
                "state": "absent" if self._closed and self._shared.closed else "unresolved" if self.unresolved else "present",
            }
        absent = self._closed and (self.process is None or self.process.poll() is not None)
        return {
            "kind": "local-service-tunnel",
            "identity": {
                "service": self.service,
                "pid": self.process.pid if self.process else None,
                "process_sha256": self.process_hash,
            },
            "disposition": "remove",
            "state": "absent" if absent else "unresolved" if self.unresolved else "present",
        }

    def artifacts(self):
        self._guard()
        return tuple(self._pins)
