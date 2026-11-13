"""Installed same-process publication, local Git preparation and base observation.

Upstream may write its explicitly selected image registry. The Git side performs
only local plumbing and bounded GETs; it issues no remote write authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import sys
import time

from devsecops.adapters.signatures import _snapshot
from devsecops.adapters.vulnerability_policy import checkpoint
from devsecops.runtime.cli import _new_directory, _verify_output_path
from devsecops.runtime.context import seal, verify_seal
from . import (deployment_commit, deployment_proposal, git_base_possession, git_transport,
               mission_operation, mission_proposal_operation, mission_publication)
from .git_destination import destination_configuration
from .mission_git_invocation import NAMES
from .mission_security import _read_retained
from .native_codex import _package
from .owned_linux import _write_terminal, _withdraw_terminal
from .state import IDENTITY_KEYS, MAX_ARTIFACT_BYTES, _read, _sha

ROOT = Path(__file__).resolve().parents[3]
SCOPE = ('run_scope', 'source_commit', 'deployment_commit',
         'proposal_configuration_sha256', 'configuration_sha256')


def _installed(expected, plugin_id, plugin_version, deadline):
    manifest, members = _package(ROOT, expected, plugin_id, plugin_version, deadline)
    # Bind already loaded engine modules as well as on-disk membership. A
    # checkout dependency imported earlier cannot satisfy this installation.
    for name, module in tuple(sys.modules.items()):
        checkpoint(deadline)
        if name != 'devsecops' and not name.startswith('devsecops.'):
            continue
        location = getattr(module, '__file__', None)
        relative = 'src/' + name.replace('.', '/')
        allowed = (relative+'.py', relative+'/__init__.py')
        if not isinstance(location, str):
            raise ValueError('Loaded engine module has no selected package source')
        path = Path(location).resolve()
        selected = next((item for item in allowed if path == ROOT/item), None)
        if selected is None or members.get(selected) != _sha(_read(path, MAX_ARTIFACT_BYTES)):
            raise ValueError('Loaded engine module is outside the selected installed package')
        checkpoint(deadline)
    checkpoint(deadline)
    return _sha(manifest)


def _child_decision(value, kind, field):
    """Preserve a valid direct child's diagnostic decision, never its authority."""
    if (type(value) is not dict or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1 or value.get('kind') != kind
            or value.get('decision') not in ('pass', 'fail', 'unknown')):
        raise ValueError('Invalid direct child result')
    verify_seal(value, field)
    return value['decision']


def _absolute(path):
    return isinstance(path, Path) and path.is_absolute() and '..' not in path.parts


def qualify_publish_and_prepare_git(*, configurations, expected_run_scope,
        expected_source_repository, expected_source_revision, plugin_id, plugin_version,
        output, runtime_root, timeout=3960):
    """Return (summary, direct transport, direct base observation), never delivery.

    configurations is a closed mapping of the eight named (Path, sha256) pairs.
    The independently supplied six run identities are selections, not restored
    journal authority. A future caller still owes CI identity and journal intent.
    """
    if (type(configurations) is not dict or set(configurations) != set(NAMES)
            or type(expected_run_scope) is not dict or set(expected_run_scope) != IDENTITY_KEYS
            or any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value)
                   for value in expected_run_scope.values())
            or not _absolute(expected_source_repository)
            or not isinstance(expected_source_revision, str)
            or not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', expected_source_revision)
            or not _absolute(output) or not _absolute(runtime_root)
            or type(timeout) not in (int, float) or not math.isfinite(timeout)
            or not 249 <= timeout <= 3960):
        raise ValueError('Eight pinned inputs, exact independent run/source and bounded outputs required')
    selected_inputs = deepcopy(configurations)
    scope = deepcopy(expected_run_scope)
    for item in selected_inputs.values():
        if (type(item) not in (tuple, list) or len(item) != 2 or not _absolute(item[0])
                or not isinstance(item[1], str) or not re.fullmatch('[a-f0-9]{64}', item[1])):
            raise ValueError('Each selection requires one absolute path and SHA-256')
    deadline = time.monotonic() + timeout

    def remaining(minimum):
        checkpoint(deadline)
        left = deadline - time.monotonic()
        if left < minimum:
            raise ValueError('Git composition deadline exhausted')
        return left

    _installed(scope['package_sha256'], plugin_id, plugin_version, deadline)
    # Read the two repository selections before output creation; their retained
    # snapshots are checked against these same pins below before any effect.
    selections = {}
    for name, parser in (('build', mission_operation._configuration),
                         ('proposal', deployment_proposal.proposal_configuration),
                         ('commit', deployment_commit.commit_configuration),
                         ('destination', destination_configuration),
                         ('publication', mission_publication.publication_configuration)):
        raw = _read(selected_inputs[name][0], 64000)
        if _sha(raw) != selected_inputs[name][1]:
            raise ValueError('Selected configuration changed before admission')
        selections[name] = parser(raw)
    build, proposal, commit, destination = (selections[name] for name in
                                          ('build', 'proposal', 'commit', 'destination'))
    if (build['run_scope'] != scope or Path(build['source']['repository']) != expected_source_repository
            or build['source']['revision'] != expected_source_revision
            or any(commit[key] != proposal[key] for key in
                   ('deployment_repository', 'deployment_commit', 'values_path'))
            or commit['proposal_configuration_sha256'] != selected_inputs['proposal'][1]
            or destination['base_commit'] != proposal['deployment_commit']):
        raise ValueError('Run, source, proposal, commit or Git destination selections disagree')
    protected = (expected_source_repository, Path(proposal['deployment_repository']), ROOT)
    for path in (output, runtime_root):
        for root in protected:
            deployment_commit._output_outside_repository(path, root, deadline)
        resolved = path.resolve()
        if any(root.resolve().is_relative_to(resolved) for root in protected):
            raise ValueError('Output cannot contain a selected source or package')
    left, right = output.resolve(), runtime_root.resolve()
    if left.is_relative_to(right) or right.is_relative_to(left):
        raise ValueError('Output and runtime roots must be separate')
    report = {'schema_version': 1, 'kind': 'mission-publish-and-prepare-git',
        'decision': 'unknown', 'factory_decision': 'unknown',
        'scope': 'publication-local-git-preparation-and-base-observation-only',
        'run_scope': scope, 'source_commit': expected_source_revision,
        'configuration_sha256': {name: item[1] for name, item in selected_inputs.items()},
        'checks': dict.fromkeys(('publication_proposal', 'commit', 'transport', 'base'), 'not-started'),
        'git_write': 'not-performed', 'delivery_authority': 'not-issued', 'omission_authorized': False,
        'ci_execution': 'not-established', 'lifecycle_completion': 'not-established',
        'started_at': datetime.now(timezone.utc).isoformat(), 'gaps': []}
    direct_proposal = prepared = transport = observed = None
    proposal_scope = transport_scope = None
    frozen, refs = {}, {}
    with _new_directory(output) as directory, _new_directory(output/'inputs') as inputs:
        for name, (path, pin) in selected_inputs.items():
            frozen[name] = output/'inputs'/(name+'.json')
            refs[name] = _snapshot(path, frozen[name], 64000, deadline=deadline)
            if refs[name]['sha256'] != pin:
                raise ValueError('Configuration changed while freezing selected inputs')

        def audit():
            remaining(.001)
            _verify_output_path(output, directory)
            _verify_output_path(output/'inputs', inputs)
            if set(os.listdir(inputs)) != {name+'.json' for name in NAMES}:
                raise ValueError('Frozen selection membership changed')
            for name, ref in refs.items():
                _read_retained(inputs, name+'.json', ref, deadline)
            _installed(scope['package_sha256'], plugin_id, plugin_version, deadline)
            remaining(.001)

        def authorities():
            audit()
            if direct_proposal is not None:
                deployment_proposal.require_deployment_proposal(direct_proposal, **proposal_scope)
            if prepared is not None:
                deployment_commit.require_prepared_deployment_commit(prepared, **transport_scope)
            if transport is not None:
                git_transport.require_prepared_git_transport(transport, **transport_scope, deadline=deadline)
            if observed is not None:
                git_base_possession.require_observed_git_base(observed, transport=transport,
                    **transport_scope, expected_config_sha256=selected_inputs['destination'][1], deadline=deadline)
            audit()

        stage = 'publication_proposal'
        try:
            audit()
            upstream, direct_proposal = mission_proposal_operation.qualify_publish_and_propose(
                **{name+'_config': frozen[name] for name in NAMES[:6]},
                **{name+'_config_sha256': selected_inputs[name][1] for name in NAMES[:6]},
                expected_source_repository=expected_source_repository,
                expected_source_revision=expected_source_revision,
                output=output/'publication-proposal', runtime_root=runtime_root,
                timeout=min(3600, remaining(249)-3))
            report['checks'][stage] = _child_decision(upstream, 'mission-publish-and-propose', 'proposal_operation_sha256')
            report['proposal_operation_sha256'] = upstream['proposal_operation_sha256']
            if upstream['decision'] != 'pass' or type(direct_proposal) is not deployment_proposal.DeploymentProposal:
                raise ValueError('Direct publication/proposal did not complete')
            bound = direct_proposal.describe()
            publisher = selections['publication']
            proposal_scope = dict(run_scope=scope, source_commit=expected_source_revision,
                origin=publisher['origin'], repository=publisher['repository'], manifest_digest=bound['manifest_digest'],
                deployment_repository=proposal['deployment_repository'], deployment_commit=proposal['deployment_commit'],
                configuration_sha256=selected_inputs['proposal'][1])
            authorities()
            stage = 'commit'
            commit_report, prepared = deployment_commit.prepare_deployment_commit(
                proposal=direct_proposal, **{key: value for key, value in proposal_scope.items() if key != 'configuration_sha256'},
                proposal_configuration_sha256=selected_inputs['proposal'][1], config=frozen['commit'],
                expected_config_sha256=selected_inputs['commit'][1], output=output/'commit',
                timeout=min(120, remaining(3)-2))
            report['checks'][stage] = _child_decision(commit_report, 'mission-local-deployment-commit', 'preparation_sha256')
            if commit_report['decision'] != 'pass' or type(prepared) is not deployment_commit.PreparedDeploymentCommit:
                raise ValueError('Direct commit preparation did not complete')
            transport_scope = dict(run_scope=scope, source_commit=expected_source_revision,
                deployment_commit=proposal['deployment_commit'], proposal_configuration_sha256=selected_inputs['proposal'][1],
                configuration_sha256=selected_inputs['commit'][1])
            authorities()
            stage = 'transport'
            transport_report, transport = git_transport.prepare_git_transport(prepared=prepared, **transport_scope,
                output=output/'transport', timeout=min(120, remaining(2)-1))
            report['checks'][stage] = _child_decision(transport_report, 'mission-local-git-transport', 'preparation_sha256')
            if transport_report['decision'] != 'pass' or type(transport) is not git_transport.PreparedGitTransport:
                raise ValueError('Direct transport preparation did not complete')
            authorities()
            stage = 'base'
            base_report, observed = git_base_possession.verify_git_base_possession(transport=transport, **transport_scope,
                config=frozen['destination'], expected_config_sha256=selected_inputs['destination'][1],
                output=output/'base', timeout=min(120, remaining(1)))
            report['checks'][stage] = _child_decision(base_report, 'mission-git-base-possession', 'observation_sha256')
            if base_report['decision'] != 'pass' or type(observed) is not git_base_possession.ObservedGitBase:
                raise ValueError('Direct base observation did not complete')
            authorities()
            report['new_commit'] = transport.describe()['new_commit']
            report['decision'] = 'pass'
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
            if report['checks'][stage] == 'fail':
                report['decision'] = 'fail'
            else:
                report['checks'][stage] = 'unknown'
            report['gaps'].append(stage+'-incomplete-or-changed')
            direct_proposal = prepared = transport = observed = None
        audit()
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report = seal(report, 'git_operation_sha256')
        identity = None
        try:
            authorities()
            identity = _write_terminal(directory, 'mission-git-operation.json', report)
            os.fsync(directory)
            authorities()
        except BaseException as error:
            if identity is not None:
                _withdraw_terminal(directory, 'mission-git-operation.json', identity, error)
            raise
    return report, transport, observed
