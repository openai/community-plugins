"""Offline policy propagation and fail-closed caller boundaries; no TLS or scanners."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import time

import pytest

from devsecops.adapters import image_layout, image_policy, image_verification, registry_evidence
from devsecops.adapters.registry_download_policy import RedirectPolicyError
from devsecops.adapters.registry_attestation_policy import AttestationPolicyError, load_attestation_size_policy
from devsecops.factory import base_qualification, case_supply_chain, contracts, mission_operation
from devsecops.factory.state import IDENTITY_KEYS, StateError
from test_mission_operation import configuration

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://registry1.dso.mil'
REPOSITORY = 'ironbank/opensource/nginx/nginx'
STORAGE = 'https://ib-prod-harbor-storage.s3.us-gov-west-1.amazonaws.com'


class ReachedBoundary(RuntimeError):
    pass


def selected_policy(tmp_path):
    value = {'schema_version': 1, 'kind': 'registry-blob-download-policy',
        'registry_origin': ORIGIN, 'repositories': [REPOSITORY, 'ironbank/other'],
        'storage_origins': [STORAGE]}
    path = tmp_path / 'download-policy.json'
    path.write_text(json.dumps(value))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def selected_size_policy(tmp_path):
    value = {'schema_version': 1, 'kind': 'registry-attestation-size-policy',
        'origin': ORIGIN, 'repository': REPOSITORY, 'subject_digest': 'sha256:' + '1' * 64,
        'descriptors': [{'digest': 'sha256:' + 'a' * 64, 'size_bytes': 8_318_244}]}
    path = tmp_path / 'attestation-size-policy.json'
    path.write_text(json.dumps(value))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def registry_policy(tmp_path, field):
    return selected_policy(tmp_path) if field == 'blob_download_policy' else selected_size_policy(tmp_path)


def acquisition_args(module, tmp_path):
    args = {'repository': REPOSITORY, 'requested_digest': 'sha256:' + '1' * 64,
        'architecture': 'amd64', 'allowed_repositories': [REPOSITORY],
        'output': tmp_path / 'not-created', 'token_env': 'TEST_REGISTRY_READ'}
    if module is registry_evidence:
        args['required_predicates'] = ['https://example.test/predicate/v1']
    return args


@pytest.mark.parametrize('module', [registry_evidence, image_layout])
@pytest.mark.parametrize('damage', ['missing-hash', 'missing-path', 'changed', 'foreign-origin', 'foreign-repository'])
def test_bad_download_selection_stops_before_session_or_credentials(module, tmp_path, monkeypatch, damage):
    path, sha = selected_policy(tmp_path)
    kwargs = {'blob_download_policy': path, 'blob_download_policy_sha256': sha}
    if damage == 'missing-hash':
        kwargs['blob_download_policy_sha256'] = None
    elif damage == 'missing-path':
        kwargs['blob_download_policy'] = None
    elif damage == 'changed':
        path.write_text('{}')
    else:
        value = json.loads(path.read_bytes())
        value['registry_origin' if damage == 'foreign-origin' else 'repositories'] = (
            'https://other.example' if damage == 'foreign-origin' else ['unselected/repository'])
        path.write_text(json.dumps(value))
        kwargs['blob_download_policy_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(module, '_RegistrySession', lambda *a, **k: pytest.fail('invalid policy reached registry session'))
    call = module.collect_registry_evidence if module is registry_evidence else module.acquire_image_layout
    with pytest.raises(RedirectPolicyError):
        call(ORIGIN, **acquisition_args(module, tmp_path), **kwargs)
    assert not (tmp_path / 'not-created').exists()


@pytest.mark.parametrize('module', [registry_evidence, image_layout])
@pytest.mark.parametrize('selected', [True, False])
def test_acquisition_session_receives_only_selected_scope(module, selected, tmp_path, monkeypatch):
    path, sha = selected_policy(tmp_path)
    def session(*a, **kwargs):
        policy = kwargs['blob_download_policy']
        if selected:
            assert policy.repositories == (REPOSITORY,)
            assert policy.storage_origins == (STORAGE,)
            assert policy.policy_sha256 == sha
        else:
            assert policy is None
        raise ReachedBoundary
    monkeypatch.setattr(module, '_RegistrySession', session)
    call = module.collect_registry_evidence if module is registry_evidence else module.acquire_image_layout
    extra = {'blob_download_policy': path, 'blob_download_policy_sha256': sha} if selected else {}
    with pytest.raises(ReachedBoundary):
        call(ORIGIN, **acquisition_args(module, tmp_path), **extra)
    assert not (tmp_path / 'not-created').exists()


@pytest.mark.parametrize('field', ['blob_download_policy', 'attestation_size_policy'])
def test_verification_forwards_pinned_policy_before_crypto(tmp_path, monkeypatch, field):
    policy, pin = registry_policy(tmp_path, field)
    key = tmp_path / 'key.pem'
    key.write_bytes(b'controlled-key')
    def collect(*a, **kwargs):
        assert kwargs[field] == policy
        assert kwargs[field + '_sha256'] == pin
        raise ReachedBoundary
    monkeypatch.setattr(image_verification, 'collect_registry_evidence', collect)
    with pytest.raises(ReachedBoundary):
        image_verification.verify_registry_image(ORIGIN, **acquisition_args(registry_evidence, tmp_path),
            public_key=key, expected_key_sha256=hashlib.sha256(key.read_bytes()).hexdigest(),
            cosign=tmp_path / 'unused-cosign', **{field: policy, field + '_sha256': pin})


@pytest.mark.parametrize('field', ['blob_download_policy', 'attestation_size_policy'])
def test_image_policy_keeps_selected_pin_through_verification(tmp_path, monkeypatch, field):
    path, pin = registry_policy(tmp_path, field)
    selected = {'subject': {'origin': ORIGIN, 'repository': REPOSITORY,
        'digest': 'sha256:'+'1'*64, 'architecture': 'amd64'},
        'predicates': [{'type': 'https://example.test/predicate'}], 'signer': {'key_sha256': '2'*64}}
    policy_path = tmp_path/'image-policy.json'
    policy_path.write_bytes(b'{}')
    monkeypatch.setattr(image_policy, '_policy', lambda _: selected)
    monkeypatch.setattr(image_policy, '_valid_now', lambda _: True)
    def verify(*args, **kwargs):
        assert args == (ORIGIN,)
        assert kwargs['allowed_repositories'] == [REPOSITORY]
        assert kwargs[field] == path and kwargs[field + '_sha256'] == pin
        raise ReachedBoundary
    monkeypatch.setattr(image_policy, 'verify_registry_image', verify)
    with pytest.raises(ReachedBoundary):
        image_policy.verify_registry_image_policy(policy_path=policy_path,
            expected_policy_sha256=hashlib.sha256(b'{}').hexdigest(), public_key=tmp_path/'unused-key',
            cosign=tmp_path/'unused-cosign', output=tmp_path/'out',
            **{field: path, field + '_sha256': pin})


@pytest.mark.parametrize('damage', ['missing-hash', 'missing-path', 'changed', 'origin', 'repository', 'subject_digest'])
def test_bad_size_selection_stops_before_session_or_credentials(tmp_path, monkeypatch, damage):
    path, sha = selected_size_policy(tmp_path)
    kwargs = {'attestation_size_policy': path, 'attestation_size_policy_sha256': sha}
    if damage == 'missing-hash':
        kwargs['attestation_size_policy_sha256'] = None
    elif damage == 'missing-path':
        kwargs['attestation_size_policy'] = None
    elif damage == 'changed':
        path.write_text('{}')
    else:
        value = json.loads(path.read_bytes())
        value[damage] = {'origin': 'https://other.example', 'repository': 'other/repository',
                         'subject_digest': 'sha256:' + '9' * 64}[damage]
        path.write_text(json.dumps(value))
        kwargs['attestation_size_policy_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(registry_evidence, '_RegistrySession',
                        lambda *a, **k: pytest.fail('invalid size policy reached registry session'))
    with pytest.raises(AttestationPolicyError):
        registry_evidence.collect_registry_evidence(ORIGIN,
            **acquisition_args(registry_evidence, tmp_path), **kwargs)
    assert not (tmp_path / 'not-created').exists()


@pytest.mark.parametrize('field', ['blob_download_policy', 'attestation_size_policy'])
def test_f0_freezes_original_registry_policy_before_publisher_or_registry_work(tmp_path, monkeypatch, field):
    path, pin = registry_policy(tmp_path, field)
    raw = path.read_bytes()
    key = tmp_path / 'key'
    key.write_bytes(b'key')
    key_sha = hashlib.sha256(key.read_bytes()).hexdigest()
    publisher = tmp_path / 'publisher.json'
    publisher.write_bytes(b'{}')
    image = tmp_path / 'image.json'
    image.write_bytes(b'{}')
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    monkeypatch.setattr(base_qualification, 'read_publisher_policy', lambda _: {
        'key': {'pem_sha256': key_sha}, 'validity': {'not_after': expiry}, 'source': {'kind': 'https'}})
    monkeypatch.setattr(base_qualification, 'read_image_policy', lambda _: {
        'signer': {'key_sha256': key_sha, 'valid_until': expiry},
        'subject': {'origin': ORIGIN, 'repository': REPOSITORY, 'digest': 'sha256:'+'1'*64, 'architecture': 'amd64'}})
    def publisher_check(**_):
        path.write_bytes(b'changed after snapshot')
        return {'publisher_trust': 'pass', 'execution_mode': 'direct', 'trust_sha256': '3'*64}
    monkeypatch.setattr(base_qualification, 'assess_publisher_trust', publisher_check)
    def verify(**kwargs):
        assert kwargs[field] != path
        assert kwargs[field].read_bytes() == raw
        assert kwargs[field + '_sha256'] == pin
        raise ReachedBoundary
    monkeypatch.setattr(base_qualification, 'verify_registry_image_policy', verify)
    with pytest.raises(ReachedBoundary):
        base_qualification._qualify(publisher_policy=publisher, publisher_policy_sha256=hashlib.sha256(b'{}').hexdigest(),
            image_policy=image, image_policy_sha256=hashlib.sha256(b'{}').hexdigest(), public_key=key,
            cosign=tmp_path/'unused', wheel=tmp_path/'unused-wheel', tools=tmp_path/'unused-tools',
            output=tmp_path/'output', runtime_output=tmp_path/'runtime', expected_uid=1000,
            run_scope={name:'2'*64 for name in IDENTITY_KEYS}, _controlled=True,
            **{field: path, field + '_sha256': pin})


@pytest.mark.parametrize('field', ['blob_download_policy', 'attestation_size_policy'])
def test_mission_configuration_passes_selected_policy_without_changing_owned_registry_defaults(tmp_path, monkeypatch, field):
    document, args = configuration(tmp_path)
    policy, pin = registry_policy(tmp_path, field)
    document[field] = {'path': str(policy), 'sha256': pin}
    args['config'].write_text(json.dumps(document))
    args['expected_config_sha256'] = hashlib.sha256(args['config'].read_bytes()).hexdigest()
    monkeypatch.setattr(mission_operation, 'prerequisites', lambda _: {})
    def base(**kwargs):
        assert kwargs[field] == policy and kwargs[field + '_sha256'] == pin
        return {'decision': 'unknown', 'qualification_sha256': '1'*64}, None
    monkeypatch.setattr(mission_operation, 'qualify_mission_base', base)
    with tempfile.TemporaryDirectory(prefix='dso-policy-', dir='/tmp') as temporary:
        report, capability = mission_operation.qualify_and_build(**args, runtime_root=Path(temporary).resolve()/'r')
    assert report['decision'] == 'unknown' and capability is None


@pytest.mark.parametrize('ref', [None, {}, {'path':'relative','sha256':'1'*64}, {'path':'/a','sha256':'invalid'},
    {'path':'/a','sha256':'1'*64,'url':'https://arbitrary.example'}])
@pytest.mark.parametrize('field', ['blob_download_policy', 'attestation_size_policy'])
def test_mission_configuration_rejects_incomplete_registry_policy_identity(tmp_path, ref, field):
    document, _ = configuration(tmp_path)
    document[field] = ref
    with pytest.raises(ValueError):
        mission_operation._configuration(json.dumps(document).encode())


@pytest.mark.parametrize('service', ['gitlab', 'argocd', 'prometheus', 'kubernetes'])
@pytest.mark.parametrize('field', ['blob_download_policy', 'attestation_size_policy'])
def test_service_policy_is_rejected_on_unrelated_roles(service, field):
    config = json.loads((ROOT/'demo/software-factory/config/demo-config.example.json').read_bytes())
    target = next(x for x in config['services'] if x['id'] == service)
    target[field] = {'path':'/operator/policy.json','sha256':'1'*64}
    assert not contracts.Draft202012Validator(contracts.CONFIG_SCHEMA).is_valid(config)
    with pytest.raises(ValueError):
        contracts.validate_document(config, 'factory-config')


@pytest.mark.parametrize('field', ['blob_download_policy', 'attestation_size_policy'])
def test_d4_policy_must_match_service_selection_and_effective_repository(tmp_path, field):
    path, pin = registry_policy(tmp_path, field)
    ref = {'path':str(path),'sha256':pin}
    selected = {'registry_auth': {'token_env':'REGISTRY_READ'}, field:ref}
    policy = {'subject':{'origin':ORIGIN,'repository':REPOSITORY,'digest':'sha256:'+'1'*64}}
    service = {'id':'registry1','origin':ORIGIN,'read_auth':{'mode':'bearer','token_env':'REGISTRY_READ'},
        'scope': {'repositories':[REPOSITORY]}, field:deepcopy(ref)}
    case_supply_chain._bind_registry_read_scope(selected, policy, {'services':[service]})
    service[field]['sha256'] = '0'*64
    with pytest.raises(StateError):
        case_supply_chain._bind_registry_read_scope(selected, policy, {'services':[service]})


@pytest.mark.parametrize('field', [None, 'attestation_size_policy', 'blob_download_policy'])
def test_d4_policy_never_broadens_service_repository_scope(tmp_path, field):
    selected = {'registry_auth': {'token_env': 'REGISTRY_READ'}}
    service = {'id': 'registry1', 'origin': ORIGIN,
        'read_auth': {'mode': 'bearer', 'token_env': 'REGISTRY_READ'},
        'scope': {'repositories': ['other/repository']}}
    if field is not None:
        path, pin = registry_policy(tmp_path, field)
        selected[field] = service[field] = {'path': str(path), 'sha256': pin}
    policy = {'subject': {'origin': ORIGIN, 'repository': REPOSITORY, 'digest': 'sha256:' + '1' * 64}}
    with pytest.raises(StateError, match='outside the configured read scope'):
        case_supply_chain._bind_registry_read_scope(selected, policy, {'services': [service]})


@pytest.mark.parametrize('script', ['verify_registry_image.py', 'assess_registry_image.py'])
@pytest.mark.parametrize('field', ['blob-download-policy', 'attestation-size-policy'])
def test_cli_requires_policy_path_and_hash_together(script, tmp_path, capsys, field):
    spec = importlib.util.spec_from_file_location('download_cli', ROOT/'scripts'/script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    common = ['--public-key','/key','--cosign','/cosign','--output',str(tmp_path/'out'),
        '--token-env','TOKEN','--'+field,'/policy']
    args = (['--origin',ORIGIN,'--repository',REPOSITORY,'--digest','sha256:'+'1'*64,
        '--key-sha256','2'*64,'--architecture','amd64','--required-predicate','https://example.test/predicate']
        if script.startswith('verify') else ['--policy','/image','--policy-sha256','2'*64])
    with pytest.raises(SystemExit) as exc:
        module.main(args+common)
    assert exc.value.code == 2 and 'must be supplied together' in capsys.readouterr().err


def size_policy_for_bytes(tmp_path, raw, *, size_delta=0):
    path, _ = selected_size_policy(tmp_path)
    value = json.loads(path.read_bytes())
    value['descriptors'] = [{'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
                             'size_bytes': len(raw) + size_delta}]
    path.write_text(json.dumps(value))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def verification_with_stub_acquisition(tmp_path, monkeypatch, envelope, *, complete=False, descriptor_damage=False):
    """Synthetic files only; every crypto boundary is replaced in the caller."""
    key = tmp_path / 'key.pem'
    key.write_bytes(b'synthetic-key')
    args = acquisition_args(registry_evidence, tmp_path)
    args.update(public_key=key, expected_key_sha256=hashlib.sha256(key.read_bytes()).hexdigest(),
                cosign=tmp_path / 'nonexistent-cosign')
    def collect(*_, **kwargs):
        directory = kwargs['output']
        directory.mkdir()
        refs = {}
        names = []
        for index, raw in enumerate((envelope, b'synthetic-signature-payload', b'synthetic-signature')):
            digest = hashlib.sha256(raw).hexdigest()
            name = 'sha256-' + digest + '.blob'
            (directory / name).write_bytes(raw)
            refs[name] = {'sha256': digest, 'bytes': len(raw),
                'media_types': ['application/vnd.dsse.envelope.v1+json'] if index == 0 else []}
            names.append(name)
        descriptor = {'digest': 'sha256:' + refs[names[0]]['sha256'], 'size_bytes': len(envelope)}
        if descriptor_damage:
            descriptor['size_bytes'] += 1
        return {'scope': {'origin': ORIGIN}, 'gaps': [], 'artifacts': refs,
            'observations': {'acquisition': {'decision': 'pass' if complete else 'unknown'},
                'signatures': [{'payload_artifact': names[1], 'signature_artifact': names[2]}],
                'required_predicates': [{'selection': 'unique-unverified'}],
                'attestations': [{'predicate_type': args['required_predicates'][0],
                    'envelope_artifact': names[0], 'descriptor': descriptor}]}}
    monkeypatch.setattr(image_verification, 'collect_registry_evidence', collect)
    monkeypatch.setattr(image_verification, 'verify_image_signature',
                        lambda **_: pytest.fail('unexpected real-signature boundary'))
    monkeypatch.setattr(image_verification, 'verify_attestations',
                        lambda **_: pytest.fail('unexpected attestation boundary'))
    return args


@pytest.mark.parametrize('selection', ['exact', 'default', 'wrong-size'])
def test_large_saved_attestation_is_preserved_only_with_exact_selected_descriptor(tmp_path, monkeypatch, selection):
    raw = b'x' * 8_318_244
    args = verification_with_stub_acquisition(tmp_path, monkeypatch, raw)
    if selection != 'default':
        path, pin = size_policy_for_bytes(tmp_path, raw, size_delta=1 if selection == 'wrong-size' else 0)
        args.update(attestation_size_policy=path, attestation_size_policy_sha256=pin)
    if selection == 'exact':
        result = image_verification.verify_registry_image(ORIGIN, **args)
        assert result['verification'] == 'unknown' and result['build_authority'] == 'not-issued'
        saved = args['output'] / ('sha256-' + hashlib.sha256(raw).hexdigest() + '.blob')
        assert saved.read_bytes() == raw
        assert result['scope']['attestation_size_policy']['policy_sha256'] == pin
    else:
        with pytest.raises((ValueError, AttestationPolicyError)):
            image_verification.verify_registry_image(ORIGIN, **args)
        assert not args['output'].exists()


@pytest.mark.parametrize('descriptor_damage', [False, True])
def test_verification_binds_saved_descriptor_before_stubbed_crypto(tmp_path, monkeypatch, descriptor_damage):
    raw = b'synthetic-envelope'
    args = verification_with_stub_acquisition(tmp_path, monkeypatch, raw,
        complete=True, descriptor_damage=descriptor_damage)
    path, pin = size_policy_for_bytes(tmp_path, raw)
    args.update(attestation_size_policy=path, attestation_size_policy_sha256=pin)
    calls = []
    if not descriptor_damage:
        monkeypatch.setattr(image_verification, 'verify_image_signature',
            lambda **_: {'observations': {'verification': {'decision': 'pass'}}, 'gaps': []})
        def attestations(**kwargs):
            predicate = args['required_predicates'][0]
            assert kwargs['envelopes'][predicate].read_bytes() == raw
            assert kwargs['envelope_descriptors'] == {predicate: {
                'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}}
            assert kwargs['attestation_size_policy'].policy_sha256 == pin
            assert kwargs['expected_digest'] == 'sha256:' + '1' * 64
            calls.append('stubbed-crypto')
            return {'observations': {'verification': {'decision': 'pass'}}, 'gaps': []}
        monkeypatch.setattr(image_verification, 'verify_attestations', attestations)
    result = image_verification.verify_registry_image(ORIGIN, **args)
    assert calls == ([] if descriptor_damage else ['stubbed-crypto'])
    assert result['verification'] == ('unknown' if descriptor_damage else 'pass')
    if descriptor_damage:
        assert 'verification-attestation-descriptor-mismatch' in result['gaps']


def test_d4_selected_tamper_routes_only_original_bound_bytes_to_negative_control(tmp_path, monkeypatch):
    raw = b'synthetic-envelope'
    path, pin = size_policy_for_bytes(tmp_path, raw)
    selected = load_attestation_size_policy(path, pin, origin=ORIGIN,
        repository=REPOSITORY, subject_digest='sha256:' + '1' * 64)
    envelope, key, untrusted = (tmp_path / name for name in ('envelope', 'key', 'untrusted'))
    envelope.write_bytes(raw)
    key.write_bytes(b'trusted-stub-key')
    untrusted.write_bytes(b'untrusted-stub-key')
    predicate = 'https://example.test/predicate/v1'
    descriptors = {predicate: {'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}}
    control = {'subject': {'origin': ORIGIN, 'repository': REPOSITORY, 'digest': 'sha256:' + '1' * 64},
        'key': key, 'envelopes': {predicate: envelope}, 'attestation_size_policy': selected,
        'envelope_descriptors': descriptors}
    calls = []
    def verify(**kwargs):
        assert kwargs['attestation_size_policy'] is selected and kwargs['envelope_descriptors'] == descriptors
        if kwargs['envelopes'][predicate] is None:
            decision, gaps = 'unknown', [{'code': 'required-attestation-missing'}]
        else:
            assert kwargs['envelopes'][predicate] == envelope
            decision, gaps = ('pass' if kwargs['public_key'] == key else 'fail'), []
        calls.append(decision)
        return {'observations': {'verification': {'decision': decision}}, 'gaps': gaps}
    def negative(**kwargs):
        assert kwargs['envelopes'] == {predicate: envelope}
        assert kwargs['envelope_descriptors'] == descriptors and kwargs['attestation_size_policy'] is selected
        assert kwargs['output'] == tmp_path / 'controls/tampered-control'
        assert envelope.read_bytes() == raw
        calls.append('derived-negative')
        return {'provider': 'attestation-negative-control', 'decision': 'unknown',
                'observations': {'verification': {'decision': 'fail'}}, 'gaps': [], 'image_authority': 'not-issued'}
    monkeypatch.setattr(case_supply_chain.attestations, 'verify_attestations', verify)
    monkeypatch.setattr(case_supply_chain.attestations, 'verify_negative_attestation_control', negative)
    result = case_supply_chain.verify_negative_attestations(control, untrusted_key=untrusted,
        cosign=tmp_path / 'nonexistent-cosign', output=tmp_path / 'controls', deadline=time.monotonic() + 30)
    assert calls == ['pass', 'unknown', 'derived-negative', 'fail']
    assert {name: row['decision'] for name, row in result.items()} == {
        'valid': 'pass', 'missing': 'unknown', 'tampered': 'fail', 'untrusted': 'fail'}
    assert not (tmp_path / 'controls/tampered-envelope.json').exists()
