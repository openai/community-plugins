"""Fixed provisioning/cleanup caller, scripted GitLab, no live credentials.

Canonical lock/package and bootstrap admission are explicit controlled seams;
identity/secret issuance, durable intents, one-attempt behavior and cleanup run.
"""
from datetime import datetime, timedelta, timezone
import os
import sys
import time
from types import ModuleType, SimpleNamespace

import pytest

from test_factory_lifecycle import inputs_for
from test_owned_git_writer_contract import selection
from devsecops.factory.gitlab_api import GitLabAPIError, GitLabSession
from devsecops.factory.lifecycle import PhaseContext
from devsecops.factory.owned_cleanup import OwnedCleanupCoordinator
from devsecops.factory.owned_linux import OwnedLinuxDriver
from devsecops.factory.owned_writer_identity import (OwnedDeliveryWriter, require_owned_writer_origin, _writer_secret)
from devsecops.factory.state import RunJournal, StateError, _canonical, _sha


@pytest.fixture
def ready(tmp_path,monkeypatch):
    inputs,identities=inputs_for(tmp_path)
    inputs['config']['expires_at']=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    raw={name:_canonical(value) if isinstance(value,dict) else value.read_bytes() for name,value in inputs.items()}
    identities={name+'_sha256':_sha(value) for name,value in raw.items()}
    journal=RunJournal.create(tmp_path/'journal',identities=identities,environment_id=inputs['config']['environment'])
    journal.initialize_lifecycle(inputs=raw,execution_mode='operator',driver_id='owned-linux')
    driver=OwnedLinuxDriver(journal,plugin_id='devsecops@test',plugin_version='test')
    cleanup=OwnedCleanupCoordinator(driver)
    context=PhaseContext(journal.state['run_id'],{'id':'provision','operation':'up'},identities,inputs,{},
                         journal.run_dir,1e20,'controlled-test')
    def admit(context,action,operation_id=None):
        assert context.run_id==journal.state['run_id'] and action in ('up','down')
        return operation_id or ('controlled-up' if action=='up' else 'controlled-down')
    monkeypatch.setattr(cleanup,'_admit',admit)
    cleanup._sealed='controlled-up'
    cleanup._deferred.add('writer')
    session=object.__new__(GitLabSession)
    session._origin='https://git.example.invalid'
    session._expected_user_id=1
    session._poisoned=False
    calls=[]
    resource={'user':None,'member':None,'token':None,'fault':None}
    def request(method,path,payload=None,expected=(200,)):
        calls.append((method,path))
        if resource['fault']==method+' '+path:
            raise GitLabAPIError('controlled-unknown',outcome='unknown')
        if method=='POST' and path=='/api/v4/users':
            assert payload['admin'] is False and payload['projects_limit']==0 and payload['can_create_group'] is False
            assert len(payload['password'])>=32
            resource['user']={k:v for k,v in payload.items() if k!='password'}|{'id':17,'is_admin':False,'state':'active'}
            return dict(resource['user'])
        if method=='POST' and path.endswith('/members'):
            assert payload=={'user_id':17,'access_level':30}
            resource['member']={'id':17,'access_level':30}
            return dict(resource['member'])
        if method=='POST' and path.endswith('/personal_access_tokens'):
            assert payload['scopes']==['read_api','write_repository']
            resource['token']={**payload,'id':91,'user_id':17,'revoked':False,'active':True}
            return dict(resource['token'],token='SYNTHETIC_WRITER_SECRET_CANARY')
        if method=='GET':
            row=(resource['user'] if path=='/api/v4/users/17' else
                 resource['member'] if path=='/api/v4/projects/37/members/17' else
                 resource['token'] if path=='/api/v4/personal_access_tokens/91' else None)
            if row is None:
                raise GitLabAPIError('http-status',404)
            return dict(row)
        if method=='DELETE':
            if path.endswith('personal_access_tokens/91'):
                resource['token']['revoked']=True
            elif path.endswith('members/17'):
                resource['member']=None
            elif path=='/api/v4/users/17':
                resource['user']=None
            else:
                pytest.fail('Unexpected controlled removal')
            return None
        pytest.fail('Unexpected controlled writer request')
    monkeypatch.setattr(session,'request',request)
    monkeypatch.setattr(session,'verify_identity',lambda:{'principal':{'id':1,'is_admin':True}})
    monkeypatch.setattr(session,'get_collection',lambda path:[])
    # Explicit transport-renewal seam; API tests cover real shared poison/secret.
    monkeypatch.setattr(session,'renew_for_operation',lambda:session)
    monkeypatch.setattr(session,'bounded_read_session',lambda:session)
    bootstrap=object()
    module=ModuleType('devsecops.factory.canonical_bootstrap')
    def require(authority,actual,*,deadline=None):
        assert authority is bootstrap and actual is journal
        if deadline is not None:
            assert deadline > time.monotonic()
        return {'origin':session.origin,'owner_user_id':1,'namespace_id':20,'projects':{'deployment':{'id':37,'path_with_namespace':'group/deploy'}}}
    module.require_bootstrap_authority=require
    monkeypatch.setitem(sys.modules,'devsecops.factory.canonical_bootstrap',module)
    policy=selection()
    policy.update(run_scope=identities,environment_id=journal.state['environment_id'])
    path=tmp_path/'writer-policy.json'
    path.write_bytes(_canonical(policy))
    component=OwnedDeliveryWriter(journal,bootstrap_authority=bootstrap,session=session,policy_path=path,
        policy_sha256=_sha(path.read_bytes()),cleanup=cleanup)
    cleanup.register_writer(component)
    return SimpleNamespace(component=component,cleanup=cleanup,context=context,journal=journal,bootstrap=bootstrap,
        session=session,calls=calls,resource=resource,policy=path,inputs=inputs)


def test_canonical_component_up_issues_separate_writer_and_down_revokes_original(ready):
    r=ready
    outcome=r.cleanup.writer_up(r.context)
    assert outcome.status=='completed'
    origin=r.component.origin()
    scope=dict(journal=r.journal,bootstrap_authority=r.bootstrap,policy_sha256=_sha(r.policy.read_bytes()))
    facts=require_owned_writer_origin(origin,**scope)
    assert facts['user_id']==17 and facts['token_id']==91 and facts['access_level']==30
    assert _writer_secret(origin,**scope)=='SYNTHETIC_WRITER_SECRET_CANARY'
    assert all(b'SYNTHETIC_WRITER_SECRET_CANARY' not in p.read_bytes() for p in r.component.output.iterdir())
    down=r.component.down(r.context)
    report=r.component.cleanup_observation(down)
    assert report['complete_cleanup'] and all(row['state']=='absent' for row in report['resources'])
    assert [path for method,path in r.calls if method=='DELETE']==[
        '/api/v4/personal_access_tokens/91','/api/v4/projects/37/members/17','/api/v4/users/17']
    with pytest.raises(StateError):
        require_owned_writer_origin(origin,**scope)


def test_uncertain_user_creation_is_retained_and_not_adopted_or_retried(ready):
    r=ready
    r.resource['fault']='POST /api/v4/users'
    with pytest.raises(GitLabAPIError):
        r.cleanup.writer_up(r.context)
    assert (r.component.output/'create-user-intent.json').is_file()
    with pytest.raises(StateError):
        r.component.up(r.context)
    report=r.component.cleanup_observation(r.component.down(r.context))
    assert not report['complete_cleanup'] and report['resources'][0]['state']=='create-unknown'
    assert sum(method=='POST' for method,_ in r.calls)==1 and not any(method=='DELETE' for method,_ in r.calls)


def test_unknown_delete_reconciles_reads_only_without_reusing_attempt(ready):
    r=ready
    r.cleanup.writer_up(r.context)
    r.resource['fault']='DELETE /api/v4/personal_access_tokens/91'
    with pytest.raises(GitLabAPIError):
        r.component.down(r.context)
    r.resource['fault']=None
    r.resource['token']['revoked']=True
    before=len(r.calls)
    result=r.component.reconcile(r.context,'controlled-down')
    assert not r.component.cleanup_observation(result)['complete_cleanup']
    assert all(method=='GET' for method,_ in r.calls[before:])
    assert sum(method=='DELETE' for method,_ in r.calls)==1


@pytest.mark.parametrize('change',['policy','event','process','scope'])
def test_changed_origin_binding_cannot_supply_writer_secret(ready,monkeypatch,change):
    r=ready
    r.cleanup.writer_up(r.context)
    origin=r.component.origin()
    scope=dict(journal=r.journal,bootstrap_authority=r.bootstrap,policy_sha256=r.component.policy_sha256)
    if change=='policy':
        r.policy.write_bytes(r.policy.read_bytes()+b'\n')
    elif change=='event':
        next(iter(r.component._events)).write_bytes(b'changed')
    elif change=='process':
        monkeypatch.setattr(os,'getpid',lambda:r.component.pid+1)
    else:
        scope['bootstrap_authority']=object()
    with pytest.raises(StateError):
        _writer_secret(origin,**scope)
