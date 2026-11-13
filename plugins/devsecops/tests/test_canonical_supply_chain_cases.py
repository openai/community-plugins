"""Local subject and real offline crypto controls; not Iron Bank/live D4 evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import time

import pytest

from devsecops.factory import case_supply_chain as case
from devsecops.factory.cases_recipe import cases_configuration
from devsecops.factory.state import StateError, _canonical, _sha
from tests.test_attestations import lab, inputs, PREDICATES, REPOSITORY, DIGEST  # noqa: F401
from tests.test_vulnerability_policy import documents, large_scanner_file_inventory


def ref(path, value):
    raw = value if type(value) is bytes else _canonical(value)
    path.write_bytes(raw)
    return {'sha256': _sha(raw), 'bytes': len(raw)}


@pytest.fixture
def publication(tmp_path):
    root = tmp_path.resolve() / 'readback'
    root.mkdir()
    values = documents()
    source = values['source']
    observed = {'image': {'manifest_digest': source['manifest_digest'], 'config_digest': source['config_digest']},
                'source': {'sha256': source['archive_sha256']}}
    data = {'sbom.spdx.json': values['sbom'], 'grype.json': values['result'],
            'scanner-receipt.json': {'observations': observed}}
    refs = {'readback-' + name: ref(root / ('readback-' + name), value) for name, value in data.items()}
    binding = {'publication_path': str(root), 'publication_readback': refs,
               'manifest_digest': source['manifest_digest'], 'config_digest': source['config_digest'],
               'architecture': source['architecture'], 'archive': {'sha256': source['archive_sha256']},
               'sbom': refs['readback-sbom.spdx.json'], 'vulnerabilities': refs['readback-grype.json'],
               'scanner_receipt_sha256': refs['readback-scanner-receipt.json']['sha256'], 'source_commit': 'd' * 40}
    return root, data, binding


def test_derived_positive_and_independent_mismatched_subjects(publication, tmp_path):
    root, _, binding = publication
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    report = case.verify_derived_subjects(binding, output=tmp_path.resolve() / 'controls', deadline=time.monotonic() + 30)
    assert report['mismatched_copies_rejected'] == ['sbom', 'scan']
    assert report['scanner_execution'] == 'authenticated-ci-evidence-no-new-scanner-or-database-acquisition'
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


def test_large_sbom_keeps_independent_mismatched_subject_controls(publication, tmp_path):
    root, data, binding = publication
    data['sbom.spdx.json']['files'] = large_scanner_file_inventory()
    updated = ref(root / 'readback-sbom.spdx.json', data['sbom.spdx.json'])
    binding['publication_readback']['readback-sbom.spdx.json'] = updated
    binding['sbom'] = updated
    report = case.verify_derived_subjects(binding, output=tmp_path.resolve() / 'large-controls',
                                        deadline=time.monotonic() + 30)
    assert report['mismatched_copies_rejected'] == ['sbom', 'scan']


@pytest.mark.parametrize('mutation', [
    lambda b: b.update(manifest_digest='sha256:' + 'f' * 64),
    lambda b: b.update(config_digest='sha256:' + 'f' * 64),
    lambda b: b['archive'].update(sha256='f' * 64),
    lambda b: b.update(scanner_receipt_sha256='f' * 64),
    lambda b: b['sbom'].update(bytes=1),
])
def test_derived_changed_subject_chain_never_passes(publication, tmp_path, mutation):
    _, _, original = publication
    binding = deepcopy(original)
    mutation(binding)
    with pytest.raises((StateError, ValueError)):
        case.verify_derived_subjects(binding, output=tmp_path.resolve() / 'controls', deadline=time.monotonic() + 30)


def test_retained_ci_readback_changed_before_control_is_rejected(publication, tmp_path):
    root, _, binding = publication
    (root / 'readback-grype.json').write_text('{}')
    with pytest.raises(StateError, match='changed'):
        case.verify_derived_subjects(binding, output=tmp_path.resolve() / 'controls', deadline=time.monotonic() + 30)


def test_real_offline_crypto_negative_controls(inputs, tmp_path):  # noqa: F811
    path, binary = inputs
    control = {'subject': {'origin': 'https://' + REPOSITORY.split('/')[0],
                           'repository': REPOSITORY.split('/', 1)[1], 'digest': DIGEST},
               'key': path / 'lab.pub', 'envelopes': {predicate: path / (str(i) + '.json') for i, predicate in enumerate(PREDICATES)}}
    original = {str(p): p.read_bytes() for p in control['envelopes'].values()}
    result = case.verify_negative_attestations(control, untrusted_key=path / 'wrong.pub', cosign=binary,
                                               output=tmp_path.resolve() / 'controls', deadline=time.monotonic() + 90)
    assert {name: row['decision'] for name, row in result.items()} == {
        'valid': 'pass', 'missing': 'unknown', 'tampered': 'fail', 'untrusted': 'fail'}
    assert original == {str(p): p.read_bytes() for p in control['envelopes'].values()}


def test_valid_key_cannot_be_relabeled_as_untrusted_control(inputs, tmp_path):  # noqa: F811
    path, binary = inputs
    control = {'subject': {'origin': 'https://registry.example.test', 'repository': 'mission', 'digest': DIGEST},
               'key': path / 'lab.pub', 'envelopes': {predicate: path / (str(i) + '.json') for i, predicate in enumerate(PREDICATES)}}
    with pytest.raises(StateError, match='must differ'):
        case.verify_negative_attestations(control, untrusted_key=path / 'lab.pub', cosign=binary,
                                          output=tmp_path.resolve() / 'controls', deadline=time.monotonic() + 30)


@pytest.fixture
def subject():
    locked = {'repository': 'registry1.dso.mil/ironbank/reference', 'manifest_sha256': 'a' * 64,
              'platform_manifest_sha256': 'b' * 64, 'config_sha256': 'c' * 64, 'architecture': 'amd64',
              'required_predicates': ['https://example.test/predicate'], 'trust_sha256': 'd' * 64}
    selected = {'trust_identity_basis': 'publisher-policy-sha256-v1', 'publisher_policy': {'sha256': 'd' * 64},
                'public_key': {'sha256': 'e' * 64}}
    policy = {'subject': {'origin': 'https://registry1.dso.mil', 'repository': 'ironbank/reference',
                          'digest': 'sha256:' + 'a' * 64, 'architecture': 'amd64'},
              'predicates': [{'type': 'https://example.test/predicate'}], 'signer': {'key_sha256': 'e' * 64}}
    return locked, policy, selected


def test_versioned_trust_basis_and_all_locked_subjects_are_bound(subject):
    locked, policy, selected = subject
    assert case._subject(locked, policy, selected, 'amd64') == policy['subject']


def test_operator_package_age_expiry_cannot_be_extended_by_later_policy_or_signer():
    policy = {'validity': {'not_after': '2026-11-01T00:00:00Z'},
              'source': {'kind': 'operator-trusted-package', 'max_age_seconds': 10}}
    signer = {'signer': {'valid_until': '2026-11-01T00:00:00Z'}}
    trust = {'source': {'reviewed_at': '2026-10-30T00:00:00Z', 'max_age_seconds': 10}}
    assert case.authority_expiry(policy, signer, trust) == '2026-10-30T00:00:10+00:00'
    trust['source']['max_age_seconds'] = 20
    with pytest.raises(StateError, match='age policy'):
        case.authority_expiry(policy, signer, trust)


@pytest.mark.parametrize('expired_by', ['policy', 'selected-age'])
def test_d4_closure_rechecks_expiry_after_negative_work(expired_by):
    now = datetime.now(timezone.utc)
    control = {'observed_at': (now - timedelta(seconds=2)).isoformat(),
               'expires_at': (now + timedelta(seconds=30)).isoformat()}
    selected = {'max_verification_age_seconds': 60}
    case.require_current_controls([control], selected)
    if expired_by == 'policy':
        control['expires_at'] = (now - timedelta(seconds=1)).isoformat()
    else:
        selected['max_verification_age_seconds'] = 1
    with pytest.raises(StateError, match='expired'):
        case.require_current_controls([control], selected)


@pytest.mark.parametrize('target,key,value', [
    ('lock', 'trust_sha256', 'f' * 64), ('lock', 'repository', 'other.test/image'),
    ('lock', 'manifest_sha256', 'f' * 64), ('lock', 'architecture', 'arm64'),
    ('lock', 'required_predicates', ['https://other.test/predicate']),
    ('selection', 'trust_identity_basis', 'unspecified-legacy-trust'),
])
def test_changed_lock_or_implicit_trust_basis_is_rejected(subject, target, key, value):
    locked, policy, selected = subject
    (locked if target == 'lock' else selected)[key] = value
    with pytest.raises(StateError, match='release lock'):
        case._subject(locked, policy, selected, 'amd64')


def test_case_recipe_does_not_accept_arbitrary_commands_or_unsupported_version():
    with pytest.raises(ValueError, match='closed'):
        cases_configuration(_canonical({'schema_version': 1, 'kind': 'owned-cases-recipe', 'command': 'pretend-pass'}))


def test_derived_application_base_must_match_the_same_positive_control(subject):
    locked, _, _ = subject
    binding = {'base': {'repository': locked['repository'], 'requested_digest': 'sha256:' + locked['manifest_sha256'],
                       'platform_manifest_digest': 'sha256:' + locked['platform_manifest_sha256'],
                       'config_digest': 'sha256:' + locked['config_sha256']}}
    case._bind_derived_base(binding, locked)
    binding['base']['requested_digest'] = 'sha256:' + 'f' * 64
    with pytest.raises(StateError, match='mission base'):
        case._bind_derived_base(binding, locked)


def test_registry_controls_cannot_use_operator_identity_in_place_of_configured_read_identity(subject):
    _, policy, _ = subject
    config = {'services': [{'id': 'registry1', 'origin': policy['subject']['origin'],
                           'scope': {'repositories': [policy['subject']['repository']]},
                           'read_auth': {'mode': 'bearer', 'token_env': 'REGISTRY_READ_TOKEN'}}]}
    selected = {'registry_auth': {'token_env': 'REGISTRY_READ_TOKEN'}}
    case._bind_registry_read_scope(selected, policy, config)
    selected['registry_auth']['token_env'] = 'REGISTRY_OPERATOR_TOKEN'
    with pytest.raises(StateError, match='read identity'):
        case._bind_registry_read_scope(selected, policy, config)
