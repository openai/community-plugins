"""Read-only validation of the eight terminal-bound native execution receipts."""
from pathlib import Path
import tempfile

from .state import _json, _read, _sha, _time


def verify_native_receipts(result_path, result, manifest):
    from .qualification import QualificationError, _document
    from .native_provenance import _retained
    root = Path(result_path).parent
    identity = result.get('native_identity')
    if identity is None or result.get('native_driver') != 'codex-cli':
        return {'status': 'incomplete', 'gap': 'actual-selected-native-client-and-receipts-required'}
    files = {row['path']: row for row in manifest['files']}
    phases = {row['phase_id']: row for row in result['phase_records']}
    required = {f'{attempt}-{suffix}': workflow for attempt in ('primary', 'replay') for suffix, workflow in (
        ('review', 'review-kubernetes-change'), ('diagnose', 'diagnose-gitops-deployment'),
        ('correction', 'prepare-gitops-correction'), ('recovery', 'verify-gitops-recovery'))}
    found = {}
    documents = {}
    for step in required:
        for reference in phases[step]['artifacts']:
            if reference['bytes'] > 1_000_000:
                continue
            name = reference['path']
            try:
                report = _document(root, files[name])[1]
            except (ValueError, UnicodeError):
                continue  # Native traces, patches and binary artifacts are separate bound bytes.
            if not isinstance(report, dict):
                continue
            documents[name] = report
            if (report.get('kind') != 'native-codex-execution' or report.get('run_id') != result['run_id']
                    or report.get('step_id') != step):
                continue
            if step in found:
                raise QualificationError('Canonical native step has ambiguous execution receipts')
            found[step] = (name, report)
    if set(found) != set(required):
        return {'status': 'incomplete', 'gap': 'eight-actual-native-execution-receipts-not-terminal-bound'}
    summaries = []
    accepted = {row['step_id']: row for row in result['native_results']}
    for step, workflow in required.items():
        name, report = found[step]
        phase = phases[step]
        phase_refs = phase['artifacts']
        candidates = [(path, value) for path, value in documents.items()
            if path in {row['path'] for row in phase_refs} and set(value) == {'schema_version', 'files'}
            and value.get('schema_version') == 1 and isinstance(value.get('files'), dict)
            and value['files'].get('execution.json') == {key: files[name][key] for key in ('sha256', 'bytes')}]
        if len(candidates) != 1:
            raise QualificationError('Native phase lacks its exact original execution manifest')
        manifest_path, execution_manifest = candidates[0]
        if not 8 <= len(execution_manifest['files']) <= 100:
            raise QualificationError('Native execution manifest exceeds its fixed member bound')
        # Phase registration freezes files under numeric names. Reconstruct only
        # bounded byte copies in temporary validation storage, never a live origin.
        with tempfile.TemporaryDirectory(prefix='factory-native-receipt-') as temporary:
            directory = Path(temporary).resolve()
            total = 0
            for member, reference in execution_manifest['files'].items():
                if not isinstance(member, str) or not member or '/' in member or '\\' in member or member in ('.', '..', 'manifest.json'):
                    raise QualificationError('Native execution manifest has an unsupported member')
                if (type(reference) is not dict or set(reference) != {'sha256', 'bytes'}
                        or type(reference['bytes']) is not int or not 0 <= reference['bytes'] <= 16_000_000):
                    raise QualificationError('Native execution manifest has an unsupported reference')
                total += reference['bytes']
                if total > 64_000_000:
                    raise QualificationError('Native execution manifest exceeds its phase byte bound')
                matching = [row for row in phase_refs if all(row[key] == reference[key] for key in ('sha256', 'bytes'))]
                if not matching:
                    raise QualificationError('Native execution member is absent from its phase artifacts')
                raw = _read(root / matching[0]['path'], 64_000_000)
                if _sha(raw) != reference['sha256'] or len(raw) != reference['bytes']:
                    raise QualificationError('Native registered artifact changed during validation')
                (directory / member).write_bytes(raw)
            (directory / 'manifest.json').write_bytes(_read(root / manifest_path, 1_000_000))
            frozen, _, native_result = _retained(directory, report, workflow=workflow)
        invocation = _json(frozen['invocation.json'])
        request = _json(frozen['request.json'])
        original = accepted.get(step)
        if (original is None or original['status'] != 'completed' or native_result['status'] != 'completed'
                or original['request_id'] != request['request_id'] or original['result']['sha256'] != _sha(frozen['native-result.json'])
                or any(native_result.get(key) != request.get(key) or report.get(key) != request.get(key)
                       for key in ('request_id', 'run_id', 'step_id'))
                or request.get('workflow') != workflow
                or native_result.get('request_sha256') != report['request_sha256']
                or not isinstance(invocation.get('argv'), list) or not invocation['argv']
                or invocation['argv'][0] != identity['client_path']
                or report['package_sha256'] != result['identities']['package_sha256']
                or _sha(frozen['package.SHA256SUMS']) != result['identities']['package_sha256']
                or request['identities'] != result['identities'] or request['run_id'] != result['run_id']
                or report['executable_sha256'] != identity['client_sha256']
                or report['client_version'] != identity['client_version'] or report['model_requested'] != identity['model']
                or report['reasoning_effort_requested'] != identity['reasoning_effort']
                or invocation['package_sha256'] != result['identities']['package_sha256']
                or invocation['executable_sha256'] != identity['client_sha256']
                or invocation['model_requested'] != identity['model']
                or invocation['reasoning_effort_requested'] != identity['reasoning_effort']
                or invocation['client_version'] != identity['client_version']
                or not (_time(phase['started_at']) <= _time(report['started_at']) <= _time(report['finished_at'])
                        <= _time(phase['finished_at']))):
            raise QualificationError('Actual native invocation differs from accepted run or selected package/client/model')
        installed = invocation['installed_plugin']
        installed_root = Path(invocation['installed_root'])
        if (installed.get('installed') is not True or installed.get('enabled') is not True
                or installed.get('pluginId') != invocation['plugin_id'] or not installed_root.is_absolute()
                or '..' in installed_root.parts or len(installed_root.parts) < 5
                or installed_root.parts[-5:-3] != ('plugins', 'cache')
                or installed_root.name != installed.get('version')):
            raise QualificationError('Native run did not record the actual installed and enabled package path')
        validation = _document(root, original['validation'])[1]
        if (validation['request_sha256'] != report['request_sha256']
                or validation['result_sha256'] != report['result_sha256']
                or not _time(report['finished_at']) <= _time(validation['validated_at']) < _time(request['expires_at'])):
            raise QualificationError('Native independent acceptance receipt differs from execution')
        summaries.append({'step_id': step, 'execution_receipt': name, 'request_id': request['request_id'],
                          'result_sha256': report['result_sha256'], 'validation_sha256': original['validation']['sha256']})
    return {'status': 'pass', 'basis': 'terminal-bound-native-trace-and-independent-acceptance-consistency',
            'steps': summaries, 'provider_observed_model': identity['provider_observed_model']}
