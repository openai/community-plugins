"""Explicit large-envelope selection preserves legacy and unrelated limits."""
from copy import copy

import pytest

from devsecops.adapters import attestations, registry_attestation_policy as policy
from devsecops.adapters.http import (
    APIError, HTTPSReadClient, _attestation_body_allowance, _attestation_body_limit, _json,
)
from test_registry_attestation_limits import BLOB, OTHER, SCOPE, document, envelope, parse


def selected(version=3, size=10_339_372, json_values=300_000):
    row = {'digest': BLOB, 'size_bytes': size}
    if version != 1:
        row['max_payload_json_values'] = json_values
    return parse(document([row], schema_version=version))


@pytest.mark.parametrize('version', [1, 2])
def test_legacy_policy_still_rejects_a_large_python_envelope(version):
    with pytest.raises(policy.AttestationPolicyError):
        selected(version)
    legacy = selected(version, size=8_388_608)
    assert legacy.limits_for(BLOB, 8_388_608) == (8_388_608, 6_291_456)


@pytest.mark.parametrize('size', [1, 10_339_372, 16_777_216])
def test_v3_explicit_descriptor_issues_only_its_exact_larger_allowance(size):
    value = selected(size=size)
    assert value.limits_for(BLOB, size) == (16_777_216, 12_582_912)
    assert value.limits_for(OTHER, 1) == (4_000_000, 2_500_000)
    assert value.payload_json_values_for(BLOB, size) == 300_000
    assert value.payload_json_values_for(OTHER, 1) == 100_000
    record = value.identity_record()
    assert record['schema_version'] == 3 and record['selected_envelope_bytes'] == 16_777_216
    assert record['selected_payload_bytes'] == 12_582_912
    blob = value.select_blob(BLOB, size)
    assert policy.validate_attestation_selection(blob, origin=SCOPE['origin'], repository=SCOPE['repository'],
        digest=BLOB, size_bytes=size) == 16_777_216
    assert value.select_blob(OTHER, 1) is None


@pytest.mark.parametrize('size', [0, -1, True, 16_777_217])
def test_v3_rejects_invalid_or_excessive_descriptor_sizes(size):
    with pytest.raises(policy.AttestationPolicyError):
        selected(size=size)


def test_v3_does_not_expand_descriptor_count_or_aggregate_budget():
    rows = [{'digest': 'sha256:' + format(i, '064x'), 'size_bytes': 16_777_216,
             'max_payload_json_values': 300_000} for i in range(4)]
    with pytest.raises(policy.AttestationPolicyError, match='total-limit'):
        parse(document(rows, schema_version=3))
    for row in rows:
        row['size_bytes'] = 1
    rows.extend({'digest': 'sha256:' + format(i, '064x'), 'size_bytes': 1,
                 'max_payload_json_values': 300_000} for i in range(4, 17))
    with pytest.raises(policy.AttestationPolicyError):
        parse(document(rows, schema_version=3))


@pytest.mark.parametrize('mutation', ['copy', 'maximum', 'policy', 'subject', 'size'])
def test_v3_blob_authority_cannot_be_copied_or_rebound(mutation):
    blob = selected().select_blob(BLOB, 10_339_372)
    if mutation == 'copy':
        blob = copy(blob)
    else:
        field, value = {'maximum': ('max_envelope_bytes', 32_000_000),
                        'policy': ('policy_sha256', '0' * 64),
                        'subject': ('subject_digest', OTHER),
                        'size': ('size_bytes', 10_339_373)}[mutation]
        object.__setattr__(blob, field, value)
    with pytest.raises(policy.AttestationPolicyError):
        policy.validate_attestation_selection(blob, origin=SCOPE['origin'], repository=SCOPE['repository'],
            digest=BLOB, size_bytes=10_339_372)


def test_v3_transport_remains_exact_and_does_not_widen_global_http_budget():
    blob = selected().select_blob(BLOB, 10_339_372)
    allowance = _attestation_body_allowance(blob, origin=SCOPE['origin'], repository=SCOPE['repository'],
        digest=BLOB, size_bytes=10_339_372)
    args = dict(origin=SCOPE['origin'], path='/v2/' + SCOPE['repository'] + '/blobs/' + BLOB,
                query='', as_json=False, stream=None)
    assert _attestation_body_limit(allowance, **args) == 10_339_372
    with pytest.raises(APIError):
        _attestation_body_limit(allowance, **{**args, 'path': '/v2/other/blobs/' + BLOB})
    with pytest.raises(APIError, match='invalid-configuration'):
        HTTPSReadClient(SCOPE['origin'], max_body_bytes=16_777_216)


def test_large_dsse_preserves_default_and_legacy_decoder_bounds():
    raw = envelope(padding=7_700_000)
    assert 8_388_608 < len(raw) < 16_777_216
    for maximum in (2_500_000, 6_291_456):
        with pytest.raises(ValueError):
            attestations._decode(raw, max_payload=maximum)
    statement, _, _, _ = attestations._decode(raw, max_payload=12_582_912, max_payload_json_values=300_000)
    assert len(statement['predicate']['padding']) == 7_700_000


@pytest.mark.parametrize('limit', [True, 99_999, 500_001])
def test_v3_json_budget_remains_explicit_and_bounded(limit):
    with pytest.raises(policy.AttestationPolicyError):
        selected(json_values=limit)


def test_v3_complexity_allowance_does_not_expand_version_two_or_http_json():
    with pytest.raises(policy.AttestationPolicyError):
        selected(version=2, size=8_000_000, json_values=350_000)
    with pytest.raises(APIError, match='invalid-json-limit'):
        _json(b'{}', max_values=350_000)
    value = selected(json_values=350_000)
    assert value.payload_json_values_for(BLOB, 10_339_372) == 350_000
    assert value.identity_record()['maximum_payload_json_values'] == 500_000


def test_version_two_decoder_cannot_receive_version_three_json_budget():
    with pytest.raises(ValueError) as error:
        attestations._decode(envelope(), max_payload=6_291_456, max_payload_json_values=350_000)
    assert error.value.code == 'attestation-json-allowance-invalid'
