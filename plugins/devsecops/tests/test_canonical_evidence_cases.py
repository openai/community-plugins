"""Controlled D5 engine and admission tests, never a live denial claim."""
from copy import deepcopy
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import time
import pytest

from test_runtime_diagnosis import measured
from devsecops.factory import case_evidence as case
from devsecops.factory.state import StateError, _canonical, _json, _sha
from devsecops.adapters.http import APIError
from devsecops.factory.native_factory_session import NativeFactorySession
from devsecops.factory.state import IDENTITY_KEYS


def test_missing_stale_and_expired_controls_use_real_validators_and_preserve_original(tmp_path):
    baseline, fault = tmp_path / 'baseline.json', tmp_path / 'fault.json'
    baseline.write_bytes(_canonical(measured()[0]))
    fault.write_bytes(_canonical(measured('fault', 60)[0]))
    original = baseline.read_bytes(), fault.read_bytes()
    result = case.input_controls(baseline, fault, output=tmp_path / 'controls')
    assert [result[key]['decision'] for key in ('missing', 'stale', 'expired')] == ['unknown'] * 3
    assert result['control_scope'] == 'copies-of-original-observations-no-runtime-origin-issued'
    restored = _json((tmp_path / 'controls/restored-diagnosis.json').read_bytes())
    assert restored['decision'] == 'unknown' and restored['recovery'] == 'not-evaluated'
    assert restored['inputs']['baseline']['state'] == restored['inputs']['fault']['state'] == 'valid'
    assert original == (baseline.read_bytes(), fault.read_bytes())


def test_invalid_original_does_not_become_a_successful_negative_control(tmp_path):
    baseline, fault = tmp_path / 'baseline.json', tmp_path / 'fault.json'
    baseline.write_bytes(b'{}')
    fault.write_bytes(_canonical(measured('fault', 60)[0]))
    with pytest.raises(StateError, match='originally valid'):
        case.input_controls(baseline, fault, output=tmp_path / 'controls')


@pytest.fixture
def denial(tmp_path, monkeypatch):
    import sys
    from devsecops.factory import owned_runtime_reader as readers
    original = measured()[0]['context_snapshot']
    raw = _canonical(original['intent'])
    path = tmp_path / 'intent.json'
    path.write_bytes(raw)
    ca = tmp_path / 'ca.pem'
    ca.write_bytes(b'controlled CA bytes')
    context = SimpleNamespace(deadline=time.monotonic() + 10, run_id='same-run', phase={'id': 'evidence-gaps', 'operation': 'case'},
        identities={key: 'a' * 64 for key in IDENTITY_KEYS})
    binding = {'role': 'denied', 'selected_resource_grants': [], 'origin': 'https://cluster.test',
        'cluster_uid': 'cluster-uid', 'serviceaccount_uid': 'denied-uid', 'ca_path': str(ca),
        'ca_sha256': _sha(ca.read_bytes()), 'run_id': context.run_id, 'identities': context.identities}
    authority, journal = object(), object()
    reader = object.__new__(readers.OwnedRuntimeReader)
    bootstrap = SimpleNamespace(require_authority=lambda _: authority, runtime_reader=lambda _, role: reader)
    cases = SimpleNamespace(_admit=lambda _: None, journal=journal, operations=SimpleNamespace(bootstrap=bootstrap))
    # Controlled original-issuer boundary; the production caller still requires
    # the exact opaque type. No serialized object is admitted in production.
    monkeypatch.setitem(sys.modules, 'devsecops.factory.canonical_bootstrap', SimpleNamespace(
        require_bootstrap_authority=lambda a, j: {'kubernetes': {'origin': 'https://cluster.test', 'cluster_uid': 'cluster-uid'}}))
    def require(value, **scope):
        assert value is reader and scope == {'journal': journal, 'bootstrap_authority': authority}
        return deepcopy(binding)
    monkeypatch.setattr(readers, 'require_runtime_reader', require)
    monkeypatch.setattr(readers, 'private_token', lambda value, **scope: 'controlled-private-token')
    return context, {'inputs': {'intent': (path, _sha(raw))}}, tmp_path, cases, binding, reader, authority


@pytest.mark.parametrize('status,code,passes', [(403, 'denied', True), (401, 'denied', False),
                                             (500, 'http-error', False), (None, None, False)])
def test_only_one_original_restricted_permission_denial_is_a_control(denial, monkeypatch, status, code, passes):
    context, inputs, output, cases, binding, reader, authority = denial
    calls = []
    class Client:
        def __init__(self, selected, **options):
            assert selected is reader and options['authority'] is authority
            assert options['binding'] == binding and options['deadline'] == context.deadline
            self.receipts = []
        def get_json(self, route):
            calls.append(route)
            self.receipts.append({'status': status})
            if code:
                raise APIError(code, status)
            return {'metadata': {}}
    monkeypatch.setattr(case, '_DeniedReadClient', Client)
    if passes:
        result = case.denied_read(context, inputs=inputs, cases=cases, output=output)
        assert result['decision'] == 'unknown' and result['http_status'] == 403
    else:
        with pytest.raises(StateError, match='permission denial'):
            case.denied_read(context, inputs=inputs, cases=cases, output=output)
    assert len(calls) == 1 and calls[0].startswith('/apis/apps/v1/namespaces/')
    receipt = (output / 'denied-read-transport.json').read_text()
    assert 'controlled-private-token' not in receipt
    assert _json(receipt.encode())['serviceaccount_uid'] == 'denied-uid'


@pytest.mark.parametrize('field,value', [('role', 'runtime'), ('selected_resource_grants', [{}]),
    ('cluster_uid', 'other'), ('origin', 'https://other.test'), ('run_id', 'other')])
def test_ordinary_or_cross_scope_reader_rejected_before_read(denial, monkeypatch, field, value):
    context, inputs, output, cases, binding, _, _ = denial
    binding[field] = value
    monkeypatch.setattr(case, '_DeniedReadClient', lambda *a, **k: pytest.fail('admission must fail before read'))
    with pytest.raises(StateError, match='no-RBAC'):
        case.denied_read(context, inputs=inputs, cases=cases, output=output)


def test_denied_transport_freezes_ca_and_never_exports_secret(denial, monkeypatch):
    import os
    import ssl
    context, _, _, cases, binding, reader, authority = denial
    loaded = []
    class Trust:
        def __init__(self, *args):
            pass
        def load_verify_locations(self, *, cadata):
            loaded.append(cadata)
    monkeypatch.setattr(ssl, 'SSLContext', Trust)
    monkeypatch.setattr(ssl, 'create_default_context', lambda **_: Trust())
    before = dict(os.environ)
    client = case._DeniedReadClient(reader, journal=cases.journal, authority=authority,
                                    binding=binding, deadline=context.deadline)
    Path(binding['ca_path']).write_bytes(b'changed after original bytes frozen')
    assert loaded == ['controlled CA bytes']
    assert client._authorization() == 'Bearer controlled-private-token'
    assert dict(os.environ) == before
    assert client._max_requests == 1 and client._deadline <= context.deadline


def test_changed_ca_rejected_before_transport_setup(denial):
    context, _, _, cases, binding, reader, authority = denial
    Path(binding['ca_path']).write_bytes(b'changed')
    with pytest.raises(StateError, match='CA bytes changed'):
        case._DeniedReadClient(reader, journal=cases.journal, authority=authority,
                              binding=binding, deadline=context.deadline)


@pytest.mark.parametrize('kind', ['symlink', 'unexpected', 'oversized'])
def test_partial_native_inventory_is_bounded_and_never_follows_links(tmp_path, kind):
    root = tmp_path / 'evidence'
    root.mkdir()
    if kind == 'symlink':
        outside = tmp_path / 'outside'
        outside.write_text('preserve')
        (root / 'case-result.json').symlink_to(outside)
    elif kind == 'unexpected':
        (root / 'unclaimed-output.txt').write_text('untrusted')
    else:
        with (root / 'case-result.json').open('wb') as stream:
            stream.truncate(16_000_001)
    with pytest.raises(StateError, match='artifact|budget'):
        case.evidence_inventory(root)
    assert any(root.iterdir())


@pytest.fixture
def native_case(tmp_path, monkeypatch):
    output = tmp_path / 'restored-review'
    output.mkdir()
    paths = {}
    for name, value in (('baseline', measured()[0]), ('primary_fault', measured('fault', 60)[0])):
        path = tmp_path / (name + '.json')
        raw = _canonical(value)
        path.write_bytes(raw)
        paths[name] = (path, _sha(raw))
    package = tmp_path / 'package'
    (package / '.codex-plugin').mkdir(parents=True)
    (package / '.codex-plugin/plugin.json').write_text('{"version":"test"}')
    client = tmp_path / 'codex'
    client.write_text('controlled-client')
    selected = SimpleNamespace(executable=client, expected_client_sha256='c' * 64,
        installed_root=package, package_sha256='a' * 64, plugin_id='test@test', model='test-model',
        reasoning_effort='low', expected_client_version='test-client', engine_python=Path('/usr/bin/python3'),
        _options=lambda cancel: {})
    session = object.__new__(NativeFactorySession)
    session._cancel = threading.Event()
    monkeypatch.setattr(NativeFactorySession, 'selection_for_readonly_case', lambda self, context: selected)
    context = SimpleNamespace(run_id='parent-run', identities={key: 'a' * 64 for key in IDENTITY_KEYS},
        deadline=time.monotonic() + 120, inputs={'config': {'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}})
    owner = SimpleNamespace(driver=SimpleNamespace(_native_session=session), _admit=lambda _: None,
        journal=SimpleNamespace(state={'environment_id': 'test-env', 'native_requests': []}))
    monkeypatch.setattr(case.native_codex, '_file_digest', lambda *args: 'c' * 64)
    monkeypatch.setattr(case.native_codex, '_package', lambda *args: None)
    monkeypatch.setattr(case.native_codex, '_engine_identity', lambda *args, **kwargs: {})
    invoked, retained = [], {}
    def execute(request_path, *, run_root, request_sha256, output, **options):
        invoked.append(request_sha256)
        request_raw = request_path.read_bytes()
        request = _json(request_raw)
        report = case.diagnose_runtime(paths['baseline'][0], paths['primary_fault'][0])
        raw = _canonical(report)
        artifact_path = request['output_directory'] + '/diagnosis.json'
        (run_root / artifact_path).write_bytes(raw)
        artifact = {'name': 'diagnosis', 'kind': 'diagnosis', 'path': artifact_path, 'sha256': _sha(raw), 'bytes': len(raw)}
        result = {'schema_version': 1, 'request_id': request['request_id'], 'run_id': request['run_id'],
            'step_id': request['step_id'], 'request_sha256': request_sha256, 'status': 'completed', 'artifacts': [artifact], 'gaps': []}
        result_raw = _canonical(result)
        (run_root / request['result_path']).write_bytes(result_raw)
        invocation = {'executable_sha256': selected.expected_client_sha256, 'model_requested': selected.model,
            'reasoning_effort_requested': selected.reasoning_effort, 'client_version': selected.expected_client_version, 'engine_python': {}}
        now = datetime.now(timezone.utc).isoformat()
        execution = {'request_id': request['request_id'], 'run_id': request['run_id'], 'step_id': request['step_id'],
            'package_sha256': selected.package_sha256, 'engine_python_identity_sha256': _sha(_canonical({})),
            'started_at': now, 'finished_at': now, 'artifacts': [{**artifact, 'retained': 'diagnosis.blob'}]}
        retained.update(frozen={'request.json': request_raw, 'native-result.json': result_raw,
            'invocation.json': _canonical(invocation), 'diagnosis.blob': raw}, result=result, report=report)
        return execution
    monkeypatch.setattr(case.native_codex, 'execute_native_codex', execute)
    monkeypatch.setattr(case.native_provenance, '_retained', lambda *a, **k: (retained['frozen'], b'{}', retained['result']))
    monkeypatch.setattr(case.native_reports, '_replay', lambda *a, **k: retained['report'])
    return context, owner, paths, output, session, invoked, retained


def test_cancelled_case_never_invokes_child_and_keeps_existing_evidence(native_case):
    context, owner, paths, output, session, invoked, _ = native_case
    session._cancel.set()
    with pytest.raises(StateError, match='cancelled'):
        case.execute_restored_review(context, cases=owner, inputs=paths, output=output, maximum=90)
    assert invoked == [] and paths['baseline'][0].exists()


@pytest.mark.parametrize('damage', ['scope', 'original'])
def test_changed_scope_or_original_after_native_output_prevents_acceptance(native_case, monkeypatch, damage):
    context, owner, paths, output, _, invoked, retained = native_case
    def replay(*args, **kwargs):
        changed = output / 'scope/baseline' if damage == 'scope' else paths['baseline'][0]
        changed.write_bytes(b'changed-after-native-output')
        return retained['report']
    monkeypatch.setattr(case.native_reports, '_replay', replay)
    with pytest.raises(StateError, match='changed'):
        case.execute_restored_review(context, cases=owner, inputs=paths, output=output, maximum=90)
    state = _json((output / 'request-run/state.json').read_bytes())
    assert len(invoked) == 1 and state['active_native'] is not None and state['native_results'] == []
    assert (output / 'request-run/native/evidence-gaps-restored/result.json').exists()


def test_case_only_request_accepts_exact_native_bytes_without_advancing_parent(native_case):
    context, owner, paths, output, _, invoked, _ = native_case
    result = case.execute_restored_review(context, cases=owner, inputs=paths, output=output, maximum=90)
    assert len(invoked) == 1 and result['runtime_authority'] == 'not-issued'
    assert result['report_decision'] == 'unknown' and result['main_native_steps_advanced'] is False
    assert owner.journal.state['native_requests'] == []
    state = _json((output / 'request-run/state.json').read_bytes())
    assert len(state['native_results']) == 1 and state['active_native'] is None
