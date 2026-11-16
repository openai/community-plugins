"""D5 actual denied read, labeled input controls and a fresh native diagnosis.

The restored diagnosis compares original historical baseline/fault evidence.
It does not relabel recovered observations as a fault or issue runtime authority.
The separate child request never advances the eight main native handoffs.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import stat
import time

from devsecops.adapters.http import APIError, HTTPSReadClient
from devsecops.runtime.collection import _missing
from devsecops.runtime.context import seal
from devsecops.runtime.diagnosis import diagnose_runtime
from . import native_codex, native_provenance, native_reports
from .lifecycle import AssertionResult, PhaseOutcome
from .state import MAX_ARTIFACT_BYTES, MAX_ARTIFACT_TOTAL_BYTES, RunJournal, StateError, _canonical, _exclusive, _json, _read, _sha, _time

ASSERTIONS = ('denied-expired-stale-missing-input-is-unknown', 'restored-evidence-new-scoped-review')
WORKFLOW = 'diagnose-gitops-deployment'


def evidence_inventory(root):
    """Only fixed producer files and actual declared native outputs, bounded."""
    permitted = {'case-result.json', 'denied-read-transport.json', 'denied-read-result.json'}
    permitted.update('controls/' + name for name in ('stale-observation-copy.json', 'expired-validity-control.json',
        'missing-diagnosis.json', 'stale-diagnosis.json', 'expired-result.json', 'restored-diagnosis.json'))
    prefix = 'restored-review/'
    permitted.add(prefix + 'case-native-intent.json')
    permitted.update(prefix + 'scope/' + name for name in ('baseline', 'fault', 'report-selection'))
    permitted.update(prefix + 'independent/' + name for name in ('replay-stdout.log', 'replay-stderr.log',
        'replay-invocation.json', 'report/diagnosis.json', 'report/report.html'))
    request_prefix = prefix + 'request-run/'
    step = 'native/evidence-gaps-restored/'
    permitted.update(request_prefix + name for name in ('state.json', '.run.lock', '.lifecycle.lock'))
    permitted.update(request_prefix + step + name for name in ('request.json', 'result.json', 'validation.json'))
    permitted.update(request_prefix + step + 'inputs/' + name for name in ('baseline', 'fault', 'report-selection'))
    result_path = root / request_prefix / step / 'result.json'
    if result_path.exists():
        result = _json(_read(result_path, 1_000_000))
        for item in result.get('artifacts', []):
            path = Path(item['path'])
            if path.is_absolute() or '..' in path.parts or not path.is_relative_to(step + 'output'):
                raise StateError('D5 declared native artifact escaped its exact output')
            permitted.add(request_prefix + item['path'])
    execution = prefix + 'execution/'
    permitted.update(execution + name for name in ('request.json', 'prompt.txt', 'package.SHA256SUMS',
        'result-schema.json', 'startup-config.json', 'invocation.json', 'trace.jsonl', 'stderr.log',
        'native-result.json', 'execution.json', 'manifest.json'))
    manifest_path = root / execution / 'manifest.json'
    if manifest_path.exists():
        manifest = _json(_read(manifest_path, 1_000_000))
        for name in manifest['files']:
            if not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
                raise StateError('D5 execution manifest contains an unsupported path')
            permitted.add(execution + name)
    files, total, entries = [], 0, 0
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as scan:
            for entry in scan:
                entries += 1
                if entries > 512:
                    raise StateError('D5 evidence inventory exceeds its entry bound')
                info = entry.stat(follow_symlinks=False)
                path = Path(entry.path)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                    continue
                relative = path.relative_to(root).as_posix()
                known_journal = (re.fullmatch(re.escape(request_prefix) + r'journal/[0-9]{8}\.json', relative)
                    or re.fullmatch(re.escape(request_prefix + step) + r'validation-[a-f0-9]{32}\.json', relative))
                if not stat.S_ISREG(info.st_mode) or relative not in permitted and not known_journal:
                    raise StateError('D5 evidence contains an unexpected or nonregular artifact')
                if info.st_size > MAX_ARTIFACT_BYTES or total + info.st_size > MAX_ARTIFACT_TOTAL_BYTES:
                    raise StateError('D5 evidence exceeds its artifact budget')
                raw = _read(path, MAX_ARTIFACT_BYTES)
                total += len(raw)
                files.append(path)
                if len(files) > 255:  # The lifecycle adds its own 256th phase receipt.
                    raise StateError('D5 evidence exceeds its canonical phase artifact count')
    return tuple(sorted(files))


def _write(root, name, value):
    raw = value if type(value) is bytes else _canonical(value)
    path = root / name
    _exclusive(path, raw)
    return path


def input_controls(baseline, fault, *, output):
    """Real deterministic validators on explicitly labeled changed copies."""
    output.mkdir(mode=0o700)
    original = {_path: _read(_path, 16_000_000) for _path in (baseline, fault)}
    restored = diagnose_runtime(baseline, fault)
    if any(row['state'] != 'valid' for row in restored['inputs'].values()):
        raise StateError('D5 controls require originally valid same-run bundles')
    missing = diagnose_runtime(baseline, None)
    if missing['decision'] != 'unknown' or not any(row['code'] == 'input-missing' for row in missing['gaps']):
        raise StateError('D5 missing input did not preserve unknown')
    stale = deepcopy(_json(original[fault]))
    if stale['window'] is None:
        raise StateError('D5 stale control requires an actual measured observation window')
    expired = datetime.fromtimestamp(stale['window']['end'] + stale['window']['max_sample_age_seconds'] + 1, timezone.utc)
    stale['observed_at'] = expired.isoformat()
    stale = seal(stale, 'bundle_sha256')
    stale_path = _write(output, 'stale-observation-copy.json', stale)
    stale_report = diagnose_runtime(baseline, stale_path)
    if (stale_report['decision'] != 'unknown'
            or not any(row['code'] == 'measurement-window-stale-or-early' for row in stale_report['gaps'])):
        raise StateError('D5 stale observation did not preserve its precise evidence gap')
    now = datetime.now(timezone.utc)
    expiry_control = {'created_at': (now - timedelta(seconds=2)).isoformat(),
                      'expires_at': (now - timedelta(seconds=1)).isoformat()}
    try:
        native_codex._current_request(expiry_control)
    except native_codex.NativeCodexError:
        expired_result = {'decision': 'unknown', 'gap': 'native-input-expired',
                          'next_action': 'Prepare a fresh scoped request from restored original inputs.'}
    else:
        raise StateError('D5 expired request was not rejected before execution')
    _write(output, 'expired-validity-control.json', {'scope': 'labeled-copy-control-no-client-invocation', **expiry_control})
    for name, value in (('missing-diagnosis.json', missing), ('stale-diagnosis.json', stale_report),
                        ('expired-result.json', expired_result), ('restored-diagnosis.json', restored)):
        _write(output, name, value)
    if any(_read(path, 16_000_000) != raw for path, raw in original.items()):
        raise StateError('D5 original observations changed during negative controls')
    return {'missing': {'decision': 'unknown', 'gap': 'input-missing',
                        'next_action': 'Restore the original hash-bound missing observation input.'},
            'stale': {'decision': 'unknown', 'gap': 'measurement-window-stale-or-early',
                      'next_action': 'Acquire a new scoped observation; retain the stale window unchanged.'},
            'expired': expired_result, 'restored_report_sha256': _sha(_canonical(restored)),
            'control_scope': 'copies-of-original-observations-no-runtime-origin-issued'}


class _DeniedReadClient(HTTPSReadClient):
    """One fixed read using an original owned no-RBAC identity and frozen CA."""
    def __init__(self, reader, *, journal, authority, binding, deadline):
        from .owned_runtime_reader import require_runtime_reader
        import ssl
        self._reader, self._scope = reader, {'journal': journal, 'bootstrap_authority': authority}
        if require_runtime_reader(reader, **self._scope) != binding:
            raise StateError('D5 reader changed before transport construction')
        ca = _read(Path(binding['ca_path']), 2_000_000)
        if _sha(ca) != binding['ca_sha256']:
            raise StateError('D5 original CA bytes changed')
        remaining = min(20, deadline - time.monotonic())
        if remaining < 1:
            raise StateError('D5 read budget expired')
        super().__init__(binding['origin'], timeout=min(5, remaining), total_timeout=remaining,
            max_requests=1, max_body_bytes=1_000_000, max_total_bytes=1_000_000)
        trust = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        trust.load_verify_locations(cadata=ca.decode('ascii'))
        self._context, self._deadline = trust, min(deadline, time.monotonic() + remaining)

    def _authorization(self):
        from .owned_runtime_reader import private_token
        from devsecops.adapters.http import _bearer
        return _bearer(private_token(self._reader, **self._scope))


def denied_read(context, *, inputs, cases, output):
    """One actual GET using the original owned restricted reader; no retries."""
    from .owned_runtime_reader import OwnedRuntimeReader, require_runtime_reader
    from .canonical_bootstrap import require_bootstrap_authority
    cases._admit(context)
    if context.phase['id'] != 'evidence-gaps' or context.phase['operation'] != 'case':
        raise StateError('D5 reader requires the current evidence-gaps case')
    bootstrap = cases.operations.bootstrap
    authority = bootstrap.require_authority(context)
    facts = require_bootstrap_authority(authority, cases.journal)
    reader = bootstrap.runtime_reader(context, role='denied')
    if type(reader) is not OwnedRuntimeReader:
        raise StateError('D5 requires the original independently provisioned restricted reader')
    binding = require_runtime_reader(reader, journal=cases.journal, bootstrap_authority=authority)
    intent_path, intent_pin = inputs['inputs']['intent']
    intent_raw = _read(intent_path, 1_000_000)
    if _sha(intent_raw) != intent_pin:
        raise StateError('D5 original read scope changed')
    intent = _json(intent_raw)
    targets = [target for target in intent['targets'] if 'mission' in target['roles']]
    if len(targets) != 1:
        raise StateError('D5 requires one actual original mission target')
    target = targets[0]['kubernetes']
    if (binding['role'] != 'denied' or binding['selected_resource_grants'] != []
            or binding['origin'] != facts['kubernetes']['origin']
            or binding['cluster_uid'] != facts['kubernetes']['cluster_uid']
            or binding['run_id'] != context.run_id or binding['identities'] != dict(context.identities)):
        raise StateError('D5 no-RBAC reader differs from original run and cluster')
    # The original runtime intent getter has already audited exact owned
    # Deployment/namespace identity. No caller-selected route is accepted here.
    client = _DeniedReadClient(reader, journal=cases.journal, authority=authority, binding=binding, deadline=context.deadline)
    route = '/apis/apps/v1/namespaces/' + target['namespace'] + '/deployments/' + target['deployment']
    failure = None
    try:
        client.get_json(route)
    except APIError as error:
        failure = (error.code, error.status)
    finally:
        _write(output, 'denied-read-transport.json', {'route': route, 'origin': binding['origin'],
            'serviceaccount_uid': binding['serviceaccount_uid'], 'ca_sha256': binding['ca_sha256'],
            'receipts': client.receipts, 'scope': 'owned-no-rbac-read-negative-control-no-revocation-or-retry'})
    cases._admit(context)
    require_runtime_reader(reader, journal=cases.journal, bootstrap_authority=authority)
    if time.monotonic() >= context.deadline:
        raise StateError('D5 read budget expired before acceptance')
    if failure != ('denied', 403):
        raise StateError('D5 did not observe the selected authenticated read-permission denial')
    report = _missing('kubernetes', 'denied', datetime.now(timezone.utc))
    _write(output, 'denied-read-result.json', report)
    return {'decision': report['decision'], 'gap': 'runtime-denied', 'http_status': 403,
            'next_action': 'Use the separately authorized ordinary read identity for a new scoped observation.'}


def execute_restored_review(context, *, cases, inputs, output, maximum):
    from .native_factory_session import NativeFactorySession
    session = cases.driver._native_session
    if type(session) is not NativeFactorySession:
        raise StateError('D5 requires the original resident native session')
    selection = session.selection_for_readonly_case(context)
    if session._cancel.is_set():
        raise StateError('D5 native review was cancelled before preparation')
    baseline, fault = inputs['baseline'][0], inputs['primary_fault'][0]
    scope = native_reports.prepare_diagnosis_scope(baseline=baseline, fault=fault,
        output=output / 'scope', deadline=context.deadline)
    prepared = native_reports._scope(scope)
    child = RunJournal.create(output / 'request-run', identities=dict(context.identities),
                              environment_id=cases.journal.state['environment_id'])
    request_info = child.request_native('evidence-gaps-restored', WORKFLOW,
        input_artifacts=scope.input_artifacts, ttl_seconds=min(maximum, 240),
        expires_at=context.inputs['config']['expires_at'])
    request_path = Path(request_info['request_path'])
    request_raw = _read(request_path, 1_000_000)
    request = _json(request_raw)
    main_ids = {row['request_id'] for row in cases.journal.state['native_requests']}
    if request['request_id'] in main_ids or child.state['run_id'] == context.run_id:
        raise StateError('D5 requires a new separate request identity')
    _write(output, 'case-native-intent.json', {'parent_run_id': context.run_id, 'child_run_id': child.state['run_id'],
        'request_sha256': _sha(request_raw), 'scope': 'historical-baseline-fault-diagnosis',
        'runtime_authority': 'not-issued', 'main_native_steps_advanced': False})
    deadline = min(context.deadline, time.monotonic() + maximum)
    def current():
        cases._admit(context)
        if session.selection_for_readonly_case(context) is not selection or session._cancel.is_set():
            raise StateError('D5 native selection changed or was cancelled')
        native_codex._current_request(request)
        native_reports._audit(prepared, deadline)
        native_provenance._unchanged_inputs(child, request)
        if {row['name']: _read(child.run_dir / row['path'], 16_000_000)
                for row in request['input_artifacts']} != prepared['raw']:
            raise StateError('D5 request differs from its original prepared scope')
        if _read(request_path, 1_000_000) != request_raw:
            raise StateError('D5 native request changed')
        if native_codex._file_digest(selection.executable.resolve(strict=True), deadline) != selection.expected_client_sha256:
            raise StateError('D5 client differs from original selected identity')
        plugin_version = _json(_read(selection.installed_root / '.codex-plugin/plugin.json', 1_000_000))['version']
        native_codex._package(selection.installed_root, selection.package_sha256,
                             selection.plugin_id, plugin_version, deadline)
        for key in ('baseline', 'primary_fault'):
            path, pin = inputs[key]
            if _sha(_read(path, 16_000_000)) != pin:
                raise StateError('D5 original restored inputs changed')
    current()
    engine = native_codex._engine_identity(selection.engine_python, cwd=output, deadline=deadline,
        environ=native_reports._environment(output), cancel=session._cancel)
    options = selection._options(session._cancel)
    options['total_timeout'] = min(maximum, deadline - time.monotonic()) * 0.7
    execution = native_codex.execute_native_codex(request_path, run_root=child.run_dir,
        request_sha256=_sha(request_raw), output=output / 'execution', **options)
    frozen, _, result = native_provenance._retained(output / 'execution', execution, workflow=WORKFLOW)
    invocation = _json(frozen['invocation.json'])
    if (frozen['request.json'] != request_raw or result['status'] != 'completed'
            or any(execution[key] != request[key] for key in ('request_id', 'run_id', 'step_id'))
            or execution['package_sha256'] != selection.package_sha256
            or invocation['executable_sha256'] != selection.expected_client_sha256
            or invocation['model_requested'] != selection.model
            or invocation['reasoning_effort_requested'] != selection.reasoning_effort
            or invocation['client_version'] != selection.expected_client_version
            or invocation['engine_python'] != engine
            or execution['engine_python_identity_sha256'] != _sha(_canonical(engine))):
        raise StateError('D5 actual native execution identity differs')
    artifacts = native_reports._joined(child, request, result, execution, frozen)
    report, reference = native_reports._document_artifacts(WORKFLOW, result, artifacts)
    if any(item['state'] != 'valid' for item in report['inputs'].values()):
        raise StateError('D5 restored report still lacks an original observation input')
    replay_dir = output / 'independent'
    replay_dir.mkdir(mode=0o700)
    expected = native_reports._replay(prepared, selection, replay_dir, deadline, session._cancel)
    if native_reports._semantics(WORKFLOW, report) != native_reports._semantics(WORKFLOW, expected):
        raise StateError('D5 native diagnosis differs from independent deterministic replay')
    current()
    if (native_provenance._retained(output / 'execution', execution, workflow=WORKFLOW)[0] != frozen
            or native_codex._engine_identity(selection.engine_python, cwd=output, deadline=deadline,
                environ=native_reports._environment(output), cancel=session._cancel) != engine):
        raise StateError('D5 execution evidence or interpreter changed')
    def validator(given_request, given_result, given_artifacts):
        if given_request != request or given_result != result or dict(given_artifacts) != artifacts:
            raise StateError('D5 independent validator received different native bytes')
        current()
        return ['new-case-only-request', 'original-historical-inputs', 'fixed-engine-replay-matched', 'no-runtime-authority']
    accepted = child.accept_native(child.run_dir / request['result_path'], identities=child.identities, validator=validator)
    if not (_time(request['created_at']) <= _time(execution['started_at']) <= _time(execution['finished_at']) < _time(request['expires_at'])):
        raise StateError('D5 execution is outside its new request validity')
    return {'request_id': request['request_id'], 'request_sha256': _sha(request_raw),
            'result_sha256': _sha(frozen['native-result.json']), 'validation_sha256': accepted['validation']['sha256'],
            'report_sha256': reference['sha256'],
            'report_decision': report['decision'], 'scope': 'historical-baseline-fault-comparison',
            'runtime_authority': 'not-issued', 'main_native_steps_advanced': False}


def execute_evidence_gaps(context, *, cases, selected, output):
    cases._admit(context)
    output.mkdir(mode=0o700)
    report = {'schema_version': 1, 'kind': 'canonical-evidence-gaps-case', 'case_id': 'D5',
        'run_id': context.run_id, 'identities': dict(context.identities), 'decision': 'unknown', 'checks': {},
        'execution_mode': context.execution_mode, 'runtime_authority': 'not-issued'}
    try:
        if context.execution_mode != 'operator':
            raise StateError('D5 live assertions require actual current operation origins')
        inputs = cases.runtime.evidence_gap_inputs(context)
        report['checks']['denied'] = denied_read(context, inputs=inputs, cases=cases, output=output)
        report['checks']['controls'] = input_controls(inputs['baseline'][0], inputs['primary_fault'][0], output=output / 'controls')
        review_dir = output / 'restored-review'
        review_dir.mkdir(mode=0o700)
        report['checks']['restored_review'] = execute_restored_review(context, cases=cases, inputs=inputs,
            output=review_dir, maximum=selected['max_native_seconds'])
        cases._admit(context)
        cases.runtime.evidence_gap_inputs(context)
        report['decision'] = 'pass'
    except (ValueError, OSError, KeyError, TypeError):
        report['gap'] = 'actual-evidence-control-or-fresh-scoped-native-report-incomplete'
    try:
        artifacts = evidence_inventory(output)
    except (OSError, ValueError, KeyError, TypeError):
        # Preserve partial directories for inspection; never delete unknown files
        # or register a symlink/unbounded native output as accepted evidence.
        report.update(decision='unknown', gap='bounded-case-evidence-inventory-incomplete',
                      partial_evidence='retained-for-inspection')
        receipt = _write(output, 'case-result.json', report)
        return PhaseOutcome('incomplete', 'actual-evidence-gaps-incomplete', (receipt,))
    receipt = _write(output, 'case-result.json', report)
    artifacts = (*artifacts, receipt)
    success = report['decision'] == 'pass'
    return PhaseOutcome('completed' if success else 'incomplete',
        'actual-evidence-gaps-verified' if success else 'actual-evidence-gaps-incomplete',
        artifacts,
        tuple(AssertionResult('D5', name, 'pass') for name in ASSERTIONS) if success else ())
