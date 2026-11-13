"""Real signed TLS registry and full layer bytes; runtime is explicitly controlled."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import pickle
import time

import pytest

from devsecops.factory import base_qualification as adapter
from devsecops.factory.state import IDENTITY_KEYS
from devsecops.runtime.context import verify_seal
from test_attestations import lab
from test_image_layout import install as install_layers
from test_image_policy import make_policy
from test_image_verification import sign_registry_objects
from test_publisher_trust import fixture as publisher_fixture
from test_registry_evidence import registry, ca, install, REPOSITORY, TOKEN, SECRET, encode
import test_registry_evidence as registry_fixture

__all__ = ['registry', 'ca', 'lab']


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def setup(tmp_path, registry, lab, monkeypatch):
    image = install_layers(registry, mutate_config=lambda value: value.update(config={}))
    prefix = '/v2/' + REPOSITORY
    documents = {'index': image['index'],
        'manifest': registry['routes'][prefix + '/manifests/' + image['manifest']]['raw'],
        'config': registry['routes'][prefix + '/blobs/' + image['config']]['raw']}
    documents.update({name + '_type': json.loads(documents[name]).get('mediaType',
        'application/vnd.oci.image.config.v1+json') for name in ('index', 'manifest', 'config')})
    monkeypatch.setattr(registry_fixture, 'chain', lambda: documents)
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    publisher, package, args = publisher_fixture(tmp_path, key=(lab[0] / 'lab.pub').read_bytes())
    scope = {'registry_origin': registry['origin'], 'repository_prefix': 'ironbank/'}
    publisher.update(publisher='controlled-test-publisher', scope=scope)
    package.update(publisher=publisher['publisher'], scope=scope, source_url='https://publisher.example.test/signing')
    args['package_path'].write_bytes(encode(package))
    publisher['source']['package_sha256'] = sha(encode(package))
    args['policy_path'].write_bytes(encode(publisher))
    image_policy = make_policy(registry, data, lab)
    image_path = tmp_path / 'image-policy.json'
    image_path.write_bytes(encode(image_policy))
    return dict(publisher_policy=args['policy_path'], publisher_policy_sha256=sha(encode(publisher)),
        publisher_package=args['package_path'], image_policy=image_path, image_policy_sha256=sha(encode(image_policy)),
        public_key=args['public_key'], cosign=lab[1], wheel=tmp_path / 'unused-wheel', tools=tmp_path / 'tools',
        output=tmp_path / 'f0', runtime_output=Path('/tmp/unused-controlled-runtime'), expected_uid=1000,
        run_scope={name: sha(name.encode()) for name in IDENTITY_KEYS}, token_env='REGISTRY_TEST_TOKEN',
        ca_bundle=registry['ca'], total_timeout=90), image


def controlled_runtime(**kwargs):
    subject, layout = kwargs['subject'], kwargs['layout']
    return {'decision': 'pass', 'execution_mode': 'controlled-test',
        'scope': 'mission-python-runtime-probe', 'run_id': 'controlled-boundary-only',
        'base': {'repository': subject['origin'].removeprefix('https://') + '/' + subject['repository'],
                 'requested_digest': subject['digest'], 'manifest_digest': layout['manifest_digest'],
                 'architecture': subject['architecture']},
        'runtime_inputs': {'layout': layout['sha256']}, 'runtime_proof': {'controlled': True}}


def test_actual_tls_cosign_layers_compose_but_controlled_runtime_cannot_issue(registry, lab, tmp_path, monkeypatch):
    args, image = setup(tmp_path, registry, lab, monkeypatch)
    monkeypatch.setattr(adapter, '_runtime', controlled_runtime)
    result, capability = adapter._qualify(**args, _controlled=True)
    verify_seal(result, 'qualification_sha256')
    assert capability is None and result['base_authority'] == 'not-issued' and result['decision'] == 'unknown'
    assert result['execution_mode'] == 'controlled-test'
    assert result['checks'] == dict.fromkeys(('publisher', 'signed_policy', 'layers', 'runtime'), 'pass')
    assert result['gaps'] == ['controlled-execution-cannot-issue-base-authority']
    assert not result.get('binding')
    # Three individually verified Cosign signatures plus the complete layer are
    # actually acquired; no provider-verdict or acquisition-result injection.
    paths = [request['path'] for request in registry['requests']]
    assert paths.count('/v2/' + REPOSITORY + '/blobs/' + image['layer']) == 1
    assert len(paths) == 16
    assert all(request['authorization'] == 'Bearer ' + TOKEN for request in registry['requests'])
    assert TOKEN not in json.dumps(result) and SECRET not in json.dumps(result)
    assert json.loads((args['output'] / 'base-qualification.json').read_bytes()) == result


@pytest.mark.parametrize('damage', ['unsigned', 'layer', 'predicate', 'revoked-key'])
def test_real_failure_stops_before_runtime(registry, lab, tmp_path, monkeypatch, damage):
    args, image = setup(tmp_path, registry, lab, monkeypatch)
    monkeypatch.setattr(adapter, '_runtime', lambda **_: pytest.fail('Incomplete evidence reached runtime'))
    if damage == 'unsigned':
        registry['routes']['/v2/' + REPOSITORY + '/manifests/sha256-' + image['requested'][7:] + '.sig']['raw'] = b'{}'
    elif damage == 'layer':
        route = registry['routes']['/v2/' + REPOSITORY + '/blobs/' + image['layer']]
        route['raw'] = b'x' * len(route['raw'])
    elif damage == 'predicate':
        policy = json.loads(args['image_policy'].read_bytes())
        policy['predicates'][0]['rules'][0]['equals'] = 'different'
        args['image_policy'].write_bytes(encode(policy))
        args['image_policy_sha256'] = sha(encode(policy))
    else:
        policy = json.loads(args['publisher_policy'].read_bytes())
        policy['revoked_spki_sha256'] = [policy['key']['spki_sha256']]
        args['publisher_policy'].write_bytes(encode(policy))
        args['publisher_policy_sha256'] = sha(encode(policy))
    result, capability = adapter._qualify(**args, _controlled=True)
    assert capability is None and result['base_authority'] == 'not-issued'
    assert result['checks']['runtime'] == 'unknown'
    assert 'base-qualification-incomplete' in result['gaps']
    if damage == 'revoked-key':
        assert not registry['requests']
        assert result['checks']['publisher'] == 'fail'
    else:
        assert result['checks']['publisher'] == 'pass'
        if damage == 'layer':
            assert result['checks']['signed_policy'] == 'pass' and result['checks']['layers'] == 'fail'
        elif damage == 'predicate':
            assert result['checks']['signed_policy'] == 'fail'


def test_public_issuer_rejects_local_publisher_before_credentials(registry, lab, tmp_path, monkeypatch):
    args, _ = setup(tmp_path, registry, lab, monkeypatch)
    with pytest.raises(ValueError, match='Canonical F0'):
        adapter.qualify_mission_base(**args)
    assert not registry['requests'] and not args['output'].exists()


@pytest.mark.parametrize('damage', ['repository', 'requested_digest', 'manifest_digest', 'architecture', 'layout'])
def test_runtime_identity_disagreement_cannot_issue(registry, lab, tmp_path, monkeypatch, damage):
    args, _ = setup(tmp_path, registry, lab, monkeypatch)
    def wrong(**kwargs):
        result = controlled_runtime(**kwargs)
        (result['runtime_inputs'] if damage == 'layout' else result['base'])[damage] = 'wrong'
        return result
    monkeypatch.setattr(adapter, '_runtime', wrong)
    result, capability = adapter._qualify(**args, _controlled=True)
    assert capability is None and result['gaps'] == ['base-qualification-incomplete']
    assert result['checks'] == dict.fromkeys(('publisher', 'signed_policy', 'layers', 'runtime'), 'pass')


def test_selected_policy_identity_changes_rejected_before_output(registry, lab, tmp_path, monkeypatch):
    args, _ = setup(tmp_path, registry, lab, monkeypatch)
    args['image_policy'].write_bytes(args['image_policy'].read_bytes() + b' ')
    with pytest.raises(ValueError, match='policy differs'):
        adapter._qualify(**args, _controlled=True)
    assert not registry['requests'] and not args['output'].exists()


def test_operator_package_freshness_must_survive_runtime(registry, lab, tmp_path, monkeypatch):
    args, _ = setup(tmp_path, registry, lab, monkeypatch)
    policy = json.loads(args['publisher_policy'].read_bytes())
    policy['source']['max_age_seconds'] = 120
    args['publisher_policy'].write_bytes(encode(policy))
    args['publisher_policy_sha256'] = sha(encode(policy))
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=5)
    def elapsed(**kwargs):
        result = controlled_runtime(**kwargs)
        monkeypatch.setattr(adapter, 'datetime', Later)
        return result
    monkeypatch.setattr(adapter, '_runtime', elapsed)
    result, capability = adapter._qualify(**args, _controlled=True)
    assert capability is None and result['gaps'] == ['base-qualification-incomplete']
    assert result['checks'] == dict.fromkeys(('publisher', 'signed_policy', 'layers', 'runtime'), 'pass')


def test_terminal_output_swap_withdraws_the_owned_receipt(registry, lab, tmp_path, monkeypatch):
    args, _ = setup(tmp_path, registry, lab, monkeypatch)
    monkeypatch.setattr(adapter, '_runtime', controlled_runtime)
    original = adapter._write
    moved = tmp_path / 'original-output'
    def changed(directory, name, value):
        result = original(directory, name, value)
        if name == 'base-qualification.json':
            args['output'].rename(moved)
            args['output'].mkdir(mode=0o700)
        return result
    monkeypatch.setattr(adapter, '_write', changed)
    with pytest.raises((ValueError, OSError)):
        adapter._qualify(**args, _controlled=True)
    assert not (moved / 'base-qualification.json').exists()
    assert not (args['output'] / 'base-qualification.json').exists()


def test_saved_verdict_or_new_object_cannot_become_authority():
    with pytest.raises(TypeError):
        adapter.VerifiedBase({'decision': 'pass'})
    for value in ({'decision': 'pass'}, object.__new__(adapter.VerifiedBase), None):
        with pytest.raises(ValueError, match='Unissued'):
            adapter.require_verified_base(value, run_scope={}, repository='any', requested_digest='any', architecture='amd64')


@pytest.mark.parametrize('damage', ['none', 'pid', 'monotonic', 'wall-time', 'run', 'repository', 'digest', 'architecture'])
def test_consumer_admission_is_process_expiry_and_identity_bound(monkeypatch, damage):
    # Unit-test only the consumer guard, without representing this as an issued
    # result from a real runtime or registry. There is no public mint/import API.
    value = object.__new__(adapter.VerifiedBase)
    scope = {name: sha(name.encode()) for name in IDENTITY_KEYS}
    binding = {'run_scope': scope, 'repository': 'registry1.dso.mil/ironbank/example/python',
        'requested_digest': 'sha256:' + '1' * 64, 'architecture': 'amd64',
        'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()}
    adapter._ISSUED[value] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic() + 60,
                             'binding': deepcopy(binding)}
    if damage == 'pid':
        adapter._ISSUED[value]['pid'] += 1
    elif damage == 'monotonic':
        adapter._ISSUED[value]['expires_monotonic'] = 0
    elif damage == 'wall-time':
        adapter._ISSUED[value]['binding']['expires_at'] = '2000-01-01T00:00:00+00:00'
    args = dict(run_scope=scope, repository=binding['repository'], requested_digest=binding['requested_digest'], architecture='amd64')
    if damage in {'run', 'repository', 'digest', 'architecture'}:
        args[{'run': 'run_scope', 'digest': 'requested_digest'}.get(damage, damage)] = 'wrong'
    try:
        if damage != 'none':
            with pytest.raises(ValueError):
                adapter.require_verified_base(value, **args)
        else:
            result = adapter.require_verified_base(value, **args)
            result['run_scope'].clear()
            assert adapter.require_verified_base(value, **args) == binding
            with pytest.raises(TypeError, match='serialized'):
                pickle.dumps(value)
    finally:
        del adapter._ISSUED[value]
