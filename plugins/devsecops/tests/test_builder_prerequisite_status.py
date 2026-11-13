"""Controlled kernel-status admission; no namespace, builder or registry effects."""

import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace

import pytest

from devsecops.factory import mission_operation, qualify_builder as q
from tests.test_mission_operation import configuration


@pytest.fixture
def linux_status(tmp_path, monkeypatch):
    selected = tmp_path / "status"
    selected.write_bytes(b"Name:\tcontrolled\nNoNewPrivs:\t0\nSeccomp:\t2\n")
    original_open = os.open

    def open_status(path, flags, *args, **kwargs):
        if os.fspath(path) == "/proc/self/status":
            path = selected
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(q.os, "open", open_status)
    monkeypatch.setattr(q.os, "getuid", lambda: 1000)
    monkeypatch.setattr(q.platform, "system", lambda: "Linux")
    return selected


def test_no_new_privs_rejected_before_helper_probe_or_process(linux_status, monkeypatch):
    linux_status.write_bytes(b"Name:\tcontrolled\nNoNewPrivs:\t1\n")
    monkeypatch.setattr(q.os, "pidfd_open", lambda *_: pytest.fail("No process probe"), raising=False)
    monkeypatch.setattr(q.pwd, "getpwuid", lambda *_: pytest.fail("No helper or account probe"))
    monkeypatch.setattr(q.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("No process start"))
    with pytest.raises(q.QualificationError, match="^uidmap-incompatible-no-new-privs$"):
        q.prerequisites(1000)


@pytest.mark.parametrize("raw", [
    b"Name:\tcontrolled\n",
    b"NoNewPrivs:\t0\nNoNewPrivs:\t1\n",
    b"NoNewPrivs:\t0\nNoNewPrivs:\t0\n",
    b"NoNewPrivs:\t2\n",
    b"NoNewPrivs:\t00\n",
    b"NoNewPrivs:\t0 extra\n",
    b"NoNewPrivs\n",
    b"NoNewPrivs:\t\xff\n",
])
def test_missing_ambiguous_or_invalid_kernel_flag_is_not_assumed_clear(linux_status, raw):
    linux_status.write_bytes(raw)
    with pytest.raises(q.QualificationError, match="^linux-no-new-privs-status-invalid$"):
        q.prerequisites(1000)


@pytest.mark.parametrize("damage", ["missing", "symlink", "oversized", "denied"])
def test_unavailable_or_unbounded_status_fails_without_fallback(linux_status, monkeypatch, damage):
    if damage == "missing":
        linux_status.unlink()
    elif damage == "symlink":
        target = linux_status.with_name("target")
        linux_status.rename(target)
        linux_status.symlink_to(target)
    elif damage == "oversized":
        linux_status.write_bytes(b"NoNewPrivs:\t0\n" + b"x" * 64000)
    else:
        original_open = q.os.open

        def denied(path, *args, **kwargs):
            if os.fspath(path) == "/proc/self/status":
                raise PermissionError("controlled private detail")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(q.os, "open", denied)
    with pytest.raises(q.QualificationError, match="^linux-no-new-privs-status-unavailable$"):
        q.prerequisites(1000)


def test_clear_flag_retains_exact_helper_and_subordinate_id_checks(linux_status, monkeypatch):
    original_regular = q._regular
    observed = []

    def regular(path, limit=64000, *, root_owned=False):
        if path == Path("/proc/self/status"):
            return original_regular(path, limit, root_owned=root_owned)
        observed.append((str(path), root_owned))
        if path.name in ("newuidmap", "newgidmap"):
            return b"controlled helper", SimpleNamespace(st_mode=stat.S_IFREG | 0o4755)
        return b"demo:100000:65536\n", None

    monkeypatch.setattr(q, "_regular", regular)
    monkeypatch.setattr(q.pwd, "getpwuid", lambda _: SimpleNamespace(pw_name="demo"))
    monkeypatch.setattr(q.os, "pidfd_open", lambda *_: os.open(os.devnull, os.O_RDONLY), raising=False)
    probes = []
    monkeypatch.setattr(q.signal, "pidfd_send_signal", lambda *_args: probes.append(_args[1]), raising=False)
    result = q.prerequisites(1000)
    assert result["uid"] == 1000
    assert result["subuid"] == result["subgid"] == {
        "start": 100000, "count": 65536, "sha256": q._sha(b"demo:100000:65536\n")}
    assert probes == [0]
    assert observed == [("/usr/bin/newuidmap", True), ("/usr/bin/newgidmap", True),
                        ("/etc/subuid", True), ("/etc/subgid", True)]
    assert all(row["mode"] == 0o4755 for row in result["helpers"].values())


def test_actual_mission_entrypoint_rejects_flag_before_credentialed_acquisition(linux_status, tmp_path, monkeypatch):
    linux_status.write_bytes(b"NoNewPrivs:\t1\n")
    _, args = configuration(tmp_path)
    monkeypatch.setattr(mission_operation, "qualify_mission_base",
                        lambda **_: pytest.fail("No credentialed acquisition"))
    monkeypatch.setattr(mission_operation, "build_mission", lambda **_: pytest.fail("No build"))
    with tempfile.TemporaryDirectory(prefix="dso-nnp-", dir="/tmp") as temporary:
        runtime = Path(temporary).resolve() / "runtime"
        with pytest.raises(q.QualificationError, match="^uidmap-incompatible-no-new-privs$"):
            mission_operation.qualify_and_build(**args, runtime_root=runtime)
        assert not args["output"].exists() and not runtime.exists()
