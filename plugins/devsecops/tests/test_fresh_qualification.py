"""Synthetic terminal-shaped receipt tests; no VM or live qualification."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest

from devsecops.factory import fresh_qualification as fresh
from devsecops.factory import fresh_foundation_recipe as recipes
from devsecops.factory.qualification import QualificationError
from devsecops.factory.state import IDENTITY_KEYS, _canonical, _sha


@pytest.fixture
def evidence(tmp_path):
    def pin(name, size=1, sha='a' * 64):
        return {'path': '/inputs/' + name, 'bytes': size, 'sha256': sha}
    transport = {name: {'path': '/transport/' + name, 'sha256': 'b' * 64}
                 for name in ('config', 'known_hosts', 'certificate', 'identity', 'proxy')}
    transport.update(identity_agent='/transport/agent', host_key_sha256='c' * 64,
        verification_basis='operator-verified', agent_protocol='native')
    recipe = {'kind': 'fresh-owned-tcg-v1', 'host': 'controlled-host',
        'remote_parent': '/home/demo/.local/share/devsecops/fresh', 'private_state_directory': '/tmp/fresh-private',
        'tool_image': 'sha256:' + 'd' * 64, 'boot_image': pin('boot', sha=recipes.BOOT_SHA256),
        'ssh_pki': {name: pin(name) for name in recipes.PKI_NAMES},
        'rke2_assets': {name: pin(name, size, sha) for name, (size, sha) in recipes.ASSETS.items()},
        'transport': transport, 'cleanup': 'remove-owned-generation-preserve-inputs'}
    recipes.validate(recipe)
    modules = {name: 'e' * 64 for name in fresh.MODULES}
    package = ''.join(f'{pin}  src/devsecops/factory/{name}.py\n' for name, pin in sorted(modules.items())).encode()
    docs = {'inputs/config.json': {'runtime': {'foundation': recipe}},
            'inputs/lock.json': {'platform': {'distribution_version': recipes.VERSION}},
            'inputs/package': package}
    identities = {name: 'f' * 64 for name in IDENTITY_KEYS}
    identities.update(package_sha256=_sha(package), config_sha256=_sha(_canonical(docs['inputs/config.json'])),
                      lock_sha256=_sha(_canonical(docs['inputs/lock.json'])))
    run = str(uuid4())
    binding = recipes.binding(recipe, run_id=run, environment_id='demo', identities=identities, modules=modules)
    moment = datetime(2026, 10, 30, tzinfo=timezone.utc)
    def at(seconds):
        return (moment + timedelta(seconds=seconds)).isoformat()
    fingerprint = {'namespaces': {'default': str(uuid4()), 'kube-system': str(uuid4())},
                   'nodes': {'controlled-node': str(uuid4())}, 'ca_sha256': 'a' * 64}
    root = {'device': 1, 'inode': 20}
    disk = {'exclusive_generation': root, 'initial_source_sha256': recipes.BOOT_SHA256,
            'helper_id': 'b' * 64, 'helper_exit_code': 0, 'disk_identity': {'device': 1, 'inode': 30},
            'virtual_bytes': 171798691840}
    guest = {'before_install': 'rke2-config-and-data-absent', 'seed_sha256': 'c' * 64}
    assets = {name: {**value, 'device': 1, 'inode': 100 + index} for index, (name, value) in enumerate(
        {'boot': recipe['boot_image'], **recipe['rke2_assets'], **recipe['ssh_pki']}.items())}
    origin = {'kind': 'fresh-created', 'generation': run, 'binding': binding, 'root_identity': root,
        'up_operation_id': 'up-operation', 'root_disk_creation': disk, 'guest_freshness': guest,
        'cluster_fingerprint': fingerprint, 'source_assets': assets,
        'api_ca_basis': 'issued-by-observed-new-guest', 'ssh_pki_basis': 'operator-supplied-pins-with-run-created-copies'}
    rows = [{'kind': name, 'state': 'absent', 'disposition': 'remove'} for name in ('network', 'seed', 'runtime')]
    rows += [{'kind': 'disposable-disk-seed-pki', 'identity': disk['disk_identity'], 'state': 'absent', 'disposition': 'remove'},
             {'kind': 'supplied-assets', 'identity': assets, 'state': 'preserved', 'disposition': 'preserve'},
             {'kind': 'supplied-tool-image', 'identity': recipe['tool_image'], 'state': 'preserved', 'disposition': 'preserve'}]
    routes = ('/readyz', '/version', '/api/v1/nodes', '/api/v1/namespaces/default', '/api/v1/namespaces/kube-system', '/api')
    observations = [{'ready': True, 'fingerprint': fingerprint, 'tls_hostname_and_ca_verified': True,
        'authenticated_api_read': '/api', 'server_version': recipes.VERSION, 'observed_at': at(5 + i),
        'transport': [{'status': 200, 'method': 'GET', 'request_index': n, 'request_sha256': _sha(route.encode()),
                       'outcome': 'complete', 'body_sha256': 'd' * 64, 'body_bytes': 1}
                      for n, route in enumerate(routes, 1)]} for i in range(3)]
    for action, phase, second in (('up', 'provision', 10), ('down', 'cleanup', 40)):
        complete = action == 'down'
        common = {'run_id': run, 'operation_id': action + '-operation', 'binding': binding,
            'generation': run, 'up_operation_id': 'up-operation', 'root_identity': root, 'root_disk_creation': disk,
            'guest_freshness': guest, 'source_assets': assets, 'unresolved_operations': [], 'complete_cleanup': complete,
            'state': 'ready' if action == 'up' else 'removed'}
        actual = {**common, 'source_assets_preserved': True, 'tool_image_preserved': True,
                  'api_observations': observations if action == 'up' else [], 'resources': rows}
        report = {'schema_version': 1, 'kind': 'owned-fresh-rke2-foundation', 'action': action,
            'run_id': run, 'operation_id': action + '-operation', 'phase_id': phase, 'identities': identities,
            'execution_mode': 'operator', 'package': {'sha256': identities['package_sha256'], 'files': len(modules)},
            'binding_sha256': binding, 'foundation_readiness': 'fresh-rke2-ready' if action == 'up' else 'unknown',
            'origin': origin, 'cluster_fingerprint': fingerprint, 'complete_cleanup': complete,
            'unresolved_operations': [], 'mutation_effect': 'applied', 'coverage': 'fresh-rke2-foundation-only',
            'finished_at': at(second), 'resources': rows + [
                {'kind': name, 'state': 'absent', 'disposition': 'remove'}
                for name in ('local-api-tunnel', 'local-issued-credential-copies')]}
        intent = {'kind': 'fresh-foundation-intent', 'run_id': run, 'operation_id': action + '-operation',
                  'action': action, 'binding': binding, 'identities': identities, 'modules': modules}
        docs.update({f'fresh-{action}/foundation.json': report, f'fresh-{action}/intent.json': intent,
                     f'fresh-{action}/remote-observation.json': actual})
    result = {'run_id': run, 'identities': identities, 'environment_id': 'demo',
              'finalized_at': at(50), 'cleanup': {'environment_released': True}, 'phase_records': []}
    docs['state.json'] = {'lifecycle': {'inputs': {name: {'path': 'inputs/' + filename}
                            for name, filename in (('config', 'config.json'), ('lock', 'lock.json'), ('package', 'package'))}}}
    def write(*, numeric=False):
        refs = {}
        for index, (name, value) in enumerate(docs.items()):
            data = value if type(value) is bytes else _canonical(value)
            registered = ('phase-' + name.split('/')[0] + '/' + str(index)) if numeric and name.startswith('fresh-') else name
            path = tmp_path / registered
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            refs[name] = {'path': registered, 'sha256': _sha(data), 'bytes': len(data)}
        result['phase_records'] = [{'phase_id': phase, 'outcome': 'completed', 'started_at': at(start),
            'operation': action,
            'finished_at': at(end), 'artifacts': [ref for name, ref in refs.items() if name.startswith('fresh-' + action + '/')]}
            for phase, action, start, end in (('provision', 'up', 0, 20), ('cleanup', 'down', 30, 45))]
        selected = [ref['path'] for name, ref in refs.items() if name.startswith('fresh-')]
        return fresh.verify_fresh_foundation(tmp_path / 'e2e-result.json', result, {'files': list(refs.values())}, selected)
    return docs, result, write


def test_complete_receipt_shape_is_consistent_but_does_not_create_runtime_authority(evidence):
    _, result, check = evidence
    proof = check()
    assert proof['status'] == 'pass' and proof['run_id'] == result['run_id']
    assert proof['basis'] == 'terminal-bound-producer-and-independent-api-receipts'


def test_numeric_journal_artifact_copies_resolve_by_kind_and_exact_phase(evidence):
    _, _, check = evidence
    proof = check(numeric=True)
    assert proof['status'] == 'pass' and proof['creation_receipt'].startswith('phase-fresh-up/')


@pytest.mark.parametrize('change', [
    lambda d: d['fresh-up/foundation.json'].update(execution_mode='controlled-test'),
    lambda d: d['fresh-up/foundation.json'].update(origin=None),
    lambda d: d['fresh-up/remote-observation.json']['api_observations'][0].update(tls_hostname_and_ca_verified=False),
    lambda d: d['fresh-up/remote-observation.json']['api_observations'][0]['transport'][0].update(status=403),
    lambda d: d['fresh-up/remote-observation.json']['api_observations'][0]['transport'][0].update(request_sha256='f' * 64),
    lambda d: d['fresh-down/remote-observation.json'].update(source_assets_preserved=False),
    lambda d: d['fresh-down/foundation.json']['resources'][-1].update(state='present'),
    lambda d: d['fresh-up/intent.json'].update(operation_id='other'),
    lambda d: d['fresh-down/remote-observation.json'].update(up_operation_id='another-generation'),
    lambda d: d['fresh-up/foundation.json']['origin']['guest_freshness'].update(before_install='retained-existing-rke2'),
])
def test_controlled_or_incomplete_origin_never_becomes_fresh(evidence, change):
    docs, _, check = evidence
    change(docs)
    with pytest.raises((QualificationError, ValueError)):
        check()


def test_metadata_only_and_never_dispatched_cleanup_cannot_qualify(evidence):
    docs, _, check = evidence
    docs['fresh-up/foundation.json']['foundation_readiness'] = 'unknown'
    assert check()['status'] == 'incomplete'


def test_two_fresh_roles_require_different_actual_disk_generation_cluster_and_ca(evidence):
    _, _, check = evidence
    first = check()
    second = deepcopy(first)
    assert not fresh.distinct_fresh_runs([first, second])
    second.update(generation=str(uuid4()), root_identity={'device': 1, 'inode': 21},
                  disk_identity={'device': 1, 'inode': 31}, cluster_uid=str(uuid4()))
    second['cluster_fingerprint']['ca_sha256'] = 'b' * 64
    second['cluster_fingerprint']['namespaces']['kube-system'] = second['cluster_uid']
    second['cluster_fingerprint']['nodes'] = {'second': str(uuid4())}
    assert fresh.distinct_fresh_runs([first, second])


def _add_renewal(evidence):
    docs, result, _ = evidence
    up, down = docs['fresh-up/foundation.json'], docs['fresh-down/foundation.json']
    recipe = docs['inputs/config.json']['runtime']['foundation']
    initial = recipe['transport']['certificate']['sha256']
    chain = {'initial_certificate_sha256': initial, 'current_certificate_sha256': initial,
             'recipe_identity_changed': False, 'renewals': []}
    up['transport_generations'] = deepcopy(chain)
    down['transport_generations'] = chain
    moment = datetime.fromisoformat(up['finished_at'])
    receipt = {'schema_version': 1, 'kind': 'fresh-foundation-certificate-renewal',
        'run_id': result['run_id'], 'operation_id': down['operation_id'], 'up_operation_id': up['operation_id'],
        'binding_sha256': up['binding_sha256'], 'identities': result['identities'],
        'modules': docs['fresh-up/intent.json']['modules'], 'package': up['package'], 'execution_mode': 'operator',
        'previous_certificate_sha256': initial, 'certificate_sha256': '2' * 64, 'initial_certificate_sha256': initial,
        'certificate_grant': {'grant_sha256': '3' * 64, 'identity_sha256': '4' * 64, 'serial': 2,
            'valid_after': int(moment.timestamp()), 'valid_before': int(moment.timestamp()) + 400,
            'algorithm': 'ssh-ed25519-cert-v01@openssh.com'},
        'original_grant_sha256': '3' * 64, 'original_identity_sha256': '4' * 64, 'minimum_validity_seconds': 60,
        'recipe_identity_changed': False, 'owner_binding_changed': False, 'remote_command': 'true',
        'certificate_only_probe': True, 'status': 'authenticated',
        'started_at': (moment + timedelta(seconds=21)).isoformat(),
        'finished_at': (moment + timedelta(seconds=22)).isoformat(),
        'transport': deepcopy(recipe['transport'])}
    receipt['transport']['certificate'] = {'path': '/private/new-client-certificate.pub', 'sha256': '2' * 64}
    receipt['transport']['config'] = {'path': '/private/new-ssh-config', 'sha256': '5' * 64}
    receipt_name = 'fresh-transport-renewal-down-operation-test.json'
    intent_name = 'fresh-transport-renewal-down-operation-test-intent.json'
    def rebind():
        intent = {key: value for key, value in receipt.items() if key not in ('transport', 'finished_at')}
        intent['status'] = 'unobserved'
        docs['fresh-down/' + receipt_name] = receipt
        docs['fresh-down/' + intent_name] = intent
        chain['current_certificate_sha256'] = receipt['certificate_sha256']
        chain['renewals'] = [{'receipt_name': receipt_name, 'intent_name': intent_name,
            'receipt_sha256': _sha(_canonical(receipt)), 'intent_sha256': _sha(_canonical(intent)),
            'operation_id': receipt['operation_id'], 'previous_certificate_sha256': receipt['previous_certificate_sha256'],
            'certificate_sha256': receipt['certificate_sha256']}]
    rebind()
    return receipt, chain, rebind


def test_terminal_bound_renewal_chain_preserves_original_creation_identity(evidence):
    receipt, _, _ = _add_renewal(evidence)
    proof = evidence[2](numeric=True)
    assert proof['status'] == 'pass' and len(proof['transport_renewals']) == 1
    assert proof['transport_renewals'][0]['certificate_sha256'] == receipt['certificate_sha256']


@pytest.mark.parametrize('change', [
    lambda r: r.update(status='rejected-or-unobserved'),
    lambda r: r.update(original_grant_sha256='9' * 64),
    lambda r: r.update(owner_binding_changed=True),
    lambda r: r.update(remote_command='cleanup'),
    lambda r: r['certificate_grant'].update(valid_before=0),
    lambda r: r['transport'].update(host_key_sha256='9' * 64),
    lambda r: r.update(started_at='2026-10-30T00:00:01+00:00'),
])
def test_renewal_cannot_change_authority_hide_failure_or_escape_phase(evidence, change):
    receipt, _, rebind = _add_renewal(evidence)
    change(receipt)
    rebind()
    with pytest.raises(QualificationError, match='renewal'):
        evidence[2](numeric=True)


def test_renewal_references_must_resolve_to_registered_bytes(evidence):
    _, chain, _ = _add_renewal(evidence)
    chain['renewals'][0]['receipt_sha256'] = '9' * 64
    with pytest.raises(QualificationError, match='absent'):
        evidence[2]()


def test_renewal_chain_cannot_reuse_or_skip_a_certificate(evidence):
    _, chain, _ = _add_renewal(evidence)
    chain['renewals'].append(deepcopy(chain['renewals'][0]))
    with pytest.raises(QualificationError, match='duplicated'):
        evidence[2]()


def test_terminal_current_certificate_cannot_silently_change(evidence):
    _, chain, _ = _add_renewal(evidence)
    chain['current_certificate_sha256'] = '9' * 64
    with pytest.raises(QualificationError, match='current certificate'):
        evidence[2]()
