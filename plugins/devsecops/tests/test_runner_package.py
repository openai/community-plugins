"""Controlled guest ownership; real reads and callers, no /opt or guest effects."""

from pathlib import Path
import os
import json
import time
from types import SimpleNamespace as NS
import pytest
from devsecops.factory import runner_package as p
from devsecops.factory.state import _sha
from tests.test_owned_ci_recipe import selected as publication_selected, cli
from tests.test_owned_deployment_validation import deployed, tools

__all__ = ["publication_selected", "deployed", "tools"]


def replace(path, raw):
    if path.exists():
        path.chmod(0o644)
    path.write_bytes(raw)
    path.chmod(0o444)


def info(original, *, uid=0, mode=None):
    values = {
        key: getattr(original, key)
        for key in ("st_dev st_ino st_mode st_uid st_gid st_size st_mtime_ns st_ctime_ns").split()
    }
    values.update(st_uid=uid, st_gid=0)
    if mode is not None:
        values["st_mode"] = mode
    return NS(**values)


@pytest.fixture
def guest(tmp_path, monkeypatch):
    prefix = tmp_path / "opt/devsecops"
    root = prefix / "package"
    root.mkdir(parents=True)
    python = prefix / "runtime/python3"
    python.parent.mkdir()
    python.write_bytes(b"controlled interpreter; never executed")
    python.chmod(0o555)
    sources = {
        ".codex-plugin/plugin.json": b'{"name":"devsecops","version":"test"}',
        "src/devsecops/__init__.py": b"# package\n",
        "src/devsecops/factory/runner_package.py": b"# selected controlled source\n",
        "scripts/publish_owned_mission.py": b"# fixed publisher\n",
        "scripts/validate_owned_deployment.py": b"# fixed MR validator\n",
        "scripts/publish_and_prepare_mission_git.py": b"# fixed argument parser\n",
    }
    for name, raw in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o444)
    manifest = root / "SHA256SUMS"

    def pin():
        members = sorted(path for path in root.rglob("*") if path.is_file() and path != manifest)
        raw = "".join(f"{_sha(path.read_bytes())}  {path.relative_to(root)}\n" for path in members).encode()
        replace(manifest, raw)
        manifest.chmod(0o444)
        return _sha(raw)

    expected = pin()
    real_lstat, real_fstat = Path.lstat, os.fstat

    def lstat(path):
        row = real_lstat(path)
        return info(row, mode=row.st_mode if path.is_relative_to(prefix) else row.st_mode & ~0o022)

    monkeypatch.setattr(Path, "lstat", lstat)
    fake_os = NS(**{key: getattr(os, key) for key in dir(os)})
    fake_os.fstat = lambda fd: info(real_fstat(fd))
    fake_os.getuid = fake_os.geteuid = fake_os.getgid = fake_os.getegid = lambda: 10001
    fake_os.getgroups = lambda: [10001]
    fake_os.environ = {
        "HOME": "/home/gitlab-runner",
        "DEVSECOPS_PLUGIN_ROOT": str(root),
        "DEVSECOPS_PYTHON": str(python),
    }
    modules = {
        name: NS(__file__=str(root / path))
        for name, path in {
            "__main__": "scripts/publish_owned_mission.py",
            "publish_and_prepare_mission_git": "scripts/publish_and_prepare_mission_git.py",
            "devsecops": "src/devsecops/__init__.py",
            "devsecops.factory.runner_package": "src/devsecops/factory/runner_package.py",
        }.items()
    }
    monkeypatch.setattr(p, "ROOT", root)
    monkeypatch.setattr(p, "_PREFIX", prefix)
    monkeypatch.setattr(p, "os", fake_os)
    monkeypatch.setattr(
        p,
        "sys",
        NS(
            platform="linux",
            version_info=(3, 11),
            executable=str(python),
            modules=modules,
            flags=NS(isolated=1, dont_write_bytecode=1),
            pycache_prefix=None,
        ),
    )
    monkeypatch.setattr(
        p, "pwd", NS(getpwuid=lambda _: NS(pw_name="gitlab-runner", pw_dir="/home/gitlab-runner"))
    )
    monkeypatch.setattr(p, "platform", NS(machine=lambda: "x86_64"))
    return NS(
        root=root, prefix=prefix, python=python, pin=pin, expected=expected, modules=modules, lstat=lstat
    )


def check(guest, expected=None):
    return p.installed_publication_package(
        expected or guest.expected, "devsecops@devsecops-local", "test", time.monotonic() + 30
    )


def test_fixed_guest_package_with_full_membership_and_modules(guest):
    assert check(guest) == guest.expected
    guest.modules["__main__"].__file__ = str(guest.root / "scripts/validate_owned_deployment.py")
    assert (
        p.installed_deployment_package(
            guest.expected, "devsecops@devsecops-local", "test", time.monotonic() + 30
        )
        == guest.expected
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "root",
        "effective-root",
        "gid",
        "effective-gid",
        "groups",
        "account",
        "home",
        "env-home",
        "linux",
        "arch",
        "selection",
        "outside-prefix",
        "python",
        "python-inside-package",
        "not-isolated",
        "writes-bytecode",
        "external-cache",
        "python-version",
    ],
)
def test_original_guest_selection_is_required(guest, mutation):
    if mutation in ("root", "effective-root", "gid", "effective-gid"):
        field = {"root": "getuid", "effective-root": "geteuid", "gid": "getgid", "effective-gid": "getegid"}[
            mutation
        ]
        setattr(p.os, field, lambda: 0)
    elif mutation == "groups":
        p.os.getgroups = lambda: [10001, 5]
    elif mutation in ("account", "home"):
        p.pwd.getpwuid = lambda _: NS(
            pw_name="other" if mutation == "account" else "gitlab-runner",
            pw_dir="/other" if mutation == "home" else "/home/gitlab-runner",
        )
    elif mutation == "env-home":
        p.os.environ["HOME"] = "/other"
    elif mutation == "linux":
        p.sys.platform = "darwin"
    elif mutation == "arch":
        p.platform.machine = lambda: "aarch64"
    elif mutation == "selection":
        p.os.environ["DEVSECOPS_PLUGIN_ROOT"] = "/other"
    elif mutation == "outside-prefix":
        p._PREFIX = Path("/opt/devsecops")
    elif mutation == "python":
        p.sys.executable = "/usr/bin/python3"
    elif mutation == "not-isolated":
        p.sys.flags.isolated = 0
    elif mutation == "writes-bytecode":
        p.sys.flags.dont_write_bytecode = 0
    elif mutation == "python-version":
        p.sys.version_info = (3, 10)
    elif mutation == "external-cache":
        p.sys.pycache_prefix = "/outside/cache"
    else:
        p.os.environ["DEVSECOPS_PYTHON"] = p.sys.executable = str(guest.root / "python")
    with pytest.raises(ValueError):
        check(guest)


@pytest.mark.parametrize("target", ["root", "member", "manifest", "interpreter"])
@pytest.mark.parametrize("defect", ["owner", "writable"])
def test_package_and_interpreter_are_immutable_to_guest(guest, monkeypatch, target, defect):
    path = {
        "root": guest.root,
        "member": guest.root / "src/devsecops/__init__.py",
        "manifest": guest.root / "SHA256SUMS",
        "interpreter": guest.python,
    }[target]

    def changed(current):
        row = guest.lstat(current)
        return (
            info(
                row,
                uid=10001 if defect == "owner" else 0,
                mode=row.st_mode | (0o020 if defect == "writable" else 0),
            )
            if current == path
            else row
        )

    monkeypatch.setattr(Path, "lstat", changed)
    with pytest.raises(ValueError):
        check(guest)


@pytest.mark.parametrize(
    "mutation",
    [
        "stale-pin",
        "changed-member",
        "extra",
        "missing",
        "symlink",
        "fifo",
        "plugin",
        "version",
        "duplicate",
        "escape",
        "bytecode-manifested",
        "pth-manifested",
    ],
)
def test_exact_current_manifest_membership_is_required(guest, mutation):
    expected = guest.expected
    member = guest.root / "src/devsecops/__init__.py"
    if mutation == "stale-pin":
        expected = "0" * 64
    elif mutation == "changed-member":
        replace(member, b"changed")
    elif mutation == "extra":
        (guest.root / "old.py").write_bytes(b"extra")
    elif mutation == "missing":
        member.unlink()
    elif mutation == "symlink":
        (guest.root / "link").symlink_to(member)
    elif mutation == "fifo":
        os.mkfifo(guest.root / "fifo")
    elif mutation in ("plugin", "version"):
        replace(
            guest.root / ".codex-plugin/plugin.json",
            (
                '{"name":"other","version":"test"}'
                if mutation == "plugin"
                else '{"name":"devsecops","version":"stale"}'
            ).encode(),
        )
        expected = guest.pin()
    elif mutation in ("bytecode-manifested", "pth-manifested"):
        (guest.root / ("old.pyc" if mutation == "bytecode-manifested" else "old.pth")).write_bytes(b"cache")
        expected = guest.pin()
    else:
        manifest = guest.root / "SHA256SUMS"
        raw = manifest.read_bytes()
        raw += raw.splitlines(keepends=True)[0] if mutation == "duplicate" else b"0" * 64 + b"  ../outside\n"
        replace(manifest, raw)
        expected = _sha(raw)
    with pytest.raises((ValueError, OSError)):
        check(guest, expected)


@pytest.mark.parametrize(
    "module", ["__main__", "publish_and_prepare_mission_git", "devsecops", "devsecops.factory.runner_package"]
)
def test_loaded_code_cannot_come_from_another_checkout(guest, module):
    guest.modules[module].__file__ = "/other/source.py"
    with pytest.raises(ValueError, match="Loaded CI code"):
        check(guest)


def test_interpreter_root_owned_symlink_uses_original_target(guest):
    target = guest.python.with_name("python-real")
    guest.python.rename(target)
    guest.python.symlink_to(target.name)
    assert check(guest) == guest.expected


def test_member_read_deadline_is_not_extended(guest, monkeypatch):
    original = p.os.read

    def expired(fd, size):
        raw = original(fd, size)
        monkeypatch.setattr(p, "checkpoint", lambda _: (_ for _ in ()).throw(ValueError("deadline")))
        return raw

    p.os.read = expired
    with pytest.raises(ValueError, match="deadline"):
        check(guest)


def test_changed_open_file_is_rejected(guest):
    original = p.os.fstat

    def changed(fd):
        row = original(fd)
        row.st_ctime_ns += 1
        return row

    p.os.fstat = changed
    with pytest.raises(ValueError, match="changed before"):
        check(guest)


def test_directory_and_aggregate_bounds(guest, monkeypatch):
    monkeypatch.setattr(p, "_MAX_DIRECTORIES", 1)
    with pytest.raises(ValueError, match="directory bound"):
        check(guest)
    monkeypatch.setattr(p, "_MAX_DIRECTORIES", 1024)
    monkeypatch.setattr(p, "_MAX_TOTAL_BYTES", 1)
    with pytest.raises(ValueError, match="bound"):
        check(guest)


def test_expired_selection_has_no_file_reads(guest, monkeypatch):
    monkeypatch.setattr(p, "_read_immutable", lambda *args: pytest.fail("expired read"))
    with pytest.raises(ValueError):
        p.installed_publication_package(
            guest.expected, "devsecops@devsecops-local", "test", time.monotonic() - 1
        )


def test_canonical_publication_calls_fixed_gate_before_and_after(guest, publication_selected, monkeypatch):
    invocation, refs, _, calls, args, _ = publication_selected
    invocation["plugin_id"], invocation["plugin_version"] = "devsecops@devsecops-local", "test"
    invocation["run_scope"]["package_sha256"] = guest.expected
    build_path = Path(refs["build"]["path"])
    build = json.loads(build_path.read_bytes())
    build["run_scope"] = invocation["run_scope"]
    build_path.write_text(json.dumps(build))
    refs["build"]["sha256"] = _sha(build_path.read_bytes())
    admissions = []

    def admission(*args):
        result = p.installed_publication_package(*args)
        admissions.append(result)
        return result

    monkeypatch.setattr(cli, "_installed", admission)
    assert cli.main(args) == 0
    assert len(calls) == 1 and admissions == [guest.expected] * 2


def test_canonical_publication_rejects_package_before_publication(guest, publication_selected, monkeypatch):
    invocation, refs, _, calls, args, _ = publication_selected
    invocation["plugin_id"], invocation["plugin_version"] = "devsecops@devsecops-local", "test"
    invocation["run_scope"]["package_sha256"] = guest.expected
    monkeypatch.setattr(cli, "_installed", p.installed_publication_package)
    (guest.root / "unmanifested.pyc").write_bytes(b"cache")
    assert cli.main(args) == 2 and not calls


def test_canonical_mr_validation_calls_fixed_gate_on_actual_checkout(guest, deployed, monkeypatch):
    from devsecops.factory import deployment_validation as v

    args, _, invocation, _ = deployed
    invocation["run_scope"]["package_sha256"] = guest.expected
    guest.modules["__main__"].__file__ = str(guest.root / "scripts/validate_owned_deployment.py")
    monkeypatch.setattr(v, "_installed", p.installed_deployment_package)
    assert v.validate_checkout(**args)["decision"] == "pass"


def test_canonical_mr_rejects_package_before_git(guest, deployed, monkeypatch):
    from devsecops.factory import deployment_validation as v

    args, _, invocation, _ = deployed
    invocation["run_scope"]["package_sha256"] = guest.expected
    guest.modules["__main__"].__file__ = str(guest.root / "scripts/validate_owned_deployment.py")
    monkeypatch.setattr(v, "_installed", p.installed_deployment_package)
    monkeypatch.setattr(v, "git", lambda *args, **kwargs: pytest.fail("Git before package admission"))
    (guest.root / "extra").write_bytes(b"extra")
    with pytest.raises(ValueError, match="unmanifested"):
        v.validate_checkout(**args)


def test_external_interpreter_link_must_not_target_plugin(guest):
    target = guest.root / "python-real"
    guest.python.rename(target)
    guest.python.symlink_to(target)
    with pytest.raises(ValueError, match="outside the plugin"):
        check(guest)


def test_canonical_publication_drift_after_publish_cannot_emit_selector(
    guest, publication_selected, monkeypatch, capsys
):
    invocation, refs, _, calls, args, _ = publication_selected
    invocation["plugin_id"], invocation["plugin_version"] = "devsecops@devsecops-local", "test"
    invocation["run_scope"]["package_sha256"] = guest.expected
    path = Path(refs["build"]["path"])
    build = json.loads(path.read_bytes())
    build["run_scope"] = invocation["run_scope"]
    path.write_text(json.dumps(build))
    refs["build"]["sha256"] = _sha(path.read_bytes())
    publish = cli.qualify_sign_and_publish

    def changed(**kwargs):
        result = publish(**kwargs)
        (guest.root / "late.pyc").write_bytes(b"stale")
        return result

    monkeypatch.setattr(cli, "qualify_sign_and_publish", changed)
    monkeypatch.setattr(cli, "_installed", p.installed_publication_package)
    assert cli.main(args) == 2 and len(calls) == 1
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "member",
    [
        "src/devsecops/factory/runner_package.py",
        "docs/interfaces/owned-runner-package.md",
        "docs/interfaces/owned-runner-ci.md",
        "docs/interfaces/runner-vm-routes.md",
    ],
)
def test_packaged_ci_admission_requires_module_and_interfaces(member):
    from scripts import build_plugin

    files = build_plugin.package_files()
    build_plugin.validate(files)
    files.pop(member)
    with pytest.raises(ValueError, match="Factory runtime is missing"):
        build_plugin.validate(files)
