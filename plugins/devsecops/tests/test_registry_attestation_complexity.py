"""Bounded parsing and exact predicate identities; cryptography is synthetic here."""
from dataclasses import replace
import json

import pytest
import test_registry_attestation_limits as limit_tests

from devsecops.adapters import attestations as adapter
from devsecops.adapters import registry_attestation_policy as policy_module
from devsecops.adapters.http import APIError, _json
from test_registry_attestation_limits import (
    BLOB, OTHER, PREDICATE, SCOPE, descriptor, document, envelope, inputs, parse,
)

no_external_execution = limit_tests.no_external_execution
synthetic_crypto = limit_tests.synthetic_crypto

SYFT = 'https://github.com/anchore/syft#output-formats'


def complex_envelope(values):
    # This statement has ten container/scalar values outside the null elements.
    return envelope(nested={'values': [None] * (values - 10)})


def selected_policy(raw, limit=300_000):
    return parse(document([{**descriptor(raw), 'max_payload_json_values': limit}], schema_version=2))


@pytest.mark.parametrize('count,limit,accepted', [
    (100_000, 100_000, True), (100_001, 100_000, False),
    (300_000, 300_000, True), (300_001, 300_000, False),
])
def test_exact_json_value_boundaries(count, limit, accepted):
    raw = json.dumps({'values': [None] * (count - 2)}).encode()
    if accepted:
        assert len(_json(raw, max_values=limit)['values']) == count - 2
    else:
        with pytest.raises(APIError, match='invalid-json'):
            _json(raw, max_values=limit)


@pytest.mark.parametrize('limit', [True, '300000', 300000.0, -1, 99_999, 300_001])
def test_json_allowance_rejects_invalid_or_unbounded_limits(limit):
    with pytest.raises(APIError, match='invalid-json-limit'):
        _json(b'{}', max_values=limit)


@pytest.mark.parametrize('raw', [
    b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}',
    b'[]', b'{"x":"\xff"}',
])
@pytest.mark.parametrize('limit', [100_000, 300_000])
def test_selected_allowance_preserves_strict_json(raw, limit):
    with pytest.raises(APIError, match='invalid-json'):
        _json(raw, max_values=limit)


@pytest.mark.parametrize('limit', [100_000, 200_000, 300_000])
def test_v2_allowance_is_explicit_and_bound_to_issued_descriptor(limit):
    raw = envelope()
    policy = selected_policy(raw, limit)
    desc = descriptor(raw)
    assert policy.payload_json_values_for(desc['digest'], desc['size_bytes']) == limit
    assert policy.payload_json_values_for(OTHER, 1) == 100_000
    assert policy.identity_record()['descriptors'] == [{**desc, 'max_payload_json_values': limit}]
    assert policy.identity_record()['schema_version'] == 2
    assert policy.require_scope(**SCOPE) is policy
    with pytest.raises(policy_module.AttestationPolicyError):
        policy.payload_json_values_for(desc['digest'], desc['size_bytes'] + 1)


@pytest.mark.parametrize('limit', [True, '300000', 300000.0, -1, 99_999, 300_001, None])
def test_policy_rejects_unsafe_json_allowances(limit):
    with pytest.raises(policy_module.AttestationPolicyError):
        selected_policy(envelope(), limit)


@pytest.mark.parametrize('mutation', ['v1-allowance', 'missing-allowance', 'extra-field', 'duplicate', 'unknown-version'])
def test_policy_version_cannot_implicitly_grant_more_parsing(mutation):
    row = {**descriptor(envelope()), 'max_payload_json_values': 300_000}
    value = document([row], schema_version=2)
    if mutation == 'v1-allowance':
        value['schema_version'] = 1
    elif mutation == 'missing-allowance':
        row.pop('max_payload_json_values')
    elif mutation == 'extra-field':
        row['max_depth'] = 1000
    elif mutation == 'duplicate':
        value['descriptors'].append(dict(row))
    else:
        value['schema_version'] = 4
    with pytest.raises(policy_module.AttestationPolicyError):
        parse(value)


@pytest.mark.parametrize('field,value', [
    ('schema_version', 1), ('payload_json_values', (300_000,)),
    ('descriptors', ((BLOB, 1),)), ('policy_sha256', '0' * 64),
])
def test_policy_identity_covers_version_and_every_json_allowance(field, value):
    policy = selected_policy(envelope(), 100_000)
    object.__setattr__(policy, field, value)
    with pytest.raises(policy_module.AttestationPolicyError, match='unissued-or-changed'):
        policy.identity_record()


def test_copied_policy_and_mutated_v1_cannot_mint_complexity_authority():
    raw = envelope()
    for forged in (replace(selected_policy(raw)), parse(document([descriptor(raw)]))):
        object.__setattr__(forged, 'schema_version', 2)
        object.__setattr__(forged, 'payload_json_values', (300_000,))
        with pytest.raises(policy_module.AttestationPolicyError, match='unissued-or-changed'):
            forged.payload_json_values_for(**descriptor(raw))


@pytest.mark.parametrize('count,version,accepted', [
    (100_000, None, True), (100_001, None, False), (100_001, 1, False),
    (100_001, 2, True), (300_000, 2, True), (300_001, 2, False),
])
def test_production_verifier_applies_only_selected_v2_allowance(synthetic_crypto, count, version, accepted):
    raw = complex_envelope(count)
    args = inputs(synthetic_crypto, raw, selected=version is not None)
    if version == 2:
        args['attestation_size_policy'] = selected_policy(raw)
    report = adapter.verify_attestations(**args)
    assert report['observations']['verification']['decision'] == ('pass' if accepted else 'unknown')
    assert report['decision'] == 'unknown'
    assert any(call[0] == 'verify-blob' for call in synthetic_crypto[3]) is accepted
    if not accepted:
        assert 'attestation-payload-json-value-limit' in {gap['code'] for gap in report['gaps']}


def test_allowance_does_not_spill_into_unselected_envelopes(synthetic_crypto):
    raw = complex_envelope(100_001)
    args = inputs(synthetic_crypto, raw)
    args['attestation_size_policy'] = parse(document([
        {'digest': OTHER, 'size_bytes': 1, 'max_payload_json_values': 300_000}], schema_version=2))
    report = adapter.verify_attestations(**args)
    assert report['observations']['verification']['decision'] == 'unknown'
    assert not any(call[0] == 'verify-blob' for call in synthetic_crypto[3])


@pytest.mark.parametrize('damage', ['bytes', 'descriptor-digest', 'descriptor-size'])
def test_complexity_allowance_never_bypasses_content_identity(synthetic_crypto, damage):
    raw = complex_envelope(100_001)
    args = inputs(synthetic_crypto, raw)
    args['attestation_size_policy'] = selected_policy(raw)
    if damage == 'bytes':
        args['envelopes'][PREDICATE].write_bytes(raw.replace(b'payloadType', b'payloadTypf'))
    elif damage == 'descriptor-digest':
        args['envelope_descriptors'][PREDICATE]['digest'] = OTHER
    else:
        args['envelope_descriptors'][PREDICATE]['size_bytes'] += 1
    report = adapter.verify_attestations(**args)
    assert report['observations']['verification']['decision'] == 'unknown'
    assert not any(call[0] == 'verify-blob' for call in synthetic_crypto[3])


@pytest.mark.parametrize('depth,accepted', [(64, True), (65, False)])
def test_explicit_value_allowance_never_increases_depth(synthetic_crypto, depth, accepted):
    value = {'leaf': True}
    for _ in range(depth - 2):
        value = {'nested': value}
    raw = envelope(nested=value)
    args = inputs(synthetic_crypto, raw)
    args['attestation_size_policy'] = selected_policy(raw)
    report = adapter.verify_attestations(**args)
    assert report['observations']['verification']['decision'] == ('pass' if accepted else 'unknown')
    if not accepted:
        assert 'attestation-payload-json-depth-limit' in {gap['code'] for gap in report['gaps']}


@pytest.mark.parametrize('expected,accepted', [
    (SYFT, True), ('https://github.com/anchore/syft%23output-formats', False),
    ('https://github.com/anchore/syft#other', False),
    ('https://github.com/anchore/syft#OUTPUT-FORMATS', False),
    ('https://github.com/anchore/syft', False),
])
def test_fragment_identifier_is_never_removed_decoded_or_normalized(synthetic_crypto, expected, accepted):
    raw = envelope(predicate=SYFT)
    args = inputs(synthetic_crypto, raw)
    path = args['envelopes'][PREDICATE]
    args.update(required_predicates=[expected], envelopes={expected: path},
                envelope_descriptors={expected: descriptor(raw)})
    report = adapter.verify_attestations(**args)
    observed = report['observations']['predicates'][0]
    assert observed['crypto'] == observed['subject_binding'] == 'pass'
    assert observed['type_binding'] == ('pass' if accepted else 'fail')
    assert observed['verification'] == ('pass' if accepted else 'fail')


def test_negative_control_preserves_complexity_bound_and_rejects_tamper(synthetic_crypto):
    raw = complex_envelope(100_001)
    args = inputs(synthetic_crypto, raw)
    args['attestation_size_policy'] = selected_policy(raw)
    report = adapter.verify_negative_attestation_control(**args, output=synthetic_crypto[0] / 'negative-control')
    assert report['observations']['original_verification'] == 'pass'
    assert report['observations']['verification']['decision'] == 'fail'
    assert report['execution_status'] == 'complete'
    assert report['image_authority'] == 'not-issued'
