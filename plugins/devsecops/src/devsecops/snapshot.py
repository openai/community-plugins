"""Freeze only selected inputs without checkout, filters, hooks, or Git writes."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import time
from .process import run
from .profiles import relative_path

MAX_FILES = 5000
MAX_BYTES = 64_000_000


def _remaining(deadline):
    value = 30 if deadline is None else min(30, deadline - time.monotonic())
    if value <= 0:
        raise ValueError('Selected input snapshot exceeded its deadline')
    return value


def git(repo: Path, *args: str, limit: int = MAX_BYTES, deadline=None) -> bytes:
    argv = ['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
            '-c', 'core.pager=cat', '-c', 'core.attributesFile=/dev/null', *args]
    code, out, _, _ = run(argv, cwd=repo, timeout=_remaining(deadline), limit=limit)
    _remaining(deadline)
    if code:
        raise ValueError("Git input unavailable; ensure requested revisions exist locally")
    return out


def resolve_ref(repo: Path, ref: str, *, deadline=None) -> str:
    if not ref or ref.startswith('-') or len(ref) > 200:
        raise ValueError("Invalid Git revision")
    value = git(repo, 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}', deadline=deadline).decode().strip()
    if not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', value):
        raise ValueError("Git did not resolve a commit")
    return value


def _write_files(files: dict[str, bytes], dest: Path, *, deadline=None) -> dict:
    total = sum(map(len, files.values()))
    if len(files) > MAX_FILES or total > MAX_BYTES:
        raise ValueError("Selected input snapshot exceeds file or byte limits")
    manifest = []
    for name, content in sorted(files.items()):
        _remaining(deadline)
        relative_path(name)
        if content.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
            raise ValueError('Selected input is an unmaterialized Git LFS pointer')
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(content)
        path.chmod(0o600)
        manifest.append({"path": name, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
    digest = hashlib.sha256()
    for item in manifest:
        _remaining(deadline)
        digest.update(item['path'].encode() + b'\0' + item['sha256'].encode() + b'\n')
    return {"sha256": digest.hexdigest(), "file_count": len(files), "bytes": total, "files": manifest}


def freeze_commit(repo: Path, sha: str, roots: list[str], dest: Path, *, deadline=None) -> dict:
    tree = git(repo, 'ls-tree', '-r', '-z', '--full-tree', sha, '--', *roots, deadline=deadline)
    files = {}
    total = 0
    entries = [e for e in tree.split(b'\0') if e]
    if len(entries) > MAX_FILES:
        raise ValueError("Selected input file limit exceeded")
    for entry in entries:
        _remaining(deadline)
        meta, name_bytes = entry.split(b'\t', 1)
        mode, kind, object_id = meta.decode('ascii').split()
        name = name_bytes.decode('utf-8')
        relative_path(name)
        if mode not in ('100644', '100755') or kind != 'blob':
            raise ValueError("Selected inputs contain a symlink or submodule; materialize trusted regular files")
        content = git(repo, 'cat-file', 'blob', object_id, limit=MAX_BYTES - total, deadline=deadline)
        total += len(content)
        if total > MAX_BYTES:
            raise ValueError("Selected input byte limit exceeded")
        files[name] = content
    result = _write_files(files, dest, deadline=deadline)
    result['revision'] = sha
    return result


def _worktree_files(repo: Path, roots: list[str]) -> dict[str, bytes]:
    raw = git(repo, 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', *roots)
    names = sorted(set(p.decode('utf-8') for p in raw.split(b'\0') if p))
    if len(names) > MAX_FILES:
        raise ValueError("Selected input file limit exceeded")
    files, total = {}, 0
    for name in names:
        relative_path(name)
        path = repo / name
        for part in (path, *path.parents):
            if part == repo:
                break
            if part.is_symlink():
                raise ValueError("Selected working-tree inputs contain a symlink")
        if not path.exists():
            continue  # tracked deletion
        if not path.is_file():
            raise ValueError("Selected working-tree input is not a regular file")
        if path.stat().st_size > MAX_BYTES - total:
            raise ValueError("Selected input byte limit exceeded")
        content = path.read_bytes()
        total += len(content)
        if total > MAX_BYTES:
            raise ValueError("Selected input byte limit exceeded")
        files[name] = content
    return files


def freeze_worktree(repo: Path, roots: list[str], dest: Path) -> dict:
    first = _worktree_files(repo, roots)
    if first != _worktree_files(repo, roots):
        raise ValueError("Working tree changed during snapshot; rerun against a stable candidate")
    result = _write_files(first, dest)
    result['revision'] = 'WORKTREE'
    result['excluded'] = 'Ignored and unselected files are not part of this snapshot'
    return result
