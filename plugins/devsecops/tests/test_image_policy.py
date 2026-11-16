"""Explicit policy against actual local TLS bytes and real Cosign signatures."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from devsecops.adapters import image_policy as adapter
from test_image_verification import sign_registry_objects
from test_attestations import lab
from test_registry_evidence import registry, ca, install, REPOSITORY, PREDICATES, SECRET, TOKEN, encode
import test_registry_evidence as registry_fixture
from test_oci import chain
from scripts.assess_registry_image import main

__all__ = ['registry', 'ca', 'lab']


def make_policy(registry, data, lab):
    now = datetime.now(timezone.utc)
    def stamp(value):
        return value.strftime('%Y-%m-%dT%H:%M:%SZ')
    return {'schema_version': 1, 'kind': 'registry-image-policy',
        'subject': {'origin': registry['origin'], 'repository': REPOSITORY,
                    'digest': data['requested'], 'architecture': 'amd64'},
        'signer': {'key_sha256': hashlib.sha256((lab[0] / 'lab.pub').read_bytes()).hexdigest(),
                   'valid_from': stamp(now - timedelta(minutes=1)), 'valid_until': stamp(now + timedelta(hours=1))},
        'predicates': [{'type': kind, 'rules': [{'path': '/private', 'equals': SECRET}]} for kind in PREDICATES]}


def arguments(tmp_path, registry, data, lab, policy=None):
    policy = make_policy(registry, data, lab) if policy is None else policy
    raw = encode(policy)
    path = tmp_path / 'operator-policy.json'
    path.write_bytes(raw)
    return dict(policy_path=path, expected_policy_sha256=hashlib.sha256(raw).hexdigest(),
        public_key=lab[0] / 'lab.pub', cosign=lab[1], output=tmp_path / 'assessment',
        token_env='REGISTRY_TEST_TOKEN', ca_bundle=registry['ca'])


def test_actual_registry_crypto_and_policy_do_not_issue_base_authority(registry, lab, tmp_path):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    result = adapter.verify_registry_image_policy(**arguments(tmp_path, registry, data, lab))
    assert result['policy_satisfaction'] == 'pass'
    assert all(value == 'pass' for value in result['checks'].values())
    assert result['decision'] == 'unknown' and result['build_authority'] == 'not-issued'
    assert len(result['predicates']) == 2
    assert 'runtime-compatibility-unqualified' in result['gaps']
    assert 'policy-window-is-not-certificate-lifecycle' in result['gaps']
    raw = (tmp_path / 'assessment/policy-assessment.json').read_bytes()
    assert SECRET.encode() not in raw and TOKEN.encode() not in raw
    assert json.loads(raw) == result


def test_mismatch_in_one_signed_required_predicate_fails_policy(registry, lab, tmp_path):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    policy = make_policy(registry, data, lab)
    policy['predicates'][1]['rules'][0]['equals'] = 'different'
    result = adapter.verify_registry_image_policy(**arguments(tmp_path, registry, data, lab, policy))
    assert result['checks']['cryptography'] == 'pass'
    assert result['checks']['predicate_rules'] == result['policy_satisfaction'] == 'fail'
    assert [row['decision'] for row in result['predicates']] == ['pass', 'fail']


@pytest.mark.parametrize('damage', ['hash', 'expired', 'future', 'wide-window', 'empty-rules', 'duplicate-type', 'bad-pointer', 'origin', 'schema-bool'])
def test_policy_rejected_before_credentials(registry, lab, tmp_path, damage):
    data = install(registry)
    policy = make_policy(registry, data, lab)
    if damage == 'expired':
        policy['signer'].update(valid_from='2020-01-01T00:00:00Z', valid_until='2020-01-02T00:00:00Z')
    elif damage == 'future':
        policy['signer'].update(valid_from='2099-01-01T00:00:00Z', valid_until='2099-01-02T00:00:00Z')
    elif damage == 'wide-window':
        policy['signer'].update(valid_from='2020-01-01T00:00:00Z', valid_until='2099-01-02T00:00:00Z')
    elif damage == 'empty-rules':
        policy['predicates'][0]['rules'] = []
    elif damage == 'duplicate-type':
        policy['predicates'][1]['type'] = PREDICATES[0]
    elif damage == 'bad-pointer':
        policy['predicates'][0]['rules'][0]['path'] = '/bad~2path'
    elif damage == 'origin':
        policy['subject']['origin'] += '/'
    elif damage == 'schema-bool':
        policy['schema_version'] = True
    args = arguments(tmp_path, registry, data, lab, policy)
    if damage == 'hash':
        args['expected_policy_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        adapter.verify_registry_image_policy(**args)
    assert not registry['requests'] and not args['output'].exists()


def test_unverified_signed_bytes_never_reach_rule_evaluation(registry, lab, tmp_path, monkeypatch):
    data = install(registry)
    monkeypatch.setattr(adapter, '_matches', lambda *_: pytest.fail('Unverified bytes reached policy'))
    result = adapter.verify_registry_image_policy(**arguments(tmp_path, registry, data, lab))
    assert result['checks']['cryptography'] != 'pass'
    assert result['checks']['predicate_rules'] == 'unknown' and result['predicates'] == []


@pytest.mark.parametrize('onbuild,expected', [(None, 'pass'), ([], 'pass'), (['RUN touch /outside'], 'fail'),
    ('', 'fail'), ({}, 'fail'), (False, 'fail')])
def test_fixed_build_recipe_cannot_inherit_onbuild(onbuild, expected):
    assert adapter._config({'os': 'linux', 'architecture': 'amd64', 'config': {'OnBuild': onbuild}}, 'amd64') == expected


@pytest.mark.parametrize('config', [
    {'onbuild': ['RUN true']}, {'ONBUILD': ['RUN true'], 'OnBuild': []},
    {'OnBuild': [], 'onBuild': ['RUN true']},
])
def test_go_decoder_onbuild_aliases_are_rejected(config):
    assert adapter._config({'os': 'linux', 'architecture': 'amd64', 'config': config}, 'amd64') == 'fail'


@pytest.mark.parametrize('alias,value', [('Config', {'OnBuild': ['RUN true']}), ('OS', 'windows'), ('Architecture', 'arm64')])
def test_go_decoder_root_aliases_are_rejected(alias, value):
    document = {'os': 'linux', 'architecture': 'amd64', 'config': {}, alias: value}
    assert adapter._config(document, 'amd64') == 'fail'


def test_large_candidate_value_is_not_repeatedly_materialized():
    assert not adapter._matches({'large': ['x'] * 100_000}, {'path': '/large', 'equals': ['x']})


def test_rule_evaluation_checks_deadline_during_traversal_and_comparison():
    calls = 0
    def expired():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise ValueError('deadline')
    with pytest.raises(ValueError, match='deadline'):
        adapter._matches({'x': [1, 2, 3, 4]}, {'path': '/x', 'equals': [1, 2, 3, 4]}, expired)


def test_signed_image_with_decoder_alias_fails_actual_policy_chain(registry, lab, tmp_path, monkeypatch):
    def configured():
        return chain(config_change=lambda value: value['config'].update(onbuild=['RUN true']))
    monkeypatch.setattr(registry_fixture, 'chain', configured)
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    result = adapter.verify_registry_image_policy(**arguments(tmp_path, registry, data, lab))
    assert result['checks']['cryptography'] == 'pass'
    assert result['checks']['image_config'] == result['policy_satisfaction'] == 'fail'


def test_cli_retains_unknown_authority_after_real_success(registry, lab, tmp_path, capsys):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    args = arguments(tmp_path, registry, data, lab)
    cli = []
    for option, name in [('policy', 'policy_path'), ('policy-sha256', 'expected_policy_sha256'),
        ('public-key', 'public_key'), ('cosign', 'cosign'), ('output', 'output'),
        ('token-env', 'token_env'), ('ca-bundle', 'ca_bundle')]:
        cli.extend(['--' + option, str(args[name])])
    assert main(cli) == 2
    output = capsys.readouterr()
    assert not output.err
    result = json.loads(output.out)
    assert result['policy_satisfaction'] == 'pass' and result['build_authority'] == 'not-issued'
    assert TOKEN not in output.out and SECRET not in output.out


@pytest.mark.parametrize('document,rule,expected', [
    ({'a/b': {'~x': [True]}}, {'path': '/a~1b/~0x/0', 'equals': True}, True),
    ({'a': True}, {'path': '/a', 'equals': 1}, False),
    ({'a': 1.0}, {'path': '/a', 'equals': 1}, False),
    ({'a': [1]}, {'path': '/a/00', 'equals': 1}, False),
    ({'a': [1]}, {'path': '/a/-1', 'equals': 1}, False),
    ({'a': [1]}, {'path': '/a/1', 'equals': 1}, False),
    ({'': {'x': None}}, {'path': '//x', 'equals': None}, True),
    ({}, {'path': '/missing', 'equals': None}, False),
])
def test_exact_pointer_rules_preserve_json_types(document, rule, expected):
    assert adapter._matches(document, rule) is expected


def test_post_verification_artifact_tampering_is_rejected(registry, lab, tmp_path, monkeypatch):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    original = adapter.verify_registry_image
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (kwargs['output'] / 'required-attestations.json').write_text('{}')
        return result
    monkeypatch.setattr(adapter, 'verify_registry_image', changed)
    with pytest.raises(ValueError, match='artifact changed'):
        adapter.verify_registry_image_policy(**arguments(tmp_path, registry, data, lab))
    assert not (tmp_path / 'assessment/policy-assessment.json').exists()


def test_expired_policy_at_publication_withdraws_result(registry, lab, tmp_path, monkeypatch):
    data = install(registry)
    sign_registry_objects(registry, data, lab)
    calls = 0
    def validity(_):
        nonlocal calls
        calls += 1
        return calls < 3
    monkeypatch.setattr(adapter, '_valid_now', validity)
    with pytest.raises(ValueError, match='expired during publication'):
        adapter.verify_registry_image_policy(**arguments(tmp_path, registry, data, lab))
    assert not (tmp_path / 'assessment/policy-assessment.json').exists()
