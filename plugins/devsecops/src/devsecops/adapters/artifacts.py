"""Pinned local Syft/SPDX and Grype scanning with explicit frozen inputs.

This adapter does not pull images, discover credentials, update a database or
approve promotion. Directory evidence is never whole-image evidence.
"""
from __future__ import annotations
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import gzip
import json
import math
import os
from pathlib import Path
import platform
import re
import stat
import tarfile
import tempfile
import time

from devsecops.process import ToolError, run
from .http import APIError, _json
from .signatures import _Invalid, _snapshot
from .scanner_json import MAX_DOCUMENT_BYTES, parse_scanner_document

ROOT = Path(__file__).resolve().parents[3]
VERSIONS = {"syft": "1.55.0", "grype": "0.121.0"}
MAX_FILES, MAX_SOURCE, MAX_OUTPUT = 10_000, 512_000_000, MAX_DOCUMENT_BYTES
MAX_DATABASE = 1_000_000_000


class _BoundedTarInfo(tarfile.TarInfo):
    """Bound headers tarfile normally consumes before yielding a member.

    Sparse extensions require a separate admitted format; rejecting them avoids
    unbounded hidden sparse maps and ambiguous native-scanner expansion.
    """
    def _metadata(self, archive, processor):
        depth = getattr(archive, "_devsecops_metadata_depth", 0) + 1
        total = getattr(archive, "_devsecops_metadata_bytes", 0) + self.size
        if self.size < 0 or self.size > 64_000 or total > 8_000_000 or depth > 16:
            raise _Invalid("archive-metadata-limit")
        archive._devsecops_metadata_depth = depth
        archive._devsecops_metadata_bytes = total
        try:
            return processor(archive)
        finally:
            archive._devsecops_metadata_depth -= 1

    def _proc_pax(self, archive):
        return self._metadata(archive, super()._proc_pax)

    def _proc_gnulong(self, archive):
        return self._metadata(archive, super()._proc_gnulong)

    def _proc_sparse(self, *args):
        raise _Invalid("archive-sparse-unsupported")

    def _proc_gnusparse_00(self, *args):
        raise _Invalid("archive-sparse-unsupported")

    def _proc_gnusparse_01(self, *args):
        raise _Invalid("archive-sparse-unsupported")

    def _proc_gnusparse_10(self, *args):
        raise _Invalid("archive-sparse-unsupported")


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _open_directory(path):
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise _Invalid("path-unsupported")
    current = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        result, current = current, None
        return result
    finally:
        if current is not None:
            os.close(current)


def _checkpoint(deadline):
    if deadline is not None and time.monotonic() > deadline:
        raise _Invalid("scan-deadline-exceeded")


def _tree_entries(path, *, deadline=None):
    root = _open_directory(path)
    try:
        result, total = {}, 0
        def visit(directory, prefix="", depth=0):
            nonlocal total
            if depth > 32:
                raise _Invalid("source-depth-exceeded")
            names = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    _checkpoint(deadline)
                    names.append(entry.name)
                    if len(names) > MAX_FILES:
                        raise _Invalid("source-tree-unsupported")
            for name in sorted(names):
                _checkpoint(deadline)
                relative = prefix+name
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if len(result) >= MAX_FILES or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise _Invalid("source-tree-unsupported")
                result[relative] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, stat.S_ISDIR(info.st_mode))
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        visit(child, relative+"/", depth+1)
                    finally:
                        os.close(child)
                else:
                    total += info.st_size
                    if total > MAX_SOURCE:
                        raise _Invalid("source-size-exceeded")
        visit(root)
        return result
    finally:
        os.close(root)


def freeze_directory(source, destination, *, deadline=None):
    """Return hash of a sorted path/type/content manifest, after stable copying."""
    before = _tree_entries(source, deadline=deadline)
    destination.mkdir(mode=0o700)
    manifest, total = [], 0
    for relative, info in sorted(before.items()):
        _checkpoint(deadline)
        target = destination/relative
        if info[-1]:
            target.mkdir(mode=0o700)
            manifest.append({"path": relative, "kind": "directory"})
        elif info[2] == 0:
            target.touch(mode=0o400)
            manifest.append({"path": relative, "kind": "file", "sha256": _hash(b""), "bytes": 0})
        else:
            receipt = _snapshot(source/relative, target, MAX_SOURCE, deadline=deadline)
            total += receipt["bytes"]
            if total > MAX_SOURCE:
                raise _Invalid("source-size-exceeded")
            manifest.append({"path": relative, "kind": "file", **receipt})
    if before != _tree_entries(source, deadline=deadline):
        raise _Invalid("source-changed-during-copy")
    return {"sha256": _hash(_canonical(manifest)), "bytes": total, "entries": len(manifest),
            "hash_scheme": "devsecops-tree-sha256-v1"}


def _oci_archive(path, expected_image_digest, architecture, *, deadline=None):
    """Validate one-platform OCI layout, every blob hash and descriptor binding."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_image_digest or "") or architecture not in {"amd64", "arm64"}:
        raise _Invalid("image-subject-required")
    seen, blobs, documents, total, count = set(), {}, {}, 0, 0
    with tarfile.open(path, "r:*", tarinfo=_BoundedTarInfo) as archive:
        for member in archive:
            _checkpoint(deadline)
            count += 1
            if count > 1024:
                raise _Invalid("oci-member-limit")
            name = member.name.removeprefix("./")
            if member.isdir() and name.rstrip("/") in {"blobs", "blobs/sha256", "."}:
                continue
            if (name in seen or len(seen) > 1024 or not member.isfile() or member.size > MAX_SOURCE
                    or name not in {"index.json", "oci-layout"} and not re.fullmatch(r"blobs/sha256/[0-9a-f]{64}", name)):
                raise _Invalid("oci-archive-unsupported")
            seen.add(name)
            total += member.size
            if total > MAX_SOURCE:
                raise _Invalid("oci-archive-size-exceeded")
            stream, size, checksum, chunks = archive.extractfile(member), 0, hashlib.sha256(), []
            while chunk := stream.read(1_000_000):
                _checkpoint(deadline)
                size += len(chunk)
                checksum.update(chunk)
                if member.size <= 2_000_000:
                    chunks.append(chunk)
            if size != member.size:
                raise _Invalid("oci-archive-truncated")
            raw = b"".join(chunks) if member.size <= 2_000_000 else None
            if name.startswith("blobs/"):
                if name.split("/")[-1] != checksum.hexdigest():
                    raise _Invalid("oci-blob-digest-mismatch")
                blobs["sha256:"+checksum.hexdigest()] = (size, raw)
            else:
                if raw is None:
                    raise _Invalid("oci-document-size-exceeded")
                documents[name] = _json(raw)
    if documents.get("oci-layout") != {"imageLayoutVersion": "1.0.0"}:
        raise _Invalid("oci-layout-unsupported")
    index = documents.get("index.json", {})
    entries = index.get("manifests")
    if index.get("schemaVersion") != 2 or not isinstance(entries, list) or len(entries) != 1:
        raise _Invalid("oci-platform-selection-unsupported")
    def descriptor(value, media):
        if (not isinstance(value, dict) or value.get("mediaType") not in media or value.get("digest") not in blobs
                or value.get("size") != blobs[value["digest"]][0]):
            raise _Invalid("oci-descriptor-mismatch")
        return blobs[value["digest"]][1]
    manifest_raw = descriptor(entries[0], {"application/vnd.oci.image.manifest.v1+json"})
    if entries[0]["digest"] != expected_image_digest or manifest_raw is None:
        raise _Invalid("oci-image-subject-mismatch")
    manifest = _json(manifest_raw)
    if manifest.get("schemaVersion") != 2 or manifest.get("mediaType") != "application/vnd.oci.image.manifest.v1+json":
        raise _Invalid("oci-manifest-unsupported")
    config_raw = descriptor(manifest.get("config"), {"application/vnd.oci.image.config.v1+json"})
    if config_raw is None:
        raise _Invalid("oci-config-size-exceeded")
    config = _json(config_raw)
    if config.get("os") != "linux" or config.get("architecture") != architecture:
        raise _Invalid("oci-platform-mismatch")
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not 1 <= len(layers) <= 128:
        raise _Invalid("oci-layers-unsupported")
    for layer in layers:
        descriptor(layer, {"application/vnd.oci.image.layer.v1.tar", "application/vnd.oci.image.layer.v1.tar+gzip"})
    rootfs = config.get("rootfs")
    if (not isinstance(rootfs, dict) or rootfs.get("type") != "layers" or not isinstance(rootfs.get("diff_ids"), list)
            or len(rootfs["diff_ids"]) != len(layers)
            or any(not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in rootfs["diff_ids"])):
        raise _Invalid("oci-rootfs-unsupported")
    _layer_bounds(path, {"blobs/sha256/"+layer["digest"].split(":")[1]:
                        (layer["mediaType"].endswith("+gzip"), diff) for layer, diff in zip(layers, rootfs["diff_ids"])}, deadline=deadline)
    return {"manifest_digest": expected_image_digest, "config_digest": manifest["config"]["digest"],
            "platform": {"os": "linux", "architecture": architecture}, "layer_count": len(layers)}


def _layer_bounds(path, wanted, *, deadline=None):
    """Bound nested decompression before handing an archive to a native scanner."""
    entries, total, decoded_total, found = 0, 0, 0, set()
    class BoundedDecoded:
        def __init__(self, stream):
            self.stream, self.checksum = stream, hashlib.sha256()
        def read(self, size=-1):
            nonlocal decoded_total
            _checkpoint(deadline)
            raw = self.stream.read(min(size, 1_000_000) if size >= 0 else 1_000_000)
            decoded_total += len(raw)
            if decoded_total > MAX_SOURCE:
                raise _Invalid("oci-layer-decompression-limit")
            self.checksum.update(raw)
            return raw
    with tarfile.open(path, "r:*", tarinfo=_BoundedTarInfo) as archive:
        for blob in archive:
            _checkpoint(deadline)
            name = blob.name.removeprefix("./")
            if name not in wanted:
                continue
            found.add(name)
            compressed, expected_diff = wanted[name]
            with archive.extractfile(blob) as raw_blob:
                stream = gzip.GzipFile(fileobj=raw_blob) if compressed else raw_blob
                decoded = BoundedDecoded(stream)
                with tarfile.open(fileobj=decoded, mode="r|", tarinfo=_BoundedTarInfo) as layer:
                    for member in layer:
                        _checkpoint(deadline)
                        entries += 1
                        total += member.size
                        parts = Path(member.name).parts
                        if (entries > 100_000 or total > MAX_SOURCE or member.size < 0 or len(parts) > 64
                                or ".." in parts or member.name.startswith("/") or "\\" in member.name
                                or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
                            raise _Invalid("oci-layer-content-unsupported")
                        if member.isfile():
                            file, size = layer.extractfile(member), 0
                            while raw := file.read(1_000_000):
                                _checkpoint(deadline)
                                size += len(raw)
                                if size > member.size:
                                    raise _Invalid("oci-layer-content-unsupported")
                            if size != member.size:
                                raise _Invalid("oci-layer-content-unsupported")
                # Hash all uncompressed tar bytes, including terminal padding.
                while decoded.read(1_000_000):
                    pass
                if "sha256:"+decoded.checksum.hexdigest() != expected_diff:
                    raise _Invalid("oci-rootfs-diffid-mismatch")
                if compressed:
                    stream.close()
    if found != set(wanted):
        raise _Invalid("oci-layer-content-unavailable")


@contextmanager
def _output(path):
    if not isinstance(path, Path) or not path.is_absolute() or not path.name or ".." in path.parts:
        raise ValueError("Output must be a new absolute directory")
    parent = _open_directory(path.parent)
    try:
        os.mkdir(path.name, 0o700, dir_fd=parent)
        directory = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        yield directory
    finally:
        os.close(directory)


def _write(directory, name, raw):
    if len(raw) > MAX_OUTPUT:
        raise _Invalid("output-size-exceeded")
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return {"path": name, "sha256": _hash(raw), "bytes": len(raw)}


def _timestamp(value):
    if not isinstance(value, str):
        raise _Invalid("database-time-unavailable")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _Invalid("database-time-unavailable") from None
    if parsed.tzinfo is None:
        raise _Invalid("database-time-unavailable")
    return parsed


def _database_archive(path, *, deadline=None):
    """Permit the one regular SQLite DB only, before Grype imports any bytes."""
    seen = False
    with ExitStack() as stack:
        with path.open('rb') as source:
            magic = source.read(4)
        if magic == b'\x28\xb5\x2f\xfd':
            from .database_zstd import decoded_database_tar
            decoded = stack.enter_context(decoded_database_tar(path, deadline=deadline))
            archive = stack.enter_context(tarfile.open(fileobj=decoded, mode='r:', tarinfo=_BoundedTarInfo))
            compression = 'zstd'
        else:
            archive = stack.enter_context(tarfile.open(path, "r:gz", tarinfo=_BoundedTarInfo))
            compression = 'gzip'
        end = 0
        for member in archive:
            _checkpoint(deadline)
            if seen or member.name != "vulnerability.db" or not member.isfile() or not 1 <= member.size <= 3_000_000_000:
                raise _Invalid("database-archive-unsupported")
            seen = True
            stream, size = archive.extractfile(member), 0
            while raw := stream.read(1_000_000):
                _checkpoint(deadline)
                if size == 0 and not raw.startswith(b"SQLite format 3\x00"):
                    raise _Invalid("database-format-unsupported")
                size += len(raw)
                if size > member.size:
                    raise _Invalid("database-size-exceeded")
            if size != member.size:
                raise _Invalid("database-truncated")
            end = member.offset_data + ((member.size + 511) // 512) * 512
        if compression == 'zstd':
            # Reject a second tar or hidden nonzero payload after its end marker.
            decoded.seek(end)
            padding = 0
            while chunk := decoded.read(65536):
                _checkpoint(deadline)
                padding += len(chunk)
                if chunk.strip(b'\x00'):
                    raise _Invalid('database-archive-trailing-content')
            if padding < 1024:
                raise _Invalid('database-truncated')
    if not seen:
        raise _Invalid("database-archive-unsupported")
    return compression


def scan_local_artifact(*, source, source_kind, expected_sha256, syft, grype, output,
                        database_snapshot=None, database_sha256=None, max_database_age_hours=120,
                        expected_image_digest=None, architecture=None, timeout=120, prepared_database=None):
    """Produce bounded local evidence. All source kinds require an external hash.

    Optional missing DB yields a real SBOM and an explicit incomplete scan.
    An OCI archive must also bind one linux platform manifest digest; an archive
    hash is never treated as an image digest. No caller clock overrides exist.
    """
    if (source_kind not in {"directory", "oci-archive"} or not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 600
            or type(max_database_age_hours) is not int or not 1 <= max_database_age_hours <= 720
            or (database_snapshot is None) != (database_sha256 is None)
            or database_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", database_sha256)
            or not isinstance(source, Path) or not source.is_absolute()
            or not isinstance(output, Path) or source_kind == "directory" and output.is_relative_to(source)):
        raise ValueError("Explicit local source, hash, output and bounded scan policy are required")
    started = datetime.now(timezone.utc)
    report = {"schema_version": 1, "provider": "artifacts", "execution_status": "partial", "decision": "unknown",
        "confirmed_failure": False, "scope": {"source_kind": source_kind, "source_sha256": expected_sha256},
        "observed_at": started.isoformat(), "observations": {"coverage": "directory-only" if source_kind == "directory" else "one-platform-image",
            "sbom": {"decision": "unknown"}, "vulnerabilities": {"decision": "unknown"}, "tools": {}, "receipts": [],
            "artifacts": {}, "database": {"decision": "unknown"}, "collection": {"started_at": started.isoformat()},
            "network_mode": "local-inputs-no-updates-no-enrichment"}, "gaps": []}
    obs, deadline = report["observations"], time.monotonic()+timeout
    def gap(code):
        report["gaps"].append({"code": code, "required": True, "message": "The supplied local artifact evidence does not establish this required condition"})
    for code in ("artifact-origin-unverified", "database-origin-unverified", "signature-and-attestations-unverified", "promotion-not-approved"):
        gap(code)
    if source_kind == "directory":
        gap("directory-not-whole-image-evidence")
    with _output(output) as outdir:
        try:
            with tempfile.TemporaryDirectory(prefix="devsecops-artifact-") as temporary:
                scratch = Path(temporary).resolve()
                for name in ("home", "empty-path", "cache", "docker", "tmp", "database"):
                    (scratch/name).mkdir(mode=0o700)
                env = {"HOME": str(scratch/"home"), "PATH": str(scratch/"empty-path"), "XDG_CONFIG_HOME": str(scratch/"home"),
                    "XDG_CACHE_HOME": str(scratch/"cache"), "DOCKER_CONFIG": str(scratch/"docker"), "TMPDIR": str(scratch/"tmp"),
                    "SYFT_CHECK_FOR_APP_UPDATE": "false", "GRYPE_CHECK_FOR_APP_UPDATE": "false",
                    "GRYPE_DB_CACHE_DIR": str(scratch/"database"), "GRYPE_DB_AUTO_UPDATE": "false",
                    "GRYPE_DB_VALIDATE_AGE": "true", "GRYPE_DB_MAX_ALLOWED_BUILT_AGE": f"{max_database_age_hours}h",
                    "GRYPE_DB_REQUIRE_UPDATE_CHECK": "false", "GRYPE_DB_VALIDATE_BY_HASH_ON_START": "true"}
                for setting in ("GOLANG_SEARCH_REMOTE_LICENSES", "GOLANG_SEARCH_LOCAL_MOD_CACHE_LICENSES", "GOLANG_SEARCH_LOCAL_VENDOR_LICENSES",
                                "GOLANG_USE_PACKAGES_LIB", "JAVA_USE_NETWORK", "JAVA_USE_MAVEN_LOCAL_REPOSITORY",
                                "JAVASCRIPT_SEARCH_REMOTE_LICENSES", "PYTHON_SEARCH_REMOTE_LICENSES"):
                    env["SYFT_"+setting] = "false"
                (scratch/"syft.yaml").write_text("check-for-app-update: false\n")
                (scratch/"grype.yaml").write_text("check-for-app-update: false\ndb:\n  auto-update: false\n")
                lock_receipt = _snapshot(ROOT/"supplychain.lock.json", scratch/"tool-lock.json", 64_000, deadline=deadline)
                lock = _json((scratch/"tool-lock.json").read_bytes())
                arch = {"aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine(), platform.machine())
                target = platform.system().lower()+"-"+arch
                def execute(tool, arguments, limit=MAX_OUTPUT):
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        raise _Invalid("scan-deadline-exceeded")
                    result, raw, _, receipt = run([str(scratch/tool), "--config", tool+".yaml", *arguments],
                        cwd=scratch, env=env, timeout=remaining, limit=limit)
                    obs["receipts"].append({**receipt, "operation": arguments[0]})
                    return result, raw
                for name, binary in (("syft", syft), ("grype", grype)):
                    tool = lock.get("tools", {}).get(name, {})
                    pinned = tool.get("platforms", {}).get(target, {})
                    receipt = _snapshot(binary, scratch/name, 200_000_000, deadline=deadline)
                    if (lock.get("schema_version") != 1 or tool.get("version") != VERSIONS[name]
                            or receipt != {"sha256": pinned.get("binary_sha256"), "bytes": pinned.get("binary_bytes")}):
                        raise _Invalid("scanner-binary-mismatch")
                    os.chmod(scratch/name, 0o500)
                    code, raw = execute(name, ["version", "-o", "json"], 64_000)
                    version = _json(raw)
                    if code or version.get("version") != VERSIONS[name] or version.get("application") != name:
                        raise _Invalid("scanner-version-mismatch")
                    obs["tools"][name] = {**receipt, "version": VERSIONS[name], "platform": target, "lock_sha256": lock_receipt["sha256"]}
                if source_kind == "directory":
                    obs["source"] = freeze_directory(source, scratch/"source", deadline=deadline)
                    target_source = "dir:source"
                else:
                    obs["source"] = _snapshot(source, scratch/"source.tar", MAX_SOURCE, deadline=deadline)
                    obs["image"] = _oci_archive(scratch/"source.tar", expected_image_digest, architecture, deadline=deadline)
                    target_source = "oci-archive:source.tar"
                if obs["source"]["sha256"] != expected_sha256:
                    raise _Invalid("source-digest-mismatch")
                code, raw = execute("syft", ["scan", target_source, "--source-name", "frozen-artifact", "--source-version", expected_sha256,
                                             "--parallelism", "2", "--output", "syft-json" if source_kind == "oci-archive" else "spdx-json"])
                if code:
                    raise _Invalid("sbom-generation-failed")
                catalog_path = scratch / 'sbom.syft.json'
                if source_kind == 'oci-archive':
                    from .scanner_catalog import inspect_image_catalog, normalize_image_result
                    obs['catalog'] = inspect_image_catalog(raw, image=obs['image'], archive_sha256=expected_sha256,
                        syft_version=VERSIONS['syft'], deadline=deadline)
                    catalog_path.write_bytes(raw)
                    catalog_path.chmod(0o400)
                    def audit_catalog():
                        _checkpoint(deadline)
                        fd = os.open(catalog_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                        with os.fdopen(fd, 'rb') as stream:
                            before = os.fstat(stream.fileno())
                            if (not stat.S_ISREG(before.st_mode) or before.st_size != obs['catalog']['bytes']
                                    or before.st_uid != os.getuid() or before.st_nlink != 1 or before.st_mode & 0o077):
                                raise _Invalid('scanner-catalog-changed')
                            current = stream.read(MAX_OUTPUT + 1)
                            after = os.fstat(stream.fileno())
                            if any(getattr(before, key) != getattr(after, key) for key in (
                                    'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_nlink')):
                                raise _Invalid('scanner-catalog-changed')
                        if len(current) != obs['catalog']['bytes'] or _hash(current) != obs['catalog']['sha256']:
                            raise _Invalid('scanner-catalog-changed')
                        _checkpoint(deadline)
                    code, raw = execute('syft', ['convert', 'sbom.syft.json', '--output', 'spdx-json'])
                    audit_catalog()
                    if code:
                        raise _Invalid('sbom-conversion-failed')
                sbom = parse_scanner_document(raw)
                if sbom.get("spdxVersion") != "SPDX-2.3" or not isinstance(sbom.get("packages"), list):
                    raise _Invalid("sbom-format-unsupported")
                if source_kind == 'oci-archive' and len(sbom['packages']) != obs['catalog']['packages'] + 1:
                    raise _Invalid('sbom-conversion-package-mismatch')
                obs["sbom"] = {"decision": "pass", "format": "SPDX-2.3", "packages": len(sbom["packages"])}
                obs["artifacts"]["sbom"] = _write(outdir, "sbom.spdx.json", raw)
                (scratch/"sbom.spdx.json").write_bytes(raw)
                if database_snapshot is None:
                    raise _Invalid("vulnerability-database-unavailable")
                db_receipt = _snapshot(database_snapshot, scratch/"database.tar.gz", MAX_DATABASE, deadline=deadline)
                if db_receipt["sha256"] != database_sha256:
                    raise _Invalid("database-digest-mismatch")
                preparation = None
                if prepared_database is not None:
                    from .prepared_database import bind_prepared_database
                    preparation = bind_prepared_database(prepared_database, database_sha256=database_sha256,
                        grype_sha256=obs['tools']['grype']['sha256'], max_database_age_hours=max_database_age_hours,
                        deadline=deadline)
                    env['GRYPE_DB_CACHE_DIR'] = preparation['cache']
                    compression = preparation['compression']
                else:
                    compression = _database_archive(scratch/"database.tar.gz", deadline=deadline)
                    database_name = 'database.tar.zst' if compression == 'zstd' else 'database.tar.gz'
                    if compression == 'zstd':
                        (scratch/'database.tar.gz').rename(scratch/database_name)
                    # Grype accepts a path or URL; only our constant copied local path is passed.
                    code, _ = execute("grype", ["db", "import", database_name], 1_000_000)
                    if code:
                        raise _Invalid("database-import-failed")
                code, raw = execute("grype", ["db", "status", "-o", "json"], 64_000)
                status = _json(raw)
                if not re.fullmatch(r"v6\.[0-9]+\.[0-9]+", str(status.get("schemaVersion"))):
                    raise _Invalid("database-status-unverified")
                built = _timestamp(status.get("built"))
                age = (datetime.now(timezone.utc)-built).total_seconds()/3600
                obs["database"] = {"decision": "pass" if 0 <= age <= max_database_age_hours else "unknown", **db_receipt,
                    "built_at": built.isoformat(), "age_hours": round(age, 4), "maximum_age_hours": max_database_age_hours,
                    "schema_version": status["schemaVersion"], "provenance": "operator-supplied-hash", "compression": compression}
                if preparation is not None:
                    obs['database']['preparation_sha256'] = preparation['preparation_sha256']
                if not 0 <= age <= max_database_age_hours:
                    raise _Invalid("database-stale-or-future")
                if code or status.get("valid") is not True:
                    obs["database"]["decision"] = "unknown"
                    raise _Invalid("database-status-unverified")
                if source_kind == 'oci-archive':
                    audit_catalog()
                code, raw = execute("grype", ["sbom:sbom.syft.json" if source_kind == 'oci-archive' else "sbom:sbom.spdx.json", "--output", "json"])
                if code:
                    raise _Invalid("vulnerability-scan-failed")
                if preparation is not None:
                    after = bind_prepared_database(prepared_database, database_sha256=database_sha256,
                        grype_sha256=obs['tools']['grype']['sha256'], max_database_age_hours=max_database_age_hours,
                        deadline=deadline)
                    if after != preparation:
                        raise _Invalid('prepared-database-content-changed')
                if source_kind == 'oci-archive':
                    audit_catalog()
                    result, raw, obs['normalization'] = normalize_image_result(raw, image=obs['image'], archive_sha256=expected_sha256,
                        grype_version=VERSIONS['grype'], deadline=deadline)
                else:
                    result = parse_scanner_document(raw)
                matches = result.get("matches")
                if not isinstance(matches, list):
                    raise _Invalid("vulnerability-format-unsupported")
                severity = {level: 0 for level in ("Critical", "High", "Medium", "Low", "Negligible", "Unknown")}
                for match in matches:
                    value = match.get("vulnerability", {}).get("severity") if isinstance(match, dict) else None
                    if value not in severity:
                        raise _Invalid("vulnerability-severity-unsupported")
                    severity[value] += 1
                obs["artifacts"]["vulnerabilities"] = _write(outdir, "grype.json", raw)
                obs["vulnerabilities"] = {"decision": "fail" if matches else "pass", "matches": len(matches), "severity": severity,
                    "interpretation": "reported-matches-not-exploitability-or-promotion-policy"}
                # Freshness is evaluated at completion, never only at the beginning of the scan.
                age = (datetime.now(timezone.utc)-built).total_seconds()/3600
                obs["database"]["age_hours"] = round(age, 4)
                if not 0 <= age <= max_database_age_hours:
                    obs["database"]["decision"] = "unknown"
                    raise _Invalid("database-stale-or-future")
        except (_Invalid, ToolError, OSError, APIError, ValueError, TypeError, KeyError, tarfile.TarError) as error:
            gap(error.code if isinstance(error, _Invalid) else "artifact-scan-incomplete")
        obs["collection"]["finished_at"] = datetime.now(timezone.utc).isoformat()
        # A report is terminal only if its visible directory still has the same identity.
        current = _open_directory(output)
        try:
            if (os.fstat(current).st_dev, os.fstat(current).st_ino) != (os.fstat(outdir).st_dev, os.fstat(outdir).st_ino):
                raise ValueError("Output directory identity changed")
        finally:
            os.close(current)
        _write(outdir, "receipt.json", _canonical(report)+b"\n")
        os.fsync(outdir)
        try:
            current = _open_directory(output)
            try:
                if (os.fstat(current).st_dev, os.fstat(current).st_ino) != (os.fstat(outdir).st_dev, os.fstat(outdir).st_ino):
                    raise ValueError("Output directory identity changed")
            finally:
                os.close(current)
        except (OSError, ValueError):
            os.unlink("receipt.json", dir_fd=outdir)
            raise ValueError("Output directory identity changed") from None
    return report
