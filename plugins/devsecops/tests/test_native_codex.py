"""Controlled process-boundary tests; these are never native model qualification.

The executable below is an explicitly synthetic protocol peer. It tests real
subprocess pipes/session cleanup and driver validation without spending a model
call, installing a plugin, or producing runtime-native authority.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import tomllib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from devsecops.factory import native_codex as driver
from devsecops.factory.state import _canonical, _sha
from scripts.native_codex import main


@pytest.fixture
def lab(tmp_path, monkeypatch):
    home = tmp_path / 'codex-home'
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('MUST_NOT_INHERIT_SECRET', 'private-customer-credential')
    package = home / 'plugins/cache/devsecops-local/devsecops/0.1.0'
    package.mkdir(parents=True)
    (package / '.codex-plugin').mkdir()
    (package / '.codex-plugin/plugin.json').write_bytes(_canonical({'name': 'devsecops', 'version': '0.1.0'}))
    for workflow in [*driver.WORKFLOWS, 'run-software-factory-demo']:
        path = package / 'skills' / workflow / 'SKILL.md'
        path.parent.mkdir(parents=True)
        path.write_text('---\nname: ' + workflow + '\n---\nExplicit synthetic test skill.\n')
    (package / 'scripts').mkdir()
    for script in ('devsecops_review.py', 'factory_demo.py'):
        (package / 'scripts' / script).write_text('# Controlled peer does not execute this fixture.\n')
    (package / 'schemas').mkdir()
    (package / 'schemas/native-result.schema.json').write_bytes(
        (Path(__file__).resolve().parents[1] / 'schemas/native-result.schema.json').read_bytes())
    def manifest():
        raw = ''.join(f'{_sha(p.read_bytes())}  {p.relative_to(package)}\n'
                      for p in sorted(package.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS').encode()
        (package / 'SHA256SUMS').write_bytes(raw)
        return _sha(raw)
    digest = manifest()
    root = tmp_path / 'run'
    root.mkdir(mode=0o700)
    work = root / 'step/output'
    work.mkdir(mode=0o700, parents=True)
    (root / 'input.json').write_bytes(b'{"controlled":true}\n')
    now = datetime.now(timezone.utc)
    request = {'schema_version': 1, 'request_id': str(uuid4()), 'run_id': str(uuid4()), 'step_id': 'review',
               'workflow': 'review-kubernetes-change', 'required_artifact_kind': 'review-report',
               'created_at': now.isoformat(), 'expires_at': (now + timedelta(minutes=10)).isoformat(),
               'identities': {key: digest if key == 'package_sha256' else 'a' * 64
                              for key in ('scenario_sha256', 'assertions_sha256', 'package_sha256',
                                          'lock_sha256', 'config_sha256', 'environment_sha256')},
               'input_artifacts': [{'name': 'evidence', 'path': 'input.json',
                                   'sha256': _sha((root / 'input.json').read_bytes()),
                                   'bytes': (root / 'input.json').stat().st_size}],
               'result_path': 'step/result.json', 'output_directory': 'step/output'}
    request_path = root / 'step/request.json'
    request_path.write_bytes(_canonical(request))
    binary = tmp_path / 'controlled-codex-peer'
    settings = tmp_path / 'peer-settings.json'
    settings.write_text('{}')
    binary.write_text(f'#!{sys.executable}\n' + f'SETTINGS = {str(settings)!r}\n' + r'''
import hashlib, json, os, pathlib, subprocess, sys, time
settings = json.loads(pathlib.Path(SETTINGS).read_text())
args = sys.argv[1:]
if args == ['--version']:
    print(settings.get('version', 'codex-cli 0.167.0-alpha.3.2'))
    raise SystemExit()
if args[:2] == ['plugin', 'list']:
    print(json.dumps({'installed': [{'pluginId': 'devsecops@devsecops-local', 'version': '0.1.0',
        'installed': True, 'enabled': settings.get('enabled', True)},
        {'pluginId': 'other@other-local', 'version': '1.0.0', 'installed': True, 'enabled': True}],
        'available': []}))
    raise SystemExit()
if args[:2] == ['mcp', 'list']:
    print(json.dumps([{'name': 'sensitive-mcp', 'env': {'TOKEN': 'unretained-preflight-secret'},
        'enabled': settings.get('mcp_stays_enabled', False) or not any(x.startswith('mcp_servers=') for x in args),
        'transport': {'type': 'stdio'}}]))
    raise SystemExit()
if args[0] in ('mcp-server', 'app-server'):
    assert '--strict-config' in args and sys.stdin.read() == ''
    if settings.get('config_failure'):
        print('private-configuration-diagnostic', file=sys.stderr)
        raise SystemExit(1)
    if args[0] == 'app-server':
        assert '--print-startup-config' in args
        print(json.dumps({'appServerVersion': settings.get('startup_version', '0.167.0-alpha.3.2'),
                          'configRequirements': {'requirements': {'controlled_test_only': True}}}))
    raise SystemExit()
assert args[0] == 'exec'
assert 'MUST_NOT_INHERIT_SECRET' not in os.environ
assert os.environ.get('CODEX_HOME')
prompt = sys.stdin.read()
request = json.loads(prompt.splitlines()[-1])
request_sha = next(x.split(': ', 1)[1] for x in prompt.splitlines() if x.startswith('request_sha256: '))
def emit(value):
    print(json.dumps(value), flush=True)
emit({'type': 'thread.started', 'thread_id': '00000000-0000-4000-a000-000000000001'})
for item in settings.get('startup_items', []):
    emit(item)
emit({'type': 'turn.started'})
mode = settings.get('mode')
if mode in ('timeout', 'cancel'):
    helper = 'import time; time.sleep(120)'
    if settings.get('helper_lifetime_lock'):
        helper = (
            'import fcntl,json,os,pathlib,time; '
            f'fd=os.open({settings["helper_lifetime_lock"]!r},os.O_RDWR|os.O_NOFOLLOW); '
            'fcntl.flock(fd,fcntl.LOCK_EX); st=os.fstat(fd); '
            f'pathlib.Path({settings["helper_acquired"]!r}).write_text(json.dumps('
            '{"pid":os.getpid(),"device":st.st_dev,"inode":st.st_ino})); '
            'time.sleep(120)'
        )
    child = subprocess.Popen([sys.executable, '-c', helper])
    pathlib.Path('child.pid').write_text(str(child.pid))
    time.sleep(120)
if mode == 'overflow':
    sys.stdout.write('x' * 200000)
    sys.stdout.flush()
    time.sleep(120)
if mode == 'nonzero':
    print('private-child-diagnostic', file=sys.stderr)
    raise SystemExit(5)
if mode == 'mcp':
    emit({'type': 'item.completed', 'item': {'type': 'mcp_tool_call', 'id': '1'}})
path = pathlib.Path('actual-controlled-artifact.json')
path.write_bytes(b'{"controlled_test_only":true}\n')
artifact = {'name': 'report', 'kind': request['required_artifact_kind'],
    'path': request['output_directory'] + '/' + path.name,
    'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
result = {'schema_version': 1, 'request_id': request['request_id'], 'run_id': request['run_id'],
    'step_id': request['step_id'], 'request_sha256': request_sha,
    'status': 'completed', 'artifacts': [artifact], 'gaps': []}
if mode == 'incomplete':
    result.update(status='incomplete', gaps=['Required input unavailable'])
if mode == 'outside':
    artifact['path'] = 'input.json'
if mode == 'hash':
    artifact['sha256'] = 'e' * 64
if mode == 'mismatch':
    result['request_sha256'] = 'f' * 64
if mode == 'duplicate':
    result['artifacts'].append(dict(artifact))
if mode == 'mutate-input':
    pathlib.Path(settings['input']).write_text('changed')
if mode == 'mutate-package':
    pathlib.Path(settings['package']).write_text('changed')
if mode == 'result-exists':
    pathlib.Path(settings['result']).write_text('preexisting')
if mode == 'symlink-artifact':
    path.unlink()
    path.symlink_to(settings['input'])
if mode == 'model-provenance':
    result['model'] = 'forged-model-identity'
if mode == 'turn-failed':
    emit({'type': 'turn.failed', 'error': {'message': 'private-child-error'}})
if mode == 'two-turns':
    emit({'type': 'turn.started'})
if mode == 'malformed':
    print('not json', flush=True)
emit({'type': 'item.completed', 'item': {'type': 'agent_message', 'id': 'result', 'text': json.dumps(result)}})
emit({'type': 'turn.completed', 'usage': {'input_tokens': 1, 'output_tokens': 1}})
''')
    binary.chmod(0o700)
    kwargs = dict(request_path=request_path, run_root=root, request_sha256=_sha(request_path.read_bytes()),
                  installed_root=package, package_sha256=digest, plugin_id='devsecops@devsecops-local',
                  model='exact-test-model', expected_client_version=driver.CLIENT_VERSION,
                  engine_python=sys.executable, reasoning_effort='ultra',
                  executable=binary, output=tmp_path / 'execution', total_timeout=20)
    def update_request():
        request_path.write_bytes(_canonical(request))
        kwargs['request_sha256'] = _sha(request_path.read_bytes())
    def mode(value, **other):
        settings.write_text(json.dumps({'mode': value, **other}))
    return {'args': kwargs, 'root': root, 'work': work, 'request': request, 'update': update_request,
            'settings': settings, 'mode': mode, 'package': package, 'manifest': manifest}


def test_real_process_protocol_and_private_retention(lab):
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'complete'
    assert result['runtime_native_authority'] == 'not-issued'
    assert result['scenario_assertion_status'] == 'not_evaluated'
    assert result['model_observed'] is None and result['turn_id'] is None
    assert result['native_skill_injection_observed'] is False
    assert result['thread_id'] == '00000000-0000-4000-a000-000000000001'
    output = lab['args']['output']
    raw = (lab['root'] / 'step/result.json').read_bytes()
    assert result['result_sha256'] == _sha(raw)
    assert (output / 'native-result.json').read_bytes() == raw
    assert result['trace_sha256'] == _sha((output / 'trace.jsonl').read_bytes())
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.iterdir())
    all_bytes = b''.join(p.read_bytes() for p in output.iterdir())
    assert b'unretained-preflight-secret' not in all_bytes
    assert b'private-customer-credential' not in all_bytes
    invocation = json.loads((output / 'invocation.json').read_bytes())
    argv = invocation['argv']
    assert '--ephemeral' in argv and '--json' in argv and '--strict-config' in argv
    assert argv[argv.index('--model') + 1] == 'exact-test-model'
    assert argv[argv.index('--cd') + 1] == str(lab['work'])
    assert argv[argv.index('--output-schema') + 1] == str(output / 'result-schema.json')
    assert 'allOf' not in json.loads((output / 'result-schema.json').read_bytes())
    assert not any(x in argv for x in ('--ignore-rules', '--ignore-user-config', '--add-dir', '--oss'))
    assert not any('dangerous' in x or 'CODEX_HOME=' in x or 'HOME=' in x for x in argv)
    assert 'mcp_servers={"sensitive-mcp"={enabled=false,command="/usr/bin/false",args=[]}}' in argv
    assert 'plugins={"other@other-local"={enabled=false}}' in argv
    assert 'features.plugins=true' not in argv
    assert 'features.shell_snapshot_v2=false' in argv
    assert 'features.step_model_switching=false' in argv
    assert 'features.memories=false' in argv
    assert invocation['config_preflight']['requests_sent'] == 0
    assert invocation['config_preflight']['decision'] == 'pass'
    assert 'shell_environment_policy.set.TMPDIR=' + json.dumps(str(lab['work'])) in argv
    assert invocation['config_preflight']['argv'][:4] == [argv[0], 'app-server', '--strict-config', '--print-startup-config']
    assert invocation['config_preflight']['scope'] == 'local-and-managed-startup-config'
    assert invocation['config_preflight']['stdout_sha256'] == _sha((output / 'startup-config.json').read_bytes())
    assert 'model_reasoning_effort="ultra"' in argv
    engine = invocation['engine_python']
    assert engine['invocation_path'] == sys.executable
    assert engine['runtime']['executable'] == sys.executable
    assert engine['runtime']['prefix'] == sys.prefix
    assert engine['runtime']['base_prefix'] == sys.base_prefix
    assert {p['module'] for p in engine['runtime']['imports']} == {'jsonschema', 'yaml', 'zstandard'}
    assert all(p['file_sha256'] for p in engine['runtime']['imports'])
    assert 'provider-observed-model-unavailable' not in result['qualification_gaps']
    assert f'[$review-kubernetes-change]({lab["package"]}/skills/review-kubernetes-change/SKILL.md)' in (
        output / 'prompt.txt').read_text()


@pytest.mark.parametrize('mode', ['outside', 'hash', 'mismatch', 'duplicate', 'model-provenance',
                                 'turn-failed', 'two-turns', 'malformed', 'mcp', 'nonzero'])
def test_child_content_cannot_forge_accepted_execution(lab, mode):
    lab['mode'](mode)
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'incomplete'
    assert result['result_sha256'] is None
    assert not (lab['root'] / 'step/result.json').exists()
    assert (lab['args']['output'] / 'trace.jsonl').exists()
    assert 'private-child' not in json.dumps(result)


@pytest.mark.parametrize('mode', ['mutate-input', 'mutate-package', 'result-exists', 'symlink-artifact'])
def test_changed_or_redirected_inputs_and_results_remain_incomplete(lab, mode):
    lab['mode'](mode, input=str(lab['root'] / 'input.json'),
                package=str(lab['package'] / 'skills/review-kubernetes-change/SKILL.md'),
                result=str(lab['root'] / 'step/result.json'))
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'incomplete' and result['result_sha256'] is None
    if mode == 'result-exists':
        assert (lab['root'] / 'step/result.json').read_text() == 'preexisting'


def test_incomplete_native_result_is_retained_without_scenario_pass(lab):
    lab['mode']('incomplete')
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'incomplete'
    assert result['result_sha256']
    assert 'native-workflow-reported-incomplete' in result['gaps']


def startup_item(kind='error', message='Under-development features enabled: executed_tool_call_metadata.'):
    # Exact observed CLI shape, with no dependency on the diagnostic wording.
    return {'type': 'item.completed', 'item': {'id': 'item_0', 'type': kind, 'message': message}}


@pytest.mark.parametrize('kind', ['error', 'notice'])
def test_startup_diagnostic_is_retained_without_replacing_completed_turn(lab, kind):
    event = startup_item(kind, 'An arbitrary startup diagnostic, not a message allowlist.')
    lab['mode'](None, startup_items=[event])
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'complete' and result['result_sha256']
    reference = result['startup_diagnostics']['artifact']
    assert result['startup_diagnostics']['count'] == 1
    raw = (lab['args']['output'] / reference['path']).read_bytes()
    assert reference == {'path': 'startup-diagnostics.json', 'sha256': _sha(raw), 'bytes': len(raw)}
    assert json.loads(raw) == {'schema_version': 1, 'diagnostics': [{'event_index': 1, 'event': event}]}
    manifest = json.loads((lab['args']['output'] / 'manifest.json').read_bytes())
    assert manifest['files'][reference['path']] == {key: reference[key] for key in ('sha256', 'bytes')}
    assert event['item']['message'] not in json.dumps(result)


def test_startup_warning_does_not_hide_real_turn_failure(lab):
    lab['mode']('turn-failed', startup_items=[startup_item()])
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'incomplete' and result['result_sha256'] is None
    assert result['startup_diagnostics']['count'] == 1
    assert not (lab['root'] / 'step/result.json').exists()


def diagnostic_trace(*diagnostics):
    result = {'schema_version': 1, 'request_id': '00000000-0000-4000-a000-000000000001',
        'run_id': '00000000-0000-4000-a000-000000000002', 'step_id': 'unit', 'request_sha256': 'a' * 64,
        'status': 'incomplete', 'artifacts': [], 'gaps': ['Controlled parser input only']}
    return [{'type': 'thread.started', 'thread_id': '00000000-0000-4000-a000-000000000003'},
        *diagnostics, {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'id': 'item_1', 'type': 'agent_message', 'text': json.dumps(result)}},
        {'type': 'turn.completed'}]


@pytest.mark.parametrize('invalid', ['started', 'updated', 'command', 'agent-message', 'mcp', 'extra-command',
    'missing-message', 'nontext-message', 'empty-message', 'invalid-id', 'duplicate', 'before-thread',
    'after-turn', 'global-error', 'missing-turn', 'missing-completion'])
def test_startup_diagnostic_cannot_admit_tool_activity_or_malformed_sequences(invalid):
    event = startup_item()
    if invalid in ('started', 'updated'):
        event['type'] = 'item.' + invalid
    elif invalid in ('command', 'agent-message', 'mcp'):
        event['item']['type'] = {'command': 'command_execution', 'agent-message': 'agent_message', 'mcp': 'mcp_tool_call'}[invalid]
    elif invalid == 'extra-command':
        event['item']['command'] = 'must not execute'
    elif invalid == 'missing-message':
        event['item'].pop('message')
    elif invalid == 'nontext-message':
        event['item']['message'] = {'status': 'unknown'}
    elif invalid == 'empty-message':
        event['item']['message'] = ''
    elif invalid == 'invalid-id':
        event['item']['id'] = []
    elif invalid == 'global-error':
        event = {'type': 'error', 'message': 'Real top-level failure'}
    events = diagnostic_trace(event)
    if invalid == 'duplicate':
        events.insert(2, event)
    elif invalid == 'before-thread':
        events[0], events[1] = events[1], events[0]
    elif invalid == 'after-turn':
        events.append(events.pop(1))
    elif invalid == 'missing-turn':
        events = events[:2]
    elif invalid == 'missing-completion':
        events.pop()
    with pytest.raises(driver.NativeCodexError):
        driver._trace(b''.join(_canonical(item) for item in events))


@pytest.mark.parametrize('bound', ['message', 'count', 'aggregate'])
def test_startup_diagnostic_bounds(bound):
    count = 33 if bound == 'count' else 9 if bound == 'aggregate' else 1
    events = []
    for index in range(count):
        event = startup_item(message='x' * (8193 if bound == 'message' else 8192 if bound == 'aggregate' else 1))
        event['item']['id'] = 'startup_' + str(index)
        events.append(event)
    with pytest.raises(driver.NativeCodexError):
        driver._trace(b''.join(_canonical(item) for item in diagnostic_trace(*events)))


@pytest.mark.parametrize('failure', ['version', 'disabled', 'package-hash', 'missing-manifest',
                                    'extra-file', 'missing-skill', 'request-hash', 'expired', 'input-hash',
                                    'nonempty-output', 'evidence-exists'])
def test_preflight_refuses_before_model_subprocess(lab, failure):
    if failure == 'version':
        lab['settings'].write_text('{"version":"codex-cli 0.144.4"}')
    elif failure == 'disabled':
        lab['settings'].write_text('{"enabled":false}')
    elif failure == 'package-hash':
        lab['args']['package_sha256'] = 'b' * 64
    elif failure == 'missing-manifest':
        (lab['package'] / 'SHA256SUMS').unlink()
    elif failure == 'extra-file':
        (lab['package'] / 'unmanifested').write_text('unexpected')
    elif failure == 'missing-skill':
        (lab['package'] / 'skills/review-kubernetes-change/SKILL.md').unlink()
    elif failure == 'request-hash':
        lab['args']['request_sha256'] = 'c' * 64
    elif failure == 'expired':
        lab['request']['expires_at'] = '2020-01-01T00:00:00+00:00'
        lab['update']()
    elif failure == 'input-hash':
        (lab['root'] / 'input.json').write_text('changed')
    elif failure == 'nonempty-output':
        (lab['work'] / 'old').touch()
    elif failure == 'evidence-exists':
        lab['args']['output'].mkdir()
    with pytest.raises((ValueError, OSError)):
        driver.execute_native_codex(**lab['args'])
    assert not (lab['work'] / 'actual-controlled-artifact.json').exists()


def test_strict_configuration_failure_stops_before_model_and_sanitizes_error(lab):
    lab['settings'].write_text('{"config_failure":true}')
    with pytest.raises(driver.NativeCodexError, match='preflight failed') as error:
        driver.execute_native_codex(**lab['args'])
    assert 'private-configuration-diagnostic' not in str(error.value)
    assert not (lab['work'] / 'actual-controlled-artifact.json').exists()
    assert not lab['args']['output'].exists()


def test_wrong_startup_client_version_stops_before_model(lab):
    lab['settings'].write_text('{"startup_version":"0.164.0-unselected"}')
    with pytest.raises(driver.NativeCodexError, match='startup configuration'):
        driver.execute_native_codex(**lab['args'])
    assert not (lab['work'] / 'actual-controlled-artifact.json').exists()


@pytest.mark.parametrize('mode', ['success', 'mismatch', 'managed-rejection'])
def test_previous_client_preserves_managed_startup_validation(lab, mode):
    version = driver.PREVIOUS_CLIENT_VERSION
    lab['args']['expected_client_version'] = version
    settings = {'version': version, 'startup_version': version.removeprefix('codex-cli ')}
    if mode == 'mismatch':
        settings['startup_version'] = driver.CLIENT_VERSION.removeprefix('codex-cli ')
    elif mode == 'managed-rejection':
        settings['config_failure'] = True
    lab['settings'].write_text(json.dumps(settings))
    if mode != 'success':
        with pytest.raises(driver.NativeCodexError):
            driver.execute_native_codex(**lab['args'])
        assert not (lab['work'] / 'actual-controlled-artifact.json').exists()
        assert not lab['args']['output'].exists()
        return
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'complete'
    invocation = json.loads((lab['args']['output'] / 'invocation.json').read_bytes())
    assert invocation['client_version'] == version
    assert invocation['config_preflight']['scope'] == 'local-and-managed-startup-config'
    assert invocation['config_preflight']['method'] == 'app-server-strict-print-startup-config'
    assert (lab['args']['output'] / 'startup-config.json').is_file()
    assert 'features.shell_snapshot_v2=false' in invocation['argv']
    assert 'features.step_model_switching=false' in invocation['argv']


def test_explicit_legacy_client_keeps_local_only_preflight_scope(lab):
    lab['args']['expected_client_version'] = driver.LEGACY_CLIENT_VERSION
    lab['settings'].write_text(json.dumps({'version': driver.LEGACY_CLIENT_VERSION}))
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'complete'
    invocation = json.loads((lab['args']['output'] / 'invocation.json').read_bytes())
    assert invocation['client_version'] == driver.LEGACY_CLIENT_VERSION
    assert invocation['config_preflight']['scope'] == 'local-config-only'
    assert not (lab['args']['output'] / 'startup-config.json').exists()
    assert 'features.shell_snapshot_v2=false' not in invocation['argv']
    assert 'features.step_model_switching=false' not in invocation['argv']


def test_configuration_preserves_exact_dotted_capability_names():
    config = driver._config([{'pluginId': 'target@local'}, {'pluginId': 'other.plugin@some.source'}],
                            {'server.with.dots': 'stdio', 'server+name': 'streamable_http'}, 'target@local')
    selected = {key: tomllib.loads('value=' + value)['value'] for key, value in
                (item.split('=', 1) for item in config) if key in ('plugins', 'mcp_servers')}
    assert selected == {'plugins': {'other.plugin@some.source': {'enabled': False}},
                        'mcp_servers': {'server.with.dots': {'enabled': False, 'command': '/usr/bin/false', 'args': []},
                                        'server+name': {'enabled': False, 'url': 'https://127.0.0.1:9/disabled'}}}


def test_effectively_enabled_mcp_refuses_before_model(lab):
    lab['settings'].write_text('{"mcp_stays_enabled":true}')
    with pytest.raises(driver.NativeCodexError, match='still enables'):
        driver.execute_native_codex(**lab['args'])
    assert not (lab['work'] / 'actual-controlled-artifact.json').exists()


def test_configuration_preflight_has_independent_total_deadline(monkeypatch, tmp_path):
    now, calls = [100.0], []
    monkeypatch.setattr(driver.time, 'monotonic', lambda: now[0])
    def checked(argv, *, deadline, **kwargs):
        calls.append(argv)
        assert deadline == 130
        driver._remaining(deadline)
        now[0] = 131
        return b'[]'
    monkeypatch.setattr(driver, '_checked_command', checked)
    with pytest.raises(driver.NativeCodexError, match='deadline'):
        driver._config_preflight(Path('/controlled/codex'), [], client_version=driver.CLIENT_VERSION,
                                 model='exact-model', cwd=tmp_path,
                                 deadline=1000, environ={}, cancel=None)
    assert len(calls) == 2


@pytest.mark.skipif(os.environ.get('DEVSECOPS_REAL_CODEX_CONFIG') != '1',
                    reason='Opt-in installed CLI configuration preflight; never invokes a model')
def test_installed_pinned_client_strict_configuration_without_model(tmp_path):
    binary = Path(os.environ.get('DEVSECOPS_REAL_CODEX_BINARY', shutil.which('codex'))).resolve(strict=True)
    version = os.environ.get('DEVSECOPS_REAL_CODEX_VERSION', driver.CLIENT_VERSION)
    deadline = time.monotonic() + 45
    environ = {key: os.environ[key] for key in driver._ENV if key in os.environ}
    args = dict(cwd=tmp_path, deadline=deadline, environ=environ, cancel=None)
    assert version in driver.CLIENT_VERSIONS
    assert driver._checked_command([str(binary), '--version'], **args).decode().strip() == version
    features = driver._checked_command([str(binary), 'features', 'list'], **args)
    names = {line.split()[0] for line in features.decode().splitlines() if line.strip()}
    rows, servers = driver._inventory(binary, **args)
    config = driver._config(rows, servers, 'devsecops@devsecops-local', client_version=version)
    config.append('model_reasoning_effort="ultra"')
    config.append('shell_environment_policy.set.TMPDIR=' + json.dumps(str(tmp_path)))
    assert {item.split('=', 1)[0].removeprefix('features.') for item in config
            if item.startswith('features.')} <= names
    checked = driver._config_preflight(binary, config, client_version=version, model='gpt-6-astra', **args)
    assert checked['decision'] == 'pass' and checked['requests_sent'] == 0
    with pytest.raises(driver.NativeCodexError, match='preflight failed'):
        driver._config_preflight(binary, [*config, 'features.unsupported_native_driver_probe=false'],
                                 client_version=version, model='gpt-6-astra', **args)


@pytest.mark.parametrize('mode', ['timeout', 'cancel', 'overflow'])
def test_limits_stop_and_reap_process_group(lab, mode, monkeypatch):
    import fcntl
    import signal

    lock_path = lab['root'].parent / 'helper-lifetime.lock'
    acquired_path = lab['root'].parent / 'helper-acquired.json'
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    identity = os.fstat(descriptor)
    lab['mode'](mode, helper_lifetime_lock=str(lock_path), helper_acquired=str(acquired_path))
    lab['args']['total_timeout'] = 3 if mode == 'cancel' else 1
    lab['args']['max_trace_bytes'] = 1024
    cancel = threading.Event()
    def cancel_after_acquisition():
        # Observe only this controlled helper's announcement before cancellation.
        # Failure to acquire still cancels boundedly, then fails the assertion.
        deadline = time.monotonic() + 1
        while not acquired_path.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        cancel.set()
    timer = threading.Timer(.2, cancel_after_acquisition) if mode == 'cancel' else None
    original = driver._process
    def after_preflight(argv, **kwargs):
        if 'exec' in argv and timer:
            timer.start()
        return original(argv, **kwargs)
    monkeypatch.setattr(driver, '_process', after_preflight)
    start = time.monotonic()
    released = False
    announced = None
    try:
        result = driver.execute_native_codex(**lab['args'], cancel=cancel)
        assert time.monotonic() - start < 3
        assert result['execution_status'] == 'incomplete'
        assert {'timeout': 'deadline-exceeded', 'cancel': 'cancelled',
                'overflow': 'output-limit-exceeded'}[mode] in result['gaps']
        assert sum((lab['args']['output'] / p).stat().st_size for p in ('trace.jsonl', 'stderr.log')) <= 1024
        if mode in ('timeout', 'cancel'):
            # The child holds this unique lock throughout its fixed sleep and
            # announces acquisition. Reacquisition independently proves release
            # of its own resource without reading the process table.
            announced = json.loads(acquired_path.read_text())
            assert (announced['device'], announced['inode']) == (identity.st_dev, identity.st_ino)
            for _ in range(40):
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    released = True
                    break
                except BlockingIOError:
                    time.sleep(.025)
            assert released, 'Cancelled native peer left its controlled helper holding its lifetime lock'
    finally:
        if timer:
            timer.cancel()
            if timer.ident is not None:
                timer.join(timeout=1.5)
        if not released and acquired_path.exists():
            announced = json.loads(acquired_path.read_text())
            assert (announced['device'], announced['inode']) == (identity.st_dev, identity.st_ino)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # Failure cleanup is limited to this still-lock-owning helper.
                # Once the lock is released, the PID never authorizes a signal.
                try:
                    os.kill(announced['pid'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        os.close(descriptor)


def test_factory_doctor_is_explicitly_only_component_coverage(lab):
    request = lab['request']
    request.update(workflow='run-software-factory-demo', required_artifact_kind='explanation',
                   qualification_mode='factory-doctor-component', profile='bigbang-live')
    request['input_artifacts'][0]['name'] = 'factory-config'
    lab['update']()
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'complete'
    assert result['scope'] == 'factory-doctor-component'
    assert 'canonical-factory-demo-not-exercised' in result['qualification_gaps']
    prompt = (lab['args']['output'] / 'prompt.txt').read_text()
    assert 'Never run e2e' in prompt and 'doctor' in prompt


def test_cli_setup_error_does_not_echo_sensitive_input_or_exception(lab, capsys):
    args = lab['args']
    (lab['root'] / 'step/request.json').write_text('private-malformed-input')
    cli = []
    mapping = {'request_path': 'request', 'executable': 'codex', 'expected_client_version': 'client-version'}
    for key, value in args.items():
        cli += ['--' + mapping.get(key, key.replace('_', '-')), str(value)]
    assert main(cli) == 2
    captured = capsys.readouterr()
    assert 'private-malformed-input' not in captured.err + captured.out
    assert 'Traceback' not in captured.err + captured.out


def test_terminal_publication_after_deadline_is_withdrawn(lab, monkeypatch):
    original = driver._write_private
    offset = [0.0]
    monkeypatch.setattr(driver, 'time', SimpleNamespace(monotonic=lambda: time.monotonic() + offset[0]))
    def delayed(directory, name, raw):
        result = original(directory, name, raw)
        if name == 'manifest.json':
            # Reach successful publication before expiring the controlled clock.
            # A one-second startup budget can instead fail an earlier stage on
            # a busy host and never exercise terminal-success withdrawal.
            assert (lab['root'] / 'step/result.json').is_file()
            assert json.loads((lab['args']['output'] / 'execution.json').read_bytes())['execution_status'] == 'complete'
            offset[0] = 31.0
        return result
    monkeypatch.setattr(driver, '_write_private', delayed)
    lab['args']['total_timeout'] = 30
    with pytest.raises(driver.NativeCodexError, match='terminal publication'):
        driver.execute_native_codex(**lab['args'])
    output = lab['args']['output']
    assert (output / 'trace.jsonl').exists()
    assert not (output / 'manifest.json').exists()
    assert not (output / 'execution.json').exists()
    assert not (lab['root'] / 'step/result.json').exists()


def test_evidence_directory_replacement_cannot_redirect_writes(lab, monkeypatch):
    original = driver._write_private
    output = lab['args']['output']
    moved = output.with_name('held-original-evidence')
    def replaced(directory, name, raw):
        if name == 'trace.jsonl':
            output.rename(moved)
            output.mkdir(mode=0o700)
        return original(directory, name, raw)
    monkeypatch.setattr(driver, '_write_private', replaced)
    with pytest.raises(driver.NativeCodexError, match='terminal publication'):
        driver.execute_native_codex(**lab['args'])
    assert list(output.iterdir()) == []
    assert (moved / 'trace.jsonl').exists()
    assert not (moved / 'manifest.json').exists()
    assert not (lab['root'] / 'step/result.json').exists()


def test_result_directory_replacement_cannot_redirect_publication(lab, monkeypatch):
    original = driver._write_private
    parent = lab['root'] / 'step'
    moved = lab['root'] / 'held-original-step'
    def replaced(directory, name, raw):
        if name == 'trace.jsonl':
            parent.rename(moved)
            parent.mkdir(mode=0o700)
        return original(directory, name, raw)
    monkeypatch.setattr(driver, '_write_private', replaced)
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'incomplete'
    assert not (parent / 'result.json').exists()
    assert not (moved / 'result.json').exists()


def test_cli_sigterm_cancels_without_terminating_parent(lab, monkeypatch, capsys):
    import os
    import signal
    original = driver._process
    def interrupt(argv, **kwargs):
        if 'exec' in argv:
            os.kill(os.getpid(), signal.SIGTERM)
        return original(argv, **kwargs)
    monkeypatch.setattr(driver, '_process', interrupt)
    cli = []
    mapping = {'request_path': 'request', 'executable': 'codex', 'expected_client_version': 'client-version'}
    for key, value in lab['args'].items():
        cli += ['--' + mapping.get(key, key.replace('_', '-')), str(value)]
    assert main(cli) == 130
    assert 'cancelled' in capsys.readouterr().out


def test_wall_clock_expiry_blocks_result_even_before_monotonic_deadline(lab, monkeypatch):
    original = driver._write_private
    expiry = datetime.fromisoformat(lab['request']['expires_at'])
    class AdvancedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return expiry + timedelta(seconds=1)
    def advance_clock(directory, name, raw):
        result = original(directory, name, raw)
        if name == 'native-result.json':
            monkeypatch.setattr(driver, 'datetime', AdvancedClock)
        return result
    monkeypatch.setattr(driver, '_write_private', advance_clock)
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'incomplete'
    assert result['result_sha256'] is None
    assert not (lab['root'] / 'step/result.json').exists()


def test_dependency_interpreter_is_required_before_native_execution(lab):
    lab['args']['engine_python'] = lab['root'] / 'unavailable-python'
    with pytest.raises(driver.NativeCodexError, match='Dependency interpreter'):
        driver.execute_native_codex(**lab['args'])
    assert not (lab['work'] / 'actual-controlled-artifact.json').exists()


@pytest.mark.parametrize('change', ['missing', 'wrong-version'])
def test_zstandard_dependency_must_match_selected_lock_before_model(lab, monkeypatch, change):
    original = driver._checked_command
    def checked(argv, **kwargs):
        raw = original(argv, **kwargs)
        if '-I' in argv and '-B' in argv and 'importlib.metadata' in argv[-1]:
            value = json.loads(raw)
            if change == 'missing':
                value['imports'] = [item for item in value['imports'] if item['module'] != 'zstandard']
            else:
                next(item for item in value['imports'] if item['module'] == 'zstandard')['version'] = '0.24.0'
            return json.dumps(value).encode()
        return raw
    monkeypatch.setattr(driver, '_checked_command', checked)
    with pytest.raises(driver.NativeCodexError):
        driver.execute_native_codex(**lab['args'])
    assert not (lab['work'] / 'actual-controlled-artifact.json').exists()


def test_dependency_identity_changed_during_run_blocks_result(lab, monkeypatch):
    original = driver._engine_identity
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls > 1:
            result['pyvenv_cfg_sha256'] = 'f' * 64
        return result
    monkeypatch.setattr(driver, '_engine_identity', changed)
    result = driver.execute_native_codex(**lab['args'])
    assert result['execution_status'] == 'incomplete'
    assert result['result_sha256'] is None
    assert not (lab['root'] / 'step/result.json').exists()
