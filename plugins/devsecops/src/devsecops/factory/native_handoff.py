"""Closed correction handoff composition; no production backend registration.

The scope is prepared from explicit host inputs before requesting native work.
Only the direct opaque origin survives in this process. Saved files are evidence,
never a constructor for either a scope or a native execution origin.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
from weakref import WeakKeyDictionary

from devsecops import correction
from devsecops.engine import _schema_receipt
from devsecops.profiles import load_profile
from devsecops.snapshot import freeze_commit, git, resolve_ref
from . import native_provenance
from .lifecycle import LifecycleEngine, _deadline, _PhaseExpired
from .native_codex import _file_digest
from .state import (StateError, _canonical, _exclusive, _fsync_directory, _json,
                    _private_directory, _read, _safe_path, _sha)
from time import monotonic


_SCOPES = WeakKeyDictionary()
_ISSUER = object()
_HASH = re.compile(r'[a-f0-9]{64}\Z')
_STEPS = {'primary-correction': 'primary', 'replay-correction': 'replay'}


def _path(value):
    if not isinstance(value, Path) or not value.is_absolute():
        raise StateError('Explicit absolute host paths are required')
    return _safe_path(value)


def _pin(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise StateError('An independently selected SHA256 is required')
    return value


def _identity(path):
    _private_directory(path)
    value = path.stat()
    return value.st_dev, value.st_ino


def _inside(path, repository):
    """Containment by ancestors' identities also covers case-insensitive aliases."""
    root = repository.stat()
    root_identity = root.st_dev, root.st_ino
    if path.is_relative_to(repository):
        return True
    for ancestor in (path, *path.parents):
        try:
            observed = ancestor.stat()
        except FileNotFoundError:
            continue
        if (observed.st_dev, observed.st_ino) == root_identity:
            return True
    return False


def _ordinary_repository(repository):
    metadata = _path(repository / '.git')
    if not metadata.is_dir() or (metadata / 'commondir').exists():
        raise StateError('Correction requires ordinary in-worktree Git metadata')
    objects = _path(metadata / 'objects')
    if not objects.is_dir():
        raise StateError('Correction Git object store is unavailable')
    count = 0
    for root, directories, files in os.walk(objects, followlinks=False):
        count += len(directories) + len(files)
        if count > 100_000 or any((Path(root) / name).is_symlink() for name in (*directories, *files)):
            raise StateError('External or oversized Git object storage is unsupported')
    if any((objects / 'info' / name).exists() for name in ('alternates', 'http-alternates')):
        raise StateError('External Git object storage is unsupported')


def _temporary_root(repository):
    # Reject unsafe explicit selections before any probe can write into source.
    # A cold stdlib cache also considers cwd as its last fallback; that unused
    # fallback must not reject an otherwise usable external temporary directory.
    candidates = [os.environ[name] for name in ('TMPDIR', 'TEMP', 'TMP') if os.environ.get(name)]
    if tempfile.tempdir is not None:
        candidates.append(os.fsdecode(tempfile.tempdir))
    if any(_inside(Path(value).resolve(), repository) for value in candidates):
        raise StateError('Ambient temporary storage must be outside the correction repository')
    if tempfile.tempdir is None:
        candidates.extend(('/tmp', '/var/tmp', '/usr/tmp', str(Path.cwd())))
        for value in candidates:
            path = Path(value).resolve()
            if _inside(path, repository):
                raise StateError('Ambient temporary storage must be outside the correction repository')
            try:
                # An explicit dir prevents tempfile from probing later fallbacks.
                # Cache only a root that was actually usable, as gettempdir does.
                with tempfile.TemporaryFile(dir=str(path)) as probe:
                    probe.write(b'\0')
                    probe.flush()
            except OSError:
                continue
            tempfile.tempdir = str(path)
            break
        else:
            raise StateError('No usable external temporary storage is available')
    path = Path(tempfile.gettempdir()).resolve(strict=True)
    if _inside(path, repository):
        raise StateError('Ambient temporary storage must be outside the correction repository')
    info = path.stat()
    return str(path), info.st_dev, info.st_ino


class CorrectionScope:
    """In-process frozen host selection, not a deployment or native authority."""
    __slots__ = ('__weakref__',)

    def __new__(cls, issuer=None):
        if issuer is not _ISSUER:
            raise StateError('Correction scopes require explicit input preparation')
        return super().__new__(cls)

    @property
    def input_artifacts(self):
        selected = _scope(self)
        return dict(selected['paths'])

    def __reduce__(self):
        raise StateError('Correction scopes cannot be restored from reports')


def _scope(value):
    if type(value) is not CorrectionScope or value not in _SCOPES:
        raise StateError('A directly prepared correction scope is required')
    selected = _SCOPES[value]
    if selected['pid'] != os.getpid():
        raise StateError('Correction scopes belong to their preparing process')
    return selected


def _audit(selected, deadline):
    if monotonic() >= deadline or _identity(selected['output']) != selected['directory_identity']:
        raise StateError('Correction input directory changed or deadline expired')
    repository_info = selected['repository'].stat()
    if (repository_info.st_dev, repository_info.st_ino) != selected['repository_identity']:
        raise StateError('Selected correction repository identity changed')
    _ordinary_repository(selected['repository'])
    if _temporary_root(selected['repository']) != selected['temporary_root']:
        raise StateError('Selected ambient temporary storage changed')
    for name, path in selected['paths'].items():
        if _read(path, 16_000_000) != selected['raw'][name]:
            raise StateError('Prepared correction input changed')
    for name, (path, pin) in selected['tools'].items():
        if _file_digest(_path(path), deadline) != pin:
            raise StateError('Selected correction executable changed')
    if _schema_receipt(_path(selected['schemas']))['sha256'] != selected['schemas_sha256']:
        raise StateError('Selected correction schemas changed')
    if monotonic() >= deadline:
        raise StateError('Correction input audit exceeded its deadline')


def prepare_correction_scope(*, repository, base_commit, profile, profile_sha256,
                             contract, contract_sha256, tools, schemas, schemas_sha256,
                             output, timeout=120):
    """Freeze exact selected Git inputs; no model, network or source mutation.

    ``tools`` is a host-supplied mapping from required fixed tool names to
    ``(absolute Path, independently selected SHA256)``. No renderer/validator PATH discovery,
    executable callbacks, configuration commands or model-selected paths occur.
    The returned inputs must be passed directly to RunJournal.request_native.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 300:
        raise StateError('Correction preparation requires a bounded timeout')
    repository, profile, contract, schemas, output = map(_path, (repository, profile, contract, schemas, output))
    if (not isinstance(base_commit, str) or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', base_commit)
            or any(_inside(path, repository) for path in (profile, contract, schemas, output))):
        raise StateError('Frozen revision and external trusted correction inputs are required')
    deadline = monotonic() + timeout
    with _deadline(deadline):
        repository_info = repository.stat()
        repository_identity = repository_info.st_dev, repository_info.st_ino
        _ordinary_repository(repository)
        temporary_root = _temporary_root(repository)
        actual_root = Path(git(repository, 'rev-parse', '--show-toplevel', deadline=deadline).decode().strip())
        if actual_root != repository:
            raise StateError('Correction repository must be its exact Git worktree root')
        profile_raw, contract_raw = _read(profile, 128_000), _read(contract, 128_000)
        if (_sha(profile_raw) != _pin(profile_sha256) or _sha(contract_raw) != _pin(contract_sha256)):
            raise StateError('Correction profile or contract differs from the selected identity')
        if resolve_ref(repository, base_commit, deadline=deadline) != base_commit:
            raise StateError('Correction base is not the selected commit')
        loaded, digest = load_profile(profile, repository, None, base_sha=base_commit)
        if digest != profile_sha256:
            raise StateError('Correction profile changed during preparation')
        approved = _json(contract_raw)
        from jsonschema import Draft202012Validator
        if (not Draft202012Validator(correction.CONTRACT_SCHEMA).is_valid(approved)
                or approved['base_commit'] != base_commit or approved['profile_sha256'] != profile_sha256):
            raise StateError('Correction contract does not bind the selected inputs')
        correction.ScopeValidator(approved['allowed_rendered_changes'])
        required = {'kubeconform'} | {t['type'] for t in loaded['targets'] if t['type'] in ('helm', 'kustomize')}
        if type(tools) is not dict or set(tools) != required:
            raise StateError('Only the exact required correction tools may be selected')
        selected_tools = {}
        for name, value in tools.items():
            if type(value) is not tuple or len(value) != 2:
                raise StateError('Each correction tool requires an explicit path and hash')
            path, pin = _path(value[0]), _pin(value[1])
            if _inside(path, repository) or _file_digest(path, deadline) != pin:
                raise StateError('Correction tool differs from the trusted external selection')
            selected_tools[name] = (path, pin)
        if _schema_receipt(schemas)['sha256'] != _pin(schemas_sha256):
            raise StateError('Correction schemas differ from the trusted selection')
        roots = sorted({root for target in loaded['targets'] for root in
            ([target['source_root']] if target['type'] == 'kustomize' else
             [target['path'], *target.get('values', [])])})
        _private_directory(output, new=True)
        directory_identity = _identity(output)
        source = output / 'source'
        _private_directory(source, new=True)
        manifest = freeze_commit(repository, base_commit, roots, source, deadline=deadline)
        if (not 1 <= len(manifest['files']) <= 59
                or not set(approved['allowed_files']) <= {item['path'] for item in manifest['files']}):
            raise StateError('Correction source inventory exceeds the native handoff or approved scope')
        paths, raw = {}, {}
        def retain(name, path, value):
            _exclusive(path, value)
            paths[name], raw[name] = path, value
        retain('correction-profile', output / 'profile.json', profile_raw)
        retain('correction-contract', output / 'contract.json', contract_raw)
        retain('correction-source-manifest', output / 'source-manifest.json', _canonical(manifest))
        for index, item in enumerate(manifest['files']):
            name, path = f'source-{index:04d}', source / item['path']
            value = _read(path, 16_000_000)
            if len(value) != item['bytes'] or _sha(value) != item['sha256']:
                raise StateError('Frozen correction source changed')
            paths[name], raw[name] = path, value
        retain('correction-selection', output / 'selection.json', _canonical({
            'schema_version': 1, 'scope': 'local-correction-only', 'base_commit': base_commit,
            'source_manifest_sha256': _sha(_canonical(manifest)),
            'profile_sha256': profile_sha256, 'contract_sha256': contract_sha256,
            'source_files': {f'source-{index:04d}': item['path'] for index, item in enumerate(manifest['files'])},
            'output_requirement': 'Return one UTF-8 Git patch against these exact selected source files.'}))
        selected = {'pid': os.getpid(), 'output': output, 'directory_identity': directory_identity,
                    'repository': repository, 'base_commit': base_commit, 'manifest': manifest,
                    'repository_identity': repository_identity,
                    'temporary_root': temporary_root,
                    'tools': selected_tools, 'schemas': schemas, 'schemas_sha256': schemas_sha256,
                    'paths': paths, 'raw': raw}
        _audit(selected, deadline)
    scope = CorrectionScope(_ISSUER)
    _SCOPES[scope] = selected
    return scope


class _CorrectionValidator:
    """Fixed implementation selected here, never supplied by a request or caller."""
    def __init__(self, selected, request, directory, deadline):
        self.selected, self.request, self.directory, self.deadline = selected, request, directory, deadline

    def __call__(self, request, result, artifacts):
        if request != self.request or result['status'] != 'completed':
            raise StateError('Correction result differs from the pending completed handoff')
        patches = [item for item in result['artifacts'] if item['kind'] == 'patch']
        if len(patches) != 1 or set(artifacts) != {patches[0]['name']}:
            raise StateError('Correction handoff requires exactly one patch and no substitute reports')
        patch = artifacts[patches[0]['name']]
        if type(patch) is not bytes or _sha(patch) != patches[0]['sha256'] or len(patch) != patches[0]['bytes']:
            raise StateError('Correction patch bytes changed')
        selected = self.selected
        _audit(selected, self.deadline)
        frozen_patch = self.directory / 'validated.patch'
        _exclusive(frozen_patch, patch)
        args = SimpleNamespace(repo=selected['repository'], base=selected['base_commit'], target=None,
            profile=selected['paths']['correction-profile'], contract=selected['paths']['correction-contract'],
            patch=frozen_patch, schema_location=selected['schemas'],
            **{name: str(selected['tools'][name][0]) if name in selected['tools'] else '/__devsecops_unused_tool__'
               for name in ('helm', 'kustomize', 'kubeconform')})
        rendered = {}
        report, review, checked_patch = correction.validate(args, rendered_artifacts=rendered)
        if (checked_patch != patch or report['base_tree'] != selected['manifest']
                or report['decision'] != 'pass' or review['decision'] != 'pass'
                or report['execution_status'] != 'complete' or report['gaps']
                or report.get('rendered_export', {}).get('status') != 'complete'):
            raise StateError('Independent correction validation is incomplete or rejected')
        _audit(selected, self.deadline)
        correction.write_outputs(report, review, patch, self.directory / 'validation', rendered_artifacts=rendered)
        _audit(selected, self.deadline)
        return ['exact-native-patch-validated', 'original-source-tree-bound',
                'approved-rendered-scope-preserved', 'image-identities-preserved']


class NativeCorrectionCoordinator:
    """Keep direct correction origin beside the existing lifecycle in one process.

    This does not advance the engine or execute another phase. On success the
    caller may call engine.advance() without native_result: acceptance already
    occurred. No missing origin is recreated after a process restart.
    """
    def __init__(self, engine):
        if type(engine) is not LifecycleEngine:
            raise StateError('A concrete canonical lifecycle engine is required')
        self.engine, self.pid, self._origins = engine, os.getpid(), {}

    def origin(self, *, attempt, request_sha256):
        if self.pid != os.getpid():
            raise StateError('Native coordinator belongs to its invoking process')
        key = (self.engine.journal.state['run_id'], attempt, request_sha256)
        if key not in self._origins:
            raise StateError('Direct native origin is unavailable; saved evidence cannot restore it')
        return self._origins[key]

    def execute(self, scope, *, request_path, request_sha256, expected_client_sha256, native_options):
        if self.pid != os.getpid():
            raise StateError('Native coordinator belongs to its invoking process')
        selected = _scope(scope)
        journal = self.engine.journal
        with journal.lifecycle_lock():
            self.engine._check_inputs()
            state = journal.state
            mode = state['lifecycle']['execution_mode']
            if mode != self.engine.mode or state['lifecycle']['driver_id'] != self.engine.driver_id:
                raise StateError('Lifecycle driver or execution mode changed')
            active, phase = state['active_native'], state['lifecycle']['active_phase']
            if (state['active_mutation'] is not None or active is None or phase is None
                    or phase['phase_id'] not in _STEPS or active['step_id'] != phase['phase_id']
                    or not state['environment_reserved'] or state['lifecycle']['abort'] is not None):
                raise StateError('Only an unresolved canonical correction handoff can execute')
            request_path = _path(request_path)
            if request_path != journal.run_dir / active['request']['path'] or request_sha256 != active['request']['sha256']:
                raise StateError('Exact pending native request is required')
            request_raw = _read(request_path, 1_000_000)
            request = _json(request_raw)
            if (_sha(request_raw) != request_sha256 or request['workflow'] != 'prepare-gitops-correction'
                    or request['identities'] != journal.identities):
                raise StateError('Pending native request identity changed')
            frozen = {item['name']: _read(journal.run_dir / item['path'], 16_000_000)
                      for item in request['input_artifacts']}
            if frozen != selected['raw']:
                raise StateError('Native request does not contain the exact prepared correction inputs')
            if (type(native_options) is not dict
                    or not native_provenance._REQUIRED_OPTIONS <= set(native_options)
                    or set(native_options) - native_provenance._REQUIRED_OPTIONS - native_provenance._OPTIONAL_OPTIONS):
                raise StateError('Only explicit bounded native options are supported')
            options = dict(native_options)
            if options['package_sha256'] != journal.identities['package_sha256']:
                raise StateError('Native package differs from the lifecycle input')
            _pin(expected_client_sha256)
            timeout = options.get('total_timeout', 900)
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 900:
                raise StateError('Canonical correction native budget is invalid')
            expiry = datetime.fromisoformat(request['expires_at'].replace('Z', '+00:00'))
            config_expiry = datetime.fromisoformat(self.engine.docs['config']['expires_at'].replace('Z', '+00:00'))
            remaining = (min(expiry, config_expiry) - datetime.now(timezone.utc)).total_seconds()
            if remaining < 1:
                raise StateError('Correction authorization expired')
            deadline = monotonic() + min(timeout, remaining)
            # Creating this exact directory is the durable no-retry claim. Even
            # a crash before model launch must be inspected, never auto-retried.
            directory = journal.run_dir / ('native-correction-' + request_sha256)
            with _deadline(deadline):
                _audit(selected, deadline)
                if directory.exists():
                    raise StateError('Correction invocation already claimed; inspect retained evidence without retry')
                _private_directory(directory, new=True)
                _exclusive(directory / 'intent.json', _canonical({'request_sha256': request_sha256,
                    'run_id': state['run_id'], 'attempt': _STEPS[phase['phase_id']],
                    'execution_mode': mode, 'scope': 'correction-handoff-only'}))
                _fsync_directory(directory)
                _fsync_directory(journal.run_dir)
                validator = _CorrectionValidator(selected, request, directory, deadline)
                options['total_timeout'] = min(timeout, deadline - monotonic())
                try:
                    origin = native_provenance.execute_validated_correction(journal,
                        request_path=request_path, request_sha256=request_sha256,
                        attempt_id=_STEPS[phase['phase_id']], environment=state['environment_id'],
                        expected_client_sha256=_pin(expected_client_sha256), validator=validator,
                        provenance_output=directory / 'origin', native_options=options,
                        _controlled_test=mode == 'controlled-test')
                    _audit(selected, deadline)
                    if (type(origin) is not native_provenance.NativeExecutionOrigin
                            or origin.document['execution_mode'] !=
                            ('controlled-test' if mode == 'controlled-test' else 'direct')):
                        raise StateError('Native origin differs from the lifecycle execution mode')
                except _PhaseExpired:
                    raise StateError('Correction handoff deadline expired; retained attempt cannot be retried') from None
            key = (state['run_id'], _STEPS[phase['phase_id']], request_sha256)
            self._origins[key] = origin
            return origin
