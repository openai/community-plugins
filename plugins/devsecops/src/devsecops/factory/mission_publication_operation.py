"""Explicit direct build, assessment, signing and immutable evidence publication."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import tempfile
import time

from devsecops.adapters.signatures import _public_key, _snapshot
from devsecops.factory.mission_publication import (
    publication_configuration, publish_signed_mission, require_published_mission,
)
from devsecops.factory.mission_signing import (
    require_locally_signed_mission, signing_configuration, trust_document,
)
from devsecops.factory.mission_signing_operation import qualify_assess_and_sign
from devsecops.factory.mission_staging import _configuration as staging_configuration
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal

_SCOPE = ('run_scope', 'source_commit', 'architecture', 'origin', 'repository', 'policy_sha256', 'trust_policy_sha256')


def qualify_sign_and_publish(*, build_config, build_config_sha256, staging_config, staging_config_sha256,
                             security_config, security_config_sha256, signing_config, signing_config_sha256,
                             publication_config, publication_config_sha256, output, runtime_root, timeout=3500,
                             expected_source_repository=None, expected_source_revision=None):
    """Run the same-process chain; no saved signing or publication report input."""
    names = ('build', 'staging', 'security', 'signing', 'publication')
    paths = (build_config, staging_config, security_config, signing_config, publication_config)
    pins = (build_config_sha256, staging_config_sha256, security_config_sha256,
            signing_config_sha256, publication_config_sha256)
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 244 <= timeout <= 3600
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value) for value in pins)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Five pinned configurations, new output and bounded deadline are required')
    deadline = time.monotonic() + timeout

    def remaining(minimum):
        left = deadline - time.monotonic()
        if left < minimum:
            raise ValueError('Evidence publication operation deadline exceeded')
        return left

    with tempfile.TemporaryDirectory(prefix='devsecops-publication-operation-') as temporary:
        temporary = Path(temporary).resolve()
        frozen = {}
        for name, path, expected in zip(names, paths, pins, strict=True):
            frozen[name] = temporary / (name + '.json')
            if _snapshot(path, frozen[name], 64000, deadline=deadline)['sha256'] != expected:
                raise ValueError('Selected publication operation configuration changed')
        signer = signing_configuration(frozen['signing'].read_bytes())
        publisher = publication_configuration(frozen['publication'].read_bytes())
        stage = staging_configuration(frozen['staging'].read_bytes())
        if ((publisher['origin'], publisher['repository']) != (stage['origin'], stage['repository'])
                or publisher['trust_policy_sha256'] != signer['trust_policy_sha256']):
            raise ValueError('Publication destination or trust differs from signing and staging selection')
        # Read both independently selected paths. Equal pins must mean equal actual
        # bytes, not merely equal values claimed by two configuration documents.
        for name, selected in (('signing', signer), ('publication', publisher)):
            trust_path = temporary / (name + '-trust.json')
            if _snapshot(Path(selected['trust_policy_path']), trust_path, 64000, deadline=deadline)['sha256'] != selected['trust_policy_sha256']:
                raise ValueError('Selected publication signer trust policy changed')
            trust = trust_document(trust_path.read_bytes(), deadline=deadline)
            if ((trust['origin'], trust['repository']) != (stage['origin'], stage['repository'])
                    or trust['public_key_sha256'] != publisher['public_key_sha256']):
                raise ValueError('Independent signer trust differs from publication destination or key')
            public_path = temporary / (name + '-public-key.pem')
            if _snapshot(Path(selected['public_key_path']), public_path, 16384, deadline=deadline)['sha256'] != trust['public_key_sha256']:
                raise ValueError('Selected publication public key changed')
            _public_key(public_path.read_bytes())
        result = {'schema_version': 1, 'kind': 'mission-qualify-sign-and-publish', 'decision': 'unknown',
            'factory_decision': 'unknown', 'scope': 'qualify-build-stage-assess-sign-publish-evidence-only',
            'configuration_sha256': dict(zip(names, pins, strict=True)),
            'started_at': datetime.now(timezone.utc).isoformat(),
            'checks': {'local_signing': 'unknown', 'publication': 'not-started'},
            'downstream': {'ci_execution': 'not-established', 'promote': 'not-started'}, 'gaps': []}
        published = None
        with _new_directory(output) as directory:
            signing, signed = qualify_assess_and_sign(
                build_config=frozen['build'], build_config_sha256=build_config_sha256,
                staging_config=frozen['staging'], staging_config_sha256=staging_config_sha256,
                security_config=frozen['security'], security_config_sha256=security_config_sha256,
                signing_config=frozen['signing'], signing_config_sha256=signing_config_sha256,
                output=output / 'signing', runtime_root=runtime_root, timeout=min(3300, remaining(244)-61),
                expected_source_repository=expected_source_repository, expected_source_revision=expected_source_revision)
            result['checks']['local_signing'] = signing['decision']
            result['signing_operation_sha256'] = signing['signing_operation_sha256']
            if signed is None:
                result['decision'] = 'fail' if signing['decision'] == 'fail' else 'unknown'
                result['gaps'].append('mission-signing-incomplete-or-failed')
            else:
                bound = signed.describe()
                scope = {name: bound[name] for name in _SCOPE}
                # This is a typed admission, not acceptance of describe()/JSON.
                require_locally_signed_mission(signed, **scope)
                if ((scope['origin'], scope['repository']) != (publisher['origin'], publisher['repository'])
                        or scope['trust_policy_sha256'] != publisher['trust_policy_sha256']):
                    raise ValueError('Direct signing authority differs from publication selection')
                report, published = publish_signed_mission(signed=signed, **scope,
                    config=frozen['publication'], expected_config_sha256=publication_config_sha256,
                    output=output / 'publication', timeout=min(600, remaining(2)))
                result['checks']['publication'] = report['decision']
                result['publication_sha256'] = report['publication_sha256']
                if published is not None:
                    binding = require_published_mission(published, **scope,
                        publication_config_sha256=publication_config_sha256)
                    result['decision'], result['binding'] = 'pass', binding
                else:
                    result['gaps'].append('mission-evidence-publication-incomplete')
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            result = seal(result, 'publication_operation_sha256')
            _verify_output_path(output, directory)
            try:
                _write(directory, 'mission-publication-operation.json', result)
                os.fsync(directory)
                _verify_output_path(output, directory)
                if result['decision'] in ('pass', 'fail'):
                    remaining(.001)
                if published is not None:
                    require_published_mission(published, **scope,
                        publication_config_sha256=publication_config_sha256)
            except BaseException:
                try:
                    os.unlink('mission-publication-operation.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            return result, published
