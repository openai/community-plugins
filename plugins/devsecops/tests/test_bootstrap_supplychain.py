import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

spec = importlib.util.spec_from_file_location(
    "bootstrap_supplychain", Path(__file__).resolve().parents[1] / "scripts/bootstrap_supplychain.py"
)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def archive(name="syft", kind=tarfile.REGTYPE, duplicate=False):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as a:
        for _ in range(2 if duplicate else 1):
            m = tarfile.TarInfo(name)
            m.type = kind
            m.size = 3
            m.linkname = "/outside"
            a.addfile(m, io.BytesIO(b"abc"))
    return out.getvalue()


def test_exact_binary_extraction():
    assert (
        b.binary_from_archive(archive(), "syft", {"binary_sha256": b.digest(b"abc"), "binary_bytes": 3})
        == b"abc"
    )


@pytest.mark.parametrize(
    "name,kind,duplicate",
    [
        ("../syft", tarfile.REGTYPE, False),
        ("/syft", tarfile.REGTYPE, False),
        ("syft", tarfile.SYMTYPE, False),
        ("syft", tarfile.LNKTYPE, False),
        ("syft", tarfile.REGTYPE, True),
        ("other", tarfile.REGTYPE, False),
    ],
)
def test_unsafe_archive_rejected(name, kind, duplicate):
    with pytest.raises(ValueError):
        b.binary_from_archive(
            archive(name, kind, duplicate), "syft", {"binary_sha256": b.digest(b"abc"), "binary_bytes": 3}
        )


def test_wrong_binary_hash():
    with pytest.raises(ValueError):
        b.binary_from_archive(archive(), "syft", {"binary_sha256": "0" * 64, "binary_bytes": 3})


def test_private_cache_only(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(ValueError):
        b.cache_path(tmp_path / "bin")
    cache = tmp_path / ".cache/devsecops/tools"
    cache.mkdir(parents=True)
    assert b.cache_path(cache) == cache
    link = cache / "alias"
    link.symlink_to(tmp_path)
    with pytest.raises(ValueError):
        b.cache_path(link / "escape")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/asset",
        "https://github.com.evil/asset",
        "https://user:pass@github.com/asset",
        "https://release-assets.githubusercontent.com:444/asset",
    ],
)
def test_redirect_scope_rejected(url):
    with pytest.raises(ValueError):
        b._Redirects().redirect_request(None, None, 302, "redirect", {}, url)


def builder_archive(extra=None):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        directory = tarfile.TarInfo("bin/")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        for name in ("buildctl", "buildkitd", "buildkit-runc"):
            entry = tarfile.TarInfo("bin/" + name)
            entry.size = 3
            archive.addfile(entry, io.BytesIO(b"abc"))
        if extra:
            entry = tarfile.TarInfo(extra[0])
            entry.type = extra[1]
            entry.linkname = "/outside"
            entry.size = 0
            archive.addfile(entry)
    return out.getvalue()


def test_builder_exact_binary_set():
    expected = {
        name: {"sha256": b.digest(b"abc"), "bytes": 3} for name in ("buildctl", "buildkitd", "buildkit-runc")
    }
    assert b.builder_binaries(builder_archive(), expected) == {
        "buildctl": b"abc",
        "buildkitd": b"abc",
        "buildkit-runc": b"abc",
    }


@pytest.mark.parametrize(
    "extra",
    [
        ("bin/", tarfile.DIRTYPE),
        ("bin/buildctl", tarfile.REGTYPE),
        ("bin/../escape", tarfile.REGTYPE),
        ("/bin/escape", tarfile.REGTYPE),
        ("bin/link", tarfile.SYMTYPE),
        ("bin/hard", tarfile.LNKTYPE),
    ],
)
def test_builder_archive_rejects_duplicates_escape_and_links(extra):
    expected = {
        name: {"sha256": b.digest(b"abc"), "bytes": 3} for name in ("buildctl", "buildkitd", "buildkit-runc")
    }
    with pytest.raises(ValueError):
        b.builder_binaries(builder_archive(extra), expected)


def test_builder_cannot_ignore_wrong_or_missing_binary():
    expected = {name: {"sha256": "0" * 64, "bytes": 3} for name in ("buildctl", "buildkitd", "buildkit-runc")}
    with pytest.raises(ValueError):
        b.builder_binaries(builder_archive(), expected)
    with pytest.raises(ValueError):
        b.builder_binaries(archive("bin/buildctl"), expected)


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_real_official_builder_archive_matches_each_locked_executable(architecture):
    item = json.loads((b.ROOT / "supplychain.lock.json").read_text())["builder"]["platforms"][
        "linux-" + architecture
    ]
    path = Path.home() / (".cache/devsecops/supplychain/buildkit-v0.34.1.linux-" + architecture + ".tar.gz")
    if not path.is_file():
        pytest.skip("Official BuildKit archive has not been staged for maintainer qualification")
    raw = path.read_bytes()
    assert b.digest(raw) == item["archive_sha256"] and len(raw) == item["archive_bytes"]
    binaries = b.builder_binaries(raw, item["binaries"])
    assert set(binaries) == {"buildctl", "buildkitd", "buildkit-runc"}


@pytest.mark.parametrize("architecture,upstream", [("amd64", "x86_64"), ("arm64", "aarch64")])
def test_real_official_rootlesskit_archive_matches_published_checksum_and_binary(architecture, upstream):
    lock = json.loads((b.ROOT / "supplychain.lock.json").read_text())["rootlesskit"]
    item = lock["platforms"]["linux-" + architecture]
    cache = Path.home() / ".cache/devsecops/supplychain/rootlesskit-v3.3.0"
    path = cache / ("rootlesskit-" + upstream + ".tar.gz")
    if not path.is_file():
        pytest.skip("Official RootlessKit archive has not been staged for maintainer qualification")
    raw = path.read_bytes()
    checksums = (cache / "SHA256SUMS").read_bytes()
    assert b.digest(checksums) == lock["checksums"]["sha256"]
    published = {line.split()[1]: line.split()[0] for line in checksums.decode().splitlines()}
    assert b.digest(raw) == published[path.name] == item["archive_sha256"]
    assert len(raw) == item["archive_bytes"]
    executable = b.binary_from_archive(raw, "rootlesskit", item)
    assert executable.startswith(b"\x7fELF")


def test_cross_platform_offline_staging_does_not_replace_host_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(b.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(b.platform, "machine", lambda: "arm64")
    host = tmp_path / ".cache/devsecops/supplychain-v1/bin"
    host.mkdir(parents=True)
    (host / "syft").write_bytes(b"host-tool-canary")
    assert b.main(["--platform", "linux-amd64", "--builder", "--offline"]) == 2
    assert (host / "syft").read_bytes() == b"host-tool-canary"
    assert not (tmp_path / ".cache/devsecops/supplychain-v1/provisioning.json").exists()
    assert "provisioning failed" in capsys.readouterr().err
