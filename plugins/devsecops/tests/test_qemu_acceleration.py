"""Controlled kernel/device contracts; these cases do not prove live KVM."""

from copy import deepcopy
import os
import stat
from types import SimpleNamespace

import pytest

from devsecops.factory import qemu_acceleration as acceleration
from devsecops.factory import runner_vm_recipe
from tests.test_runner_vm_recipe import selection


@pytest.mark.parametrize("value", [
    {}, {"kind": "tcg", "device_group": 108}, {"kind": "kvm", "device_group": True},
    {"kind": "kvm", "device_group": 0}, {"kind": "kvm", "device_group": -1},
    {"kind": "kvm", "device_group": "108"}, {"kind": "kvm", "device_group": 2**31},
    {"kind": "kvm", "device_group": 108, "device": "/dev/mem"},
])
def test_ambiguous_or_widened_selection_is_rejected(value):
    with pytest.raises(ValueError):
        acceleration.validate(value)


def test_tcg_does_not_probe_devices_or_acquire_a_group(monkeypatch):
    monkeypatch.setattr(acceleration, "os", SimpleNamespace())
    assert acceleration.docker_arguments(None) == []
    assert acceleration.preflight(None) == {"kind": "tcg", "device_exposed": False}
    assert acceleration.runtime_matches({}, None)
    assert not acceleration.runtime_matches({"GroupAdd": ["108"]}, None)
    assert not acceleration.runtime_matches({"Devices": False}, None)
    assert not acceleration.runtime_matches({"DeviceCgroupRules": ["c *:* rwm"]}, None)
    assert not acceleration.runtime_matches({"DeviceRequests": [{}]}, None)


def test_kvm_grants_only_read_write_access_to_the_selected_character_device():
    selected = {"kind": "kvm", "device_group": 108}
    assert acceleration.docker_arguments(selected) == [
        "--device", "/dev/kvm:/dev/kvm:rw", "--group-add", "108"
    ]
    host = {"Devices": [{"PathOnHost": "/dev/kvm", "PathInContainer": "/dev/kvm", "CgroupPermissions": "rw"}], "GroupAdd": ["108"]}
    assert acceleration.runtime_matches(host, selected)
    for key, value in (("Devices", []), ("GroupAdd", ["0"]), ("GroupAdd", ["108", "0"]),
                       ("GroupAdd", [108]), ("Devices", {}),
                       ("DeviceCgroupRules", ["c *:* rwm"]), ("DeviceRequests", [{}]),
                       ("DeviceCgroupRules", {}), ("DeviceRequests", False)):
        damaged = deepcopy(host)
        damaged[key] = value
        assert not acceleration.runtime_matches(damaged, selected)
    for key, value in (("PathOnHost", "/dev/mem"), ("PathInContainer", "/dev/mem"), ("CgroupPermissions", "rwm")):
        damaged = deepcopy(host)
        damaged["Devices"][0][key] = value
        assert not acceleration.runtime_matches(damaged, selected)


def kernel(monkeypatch, *, mode=stat.S_IFCHR | 0o660, uid=0, gid=108, minor=232, version=12, create_error=None, changed=False):
    info = SimpleNamespace(st_dev=3, st_ino=7, st_rdev=os.makedev(10, minor), st_uid=uid,
                           st_gid=gid, st_mode=mode, st_mtime_ns=11, st_ctime_ns=11)
    opened, closed, calls = [], [], []
    def open_device(path, flags):
        opened.append((path, flags))
        return 40
    def ioctl(fd, command, value):
        calls.append((fd, command, value))
        if command == acceleration.KVM_GET_API_VERSION:
            return version
        if create_error is not None:
            raise create_error
        return 41
    def fstat(fd):
        return SimpleNamespace(**{**vars(info), "st_ino": 9}) if changed else info
    fake = SimpleNamespace(lstat=lambda path: info, fstat=fstat, open=open_device, close=closed.append,
                           getuid=lambda: 1000, geteuid=lambda: 1000, major=os.major, minor=os.minor,
                           uname=lambda: SimpleNamespace(machine="x86_64"),
                           O_RDWR=os.O_RDWR, O_NOFOLLOW=os.O_NOFOLLOW, O_CLOEXEC=os.O_CLOEXEC)
    monkeypatch.setattr(acceleration, "os", fake)
    monkeypatch.setattr(acceleration, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(acceleration, "fcntl", SimpleNamespace(ioctl=ioctl))
    return SimpleNamespace(opened=opened, closed=closed, calls=calls, os=fake)


def test_kernel_capability_admission_closes_both_kernel_descriptors(monkeypatch):
    observed = kernel(monkeypatch)
    receipt = acceleration.preflight({"kind": "kvm", "device_group": 108})
    assert receipt["empty_vm_created_and_closed"] and receipt["api_version"] == 12
    assert receipt["fallback_permitted"] is False
    assert observed.closed == [41, 40]
    assert observed.opened[0][1] & os.O_NOFOLLOW and observed.opened[0][1] & os.O_CLOEXEC


@pytest.mark.parametrize("damage", [
    {"mode": stat.S_IFLNK | 0o660}, {"mode": stat.S_IFREG | 0o660},
    {"mode": stat.S_IFCHR | 0o666}, {"uid": 1000}, {"gid": 0}, {"gid": 109}, {"minor": 233},
])
def test_wrong_device_rejected_before_open(monkeypatch, damage):
    observed = kernel(monkeypatch, **damage)
    with pytest.raises(ValueError):
        acceleration.preflight({"kind": "kvm", "device_group": 108})
    assert not observed.opened and not observed.calls


@pytest.mark.parametrize("damage,error", [
    ({"version": 11}, ValueError), ({"changed": True}, ValueError),
    ({"create_error": OSError("controlled unavailable KVM")}, OSError),
])
def test_failed_kernel_admission_closes_opened_device(monkeypatch, damage, error):
    observed = kernel(monkeypatch, **damage)
    with pytest.raises(error):
        acceleration.preflight({"kind": "kvm", "device_group": 108})
    assert observed.closed == [40]


def test_no_root_or_non_linux_admission(monkeypatch):
    observed = kernel(monkeypatch)
    observed.os.getuid = lambda: 0
    with pytest.raises(ValueError, match="nonroot"):
        acceleration.preflight({"kind": "kvm", "device_group": 108})
    assert not observed.opened
    observed.os.getuid = lambda: 1000
    monkeypatch.setattr(acceleration, "sys", SimpleNamespace(platform="darwin"))
    with pytest.raises(ValueError, match="Linux"):
        acceleration.preflight({"kind": "kvm", "device_group": 108})
    assert not observed.opened


def test_missing_device_and_wrong_architecture_never_fall_back(monkeypatch):
    observed = kernel(monkeypatch)
    observed.os.uname = lambda: SimpleNamespace(machine="aarch64")
    with pytest.raises(ValueError, match="x86_64"):
        acceleration.preflight({"kind": "kvm", "device_group": 108})
    observed.os.uname = lambda: SimpleNamespace(machine="x86_64")
    def missing(path):
        raise FileNotFoundError("controlled missing KVM")
    observed.os.lstat = missing
    with pytest.raises(FileNotFoundError):
        acceleration.preflight({"kind": "kvm", "device_group": 108})
    assert not observed.opened and not observed.calls


def test_device_swap_after_vm_creation_closes_both_descriptors(monkeypatch):
    observed = kernel(monkeypatch)
    original = observed.os.fstat(40)
    count = []
    def replaced(fd):
        count.append(fd)
        return original if len(count) == 1 else SimpleNamespace(**{**vars(original), "st_ino": 19})
    observed.os.fstat = replaced
    with pytest.raises(ValueError, match="changed"):
        acceleration.preflight({"kind": "kvm", "device_group": 108})
    assert observed.closed == [41, 40]


def test_runner_cannot_change_the_foundation_acceleration_boundary(tmp_path):
    runner, foundation = selection(tmp_path)
    runner["acceleration"] = {"kind": "kvm", "device_group": 108}
    with pytest.raises(ValueError, match="acceleration"):
        runner_vm_recipe.validate_against_foundation(runner, foundation)
    foundation["acceleration"] = deepcopy(runner["acceleration"])
    assert runner_vm_recipe.validate_against_foundation(runner, foundation) == runner
    foundation["acceleration"]["device_group"] = 109
    with pytest.raises(ValueError, match="acceleration"):
        runner_vm_recipe.validate_against_foundation(runner, foundation)
