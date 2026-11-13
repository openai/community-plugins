"""Strict operator configuration for same-process F0 qualification and build."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import tempfile
import time

from devsecops.adapters.http import _json
from devsecops.adapters.signatures import _snapshot
from devsecops.factory.base_qualification import qualify_mission_base
from devsecops.factory.mission_executor import build_mission
from devsecops.factory.qualify_builder import prerequisites
from devsecops.factory.state import IDENTITY_KEYS
from devsecops.profiles import relative_path
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal


def _configuration(raw):
    document = _json(raw)
    if (set(document) - {'blob_download_policy', 'attestation_size_policy'} != {'schema_version', 'kind', 'source', 'publisher', 'image', 'tools', 'owner_uid', 'run_scope', 'registry_auth'}
            or type(document['schema_version']) is not int or document['schema_version'] != 1
            or document['kind'] != 'mission-build-configuration'
            or type(document['owner_uid']) is not int or document['owner_uid'] <= 0):
        raise ValueError('Unsupported mission build configuration')
    fields = {'source': {'repository', 'revision', 'path'}, 'publisher': {'policy', 'policy_sha256', 'public_key', 'package'},
              'image': {'policy', 'policy_sha256'}, 'tools': {'cosign', 'build_tools', 'wheel'}}
    for name, expected in fields.items():
        if not isinstance(document[name], dict) or set(document[name]) != expected:
            raise ValueError('Mission configuration has missing or unexpected fields')
    for name in ('blob_download_policy', 'attestation_size_policy'):
        if name not in document:
            continue
        download = document[name]
        if (not isinstance(download, dict) or set(download) != {'path', 'sha256'}
                or not isinstance(download['path'], str) or not Path(download['path']).is_absolute()
                or '..' in Path(download['path']).parts
                or not isinstance(download['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', download['sha256'])):
            raise ValueError('Registry policy requires an explicit absolute path and SHA-256')
    scope = document['run_scope']
    if (not isinstance(scope, dict) or set(scope) != IDENTITY_KEYS
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value) for value in scope.values())):
        raise ValueError('Exact factory run identities are required')
    for section in ('publisher', 'image'):
        if not isinstance(document[section]['policy_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', document[section]['policy_sha256']):
            raise ValueError('Independently selected policy identities are required')
    source = document['source']
    if not isinstance(source['revision'], str) or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', source['revision']):
        raise ValueError('An immutable local Git commit is required')
    relative_path(source['path'])
    for section, names in {'source': ('repository',), 'publisher': ('policy', 'public_key', 'package'),
                           'image': ('policy',), 'tools': ('cosign', 'build_tools', 'wheel')}.items():
        for name in names:
            value = document[section][name]
            if section == 'publisher' and name == 'package' and value is None:
                continue
            if not isinstance(value, str) or not Path(value).is_absolute() or '..' in Path(value).parts:
                raise ValueError('Operator material paths must be explicit and absolute')
    auth = document['registry_auth']
    if not isinstance(auth, dict) or set(auth) - {'token_env', 'basic_username_env', 'basic_password_env', 'token_path', 'token_service', 'ca_bundle'}:
        raise ValueError('Only explicit registry credential references are supported')
    for name, value in auth.items():
        if not isinstance(value, str) or not value or '\x00' in value:
            raise ValueError('Registry reference value is unsupported')
        if name.endswith('_env') and not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', value):
            raise ValueError('Registry credentials must be environment variable names')
        if name == 'ca_bundle' and (not Path(value).is_absolute() or '..' in Path(value).parts):
            raise ValueError('Registry CA must be an explicit absolute path')
    if 'token_env' in auth and any(name.startswith('basic_') for name in auth):
        raise ValueError('Choose one explicit registry authentication mode')
    return document


def qualify_and_build(*, config, expected_config_sha256, output, runtime_root, timeout=1500,
                      expected_source_repository=None, expected_source_revision=None):
    """Return narrow build evidence and its same-process artifact capability."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 60 <= timeout <= 1800
            or not isinstance(expected_config_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_config_sha256)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts
            or not isinstance(runtime_root, Path) or not runtime_root.is_absolute() or '..' in runtime_root.parts
            or len(os.fsencode(runtime_root)) > 60 or not re.fullmatch(r'/[A-Za-z0-9._/-]+', str(runtime_root))):
        raise ValueError('Selected configuration identity, new outputs and bounded runtime are required')
    deadline = time.monotonic() + timeout
    def remaining(minimum):
        value = deadline - time.monotonic()
        if value < minimum:
            raise ValueError('Mission operation exceeded its deadline')
        return value
    with tempfile.TemporaryDirectory(prefix='devsecops-mission-config-') as temporary:
        frozen = Path(temporary).resolve() / 'configuration.json'
        reference = _snapshot(config, frozen, 64_000, deadline=deadline)
        if reference['sha256'] != expected_config_sha256:
            raise ValueError('Mission configuration differs from selected identity')
        selected = _configuration(frozen.read_bytes())
        if expected_source_repository is not None or expected_source_revision is not None:
            if (not isinstance(expected_source_repository, Path) or not expected_source_repository.is_absolute()
                    or '..' in expected_source_repository.parts
                    or not isinstance(expected_source_revision, str)
                    or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', expected_source_revision)
                    or Path(selected['source']['repository']) != expected_source_repository
                    or selected['source']['revision'] != expected_source_revision):
                raise ValueError('Build configuration differs from the caller selected checkout or commit')
        prerequisites(selected['owner_uid'])  # Read-only admission before credentialed acquisition.
        publisher, image, tools = selected['publisher'], selected['image'], selected['tools']
        auth = dict(selected['registry_auth'])
        for name in ('blob_download_policy', 'attestation_size_policy'):
            if name in selected:
                auth.update({name: Path(selected[name]['path']), name + '_sha256': selected[name]['sha256']})
        if 'ca_bundle' in auth:
            auth['ca_bundle'] = Path(auth['ca_bundle'])
        result = {'schema_version': 1, 'kind': 'mission-qualify-and-build', 'scope': 'qualify-and-build-only',
            'decision': 'unknown', 'factory_decision': 'unknown', 'execution_mode': 'direct-local-linux',
            'configuration_sha256': reference['sha256'], 'run_scope': selected['run_scope'],
            'source_admission': 'explicit-caller-checkout-and-commit' if expected_source_repository is not None else 'operator-configuration',
            'started_at': datetime.now(timezone.utc).isoformat(), 'checks': {'base': 'unknown', 'build': 'unknown'},
            'downstream': {'push': 'not-started', 'scan': 'not-started', 'sign': 'not-started', 'promote': 'not-started'}, 'gaps': []}
        capability = None
        with _new_directory(output) as directory, _new_directory(runtime_root):
            qualification, base = qualify_mission_base(publisher_policy=Path(publisher['policy']),
                publisher_policy_sha256=publisher['policy_sha256'], public_key=Path(publisher['public_key']),
                publisher_package=Path(publisher['package']) if publisher['package'] is not None else None,
                image_policy=Path(image['policy']), image_policy_sha256=image['policy_sha256'],
                cosign=Path(tools['cosign']), wheel=Path(tools['wheel']), tools=Path(tools['build_tools']),
                expected_uid=selected['owner_uid'], run_scope=selected['run_scope'], output=output / 'base',
                runtime_output=runtime_root / 'base', total_timeout=min(900, remaining(60)-30), **auth)
            result['checks']['base'] = qualification['decision']
            result['base_qualification_sha256'] = qualification['qualification_sha256']
            if base is None:
                result['gaps'].append('base-qualification-incomplete')
            else:
                subject = qualification['subject']
                source = selected['source']
                built, capability = build_mission(verified_base=base, run_scope=selected['run_scope'],
                    repo=Path(source['repository']), revision=source['revision'], source_path=source['path'],
                    wheel=Path(tools['wheel']), base_repository=subject['origin'].removeprefix('https://') + '/' + subject['repository'],
                    base_digest=subject['digest'], architecture=subject['architecture'], tools=Path(tools['build_tools']),
                    expected_uid=selected['owner_uid'], output=output / 'mission', runtime_output=runtime_root / 'mission',
                    timeout=min(900, remaining(30)))
                result['checks']['build'] = result['decision'] = built['decision']
                result['execution_mode'] = built['execution_mode']
                result['mission_build_sha256'] = built['build_sha256']
                if capability is None:
                    result['gaps'].append('mission-build-incomplete')
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            result = seal(result, 'operation_sha256')
            _verify_output_path(output, directory)
            try:
                _write(directory, 'mission-operation.json', result)
                os.fsync(directory)
                _verify_output_path(output, directory)
                if capability is not None:
                    remaining(0.001)
                    from .mission_executor import require_built_mission
                    require_built_mission(capability, run_scope=selected['run_scope'],
                        source_commit=selected['source']['revision'], architecture=qualification['subject']['architecture'])
            except BaseException:
                try:
                    os.unlink('mission-operation.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            return result, capability
