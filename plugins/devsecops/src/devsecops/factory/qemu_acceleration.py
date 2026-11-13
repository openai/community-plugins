"""Explicit, narrow KVM admission for an already authorized owned Linux host.

Absence selects the existing TCG contract. This module never loads a kernel
module, changes a device, installs software, grants privileges, or falls back.
It is transported with the exact installed package to each remote VM driver.
"""

from copy import deepcopy
import fcntl
import os
import stat
import sys

DEVICE = "/dev/kvm"
KVM_GET_API_VERSION = 0xAE00
KVM_CREATE_VM = 0xAE01


def validate(value):
    if value is None:
        return None
    if (
        type(value) is not dict
        or set(value) != {"kind", "device_group"}
        or value["kind"] != "kvm"
        or type(value["device_group"]) is not int
        or not 1 <= value["device_group"] <= 2**31 - 1
    ):
        raise ValueError("KVM requires its exact device and one explicit nonroot device group")
    return deepcopy(value)


def docker_arguments(value):
    selected = validate(value)
    if selected is None:
        return []
    return ["--device", DEVICE + ":" + DEVICE + ":rw", "--group-add", str(selected["device_group"])]


def runtime_matches(host, value):
    selected = validate(value)
    for name in ("DeviceCgroupRules", "DeviceRequests"):
        additional = host.get(name)
        if additional is not None and (type(additional) is not list or additional):
            return False
    devices = [] if selected is None else [
        {"PathOnHost": DEVICE, "PathInContainer": DEVICE, "CgroupPermissions": "rw"}
    ]
    groups = [] if selected is None else [str(selected["device_group"])]
    actual_devices, actual_groups = host.get("Devices"), host.get("GroupAdd")
    if actual_devices is None:
        actual_devices = []
    if actual_groups is None:
        actual_groups = []
    return (type(actual_devices) is list and type(actual_groups) is list
            and actual_devices == devices and actual_groups == groups)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_rdev, info.st_uid, info.st_gid,
            info.st_mode, info.st_mtime_ns, info.st_ctime_ns)


def preflight(value):
    """Prove nonroot access and create/close one empty VM before persistent effects."""
    selected = validate(value)
    if selected is None:
        return {"kind": "tcg", "device_exposed": False}
    if sys.platform != "linux" or os.getuid() == 0 or os.geteuid() == 0:
        raise ValueError("KVM admission requires the nonroot operator on its selected Linux host")
    if os.uname().machine != "x86_64":
        raise ValueError("This KVM guest requires a native x86_64 host")
    original = os.lstat(DEVICE)
    if (
        not stat.S_ISCHR(original.st_mode)
        or (os.major(original.st_rdev), os.minor(original.st_rdev)) != (10, 232)
        or original.st_uid != 0
        or original.st_gid != selected["device_group"]
        or stat.S_IMODE(original.st_mode) != 0o660
    ):
        raise ValueError("KVM device identity, group or permissions differ from the explicit selection")
    descriptor = os.open(DEVICE, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    guest = None
    try:
        if _identity(os.fstat(descriptor)) != _identity(original):
            raise ValueError("KVM device changed while it was opened")
        version = fcntl.ioctl(descriptor, KVM_GET_API_VERSION, 0)
        if type(version) is not int or version != 12:
            raise ValueError("KVM userspace API version is unsupported")
        guest = fcntl.ioctl(descriptor, KVM_CREATE_VM, 0)
        if type(guest) is not int or guest < 0:
            raise ValueError("KVM did not create an actual VM descriptor")
        if _identity(os.fstat(descriptor)) != _identity(original) or _identity(os.lstat(DEVICE)) != _identity(original):
            raise ValueError("KVM device changed during admission")
    finally:
        try:
            if guest is not None and type(guest) is int and guest >= 0:
                os.close(guest)
        finally:
            os.close(descriptor)
    return {
        "kind": "kvm", "device": DEVICE, "device_group": selected["device_group"],
        "device_major": 10, "device_minor": 232, "device_owner": 0,
        "device_mode": "0660", "api_version": 12,
        "empty_vm_created_and_closed": True,
        "device_identity": {"filesystem": original.st_dev, "inode": original.st_ino},
        "fallback_permitted": False,
    }
