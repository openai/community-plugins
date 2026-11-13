"""Final run publication after durable cleanup; hashes are consistency evidence.

The graph is one-way: journal and registered artifacts -> manifest -> result.
Media and human playback decisions are separate, later evidence. Verification
never repairs a projection, executes a phase, or reconstructs a capability.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from .evidence_export import _Route, _source, _regular, _references, _checkpoint
from .state import (RunJournal, StateError, MAX_DOCUMENT_BYTES, MAX_EVENTS,
                    MAX_JOURNAL_BYTES, _canonical, _json, _sha, _time, _validate)

MAX_TERMINAL_BYTES = 256_000_000
MAX_MANIFEST_BYTES = 8_000_000
_RESULT = 'e2e-result.json'
_MANIFEST = 'evidence-manifest.json'


def _read_at(route, name, deadline, maximum=MAX_DOCUMENT_BYTES):
    with _source(route, name, deadline) as (fd, info):
        if info.st_size > maximum:
            raise StateError('Terminal document exceeds its bounded size')
        raw = bytearray()
        while True:
            _checkpoint(deadline)
            block = os.read(fd, min(65536, maximum - len(raw) + 1))
            if not block:
                break
            raw.extend(block)
            if len(raw) > maximum:
                raise StateError('Terminal document exceeds its bounded size')
        return bytes(raw)


def _decode(raw, maximum=MAX_DOCUMENT_BYTES):
    # The state parser intentionally has a smaller document bound. A manifest
    # can contain a bounded row for each event and registered artifact.
    if maximum == MAX_DOCUMENT_BYTES:
        return _json(raw)
    import json
    from .state import _pairs
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError) as error:
        raise StateError('Invalid terminal JSON') from error


def _ref(path, raw):
    return {'path': path, 'sha256': _sha(raw), 'bytes': len(raw)}


def _journal_members(route, count, deadline):
    expected = {f'{number:08d}.json' for number in range(1, count + 1)}
    entries = set()
    journal_fd = os.open('journal', route.flags, dir_fd=route.fd)
    try:
        with os.scandir(journal_fd) as stream:
            for entry in stream:
                _checkpoint(deadline)
                if entry.name not in expected or entry.name in entries or len(entries) >= count:
                    raise StateError('Terminal journal must be an exact contiguous event sequence')
                entries.add(entry.name)
    finally:
        os.close(journal_fd)
    if entries != expected:
        raise StateError('Terminal journal must be an exact contiguous event sequence')

def _replay(route, deadline):
    raw = _read_at(route, 'state.json', deadline)
    state = _json(raw)
    _validate('factory-state', state)
    count = state['journal_seq']
    if not 1 <= count <= MAX_EVENTS:
        raise StateError('Terminal journal exceeds its event bound')
    journal = RunJournal(route.path, identities=state['identities'])
    _journal_members(route, count, deadline)
    files = {'state.json': _ref('state.json', raw)}
    total = 0
    for number in range(1, count + 1):
        name = f'journal/{number:08d}.json'
        event_raw = _read_at(route, name, deadline)
        total += len(event_raw)
        if total > MAX_JOURNAL_BYTES:
            raise StateError('Terminal journal exceeds its aggregate bound')
        event = _json(event_raw)
        previous = journal._state
        if (type(event) is not dict or set(event) != {
                'schema_version', 'seq', 'previous_sha256', 'type', 'time', 'payload'}
                or type(event['schema_version']) is not int or event['schema_version'] != 1
                or type(event['seq']) is not int or event['seq'] != number
                or event['previous_sha256'] != (previous['journal_head_sha256'] if previous else None)):
            raise StateError('Terminal journal identity or hash chain is invalid')
        _time(event['time'])
        if previous and _time(event['time']) < _time(previous['updated_at']):
            raise StateError('Terminal journal time moved backwards')
        journal._state = journal._reduce(event, _sha(event_raw))
        _validate('factory-state', journal._state)
        files[name] = _ref(name, event_raw)
    if journal._state != state or journal.identities != state['identities']:
        raise StateError('Terminal projection differs from immutable journal replay')
    lifecycle = state.get('lifecycle', {})
    if (not lifecycle.get('final_result') or state['execution_status'] != 'released'
            or state['environment_reserved'] or state['active_mutation'] is not None
            or state['active_native'] is not None or lifecycle['active_phase'] is not None
            or state['cleanup_validation'] is None
            or lifecycle['final_result'] != journal._derive_lifecycle_result(lifecycle)):
        raise StateError('Terminal publication requires finalized observed cleanup')
    refs, paths, _ = _references(state)
    refs = [{name: ref[name] for name in ('path', 'sha256', 'bytes', 'location')} for ref in refs]
    for name, reference in paths.items():
        if name in (_RESULT, _MANIFEST) or name in files and files[name] != reference:
            raise StateError('Terminal evidence membership conflicts')
        files[name] = reference
    if sum(row['bytes'] for row in files.values()) > MAX_TERMINAL_BYTES:
        raise StateError('Terminal evidence exceeds its aggregate size bound')
    for name, reference in files.items():
        _regular(route, name, reference, deadline)
    _journal_members(route, count, deadline)
    route.audit()
    return state, refs, files


def _documents(route, state, refs, files, deadline, *, native_identity):
    lifecycle = state['lifecycle']
    def input_doc(name):
        reference = lifecycle['inputs'][name]
        return _json(_read_at(route, reference['path'], deadline))
    scenario, config = input_doc('scenario'), input_doc('config')
    common = {'schema_version': 1, 'run_id': state['run_id'],
              'scenario_id': scenario['scenario_id'], 'environment_id': state['environment_id'],
              'identities': deepcopy(state['identities']),
              'operation_backend': lifecycle['driver_id'],
              'execution_mode': lifecycle['execution_mode'], 'finalized_at': state['updated_at']}
    manifest = {**common, 'kind': 'factory-terminal-evidence-manifest',
                'journal_seq': state['journal_seq'], 'journal_head_sha256': state['journal_head_sha256'],
                'files': [files[name] for name in sorted(files)], 'references': refs,
                'authenticity': 'hash-binding-only'}
    selected = lifecycle.get('auxiliary_inputs', {}).get('native_selection')
    native_driver = 'codex-cli' if selected else 'current-host'
    if native_identity is not None:
        if selected is None or not state['native_results']:
            raise StateError('Native identity requires selected client and accepted native receipts')
        selection = _json(_read_at(route, selected['path'], deadline))
        expected = {'client_path': selection['executable'],
                    'client_sha256': selection['expected_client_sha256'],
                    'client_version': selection['expected_client_version'],
                    'model': selection['model'], 'reasoning_effort': selection['reasoning_effort'],
                    'provider_observed_model': None,
                    'identity_basis': 'explicit-selection-and-native-receipts'}
        if native_identity != expected:
            raise StateError('Native identity differs from frozen selection and receipt scope')
    phase_by_id = {phase['id']: phase for phase in lifecycle['phases']}
    records = [{**deepcopy(record), 'attempt': phase_by_id[record['phase_id']]['attempt'],
                'operation': phase_by_id[record['phase_id']]['operation']}
               for record in lifecycle['phase_records']]
    _validate('terminal-evidence-manifest', manifest)
    manifest_raw = _canonical(manifest)
    if len(manifest_raw) > MAX_MANIFEST_BYTES:
        raise StateError('Terminal manifest exceeds its metadata bound')
    result = {**common, **deepcopy(lifecycle['final_result']), 'kind': 'factory-e2e-result',
              'profile': config['profile'], 'native_driver': native_driver,
              'native_identity': deepcopy(native_identity), 'phase_records': records,
              'native_results': deepcopy(state['native_results']),
              'cleanup': {'environment_released': True, 'artifacts': deepcopy(state['cleanup_artifacts']),
                          'validation': deepcopy(state['cleanup_validation'])},
              'evidence_manifest': _ref(_MANIFEST, manifest_raw)}
    _validate('scenario-result', result)
    result_raw = _canonical(result)
    if len(result_raw) > MAX_DOCUMENT_BYTES:
        raise StateError('Terminal result exceeds its metadata bound')
    return manifest, manifest_raw, result, result_raw


def _publish_one(route, name, raw, deadline):
    try:
        existing = _read_at(route, name, deadline, MAX_MANIFEST_BYTES)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if existing != raw:
            raise StateError('Existing immutable terminal publication differs')
        return
    temporary = '.' + name + '.' + uuid4().hex + '.tmp'
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=route.fd)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        route.audit()
        os.link(temporary, name, src_dir_fd=route.fd, dst_dir_fd=route.fd, follow_symlinks=False)
        os.unlink(temporary, dir_fd=route.fd)
        os.fsync(route.fd)
        route.audit()
    finally:
        try:
            os.unlink(temporary, dir_fd=route.fd)
        except FileNotFoundError:
            pass


def publish_terminal(journal, *, native_identity=None, deadline=None):
    """Publish the same locked resident journal, after its terminal commit."""
    if (type(journal) is not RunJournal or journal._process_id != os.getpid()
            or not journal._lifecycle_locked):
        raise StateError('Terminal publication requires its resident lifecycle lock')
    deadline = time.monotonic() + 120 if deadline is None else deadline
    route = _Route(journal.run_dir, deadline)
    try:
        state, refs, files = _replay(route, deadline)
        if state != journal.state:
            raise StateError('Terminal journal changed before publication')
        # An already-published identity is immutable on a later process resume.
        try:
            prior = _json(_read_at(route, _RESULT, deadline))
        except FileNotFoundError:
            prior = None
        if prior is not None and native_identity is None:
            native_identity = prior.get('native_identity')
        manifest, manifest_raw, result, result_raw = _documents(
            route, state, refs, files, deadline, native_identity=native_identity)
        _publish_one(route, _MANIFEST, manifest_raw, deadline)
        _publish_one(route, _RESULT, result_raw, deadline)
        for name, reference in files.items():
            _regular(route, name, reference, deadline)
        _journal_members(route, state['journal_seq'], deadline)
        return {'result': result, 'manifest': manifest}
    finally:
        route.close()


def verify_terminal(run_dir, *, expected_result_sha256=None, expected_manifest_sha256=None, deadline=None):
    """Read-only byte verification and journal replay; never repair or execute."""
    deadline = time.monotonic() + 120 if deadline is None else deadline
    route = _Route(Path(run_dir), deadline)
    try:
        result_raw = _read_at(route, _RESULT, deadline)
        manifest_raw = _read_at(route, _MANIFEST, deadline, MAX_MANIFEST_BYTES)
        for raw, pin in ((result_raw, expected_result_sha256), (manifest_raw, expected_manifest_sha256)):
            if pin is not None and (not isinstance(pin, str) or not re.fullmatch(r'[0-9a-f]{64}', pin)
                                    or _sha(raw) != pin):
                raise StateError('Terminal publication differs from its explicit SHA256')
        result, manifest = _json(result_raw), _decode(manifest_raw, MAX_MANIFEST_BYTES)
        _validate('scenario-result', result)
        _validate('terminal-evidence-manifest', manifest)
        state, refs, files = _replay(route, deadline)
        expected_manifest, expected_manifest_raw, expected_result, expected_result_raw = _documents(
            route, state, refs, files, deadline, native_identity=result['native_identity'])
        if result_raw != expected_result_raw or manifest_raw != expected_manifest_raw:
            raise StateError('Terminal publication differs from finalized journal evidence')
        if (_read_at(route, _RESULT, deadline) != result_raw
                or _read_at(route, _MANIFEST, deadline, MAX_MANIFEST_BYTES) != manifest_raw):
            raise StateError('Terminal publication changed during verification')
        for name, reference in files.items():
            _regular(route, name, reference, deadline)
        _journal_members(route, state['journal_seq'], deadline)
        return {'result': expected_result, 'manifest': expected_manifest}
    finally:
        route.close()
