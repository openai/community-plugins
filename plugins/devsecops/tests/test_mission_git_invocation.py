"""Fixed CLI admission and actual local Git with controlled upstream and GETs."""
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import zipfile

import pytest
import yaml

from devsecops.factory import mission_git_invocation as invocation
from devsecops.factory import mission_git_operation as operation
from devsecops.runtime.context import canonical_bytes, seal
from scripts import build_plugin, publish_and_prepare_mission_git as cli
from test_mission_git_operation import case, subject, sha
from test_git_transport import inventory

__all__ = ['case', 'subject']
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'scripts/publish_and_prepare_mission_git.py'
RECIPE = ROOT/'demo/software-factory/ci/gitlab-git-preparation.yml'


def envelope(subject):
    args = subject['args']
    return dict(schema_version=1, kind='mission-git-preparation-invocation',
        configurations={name: dict(path=str(path), sha256=pin)
                        for name, (path, pin) in args['configurations'].items()},
        run_scope=deepcopy(args['expected_run_scope']), plugin_id=args['plugin_id'],
        plugin_version=args['plugin_version'], source=dict(repository=str(args['expected_source_repository']),
        revision=args['expected_source_revision']), output=str(args['output']),
        runtime_root=str(args['runtime_root']), timeout=3960)


def arguments(subject, value=None):
    raw = canonical_bytes(envelope(subject) if value is None else value)
    path = subject['args']['output'].parent/'invocation.json'
    path.write_bytes(raw)
    return ['--invocation', str(path), '--invocation-sha256', sha(raw),
        '--expected-source-repository', str(subject['args']['expected_source_repository']),
        '--expected-source-revision', subject['args']['expected_source_revision']]


def test_shared_schema_and_parser_are_read_only(subject):
    value = envelope(subject)
    assert json.loads((ROOT/'schemas/mission-git-invocation.schema.json').read_bytes()) == invocation.INVOCATION_SCHEMA
    assert invocation.invocation_configuration(canonical_bytes(value)) == value
    assert not subject['calls'] and not subject['gets']
    assert not subject['args']['output'].exists()


@pytest.mark.parametrize('change', [
    lambda x: x.update(schema_version=True), lambda x: x.update(extra='CANARY'),
    lambda x: x.update(timeout=True), lambda x: x.update(timeout=248), lambda x: x.update(timeout=3961),
    lambda x: x['configurations'].pop('destination'),
    lambda x: x['configurations'].update(saved_receipt={}),
    lambda x: x['configurations']['build'].update(path='/absolute/../private'),
    lambda x: x['source'].update(revision='main'), lambda x: x['source'].update(repository='relative'),
    lambda x: x['run_scope'].update(ci_job_id='37'), lambda x: x['run_scope'].update(package_sha256='x'*64),
    lambda x: x.update(plugin_id='../../CANARY'), lambda x: x.update(plugin_version='latest'),
    lambda x: x.update(runtime_root='/tmp/../runtime'), lambda x: x.update(output='/tmp/\nCANARY'),
])
def test_closed_envelope_rejects_before_any_effect(subject, change, capsys):
    value = envelope(subject)
    change(value)
    assert cli.main(arguments(subject, value)) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == cli.summary()
    assert not subject['calls'] and not subject['gets']


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'{"timeout":NaN}', b'{"timeout":Infinity}', b'{'*64001])
def test_invalid_bounded_json(raw):
    with pytest.raises(ValueError):
        invocation.invocation_configuration(raw)


@pytest.mark.parametrize('mutation', ['pin', 'bytes', 'symlink', 'relative', 'source', 'revision'])
def test_selected_file_and_independent_source_checks_before_effect(subject, mutation, capsys):
    args = arguments(subject)
    path = Path(args[1])
    if mutation == 'pin':
        args[3] = '0'*64
    elif mutation == 'bytes':
        path.write_bytes(path.read_bytes()+b' ')
    elif mutation == 'symlink':
        other = path.with_suffix('.target')
        path.rename(other)
        path.symlink_to(other)
    elif mutation == 'relative':
        args[1] = 'invocation.json'
    elif mutation == 'source':
        args[5] = '/wrong/source'
    else:
        args[7] = '9'*40
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out) == cli.summary()
    assert not subject['calls'] and not subject['gets']


@pytest.mark.parametrize('name', operation.NAMES)
def test_configuration_hash_drift_stops_before_upstream(subject, name, capsys):
    args = arguments(subject)
    subject['args']['configurations'][name][0].write_bytes(b'changed CANARY')
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out) == cli.summary()
    assert not subject['calls'] and not subject['gets']


@pytest.mark.parametrize('change', ['output', 'runtime', 'overlap', 'run', 'package'])
def test_composition_admission_stays_active(subject, change, capsys, monkeypatch):
    value = envelope(subject)
    if change in ('output', 'runtime'):
        value['output' if change == 'output' else 'runtime_root'] = str(subject['args']['expected_source_repository']/'forbidden')
    elif change == 'overlap':
        value['runtime_root'] = value['output']+'/child'
    else:
        value['run_scope']['package_sha256' if change == 'package' else 'config_sha256'] = '7'*64
    # Installed fixture uses an assertion; use production-like rejection for wrong selected package.
    original = operation._installed
    def installed(expected, *rest):
        if expected != subject['args']['expected_run_scope']['package_sha256']:
            raise ValueError('Package mismatch CANARY')
        return original(expected, *rest)
    monkeypatch.setattr(operation, '_installed', installed)
    assert cli.main(arguments(subject, value)) == 2
    assert json.loads(capsys.readouterr().out) == cli.summary()
    assert not subject['calls'] and not subject['gets']


def test_actual_cli_local_git_stages_emit_only_sanitized_result(subject, capsys):
    before = inventory(subject['case']['repo'])
    assert cli.main(arguments(subject)) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    private = json.loads((subject['args']['output']/'mission-git-operation.json').read_bytes())
    assert result == cli.summary(private)
    assert result['decision'] == 'pass' and set(result['checks'].values()) == {'pass'}
    assert result['factory_decision'] == 'unknown' and result['ci_execution'] == 'not-established'
    assert result['git_write'] == 'not-performed' and result['delivery_authority'] == 'not-issued'
    assert inventory(subject['case']['repo']) == before
    assert len(subject['calls']) == 1 and subject['gets']
    assert not captured.err and len(captured.out.encode()) < 1024
    assert all(text not in captured.out for text in ('CANARY', str(subject['args']['output'].parent),
        'source_commit', 'configuration_sha256', 'run_scope', 'new_commit'))
    assert (subject['args']['output']/'inputs/destination.json').exists()
    assert not list(subject['args']['expected_source_repository'].iterdir())


def test_explicit_policy_failure_exit_one_stops_later_stages(subject, monkeypatch, capsys):
    def reject(**kwargs):
        subject['calls'].append(kwargs)
        return seal({'schema_version':1, 'kind':'mission-publish-and-propose', 'decision':'fail'},
                    'proposal_operation_sha256'), None
    monkeypatch.setattr(operation.mission_proposal_operation, 'qualify_publish_and_propose', reject)
    assert cli.main(arguments(subject)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['decision'] == 'fail' and result['checks']['commit'] == 'not-started'
    assert len(subject['calls']) == 1 and not subject['gets']


@pytest.mark.parametrize('error', [ValueError('CANARY'), KeyboardInterrupt()])
def test_unknown_or_interruption_retains_intent_without_retry(subject, capsys, error):
    def hook(kwargs):
        raise error
    subject['hook'] = hook
    args = arguments(subject)
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            cli.main(args)
        assert capsys.readouterr().out == ''
    else:
        assert cli.main(args) == 2
        assert json.loads(capsys.readouterr().out)['decision'] == 'unknown'
    assert len(subject['calls']) == 1 and not subject['gets']
    assert (subject['args']['output']/'publication-proposal/retained-intent.txt').is_file()


def run_cli(args, script=SCRIPT):
    return subprocess.run([sys.executable, '-I', '-B', str(script), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)


@pytest.mark.parametrize('args', [[], ['--unexpected', 'CANARY'], ['--invoc', 'CANARY']])
def test_actual_entry_argument_errors_are_sanitized(args):
    result = run_cli(args)
    assert result.returncode == 2 and not result.stdout
    assert result.stderr == b'Invalid mission Git command arguments\n'


def test_actual_entry_rejects_uninstalled_package_without_effect(subject):
    result = run_cli(arguments(subject))
    assert result.returncode == 2 and json.loads(result.stdout) == cli.summary()
    assert not result.stderr and not subject['args']['output'].exists()


@pytest.mark.parametrize('missing', [
    'src/devsecops/factory/mission_git_invocation.py', 'scripts/publish_and_prepare_mission_git.py',
    'schemas/mission-git-invocation.schema.json', 'docs/interfaces/mission-git-invocation.md',
    'demo/software-factory/ci/gitlab-git-preparation.yml', 'src/devsecops/factory/mission_git_operation.py',
    'src/devsecops/factory/deployment_commit.py', 'src/devsecops/factory/git_transport.py',
    'src/devsecops/factory/git_base_possession.py', 'docs/interfaces/mission-git-composition.md',
])
def test_package_requires_complete_caller_group(missing):
    files = build_plugin.package_files()
    files.pop(missing)
    with pytest.raises(ValueError, match='Mission Git invocation'):
        build_plugin.validate(files)


def test_extracted_package_cli_uses_selected_root_in_isolated_mode(tmp_path):
    archive = tmp_path/'candidate.zip'
    result = build_plugin.build(archive)
    assert result['files'] > 0
    extracted = tmp_path/'with spaces'
    with zipfile.ZipFile(archive) as package:
        package.extractall(extracted)
    script = extracted/'devsecops/scripts/publish_and_prepare_mission_git.py'
    result = run_cli(['--help'], script)
    assert result.returncode == 0 and b'--invocation-sha256' in result.stdout
    assert not result.stderr


@pytest.mark.parametrize('code', [0, 1, 2])
def test_recipe_preserves_cli_exit_and_archives_only_private_mode_summary(tmp_path, code):
    script, env, log = recipe_fixture(tmp_path, code)
    result = subprocess.run(['/bin/sh', '-c', script], env=env, capture_output=True, timeout=30)
    assert result.returncode == code
    output = tmp_path/'checkout/mission-git-summary.json'
    assert json.loads(output.read_bytes()) == {'decision': {0:'pass', 1:'fail', 2:'unknown'}[code]}
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    args = json.loads(log.read_bytes())
    assert args[:2] == ['-I', '-B']
    assert args[3:] == ['--invocation', env['DEVSECOPS_GIT_INVOCATION'], '--invocation-sha256', '1'*64,
        '--expected-source-repository', env['CI_PROJECT_DIR'], '--expected-source-revision', '2'*40]


@pytest.mark.parametrize('existing', ['file', 'symlink', 'dangling', 'directory', 'fifo'])
def test_recipe_refuses_existing_summary_before_invocation(tmp_path, existing):
    script, env, log = recipe_fixture(tmp_path, 0)
    output = tmp_path/'checkout/mission-git-summary.json'
    if existing == 'file':
        output.write_text('retain')
    elif existing in ('symlink', 'dangling'):
        target = tmp_path/'target'
        if existing == 'symlink':
            target.write_text('retain')
        output.symlink_to(target)
    elif existing == 'directory':
        output.mkdir()
    else:
        os.mkfifo(output)
    result = subprocess.run(['/bin/sh', '-c', script], env=env, capture_output=True, timeout=30)
    assert result.returncode == 2 and not log.exists()
    if existing == 'file':
        assert output.read_text() == 'retain'


def recipe_fixture(tmp_path, code):
    value = yaml.safe_load(RECIPE.read_text())
    job = value['publish_and_prepare_mission_git']
    assert job['rules'] == [{'if': '$CI_PIPELINE_SOURCE == "merge_request_event"'}]
    assert job['retry'] == 0 and job['timeout'] == '75 minutes'
    assert job['artifacts']['paths'] == ['mission-git-summary.json']
    assert job['artifacts']['when'] == 'on_success'
    assert len(job['script']) == 1
    checkout = tmp_path/'checkout'
    checkout.mkdir()
    log = tmp_path/'invocation-args.json'
    runner = tmp_path/'controlled-python'
    runner.write_text('#!'+sys.executable+'\nimport json,sys\n'
        +f'open({str(log)!r}, "w").write(json.dumps(sys.argv[1:]))\n'
        +f'print(json.dumps({{"decision": { {0:"pass",1:"fail",2:"unknown"}[code]!r}}}))\n'
        +f'raise SystemExit({code})\n')
    runner.chmod(0o700)
    env = dict(PATH='/usr/bin:/bin', DEVSECOPS_PYTHON=str(runner), DEVSECOPS_PLUGIN_ROOT='/selected/plugin',
        DEVSECOPS_GIT_INVOCATION='/private/invocation.json', DEVSECOPS_GIT_INVOCATION_SHA256='1'*64,
        CI_PROJECT_DIR=str(checkout), CI_COMMIT_SHA='2'*40)
    return job['script'][0], env, log


@pytest.mark.parametrize('field,value', [('git_write', 'performed'), ('factory_decision', 'pass'),
    ('ci_execution', 'authenticated'), ('delivery_authority', 'issued'), ('omission_authorized', True)])
def test_summary_rejects_contradictory_scope(field, value):
    report = dict(cli.summary(), kind='mission-publish-and-prepare-git', decision='pass',
                  checks=dict.fromkeys(('publication_proposal', 'commit', 'transport', 'base'), 'pass'))
    report[field] = value
    report = seal(report, 'git_operation_sha256')
    with pytest.raises(ValueError):
        cli.summary(report)


def test_pure_parser_does_not_load_effectful_composition():
    code = ('import sys;sys.path.insert(0, '+repr(str(ROOT/'src'))+');'
            'from devsecops.factory.mission_git_invocation import invocation_configuration;'
            'assert "devsecops.factory.mission_git_operation" not in sys.modules;'
            'assert "devsecops.factory.owned_linux" not in sys.modules')
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
