"""Read-only admission of the preselected package in the owned Linux CI guest.

Only the two fixed CI entry points use this contract. It is not a Codex install,
capability restoration, or an alternative path for the native desktop driver.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import sys

from devsecops.adapters.vulnerability_policy import checkpoint
from .state import _json, _sha

ROOT = Path(__file__).resolve().parents[3]
_PREFIX = Path("/opt/devsecops")
_HOME = "/home/gitlab-runner"
_UID = 10001
_MAX_FILES = 1024
_MAX_DIRECTORIES = 1024
_MAX_FILE_BYTES = 16_000_000
_MAX_TOTAL_BYTES = 128_000_000
_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9._+-]{0,119}"
_ENTRYPOINTS = ("scripts/publish_owned_mission.py", "scripts/validate_owned_deployment.py")


def _identity(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _owned(path, deadline, *, directory=False):
    checkpoint(deadline)
    if not path.is_absolute() or str(path) != os.path.normpath(str(path)) or ".." in path.parts:
        raise ValueError("Absolute immutable guest package paths required")
    for current in (path, *path.parents):
        checkpoint(deadline)
        row = current.lstat()
        if (
            row.st_uid != 0
            or row.st_mode & 0o022
            or stat.S_ISLNK(row.st_mode)
            or current != path
            and not stat.S_ISDIR(row.st_mode)
        ):
            raise ValueError("Root-owned guest paths must be immutable to the runner")
    row = path.lstat()
    if not (stat.S_ISDIR(row.st_mode) if directory else stat.S_ISREG(row.st_mode)):
        raise ValueError("Guest package member has an unsupported file type")
    checkpoint(deadline)
    return row


def _read_immutable(path, limit, deadline):
    before = _owned(path, deadline)
    if before.st_size > limit:
        raise ValueError("Guest package member exceeds its bound")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if _identity(os.fstat(descriptor)) != _identity(before):
            raise ValueError("Guest package member changed before reading")
        chunks, size = [], 0
        while True:
            checkpoint(deadline)
            part = os.read(descriptor, min(65536, limit + 1 - size))
            checkpoint(deadline)
            if not part:
                break
            chunks.append(part)
            size += len(part)
            if size > limit:
                raise ValueError("Guest package member exceeds its bound")
        if _identity(os.fstat(descriptor)) != _identity(before) or _identity(
            _owned(path, deadline)
        ) != _identity(before):
            raise ValueError("Guest package member changed during reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _guest(deadline):
    checkpoint(deadline)
    user = pwd.getpwuid(os.getuid())
    if (
        sys.platform != "linux"
        or sys.version_info < (3, 11)
        or sys.flags.isolated != 1
        or sys.flags.dont_write_bytecode != 1
        or sys.pycache_prefix is not None
        or platform.machine() != "x86_64"
        or any(value != _UID for value in (os.getuid(), os.geteuid(), os.getgid(), os.getegid()))
        or set(os.getgroups()) - {_UID}
        or user.pw_name != "gitlab-runner"
        or user.pw_dir != _HOME
        or os.environ.get("HOME") != _HOME
    ):
        raise ValueError("The original nonroot Linux amd64 CI guest is required")
    selected = os.environ.get("DEVSECOPS_PLUGIN_ROOT")
    if (
        not isinstance(selected, str)
        or selected != str(ROOT)
        or not ROOT.is_relative_to(_PREFIX)
        or ROOT == _PREFIX
    ):
        raise ValueError("The preselected /opt guest package must contain this loaded engine")
    _owned(ROOT, deadline, directory=True)
    # The selected prebuilt interpreter may be a root-owned venv symlink. Its
    # literal selection and every link/target ancestor must be immutable; it
    # cannot select an interpreter bundled inside the plugin or a user runtime.
    selected_python = os.environ.get("DEVSECOPS_PYTHON")
    if (
        not isinstance(selected_python, str)
        or not re.fullmatch(re.escape(str(_PREFIX)) + r"/[A-Za-z0-9_./-]+", selected_python)
        or ".." in Path(selected_python).parts
        or str(Path(selected_python)) != selected_python
        or sys.executable != selected_python
        or Path(selected_python).is_relative_to(ROOT)
    ):
        raise ValueError("The selected external prebuilt guest interpreter is required")
    path = Path(selected_python)
    for _ in range(16):
        checkpoint(deadline)
        _owned(path.parent, deadline, directory=True)
        row = path.lstat()
        if row.st_uid != 0:
            raise ValueError("Guest interpreter link is not root owned")
        if not stat.S_ISLNK(row.st_mode):
            info = _owned(path, deadline)
            if path.is_relative_to(ROOT):
                raise ValueError("Guest interpreter target must remain outside the plugin")
            if not info.st_mode & 0o111:
                raise ValueError("Guest interpreter is not executable")
            break
        target = os.readlink(path)
        path = Path(os.path.normpath(target if os.path.isabs(target) else str(path.parent / target)))
    else:
        raise ValueError("Guest interpreter link chain exceeds its bound")

    checkpoint(deadline)


def _members(deadline):
    actual, directories, queue = set(), 0, [ROOT]
    while queue:
        checkpoint(deadline)
        current = queue.pop()
        _owned(current, deadline, directory=True)
        directories += 1
        if directories > _MAX_DIRECTORIES:
            raise ValueError("Guest package directory bound exceeded")
        with os.scandir(current) as entries:
            for item in entries:
                checkpoint(deadline)
                if item.is_symlink():
                    raise ValueError("Guest package contains a symlink")
                if item.is_dir(follow_symlinks=False):
                    queue.append(Path(item.path))
                    if len(queue) + directories > _MAX_DIRECTORIES:
                        raise ValueError("Guest package directory bound exceeded")
                elif item.is_file(follow_symlinks=False):
                    actual.add(str(Path(item.path).relative_to(ROOT)))
                    if len(actual) > _MAX_FILES + 1:
                        raise ValueError("Guest package file bound exceeded")
                else:
                    raise ValueError("Guest package contains a nonregular member")
    return actual


def _installed(expected, plugin_id, version, deadline, *, entrypoint):
    if (
        not isinstance(expected, str)
        or not re.fullmatch("[a-f0-9]{64}", expected)
        or not isinstance(plugin_id, str)
        or not re.fullmatch(_SEGMENT + "@" + _SEGMENT, plugin_id)
        or not isinstance(version, str)
        or not re.fullmatch("[A-Za-z0-9_.-]{1,100}", version)
        or type(deadline) not in (int, float)
        or not math.isfinite(deadline)
        or entrypoint not in _ENTRYPOINTS
    ):
        raise ValueError("Exact current package and bounded fixed CI selection required")
    _guest(deadline)
    manifest = _read_immutable(ROOT / "SHA256SUMS", 256000, deadline)
    if _sha(manifest) != expected:
        raise ValueError("Guest installed manifest differs from the selected current package")
    lines = manifest.decode("ascii").splitlines()
    if not 1 <= len(lines) <= _MAX_FILES:
        raise ValueError("Guest package member count exceeds its bound")
    members, total, metadata = {}, 0, None
    for line in lines:
        checkpoint(deadline)
        match = re.fullmatch(r"([a-f0-9]{64})  ([A-Za-z0-9_./-]+)", line)
        if not match:
            raise ValueError("Malformed guest package manifest")
        digest, name = match.groups()
        if (
            name in members
            or name == "SHA256SUMS"
            or name.endswith((".pyc", ".pyo", ".pth"))
            or "__pycache__" in Path(name).parts
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            or str(Path(name)) != name
        ):
            raise ValueError("Guest package manifest member is not unique and relative")
        raw = _read_immutable(ROOT / name, _MAX_FILE_BYTES, deadline)
        total += len(raw)
        if total > _MAX_TOTAL_BYTES or _sha(raw) != digest:
            raise ValueError("Guest package contents differ or exceed their bound")
        members[name] = digest
        if name == ".codex-plugin/plugin.json":
            metadata = _json(raw)
    if _members(deadline) != set(members) | {"SHA256SUMS"}:
        raise ValueError("Guest package has unmanifested or missing files")
    if (
        not isinstance(metadata, dict)
        or metadata.get("name") != plugin_id.split("@")[0]
        or metadata.get("version") != version
    ):
        raise ValueError("Guest plugin identity differs from the selected current package")
    if any(key in metadata for key in ("mcpServers", "mcp_servers", "hooks")) or any(
        name == ".mcp.json" or name.startswith("hooks/") for name in members
    ):
        raise ValueError("Fixed CI supports the selected skills-only package")
    for name, module in tuple(sys.modules.items()):
        checkpoint(deadline)
        if name == "__main__":
            allowed = (entrypoint,)
        elif name == "publish_and_prepare_mission_git":
            allowed = ("scripts/publish_and_prepare_mission_git.py",)
        elif name == "devsecops" or name.startswith("devsecops."):
            relative = "src/" + name.replace(".", "/")
            allowed = (relative + ".py", relative + "/__init__.py")
        else:
            continue
        location = getattr(module, "__file__", None)
        selected = next(
            (item for item in allowed if isinstance(location, str) and Path(location) == ROOT / item), None
        )
        if (
            selected is None
            or selected not in members
            or _sha(_read_immutable(ROOT / selected, _MAX_FILE_BYTES, deadline)) != members[selected]
        ):
            raise ValueError("Loaded CI code differs from the selected guest package")
    if "__main__" not in sys.modules or "devsecops.factory.runner_package" not in sys.modules:
        raise ValueError("Fixed CI entry point and package admission module must be loaded")
    if _read_immutable(ROOT / "SHA256SUMS", 256000, deadline) != manifest:
        raise ValueError("Guest package manifest changed during admission")
    _guest(deadline)
    checkpoint(deadline)
    return expected


def installed_publication_package(expected, plugin_id, version, deadline):
    return _installed(expected, plugin_id, version, deadline, entrypoint=_ENTRYPOINTS[0])


def installed_deployment_package(expected, plugin_id, version, deadline):
    return _installed(expected, plugin_id, version, deadline, entrypoint=_ENTRYPOINTS[1])
