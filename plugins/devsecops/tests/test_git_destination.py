"""Actual local issuer objects and scripted HTTP; no service or credential access."""
import base64
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import pickle
import ssl
import time
from types import SimpleNamespace

import pytest

from devsecops.adapters.http import APIError, ReadResponse
from devsecops.factory import git_destination as destination
from devsecops.factory import git_transport
from devsecops.runtime.context import canonical_bytes, verify_seal
from test_deployment_commit import case
from test_git_transport import prepared_case, SCOPE, inventory

__all__ = ['case']


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def packet(raw):
    return f'{len(raw) + 4:04x}'.encode() + raw


def advertisement(base, *, target=None, capabilities=None, tags=False):
    caps = capabilities or b'multi_ack_detailed thin-pack side-band-64k ofs-delta shallow filter object-format=sha1 symref=HEAD:refs/heads/main agent=git/2.43.0'
    rows = [packet(base.encode() + b' HEAD\0' + caps + b'\n'),
            packet(base.encode() + b' refs/heads/main\n')]
    if target:
        rows.append(packet(target.encode() + b' refs/heads/demo-review\n'))
    if tags:
        rows.extend([packet(b'a' * 40 + b' refs/tags/v1\n'), packet(base.encode() + b' refs/tags/v1^{}\n')])
    return packet(b'# service=git-upload-pack\n') + b'0000' + b''.join(rows) + b'0000'


def selection(base):
    return {'schema_version': 1, 'kind': 'mission-git-destination-observation-configuration',
        'origin': 'https://git.example.test', 'project_id': 37, 'repository_path': '/group/deploy.git',
        'base_ref': 'refs/heads/main', 'base_commit': base, 'target_ref': 'refs/heads/demo-review',
        'path_with_namespace': 'group/deploy', 'api_read_credential': {'kind': 'bearer', 'token_env': 'UNIT_API_READ'},
        'git_read_credential': {'kind': 'basic', 'username_env': 'UNIT_GIT_USER', 'password_env': 'UNIT_GIT_READ'},
        'trust': {'mode': 'system'}}


@pytest.fixture
def observed(case, tmp_path, monkeypatch):
    # The proposal is the pre-existing controlled local fixture. Public commit
    # and transport issuers create actual direct objects and audited files.
    transport_args, _, _ = prepared_case(case)
    _, plan = git_transport.prepare_git_transport(**transport_args)
    config = selection(transport_args['deployment_commit'])
    config_path = tmp_path.resolve() / 'destination.json'
    config_path.write_bytes(canonical_bytes(config))
    scope = {key: transport_args[key] for key in SCOPE}
    args = {'transport': plan, **scope, 'config': config_path,
            'expected_config_sha256': sha(config_path.read_bytes()), 'output': tmp_path.resolve() / 'observed'}
    project = canonical_bytes({'id': 37, 'http_url_to_repo': 'https://git.example.test/group/deploy.git',
                              'path_with_namespace': 'group/deploy', 'unselected_url': 'https://never-follow.invalid/'})
    advert = advertisement(config['base_commit'], tags=True)
    replies = [ReadResponse(200, project, 'application/json'), ReadResponse(200, advert, destination._MEDIA)] * 2
    state = SimpleNamespace(args=args, config=config, replies=replies, calls=[], clients=[], hook=None,
        plan=plan, source_case=case, transport_args=transport_args, project=project, advert=advert)
    monkeypatch.setenv('UNIT_API_READ', 'SYNTHETIC_API_TOKEN_CANARY')
    monkeypatch.setenv('UNIT_GIT_USER', 'synthetic-user')
    monkeypatch.setenv('UNIT_GIT_READ', 'SYNTHETIC_GIT_TOKEN_CANARY')

    class ScriptedClient:
        def __init__(self, origin, **kwargs):
            self.origin, self.options = origin, kwargs
            self._authorization = lambda: None
            self.kind = 'api' if len(state.clients) == 0 else 'git'
            self._receipts = []
            state.clients.append(self)

        @property
        def receipts(self):
            return deepcopy(self._receipts)

        def get_bytes(self, path, params=None, *, accept):
            assert time.monotonic() < self._deadline
            index = len(state.calls)
            state.calls.append({'kind': self.kind, 'path': path, 'params': params, 'accept': accept,
                                'authorization': self._authorization()})
            self._receipts.append({'request_index': len(self._receipts) + 1, 'method': 'GET', 'outcome': 'complete'})
            response = state.replies[index]
            if state.hook is not None:
                state.hook(index)
            if isinstance(response, Exception):
                raise response
            return response

    monkeypatch.setattr(destination, 'HTTPSReadClient', ScriptedClient)
    return state


def update(observed):
    observed.args['config'].write_bytes(canonical_bytes(observed.config))
    observed.args['expected_config_sha256'] = sha(observed.args['config'].read_bytes())


def test_four_fixed_gets_preserve_scope_and_issue_no_authority(observed):
    before = inventory(observed.source_case['repo'])
    prepared_before = inventory(observed.source_case['args']['output'])
    transport_before = inventory(observed.transport_args['output'])
    report = destination.observe_git_destination(**observed.args)
    assert report['decision'] == 'pass' and report['execution_status'] == 'complete'
    verify_seal(report, 'observation_sha256')
    assert report['factory_decision'] == 'unknown' and report['remote_possession'] == 'unverified'
    assert report['omission_authorized'] is False and report['delivery_authority'] == 'not-issued'
    assert report['atomic_snapshot'] is False
    assert report['selection']['origin'] != report['image_publication']['origin']
    assert report['new_commit'] != report['selection']['base_commit']
    assert report['snapshots'][0] == report['snapshots'][1]
    assert report['snapshots'][0]['advertisement']['target_observed_oid'] is None
    assert len(observed.calls) == 4
    for index, call in enumerate(observed.calls):
        assert call['kind'] == ('api' if index % 2 == 0 else 'git')
        assert call['path'] == ('/api/v4/projects/37' if index % 2 == 0 else '/group/deploy.git/info/refs')
        assert call['params'] == (None if index % 2 == 0 else {'service': 'git-upload-pack'})
        assert call['accept'] == (('application/json',) if index % 2 == 0 else (destination._MEDIA,))
    assert observed.clients[0]._deadline == observed.clients[1]._deadline
    assert all(client.options['max_requests'] == 2 for client in observed.clients)
    assert observed.clients[0].options['max_body_bytes'] == 64 * 1024
    assert observed.clients[1].options['max_body_bytes'] == 2 * 1024 * 1024
    for name, ref in report['retained'].items():
        path = observed.args['output'] / name
        assert sha(path.read_bytes()) == ref['sha256'] and path.stat().st_size == ref['bytes']
        assert path.stat().st_mode & 0o077 == 0
    assert json.loads((observed.args['output'] / 'git-destination.json').read_bytes()) == report
    assert inventory(observed.source_case['repo']) == before
    assert inventory(observed.source_case['args']['output']) == prepared_before
    assert inventory(observed.transport_args['output']) == transport_before


def test_credentials_frozen_separately_and_never_retained(observed, monkeypatch):
    def change_after_first(index):
        if index == 0:
            monkeypatch.setenv('UNIT_API_READ', 'CHANGED_API')
            monkeypatch.setenv('UNIT_GIT_READ', 'CHANGED_GIT')
            monkeypatch.setenv('UNIT_GIT_USER', 'changed-user')
    observed.hook = change_after_first
    report = destination.observe_git_destination(**observed.args)
    expected_git = 'Basic ' + base64.b64encode(b'synthetic-user:SYNTHETIC_GIT_TOKEN_CANARY').decode()
    assert [item['authorization'] for item in observed.calls] == [
        'Bearer SYNTHETIC_API_TOKEN_CANARY', expected_git,
        'Bearer SYNTHETIC_API_TOKEN_CANARY', expected_git]
    assert all(client._authorization() is None for client in observed.clients)
    assert 'CANARY' not in json.dumps(report)
    assert all(b'CANARY' not in path.read_bytes() for path in observed.args['output'].iterdir())


@pytest.mark.parametrize('mutation', ['dict', 'unissued', 'expired', 'pid', 'scope'])
def test_invalid_direct_plan_rejects_before_input_credentials_or_output(observed, monkeypatch, mutation):
    if mutation == 'dict':
        observed.args['transport'] = observed.plan.describe()
    elif mutation == 'unissued':
        observed.args['transport'] = object.__new__(git_transport.PreparedGitTransport)
    elif mutation in ('expired', 'pid'):
        record = git_transport._PLANS[observed.plan]
        record['expires_monotonic' if mutation == 'expired' else 'pid'] = -1
    else:
        observed.args['configuration_sha256'] = '0' * 64
    monkeypatch.setattr(destination, '_read_input', lambda *a: pytest.fail('invalid plan read input'))
    monkeypatch.setattr(destination, '_authorization', lambda *a: pytest.fail('invalid plan read credential'))
    with pytest.raises(ValueError):
        destination.observe_git_destination(**observed.args)
    assert not observed.calls and not observed.args['output'].exists()


def test_transport_plan_cannot_be_serialized(observed):
    with pytest.raises(TypeError):
        pickle.dumps(observed.plan)


@pytest.mark.parametrize('target', ['repository', 'preparation', 'transport', 'alias'])
def test_observer_output_rejects_all_protected_roots(observed, tmp_path, target):
    roots = {'repository': observed.source_case['repo'], 'preparation': observed.source_case['args']['output'],
             'transport': observed.transport_args['output']}
    before = {key: inventory(root) for key, root in roots.items()}
    if target == 'alias':
        root = tmp_path / 'alias'
        root.symlink_to(roots['repository'], target_is_directory=True)
    else:
        root = roots[target]
    observed.args['output'] = root / 'unadmitted/new'
    with pytest.raises((ValueError, OSError)):
        destination.observe_git_destination(**observed.args)
    assert not observed.calls and not observed.args['output'].exists()
    assert all(inventory(root) == before[key] for key, root in roots.items())


@pytest.mark.parametrize('kind', ['wrong-project', 'bool-project', 'wrong-url', 'duplicate-json', 'nan-json',
    'media', 'pagination', 'oversize', 'denied', 'target-drift', 'base-drift', 'project-drift'])
def test_untrusted_or_changed_remote_observations_remain_unknown(observed, kind):
    if kind in ('wrong-project', 'bool-project', 'wrong-url', 'project-drift'):
        value = json.loads(observed.project)
        if kind in ('wrong-project', 'project-drift'):
            value['id'] = 38
        elif kind == 'bool-project':
            value['id'] = True
        else:
            value['http_url_to_repo'] = 'https://never-follow.invalid/repo.git'
        observed.replies[2 if kind == 'project-drift' else 0] = ReadResponse(200, canonical_bytes(value), 'application/json')
    elif kind == 'duplicate-json':
        observed.replies[0] = ReadResponse(200, b'{"id":37,"id":37}', 'application/json')
    elif kind == 'nan-json':
        observed.replies[0] = ReadResponse(200, b'{"id":37,"ignored":NaN}', 'application/json')
    elif kind == 'media':
        observed.replies[1] = replace(observed.replies[1], media_type='text/plain')
    elif kind == 'pagination':
        observed.replies[0] = replace(observed.replies[0], pagination={'x-next-page': '2'})
    elif kind == 'oversize':
        observed.replies[0] = ReadResponse(200, b'x' * (64 * 1024 + 1), 'application/json')
    elif kind == 'denied':
        observed.replies[0] = APIError('denied', 403)
    elif kind == 'target-drift':
        observed.replies[3] = ReadResponse(200, advertisement(observed.config['base_commit'], target='e' * 40, tags=True), destination._MEDIA)
    else:
        observed.replies[3] = ReadResponse(200, advertisement('e' * 40, tags=True), destination._MEDIA)
    report = destination.observe_git_destination(**observed.args)
    assert report['decision'] == 'unknown' and report['execution_status'] == 'partial'
    assert report['gaps'] and report['delivery_authority'] == 'not-issued'
    assert len(observed.calls) <= 4 and all(client._authorization() is None for client in observed.clients)


@pytest.mark.parametrize('mutation', ['config', 'transport-blob', 'retained-blob', 'expired', 'output-route'])
def test_fourth_response_change_prevents_terminal_publication(observed, tmp_path, mutation):
    def change(index):
        if index != 3:
            return
        if mutation == 'config':
            observed.args['config'].write_bytes(observed.args['config'].read_bytes() + b' ')
        elif mutation == 'transport-blob':
            path = observed.transport_args['output'] / 'prepared.pack'
            path.chmod(0o600)
            path.write_bytes(path.read_bytes() + b'changed')
        elif mutation == 'retained-blob':
            path = observed.args['output'] / ('sha256-' + sha(observed.project) + '.blob')
            path.chmod(0o600)
            path.write_bytes(path.read_bytes() + b'changed')
        elif mutation == 'expired':
            git_transport._PLANS[observed.plan]['expires_monotonic'] = 0
        else:
            observed.args['output'].rename(tmp_path / 'moved-output')
            observed.args['output'].symlink_to(observed.source_case['repo'], target_is_directory=True)
    observed.hook = change
    with pytest.raises((ValueError, OSError)):
        destination.observe_git_destination(**observed.args)
    assert not (observed.args['output'] / 'git-destination.json').exists()
    assert not (tmp_path / 'moved-output/git-destination.json').exists()


def test_expiry_during_read_cannot_publish_terminal(observed):
    observed.args['timeout'] = 1
    observed.hook = lambda index: time.sleep(1.05) if index == 0 else None
    with pytest.raises((ValueError, APIError)):
        destination.observe_git_destination(**observed.args)
    assert len(observed.calls) <= 1
    assert not (observed.args['output'] / 'git-destination.json').exists()


def test_context_exit_audit_withdraws_already_written_terminal(observed, monkeypatch):
    original = destination.open_transport_consumer_output
    terminal_seen = []

    @contextmanager
    def mutate_after_return(*args, **kwargs):
        with original(*args, **kwargs) as admitted:
            yield admitted
            terminal = observed.args['output'] / 'git-destination.json'
            terminal_seen.append(terminal.is_file())
            frozen = observed.args['output'] / 'git-destination-config.json'
            frozen.chmod(0o600)
            frozen.write_bytes(frozen.read_bytes() + b'changed after terminal publication')

    monkeypatch.setattr(destination, 'open_transport_consumer_output', mutate_after_return)
    with pytest.raises(ValueError):
        destination.observe_git_destination(**observed.args)
    assert terminal_seen == [True]
    assert len(observed.calls) == 4
    assert not (observed.args['output'] / 'git-destination.json').exists()


def test_aggregate_response_bound_counts_repeated_bodies(observed, monkeypatch):
    maximum = len(observed.project) + len(observed.advert)
    monkeypatch.setattr(destination, 'MAX_TOTAL_BYTES', maximum)
    report = destination.observe_git_destination(**observed.args)
    assert len(observed.calls) == 3 and len(report['responses']) == 2
    assert report['decision'] == 'unknown' and report['execution_status'] == 'partial'
    assert report['gaps'] == ['body-limit']
    assert len([name for name in report['retained'] if name.endswith('.blob')]) == 2
    assert report['remote_possession'] == 'unverified' and report['delivery_authority'] == 'not-issued'


def test_ca_is_loaded_from_pinned_bytes_and_change_after_fourth_read_blocks(observed, tmp_path, monkeypatch):
    ca = tmp_path.resolve() / 'explicit-ca.pem'
    ca.write_bytes(b'synthetic non-secret CA fixture; no TLS handshake')
    observed.config['trust'] = {'mode': 'bundle', 'path': str(ca), 'sha256': sha(ca.read_bytes())}
    update(observed)
    loaded = []
    class FrozenContext:
        def __init__(self, protocol):
            assert protocol == ssl.PROTOCOL_TLS_CLIENT

        def load_verify_locations(self, *, cadata):
            loaded.append(cadata)
    monkeypatch.setattr(destination.ssl, 'SSLContext', FrozenContext)
    def change(index):
        if index == 3:
            ca.write_bytes(b'replaced CA fixture')
    observed.hook = change
    with pytest.raises(ValueError):
        destination.observe_git_destination(**observed.args)
    assert loaded == ['synthetic non-secret CA fixture; no TLS handshake']
    assert all(client._context.minimum_version == ssl.TLSVersion.TLSv1_2 for client in observed.clients)
    assert all('ca_bundle' not in client.options for client in observed.clients)
    assert not (observed.args['output'] / 'git-destination.json').exists()


@pytest.mark.parametrize('mutation', ['extra', 'wrong-hash', 'wrong-base', 'same-ref', 'same-credential', 'redirect-origin'])
def test_closed_configuration_rejects_before_credentials_or_requests(observed, monkeypatch, mutation):
    if mutation == 'extra':
        observed.config['imported_authority'] = True
    elif mutation == 'wrong-base':
        observed.config['base_commit'] = 'e' * 40
    elif mutation == 'same-ref':
        observed.config['target_ref'] = observed.config['base_ref']
    elif mutation == 'same-credential':
        observed.config['git_read_credential'] = deepcopy(observed.config['api_read_credential'])
    elif mutation == 'redirect-origin':
        observed.config['origin'] += '/'
    update(observed)
    if mutation == 'wrong-hash':
        observed.args['expected_config_sha256'] = '0' * 64
    monkeypatch.setattr(destination, '_authorization', lambda *a: pytest.fail('invalid selection read credentials'))
    with pytest.raises(ValueError):
        destination.observe_git_destination(**observed.args)
    assert not observed.calls and not observed.args['output'].exists()


def test_advertisement_supports_head_tags_and_peeled_tag_without_possession_claim():
    config = selection('b' * 40)
    value = destination._advertisement(advertisement(config['base_commit'], tags=True), config, time.monotonic() + 5)
    assert value['refs']['HEAD'] == value['refs']['refs/heads/main'] == 'b' * 40
    assert value['refs']['refs/tags/v1^{}'] == 'b' * 40
    assert value['target_observed_oid'] is None


@pytest.mark.parametrize('mutation', ['truncated', 'trailing', 'delimiter', 'response-end', 'version2',
    'receive-pack', 'duplicate-ref', 'later-nul', 'no-first-nul', 'zero-oid', 'sha256',
    'unknown-capability', 'duplicate-capability', 'bad-symref', 'orphan-peeled', 'branch-peeled',
    'bad-ref', 'uppercase-length', 'short-length'])
def test_advertisement_rejects_unsupported_or_ambiguous_wire_forms(mutation):
    config = selection('b' * 40)
    raw = advertisement(config['base_commit'])
    if mutation == 'truncated':
        raw = raw[:-1]
    elif mutation == 'trailing':
        raw += b'extra'
    elif mutation in ('delimiter', 'response-end'):
        raw = raw[:-4] + (b'0001' if mutation == 'delimiter' else b'0002')
    elif mutation == 'version2':
        raw = packet(b'version 2\n') + b'0000'
    elif mutation == 'receive-pack':
        raw = raw.replace(b'git-upload-pack', b'git-receive-pak')
    elif mutation == 'duplicate-ref':
        raw = raw[:-4] + packet(b'b' * 40 + b' refs/heads/main\n') + b'0000'
    elif mutation == 'later-nul':
        raw = raw[:-4] + packet(b'b' * 40 + b' refs/heads/other\0filter\n') + b'0000'
    elif mutation == 'no-first-nul':
        raw = packet(b'# service=git-upload-pack\n') + b'0000' + packet(b'b' * 40 + b' refs/heads/main\n') + b'0000'
    elif mutation == 'zero-oid':
        raw = advertisement('0' * 40)
    elif mutation in ('sha256', 'unknown-capability', 'duplicate-capability', 'bad-symref'):
        caps = {'sha256': b'object-format=sha256', 'unknown-capability': b'future-mode',
                'duplicate-capability': b'filter filter', 'bad-symref': b'symref=HEAD:refs/heads/missing'}[mutation]
        raw = advertisement(config['base_commit'], capabilities=caps)
    elif mutation in ('orphan-peeled', 'branch-peeled', 'bad-ref'):
        name = {'orphan-peeled': b'refs/tags/missing^{}', 'branch-peeled': b'refs/heads/main^{}',
                'bad-ref': b'refs/heads/../alias'}[mutation]
        raw = raw[:-4] + packet(b'a' * 40 + b' ' + name + b'\n') + b'0000'
    elif mutation == 'uppercase-length':
        raw = raw[:4].upper() + raw[4:]
    else:
        raw = b'0003' + raw[4:]
    with pytest.raises((APIError, ValueError)):
        destination._advertisement(raw, config, time.monotonic() + 5)


def test_advertisement_enforces_ref_count_and_body_bounds(monkeypatch):
    config = selection('b' * 40)
    raw = advertisement(config['base_commit'])
    monkeypatch.setattr(destination, 'MAX_REFS', 1)
    with pytest.raises(APIError, match='limit'):
        destination._advertisement(raw, config, time.monotonic() + 5)
    monkeypatch.setattr(destination, 'MAX_ADVERTISEMENT_BYTES', len(raw) - 1)
    with pytest.raises(APIError, match='limit'):
        destination._advertisement(raw, config, time.monotonic() + 5)
