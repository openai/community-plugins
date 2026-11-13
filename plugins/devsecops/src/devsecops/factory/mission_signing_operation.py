"""Explicit same-process delivery, security assessment and local image signing."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import tempfile
import time

from devsecops.adapters.signatures import _public_key, _snapshot
from devsecops.factory.mission_security_operation import qualify_stage_and_assess
from devsecops.factory.mission_signing import (
    require_locally_signed_mission, sign_assessed_mission, signing_configuration, trust_document,
)
from devsecops.factory.mission_staging import _configuration as staging_configuration
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal


def qualify_assess_and_sign(*, build_config, build_config_sha256, staging_config, staging_config_sha256,
                            security_config, security_config_sha256, signing_config, signing_config_sha256,
                            output, runtime_root, timeout=3300,
                            expected_source_repository=None, expected_source_revision=None):
    """Runs actual chain; no stage, assessment or signing reports are imported."""
    pins = (build_config_sha256, staging_config_sha256, security_config_sha256, signing_config_sha256)
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 183 <= timeout <= 3600
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value) for value in pins)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Four pinned configurations, new output and bounded deadline are required')
    deadline = time.monotonic() + timeout
    def remaining(minimum):
        left = deadline - time.monotonic()
        if left < minimum:
            raise ValueError('Local signing operation deadline exceeded')
        return left
    with tempfile.TemporaryDirectory(prefix='devsecops-signing-operation-') as temporary:
        temporary = Path(temporary).resolve()
        frozen = {}
        for name, path, expected in [('build', build_config, build_config_sha256), ('staging', staging_config, staging_config_sha256),
                                    ('security', security_config, security_config_sha256), ('signing', signing_config, signing_config_sha256)]:
            frozen[name] = temporary / (name + '.json')
            if _snapshot(path, frozen[name], 64000, deadline=deadline)['sha256'] != expected:
                raise ValueError('Selected signing operation configuration changed')
        selected = signing_configuration(frozen['signing'].read_bytes())
        trust_path, key_path = temporary / 'trust.json', temporary / 'public-key.pem'
        if _snapshot(Path(selected['trust_policy_path']), trust_path, 64000, deadline=deadline)['sha256'] != selected['trust_policy_sha256']:
            raise ValueError('Selected application signing trust policy changed')
        trust = trust_document(trust_path.read_bytes(), deadline=deadline)
        stage = staging_configuration(frozen['staging'].read_bytes())
        if (trust['origin'], trust['repository']) != (stage['origin'], stage['repository']):
            raise ValueError('Application signing trust differs from selected staging destination')
        if _snapshot(Path(selected['public_key_path']), key_path, 16384, deadline=deadline)['sha256'] != trust['public_key_sha256']:
            raise ValueError('Selected application signing public key changed')
        _public_key(key_path.read_bytes())
        result = {'schema_version': 1, 'kind': 'mission-qualify-assess-and-sign', 'decision': 'unknown',
            'factory_decision': 'unknown', 'scope': 'qualify-build-stage-assess-local-sign-only',
            'configuration_sha256': dict(zip(('build', 'staging', 'security', 'signing'), pins)),
            'started_at': datetime.now(timezone.utc).isoformat(), 'checks': {'assessment': 'unknown', 'local_signing': 'not-started'},
            'downstream': {'publish': 'not-started', 'registry_readback': 'not-started', 'promote': 'not-started'}, 'gaps': []}
        signed = None
        with _new_directory(output) as directory:
            assessment, assessed = qualify_stage_and_assess(build_config=frozen['build'], build_config_sha256=build_config_sha256,
                staging_config=frozen['staging'], staging_config_sha256=staging_config_sha256,
                security_config=frozen['security'], security_config_sha256=security_config_sha256,
                output=output / 'assessment', runtime_root=runtime_root, timeout=min(3300, remaining(183)-61),
                expected_source_repository=expected_source_repository, expected_source_revision=expected_source_revision)
            result['checks']['assessment'] = assessment['decision']
            result['security_operation_sha256'] = assessment['security_operation_sha256']
            if assessed is None:
                result['decision'] = 'fail' if assessment['decision'] == 'fail' else 'unknown'
                result['gaps'].append('mission-assessment-incomplete-or-failed')
            else:
                bound = assessed.describe()
                report, signed = sign_assessed_mission(assessed=assessed,
                    **{name: bound[name] for name in ('run_scope', 'source_commit', 'architecture', 'origin', 'repository', 'policy_sha256')},
                    config=frozen['signing'], expected_config_sha256=signing_config_sha256,
                    output=output / 'signing', timeout=min(300, remaining(2)))
                result['checks']['local_signing'] = report['decision']
                result['signing_sha256'] = report['signing_sha256']
                if signed is not None:
                    result['decision'], result['binding'] = 'pass', signed.describe()
                else:
                    result['gaps'].append('mission-local-signing-incomplete')
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            result = seal(result, 'signing_operation_sha256')
            _verify_output_path(output, directory)
            try:
                _write(directory, 'mission-signing-operation.json', result)
                os.fsync(directory)
                _verify_output_path(output, directory)
                if result['decision'] in ('pass', 'fail'):
                    remaining(.001)
                if signed is not None:
                    bound = result['binding']
                    require_locally_signed_mission(signed, **{name: bound[name] for name in
                        ('run_scope', 'source_commit', 'architecture', 'origin', 'repository', 'policy_sha256', 'trust_policy_sha256')})
            except BaseException:
                try:
                    os.unlink('mission-signing-operation.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            return result, signed
