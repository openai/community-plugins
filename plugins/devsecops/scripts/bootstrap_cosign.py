#!/usr/bin/env python3
"""Provision a checksum-pinned Cosign under ~/.cache/devsecops, never globally."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
VERSION = "3.1.4"
LIMIT = 160_000_000
RELEASE = "https://github.com/sigstore/cosign/releases/download/v" + VERSION + "/"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        parsed = urlsplit(newurl)
        if (parsed.scheme != "https" or parsed.hostname not in {"github.com", "release-assets.githubusercontent.com"}
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError("Cosign release redirect is outside the allowed origin set")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download(url, expected, limit):
    if not url.startswith(RELEASE) or not re.fullmatch(r"[A-Za-z0-9_.-]+", url[len(RELEASE):]):
        raise ValueError("Cosign download must be an explicit pinned release asset")
    request = urllib.request.Request(url, headers={"User-Agent": "devsecops-cosign-bootstrap/1"})
    opener = urllib.request.build_opener(_Redirects())
    with opener.open(request, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit or digest(data) != expected:
        raise ValueError("Cosign release size or checksum verification failed")
    return data


def cache_path(path):
    path = Path(os.path.abspath(path))
    root = Path.home() / ".cache/devsecops"
    if path == root or not path.is_relative_to(root):
        raise ValueError("Cosign cache must be below ~/.cache/devsecops")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Cosign cache paths must not contain symlinks")
    return path


def atomic(path, data, executable=False):
    cache_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".cosign-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o700 if executable else 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path.home()/".cache/devsecops/cosign-v3.1.4")
    parser.add_argument("--offline", action="store_true", help="Verify and reuse an already staged pinned executable")
    args = parser.parse_args(argv)
    try:
        raw_lock = (ROOT / "cosign.lock.json").read_bytes()
        lock = json.loads(raw_lock)
        if lock["schema_version"] != 1 or lock["version"] != VERSION:
            raise ValueError("Unsupported Cosign lock")
        arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine(), platform.machine())
        target = platform.system().lower() + "-" + arch
        item = lock["tools"][target]
        if item["asset"] != "cosign-" + target or not 0 < item["bytes"] <= LIMIT:
            raise ValueError("Unsupported Cosign release asset")
        cache = cache_path(args.cache)
        executable = cache_path(cache / "bin/cosign")
        content = (executable.read_bytes() if executable.is_file() and executable.stat().st_size <= LIMIT else b"")
        if digest(content) != item["sha256"] or len(content) != item["bytes"]:
            if args.offline:
                raise ValueError("Offline Cosign executable is missing or has changed")
            checksums = download(lock["checksums"]["url"], lock["checksums"]["sha256"], 1_000_000)
            published = {}
            for line in checksums.decode("ascii").splitlines():
                checksum, asset = line.split()
                if asset in published:
                    raise ValueError("Duplicate Cosign release checksum")
                published[asset] = checksum
            if published.get(item["asset"]) != item["sha256"]:
                raise ValueError("Cosign lock differs from the official release checksums")
            content = download(RELEASE + item["asset"], item["sha256"], item["bytes"])
            atomic(executable, content, executable=True)
        receipt = {"schema_version": 1, "version": VERSION, "platform": target,
                   "lock_sha256": digest(raw_lock), "release": lock["release"],
                   "checksums_sha256": lock["checksums"]["sha256"], "binary_sha256": digest(content),
                   "binary_bytes": len(content), "release_signature_verification": "not-performed"}
        atomic(cache / "provisioning.json", (json.dumps(receipt, indent=2) + "\n").encode())
        print(json.dumps({"cosign": str(executable), "provisioning": str(cache / "provisioning.json")}))
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "error", "message": "Cosign provisioning failed; verify the trusted lock, platform, cache and download availability"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
