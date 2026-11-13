"""Real private journals/package bytes; controlled transport, no live guest claim."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

import pytest

from devsecops.factory import owned_foundation as foundation, tcg
from devsecops.factory.contracts import validate_document
from devsecops.factory.lifecycle import PhaseContext, SCENARIO_ROOT, _readonly
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha
from tests.test_factory_contracts import config, factory_lock


@pytest.fixture
def setup(tmp_path, monkeypatch):
    methods = {name: getattr(tcg.RetainedTCGDriver, name) for name in ('up', 'down', 'reconcile')}
    home = tmp_path/'codex'
    package = home/'plugins/cache/devsecops-local/devsecops/0.1.0'
    root = foundation.PACKAGE_ROOT
    names = ['src/devsecops/factory/owned_foundation.py', 'src/devsecops/factory/tcg.py',
        'src/devsecops/factory/service_inventory.py',
        'demo/software-factory/provision/tcg/rke2-release.json',
        *['demo/software-factory/scenarios/ironbank-mission-recovery-v1/'+name+'.json'
          for name in ('scenario', 'assertions')]]
    files = {name: (root/name).read_bytes() for name in names}
    files['.codex-plugin/plugin.json'] = _canonical({'name': 'devsecops', 'version': '0.1.0'})
    for name, raw in files.items():
        path = package/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    manifest = ''.join(f'{_sha(raw)}  {name}\n' for name, raw in sorted(files.items())).encode()
    (package/'SHA256SUMS').write_bytes(manifest)
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setattr(foundation, 'PACKAGE_ROOT', package)
    monkeypatch.setattr(foundation, '__file__', str(package/'src/devsecops/factory/owned_foundation.py'))
    monkeypatch.setattr(tcg, '__file__', str(package/'src/devsecops/factory/tcg.py'))
    monkeypatch.setattr(foundation.service_inventory, '__file__',
                        str(package/'src/devsecops/factory/service_inventory.py'))
    selected = config()
    selected['expires_at'] = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    selected['allowed_mutations'] = ['provision', 'cleanup']
    transport = {'identity_agent': str(tmp_path/'agent'), 'host_key_sha256': '5'*64,
                 'verification_basis': 'operator-verified'}
    for name in ('config', 'known_hosts', 'certificate', 'identity', 'proxy'):
        path = tmp_path/('transport-'+name)
        path.write_bytes(('controlled-'+name).encode())
        transport[name] = {'path': str(path), 'sha256': _sha(path.read_bytes())}
    selected['runtime']['foundation'] = chosen = {
        'kind': 'retained-tcg-v1', 'host': 'controlled-host',
        'remote_lab': '/home/operator/.local/share/devsecops/labs/controlled',
        'private_state_directory': str(tmp_path/'private'), 'tool_image': 'sha256:'+'a'*64,
        'provision_run_id': 'original-controlled', 'original_ca_sha256': 'b'*64,
        'source_receipt': {'path': str(tmp_path/'original.json'), 'sha256': '0'*64},
        'transport': transport, 'retained_dependencies': 'preserve'}
    pins_raw = files['demo/software-factory/provision/tcg/rke2-release.json']
    original = {'schema_version': 1, 'kind': 'tcg-rke2-readiness-screen',
        'run_id': chosen['provision_run_id'], 'private_lab_path': chosen['remote_lab'],
        'status': 'rke2_readiness_pass', 'live_platform_qualification': 'rke2_api_and_node_only',
        'guest': {'vcpus': 8, 'memory_bytes': 32*1024**3, 'disk_virtual_bytes': 160*1024**3,
                  'accelerator': 'tcg', 'architecture': 'x86_64'},
        'toolchain': {'image_id': chosen['tool_image']},
        'rke2': {'status': 'api_and_node_ready', 'guest_local_artifacts_verified': True,
            'consecutive_ready_observations': 3,
            'observations': [{'readyz': 'ok', 'server_version': tcg.VERSION,
                'nodes': [{'ready': True, 'kubelet_version': tcg.VERSION}]} for _ in range(3)],
            'credentials': {'api_ca_sha256': chosen['original_ca_sha256'],
                'directory': chosen['remote_lab']+'/operator', 'status': 'issued_by_observed_guest'},
            'api_read_smoke': {'kind': 'APIVersions', 'versions': ['v1']}},
        'cleanup': {'containers_and_network_removed': True, 'remaining_runtime_resources': [],
            'preserved_private_lab_path': chosen['remote_lab'], 'preserved_tool_image_id': chosen['tool_image'],
            'preserved_files': ['probe.qcow2', 'seed.iso', 'ssh/operator', 'ssh/host', 'operator']},
        'rke2_provenance': {'pins': json.loads(pins_raw), 'pins_sha256': _sha(pins_raw),
            'all_asset_hashes_verified': True, 'release_checksums_verified': True}}
    lock = factory_lock()
    lock['platform'].update(distribution='rke2', distribution_version=tcg.VERSION,
        kubernetes_version=tcg.VERSION, cni='canal')
    lock.update(**{name+'_sha256': _sha(files['demo/software-factory/scenarios/ironbank-mission-recovery-v1/'+name+'.json'])
                   for name in ('scenario', 'assertions')})
    values = {'scenario': json.loads((SCENARIO_ROOT/'scenario.json').read_bytes()),
        'assertions': json.loads((SCENARIO_ROOT/'assertions.json').read_bytes()), 'config': selected, 'lock': lock,
        'environment': {'schema_version': 1, 'environment_id': selected['environment'], 'services': [
            {'id': row['id'], 'disposition': 'retained-foundation' if row['id'] == 'kubernetes'
             else 'external-shared' if row['id'] == 'registry1' else 'planned-owned'} for row in selected['services']]}}
    calls = []
    changes = {}
    def observed(driver, action, operation, deadline):
        calls.append((action, operation, deadline))
        ready = {'ready': True, 'fingerprint': {'ca_sha256': chosen['original_ca_sha256'],
                 'namespaces': {'default': 'uid-default', 'kube-system': 'uid-system'}, 'nodes': {'node': 'uid-node'}}}
        report = {'schema_version': 1, 'kind': 'retained-tcg-observation', 'action': action,
            'run_id': driver.spec.run_id, 'operation_id': operation, 'identities': driver.spec.identities,
            'binding_sha256': driver.spec.digest, 'driver_sha256': driver.source_sha256,
            'state': 'absent' if action == 'down' else 'running_ready', 'mutation_effect': 'applied',
            'canonical_platform_qualified': False, 'api_observations': [ready]*3, 'api_observation': ready,
            'remote': {'state': 'absent' if action == 'down' else 'running_unready',
                'container_id': None if action == 'down' else 'c'*64,
                'network_id': None if action == 'down' else 'd'*64,
                'disk_identity': {'device': 1, 'inode': 10}, 'remaining_owned_runtime': action != 'down'},
            'tunnel_cleanup': {'owned_tunnel_absent': action == 'down'}}
        report.update(deepcopy(changes))
        path = driver.directory/'receipts'/f'{operation}-{action}.json'
        tcg._write(path, _canonical(report))
        return tcg.TCGObservation(report['state'], path, _sha(path.read_bytes()), report['mutation_effect'])
    for name in ('up', 'down', 'reconcile'):
        monkeypatch.setattr(tcg.RetainedTCGDriver, name,
            lambda driver, operation, *, deadline, action=name: observed(driver, action, operation, deadline))
    def start():
        raw_original = _canonical(original)
        Path(chosen['source_receipt']['path']).write_bytes(raw_original)
        chosen['source_receipt']['sha256'] = _sha(raw_original)
        raw = {name: _canonical(value) for name, value in values.items()}
        for name in ('scenario', 'assertions'):
            raw[name] = files['demo/software-factory/scenarios/ironbank-mission-recovery-v1/'+name+'.json']
        raw['package'] = manifest
        identities = {name+'_sha256': _sha(value) for name, value in raw.items()}
        journal = RunJournal.create(tmp_path/'run', identities=identities, environment_id=selected['environment'])
        journal.initialize_lifecycle(inputs=raw, execution_mode='controlled-test', driver_id='owned-linux')
        return journal, foundation.OwnedRKE2Foundation(journal, plugin_id='devsecops@devsecops-local', plugin_version='0.1.0')
    return {'tmp': tmp_path, 'values': values, 'chosen': chosen, 'original': original,
            'start': start, 'calls': calls, 'changes': changes, 'package': package, 'methods': methods}


def context(journal, values, action):
    phase = next(row for row in values['scenario']['phases'] if row['operation'] == action)
    return PhaseContext(journal.state['run_id'], _readonly(phase), _readonly(journal.identities),
        _readonly(values), _readonly(journal.state['lifecycle']['inputs']), journal.run_dir,
        time.monotonic()+30, 'controlled-test')


@contextmanager
def invocation(setup, action='up'):
    journal, driver = setup['start']()
    proof = setup['tmp']/'fixture-evidence.json'
    proof.write_text('{"controlled_test":true}')
    with journal.lifecycle_lock(), journal.mutation_lock(setup['tmp']/'locks'):
        journal.begin_phase('preflight')
        if action == 'down':
            journal.abort_lifecycle('preflight', 'incomplete', 'controlled-stop')
            journal.finish_phase('preflight', 'incomplete', 'controlled-stop', receipt_artifacts=[proof])
            journal.begin_phase('export')
            journal.finish_phase('export', 'completed', 'controlled-only', receipt_artifacts=[proof])
        else:
            journal.finish_phase('preflight', 'completed', 'controlled-only', receipt_artifacts=[proof])
            journal.begin_phase('prepare')
            journal.finish_phase('prepare', 'completed', 'controlled-only', receipt_artifacts=[proof])
        ctx = context(journal, setup['values'], action)
        journal.begin_phase(ctx.phase['id'])
        operation = journal.begin_mutation(ctx.phase['id'], action, intent_artifacts=[proof])
        yield journal, driver, ctx, operation


def test_actual_journal_operation_is_passed_to_fixed_driver_and_retained_inventory_is_explicit(setup):
    with invocation(setup) as (journal, driver, ctx, operation):
        outcome = driver.up(ctx)
        report = json.loads(outcome.artifacts[0].read_bytes())
        assert setup['calls'][0][:2] == ('up', operation)
        assert outcome.status == 'incomplete' and outcome.mutation_effect == 'applied' and not outcome.assertions
        assert report['foundation_readiness'] == 'retained-rke2-ready'
        assert not report['canonical_success'] and not report['complete_cleanup']
        assert report['execution_mode'] == 'controlled-test'
        assert report['original_provenance']['authority'] == 'operator-selected-historical-provenance'
        resources = {row['kind']: row for row in report['resources']}
        assert resources['retained-disk']['identity'] == {'device': 1, 'inode': 10}
        assert resources['retained-disk']['disposition'] == 'preserve'
        assert resources['container']['identity'] == 'c'*64
        assert all(row['ownership'] == 'unverified' and row['bootstrap'] == 'not-performed' for row in report['services'])
        assert journal.state['active_mutation']['operation_id'] == operation
        assert journal.state['environment_reserved']


def test_cleanup_after_window_closes_keeps_all_retained_dependencies_and_cannot_release(setup):
    setup['values']['config']['expires_at'] = '2020-01-01T00:00:00Z'
    with invocation(setup, 'down') as (journal, driver, ctx, _):
        result = driver.down(ctx)
        report = json.loads(result.artifacts[0].read_bytes())
        assert report['runtime_cleanup'] == 'observed-absent' and not report['complete_cleanup']
        assert result.status == 'incomplete'
        with pytest.raises(StateError, match='cannot release'):
            driver.validate_cleanup(tuple(path.read_bytes() for path in result.artifacts))
        assert journal.state['environment_reserved']


def test_fixed_tcg_state_machine_records_intent_readiness_and_tunnel_identity(setup, monkeypatch):
    """Exercise the real retained driver; replace only external transport boundaries."""
    for name, method in setup['methods'].items():
        monkeypatch.setattr(tcg.RetainedTCGDriver, name, method)
    def remote(driver, action, operation, deadline, *args):
        assert json.loads((driver.directory/'active.json').read_bytes()) == {
            'action': action, 'operation_id': operation}
        return {'state': 'running_unready', 'address': '172.18.0.2',
            'container_id': 'c'*64, 'network_id': 'd'*64, 'disk_identity': {'device': 1, 'inode': 10}}
    monkeypatch.setattr(tcg.RetainedTCGDriver, '_remote', remote)
    monkeypatch.setattr(tcg.RetainedTCGDriver, '_credentials', lambda *args: None)
    monkeypatch.setattr(tcg.RetainedTCGDriver, '_tunnel', lambda driver, address, operation, deadline: {
        'pid': 12345, 'process_sha256': 'e'*64, 'control': str(driver.directory/'ctl-fixture'),
        'address': address, 'port': 12345, 'operation_id': operation})
    monkeypatch.setattr(tcg.RetainedTCGDriver, '_api', lambda driver, port, deadline: {
        'ready': True, 'fingerprint': {'ca_sha256': driver.spec.original_ca_sha256,
            'nodes': {'node': 'node-uid'}, 'namespaces': {'default': 'default-uid', 'kube-system': 'system-uid'}}})
    monkeypatch.setattr(tcg.time, 'sleep', lambda seconds: None)
    with invocation(setup) as (_, driver, ctx, operation):
        result = driver.up(ctx)
        report = json.loads(result.artifacts[0].read_bytes())
        private = Path(setup['chosen']['private_state_directory'])
        assert report['foundation_readiness'] == 'retained-rke2-ready'
        assert not (private/'active.json').exists()
        assert (private/'cluster-fingerprint.json').exists()
        tunnel = next(row for row in report['resources'] if row['kind'] == 'tunnel')
        assert tunnel['identity']['operation_id'] == operation
        assert tunnel['identity']['pid'] == 12345


def test_reopened_journal_can_only_reconcile_the_existing_operation(setup):
    with invocation(setup) as (journal, _, ctx, operation):
        pass
    reopened = RunJournal.open(journal.run_dir, identities=journal.identities)
    driver = foundation.OwnedRKE2Foundation(reopened, plugin_id='devsecops@devsecops-local', plugin_version='0.1.0')
    with reopened.lifecycle_lock(), reopened.mutation_lock(setup['tmp']/'locks'):
        with pytest.raises(StateError, match='reconciliation'):
            driver.up(ctx)
        result = driver.reconcile(ctx, operation)
        assert result.status == 'incomplete'
        assert setup['calls'][0][:2] == ('reconcile', operation)
        assert reopened.state['active_mutation']['operation_id'] == operation


@pytest.mark.parametrize('change', ['receipt-pin', 'transport-pin', 'package', 'context-input', 'context-run', 'context-mode'])
def test_identity_changes_prevent_all_driver_calls(setup, change):
    with invocation(setup) as (_, driver, ctx, _):
        if change == 'receipt-pin':
            Path(setup['chosen']['source_receipt']['path']).write_text('{}')
        elif change == 'transport-pin':
            Path(setup['chosen']['transport']['config']['path']).write_text('changed')
        elif change == 'package':
            (setup['package']/'.codex-plugin/plugin.json').write_text('{}')
        elif change == 'context-input':
            ctx = replace(ctx, identities=_readonly({**dict(ctx.identities), 'config_sha256': 'f'*64}))
        elif change == 'context-run':
            ctx = replace(ctx, run_id='other')
        else:
            ctx = replace(ctx, execution_mode='operator')
        with pytest.raises((StateError, ValueError)):
            driver.up(ctx)
    assert setup['calls'] == []


@pytest.mark.parametrize('change', ['ca', 'run', 'tool', 'path', 'cleanup', 'ready', 'pins', 'kind'])
def test_exact_original_receipt_semantics_are_checked_even_with_correct_file_pin(setup, change):
    original = setup['original']
    if change == 'ca':
        original['rke2']['credentials']['api_ca_sha256'] = 'f'*64
    elif change == 'run':
        original['run_id'] = 'other'
    elif change == 'tool':
        original['toolchain']['image_id'] = 'sha256:'+'f'*64
    elif change == 'path':
        original['private_lab_path'] += '-other'
    elif change == 'cleanup':
        original['cleanup']['remaining_runtime_resources'] = ['foreign']
    elif change == 'ready':
        original['rke2']['observations'][-1]['nodes'][0]['ready'] = False
    elif change == 'pins':
        original['rke2_provenance']['pins']['assets'][0]['sha256'] = 'f'*64
    else:
        original['kind'] = 'tcg-linux-boot-probe'
    with invocation(setup) as (_, driver, ctx, _), pytest.raises(StateError, match='Original retained'):
        driver.up(ctx)
    assert not setup['calls']


@pytest.mark.parametrize('change', ['omit', 'extra', 'duplicate', 'registry1-owned', 'kubernetes-shared'])
def test_service_inventory_cannot_omit_or_reclassify_retained_and_publisher_resources(setup, change):
    rows = setup['values']['environment']['services']
    if change == 'omit':
        rows.pop()
    elif change == 'extra':
        setup['values']['environment']['approval'] = 'claimed'
    elif change == 'duplicate':
        rows.append(deepcopy(rows[0]))
    elif change == 'registry1-owned':
        next(row for row in rows if row['id'] == 'registry1')['disposition'] = 'planned-owned'
    else:
        next(row for row in rows if row['id'] == 'kubernetes')['disposition'] = 'external-shared'
    with invocation(setup) as (_, driver, ctx, _), pytest.raises(StateError):
        driver.up(ctx)
    assert not setup['calls']


@pytest.mark.parametrize('change', ['loader', 'content'])
def test_inventory_implementation_must_belong_to_installed_package_before_dispatch(setup, monkeypatch, change):
    selected = setup['package']/'src/devsecops/factory/service_inventory.py'
    if change == 'loader':
        outside = setup['tmp']/'same-inventory-bytes.py'
        outside.write_bytes(selected.read_bytes())
        monkeypatch.setattr(foundation.service_inventory, '__file__', str(outside))
    else:
        selected.write_bytes(selected.read_bytes()+b'\n# changed\n')
    with invocation(setup) as (_, driver, ctx, _), pytest.raises(StateError):
        driver.up(ctx)
    assert not setup['calls']


@pytest.mark.parametrize('change', ['expired', 'no-provision', 'no-cleanup', 'wrong-version', 'inside-journal'])
def test_unsupported_scope_window_recipe_and_state_root_stop_before_dispatch(setup, change):
    config = setup['values']['config']
    if change == 'expired':
        config['expires_at'] = '2020-01-01T00:00:00Z'
    elif change.startswith('no-'):
        config['allowed_mutations'].remove(change[3:])
    elif change == 'wrong-version':
        setup['values']['lock']['platform']['kubernetes_version'] = 'v1.34.0'
    else:
        setup['chosen']['private_state_directory'] = str(setup['tmp']/'run/private')
    with invocation(setup) as (_, driver, ctx, _), pytest.raises(StateError):
        driver.up(ctx)
    assert not setup['calls']


@pytest.mark.parametrize('location', ['child', 'ancestor', 'case-alias'])
def test_private_state_cannot_write_into_or_above_installed_package(setup, location):
    package = setup['package']
    candidate = package/'forbidden-state'
    if location == 'ancestor':
        candidate = package.parent
    elif location == 'case-alias':
        alias = package.parent.with_name(package.parent.name.upper())/package.name
        if not alias.exists() or alias.stat().st_ino != package.stat().st_ino:
            pytest.skip('Case aliases are unavailable on this filesystem')
        candidate = alias/'forbidden-state'
    setup['chosen']['private_state_directory'] = str(candidate)
    before = {str(p.relative_to(package)): p.read_bytes() for p in package.rglob('*') if p.is_file()}
    with invocation(setup) as (_, driver, ctx, _), pytest.raises(StateError, match='installed package'):
        driver.up(ctx)
    assert not setup['calls'] and not (package/'forbidden-state').exists()
    assert before == {str(p.relative_to(package)): p.read_bytes() for p in package.rglob('*') if p.is_file()}


def test_ambiguous_effect_cannot_be_retried_and_reconciliation_uses_original_operation(setup):
    setup['changes'].update(state='reconciliation_required', mutation_effect='unknown')
    with invocation(setup) as (journal, driver, ctx, operation):
        result = driver.up(ctx)
        journal.reconcile_mutation(operation, 'unknown', receipt_artifacts=result.artifacts)
        with pytest.raises(StateError, match='requires reconciliation'):
            driver.up(ctx)
        with pytest.raises(StateError, match='does not match'):
            driver.reconcile(ctx, 'other')
        report = driver.reconcile(ctx, operation)
        assert report.mutation_effect == 'unknown'
        assert [row[:2] for row in setup['calls']] == [('up', operation), ('reconcile', operation)]
        assert journal.state['execution_status'] == 'reconciliation_required'


@pytest.mark.parametrize('change', ['foreign-run', 'foreign-operation', 'foreign-binding', 'bad-resource', 'missing-disk'])
def test_mismatched_direct_observation_never_yields_usable_summary(setup, change):
    changes = setup['changes']
    if change == 'foreign-run':
        changes['run_id'] = 'other'
    elif change == 'foreign-operation':
        changes['operation_id'] = 'other'
    elif change == 'foreign-binding':
        changes['binding_sha256'] = 'f'*64
    else:
        changes['remote'] = {'container_id': 'invalid' if change == 'bad-resource' else 'c'*64}
    with invocation(setup) as (journal, driver, ctx, _), pytest.raises(StateError):
        driver.up(ctx)
    assert not list(journal.run_dir.glob('foundation-*/foundation.json'))


def test_wall_clock_advance_at_terminal_write_withdraws_summary_and_keeps_pending_effect(setup, monkeypatch):
    clock = [datetime.now(timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr(foundation, 'datetime', Clock)
    write = foundation._write
    def terminal(directory, name, value):
        result = write(directory, name, value)
        if name == 'foundation.json':
            clock[0] += timedelta(hours=2)
        return result
    monkeypatch.setattr(foundation, '_write', terminal)
    with invocation(setup) as (journal, driver, ctx, _), pytest.raises(StateError, match='window expired'):
        driver.up(ctx)
    assert len(setup['calls']) == 1
    assert not list(journal.run_dir.glob('foundation-*/foundation.json'))
    assert list(journal.run_dir.glob('foundation-*/tcg-observation.json'))
    assert journal.state['active_mutation'] is not None


def test_current_mutation_requires_owned_object_lock_phase_and_pid(setup, monkeypatch):
    with invocation(setup) as (journal, _, _, operation):
        assert journal.current_mutation('provision', 'up') == operation
        with pytest.raises(StateError):
            journal.current_mutation('cleanup', 'down')
        journal._owned_operations.clear()
        with pytest.raises(StateError, match='reconciliation'):
            journal.current_mutation('provision', 'up')
        assert journal.current_mutation('provision', 'up', reconciliation=True) == operation
        monkeypatch.setattr(journal, '_process_id', -1)
        with pytest.raises(StateError, match='process and environment lock'):
            journal.current_mutation('provision', 'up', reconciliation=True)
    with pytest.raises(StateError):
        journal.current_mutation('provision', 'up', reconciliation=True)


@pytest.mark.parametrize('field,value', [('command', 'echo bad'), ('retained_dependencies', 'delete'),
                                      ('kind', 'other'), ('transport', {'host_key_check': False})])
def test_closed_config_has_no_command_callback_or_implicit_trust(setup, field, value):
    selected = setup['values']['config']
    selected['runtime']['foundation'][field] = value
    with pytest.raises(ValueError):
        validate_document(selected, 'factory-config')
