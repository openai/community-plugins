"""Local bookkeeping and registered CLI fixtures; no live service effects."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from devsecops.factory import auxiliary_inputs as aux, cli, evidence_export as export, owned_linux as owned
from devsecops.factory import mission_git_invocation as invocation, service_recipe, state as state_module
from devsecops.factory.lifecycle import LifecycleEngine, SCENARIO_ROOT
from devsecops.factory.state import AUXILIARY_NAMES, IDENTITY_KEYS, RunJournal, StateError, _canonical, _sha
from tests.test_factory_lifecycle import inputs_for
from tests.test_owned_registry import recipe
from tests.test_factory_evidence_export import _archive

PLUGIN = {'plugin_id': 'devsecops@devsecops-local', 'plugin_version': '0.1.0'}


def write(path, value):
    path.write_bytes(value if isinstance(value, bytes) else _canonical(value))
    path.chmod(0o600)
    return path, _sha(path.read_bytes())


def current_inputs(tmp_path):
    inputs, _ = inputs_for(tmp_path)
    inputs['config']['expires_at'] = (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    # Registry diagnostics have no HTTP handler; missing services remain gaps.
    inputs['config']['services'] = [next(s for s in inputs['config']['services'] if s['id'] == 'registry')]
    raw = {name: _canonical(value) if isinstance(value, dict) else value.read_bytes() for name, value in inputs.items()}
    return inputs, raw, {name+'_sha256': _sha(value) for name, value in raw.items()}


def selection(tmp_path, identities, environment):
    configs = {name: write(tmp_path/(name+'.json'), {'fixture': name}) for name in invocation.NAMES}
    value = {'schema_version': 1, 'kind': 'mission-git-preparation-invocation',
        'configurations': {name: {'path': str(pair[0]), 'sha256': pair[1]} for name, pair in configs.items()},
        'run_scope': identities, **PLUGIN, 'source': {'repository': str(tmp_path/'checkout'), 'revision': 'a'*40},
        'output': str(tmp_path/'publication-output'), 'runtime_root': str(tmp_path/'runtime'), 'timeout': 300}
    return {'mission_git_'+name: pair for name, pair in configs.items()} | {
        'mission_git_invocation': write(tmp_path/'invocation.json', value),
        'owned_registry_recipe': write(tmp_path/'recipe.json', recipe(tmp_path, environment))}


def load(chosen, journal):
    return aux.load_selection(chosen, identities=journal.identities,
        environment_id=journal.state['environment_id'], **PLUGIN)


def initialize(journal, raw, selected=None):
    journal.initialize_lifecycle(inputs=raw, execution_mode='operator', driver_id='owned-linux', auxiliary_inputs=selected)


@pytest.fixture
def case(tmp_path):
    inputs, raw, identities = current_inputs(tmp_path)
    journal = RunJournal.create(tmp_path/'run', identities=identities, environment_id=inputs['config']['environment'])
    return journal, inputs, raw, selection(tmp_path, identities, inputs['config']['environment'])


def test_atomic_selection_preserves_six_identities_and_exact_resume(case):
    journal, _, raw, chosen = case
    initialize(journal, raw, load(chosen, journal))
    state = journal.state
    assert set(state['identities']) == IDENTITY_KEYS and len(IDENTITY_KEYS) == 6
    assert set(state['lifecycle']['auxiliary_inputs']) == set(chosen)
    event = json.loads((journal.run_dir/'journal/00000002.json').read_bytes())
    assert event['type'] == 'lifecycle_initialized'
    assert event['payload']['auxiliary_inputs'] == state['lifecycle']['auxiliary_inputs']
    reopened = RunJournal.open(journal.run_dir, identities=journal.identities)
    initialize(reopened, raw, load(chosen, reopened))
    assert reopened.state == state
    assert not state['environment_reserved'] and not state['operations'] and not state['lifecycle']['phase_records']


@pytest.mark.parametrize('empty', [None, {}])
def test_legacy_absence_cannot_be_retroactively_bound_even_after_progress(case, empty):
    journal, _, raw, chosen = case
    initialize(journal, raw, empty)
    assert 'auxiliary_inputs' not in journal.state['lifecycle']
    initialize(journal, raw, empty)
    with pytest.raises(StateError, match='changed or is missing'):
        initialize(journal, raw, load(chosen, journal))
    with journal.lifecycle_lock():
        journal.begin_phase('preflight')
        with pytest.raises(StateError, match='changed or is missing'):
            initialize(journal, raw, load(chosen, journal))
    assert 'auxiliary_inputs' not in journal.state['lifecycle']


@pytest.mark.parametrize('change', ['missing', 'added', 'bytes', 'pin', 'partial', 'callback', 'wrong-scope', 'wrong-plugin'])
def test_resume_rejects_changed_selection_without_progress(case, change):
    journal, _, raw, chosen = case
    initialize(journal, raw, load(chosen, journal))
    before, supplied = journal.state, deepcopy(chosen)
    if change == 'missing':
        supplied = {}
    elif change == 'added':
        supplied['arbitrary_command'] = supplied['owned_registry_recipe']
    elif change == 'bytes':
        supplied['owned_registry_recipe'][0].write_bytes(b'{}')
    elif change == 'pin':
        supplied['owned_registry_recipe'] = (supplied['owned_registry_recipe'][0], '0'*64)
    elif change == 'partial':
        del supplied['mission_git_build']
    elif change == 'callback':
        supplied['callback'] = lambda: pytest.fail('callback executed')
    else:
        path = supplied['mission_git_invocation'][0]
        value = json.loads(path.read_bytes())
        if change == 'wrong-scope':
            value['run_scope']['config_sha256'] = '0'*64
        else:
            value['plugin_version'] = '9.9.9'
        supplied['mission_git_invocation'] = write(path, value)
    with pytest.raises(StateError):
        initialize(journal, raw, load(supplied, journal))
    assert journal.state == before


@pytest.mark.parametrize('point', ['during-freeze', 'before-event', 'after-event'])
def test_interrupted_initialization_is_atomic(case, monkeypatch, point):
    journal, _, raw, chosen = case
    selected = load(chosen, journal)
    if point in ('before-event', 'during-freeze'):
        original = state_module._exclusive
        def fail(path, content):
            if ((point == 'before-event' and path.parent.name == 'journal' and path.name == '00000002.json')
                    or (point == 'during-freeze' and path.name == 'mission_git_commit')):
                raise OSError('controlled precommit crash')
            return original(path, content)
        monkeypatch.setattr(state_module, '_exclusive', fail)
    else:
        monkeypatch.setattr(journal, '_write_projection', lambda: (_ for _ in ()).throw(OSError('controlled crash')))
    with pytest.raises(OSError):
        initialize(journal, raw, selected)
    monkeypatch.undo()
    reopened = RunJournal.open(journal.run_dir, identities=journal.identities)
    if point != 'after-event':
        assert 'lifecycle' not in reopened.state
    else:
        assert set(reopened.state['lifecycle']['auxiliary_inputs']) == set(chosen)
        with pytest.raises(StateError):
            initialize(reopened, raw)
    initialize(reopened, raw, selected)
    assert reopened.state['journal_seq'] == 2


def test_fresh_source_paths_use_content_identity(case, tmp_path):
    journal, _, raw, chosen = case
    initialize(journal, raw, load(chosen, journal))
    fresh = deepcopy(chosen)
    for name in ('mission_git_invocation', 'owned_registry_recipe'):
        fresh[name] = write(tmp_path/('fresh-'+name), chosen[name][0].read_bytes())
    initialize(journal, raw, load(fresh, journal))
    fresh['owned_registry_recipe'][0].unlink()
    with pytest.raises((StateError, OSError)):
        load(fresh, journal)


def test_export_reference_completeness_and_tamper_detection(case):
    journal, _, raw, chosen = case
    initialize(journal, raw, load(chosen, journal))
    refs, paths, blobs = export._references(journal.state)
    bound = journal.state['lifecycle']['auxiliary_inputs']
    for name, ref in bound.items():
        assert any(row['location'] == 'lifecycle.auxiliary_inputs.'+name and row['sha256'] == ref['sha256'] for row in refs)
        assert ref['path'] in paths and ref['sha256'] in blobs
    (journal.run_dir/bound['owned_registry_recipe']['path']).write_bytes(b'changed')
    with pytest.raises(StateError):
        RunJournal.open(journal.run_dir, identities=journal.identities)


def test_missing_selection_cannot_materialize_intent(case):
    journal, _, raw, chosen = case
    initialize(journal, raw, load(chosen, journal))
    with pytest.raises(StateError):
        aux.selected_artifacts(journal, {})
    assert not journal.state['environment_reserved'] and not journal.state['operations']


@pytest.fixture
def installed(tmp_path, monkeypatch):
    package = tmp_path/'codex-home/plugins/cache/devsecops-local/devsecops/0.1.0'
    modules = [owned, owned.service_inventory, export, aux, invocation, service_recipe]
    files = {'.codex-plugin/plugin.json': _canonical({'name': 'devsecops', 'version': '0.1.0'}),
        **{'src/devsecops/factory/'+Path(m.__file__).name: Path(m.__file__).read_bytes() for m in modules},
        **{'demo/software-factory/scenarios/ironbank-mission-recovery-v1/'+name+'.json':
            (SCENARIO_ROOT/(name+'.json')).read_bytes() for name in ('scenario', 'assertions')}}
    for name, raw in files.items():
        (package/name).parent.mkdir(parents=True, exist_ok=True)
        write(package/name, raw)
    write(package/'SHA256SUMS', ''.join(f'{_sha(raw)}  {name}\n' for name, raw in sorted(files.items())).encode())
    monkeypatch.setenv('CODEX_HOME', str(tmp_path/'codex-home'))
    monkeypatch.setattr(owned, 'PACKAGE_ROOT', package)
    for module in modules:
        monkeypatch.setattr(module, '__file__', str(package/'src/devsecops/factory'/Path(module.__file__).name))
    monkeypatch.setattr(owned, 'HTTPSReadClient', lambda *a, **k: pytest.fail('network client created'))
    inputs, _, _ = current_inputs(tmp_path)
    inputs['package'] = package/'SHA256SUMS'
    for name in ('config', 'lock', 'environment'):
        inputs[name] = write(tmp_path/(name+'-input.json'), inputs[name])[0]
    identities = {name+'_sha256': _sha(path.read_bytes()) for name, path in inputs.items()}
    environment = json.loads(inputs['config'].read_bytes())['environment']
    chosen = selection(tmp_path, identities, environment)
    args = ['e2e', '--profile', 'bigbang-live', '--config', str(inputs['config']), '--lock', str(inputs['lock']),
        '--environment', environment, '--environment-inventory', str(inputs['environment']),
        '--package-manifest', str(inputs['package']), '--shared-lock-dir', str(tmp_path/'locks'),
        '--operation-backend', 'owned-linux', '--plugin-id', PLUGIN['plugin_id'], '--plugin-version', PLUGIN['plugin_version']]
    flags = ['--mission-git-invocation', str(chosen['mission_git_invocation'][0]), '--mission-git-invocation-sha256',
        chosen['mission_git_invocation'][1], '--owned-registry-recipe', str(chosen['owned_registry_recipe'][0]),
        '--owned-registry-recipe-sha256', chosen['owned_registry_recipe'][1]]
    return inputs, identities, chosen, args, flags


def test_registered_cli_exact_selection_and_resume_remain_incomplete(installed, tmp_path, capsys):
    _, identities, chosen, args, flags = installed
    run = tmp_path/'cli-run'
    assert cli.main([*args, *flags, '--output', str(run)]) == 2
    captured = capsys.readouterr()
    assert not captured.err
    report = json.loads(captured.out)
    assert not report['canonical_success'] and report['execution_status'] == 'incomplete'
    journal = RunJournal.open(run, identities=identities)
    before = journal.state
    assert set(before['lifecycle']['auxiliary_inputs']) == set(chosen)
    assert not before['environment_reserved'] and not before['operations'] and not (tmp_path/'locks').exists()
    assert before['lifecycle']['phase_records'][0]['outcome'] == 'incomplete'
    diagnostic = json.loads(next(run.glob('owned-linux-*/diagnostic.json')).read_bytes())
    assert diagnostic['package']['content_verified'] and not diagnostic['external_mutations_performed']
    assert cli.main([*args, *flags, '--resume', str(run)]) == 2
    resumed = capsys.readouterr()
    assert not resumed.err and json.loads(resumed.out) == report and journal.state == before
    assert cli.main([*args, '--resume', str(run)]) == 2
    rejected = capsys.readouterr()
    assert not rejected.out and 'missing' in json.loads(rejected.err)['message'] and journal.state == before


@pytest.mark.parametrize('bad', ['path-only', 'hash-only', 'wrong-backend', 'wrong-pin'])
def test_cli_invalid_selection_rejected_before_new_run(installed, tmp_path, capsys, bad):
    _, _, _, args, flags = installed
    if bad == 'path-only':
        flags = flags[:2]
    elif bad == 'hash-only':
        flags = flags[2:4]
    elif bad == 'wrong-backend':
        args[args.index('--operation-backend')+1] = 'unavailable'
        for flag in ('--plugin-id', '--plugin-version'):
            offset = args.index(flag)
            del args[offset:offset+2]
    else:
        flags[3] = '0'*64
    run = tmp_path/'rejected'
    assert cli.main([*args, *flags, '--output', str(run)]) == 2
    assert capsys.readouterr().err and not run.exists()


def test_fixed_predispatch_materializes_configs_and_recipe_in_actual_intent(installed, tmp_path):
    inputs, identities, chosen, _, _ = installed
    journal = RunJournal.create(tmp_path/'intent-run', identities=identities,
        environment_id=json.loads(inputs['config'].read_bytes())['environment'])
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path/'intent-locks',
        driver_id='owned-linux', auxiliary_inputs=chosen, **PLUGIN)
    # Controlled bookkeeping predecessor only; never a live preflight claim.
    with journal.lifecycle_lock(), journal.mutation_lock(tmp_path/'intent-locks'):
        journal.begin_phase('preflight')
        evidence = write(journal.run_dir/'fixture-only', b'controlled fixture predecessor')[0]
        journal.finish_phase('preflight', 'completed', 'controlled-local-fixture', receipt_artifacts=[evidence])
        phase = engine.docs['scenario']['phases'][1]
        journal.begin_phase(phase['id'])
        assert engine._phase(phase) is False
        operation, = journal.state['operations']
        assert {pair[1] for pair in chosen.values()} < {ref['sha256'] for ref in operation['intent_artifacts']}
        assert operation['status'] == 'not_applied'
        assert journal.state['lifecycle']['phase_records'][-1]['outcome'] == 'incomplete'


def test_changed_config_file_rejected_before_intent(installed, tmp_path):
    inputs, identities, chosen, _, _ = installed
    journal = RunJournal.create(tmp_path/'changed-run', identities=identities,
        environment_id=json.loads(inputs['config'].read_bytes())['environment'])
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path/'locks',
        driver_id='owned-linux', auxiliary_inputs=chosen, **PLUGIN)
    chosen['mission_git_build'][0].write_bytes(b'changed')
    with pytest.raises(StateError):
        engine.advance()
    assert not journal.state['environment_reserved'] and not journal.state['operations']


@pytest.mark.parametrize('selected_names', ['registry', 'mission'])
def test_independent_optional_groups_cannot_be_added_later(case, selected_names):
    journal, _, raw, chosen = case
    selected = {name: pair for name, pair in chosen.items()
                if (name == 'owned_registry_recipe') == (selected_names == 'registry')}
    initialize(journal, raw, load(selected, journal))
    initialize(journal, raw, load(selected, journal))
    with pytest.raises(StateError, match='changed or is missing'):
        initialize(journal, raw, load(chosen, journal))


@pytest.mark.parametrize('change', ['valid-recipe', 'valid-envelope', 'configuration-path'])
def test_repinning_valid_changed_content_cannot_resume(case, tmp_path, change):
    journal, _, raw, chosen = case
    initialize(journal, raw, load(chosen, journal))
    selected = deepcopy(chosen)
    if change == 'valid-recipe':
        name = 'owned_registry_recipe'
        value = json.loads(selected[name][0].read_bytes())
        value['private_state_directory'] = str(tmp_path/'different-private-state')
    else:
        name = 'mission_git_invocation'
        value = json.loads(selected[name][0].read_bytes())
        if change == 'valid-envelope':
            value['source']['revision'] = 'b'*40
        else:
            selected['mission_git_build'] = write(tmp_path/'new-build.json', chosen['mission_git_build'][0].read_bytes())
            value['configurations']['build']['path'] = str(selected['mission_git_build'][0])
    selected[name] = write(selected[name][0], value)
    with pytest.raises(StateError, match='changed or is missing'):
        initialize(journal, raw, load(selected, journal))


@pytest.mark.parametrize('bad', ['symlink', 'relative', 'parent', 'oversized', 'wrong-environment'])
def test_auxiliary_reads_are_bounded_and_fail_closed(case, tmp_path, bad):
    journal, _, _, chosen = case
    path, pin = chosen['owned_registry_recipe']
    if bad == 'symlink':
        alias = tmp_path/'alias'
        alias.symlink_to(path)
        chosen['owned_registry_recipe'] = alias, pin
    elif bad == 'relative':
        chosen['owned_registry_recipe'] = Path('recipe.json'), pin
    elif bad == 'parent':
        chosen['owned_registry_recipe'] = tmp_path/'..'/tmp_path.name/'recipe.json', pin
    elif bad == 'oversized':
        chosen['owned_registry_recipe'] = write(path, b'x'*64001)
    else:
        chosen['owned_registry_recipe'] = write(path, recipe(tmp_path, 'different-environment'))
    with pytest.raises((StateError, OSError, ValueError)):
        load(chosen, journal)
    assert not journal.state['operations'] and not journal.state['environment_reserved']


def test_private_export_contains_all_auxiliary_bytes(installed, tmp_path):
    inputs, identities, chosen, _, _ = installed
    journal = RunJournal.create(tmp_path/'export-run', identities=identities,
        environment_id=json.loads(inputs['config'].read_bytes())['environment'])
    engine = LifecycleEngine(journal, inputs=inputs, shared_lock_dir=tmp_path/'export-locks',
        driver_id='owned-linux', auxiliary_inputs=chosen, **PLUGIN)
    with journal.lifecycle_lock(), journal.mutation_lock(tmp_path/'export-locks'):
        journal.begin_phase('preflight')
        evidence = write(journal.run_dir/'fixture-stop', b'controlled incomplete predecessor')[0]
        journal.finish_phase('preflight', 'incomplete', 'controlled-fixture-stop', receipt_artifacts=[evidence])
        journal.abort_lifecycle('preflight', 'incomplete', 'controlled-fixture-stop')
        journal.begin_phase('export')
        phase = next(p for p in engine.docs['scenario']['phases'] if p['id'] == 'export')
        before = journal.state
        outcome = engine.driver.execute(engine._context(phase))
        manifest, members = _archive(outcome.artifacts)
        assert journal.state == before and manifest['canonical_success'] is False
        for name, pair in chosen.items():
            ref = next(row for row in manifest['references'] if row['location'] == 'lifecycle.auxiliary_inputs.'+name)
            assert ref['sha256'] == pair[1] and members[ref['member']] == pair[0].read_bytes()


@pytest.mark.parametrize('missing', [
    'src/devsecops/factory/auxiliary_inputs.py', 'src/devsecops/factory/mission_git_invocation.py',
    'src/devsecops/factory/service_recipe.py', 'src/devsecops/factory/evidence_export.py',
    'docs/interfaces/lifecycle-auxiliary-inputs.md',
])
def test_package_requires_auxiliary_runtime_dependencies(missing):
    from scripts import build_plugin
    files = build_plugin.package_files()
    build_plugin.validate(files)
    del files[missing]
    with pytest.raises(ValueError):
        build_plugin.validate(files)


def test_shared_parser_schema_and_closed_export_slots_are_exact():
    from tests.test_factory_evidence_export import test_closed_reference_selector_covers_every_artifact_slot_in_current_schema
    root = Path(__file__).resolve().parents[1]
    assert json.loads((root/'schemas/mission-git-invocation.schema.json').read_bytes()) == invocation.INVOCATION_SCHEMA
    assert AUXILIARY_NAMES == {'owned_registry_recipe', 'mission_git_invocation',
                               'native_selection', 'owned_operations_recipe', 'owned_bootstrap_recipe', 'owned_cases_recipe', 'owned_git_writer', 'owned_runtime_recipe',
                               *('mission_git_'+name for name in invocation.NAMES)}
    test_closed_reference_selector_covers_every_artifact_slot_in_current_schema()


@pytest.mark.parametrize('bad', ['empty', 'partial', 'wrong-backend', 'test-mode', 'list', 'unknown'])
def test_reducer_rejects_invalid_auxiliary_initialization(case, bad):
    journal, _, raw, chosen = case
    created = journal.state
    initialize(journal, raw, load(chosen, journal))
    event = json.loads((journal.run_dir/'journal/00000002.json').read_bytes())
    payload = event['payload']
    if bad == 'empty':
        payload['auxiliary_inputs'] = {}
    elif bad == 'partial':
        del payload['auxiliary_inputs']['mission_git_build']
    elif bad == 'wrong-backend':
        payload['driver_id'] = 'unavailable'
    elif bad == 'test-mode':
        payload['execution_mode'] = 'controlled-test'
    elif bad == 'list':
        payload['auxiliary_inputs'] = []
    else:
        payload['auxiliary_inputs']['callback'] = payload['auxiliary_inputs']['owned_registry_recipe']
    journal._state = created  # exercise event admission directly, not a disk edit
    with pytest.raises(StateError):
        journal._reduce(event, _sha(_canonical(event)))
