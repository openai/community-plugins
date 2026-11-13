"""Private checkpoint fixtures; no platform, native model, cleanup or live pass."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import time
import zipfile

import pytest

from devsecops.factory import evidence_export as export, owned_linux as owned
from devsecops.factory.lifecycle import LifecycleEngine, PhaseContext, SCENARIO_ROOT, _readonly
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha
from tests.test_factory_contracts import config, factory_lock
from tests.test_factory_state import Clock, native_result, validate_fixture


def _write(path, raw):
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def _references(value):
    """Test oracle over the strict state schema, never production path discovery."""
    if isinstance(value, dict):
        if {'path', 'sha256', 'bytes'} <= value.keys():
            yield {key: value[key] for key in ('path', 'sha256', 'bytes')}
        for child in value.values():
            yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def _context(journal, values, phase_id='export'):
    state = journal.state
    phase = next(p for p in values['scenario']['phases'] if p['id'] == phase_id)
    return PhaseContext(state['run_id'], _readonly(phase), _readonly(journal.identities),
        _readonly(values), _readonly(state['lifecycle']['inputs']), journal.run_dir,
        time.monotonic() + 30, state['lifecycle']['execution_mode'])


@pytest.fixture
def factory(tmp_path, monkeypatch):
    """Actual local journal/package fixtures, independent of TLS diagnostics."""
    package = tmp_path/'codex-home/plugins/cache/devsecops-local/devsecops/0.1.0'
    files = {'.codex-plugin/plugin.json': _canonical({'name': 'devsecops', 'version': '0.1.0'}),
        **{'src/devsecops/factory/'+name+'.py': Path(module.__file__).read_bytes()
           for name, module in [('owned_linux', owned), ('service_inventory', owned.service_inventory),
                                ('evidence_export', export), ('auxiliary_inputs', owned.auxiliary_inputs)]},
        **{'demo/software-factory/scenarios/ironbank-mission-recovery-v1/'+name+'.json':
           (SCENARIO_ROOT/(name+'.json')).read_bytes() for name in ('scenario', 'assertions')}}
    for name, raw in files.items():
        path = package/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = ''.join(f'{_sha(raw)}  {name}\n' for name, raw in sorted(files.items())).encode()
    (package/'SHA256SUMS').write_bytes(manifest)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path/'codex-home'))
    monkeypatch.setattr(owned, 'PACKAGE_ROOT', package)
    for name, module in [('owned_linux', owned), ('service_inventory', owned.service_inventory),
                         ('evidence_export', export), ('auxiliary_inputs', owned.auxiliary_inputs)]:
        monkeypatch.setattr(module, '__file__', str(package/'src/devsecops/factory'/f'{name}.py'))
    monkeypatch.setattr(owned, 'HTTPSReadClient', lambda *a, **k: pytest.fail('export contacted a service'))
    monkeypatch.setattr(owned, 'inspect', lambda *a, **k: pytest.fail('export invoked preflight'))
    count = 0

    @contextmanager
    def make(*, normal=False, native=None, operation=True, expired=False, active=True, engine=False):
        nonlocal count
        count += 1
        selected = config()
        selected['expires_at'] = (datetime.now(timezone.utc) + timedelta(
            days=-1 if expired else 1)).isoformat()
        selected['allowed_mutations'] = ['acquire', 'provision', 'build', 'promote', 'inject',
                                        'deliver', 'probe', 'reset', 'cleanup']
        scenario = json.loads(files['demo/software-factory/scenarios/ironbank-mission-recovery-v1/scenario.json'])
        assertions = json.loads(files['demo/software-factory/scenarios/ironbank-mission-recovery-v1/assertions.json'])
        lock = factory_lock()
        lock.update(scenario_sha256=_sha((SCENARIO_ROOT/'scenario.json').read_bytes()),
                    assertions_sha256=_sha((SCENARIO_ROOT/'assertions.json').read_bytes()))
        values = dict(scenario=scenario, assertions=assertions, config=selected, lock=lock,
                      environment={'environment_id': selected['environment'], 'controlled_test': True})
        raw = {name: _canonical(value) for name, value in values.items()}
        raw.update(package=manifest, scenario=(SCENARIO_ROOT/'scenario.json').read_bytes(),
                   assertions=(SCENARIO_ROOT/'assertions.json').read_bytes())
        identities = {name+'_sha256': _sha(value) for name, value in raw.items()}
        journal = RunJournal.create(tmp_path/f'run {count}', identities=identities,
            environment_id=selected['environment'], clock=Clock())
        mode = 'operator' if engine else 'controlled-test'
        journal.initialize_lifecycle(inputs=raw, execution_mode=mode, driver_id='owned-linux')
        selected_engine = None
        if engine:
            # Exercise the registered dispatcher. Prior local fixture receipts
            # carry no scenario assertions or live qualification, in either mode.
            selected_engine = LifecycleEngine(journal,
                inputs={name: journal.run_dir/ref['path'] for name, ref in journal.state['lifecycle']['inputs'].items()},
                shared_lock_dir=tmp_path/f'locks-{count}', driver_id='owned-linux',
                plugin_id='devsecops@devsecops-local', plugin_version='0.1.0')
            driver = selected_engine.driver
        else:
            driver = owned.OwnedLinuxDriver(journal, plugin_id='devsecops@devsecops-local', plugin_version='0.1.0')
        evidence = _write(journal.run_dir/'unregistered-fixture-source', b'controlled local evidence\n')
        with ExitStack() as stack:
            stack.enter_context(journal.lifecycle_lock())
            stack.enter_context(journal.mutation_lock(tmp_path/f'locks-{count}'))
            for phase in scenario['phases']:
                if phase['id'] == 'export':
                    break
                journal.begin_phase(phase['id'])
                if phase['id'] == 'prepare' and operation:
                    op = journal.begin_mutation('prepare', 'prepare', intent_artifacts=[evidence])
                    journal.reconcile_mutation(op, 'not_applied', receipt_artifacts=[evidence])
                if native is not None and phase['operation'] == 'native':
                    request = journal.request_native(phase['id'], phase['workflow'],
                        input_artifacts={'local-observation': evidence}, ttl_seconds=30)
                    if native == 'pending':
                        yield {'journal': journal, 'driver': driver, 'values': values,
                               'context': _context(journal, values, phase['id']), 'package': package,
                               'evidence': evidence, 'request': request}
                        return
                    if native == 'cancelled':
                        journal.cancel_native('controlled-cancellation')
                    else:
                        result, _ = native_result(journal, request, status=native)
                        journal.accept_native(result, identities=identities, validator=validate_fixture)
                    journal.finish_phase(phase['id'], 'incomplete', 'controlled-native-stop',
                                         receipt_artifacts=[evidence])
                    journal.abort_lifecycle(phase['id'], 'incomplete', 'controlled-native-stop')
                    break
                if phase['id'] == 'prepare' and not normal and native is None:
                    journal.finish_phase('prepare', 'error', 'controlled-original-failure',
                                         receipt_artifacts=[evidence])
                    journal.abort_lifecycle('prepare', 'error', 'controlled-original-failure')
                    break
                journal.finish_phase(phase['id'], 'completed', 'controlled-local-fixture',
                                     receipt_artifacts=[evidence])
            if active:
                journal.begin_phase('export')
            yield {'journal': journal, 'driver': driver, 'values': values,
                   'context': _context(journal, values), 'package': package, 'evidence': evidence,
                   'engine': selected_engine}
    return make


def _export(ready):
    context = ready['context']
    return export.export_checkpoint(ready['journal'], context, deadline=context.deadline)


def _archive(artifacts):
    manifests = [path for path in artifacts if path.name == 'checkpoint-manifest.json']
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_bytes())
    parts = manifest['archive']['parts']
    assert set(path.name for path in artifacts) == {'checkpoint-manifest.json', *(p['path'] for p in parts)}
    raw = b''
    for part in parts:
        data = (manifests[0].parent/part['path']).read_bytes()
        assert len(data) == part['bytes'] and _sha(data) == part['sha256']
        raw += data
    assert len(raw) == manifest['archive']['bytes']
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.testzip() is None
        assert all(item.compress_type == zipfile.ZIP_STORED for item in archive.infolist())
        members = {name: archive.read(name) for name in archive.namelist()}
        assert len(members) == len(archive.namelist())
    assert {item['name'] for item in manifest['members']} == set(members)
    for member in manifest['members']:
        assert member['bytes'] == len(members[member['name']])
        assert member['sha256'] == _sha(members[member['name']])
    return manifest, members


@pytest.mark.parametrize('normal', [False, True])
def test_exact_registered_checkpoint_is_private_pre_cleanup_and_does_not_advance(factory, normal):
    with factory(normal=normal) as ready:
        journal = ready['journal']
        before = journal.state
        expected = list(_references(before))
        with _export(ready) as artifacts:
            manifest, members = _archive(artifacts)
        assert journal.state == before
        assert manifest['kind'] == 'factory-private-journal-export'
        assert manifest['snapshot_status'] == 'complete'
        assert manifest['terminal'] is False and manifest['canonical_success'] is False
        assert manifest['membership'] == 'journal-registered-artifacts-only'
        assert manifest['checkpoint'] == {
            'journal_seq': before['journal_seq'], 'journal_head_sha256': before['journal_head_sha256'],
            'state_sha256': _sha(_canonical(before))}
        assert json.loads(members['checkpoint.json'])['state'] == before
        assert len(manifest['references']) == len(expected)
        assert {(r['path'], r['sha256'], r['bytes']) for r in manifest['references']} == {
            (r['path'], r['sha256'], r['bytes']) for r in expected}
        for ref in manifest['references']:
            assert members[ref['member']] == (journal.run_dir/ref['path']).read_bytes()
        assert len([name for name in members if name.startswith('artifacts/')]) == len({r['sha256'] for r in expected})
        for seq in range(1, before['journal_seq']+1):
            name = f'journal/{seq:08d}.json'
            assert members[name] == (journal.run_dir/name).read_bytes()
        assert 'checkpoint-manifest.json' not in members
        assert len([name for name in members if name.startswith('journal/')]) == before['journal_seq']
        assert journal.state['lifecycle']['final_result'] is None
        assert journal.state['environment_reserved'] is True
        assert all(path.stat().st_mode & 0o077 == 0 for path in artifacts)
        assert artifacts[0].parent.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('status', ['cancelled', 'incomplete', 'completed'])
def test_native_request_and_accepted_artifacts_remain_evidence_without_result_destinations(factory, status):
    with factory(native=status) as ready:
        state = ready['journal'].state
        request = state['native_requests'][0]
        document = json.loads((ready['journal'].run_dir/request['request']['path']).read_bytes())
        orphan = ready['journal'].run_dir/document['output_directory']/'unregistered-secret'
        orphan.write_bytes(b'UNREGISTERED-NATIVE-SECRET')
        with _export(ready) as artifacts:
            manifest, members = _archive(artifacts)
        selected = {r['path'] for r in manifest['references']}
        assert request['request']['path'] in selected
        assert all(r['path'] in selected for r in request['input_artifacts'])
        assert str(orphan.relative_to(ready['journal'].run_dir)) not in selected
        assert b'UNREGISTERED-NATIVE-SECRET' not in b''.join(members.values())
        if status == 'cancelled':
            assert document['result_path'] not in selected
        else:
            accepted = state['native_results'][0]
            assert accepted['result']['path'] in selected and accepted['validation']['path'] in selected
            assert all(r['path'] in selected for r in accepted['artifacts'])


def test_opaque_report_paths_unregistered_tree_and_environment_values_are_never_discovered(factory, monkeypatch):
    monkeypatch.setenv('EXPORT_CREDENTIAL_CANARY', 'PRIVATE-ENVIRONMENT-CREDENTIAL')
    with factory() as ready:
        journal = ready['journal']
        hidden = _write(journal.run_dir/'credential-canary', b'PRIVATE-UNREGISTERED-CREDENTIAL')
        _write(journal.run_dir/'unreferenced-report.json', _canonical({'path': str(hidden), 'url': 'https://forbidden.invalid'}))
        outside = _write(journal.run_dir.parent/'outside-secret', b'PRIVATE-OUTSIDE-CREDENTIAL')
        (journal.run_dir/'unreferenced-symlink').symlink_to(outside)
        with _export(ready) as artifacts:
            _, members = _archive(artifacts)
        combined = b''.join(members.values())
        for secret in (b'PRIVATE-ENVIRONMENT-CREDENTIAL', b'PRIVATE-UNREGISTERED-CREDENTIAL', b'PRIVATE-OUTSIDE-CREDENTIAL'):
            assert secret not in combined


def test_registered_driver_completes_only_export_and_preserves_failure_and_reservation(factory):
    with factory(expired=True) as ready:
        before = ready['journal'].state
        outcome = ready['driver'].execute(ready['context'])
        assert outcome.status == 'completed' and outcome.code == 'owned-linux-evidence-exported'
        assert outcome.assertions == () and outcome.mutation_effect == 'not_applied'
        _archive(outcome.artifacts)
        assert ready['journal'].state == before
        ready['journal'].finish_phase('export', outcome.status, outcome.code,
            receipt_artifacts=outcome.artifacts, assertions=outcome.assertions)
        after = ready['journal'].state
        assert after['lifecycle']['abort'] == before['lifecycle']['abort']
        assert after['lifecycle']['final_result'] is None and after['environment_reserved']
        assert after['lifecycle']['phase_records'][-1]['assertions'] == []


@pytest.mark.parametrize('change', ['run', 'identity', 'mode', 'phase', 'inputs', 'path', 'deadline'])
def test_scope_changes_reject_before_output(factory, change):
    with factory() as ready:
        context = ready['context']
        changes = {
            'run': {'run_id': 'foreign-run'},
            'identity': {'identities': _readonly({**dict(context.identities), 'package_sha256': '0'*64})},
            'mode': {'execution_mode': 'operator'},
            'phase': {'phase': _readonly({**dict(context.phase), 'id': 'cleanup', 'operation': 'down'})},
            'inputs': {'input_artifacts': _readonly({})},
            'path': {'run_dir': context.run_dir.parent},
            'deadline': {'deadline': time.monotonic()-1},
        }
        ready['context'] = replace(context, **changes[change])
        with pytest.raises((StateError, ValueError, TimeoutError, OSError)):
            with _export(ready):
                pytest.fail('invalid export was admitted')
        assert not list(ready['journal'].run_dir.glob('export-checkpoint-*'))


@pytest.mark.parametrize('flag', ['_lifecycle_locked', '_environment_locked'])
def test_both_current_locks_are_required(factory, monkeypatch, flag):
    with factory() as ready:
        monkeypatch.setattr(ready['journal'], flag, False)
        with pytest.raises((StateError, ValueError)):
            with _export(ready):
                pytest.fail('unlocked export was admitted')
        assert not list(ready['journal'].run_dir.glob('export-checkpoint-*'))


def test_foreign_process_journal_rejected_before_output(factory, monkeypatch):
    with factory() as ready:
        monkeypatch.setattr(ready['journal'], '_process_id', os.getpid()+1)
        with pytest.raises((StateError, ValueError)):
            with _export(ready):
                pytest.fail('foreign-process export was admitted')
        assert not list(ready['journal'].run_dir.glob('export-checkpoint-*'))


def test_pending_native_request_cannot_be_exported_or_cancelled_by_helper(factory):
    with factory(native='pending') as ready:
        before = ready['journal'].state
        with pytest.raises((StateError, ValueError)):
            with _export(ready):
                pytest.fail('pending request was admitted')
        assert ready['journal'].state == before
        assert not list(ready['journal'].run_dir.glob('export-checkpoint-*'))


@pytest.mark.parametrize('change', ['symlink', 'hardlink', 'bytes'])
def test_registered_source_substitution_is_rejected_before_terminal_marker(factory, change):
    with factory() as ready:
        journal = ready['journal']
        ref = journal.state['lifecycle']['phase_records'][0]['artifacts'][0]
        path = journal.run_dir/ref['path']
        if change == 'hardlink':
            os.link(path, journal.run_dir/'extra-link')
        elif change == 'symlink':
            old = path.with_name('retained-original')
            path.rename(old)
            path.symlink_to(old)
        else:
            path.write_bytes(b'changed source')
        with pytest.raises((StateError, ValueError, OSError)):
            with _export(ready):
                pytest.fail('changed source was admitted')
        assert not list(journal.run_dir.glob('export-checkpoint-*/checkpoint-manifest.json'))


@pytest.mark.parametrize('failure', [RuntimeError, KeyboardInterrupt, TimeoutError])
def test_caller_postcheck_failure_withdraws_owned_manifest_and_keeps_failed_parts(factory, failure):
    with factory() as ready:
        artifacts = None
        with pytest.raises(failure):
            with _export(ready) as artifacts:
                assert any(p.name == 'checkpoint-manifest.json' and p.exists() for p in artifacts)
                raise failure('controlled caller postcheck failure')
        assert artifacts is not None
        assert not next(p for p in artifacts if p.name == 'checkpoint-manifest.json').exists()
        assert all(p.exists() for p in artifacts if p.name != 'checkpoint-manifest.json')


def test_terminal_withdrawal_does_not_unlink_replaced_manifest(factory):
    with factory() as ready:
        replacement = b'foreign marker'
        with pytest.raises(RuntimeError):
            with _export(ready) as artifacts:
                marker = next(p for p in artifacts if p.name == 'checkpoint-manifest.json')
                with marker.open('rb'):
                    marker.unlink()
                    marker.write_bytes(replacement)
                    raise RuntimeError('controlled postcheck failure')
        assert marker.read_bytes() == replacement


@pytest.mark.parametrize('change', ['source', 'part', 'journal', 'output-name', 'run-route'])
def test_final_audit_rejects_source_output_or_route_changes_and_withdraws_marker(factory, change):
    with factory() as ready:
        journal = ready['journal']
        before = journal.state
        moved = None
        with pytest.raises((StateError, ValueError, OSError)):
            with _export(ready) as artifacts:
                marker = next(p for p in artifacts if p.name == 'checkpoint-manifest.json')
                if change == 'source':
                    ref = before['lifecycle']['phase_records'][0]['artifacts'][0]
                    (journal.run_dir/ref['path']).write_bytes(b'source changed after export')
                elif change == 'part':
                    part = next(p for p in artifacts if p != marker)
                    part.chmod(0o600)
                    part.write_bytes(b'changed part')
                elif change == 'journal':
                    journal.finish_phase('export', 'error', 'controlled-concurrent-finish',
                                         receipt_artifacts=[ready['evidence']])
                elif change == 'output-name':
                    moved = marker.parent.with_name('moved-export-directory')
                    marker.parent.rename(moved)
                    marker.parent.mkdir(mode=0o700)
                else:
                    moved = journal.run_dir.with_name('moved-run-directory')
                    journal.run_dir.rename(moved)
                    journal.run_dir.mkdir(mode=0o700)
        if change == 'output-name':
            assert not list(marker.parent.iterdir())
            assert not (moved/'checkpoint-manifest.json').exists()
        elif change == 'run-route':
            assert not list(journal.run_dir.iterdir())
            assert not (moved/marker.parent.name/'checkpoint-manifest.json').exists()
        else:
            assert not marker.exists()


def test_deadline_expiring_after_yield_withdraws_terminal_marker(factory, monkeypatch):
    with factory() as ready:
        with pytest.raises((TimeoutError, ValueError)):
            with _export(ready) as artifacts:
                monkeypatch.setattr(export.time, 'monotonic', lambda: ready['context'].deadline+1)
        assert not next(p for p in artifacts if p.name == 'checkpoint-manifest.json').exists()


def test_bound_parts_reconstruct_deterministically_and_fit_phase_freeze(factory, monkeypatch):
    with factory() as ready:
        with _export(ready) as original:
            full, _ = _archive(original)
        part_size = (full['archive']['bytes']+2)//3
        monkeypatch.setattr(export, 'PART_BYTES', part_size)
        with _export(ready) as artifacts:
            first, members = _archive(artifacts)
        assert 1 < len(first['archive']['parts']) <= 8
        assert all(p['bytes'] <= part_size for p in first['archive']['parts'])
        with _export(ready) as again:
            second, again_members = _archive(again)
        assert members == again_members
        assert [(p['sha256'], p['bytes']) for p in first['archive']['parts']] == [
            (p['sha256'], p['bytes']) for p in second['archive']['parts']]


@pytest.mark.parametrize('constant,value', [('MAX_REFERENCE_OCCURRENCES', 1), ('MAX_EXPORT_BYTES', 1000),
    ('MAX_METADATA_BYTES', 100), ('MAX_ARTIFACT_BYTES', 1), ('MAX_JOURNAL_BYTES', 1), ('MAX_EVENTS', 1)])
def test_membership_and_aggregate_limits_fail_without_complete_marker(factory, monkeypatch, constant, value):
    monkeypatch.setattr(export, constant, value)
    with factory() as ready:
        with pytest.raises((StateError, ValueError, OSError)):
            with _export(ready):
                pytest.fail('oversize checkpoint was admitted')
        assert not list(ready['journal'].run_dir.glob('export-checkpoint-*/checkpoint-manifest.json'))


def test_missing_export_phase_rejects_without_manufacturing_progress(factory):
    with factory(active=False) as ready:
        before = ready['journal'].state
        with pytest.raises((StateError, ValueError)):
            with _export(ready):
                pytest.fail('inactive export was admitted')
        assert ready['journal'].state == before


def test_closed_reference_selector_covers_every_artifact_slot_in_current_schema():
    schema_path = SCENARIO_ROOT.parents[3]/'schemas/factory-state.schema.json'
    schema = json.loads(schema_path.read_bytes())

    def slots(node, path=()):
        properties = node.get('properties', {})
        if {'path', 'sha256', 'bytes'} <= properties.keys():
            yield '.'.join(path)
        for key, child in properties.items():
            yield from slots(child, (*path, key))
        if isinstance(node.get('items'), dict):
            yield from slots(node['items'], (*path, '[]'))
        for branch in ('allOf', 'anyOf', 'oneOf'):
            for child in node.get(branch, []):
                yield from slots(child, path)

    denied_active = {'active_mutation.intent_artifacts.[]', 'active_mutation.receipt_artifacts.[]',
                     'active_native.request', 'active_native.input_artifacts.[]'}
    assert set(slots(schema)) == set(export.REFERENCE_SLOTS) | denied_active


@pytest.mark.parametrize('method', ['_package', '_admit'])
@pytest.mark.parametrize('failure', [StateError, KeyboardInterrupt])
def test_registered_driver_postchecks_withdraw_manifest_after_copy(factory, monkeypatch, method, failure):
    with factory() as ready:
        driver = ready['driver']
        original = getattr(driver, method)
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                markers = list(ready['journal'].run_dir.glob('export-checkpoint-*/checkpoint-manifest.json'))
                assert len(markers) == 1
                raise failure('controlled registered postcheck failure')
            return original(*args, **kwargs)

        monkeypatch.setattr(driver, method, fail_second)
        with pytest.raises(failure):
            driver.execute(ready['context'])
        assert calls == 2
        outputs = list(ready['journal'].run_dir.glob('export-checkpoint-*'))
        assert len(outputs) == 1 and list(outputs[0].glob('*.zip.part'))
        assert not (outputs[0]/'checkpoint-manifest.json').exists()


def test_snapshot_must_end_at_export_phase_start_event(factory):
    with factory(normal=True) as ready:
        journal = ready['journal']
        assert journal.state['lifecycle']['abort'] is None
        journal.abort_lifecycle('export', 'error', 'controlled-post-start-abort')
        before = journal.state
        with pytest.raises((StateError, ValueError)):
            with _export(ready):
                pytest.fail('advanced journal was admitted as export-start checkpoint')
        assert journal.state == before
        assert not list(journal.run_dir.glob('export-checkpoint-*'))


def test_caller_deadline_cannot_widen_expired_phase_budget(factory):
    with factory() as ready:
        context = replace(ready['context'], deadline=time.monotonic()-1)
        with pytest.raises(TimeoutError):
            with export.export_checkpoint(ready['journal'], context, deadline=time.monotonic()+30):
                pytest.fail('expired phase received a wider caller budget')
        assert not list(ready['journal'].run_dir.glob('export-checkpoint-*'))


@pytest.mark.parametrize('target', ['manifest', 'part', 'directory'])
def test_output_must_remain_private_through_final_audit(factory, target):
    with factory() as ready:
        with pytest.raises((StateError, ValueError)):
            with _export(ready) as artifacts:
                marker = next(p for p in artifacts if p.name == 'checkpoint-manifest.json')
                path = marker if target == 'manifest' else marker.parent if target == 'directory' else artifacts[0]
                path.chmod(0o755 if target == 'directory' else 0o644)
        assert not marker.exists()


@pytest.mark.parametrize('engine,secondary_close_error', [(False, False), (False, True), (True, True)])
def test_midstream_interrupt_stops_all_zip_bytes_and_preserves_primary_interruption(
        factory, monkeypatch, engine, secondary_close_error):
    with factory(engine=engine) as ready:
        output_inodes, after_fault = set(), []
        fault = False
        original_create = export._Output.create
        original_write = os.write
        original_regular = export._regular
        original_close = zipfile.ZipFile.close
        primary = KeyboardInterrupt('controlled source-stream interruption')
        secondary_seen = []

        def create(output, name):
            fd = original_create(output, name)
            info = os.fstat(fd)
            output_inodes.add((info.st_dev, info.st_ino))
            return fd

        def write(fd, raw):
            info = os.fstat(fd)
            output = (info.st_dev, info.st_ino) in output_inodes
            written = original_write(fd, raw)
            if fault and output:
                after_fault.append(written)
            return written

        def regular(root, path, reference, deadline, sink=None):
            if sink is None or fault:
                return original_regular(root, path, reference, deadline, sink)

            def interrupt_after_chunk(raw):
                nonlocal fault
                sink(raw)
                fault = True
                raise primary

            return original_regular(root, path, reference, deadline, interrupt_after_chunk)

        def close(archive):
            active = archive.fp is not None
            try:
                return original_close(archive)
            finally:
                if fault and secondary_close_error and active:
                    secondary_seen.append(True)
                    raise OSError('controlled secondary archive-close failure')

        monkeypatch.setattr(export._Output, 'create', create)
        monkeypatch.setattr(export.os, 'write', write)
        monkeypatch.setattr(export, '_regular', regular)
        monkeypatch.setattr(zipfile.ZipFile, 'close', close)
        before_abort = ready['journal'].state['lifecycle']['abort']
        if engine:
            assert ready['engine']._phase(ready['context'].phase) is False
            after = ready['journal'].state
            record = after['lifecycle']['phase_records'][-1]
            assert record['phase_id'] == 'export'
            assert record['outcome'] == 'interrupted' and record['code'] == 'phase-interrupted'
            assert after['lifecycle']['abort'] == before_abort
            assert after['lifecycle']['final_result'] is None and after['environment_reserved']
        else:
            with pytest.raises(KeyboardInterrupt) as error:
                with _export(ready):
                    pytest.fail('interrupted stream returned completed artifacts')
            assert error.value is primary
        assert fault and output_inodes and after_fault == []
        assert bool(secondary_seen) == secondary_close_error
        outputs = list(ready['journal'].run_dir.glob('export-checkpoint-*'))
        assert len(outputs) == 1 and list(outputs[0].glob('*.zip.part'))
        assert not (outputs[0]/'checkpoint-manifest.json').exists()


@pytest.mark.parametrize('cleanup', ['withdraw-fsync', 'parts-close'])
def test_cleanup_failure_does_not_mask_primary_caller_interruption(factory, monkeypatch, cleanup):
    with factory() as ready:
        primary = KeyboardInterrupt('controlled caller interruption')
        with monkeypatch.context() as local:
            with pytest.raises(KeyboardInterrupt) as error:
                with _export(ready) as artifacts:
                    marker = next(p for p in artifacts if p.name == 'checkpoint-manifest.json')
                    if cleanup == 'withdraw-fsync':
                        def fail_fsync(_):
                            raise OSError('PRIVATE-CLEANUP-ERROR-CANARY')
                        local.setattr(export.os, 'fsync', fail_fsync)
                    else:
                        original = export._Parts.close

                        def fail_close(parts):
                            original(parts)
                            raise OSError('PRIVATE-CLEANUP-ERROR-CANARY')
                        local.setattr(export._Parts, 'close', fail_close)
                    raise primary
            assert error.value is primary
            assert 'OSError' in ' '.join(getattr(primary, '__notes__', []))
            assert 'PRIVATE-CLEANUP-ERROR-CANARY' not in ' '.join(getattr(primary, '__notes__', []))
            assert not marker.exists()


def test_source_descriptor_close_failure_preserves_copy_interruption_and_closes_all_ancestors(tmp_path, monkeypatch):
    source = tmp_path/'first'/'second'/'evidence'
    source.parent.mkdir(parents=True, mode=0o700)
    raw = b'bounded source stream\n'
    _write(source, raw)
    deadline = time.monotonic()+30
    root = export._Route(tmp_path.resolve(), deadline)
    opened, attempted, copied = [], [], []
    primary = KeyboardInterrupt('controlled primary copy interruption')
    original_open, original_close = os.open, os.close
    file_fd = None

    def record_open(path, flags, *args, **kwargs):
        nonlocal file_fd
        fd = original_open(path, flags, *args, **kwargs)
        opened.append(fd)
        if path == 'evidence':
            file_fd = fd
        return fd

    def close_then_fail(fd):
        attempted.append(fd)
        original_close(fd)
        if fd == file_fd:
            raise OSError('PRIVATE-SOURCE-CLOSE-CANARY')

    def interrupted_sink(chunk):
        copied.append(chunk)
        raise primary

    try:
        with monkeypatch.context() as local:
            local.setattr(export.os, 'open', record_open)
            local.setattr(export.os, 'close', close_then_fail)
            with pytest.raises(KeyboardInterrupt) as error:
                export._regular(root, 'first/second/evidence',
                    {'bytes': len(raw), 'sha256': _sha(raw)}, deadline, interrupted_sink)
            assert error.value is primary
        assert copied == [raw]
        assert len(opened) == 3 and attempted == list(reversed(opened))
        assert len(attempted) == len(set(attempted))
        notes = ' '.join(getattr(primary, '__notes__', []))
        assert 'OSError' in notes and 'PRIVATE-SOURCE-CLOSE-CANARY' not in notes
        for fd in opened:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        root.close()


def test_route_close_attempts_all_owned_fds_and_is_not_repeated_after_failure(tmp_path, monkeypatch):
    route = export._Route(tmp_path.resolve(), time.monotonic()+30)
    owned = [fd for _, fd in reversed(route.nodes)]
    attempted = []
    failure = OSError('controlled first route close failure')
    original_close = os.close

    def close_then_fail(fd):
        attempted.append(fd)
        original_close(fd)
        if fd == owned[0]:
            raise failure

    with monkeypatch.context() as local:
        local.setattr(export.os, 'close', close_then_fail)
        with pytest.raises(OSError) as error:
            route.close()
        assert error.value is failure
        assert attempted == owned
        assert route.nodes == []
        route.close()
        assert attempted == owned
    for fd in owned:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_output_close_attempts_remaining_fds_and_clears_both_ownership_fields(tmp_path, monkeypatch):
    deadline = time.monotonic()+30
    root = export._Route(tmp_path.resolve(), deadline)
    output = export._Output(root, deadline)
    writer = output.create('checkpoint-manifest.json')
    os.close(writer)
    owned = [output.manifest_fd, output.fd]
    attempted = []
    failure = OSError('controlled first output close failure')
    original_close = os.close

    def close_then_fail(fd):
        attempted.append(fd)
        original_close(fd)
        if fd == owned[0]:
            raise failure

    try:
        with monkeypatch.context() as local:
            local.setattr(export.os, 'close', close_then_fail)
            with pytest.raises(OSError) as error:
                output.close()
            assert error.value is failure
            assert attempted == owned
            assert output.manifest_fd is None and output.fd is None
            output.close()
            assert attempted == owned
        for fd in owned:
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        root.close()


def test_output_readback_close_failure_preserves_read_interruption(tmp_path, monkeypatch):
    deadline = time.monotonic()+30
    root = export._Route(tmp_path.resolve(), deadline)
    output = export._Output(root, deadline)
    raw = b'private retained part\n'
    writer = output.create('retained.part')
    os.write(writer, raw)
    os.close(writer)
    original_open, original_read, original_close = os.open, os.read, os.close
    reader_fd = None
    attempted, read_chunks = [], []
    primary = KeyboardInterrupt('controlled primary readback interruption')

    def record_open(path, flags, *args, **kwargs):
        nonlocal reader_fd
        fd = original_open(path, flags, *args, **kwargs)
        if path == 'retained.part':
            reader_fd = fd
        return fd

    def interrupt_read(fd, count):
        chunk = original_read(fd, count)
        if fd == reader_fd:
            read_chunks.append(chunk)
            raise primary
        return chunk

    def close_then_fail(fd):
        attempted.append(fd)
        original_close(fd)
        if fd == reader_fd:
            raise OSError('PRIVATE-READBACK-CLOSE-CANARY')

    try:
        with monkeypatch.context() as local:
            local.setattr(export.os, 'open', record_open)
            local.setattr(export.os, 'read', interrupt_read)
            local.setattr(export.os, 'close', close_then_fail)
            with pytest.raises(KeyboardInterrupt) as error:
                output.check_files([{'path': 'retained.part', 'bytes': len(raw), 'sha256': _sha(raw)}])
            assert error.value is primary
        assert read_chunks == [raw] and attempted == [reader_fd]
        notes = ' '.join(getattr(primary, '__notes__', []))
        assert 'OSError' in notes and 'PRIVATE-READBACK-CLOSE-CANARY' not in notes
        with pytest.raises(OSError):
            os.fstat(reader_fd)
        output.audit()
    finally:
        output.close()
        root.close()
