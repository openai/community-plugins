"""Scanner mechanics on synthetic inputs. No production vulnerability DB claim."""

from datetime import datetime, timedelta, timezone
import hashlib
import gzip
import io
import os
from pathlib import Path
import sqlite3
import tarfile
import time

import pytest

from devsecops.adapters import artifacts as a


@pytest.fixture
def tools():
    paths = {
        name: Path(
            os.environ.get(
                "DEVSECOPS_" + name.upper(), Path.home() / ".cache/devsecops/supplychain-v1/bin" / name
            )
        )
        for name in ("syft", "grype")
    }
    if not all(p.is_file() for p in paths.values()):
        pytest.skip("Provision exact tools with scripts/bootstrap_supplychain.py")
    return paths


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    dist = root / "example_controlled-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: example-controlled\nVersion: 1.0\n")
    return root


def tree_hash(source, tmp_path):
    return a.freeze_directory(source, tmp_path / "frozen")["sha256"]


def database(tmp_path, age_hours=0):
    """Empty, deliberately synthetic schema. It contains no real CVE intelligence."""
    with sqlite3.connect(tmp_path / "vulnerability.db") as db:
        db.execute(
            "CREATE TABLE db_metadata (build_timestamp datetime NOT NULL, model integer NOT NULL, revision integer NOT NULL, addition integer NOT NULL)"
        )
        db.execute(
            "INSERT INTO db_metadata VALUES (?,?,?,?)",
            ((datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat(), 6, 1, 10),
        )
    path = tmp_path / "db.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        archive.add(tmp_path / "vulnerability.db", arcname="vulnerability.db")
    return {"database_snapshot": path, "database_sha256": a._hash(path.read_bytes())}


def codes(receipt):
    return {gap["code"] for gap in receipt["gaps"]}


def test_actual_syft_and_grype_empty_controlled_db(source, tmp_path, tools, monkeypatch):
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "AWS_SECRET_ACCESS_KEY",
        "SYFT_CONFIG",
        "GRYPE_CONFIG",
        "DOCKER_CONFIG",
    ):
        monkeypatch.setenv(name, "sensitive-canary-DO-NOT-EXPORT")
    result = a.scan_local_artifact(
        source=source,
        source_kind="directory",
        expected_sha256=tree_hash(source, tmp_path),
        output=tmp_path / "output",
        **tools,
        **database(tmp_path),
    )
    assert result["decision"] == "unknown" and result["execution_status"] == "partial"
    obs = result["observations"]
    assert obs["sbom"]["decision"] == "pass" and obs["sbom"]["packages"] >= 2
    assert obs["vulnerabilities"]["decision"] == "pass" and obs["vulnerabilities"]["matches"] == 0
    assert obs["database"]["schema_version"] == "v6.1.10"
    assert obs["database"]["decision"] == "pass"
    assert "directory-not-whole-image-evidence" in codes(result)
    assert "database-origin-unverified" in codes(result)
    for path in (tmp_path / "output").iterdir():
        assert "sensitive-canary-DO-NOT-EXPORT" not in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600
    for receipt in obs["artifacts"].values():
        raw = (tmp_path / "output" / receipt["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == receipt["sha256"] and len(raw) == receipt["bytes"]


def test_missing_db_preserves_actual_sbom(source, tmp_path, tools):
    result = a.scan_local_artifact(
        source=source,
        source_kind="directory",
        expected_sha256=tree_hash(source, tmp_path),
        output=tmp_path / "output",
        **tools,
    )
    assert result["observations"]["sbom"]["decision"] == "pass"
    assert "vulnerability-database-unavailable" in codes(result)
    assert not (tmp_path / "output" / "grype.json").exists()


@pytest.mark.parametrize("age_hours", [121, -24])
def test_actual_db_freshness(source, tmp_path, tools, age_hours):
    result = a.scan_local_artifact(
        source=source,
        source_kind="directory",
        expected_sha256=tree_hash(source, tmp_path),
        output=tmp_path / "output",
        **tools,
        **database(tmp_path, age_hours),
    )
    assert result["observations"]["vulnerabilities"]["decision"] == "unknown"
    assert "database-stale-or-future" in codes(result)


def test_wrong_source_hash_prevents_scan(source, tmp_path, tools):
    result = a.scan_local_artifact(
        source=source, source_kind="directory", expected_sha256="0" * 64, output=tmp_path / "output", **tools
    )
    assert "source-digest-mismatch" in codes(result)
    assert not (tmp_path / "output" / "sbom.spdx.json").exists()


@pytest.mark.parametrize("name", ["syft", "grype"])
def test_changed_executable_is_never_executed(source, tmp_path, tools, name):
    marker = tmp_path / "marker"
    binary = tmp_path / "fake"
    binary.write_text("#!/bin/sh\ntouch " + str(marker))
    binary.chmod(0o700)
    tools[name] = binary
    result = a.scan_local_artifact(
        source=source, source_kind="directory", expected_sha256="0" * 64, output=tmp_path / "output", **tools
    )
    assert "scanner-binary-mismatch" in codes(result) and not marker.exists()


def test_tree_hash_is_location_independent_and_changes_with_bytes(tmp_path):
    first = tmp_path / "one"
    first.mkdir()
    (first / "empty").touch()
    (first / "dir").mkdir()
    (first / "dir" / "x").write_text("a")
    receipt = a.freeze_directory(first, tmp_path / "two")
    assert a.freeze_directory(tmp_path / "two", tmp_path / "three")["sha256"] == receipt["sha256"]
    (first / "dir" / "x").write_text("b")
    assert a.freeze_directory(first, tmp_path / "four")["sha256"] != receipt["sha256"]


@pytest.mark.parametrize("kind", ["file-link", "directory-link", "fifo", "ancestor"])
def test_tree_rejects_unsafe_paths(source, tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x").write_text("private")
    if kind == "file-link":
        (source / "link").symlink_to(outside / "x")
    elif kind == "directory-link":
        (source / "link").symlink_to(outside, target_is_directory=True)
    elif kind == "fifo":
        os.mkfifo(source / "fifo")
    else:
        link = tmp_path / "alias"
        link.symlink_to(source, target_is_directory=True)
        source = link
    with pytest.raises((ValueError, OSError)):
        a.freeze_directory(source, tmp_path / "frozen")


def test_source_mutation_is_rejected(source, tmp_path, monkeypatch):
    original = a._snapshot

    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        (source / "new-file").write_text("changed")
        return result

    monkeypatch.setattr(a, "_snapshot", changing)
    with pytest.raises(ValueError, match="unavailable, changed or unsupported"):
        a.freeze_directory(source, tmp_path / "frozen")


@pytest.mark.parametrize("member", ["../escape", "/escape", "dir/vulnerability.db", "other.db"])
def test_database_archive_has_exact_layout(tmp_path, member):
    p = tmp_path / "archive.tar.gz"
    with tarfile.open(p, "w:gz") as archive:
        entry = tarfile.TarInfo(member)
        raw = b"SQLite format 3\0"
        entry.size = len(raw)
        archive.addfile(entry, io.BytesIO(raw))
    with pytest.raises(ValueError):
        a._database_archive(p)


def oci_archive(
    tmp_path,
    *,
    architecture="amd64",
    env="SECRET_CONFIG_CANARY",
    tamper=False,
    compressed=False,
    wrong_diff=False,
    padding=0,
    os_release=None,
    labels=None,
    package_name='example-controlled',
    package_version='1.0',
):
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode="w") as archive:
        raw = f"Metadata-Version: 2.1\nName: {package_name}\nVersion: {package_version}\n".encode()
        item = tarfile.TarInfo(f"site-packages/{package_name.replace('-', '_')}-{package_version}.dist-info/METADATA")
        item.size = len(raw)
        archive.addfile(item, io.BytesIO(raw))
        if os_release is not None:
            item = tarfile.TarInfo('etc/os-release')
            item.size = len(os_release)
            archive.addfile(item, io.BytesIO(os_release))
    unpacked = layer.getvalue() + b"\0" * padding
    layer_bytes = gzip.compress(unpacked) if compressed else unpacked
    config = a._canonical(
        {
            "os": "linux",
            "architecture": architecture,
            "config": {"Env": ["TOKEN=" + env], **({'Labels': labels} if labels is not None else {})},
            "rootfs": {
                "type": "layers",
                "diff_ids": ["sha256:" + ("0" * 64 if wrong_diff else a._hash(unpacked))],
            },
        }
    )

    def descriptor(raw, kind):
        return {
            "mediaType": "application/vnd.oci.image." + kind,
            "digest": "sha256:" + a._hash(raw),
            "size": len(raw),
        }

    manifest = a._canonical(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": descriptor(config, "config.v1+json"),
            "layers": [descriptor(layer_bytes, "layer.v1.tar" + ("+gzip" if compressed else ""))],
        }
    )
    index = a._canonical({"schemaVersion": 2, "manifests": [descriptor(manifest, "manifest.v1+json")]})
    p = tmp_path / "image.tar"
    with tarfile.open(p, "w") as archive:
        for name, raw in [
            ("oci-layout", b'{"imageLayoutVersion":"1.0.0"}'),
            ("index.json", index),
            *[("blobs/sha256/" + a._hash(raw), raw) for raw in (config, layer_bytes, manifest)],
        ]:
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw if not tamper or raw != config else b"x" * len(raw)))
    return p, "sha256:" + a._hash(manifest)


def test_actual_oci_sbom_preserves_manifest_identity_not_config_or_archive(tmp_path, tools):
    source, digest = oci_archive(tmp_path)
    result = a.scan_local_artifact(
        source=source,
        source_kind="oci-archive",
        expected_sha256=a._hash(source.read_bytes()),
        expected_image_digest=digest,
        architecture="amd64",
        output=tmp_path / "output",
        **tools,
    )
    obs = result["observations"]
    assert obs["sbom"]["decision"] == "pass"
    assert obs["image"]["manifest_digest"] == digest
    assert obs["image"]["config_digest"] != digest != "sha256:" + obs["source"]["sha256"]
    assert "directory-not-whole-image-evidence" not in codes(result)
    assert "SECRET_CONFIG_CANARY" not in "".join(p.read_text() for p in (tmp_path / "output").iterdir())


def test_compressed_layer_diffid_uses_uncompressed_tar_bytes(tmp_path, tools):
    source, digest = oci_archive(tmp_path, compressed=True)
    result = a.scan_local_artifact(
        source=source,
        source_kind="oci-archive",
        expected_sha256=a._hash(source.read_bytes()),
        expected_image_digest=digest,
        architecture="amd64",
        output=tmp_path / "output",
        **tools,
    )
    assert result["observations"]["sbom"]["decision"] == "pass"


def test_valid_blob_hash_cannot_hide_wrong_uncompressed_layer_identity(tmp_path):
    source, digest = oci_archive(tmp_path, compressed=True, wrong_diff=True)
    with pytest.raises(a._Invalid) as error:
        a._oci_archive(source, digest, "amd64")
    assert error.value.code == "oci-rootfs-diffid-mismatch"


def test_small_compressed_blob_cannot_exceed_decoded_bound(tmp_path, monkeypatch):
    source, digest = oci_archive(tmp_path, compressed=True, padding=100_000)
    monkeypatch.setattr(a, "MAX_SOURCE", 50_000)
    assert source.stat().st_size < a.MAX_SOURCE
    with pytest.raises(a._Invalid) as error:
        a._oci_archive(source, digest, "amd64")
    assert error.value.code == "oci-layer-decompression-limit"


@pytest.mark.parametrize("defect", ["digest", "architecture", "blob"])
def test_oci_rejects_wrong_subject_or_bytes(tmp_path, defect):
    source, digest = oci_archive(tmp_path, tamper=defect == "blob")
    with pytest.raises(ValueError):
        a._oci_archive(
            source,
            "sha256:" + "0" * 64 if defect == "digest" else digest,
            "arm64" if defect == "architecture" else "amd64",
        )


def test_output_existing_or_symlink_rejected_before_tools(source, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("tool must not execute")

    monkeypatch.setattr(a, "run", forbidden)
    for output in (source, tmp_path / "link"):
        if output.name == "link":
            output.symlink_to(source, target_is_directory=True)
        with pytest.raises((OSError, ValueError)):
            a.scan_local_artifact(
                source=source,
                source_kind="directory",
                expected_sha256="0" * 64,
                output=output,
                syft=Path("/unused"),
                grype=Path("/unused"),
            )


@pytest.mark.parametrize("replace", [True, False])
def test_terminal_directory_swap_withdraws_receipt(source, tmp_path, monkeypatch, replace):
    original = a._write
    output, moved = tmp_path / "output", tmp_path / "moved"

    def swap(directory, name, raw):
        result = original(directory, name, raw)
        if name == "receipt.json":
            output.rename(moved)
            if replace:
                output.mkdir()
        return result

    monkeypatch.setattr(a, "_write", swap)
    with pytest.raises(ValueError, match="identity changed"):
        a.scan_local_artifact(
            source=source,
            source_kind="directory",
            expected_sha256="0" * 64,
            output=output,
            syft=Path("/missing"),
            grype=Path("/missing"),
        )
    assert not (moved / "receipt.json").exists()


def test_db_age_is_rechecked_after_actual_scan(source, tmp_path, tools, monkeypatch):
    class Clock:
        delta = timedelta(0)

        @classmethod
        def now(cls, tz):
            return datetime.now(tz) + cls.delta

        fromisoformat = datetime.fromisoformat

    original = a.run

    def delayed(*args, **kwargs):
        result = original(*args, **kwargs)
        if "sbom:sbom.spdx.json" in args[0]:
            Clock.delta = timedelta(hours=121)
        return result

    monkeypatch.setattr(a, "datetime", Clock)
    monkeypatch.setattr(a, "run", delayed)
    result = a.scan_local_artifact(
        source=source,
        source_kind="directory",
        expected_sha256=tree_hash(source, tmp_path),
        output=tmp_path / "output",
        **tools,
        **database(tmp_path),
    )
    assert result["observations"]["database"]["decision"] == "unknown"
    assert "database-stale-or-future" in codes(result)
    assert result["decision"] == "unknown"


def test_repeated_oci_directory_members_count_toward_limit(tmp_path):
    path = tmp_path / "bomb.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for _ in range(1025):
            member = tarfile.TarInfo("blobs/")
            member.type = tarfile.DIRTYPE
            archive.addfile(member)
    with pytest.raises(a._Invalid) as error:
        a._oci_archive(path, "sha256:" + "0" * 64, "amd64")
    assert error.value.code == "oci-member-limit"


@pytest.mark.parametrize("operation", ["tree", "oci", "db", "copy"])
def test_input_loops_honor_the_shared_deadline(source, tmp_path, operation):
    deadline = time.monotonic() - 1
    with pytest.raises(a._Invalid) as error:
        if operation == "tree":
            a.freeze_directory(source, tmp_path / "frozen", deadline=deadline)
        elif operation == "oci":
            path, digest = oci_archive(tmp_path)
            a._oci_archive(path, digest, "amd64", deadline=deadline)
        elif operation == "db":
            a._database_archive(database(tmp_path)["database_snapshot"], deadline=deadline)
        else:
            a._snapshot(
                source / "example_controlled-1.0.dist-info" / "METADATA",
                tmp_path / "copied",
                1000,
                deadline=deadline,
            )
    assert error.value.code in {"scan-deadline-exceeded", "input-deadline-exceeded"}


@pytest.mark.parametrize("operation", ["oci", "db"])
@pytest.mark.parametrize(
    "kind", [tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK]
)
def test_hidden_oversized_tar_metadata_rejected_before_payload_read(tmp_path, operation, kind):
    header = tarfile.TarInfo("metadata")
    header.type = kind
    header.size = 64_001
    path = tmp_path / "metadata.tar.gz"
    path.write_bytes(gzip.compress(header.tobuf()))  # Deliberately omit the claimed metadata body.
    with pytest.raises(a._Invalid) as error:
        if operation == "oci":
            a._oci_archive(path, "sha256:" + "0" * 64, "amd64")
        else:
            a._database_archive(path)
    assert error.value.code == "archive-metadata-limit"


def test_nested_metadata_headers_cannot_overflow_tar_parser_recursion(tmp_path):
    header = tarfile.TarInfo("metadata")
    header.type = tarfile.XHDTYPE
    header.size = 0
    path = tmp_path / "recursive.tar.gz"
    path.write_bytes(gzip.compress(header.tobuf() * 20))
    with pytest.raises(a._Invalid) as error:
        a._oci_archive(path, "sha256:" + "0" * 64, "amd64")
    assert error.value.code == "archive-metadata-limit"


def test_sparse_tar_headers_are_not_silently_admitted(tmp_path):
    header = tarfile.TarInfo("vulnerability.db")
    header.type = tarfile.GNUTYPE_SPARSE
    path = tmp_path / "sparse.tar.gz"
    path.write_bytes(gzip.compress(header.tobuf()))
    with pytest.raises(a._Invalid) as error:
        a._database_archive(path)
    assert error.value.code == "archive-sparse-unsupported"


def test_layer_parser_has_the_same_hidden_metadata_bound():
    header = tarfile.TarInfo("metadata")
    header.type = tarfile.XHDTYPE
    header.size = 64_001
    with pytest.raises(a._Invalid) as error:
        tarfile.open(fileobj=io.BytesIO(header.tobuf()), mode="r|", tarinfo=a._BoundedTarInfo)
    assert error.value.code == "archive-metadata-limit"


@pytest.mark.skipif(
    os.environ.get("DEVSECOPS_TEST_NETWORK_SANDBOX") != "1",
    reason="Explicit host macOS network-denial qualification",
)
@pytest.mark.parametrize("kind", ["directory", "oci-archive"])
def test_real_scanners_with_explicit_database_under_os_network_denial(
    source, tmp_path, tools, monkeypatch, kind
):
    original = a.run

    def no_network(argv, **kwargs):
        return original(
            ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)", *argv], **kwargs
        )

    monkeypatch.setattr(a, "run", no_network)
    if kind == "directory":
        subject = dict(source=source, expected_sha256=tree_hash(source, tmp_path))
    else:
        image, digest = oci_archive(tmp_path)
        subject = dict(
            source=image,
            expected_sha256=a._hash(image.read_bytes()),
            expected_image_digest=digest,
            architecture="amd64",
        )
    result = a.scan_local_artifact(
        **subject, source_kind=kind, output=tmp_path / "output", **tools, **database(tmp_path)
    )
    assert result["observations"]["sbom"]["decision"] == "pass"
    assert result["observations"]["vulnerabilities"]["decision"] == "pass"
    assert result["decision"] == "unknown"  # Synthetic empty DB is never canonical security coverage.
    assert all(
        receipt["tool"] == "sandbox-exec" and receipt["exit_code"] == 0
        for receipt in result["observations"]["receipts"]
    )
