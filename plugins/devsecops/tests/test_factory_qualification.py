"""Synthetic artifact/runner contract tests, never live or media qualification."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import zipfile

import pytest

from devsecops.factory import qualification as q
from devsecops.factory import qualification_runner as runner
from devsecops.factory.qualification_contracts import HANDOFFS, HANDOFF_REQUIREMENTS, SCHEMAS
from devsecops.factory.state import StateError, _canonical, _sha


def put(root, name, value):
    raw = value if isinstance(value, bytes) else _canonical(value)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {'path': name, 'sha256': _sha(raw), 'bytes': len(raw)}


@pytest.fixture
def media(tmp_path):
    base = datetime(2026, 10, 30, tzinfo=timezone.utc)
    def at(seconds):
        return (base + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')
    root = tmp_path.resolve()
    run_root = root / 'runs/one'
    canonical = json.loads((q.SCENARIO_ROOT / 'scenario.json').read_text())['phases']
    files, phases = [], []
    for index, phase in enumerate(canonical, 1):
        reference = put(run_root, f'artifacts/{phase["id"]}.json', {'kind': 'synthetic-unit-receipt', 'identity': phase['id']})
        files.append(reference)
        phases.append({'phase_id': phase['id'], 'attempt': phase['attempt'], 'operation': phase['operation'],
                       'started_at': at(index), 'finished_at': at(index + .9), 'outcome': 'completed',
                       'artifacts': [reference]})
    manifest = {'files': files}
    manifest_ref = put(run_root, 'evidence-manifest.json', manifest)
    identity = {'client_path': '/synthetic/codex', 'client_sha256': 'c' * 64, 'client_version': 'synthetic-client',
                'model': 'synthetic-model', 'reasoning_effort': 'medium', 'provider_observed_model': None,
                'identity_basis': 'explicit-selection-and-native-receipts'}
    result = {'run_id': 'synthetic-run', 'profile': 'bigbang-live', 'environment_id': 'synthetic-environment',
              'identities': {key + '_sha256': 'a' * 64 for key in ('package', 'scenario', 'assertions', 'config', 'lock', 'environment')},
              'native_identity': identity, 'phase_records': phases, 'finalized_at': at(34),
              'evidence_manifest': manifest_ref, 'canonical_success': True, 'terminal': True, 'exit_code': 0,
              'execution_mode': 'operator', 'operation_backend': 'owned-linux', 'missing_assertions': [],
              'cleanup': {'environment_released': True}}
    result_ref = put(root, 'runs/one/e2e-result.json', result)
    chapters = [{'id': 'prerequisites', 'title': 'Starting context', 'phase_id': None, 'attempt': 'shared',
                 'source_range': {'start_seconds': 0, 'end_seconds': 1}, 'observed_at': at(.5), 'evidence_paths': []}]
    chapters += [{'id': p['phase_id'], 'title': p['phase_id'], 'phase_id': p['phase_id'], 'attempt': p['attempt'],
                  'source_range': {'start_seconds': index, 'end_seconds': index + 1}, 'observed_at': at(index + .5),
                  'evidence_paths': [item['path'] for item in p['artifacts']]} for index, p in enumerate(phases, 1)]
    chapters.append({'id': 'terminal-result', 'title': 'Result', 'phase_id': None, 'attempt': 'shared',
                     'source_range': {'start_seconds': 34, 'end_seconds': 35}, 'observed_at': at(34.5),
                     'evidence_paths': ['e2e-result.json', 'evidence-manifest.json']})
    chapter_doc = {'schema_version': 1, 'kind': 'factory-video-chapters', 'run_id': result['run_id'],
                   'duration_seconds': 35, 'chapters': chapters,
                   'handoffs': [{'kind': kind, 'source_range': {'start_seconds': 1, 'end_seconds': 34},
                                 'phase_ids': sorted(HANDOFF_REQUIREMENTS[kind][0]),
                                 'claims': [{'artifact': files[0]['path'], 'role': role,
                                 'json_pointer': '/identity', 'label': 'synthetic identity', 'value': 'preflight'}
                                 for role in sorted(HANDOFF_REQUIREMENTS[kind][1])]}
                                for kind in HANDOFFS]}
    review = {'schema_version': 1, 'kind': 'factory-video-review', 'run_id': result['run_id'],
              'profile': result['profile'], 'environment_id': result['environment_id'], 'identities': result['identities'],
              'native_identity_sha256': _sha(_canonical(identity)),
              'requested_client_model': {key: identity[key] for key in ('client_version', 'client_sha256', 'model', 'reasoning_effort')},
              'result': result_ref, 'evidence_manifest': {**manifest_ref, 'path': 'runs/one/evidence-manifest.json'},
              'video': put(root, 'media/full-demo.mp4', b'Synthetic file bytes; never claim playable media.'),
              'chapters': put(root, 'media/chapters.json', chapter_doc),
              'transcript': put(root, 'media/captions.vtt', b'WEBVTT\n\n00:00.000 --> 00:35.000\nSynthetic caption example.\n'),
              'video_format': 'mp4', 'transcript_format': 'webvtt', 'duration_seconds': 35,
              'capture_started_at': at(0), 'capture_ended_at': at(35), 'recording_date': at(0),
              'reviewer': 'synthetic-viewer', 'reviewed_at': at(40), 'reviewer_did_not_build_demo': True,
              'watched_full_delivered_video': True, 'playback_decision': 'pass', 'completeness_decision': 'pass',
              'clarity_decision': 'pass', 'continuous_source': True, 'no_off_camera_repair': True,
              'no_fixture_replacement': True, 'readable_text': True, 'narration_or_captions_explain_transitions': True,
              'no_exposed_credentials': True, 'answers': {key: 'Synthetic test answer, not a human acceptance receipt.' for key in
                ('what_deployed', 'codex_contribution', 'fault_cause', 'delivered_patch', 'recovery_and_isolation', 'how_to_rerun')},
              'confusing_steps': [], 'unresolved_material_gaps': [], 'optional_edits': []}
    record = (run_root / 'e2e-result.json', result, run_root / 'evidence-manifest.json', manifest)
    def check():
        review['chapters'] = put(root, 'media/chapters.json', chapter_doc)
        reference = put(root, 'media/video-review.json', review)
        return q.verify_video(root, reference, [record], author_id='synthetic-author')
    return root, review, chapter_doc, record, check


def test_video_consistency_is_not_machine_playback(media):
    _, _, _, _, check = media
    result = check()
    assert result['artifact_checks'] == 'pass'
    assert result['human_decisions_basis'] == 'recorded-independent-review-not-machine-playback'
    assert result['G7.V1'] == result['G10.V1'] == 'pass'


@pytest.mark.parametrize('field,value,gate', [
    ('reviewer', 'synthetic-author', 'G10.V1'), ('reviewer_did_not_build_demo', False, 'G10.V1'),
    ('watched_full_delivered_video', False, 'G7.V1'), ('playback_decision', 'incomplete', 'G7.V1'),
    ('continuous_source', False, 'G7.V1'), ('no_off_camera_repair', False, 'G7.V1'),
    ('no_fixture_replacement', False, 'G7.V1'), ('completeness_decision', 'fail', 'G7.V1'),
    ('clarity_decision', 'fail', 'G10.V1'), ('readable_text', False, 'G10.V1'),
    ('no_exposed_credentials', False, 'G10.V1'), ('unresolved_material_gaps', ['Unreadable delivery diff'], 'G10.V1'),
])
def test_recorded_review_gaps_never_pass(media, field, value, gate):
    _, review, _, _, check = media
    review[field] = value
    assert check()[gate] == 'incomplete'


@pytest.mark.parametrize('mutation', [
    lambda r, c: r.update(schema_version=2),
    lambda r, c: r.update(unrecognized='not allowed'),
    lambda r, c: r.update(run_id='different-run'),
    lambda r, c: r['requested_client_model'].update(model='different-model'),
    lambda r, c: r.update(duration_seconds=40),
    lambda r, c: r['video'].update(sha256='f' * 64),
    lambda r, c: r['video'].update(path='../escape.mp4'),
    lambda r, c: c['chapters'].pop(10),
    lambda r, c: c['chapters'][10].update(attempt='shared'),
    lambda r, c: c['chapters'][10]['source_range'].update(start_seconds=10.5),
    lambda r, c: c['chapters'][10].update(evidence_paths=[]),
    lambda r, c: c['chapters'][10].update(evidence_paths=['absent.json']),
    lambda r, c: c['chapters'][10].update(observed_at='2026-10-31T00:00:00Z'),
    lambda r, c: c['handoffs'][0]['claims'][0].update(value='invented-commit'),
    lambda r, c: c['handoffs'][0]['claims'][0].update(json_pointer='/absent'),
    lambda r, c: c['handoffs'][0].update(phase_ids=['unknown-phase']),
    lambda r, c: c['handoffs'][0].update(kind='fault-correction-recovery'),
    lambda r, c: c['handoffs'][0]['claims'].pop(),
    lambda r, c: c['handoffs'][0].update(phase_ids=['preflight']),
    lambda r, c: r['answers'].update(codex_contribution=''),
])
def test_schema_identity_timecode_and_evidence_mismatches_rejected(media, mutation):
    _, review, chapters, _, check = media
    mutation(review, chapters)
    with pytest.raises((StateError, ValueError)):
        check()


def test_media_cannot_rewrite_finalized_run(media):
    root, review, _, _, check = media
    review['video'] = put(root, 'runs/one/full-demo.mp4', b'forbidden location')
    with pytest.raises(StateError, match='outside'):
        check()


def test_symlink_media_rejected(media):
    root, _, _, _, check = media
    target = root / 'media/full-demo.mp4'
    raw = target.read_bytes()
    target.unlink()
    other = root / 'other.mp4'
    other.write_bytes(raw)
    target.symlink_to(other)
    with pytest.raises(StateError, match='Symlink'):
        check()


@pytest.mark.parametrize('caption', [b'No timestamps', b'WEBVTT\n\n00:00.000 --> 00:36.000\nBeyond source\n',
                                    b'WEBVTT\n\n00:10.000 --> 00:05.000\nReversed\n',
                                    b'WEBVTT\n\n00:00.000 --> 00:05.000\n\n1\n00:05.000 --> 00:10.000\nText\n'])
def test_invalid_caption_ranges_and_empty_cues_rejected(media, caption):
    root, review, _, _, check = media
    review['transcript'] = put(root, 'media/captions.vtt', caption)
    with pytest.raises(StateError):
        check()


def test_optional_edit_must_map_source_time_and_speed(media):
    root, review, _, _, check = media
    edit = {'video': put(root, 'media/edit.mp4', b'edit'), 'duration_seconds': 10,
            'discloses_cuts_speed_and_elapsed_time': True,
            'segments': [{'source_range': {'start_seconds': 0, 'end_seconds': 20},
                          'edit_range': {'start_seconds': 0, 'end_seconds': 10}, 'speed': 2}]}
    review['optional_edits'] = [edit]
    assert check()['artifact_checks'] == 'pass'
    edit['segments'][0]['speed'] = 1
    with pytest.raises(StateError, match='speed'):
        check()


def test_missing_native_identity_cannot_be_invented_from_review(media):
    root, review, _, record, check = media
    record[1]['native_identity'] = None
    review['native_identity_sha256'] = _sha(_canonical(None))
    review['result'] = put(root, 'runs/one/e2e-result.json', record[1])
    with pytest.raises(StateError, match='client/model'):
        check()
    review['requested_client_model'] = None
    assert check()['G7.V1'] == 'incomplete'


def test_terminal_verifier_is_required_not_success_json(media, monkeypatch):
    root, review, _, _, _ = media
    from types import SimpleNamespace
    calls = []
    def verify(path, **kwargs):
        calls.append((path, kwargs))
        raise StateError('Synthetic proof that verification is required')
    monkeypatch.setitem(sys.modules, 'devsecops.factory.terminal_evidence', SimpleNamespace(verify_terminal=verify))
    with pytest.raises(StateError, match='verification is required'):
        q._recorded_run(root, {'result': review['result']})
    assert calls[0][1] == {'expected_result_sha256': review['result']['sha256']}


def test_real_controlled_journal_publication_replays_read_only(tmp_path):
    from tests.test_factory_lifecycle import FixtureDriver, inputs_for, complete
    from tests.test_factory_state import Clock
    from devsecops.factory.lifecycle import LifecycleEngine
    from devsecops.factory.state import RunJournal
    from devsecops.factory.terminal_evidence import publish_terminal
    root = tmp_path.resolve()
    inputs, identities = inputs_for(root)
    journal = RunJournal.create(root / 'run', identities=identities,
                                environment_id=inputs['config']['environment'], clock=Clock())
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=root / 'locks', _test_driver=FixtureDriver())
    result, count = complete(engine)
    assert count == 8 and result['canonical_success'] is False
    with journal.lifecycle_lock():
        publish_terminal(journal)
    path = root / 'run/e2e-result.json'
    reference = {'path': 'run/e2e-result.json', 'sha256': _sha(path.read_bytes()), 'bytes': path.stat().st_size}
    before = {str(p.relative_to(root)): _sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    read = q._recorded_run(root, {'result': reference})
    after = {str(p.relative_to(root)): _sha(p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    assert before == after
    assert read[1]['execution_mode'] == 'controlled-test' and read[1]['canonical_success'] is False
    path.write_text(path.read_text().replace('"canonical_success":false', '"canonical_success":true'))
    reference.update(sha256=_sha(path.read_bytes()), bytes=path.stat().st_size)
    with pytest.raises(StateError, match='publication differs'):
        q._recorded_run(root, {'result': reference})


@pytest.mark.parametrize('canonical_receipts', [False, True])
def test_gate_aggregator_separates_canonical_proof_from_full_release(media, monkeypatch, canonical_receipts):
    root, review, chapters, record, check = media
    check()
    runs, records = [], []
    for index, role in enumerate(('fresh-1', 'fresh-2', 'reset-replay')):
        result = deepcopy(record[1])
        result['run_id'] = 'synthetic-run' if index == 0 else f'synthetic-run-{index}'
        path = f'runs/{"one" if index == 0 else index}/e2e-result.json'
        reference = put(root, path, result)
        runs.append({'role': role, 'result': reference, 'operator_id': 'synthetic-operator',
                     'operator_attested_at': '2026-10-30T00:01:00Z', 'operator_performed_run': True,
                     'environment_origin': 'fresh', 'cluster_uid': f'declared-{index}', 'environment_evidence': []})
        records.append((root / path, result, record[2], record[3]))
    qualification = {'schema_version': 1, 'kind': 'factory-release-qualification', 'qualification_id': 'synthetic-qualification',
                     'created_at': '2026-10-30T00:02:00Z', 'author_id': 'synthetic-author',
                     **{key: 'a' * 64 for key in ('package_manifest_sha256', 'scenario_sha256', 'assertions_sha256', 'lock_sha256')},
                     'runs': runs, 'video_review': put(root, 'media/video-review.json', review), 'additional_gate_evidence': []}
    path = root / 'qualification.json'
    put(root, path.name, qualification)
    monkeypatch.setattr(q, '_recorded_run', lambda root, entry: records[{'fresh-1': 0, 'fresh-2': 1, 'reset-replay': 2}[entry['role']]])
    from devsecops.factory import ci_evidence_qualification
    monkeypatch.setattr(ci_evidence_qualification, 'verify_ci_bundles', lambda *args: [])
    if canonical_receipts:
        from devsecops.factory import fresh_qualification, native_qualification
        # This aggregator test substitutes already-qualified adapter decisions;
        # separate contract tests exercise actual retained receipt validation.
        def fresh_proof(path, result, manifest, evidence):
            index = 0 if result['run_id'] == 'synthetic-run' else 1
            uid = 'declared-' + str(index)
            return {'status': 'pass', 'cluster_uid': uid, 'generation': 'generation-' + str(index),
                'root_identity': {'device': 1, 'inode': index + 1}, 'disk_identity': {'device': 1, 'inode': index + 3},
                'cluster_fingerprint': {'namespaces': {'kube-system': uid}, 'nodes': {'node': uid}, 'ca_sha256': str(index) * 64}}
        monkeypatch.setattr(fresh_qualification, 'verify_fresh_foundation', fresh_proof)
        monkeypatch.setattr(native_qualification, 'verify_native_receipts', lambda *args: {'status': 'pass'})
    result = q.qualify(path)
    assert result['release_qualified'] is False and result['exit_code'] == 2
    assert all(result['gates'][f'G{i}']['status'] == 'incomplete' for i in range(1, 11) if i != 7)
    if canonical_receipts:
        assert result['gates']['G7']['status'] == result['gates']['G7']['canonical_evidence'] == 'pass'
        assert result['gates']['G7']['gaps'] == []
    else:
        assert result['gates']['G7']['status'] == 'incomplete'
        assert any('fresh-cluster-provenance' in gap for gap in result['gates']['G7']['gaps'])
    q.write_report(result, root / 'reports')
    assert {p.name for p in (root / 'reports').iterdir()} == {'report.json', 'report.md', 'report.html'}
    with pytest.raises(StateError, match='new'):
        q.write_report(result, root / 'reports')
    with pytest.raises(StateError, match='outside'):
        q.qualify_and_write(path, root / 'runs/one/reports')


def test_checked_in_schemas_match_the_runtime_contracts():
    root = Path(q.__file__).resolve().parents[3]
    for name, expected in SCHEMAS.items():
        assert json.loads((root / 'schemas' / (name + '.schema.json')).read_text()) == expected


@pytest.fixture
def invocation(tmp_path, monkeypatch):
    root = tmp_path.resolve() / 'codex/plugins/cache/devsecops-local/devsecops/0.2.0-test'
    root.mkdir(parents=True)
    author = tmp_path.resolve() / 'author'
    author.mkdir()
    monkeypatch.setattr(runner, 'PACKAGE_ROOT', root)
    monkeypatch.setattr(runner.native_codex, '_package', lambda *args: (b'manifest', {
        name: 'd' * 64 for name in ('scripts/factory_demo.py', 'scripts/qualify_release.py',
                                  'src/devsecops/factory/terminal_evidence.py', 'src/devsecops/factory/qualification.py')}))
    engine = tmp_path.resolve()/'engine'
    (engine/'bin').mkdir(parents=True)
    (engine/'bin/python').write_bytes(b'synthetic interpreter; never executed')
    imports = []
    for name in ('jsonschema', 'yaml', 'zstandard'):
        module = engine/'lib'/name/'__init__.py'
        module.parent.mkdir(parents=True)
        module.write_bytes(b'# inert dependency identity')
        imports.append({'module': name, 'file': str(module)})
    engine_identity = {'resolved_executable': str(engine/'bin/python'),
        'runtime': {'executable': str(engine/'bin/python'), 'prefix': str(engine),
                    'base_prefix': str(engine), 'imports': imports}}
    monkeypatch.setattr(runner.native_codex, '_engine_identity', lambda *args, **kwargs: engine_identity)
    calls = []
    def process(argv, **kwargs):
        calls.append((argv, kwargs))
        return {'returncode': 3, 'reason': None, 'stdout': b'{"exit_code":3}', 'stderr': b'', 'spawned': True}
    monkeypatch.setattr(runner.native_codex, '_process', process)
    arguments = ['e2e', '--profile', 'bigbang-live', '--operation-backend', 'owned-linux',
                 '--plugin-id', 'devsecops@devsecops-local', '--plugin-version', '0.2.0-test',
                 '--config', str(tmp_path / 'config.json'), '--lock', str(tmp_path / 'lock.json'),
                 '--environment', 'synthetic', '--environment-inventory', str(tmp_path / 'inventory.json'),
                 '--output', str(tmp_path / 'run'), '--package-manifest', str(root / 'SHA256SUMS')]
    settings = {'installed_root': root, 'package_sha256': 'a' * 64, 'plugin_id': 'devsecops@devsecops-local',
                'plugin_version': '0.2.0-test', 'engine_python': tmp_path / 'engine/bin/python',
                'author_checkout': author, 'output': tmp_path / 'invocation', 'timeout': 60, 'arguments': arguments}
    return settings, calls


def test_canonical_runner_preserves_handoff_and_uses_only_fixed_installed_entrypoint(invocation):
    settings, calls = invocation
    result = runner.run_canonical(**settings)
    assert result['status'] == 'native-handoff-required' and not result['canonical_success']
    assert calls[0][0] == [str(settings['engine_python']), '-I', '-B',
                            str(settings['installed_root'] / 'scripts/factory_demo.py'), *settings['arguments']]
    assert (settings['output'] / 'started.json').exists()
    assert result['automatic_retry'] is False


def test_uncertain_canonical_invocation_never_retries(invocation, monkeypatch):
    settings, _ = invocation
    calls = []
    def process(*args, **kwargs):
        calls.append(1)
        return {'returncode': None, 'reason': 'deadline-exceeded', 'stdout': b'', 'stderr': b'', 'spawned': True}
    monkeypatch.setattr(runner.native_codex, '_process', process)
    assert runner.run_canonical(**settings)['status'] == 'uncertain-needs-reconciliation'
    assert len(calls) == 1
    with pytest.raises(StateError, match='new'):
        runner.run_canonical(**settings)
    assert len(calls) == 1


def test_runner_cannot_use_author_source_or_other_command(invocation):
    settings, calls = invocation
    settings['installed_root'] = settings['author_checkout']
    with pytest.raises(StateError, match='outside'):
        runner.run_canonical(**settings)
    assert calls == []


def test_runner_zero_exit_requires_verified_terminal_result(invocation, monkeypatch):
    settings, _ = invocation
    monkeypatch.setattr(runner.native_codex, '_process', lambda *args, **kwargs:
                        {'returncode': 0, 'reason': None, 'stdout': b'{"canonical_success":true}', 'stderr': b'', 'spawned': True})
    from types import SimpleNamespace
    def verify(*args, **kwargs):
        raise StateError('Synthetic missing final proof')
    monkeypatch.setitem(sys.modules, 'devsecops.factory.terminal_evidence', SimpleNamespace(verify_terminal=verify))
    result = runner.run_canonical(**settings)
    assert result['status'] == 'terminal-result-not-qualified' and result['canonical_success'] is False


def test_candidate_staging_is_exact_and_never_an_install(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    client = root / 'codex'
    client.write_bytes(b'synthetic installer bytes; never executed')
    files = {'.codex-plugin/plugin.json': _canonical({'name': 'devsecops', 'version': '0.2.0-test'}),
             'scripts/factory_demo.py': b'# synthetic', 'scripts/qualify_release.py': b'# synthetic',
             'src/devsecops/factory/qualification.py': b'# synthetic'}
    files['SHA256SUMS'] = ''.join(f'{_sha(raw)}  {name}\n' for name, raw in sorted(files.items())).encode()
    archive = root / 'candidate.zip'
    with zipfile.ZipFile(archive, 'w') as zipped:
        for name, raw in files.items():
            zipped.writestr('devsecops/' + name, raw)
    plan = runner.stage_candidate(archive=archive, archive_sha256=_sha(archive.read_bytes()),
             package_sha256=_sha(files['SHA256SUMS']), plugin_id='devsecops@devsecops-local', plugin_version='0.2.0-test',
             author_checkout=root / 'author', output=root / 'staged', codex_executable=client,
             codex_sha256=_sha(client.read_bytes()), codex_version=runner.native_codex.CLIENT_VERSION, codex_home=root / 'fresh-home')
    assert plan['status'] == 'staged-not-installed' and plan['release_qualified'] is False
    assert not (root / 'fresh-home').exists()
    assert plan['commands'][2] == [str(client), 'plugin', 'add', 'devsecops@devsecops-local', '--json']
    assert (root / 'staged/marketplace/plugins/devsecops/SHA256SUMS').read_bytes() == files['SHA256SUMS']


@pytest.mark.parametrize('field', ['resolved_executable', 'executable', 'prefix', 'base_prefix', 'import'])
@pytest.mark.parametrize('location', ['author', 'cache'])
def test_runner_rejects_actual_engine_and_dependency_origins_in_author_or_cache(invocation, monkeypatch, field, location):
    settings, calls = invocation
    identity = deepcopy(runner.native_codex._engine_identity(None))
    forbidden = settings['author_checkout'] if location == 'author' else settings['installed_root'].parents[2]
    path = forbidden/'unexpected-runtime-origin'
    path.write_bytes(b'synthetic origin; never executed')
    if field == 'resolved_executable':
        identity[field] = str(path)
    elif field == 'import':
        identity['runtime']['imports'][0]['file'] = str(path)
    else:
        identity['runtime'][field] = str(path)
    monkeypatch.setattr(runner.native_codex, '_engine_identity', lambda *args, **kwargs: identity)
    with pytest.raises(StateError, match='actual import origin'):
        runner.run_canonical(**settings)
    assert calls == [] and not settings['output'].exists()


def test_runner_checks_resolved_venv_binary_without_rewriting_invocation_path(invocation, monkeypatch):
    settings, calls = invocation
    identity = deepcopy(runner.native_codex._engine_identity(None))
    original = settings['engine_python']
    target = original.with_name('real-python')
    original.rename(target)
    original.symlink_to(target)
    identity['resolved_executable'] = str(target)
    monkeypatch.setattr(runner.native_codex, '_engine_identity', lambda *args, **kwargs: identity)
    assert runner.run_canonical(**settings)['status'] == 'native-handoff-required'
    assert calls[0][0][0] == str(original)
