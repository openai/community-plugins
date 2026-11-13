#!/usr/bin/env python3
"""Install exact Syft/Grype release bytes beneath ~/.cache/devsecops only."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = {"syft": "1.55.0", "grype": "0.121.0"}
MAX_ARCHIVE, MAX_BINARY = 50_000_000, 200_000_000


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def cache_path(path):
    path = Path(os.path.abspath(path))
    root = Path.home()/".cache/devsecops"
    if path == root or not path.is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Supply-chain cache must be below the dedicated private cache without symlinks")
    return path


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        p = urlsplit(newurl)
        if p.scheme != "https" or p.hostname not in {"github.com", "release-assets.githubusercontent.com"} or p.username or p.password or p.port not in (None, 443):
            raise ValueError("Release redirect is outside the approved origin set")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download(url, checksum, limit, prefix):
    if not url.startswith(prefix) or "/" in url[len(prefix):] or not url[len(prefix):]:
        raise ValueError("Only an exact pinned release asset may be downloaded")
    request = urllib.request.Request(url, headers={"User-Agent": "devsecops-supplychain-bootstrap/1"})
    with urllib.request.build_opener(_Redirects()).open(request, timeout=45) as response:
        raw = response.read(limit+1)
    if len(raw) > limit or digest(raw) != checksum:
        raise ValueError("Release size or hash mismatch")
    return raw


def binary_from_archive(raw, name, expected):
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        selected, seen, total = None, set(), 0
        for member in archive:
            total += member.size
            if (len(seen) >= 32 or member.name in seen or not member.isfile()
                    or len(Path(member.name).parts) != 1 or member.name in {".", ".."}
                    or "/" in member.name or "\\" in member.name or total > MAX_BINARY+1_000_000
                    or not 0 <= member.size <= MAX_BINARY):
                raise ValueError("Unsupported release archive member")
            seen.add(member.name)
            if member.name == name:
                selected = archive.extractfile(member).read(MAX_BINARY+1)
    if selected is None or len(selected) != expected["binary_bytes"] or digest(selected) != expected["binary_sha256"]:
        raise ValueError("Pinned executable mismatch")
    return selected


def builder_binaries(raw, expected):
    selected, seen, total = {}, set(), 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            if len(seen) >= 64 or member.name in seen:
                raise ValueError("Unsupported builder archive member")
            seen.add(member.name)
            if member.isdir() and member.name.rstrip("/") == "bin":
                continue
            total += member.size
            parts = member.name.split("/")
            if (not member.isfile() or len(parts) != 2 or parts[0] != "bin"
                    or parts[1] in {"", ".", ".."} or "\\" in member.name or total > 1_000_000_000
                    or not 0 <= member.size <= MAX_BINARY):
                raise ValueError("Unsupported builder archive member")
            if parts[1] in {"buildctl", "buildkitd", "buildkit-runc"}:
                data = archive.extractfile(member).read(MAX_BINARY+1)
                if {"sha256": digest(data), "bytes": len(data)} != expected.get(parts[1]):
                    raise ValueError("Pinned builder executable mismatch")
                selected[parts[1]] = data
    if set(selected) != {"buildctl", "buildkitd", "buildkit-runc"}:
        raise ValueError("Builder executable missing")
    return selected


def atomic(path, raw, executable=False):
    cache_path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".supplychain-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o700 if executable else 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--platform", choices=["darwin-amd64", "darwin-arm64", "linux-amd64", "linux-arm64"],
                        help="Stage another platform's bytes for transfer; executables are never run")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--builder", action="store_true", help="Also stage pinned Linux BuildKit, its runc and RootlessKit; never start a daemon")
    args = parser.parse_args(argv)
    try:
        raw_lock = (ROOT/"supplychain.lock.json").read_bytes()
        lock = json.loads(raw_lock)
        if lock["schema_version"] != 1 or set(lock["tools"]) != set(VERSIONS):
            raise ValueError("Unsupported supply-chain lock")
        arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine(), platform.machine())
        host = platform.system().lower()+"-"+arch
        target = args.platform or host
        default_cache = Path.home()/".cache/devsecops"/("supplychain-v1" if target == host else "supplychain-v1-"+target)
        cache = cache_path(args.cache or default_cache)
        receipts = {}
        for name, version in VERSIONS.items():
            tool = lock["tools"][name]
            if tool["version"] != version:
                raise ValueError("Unsupported release version")
            item, executable = tool["platforms"][target], cache_path(cache/"bin"/name)
            raw = executable.read_bytes() if executable.is_file() and executable.stat().st_size <= MAX_BINARY else b""
            if digest(raw) != item["binary_sha256"] or len(raw) != item["binary_bytes"]:
                if args.offline:
                    raise ValueError("Staged executable is missing or changed")
                prefix = f"https://github.com/anchore/{name}/releases/download/v{version}/"
                checksums = download(tool["checksums"]["url"], tool["checksums"]["sha256"], 100_000, prefix)
                published = {}
                for line in checksums.decode("ascii").splitlines():
                    checksum, filename = line.split()
                    if filename in published:
                        raise ValueError("Duplicate release checksum")
                    published[filename] = checksum
                if published.get(item["url"].split("/")[-1]) != item["archive_sha256"] or not 0 < item["archive_bytes"] <= MAX_ARCHIVE:
                    raise ValueError("Lock differs from published release checksums")
                archive = download(item["url"], item["archive_sha256"], item["archive_bytes"], prefix)
                if len(archive) != item["archive_bytes"]:
                    raise ValueError("Archive length mismatch")
                raw = binary_from_archive(archive, name, item)
                atomic(executable, raw, True)
            receipts[name] = {"version": version, "platform": target, "binary_sha256": digest(raw),
                "binary_bytes": len(raw), "archive_sha256": item["archive_sha256"], "release_signature_verification": "not-performed"}
        if args.builder:
            builder = lock.get("builder", {})
            if builder.get("version") != "0.34.1" or target not in {"linux-amd64", "linux-arm64"}:
                raise ValueError("Pinned builder staging requires Linux")
            item = builder["platforms"][target]
            staged = {}
            for name, expected in item["binaries"].items():
                if name not in {"buildctl", "buildkitd", "buildkit-runc"}:
                    raise ValueError("Unsupported builder executable")
                path = cache_path(cache/"bin"/name)
                body = path.read_bytes() if path.is_file() and path.stat().st_size <= MAX_BINARY else b""
                if {"sha256": digest(body), "bytes": len(body)} == expected:
                    staged[name] = body
            if set(staged) != {"buildctl", "buildkitd", "buildkit-runc"}:
                if args.offline or not 0 < item["archive_bytes"] <= 150_000_000:
                    raise ValueError("Pinned builder is unavailable")
                raw = download(item["url"], item["archive_sha256"], item["archive_bytes"],
                    "https://github.com/moby/buildkit/releases/download/v0.34.1/")
                if len(raw) != item["archive_bytes"]:
                    raise ValueError("Builder archive length mismatch")
                staged = builder_binaries(raw, item["binaries"])
                for name, body in staged.items():
                    atomic(cache/"bin"/name, body, True)
            receipts["builder"] = {"version": "0.34.1", "platform": target, "binaries": item["binaries"],
                "archive_sha256": item["archive_sha256"], "checksum_source": "github-release-asset-digest",
                "release_signature_verification": "not-performed", "daemon_started": False,
                "rootless_runtime_qualified": False}
            rootless = lock.get("rootlesskit", {})
            if rootless.get("version") != "3.3.0":
                raise ValueError("Unsupported RootlessKit release")
            item = rootless["platforms"][target]
            executable = cache_path(cache/"bin/rootlesskit")
            raw = executable.read_bytes() if executable.is_file() and executable.stat().st_size <= MAX_BINARY else b""
            if digest(raw) != item["binary_sha256"] or len(raw) != item["binary_bytes"]:
                if args.offline:
                    raise ValueError("Pinned RootlessKit is unavailable")
                prefix = "https://github.com/rootless-containers/rootlesskit/releases/download/v3.3.0/"
                checksums = download(rootless["checksums"]["url"], rootless["checksums"]["sha256"], 100_000, prefix)
                published = {}
                for line in checksums.decode("ascii").splitlines():
                    checksum, filename = line.split()
                    if filename in published:
                        raise ValueError("Duplicate RootlessKit release checksum")
                    published[filename] = checksum
                if published.get(item["url"].split("/")[-1]) != item["archive_sha256"] or not 0 < item["archive_bytes"] <= MAX_ARCHIVE:
                    raise ValueError("RootlessKit lock differs from published checksums")
                archive = download(item["url"], item["archive_sha256"], item["archive_bytes"], prefix)
                if len(archive) != item["archive_bytes"]:
                    raise ValueError("RootlessKit archive length mismatch")
                raw = binary_from_archive(archive, "rootlesskit", item)
                atomic(executable, raw, True)
            receipts["rootlesskit"] = {"version": "3.3.0", "platform": target,
                "binary_sha256": digest(raw), "binary_bytes": len(raw), "archive_sha256": item["archive_sha256"],
                "release_signature_verification": "not-performed", "daemon_started": False}
        atomic(cache/"provisioning.json", (json.dumps({"schema_version": 1, "lock_sha256": digest(raw_lock), "tools": receipts}, indent=2)+"\n").encode())
        print(json.dumps({"directory": str(cache), "platform": target, "tools": list(receipts), "executables_run": False}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError):
        print(json.dumps({"status": "error", "message": "Supply-chain tool provisioning failed; verify explicit lock, cache and release availability"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
