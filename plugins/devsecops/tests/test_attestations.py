"""Real individually signed DSSE inputs; generated keys are not Iron Bank trust."""
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil

import pytest

from devsecops.adapters import attestations as adapter
from devsecops.process import run

REPOSITORY = 'registry.example.test/mission'
DIGEST = 'sha256:' + 'a' * 64
PREDICATES = ['https://vat.dso.mil/api/p1/predicate/beta1',
              'https://repo1.dso.mil/dsop/dccscr/-/raw/master/hardening%20manifest/README.md']


def statement(predicate=PREDICATES[0]):
    return {'_type': 'https://in-toto.io/Statement/v0.1',
            'subject': [{'name': REPOSITORY, 'digest': {'sha256': DIGEST[7:]}}],
            'predicateType': predicate, 'predicate': {'private': 'SENTINEL-NOT-FOR-EXPORT'}}


def sign(path, binary, value, prefix):
    raw = json.dumps(value).encode()
    kind = b'application/vnd.in-toto+json'
    pae = b'DSSEv1 ' + str(len(kind)).encode() + b' ' + kind + b' ' + str(len(raw)).encode() + b' ' + raw
    (path / (prefix + '.pae')).write_bytes(pae)
    code, _, _, _ = run([str(binary), 'sign-blob', '--key', 'lab.key', '--use-signing-config=false',
        '--tlog-upload=false', '--bundle', prefix + '.bundle', prefix + '.pae'], cwd=path,
        env={'PATH': '/usr/bin:/bin', 'HOME': str(path), 'COSIGN_PASSWORD': ''}, timeout=15, limit=64_000)
    assert code == 0
    bundle = json.loads((path / (prefix + '.bundle')).read_bytes())
    envelope = {'payloadType': kind.decode(), 'payload': base64.b64encode(raw).decode(),
                'signatures': [{'keyid': '', 'sig': bundle['messageSignature']['signature']}]}
    (path / (prefix + '.json')).write_text(json.dumps(envelope))
    return path / (prefix + '.json'), bundle['verificationMaterial']


@pytest.fixture(scope='module')
def lab(tmp_path_factory):
    binary = Path(os.environ.get('DEVSECOPS_COSIGN', Path.home()/'.cache/devsecops/cosign-v3.1.4/bin/cosign'))
    if not binary.is_file():
        pytest.skip('Real pinned Cosign unavailable')
    path = tmp_path_factory.mktemp('real-dsse').resolve()
    for name in ('lab', 'wrong'):
        code, _, _, _ = run([str(binary), 'generate-key-pair', '--output-key-prefix', name], cwd=path,
            env={'PATH': '/usr/bin:/bin', 'HOME': str(path), 'COSIGN_PASSWORD': ''}, timeout=15, limit=64_000)
        assert code == 0
    for index, predicate in enumerate(PREDICATES):
        sign(path, binary, statement(predicate), str(index))
    return path, binary


@pytest.fixture
def inputs(lab, tmp_path):
    source, binary = lab
    path = tmp_path / 'inputs with spaces'
    shutil.copytree(source, path)
    return path, binary


def verify(inputs, **overrides):
    path, binary = inputs
    args = dict(expected_repository=REPOSITORY, expected_digest=DIGEST, required_predicates=PREDICATES,
                public_key=path/'lab.pub', envelopes={p: path/(str(i)+'.json') for i,p in enumerate(PREDICATES)},
                cosign=binary)
    args.update(overrides)
    return adapter.verify_attestations(**args)


def test_each_required_predicate_is_individually_verified_with_real_cosign(inputs):
    result = verify(inputs)
    obs = result['observations']
    assert obs['verification']['decision'] == 'pass'
    assert len(obs['predicates']) == 2 and all(p['verification'] == 'pass' for p in obs['predicates'])
    assert [r['operation'] for r in obs['receipts']] == ['version', 'verify-dsse-pae', 'verify-dsse-pae']
    assert result['decision'] == 'unknown' and not result['confirmed_failure']
    assert 'SENTINEL-NOT-FOR-EXPORT' not in json.dumps(result) and str(inputs[0]) not in json.dumps(result)
    assert {g['code'] for g in result['gaps']} == {'signer-authority-unverified', 'key-lifecycle-unverified',
        'transparency-log-unverified', 'official-image-origin-unverified', 'predicate-semantics-unassessed'}


def test_pae_signature_is_also_accepted_by_cosign_native_attestation_verifier(inputs):
    path, binary = inputs
    envelope = json.loads((path/'0.json').read_bytes())
    material = json.loads((path/'0.bundle').read_bytes())['verificationMaterial']
    bundle = {'mediaType': 'application/vnd.dev.sigstore.bundle.v0.3+json',
              'verificationMaterial': material, 'dsseEnvelope': envelope}
    (path/'native-bundle.json').write_text(json.dumps(bundle))
    code, _, _, _ = run([str(binary), 'verify-blob-attestation', '--key', 'lab.pub',
        '--bundle', 'native-bundle.json', '--digest', DIGEST[7:], '--digestAlg', 'sha256',
        '--type', PREDICATES[0], '--insecure-ignore-tlog'], cwd=path,
        env={'PATH': '/usr/bin:/bin', 'HOME': str(path)}, timeout=15, limit=64_000)
    assert code == 0, 'Independent Cosign DSSE verifier must accept the same signed envelope'


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'tamper', 'wrong-key', 'subject', 'predicate'])
def test_one_good_signature_cannot_cover_another_required_artifact(inputs, change):
    path, binary = inputs
    kwargs = {}
    if change == 'missing':
        kwargs['envelopes'] = {PREDICATES[0]: path/'0.json'}
    elif change == 'duplicate':
        kwargs['envelopes'] = {p: path/'0.json' for p in PREDICATES}
    elif change == 'wrong-key':
        kwargs['public_key'] = path/'wrong.pub'
    elif change in {'subject', 'predicate'}:
        value = statement(PREDICATES[1])
        if change == 'subject':
            value['subject'][0]['digest']['sha256'] = 'b' * 64
        else:
            value['predicateType'] = 'https://example.test/other'
        sign(path, binary, value, '1')
    else:
        envelope = json.loads((path/'1.json').read_bytes())
        value = json.loads(base64.b64decode(envelope['payload']))
        value['predicate']['private'] = 'tampered'
        envelope['payload'] = base64.b64encode(json.dumps(value).encode()).decode()
        (path/'1.json').write_text(json.dumps(envelope))
    result = verify(inputs, **kwargs)
    assert result['observations']['verification']['decision'] == ('unknown' if change == 'missing' else 'fail')
    assert result['decision'] == 'unknown' and not result['confirmed_failure']


@pytest.mark.parametrize('mutate', [
    lambda e: e.update(payloadType='text/plain'),
    lambda e: e.update(signatures=[]),
    lambda e: e['signatures'].append(deepcopy(e['signatures'][0])),
    lambda e: e.update(certificate='unqualified'),
    lambda e: e.update(payload='not base64'),
])
def test_unsupported_envelope_cannot_be_silently_normalized(inputs, mutate):
    path, _ = inputs
    value = json.loads((path/'0.json').read_bytes())
    mutate(value)
    (path/'0.json').write_text(json.dumps(value))
    result = verify(inputs)
    assert result['observations']['predicates'][0]['verification'] == 'unknown'
    assert result['observations']['predicates'][1]['verification'] == 'pass'
    assert len(result['observations']['receipts']) == 2


def test_duplicate_json_and_symlink_are_not_read_as_attestations(inputs):
    path, _ = inputs
    (path/'0.json').write_text('{"payload":"one","payload":"two"}')
    (path/'1.json').unlink()
    (path/'1.json').symlink_to(path/'0.json')
    result = verify(inputs)
    assert all(p['verification'] == 'unknown' for p in result['observations']['predicates'])
    assert len(result['observations']['receipts']) == 1


def test_inputs_are_frozen_and_process_environment_has_no_ambient_credentials(inputs, monkeypatch):
    path, _ = inputs
    original = adapter.run
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', 'SENTINEL-CREDENTIAL')
    key_hash = hashlib.sha256((path/'lab.pub').read_bytes()).hexdigest()
    def observed(argv, **kwargs):
        assert 'AWS_SECRET_ACCESS_KEY' not in kwargs['env']
        assert kwargs['env']['PATH'].endswith('empty-path')
        if argv[1] == 'version':
            (path/'lab.pub').write_text('changed after freeze')
        return original(argv, **kwargs)
    monkeypatch.setattr(adapter, 'run', observed)
    result = verify(inputs)
    assert result['observations']['verification']['decision'] == 'pass'
    assert result['observations']['inputs']['public-key']['sha256'] == key_hash


def test_urlsafe_envelope_keeps_the_exact_signed_payload(inputs):
    path, _ = inputs
    for index in range(2):
        file = path / (str(index) + '.json')
        envelope = json.loads(file.read_bytes())
        envelope['payload'] = envelope['payload'].translate(str.maketrans('+/', '-_'))
        envelope['signatures'][0]['sig'] = envelope['signatures'][0]['sig'].translate(str.maketrans('+/', '-_'))
        file.write_text(json.dumps(envelope))
    assert verify(inputs)['observations']['verification']['decision'] == 'pass'


@pytest.mark.skipif(os.environ.get('DEVSECOPS_TEST_NETWORK_SANDBOX') != '1',
                    reason='Explicit host macOS network-denial qualification')
def test_individual_attestation_verification_with_os_network_denied(inputs, monkeypatch):
    assert platform.system() == 'Darwin'
    original = adapter.run
    def isolated(argv, **kwargs):
        return original(['/usr/bin/sandbox-exec', '-p', '(version 1)(allow default)(deny network*)', *argv], **kwargs)
    monkeypatch.setattr(adapter, 'run', isolated)
    result = verify(inputs)
    assert result['observations']['verification']['decision'] == 'pass'
    assert all(r['tool'] == 'sandbox-exec' and r['exit_code'] == 0 for r in result['observations']['receipts'])


def test_arbitrary_predicate_names_and_extra_sources_fail_before_file_access(inputs, monkeypatch):
    monkeypatch.setattr(adapter, '_snapshot', lambda *a, **k: pytest.fail('Must validate before filesystem access'))
    for overrides in ({'required_predicates': []}, {'required_predicates': [PREDICATES[0]] * 2},
                      {'required_predicates': ['https://user:password@example.test/predicate']},
                      {'envelopes': {'https://example.test/unrequired': inputs[0]/'0.json'}}):
        with pytest.raises(ValueError):
            verify(inputs, **overrides)


def test_last_successful_child_cannot_complete_after_shared_deadline(inputs, monkeypatch):
    original_run, monotonic = adapter.run, adapter.time.monotonic
    offset = 0
    monkeypatch.setattr(adapter.time, 'monotonic', lambda: monotonic() + offset)
    def delayed(argv, **kwargs):
        nonlocal offset
        result = original_run(argv, **kwargs)
        if argv[-1] == '1.pae':
            offset = 121
        return result
    monkeypatch.setattr(adapter, 'run', delayed)
    result = verify(inputs, timeout=120)
    assert result['observations']['verification']['decision'] == 'unknown'
    assert result['observations']['predicates'][0]['verification'] == 'pass'
    assert result['observations']['predicates'][1]['verification'] == 'unknown'
    assert any(g['code'] == 'attestation-time-budget-exhausted' for g in result['gaps'])


def test_child_budget_failure_stops_later_input_reads_and_keeps_all_requirements(inputs, monkeypatch):
    original_run, original_snapshot = adapter.run, adapter._snapshot
    copied = []
    def bounded(argv, **kwargs):
        if argv[-1] == '0.pae':
            raise adapter.ToolError('test child time/output bound')
        return original_run(argv, **kwargs)
    def snapshot(source, destination, *args, **kwargs):
        copied.append(destination.name)
        return original_snapshot(source, destination, *args, **kwargs)
    monkeypatch.setattr(adapter, 'run', bounded)
    monkeypatch.setattr(adapter, '_snapshot', snapshot)
    result = verify(inputs)
    assert '0.json' in copied and '1.json' not in copied
    assert [p['predicate_type'] for p in result['observations']['predicates']] == PREDICATES
    assert all(p['verification'] == 'unknown' for p in result['observations']['predicates'])
    assert any(g['code'] == 'attestation-tool-budget-or-execution-failed' for g in result['gaps'])


def test_signed_numeric_digest_is_malformed_not_a_subject_verdict(inputs):
    path, binary = inputs
    value = statement(PREDICATES[0])
    value['subject'][0]['digest']['sha256'] = int('1' * 64)
    sign(path, binary, value, '0')
    result = verify(inputs)
    assert result['observations']['predicates'][0]['crypto'] == 'unknown'
    assert result['observations']['predicates'][0]['verification'] == 'unknown'
    assert result['observations']['predicates'][1]['verification'] == 'pass'
    assert any(g['code'] == 'attestation-subject-unsupported' for g in result['gaps'])
