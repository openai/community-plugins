"""Fixed native report handoffs; actual execution is not runtime authority.

Only opaque host-prepared scopes select inputs. No saved report, callback, model
instruction, or configuration string can select a validator or restore an origin.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import tempfile
from time import monotonic
from types import MappingProxyType
from weakref import WeakKeyDictionary
from uuid import UUID

from devsecops.contracts import validate_report
from devsecops.engine import _schema_receipt
from devsecops.profiles import load_profile
from devsecops.runtime.context import validate, verify_seal
from devsecops.runtime.diagnosis import DIAGNOSIS_SCHEMA
from devsecops.runtime.recovery import BASE_ARTIFACTS, RECOVERY_CONTRACT_SCHEMA, RECOVERY_SCHEMA, verify_recovery
from devsecops.snapshot import freeze_commit, resolve_ref
from . import native_codex, native_provenance
from .lifecycle import LifecycleEngine, _deadline, _PhaseExpired
from .native_handoff import _identity, _inside, _ordinary_repository, _path, _pin, _temporary_root
from .native_session import NativeCodexSelection
from .state import (MAX_ARTIFACT_BYTES, MAX_ARTIFACT_TOTAL_BYTES, MAX_DOCUMENT_BYTES, StateError,
                    _canonical, _exclusive, _fsync_directory, _json, _private_directory, _read, _sha, _time)

_KINDS = {'review-kubernetes-change': ('review', 'review-report', 'report.json'),
          'diagnose-gitops-deployment': ('diagnose-gitops', 'diagnosis', 'diagnosis.json'),
          'verify-gitops-recovery': ('verify-gitops-recovery', 'recovery-report', 'recovery.json')}
_STEPS = {f'{attempt}-{suffix}': workflow for attempt in ('primary', 'replay')
          for suffix, workflow in (('review', 'review-kubernetes-change'),
                                  ('diagnose', 'diagnose-gitops-deployment'),
                                  ('recovery', 'verify-gitops-recovery'))}
_SCOPES, _REPORTS = WeakKeyDictionary(), WeakKeyDictionary()
_ISSUER = object()


def _remaining(deadline):
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or monotonic() >= deadline:
        raise StateError('Native report deadline expired')
    return deadline - monotonic()


class NativeReportScope:
    __slots__ = ('__weakref__',)

    def __new__(cls, issuer=None):
        if issuer is not _ISSUER:
            raise StateError('Report scopes require direct host preparation')
        return super().__new__(cls)

    @property
    def input_artifacts(self):
        return MappingProxyType(dict(_scope(self)['paths']))

    def __reduce__(self):
        raise StateError('Report scopes cannot be restored from documents')


class ValidatedNativeReport:
    __slots__ = ('__weakref__',)

    def __new__(cls, issuer=None):
        if issuer is not _ISSUER:
            raise StateError('Report execution requires direct invocation and validation')
        return super().__new__(cls)

    @property
    def document(self):
        return deepcopy(_issued(self)['document'])

    def __reduce__(self):
        raise StateError('Report executions cannot be restored from documents')


def _issued(value):
    if type(value) is not ValidatedNativeReport or value not in _REPORTS or _REPORTS[value]['pid'] != os.getpid():
        raise StateError('Original report execution from this process is required')
    return _REPORTS[value]


def native_report_artifacts(value):
    """Return only the bounded original accepted report/execution file set."""
    files = _issued(value)['files']
    if not 1 <= len(files) <= 32 or sum(map(len, files.values())) > 128_000_000:
        raise StateError('Native report artifact set exceeds its bound')
    for path, raw in files.items():
        if _read(path, 64_000_000) != raw:
            raise StateError('Original native report artifact bytes changed')
    return tuple(sorted(files))


def _scope(value):
    if type(value) is not NativeReportScope or value not in _SCOPES or _SCOPES[value]['pid'] != os.getpid():
        raise StateError('Original prepared report scope from this process is required')
    return _SCOPES[value]


def _preparation_budget(deadline):
    if _remaining(deadline) > 300:
        raise StateError('Report scope preparation is bounded to 300 seconds')


def _freeze(workflow, inputs, *, output, deadline, options=None):
    _preparation_budget(deadline)
    output = _path(output)
    if type(inputs) is not dict or len(inputs) > 63:
        raise StateError('Report input count exceeds the handoff bound')
    raw, total = {}, 0
    for name, path in inputs.items():
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,119}', name):
            raise StateError('Fixed report input name is invalid')
        _remaining(deadline)
        source = _path(path)
        raw[name] = _read(source, min(MAX_ARTIFACT_BYTES, MAX_ARTIFACT_TOTAL_BYTES - total))
        total += len(raw[name])
    _private_directory(output, new=True)
    paths = {}
    for name, content in raw.items():
        _remaining(deadline)
        paths[name] = output / name
        _exclusive(paths[name], content)
    selected = {'pid': os.getpid(), 'workflow': workflow, 'output': output,
                'directory_identity': _identity(output), 'paths': paths, 'raw': raw,
                'options': options or {}}
    return selected


def _finish_scope(selected, arguments, deadline):
    selection = {'schema_version': 1, 'kind': 'native-report-selection',
                 'workflow': selected['workflow'], 'arguments': arguments,
                 'output_requirement': 'Return the actual engine structured report with its required artifact kind. '
                 'An optional report.html companion must be supporting-evidence. '
                 'Completed execution may contain a fail or unknown report; preserve its gaps and claims.',
                 'authority': 'offline-inputs-only'}
    if selected['workflow'] == 'review-kubernetes-change':
        options = selected['options']
        selection['review_binding'] = {'source_manifests': options['manifests'],
            'tool_sha256': {name: pin for name, (_, pin) in options['tools'].items()},
            'schemas_sha256': options['schemas_sha256']}
    content = _canonical(selection)
    if sum(map(len, selected['raw'].values())) + len(content) > MAX_ARTIFACT_TOTAL_BYTES:
        raise StateError('Prepared report inputs exceed the aggregate handoff bound')
    selected['raw']['report-selection'] = content
    selected['paths']['report-selection'] = selected['output'] / 'report-selection'
    _exclusive(selected['paths']['report-selection'], content)
    selected['arguments'] = tuple(arguments)
    _remaining(deadline)
    _fsync_directory(selected['output'])
    result = NativeReportScope(_ISSUER)
    _SCOPES[result] = selected
    return result


def prepare_diagnosis_scope(*, baseline, fault, output, deadline):
    selected = _freeze('diagnose-gitops-deployment', {'baseline': baseline, 'fault': fault},
                       output=output, deadline=deadline)
    return _finish_scope(selected, [item for name in ('baseline', 'fault')
                                   for item in ('--' + name, str(selected['paths'][name]))], deadline)


def prepare_recovery_scope(*, baseline, fault, recovered, contract, contract_sha256,
                           evidence, output, deadline):
    _preparation_budget(deadline)
    contract_raw = _read(_path(contract), 128_000)
    if _sha(contract_raw) != _pin(contract_sha256):
        raise StateError('Recovery contract differs from the selected pin')
    approved = _json(contract_raw)
    validate(approved, RECOVERY_CONTRACT_SCHEMA)
    allowed = set(BASE_ARTIFACTS) | {'http:' + row['id'] for row in approved['probe_cases']}
    if type(evidence) is not dict or not set(evidence) <= allowed:
        raise StateError('Recovery evidence names are unsupported')
    selected = _freeze('verify-gitops-recovery',
        {'baseline': baseline, 'fault': fault, 'recovered': recovered, 'contract': contract,
         **{'evidence-' + str(index): path for index, (_, path) in enumerate(sorted(evidence.items()))}},
        output=output, deadline=deadline, options={'evidence_names': tuple(sorted(evidence))})
    if selected['raw']['contract'] != contract_raw:
        raise StateError('Recovery contract changed during preparation')
    args = [item for name in ('baseline', 'fault', 'recovered', 'contract')
            for item in ('--' + name, str(selected['paths'][name]))]
    for index, name in enumerate(sorted(evidence)):
        args += ['--evidence', name + '=' + str(selected['paths']['evidence-' + str(index)])]
    return _finish_scope(selected, args, deadline)


def _source_manifests(options, destination, deadline):
    result = {}
    for side in ('base', 'head'):
        path = destination / side
        path.mkdir(mode=0o700)
        result[side] = freeze_commit(options['repository'], options[side], options['roots'], path, deadline=deadline)
    return result


def prepare_review_scope(*, repository, base_commit, head_commit, profile, profile_sha256,
                         tools, schemas, schemas_sha256, output, deadline):
    _preparation_budget(deadline)
    repository, profile, schemas, output = map(_path, (repository, profile, schemas, output))
    if (any(_inside(path, repository) for path in (profile, schemas, output))
            or any(not isinstance(ref, str) or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', ref)
                   for ref in (base_commit, head_commit))):
        raise StateError('Exact local commits and external review inputs are required')
    _ordinary_repository(repository)
    temporary = _temporary_root(repository)
    if any(resolve_ref(repository, ref, deadline=deadline) != ref for ref in (base_commit, head_commit)):
        raise StateError('Review revisions changed')
    loaded, digest = load_profile(profile, repository, None, base_sha=base_commit)
    if digest != _pin(profile_sha256):
        raise StateError('Review profile differs from the selected pin')
    required = {'kubeconform'} | {row['type'] for row in loaded['targets'] if row['type'] in ('helm', 'kustomize')}
    if type(tools) is not dict or set(tools) != required:
        raise StateError('Exact required review tools must be explicitly pinned')
    pinned = {}
    for name, value in tools.items():
        if not isinstance(value, tuple) or len(value) != 2:
            raise StateError('Explicit tool path and SHA256 pairs are required')
        path, pin = _path(value[0]), _pin(value[1])
        if _inside(path, repository) or native_codex._file_digest(path, deadline) != pin:
            raise StateError('Review tool identity is invalid')
        pinned[name] = (path, pin)
    if _schema_receipt(schemas)['sha256'] != _pin(schemas_sha256):
        raise StateError('Review schema identity is invalid')
    options = {'repository': repository, 'repository_identity': (repository.stat().st_dev, repository.stat().st_ino),
               'base': base_commit, 'head': head_commit, 'tools': pinned, 'schemas': schemas,
               'schemas_sha256': schemas_sha256, 'temporary_root': temporary,
               'roots': sorted({root for row in loaded['targets'] for root in
                    ([row['source_root']] if row['type'] == 'kustomize' else [row['path'], *row.get('values', [])])})}
    with tempfile.TemporaryDirectory(dir=temporary[0], prefix='native-review-input-') as scratch:
        root = Path(scratch)
        manifests = _source_manifests(options, root, deadline)
        inputs = {'review-profile': profile}
        for side in ('base', 'head'):
            inputs.update({f'{side}-source-{index:04d}': root / side / item['path']
                           for index, item in enumerate(manifests[side]['files'])})
        selected = _freeze('review-kubernetes-change', inputs, output=output, deadline=deadline, options=options)
    if _sha(selected['raw']['review-profile']) != profile_sha256:
        raise StateError('Review profile changed during preparation')
    options['manifests'] = manifests
    args = ['--repo', str(repository), '--base', base_commit, '--head', head_commit,
            '--profile', str(selected['paths']['review-profile']), '--schema-location', str(schemas)]
    for name in ('helm', 'kustomize', 'kubeconform'):
        args += ['--' + name, str(pinned[name][0]) if name in pinned else '/__devsecops_unused_tool__']
    return _finish_scope(selected, args, deadline)


def _audit(selected, deadline):
    _remaining(deadline)
    if _identity(selected['output']) != selected['directory_identity']:
        raise StateError('Prepared report directory changed')
    for name, path in selected['paths'].items():
        if _read(_path(path), MAX_ARTIFACT_BYTES) != selected['raw'][name]:
            raise StateError('Prepared report input changed')
        _remaining(deadline)
    if selected['workflow'] == 'review-kubernetes-change':
        options = selected['options']
        repo = options['repository']
        if (repo.stat().st_dev, repo.stat().st_ino) != options['repository_identity']:
            raise StateError('Review repository identity changed')
        _ordinary_repository(repo)
        if _temporary_root(repo) != options['temporary_root']:
            raise StateError('Review temporary storage changed')
        for path, pin in options['tools'].values():
            if native_codex._file_digest(_path(path), deadline) != pin:
                raise StateError('Review tool changed')
        if _schema_receipt(_path(options['schemas']))['sha256'] != options['schemas_sha256']:
            raise StateError('Review schemas changed')
        with tempfile.TemporaryDirectory(dir=options['temporary_root'][0], prefix='native-review-audit-') as scratch:
            if _source_manifests(options, Path(scratch), deadline) != options['manifests']:
                raise StateError('Review source manifests changed')
    _remaining(deadline)


def _receipt_semantics(value):
    # Raw child process stdout/elapsed hashes are retained observations, not an
    # assertion that the independent process emitted identical scratch paths.
    allowed = {'tool', 'exit_code', 'duration_ms', 'stdout_sha256', 'stderr_sha256',
               'stdout_bytes', 'stderr_bytes', 'operation', 'side', 'target', 'attempt'}
    if (not isinstance(value, dict) or set(value) - allowed
            or not {'tool', 'exit_code', 'duration_ms', 'stdout_sha256', 'stderr_sha256',
                    'stdout_bytes', 'stderr_bytes'} <= set(value)):
        raise StateError('Unsupported process receipt shape')
    for name in ('stdout_sha256', 'stderr_sha256'):
        _pin(value[name])
    if any(type(value[name]) is not int or not 0 <= value[name] <= limit for name, limit in
           (('duration_ms', 900_000), ('stdout_bytes', MAX_ARTIFACT_BYTES), ('stderr_bytes', MAX_ARTIFACT_BYTES))) or type(value['exit_code']) is not int:
        raise StateError('Unsupported process receipt bounds')
    result = {name: value[name] for name in ('tool', 'exit_code', 'operation', 'side', 'target', 'attempt') if name in value}
    if value.get('operation') in ('helm-template', 'kustomize-build'):
        result.update(stdout_sha256=value['stdout_sha256'], stdout_bytes=value['stdout_bytes'])
    return result


def _semantics(workflow, report):
    value = deepcopy(report)
    if workflow == 'diagnose-gitops-deployment':
        validate(value, DIAGNOSIS_SCHEMA)
        verify_seal(value, 'diagnosis_sha256')
        return value  # No observation clock exists in diagnosis.
    if workflow == 'verify-gitops-recovery':
        validate(value, RECOVERY_SCHEMA)
        verify_seal(value, 'recovery_sha256')
        value.pop('observed_at')
        value.pop('recovery_sha256')
        return value
    validate_report(value)
    if str(UUID(value['run_id'])) != value['run_id'] or _time(value['started_at']) > _time(value['finished_at']):
        raise StateError('Review observation metadata is invalid')
    for key in ('run_id', 'started_at', 'finished_at'):
        value.pop(key)
    for tool in value['toolchain'].values():
        if 'receipt' in tool:
            tool['receipt'] = _receipt_semantics(tool['receipt'])
    for target in value['targets']:
        target.pop('policy_evaluated_at', None)
        target['receipts'] = [_receipt_semantics(item) for item in target['receipts']]
        for validation in target['validation']:
            validation['results'] = sorted(validation['results'], key=_canonical)
    return value


def _current_recovery(selected):
    paths = selected['paths']
    evidence = {name: paths['evidence-' + str(index)]
                for index, name in enumerate(selected['options']['evidence_names'])}
    return verify_recovery(paths['baseline'], paths['fault'], paths['recovered'],
                           contract=paths['contract'], evidence=evidence)


def _fresh(selected, report):
    workflow = selected['workflow']
    if workflow == 'verify-gitops-recovery':
        if _semantics(workflow, _current_recovery(selected)) != _semantics(workflow, report):
            raise StateError('Recovery freshness or evidence semantics changed before acceptance')
    elif workflow == 'review-kubernetes-change':
        # Exception validity is calendar-day based. Conservatively refuse any
        # crossed UTC evaluation date instead of accepting a stale exception.
        today = datetime.now(timezone.utc).date().isoformat()
        if any(target['policy_evaluated_at'] != today for target in report['targets']):
            raise StateError('Review policy evaluation date changed before acceptance')


def _environment(temporary):
    return {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C',
            'TMPDIR': str(temporary), 'PYTHONDONTWRITEBYTECODE': '1',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1'}


def _replay(selected, selection, directory, deadline, cancel):
    temporary = directory / 'tmp'
    _private_directory(temporary, new=True)
    command, _, filename = _KINDS[selected['workflow']]
    output = directory / 'report'
    argv = [str(selection.engine_python), '-I', '-B',
            str(selection.installed_root / 'scripts/devsecops_review.py'), command,
            *selected['arguments'], '--output', str(output)]
    process = native_codex._process(argv, cwd=directory, stdin=b'', deadline=deadline,
                                   limit=1_000_000, environ=_environment(temporary), cancel=cancel)
    _exclusive(directory / 'replay-stdout.log', process['stdout'])
    _exclusive(directory / 'replay-stderr.log', process['stderr'])
    _exclusive(directory / 'replay-invocation.json', _canonical({'argv': argv,
                'environment_names': sorted(_environment(temporary)), 'exit_code': process['returncode']}))
    if process['reason'] or process['returncode'] not in (0, 1, 2):
        raise StateError('Independent report replay did not complete')
    report = _json(_read(output / filename, MAX_ARTIFACT_BYTES))
    _semantics(selected['workflow'], report)
    if process['returncode'] != {'pass': 0, 'fail': 1, 'unknown': 2}[report['decision']]:
        raise StateError('Independent report decision differs from the process result')
    return report


def _joined(journal, request, result, report, frozen):
    declared, recorded = result['artifacts'], report['artifacts']
    if (len({row['path'] for row in declared}) != len(declared)
            or len({row['name'] for row in declared}) != len(declared)
            or len({row['path'] for row in recorded}) != len(recorded)
            or {row['path'] for row in declared} != {row['path'] for row in recorded}):
        raise StateError('Native report artifact membership is ambiguous')
    joined = {}
    for item in declared:
        observed = next(row for row in recorded if row['path'] == item['path'])
        raw = _read(native_codex._relative(journal.run_dir, item['path']), MAX_ARTIFACT_BYTES)
        if (not Path(item['path']).is_relative_to(request['output_directory'])
                or any(item[key] != observed[key] for key in ('path', 'sha256', 'bytes'))
                or observed['retained'] not in frozen or frozen[observed['retained']] != raw
                or item['sha256'] != _sha(raw) or item['bytes'] != len(raw)):
            raise StateError('Native report artifact bytes or retained bindings changed')
        joined[item['name']] = raw
    return joined


def _document_artifacts(workflow, result, artifacts):
    kind = _KINDS[workflow][1]
    reports = [row for row in result['artifacts'] if row['kind'] == kind]
    if len(reports) != 1 or len(result['artifacts']) > 2:
        raise StateError('Exactly one structured report and at most one HTML companion are supported')
    report = _json(artifacts[reports[0]['name']])
    _semantics(workflow, report)
    companions = [row for row in result['artifacts'] if row is not reports[0]]
    if companions:
        from devsecops import presentation
        from devsecops.runtime import presentation as runtime_presentation
        row = companions[0]
        html = (presentation.render_html(report) if workflow == 'review-kubernetes-change'
                else runtime_presentation.render_html(report, _KINDS[workflow][2])).encode()
        if row['kind'] != 'supporting-evidence' or artifacts[row['name']] != html:
            raise StateError('Report companion is not the exact deterministic HTML presentation')
    return report, reports[0]


class NativeReportCoordinator:
    """Invoke and validate fixed report workflows without advancing the lifecycle."""
    def __init__(self, engine, selection):
        if type(engine) is not LifecycleEngine or type(selection) is not NativeCodexSelection:
            raise StateError('Concrete lifecycle and explicit native selection are required')
        selection.__post_init__()
        if selection.package_sha256 != engine.journal.identities['package_sha256']:
            raise StateError('Report package differs from lifecycle')
        self.engine, self.selection, self.pid = engine, selection, os.getpid()
        self._reports = {}

    def report(self, *, step_id, request_sha256):
        if self.pid != os.getpid() or (step_id, request_sha256) not in self._reports:
            raise StateError('Original native report is unavailable; saved documents cannot restore it')
        value = self._reports[(step_id, request_sha256)]
        issued = _issued(value)
        self.engine._check_inputs()
        for path, raw in issued['files'].items():
            if _read(path, 64_000_000) != raw:
                raise StateError('Accepted report evidence changed')
        return value

    def execute(self, scope, *, cancel=None):
        if self.pid != os.getpid():
            raise StateError('Report coordinator belongs to its invoking process')
        selected, journal, selection = _scope(scope), self.engine.journal, self.selection
        with journal.lifecycle_lock():
            self.engine._check_inputs()
            state = journal.state
            active, phase = state['active_native'], state['lifecycle']['active_phase']
            if (active is None or phase is None or not state['environment_reserved']
                    or state['active_mutation'] is not None or state['lifecycle']['abort'] is not None
                    or active['step_id'] != phase['phase_id'] or active['step_id'] not in _STEPS
                    or _STEPS[active['step_id']] != active['workflow'] or active['workflow'] != selected['workflow']
                    or state['lifecycle']['execution_mode'] != self.engine.mode
                    or state['lifecycle']['driver_id'] != self.engine.driver_id):
                raise StateError('Exact pending canonical report handoff is required')
            request_path = journal.run_dir / active['request']['path']
            request_raw = _read(request_path, MAX_DOCUMENT_BYTES)
            request = _json(request_raw)
            if _sha(request_raw) != active['request']['sha256'] or request['identities'] != journal.identities:
                raise StateError('Native report request identity changed')
            deadline = monotonic() + min(selection.total_timeout,
                (_time(request['expires_at']) - datetime.now(timezone.utc)).total_seconds(),
                (_time(self.engine.docs['config']['expires_at']) - datetime.now(timezone.utc)).total_seconds())
            if _remaining(deadline) < 3:
                raise StateError('Report invocation requires time for independent validation')
            directory = journal.run_dir / ('native-report-' + active['request']['sha256'])
            if directory.exists() or directory.is_symlink():
                raise StateError('Report invocation already claimed; inspect evidence without retry')
            def current():
                _remaining(deadline)
                native_codex._current_request(request)
                if (cancel is not None and cancel.is_set()) or datetime.now(timezone.utc) >= _time(self.engine.docs['config']['expires_at']):
                    raise StateError('Report invocation cancelled or authorization expired')
                native_provenance._unchanged_inputs(journal, request)
                if {row['name']: _read(journal.run_dir / row['path'], MAX_ARTIFACT_BYTES)
                    for row in request['input_artifacts']} != selected['raw']:
                    raise StateError('Native report inputs differ from prepared scope')
                if _read(request_path, MAX_DOCUMENT_BYTES) != request_raw:
                    raise StateError('Native report request changed')
            with _deadline(deadline), ExitStack() as stack:
                current()
                _audit(selected, deadline)
                if native_codex._file_digest(selection.executable.resolve(strict=True), deadline) != selection.expected_client_sha256:
                    raise StateError('Native client differs from the selected pin')
                plugin_version = _json(_read(selection.installed_root / '.codex-plugin/plugin.json', MAX_DOCUMENT_BYTES))['version']
                native_codex._package(selection.installed_root, selection.package_sha256, selection.plugin_id, plugin_version, deadline)
                engine_identity = native_codex._engine_identity(selection.engine_python, cwd=selected['output'],
                    deadline=deadline, environ=_environment(selected['output']), cancel=cancel)
                _private_directory(directory, new=True)
                directory_fd = stack.enter_context(native_codex._directory(directory))
                native_codex._write_private(directory_fd, 'intent.json', _canonical({'request_sha256': active['request']['sha256'],
                    'workflow': selected['workflow'], 'execution_mode': self.engine.mode,
                    'scope': 'native-report-only'}))
                _fsync_directory(directory)
                _fsync_directory(journal.run_dir)
                identity = _identity(directory)
                options = selection._options(cancel)
                reserve = min(120, _remaining(deadline) / 3)
                options['total_timeout'] = _remaining(deadline) - reserve
                try:
                    execution = native_codex.execute_native_codex(request_path, run_root=journal.run_dir,
                        request_sha256=active['request']['sha256'], output=directory / 'execution', **options)
                    frozen, execution_manifest, result = native_provenance._retained(
                        directory / 'execution', execution, workflow=selected['workflow'])
                    invocation = _json(frozen['invocation.json'])
                    def execution_audit():
                        native_codex._directory_unchanged(directory, directory_fd)
                        native_codex._package(selection.installed_root, selection.package_sha256, selection.plugin_id, plugin_version, deadline)
                        if native_codex._file_digest(selection.executable.resolve(strict=True), deadline) != selection.expected_client_sha256:
                            raise StateError('Native executable changed during report validation')
                        if native_codex._engine_identity(selection.engine_python, cwd=selected['output'],
                                deadline=deadline, environ=_environment(selected['output']), cancel=cancel) != engine_identity:
                            raise StateError('Engine interpreter changed during report validation')
                        if native_provenance._retained(directory / 'execution', execution, workflow=selected['workflow'])[0] != frozen:
                            raise StateError('Retained execution evidence changed')
                    if (frozen['request.json'] != request_raw or result['status'] != 'completed'
                            or any(execution[key] != request[key] for key in ('request_id', 'run_id', 'step_id'))
                            or execution['package_sha256'] != selection.package_sha256
                            or invocation['executable_sha256'] != selection.expected_client_sha256
                            or invocation['model_requested'] != selection.model
                            or invocation['reasoning_effort_requested'] != selection.reasoning_effort
                            or invocation['client_version'] != selection.expected_client_version
                            or invocation['engine_python'] != engine_identity
                            or execution['engine_python_identity_sha256'] != _sha(_canonical(engine_identity))):
                        raise StateError('Direct report invocation identity differs from selected inputs')
                    artifacts = _joined(journal, request, result, execution, frozen)
                    report, report_ref = _document_artifacts(selected['workflow'], result, artifacts)
                    current()
                    _audit(selected, deadline)
                    if selected['workflow'] == 'review-kubernetes-change' and not (
                            _time(execution['started_at']) <= _time(report['started_at']) <=
                            _time(report['finished_at']) <= _time(execution['finished_at'])):
                        raise StateError('Review report time is outside actual native execution')
                    if selected['workflow'] == 'verify-gitops-recovery' and not (
                            _time(execution['started_at']) <= _time(report['observed_at']) <= _time(execution['finished_at'])):
                        raise StateError('Recovery report time is outside actual native execution')
                    replay_dir = directory / 'independent'
                    _private_directory(replay_dir, new=True)
                    expected = _replay(selected, selection, replay_dir, deadline, cancel)
                    if _semantics(selected['workflow'], report) != _semantics(selected['workflow'], expected):
                        raise StateError('Native report differs from independent fixed replay')
                    result_path = journal.run_dir / request['result_path']
                    def validator(given_request, given_result, given_artifacts):
                        if given_request != request or given_result != result or dict(given_artifacts) != artifacts:
                            raise StateError('Independent validator received different native bytes')
                        current()
                        _audit(selected, deadline)
                        _fresh(selected, report)
                        return ['exact-native-report-bytes-bound', 'fixed-engine-replay-matched',
                                'domain-verdict-preserved', 'no-runtime-authority-issued']
                    execution_audit()
                    accepted = journal.accept_native(result_path, identities=journal.identities, validator=validator)
                    validation_path = journal.run_dir / accepted['validation']['path']
                    validation_raw = _read(validation_path, MAX_DOCUMENT_BYTES)
                    validation = _json(validation_raw)
                    if (accepted['validation']['sha256'] != _sha(validation_raw)
                            or accepted['validation']['bytes'] != len(validation_raw)
                            or validation['request_sha256'] != _sha(request_raw)
                            or validation['result_sha256'] != _sha(frozen['native-result.json'])
                            or not (_time(request['created_at']) <= _time(execution['started_at']) <=
                                    _time(execution['finished_at']) <= _time(validation['validated_at']) < _time(request['expires_at']))):
                        raise StateError('Accepted report validation binding changed')
                    current()
                    _audit(selected, deadline)
                    _fresh(selected, report)
                    if _identity(directory) != identity:
                        raise StateError('Report evidence directory changed')
                    document = {'schema_version': 1, 'kind': 'native-report-execution',
                        'authority_scope': 'native-report-and-artifact-validation-only',
                        'execution_mode': 'controlled-test' if self.engine.mode == 'controlled-test' else 'direct',
                        'run_id': state['run_id'], 'step_id': active['step_id'],
                        'workflow': selected['workflow'], 'identities': dict(journal.identities),
                        'request_sha256': _sha(request_raw), 'result_sha256': _sha(frozen['native-result.json']),
                        'report_sha256': report_ref['sha256'], 'domain_decision': report['decision'],
                        'validation_sha256': _sha(validation_raw), 'execution_manifest_sha256': _sha(execution_manifest),
                        'trace_sha256': execution['trace_sha256'], 'thread_id': execution['thread_id'],
                        'configured_model': selection.model, 'configured_reasoning_effort': selection.reasoning_effort,
                        'client_version': selection.expected_client_version, 'client_sha256': selection.expected_client_sha256,
                        'independent_report_sha256': _sha(_read(replay_dir / 'report' / _KINDS[selected['workflow']][2], MAX_ARTIFACT_BYTES)),
                        'runtime_authority': 'not-issued', 'scenario_assertion_status': 'not_evaluated'}
                    terminal = directory / 'validated-report.json'
                    native_codex._write_private(directory_fd, terminal.name, _canonical(document))
                    current()
                    execution_audit()
                    _audit(selected, deadline)
                    _fresh(selected, report)
                    if _identity(directory) != identity:
                        raise StateError('Report evidence directory changed during publication')
                    files = {request_path: request_raw, result_path: frozen['native-result.json'],
                             validation_path: validation_raw, terminal: _canonical(document)}
                    files.update({journal.run_dir / row['path']: artifacts[row['name']] for row in result['artifacts']})
                    files.update({directory / 'execution' / name: raw for name, raw in frozen.items()})
                    files[directory / 'execution/manifest.json'] = execution_manifest
                    for path in (replay_dir / 'report' / _KINDS[selected['workflow']][2],
                                 replay_dir / 'replay-invocation.json', replay_dir / 'replay-stdout.log', replay_dir / 'replay-stderr.log'):
                        files[path] = _read(path, MAX_ARTIFACT_BYTES)
                    if any(_read(path, 64_000_000) != raw for path, raw in files.items()):
                        raise StateError('Native report bytes changed during publication')
                    current()
                    _fresh(selected, report)
                    native_codex._directory_unchanged(directory, directory_fd)
                    value = ValidatedNativeReport(_ISSUER)
                    _REPORTS[value] = {'pid': os.getpid(), 'document': document, 'files': files}
                    self._reports[(active['step_id'], active['request']['sha256'])] = value
                    return value
                except BaseException as error:
                    # Only remove our own terminal marker via the retained fd.
                    try:
                        os.unlink('validated-report.json', dir_fd=directory_fd)
                        os.fsync(directory_fd)
                    except FileNotFoundError:
                        pass
                    if isinstance(error, _PhaseExpired):
                        raise StateError('Report deadline expired; retained attempt cannot be retried') from None
                    raise
