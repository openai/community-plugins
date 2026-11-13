"""Synthetic complete traces test receipt consistency, never model execution."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest

from devsecops.factory.native_qualification import verify_native_receipts
from devsecops.factory.qualification import QualificationError
from devsecops.factory.state import IDENTITY_KEYS, _canonical, _sha


@pytest.fixture
def evidence(tmp_path):
    package = b'controlled-package-manifest\n'
    identities = {key: 'a' * 64 for key in IDENTITY_KEYS}
    identities['package_sha256'] = _sha(package)
    run = str(uuid4())
    moment = datetime(2026, 10, 30, tzinfo=timezone.utc)
    def at(n):
        return (moment + timedelta(seconds=n)).isoformat()
    identity = {'client_path': '/tools/codex', 'client_sha256': 'c' * 64, 'client_version': 'test-client', 'model': 'test-model',
                'reasoning_effort': 'low', 'provider_observed_model': None}
    result = {'run_id': run, 'identities': identities, 'native_driver': 'codex-cli', 'native_identity': identity,
              'native_results': [], 'phase_records': []}
    files, reports = [], []
    def put(name, raw):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        ref = {'path': name, 'sha256': _sha(raw), 'bytes': len(raw)}
        files.append(ref)
        return ref
    for index, (attempt, suffix, workflow) in enumerate((attempt, suffix, workflow)
            for attempt in ('primary', 'replay') for suffix, workflow in (
                ('review', 'review-kubernetes-change'), ('diagnose', 'diagnose-gitops-deployment'),
                ('correction', 'prepare-gitops-correction'), ('recovery', 'verify-gitops-recovery'))):
        step, request_id = attempt + '-' + suffix, str(uuid4())
        request = {'request_id': request_id, 'run_id': run, 'step_id': step, 'identities': identities,
                   'workflow': workflow, 'created_at': at(index * 10), 'expires_at': at(index * 10 + 9)}
        request_raw = _canonical(request)
        output = {'schema_version': 1, 'request_id': request_id, 'run_id': run, 'step_id': step,
            'request_sha256': _sha(request_raw), 'status': 'completed', 'gaps': [], 'artifacts': [
                {'name': 'test-artifact', 'kind': 'diagnosis', 'path': 'test/artifact', 'sha256': 'd' * 64, 'bytes': 1}]}
        output_raw = _canonical(output)
        trace = b''.join(_canonical(item) for item in (
            {'type': 'thread.started', 'thread_id': 'test-' + str(index)}, {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': output_raw.decode()}},
            {'type': 'turn.completed'}))
        report = {'request_id': request_id, 'kind': 'native-codex-execution', 'run_id': run, 'step_id': step, 'workflow': workflow,
            'execution_status': 'complete', 'scope': 'native-handoff', 'exit_code': 0, 'process_started': True,
            'gaps': [], 'thread_id': 'test-' + str(index), 'trace_sha256': _sha(trace),
            'result_sha256': _sha(output_raw), 'request_sha256': _sha(request_raw),
            'package_sha256': identities['package_sha256'], 'executable_sha256': identity['client_sha256'],
            'client_version': identity['client_version'], 'model_requested': identity['model'],
            'reasoning_effort_requested': identity['reasoning_effort'],
            'started_at': at(index * 10 + 1), 'finished_at': at(index * 10 + 5)}
        invocation = {'argv': [identity['client_path'], 'exec'], 'package_sha256': identities['package_sha256'], 'executable_sha256': identity['client_sha256'],
            'model_requested': identity['model'], 'reasoning_effort_requested': identity['reasoning_effort'],
            'client_version': identity['client_version'], 'plugin_id': 'plugin@market',
            'installed_root': '/home/operator/.codex/plugins/cache/market/plugin/1.0',
            'installed_plugin': {'pluginId': 'plugin@market', 'installed': True, 'enabled': True, 'version': '1.0'}}
        frozen = {'request.json': request_raw, 'native-result.json': output_raw, 'trace.jsonl': trace,
                  'execution.json': _canonical(report), 'invocation.json': _canonical(invocation),
                  'package.SHA256SUMS': package, 'prompt.txt': b'controlled prompt', 'stderr.log': b''}
        phase_refs, per_name = [], {}
        for n, (name, raw) in enumerate(frozen.items()):
            ref = put(f'phase-{index}/{n}', raw)
            phase_refs.append(ref)
            per_name[name] = {key: ref[key] for key in ('sha256', 'bytes')}
            if name == 'execution.json':
                reports.append((ref, report))
        phase_refs.append(put(f'phase-{index}/manifest', _canonical({'schema_version': 1, 'files': per_name})))
        validation = {'request_sha256': _sha(request_raw), 'result_sha256': _sha(output_raw),
                      'validated_at': at(index * 10 + 6)}
        validation_ref = put(f'native/{step}/validation', _canonical(validation))
        result['native_results'].append({'step_id': step, 'request_id': request_id, 'status': 'completed',
            'result': {'sha256': _sha(output_raw)}, 'validation': validation_ref})
        result['phase_records'].append({'phase_id': step, 'artifacts': phase_refs,
            'started_at': at(index * 10), 'finished_at': at(index * 10 + 8)})
    return tmp_path, result, {'files': files}, reports


def test_eight_numeric_registered_execution_sets_validate_without_live_origin(evidence):
    root, result, manifest, _ = evidence
    proof = verify_native_receipts(root / 'e2e-result.json', result, manifest)
    assert proof['status'] == 'pass' and len(proof['steps']) == 8
    assert proof['provider_observed_model'] is None


def test_missing_actual_execution_cannot_be_replaced_by_selected_identity(evidence):
    root, result, manifest, _ = evidence
    result['phase_records'][0]['artifacts'] = []
    assert verify_native_receipts(root / 'e2e-result.json', result, manifest)['status'] == 'incomplete'


def test_changed_native_client_selection_does_not_qualify(evidence):
    root, result, manifest, _ = evidence
    result['native_identity']['model'] = 'different'
    with pytest.raises(QualificationError, match='selected package/client/model'):
        verify_native_receipts(root / 'e2e-result.json', result, manifest)


def test_unregistered_execution_member_does_not_qualify(evidence):
    root, result, manifest, _ = evidence
    result['phase_records'][0]['artifacts'].pop(0)
    with pytest.raises(QualificationError, match='absent from its phase'):
        verify_native_receipts(root / 'e2e-result.json', result, manifest)


def test_schema_consistent_foreign_package_does_not_qualify(evidence):
    root, result, manifest, _ = evidence
    changed = deepcopy(result)
    changed['identities']['package_sha256'] = 'f' * 64
    with pytest.raises(QualificationError):
        verify_native_receipts(root / 'e2e-result.json', changed, manifest)
