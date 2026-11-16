"""Actual loopback TLS diagnostics, never a live factory or production grant."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

from devsecops.factory import doctor, owned_linux as owned
from devsecops.factory.lifecycle import PhaseContext, SCENARIO_ROOT, _readonly
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha
from tests.test_argocd import ca, server  # noqa: F401
from tests.test_factory_contracts import config, factory_lock

PRIVATE = 'PRIVATE-RESPONSE-CANARY'
READ_TOKEN = 'READ-CREDENTIAL-CANARY'
OPERATOR_TOKEN = 'OPERATOR-CREDENTIAL-CANARY'


def responses():
    return [
        {'json': {'Version': 'v1.2.3', 'private': PRIVATE}},
        {'json': {'id': 7, 'description': PRIVATE, 'permissions': {'private': PRIVATE}}},
        {'json': {'gitVersion': 'v1.2.3', 'private': PRIVATE}},
        {'raw': b'ok', 'content_type': 'text/plain'},
        {'json': {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {
            'name': 'mission-demo', 'uid': 'real-loopback-namespace-uid', 'annotations': {'private': PRIVATE}}}},
        {'json': {'status': 'success', 'data': {'version': '3.1.0', 'private': PRIVATE}}},
    ]


@pytest.fixture
def setup(tmp_path, monkeypatch, server):  # noqa: F811
    home = tmp_path/'codex-home'
    package = home/'plugins/cache/devsecops-local/devsecops/0.1.0'
    source = Path(owned.__file__).resolve()
    files = {'.codex-plugin/plugin.json': _canonical({'name': 'devsecops', 'version': '0.1.0'}),
        'src/devsecops/factory/owned_linux.py': source.read_bytes(),
        'src/devsecops/factory/auxiliary_inputs.py': Path(owned.auxiliary_inputs.__file__).read_bytes(),
        'src/devsecops/factory/service_inventory.py': Path(owned.service_inventory.__file__).read_bytes(),
        'src/devsecops/factory/evidence_export.py': Path(owned.evidence_export.__file__).read_bytes(),
        **{'demo/software-factory/scenarios/ironbank-mission-recovery-v1/'+name+'.json':
           (SCENARIO_ROOT/(name+'.json')).read_bytes() for name in ('scenario', 'assertions')}}
    for name, raw in files.items():
        path = package/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = ''.join(f'{_sha(raw)}  {name}\n' for name, raw in sorted(files.items())).encode()
    (package/'SHA256SUMS').write_bytes(manifest)
    monkeypatch.setenv('CODEX_HOME', str(home))
    # Controlled source-location fixture, not a native installation claim.
    monkeypatch.setattr(owned, 'PACKAGE_ROOT', package)
    monkeypatch.setattr(owned, '__file__', str(package/'src/devsecops/factory/owned_linux.py'))
    monkeypatch.setattr(owned.auxiliary_inputs, '__file__', str(package/'src/devsecops/factory/auxiliary_inputs.py'))
    monkeypatch.setattr(owned.service_inventory, '__file__', str(package/'src/devsecops/factory/service_inventory.py'))
    monkeypatch.setattr(owned.evidence_export, '__file__', str(package/'src/devsecops/factory/evidence_export.py'))
    selected = config()
    selected['expires_at'] = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    ca_copy = tmp_path/'selected-ca.pem'
    ca_copy.write_bytes(server['ca'].read_bytes())
    for service in selected['services']:
        service['origin'] = server['origin']
        service['ca_file'] = str(ca_copy)
        service['read_auth'] = {'mode': 'bearer', 'token_env': 'DIAGNOSTIC_READ_' + service['id'].upper()}
        service['operator_auth'] = {'mode': 'bearer', 'token_env': 'DIAGNOSTIC_OPERATOR_' + service['id'].upper()}
        monkeypatch.setenv(service['read_auth']['token_env'], READ_TOKEN)
        monkeypatch.setenv(service['operator_auth']['token_env'], OPERATOR_TOKEN)
        if service['id'] == 'gitlab':
            service['scope']['project_ids'] = [7]
        if service['id'] == 'kubernetes':
            service['scope']['namespaces'] = ['mission-demo']
    tool = tmp_path/'controlled-tool-bytes'
    tool.write_bytes(b'Controlled file integrity input, never executed')
    selected['tool_paths']['helm'] = str(tool)
    lock = factory_lock()
    lock['tools'][0]['sha256'] = _sha(tool.read_bytes())
    lock.update(scenario_sha256=_sha(files['demo/software-factory/scenarios/ironbank-mission-recovery-v1/scenario.json']),
        assertions_sha256=_sha(files['demo/software-factory/scenarios/ironbank-mission-recovery-v1/assertions.json']))
    values = {'scenario': json.loads((SCENARIO_ROOT/'scenario.json').read_bytes()),
        'assertions': json.loads((SCENARIO_ROOT/'assertions.json').read_bytes()), 'config': selected,
        'lock': lock, 'environment': {'environment_id': selected['environment'], 'controlled_test': True}}
    def start():
        raw = {name: _canonical(value) for name, value in values.items()}
        for name in ('scenario', 'assertions'):
            raw[name] = files['demo/software-factory/scenarios/ironbank-mission-recovery-v1/'+name+'.json']
        raw['package'] = manifest
        identities = {name+'_sha256': _sha(value) for name, value in raw.items()}
        journal = RunJournal.create(tmp_path/'run', identities=identities, environment_id=selected['environment'])
        journal.initialize_lifecycle(inputs=raw, execution_mode='controlled-test', driver_id=owned.DRIVER_ID)
        driver = owned.OwnedLinuxDriver(journal, plugin_id='devsecops@devsecops-local', plugin_version='0.1.0')
        return journal, driver
    server['responses'] = responses()
    return {'start': start, 'values': values, 'server': server, 'package': package, 'ca': ca_copy}


def context(journal, values, phase=None):
    state = journal.state
    return PhaseContext(state['run_id'], _readonly(phase or values['scenario']['phases'][0]),
        _readonly(journal.identities), _readonly(values), _readonly(state['lifecycle']['inputs']),
        journal.run_dir, time.monotonic()+30, 'controlled-test')


def execute(setup):
    journal, driver = setup['start']()
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        outcome = driver.execute(context(journal, setup['values']))
    report = json.loads(outcome.artifacts[0].read_bytes())
    return outcome, report, journal


def test_real_tls_projects_namespace_versions_and_credentials_remain_narrow_observations(setup, monkeypatch):
    for variable in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.setenv(variable, 'http://127.0.0.1:1')
    outcome, report, journal = execute(setup)
    assert outcome.status == 'incomplete' and outcome.mutation_effect == 'not_applied' and not outcome.assertions
    assert report['readiness'] == 'unknown' and report['canonical_success'] is False
    assert report['external_mutations_performed'] is False and report['created_resources'] == []
    assert report['resource_ownership'] == 'unverified' and report['cleanup'] == 'not-performed'
    rows = {row['service']: row for row in report['services']}
    assert rows['gitlab']['observations']['projects'] == [{'id': 7, 'read_observed': True}]
    assert rows['kubernetes']['observations']['readyz_ok'] is True
    assert rows['kubernetes']['observations']['namespaces'][0]['ownership'] == 'unverified'
    for service in ('gitlab', 'kubernetes', 'argocd', 'prometheus'):
        row = rows[service]
        assert row['observation_status'] == 'complete' and row['tls_destination_verified']
        assert row['credential_sent'] == 'yes' and row['authentication'] == row['read_grants'] == 'unverified'
        assert row['operator_grants'] == 'not-probed'
    assert [request['path'] for request in setup['server']['requests']] == [
        '/api/version', '/api/v4/projects/7', '/version', '/readyz',
        '/api/v1/namespaces/mission-demo', '/api/v1/status/buildinfo']
    assert all(row['authorization'] == 'Bearer '+READ_TOKEN for row in setup['server']['requests'])
    for path in outcome.artifacts[0].parent.iterdir():
        assert all(secret not in path.read_text() for secret in (PRIVATE, READ_TOKEN, OPERATOR_TOKEN))
    assert not journal.state['environment_reserved'] and journal.state['operations'] == []
    assert rows['registry']['gaps'] == rows['registry1']['gaps'] == ['service-diagnostic-unimplemented']


@pytest.mark.parametrize('change,expected', [
    ('denied', 'service-denied'), ('redirect', 'service-redirect'),
    ('mismatch', 'service-project-scope-mismatch'), ('invalid', 'service-invalid-json'),
])
def test_project_failures_are_scoped_sanitized_and_never_follow_remote_routes(setup, change, expected):
    setup['server']['responses'][1] = {
        'denied': {'status': 403, 'raw': PRIVATE.encode()},
        'redirect': {'status': 302, 'headers': {'Location': 'https://outside.invalid/'+PRIVATE}},
        'mismatch': {'json': {'id': 8, 'description': PRIVATE}},
        'invalid': {'raw': b'{"id":7,"id":8}'},
    }[change]
    _, report, _ = execute(setup)
    row = next(row for row in report['services'] if row['service'] == 'gitlab')
    assert row['observation_status'] == 'incomplete' and expected in row['gaps']
    assert len(setup['server']['requests']) == 6
    assert PRIVATE not in json.dumps(report)


@pytest.mark.parametrize('change', ['version', 'readiness', 'namespace'])
def test_cluster_diagnostics_never_infer_ownership_or_hide_mismatch(setup, change):
    index, response = {'version': (2, {'json': {'gitVersion': 'v9.9.9'}}),
        'readiness': (3, {'raw': PRIVATE.encode(), 'content_type': 'text/plain'}),
        'namespace': (4, {'json': {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'other', 'uid': 'x'}}})}[change]
    setup['server']['responses'][index] = response
    _, report, _ = execute(setup)
    row = next(row for row in report['services'] if row['service'] == 'kubernetes')
    assert {'version': 'locked-version-mismatch', 'readiness': 'api-readiness-not-confirmed',
        'namespace': 'service-namespace-scope-mismatch'}[change] in row['gaps']
    assert report['canonical_success'] is False and PRIVATE not in json.dumps(report)


@pytest.mark.parametrize('change', ['overlap', 'scope', 'missing-token', 'basic'])
def test_unusable_read_configuration_stops_that_service_before_requests(setup, monkeypatch, change):
    service = next(row for row in setup['values']['config']['services'] if row['id'] == 'gitlab')
    if change == 'overlap':
        service['read_auth'] = deepcopy(service['operator_auth'])
    elif change == 'scope':
        service['scope']['project_ids'] = list(range(1, 10))
    elif change == 'missing-token':
        monkeypatch.delenv(service['read_auth']['token_env'])
    else:
        service['read_auth'] = {'mode': 'basic', 'username_env': 'U', 'password_env': 'P'}
    setup['server']['responses'].pop(1)
    _, report, _ = execute(setup)
    assert not any('/projects/' in request['path'] for request in setup['server']['requests'])
    row = next(row for row in report['services'] if row['service'] == 'gitlab')
    assert row['observation_status'] == 'incomplete' and row['gaps']
    assert row['credential_sent'] == 'not-confirmed'


def test_public_version_read_does_not_authenticate_operator(setup):
    service = next(row for row in setup['values']['config']['services'] if row['id'] == 'argocd')
    service['read_auth'] = {'mode': 'none'}
    _, report, _ = execute(setup)
    row = next(row for row in report['services'] if row['service'] == 'argocd')
    assert row['credential_sent'] == 'none-selected' and row['authentication'] == 'unverified'
    assert setup['server']['requests'][0]['authorization'] is None


@pytest.mark.parametrize('change', ['changed', 'extra', 'not-cache'])
def test_package_and_window_admission_precede_all_service_access(setup, monkeypatch, change):
    if change == 'changed':
        (setup['package']/'.codex-plugin/plugin.json').write_text('{}')
    elif change == 'extra':
        (setup['package']/'unmanifested').write_text(PRIVATE)
    elif change == 'not-cache':
        monkeypatch.setenv('CODEX_HOME', str(setup['package'].parent))
    _, report, journal = execute(setup)
    assert setup['server']['requests'] == []
    assert 'preflight-input-package-or-budget-unavailable' in report['gaps']
    assert journal.state['operations'] == []


@pytest.mark.parametrize('selection', [None, {}])
def test_empty_auxiliary_selection_checks_loaded_parser_origin(setup, monkeypatch, tmp_path, selection):
    # Identical bytes in another checkout still cannot stand in for the loaded
    # parser from this selected package, even when no optional inputs are used.
    foreign = tmp_path/'foreign-auxiliary-inputs.py'
    foreign.write_bytes(Path(owned.auxiliary_inputs.__file__).read_bytes())
    monkeypatch.setattr(owned.auxiliary_inputs, '__file__', str(foreign))
    journal, _ = setup['start']()
    driver = owned.OwnedLinuxDriver(journal, plugin_id='devsecops@devsecops-local',
        plugin_version='0.1.0', auxiliary_inputs=selection)
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        outcome = driver.execute(context(journal, setup['values']))
    report = json.loads(outcome.artifacts[0].read_bytes())
    assert outcome.status == 'incomplete' and setup['server']['requests'] == []
    assert 'preflight-input-package-or-budget-unavailable' in report['gaps']
    assert journal.state['operations'] == [] and not journal.state['environment_reserved']


def test_expired_operator_window_rejects_before_diagnostic_creation(setup):
    setup['values']['config']['expires_at'] = '2020-01-01T00:00:00Z'
    journal, driver = setup['start']()
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        with pytest.raises(StateError, match='window has expired'):
            driver.execute(context(journal, setup['values']))
    assert not setup['server']['requests'] and not list(journal.run_dir.glob('owned-linux-*'))


@pytest.mark.parametrize('route', ['/api/version', '/api/v4/projects/7', 'terminal-publication'])
def test_wall_clock_advance_cannot_continue_reads_or_publish_with_monotonic_budget_remaining(setup, monkeypatch, route):
    current = [datetime.now(timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current[0]
    monkeypatch.setattr(owned, 'datetime', Clock)
    request, write = owned.HTTPSReadClient.get_json, owned._write_terminal
    def read(client, path, *args, **kwargs):
        result = request(client, path, *args, **kwargs)
        if path == route:
            current[0] += timedelta(hours=2)
        return result
    def published(directory, name, value):
        result = write(directory, name, value)
        if name == 'diagnostic.json' and route == 'terminal-publication':
            current[0] += timedelta(hours=2)
        return result
    monkeypatch.setattr(owned.HTTPSReadClient, 'get_json', read)
    monkeypatch.setattr(owned, '_write_terminal', published)
    journal, driver = setup['start']()
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        ctx = context(journal, setup['values'])
        with pytest.raises(StateError, match='window has expired'):
            driver.execute(ctx)
        assert time.monotonic() < ctx.deadline
    assert len(setup['server']['requests']) == {'/api/version': 1, '/api/v4/projects/7': 2, 'terminal-publication': 6}[route]
    assert not list(journal.run_dir.rglob('diagnostic.json'))


def test_hash_chunk_deadline_cancels_local_inspection_before_any_service_request(setup, monkeypatch):
    now = [time.monotonic()]
    actual_open = Path.open
    calls = []
    tool = Path(setup['values']['config']['tool_paths']['helm'])
    class SlowFile:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def read(self, size):
            calls.append(size)
            data = self.stream.read(size)
            now[0] += 121
            return data
    def opened(path, *args, **kwargs):
        stream = actual_open(path, *args, **kwargs)
        return SlowFile(stream) if path == tool else stream
    monkeypatch.setattr(doctor.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(Path, 'open', opened)
    journal, driver = setup['start']()
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        with pytest.raises(ValueError):
            driver.execute(context(journal, setup['values']))
    assert calls == [1024*1024] and not setup['server']['requests']
    assert not list(journal.run_dir.rglob('diagnostic.json'))


def test_doctor_deadline_is_not_converted_into_a_missing_tool_gap(setup, monkeypatch):
    values = setup['values']
    root = setup['package'].parents[5]
    for name in ('config', 'lock'):
        (root/(name+'.json')).write_bytes(_canonical(values[name]))
    actual = doctor._file_hash
    def bounded(path, **kwargs):
        if path == Path(values['config']['tool_paths']['helm']):
            raise doctor._InspectionDeadline('Local readiness deadline exceeded')
        return actual(path, **kwargs)
    monkeypatch.setattr(doctor, '_file_hash', bounded)
    with pytest.raises(TimeoutError, match='readiness deadline'):
        doctor.inspect(root/'config.json', profile='bigbang-live', lock_path=root/'lock.json',
                       deadline=time.monotonic()+30)


@pytest.mark.parametrize('change', ['foreign-run', 'input', 'expired', 'phase', 'unlocked'])
def test_forged_or_stale_context_cannot_start_diagnostics(setup, change):
    journal, driver = setup['start']()
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        ctx = context(journal, setup['values'])
        if change == 'foreign-run':
            ctx = replace(ctx, run_id='other')
        elif change == 'input':
            ctx = replace(ctx, inputs={})
        elif change == 'expired':
            ctx = replace(ctx, deadline=time.monotonic()-1)
        elif change == 'phase':
            ctx = replace(ctx, phase=setup['values']['scenario']['phases'][1])
        else:
            journal._lifecycle_locked = False
        with pytest.raises(ValueError):
            driver.execute(ctx)
    assert not list(journal.run_dir.glob('owned-linux-*')) and not setup['server']['requests']


def test_invalid_private_ca_yields_no_requests_or_authentication_claim(setup):
    setup['ca'].write_text('not a CA')
    _, report, _ = execute(setup)
    assert not setup['server']['requests']
    assert all(not row['tls_destination_verified'] for row in report['services'])


def test_real_tls_certificate_rejection_never_sends_credentials(setup):
    for service in setup['values']['config']['services']:
        service.pop('ca_file')
    _, report, _ = execute(setup)
    assert not setup['server']['requests']
    rows = [row for row in report['services'] if row['service'] not in {'registry', 'registry1'}]
    assert all('service-tls' in row['gaps'] and not row['tls_destination_verified'] for row in rows)
    assert all(row['credential_sent'] == 'not-confirmed' for row in rows)


def test_actual_tls_slow_read_cannot_outlive_phase_or_publish_a_terminal_receipt(setup):
    setup['server']['responses'][0]['body_delay'] = .5
    journal, driver = setup['start']()
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        ctx = replace(context(journal, setup['values']), deadline=time.monotonic()+.15)
        start = time.monotonic()
        with pytest.raises(ValueError):
            driver.execute(ctx)
    assert time.monotonic()-start < 1
    assert len(setup['server']['requests']) <= 1
    assert not list(journal.run_dir.rglob('diagnostic.json'))


@pytest.mark.parametrize('change', ['package', 'deadline', 'route', 'inventory-loader'])
def test_terminal_change_withdraws_diagnostic(setup, monkeypatch, change):
    actual = owned._write_terminal
    def changed(directory, name, value):
        ref = actual(directory, name, value)
        if name == 'diagnostic.json':
            if change == 'package':
                (setup['package']/'unmanifested').write_text('changed')
            elif change == 'deadline':
                monkeypatch.setattr(owned.time, 'monotonic', lambda: 10**15)
            elif change == 'inventory-loader':
                monkeypatch.setattr(owned.service_inventory, '__file__', str(setup['ca']))
            else:
                output = next((setup['package'].parents[5]/'run').glob('owned-linux-*'))
                output.rename(output.with_name('moved-diagnostic'))
                output.mkdir(mode=0o700)
        return ref
    monkeypatch.setattr(owned, '_write_terminal', changed)
    journal, driver = setup['start']()
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        with pytest.raises(ValueError):
            driver.execute(context(journal, setup['values']))
    assert not list(journal.run_dir.rglob('diagnostic.json'))


def test_terminal_creation_conflict_preserves_existing_diagnostic(setup, monkeypatch):
    original_open = os.open
    conflict = b'pre-existing diagnostic must survive'
    selected = []

    def collision(path, flags, mode=0o777, *, dir_fd=None):
        if path == 'diagnostic.json' and flags & os.O_EXCL and not selected:
            fd = original_open(path, flags, mode, dir_fd=dir_fd)
            os.write(fd, conflict)
            os.close(fd)
            selected.append(True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(owned.os, 'open', collision)
    journal, driver = setup['start']()
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        with pytest.raises(FileExistsError):
            driver.execute(context(journal, setup['values']))
    paths = list(journal.run_dir.glob('owned-linux-*/diagnostic.json'))
    assert len(paths) == 1 and paths[0].read_bytes() == conflict
    assert not journal.state['lifecycle']['phase_records']


@pytest.mark.parametrize('replacement', ['file', 'symlink'])
def test_failed_final_audit_preserves_replacement_terminal(setup, monkeypatch, tmp_path, replacement):
    write = owned._write_terminal
    target = tmp_path/'replacement-target'
    target.write_bytes(b'replacement must survive')

    def replace_terminal(directory, name, value):
        identity = write(directory, name, value)
        os.rename(name, 'failed-owned-diagnostic.json', src_dir_fd=directory, dst_dir_fd=directory)
        if replacement == 'symlink':
            os.symlink(target, name, dir_fd=directory)
        else:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
            os.write(fd, target.read_bytes())
            os.close(fd)
        (setup['package']/'unmanifested').write_text('Force the post-publication package audit to fail')
        return identity

    monkeypatch.setattr(owned, '_write_terminal', replace_terminal)
    journal, driver = setup['start']()
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        with pytest.raises(ValueError):
            driver.execute(context(journal, setup['values']))
    terminal = next(journal.run_dir.glob('owned-linux-*/diagnostic.json'))
    assert terminal.read_bytes() == target.read_bytes() == b'replacement must survive'
    assert terminal.is_symlink() == (replacement == 'symlink')
    assert not journal.state['lifecycle']['phase_records']


@pytest.mark.parametrize('fault', ['write', 'fsync', 'close', 'interrupt-and-close'])
def test_terminal_io_failure_withdraws_own_file_and_preserves_original_error(tmp_path, monkeypatch, fault):
    original_open, original_write = os.open, os.write
    original_fsync, original_close = os.fsync, os.close
    terminal_fd = []
    directory = original_open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)

    def opened(path, flags, mode=0o777, *, dir_fd=None):
        fd = original_open(path, flags, mode, dir_fd=dir_fd)
        if path == 'diagnostic.json':
            terminal_fd.append(fd)
        return fd

    def write(fd, raw):
        if terminal_fd and fd == terminal_fd[0] and fault in ('write', 'interrupt-and-close'):
            original_write(fd, raw[:3])
            if fault == 'interrupt-and-close':
                raise KeyboardInterrupt('original interruption')
            raise OSError('original write failure')
        return original_write(fd, raw)

    def fsync(fd):
        if terminal_fd and fd == terminal_fd[0] and fault == 'fsync':
            raise OSError('original fsync failure')
        return original_fsync(fd)

    def close(fd):
        original_close(fd)
        if terminal_fd and fd == terminal_fd[0] and fault in ('close', 'interrupt-and-close'):
            raise OSError('close failure')

    monkeypatch.setattr(owned.os, 'open', opened)
    monkeypatch.setattr(owned.os, 'write', write)
    monkeypatch.setattr(owned.os, 'fsync', fsync)
    monkeypatch.setattr(owned.os, 'close', close)
    expected = KeyboardInterrupt if fault == 'interrupt-and-close' else OSError
    message = 'original interruption' if fault == 'interrupt-and-close' else fault+' failure'
    try:
        with pytest.raises(expected, match=message) as caught:
            owned._write_terminal(directory, 'diagnostic.json', {'readiness': 'unknown'})
        assert not (tmp_path/'diagnostic.json').exists()
        if fault == 'interrupt-and-close':
            assert any('Diagnostic close failed' in note for note in caught.value.__notes__)
    finally:
        original_close(directory)


def test_terminal_short_writes_complete_valid_private_json(tmp_path, monkeypatch):
    original_write = os.write
    monkeypatch.setattr(owned.os, 'write', lambda fd, raw: original_write(fd, raw[:3]))
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        identity = owned._write_terminal(directory, 'diagnostic.json', {'readiness': 'unknown'})
    finally:
        os.close(directory)
    terminal = tmp_path/'diagnostic.json'
    assert json.loads(terminal.read_bytes()) == {'readiness': 'unknown'}
    assert terminal.stat().st_mode & 0o777 == 0o600
    assert identity == (terminal.stat().st_dev, terminal.stat().st_ino)


def test_withdrawal_error_preserves_original_interruption(tmp_path, monkeypatch):
    original_write = os.write
    def interrupted(fd, raw):
        original_write(fd, raw[:3])
        raise KeyboardInterrupt('original interruption')
    def denied_unlink(*args, **kwargs):
        raise PermissionError('controlled withdrawal failure')
    monkeypatch.setattr(owned.os, 'write', interrupted)
    monkeypatch.setattr(owned.os, 'unlink', denied_unlink)
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(KeyboardInterrupt, match='original interruption') as caught:
            owned._write_terminal(directory, 'diagnostic.json', {'readiness': 'unknown'})
        assert any('Diagnostic withdrawal failed' in note for note in caught.value.__notes__)
        assert (tmp_path/'diagnostic.json').exists()  # Failed attempt evidence, never accepted output.
    finally:
        os.close(directory)


def test_other_phase_is_explicitly_unimplemented_and_native_cleanup_cannot_pass(setup):
    journal, driver = setup['start']()
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        fixture = journal.run_dir/'fixture-only.json'
        fixture.write_text('{"controlled_fixture":true}')
        fixture.chmod(0o600)
        journal.finish_phase('preflight', 'completed', 'controlled-fixture-only', receipt_artifacts=(fixture,))
        phase = setup['values']['scenario']['phases'][1]
        journal.begin_phase(phase['id'])
        outcome = driver.execute(context(journal, setup['values'], phase))
    assert outcome.status == 'incomplete' and outcome.code == 'owned-linux-phase-unimplemented'
    assert outcome.mutation_effect == 'not_applied' and not setup['server']['requests']
    for call in (lambda: driver.native_inputs(None), lambda: driver.validate_native({}, {}, {}),
                 lambda: driver.validate_cleanup((b'{"success":true}',))):
        with pytest.raises(StateError):
            call()


def test_clean_installed_package_calls_fixed_handler_over_real_tls_without_source_imports(setup, tmp_path):
    from scripts import build_plugin
    archive = tmp_path/'plugin.zip'
    package_info = build_plugin.build(archive)
    extracted = tmp_path/'extracted'
    with zipfile.ZipFile(archive) as package:
        package.extractall(extracted)
    home = tmp_path/'actual installed runtime with spaces'
    installed = home/'plugins/cache/devsecops-local/devsecops'/package_info['version']
    installed.parent.mkdir(parents=True)
    (extracted/'devsecops').rename(installed)
    values = tmp_path/'diagnostic-inputs.json'
    values.write_bytes(_canonical(setup['values']))
    program = '''
import json,sys,time
from pathlib import Path
root,values_path,run_path=map(Path,sys.argv[1:4])
sys.path.insert(0,str(root/'src'))
from devsecops.factory.owned_linux import OwnedLinuxDriver,DRIVER_ID
from devsecops.factory.lifecycle import PhaseContext,_readonly
from devsecops.factory.state import RunJournal,_canonical,_sha
import devsecops.factory.owned_linux as implementation
values=json.loads(values_path.read_bytes())
raw={name:_canonical(value) for name,value in values.items()}
for name in ('scenario','assertions'):
    raw[name]=(root/'demo/software-factory/scenarios/ironbank-mission-recovery-v1'/(name+'.json')).read_bytes()
raw['package']=(root/'SHA256SUMS').read_bytes()
identities={name+'_sha256':_sha(value) for name,value in raw.items()}
journal=RunJournal.create(run_path,identities=identities,environment_id=values['config']['environment'])
journal.initialize_lifecycle(inputs=raw,execution_mode='controlled-test',driver_id=DRIVER_ID)
version=json.loads((root/'.codex-plugin/plugin.json').read_bytes())['version']
driver=OwnedLinuxDriver(journal,plugin_id='devsecops@devsecops-local',plugin_version=version)
with journal.lifecycle_lock():
    journal.begin_phase('preflight')
    state=journal.state
    context=PhaseContext(state['run_id'],_readonly(values['scenario']['phases'][0]),_readonly(identities),
        _readonly(values),_readonly(state['lifecycle']['inputs']),journal.run_dir,time.monotonic()+60,'controlled-test')
    outcome=driver.execute(context)
print(json.dumps({'module':implementation.__file__,'status':outcome.status,'report':json.loads(outcome.artifacts[0].read_bytes())}))
'''
    env = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    env['CODEX_HOME'] = str(home)
    result = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(installed), str(values), str(tmp_path/'installed-run')],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert Path(observed['module']).is_relative_to(installed)
    assert observed['status'] == 'incomplete' and observed['report']['package']['content_verified']
    assert observed['report']['canonical_success'] is False
    assert len(setup['server']['requests']) == 6
    assert all(value not in result.stdout+result.stderr for value in (PRIVATE, READ_TOKEN, OPERATOR_TOKEN))
