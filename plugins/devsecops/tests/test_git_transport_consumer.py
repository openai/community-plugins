"""Consumer output stays local, bounded and tied to a live direct transport plan."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import pickle
import shutil
import stat
import time

import pytest

from devsecops.factory import deployment_commit as commits
from devsecops.factory import git_transport as transport
from test_git_transport import SCOPE, case, inventory, prepared_case


__all__ = ['case']
REJECTED = (ValueError, OSError, TypeError)
MIB = 1024 * 1024


@pytest.fixture
def consumer_case(case):
    # All authority is the existing explicitly non-live, process-local fixture.
    # A nested scope also exercises defensive copying in every consumer path.
    scope = {'run': 'consumer-fixture-only', 'attempt': {'number': 1}}
    case['args']['run_scope'] = scope
    case['binding']['run_scope'] = scope
    args, _, _ = prepared_case(case)
    report, plan = transport.prepare_git_transport(**args)
    assert report['decision'] == 'pass' and type(plan) is transport.PreparedGitTransport
    return {'case': case, 'plan': plan, 'prepared': args['prepared'],
            'scope': deepcopy({key: args[key] for key in SCOPE}),
            'transport_output': args['output'],
            'output': args['output'].parent / 'consumer'}


def consumer(fixture, *, output=None, scope=None, deadline=None):
    return transport.open_transport_consumer_output(
        fixture['plan'], **(fixture['scope'] if scope is None else scope),
        output=fixture['output'] if output is None else output,
        deadline=time.monotonic() + 60 if deadline is None else deadline)


def digest(raw):
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def forbidden(*args, **kwargs):
    pytest.fail('Rejected consumer input reached filesystem mutation or native execution')


def corrupt(path):
    path.chmod(0o600)
    path.write_bytes(path.read_bytes() + b'CHANGED')


def test_sibling_output_is_private_exclusive_and_confers_no_authority(consumer_case, monkeypatch):
    fixture = consumer_case
    before = transport.require_prepared_git_transport(fixture['plan'], **fixture['scope'])
    protected = [fixture['case']['repo'], fixture['case']['args']['output'], fixture['transport_output']]
    originals = [inventory(path) for path in protected]
    monkeypatch.setattr(transport, 'run', forbidden)
    with consumer(fixture) as admitted:
        assert not isinstance(admitted, transport.PreparedGitTransport)
        admitted.audit()
        fd = admitted.directory_fd
        info = os.fstat(fd)
        assert stat.S_ISDIR(info.st_mode)
        assert (info.st_dev, info.st_ino) == (fixture['output'].stat().st_dev, fixture['output'].stat().st_ino)
        assert stat.S_IMODE(info.st_mode) == 0o700
        assert admitted.write_bytes('objects.bin', b'local bytes') == digest(b'local bytes')
        assert admitted.write_bytes('receipt.json', b'{"local":true}\n', terminal=True) == digest(b'{"local":true}\n')
        admitted.audit()
    assert set(path.name for path in fixture['output'].iterdir()) == {'objects.bin', 'receipt.json'}
    assert (fixture['output'] / 'objects.bin').read_bytes() == b'local bytes'
    for path in fixture['output'].iterdir():
        assert path.stat().st_mode & 0o077 == 0
    after = transport.require_prepared_git_transport(fixture['plan'], **fixture['scope'])
    assert after == before
    assert after['git_destination'] is None and after['transmitted_pack'] is None
    assert after['remote_possession'] == 'unverified' and after['omission_authorized'] is False
    assert originals == [inventory(path) for path in protected]


def test_protected_roots_and_aliases_reject_before_any_mkdir(consumer_case, tmp_path, monkeypatch):
    fixture = consumer_case
    roots = [fixture['case']['repo'], fixture['case']['repo'] / '.git',
             fixture['case']['args']['output'], fixture['transport_output']]
    outputs = []
    for number, root in enumerate(roots):
        outputs.extend([root, root / 'new-parent' / 'consumer'])
        alias = tmp_path / f'alias-{number}'
        alias.symlink_to(root, target_is_directory=True)
        outputs.append(alias / 'new-parent' / 'consumer')
        case_alias = root.with_name(root.name.swapcase())
        if case_alias.exists() and case_alias.samefile(root):
            outputs.append(case_alias / 'new-parent' / 'consumer')
    originals = [inventory(path) for path in roots]
    monkeypatch.setattr(transport.os, 'mkdir', forbidden)
    for output in outputs:
        with pytest.raises(REJECTED):
            with consumer(fixture, output=output):
                pytest.fail('Protected output was admitted')
    assert originals == [inventory(path) for path in roots]


def test_existing_output_is_not_adopted_or_changed(consumer_case):
    fixture = consumer_case
    fixture['output'].mkdir()
    (fixture['output'] / 'canary').write_bytes(b'PREEXISTING')
    original = inventory(fixture['output'])
    with pytest.raises(REJECTED):
        with consumer(fixture):
            pytest.fail('Existing directory was adopted')
    assert inventory(fixture['output']) == original


def test_direct_authority_pid_expiry_and_scope_fail_before_creation(consumer_case, monkeypatch):
    fixture = consumer_case
    monkeypatch.setattr(transport.os, 'mkdir', forbidden)
    plan_record = transport._PLANS[fixture['plan']]
    prepared_record = commits._PREPARED[fixture['prepared']]
    for record, key, bad in ((plan_record, 'pid', -1), (plan_record, 'expires_monotonic', 0),
                             (prepared_record, 'pid', -1), (prepared_record, 'expires_monotonic', 0)):
        original = record[key]
        try:
            record[key] = bad
            with pytest.raises(REJECTED):
                with consumer(fixture):
                    pytest.fail('Invalid direct authority was admitted')
        finally:
            record[key] = original
    for key in SCOPE:
        scope = deepcopy(fixture['scope'])
        scope[key] = {'different': True} if key == 'run_scope' else '0' * len(scope[key])
        with pytest.raises(REJECTED):
            with consumer(fixture, scope=scope):
                pytest.fail('Mismatched scope was admitted')
    for forged in (fixture['plan'].describe(), object.__new__(transport.PreparedGitTransport)):
        with pytest.raises(REJECTED):
            with transport.open_transport_consumer_output(forged, **fixture['scope'],
                    output=fixture['output'], deadline=time.monotonic() + 60):
                pytest.fail('Reconstructed authority was admitted')
    assert not fixture['output'].exists()


@pytest.mark.parametrize('target', ['repository', 'git-metadata', 'prepared-evidence', 'transport-evidence'])
def test_same_bytes_in_replaced_source_directories_are_rejected(consumer_case, monkeypatch, target):
    fixture = consumer_case
    path = {'repository': fixture['case']['repo'], 'git-metadata': fixture['case']['repo'] / '.git',
            'prepared-evidence': fixture['case']['args']['output'],
            'transport-evidence': fixture['transport_output']}[target]
    moved = path.with_name(path.name + '-original')
    path.rename(moved)
    shutil.copytree(moved, path, symlinks=True)
    monkeypatch.setattr(transport.os, 'mkdir', forbidden)
    with pytest.raises(REJECTED):
        with consumer(fixture):
            pytest.fail('Replaced source directory was admitted')
    assert not fixture['output'].exists()


@pytest.mark.parametrize('target', ['prepared', 'transport'])
def test_retained_byte_changes_reject_before_output_creation(consumer_case, monkeypatch, target):
    fixture = consumer_case
    path = (fixture['case']['args']['output'] / 'commit.txt' if target == 'prepared'
            else fixture['transport_output'] / 'base.pack')
    corrupt(path)
    monkeypatch.setattr(transport.os, 'mkdir', forbidden)
    with pytest.raises(REJECTED):
        with consumer(fixture):
            pytest.fail('Changed retained bytes were admitted')
    assert not fixture['output'].exists()


@pytest.mark.parametrize('target', ['repository', 'git-metadata', 'prepared-evidence', 'transport-evidence'])
def test_directory_replacement_during_context_is_reaudited_at_exit(consumer_case, target):
    fixture = consumer_case
    path = {'repository': fixture['case']['repo'], 'git-metadata': fixture['case']['repo'] / '.git',
            'prepared-evidence': fixture['case']['args']['output'],
            'transport-evidence': fixture['transport_output']}[target]
    with pytest.raises(REJECTED):
        with consumer(fixture) as admitted:
            admitted.write_bytes('diagnostic.bin', b'RETAIN-ME')
            admitted.write_bytes('terminal.json', b'{}', terminal=True)
            moved = path.with_name(path.name + '-original')
            path.rename(moved)
            shutil.copytree(moved, path, symlinks=True)
    assert (fixture['output'] / 'diagnostic.bin').read_bytes() == b'RETAIN-ME'
    assert not (fixture['output'] / 'terminal.json').exists()


@pytest.mark.parametrize('target', ['prepared-bytes', 'transport-bytes', 'prepared-expiry', 'transport-expiry'])
def test_exit_reaudit_withdraws_owned_terminals_and_retains_other_artifacts(consumer_case, target):
    fixture = consumer_case
    with pytest.raises(REJECTED):
        with consumer(fixture) as admitted:
            admitted.write_bytes('diagnostic.bin', b'RETAIN-ME')
            admitted.write_bytes('terminal.json', b'{"local":true}', terminal=True)
            admitted.write_bytes('second-terminal.json', b'{}', terminal=True)
            if target == 'prepared-bytes':
                corrupt(fixture['case']['args']['output'] / 'commit.txt')
            elif target == 'transport-bytes':
                corrupt(fixture['transport_output'] / 'base.pack')
            elif target == 'prepared-expiry':
                commits._PREPARED[fixture['prepared']]['expires_monotonic'] = 0
            else:
                transport._PLANS[fixture['plan']]['expires_monotonic'] = 0
    assert (fixture['output'] / 'diagnostic.bin').read_bytes() == b'RETAIN-ME'
    assert not (fixture['output'] / 'terminal.json').exists()
    assert not (fixture['output'] / 'second-terminal.json').exists()


def test_parent_route_swap_between_validation_and_creation_cannot_write_outside(consumer_case, tmp_path, monkeypatch):
    fixture = consumer_case
    parent = tmp_path / 'destination-parent'
    parent.mkdir()
    output = parent / 'consumer'
    moved = tmp_path / 'held-parent'
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'canary').write_bytes(b'OUTSIDE')
    original_mkdir = os.mkdir
    fired = False

    def swap_before_create(path, mode=0o777, *, dir_fd=None):
        nonlocal fired
        if path == output.name and dir_fd is not None and not fired:
            fired = True
            parent.rename(moved)
            parent.symlink_to(outside, target_is_directory=True)
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(transport.os, 'mkdir', swap_before_create)
    with pytest.raises(REJECTED):
        with consumer(fixture, output=output):
            pytest.fail('Redirected output route was admitted')
    assert fired
    assert set(path.name for path in outside.iterdir()) == {'canary'}
    assert (outside / 'canary').read_bytes() == b'OUTSIDE'


def test_route_swap_after_write_guard_stays_on_held_directory_and_withdraws_terminal(consumer_case, tmp_path, monkeypatch):
    fixture = consumer_case
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'canary').write_bytes(b'OUTSIDE')
    moved = tmp_path / 'moved-consumer'
    original_open = os.open
    fired = False

    def swap_before_write(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal fired
        if path == 'new-terminal.json' and flags & os.O_CREAT and not fired:
            fired = True
            fixture['output'].rename(moved)
            fixture['output'].symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    with pytest.raises(REJECTED):
        with consumer(fixture) as admitted:
            admitted.write_bytes('diagnostic.bin', b'RETAIN-ME')
            admitted.write_bytes('old-terminal.json', b'{}', terminal=True)
            monkeypatch.setattr(transport.os, 'open', swap_before_write)
            admitted.write_bytes('new-terminal.json', b'{}', terminal=True)
    assert fired
    assert set(path.name for path in outside.iterdir()) == {'canary'}
    assert (outside / 'canary').read_bytes() == b'OUTSIDE'
    assert (moved / 'diagnostic.bin').read_bytes() == b'RETAIN-ME'
    assert not (moved / 'old-terminal.json').exists()
    assert not (moved / 'new-terminal.json').exists()


@pytest.mark.parametrize('kind', ['closed', 'retargeted'])
def test_borrowed_descriptor_tampering_rejects_without_outside_write_or_unlink(consumer_case, tmp_path, kind):
    fixture = consumer_case
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'terminal.json').write_bytes(b'OUTSIDE-CANARY')
    borrowed = None
    with pytest.raises(REJECTED):
        with consumer(fixture) as admitted:
            admitted.write_bytes('terminal.json', b'{}', terminal=True)
            borrowed = admitted.directory_fd
            if kind == 'closed':
                os.close(borrowed)
            else:
                external_fd = os.open(outside, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.dup2(external_fd, borrowed)
                finally:
                    os.close(external_fd)
            admitted.write_bytes('attempt.bin', b'MUST-NOT-ESCAPE')
    assert set(path.name for path in outside.iterdir()) == {'terminal.json'}
    assert (outside / 'terminal.json').read_bytes() == b'OUTSIDE-CANARY'
    # Cleanup must use the private held output, never the caller-retargeted fd.
    assert not (fixture['output'] / 'terminal.json').exists()
    if kind == 'retargeted':
        try:
            os.close(borrowed)
        except OSError:
            pass


def test_scope_is_frozen_at_entry_and_lifetime_is_context_bound(consumer_case):
    fixture = consumer_case
    scope = deepcopy(fixture['scope'])
    with consumer(fixture, scope=scope) as admitted:
        scope['run_scope']['attempt']['number'] = 99
        admitted.audit()
        admitted.write_bytes('local.bin', b'unchanged admitted scope')
        with pytest.raises(TypeError):
            type(admitted)()
        with pytest.raises(TypeError):
            pickle.dumps(admitted)
    for operation in (admitted.audit, lambda: admitted.directory_fd,
                      lambda: admitted.write_bytes('late.bin', b'late')):
        with pytest.raises(REJECTED):
            operation()
    assert not (fixture['output'] / 'late.bin').exists()
    actual = transport.require_prepared_git_transport(fixture['plan'], **fixture['scope'])
    assert actual['run_scope'] == fixture['scope']['run_scope']


def test_raw_names_types_and_exclusive_writes_are_bounded(consumer_case, tmp_path):
    fixture = consumer_case
    bad_names = ['', '.', '..', '../escape', 'nested/name', '/absolute', 'bad\\name',
                 'nul\0name', 'line\nname', 'x' * 256, b'bytes', Path('path'), None, 1]
    with consumer(fixture) as admitted:
        for name in bad_names:
            with pytest.raises(REJECTED):
                admitted.write_bytes(name, b'no')
        for raw in ('text', bytearray(b'bytes'), memoryview(b'bytes'), None, 1):
            with pytest.raises(REJECTED):
                admitted.write_bytes('invalid-raw.bin', raw)
        with pytest.raises(REJECTED):
            admitted.write_bytes('too-large.bin', b'x' * (8 * MIB + 1))
        admitted.write_bytes('exclusive.bin', b'ORIGINAL')
        admitted.write_bytes('terminal.json', b'{}', terminal=True)
        with pytest.raises(REJECTED):
            admitted.write_bytes('exclusive.bin', b'REPLACEMENT')
        with pytest.raises(REJECTED):
            admitted.write_bytes('terminal.json', b'REPLACEMENT', terminal=True)
        assert (fixture['output'] / 'exclusive.bin').read_bytes() == b'ORIGINAL'
        assert (fixture['output'] / 'terminal.json').read_bytes() == b'{}'
    assert set(path.name for path in fixture['output'].iterdir()) == {'exclusive.bin', 'terminal.json'}
    assert not (tmp_path / 'escape').exists()


def test_preexisting_link_name_is_never_followed_or_overwritten(consumer_case, tmp_path):
    fixture = consumer_case
    outside = tmp_path / 'outside-canary'
    outside.write_bytes(b'OUTSIDE')
    with pytest.raises(REJECTED):
        with consumer(fixture) as admitted:
            os.symlink(outside, 'linked.bin', dir_fd=admitted.directory_fd)
            admitted.write_bytes('linked.bin', b'MUST-NOT-OVERWRITE', terminal=True)
    assert (fixture['output'] / 'linked.bin').is_symlink()
    assert outside.read_bytes() == b'OUTSIDE'


def test_aggregate_byte_limit_accepts_exact_boundary_and_rejects_one_more(consumer_case):
    fixture = consumer_case
    raw = b'x' * (8 * MIB)
    with consumer(fixture) as admitted:
        assert admitted.write_bytes('first.bin', raw) == digest(raw)
        assert admitted.write_bytes('second.bin', raw) == digest(raw)
        with pytest.raises(REJECTED):
            admitted.write_bytes('overflow.bin', b'x')
    assert set(path.name for path in fixture['output'].iterdir()) == {'first.bin', 'second.bin'}


def test_file_count_limit_accepts_64_and_rejects_65th(consumer_case):
    fixture = consumer_case
    with consumer(fixture) as admitted:
        for index in range(64):
            admitted.write_bytes(f'artifact-{index}.bin', b'x')
        with pytest.raises(REJECTED):
            admitted.write_bytes('overflow.bin', b'x')
    assert len(list(fixture['output'].iterdir())) == 64


def test_invalid_deadlines_reject_before_io(consumer_case, monkeypatch):
    fixture = consumer_case
    monkeypatch.setattr(transport, '_open_directory', forbidden)
    monkeypatch.setattr(transport.os, 'open', forbidden)
    monkeypatch.setattr(transport.os, 'mkdir', forbidden)
    for deadline in (True, False, float('nan'), float('inf'), -float('inf'), 'soon', object()):
        with pytest.raises(REJECTED):
            transport.require_prepared_git_transport(fixture['plan'], **fixture['scope'], deadline=deadline)
        with pytest.raises(REJECTED):
            with consumer(fixture, deadline=deadline):
                pytest.fail('Invalid deadline was admitted')


def test_consumer_deadline_is_shared_and_expires_during_retained_audit_before_creation(consumer_case, monkeypatch):
    fixture = consumer_case
    clock = [time.monotonic()]
    deadline = clock[0] + 1
    original_read = transport._read_retained
    observed = []

    def expire_after_read(directory, name, expected, selected_deadline, **kwargs):
        observed.append(selected_deadline)
        result = original_read(directory, name, expected, selected_deadline, **kwargs)
        clock[0] = deadline + 1
        return result

    monkeypatch.setattr(transport.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(transport, '_read_retained', expire_after_read)
    monkeypatch.setattr(transport.os, 'mkdir', forbidden)
    with pytest.raises(REJECTED):
        with consumer(fixture, deadline=deadline):
            pytest.fail('Deadline expiry during retained audit was admitted')
    assert observed and all(value <= deadline for value in observed)
    assert not fixture['output'].exists()


def test_direct_requirement_clamps_deadline_and_rejects_expiry_during_retained_read(consumer_case, monkeypatch):
    fixture = consumer_case
    clock = [time.monotonic()]
    started = clock[0]
    original_read = transport._read_retained
    observed = []
    expire = [False]

    def audit_read(directory, name, expected, deadline, **kwargs):
        observed.append(deadline)
        result = original_read(directory, name, expected, deadline, **kwargs)
        if expire[0]:
            clock[0] = deadline + 1
        return result

    monkeypatch.setattr(transport.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(transport, '_read_retained', audit_read)
    transport.require_prepared_git_transport(fixture['plan'], **fixture['scope'], deadline=started + 100)
    assert observed and all(value <= started + 10 for value in observed)
    observed.clear()
    expire[0] = True
    with pytest.raises(REJECTED):
        transport.require_prepared_git_transport(fixture['plan'], **fixture['scope'], deadline=started + 1)
    assert observed and all(value <= started + 1 for value in observed)


def test_deadline_expiring_during_write_audit_prevents_payload_and_withdraws_terminal(consumer_case, monkeypatch):
    fixture = consumer_case
    clock = [time.monotonic()]
    deadline = clock[0] + 60
    original_read = transport._read_retained
    observed = []

    def expire_after_read(directory, name, expected, selected_deadline, **kwargs):
        observed.append(selected_deadline)
        result = original_read(directory, name, expected, selected_deadline, **kwargs)
        clock[0] = deadline + 1
        return result

    with pytest.raises(REJECTED):
        with consumer(fixture, deadline=deadline) as admitted:
            admitted.write_bytes('diagnostic.bin', b'RETAIN-ME')
            admitted.write_bytes('terminal.json', b'{}', terminal=True)
            monkeypatch.setattr(transport.time, 'monotonic', lambda: clock[0])
            monkeypatch.setattr(transport, '_read_retained', expire_after_read)
            admitted.write_bytes('late.bin', b'MUST-NOT-BE-WRITTEN')
    assert observed and all(value <= deadline for value in observed)
    assert (fixture['output'] / 'diagnostic.bin').read_bytes() == b'RETAIN-ME'
    assert not (fixture['output'] / 'late.bin').exists()
    assert not (fixture['output'] / 'terminal.json').exists()


@pytest.mark.parametrize('operation', ['audit', 'directory_fd'])
def test_caught_public_audit_failure_immediately_withdraws_terminal(consumer_case, operation):
    fixture = consumer_case
    diagnostic = fixture['output'] / 'diagnostic.bin'
    terminal = fixture['output'] / 'terminal.json'
    original = b'LOCAL-DIAGNOSTIC'
    with pytest.raises(REJECTED):
        with consumer(fixture) as admitted:
            admitted.write_bytes('diagnostic.bin', original)
            admitted.write_bytes('terminal.json', b'{"local":true}', terminal=True)
            assert terminal.exists()
            corrupt(diagnostic)
            with pytest.raises(REJECTED):
                if operation == 'audit':
                    admitted.audit()
                else:
                    _ = admitted.directory_fd
            # Catching the error cannot leave a previous success artifact visible.
            assert not terminal.exists()
            diagnostic.write_bytes(original)
            # Normal context exit must still reject the withdrawn terminal's
            # retained reference, even though the changed diagnostic is restored.
    assert diagnostic.read_bytes() == original
    assert not terminal.exists()


def test_unissued_entries_stop_directory_enumeration_at_first_excess(consumer_case, monkeypatch):
    fixture = consumer_case
    original_scandir, original_listdir = os.scandir, os.listdir
    observed = []
    output_identity = None
    expected_files = 2

    def selected_output(path):
        if type(path) is not int:
            return False
        info = os.fstat(path)
        return (info.st_dev, info.st_ino) == output_identity

    class CountedEntries:
        def __init__(self, entries):
            self.entries = entries
            self.count = 0
            observed.append(self)

        def __enter__(self):
            self.entries.__enter__()
            return self

        def __exit__(self, *args):
            return self.entries.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self.entries)
            self.count += 1
            return entry

    def bounded_scandir(path):
        entries = original_scandir(path)
        return CountedEntries(entries) if selected_output(path) else entries

    def no_output_listdir(path):
        if selected_output(path):
            pytest.fail('Consumer audit materialized an unbounded directory listing')
        return original_listdir(path)

    with pytest.raises(REJECTED):
        with consumer(fixture) as admitted:
            admitted.write_bytes('diagnostic.bin', b'RETAIN-ME')
            admitted.write_bytes('terminal.json', b'{}', terminal=True)
            borrowed = admitted.directory_fd
            info = os.fstat(borrowed)
            output_identity = (info.st_dev, info.st_ino)
            # Deliberate caller tampering through the borrowed fd; only this
            # small real directory is used to test an otherwise unbounded scan.
            for index in range(70):
                fd = os.open(f'unissued-{index}.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             0o600, dir_fd=borrowed)
                os.close(fd)
            monkeypatch.setattr(transport.os, 'scandir', bounded_scandir)
            monkeypatch.setattr(transport.os, 'listdir', no_output_listdir)
            with pytest.raises(REJECTED):
                admitted.audit()
            assert observed and all(scan.count <= expected_files + 1 for scan in observed)
            assert not (fixture['output'] / 'terminal.json').exists()
    assert observed and all(scan.count <= expected_files + 1 for scan in observed)
    assert (fixture['output'] / 'diagnostic.bin').read_bytes() == b'RETAIN-ME'
    assert not (fixture['output'] / 'terminal.json').exists()
