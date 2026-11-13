"""Current published image to a deterministic local deployment Git proposal."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import difflib
import hashlib
import math
import os
from pathlib import Path
import platform
import re
import tempfile
import time
from weakref import WeakKeyDictionary

from devsecops.adapters.artifacts import _open_directory
from devsecops.adapters.http import _json
from devsecops.adapters.mission_values import replace_mission_image
from devsecops.adapters.signatures import _snapshot
from devsecops.adapters.vulnerability_policy import checkpoint, timestamp
from devsecops.correction import _pointers
from devsecops.factory.mission_publication import require_published_mission
from devsecops.factory.mission_security import _read_retained
from devsecops.profiles import relative_path
from devsecops.resources import strict_load_yaml
from devsecops.render import collect, local_schema_only, tool_identity, validate
from devsecops.schema_catalog import select_schema_set
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import canonical_bytes, seal
from devsecops.snapshot import freeze_commit, git, resolve_ref

ROOT = Path(__file__).resolve().parents[3]
CHART = ROOT / 'demo/software-factory/charts/mission-status'
CHART_FILES = ('Chart.yaml', 'README.md', 'values.schema.json', 'values.yaml', 'templates/_helpers.tpl',
    'templates/deployment.yaml', 'templates/networkpolicy.yaml', 'templates/service.yaml', 'templates/serviceaccount.yaml')
_PROPOSALS = WeakKeyDictionary()
_KEYS = {'repository': ['image', 'repository'], 'digest': ['image', 'digest']}


class DeploymentProposal:
    """Validated local patch only; no remote commit, merge or deployment grant."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('Deployment proposals require a direct current publication and real validation')

    def __reduce__(self):
        raise TypeError('Deployment proposal authority cannot be serialized')

    def describe(self):
        record = _PROPOSALS.get(self)
        if record is None:
            raise ValueError('Unissued deployment proposal authority')
        return deepcopy(record['binding'])


def require_deployment_proposal(value, *, run_scope, source_commit, origin, repository, manifest_digest,
                                deployment_repository, deployment_commit, configuration_sha256):
    record = _PROPOSALS.get(value) if type(value) is DeploymentProposal else None
    if record is None or record['pid'] != os.getpid() or time.monotonic() >= record['expires_monotonic']:
        raise ValueError('Unissued, foreign-process or expired deployment proposal')
    selected = dict(run_scope=run_scope, source_commit=source_commit, origin=origin, repository=repository,
        manifest_digest=manifest_digest, deployment_repository=deployment_repository,
        deployment_commit=deployment_commit, configuration_sha256=configuration_sha256)
    bound = record['binding']
    if any(bound[key] != item for key, item in selected.items()) or datetime.now(timezone.utc) >= timestamp(bound['expires_at']):
        raise ValueError('Deployment proposal differs from selected run, source or image')
    return deepcopy(bound)


def proposal_configuration(raw):
    selected = _json(raw)
    fields = {'schema_version', 'kind', 'deployment_repository', 'deployment_commit', 'chart_path', 'values_path',
        'image_keys', 'release', 'namespace', 'kubernetes_version', 'helm_path', 'helm_sha256',
        'kubeconform_path', 'kubeconform_sha256', 'schemas_path'}
    if (set(selected) != fields or type(selected['schema_version']) is not int or selected['schema_version'] != 1
            or selected['kind'] != 'mission-deployment-proposal-configuration' or selected['image_keys'] != _KEYS
            or not isinstance(selected['deployment_commit'], str) or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', selected['deployment_commit'])):
        raise ValueError('Explicit deployment commit and fixed mission image selectors required')
    for name in ('deployment_repository', 'helm_path', 'kubeconform_path', 'schemas_path'):
        if (not isinstance(selected[name], str) or len(selected[name]) > 4096
                or not Path(selected[name]).is_absolute() or '..' in Path(selected[name]).parts):
            raise ValueError('Explicit absolute source and tool paths required')
    for name in ('helm_sha256', 'kubeconform_sha256'):
        if not isinstance(selected[name], str) or not re.fullmatch(r'[a-f0-9]{64}', selected[name]):
            raise ValueError('Approved executable byte pins required')
    for name in ('chart_path', 'values_path'):
        value = selected[name]
        if not isinstance(value, str) or len(value) > 300 or not re.fullmatch(r'[A-Za-z0-9_./-]+', value):
            raise ValueError('Normalized literal deployment paths required')
        relative_path(value)
    for name, maximum in [('release', 50), ('namespace', 63)]:
        if (not isinstance(selected[name], str) or not 1 <= len(selected[name]) <= maximum
                or not re.fullmatch(r'[a-z0-9](?:[-a-z0-9]*[a-z0-9])?', selected[name])):
            raise ValueError('Explicit supported release and namespace required')
    if not isinstance(selected['kubernetes_version'], str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', selected['kubernetes_version']):
        raise ValueError('Exact qualified Kubernetes schema version required')
    chart, values = Path(selected['chart_path']), Path(selected['values_path'])
    if values.is_relative_to(chart) and values != chart / 'values.yaml':
        raise ValueError('Only chart defaults or a separate values overlay may change')
    repo = Path(selected['deployment_repository'])
    if any(Path(selected[name]).is_relative_to(repo) for name in ('helm_path', 'kubeconform_path', 'schemas_path')):
        raise ValueError('Approved tools and schemas must be outside the deployment checkout')
    return selected


def _patch(before, after, path):
    if before == after:
        return b''
    lines = difflib.unified_diff(before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True),
        fromfile='a/' + path, tofile='b/' + path)
    result = 'diff --git a/' + path + ' b/' + path + '\n'
    for line in lines:
        result += line if line.endswith('\n') else line + '\n\\ No newline at end of file\n'
    raw = result.encode()
    if len(raw) > 256000:
        raise ValueError('Deployment patch exceeds its bound')
    return raw


def _tree_guard(root, expected, deadline):
    """Audit a small private frozen chart/values tree, including its inventory."""
    checkpoint(deadline)
    actual = set()
    for count, path in enumerate(root.rglob('*'), 1):
        checkpoint(deadline)
        if count > 128 or path.is_symlink():
            raise ValueError('Frozen deployment tree changed')
        if path.is_file():
            actual.add(str(path.relative_to(root)))
    if actual != set(expected):
        raise ValueError('Frozen deployment file inventory changed')
    for name, ref in expected.items():
        parent = _open_directory((root / name).parent)
        try:
            _read_retained(parent, Path(name).name, ref, deadline)
        finally:
            os.close(parent)
    checkpoint(deadline)


def propose_deployment_change(*, published, run_scope, source_commit, architecture, origin, repository,
                              policy_sha256, trust_policy_sha256, publication_config_sha256,
                              config, expected_config_sha256, output, timeout=300):
    """Prepare two scalar edits and prove the sole rendered change is the image."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 2 <= timeout <= 600
            or not isinstance(expected_config_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_config_sha256)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Pinned configuration, new output and bounded deadline required')
    deadline = time.monotonic() + timeout
    scope = deepcopy(dict(run_scope=run_scope, source_commit=source_commit, architecture=architecture,
        origin=origin, repository=repository, policy_sha256=policy_sha256, trust_policy_sha256=trust_policy_sha256,
        publication_config_sha256=publication_config_sha256))
    def admission():
        checkpoint(deadline)
        from .ci_publication import VerifiedCIPublication, require_verified_ci_publication
        if type(published) is VerifiedCIPublication:
            return require_verified_ci_publication(published, **scope)
        return require_published_mission(published, **scope)
    source = admission()
    report = {'schema_version': 1, 'kind': 'mission-deployment-proposal', 'decision': 'unknown',
        'factory_decision': 'unknown', 'proposal_authority': 'not-issued', 'scope': 'local-deployment-git-proposal-only',
        'run_scope': scope['run_scope'], 'source_commit': source_commit, 'configuration_sha256': expected_config_sha256,
        'image': {'repository': origin.removeprefix('https://') + '/' + repository, 'digest': source['manifest_digest'],
                  'published_architecture': architecture, 'runtime_architecture': 'not-observed'},
        'started_at': datetime.now(timezone.utc).isoformat(),
        'checks': {'publication': 'pass', 'source': 'unknown', 'chart': 'unknown', 'tools': 'unknown',
                   'values': 'unknown', 'render': 'not-started', 'schema': 'not-started', 'image_only': 'not-started'},
        'downstream': dict.fromkeys(('git_commit', 'merge_request', 'merge', 'ci_execution', 'deployment', 'promotion'), 'not-performed'),
        'gaps': []}
    binding = None
    with _new_directory(output) as directory, tempfile.TemporaryDirectory(prefix='devsecops-deployment-proposal-') as scratch:
        scratch = Path(scratch).resolve()
        retained, trees = {}, []
        def freeze(path, name, limit, expected=None):
            _verify_output_path(output, directory)
            ref = _snapshot(path, output / name, limit, deadline=deadline)
            if expected is not None and ref['sha256'] != expected:
                raise ValueError('Selected deployment input changed')
            retained[name] = ref
            return ref
        def read(name):
            return _read_retained(directory, name, retained[name], deadline, materialize=True)
        def publication_guard():
            admission()
            published_path = Path(source['publication_path'])
            fd = _open_directory(published_path)
            try:
                for name, ref in source['publication_readback'].items():
                    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name):
                        raise ValueError('Publication evidence route changed')
                    _read_retained(fd, name, ref, deadline)
                _verify_output_path(published_path, fd)
            finally:
                os.close(fd)
        def audit():
            publication_guard()
            _verify_output_path(output, directory)
            for name, ref in retained.items():
                _read_retained(directory, name, ref, deadline)
            for root, refs in trees:
                _tree_guard(root, refs, deadline)
            _verify_output_path(output, directory)
        try:
            publication_guard()
            freeze(config, 'proposal-config.json', 64000, expected_config_sha256)
            selected = proposal_configuration(read('proposal-config.json'))
            repo = Path(selected['deployment_repository'])
            if (Path(git(repo, 'rev-parse', '--show-toplevel', limit=8192, deadline=deadline).decode().strip()) != repo
                    or resolve_ref(repo, selected['deployment_commit'], deadline=deadline) != selected['deployment_commit']):
                raise ValueError('Deployment repository or exact commit differs')
            roots = sorted({selected['chart_path'], selected['values_path']})
            before = scratch / 'before'
            before.mkdir(mode=0o700)
            original = freeze_commit(repo, selected['deployment_commit'], roots, before, deadline=deadline)
            expected_files = {selected['chart_path'] + '/' + name for name in CHART_FILES} | {selected['values_path']}
            original_refs = {item['path']: {key: item[key] for key in ('sha256', 'bytes')} for item in original['files']}
            if set(original_refs) != expected_files:
                raise ValueError('Exact shipped chart and selected values file required')
            trees.append((before, original_refs))
            report['deployment'] = {name: selected[name] for name in ('deployment_repository', 'deployment_commit',
                'chart_path', 'values_path', 'release', 'namespace', 'kubernetes_version', 'image_keys')}
            report['source_snapshot'], report['checks']['source'] = original, 'pass'
            recipe = {}
            for index, name in enumerate(CHART_FILES):
                trusted = scratch / ('trusted-chart-' + str(index))
                ref = _snapshot(CHART / name, trusted, 1_000_000, deadline=deadline)
                recipe[name] = ref
                if (selected['chart_path'] + '/' + name != selected['values_path']
                        and original_refs[selected['chart_path'] + '/' + name] != ref):
                    raise ValueError('Deployment chart differs from shipped mission chart')
            report['chart_recipe'], report['checks']['chart'] = recipe, 'pass'
            values = (before / selected['values_path']).read_bytes()
            proposed, changes = replace_mission_image(values, repository=report['image']['repository'],
                digest=source['manifest_digest'], deadline=deadline)
            empty = [changes[key]['before'] == '' for key in ('repository', 'digest')]
            if any(empty) and not all(empty):
                raise ValueError('Initial mission image requires both selected scalars empty')
            uninitialized = all(empty)
            if uninitialized:
                before_doc, after_doc = strict_load_yaml(values.decode()), strict_load_yaml(proposed.decode())
                if len(before_doc) != 1 or len(after_doc) != 1 or set(_pointers(before_doc[0], after_doc[0])) != {
                        '/image/repository', '/image/digest'}:
                    raise ValueError('First image initialization changed unrelated source fields')
            report['before_state'] = 'uninitialized-image' if uninitialized else 'initialized-image'
            retained['values-before.yaml'] = _write(directory, 'values-before.yaml', values.decode('utf-8'))
            retained['values-after.yaml'] = _write(directory, 'values-after.yaml', proposed.decode('utf-8'))
            patch = _patch(values, proposed, selected['values_path'])
            retained['proposal.patch'] = _write(directory, 'proposal.patch', patch.decode('utf-8'))
            report['changes'], report['change_required'], report['checks']['values'] = changes, bool(patch), 'pass'
            after = scratch / 'after'
            after.mkdir(mode=0o700)
            after_refs = deepcopy(original_refs)
            for name in sorted(original_refs):
                path = after / name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                raw = proposed if name == selected['values_path'] else (before / name).read_bytes()
                path.write_bytes(raw)
                path.chmod(0o600)
                after_refs[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
            trees.append((after, after_refs))
            report['candidate_files'] = after_refs
            freeze(ROOT / 'toolchain.lock.json', 'toolchain-lock.json', 64000)
            lock = _json(read('toolchain-lock.json'))
            schema_source, selected_schemas = select_schema_set(lock, selected['kubernetes_version'])
            report['schema_source'] = schema_source
            target_platform = platform.system().lower() + '-' + {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine(), platform.machine())
            tools = {}
            tool_root, tool_refs = scratch / 'tools', {}
            tool_root.mkdir(mode=0o700)
            for name, version_args in [('helm', ['version', '--short']), ('kubeconform', ['-v'])]:
                binary = tool_root / name
                ref = _snapshot(Path(selected[name + '_path']), binary, 200_000_000, deadline=deadline)
                if ref['sha256'] != selected[name + '_sha256']:
                    raise ValueError('Approved executable differs from selected pin')
                binary.chmod(0o500)
                identity = tool_identity(binary, version_args, scratch, deadline=deadline)
                if (identity['sha256'] != ref['sha256']
                        or identity['version'].removeprefix('v').split('+')[0] != lock['tools'][target_platform][name]['version']):
                    raise ValueError('Approved executable version differs from qualified tool contract')
                tools[name] = binary
                tool_refs[name] = ref
                report.setdefault('tools', {})[name] = {**{key: identity[key] for key in ('version', 'sha256', 'receipt')},
                    'authority': 'operator-selected-executable-pin', 'archive_origin': 'not-reverified'}
            trees.append((tool_root, tool_refs))
            schemas = scratch / 'schemas'
            schemas.mkdir(mode=0o700)
            schema_refs = {}
            for item in selected_schemas:
                name = relative_path(item['path'])
                path = schemas / name
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                ref = _snapshot(Path(selected['schemas_path']) / name, path, 8_000_000, deadline=deadline)
                if ref['sha256'] != item['sha256']:
                    raise ValueError('Qualified local schema bytes differ')
                local_schema_only(_json(path.read_bytes()))
                schema_refs[name] = ref
            trees.append((schemas, schema_refs))
            report['schemas'], report['checks']['tools'] = schema_refs, 'pass'
            target = {'name': 'mission', 'type': 'helm', 'path': selected['chart_path'], 'values': [selected['values_path']],
                'release': selected['release'], 'namespace': selected['namespace'], 'kubernetes_version': selected['kubernetes_version'], 'mode': 'upgrade'}
            sides = {}
            if uninitialized:
                # The exact installed chart plus scalar-only replacement proves
                # this first source transition. Empty image values have never
                # represented a deployable or healthy prior workload.
                retained['rendered-before.json'] = _write(directory, 'rendered-before.json',
                    {'resources': {}, 'state': 'uninitialized-image', 'rendered': False})
                report.setdefault('validation', {})['before'] = {
                    'decision': 'not-performed', 'reason': 'uninitialized-image'}
            render_inputs = [('after', after)] if uninitialized else [('before', before), ('after', after)]
            for name, tree in render_inputs:
                audit()
                work = scratch / ('render-' + name)
                work.mkdir(mode=0o700)
                rendered = collect(tree, target, tools['helm'], work, deadline=deadline)
                if not rendered['present'] or rendered['gaps'] or not rendered['records']:
                    raise ValueError('Mission Helm rendering is incomplete')
                checked = validate(rendered['records'], target, tools['kubeconform'], schemas, work, deadline=deadline)
                if checked['decision'] != 'pass' or checked['gaps']:
                    raise ValueError('Mission local schema validation is incomplete or invalid')
                sides[name] = {row['id']: row['body'] for row in rendered['records']}
                retained['rendered-' + name + '.json'] = _write(directory, 'rendered-' + name + '.json', {'resources': sides[name]})
                report.setdefault('validation', {})[name] = {'helm': rendered['receipts'], 'schema': checked,
                    'render_sha256': rendered['render_sha256']}
            report['checks']['render'], report['checks']['schema'] = 'pass', 'pass'
            identity = 'apps/Deployment/' + selected['namespace'] + '/' + selected['release']
            if (identity not in sides['after'] or not uninitialized
                    and set(sides['before']) != set(sides['after'])):
                raise ValueError('Mission rendered inventory changed')
            pointer = '/spec/template/spec/containers/0/image'
            changed = ([{'resource': identity, 'pointer': pointer, 'kind': 'first-image-initialization'}]
                if uninitialized else [])
            for resource in sorted(sides.get('before', {})):
                checkpoint(deadline)
                for field in _pointers(sides['before'][resource], sides['after'][resource]):
                    if resource != identity or field != pointer:
                        raise ValueError('Deployment proposal changes rendered fields other than mission image')
                    changed.append({'resource': resource, 'pointer': field})
            workload = sides['after'][identity]['spec']['template']['spec']
            containers = workload['containers']
            if (len(containers) != 1 or containers[0]['name'] != 'mission-status'
                    or containers[0]['image'] != report['image']['repository'] + '@' + source['manifest_digest']
                    or workload['nodeSelector']['kubernetes.io/arch'] != architecture
                    or bool(changed) != bool(patch)):
                raise ValueError('Rendered mission image or selected node architecture differs')
            report['rendered_changes'], report['checks']['image_only'] = changed, 'pass'
            report['published_evidence'] = {'binding_sha256': hashlib.sha256(canonical_bytes(source)).hexdigest(),
                'artifact_manifest': deepcopy(source['artifact_manifest']), 'publication_configuration_sha256': publication_config_sha256,
                'policy_sha256': policy_sha256, 'trust_policy_sha256': trust_policy_sha256,
                'signer': deepcopy(source['signer']), 'database': deepcopy(source['database']), 'scanner_tools': deepcopy(source['tools']),
                'build_report_sha256': source['build_report_sha256'], 'context_sha256': source['context_sha256'],
                'base': deepcopy(source['base']), 'expires_at': source['expires_at']}
            audit()
            if source.get('execution_mode') == 'controlled-test' or source['signer']['authority'] == 'controlled-test':
                report['gaps'].append('controlled-publication-cannot-issue-deployment-proposal-authority')
            else:
                binding = {'run_scope': scope['run_scope'], 'source_commit': source_commit, 'origin': origin, 'repository': repository,
                    'manifest_digest': source['manifest_digest'], 'architecture': architecture,
                    'deployment_repository': selected['deployment_repository'], 'deployment_commit': selected['deployment_commit'],
                    'configuration_sha256': expected_config_sha256, 'published_evidence': deepcopy(report['published_evidence']),
                    'patch': {'path': str(output / 'proposal.patch'), **retained['proposal.patch']},
                    'values_path': selected['values_path'], 'before': retained['values-before.yaml'], 'after': retained['values-after.yaml'],
                    'source_snapshot_sha256': original['sha256'], 'candidate_files': after_refs,
                    'rendered_before': retained['rendered-before.json'], 'rendered_after': retained['rendered-after.json'],
                    'before_state': report['before_state'],
                    'change_required': bool(patch), 'scope': 'local-deployment-git-proposal-only', 'expires_at': source['expires_at']}
                report['decision'], report['proposal_authority'], report['binding'] = 'pass', 'issued', deepcopy(binding)
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
            binding = None
            report['gaps'].append('deployment-proposal-incomplete-or-changed')
        report['artifacts'] = deepcopy(retained)
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report = seal(report, 'proposal_sha256')
        _verify_output_path(output, directory)
        try:
            _write(directory, 'deployment-proposal.json', report)
            os.fsync(directory)
            _verify_output_path(output, directory)
            if report['checks']['image_only'] == 'pass':
                audit()
        except BaseException:
            try:
                os.unlink('deployment-proposal.json', dir_fd=directory)
            except FileNotFoundError:
                pass
            os.fsync(directory)
            raise
        if binding is None:
            return report, None
        capability = object.__new__(DeploymentProposal)
        _PROPOSALS[capability] = {'pid': os.getpid(), 'binding': deepcopy(binding),
            'expires_monotonic': time.monotonic() + (timestamp(source['expires_at'])-datetime.now(timezone.utc)).total_seconds()}
        return report, capability
