"""Controlled HTTP boundary tests: these are not real GitLab/E2E executions."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from devsecops.factory.gitlab_api import GitLabAPIError
from devsecops.factory.gitlab_mr import (
    MRSelection, MergeRequestError, MergeRequestWorkflow, reconcile_selection,
)
from test_gitlab_api import Response, identity, page, session, wire as _wire

wire = _wire


SOURCE, TARGET = 'a' * 40, 'b' * 40


def selected():
    return MRSelection(91, 'demo-owner/delivery', 20, 'review/demo-run', 'main',
                       SOURCE, TARGET, ('review', 'test'), 'c' * 64)


def project():
    return {'id': 91, 'path_with_namespace': 'demo-owner/delivery',
        'owner': {'id': 17}, 'namespace': {'id': 20, 'kind': 'user', 'full_path': 'demo-owner'},
        'visibility': 'private', 'archived': False, 'default_branch': 'main',
        'permissions': {'project_access': {'access_level': 40}},
        'merge_method': 'ff', 'squash_option': 'never',
        'only_allow_merge_if_pipeline_succeeds': True, 'allow_merge_on_skipped_pipeline': False,
        'only_allow_merge_if_all_discussions_are_resolved': True,
        'remove_source_branch_after_merge': False}


def branches(wire, *, merged=False, source=SOURCE, target=None):
    wire.queue.extend([Response({'name': 'review/demo-run', 'commit': {'id': source}, 'protected': False}),
        Response({'name': 'main', 'commit': {'id': target or (SOURCE if merged else TARGET)}, 'protected': True})])


def pipeline():
    return {'id': 701, 'project_id': 91, 'sha': SOURCE, 'ref': 'refs/merge-requests/12/head',
            'status': 'success', 'source': 'merge_request_event', 'tag': False, 'yaml_errors': None}


def mr(flow, *, merged=False):
    return {'iid': 12, 'project_id': 91, 'source_project_id': 91, 'target_project_id': 91,
        'source_branch': 'review/demo-run', 'target_branch': 'main', 'sha': SOURCE,
        'state': 'merged' if merged else 'opened', 'draft': False, 'author': {'id': 17},
        'description': flow._description, 'detailed_merge_status': 'mergeable', 'has_conflicts': False,
        'blocking_discussions_resolved': True, 'squash': False,
        'diff_refs': {'base_sha': TARGET, 'start_sha': TARGET, 'head_sha': SOURCE},
        'head_pipeline': pipeline()}


def jobs():
    return [{'id': jid, 'name': name, 'status': 'success', 'allow_failure': False,
             'erased_at': None, 'finished_at': '2026-10-30T12:00:00Z',
             'pipeline': pipeline(), 'commit': {'id': SOURCE}}
            for jid, name in ((901, 'review'), (902, 'test'))]


def policy(wire, value=None):
    identity(wire)
    wire.queue.append(Response(value if value is not None else project()))


def create_responses(wire, flow, *, response=None, existing=None):
    policy(wire)
    branches(wire)
    wire.queue.append(page(existing or []))
    if existing:
        return
    identity(wire)
    wire.queue.append(response if response is not None else Response(mr(flow), status=201))
    wire.queue.append(Response(mr(flow)))
    branches(wire)


def gates_responses(wire, flow, *, mr_value=None, pipe=None, job_values=None, triggers=None,
                    final_mr=None):
    policy(wire)
    branches(wire)
    wire.queue.append(Response(mr_value if mr_value is not None else mr(flow)))
    wire.queue.append(Response(pipe if pipe is not None else pipeline()))
    wire.queue.append(page(job_values if job_values is not None else jobs()))
    wire.queue.append(page(triggers or []))
    wire.queue.append(Response(final_mr if final_mr is not None else mr(flow)))
    branches(wire)


def opened(wire, tmp_path):
    flow = MergeRequestWorkflow(session(), selected(), journal_root=tmp_path)
    create_responses(wire, flow)
    assert flow.create()['iid'] == 12
    assert not wire.queue
    return flow


def merge_responses(wire, flow, *, response=None, final=None, target=SOURCE):
    gates_responses(wire, flow)
    identity(wire)
    wire.queue.append(response if response is not None else Response(mr(flow, merged=True)))
    policy(wire)
    wire.queue.append(Response(final if final is not None else mr(flow, merged=True)))
    branches(wire, merged=True, target=target)


def test_full_create_gates_and_source_pinned_merge(wire, tmp_path):
    with opened(wire, tmp_path) as flow:
        merge_responses(wire, flow)
        receipt = flow.merge()
        assert not wire.queue
        assert receipt['target_observed_sha'] == SOURCE
        assert receipt['required_jobs'] == {'review': 901, 'test': 902}
        assert receipt['pipeline_id'] == 701
        assert receipt['target_sha_atomic_precondition'] is False
        assert receipt['pipeline_id_atomic_precondition'] is False
        assert receipt['controller_sync'] == 'not-established'
        assert receipt['delivery_authority'] == 'not-issued'
        assert {path.name for path in flow.path.iterdir()} == {
            'selection.json', 'create-intent.json', 'create-response.json', 'created.json',
            'merge-intent.json', 'merge-response.json', 'merged.json'}
        writes = [call for call in wire.calls if call['method'] != 'GET']
        assert [call['method'] for call in writes] == ['POST', 'PUT']
        payload = json.loads(writes[-1]['body'])
        assert payload == {'sha': SOURCE, 'auto_merge': False, 'squash': False,
                           'should_remove_source_branch': False}
        with pytest.raises(MergeRequestError, match='current-created-mr-required'):
            flow.merge()


@pytest.mark.parametrize('change', [
    {'merge_method': 'merge'}, {'squash_option': 'always'}, {'visibility': 'public'},
    {'archived': True}, {'default_branch': 'other'}, {'owner': {'id': 19}},
    {'only_allow_merge_if_pipeline_succeeds': False}, {'allow_merge_on_skipped_pipeline': True},
    {'only_allow_merge_if_all_discussions_are_resolved': False},
    {'remove_source_branch_after_merge': True}, {'permissions': {'project_access': {'access_level': 30}}},
    {'namespace': {'id': 20, 'kind': 'group', 'full_path': 'demo-owner'}},
    {'namespace': {'id': 21, 'kind': 'user', 'full_path': 'demo-owner'}},
    {'path_with_namespace': 'someone/delivery'}, {'id': 92}])
def test_wrong_owner_policy_permission_never_creates(wire, tmp_path, change):
    with MergeRequestWorkflow(session(), selected(), journal_root=tmp_path) as flow:
        value = project()
        value.update(change)
        policy(wire, value)
        with pytest.raises(MergeRequestError):
            flow.create()
        assert all(call['method'] == 'GET' for call in wire.calls)
        assert not (flow.path / 'create-intent.json').exists()


@pytest.mark.parametrize('branch', ['source', 'target'])
def test_wrong_source_or_target_sha_blocks_creation(wire, tmp_path, branch):
    with MergeRequestWorkflow(session(), selected(), journal_root=tmp_path) as flow:
        policy(wire)
        branches(wire, **{branch: 'd' * 40})
        with pytest.raises(MergeRequestError, match='branch-sha-or-protection'):
            flow.create()
        assert all(call['method'] == 'GET' for call in wire.calls)


@pytest.mark.parametrize('change', [
    {'sha': 'e' * 40}, {'source_project_id': 92}, {'target_project_id': 92},
    {'target_branch': 'release'}, {'source_branch': 'another'}, {'draft': True},
    {'author': {'id': 19}}, {'description': 'somebody else'}, {'state': 'closed'}])
def test_invalid_create_readback_blocks_session(wire, tmp_path, change):
    with MergeRequestWorkflow(session(), selected(), journal_root=tmp_path) as flow:
        value = mr(flow)
        value.update(change)
        create_responses(wire, flow, response=Response(value, status=201))
        with pytest.raises(MergeRequestError):
            flow.create()
        assert flow.session.writes_blocked
        before = len(wire.calls)
        with pytest.raises(MergeRequestError, match='reconcile-only'):
            flow.create()
        assert len(wire.calls) == before


def test_existing_mr_even_closed_blocks_duplicate(wire, tmp_path):
    with MergeRequestWorkflow(session(), selected(), journal_root=tmp_path) as flow:
        create_responses(wire, flow, existing=[{'iid': 44, 'state': 'closed'}])
        with pytest.raises(MergeRequestError, match='existing-mr-reconcile-only'):
            flow.create()
        assert all(call['method'] == 'GET' for call in wire.calls)


@pytest.mark.parametrize('reply', [TimeoutError('secret diagnostic'), Response({}, status=409),
                                  Response(raw=b'not json', status=201)])
def test_uncertain_create_no_retry_across_workflow_reentry(wire, tmp_path, reply):
    with MergeRequestWorkflow(session(), selected(), journal_root=tmp_path) as flow:
        create_responses(wire, flow, response=reply)
        with pytest.raises(GitLabAPIError) as caught:
            flow.create()
        assert caught.value.outcome == 'unknown'
        assert (flow.path / 'create-intent.json').exists()
        assert flow.session.writes_blocked
        before = len(wire.calls)
        with pytest.raises(MergeRequestError, match='existing-attempt-reconcile-only'):
            MergeRequestWorkflow(session(), selected(), journal_root=tmp_path)
        assert len(wire.calls) == before
        wire.queue.clear()
        identity(wire)
        wire.queue.append(page([]))
        wire.queue.append(Response({'name': 'main', 'commit': {'id': TARGET}}))
        evidence = flow.reconcile()
        assert evidence['outcome'] == 'unresolved'
        assert evidence['absence_proves_nonexecution'] is False
        assert evidence['retry_allowed'] is False
        assert not wire.queue


@pytest.mark.parametrize('change', [
    {'detailed_merge_status': 'checking'}, {'has_conflicts': True},
    {'blocking_discussions_resolved': False}, {'squash': True}, {'diff_refs': None},
    {'diff_refs': {'base_sha': TARGET, 'start_sha': 'e' * 40, 'head_sha': SOURCE}},
    {'head_pipeline': None}, {'sha': 'e' * 40}])
def test_mr_gates_prevent_merge(wire, tmp_path, change):
    with opened(wire, tmp_path) as flow:
        value = mr(flow)
        value.update(change)
        gates_responses(wire, flow, mr_value=value)
        with pytest.raises(MergeRequestError):
            flow.merge()
        assert not any(call['method'] == 'PUT' for call in wire.calls)
        assert not (flow.path / 'merge-intent.json').exists()


@pytest.mark.parametrize('change', [
    {'id': 702}, {'project_id': 92}, {'sha': 'e' * 40}, {'ref': 'review/demo-run'},
    {'status': 'failed'}, {'status': 'running'}, {'source': 'push'}, {'source': 'api'},
    {'tag': True}, {'yaml_errors': 'bad configuration'}])
def test_wrong_pipeline_bindings_block_merge(wire, tmp_path, change):
    with opened(wire, tmp_path) as flow:
        value = pipeline()
        value.update(change)
        gates_responses(wire, flow, pipe=value)
        with pytest.raises(MergeRequestError):
            flow.merge()
        assert not any(call['method'] == 'PUT' for call in wire.calls)


@pytest.mark.parametrize('change', [
    {'status': 'failed'}, {'status': 'manual'}, {'status': 'skipped'}, {'allow_failure': True},
    {'commit': {'id': 'e' * 40}}, {'pipeline': {'id': 702}}, {'finished_at': None},
    {'erased_at': '2026-10-30T01:00:00Z'}, {'finished_at': {'time': 'now'}},
    {'finished_at': '2026-10-30T01:00:00'}, {'finished_at': 'invalid'}])
def test_required_job_binding_and_success(wire, tmp_path, change):
    with opened(wire, tmp_path) as flow:
        values = jobs()
        values[0].update(change)
        gates_responses(wire, flow, job_values=values)
        with pytest.raises(MergeRequestError, match='required-job-gate'):
            flow.merge()
        assert not any(call['method'] == 'PUT' for call in wire.calls)


@pytest.mark.parametrize('kind', ['missing', 'duplicate-name', 'duplicate-id', 'child'])
def test_complete_unique_required_jobs(wire, tmp_path, kind):
    with opened(wire, tmp_path) as flow:
        values = jobs()
        triggers = []
        if kind == 'missing':
            values.pop()
        elif kind == 'duplicate-name':
            values.append({**deepcopy(values[0]), 'id': 905})
        elif kind == 'duplicate-id':
            values.append({**deepcopy(values[0]), 'name': 'extra'})
        else:
            triggers = [{'id': 1001}]
        gates_responses(wire, flow, job_values=values, triggers=triggers)
        with pytest.raises(MergeRequestError):
            flow.merge()
        assert not any(call['method'] == 'PUT' for call in wire.calls)


def test_pipeline_change_during_gate_observation_blocks_merge(wire, tmp_path):
    with opened(wire, tmp_path) as flow:
        value = mr(flow)
        value['head_pipeline']['id'] = 702
        gates_responses(wire, flow, final_mr=value)
        with pytest.raises(MergeRequestError, match='pipeline-changed'):
            flow.merge()
        assert not any(call['method'] == 'PUT' for call in wire.calls)


@pytest.mark.parametrize('reply', [TimeoutError('private diagnostic'), Response({}, status=409)])
def test_unknown_merge_is_get_only_and_never_auto_retried(wire, tmp_path, reply):
    with opened(wire, tmp_path) as flow:
        merge_responses(wire, flow, response=reply)
        with pytest.raises(GitLabAPIError) as caught:
            flow.merge()
        assert caught.value.outcome == 'unknown'
        assert (flow.path / 'merge-intent.json').exists()
        assert not (flow.path / 'merged.json').exists()
        wire.queue.clear()
        before = len(wire.calls)
        identity(wire)
        wire.queue.append(page([mr(flow, merged=True)]))
        wire.queue.append(Response({'name': 'main', 'commit': {'id': SOURCE}}))
        evidence = flow.reconcile()
        assert evidence['matching_mrs'] == [{'iid': 12, 'state': 'merged'}]
        assert evidence['write_authority'] == 'not-issued'
        assert evidence['retry_allowed'] is False
        assert all(call['method'] == 'GET' for call in wire.calls[before:])
        with pytest.raises(MergeRequestError):
            flow.merge()


def test_merge_response_without_exact_final_branch_never_claims_delivery(wire, tmp_path):
    with opened(wire, tmp_path) as flow:
        merge_responses(wire, flow, target='e' * 40)
        with pytest.raises(MergeRequestError, match='branch-sha-or-protection'):
            flow.merge()
        assert flow.session.writes_blocked
        assert not (flow.path / 'merged.json').exists()


def test_retained_selection_or_intent_cannot_mint_workflow_authority(wire, tmp_path):
    with opened(wire, tmp_path) as flow:
        (flow.path / 'create-intent.json').write_text('{}')
        before = len(wire.calls)
        with pytest.raises(MergeRequestError, match='journal-changed'):
            flow.merge()
        assert len(wire.calls) == before
        with pytest.raises(MergeRequestError, match='direct-session-and-selection-required'):
            MergeRequestWorkflow(flow.session, json.loads((flow.path / 'selection.json').read_text()),
                                 journal_root=tmp_path)


@pytest.mark.parametrize('change', [
    {'run_id': 'd' * 64}, {'required_jobs': ('test', 'review')},
    {'required_jobs': ('test',)}, {'source_sha': 'e' * 40}, {'target_sha': 'f' * 40}])
def test_changed_run_sha_or_gate_order_cannot_reset_external_operation_guard(wire, tmp_path, change):
    with MergeRequestWorkflow(session(), selected(), journal_root=tmp_path):
        before = len(wire.calls)
        with pytest.raises(MergeRequestError, match='existing-attempt-reconcile-only'):
            MergeRequestWorkflow(session(), replace(selected(), **change), journal_root=tmp_path)
        assert len(wire.calls) == before


@pytest.mark.parametrize('change', [{'status': 'failed'}, {'sha': 'e' * 40},
                                  {'source': 'push'}, {'project_id': 92}])
def test_postmerge_head_still_requires_same_pipeline_identity(wire, tmp_path, change):
    with opened(wire, tmp_path) as flow:
        final = mr(flow, merged=True)
        final['head_pipeline'].update(change)
        merge_responses(wire, flow, final=final)
        with pytest.raises(MergeRequestError, match='merged-pipeline-changed'):
            flow.merge()
        assert flow.session.writes_blocked
        assert not (flow.path / 'merged.json').exists()


def test_closed_workflow_cannot_mutate(wire, tmp_path):
    flow = MergeRequestWorkflow(session(), selected(), journal_root=tmp_path)
    flow.close()
    with pytest.raises(MergeRequestError, match='closed-or-foreign-workflow'):
        flow.create()
    assert not wire.calls


@pytest.mark.parametrize('change', [{'iid': True}, {'iid': 0}, {'project_id': True},
                                  {'author': {'id': True}}])
def test_malformed_reconciliation_identity_is_unresolved(wire, tmp_path, change):
    with MergeRequestWorkflow(session(), selected(), journal_root=tmp_path) as flow:
        value = mr(flow)
        value.update(change)
        identity(wire)
        wire.queue.append(page([value]))
        wire.queue.append(Response({'name': 'main', 'commit': []}))
        result = reconcile_selection(flow.session, flow.selection)
        assert result['matching_mrs'] == []
        assert result['target_observed_sha'] is None
        assert result['outcome'] == 'unresolved'
        assert all(call['method'] == 'GET' for call in wire.calls)


@pytest.mark.parametrize('change', [{'project_id': True}, {'source_sha': TARGET},
    {'target_branch': 'review/demo-run'}, {'source_branch': '../escape'},
    {'required_jobs': ()}, {'required_jobs': ('test', 'test')}, {'required_jobs': ['test']},
    {'required_jobs': ([],)},
    {'run_id': 'demo'}, {'path_with_namespace': 'group/subgroup/project'}])
def test_invalid_selections_fail_before_reads(change):
    with pytest.raises(ValueError):
        replace(selected(), **change)
