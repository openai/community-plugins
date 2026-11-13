"""Actual local Git and native preparation; controlled remote/F0 origin readers."""
from copy import deepcopy
from dataclasses import replace
import json
import os
import shutil
import threading
from types import SimpleNamespace

import pytest

from test_policy_git_source import correction, policy_case
from test_correction import git
from devsecops.factory import owned_checkout, owned_git_delivery, network_policy_qualification
from devsecops.factory.native_handoff import prepare_correction_scope
from devsecops.factory.owned_correction_contract import require_correction_contract
from devsecops.factory.state import StateError, _canonical, _sha

__all__ = ['correction', 'policy_case']


@pytest.fixture
def contract_case(policy_case, tmp_path, monkeypatch):
    case = policy_case
    ops, args, plan = case.ops, case.args, case.plan
    baseline_repo = tmp_path/'baseline-source'
    shutil.copytree(args.repo, baseline_repo)
    baseline_revision = git(args.repo, 'rev-parse', 'HEAD')
    ops.pid, ops._thread = os.getpid(), threading.get_ident()
    ops._deliveries, ops._source_checkouts, ops._correction_contracts, ops._derived_evidence = {}, {}, {}, {}
    ops.journal.state = {'run_id': case.context.run_id}
    (ops.journal.run_dir/'owned-operations').mkdir(parents=True)
    baseline_origin = object()
    ops._source_checkouts[('primary','baseline')] = baseline_origin
    ops._deliveries[('primary','baseline')] = baseline_delivery = object()
    checkouts = {baseline_origin:{'revision':baseline_revision, 'repository':str(baseline_repo)}}
    deliveries = {baseline_delivery:{'run_id':case.context.run_id, 'deployment_commit':baseline_revision,
        'watched_revision':baseline_revision, 'delivery_stage':'merged-watched-branch'}}
    origin = object()
    qualification = {'run_id':case.context.run_id, 'identities':dict(case.context.identities),
        'source_path':plan.source_path, 'source_commit':baseline_revision, 'resource_id':plan.resource_id,
        'pointer':plan.pointer, 'before':plan.before, 'after':plan.after, 'qualification_sha256':'f'*64}
    case.selection['qualification'] = qualification
    ops.runtime._network_policy_path = origin
    def qualified(value, **kwargs):
        assert value is origin and kwargs['runtime'] is ops.runtime
        assert kwargs['run_id'] == case.context.run_id and kwargs['identities'] == ops.journal.identities
        return deepcopy(qualification)
    monkeypatch.setattr(network_policy_qualification, 'require_qualified_network_policy_path', qualified)
    monkeypatch.setattr(owned_checkout, 'require_owned_checkout', lambda value, **kwargs: deepcopy(checkouts[value]))
    monkeypatch.setattr(owned_git_delivery, 'require_delivery', lambda value, **kwargs: deepcopy(deliveries[value]))
    del ops.delivery
    del ops.source_checkout
    native = ops._recipe()['native']
    template = {'schema_version':1, 'id':'controlled-qualified-policy', 'base_commit':'0'*40,
        'profile_sha256':native['profile']['sha256'], 'allowed_files':[plan.source_path],
        'allowed_rendered_changes':[{'resource':plan.resource_id,'pointers':[plan.pointer]}]}
    args.contract.write_bytes(_canonical(template))
    native['correction_contract'] = {'path':str(args.contract), 'sha256':_sha(args.contract.read_bytes())}
    def retain(attempt):
        repo = tmp_path/(attempt+'-fault')
        shutil.copytree(baseline_repo, repo)
        raw = b'metricsLabel: devsecops-f0-denied\n'
        (repo/plan.source_path).write_bytes(raw)
        git(repo,'add','--all')
        git(repo,'commit','-qm',attempt+' actual local fault')
        revision = git(repo,'rev-parse','HEAD')
        rendered = tmp_path/(attempt+'-rendered.json')
        rendered.write_bytes(_canonical({'resources':[{'id':plan.resource_id,'body':json.loads(plan.fault)}]}))
        checkout, delivery = object(), object()
        checkouts[checkout] = {'revision':revision,'repository':str(repo)}
        deliveries[delivery] = {'run_id':case.context.run_id,'attempt':attempt,'phase':attempt+'-inject',
            'operation_id':attempt+'-operation','parent':baseline_revision,'tree':git(repo,'rev-parse','HEAD^{tree}'),
            'deployment_commit':revision,'watched_revision':revision,'delivery_stage':'merged-watched-branch',
            'source_kind':'qualified-policy-change','rendered_path':str(rendered),'rendered_sha256':_sha(rendered.read_bytes()),
            'source_provenance':{'source_path':plan.source_path,'source_pointer':'/metricsLabel',
                'qualification_sha256':'f'*64,'previous_commit':baseline_revision,'phase':attempt+'-inject'},
            'selected_tree':{'files':[{'path':plan.source_path,'bytes':len(raw),'sha256':_sha(raw)}]}}
        monkeypatch.setattr(owned_checkout,'materialize_delivered_repository',lambda *a,**k:checkout)
        context=replace(case.context,phase={'id':attempt+'-inject','operation':'inject','attempt':attempt})
        ops._retain_delivery(context,delivery,attempt=attempt,stage='fault')
        return ops.source_selection(attempt)
    return SimpleNamespace(ops=ops, args=args, native=native, template=template, retain=retain,
        deliveries=deliveries, checkouts=checkouts, plan=plan)


def test_canonical_fault_retention_derives_stable_distinct_contracts_and_real_native_scope(contract_case,tmp_path):
    case=contract_case
    selections=[case.retain(attempt) for attempt in ('primary','replay')]
    assert selections[0].fault_commit != selections[1].fault_commit
    for attempt, selected in zip(('primary','replay'),selections):
        assert case.ops.source_selection(attempt) == selected
        document=json.loads(selected.correction_contract.read_bytes())
        assert document == {**case.template,'base_commit':selected.fault_commit}
        assert selected.correction_contract != case.args.contract
        assert json.loads(case.args.contract.read_bytes())['base_commit'] == '0'*40
        scope=prepare_correction_scope(repository=selected.repository,base_commit=selected.fault_commit,
            profile=selected.profile,profile_sha256=selected.profile_sha256,
            contract=selected.correction_contract,contract_sha256=selected.correction_contract_sha256,
            tools=dict(selected.tools),schemas=selected.schemas,schemas_sha256=selected.schemas_sha256,
            output=tmp_path/(attempt+'-scope'))
        assert scope is not None
    assert len(case.ops._derived_evidence) == 14


@pytest.mark.parametrize('change',['file','pointer','profile'])
def test_template_cannot_expand_or_change_qualified_scope(contract_case,change):
    case=contract_case
    value=deepcopy(case.template)
    if change=='file':
        value['allowed_files'].append('deploy/other.yaml')
    elif change=='pointer':
        value['allowed_rendered_changes'][0]['pointers']=['/spec']
    else:
        value['profile_sha256']='e'*64
    case.args.contract.write_bytes(_canonical(value))
    case.native['correction_contract']['sha256']=_sha(case.args.contract.read_bytes())
    with pytest.raises(StateError,match='exactly the qualified'):
        case.retain('primary')
    assert case.ops._correction_contracts == {}


def test_original_and_derived_bytes_reaudited_before_native_selection(contract_case):
    case=contract_case
    selected=case.retain('primary')
    selected.correction_contract.write_bytes(b'{}')
    with pytest.raises(StateError,match='evidence bytes changed'):
        case.ops.source_selection('primary')


def test_replaced_original_handle_and_saved_document_cannot_restore_derivation(contract_case):
    case=contract_case
    selected=case.retain('primary')
    with pytest.raises(StateError,match='resident correction derivation'):
        require_correction_contract(json.loads(selected.correction_contract.read_bytes()),operations=case.ops,attempt='primary')
    old=case.ops._source_checkouts[('primary','fault')]
    replacement=object()
    case.checkouts[replacement]=deepcopy(case.checkouts[old])
    case.ops._source_checkouts[('primary','fault')]=replacement
    with pytest.raises(StateError,match='origins were replaced'):
        case.ops.source_selection('primary')


def test_worktree_edit_cannot_silently_change_original_source_binding(contract_case):
    case=contract_case
    selected=case.retain('primary')
    path=selected.repository/case.plan.source_path
    path.write_bytes(path.read_bytes()+b'other: unapproved\n')
    with pytest.raises(StateError,match='input bytes changed|bounded regular file'):
        case.ops.source_selection('primary')


def test_operation_evidence_reserves_room_for_phase_receipt(tmp_path):
    from devsecops.factory.lifecycle import LifecycleEngine, PhaseOutcome
    from devsecops.factory.owned_operations import OwnedOperationCoordinator
    from devsecops.factory.state import MAX_RESULT_ARTIFACTS
    paths=[]
    for index in range(MAX_RESULT_ARTIFACTS):
        path=tmp_path/str(index)
        path.write_bytes(b'controlled evidence')
        paths.append(path)
    accepted=OwnedOperationCoordinator._bounded_artifacts(paths[:-1])
    engine=object.__new__(LifecycleEngine)
    outcome=PhaseOutcome('completed','controlled-evidence',accepted)
    assert engine._outcome(outcome,{'case_ids':[]}) == ('completed',[])
    with pytest.raises(StateError,match='path closure exceeds'):
        OwnedOperationCoordinator._bounded_artifacts(paths)
