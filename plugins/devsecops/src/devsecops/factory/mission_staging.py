"""Stage only a same-process mission build, then independently read every byte."""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import tarfile
import tempfile
import time
from weakref import WeakKeyDictionary

from devsecops.adapters.artifacts import _BoundedTarInfo, _oci_archive, _open_directory
from devsecops.adapters.http import APIError, HTTPSReadClient, _json
from devsecops.adapters.image_layout import acquire_image_layout, _audit_layout, _verify_layout_route
from devsecops.adapters.oci import MANIFEST, _repository
from devsecops.adapters.signatures import _snapshot
from devsecops.adapters.staging_registry import _StagingRegistry, _token_route
from devsecops.factory.mission_executor import require_built_mission
from devsecops.factory.mission_recipe import MAX_IMAGE, checkpoint
from devsecops.factory.qualify_builder import _image_receipt
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import canonical_bytes, seal

_STAGED = WeakKeyDictionary()


class StagedMission:
    """Narrow staging/readback authority; never a scan, signature or promotion."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('StagedMission can only be issued after actual immutable readback')

    def __reduce__(self):
        raise TypeError('Staging authority cannot be serialized')

    def describe(self):
        record = _STAGED.get(self)
        if record is None:
            raise ValueError('Unissued staging authority')
        return deepcopy(record['binding'])


def require_staged_mission(value, *, run_scope, source_commit, architecture, origin, repository):
    record = _STAGED.get(value) if type(value) is StagedMission else None
    if (record is None or record['pid'] != os.getpid() or record['expires_monotonic'] <= time.monotonic()):
        raise ValueError('Unissued, foreign-process or expired staging authority')
    binding = record['binding']
    if (any(binding[name] != selected for name, selected in (
            ('run_scope', run_scope), ('source_commit', source_commit), ('architecture', architecture),
            ('origin', origin), ('repository', repository)))
            or datetime.now(timezone.utc) >= datetime.fromisoformat(binding['expires_at'])):
        raise ValueError('Staging authority differs from selected run or destination')
    return deepcopy(binding)


def _configuration(raw):
    selected = _json(raw)
    if (set(selected) != {'schema_version', 'kind', 'origin', 'repository', 'credentials', 'ca_bundle'}
            or type(selected['schema_version']) is not int or selected['schema_version'] != 1
            or selected['kind'] != 'mission-staging-configuration' or not _repository(selected['repository'])):
        raise ValueError('Unsupported staging configuration')
    origin = HTTPSReadClient(selected['origin'])
    if selected['origin'] != origin.origin or origin._host == 'registry1.dso.mil':
        raise ValueError('An exact staging origin separate from the publisher is required')
    credentials = selected['credentials']
    if not isinstance(credentials, dict) or set(credentials) not in (
            {'token_env'}, {'basic_username_env', 'basic_password_env', 'token_path', 'token_service'}):
        raise ValueError('Dedicated staging credential references are required')
    for name, value in credentials.items():
        if name.endswith('_env') and (not isinstance(value, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', value)):
            raise ValueError('Staging credentials must be explicit environment references')
    if 'basic_username_env' in credentials and credentials['basic_username_env'] == credentials['basic_password_env']:
        raise ValueError('Username and password references must differ')
    _token_route(credentials.get('token_path'), credentials.get('token_service'))
    ca = selected['ca_bundle']
    if ca is not None and (not isinstance(ca, str) or not Path(ca).is_absolute() or '..' in Path(ca).parts):
        raise ValueError('Staging CA must be an explicit absolute path')
    return selected


def _archive_members(archive, binding, deadline):
    members = {}
    for member in archive:
        checkpoint(deadline)
        name = member.name.removeprefix('./')
        if name.startswith('blobs/sha256/') and member.isfile():
            members['sha256:' + name.rsplit('/', 1)[-1]] = member
    subject = members[binding['manifest_digest']]
    if subject.size > 2_000_000:
        raise ValueError('Manifest size exceeded')
    manifest = _json(archive.extractfile(subject).read(2_000_001))
    if manifest['config']['digest'] != binding['config_digest']:
        raise ValueError('Config differs from built identity')
    descriptors = [manifest['config'], *manifest['layers']]
    unique = {}
    for descriptor in descriptors:
        digest = descriptor['digest']
        if descriptor.get('urls') or descriptor.get('data') or descriptor.get('artifactType'):
            raise ValueError('External descriptors cannot be staged')
        if digest in unique and descriptor != unique[digest]:
            raise ValueError('Conflicting blob descriptors')
        unique[digest] = descriptor
    manifest_descriptor = {'mediaType': MANIFEST, 'digest': binding['manifest_digest'], 'size': subject.size}
    return members, list(unique.values()), manifest_descriptor


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_uid, info.st_mode, info.st_nlink)


def _freeze_stream(stream, reference, deadline):
    """Validate the retained descriptor, not a path reopened after validation."""
    fd = stream.fileno()
    before = os.fstat(fd)
    if (not stat.S_ISREG(before.st_mode) or before.st_size != reference['bytes']
            or before.st_uid != os.getuid() or before.st_nlink != 1 or before.st_mode & 0o077):
        raise ValueError('Frozen image descriptor is unsupported')
    os.fchmod(fd, 0o400)
    before = os.fstat(fd)
    checksum, offset = hashlib.sha256(), 0
    while offset < before.st_size:
        checkpoint(deadline)
        chunk = os.pread(fd, min(65536, before.st_size-offset), offset)
        if not chunk:
            raise ValueError('Frozen image descriptor changed')
        checksum.update(chunk)
        offset += len(chunk)
    if _file_identity(before) != _file_identity(os.fstat(fd)) or checksum.hexdigest() != reference['sha256']:
        raise ValueError('Frozen image descriptor differs from built image')
    return _file_identity(before)


def _readback_guard(root, directory, layout, blobs, inventory, deadline):
    checkpoint(deadline)
    _verify_output_path(root, directory)
    _verify_layout_route(directory, layout, blobs, deadline)
    _audit_layout(directory, layout, blobs, inventory['blobs'], deadline)
    for name, key in [('index.json', 'index_sha256'), ('oci-layout', 'layout_version_sha256')]:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=layout)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 2_000_000 or before.st_nlink != 1:
                raise ValueError('Readback marker is unsupported')
            raw = stream.read(2_000_001)
            after = os.fstat(stream.fileno())
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or hashlib.sha256(raw).hexdigest() != inventory[key]):
                raise ValueError('Readback marker changed')
        checkpoint(deadline)
    _verify_layout_route(directory, layout, blobs, deadline)


def stage_mission(*, built, run_scope, source_commit, architecture,
                  config, expected_config_sha256, output, timeout=600):
    """Concrete upload and independent pull; no report-to-authority constructor.

The selected staging account must be separately provisioned with pull/push on
this repository. Ambiguous writes are retained and never automatically retried
or deleted. This operation has no signing or promotion behavior.
"""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 900
            or not isinstance(expected_config_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_config_sha256)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Pinned staging configuration, new output and bounded deadline are required')
    deadline = time.monotonic() + timeout
    run_scope = deepcopy(run_scope)
    def admission():
        checkpoint(deadline)
        return require_built_mission(built, run_scope=run_scope, source_commit=source_commit, architecture=architecture)
    source = admission()  # Reject imported JSON before credentials, I/O or network.
    with tempfile.TemporaryDirectory(prefix='devsecops-staging-config-') as temporary:
        temporary = Path(temporary).resolve()
        frozen_config = temporary / 'configuration.json'
        config_ref = _snapshot(config, frozen_config, 64000, deadline=deadline)
        if config_ref['sha256'] != expected_config_sha256:
            raise ValueError('Staging configuration differs from selected identity')
        selected = _configuration(frozen_config.read_bytes())
        ca = None
        ca_ref = None
        if selected['ca_bundle'] is not None:
            ca = temporary / 'ca.pem'
            ca_ref = _snapshot(Path(selected['ca_bundle']), ca, 2_000_000, deadline=deadline)
        client = _StagingRegistry(selected['origin'], repository=selected['repository'], deadline=deadline,
            ca_bundle=ca, **selected['credentials'])
        report = {'schema_version': 1, 'kind': 'mission-image-staging', 'decision': 'unknown',
            'stage_authority': 'not-issued', 'factory_decision': 'unknown', 'scope': 'staging-and-readback-only',
            'execution_mode': source.get('execution_mode', 'direct'),
            'run_scope': run_scope, 'source_commit': source_commit, 'architecture': architecture,
            'destination': {'origin': selected['origin'], 'repository': selected['repository'],
                'manifest_digest': source['manifest_digest']}, 'configuration_sha256': config_ref['sha256'],
            'ca_sha256': ca_ref['sha256'] if ca_ref else None, 'started_at': datetime.now(timezone.utc).isoformat(),
            'checks': {'build': 'pass', 'artifact': 'unknown', 'upload': 'unknown', 'readback': 'unknown'},
            'mutations': [], 'receipts': [], 'gaps': [],
            'downstream': {'scan': 'not-started', 'sign': 'not-started', 'promote': 'not-started'}}
        binding = None
        with _new_directory(output) as directory, ExitStack() as handles:
            def archive_guard():
                checkpoint(deadline)
                if _file_identity(os.fstat(stream.fileno())) != frozen_identity:
                    raise ValueError('Frozen upload source changed')

            def intent(kind, digest):
                admission()
                archive_guard()
                _verify_output_path(output, directory)
                item = {'kind': kind, 'digest': digest, 'state': 'may-have-written',
                    'destination': report['destination'], 'configuration_sha256': config_ref['sha256'],
                    'run_scope': run_scope, 'source_commit': source_commit}
                name = 'mutation-' + str(len(report['mutations']) + 1).zfill(3) + '.json'
                ref = _write(directory, name, item)
                os.fsync(directory)
                report['mutations'].append({'intent': name, **ref, 'state': 'unknown'})
                admission()
            try:
                image_path = output / 'image.oci.tar'
                archive_ref = _snapshot(Path(source['archive_path']), image_path, MAX_IMAGE, deadline=deadline)
                if archive_ref != {'sha256': source['archive_sha256'], 'bytes': source['archive_bytes']}:
                    raise ValueError('Image differs from built artifact')
                image = _oci_archive(image_path, source['manifest_digest'], architecture, deadline=deadline)
                if image['config_digest'] != source['config_digest']:
                    raise ValueError('Image config differs from built artifact')
                report['artifact'] = {'path': 'image.oci.tar', **archive_ref, **image}
                report['checks']['artifact'] = 'pass'
                _verify_output_path(output, directory)
                fd = os.open('image.oci.tar', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                stream = handles.enter_context(os.fdopen(fd, 'rb'))
                frozen_identity = _freeze_stream(stream, archive_ref, deadline)
                archive = handles.enter_context(tarfile.open(fileobj=stream, mode='r:*', tarinfo=_BoundedTarInfo))
                members, blobs_to_upload, manifest = _archive_members(archive, source, deadline)
                archive_guard()
                class Body:
                    def __init__(self, body):
                        self.body = body

                    def read(self, count):
                        archive_guard()
                        chunk = self.body.read(count)
                        archive_guard()
                        return chunk
                expected = {d['digest'][7:]: {'sha256': d['digest'][7:], 'bytes': d['size'],
                    'kind': 'metadata' if d in (blobs_to_upload[0], manifest) else 'layer'} for d in [*blobs_to_upload, manifest]}
                admission()
                client.authenticate(source['manifest_digest'])
                for descriptor in blobs_to_upload:
                    intent('begin-blob', descriptor['digest'])
                    location = client.begin_blob()
                    report['mutations'][-1]['state'] = 'upload-session-acknowledged'
                    intent('complete-blob', descriptor['digest'])
                    with archive.extractfile(members[descriptor['digest']]) as body:
                        client.finish_blob(location, Body(body), descriptor)
                    report['mutations'][-1]['state'] = 'acknowledged-awaiting-readback'
                intent('put-manifest', manifest['digest'])
                with archive.extractfile(members[manifest['digest']]) as body:
                    client.put_manifest(Body(body), manifest)
                report['mutations'][-1]['state'] = 'acknowledged-awaiting-readback'
                report['checks']['upload'] = 'pass'
                admission()
                remaining = deadline-time.monotonic()
                readback = acquire_image_layout(selected['origin'], repository=selected['repository'],
                    allowed_repositories=[selected['repository']], requested_digest=source['manifest_digest'],
                    architecture=architecture, output=output / 'readback', ca_bundle=ca,
                    total_timeout=min(600, remaining), timeout=min(120, remaining), max_total_bytes=MAX_IMAGE,
                    max_layer_bytes=MAX_IMAGE, max_decoded_bytes=1_024_000_000, **selected['credentials'])
                report['checks']['readback'] = readback['observations']['acquisition']['decision']
                report['readback_sha256'] = readback['manifest_sha256']
                admission()
                inventory = readback['observations']['layout']
                if (report['checks']['readback'] != 'pass' or readback['artifacts'] != expected
                        or readback['observations']['image']['config_digest'] != source['config_digest']
                        or inventory['manifest_digest'] != source['manifest_digest']):
                    raise ValueError('Immutable registry readback differs from built image')
                readback_directory = _open_directory(output / 'readback')
                handles.callback(os.close, readback_directory)
                layout = os.open('layout', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=readback_directory)
                handles.callback(os.close, layout)
                intermediate = os.open('blobs', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=layout)
                try:
                    blobs_fd = os.open('sha256', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=intermediate)
                    handles.callback(os.close, blobs_fd)
                finally:
                    os.close(intermediate)
                _readback_guard(output / 'readback', readback_directory, layout, blobs_fd, inventory, deadline)
                readback_ref = _snapshot(output / 'readback/image-layout.json', output / 'readback-receipt.json', 256000, deadline=deadline)
                if (output / 'readback-receipt.json').read_bytes() != canonical_bytes(readback):
                    raise ValueError('Readback receipt changed')
                report['readback_receipt'] = readback_ref
                for mutation in report['mutations']:
                    mutation['state'] = 'content-confirmed-by-immutable-readback'
                now = datetime.now(timezone.utc)
                expires = min(now + timedelta(minutes=15), datetime.fromisoformat(source['expires_at']))
                if report['execution_mode'] == 'controlled-test':
                    report['gaps'].append('controlled-build-cannot-issue-stage-authority')
                else:
                    binding = {'run_scope': run_scope, 'source_commit': source_commit, 'architecture': architecture,
                        'origin': selected['origin'], 'repository': selected['repository'],
                        'manifest_digest': source['manifest_digest'], 'config_digest': source['config_digest'],
                        'archive_path': str(image_path), 'archive_sha256': archive_ref['sha256'], 'archive_bytes': archive_ref['bytes'],
                        'context_sha256': source['context_sha256'], 'base': deepcopy(source['base']),
                        'build_report_sha256': source['builder_report_sha256'], 'configuration_sha256': config_ref['sha256'],
                        'layout_path': str(output / 'readback/layout'), 'layout_inventory': deepcopy(inventory),
                        'readback_sha256': readback['manifest_sha256'], 'expires_at': expires.isoformat()}
                    report['decision'], report['stage_authority'] = 'pass', 'issued'
                    report['binding'] = deepcopy(binding)
            except (APIError, OSError, ValueError, TypeError, KeyError, IndexError, tarfile.TarError) as error:
                binding = None
                report['gaps'].append('staging-or-readback-incomplete')
                if isinstance(error, APIError):
                    report['gaps'].append(error.code)
            finally:
                report['receipts'] = client.receipts
                client.close()
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            report = seal(report, 'stage_sha256')
            _verify_output_path(output, directory)
            try:
                _write(directory, 'mission-staging.json', report)
                os.fsync(directory)
                _verify_output_path(output, directory)
                if binding is not None:
                    admission()
                    archive_guard()
                    _readback_guard(output / 'readback', readback_directory, layout, blobs_fd, inventory, deadline)
                    if _image_receipt(image_path, MAX_IMAGE, deadline) != {
                            'archive_sha256': source['archive_sha256'], 'archive_bytes': source['archive_bytes']}:
                        raise ValueError('Retained staged archive changed during publication')
                    if datetime.now(timezone.utc) >= expires:
                        raise ValueError('Staging authority expired during publication')
                    _verify_output_path(output, directory)
            except BaseException:
                try:
                    os.unlink('mission-staging.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            if binding is None:
                return report, None
            capability = object.__new__(StagedMission)
            _STAGED[capability] = {'pid': os.getpid(), 'binding': deepcopy(binding),
                'expires_monotonic': time.monotonic() + (expires-datetime.now(timezone.utc)).total_seconds()}
            return report, capability
