"""Run build, immutable staging and security assessment in one trusted process."""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import tempfile
import time

from devsecops.adapters.artifacts import MAX_DATABASE
from devsecops.adapters.http import _json
from devsecops.adapters.signatures import _snapshot
from devsecops.adapters.vulnerability_policy import database_listing, policy_document
from devsecops.factory.mission_delivery import qualify_build_and_stage
from devsecops.factory.mission_security import _read_retained, assess_staged_mission, require_assessed_mission
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal


def _configuration(raw):
    value = _json(raw)
    required = {'schema_version', 'kind', 'policy_path', 'policy_sha256', 'database_path',
                'listing_path', 'syft_path', 'grype_path'}
    optional = {'database_preparation_timeout_seconds', 'scanner_timeout_seconds'}
    if (not required <= set(value) or set(value) - required - optional
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['kind'] != 'mission-security-configuration'
            or not isinstance(value['policy_sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', value['policy_sha256'])):
        raise ValueError('Explicit pinned security policy and local inputs are required')
    for name, maximum in (('database_preparation_timeout_seconds', 1800), ('scanner_timeout_seconds', 600)):
        if name in value and (type(value[name]) is not int or not 1 <= value[name] <= maximum):
            raise ValueError('Database preparation and scanning require bounded execution budgets')
    for name in ('policy_path', 'database_path', 'listing_path', 'syft_path', 'grype_path'):
        if (not isinstance(value[name], str) or len(value[name]) > 4096
                or not Path(value[name]).is_absolute() or '..' in Path(value[name]).parts):
            raise ValueError('Security paths must be explicit absolute local paths')
    return value


def qualify_stage_and_assess(*, build_config, build_config_sha256, staging_config, staging_config_sha256,
                             security_config, security_config_sha256, output, runtime_root, timeout=3000,
                             expected_source_repository=None, expected_source_revision=None):
    """Only direct typed results cross stages; saved reports are never inputs."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 122 <= timeout <= 3600
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
                   for value in (build_config_sha256, staging_config_sha256, security_config_sha256))
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Three pinned configurations, new output and bounded deadline are required')
    deadline = time.monotonic() + timeout
    def remaining(minimum):
        left = deadline - time.monotonic()
        if left < minimum:
            raise ValueError('Mission security operation deadline exceeded')
        return left
    with tempfile.TemporaryDirectory(prefix='devsecops-security-config-') as temporary:
        temporary = Path(temporary).resolve()
        frozen = {}
        for name, path, expected in [('build', build_config, build_config_sha256),
                                    ('staging', staging_config, staging_config_sha256),
                                    ('security', security_config, security_config_sha256)]:
            frozen[name] = temporary / (name + '.json')
            if _snapshot(path, frozen[name], 64000, deadline=deadline)['sha256'] != expected:
                raise ValueError('Selected operation configuration changed')
        config = _configuration(frozen['security'].read_bytes())
        policy_path, listing_path, database_path = (temporary / name for name in ('policy.json', 'listing.json', 'database.archive'))
        policy_ref = _snapshot(Path(config['policy_path']), policy_path, 256000, deadline=deadline)
        if policy_ref['sha256'] != config['policy_sha256']:
            raise ValueError('Selected security policy changed')
        policy = policy_document(policy_path.read_bytes(), deadline=deadline)
        listing_ref = _snapshot(Path(config['listing_path']), listing_path, 64000, deadline=deadline)
        if listing_ref['sha256'] != policy['database']['listing_sha256']:
            raise ValueError('Selected database listing changed')
        database_listing(listing_path.read_bytes(), policy, deadline=deadline)
        database_ref = _snapshot(Path(config['database_path']), database_path, MAX_DATABASE, deadline=deadline)
        if database_ref['sha256'] != policy['database']['archive_sha256']:
            raise ValueError('Selected offline database changed')
        result = {'schema_version': 1, 'kind': 'mission-qualify-stage-and-assess',
            'decision': 'unknown', 'factory_decision': 'unknown', 'scope': 'qualify-build-stage-scan-policy-only',
            'configuration_sha256': {'build': build_config_sha256, 'staging': staging_config_sha256, 'security': security_config_sha256},
            'started_at': datetime.now(timezone.utc).isoformat(),
            'checks': {'delivery': 'unknown', 'assessment': 'not-started'},
            'downstream': {'sign': 'not-started', 'promote': 'not-started'}, 'gaps': []}
        assessed = None
        with _new_directory(output) as directory, ExitStack() as preparation_context:
            prepared_database = None
            if 'database_preparation_timeout_seconds' in config:
                from devsecops.adapters.prepared_database import prepare_database
                prepared_database = preparation_context.enter_context(prepare_database(
                    database=database_path, database_sha256=policy['database']['archive_sha256'],
                    grype=Path(config['grype_path']), output=output / 'database-preparation',
                    max_database_age_hours=policy['database']['maximum_age_hours'],
                    timeout=min(config['database_preparation_timeout_seconds'], remaining(123)-122)))
                # Preparation does not renew authority or permit altered frozen inputs.
                frozen_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    policy_raw = _read_retained(frozen_fd, policy_path.name, policy_ref, deadline, materialize=True)
                    listing_raw = _read_retained(frozen_fd, listing_path.name, listing_ref, deadline, materialize=True)
                    _read_retained(frozen_fd, database_path.name, database_ref, deadline)
                finally:
                    os.close(frozen_fd)
                policy = policy_document(policy_raw, deadline=deadline)
                database_listing(listing_raw, policy, deadline=deadline)
            delivery_report, staged = qualify_build_and_stage(build_config=frozen['build'], build_config_sha256=build_config_sha256,
                staging_config=frozen['staging'], staging_config_sha256=staging_config_sha256,
                output=output / 'delivery', runtime_root=runtime_root, timeout=min(2700, remaining(122) - 61),
                expected_source_repository=expected_source_repository, expected_source_revision=expected_source_revision)
            result['checks']['delivery'] = delivery_report['decision']
            result['delivery_sha256'] = delivery_report['delivery_sha256']
            if staged is None:
                result['gaps'].append('mission-delivery-incomplete')
            else:
                selected = staged.describe()
                report, assessed = assess_staged_mission(staged=staged,
                    **{name: selected[name] for name in ('run_scope', 'source_commit', 'architecture', 'origin', 'repository')},
                    policy=policy_path, expected_policy_sha256=config['policy_sha256'], database=database_path, listing=listing_path,
                    syft=Path(config['syft_path']), grype=Path(config['grype_path']), output=output / 'security',
                    timeout=min(900, remaining(1)),
                    scanner_timeout_seconds=config.get('scanner_timeout_seconds', 300),
                    prepared_database=prepared_database)
                result['checks']['assessment'] = report['decision']
                result['assessment_sha256'] = report['assessment_sha256']
                if assessed is not None:
                    result['decision'], result['binding'] = 'pass', assessed.describe()
                elif report['decision'] == 'fail':
                    result['decision'] = 'fail'
                    result['gaps'].append('mission-security-policy-failed')
                else:
                    result['gaps'].append('mission-security-assessment-incomplete')
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            result = seal(result, 'security_operation_sha256')
            _verify_output_path(output, directory)
            try:
                _write(directory, 'mission-security-operation.json', result)
                os.fsync(directory)
                _verify_output_path(output, directory)
                if result['decision'] in ('pass', 'fail'):
                    remaining(.001)
                if assessed is not None:
                    bound = result['binding']
                    require_assessed_mission(assessed, **{name: bound[name] for name in
                        ('run_scope', 'source_commit', 'architecture', 'origin', 'repository', 'policy_sha256')})
            except BaseException:
                try:
                    os.unlink('mission-security-operation.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            return result, assessed
