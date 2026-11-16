"""Build the exact Git mission source using an actual F0-issued base capability."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import tarfile
import time
from weakref import WeakKeyDictionary

from devsecops.adapters.artifacts import _oci_archive
from devsecops.adapters.http import _json
from devsecops.adapters.signatures import _snapshot
from devsecops.factory.base_qualification import require_verified_base
from devsecops.factory.build import prepare_mission_build
from devsecops.factory.mission_recipe import KIND, MAX_IMAGE, checkpoint
from devsecops.factory import qualify_builder
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal

_BUILT = WeakKeyDictionary()


class BuiltMission:
    """Same-process build result; a report file cannot grant staging authority."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('BuiltMission can only be issued by the actual mission executor')

    def __reduce__(self):
        raise TypeError('BuiltMission authority cannot be serialized')

    def describe(self):
        record = _BUILT.get(self)
        if record is None:
            raise ValueError('Unissued mission build object')
        return deepcopy(record['binding'])


def require_built_mission(value, *, run_scope, source_commit, architecture):
    record = _BUILT.get(value) if type(value) is BuiltMission else None
    if record is None or record['pid'] != os.getpid() or record['expires_monotonic'] <= time.monotonic():
        raise ValueError('Unissued, foreign-process or expired mission build authority')
    binding = record['binding']
    if (binding['run_scope'] != run_scope or binding['source_commit'] != source_commit
            or binding['architecture'] != architecture
            or datetime.now(timezone.utc) >= datetime.fromisoformat(binding['expires_at'])):
        raise ValueError('Mission build authority differs from selected run or source')
    return deepcopy(binding)


def build_mission(*, verified_base, run_scope, repo, revision, source_path, wheel,
                  base_repository, base_digest, architecture, tools, expected_uid,
                  output, runtime_output, timeout=600):
    """Prepare and build directly; no imported context or executor verdict API."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 30 <= timeout <= 900
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts
            or not isinstance(runtime_output, Path) or not runtime_output.is_absolute() or '..' in runtime_output.parts):
        raise ValueError('Mission build requires new absolute outputs and a bounded deadline')
    run_scope = deepcopy(run_scope)
    deadline = time.monotonic() + timeout
    def admission():
        checkpoint(deadline)
        return require_verified_base(verified_base, run_scope=run_scope, repository=base_repository,
            requested_digest=base_digest, architecture=architecture)
    base = admission()  # Reject JSON or expired authority before any output/Git operation.
    report = {'schema_version': 1, 'kind': 'mission-image-build', 'scope': 'local-mission-image-build',
        'decision': 'unknown', 'build_authority': 'not-issued', 'factory_decision': 'unknown',
        'execution_mode': 'direct-local-linux', 'run_scope': run_scope,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'checks': {'base': 'pass', 'source': 'unknown', 'executor': 'unknown', 'artifact': 'unknown'},
        'downstream': {'push': 'not-started', 'scan': 'not-started', 'sign': 'not-started', 'promote': 'not-started'},
        'evidence': {}, 'gaps': []}
    binding = None
    with _new_directory(output) as directory:
        try:
            prepared = prepare_mission_build(repo=repo, revision=revision, source_path=source_path, wheel=wheel,
                base_repository=base_repository, base_digest=base_digest, architecture=architecture,
                output=output / 'prepared', timeout=min(120, deadline-time.monotonic()))
            report['checks']['source'] = 'pass'
            report['source'], report['context'] = deepcopy(prepared['source']), deepcopy(prepared['context'])
            recipe = {'kind': KIND, 'repository': base_repository, 'requested_digest': base_digest,
                'manifest_digest': base['platform_manifest_digest'], 'architecture': architecture,
                'source_commit': revision, 'context_sha256': prepared['context']['sha256']}
            admission()
            remaining = deadline-time.monotonic()
            if remaining < 30:
                raise ValueError('Insufficient time for mission execution')
            execution = qualify_builder.qualify(output=runtime_output, tools=tools, expected_uid=expected_uid,
                timeout=min(600, remaining), _mission_build={'recipe': recipe, 'prepared': (output / 'prepared', prepared),
                    'base_authority': verified_base, 'run_scope': run_scope})
            report['checks']['executor'] = execution['decision']
            execution_ref = _snapshot(runtime_output / 'qualification.json', output / 'builder-qualification.json', 64_000, deadline=deadline)
            if _json((output / 'builder-qualification.json').read_bytes()) != execution:
                raise ValueError('Mission executor observation changed')
            report['evidence']['builder-qualification.json'] = execution_ref
            if (execution['decision'] != 'pass' or execution['scope'] != 'local-mission-image-build'
                    or execution['base'] != recipe or execution['mission_inputs']['context'] != prepared['context']
                    or execution['mission_inputs']['source'] != prepared['source']
                    or execution['mission_inputs']['layout'] != base['layout_sha256']
                    or execution['image_lineage']['base_manifest_digest'] != base['platform_manifest_digest']
                    or execution['image_lineage']['base_config_digest'] != base['config_digest']):
                raise ValueError('Mission execution did not bind the exact qualified inputs')
            admission()
            archive = _snapshot(runtime_output / 'image.oci.tar', output / 'image.oci.tar', MAX_IMAGE, deadline=deadline)
            expected = execution['artifact']
            if archive != {'sha256': expected['archive_sha256'], 'bytes': expected['archive_bytes']}:
                raise ValueError('Mission image archive changed after execution')
            image = _oci_archive(output / 'image.oci.tar', expected['manifest_digest'], architecture, deadline=deadline)
            if any(image[key] != expected[key] for key in image):
                raise ValueError('Frozen mission OCI identity changed')
            report['checks']['artifact'] = 'pass'
            report['artifact'] = {'path': 'image.oci.tar', **archive, **image,
                'lineage': deepcopy(execution['image_lineage']), 'source_context_sha256': prepared['context']['sha256']}
            report['tools'] = deepcopy(execution['tools'])
            report['tool_lock'] = deepcopy(execution['tool_lock'])
            admission()
            now = datetime.now(timezone.utc)
            expires = min(now + timedelta(minutes=15), datetime.fromisoformat(base['expires_at']))
            if expires <= now:
                raise ValueError('Mission base authority expired')
            if execution.get('execution_mode') == 'controlled-test':
                report['execution_mode'] = 'controlled-test'
                report['gaps'].append('controlled-execution-cannot-issue-build-authority')
            else:
                binding = {'run_scope': deepcopy(run_scope), 'source_commit': revision,
                    'context_sha256': prepared['context']['sha256'], 'architecture': architecture,
                    'base': {key: base[key] for key in ('repository', 'requested_digest', 'platform_manifest_digest',
                        'config_digest', 'layout_sha256', 'verification_sha256', 'publisher_trust_sha256')},
                    'archive_path': str(output / 'image.oci.tar'), 'archive_sha256': archive['sha256'],
                    'archive_bytes': archive['bytes'], 'manifest_digest': image['manifest_digest'],
                    'config_digest': image['config_digest'], 'builder_run_id': execution['run_id'],
                    'builder_report_sha256': execution_ref['sha256'], 'expires_at': expires.isoformat()}
                report['decision'], report['build_authority'] = 'pass', 'issued'
        except (OSError, ValueError, TypeError, KeyError, IndexError, tarfile.TarError):
            binding = None
            report['gaps'].append('mission-build-incomplete')
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        if binding is not None:
            report['binding'] = deepcopy(binding)
        report = seal(report, 'build_sha256')
        _verify_output_path(output, directory)
        try:
            _write(directory, 'mission-build.json', report)
            os.fsync(directory)
            _verify_output_path(output, directory)
            if binding is not None:
                admission()
                published = qualify_builder._image_receipt(output / 'image.oci.tar', MAX_IMAGE, deadline)
                if published != {'archive_sha256': binding['archive_sha256'], 'archive_bytes': binding['archive_bytes']}:
                    raise ValueError('Mission artifact changed during publication')
                _verify_output_path(output, directory)
                if datetime.now(timezone.utc) >= datetime.fromisoformat(binding['expires_at']):
                    raise ValueError('Mission build authority expired during publication')
        except BaseException:
            try:
                os.unlink('mission-build.json', dir_fd=directory)
            except FileNotFoundError:
                pass
            os.fsync(directory)
            raise
        if binding is None:
            return report, None
        capability = object.__new__(BuiltMission)
        _BUILT[capability] = {'pid': os.getpid(), 'binding': deepcopy(binding),
            'expires_monotonic': time.monotonic() + (expires-datetime.now(timezone.utc)).total_seconds()}
        return report, capability
