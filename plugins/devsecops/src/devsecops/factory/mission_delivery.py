"""Explicit same-process qualification, build and staging composition."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import tempfile
import time

from devsecops.adapters.signatures import _snapshot
from devsecops.factory.mission_operation import qualify_and_build
from devsecops.factory.mission_staging import _configuration, stage_mission, require_staged_mission
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal


def qualify_build_and_stage(*, build_config, build_config_sha256, staging_config, staging_config_sha256,
                           output, runtime_root, timeout=2400,
                           expected_source_repository=None, expected_source_revision=None):
    """Stage only the direct build result; existing reports are never inputs."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 61 <= timeout <= 2700
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
                   for value in (build_config_sha256, staging_config_sha256))
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Separate pinned build/staging configurations and bounded output are required')
    deadline = time.monotonic() + timeout
    def remaining(minimum):
        left = deadline - time.monotonic()
        if left < minimum:
            raise ValueError('Mission delivery deadline exceeded')
        return left
    with tempfile.TemporaryDirectory(prefix='devsecops-delivery-config-') as temporary:
        temporary = Path(temporary).resolve()
        frozen_build, frozen_stage = temporary / 'build.json', temporary / 'staging.json'
        for source, frozen, expected in [(build_config, frozen_build, build_config_sha256),
                                         (staging_config, frozen_stage, staging_config_sha256)]:
            if _snapshot(source, frozen, 64000, deadline=deadline)['sha256'] != expected:
                raise ValueError('Selected delivery configuration changed')
        _configuration(frozen_stage.read_bytes())
        result = {'schema_version': 1, 'kind': 'mission-qualify-build-and-stage',
            'decision': 'unknown', 'factory_decision': 'unknown', 'scope': 'qualify-build-stage-only',
            'build_configuration_sha256': build_config_sha256, 'staging_configuration_sha256': staging_config_sha256,
            'started_at': datetime.now(timezone.utc).isoformat(), 'checks': {'build': 'unknown', 'stage': 'not-started'},
            'downstream': {'scan': 'not-started', 'sign': 'not-started', 'promote': 'not-started'}, 'gaps': []}
        staged = None
        with _new_directory(output) as directory:
            build_report, built = qualify_and_build(config=frozen_build, expected_config_sha256=build_config_sha256,
                output=output / 'build', runtime_root=runtime_root, timeout=min(1800, remaining(61)-1),
                expected_source_repository=expected_source_repository, expected_source_revision=expected_source_revision)
            result['checks']['build'] = build_report['decision']
            result['build_operation_sha256'] = build_report['operation_sha256']
            if built is None:
                result['gaps'].append('mission-build-incomplete')
            else:
                bound = built.describe()
                # stage_mission itself validates the real capability before any
                # network action. describe() supplies selection, never authority.
                stage_report, staged = stage_mission(built=built, run_scope=bound['run_scope'],
                    source_commit=bound['source_commit'], architecture=bound['architecture'],
                    config=frozen_stage, expected_config_sha256=staging_config_sha256,
                    output=output / 'staging', timeout=min(900, remaining(1)))
                result['checks']['stage'] = stage_report['decision']
                result['staging_sha256'] = stage_report['stage_sha256']
                if staged is None:
                    result['gaps'].append('mission-staging-incomplete')
                else:
                    result['decision'] = 'pass'
                    result['binding'] = staged.describe()
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            result = seal(result, 'delivery_sha256')
            _verify_output_path(output, directory)
            try:
                _write(directory, 'mission-delivery.json', result)
                os.fsync(directory)
                _verify_output_path(output, directory)
                if staged is not None:
                    remaining(.001)
                    selected = result['binding']
                    require_staged_mission(staged, **{name: selected[name] for name in
                        ('run_scope', 'source_commit', 'architecture', 'origin', 'repository')})
            except BaseException:
                try:
                    os.unlink('mission-delivery.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            return result, staged
