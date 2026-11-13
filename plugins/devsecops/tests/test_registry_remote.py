"""Execute fixed driver against an explicit controlled Docker command model."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import pytest

from devsecops.factory import registry_remote as remote, service_recipe as recipes, tcg
from test_owned_registry import recipe


class Docker:
    def __init__(self, selected, root, payload):
        self.recipe, self.root, self.payload = selected, root, payload
        self.resources, self.calls, self.fault = {}, [], None
        self.pending_create = None
        self.images = {selected['image']['image_id']: {'Id': selected['image']['image_id'], 'Os': 'linux',
            'Architecture': 'amd64', 'RepoDigests': [selected['image']['reference']],
            'Config': {'Entrypoint': [selected['image']['entrypoint']],
                       'User': '0', 'Cmd': ['serve', '/etc/zot/config.json']}},
            selected['tool_image']: {'Id': selected['tool_image'], 'Os': 'linux', 'Architecture': 'amd64'}}

    def run(self, argv, deadline, check=True):
        assert time.monotonic() < deadline
        self.calls.append(argv)
        assert argv[0] == 'docker'
        code, out, err = 0, b'', b''
        def option(name, default=None):
            return argv[argv.index(name)+1] if name in argv else default
        def multi(name):
            return [argv[index+1] for index, value in enumerate(argv) if value == name]
        def lookup(value):
            return next((item for key, item in self.resources.items() if key == value or item['Name'].removeprefix('/') == value), None)
        if argv[1:3] in (['network', 'inspect'], ['container', 'inspect'], ['image', 'inspect']):
            item = self.images.get(argv[3]) if argv[1] == 'image' else lookup(argv[3])
            if item is None:
                code, err = 1, b'Error: No such object'
            else:
                out = json.dumps([item]).encode()
        elif argv[1:3] in (['network', 'ls'], ['container', 'ls']):
            filters = [item.removeprefix('label=').split('=', 1) for item in multi('--filter')]
            rows = []
            for key, item in self.resources.items():
                if (argv[1] == 'network') != ('Internal' in item):
                    continue
                labels = item['Labels'] if 'Internal' in item else item['Config']['Labels']
                if all(labels.get(key) == value for key, value in filters):
                    rows.append(item['Id'])
            out = ('\n'.join(rows)+'\n').encode()
        elif argv[1:3] == ['network', 'create']:
            key = '1'*64
            self.resources[key] = {'Id': key, 'Name': argv[-1], 'Internal': True, 'Driver': 'bridge', 'Containers': {},
                'Labels': dict(item.split('=', 1) for item in multi('--label'))}
            if self.fault == 'network-create-pending':
                self.pending_create = self.resources.pop(key)
                raise tcg.TCGError('Controlled daemon has not completed network creation')
            out = (key+'\n').encode()
        elif argv[1] == 'create':
            role = dict(item.split('=', 1) for item in multi('--label'))['io.devsecops.registry.role']
            key = ('2' if role == 'registry' else '3')*64
            selected_image = self.recipe['image']['image_id'] if role == 'registry' else self.recipe['tool_image']
            network = lookup(option('--network'))
            mounts = []
            for arg in multi('--mount'):
                parts = dict(item.split('=', 1) for item in arg.split(',') if '=' in item)
                mounts.append({'Source': parts['src'], 'Destination': parts['dst'], 'RW': 'readonly' not in arg})
            self.resources[key] = {'Id': key, 'Name': '/'+option('--name'), 'Image': selected_image,
                'State': {'Running': False, 'Status': 'created'}, 'Mounts': mounts,
                'Config': {'Labels': dict(item.split('=', 1) for item in multi('--label')),
                    'User': option('--user'), 'Entrypoint': [option('--entrypoint')],
                    'Healthcheck': {'Test': ['NONE']},
                    'Cmd': argv[argv.index(selected_image)+1:]},
                'HostConfig': {'Privileged': False, 'ReadonlyRootfs': '--read-only' in argv,
                    'CapAdd': None, 'CapDrop': multi('--cap-drop'), 'SecurityOpt': multi('--security-opt'),
                    'PortBindings': {}, 'PidMode': '', 'IpcMode': 'private', 'Devices': [],
                    'NetworkMode': option('--network'), 'Binds': None, 'PidsLimit': int(option('--pids-limit')),
                    'Memory': int(option('--memory')), 'MemorySwap': int(option('--memory-swap')),
                    'NanoCpus': 10**9, 'RestartPolicy': {'Name': 'no'}},
                'NetworkSettings': {'Networks': {network['Name']: {'NetworkID': ''}}}}
            if self.fault == 'create-disconnect':
                raise tcg.TCGError('Controlled create lost its acknowledgement')
            if self.fault == 'container-create-pending':
                self.pending_create = self.resources.pop(key)
                raise tcg.TCGError('Controlled daemon has not completed container creation')
            out = (key+'\n').encode()
        elif argv[1] == 'start':
            item = lookup(argv[2])
            item['State'] = {'Running': True, 'Status': 'running'}
            net = lookup(item['HostConfig']['NetworkMode'])
            item['NetworkSettings']['Networks'][net['Name']]['NetworkID'] = net['Id']
            net['Containers'][item['Id']] = {}
            out = (item['Id']+'\n').encode()
        elif argv[1] == 'wait':
            item = lookup(argv[2])
            item['State'] = {'Running': False, 'Status': 'exited'}
            record = {'kind': 'registry-storage-qualification-only', 'binding_sha256': self.payload['binding'],
                'origin': self.recipe['origin'], 'repository': self.recipe['repository']+'/qualification',
                'status': 'unknown' if self.fault == 'qualification' else 'pass', 'canonical_success': False}
            tcg._write(self.root/'probe-output/qualification.json', recipes.canonical(record))
            out = b'0\n'
        elif argv[1] == 'stop':
            item = lookup(argv[-1])
            item['State'] = {'Running': False, 'Status': 'exited'}
            out = (item['Id']+'\n').encode()
        elif argv[1] == 'rm':
            key = argv[2]
            for item in self.resources.values():
                if 'Containers' in item:
                    item['Containers'].pop(key, None)
            self.resources.pop(key)
            out = (key+'\n').encode()
        elif argv[1:3] == ['network', 'rm']:
            self.resources.pop(argv[3])
            out = (argv[3]+'\n').encode()
        else:
            raise AssertionError('Unmodeled Docker command: '+str(argv))
        if check and code:
            raise tcg.TCGError('Controlled command failed')
        return subprocess.CompletedProcess(argv, code, out, err)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    selected = recipe(tmp_path)
    root = tmp_path/'remote'/'demo-test'
    root.parent.mkdir(mode=0o700)
    path = remote.Path
    monkeypatch.setattr(remote, 'Path', lambda value: root if value == selected['remote_directory'] else path(value))
    payload = {'recipe': selected, 'action': 'up', 'operation_id': str(uuid4()), 'run_id': str(uuid4()),
        'identities': {}, 'package_modules': {}, 'timeout': 30,
        'expires_at': (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
    payload['binding'] = recipes.binding(selected, run_id=payload['run_id'], identities={}, package_modules={})
    payload['materials'] = {name: base64.b64encode(Path(ref['path']).read_bytes()).decode() for name, ref in selected['materials'].items()}
    payload['probe_zip'] = base64.b64encode(b'controlled-probe-zip').decode()
    payload['probe_zip_sha256'] = tcg._sha(b'controlled-probe-zip')
    payload['probe_settings'] = {'origin': selected['origin'], 'repository': selected['repository']+'/qualification',
        'binding': payload['binding'], 'expires_at': payload['expires_at'],
        'operator': {'username': 'operator', 'password': 'controlled-secret'},
        'reader': {'username': 'reader', 'password': 'controlled-reader'}}
    docker = Docker(selected, root, payload)
    monkeypatch.setattr(tcg, '_run', docker.run)
    return payload, root, docker


def test_fixed_runtime_create_readonly_reconcile_and_exact_cleanup(runtime):
    payload, root, docker = runtime
    result = remote.execute(payload, tcg, recipes)
    assert result['bootstrap'] == 'container-running' and result['storage_qualification'] == 'pass'
    assert not result['canonical_success'] and result['mutation_effect'] == 'unknown'
    argv = json.dumps(docker.calls)
    assert 'controlled-secret' not in argv and 'pull' not in [call[1] for call in docker.calls]
    assert all('--pull=never' in call and '--no-healthcheck' in call for call in docker.calls if call[1] == 'create')
    offset = len(docker.calls)
    observation = remote.execute({**payload, 'action': 'reconcile'}, tcg, recipes)
    assert observation['bootstrap'] == 'container-running' and observation['storage_qualification'] == 'not-observed'
    assert all(call[2] in ('ls', 'inspect') for call in docker.calls[offset:])
    report = remote.execute({**payload, 'action': 'down', 'operation_id': str(uuid4())}, tcg, recipes)
    assert not docker.resources and report['cleanup'] == 'owned-runtime-absent-private-material-retained'
    assert (root/'data').is_dir() and (root/'probe-input/qualification.json').is_file()


def test_fixed_create_overrides_observed_root_user_and_default_config(runtime):
    payload, _, docker = runtime
    image = docker.images[payload['recipe']['image']['image_id']]
    assert image['Config']['User'] == '0'
    assert image['Config']['Cmd'] == ['serve', '/etc/zot/config.json']
    remote.execute(payload, tcg, recipes)
    registry = docker.resources['2'*64]
    assert registry['Config']['User'] == f'{os.getuid()}:{os.getgid()}'
    assert registry['Config']['User'].split(':')[0] != '0'
    assert registry['Config']['Entrypoint'] == ['/usr/local/bin/zot-linux-amd64-minimal']
    assert registry['Config']['Cmd'] == ['serve', '/config/config.json']


@pytest.mark.parametrize('field', ['getuid', 'geteuid'])
def test_root_operator_is_rejected_before_private_or_docker_writes(runtime, monkeypatch, field):
    payload, root, docker = runtime
    monkeypatch.setattr(remote.os, field, lambda: 0)
    with pytest.raises(ValueError, match='non-root'):
        remote.execute(payload, tcg, recipes)
    assert not root.exists() and not docker.calls


def test_cached_image_with_unobserved_entrypoint_is_rejected_before_docker_mutation(runtime):
    payload, _, docker = runtime
    docker.images[payload['recipe']['image']['image_id']]['Config']['Entrypoint'] = ['/usr/bin/zot']
    with pytest.raises(ValueError, match='already exist'):
        remote.execute(payload, tcg, recipes)
    assert not docker.resources
    assert all(call[2] in ('ls', 'inspect') for call in docker.calls)


def test_lost_create_acknowledgement_cannot_be_retried_and_reconciliation_only_observes(runtime):
    payload, _, docker = runtime
    docker.fault = 'create-disconnect'
    with pytest.raises(tcg.TCGError):
        remote.execute(payload, tcg, recipes)
    creates = sum(call[1] == 'create' for call in docker.calls)
    with pytest.raises(ValueError, match='already attempted'):
        remote.execute(payload, tcg, recipes)
    assert sum(call[1] == 'create' for call in docker.calls) == creates
    report = remote.execute({**payload, 'action': 'reconcile'}, tcg, recipes)
    assert report['inflight']['verb'] == 'create' and report['storage_qualification'] == 'not-observed'
    assert sum(call[1] == 'create' for call in docker.calls) == creates


@pytest.mark.parametrize('role', ['network', 'container'])
def test_late_daemon_create_preserves_intent_until_exact_target_is_observed(runtime, role):
    payload, root, docker = runtime
    docker.fault = role+'-create-pending'
    with pytest.raises(tcg.TCGError, match='not completed'):
        remote.execute(payload, tcg, recipes)
    prior_state = (root/'state.json').read_bytes()
    prior_resources = deepcopy(docker.resources)
    offset = len(docker.calls)
    cleanup = {**payload, 'action': 'down', 'operation_id': str(uuid4())}
    report = remote.execute(cleanup, tcg, recipes)
    assert report['cleanup'] == 'unknown-create-still-inflight'
    assert report['inflight']['operation_id'] == payload['operation_id']
    assert report['mutation_effect'] == 'unknown'
    assert (root/'state.json').read_bytes() == prior_state
    assert docker.resources == prior_resources
    assert all(call[2] in ('ls', 'inspect') for call in docker.calls[offset:])

    # The daemon finishes after cleanup observed absence. A later operation
    # can now reconcile and remove the exact creation, without reissuing it.
    target = docker.pending_create
    docker.resources[target['Id']] = target
    docker.pending_create = None
    observed = remote.execute({**payload, 'action': 'reconcile'}, tcg, recipes)
    assert observed['inflight'] == report['inflight']
    finished = remote.execute(cleanup, tcg, recipes)
    assert finished['cleanup'] == 'owned-runtime-absent-private-material-retained'
    assert finished['inflight'] is None and not docker.resources


@pytest.mark.parametrize('fault', ['wrong-owner', 'extra-member', 'wrong-image', 'privileged', 'new-mount', 'default-network', 'changed-command'])
def test_changed_runtime_identity_or_isolation_prevents_any_cleanup_mutation(runtime, fault):
    payload, _, docker = runtime
    remote.execute(payload, tcg, recipes)
    item = docker.resources['2'*64]
    if fault == 'wrong-owner':
        item['Config']['Labels'][remote.LABEL] = 'f'*64
    elif fault == 'extra-member':
        other = deepcopy(item)
        other['Id'] = '9'*64
        docker.resources[other['Id']] = other
    elif fault == 'wrong-image':
        item['Image'] = 'sha256:'+'e'*64
    elif fault == 'privileged':
        item['HostConfig']['Privileged'] = True
    elif fault == 'new-mount':
        item['Mounts'].append({'Source': '/etc', 'Destination': '/host', 'RW': True})
    elif fault == 'default-network':
        item['HostConfig']['NetworkMode'] = 'host'
    elif fault == 'changed-command':
        item['Config']['Cmd'] = ['serve', '/unapproved/config.json']
    offset = len(docker.calls)
    with pytest.raises(ValueError):
        remote.execute({**payload, 'action': 'down', 'operation_id': str(uuid4())}, tcg, recipes)
    assert not any(call[1] in ('stop', 'rm') or call[1:3] == ['network', 'rm'] for call in docker.calls[offset:])


def test_failed_qualification_never_changes_bootstrap_into_whole_operation_pass(runtime):
    payload, _, docker = runtime
    docker.fault = 'qualification'
    report = remote.execute(payload, tcg, recipes)
    assert report['bootstrap'] == 'container-running' and report['storage_qualification'] == 'unknown'
    assert report['mutation_effect'] == 'unknown' and not report['canonical_success']


def test_expired_operator_window_cannot_create_docker_resources(runtime):
    payload, _, docker = runtime
    payload['expires_at'] = '2020-01-01T00:00:00Z'
    with pytest.raises(ValueError, match='window'):
        remote.execute(payload, tcg, recipes)
    assert not docker.calls


@pytest.mark.parametrize('filename', ['ca.crt', 'qualification.json'])
@pytest.mark.parametrize('boundary', ['probe-create', 'probe-wait'])
def test_changed_mounted_probe_inputs_stop_before_start_or_terminal_report(runtime, monkeypatch, filename, boundary):
    payload, root, docker = runtime
    original = docker.run
    changed = []
    def mutate_after_command(argv, deadline, check=True):
        result = original(argv, deadline, check)
        create_probe = argv[1] == 'create' and payload['recipe']['tool_image'] in argv
        if (boundary == 'probe-create' and create_probe or boundary == 'probe-wait' and argv[1] == 'wait'):
            (root/'probe-input'/filename).write_bytes(b'changed mounted qualification input')
            changed.append(len(docker.calls))
        return result
    monkeypatch.setattr(tcg, '_run', mutate_after_command)
    with pytest.raises(ValueError, match='mounted qualification'):
        remote.execute(payload, tcg, recipes)
    assert len(changed) == 1 and len(docker.calls) == changed[0]
    if boundary == 'probe-create':
        assert not any(call[1] == 'start' and call[-1] == '3'*64 for call in docker.calls)


def test_cleanup_validates_retained_probe_settings_without_secret_payload(runtime):
    payload, root, docker = runtime
    remote.execute(payload, tcg, recipes)
    stored = json.loads((root/'state.json').read_bytes())
    assert stored['probe_settings_sha256'] == tcg._sha((root/'probe-input/qualification.json').read_bytes())
    cleanup = {key: value for key, value in payload.items() if key not in ('materials', 'probe_settings', 'probe_zip')}
    cleanup.update(action='down', operation_id=str(uuid4()))
    report = remote.execute(cleanup, tcg, recipes)
    assert report['cleanup'] == 'owned-runtime-absent-private-material-retained'
    assert not docker.resources


def v2(payload):
    payload['recipe'].update(schema_version=2, kind='owned-zot-v2', cleanup='remove-owned-private-material')
    payload['binding'] = recipes.binding(payload['recipe'], run_id=payload['run_id'], identities={}, package_modules={})
    payload['probe_settings']['binding'] = payload['binding']
    return payload


def test_v2_removes_only_owned_payload_and_preserves_reconciliation_evidence(runtime):
    payload, root, docker = runtime
    v2(payload)
    remote.execute(payload, tcg, recipes)
    cleanup = {**payload, 'action': 'down', 'operation_id': str(uuid4())}
    result = remote.execute(cleanup, tcg, recipes)
    assert result['cleanup'] == 'owned-runtime-and-private-material-absent'
    assert set(path.name for path in root.iterdir()) == {'state.json', 'owner.json', 'lock'}
    assert not docker.resources
    before = len(docker.calls)
    reconciled = remote.execute({**cleanup, 'action': 'reconcile'}, tcg, recipes)
    assert reconciled['cleanup'] == result['cleanup']
    assert all(call[2] in ('ls', 'inspect') for call in docker.calls[before:])
    assert Path(payload['recipe']['materials']['private_key']['path']).exists()


@pytest.mark.parametrize('link_kind', ['symlink', 'hardlink'])
def test_v2_rejects_linked_payload_before_first_unlink(runtime, tmp_path, link_kind):
    payload, root, docker = runtime
    v2(payload)
    remote.execute(payload, tcg, recipes)
    external = tmp_path/'external-secret'
    external.write_bytes(b'preserve-operator-data')
    linked = root/'data'/'foreign'
    if link_kind == 'symlink':
        linked.symlink_to(external)
    else:
        os.link(external, linked)
    with pytest.raises(ValueError, match='linked'):
        remote.execute({**payload, 'action': 'down', 'operation_id': str(uuid4())}, tcg, recipes)
    assert external.read_bytes() == b'preserve-operator-data'
    assert (root/'config/private-key.pem').exists()


def test_v2_interrupted_purge_is_read_only_reconciled_without_repeating(runtime, monkeypatch):
    payload, root, docker = runtime
    v2(payload)
    remote.execute(payload, tcg, recipes)
    cleanup = {**payload, 'action': 'down', 'operation_id': str(uuid4())}
    original = remote._purge_owned_payload
    def lost_reply(*args):
        original(*args)
        raise KeyboardInterrupt()
    monkeypatch.setattr(remote, '_purge_owned_payload', lost_reply)
    with pytest.raises(KeyboardInterrupt):
        remote.execute(cleanup, tcg, recipes)
    before = len(docker.calls)
    result = remote.execute({**cleanup, 'action': 'reconcile'}, tcg, recipes)
    assert result['cleanup'] == 'owned-runtime-and-private-material-absent'
    assert all(call[2] in ('ls', 'inspect') for call in docker.calls[before:])
    with pytest.raises(ValueError, match='already attempted'):
        remote.execute(cleanup, tcg, recipes)
