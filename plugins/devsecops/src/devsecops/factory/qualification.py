"""Read-only qualification of immutable run and media evidence.

Hashes establish consistency, not provenance. This validator never treats JSON
as a native capability, a fresh-cluster attestation, or a model execution. Human
playback/comprehension decisions are reported explicitly as recorded decisions.
Unimplemented release-gate adapters remain incomplete. Fresh RKE2 uses exact
terminal-bound producer and cleanup receipts, never an environment label.
"""
from __future__ import annotations

import hashlib
import html
import os
from pathlib import Path
import re
import stat

from jsonschema import Draft202012Validator, FormatChecker

from .lifecycle import SCENARIO_ROOT
from .qualification_contracts import HANDOFFS, HANDOFF_REQUIREMENTS, SCHEMAS
from .state import MAX_DOCUMENT_BYTES, StateError, _canonical, _exclusive, _json, _read, _safe_path, _sha, _time

MAX_FILES = 100_000
MAX_MEDIA_BYTES = 1_000_000_000_000
SCOPE = ('G7 canonical run consistency', 'G7.V1 recorded completeness', 'G10.V1 recorded viewer acceptance')


class QualificationError(StateError):
    """Sanitized contract error, distinct from an unmet release criterion."""


def validate(name, value):
    if next(Draft202012Validator(SCHEMAS[name], format_checker=FormatChecker()).iter_errors(value), None):
        raise QualificationError('Invalid ' + name + ' contract')
    return value


def _relative(root, relative):
    path = Path(relative)
    if (not isinstance(relative, str) or path.is_absolute() or not relative or
            any(part in ('', '.', '..') for part in relative.split('/')) or '\\' in relative):
        raise QualificationError('Evidence references must be normalized relative paths')
    result = _safe_path(root / path)
    if not result.is_relative_to(root):
        raise QualificationError('Evidence reference escapes its bundle')
    return result


def file_reference(root, reference, *, document=False):
    """Hash a bounded regular file without retaining media in memory."""
    path = _relative(root, reference['path'])
    maximum = MAX_DOCUMENT_BYTES if document else MAX_MEDIA_BYTES
    digest, total = hashlib.sha256(), 0
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise QualificationError('Referenced artifact is not a bounded regular file')
        while chunk := stream.read(1_048_576):
            total += len(chunk)
            if total > maximum:
                raise QualificationError('Referenced artifact exceeds its bound')
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or total != reference['bytes'] or digest.hexdigest() != reference['sha256']):
        raise QualificationError('Referenced artifact identity differs or changed during reading')
    return path


def _document(root, reference, name=None):
    path = file_reference(root, reference, document=True)
    raw = _read(path, MAX_DOCUMENT_BYTES)
    if len(raw) != reference['bytes'] or _sha(raw) != reference['sha256']:
        raise QualificationError('Referenced document changed during validation')
    value = _json(raw)
    return path, validate(name, value) if name else value


def _range(value, duration):
    start, end = value['start_seconds'], value['end_seconds']
    if not 0 <= start < end <= duration:
        raise QualificationError('Timecode range is outside its source media')
    return start, end


def _pointer(value, pointer):
    if pointer == '':
        return value
    if not pointer.startswith('/') or re.search(r'~(?![01])', pointer):
        raise QualificationError('An evidence identity pointer is malformed')
    try:
        for part in pointer[1:].split('/'):
            part = part.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                if not re.fullmatch(r'0|[1-9][0-9]*', part):
                    raise KeyError
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                raise KeyError
        return value
    except (KeyError, IndexError, ValueError):
        raise QualificationError('An evidence identity pointer does not resolve') from None


def _captions(path, kind, duration):
    raw = _read(path, MAX_DOCUMENT_BYTES).decode('utf-8-sig').replace('\r\n', '\n')
    if kind == 'webvtt' and not raw.startswith('WEBVTT'):
        raise QualificationError('The declared WebVTT transcript has no header')
    separator = '.' if kind == 'webvtt' else ','
    stamp = r'(?:([0-9]{2,}):)?([0-5][0-9]):([0-5][0-9])' + re.escape(separator) + r'([0-9]{3})'
    line = re.compile(r'^' + stamp + r' --> ' + stamp + r'(?:[ \t].*)?$', re.MULTILINE)
    def seconds(parts):
        hours, minutes, sec, millis = parts
        return int(hours or 0) * 3600 + int(minutes) * 60 + int(sec) + int(millis) / 1000
    matches = list(line.finditer(raw))
    if not matches or len(matches) > 100_000 or raw.count('-->') != len(matches):
        raise QualificationError('The transcript requires bounded valid timecoded cues')
    previous = -1
    for index, match in enumerate(matches):
        start, end = seconds(match.groups()[:4]), seconds(match.groups()[4:])
        cue_text = raw[match.end():].split('\n\n', 1)[0].strip()
        if not 0 <= start < end <= duration + 0.001 or start < previous or not cue_text:
            raise QualificationError('Transcript cue content or timing is invalid')
        previous = start


def _recorded_run(root, entry):
    result_path, result = _document(root, entry['result'])
    if result_path.name != 'e2e-result.json':
        raise QualificationError('Canonical results require their fixed terminal filename')
    # The terminal verifier replays the immutable journal and compares publication
    # against final state. Merely trusting a successful result JSON is insufficient.
    from .terminal_evidence import verify_terminal
    verified = verify_terminal(result_path.parent, expected_result_sha256=entry['result']['sha256'])
    if verified['result'] != result:
        raise QualificationError('Terminal publication differs from independently replayed evidence')
    manifest_path = file_reference(result_path.parent, result['evidence_manifest'])
    return result_path, result, manifest_path, verified['manifest']


def _media_outside_runs(path, run_roots):
    if any(path.is_relative_to(root) for root in run_roots):
        raise QualificationError('Video evidence must remain outside every finalized run directory')


def verify_video(root, reference, runs, *, author_id):
    review_path, review = _document(root, reference, 'video-review')
    run = next((item for item in runs if item[1]['run_id'] == review['run_id']), None)
    if run is None:
        raise QualificationError('Recording is not one of the qualifying canonical runs')
    result_path, result, manifest_path, manifest = run
    roots = [item[0].parent for item in runs]
    _media_outside_runs(review_path, roots)
    for name, expected in (('result', result_path), ('evidence_manifest', manifest_path)):
        actual = file_reference(root, review[name], document=name == 'result')
        if actual != expected:
            raise QualificationError('Recording references another terminal result or manifest')
    if any(review[key] != result[key] for key in ('run_id', 'profile', 'environment_id', 'identities')):
        raise QualificationError('Recording identities differ from the filmed canonical run')
    if _sha(_canonical(result['native_identity'])) != review['native_identity_sha256']:
        raise QualificationError('Recording client/model identity differs from the terminal record')
    native = result['native_identity']
    expected_native = None if native is None else {key: native[key] for key in
                        ('client_version', 'client_sha256', 'model', 'reasoning_effort')}
    if review['requested_client_model'] != expected_native:
        raise QualificationError('Recorded requested client/model does not match the canonical selection')
    paths = {key: file_reference(root, review[key], document=key != 'video')
             for key in ('video', 'chapters', 'transcript')}
    for path in paths.values():
        _media_outside_runs(path, roots)
    if len(set(paths.values())) != 3 or review_path in paths.values():
        raise QualificationError('Video and companion files must be distinct artifacts')
    if paths['video'].suffix.lower() != '.' + review['video_format']:
        raise QualificationError('The declared playable media format differs from its filename')
    _, chapter_doc = _document(root, review['chapters'], 'video-chapters')
    duration = review['duration_seconds']
    if chapter_doc['duration_seconds'] != duration or chapter_doc['run_id'] != result['run_id']:
        raise QualificationError('Chapter source duration or run identity differs')
    started, ended = _time(review['capture_started_at']), _time(review['capture_ended_at'])
    if abs((ended - started).total_seconds() - duration) > 1:
        raise QualificationError('Continuous source duration differs from capture wall time')
    if not started <= _time(review['recording_date']) <= ended < _time(review['reviewed_at']):
        raise QualificationError('Recording and reviewer timestamps are out of order')
    if not started < _time(result['phase_records'][0]['started_at']) <= _time(result['finalized_at']) < ended:
        raise QualificationError('Capture does not surround the complete canonical execution')
    chapters = chapter_doc['chapters']
    if (chapters[0]['id'] != 'prerequisites' or chapters[-1]['id'] != 'terminal-result'
            or chapters[0]['phase_id'] is not None or chapters[-1]['phase_id'] is not None
            or len({item['id'] for item in chapters}) != len(chapters)):
        raise QualificationError('Chapters must include unique prerequisite and terminal-result boundaries')
    files = {item['path']: item for item in manifest['files']}
    all_evidence = set(files) | {'e2e-result.json', 'evidence-manifest.json'}
    previous = 0
    for chapter in chapters:
        start, end = _range(chapter['source_range'], duration)
        if abs(start - previous) > 0.001:
            raise QualificationError('Full source chapter coverage contains a gap or overlap')
        observed = (_time(chapter['observed_at']) - started).total_seconds()
        if not start - 1 <= observed <= end + 1 or not set(chapter['evidence_paths']) <= all_evidence:
            raise QualificationError('Chapter observation or exact evidence mapping is invalid')
        previous = end
    if abs(previous - duration) > 0.001:
        raise QualificationError('Chapter coverage does not reach the end of the source video')
    for chapter, phase in zip(chapters[1:-1], result['phase_records'], strict=True):
        if chapter['phase_id'] != phase['phase_id'] or chapter['attempt'] != phase['attempt']:
            raise QualificationError('Chapter order or attempt differs from the canonical 33-phase graph')
        phase_paths = {item['path'] for item in phase['artifacts']}
        if not phase_paths or not phase_paths <= set(chapter['evidence_paths']):
            raise QualificationError('A chapter omits its exact phase receipt artifacts')
        start, end = _range(chapter['source_range'], duration)
        if not (start - 1 <= (_time(phase['started_at']) - started).total_seconds()
                <= (_time(phase['finished_at']) - started).total_seconds() <= end + 1):
            raise QualificationError('Chapter timecodes do not contain the actual recorded phase interval')
    terminal_start, terminal_end = _range(chapters[-1]['source_range'], duration)
    if ('e2e-result.json' not in chapters[-1]['evidence_paths']
            or not terminal_start - 1 <= (_time(result['finalized_at']) - started).total_seconds() <= terminal_end):
        raise QualificationError('The terminal-result chapter does not map the finalized result')
    if {item['kind'] for item in chapter_doc['handoffs']} != set(HANDOFFS):
        raise QualificationError('The five required observable handoffs are not covered')
    by_phase = {item['phase_id']: item for item in chapters[1:-1]}
    for handoff in chapter_doc['handoffs']:
        start, end = _range(handoff['source_range'], duration)
        if not set(handoff['phase_ids']) <= by_phase.keys():
            raise QualificationError('A handoff refers to an unknown canonical phase')
        required_phases, required_roles = HANDOFF_REQUIREMENTS[handoff['kind']]
        if (not required_phases <= set(handoff['phase_ids'])
                or not required_roles <= {claim['role'] for claim in handoff['claims']}):
            raise QualificationError('A handoff omits required phase or identity-role coverage')
        for phase_id in handoff['phase_ids']:
            pstart, pend = _range(by_phase[phase_id]['source_range'], duration)
            if end <= pstart or start >= pend:
                raise QualificationError('A handoff time range misses its stated phase')
        for claim in handoff['claims']:
            if claim['artifact'] not in files:
                raise QualificationError('A handoff identity is not in finalized run evidence')
            _, value = _document(result_path.parent, files[claim['artifact']])
            actual = _pointer(value, claim['json_pointer'])
            if type(actual) is not type(claim['value']) or actual != claim['value']:
                raise QualificationError('A handoff identity differs from the exact evidence field')
            if actual is None or actual == '':
                raise QualificationError('An unresolved identity cannot establish handoff coverage')
    _captions(paths['transcript'], review['transcript_format'], duration)
    for edit in review['optional_edits']:
        edit_path = file_reference(root, edit['video'])
        _media_outside_runs(edit_path, roots)
        if edit_path in paths.values() or not edit['discloses_cuts_speed_and_elapsed_time']:
            raise QualificationError('Optional edits must disclose their changes and preserve distinct full source')
        previous = 0
        for segment in edit['segments']:
            sstart, send = _range(segment['source_range'], duration)
            estart, eend = _range(segment['edit_range'], edit['duration_seconds'])
            if abs(estart - previous) > 0.001 or abs((send - sstart) / segment['speed'] - (eend - estart)) > 0.05:
                raise QualificationError('Optional edit timecodes or declared speed do not map to source')
            previous = eend
        if abs(previous - edit['duration_seconds']) > 0.001:
            raise QualificationError('Optional edit mapping is incomplete')
    independent = review['reviewer'] != author_id and review['reviewer_did_not_build_demo']
    playback = (independent and native is not None and review['watched_full_delivered_video']
                and review['playback_decision'] == 'pass' and result['canonical_success'] is True)
    completeness = (playback and review['completeness_decision'] == 'pass' and review['continuous_source']
                    and review['no_off_camera_repair'] and review['no_fixture_replacement'])
    clarity = (playback and review['clarity_decision'] == 'pass' and review['readable_text']
               and review['narration_or_captions_explain_transitions'] and review['no_exposed_credentials']
               and not review['unresolved_material_gaps'])
    return {'artifact_checks': 'pass', 'human_decisions_basis': 'recorded-independent-review-not-machine-playback',
            'reviewer': review['reviewer'], 'G7.V1': 'pass' if completeness else 'incomplete',
            'G10.V1': 'pass' if clarity else 'incomplete', 'video_sha256': review['video']['sha256'],
            'duration_seconds': duration}


def qualify(path):
    path = _safe_path(Path(path))
    root = path.parent
    raw = _read(path, MAX_DOCUMENT_BYTES)
    qualification = validate('qualification', _json(raw))
    entries = qualification['runs']
    if {item['role'] for item in entries} != {'fresh-1', 'fresh-2', 'reset-replay'}:
        raise QualificationError('Qualification requires exactly two fresh roles and one reset/replay role')
    runs = [_recorded_run(root, item) for item in entries]
    if len({item[1]['run_id'] for item in runs}) != 3 or len({item[0] for item in runs}) != 3:
        raise QualificationError('Qualification must bind three distinct complete canonical runs')
    canonical = _json(_read(SCENARIO_ROOT / 'scenario.json', MAX_DOCUMENT_BYTES))['phases']
    gaps = []
    for entry, (_, result, _, manifest) in zip(entries, runs, strict=True):
        required = {'package_sha256': qualification['package_manifest_sha256'],
                    'scenario_sha256': qualification['scenario_sha256'],
                    'assertions_sha256': qualification['assertions_sha256'], 'lock_sha256': qualification['lock_sha256']}
        if any(result['identities'][key] != value for key, value in required.items()):
            raise QualificationError('Qualification runs do not use the same frozen package, lock and scenario')
        if (result['profile'] != 'bigbang-live' or result['execution_mode'] != 'operator'
                or result['operation_backend'] != 'owned-linux' or result['canonical_success'] is not True
                or result['terminal'] is not True or result['exit_code'] != 0
                or result['missing_assertions'] or not result['cleanup']['environment_released']):
            gaps.append('canonical-run-not-successful:' + entry['role'])
        expected = [(p['id'], p['attempt'], p['operation']) for p in canonical]
        actual = [(p['phase_id'], p['attempt'], p['operation']) for p in result['phase_records']]
        if actual != expected or any(p['outcome'] != 'completed' for p in result['phase_records']):
            gaps.append('canonical-phase-coverage-incomplete:' + entry['role'])
        if _time(entry['operator_attested_at']) < _time(result['finalized_at']):
            raise QualificationError('Operator attestation predates the completed run')
        if not entry['operator_performed_run'] or (entry['role'].startswith('fresh-')
                                                  and entry['operator_id'] == qualification['author_id']):
            gaps.append('independent-second-operator-missing:' + entry['role'])
        known = {item['path'] for item in manifest['files']}
        if not set(entry['environment_evidence']) <= known:
            raise QualificationError('Environment evidence is absent from the finalized manifest')
    from .fresh_qualification import verify_fresh_foundation, distinct_fresh_runs
    from .native_qualification import verify_native_receipts
    from .ci_evidence_qualification import verify_ci_bundles
    native_proofs = []
    for entry, (result_path, result, _, manifest) in zip(entries, runs, strict=True):
        proof = verify_native_receipts(result_path, result, manifest)
        native_proofs.append(proof)
        if proof['status'] != 'pass':
            gaps.append('canonical-native-proof-incomplete:' + entry['role'] + ':' + proof['gap'])
    ci_bundles = [verify_ci_bundles(result_path, result, manifest) for result_path, result, _, manifest in runs]
    fresh_proofs = []
    for entry, (result_path, result, _, manifest) in zip(entries, runs, strict=True):
        if not entry['role'].startswith('fresh-'):
            continue
        proof = verify_fresh_foundation(result_path, result, manifest, entry['environment_evidence'])
        fresh_proofs.append(proof)
        if proof['status'] != 'pass':
            gaps.append('fresh-cluster-provenance-incomplete:' + entry['role'] + ':' + proof['gap'])
        elif entry['environment_origin'] != 'fresh' or entry['cluster_uid'] != proof['cluster_uid']:
            raise QualificationError('Declared fresh environment differs from actual producer evidence')
    if not distinct_fresh_runs(fresh_proofs):
        gaps.append('two-independent-fresh-disk-generation-and-cluster-origins-required')
    fresh = [entry for entry in entries if entry['role'].startswith('fresh-')]
    if (any(entry['environment_origin'] != 'fresh' or not entry['cluster_uid'] for entry in fresh)
            or len({entry['cluster_uid'] for entry in fresh}) != 2):
        gaps.append('two-distinct-fresh-clusters-not-recorded')
    for item in qualification['additional_gate_evidence']:
        for reference in item['artifacts']:
            file_reference(root, reference)
    video = verify_video(root, qualification['video_review'], runs, author_id=qualification['author_id'])
    gates = {'G' + str(i): {'status': 'incomplete', 'reason': 'required-live-contract-and-adapter-lanes-not-evaluated'}
             for i in range(1, 11)}
    gates['G7'] = {'status': 'pass' if not gaps and video['G7.V1'] == 'pass' else 'incomplete',
                   'reason': 'three-canonical-runs-two-fresh-second-operator-reset-cleanup-and-continuous-recording',
                   'canonical_evidence': 'pass' if not gaps else 'incomplete', 'gaps': gaps,
                   'fresh_foundation_evidence': fresh_proofs, 'native_execution_evidence': native_proofs,
                   'ci_historical_bundles': ci_bundles}
    gates['G7.V1'] = {'status': video['G7.V1'], 'basis': video['human_decisions_basis']}
    gates['G10.V1'] = {'status': video['G10.V1'], 'basis': video['human_decisions_basis']}
    if any(gap.startswith('canonical-') for gap in gaps):
        gates['G7.V1'] = {'status': 'incomplete', 'reason': 'canonical-run-acceptance-incomplete'}
    if _read(path, MAX_DOCUMENT_BYTES) != raw:
        raise QualificationError('Qualification input changed during verification')
    return {'schema_version': 1, 'kind': 'factory-qualification-report',
            'qualification_id': qualification['qualification_id'], 'qualification_sha256': _sha(raw),
            'scope': list(SCOPE), 'assurance': 'hash-bound-evidence-and-recorded-human-decisions',
            'release_qualified': False, 'exit_code': 2, 'gates': gates, 'video': video,
            'run_ids': [item[1]['run_id'] for item in runs],
            'limitations': ['No media playback or comprehension is inferred from file metadata.',
                            'Fresh creation is checked only from terminal-bound producer receipts, never a label; no model, hosted, disconnected or alternate adapter qualification is inferred.',
                            'A terminal manifest is a hash binding; it is not a signature or independent origin attestation.']}


def write_report(report, output, *, protected_directories=()):
    """Publish separate immutable JSON/Markdown/HTML, never alter run evidence."""
    output = _safe_path(Path(output))
    _media_outside_runs(output, protected_directories)
    if output.exists():
        raise QualificationError('Qualification report output must be new')
    output.mkdir(mode=0o700)
    rows = ['| Gate | Status |', '| --- | --- |'] + [f'| {gate} | {item["status"]} |' for gate, item in report['gates'].items()]
    markdown = ('# Qualification evidence check\n\nRelease qualification remains incomplete.\n\n' + '\n'.join(rows)
                + '\n\n' + '\n'.join('- ' + item for item in report['limitations']) + '\n')
    document = '<!doctype html><meta charset="utf-8"><title>Qualification evidence check</title><pre>' + html.escape(markdown) + '</pre>'
    for name, raw in (('report.json', _canonical(report)), ('report.md', markdown.encode()), ('report.html', document.encode())):
        _exclusive(output / name, raw)


def qualify_and_write(path, output):
    path = _safe_path(Path(path))
    report = qualify(path)
    raw = _read(path, MAX_DOCUMENT_BYTES)
    if _sha(raw) != report['qualification_sha256']:
        raise QualificationError('Qualification input changed before report publication')
    document = validate('qualification', _json(raw))
    roots = [_relative(path.parent, row['result']['path']).parent for row in document['runs']]
    write_report(report, output, protected_directories=roots)
    return report
