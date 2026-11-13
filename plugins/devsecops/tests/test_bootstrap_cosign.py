"""Provisioning controls with synthetic assets; real binary proof is separate."""
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
from urllib.request import Request

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap_cosign", ROOT/"scripts/bootstrap_cosign.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


@pytest.fixture
def provision(tmp_path, monkeypatch):
    fake_home = tmp_path/"home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    binary = b"synthetic-tool-bytes"
    checksum = hashlib.sha256(binary).hexdigest()
    checksums = (checksum+"  cosign-linux-amd64\n").encode()
    lock = {"schema_version": 1, "version": "3.1.4", "release": "https://github.com/sigstore/cosign/releases/tag/v3.1.4",
            "checksums": {"url": bootstrap.RELEASE+"cosign_checksums.txt", "sha256": hashlib.sha256(checksums).hexdigest()},
            "tools": {"linux-amd64": {"asset": "cosign-linux-amd64", "sha256": checksum, "bytes": len(binary)}}}
    (tmp_path/"cosign.lock.json").write_text(json.dumps(lock))
    calls = []
    def download(url, expected, limit):
        calls.append(url)
        raw = checksums if url.endswith("checksums.txt") else binary
        assert hashlib.sha256(raw).hexdigest() == expected and len(raw) <= limit
        return raw
    monkeypatch.setattr(bootstrap, "download", download)
    return fake_home/".cache/devsecops/test-cosign", lock, calls


def test_checksum_bound_install_and_offline_reuse(provision):
    cache, lock, calls = provision
    assert bootstrap.main(["--cache", str(cache)]) == 0
    assert len(calls) == 2
    assert bootstrap.main(["--cache", str(cache), "--offline"]) == 0 and len(calls) == 2
    assert stat.S_IMODE((cache/"bin/cosign").stat().st_mode) == 0o700
    assert stat.S_IMODE((cache/"provisioning.json").stat().st_mode) == 0o600
    receipt = json.loads((cache/"provisioning.json").read_text())
    assert receipt["binary_sha256"] == lock["tools"]["linux-amd64"]["sha256"]
    assert receipt["release_signature_verification"] == "not-performed"


def test_offline_changed_binary_does_not_download_or_overwrite(provision):
    cache, _, calls = provision
    assert bootstrap.main(["--cache", str(cache)]) == 0
    (cache/"bin/cosign").write_bytes(b"changed")
    assert bootstrap.main(["--cache", str(cache), "--offline"]) == 2
    assert len(calls) == 2 and (cache/"bin/cosign").read_bytes() == b"changed"


@pytest.mark.parametrize("target", ["outside", "root", "symlink"])
def test_cache_cannot_escape_dedicated_root(provision, tmp_path, target):
    cache, _, calls = provision
    if target == "outside":
        cache = tmp_path/"outside"
    elif target == "root":
        cache = cache.parent
    else:
        cache.parent.mkdir(parents=True)
        cache.symlink_to(tmp_path, target_is_directory=True)
    assert bootstrap.main(["--cache", str(cache)]) == 2 and calls == []


@pytest.mark.parametrize("destination", ["http://github.com/file", "https://evil.example/file",
    "https://github.com@evil.example/file", "https://user:PRIVATE@github.com/file", "https://github.com:444/file"])
def test_redirect_allowlist_prevents_origin_expansion(destination):
    with pytest.raises(ValueError):
        bootstrap._Redirects().redirect_request(Request(bootstrap.RELEASE+"cosign-linux-amd64"), None, 302,
                                                "Found", {}, destination)


def test_untrusted_lock_failure_is_sanitized(provision, tmp_path, capsys):
    cache, lock, _ = provision
    lock["version"] = "PRIVATE-UNSUPPORTED-LOCK"
    (tmp_path/"cosign.lock.json").write_text(json.dumps(lock))
    assert bootstrap.main(["--cache", str(cache)]) == 2
    output = capsys.readouterr()
    assert "PRIVATE" not in output.err and "Cosign provisioning failed" in output.err
