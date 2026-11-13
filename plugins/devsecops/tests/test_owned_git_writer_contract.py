"""Pure writer scope tests; no writer or lab credential is created or used."""
from datetime import datetime, timedelta, timezone
import pickle

import pytest

from devsecops.factory.owned_git_delivery import (ObservedGitDelivery, delivery_writer_configuration,
                                                 require_delivery)
from devsecops.factory.state import IDENTITY_KEYS, StateError, _canonical


def selection():
    return {'schema_version':1,'kind':'owned-git-writer-policy',
        'token_profile':'classic-personal-access-token','credential_role':'owned-run-delivery-writer','expected_gitlab_version':'19.5.3',
        'expected_project_access_level':30,'expected_scopes':['read_api','write_repository'],
        'target_ref_prefix':'refs/heads/owned-demo/','run_scope':dict.fromkeys(IDENTITY_KEYS,'a'*64),
        'environment_id':'owned-lab','expires_at':(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()}


def test_exact_policy_roundtrip():
    value=selection()
    assert delivery_writer_configuration(_canonical(value))==value


@pytest.mark.parametrize('field,value',[('expected_scopes',['api']),('expected_project_access_level',40),
    ('credential_role','root-service-token'),('schema_version',True),('expected_gitlab_version','latest'),
    ('expires_at','2026-10-30T00:00:00'),('run_scope',{}),('target_ref_prefix','refs/heads/'),
    ('token_env','unrecognized-env'),('extra','not-admitted')])
def test_wrong_or_broader_selection_rejected(field,value):
    policy=selection()
    policy[field]=value
    with pytest.raises(ValueError):
        delivery_writer_configuration(_canonical(policy))


def test_saved_delivery_and_unissued_object_are_not_origins():
    with pytest.raises(TypeError):
        ObservedGitDelivery()
    fake=object.__new__(ObservedGitDelivery)
    for value in ({'delivery_stage':'merged-watched-branch'},fake):
        with pytest.raises(StateError):
            require_delivery(value,journal=object())
    with pytest.raises(TypeError):
        pickle.dumps(fake)
