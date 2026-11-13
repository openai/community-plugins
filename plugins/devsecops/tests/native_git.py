"""Select and pin a real host Git for portable, controlled test fixtures."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import NamedTuple


NATIVE_HEADERS = {
    b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}


class NativeGit(NamedTuple):
    path: Path
    sha256: str

    def verify(self):
        assert self.path.is_file() and os.access(self.path, os.X_OK), "Native Git is unavailable"
        assert hashlib.sha256(self.path.read_bytes()).hexdigest() == self.sha256, "Native Git identity changed"


@lru_cache(maxsize=1)
def selected_git() -> NativeGit:
    """An operator pin takes precedence; otherwise pin the native host tool once.

    This identifies the executable exercised by tests, not its publisher. Never
    execute a routing shim, change the selected bytes, or silently skip coverage.
    """
    requested = os.environ.get("DEVSECOPS_TEST_GIT")
    expected = os.environ.get("DEVSECOPS_TEST_GIT_SHA256")
    if bool(requested) != bool(expected):
        raise ValueError("Configure DEVSECOPS_TEST_GIT and DEVSECOPS_TEST_GIT_SHA256 together")
    if requested and (not Path(requested).is_absolute() or not re.fullmatch(r"[0-9a-f]{64}", expected)):
        raise ValueError("Native Git requires an absolute path and lowercase SHA-256")
    candidates = [requested] if requested else ["/opt/homebrew/bin/git", "/usr/local/bin/git", "/usr/bin/git", shutil.which("git")]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).resolve()
        # PATH may expose a native multicall router through a git symlink.
        # Resolving that alias changes argv[0]; it is not the actual Git tool.
        if path.name != "git":
            continue
        if not path.is_file() or not os.access(path, os.X_OK):
            continue
        with path.open("rb") as handle:
            if handle.read(4) not in NATIVE_HEADERS:
                continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        tool = NativeGit(path, expected or actual)
        tool.verify()
        return tool
    raise RuntimeError("A native Git executable is required; configure its absolute path and SHA-256")


@lru_cache(maxsize=1)
def git_exec_path() -> Path:
    tool = selected_git()
    tool.verify()
    environment = {"PATH": "", "LANG": "C", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run([str(tool.path), "--exec-path"], env=environment,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, "Cannot resolve the selected Git's executable directory"
    path = Path(result.stdout.strip())
    assert path.is_absolute() and path.is_dir(), "Git executable directory is not an absolute directory"
    assert (path / "git-http-backend").is_file(), "The selected Git has no HTTP backend"
    tool.verify()
    return path.resolve()
