"""Execute the exact transported runner helper with local scoped paths/TLS double."""

from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import ssl
from types import SimpleNamespace
from uuid import uuid4
import pytest
from devsecops.factory.service_link_remote import RUNNER_HELPER


@pytest.fixture
def runner(tmp_path, monkeypatch):
    root = tmp_path / "container"
    root.mkdir()
    for path in ("etc", "var/lib/devsecops", "opt/devsecops/runner-inputs/service-trust"):
        (root / path).mkdir(parents=True, exist_ok=True)
    hosts = root / "etc/hosts"
    hosts.write_bytes(b"127.0.0.1 localhost\n172.22.0.3 runner\n")
    for role in ("gitlab", "registry"):
        (root / f"opt/devsecops/runner-inputs/service-trust/{role}-ca.pem").write_bytes(
            b"CA-" + role.encode()
        )
    original_open, original_stat, original_mkdir = os.open, os.stat, os.mkdir

    def mapped(path):
        if isinstance(path, (str, Path)) and any(
            str(path).startswith(prefix)
            for prefix in ("/etc/hosts", "/var/lib/devsecops/service-links-", "/opt/devsecops/runner-inputs/")
        ):
            return root / str(path).lstrip("/")
        return path

    monkeypatch.setattr(os, "open", lambda path, *a, **kw: original_open(mapped(path), *a, **kw))
    monkeypatch.setattr(os, "stat", lambda path, *a, **kw: original_stat(mapped(path), *a, **kw))
    monkeypatch.setattr(os, "mkdir", lambda path, *a, **kw: original_mkdir(mapped(path), *a, **kw))
    calls = []
    controls = {}

    class TLS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def settimeout(self, value):
            assert value == 15

        def sendall(self, value):
            calls.append(("request", value))

        def recv(self, size):
            return b"HTTP/1.1 " + (b"200" if controls.get("bad_challenge") else b"401") + b" Unauthorized\r\n"

    class Context:
        def wrap_socket(self, connection, *, server_hostname):
            calls.append(("sni", server_hostname))
            return TLS()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def context(*, cafile):
        calls.append(("ca", cafile))
        return Context()

    def connection(address, *, timeout):
        calls.append(("connect", address))
        assert timeout == 15
        return Connection()

    monkeypatch.setattr(ssl, "create_default_context", context)
    monkeypatch.setattr(socket, "create_connection", connection)
    packet = {
        "run_id": str(uuid4()),
        "binding": "b" * 64,
        "gateway": "172.22.0.1",
        "hostname": "registry.demo.test",
        "ca_sha256": {
            role: hashlib.sha256(b"CA-" + role.encode()).hexdigest() for role in ("gitlab", "registry")
        },
    }

    def call(action):
        value = {**packet, "action": action}
        monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(value).encode())))
        output = io.StringIO()
        with redirect_stdout(output):
            exec(compile(RUNNER_HELPER, "owned-runner-links", "exec"), {"__name__": "owned_runner_links"})
        return json.loads(output.getvalue())

    return SimpleNamespace(call=call, root=root, hosts=hosts, packet=packet, calls=calls, controls=controls)


def test_exact_runner_namespace_mapping_tls_and_restore(runner):
    original = runner.hosts.read_bytes()
    up = runner.call("up")
    assert up["complete_cleanup"] is False
    assert ("connect", ("registry.demo.test", 8443)) in runner.calls
    assert ("sni", "registry.demo.test") in runner.calls
    assert ("ca", "/opt/devsecops/runner-inputs/service-trust/registry-ca.pem") in runner.calls
    assert b"172.22.0.1 registry.demo.test" in runner.hosts.read_bytes()
    count = len(runner.calls)
    read = runner.call("reconcile")
    assert len(runner.calls) == count and read["unresolved_operations"] == []
    down = runner.call("down")
    assert down["complete_cleanup"] is True and runner.hosts.read_bytes() == original
    with pytest.raises(ValueError, match="mapping drift"):
        runner.call("down")


def test_runner_staged_ca_drift_blocks_before_mapping(runner):
    original = runner.hosts.read_bytes()
    (runner.root / "opt/devsecops/runner-inputs/service-trust/registry-ca.pem").write_bytes(b"wrong")
    with pytest.raises(ValueError, match="CA differs"):
        runner.call("up")
    assert runner.hosts.read_bytes() == original and runner.calls == []


def test_runner_external_hosts_drift_is_preserved(runner):
    runner.call("up")
    runner.hosts.write_bytes(runner.hosts.read_bytes() + b"192.0.2.1 unrelated.test\n")
    changed = runner.hosts.read_bytes()
    with pytest.raises(ValueError, match="mapping drift"):
        runner.call("down")
    assert runner.hosts.read_bytes() == changed


def test_runner_failed_tls_keeps_original_mapping_for_readonly_reconciliation(runner):
    runner.controls["bad_challenge"] = True
    with pytest.raises(ValueError, match="challenge differs"):
        runner.call("up")
    read = runner.call("reconcile")
    assert read["unresolved_operations"] == [] and read["complete_cleanup"] is False
    assert runner.call("down")["complete_cleanup"] is True
