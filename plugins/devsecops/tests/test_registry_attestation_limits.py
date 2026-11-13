"""Synthetic descriptor and DSSE bounds; all cryptographic execution is stubbed."""
from dataclasses import FrozenInstanceError, replace
import base64
import hashlib
import json
from pathlib import Path
import platform
import socket
import subprocess
import time

import pytest

from devsecops.adapters import attestations as adapter
from devsecops.adapters import registry_attestation_policy as policy_module
from devsecops.adapters.http import APIError

ORIGIN = 'https://registry1.dso.mil'
REPOSITORY = 'ironbank/opensource/nginx/nginx'
SUBJECT = 'sha256:' + 'a' * 64
BLOB = 'sha256:' + 'b' * 64
OTHER = 'sha256:' + 'c' * 64
PREDICATE = 'https://example.test/attestation/v1'
SCOPE = dict(origin=ORIGIN, repository=REPOSITORY, subject_digest=SUBJECT)


@pytest.fixture(autouse=True)
def no_external_execution(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail('This synthetic suite must never execute a process or access a socket')
    monkeypatch.setattr(subprocess, 'Popen', blocked)
    monkeypatch.setattr(socket, 'socket', blocked)


def document(descriptors=None, **changes):
    value = {'schema_version': 1, 'kind': 'registry-attestation-size-policy', **SCOPE,
             'descriptors': ([{'digest': BLOB, 'size_bytes': 8_318_244}]
                             if descriptors is None else descriptors)}
    value.update(changes)
    return value


def parse(value=None, **kwargs):
    return policy_module.parse_attestation_size_policy(
        json.dumps(document() if value is None else value).encode(), **kwargs)


def descriptor(raw):
    return {'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)}


def envelope(predicate=PREDICATE, *, padding=0, total_bytes=None, subject=SUBJECT, nested=None):
    statement = {'_type': 'https://in-toto.io/Statement/v1',
        'subject': [{'name': ORIGIN[8:] + '/' + REPOSITORY, 'digest': {'sha256': subject[7:]}}],
        'predicateType': predicate, 'predicate': nested or {'padding': 'x' * padding}}
    payload = json.dumps(statement, separators=(',', ':')).encode()
    raw = json.dumps({'payloadType': adapter.PAYLOAD_TYPE,
        'payload': base64.b64encode(payload).decode(),
        'signatures': [{'sig': base64.b64encode(b's' * 64).decode()}]}, separators=(',', ':')).encode()
    if total_bytes is not None:
        assert len(raw) <= total_bytes
        raw += b' ' * (total_bytes - len(raw))
    return raw


@pytest.fixture
def synthetic_crypto(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    executable = root / 'synthetic-cosign'
    executable.write_bytes(b'INERT TEST DATA; NEVER AN EXECUTABLE')
    binary = executable.read_bytes()
    arch = {'aarch64': 'arm64', 'x86_64': 'amd64'}.get(platform.machine(), platform.machine())
    target = platform.system().lower() + '-' + arch
    (root / 'cosign.lock.json').write_text(json.dumps({'schema_version': 1, 'version': adapter.VERSION,
        'tools': {target: {'sha256': hashlib.sha256(binary).hexdigest(), 'bytes': len(binary)}}}))
    public_key = root / 'synthetic-key'
    public_key.write_bytes(b'SYNTHETIC KEY NOT USED FOR CRYPTOGRAPHY')
    calls = []
    behavior = {'positive': True, 'negative': 'reject'}
    def fake_run(args, *, cwd, timeout, limit, env):
        assert timeout <= 120 and limit == 64_000
        assert env['PATH'].endswith('/empty-path')
        calls.append(list(args[1:]))
        receipt = {'synthetic': True, 'exit_code': 0}
        if args[1:] == ['version', '--json']:
            return 0, json.dumps({'gitVersion': 'v' + adapter.VERSION}).encode(), b'', receipt
        assert args[1] == 'verify-blob' and '--offline' in args and '--insecure-ignore-tlog' in args
        assert Path(cwd, args[-1]).read_bytes().startswith(b'DSSEv1 ')
        if args[-1] == 'control.pae':
            if behavior['negative'] == 'reject':
                return 1, b'', b'invalid signature when validating ASN.1 encoded signature', receipt
            if behavior['negative'] == 'unavailable':
                return 1, b'', b'synthetic tool unavailable', receipt
            return 0, b'', b'', receipt
        if not behavior['positive']:
            return 1, b'', b'invalid signature when validating ASN.1 encoded signature', receipt
        return 0, b'', b'', receipt
    monkeypatch.setattr(adapter, 'ROOT', root)
    monkeypatch.setattr(adapter, '_public_key', lambda raw: None)
    monkeypatch.setattr(adapter, 'run', fake_run)
    return root, executable, public_key, calls, behavior


def inputs(crypto, raw, *, selected=True):
    root, executable, public_key, _, _ = crypto
    path = root / 'original-envelope.json'
    path.write_bytes(raw)
    args = dict(expected_repository=ORIGIN[8:] + '/' + REPOSITORY, expected_digest=SUBJECT,
        required_predicates=[PREDICATE], public_key=public_key, envelopes={PREDICATE: path}, cosign=executable)
    if selected:
        desc = descriptor(raw)
        args.update(attestation_size_policy=parse(document([desc])), envelope_descriptors={PREDICATE: desc})
    return args


def test_fixed_profile_preserves_default_limits_and_full_identity():
    policy = parse()
    assert (adapter.MAX_ENVELOPE, adapter.MAX_PAYLOAD) == (4_000_000, 2_500_000)
    assert policy.limits_for(BLOB, 8_318_244) == (8_388_608, 6_291_456)
    assert policy.limits_for(OTHER, 4_000_000) == (4_000_000, 2_500_000)
    assert policy.select_blob(OTHER, 4_000_000) is None
    assert policy.identity_record()['descriptors'] == document()['descriptors']
    assert policy.require_scope(**SCOPE) is policy
    with pytest.raises(FrozenInstanceError):
        policy.repository = 'other/repository'


@pytest.mark.parametrize('change', [
    {'schema_version': True}, {'schema_version': 2}, {'kind': 'other'}, {'extra': 'field'},
    {'origin': 'http://registry1.dso.mil'}, {'origin': ORIGIN + '/'},
    {'origin': 'https://user:secret@registry1.dso.mil'}, {'origin': ORIGIN + '?secret=x'},
    {'repository': '../repo'}, {'repository': '*'}, {'subject_digest': 'sha256:' + 'A' * 64},
    {'descriptors': []}, {'descriptors': {}},
    {'descriptors': [{'digest': BLOB, 'size_bytes': True}]},
    {'descriptors': [{'digest': BLOB, 'size_bytes': 0}]},
    {'descriptors': [{'digest': BLOB, 'size_bytes': 8_388_609}]},
    {'descriptors': [{'digest': BLOB, 'size_bytes': 1, 'extra': 2}]},
    {'descriptors': [{'digest': BLOB, 'size_bytes': 1}] * 2},
    {'descriptors': [{'digest': 'sha256:' + format(i, '064x'), 'size_bytes': 1} for i in range(17)]},
    {'descriptors': [{'digest': 'sha256:' + format(i, '064x'), 'size_bytes': 8_388_608} for i in range(8)]},
])
def test_closed_policy_rejects_invalid_or_unbounded_inputs(change):
    with pytest.raises(policy_module.AttestationPolicyError):
        parse(document(**change))


@pytest.mark.parametrize('raw', [b'[]', b'null', b'{}', b'{', b' ' * 64_001,
    b'{"schema_version":1,"schema_version":1}'])
def test_parser_rejects_invalid_json_shape_duplicate_keys_and_document_bound(raw):
    with pytest.raises(policy_module.AttestationPolicyError):
        policy_module.parse_attestation_size_policy(raw)


@pytest.mark.parametrize('size', [1, 8_388_608])
def test_descriptor_exact_accepted_boundaries(size):
    assert parse(document([{'digest': BLOB, 'size_bytes': size}])).limits_for(BLOB, size) == (8_388_608, 6_291_456)


def test_aggregate_exact_boundary():
    rows = [{'digest': 'sha256:' + format(i, '064x'), 'size_bytes': 8_000_000} for i in range(8)]
    assert len(parse(document(rows)).descriptors) == 8
    rows[-1]['size_bytes'] += 1
    with pytest.raises(policy_module.AttestationPolicyError):
        parse(document(rows))


@pytest.mark.parametrize('digest,size', [(BLOB, 8_318_243), (BLOB, 8_318_245), (OTHER, 4_000_001),
    (BLOB, True), (BLOB, 0), ('invalid', 1)])
def test_unselected_oversize_and_selected_mismatch_reject(digest, size):
    with pytest.raises(policy_module.AttestationPolicyError):
        parse().select_blob(digest, size)


def test_policy_scope_hash_deadline_and_paired_loader(tmp_path):
    raw = json.dumps(document()).encode()
    sha = hashlib.sha256(raw).hexdigest()
    path = tmp_path.resolve() / 'policy.json'
    path.write_bytes(raw)
    loaded = policy_module.load_attestation_size_policy(path, sha, **SCOPE)
    assert loaded.policy_sha256 == sha
    assert policy_module.load_attestation_size_policy(None, None, **SCOPE) is None
    for path_value, hash_value in [(path, None), (None, sha), (path, '0' * 64), (Path('relative'), sha)]:
        with pytest.raises(policy_module.AttestationPolicyError):
            policy_module.load_attestation_size_policy(path_value, hash_value, **SCOPE)
    for kwargs in [{'origin': ORIGIN}, {**SCOPE, 'repository': 'different/repo'},
        {**SCOPE, 'subject_digest': OTHER}, {'expected_sha256': '0' * 64}, {'deadline': time.monotonic() - 1}]:
        with pytest.raises(policy_module.AttestationPolicyError):
            policy_module.parse_attestation_size_policy(raw, **kwargs)
    link = path.parent / 'policy-link.json'
    link.symlink_to(path)
    with pytest.raises(policy_module.AttestationPolicyError):
        policy_module.load_attestation_size_policy(link, sha, **SCOPE)
    directory_link = path.parent / 'linked-directory'
    directory_link.symlink_to(path.parent, target_is_directory=True)
    with pytest.raises(policy_module.AttestationPolicyError):
        policy_module.load_attestation_size_policy(directory_link / path.name, sha, **SCOPE)


def test_selections_require_issued_identity_and_exact_context():
    policy = parse()
    selection = policy.select_blob(BLOB, 8_318_244)
    scope = dict(origin=ORIGIN, repository=REPOSITORY, digest=BLOB, size_bytes=8_318_244)
    assert policy_module.validate_attestation_selection(selection, **scope) == 8_388_608
    for forged in [replace(selection), policy_module.SelectedAttestationBlob(
        ORIGIN, REPOSITORY, SUBJECT, BLOB, 8_318_244, policy.policy_sha256)]:
        with pytest.raises(policy_module.AttestationPolicyError):
            policy_module.validate_attestation_selection(forged, **scope)
    for key, value in [('origin', 'https://other.example'), ('repository', 'other/repo'),
                       ('digest', OTHER), ('size_bytes', 8_318_243)]:
        with pytest.raises(policy_module.AttestationPolicyError):
            policy_module.validate_attestation_selection(selection, **{**scope, key: value})
    object.__setattr__(selection, 'size_bytes', 4_000_000)
    with pytest.raises(policy_module.AttestationPolicyError):
        policy_module.validate_attestation_selection(selection, **{**scope, 'size_bytes': 4_000_000})
    for forged_policy in [replace(policy), policy_module.AttestationSizePolicy(
        ORIGIN, REPOSITORY, SUBJECT, policy.descriptors, policy.policy_sha256)]:
        with pytest.raises(policy_module.AttestationPolicyError):
            forged_policy.select_blob(BLOB, 8_318_244)
    object.__setattr__(policy, 'policy_sha256', '0' * 64)
    with pytest.raises(policy_module.AttestationPolicyError):
        policy.identity_record()


@pytest.mark.parametrize('size,selected,accepted', [
    (4_000_000, False, True), (4_000_001, False, False),
    (8_388_608, True, True), (8_388_609, True, False),
])
def test_decode_envelope_boundaries(size, selected, accepted):
    raw = envelope(total_bytes=size)
    limit = policy_module.MAX_PAYLOAD if selected else adapter.MAX_PAYLOAD
    if accepted:
        assert adapter._decode(raw, max_payload=limit)[0]['predicateType'] == PREDICATE
    else:
        with pytest.raises(adapter._Invalid):
            adapter._decode(raw, max_payload=limit)


@pytest.mark.parametrize('maximum', [2_500_000, 6_291_456])
def test_decoded_payload_exact_limits(maximum):
    encoded = base64.b64encode(b'x' * maximum).decode()
    assert len(adapter._dsse_base64(encoded, maximum=maximum)) == maximum
    with pytest.raises(adapter._Invalid):
        adapter._dsse_base64(base64.b64encode(b'x' * (maximum + 1)).decode(), maximum=maximum)


@pytest.mark.parametrize('nested', ['depth', 'nodes'])
def test_selected_profile_retains_json_complexity_bounds(nested):
    value = {'leaf': True}
    if nested == 'depth':
        for _ in range(65):
            value = {'nested': value}
    else:
        value = {'values': [1] * 100_001}
    with pytest.raises(APIError):
        adapter._decode(envelope(nested=value), max_payload=policy_module.MAX_PAYLOAD)


def test_selected_large_payload_passes_only_with_exact_descriptor(synthetic_crypto):
    raw = envelope(padding=3_200_000)
    assert len(raw) > 4_000_000
    args = inputs(synthetic_crypto, raw)
    report = adapter.verify_attestations(**args)
    assert report['observations']['verification']['decision'] == 'pass'
    assert report['decision'] == 'unknown'  # Crypto alone still grants no publisher authority.
    assert any(g['code'] == 'signer-authority-unverified' for g in report['gaps'])
    calls = synthetic_crypto[3]
    calls.clear()
    report = adapter.verify_attestations(**inputs(synthetic_crypto, raw, selected=False))
    assert report['observations']['verification']['decision'] == 'unknown'
    assert not any(call[0] == 'verify-blob' for call in calls)


@pytest.mark.parametrize('mutation', ['hash', 'size', 'missing'])
def test_original_descriptor_mismatch_stops_before_crypto(synthetic_crypto, mutation):
    args = inputs(synthetic_crypto, envelope())
    if mutation == 'hash':
        original = args['envelopes'][PREDICATE]
        raw = original.read_bytes().replace(b'payloadType', b'payloadTypf')
        original.write_bytes(raw)
    elif mutation == 'size':
        args['envelopes'][PREDICATE].write_bytes(args['envelopes'][PREDICATE].read_bytes() + b' ')
    else:
        args['envelope_descriptors'] = {}
    report = adapter.verify_attestations(**args)
    assert report['observations']['verification']['decision'] == 'unknown'
    assert not any(call[0] == 'verify-blob' for call in synthetic_crypto[3])


def test_selected_profile_preserves_subject_and_predicate_checks(synthetic_crypto):
    args = inputs(synthetic_crypto, envelope(subject=OTHER))
    report = adapter.verify_attestations(**args)
    assert report['observations']['verification']['decision'] == 'fail'
    assert report['observations']['predicates'][0]['subject_binding'] == 'fail'


def test_mixed_selected_and_default_inputs_share_64mb_copy_budget(synthetic_crypto, monkeypatch):
    root, executable, key, calls, _ = synthetic_crypto
    predicates, envelopes, descriptors, selected = [], {}, {}, []
    for index in range(9):
        predicate = f'https://example.test/attestation/{index}'
        raw = envelope(predicate, total_bytes=8_388_608 if index < 7 else 3_000_000)
        path = root / f'envelope-{index}.json'
        path.write_bytes(raw)
        predicates.append(predicate)
        envelopes[predicate] = path
        descriptors[predicate] = descriptor(raw)
        if index < 7:
            selected.append(descriptor(raw))
    copied = []
    original_snapshot = adapter._snapshot
    def snapshot(source, destination, limit, **kwargs):
        if source in envelopes.values():
            copied.append(source)
        return original_snapshot(source, destination, limit, **kwargs)
    monkeypatch.setattr(adapter, '_snapshot', snapshot)
    report = adapter.verify_attestations(expected_repository=ORIGIN[8:] + '/' + REPOSITORY,
        expected_digest=SUBJECT, required_predicates=predicates, public_key=key, cosign=executable,
        envelopes=envelopes, envelope_descriptors=descriptors, attestation_size_policy=parse(document(selected)))
    assert len(copied) == 8 and envelopes[predicates[-1]] not in copied
    assert sum(descriptors[p]['size_bytes'] for p in predicates[:-1]) <= 64_000_000
    assert sum(d['size_bytes'] for d in descriptors.values()) > 64_000_000
    assert any(g['code'] == 'attestation-aggregate-size-limit' for g in report['gaps'])
    assert report['observations']['verification']['decision'] == 'unknown'
    assert sum(c[0] == 'verify-blob' for c in calls) == 8


def test_negative_control_reverifies_original_then_rejects_derived_without_authority(synthetic_crypto):
    raw = envelope(padding=3_100_000)
    args = inputs(synthetic_crypto, raw)
    output = synthetic_crypto[0] / 'control'
    report = adapter.verify_negative_attestation_control(**args, output=output)
    assert report['provider'] == 'attestation-negative-control'
    assert report['observations']['original_verification'] == 'pass'
    assert report['observations']['verification']['decision'] == 'fail'
    assert report['decision'] == 'unknown' and report['image_authority'] == 'not-issued'
    assert report['execution_status'] == 'complete'
    original_report = (output / report['observations']['original_report_artifact']).read_bytes()
    assert hashlib.sha256(original_report).hexdigest() == report['observations']['original_report_sha256']
    assert json.loads(original_report)['observations']['verification']['decision'] == 'pass'
    assert (output / 'original-verification.json').stat().st_mode & 0o777 == 0o600
    derived = (output / 'tampered-envelope.json').read_bytes()
    info = report['observations']['derivation']
    assert info['original_sha256'] == hashlib.sha256(raw).hexdigest()
    assert info['derived_sha256'] == hashlib.sha256(derived).hexdigest()
    assert not info['production_input'] and raw != derived
    assert json.loads(raw)['signatures'] == json.loads(derived)['signatures']
    assert len(derived) <= policy_module.MAX_ENVELOPE
    args['envelopes'] = {PREDICATE: output / 'tampered-envelope.json'}
    synthetic_crypto[3].clear()
    positive = adapter.verify_attestations(**args)
    assert positive['observations']['verification']['decision'] == 'unknown'
    assert not any(call[0] == 'verify-blob' for call in synthetic_crypto[3])


@pytest.mark.parametrize('failure', ['unbound', 'original-crypto', 'accepted-derived', 'tool-unavailable'])
def test_negative_control_cannot_turn_missing_rejection_into_success(synthetic_crypto, failure):
    args = inputs(synthetic_crypto, envelope())
    if failure == 'unbound':
        args['envelopes'][PREDICATE].write_bytes(b'invalid changed original')
    elif failure == 'original-crypto':
        synthetic_crypto[4]['positive'] = False
    elif failure == 'accepted-derived':
        synthetic_crypto[4]['negative'] = 'accept'
    else:
        synthetic_crypto[4]['negative'] = 'unavailable'
    report = adapter.verify_negative_attestation_control(**args, output=synthetic_crypto[0] / 'control')
    assert report['observations']['verification']['decision'] == 'unknown'
    assert report['decision'] == 'unknown' and report['image_authority'] == 'not-issued'
    assert report['execution_status'] == 'partial'
    if failure in {'unbound', 'original-crypto'}:
        assert not any(call[-1] == 'control.pae' for call in synthetic_crypto[3])
    if failure == 'unbound':
        assert not synthetic_crypto[3]
    if failure == 'original-crypto':
        original_report = (synthetic_crypto[0] / 'control' / 'original-verification.json').read_bytes()
        assert hashlib.sha256(original_report).hexdigest() == report['observations']['original_report_sha256']
        assert json.loads(original_report)['observations']['verification']['decision'] == 'fail'


def test_negative_control_rejects_oversize_derivation_before_native_control(synthetic_crypto, monkeypatch):
    args = inputs(synthetic_crypto, envelope())
    original_decode = adapter._decode
    count = 0
    def decode(raw, **kwargs):
        nonlocal count
        count += 1
        statement, payload, pae, signature = original_decode(raw, **kwargs)
        if count == 2:  # Simulate a future internal derivation unexpectedly expanding its output.
            statement['predicate']['oversize'] = 'x' * (policy_module.MAX_PAYLOAD + 1)
        return statement, payload, pae, signature
    monkeypatch.setattr(adapter, '_decode', decode)
    report = adapter.verify_negative_attestation_control(**args, output=synthetic_crypto[0] / 'control')
    assert any(g['code'] == 'negative-control-derived-size-invalid' for g in report['gaps'])
    assert report['observations']['verification']['decision'] == 'unknown'
    assert not any(call[-1] == 'control.pae' for call in synthetic_crypto[3])
    assert not (synthetic_crypto[0] / 'control' / 'tampered-envelope.json').exists()
