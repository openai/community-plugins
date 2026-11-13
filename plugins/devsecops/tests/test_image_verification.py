"""Real TLS acquisition plus real Cosign; no injected provider verdicts."""
import base64
import hashlib
import json

import pytest

from devsecops.adapters.image_verification import verify_registry_image
from devsecops.adapters import image_verification as adapter
from devsecops.adapters.oci import MANIFEST
from devsecops.process import run
from scripts.verify_registry_image import main
from test_attestations import lab
from test_registry_evidence import (registry, ca, install, REPOSITORY, PREDICATES,
                                    encode, digest, SECRET, TOKEN)

__all__ = ['registry', 'ca', 'lab']


def sign_registry_objects(registry, data, lab):
    path, binary = lab
    routes = registry['routes']
    for suffix in ('sig', 'att'):
        manifest = data[suffix]
        for index, descriptor in enumerate(manifest['layers']):
            blob_path = '/v2/' + REPOSITORY + '/blobs/' + descriptor['digest']
            content = routes[blob_path]['raw']
            if suffix == 'att':
                envelope = json.loads(content)
                payload = base64.b64decode(envelope['payload'])
                kind = envelope['payloadType'].encode()
                signed = b'DSSEv1 ' + str(len(kind)).encode() + b' ' + kind + b' ' + str(len(payload)).encode() + b' ' + payload
            else:
                signed = content
            prefix = 'registry-' + suffix + str(index)
            (path / (prefix + '.payload')).write_bytes(signed)
            code, _, _, _ = run([str(binary), 'sign-blob', '--key', 'lab.key', '--use-signing-config=false',
                '--tlog-upload=false', '--bundle', prefix + '.bundle', prefix + '.payload'], cwd=path,
                env={'HOME': str(path), 'PATH': '/usr/bin:/bin', 'COSIGN_PASSWORD': ''}, timeout=15, limit=64_000)
            assert code == 0
            signature = json.loads((path / (prefix + '.bundle')).read_bytes())['messageSignature']['signature']
            if suffix == 'sig':
                descriptor['annotations']['dev.cosignproject.cosign/signature'] = signature
            else:
                envelope['signatures'][0]['sig'] = signature
                content = encode(envelope)
                descriptor['digest'], descriptor['size'] = digest(content), len(content)
                routes['/v2/' + REPOSITORY + '/blobs/' + descriptor['digest']] = {
                    'raw': content, 'media': descriptor['mediaType'], 'headers': {'Docker-Content-Digest': digest(content)}}
        raw = encode(manifest)
        response = {'raw': raw, 'media': MANIFEST, 'headers': {'Docker-Content-Digest': digest(raw)}}
        routes['/v2/' + REPOSITORY + '/manifests/' + digest(raw)] = response
        routes['/v2/' + REPOSITORY + '/manifests/sha256-' + data['requested'][7:] + '.' + suffix] = response


def arguments(registry, data, lab, output):
    path, binary = lab
    return dict(origin=registry['origin'], repository=REPOSITORY, requested_digest=data['requested'],
        architecture='amd64', allowed_repositories=[REPOSITORY], required_predicates=PREDICATES,
        public_key=path/'lab.pub', expected_key_sha256=hashlib.sha256((path/'lab.pub').read_bytes()).hexdigest(),
        cosign=binary, output=output.resolve(), token_env='REGISTRY_TEST_TOKEN', ca_bundle=registry['ca'])


def test_direct_tls_acquisition_to_real_crypto_keeps_trust_and_build_separate(registry, lab, tmp_path):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    output = tmp_path / 'image verification with spaces'
    result = verify_registry_image(**arguments(registry, data, lab, output))
    assert result['checks'] == {'acquisition': 'pass', 'image_signature': 'pass', 'required_attestations': 'pass'}
    assert result['verification'] == 'pass' and result['decision'] == 'unknown'
    assert result['build_authority'] == 'not-issued'
    assert len(registry['requests']) == 12
    manifest = json.loads((output/'image-manifest.json').read_bytes())
    for name, ref in manifest['artifacts'].items():
        raw = (output/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == ref['sha256'] and len(raw) == ref['bytes']
        assert (output/name).stat().st_mode & 0o077 == 0
    assert SECRET not in json.dumps(result) and TOKEN not in json.dumps(result)
    assert str(lab[0]) not in json.dumps(result)


def test_changed_explicit_key_stops_before_any_registry_authentication(registry, lab, tmp_path):
    data = install(registry)
    args = arguments(registry, data, lab, tmp_path/'no output')
    args['expected_key_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Public key'):
        verify_registry_image(**args)
    assert not registry['requests'] and not args['output'].exists()


def test_missing_predicate_never_hides_behind_valid_image_signature(registry, lab, tmp_path):
    data = install(registry, predicates=[PREDICATES[0]])
    sign_registry_objects(registry, data, lab)
    result = verify_registry_image(**arguments(registry, data, lab, tmp_path/'missing'))
    assert result['checks']['acquisition'] == result['verification'] == 'unknown'
    assert result['checks']['image_signature'] == 'unknown'
    assert 'registry-acquisition-incomplete' in result['gaps']


def test_script_real_chain_returns_unknown_authority_even_with_valid_crypto(registry, lab, tmp_path, capsys):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    a = arguments(registry, data, lab, tmp_path/'script output')
    cli = []
    for name, option in [('origin','origin'), ('repository','repository'), ('requested_digest','digest'),
        ('architecture','architecture'), ('expected_key_sha256','key-sha256'), ('public_key','public-key'),
        ('cosign','cosign'), ('output','output'), ('token_env','token-env'), ('ca_bundle','ca-bundle')]:
        cli += ['--' + option, str(a[name])]
    for predicate in PREDICATES:
        cli += ['--required-predicate', predicate]
    assert main(cli) == 2
    captured = capsys.readouterr()
    assert not captured.err
    assert json.loads(captured.out)['verification'] == 'pass'
    assert TOKEN not in captured.out and SECRET not in captured.out


def test_required_acquisition_and_crypto_trust_gaps_survive_composition(registry, lab, tmp_path):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    result = verify_registry_image(**arguments(registry, data, lab, tmp_path / 'gaps'))
    assert {'legacy-storage-convention-unqualified', 'image-layer-content-not-read',
            'transparency-log-unverified', 'official-image-origin-unverified'} <= set(result['gaps'])
    assert result['verification'] == 'pass' and result['build_authority'] == 'not-issued'


def test_normalized_origin_uses_the_same_signed_repository(registry, lab, tmp_path):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    args = arguments(registry, data, lab, tmp_path / 'normalized')
    args['origin'] += '/'
    result = verify_registry_image(**args)
    assert result['verification'] == 'pass'


def test_existing_output_fails_before_authentication(registry, lab, tmp_path):
    data = install(registry)
    output = tmp_path / 'already exists'
    output.mkdir()
    with pytest.raises(ValueError):
        verify_registry_image(**arguments(registry, data, lab, output))
    assert not registry['requests'] and not list(output.iterdir())


@pytest.mark.parametrize('damage', ['absolute-reference', 'unlisted-reference', 'wrong-name-hash', 'changed-body'])
def test_every_selection_must_name_a_frozen_content_addressed_artifact(registry, lab, tmp_path, monkeypatch, damage):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    original = adapter.collect_registry_evidence
    def altered(*args, **kwargs):
        result = original(*args, **kwargs)
        signature = result['observations']['signatures'][0]
        if damage == 'absolute-reference':
            signature['payload_artifact'] = '/unapproved/payload.json'
        elif damage == 'unlisted-reference':
            signature['payload_artifact'] = 'sha256-' + '0' * 64 + '.blob'
        elif damage == 'wrong-name-hash':
            name = signature['payload_artifact']
            wrong = 'sha256-' + '0' * 64 + '.blob'
            (kwargs['output'] / name).rename(kwargs['output'] / wrong)
            result['artifacts'][wrong] = result['artifacts'].pop(name)
            signature['payload_artifact'] = wrong
        else:
            path = kwargs['output'] / signature['payload_artifact']
            path.write_bytes(path.read_bytes() + b' ')
        return result
    monkeypatch.setattr(adapter, 'collect_registry_evidence', altered)
    monkeypatch.setattr(adapter, 'verify_image_signature', lambda **_: pytest.fail('Unsafe reference reached cryptographic verification'))
    args = arguments(registry, data, lab, tmp_path / 'unsafe')
    if damage in {'wrong-name-hash', 'changed-body'}:
        with pytest.raises(ValueError):
            verify_registry_image(**args)
        assert not args['output'].exists()
    else:
        result = verify_registry_image(**args)
        assert result['verification'] == 'unknown'
        assert 'verification-artifact-reference-invalid' in result['gaps']


def test_late_publication_withdraws_terminal_manifest(registry, lab, tmp_path, monkeypatch):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    original, monotonic = adapter._write, adapter.time.monotonic
    offset = 0
    monkeypatch.setattr(adapter.time, 'monotonic', lambda: monotonic() + offset)
    def late(directory, name, value):
        nonlocal offset
        result = original(directory, name, value)
        if name == 'image-manifest.json':
            offset = 301
        return result
    monkeypatch.setattr(adapter, '_write', late)
    args = arguments(registry, data, lab, tmp_path / 'late')
    with pytest.raises(ValueError):
        verify_registry_image(**args)
    assert not (args['output'] / 'image-manifest.json').exists()


def test_cli_authentication_denied_is_specific_and_sanitized(registry, lab, tmp_path, monkeypatch, capsys):
    data = install(registry)
    a = arguments(registry, data, lab, tmp_path / 'denied')
    monkeypatch.setenv('REGISTRY_TEST_TOKEN', 'DO-NOT-EXPORT-WRONG-TOKEN')
    cli = []
    for name, option in [('origin','origin'), ('repository','repository'), ('requested_digest','digest'),
        ('architecture','architecture'), ('expected_key_sha256','key-sha256'), ('public_key','public-key'),
        ('cosign','cosign'), ('output','output'), ('token_env','token-env'), ('ca_bundle','ca-bundle')]:
        cli += ['--' + option, str(a[name])]
    for predicate in PREDICATES:
        cli += ['--required-predicate', predicate]
    assert main(cli) == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report['verification'] == 'unknown' and report['build_authority'] == 'not-issued'
    assert 'registry-denied' in report['gaps']
    assert len(registry['requests']) == 1
    assert 'DO-NOT-EXPORT' not in captured.out + captured.err and SECRET not in captured.out + captured.err


def test_explicit_identity_of_the_wrong_key_does_not_hide_crypto_failure(registry, lab, tmp_path):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    args = arguments(registry, data, lab, tmp_path / 'wrong trusted identity')
    args['public_key'] = lab[0] / 'wrong.pub'
    args['expected_key_sha256'] = hashlib.sha256(args['public_key'].read_bytes()).hexdigest()
    result = verify_registry_image(**args)
    assert result['checks']['image_signature'] == result['checks']['required_attestations'] == 'fail'
    assert result['verification'] == 'fail' and result['decision'] == 'unknown'
    assert result['build_authority'] == 'not-issued'
    assert 'signature-verification-failed' in result['gaps']
    assert 'attestation-signature-invalid' in result['gaps']
