"""Controlled worker-RPC admission tests; no live rootless qualification claim."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile

import pytest

from devsecops.factory import build as b


@pytest.fixture
def worker(tmp_path, monkeypatch):
    binary = tmp_path / "source-buildctl"
    binary.write_bytes(b"controlled-test-executable-not-run")
    expected = {"sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "bytes": binary.stat().st_size}
    lock = {"builder": {"version": "0.34.1", "platforms": {"linux-amd64": {"binaries": {"buildctl": expected}}}}}
    (tmp_path / "supplychain.lock.json").write_text(json.dumps(lock))
    monkeypatch.setattr(b, "ROOT", tmp_path)
    monkeypatch.setattr(b.platform, "machine", lambda: "x86_64")
    peer = {"pid": 42, "uid": 1000, "process_start_ticks": 12, "socket_device": 1, "socket_inode": 2,
            "network_namespace_distinct_from_caller": True}
    peers, calls = [], []
    def inspect(**kwargs):
        peers.append(kwargs)
        return copy.deepcopy(peer)
    monkeypatch.setattr(b, "inspect_rootless_builder", inspect)
    prefix = "org.mobyproject.buildkit.worker."
    response = [{"id": "controlled-worker", "labels": {prefix + "executor": "oci", prefix + "snapshotter": "native",
        prefix + "oci.process-mode": "sandbox", prefix + "hostname": "DO_NOT_RETAIN_HOST_CANARY"},
        "platforms": [{"os": "linux", "architecture": "amd64"}], "cdiDevices": [],
        "buildkitVersion": {"package": "github.com/moby/buildkit", "version": "v0.34.1", "revision": "a" * 40,
                            "unknown": "DO_NOT_RETAIN_VERSION_CANARY"}}]
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        raw = (b"buildctl github.com/moby/buildkit v0.34.1 " + b"a" * 40 + b"\n"
               if "--version" in argv else json.dumps(response).encode())
        return 0, raw, b"", {"tool": "buildctl", "exit_code": 0}
    monkeypatch.setattr(b, "run", run)
    arguments = dict(socket_path=tmp_path / "builder.sock", expected_uid=1000, expected_device=1,
                     expected_inode=2, buildctl=binary, architecture="amd64")
    return arguments, response, peer, peers, calls


def test_rpc_inspection_is_bounded_pinned_private_and_not_build_authority(worker):
    arguments, _, _, peers, calls = worker
    result = b.inspect_rootless_worker(**arguments)
    assert result["worker_readiness"] == "rpc-verified"
    assert result["runtime_execution"] == "not-tested" and result["build_authorization"] == "not-granted"
    assert len(peers) == 2 and len(calls) == 2
    assert calls[1][0][1:] == ["--addr", "unix://" + str(arguments["socket_path"]), "debug", "workers", "--format", "{{json .}}"]
    for argv, kwargs in calls:
        assert "build" not in argv and "--allow" not in argv
        assert 0 < kwargs["timeout"] <= 30 and kwargs["limit"] == 1_000_000
        assert kwargs["env"]["OTEL_SDK_DISABLED"] == "true"
        assert set(kwargs["env"]) == {"PATH", "HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "DOCKER_CONFIG", "OTEL_SDK_DISABLED"}
    assert "CANARY" not in json.dumps(result)
    assert not Path(calls[0][0][0]).exists()  # Private tool/config scratch removed.


@pytest.mark.parametrize("defect", ["multi", "none", "sandbox", "executor", "snapshotter", "platform", "os", "version", "revision", "devices", "worker-id"])
def test_unsupported_worker_never_returns_verified_result(worker, defect):
    arguments, response, _, _, _ = worker
    prefix = "org.mobyproject.buildkit.worker."
    if defect == "multi":
        response.append(copy.deepcopy(response[0]))
    elif defect == "none":
        response.clear()
    elif defect == "sandbox":
        response[0]["labels"][prefix + "oci.process-mode"] = "no-sandbox"
    elif defect == "executor":
        response[0]["labels"][prefix + "executor"] = "containerd"
    elif defect == "snapshotter":
        response[0]["labels"][prefix + "snapshotter"] = "unqualified"
    elif defect == "platform":
        response[0]["platforms"] = [{"os": "linux", "architecture": "arm64"}]
    elif defect == "os":
        response[0]["platforms"] = [{"os": "windows", "architecture": "amd64"}]
    elif defect == "version":
        response[0]["buildkitVersion"]["version"] = "v0.34.2"
    elif defect == "revision":
        response[0]["buildkitVersion"]["revision"] = "b" * 40
    elif defect == "devices":
        response[0]["cdiDevices"] = [{"name": "unqualified-device", "autoAllow": True}]
    else:
        response[0]["id"] = "arbitrary\ntext"
    with pytest.raises(ValueError):
        b.inspect_rootless_worker(**arguments)


def test_peer_network_namespace_must_differ_before_any_query(worker):
    arguments, _, peer, _, calls = worker
    peer["network_namespace_distinct_from_caller"] = False
    with pytest.raises(ValueError, match="network namespace"):
        b.inspect_rootless_worker(**arguments)
    assert not calls


def test_peer_changed_after_query_is_not_accepted(worker, monkeypatch):
    arguments, _, peer, _, calls = worker
    original = b.inspect_rootless_builder
    def changed(**kwargs):
        if calls:
            peer["process_start_ticks"] += 1
        return original(**kwargs)
    monkeypatch.setattr(b, "inspect_rootless_builder", changed)
    with pytest.raises(ValueError, match="identity changed"):
        b.inspect_rootless_worker(**arguments)


def test_changed_buildctl_never_executes(worker):
    arguments, _, _, _, calls = worker
    arguments["buildctl"].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="pinned executable"):
        b.inspect_rootless_worker(**arguments)
    assert not calls


@pytest.mark.parametrize("timeout", [0, -1, 121, float("inf"), float("nan"), True, "30"])
def test_invalid_deadline_rejected_before_peer_query(worker, timeout):
    arguments, _, _, peers, calls = worker
    with pytest.raises(ValueError, match="timeout"):
        b.inspect_rootless_worker(**arguments, timeout=timeout)
    assert not peers and not calls


def test_rpc_failure_not_converted_to_readiness(worker, monkeypatch):
    arguments, _, _, _, _ = worker
    monkeypatch.setattr(b, "run", lambda *args, **kwargs: (1, b"", b"SECRET_TOOL_ERROR_CANARY", {}))
    with pytest.raises(ValueError, match="query failed") as error:
        b.inspect_rootless_worker(**arguments)
    assert "CANARY" not in str(error.value)


def test_socket_route_requires_protected_operator_parent():
    with tempfile.TemporaryDirectory(prefix="dso-", dir="/tmp") as temporary:
        parent = Path(temporary).resolve()
        descriptor = b._socket_parent(parent / "builder.sock", os.getuid())
        os.close(descriptor)
        parent.chmod(0o777)
        with pytest.raises(ValueError, match="writable|protected"):
            b._socket_parent(parent / "builder.sock", os.getuid())
        parent.chmod(0o700)


@pytest.mark.parametrize("name", ["builder?socket", "builder#socket", "builder%2fsocket", "builder\\socket", "builder\nsocket"])
def test_socket_route_cannot_be_reinterpreted_as_another_rpc_url(name):
    with pytest.raises(ValueError, match="unambiguous"):
        b._socket_parent(Path("/tmp") / name, os.getuid())


def test_socket_route_rejects_symlinked_ancestor():
    with tempfile.TemporaryDirectory(prefix="dso-", dir="/tmp") as temporary:
        parent = Path(temporary).resolve()
        (parent / "target").mkdir()
        (parent / "alias").symlink_to(parent / "target", target_is_directory=True)
        with pytest.raises(OSError):
            b._socket_parent(parent / "alias/builder.sock", os.getuid())
