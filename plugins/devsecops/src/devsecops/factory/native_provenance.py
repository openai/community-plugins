"""Direct native execution plus trusted-host artifact validation.

There is deliberately no load/from-JSON/receipt-to-authority constructor. The
opaque origin is issued only in the process that calls the concrete Codex driver
and RunJournal's independent validator. Portable documents remain offline data.
"""
from __future__ import annotations

from copy import deepcopy
import inspect
import os
from pathlib import Path
import re
import socket
import time
from types import CodeType, MappingProxyType
from uuid import uuid4
from weakref import WeakKeyDictionary

from devsecops.runtime.context import validate
from devsecops.runtime.recovery import NATIVE_EXECUTION_V2_SCHEMA
from . import native_codex
from .state import (MAX_DOCUMENT_BYTES, RunJournal, StateError, _canonical, _identifier,
                    _json, _read, _safe_path, _sha, _time)

_ISSUER = object()
_ISSUED = WeakKeyDictionary()
_REQUIRED_OPTIONS = {'installed_root', 'package_sha256', 'plugin_id', 'model', 'reasoning_effort',
                     'engine_python', 'expected_client_version', 'executable'}
_OPTIONAL_OPTIONS = {'total_timeout', 'max_trace_bytes', 'cancel'}
_BINDINGS = ('native_execution', 'native_request', 'native_result', 'native_validation', 'patch')
_HASH = re.compile(r'[a-f0-9]{64}\Z')


class NativeExecutionOrigin:
    """Process-local, non-serializable origin for exactly five frozen artifacts."""
    __slots__ = ('__weakref__',)

    def __new__(cls, issuer=None):
        if issuer is not _ISSUER:
            raise StateError('Native origins require direct execution and validation')
        return super().__new__(cls)

    @property
    def document(self):
        return _json(_ISSUED[self]['artifacts']['native_execution'])

    @property
    def artifact_sha256(self):
        return {name: _sha(raw) for name, raw in _ISSUED[self]['artifacts'].items()}

    def __reduce__(self):
        raise StateError('Native origins cannot be serialized or restored from reports')


def native_execution_artifacts(origin):
    """Exact original files retained at issuance; never reconstructed by scan."""
    record = _ISSUED.get(origin) if type(origin) is NativeExecutionOrigin else None
    if record is None or record['pid'] != os.getpid() or 'files' not in record:
        raise StateError('Original native execution file provenance is unavailable')
    files = record['files']
    if not 1 <= len(files) <= 32 or sum(map(len, files.values())) > 128_000_000:
        raise StateError('Native execution artifact set exceeds its bound')
    for path, raw in files.items():
        if _read(path, 64_000_000) != raw:
            raise StateError('Original native execution artifact bytes changed')
    return tuple(sorted(files))


def verify_native_execution_origin(origin, *, artifacts):
    """Authenticate only exact native bytes using an original process-local object.

    The rest of a recovery bundle is not in this origin's authority. Additional
    artifacts are ignored; every one of the five native artifacts must match.
    """
    if type(origin) is not NativeExecutionOrigin or origin not in _ISSUED:
        raise StateError('Only a directly issued native execution origin is accepted')
    issued = _ISSUED[origin]
    if issued['pid'] != os.getpid():
        raise StateError('Native execution origins are valid only in the issuing process')
    valid = all(isinstance(artifacts.get(name), bytes) and artifacts[name] == raw
                for name, raw in issued['artifacts'].items())
    return {'verified': valid and issued['execution_mode'] == 'direct', 'bindings_match': valid,
            'execution_mode': issued['execution_mode'], 'scope': 'native-execution',
            'receipt_sha256': _sha(issued['artifacts']['native_execution'])}


def _code_identity(value, depth=0):
    # marshal's reference/interning flags can change after first execution. Use
    # semantic code fields so CPython adaptive execution cannot create false drift.
    if depth > 32:
        raise StateError('Validator code identity exceeds its depth bound')
    if isinstance(value, CodeType):
        return {'code': value.co_code.hex(), 'exceptions': value.co_exceptiontable.hex(),
                'constants': [_code_identity(x, depth + 1) for x in value.co_consts],
                'names': value.co_names, 'variables': value.co_varnames,
                'free_variables': value.co_freevars, 'cell_variables': value.co_cellvars,
                'args': value.co_argcount, 'positional_only': value.co_posonlyargcount,
                'keyword_only': value.co_kwonlyargcount, 'flags': value.co_flags}
    if isinstance(value, tuple):
        return {'tuple': [_code_identity(x, depth + 1) for x in value]}
    if isinstance(value, frozenset):
        return {'frozenset': sorted(_canonical(_code_identity(x, depth + 1)).decode() for x in value)}
    if isinstance(value, bytes):
        return {'bytes': value.hex()}
    if isinstance(value, float):
        return {'float': value.hex()}
    if isinstance(value, complex):
        return {'complex': [value.real.hex(), value.imag.hex()]}
    if value is Ellipsis:
        return {'ellipsis': True}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise StateError('Validator code identity is unsupported')


def _validator_identity(validator):
    if not callable(validator):
        raise StateError('An independent trusted-host artifact validator is required')
    target = validator if inspect.isfunction(validator) or inspect.ismethod(validator) else validator.__call__
    if inspect.ismethod(target):
        target = target.__func__
    source = inspect.getsourcefile(target)
    if not inspect.isfunction(target) or source is None:
        raise StateError('Validator must have an identifiable trusted-host Python implementation')
    code_raw = _canonical(_code_identity(target.__code__))
    if len(code_raw) > 4_000_000:
        raise StateError('Validator code identity exceeds its byte bound')
    return {'module': target.__module__, 'name': target.__qualname__,
            'code_sha256': _sha(code_raw),
            'source_file_sha256': _sha(_read(Path(source).absolute(), 4_000_000))}


def _retained(directory, report, *, workflow="prepare-gitops-correction"):
    """Check the directly invoked driver's exact terminal bytes, never an import."""
    manifest_raw = _read(directory / 'manifest.json', MAX_DOCUMENT_BYTES)
    manifest = _json(manifest_raw)
    if not isinstance(manifest, dict) or set(manifest) != {'schema_version', 'files'} or manifest['schema_version'] != 1:
        raise StateError('Native driver terminal manifest is invalid')
    files = manifest['files']
    if not isinstance(files, dict) or not 8 <= len(files) <= 100:
        raise StateError('Native driver evidence membership is unsupported')
    frozen, total = {}, 0
    for name, reference in files.items():
        if (not isinstance(name, str) or not name or '/' in name or '\\' in name or name in ('.', '..', 'manifest.json')
                or not isinstance(reference, dict) or set(reference) != {'sha256', 'bytes'}):
            raise StateError('Native driver evidence reference is invalid')
        raw = _read(directory / name, 64_000_000)
        total += len(raw)
        if total > 160_000_000 or reference != {'sha256': _sha(raw), 'bytes': len(raw)}:
            raise StateError('Native driver evidence bytes changed')
        frozen[name] = raw
    if {p.name for p in directory.iterdir()} != set(files) | {'manifest.json'}:
        raise StateError('Native driver evidence membership changed')
    if frozen.get('execution.json') != _canonical(report):
        raise StateError('Native driver return differs from retained execution bytes')
    thread, result_raw, result = native_codex._trace(frozen['trace.jsonl'])
    if (report['execution_status'] != 'complete' or report['scope'] != 'native-handoff'
            or report['workflow'] != workflow or report['exit_code'] != 0
            or report['process_started'] is not True or report['gaps']
            or report['thread_id'] != thread or report['trace_sha256'] != _sha(frozen['trace.jsonl'])
            or report['result_sha256'] != _sha(result_raw) or frozen['native-result.json'] != result_raw
            or report['request_sha256'] != _sha(frozen['request.json'])):
        raise StateError('Direct native execution is incomplete or inconsistent')
    return frozen, manifest_raw, result


def _unchanged_inputs(journal, request):
    total = 0
    for item in request['input_artifacts']:
        raw = _read(native_codex._relative(journal.run_dir, item['path']), 16_000_000)
        total += len(raw)
        if total > 64_000_000 or len(raw) != item['bytes'] or _sha(raw) != item['sha256']:
            raise StateError('Frozen native inputs changed during independent validation')


def _current_authorization(request, options):
    if options.get('cancel') is not None and options['cancel'].is_set():
        raise StateError('Native provenance was cancelled')
    native_codex._current_request(request)


def execute_validated_correction(journal: RunJournal, *, request_path: Path, request_sha256: str,
                                 attempt_id: str, environment: str, expected_client_sha256: str,
                                 validator, provenance_output: Path, native_options: dict,
                                 _controlled_test: bool = False) -> NativeExecutionOrigin:
    """Invoke the real driver, accept independently checked artifacts, mint origin.

    ``validator`` is executable trusted-host code, never a name/path from request,
    config, repository, model, or receipt JSON. Production composition must select
    a fixed correction validator. That validator must impose its own tool/time
    bounds; the synchronous callback is not an arbitrary-code sandbox.

    The existing RunJournal acceptance event is committed before provenance
    publication. If publication fails, no origin is issued: the caller must stop
    delivery despite the accepted artifact handoff. There is no automatic retry,
    JSON import, journal rollback, or source-authentication upgrade on restart.
    """
    if type(journal) is not RunJournal or type(_controlled_test) is not bool:
        raise StateError('A concrete owned RunJournal is required')
    _identifier(attempt_id)
    if not isinstance(environment, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', environment):
        raise StateError('An explicit owned environment identity is required')
    if not isinstance(expected_client_sha256, str) or not _HASH.fullmatch(expected_client_sha256):
        raise StateError('An independently pinned Codex executable digest is required')
    if (not isinstance(native_options, dict) or not _REQUIRED_OPTIONS <= set(native_options)
            or set(native_options) - _REQUIRED_OPTIONS - _OPTIONAL_OPTIONS):
        raise StateError('Native invocation options are incomplete or unsupported')
    options = dict(native_options)
    validator_identity = _validator_identity(validator)
    state = journal.state
    active = state.get('active_native')
    request_path = _safe_path(request_path)
    if (state['environment_id'] != environment or active is None
            or active['request']['sha256'] != request_sha256
            or request_path != journal.run_dir / active['request']['path']):
        raise StateError('Native provenance requires the exact pending owned handoff')
    raw_request = _read(request_path, MAX_DOCUMENT_BYTES)
    request = _json(raw_request)
    if (request['workflow'] != 'prepare-gitops-correction' or request['required_artifact_kind'] != 'patch'
            or request['identities'] != journal.identities or _sha(raw_request) != request_sha256
            or options['package_sha256'] != journal.identities['package_sha256']):
        raise StateError('Native provenance requires the bound correction request and package')
    _current_authorization(request, options)
    timeout = options.get('total_timeout', 900)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 3600:
        raise StateError('Native invocation deadline is invalid')
    deadline = time.monotonic() + timeout
    executable = Path(options['executable']).resolve(strict=True)
    if native_codex._file_digest(executable, deadline) != expected_client_sha256:
        raise StateError('Codex executable differs from the independently pinned binary')
    provenance_output = _safe_path(provenance_output)
    if provenance_output.exists():
        raise StateError('Native provenance requires a new private directory')
    execution_id = str(uuid4())
    with native_codex._directory(provenance_output, create=True) as directory:
        retained, written = {}, {}
        def write(name, raw):
            retained[name] = native_codex._write_private(directory, name, raw)
            written[provenance_output / name] = raw
        write('validator-identity.json', _canonical(validator_identity))
        options['total_timeout'] = native_codex._remaining(deadline)
        report = native_codex.execute_native_codex(request_path, run_root=journal.run_dir,
            request_sha256=request_sha256, output=provenance_output / 'execution', **options)
        frozen, execution_manifest, result = _retained(provenance_output / 'execution', report)
        if (report['executable_sha256'] != expected_client_sha256
                or report['model_requested'] != options['model']
                or report['reasoning_effort_requested'] != options['reasoning_effort']
                or report['client_version'] != options['expected_client_version']
                or report['package_sha256'] != options['package_sha256'] or frozen['request.json'] != raw_request):
            raise StateError('Native execution differs from the pinned caller configuration')
        patches = [item for item in result['artifacts'] if item['kind'] == 'patch']
        if len(patches) != 1:
            raise StateError('Exactly one actual native patch is required')
        patch_ref = patches[0]
        patch = _read(native_codex._relative(journal.run_dir, patch_ref['path']), 256_000)
        if (not patch or b'\0' in patch or patch_ref['sha256'] != _sha(patch)
                or patch_ref['bytes'] != len(patch)):
            raise StateError('Native patch bytes do not match the result')
        result_path = native_codex._relative(journal.run_dir, request['result_path'])
        if _read(result_path, MAX_DOCUMENT_BYTES) != frozen['native-result.json']:
            raise StateError('Native result bytes changed before independent validation')

        def bounded_validator(current_request, current_result, artifacts):
            _current_authorization(request, options)
            _unchanged_inputs(journal, request)
            if (current_request != request or current_result != result
                    or artifacts.get(patch_ref['name']) != patch):
                raise StateError('Independent validator received different native bytes')
            try:
                checks = validator(current_request, current_result, artifacts)
            except Exception:
                raise StateError('Independent native artifact validation failed') from None
            _current_authorization(request, options)
            _unchanged_inputs(journal, request)
            if _validator_identity(validator) != validator_identity:
                raise StateError('Trusted validator identity changed during execution')
            return checks

        accepted = journal.accept_native(result_path, identities=journal.identities, validator=bounded_validator)
        reference = accepted['validation']
        raw_validation = _read(native_codex._relative(journal.run_dir, reference['path']), MAX_DOCUMENT_BYTES)
        validation = _json(raw_validation)
        if reference['sha256'] != _sha(raw_validation) or reference['bytes'] != len(raw_validation):
            raise StateError('Independent validation receipt changed')
        _current_authorization(request, options)
        if not (_time(request['created_at']) <= _time(report['started_at']) <= _time(report['finished_at'])
                <= _time(validation['validated_at']) < _time(request['expires_at'])):
            raise StateError('Native execution and validation do not share the current request interval')
        invocation = _json(frozen['invocation.json'])
        if (invocation['executable_sha256'] != expected_client_sha256
                or invocation['model_requested'] != options['model']
                or invocation['reasoning_effort_requested'] != options['reasoning_effort']):
            raise StateError('Retained invocation identity differs from the caller')
        mode = 'controlled-test' if _controlled_test else 'direct'
        document = {
            'schema_version': 2, 'kind': 'runtime-native-execution', 'driver': 'codex-cli-single-turn',
            'execution_id': execution_id, 'execution_mode': mode,
            'authority_scope': 'native-execution-and-artifact-validation-only',
            'run_id': request['run_id'], 'attempt_id': attempt_id, 'environment': environment,
            'identities': deepcopy(journal.identities), 'request_id': request['request_id'],
            'step_id': request['step_id'], 'workflow': request['workflow'],
            'host_id': 'local-' + _sha(socket.gethostname().encode())[:32],
            'thread_id': report['thread_id'], 'turn_ordinal': 1, 'server_turn_id': None,
            'configured_model': report['model_requested'],
            'configured_reasoning_effort': report['reasoning_effort_requested'],
            'client_version': report['client_version'], 'executable_sha256': expected_client_sha256,
            'engine_python_identity_sha256': report['engine_python_identity_sha256'],
            'request_sha256': request_sha256, 'result_sha256': report['result_sha256'],
            'patch_sha256': _sha(patch), 'package_sha256': report['package_sha256'],
            'trace_sha256': report['trace_sha256'], 'invocation_sha256': _sha(frozen['invocation.json']),
            'driver_execution_sha256': _sha(frozen['execution.json']),
            'execution_manifest_sha256': _sha(execution_manifest),
            'validator_identity_sha256': _sha(_canonical(validator_identity)),
            'validation_receipt_sha256': _sha(raw_validation),
            'started_at': report['started_at'], 'finished_at': report['finished_at'],
            'validated_at': validation['validated_at'],
        }
        validate(document, NATIVE_EXECUTION_V2_SCHEMA)
        artifact_bytes = {'native_execution': _canonical(document), 'native_request': raw_request,
                          'native_result': frozen['native-result.json'], 'native_validation': raw_validation,
                          'patch': patch}
        for name, raw in artifact_bytes.items():
            write(name + ('.patch' if name == 'patch' else '.json'), raw)
        native_codex._directory_unchanged(provenance_output, directory)
        _current_authorization(request, options)
        write('origin-manifest.json', _canonical({'schema_version': 1, 'execution_id': execution_id,
            'files': retained, 'execution_manifest_sha256': _sha(execution_manifest)}))
        try:
            native_codex._directory_unchanged(provenance_output, directory)
            _current_authorization(request, options)
            if _retained(provenance_output / 'execution', report)[1] != execution_manifest:
                raise StateError('Native evidence changed during provenance publication')
            if (_read(result_path, MAX_DOCUMENT_BYTES) != artifact_bytes['native_result']
                    or _read(request_path, MAX_DOCUMENT_BYTES) != raw_request
                    or _read(native_codex._relative(journal.run_dir, reference['path']), MAX_DOCUMENT_BYTES) != raw_validation
                    or _read(native_codex._relative(journal.run_dir, patch_ref['path']), 256_000) != patch):
                raise StateError('Native artifacts changed during provenance publication')
            _unchanged_inputs(journal, request)
        except (ValueError, OSError):
            os.unlink('origin-manifest.json', dir_fd=directory)
            os.fsync(directory)
            raise
        origin = NativeExecutionOrigin(_ISSUER)
        files = {**written, **{provenance_output / 'execution' / name: raw for name, raw in frozen.items()},
                 provenance_output / 'execution/manifest.json': execution_manifest,
                 request_path: raw_request, result_path: frozen['native-result.json'],
                 native_codex._relative(journal.run_dir, reference['path']): raw_validation,
                 native_codex._relative(journal.run_dir, patch_ref['path']): patch}
        _ISSUED[origin] = {'artifacts': MappingProxyType(artifact_bytes), 'execution_mode': mode,
                           'pid': os.getpid(), 'files': MappingProxyType(files)}
        try:
            native_execution_artifacts(origin)
        except BaseException:
            _ISSUED.pop(origin, None)
            raise
        return origin
