"""Direct publication followed by a validated local deployment proposal."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import tempfile
import time

from devsecops.adapters.artifacts import _open_directory
from devsecops.adapters.http import _json
from devsecops.adapters.signatures import _snapshot
from devsecops.factory.deployment_proposal import (
    DeploymentProposal, proposal_configuration, propose_deployment_change, require_deployment_proposal,
)
from devsecops.factory.mission_publication import (
    PublishedMission, publication_configuration, require_published_mission,
)
from devsecops.factory.mission_publication_operation import qualify_sign_and_publish
from devsecops.factory.mission_security import _read_retained
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal
from devsecops.schema_catalog import select_schema_set

ROOT = Path(__file__).resolve().parents[3]
_SCOPE = ('run_scope', 'source_commit', 'architecture', 'origin', 'repository', 'policy_sha256', 'trust_policy_sha256')


def qualify_publish_and_propose(*, build_config, build_config_sha256, staging_config, staging_config_sha256,
                               security_config, security_config_sha256, signing_config, signing_config_sha256,
                               publication_config, publication_config_sha256, proposal_config, proposal_config_sha256,
                               output, runtime_root, timeout=3500,
                               expected_source_repository=None, expected_source_revision=None):
    """Keep the current publication object in process; never reload report authority.

    Upstream publication can write the explicitly authorized staging registry.
    The final step only prepares a local patch; no Git or controller mutation.
    """
    names = ('build', 'staging', 'security', 'signing', 'publication', 'proposal')
    paths = (build_config, staging_config, security_config, signing_config, publication_config, proposal_config)
    pins = (build_config_sha256, staging_config_sha256, security_config_sha256,
            signing_config_sha256, publication_config_sha256, proposal_config_sha256)
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 246 <= timeout <= 3600
            or any(not isinstance(pin, str) or not re.fullmatch(r'[a-f0-9]{64}', pin) for pin in pins)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Six pinned configurations, new output and bounded deadline required')
    deadline = time.monotonic() + timeout

    def remaining(minimum):
        left = deadline - time.monotonic()
        if left < minimum:
            raise ValueError('Publication and proposal operation deadline exceeded')
        return left

    with tempfile.TemporaryDirectory(prefix='devsecops-proposal-operation-') as temporary:
        temporary = Path(temporary).resolve()
        frozen, frozen_refs = {}, {}
        # Reject changed pins or an invalid proposal selection before the
        # existing publication chain can perform its first registry mutation.
        for name, path, pin in zip(names, paths, pins, strict=True):
            frozen[name] = temporary / (name + '.json')
            frozen_refs[name] = _snapshot(path, frozen[name], 64000, deadline=deadline)
            if frozen_refs[name]['sha256'] != pin:
                raise ValueError('Selected publication/proposal configuration changed')
        def audit_configs():
            fd = _open_directory(temporary)
            try:
                for name, ref in frozen_refs.items():
                    _read_retained(fd, name + '.json', ref, deadline)
                _verify_output_path(temporary, fd)
            finally:
                os.close(fd)
        selected = proposal_configuration(frozen['proposal'].read_bytes())
        publisher = publication_configuration(frozen['publication'].read_bytes())
        lock = _json((ROOT / 'toolchain.lock.json').read_bytes())
        select_schema_set(lock, selected['kubernetes_version'])
        result = {'schema_version': 1, 'kind': 'mission-publish-and-propose', 'decision': 'unknown',
            'factory_decision': 'unknown', 'scope': 'publication-and-local-deployment-proposal-only',
            'configuration_sha256': dict(zip(names, pins, strict=True)),
            'started_at': datetime.now(timezone.utc).isoformat(),
            'checks': {'publication': 'not-started', 'proposal': 'not-started'},
            'downstream': dict.fromkeys(('git_commit', 'merge_request', 'merge', 'deployment', 'promotion'), 'not-performed'),
            'ci_execution': 'not-established', 'gaps': []}
        published = proposal = None
        scope = proposal_scope = None
        stage = 'publication'
        with _new_directory(output) as directory:
            try:
                audit_configs()
                publication, published = qualify_sign_and_publish(
                    **{name + '_config': frozen[name] for name in names[:-1]},
                    **{name + '_config_sha256': pin for name, pin in zip(names[:-1], pins[:-1], strict=True)},
                    output=output / 'publication', runtime_root=runtime_root,
                    timeout=min(3600, remaining(246) - 2),
                    expected_source_repository=expected_source_repository, expected_source_revision=expected_source_revision)
                result['checks']['publication'] = publication['decision']
                result['publication_operation_sha256'] = publication['publication_operation_sha256']
                if publication['decision'] != 'pass' or published is None:
                    result['decision'] = 'fail' if publication['decision'] == 'fail' else 'unknown'
                    result['gaps'].append('publication-incomplete-or-failed')
                else:
                    audit_configs()
                    if type(published) is not PublishedMission:
                        raise ValueError('Direct typed publication authority required')
                    bound = published.describe()
                    scope = {name: bound[name] for name in _SCOPE}
                    scope['publication_config_sha256'] = publication_config_sha256
                    bound = require_published_mission(published, **scope)
                    if ((bound['origin'], bound['repository']) != (publisher['origin'], publisher['repository'])
                            or bound['trust_policy_sha256'] != publisher['trust_policy_sha256']
                            or (expected_source_revision is not None and bound['source_commit'] != expected_source_revision)):
                        raise ValueError('Publication differs from independently selected source or destination')
                    stage = 'proposal'
                    report, proposal = propose_deployment_change(published=published, **scope,
                        config=frozen['proposal'], expected_config_sha256=proposal_config_sha256,
                        output=output / 'proposal', timeout=min(600, remaining(2)))
                    result['checks']['proposal'] = report['decision']
                    result['proposal_sha256'] = report['proposal_sha256']
                    if report['decision'] == 'pass' and proposal is not None:
                        if type(proposal) is not DeploymentProposal:
                            raise ValueError('Direct typed proposal authority required')
                        proposal_scope = {name: bound[name] for name in ('run_scope', 'source_commit', 'origin', 'repository', 'manifest_digest')}
                        proposal_scope.update(deployment_repository=selected['deployment_repository'],
                            deployment_commit=selected['deployment_commit'], configuration_sha256=proposal_config_sha256)
                        result['binding'] = require_deployment_proposal(proposal, **proposal_scope)
                        audit_configs()
                        result['decision'] = 'pass'
                    else:
                        proposal = None
                        result['decision'] = 'fail' if report['decision'] == 'fail' else 'unknown'
                        result['gaps'].append('deployment-proposal-incomplete-or-failed')
            except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
                proposal = None
                result.pop('binding', None)
                result['decision'] = 'unknown'
                result['checks'][stage] = 'unknown'
                result['gaps'].append(stage + '-operation-incomplete-or-changed')
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            result = seal(result, 'proposal_operation_sha256')
            _verify_output_path(output, directory)
            try:
                _write(directory, 'mission-proposal-operation.json', result)
                os.fsync(directory)
                _verify_output_path(output, directory)
                if result['decision'] in ('pass', 'fail'):
                    remaining(.001)
                if proposal is not None:
                    audit_configs()
                    require_published_mission(published, **scope)
                    require_deployment_proposal(proposal, **proposal_scope)
            except BaseException:
                try:
                    os.unlink('mission-proposal-operation.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            return result, proposal
