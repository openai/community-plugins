"""Controlled protocol and original-process tests: no VM/service/model use."""

import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from devsecops.factory import runner_service_guest as module


@pytest.fixture
def guest():
    result = {"__name__": "controlled_guest"}
    exec(compile(module.GUEST_PROGRAM, "<controlled-guest>", "exec"), result)
    return result


@pytest.fixture
def packet(guest):
    run = "00000000-0000-4000-8000-000000000001"
    root = guest["paths"](run)["inputs"]
    digest = hashlib.sha256(b"{}").hexdigest()
    trust = {
        n: {"path": root + "/service-trust/" + n + "-ca.pem", "sha256": digest}
        for n in ("gitlab", "registry")
    }
    files = {
        n + "-ca.pem": {
            **r,
            "bytes": 2,
            "device": 1,
            "inode": i + 2,
            "uid": 10001,
            "gid": 10001,
            "mode": 0o600,
        }
        for i, (n, r) in enumerate(trust.items())
    }
    files["ca-bundle.pem"] = {
        **files["gitlab-ca.pem"],
        "path": root + "/service-trust/ca-bundle.pem",
        "inode": 7,
    }
    entries = {
        root + "/invocation.json": {
            "content": base64.b64encode(b"{}").decode(),
            "sha256": digest,
            "private": False,
        }
    }
    entries.update({r["path"]: dict(next(iter(entries.values()))) for r in trust.values()})
    return {
        "schema_version": 1,
        "action": "install",
        "run_id": run,
        "operation_id": run,
        "service_nonce": "00000000-0000-4000-8000-000000000002",
        "timeout": 10,
        "runner_vm": {
            "binding": "a" * 64,
            "generation": "runner-vm-" + run,
            "guest_boot_id": "00000000-0000-4000-8000-000000000003",
            "guest_uid": 10001,
            "guest_gid": 10001,
            "root_identity": {"device": 1, "inode": 2},
            "disk_identity": {"device": 1, "inode": 3},
            "runtime_id": "b" * 64,
            "network_id": "c" * 64,
            "ssh_host_key_sha256": "d" * 64,
        },
        "installation": {
            "runner_id": 42,
            "runner_token": "glrt-" + "q" * 24,
            "description": "owned-runner",
            "origin": "https://gitlab.demo.test:8444",
            "entries": entries,
            "environment": {
                "DEVSECOPS_GIT_INVOCATION": root + "/invocation.json",
                "DEVSECOPS_GIT_INVOCATION_SHA256": digest,
                "DEVSECOPS_PYTHON": "/opt/devsecops/python/bin/python3",
                "DEVSECOPS_PLUGIN_ROOT": "/opt/devsecops/plugin",
            },
            "secrets": {"DEVSECOPS_REGISTRY_PASSWORD": "explicit-selected-private-value"},
            "service_trust": trust,
            "route_trust": {
                "files": files,
                "empty_directory": {
                    "path": root + "/service-trust/empty-ca-directory",
                    "device": 1,
                    "inode": 9,
                    "uid": 10001,
                    "gid": 10001,
                    "mode": 0o700,
                },
                "environment": {
                    n: root + "/service-trust/ca-bundle.pem"
                    for n in ("SSL_CERT_FILE", "GIT_SSL_CAINFO", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE")
                }
                | {"SSL_CERT_DIR": root + "/service-trust/empty-ca-directory"}
                | {
                    n: ""
                    for n in (
                        "HTTP_PROXY",
                        "HTTPS_PROXY",
                        "ALL_PROXY",
                        "NO_PROXY",
                        "http_proxy",
                        "https_proxy",
                        "all_proxy",
                        "no_proxy",
                    )
                },
            },
            "guest_package": {
                "root": "/opt/devsecops/plugin",
                "manifest_sha256": "e" * 64,
                "plugin_id": "devsecops@devsecops-local",
                "plugin_version": "0.2.0-alpha.9",
            },
            "guest_paths": guest["paths"](run),
        },
    }


def later(packet, action="observe"):
    value = {k: deepcopy(v) for k, v in packet.items() if k != "installation"}
    value.update(action=action, expected_process=None)
    return value


def test_actual_packaged_validator(guest, packet):
    assert guest["validate"](packet) is packet
    for action in ("observe", "down", "reconcile"):
        guest["validate"](later(packet, action))
    assert len(guest["paths"](packet["run_id"])["runtime"].encode()) == 59


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(command=["sudo", "true"]),
        lambda p: p.update(schema_version=True),
        lambda p: p.update(timeout=float("nan")),
        lambda p: p.update(timeout=float("inf")),
        lambda p: p.update(timeout=0),
        lambda p: p.update(timeout=301),
        lambda p: p.update(service_nonce="saved-nonce"),
        lambda p: p["runner_vm"].update(guest_uid=0),
        lambda p: p["runner_vm"].update(guest_gid=True),
        lambda p: p["runner_vm"].update(runtime_id="unbound"),
        lambda p: p["runner_vm"].update(generation="rke2-same-guest"),
        lambda p: p["runner_vm"]["root_identity"].update(inode=0),
        lambda p: p["installation"].update(origin="http://gitlab.demo.test:8444"),
        lambda p: p["installation"].update(runner_token="secret\nline"),
        lambda p: p["installation"]["guest_paths"].update(runtime="/home/runner/builds/short"),
        lambda p: p["installation"]["secrets"].update(LD_PRELOAD="/tmp/evil"),
        lambda p: p["installation"]["secrets"].update(DEVSECOPS_PYTHON="/tmp/python"),
        lambda p: p["installation"]["environment"].update(PATH="/tmp"),
        lambda p: p["installation"]["guest_package"].update(manifest_sha256="unpinned"),
        lambda p: p["installation"]["route_trust"]["files"]["gitlab-ca.pem"].update(uid=0),
        lambda p: p["installation"]["route_trust"]["environment"].update(SSL_CERT_FILE="/etc/ssl/cert.pem"),
        lambda p: p["installation"]["service_trust"]["gitlab"].update(sha256="a" * 64),
    ],
)
def test_closed_contract_rejects_drift_before_effect(guest, packet, change):
    change(packet)
    with pytest.raises((ValueError, TypeError, KeyError)):
        guest["validate"](packet)


def test_input_escape_rejected(guest, packet):
    entries = packet["installation"]["entries"]
    entries["/etc/profile"] = next(iter(entries.values()))
    with pytest.raises(ValueError, match="escaped"):
        guest["validate"](packet)


def test_changed_input_bytes_rejected(guest, packet):
    next(iter(packet["installation"]["entries"].values()))["content"] = "Y2hhbmdlZA=="
    with pytest.raises(ValueError, match="bytes differ"):
        guest["validate"](packet)


def test_canonical_remote_caller_uses_original_scope_and_stdin(monkeypatch, packet):
    calls = []

    class Scope:
        def __init__(self):
            self.packet = {"run_id": packet["run_id"]}

        def identity(self):
            return packet["runner_vm"]

        def observe_guest(self):
            calls.append("observe")

        def ssh_argv(self):
            return ["ssh", "gitlab-runner@fixed-owned-endpoint"]

        def command(self, argv, seconds, *, input_bytes):
            calls.append((argv, seconds, input_bytes))
            return SimpleNamespace(stdout=json.dumps({"runner_vm": packet["runner_vm"]}).encode())

    monkeypatch.setattr(module, "CONTEXT_TYPE", Scope)
    module.execute(packet, scope=Scope())
    argv, seconds, raw = calls[1]
    assert calls[0] == "observe" and seconds == 10
    assert "python3 -I -B -c" in argv[-1]
    assert packet["service_nonce"] not in argv[-1] and packet["installation"]["runner_token"] not in argv[-1]
    assert json.loads(raw) == packet
    with pytest.raises(ValueError, match="Original RunnerVMRemoteContext"):
        module.execute(packet, scope=SimpleNamespace())


def test_remote_caller_rejects_drift_before_observation(monkeypatch, packet):
    class Scope:
        packet = {"run_id": "different"}

        def identity(self):
            return packet["runner_vm"]

        def observe_guest(self):
            pytest.fail("must not observe mismatched scope")

    monkeypatch.setattr(module, "CONTEXT_TYPE", Scope)
    with pytest.raises(ValueError, match="original VM"):
        module.execute(packet, scope=Scope())


def owner(guest, packet, tmp_path, monkeypatch):
    value = guest["Supervisor"](packet, time.monotonic() + 5)
    value.root = tmp_path
    value.trust_rows = {}
    monkeypatch.setitem(guest, "directory", lambda p: guest["identity"](p))
    monkeypatch.setattr(value, "audit_empty_trust", lambda: None)
    return value


def test_partial_write_retains_original_inode(guest, packet, tmp_path, monkeypatch):
    value = owner(guest, packet, tmp_path, monkeypatch)
    real = os.fdopen

    class Partial:
        def __init__(self, fd):
            self.file = real(fd, "wb")

        def __enter__(self):
            return self

        def write(self, raw):
            self.file.write(raw[:2])
            self.file.flush()
            raise OSError("controlled partial")

        def __exit__(self, *args):
            self.file.close()

    monkeypatch.setattr(guest["os"], "fdopen", lambda fd, mode: Partial(fd))
    target = tmp_path / "selected"
    with pytest.raises(OSError, match="controlled partial"):
        value.write(target, b"secret")
    assert target.read_bytes() == b"se" and value.files[str(target)] == guest["identity"](target)
    assert not value.pending_paths


def test_unacknowledged_creation_blocks_cleanup(guest, packet, tmp_path, monkeypatch):
    value = owner(guest, packet, tmp_path, monkeypatch)
    value.pending_paths.add("uncertain-effect")
    with pytest.raises(ValueError, match="Unacknowledged"):
        value.stop(time.monotonic() + 1)
    assert not value.closed


def test_no_json_process_adoption(guest, packet, tmp_path, monkeypatch):
    value = owner(guest, packet, tmp_path, monkeypatch)
    (tmp_path / "state.json").write_text('{"pid":1,"complete":true}')
    assert value.handles == {} and not value.closed and "state.json" not in module.GUEST_PROGRAM


def test_stop_reaps_actual_owned_local_child(guest, packet, tmp_path, monkeypatch):
    # Actual local subprocess and waitpid; pidfd/ancestry adapters are controlled.
    value = owner(guest, packet, tmp_path, monkeypatch)
    child = subprocess.Popen([sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"])
    reader, writer = os.pipe()
    observed = {"pid": child.pid, "original": "controlled-local-child"}
    value.handles[child.pid] = (reader, observed)
    monkeypatch.setattr(value, "collect", lambda deadline: None)
    monkeypatch.setitem(
        guest, "proc", lambda pid: observed if pid == child.pid else pytest.fail("unrelated process")
    )
    monkeypatch.setitem(guest, "exited", lambda fd: False)
    sent = []

    def send(fd, sig):
        assert fd == reader
        sent.append(sig)
        os.kill(child.pid, sig)

    monkeypatch.setitem(guest, "send", send)
    try:
        value.stop(time.monotonic() + 3)
        assert value.closed and not value.handles and sent == [signal.SIGKILL]
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        os.close(writer)
        if not value.closed:
            child.kill()
            child.wait()
            os.close(reader)
        else:
            child.returncode = -signal.SIGKILL


def test_privileged_descendant_has_no_signal_fallback(guest, packet, tmp_path, monkeypatch):
    value = owner(guest, packet, tmp_path, monkeypatch)
    value.handles[99999] = (123, {"pid": 99999})
    monkeypatch.setattr(value, "collect", lambda deadline: None)
    monkeypatch.setitem(guest, "proc", lambda pid: {"pid": pid})
    monkeypatch.setitem(guest, "exited", lambda fd: False)

    def denied(fd, sig):
        raise PermissionError("controlled mapped uid")

    monkeypatch.setitem(guest, "send", denied)
    with pytest.raises(PermissionError):
        value.stop(time.monotonic() + 1)
    assert not value.closed and 99999 in value.handles


def test_replacement_file_not_deleted(guest, packet, tmp_path, monkeypatch):
    value = owner(guest, packet, tmp_path, monkeypatch)
    path = tmp_path / "private"
    value.write(path, b"original")
    path.rename(tmp_path / "original")
    path.write_bytes(b"replacement")
    path.chmod(0o600)
    monkeypatch.setattr(value, "collect", lambda deadline: None)
    with pytest.raises(ValueError, match="changed identity"):
        value.stop(time.monotonic() + 1)
    assert path.read_bytes() == b"replacement" and not value.closed


def test_reused_pid_outside_original_ancestry_rejected(guest, packet, tmp_path, monkeypatch):
    value = owner(guest, packet, tmp_path, monkeypatch)
    closes = []
    monkeypatch.setattr(guest["os"], "pidfd_open", lambda pid, flags: 77, raising=False)
    monkeypatch.setattr(guest["os"], "close", closes.append)
    monkeypatch.setattr(Path, "read_bytes", lambda p: b"123 (controlled) S 999999 1 1")
    with pytest.raises(ValueError, match="left the original ancestry"):
        value.enroll(123, 456)
    assert closes == [77] and not value.handles


def test_public_observation_excludes_all_private_values(guest, packet, tmp_path, monkeypatch):
    value = owner(guest, packet, tmp_path, monkeypatch)
    value.process_identity = {"pid": 123}
    value.socket_identity = {"inode": 1}
    value.failure = "installation-incomplete"
    raw = json.dumps(value.report("reconcile"))
    for secret in (
        packet["service_nonce"],
        packet["installation"]["runner_token"],
        *packet["installation"]["secrets"].values(),
    ):
        assert secret not in raw
    assert value.report("reconcile")["unresolved_operations"] == [packet["operation_id"]]


def test_cleanup_preserves_route_owner_files(guest, packet, tmp_path, monkeypatch):
    value = owner(guest, packet, tmp_path, monkeypatch)
    trust = tmp_path / "ca.pem"
    trust.write_bytes(b"route-ca")
    value.write(tmp_path / "input", b"private")
    value.process_identity = {"pid": 123}
    value.socket_identity = {"inode": 1}
    monkeypatch.setattr(value, "collect", lambda deadline: None)
    value.stop(time.monotonic() + 1)
    report = value.report("down")
    assert trust.read_bytes() == b"route-ca" and not (tmp_path / "input").exists()
    assert report["complete_cleanup"] and report["descendants_reaped"] and not report["supervisor_terminated"]
    assert report["resources"] and all(row["state"] in ("absent", "preserved") for row in report["resources"])


def test_eof_and_absolute_deadline(guest, monkeypatch):
    class Socket:
        def settimeout(self, value):
            pass

        def recv(self, size):
            return b""

    with pytest.raises(ValueError, match="Incomplete"):
        guest["receive"](Socket(), time.monotonic() + 1)
    ticks = iter((1, 2, 4))
    monkeypatch.setattr(guest["time"], "monotonic", lambda: next(ticks))
    Socket.recv = lambda self, size: b"{"
    with pytest.raises(ValueError, match="deadline"):
        guest["receive"](Socket(), 3)


def test_actual_packaged_guest_process_rejects_before_effect_without_private_output(packet):
    # This is a real local Python invocation, not a Linux VM qualification.
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", module.GUEST_PROGRAM],
        input=json.dumps(packet).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
    )
    assert result.returncode == 73 and result.stdout == b""
    assert (
        result.stderr
        == b"Fixed runner guest operation incomplete; original outcome requires reconciliation.\n"
    )
    assert packet["installation"]["runner_token"].encode() not in result.stderr


@pytest.mark.parametrize("bad_auth", [False, True])
def test_control_exchange_authenticates_without_disclosing_nonce(
    guest, packet, tmp_path, monkeypatch, bad_auth
):
    import hmac

    request = later(packet)
    observed = {"pid": 123, "boot_id": packet["runner_vm"]["guest_boot_id"]}
    inode = {"device": 1, "inode": 2, "mode": 0o600, "uid": 10001, "gid": 10001}
    captured = []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, seconds):
            pass

        def connect(self, path):
            pass

        def close(self):
            pass

        def sendall(self, raw):
            captured.append(raw)

        def recv(self, size):
            wire = json.loads(captured[-1])
            challenge = bytes.fromhex(wire["challenge"])
            public = {
                "runner_vm": packet["runner_vm"],
                "process": {
                    "supervisor": observed,
                    "control_socket": inode,
                    "service_nonce_sha256": guest["sha"](packet["service_nonce"].encode()),
                },
                "complete_cleanup": False,
            }
            tag = hmac.new(
                packet["service_nonce"].encode(), challenge + guest["encoded"](public), hashlib.sha256
            ).hexdigest()
            return (
                guest["encoded"]({"result": public, "authentication": "0" * 64 if bad_auth else tag}) + b"\n"
            )

    monkeypatch.setattr(guest["socket"], "socket", lambda *args: Socket())
    monkeypatch.setitem(guest, "identity", lambda path: inode)
    monkeypatch.setitem(guest, "peer", lambda *args: (77, observed))
    closed = []
    monkeypatch.setattr(guest["os"], "close", closed.append)
    if bad_auth:
        with pytest.raises(ValueError, match="authentication differs"):
            guest["transact"](request, time.monotonic() + 1)
    else:
        assert guest["transact"](request, time.monotonic() + 1)["process"]["supervisor"] == observed
    assert closed == [77]
    assert packet["service_nonce"].encode() not in captured[0]
    assert "service_nonce" not in json.loads(captured[0])["request"]


def test_empty_trust_directory_rejects_added_system_ca(guest, packet, tmp_path, monkeypatch):
    value = guest["Supervisor"](packet, time.monotonic() + 1)
    empty = tmp_path / "empty"
    empty.mkdir(mode=0o700)
    value.empty_trust = {"path": str(empty), **guest["identity"](empty)}
    monkeypatch.setitem(guest, "directory", lambda path: guest["identity"](path))
    value.audit_empty_trust()
    (empty / "unselected.pem").write_bytes(b"not-selected")
    with pytest.raises(ValueError, match="empty private CA"):
        value.audit_empty_trust()


def test_preflight_checks_qualified_plugin_against_manifest_name(guest, packet, tmp_path, monkeypatch):
    value = owner(guest, packet, tmp_path, monkeypatch)
    root = tmp_path / "plugin"
    (root / ".codex-plugin").mkdir(parents=True)
    plugin = root / ".codex-plugin/plugin.json"
    plugin.write_text(json.dumps({"name": "devsecops", "version": "0.2.0-alpha.9"}))
    manifest = root / "SHA256SUMS"
    manifest.write_text(guest["sha"](plugin.read_bytes()) + "  .codex-plugin/plugin.json\n")
    value.installation["guest_package"].update(
        root=str(root), manifest_sha256=guest["sha"](manifest.read_bytes())
    )
    observed = []

    def immutable(path, limit=0):
        observed.append(path)
        return path, guest["sha"](path.read_bytes()) if path.is_file() else "f" * 64

    monkeypatch.setitem(guest, "immutable", immutable)
    monkeypatch.setitem(guest, "immutable_path", lambda path: path)
    monkeypatch.setitem(guest, "read", lambda path, *args, **kwargs: path.read_bytes())
    monkeypatch.setattr(
        guest["subprocess"], "run", lambda *args, **kwargs: SimpleNamespace(stdout=b"Version:      19.5.1\n")
    )
    value.preflight()
    assert value.binary == str(guest["RUNNER"])
    for wrong_version in ("19.5.3", "19.5.10", "19.5.1-extra"):
        with monkeypatch.context() as changed:
            changed.setattr(guest["subprocess"], "run", lambda *args, **kwargs:
                SimpleNamespace(stdout=("Version:      " + wrong_version + "\n").encode()))
            with pytest.raises(ValueError, match="GitLab Runner 19.5.1 required"):
                value.preflight()
    extra = root / "unmanifested.py"
    extra.write_text("old code")
    with pytest.raises(ValueError, match="unmanifested"):
        value.preflight()
    extra.unlink()
    value.installation["guest_package"]["manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="manifest differs"):
        value.preflight()


def test_resident_server_authenticates_before_any_cleanup(guest, packet, tmp_path, monkeypatch):
    import hmac

    value = owner(guest, packet, tmp_path, monkeypatch)
    value.socket_path = tmp_path / "runner.sock"
    identity = {"pid": 123, "uid": [10001] * 4, "gid": [10001] * 4}
    calls = []
    request = later(packet, "down")
    request.pop("service_nonce")
    challenge = b"a" * 32
    tag = hmac.new(
        packet["service_nonce"].encode(), challenge + guest["encoded"](request), hashlib.sha256
    ).hexdigest()
    frames = [
        {"challenge": challenge.hex(), "request": request, "authentication": "0" * 64},
        {"challenge": challenge.hex(), "request": request, "authentication": tag},
    ]
    replies = []

    class Connection:
        def __init__(self, frame):
            self.frame = frame

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def getsockopt(self, *args):
            return guest["struct"].pack("3i", 999, 10001, 10001)

        def settimeout(self, value):
            pass

        def recv(self, size):
            return guest["encoded"](self.frame) + b"\n"

        def sendall(self, raw):
            replies.append(json.loads(raw))

    class Server:
        def bind(self, path):
            Path(path).touch()

        def listen(self, size):
            pass

        def accept(self):
            return Connection(frames.pop(0)), None

        def close(self):
            pass

    monkeypatch.setattr(guest["socket"], "socket", lambda *args: Server())
    monkeypatch.setattr(guest["socket"], "SO_PEERCRED", 17, raising=False)
    monkeypatch.setattr(
        guest["ctypes"], "CDLL", lambda *args, **kwargs: SimpleNamespace(prctl=lambda *args: 0)
    )
    monkeypatch.setitem(guest, "immutable", lambda path: (path, "f" * 64))
    monkeypatch.setitem(guest, "proc", lambda pid: identity)
    monkeypatch.setitem(guest, "guard", lambda *args: None)
    monkeypatch.setattr(value, "install", lambda: None)

    def stop(deadline):
        calls.append("stop")
        value.closed = True

    monkeypatch.setattr(value, "stop", stop)
    value.serve()
    assert calls == ["stop"] and replies[0] == {"error": "runner-control-rejected"}
    assert replies[1]["result"]["complete_cleanup"] and not value.socket_path.exists()
    assert packet["service_nonce"] not in json.dumps(replies)


def test_unknown_resident_supervisor_cannot_be_recreated_from_state(guest, packet, tmp_path, monkeypatch):
    class Socket:
        def settimeout(self, value):
            pass

        def connect(self, path):
            raise ConnectionRefusedError()

        def close(self):
            pass

    monkeypatch.setattr(guest["socket"], "socket", lambda *args: Socket())
    with pytest.raises(ValueError, match="VM disposal proof required"):
        guest["transact"](later(packet, "reconcile"), time.monotonic() + 1)


def test_current_observation_rejects_changed_runner_identity(guest, packet, tmp_path, monkeypatch):
    value = owner(guest, packet, tmp_path, monkeypatch)
    value.process_identity = {"pid": 1}
    value.socket_identity = {"inode": 1}
    value.runner = SimpleNamespace(pid=123)
    value.runner_identity = {"pid": 123, "original": True}
    value.handles[123] = (77, value.runner_identity)
    value.ready = True
    monkeypatch.setitem(guest, "exited", lambda fd: False)
    monkeypatch.setitem(guest, "proc", lambda pid: {"changed": True})
    result = value.report("observe")
    assert not result["service_ready"] and result["mutation_effect"] == "unknown"
    assert result["unresolved_operations"] == [packet["operation_id"]]


def test_current_observation_rejects_rewritten_installed_input(guest, packet, tmp_path, monkeypatch):
    value = owner(guest, packet, tmp_path, monkeypatch)
    value.process_identity = {"pid": os.getpid()}
    value.runner = SimpleNamespace(pid=123)
    value.runner_identity = {"pid": 123}
    value.socket_path = tmp_path / "runner.sock"
    value.socket_path.touch()
    value.socket_identity = guest["identity"](value.socket_path)
    value.write(tmp_path / "input", b"original")
    monkeypatch.setitem(guest, "proc", lambda pid: {"pid": pid})
    monkeypatch.setitem(guest, "read", lambda path, **kwargs: path.read_bytes())
    value.audit_live()
    (tmp_path / "input").write_bytes(b"changed")
    with pytest.raises(ValueError, match="installed private input"):
        value.audit_live()


@pytest.mark.parametrize(
    "uids,gids",
    [([10001, 0, 0, 10001], [10001] * 4), ([100001] * 4, [100001] * 4), ([10001] * 4, [10001, 0, 0, 10001])],
)
def test_descendant_account_changes_are_not_enrolled_for_signals(
    guest, packet, tmp_path, monkeypatch, uids, gids
):
    value = owner(guest, packet, tmp_path, monkeypatch)
    closes = []
    monkeypatch.setattr(guest["os"], "pidfd_open", lambda pid, flags: 77, raising=False)
    monkeypatch.setattr(guest["os"], "close", closes.append)
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"123 (child) S 456 1 1")
    monkeypatch.setitem(guest, "proc", lambda pid: {"pid": pid, "uid": uids, "gid": gids})
    monkeypatch.setitem(guest, "send", lambda *args: pytest.fail("must never signal changed-account child"))
    with pytest.raises(ValueError, match="requires original VM disposal"):
        value.enroll(123, 456)
    assert closes == [77] and value.handles == {}
