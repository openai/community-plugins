"""Real journal/files; controlled Docker observations. No live provisioning claim."""
import base64
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from devsecops.factory import owned_registry as owned, registry_remote as remote, service_recipe as recipes, tcg
from devsecops.factory import registry_qualification
from devsecops.factory.state import StateError, _canonical, _sha
from tests.test_owned_foundation import setup as foundation_setup, context

__all__ = ['foundation_setup']
_REAL_DISPATCH = owned.OwnedOCIRegistry._dispatch


def recipe(tmp_path, environment='demo-test'):
    value = {'schema_version': 1, 'kind': 'owned-zot-v1', 'version': '2.1.22', 'environment': environment,
        'origin': 'https://registry.'+environment+'.test:8443', 'repository': 'devsecops/'+environment,
        'remote_directory': '/home/operator/.local/share/devsecops/registries/'+environment,
        'private_state_directory': str(tmp_path/'registry-private'),
        'image': {'reference': 'ghcr.io/project-zot/zot-minimal@sha256:'+'a'*64, 'image_id': 'sha256:'+'b'*64,
                  'entrypoint': '/usr/local/bin/zot-linux-amd64-minimal'},
        'tool_image': 'sha256:'+'c'*64, 'transport': {'identity_agent': str(tmp_path/'agent'),
            'host_key_sha256': 'd'*64, 'verification_basis': 'operator-verified', 'agent_protocol': 'native'},
        'materials': {}, 'credentials': {'operator': {'username': 'operator', 'password_env': 'REGISTRY_WRITE_PASSWORD'},
                                       'reader': {'username': 'reader', 'password_env': 'REGISTRY_READ_PASSWORD'}}}
    for group, names in (('transport', ('config', 'known_hosts', 'certificate', 'identity', 'proxy')),
                         ('materials', ('ca', 'certificate', 'private_key', 'htpasswd'))):
        for name in names:
            path = tmp_path/(group+'-'+name)
            path.write_bytes(('controlled-'+name).encode())
            path.chmod(0o600)
            value[group][name] = {'path': str(path), 'sha256': _sha(path.read_bytes())}
    return value


@pytest.fixture
def service(foundation_setup, monkeypatch):
    base = foundation_setup
    selected = base['values']['config']
    chosen = recipe(base['tmp'], selected['environment'])
    registry = next(row for row in selected['services'] if row['id'] == 'registry')
    registry.update(origin=chosen['origin'], ca_file=chosen['materials']['ca']['path'],
        scope={'repositories': [chosen['repository'], chosen['repository']+'/qualification']},
        read_auth={'mode': 'basic', 'username_env': 'REGISTRY_READER', 'password_env': 'REGISTRY_READ_PASSWORD'},
        operator_auth={'mode': 'basic', 'username_env': 'REGISTRY_OPERATOR', 'password_env': 'REGISTRY_WRITE_PASSWORD'})
    for name, value in {'REGISTRY_READER': 'reader', 'REGISTRY_OPERATOR': 'operator',
                        'REGISTRY_READ_PASSWORD': 'test-read-secret-canary', 'REGISTRY_WRITE_PASSWORD': 'test-write-secret-canary'}.items():
        monkeypatch.setenv(name, value)
    source = Path(owned.__file__).resolve().parents[3]
    package = base['package']
    for name in set(owned.MODULES) | set(owned.PROBE_MODULES):
        target = package/'src/devsecops'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source/'src/devsecops'/name).read_bytes())
    for module in (owned, recipes, remote, registry_qualification):
        monkeypatch.setattr(module, '__file__', str(package/'src/devsecops/factory'/Path(module.__file__).name))
    monkeypatch.setattr(owned, 'PACKAGE_ROOT', package)
    calls, changes = [], {}
    # Build an independent journal because all six identities must bind the
    # complete new package before initialize_lifecycle freezes its inputs.
    from devsecops.factory.state import RunJournal
    def new_start():
        raw_original = _canonical(base['original'])
        Path(base['chosen']['source_receipt']['path']).write_bytes(raw_original)
        base['chosen']['source_receipt']['sha256'] = _sha(raw_original)
        files = {str(path.relative_to(package)): path.read_bytes() for path in package.rglob('*')
                 if path.is_file() and path.name != 'SHA256SUMS'}
        manifest = ''.join(f'{_sha(raw)}  {name}\n' for name, raw in sorted(files.items())).encode()
        (package/'SHA256SUMS').write_bytes(manifest)
        raw = {name: _canonical(value) for name, value in base['values'].items()}
        for name in ('scenario', 'assertions'):
            raw[name] = (package/'demo/software-factory/scenarios/ironbank-mission-recovery-v1'/f'{name}.json').read_bytes()
        raw['package'] = manifest
        identities = {name+'_sha256': _sha(value) for name, value in raw.items()}
        journal = RunJournal.create(base['tmp']/'registry-run', identities=identities, environment_id=selected['environment'])
        journal.initialize_lifecycle(inputs=raw, execution_mode='controlled-test', driver_id='owned-linux')
        return journal
    def dispatch(driver, selected, packet, directory, deadline):
        calls.append(deepcopy(packet))
        if changes.get('interrupt'):
            raise tcg.TCGError('controlled interruption')
        if changes.get('hook'):
            changes['hook']()
        return {'bootstrap': 'container-running', 'storage_qualification': 'pass', 'resources': [],
                'cleanup': 'incomplete', 'canonical_success': False, 'mutation_effect': 'unknown'}
    monkeypatch.setattr(owned.OwnedOCIRegistry, '_dispatch', dispatch)
    return {**base, 'recipe': chosen, 'new_start': new_start, 'calls': calls, 'changes': changes}


@contextmanager
def invoke(service, action='up', include_intent=True):
    journal = service['new_start']()
    path = service['tmp']/'recipe.json'
    path.write_bytes(_canonical(service['recipe']))
    driver = owned.OwnedOCIRegistry(journal, recipe_path=path, recipe_sha256=_sha(path.read_bytes()),
                                  plugin_id='devsecops@devsecops-local', plugin_version='0.1.0')
    proof = service['tmp']/'controlled.json'
    proof.write_text('{"controlled":true}')
    with journal.lifecycle_lock(), journal.mutation_lock(service['tmp']/'registry-locks'):
        journal.begin_phase('preflight')
        if action == 'down':
            journal.abort_lifecycle('preflight', 'incomplete', 'controlled-stop')
            journal.finish_phase('preflight', 'incomplete', 'controlled-stop', receipt_artifacts=[proof])
            journal.begin_phase('export')
            journal.finish_phase('export', 'completed', 'controlled', receipt_artifacts=[proof])
        else:
            journal.finish_phase('preflight', 'completed', 'controlled', receipt_artifacts=[proof])
            journal.begin_phase('prepare')
            journal.finish_phase('prepare', 'completed', 'controlled', receipt_artifacts=[proof])
        ctx = context(journal, service['values'], action)
        journal.begin_phase(ctx.phase['id'])
        operation = journal.begin_mutation(ctx.phase['id'], action, intent_artifacts=[path] if include_intent else [proof])
        yield journal, driver, ctx, operation


def test_journal_component_keeps_bootstrap_uploads_and_phase_effect_separate(service):
    with invoke(service) as (journal, driver, ctx, operation):
        outcome = driver.up(ctx)
        report = json.loads(outcome.artifacts[-1].read_bytes())
        assert outcome.status == 'incomplete' and outcome.mutation_effect == 'unknown'
        assert report['observation']['storage_qualification'] == 'pass' and not report['canonical_success']
        assert report['whole_phase_mutation_effect'] == 'unknown' and not report['complete_cleanup']
        assert service['calls'][0]['operation_id'] == operation
        assert journal.state['active_mutation']['operation_id'] == operation
        assert journal.state['environment_reserved']
        assert b'secret-canary' not in b''.join(path.read_bytes() for path in outcome.artifacts)
        with pytest.raises(StateError, match='cannot release'):
            driver.validate_cleanup([])


def test_exact_recipe_must_be_an_intent_before_any_dispatch(service):
    with invoke(service, include_intent=False) as (_, driver, ctx, _):
        with pytest.raises(StateError, match='frozen current mutation intent'):
            driver.up(ctx)
    assert not service['calls']


@pytest.mark.parametrize('change', ['scope', 'origin', 'credential', 'material', 'package-state', 'journal-state', 'expired'])
def test_recipe_and_private_paths_fail_closed_before_dispatch(service, change):
    chosen = service['recipe']
    if change == 'scope':
        next(row for row in service['values']['config']['services'] if row['id'] == 'registry')['scope'] = {'repositories': ['other']}
    elif change == 'origin':
        chosen['origin'] = 'https://registry1.dso.mil'
    elif change == 'credential':
        chosen['credentials']['operator']['password_env'] = 'OTHER_PASSWORD'
    elif change == 'material':
        chosen['materials']['private_key']['sha256'] = '0'*64
    elif change == 'package-state':
        chosen['private_state_directory'] = str(service['package']/'registry-state')
    elif change == 'journal-state':
        chosen['private_state_directory'] = str(service['tmp']/'registry-run/private')
    elif change == 'expired':
        service['values']['config']['expires_at'] = '2020-01-01T00:00:00Z'
    with invoke(service) as (_, driver, ctx, _):
        with pytest.raises((StateError, ValueError)):
            driver.up(ctx)
    assert not service['calls']


def test_dispatch_failure_retains_unknown_and_reconciliation_cannot_retry_upload(service):
    service['changes']['interrupt'] = True
    with invoke(service) as (_, driver, ctx, operation):
        outcome = driver.up(ctx)
        assert outcome.mutation_effect == 'unknown'
        service['changes']['interrupt'] = False
        driver.reconcile(ctx, operation)
        assert [item['action'] for item in service['calls']] == ['up', 'reconcile']
        assert 'materials' not in service['calls'][-1] and 'probe_settings' not in service['calls'][-1]


def test_cleanup_after_window_closes_needs_no_secret_values(service, monkeypatch):
    service['values']['config']['expires_at'] = '2020-01-01T00:00:00Z'
    monkeypatch.delenv('REGISTRY_WRITE_PASSWORD')
    monkeypatch.delenv('REGISTRY_READ_PASSWORD')
    with invoke(service, 'down') as (_, driver, ctx, _):
        assert driver.down(ctx).status == 'incomplete'
    assert service['calls'][0]['action'] == 'down' and 'materials' not in service['calls'][0]


def test_probe_zip_is_exact_stdlib_closure_and_excludes_credentials(service):
    import io
    import zipfile
    with invoke(service) as (_, driver, ctx, _):
        driver.up(ctx)
    raw = base64.b64decode(service['calls'][0]['probe_zip'])
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        assert set(zipped.namelist()) == {'devsecops/'+name for name in owned.PROBE_MODULES} | {'__main__.py'}
        assert b'secret-canary' not in raw and b'jsonschema' not in zipped.read('devsecops/factory/registry_qualification.py')
    path = service['tmp']/'probe.zip'
    path.write_bytes(raw)
    # -I -S excludes both the checkout and installed third-party dependencies.
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
        'import sys;sys.path.insert(0,sys.argv[1]);from devsecops.factory.registry_qualification import objects;assert len(objects("a"*64))==4',
        str(path)], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()


def test_wall_clock_expiry_after_dispatch_prevents_terminal_receipt(service, monkeypatch):
    from datetime import datetime, timezone
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 1, tzinfo=timezone.utc)
    service['changes']['hook'] = lambda: monkeypatch.setattr(owned, 'datetime', Future)
    with invoke(service) as (journal, driver, ctx, _):
        with pytest.raises(StateError, match='window'):
            driver.up(ctx)
        assert not list(journal.run_dir.glob('registry-*/registry.json'))
        assert list(journal.run_dir.glob('registry-*/intent.json'))


def test_terminal_deadline_after_publication_withdraws_report(service, monkeypatch):
    original = owned._write_terminal
    def delayed(directory, name, value):
        identity = original(directory, name, value)
        if name == 'registry.json':
            monkeypatch.setattr(owned.time, 'monotonic', lambda: 10**12)
        return identity
    monkeypatch.setattr(owned, '_write_terminal', delayed)
    with invoke(service) as (journal, driver, ctx, _):
        with pytest.raises(StateError, match='deadline'):
            driver.up(ctx)
        assert not list(journal.run_dir.glob('registry-*/registry.json'))
        assert list(journal.run_dir.glob('registry-*/intent.json'))


def test_terminal_exclusive_conflict_preserves_unowned_receipt(service, monkeypatch):
    original = owned._write_terminal
    retained = b'concurrent terminal evidence this operation did not create'
    def conflict(directory, name, value):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            os.write(fd, retained)
        finally:
            os.close(fd)
        return original(directory, name, value)
    monkeypatch.setattr(owned, '_write_terminal', conflict)
    with invoke(service) as (journal, driver, ctx, _):
        with pytest.raises(FileExistsError):
            driver.up(ctx)
        paths = list(journal.run_dir.glob('registry-*/registry.json'))
        assert len(paths) == 1 and paths[0].read_bytes() == retained
        assert list(journal.run_dir.glob('registry-*/intent.json'))


def test_terminal_withdrawal_preserves_replacement_identity(service, monkeypatch):
    original = owned._write_terminal
    retained = b'replacement evidence from a separate writer'
    def replace_after_write(directory, name, value):
        identity = original(directory, name, value)
        os.rename(name, 'displaced-owned.json', src_dir_fd=directory, dst_dir_fd=directory)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            os.write(fd, retained)
        finally:
            os.close(fd)
        monkeypatch.setattr(owned.time, 'monotonic', lambda: 10**12)
        return identity
    monkeypatch.setattr(owned, '_write_terminal', replace_after_write)
    with invoke(service) as (journal, driver, ctx, _):
        with pytest.raises(StateError, match='deadline'):
            driver.up(ctx)
        paths = list(journal.run_dir.glob('registry-*/registry.json'))
        assert len(paths) == 1 and paths[0].read_bytes() == retained


@pytest.mark.parametrize('name', ['factory/tcg.py', 'factory/service_recipe.py', 'factory/registry_remote.py'])
def test_captured_transport_bytes_must_match_admitted_package_before_ssh(service, monkeypatch, name):
    def no_transport(*args, **kwargs):
        pytest.fail('Unapproved captured bytes reached SSH transport')
    monkeypatch.setattr(tcg, '_ssh_context', no_transport)
    with invoke(service) as (_, driver, ctx, _):
        _, selected, hashes, _ = driver._admit(ctx, 'up', ctx.deadline, None)
        path = service['package']/'src/devsecops'/name
        path.write_bytes(path.read_bytes()+b'\n# changed after admission\n')
        with pytest.raises(StateError, match='Captured registry transport module'):
            _REAL_DISPATCH(driver, selected, {'package_modules': hashes}, service['tmp'], ctx.deadline)


@pytest.mark.parametrize('name', ['__init__.py', 'adapters/http.py'])
def test_captured_probe_bytes_must_match_admitted_package(service, name):
    with invoke(service) as (_, driver, ctx, _):
        _, _, hashes, _ = driver._admit(ctx, 'up', ctx.deadline, None)
        path = service['package']/'src/devsecops'/name
        path.write_bytes(path.read_bytes()+b'\n# changed after admission\n')
        with pytest.raises(StateError, match='Captured registry probe module'):
            owned._probe_zip(hashes)


@pytest.mark.parametrize('field,value', [('version', 'latest'), ('tool_image', 'ubuntu:latest'),
                                       ('extra', 'command'), ('repository', 'another/repo')])
def test_closed_recipe_rejects_unsupported_fields_and_moving_inputs(tmp_path, field, value):
    selected = recipe(tmp_path)
    selected[field] = value
    with pytest.raises(ValueError):
        recipes.validate(selected)


@pytest.mark.parametrize('entrypoint', ['/usr/bin/zot', '/bin/sh', '/usr/local/bin/zot-linux-arm64-minimal'])
def test_recipe_rejects_unobserved_release_entrypoints(tmp_path, entrypoint):
    selected = recipe(tmp_path)
    selected['image']['entrypoint'] = entrypoint
    with pytest.raises(ValueError, match='entry point'):
        recipes.validate(selected)


def test_generated_zot_config_is_exact_repo_scoped_and_has_no_admin_or_delete(tmp_path):
    selected = recipe(tmp_path)
    settings = recipes.configuration(selected)
    assert set(settings['http']['accessControl']['repositories']) == {selected['repository'], selected['repository']+'/qualification'}
    assert 'adminPolicy' not in json.dumps(settings) and 'delete' not in json.dumps(settings)
    assert 'extensions' not in settings and settings['storage']['gc'] is False
