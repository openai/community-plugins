"""Real local TLS and Cosign exercise every parser-policy handoff; not P1 acceptance."""
import base64
import hashlib
import json

import pytest

from devsecops.adapters import image_policy
from test_attestations import lab
from test_image_policy import arguments, make_policy
from test_image_verification import sign_registry_objects
from test_registry_evidence import registry, install, REPOSITORY, PREDICATES, digest, encode
from test_registry_redirect_transport import tls_material as ca

__all__ = ['ca', 'registry', 'lab']

SYFT = 'https://github.com/anchore/syft#output-formats'


@pytest.mark.parametrize('version', [None, 1, 2, 3])
def test_large_fragment_predicate_acquires_verifies_and_reassesses_with_same_policy(
        registry, lab, tmp_path, version):
    # Size policies require a DNS origin; retain real hostname/TLS validation.
    registry['origin'] = registry['origin'].replace('127.0.0.1', 'localhost')
    data = install(registry, predicates=[SYFT, PREDICATES[1]])
    descriptor = data['att']['layers'][0]
    blob_prefix = '/v2/' + REPOSITORY + '/blobs/'
    envelope = json.loads(registry['routes'][blob_prefix + descriptor['digest']]['raw'])
    statement = json.loads(base64.b64decode(envelope['payload']))
    statement['predicate']['values'] = [None] * 100_001
    if version == 3:
        statement['predicate']['values'] = [None] * 326_200
        statement['predicate']['padding'] = 'x' * 5_500_000
    envelope['payload'] = base64.b64encode(encode(statement)).decode()
    raw = encode(envelope)
    descriptor.update(digest=digest(raw), size=len(raw))
    registry['routes'][blob_prefix + descriptor['digest']] = {
        'raw': raw, 'media': descriptor['mediaType'], 'headers': {'Docker-Content-Digest': digest(raw)}}
    # Sign the changed statement with the test key, preserving the actual PAE.
    sign_registry_objects(registry, data, lab)
    signed_descriptor = data['att']['layers'][0]
    operator_policy = make_policy(registry, data, lab)
    operator_policy['predicates'][0]['type'] = SYFT
    args = arguments(tmp_path, registry, data, lab, operator_policy)
    if version is not None:
        selected = {'digest': signed_descriptor['digest'], 'size_bytes': signed_descriptor['size']}
        if version in (2, 3):
            selected['max_payload_json_values'] = 350_000 if version == 3 else 300_000
        policy = {'schema_version': version, 'kind': 'registry-attestation-size-policy',
            'origin': registry['origin'], 'repository': REPOSITORY, 'subject_digest': data['requested'],
            'descriptors': [selected]}
        path = tmp_path / 'bounds.json'
        path.write_bytes(encode(policy))
        args.update(attestation_size_policy=path,
                    attestation_size_policy_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    result = image_policy.verify_registry_image_policy(**args)
    acquisition = json.loads((args['output'] / 'verification/registry-acquisition.json').read_bytes())
    observed = acquisition['observations']
    if version in (2, 3):
        assert result['policy_satisfaction'] == 'pass'
        assert all(check == 'pass' for check in result['checks'].values())
        assert result['attestation_size_policy']['schema_version'] == version
        if version == 3:
            assert signed_descriptor['size'] > 8 * 1024**2
        assert observed['attestations'][0]['predicate_type'] == SYFT
        assert observed['acquisition']['decision'] == 'pass'
    else:
        assert result['policy_satisfaction'] == 'unknown'
        assert result['checks']['predicate_rules'] == 'unknown'
        assert observed['acquisition']['decision'] == 'unknown'
        assert observed['attestations'][0]['parse_failure_code'] == 'attestation-payload-json-value-limit'
        assert observed['required_predicates'][0]['selection'] == 'unresolved'
        codes = {row['code'] for row in acquisition['gaps']}
        assert 'registry-required-predicate-unresolved' in codes
        assert 'registry-required-predicate-missing' not in codes
    assert result['decision'] == 'unknown' and result['build_authority'] == 'not-issued'
