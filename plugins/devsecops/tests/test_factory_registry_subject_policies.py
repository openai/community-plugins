"""One registry can retain distinct, immutable per-image attestation limits."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from devsecops.adapters.http import APIError
from devsecops.adapters.registry_attestation_policy import parse_attestation_size_policy
from devsecops.factory import case_supply_chain as case
from devsecops.factory.contracts import validate_document
from devsecops.factory.doctor import PACKAGE_ROOT
from devsecops.factory.state import StateError

ORIGIN = 'https://registry1.dso.mil'


def save(path, value):
    raw = (json.dumps(value, sort_keys=True) + '\n').encode()
    path.write_bytes(raw)
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def subjects(tmp_path):
    result = []
    for index, repository in enumerate(('ironbank/opensource/python', 'ironbank/opensource/nginx/nginx')):
        subject = {'origin': ORIGIN, 'repository': repository, 'digest': 'sha256:' + str(index + 1) * 64}
        allowance = {'schema_version': 3, 'kind': 'registry-attestation-size-policy',
                     'origin': ORIGIN, 'repository': repository, 'subject_digest': subject['digest'],
                     'descriptors': [{'digest': 'sha256:' + str(index + 3) * 64,
                                      'size_bytes': 5_000_001, 'max_payload_json_values': 250_000}]}
        ref = save(tmp_path / f'{index}.json', allowance)
        selected = {'registry_auth': {'token_env': 'REGISTRY_READ_TOKEN'}, 'attestation_size_policy': ref}
        result.append({'policy': {'subject': subject}, 'selected': selected, 'allowance': allowance, 'ref': ref})
    return result


def registry(subjects):
    return {'id': 'registry1', 'origin': ORIGIN, 'read_auth': {'mode': 'bearer', 'token_env': 'REGISTRY_READ_TOKEN'},
            'scope': {'repositories': [row['policy']['subject']['repository'] for row in subjects]},
            'attestation_size_policies': [row['ref'] for row in subjects]}


def config(service):
    value = json.loads((PACKAGE_ROOT / 'demo/software-factory/config/demo-config.example.json').read_bytes())
    value['services'] = [service]
    return value


def bind(row, service):
    case._bind_registry_read_scope(row['selected'], row['policy'], {'services': [service]})


@pytest.mark.parametrize('index', [0, 1])
def test_distinct_image_controls_share_one_configured_registry(subjects, index):
    service = registry(subjects)
    validate_document(config(service), 'factory-config')
    bind(subjects[index], service)


def test_single_policy_and_default_limit_backwards_compatibility(subjects):
    service = registry(subjects)
    del service['attestation_size_policies']
    service['attestation_size_policy'] = subjects[0]['ref']
    validate_document(config(service), 'factory-config')
    bind(subjects[0], service)
    del service['attestation_size_policy']
    selected = deepcopy(subjects[0])
    del selected['selected']['attestation_size_policy']
    bind(selected, service)


@pytest.mark.parametrize('change', ['both', 'empty', 'over-limit', 'non-registry', 'unscoped', 'parent', 'duplicate', 'duplicate-path'])
def test_configuration_rejects_ambiguous_or_unbounded_policy_lists(subjects, change):
    service = registry(subjects)
    if change == 'both':
        service['attestation_size_policy'] = subjects[0]['ref']
    elif change == 'empty':
        service['attestation_size_policies'] = []
    elif change == 'over-limit':
        service['attestation_size_policies'] = [{'path': '/private/policy-' + str(i), 'sha256': f'{i:064x}'} for i in range(17)]
    elif change == 'non-registry':
        service['id'] = 'gitlab'
    elif change == 'unscoped':
        service['scope']['repositories'] = []
    elif change == 'parent':
        service['attestation_size_policies'][0] = {'path': '/private/../policy', 'sha256': 'a' * 64}
    elif change == 'duplicate':
        service['attestation_size_policies'].append(deepcopy(subjects[0]['ref']))
    else:
        service['attestation_size_policies'].append({**subjects[0]['ref'], 'sha256': 'f' * 64})
    with pytest.raises(ValueError):
        validate_document(config(service), 'factory-config')


@pytest.mark.parametrize('change', ['unregistered', 'absent', 'wrong-subject', 'changed-bytes', 'operator-credential', 'duplicate-subject', 'outside-origin', 'outside-repository'])
def test_controls_reject_unselected_changed_or_ambiguous_allowances(subjects, tmp_path, change):
    service = registry(subjects)
    chosen = deepcopy(subjects[0])
    if change == 'unregistered':
        chosen['selected']['attestation_size_policy'] = save(tmp_path / 'unregistered.json', subjects[0]['allowance'])
    elif change == 'absent':
        del chosen['selected']['attestation_size_policy']
    elif change == 'wrong-subject':
        chosen['selected']['attestation_size_policy'] = subjects[1]['ref']
    elif change == 'changed-bytes':
        Path(subjects[1]['ref']['path']).write_text('{}')
    elif change == 'operator-credential':
        chosen['selected']['registry_auth']['token_env'] = 'REGISTRY_OPERATOR_TOKEN'
    else:
        value = deepcopy(subjects[0 if change == 'duplicate-subject' else 1]['allowance'])
        if change == 'outside-origin':
            value['origin'] = 'https://unrelated.example'
        elif change == 'outside-repository':
            value['repository'] = 'unselected/repository'
        service['attestation_size_policies'][1] = save(tmp_path / 'other-policy.json', value)
    with pytest.raises((StateError, APIError, ValueError)):
        bind(chosen, service)


def test_per_image_allowances_do_not_merge_or_widen_defaults(subjects):
    service = registry(subjects)
    for index, row in enumerate(subjects):
        bind(row, service)
        limits = parse_attestation_size_policy(Path(row['ref']['path']).read_bytes(), row['ref']['sha256'])
        own = row['allowance']['descriptors'][0]
        other = subjects[1 - index]['allowance']['descriptors'][0]
        assert limits.payload_json_values_for(own['digest'], own['size_bytes']) == 250_000
        assert limits.limits_for('sha256:' + '9' * 64, 100) == (4_000_000, 2_500_000)
        with pytest.raises(APIError):
            limits.limits_for(other['digest'], other['size_bytes'])


def test_legacy_pin_cannot_silently_match_another_image(subjects):
    service = registry(subjects)
    del service['attestation_size_policies']
    service['attestation_size_policy'] = subjects[0]['ref']
    with pytest.raises(StateError):
        bind(subjects[1], service)
