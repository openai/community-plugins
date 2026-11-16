"""Direct authenticated image acquisition into a bounded, private OCI layout.

This establishes byte identity only. It never grants publisher, policy, runtime,
or build authority, and it cannot import a caller-provided success receipt.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
import gzip
import hashlib
import os
from pathlib import Path
import re
import stat
import tarfile
import tempfile
import time

from .artifacts import _BoundedTarInfo
from .http import APIError, _bearer, _json
from .http_blob import HTTPSBlobReadClient, MAX_LAYER, MAX_TOTAL
from .oci import (ARCHITECTURES, INDEX, MAX_OBJECT, _RegistrySession, _collect_image_chain,
                  _digest, _repository)
from .signatures import _Invalid, _snapshot
from .registry_download_policy import load_blob_download_policy
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import canonical_bytes, seal

TAR = 'application/vnd.oci.image.layer.v1.tar'
GZIP = 'application/vnd.oci.image.layer.v1.tar+gzip'
DOCKER_GZIP = 'application/vnd.docker.image.rootfs.diff.tar.gzip'
LAYER_TYPES = (TAR, GZIP, DOCKER_GZIP)


def _verify_layout_route(directory, layout, blobs, deadline):
    opened = []
    try:
        current = directory
        for name, expected in (('layout', layout), ('blobs', None), ('sha256', blobs)):
            if time.monotonic() >= deadline:
                raise APIError('deadline')
            current = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            opened.append(current)
            actual = os.fstat(current)
            if actual.st_uid != os.getuid() or actual.st_mode & 0o077:
                raise APIError('blob-output-changed')
            if expected is not None:
                original = os.fstat(expected)
                if (actual.st_dev, actual.st_ino) != (original.st_dev, original.st_ino):
                    raise APIError('blob-output-changed')
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _audit_layout(directory, layout, blobs, artifacts, deadline):
    """Read back the published routes and exact immutable files through dirfds."""
    def checkpoint():
        if time.monotonic() >= deadline:
            raise APIError('deadline')
    _verify_layout_route(directory, layout, blobs, deadline)
    names = []
    with os.scandir(blobs) as entries:
        for entry in entries:
            checkpoint()
            names.append(entry.name)
            if len(names) > 132:
                raise APIError('blob-output-changed')
    if set(names) != set(artifacts):
        raise APIError('blob-output-changed')
    for name, reference in artifacts.items():
        checkpoint()
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=blobs)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_size != reference['bytes']
                    or before.st_uid != os.getuid() or before.st_nlink != 1 or before.st_mode & 0o277):
                raise APIError('blob-output-changed')
            checksum, size = hashlib.sha256(), 0
            while chunk := stream.read(65536):
                checkpoint()
                size += len(chunk)
                if size > reference['bytes']:
                    raise APIError('blob-output-changed')
                checksum.update(chunk)
            after = os.fstat(fd)
            if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or checksum.hexdigest() != reference['sha256']):
                raise APIError('blob-output-changed')
    checkpoint()


class _RegistryBlobClient(HTTPSBlobReadClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_token = None

    def _authorization(self):
        return _bearer(self.session_token) if self.session_token is not None else super()._authorization()


def _layer_identity(fd, *, compressed, expected_diff, budget, deadline):
    """Parse without extraction, bounding decoded bytes, entries and tar metadata."""
    def checkpoint():
        if time.monotonic() >= deadline:
            raise APIError('deadline')
    class Compressed:
        # gzip scans optional names/comments one byte at a time. Checking only
        # after its decoded read returns would leave that header loop unbounded.
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            checkpoint()
            return self.stream.read(min(size, 65536) if size >= 0 else 65536)

    class Decoded:
        def __init__(self, stream):
            self.stream, self.checksum, self.size = stream, hashlib.sha256(), 0

        def read(self, size=-1):
            checkpoint()
            raw = self.stream.read(min(size, 65536) if size >= 0 else 65536)
            self.size += len(raw)
            budget['bytes'] += len(raw)
            if budget['bytes'] > budget['limit']:
                raise APIError('layer-decompression-limit')
            self.checksum.update(raw)
            return raw
    try:
        with os.fdopen(os.dup(fd), 'rb') as raw:
            raw.seek(0)
            stream = gzip.GzipFile(fileobj=Compressed(raw)) if compressed else raw
            try:
                decoded = Decoded(stream)
                with tarfile.open(fileobj=decoded, mode='r|', tarinfo=_BoundedTarInfo) as archive:
                    for member in archive:
                        checkpoint()
                        budget['entries'] += 1
                        budget['declared_bytes'] += member.size
                        parts = Path(member.name).parts
                        if (budget['entries'] > 100_000 or budget['declared_bytes'] > budget['limit']
                                or member.size < 0 or len(parts) > 64 or '..' in parts
                                or member.name.startswith('/') or '\\' in member.name
                                or not (member.isfile() or member.isdir() or member.issym() or member.islnk())):
                            raise APIError('layer-content-unsupported')
                        if member.isfile():
                            with archive.extractfile(member) as payload:
                                size = 0
                                while chunk := payload.read(65536):
                                    checkpoint()
                                    size += len(chunk)
                                    if size > member.size:
                                        raise APIError('layer-content-unsupported')
                                if size != member.size:
                                    raise APIError('layer-content-unsupported')
                while decoded.read(65536):
                    pass
                if 'sha256:' + decoded.checksum.hexdigest() != expected_diff:
                    raise APIError('rootfs-diffid-mismatch')
                checkpoint()
                return {'diff_id': expected_diff, 'decoded_bytes': decoded.size}
            finally:
                if compressed:
                    stream.close()
    except (OSError, EOFError, tarfile.TarError, _Invalid):
        raise APIError('layer-content-unsupported') from None


def acquire_image_layout(origin, *, repository, requested_digest, architecture,
                         allowed_repositories, output, variant=None,
                         token_env=None, basic_username_env=None, basic_password_env=None,
                         token_path=None, token_service=None, ca_bundle=None,
                         timeout=30, total_timeout=300, max_layer_bytes=MAX_LAYER,
                         max_total_bytes=MAX_TOTAL, max_decoded_bytes=MAX_TOTAL,
                         blob_download_policy=None, blob_download_policy_sha256=None):
    """Fetch exact image bytes; publish a usable layout only after every check.

    Credentials are explicit environment references. Auth is limited to one
    configured origin/repository. The output must be a new private directory.
    Regular tar and gzip layers are supported; zstd, foreign/external layers,
    metadata URLs and delegated credentials are rejected. Blob redirects require
    an independently pinned exact-origin download policy.
    """
    if (not _repository(repository) or not _digest(requested_digest)
            or not isinstance(allowed_repositories, (list, tuple)) or not 1 <= len(allowed_repositories) <= 32
            or any(not _repository(p) for p in allowed_repositories)
            or len(set(allowed_repositories)) != len(allowed_repositories) or repository not in allowed_repositories
            or not isinstance(architecture, str) or architecture not in ARCHITECTURES
            or (variant is not None and (not isinstance(variant, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,31}', variant)))
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts
            or (token_env is None and basic_username_env is None)
            or type(max_decoded_bytes) is not int or not 1 <= max_decoded_bytes <= MAX_TOTAL):
        raise APIError('invalid-configuration')
    selected_download = load_blob_download_policy(blob_download_policy, blob_download_policy_sha256,
        registry_origin=origin, allowed_repositories=allowed_repositories)
    began = time.monotonic()
    options = dict(token_env=token_env, basic_username_env=basic_username_env,
        basic_password_env=basic_password_env, timeout=timeout, total_timeout=total_timeout,
        max_layer_bytes=max_layer_bytes, max_total_bytes=max_total_bytes)
    probe = _RegistryBlobClient(origin, **options)
    _RegistrySession(probe, repository=repository, token_path=token_path, token_service=token_service,
        blob_download_policy=selected_download)
    probe._authorization()
    deadline = began + total_timeout
    def clock():
        return datetime.now(timezone.utc).isoformat()
    def checkpoint():
        if time.monotonic() >= deadline:
            raise APIError('deadline')
    result = {'schema_version': 1, 'provider': 'registry-image-layout', 'decision': 'unknown',
        'execution_status': 'partial', 'build_authority': 'not-issued',
        'scope': {'origin': probe.origin, 'repository': repository, 'requested_digest': requested_digest,
                  'os': 'linux', 'architecture': architecture, 'variant': variant},
        'observed_at': clock(), 'artifacts': {}, 'gaps': [
            {'code': code, 'required': True} for code in ('publisher-authority-unverified',
                'publisher-signature-unverified', 'required-attestation-unverified',
                'predicate-semantics-unassessed', 'runtime-compatibility-unverified')],
        'observations': {'acquisition': {'decision': 'unknown', 'source': 'direct-authenticated-https'},
            'image': {'content_identity': {'decision': 'unknown'}, 'objects': []},
            'layers': [], 'layout': None, 'authentication': {}, 'receipts': [],
            'bounds': {'layer_bytes': max_layer_bytes, 'total_bytes': max_total_bytes,
                       'decoded_bytes': max_decoded_bytes, 'layer_count': 128, 'timeout_seconds': total_timeout},
            'tls': {'trust': 'system-ca' if ca_bundle is None else 'explicit-frozen-ca', 'ca_sha256': None},
            'collection': {'started_at': clock()}}}
    if selected_download is not None:
        result['scope']['blob_download_policy'] = selected_download.identity_record()
    obs, client = result['observations'], None
    with _new_directory(output) as directory, ExitStack() as handles:
        os.mkdir('layout', 0o700, dir_fd=directory)
        layout = os.open('layout', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        handles.callback(os.close, layout)
        blobs, markers = None, []
        try:
            os.mkdir('blobs', 0o700, dir_fd=layout)
            parent = os.open('blobs', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=layout)
            try:
                os.mkdir('sha256', 0o700, dir_fd=parent)
                blobs = os.open('sha256', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                handles.callback(os.close, blobs)
            finally:
                os.close(parent)
            documents = {}
            def store_metadata(raw, receipt):
                checkpoint()
                if not 0 < len(raw) <= MAX_OBJECT:
                    raise APIError('object-limit')
                digest = receipt['digest']
                documents[digest] = (raw, receipt)
                name = digest[7:]
                if name not in result['artifacts']:
                    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=blobs)
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    result['artifacts'][name] = {'sha256': name, 'bytes': len(raw), 'kind': 'metadata'}
            with tempfile.TemporaryDirectory(prefix='devsecops-image-ca-') as temporary:
                frozen_ca = None
                if ca_bundle is not None:
                    frozen_ca = Path(temporary).resolve() / 'ca.pem'
                    obs['tls']['ca_sha256'] = _snapshot(ca_bundle, frozen_ca, 2_000_000, deadline=deadline)['sha256']
                client = _RegistryBlobClient(origin, ca_bundle=frozen_ca, **options)
                client._deadline = deadline
                session = _RegistrySession(client, repository=repository, token_path=token_path, token_service=token_service,
                    blob_download_policy=selected_download)
                obs['authentication'] = session.authentication
                _collect_image_chain(session, requested_digest=requested_digest, architecture=architecture,
                    variant=variant, observations=obs['image'], object_sink=store_metadata)
                manifest_raw, manifest_ref = documents[obs['image']['platform_manifest_digest']]
                manifest = _json(manifest_raw)
                config = _json(documents[obs['image']['config_digest']][0])
                layers = manifest['layers']
                if len(layers) > 128:
                    raise APIError('layer-count-limit')
                for descriptor in [manifest['config'], *layers]:
                    if set(descriptor) - {'mediaType', 'digest', 'size', 'annotations'}:
                        raise APIError('external-descriptor-unsupported')
                if any(layer['mediaType'] not in LAYER_TYPES for layer in layers):
                    raise APIError('layer-media-unsupported')
                if any(not 1 <= layer['size'] <= max_layer_bytes for layer in layers):
                    raise APIError('layer-size-limit')
                if sum(layer['size'] for layer in layers) + client._bytes > max_total_bytes:
                    raise APIError('body-limit')
                budget = {'bytes': 0, 'declared_bytes': 0, 'entries': 0, 'limit': max_decoded_bytes}
                for index, (descriptor, diff_id) in enumerate(zip(layers, config['rootfs']['diff_ids'], strict=True)):
                    checkpoint()
                    name, partial = descriptor['digest'][7:], f'.partial-{index}'
                    # Duplicate content still binds each rootfs diffID. It is
                    # re-read through a new private file, never caller storage.
                    fd = os.open(partial, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=blobs)
                    try:
                        fetched = session.blob(descriptor['digest'], fd=fd, expected_size=descriptor['size'],
                            accept=(descriptor['mediaType'], 'application/octet-stream'))
                        decoded = _layer_identity(fd, compressed=descriptor['mediaType'] != TAR,
                            expected_diff=diff_id, budget=budget, deadline=deadline)
                        os.fchmod(fd, 0o400)
                        if name not in result['artifacts']:
                            os.rename(partial, name, src_dir_fd=blobs, dst_dir_fd=blobs)
                            result['artifacts'][name] = {'sha256': name, 'bytes': fetched.size_bytes, 'kind': 'layer'}
                        else:
                            os.unlink(partial, dir_fd=blobs)
                        obs['layers'].append({'index': index, 'digest': descriptor['digest'],
                            'size_bytes': fetched.size_bytes, 'media_type': descriptor['mediaType'], **decoded})
                    finally:
                        os.close(fd)
                checkpoint()
                _verify_output_path(output, directory)
                _audit_layout(directory, layout, blobs, result['artifacts'], deadline)
                platform = {'os': 'linux', 'architecture': architecture}
                effective_variant = obs['image']['platform']['config_variant'] or obs['image']['platform']['index_variant']
                if effective_variant is not None:
                    platform['variant'] = effective_variant
                index = canonical_bytes({'schemaVersion': 2, 'mediaType': INDEX, 'manifests': [{
                    'mediaType': manifest_ref['media_type'], 'digest': obs['image']['platform_manifest_digest'],
                    'size': len(manifest_raw), 'platform': platform}]})
                # Completion markers are absent on failure. Original index bytes
                # are retained only as provenance, never as missing-platform refs.
                _write(layout, 'index.json', index.decode('utf-8'))
                markers.append('index.json')
                layout_ref = _write(layout, 'oci-layout', {'imageLayoutVersion': '1.0.0'})
                markers.append('oci-layout')
                inventory = {'blobs': result['artifacts'], 'index_sha256': hashlib.sha256(index).hexdigest(),
                             'layout_version_sha256': layout_ref['sha256'],
                             'manifest_digest': obs['image']['platform_manifest_digest']}
                _verify_output_path(output, directory)
                _verify_layout_route(directory, layout, blobs, deadline)
                checkpoint()
                obs['layout'] = {'directory': 'layout', 'sha256': hashlib.sha256(canonical_bytes(inventory)).hexdigest(),
                    'hash_scheme': 'devsecops-image-layout-v1', **inventory}
                obs['acquisition']['decision'] = 'pass'
                result['execution_status'] = 'completed'
        except APIError as error:
            result['gaps'].append({'code': 'image-' + error.code, 'required': True})
            if error.code in {'digest-mismatch', 'header-digest-mismatch', 'descriptor-size-mismatch',
                              'platform-mismatch', 'rootfs-diffid-mismatch', 'blob-output-changed'}:
                obs['acquisition']['decision'] = 'fail'
        except (_Invalid, OSError, ValueError, TypeError, KeyError):
            result['gaps'].append({'code': 'image-input-or-output-unavailable', 'required': True})
        finally:
            if obs['acquisition']['decision'] != 'pass':
                for name in markers:
                    os.unlink(name, dir_fd=layout)
            if client is not None:
                client.session_token = None
                obs['receipts'] = client.receipts
            obs['collection']['finished_at'] = result['observed_at'] = clock()
        result = seal(result, 'manifest_sha256')
        try:
            _verify_output_path(output, directory)
            _write(directory, 'image-layout.json', result)
            os.fsync(directory)
            _verify_output_path(output, directory)
            if obs['acquisition']['decision'] == 'pass':
                _verify_layout_route(directory, layout, blobs, deadline)
                for name, expected in (('index.json', obs['layout']['index_sha256']),
                                       ('oci-layout', obs['layout']['layout_version_sha256'])):
                    checkpoint()
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=layout)
                    with os.fdopen(fd, 'rb') as stream:
                        info = os.fstat(fd)
                        if (not stat.S_ISREG(info.st_mode) or info.st_size > MAX_OBJECT
                                or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077):
                            raise APIError('blob-output-changed')
                        raw = stream.read(MAX_OBJECT + 1)
                        if len(raw) != info.st_size or hashlib.sha256(raw).hexdigest() != expected:
                            raise APIError('blob-output-changed')
                _verify_output_path(output, directory)
                _verify_layout_route(directory, layout, blobs, deadline)
                checkpoint()
        except BaseException:
            # Retain the owned dirfds through the terminal publication. An
            # expired or replaced layout must not retain either a pass receipt
            # or the completion markers, including in a detached owned inode.
            for target, name in [(directory, 'image-layout.json'), *[(layout, name) for name in markers]]:
                try:
                    os.unlink(name, dir_fd=target)
                except FileNotFoundError:
                    pass
            os.fsync(layout)
            os.fsync(directory)
            raise
        return result
