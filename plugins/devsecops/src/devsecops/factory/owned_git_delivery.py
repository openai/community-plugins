"""Current owned Git delivery: original inputs, one claimed push, exact readback.

Only the initial image-values transport is currently supported. Fault,
correction and reset require their own direct commit producers; this module
never converts an arbitrary commit or saved report into a prepared transport.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import fnmatch
import os
from pathlib import Path
import re
import ssl
import threading
from urllib.parse import quote, urlsplit
from weakref import WeakKeyDictionary

from . import git_request_claim
from .git_destination import destination_configuration, _read_input, _sha
from .git_receive_http import Budget, GitReceiveClient, Limits, validate_status
from .git_writer_identity import writer_configuration, _projection
from .gitlab_api import _json as api_json
from .lifecycle import PhaseContext
from .mission_git_operation import _installed
from .state import IDENTITY_KEYS, StateError, _canonical, _exclusive, _json, _private_directory, _read

_DELIVERIES = WeakKeyDictionary()
_HASH = re.compile('[a-f0-9]{64}')
_BASE_FIELDS = {'schema_version', 'kind', 'token_profile', 'credential_role', 'expected_gitlab_version', 'expected_project_access_level',
    'expected_scopes', 'target_ref_prefix'}


def delivery_writer_configuration(raw):
    value = _json(raw)
    if (type(value) is not dict or set(value) != _BASE_FIELDS | {'run_scope', 'environment_id', 'expires_at'}
            or value['kind'] != 'owned-git-writer-policy'
            or value['credential_role'] != 'owned-run-delivery-writer'
            or type(value['run_scope']) is not dict or set(value['run_scope']) != IDENTITY_KEYS
            or any(not isinstance(v, str) or not _HASH.fullmatch(v) for v in value['run_scope'].values())
            or not isinstance(value['environment_id'], str)
            or not re.fullmatch('[a-z][a-z0-9-]{0,31}', value['environment_id'])
            or value['expected_gitlab_version'] != '19.5.3'
            or not isinstance(value['target_ref_prefix'], str) or len(value['target_ref_prefix']) > 128):
        raise StateError('Exact owned writer scope and supported server required')
    selected = {key: value[key] for key in _BASE_FIELDS if key != 'credential_role'}
    selected.update(kind='mission-git-writer-observation-configuration', destination_configuration_sha256='0'*64,
                    expected_user_id=1, expected_token_id=1, token_env='DERIVED_ORIGINAL_WRITER')
    writer_configuration(_canonical(selected))
    try:
        expires = datetime.fromisoformat(value['expires_at'].replace('Z', '+00:00'))
        if expires.tzinfo is None:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise StateError('Writer requires an explicit absolute expiry') from None
    return value


class ObservedGitDelivery:
    __slots__ = ('__weakref__',)
    def __new__(cls):
        raise TypeError('Delivery requires the original current writer and exact readback')
    def __reduce__(self):
        raise TypeError('Delivery origins cannot be restored from documents')


def _record(value, journal):
    record = _DELIVERIES.get(value) if type(value) is ObservedGitDelivery else None
    if (record is None or record['journal'] is not journal or record['pid'] != os.getpid()
            or record['thread'] != threading.get_ident() or journal.state['run_id'] != record['binding']['run_id']
            or journal.identities != record['identities']):
        raise StateError('Original current owned Git delivery required')
    return record


def require_delivery(value, *, journal):
    record = _record(value, journal)
    if record.get('revoked'):
        raise StateError('Delivery was revoked after incomplete continuation')
    # This origin is issued only after the one receive has exact independent
    # readback. Its historical proof survives upload/base expiry; later writes
    # must pass their own current operation and fresh remote gate admission.
    for path, ref in record['source_artifacts'].items():
        raw = _read(path, 64_000_000)
        if _sha(raw) != ref['sha256'] or len(raw) != ref['bytes']:
            raise StateError('Original delivered source artifact changed')
    for path, pin in record['retained'].items():
        if _sha(_read(path, 16_000_000)) != pin:
            raise StateError('Retained delivery evidence changed')
    directory = record['output'].stat()
    if (directory.st_dev, directory.st_ino) != record['directory_identity']:
        raise StateError('Delivery output directory changed')
    operation = next((row for row in journal.state['operations']
                      if row['operation_id'] == record['binding']['operation_id']), None)
    if operation is None or operation['status'] not in ('pending', 'applied'):
        raise StateError('Delivery operation is unresolved or failed')
    return deepcopy(record['binding'])


def delivery_artifacts(value, *, journal):
    """Original public source, request, readback and MR bytes; no credentials."""
    require_delivery(value,journal=journal)
    record=_record(value,journal)
    paths=set(record['source_artifacts']) | set(record['retained'])
    if len(paths)>128:
        raise StateError('Original delivery evidence exceeds its path bound')
    return tuple(sorted(paths,key=str))


def _prepared_inputs(prepared, journal):
    from .owned_policy_delivery import PreparedPolicyGit, require_policy_git, require_policy_git_sources
    if type(prepared) is PreparedPolicyGit:
        return require_policy_git(prepared,journal=journal), require_policy_git_sources(prepared,journal=journal)
    from .owned_operations import require_operation_git, require_operation_git_sources
    return require_operation_git(prepared,journal=journal), require_operation_git_sources(prepared,journal=journal)


def _context(context, journal):
    if (type(context) is not PhaseContext or not journal._lifecycle_locked
            or not journal._environment_locked or journal._process_id != os.getpid()
            or threading.current_thread() is not threading.main_thread()
            or context.execution_mode != 'operator' or context.run_dir != journal.run_dir
            or context.run_id != journal.state['run_id'] or dict(context.identities) != journal.identities
            or context.remaining_seconds() <= 0):
        raise StateError('Exact current operator delivery context required')
    from .owned_linux import _plain
    state=journal.state
    life=state.get('lifecycle')
    if (life is None or life['execution_mode'] != 'operator'
            or dict(context.input_artifacts) != life['inputs']
            or _plain(context.phase) not in life['phases']):
        raise StateError('Writer requires exact current operator phase and frozen inputs')
    operation, identities, run_id, expiry = git_request_claim._admit(journal, context.phase['id'])
    config_ref=life['inputs']['config']
    config=_json(_read(journal._artifact_path(config_ref['path']),64000))
    if _plain(context.inputs['config']) != config:
        raise StateError('Writer configuration differs from current journal')
    required = {'promote','deliver'} if context.phase['id']=='build-promote' else {'deliver',context.phase['operation']}
    if not required <= set(context.inputs['config']['allowed_mutations']):
        raise StateError('Current build and promotion selection required')
    return operation, identities, run_id, expiry


def _bootstrap(authority, journal, destination):
    from .canonical_bootstrap import require_bootstrap_authority
    owned = require_bootstrap_authority(authority, journal)
    project = owned['projects']['deployment']
    if (destination['origin'] != owned['origin'] or destination['project_id'] != project['id']
            or destination['path_with_namespace'] != project['path_with_namespace']
            or destination['repository_path'] != '/'+project['path_with_namespace']+'.git'):
        raise StateError('Destination differs from the directly owned project')
    return owned


def _admission(client, selected, destination, *, store):
    base = '/api/v4/projects/'+str(destination['project_id'])
    result = {}
    for kind, path in (('version', '/api/v4/version'), ('user', '/api/v4/user'),
            ('token', '/api/v4/personal_access_tokens/self'), ('project', base),
            ('member', base+'/members/all/'+str(selected['expected_user_id']))):
        _, headers, raw = client.read_api(path)
        if b'x-next-page' in headers or b'link' in headers:
            raise StateError('Writer identity observation unexpectedly paginated')
        result[kind] = _projection(kind, raw, selected, destination, datetime.now(timezone.utc))
        if kind == 'user':
            username = api_json(raw).get('username')
            if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,255}', username):
                raise StateError('Writer username is unavailable')
            result['username'] = username
        store(kind, {'response_sha256': _sha(raw), 'projection': result[kind]})
    _, headers, raw = client.read_api(base+'/protected_branches?per_page=100&page=1')
    protected = api_json(raw)
    if (type(protected) is not list or len(protected) > 100
            or headers.get(b'x-page') != b'1' or headers.get(b'x-per-page') != b'100'
            or headers.get(b'x-next-page') != b''
            or b'rel="next"' in headers.get(b'link', b'')):
        raise StateError('Complete bounded branch protection observation required')
    branch = destination['target_ref'].removeprefix('refs/heads/')
    for row in protected:
        pattern = row.get('name') if type(row) is dict else None
        if (not isinstance(pattern, str) or not re.fullmatch(r'[A-Za-z0-9_./*+-]{1,255}', pattern)
                or fnmatch.fnmatchcase(branch, pattern)):
            raise StateError('Review branch matches protected or unsupported policy')
    store('branch-policy', {'response_sha256': _sha(raw), 'target_ref': destination['target_ref'],
                            'protected_names': [row['name'] for row in protected]})
    result['protected_names'] = [row['name'] for row in protected]
    return result


def deliver_prepared(context, *, journal, prepared, bootstrap_authority, destination, destination_sha256,
                     writer, writer_sha256, writer_origin, plugin_id, plugin_version, service_route=None):
    """Create one exact review branch. Does not merge or claim runtime recovery."""
    operation, identities, run_id, config_expiry = _context(context, journal)
    (transport, observed, scope, original_destination_sha), (proposal, commit, publication) = _prepared_inputs(prepared,journal)
    from .policy_git_source import PolicyGitSource
    if (context.phase['id']=='build-promote') == (type(proposal) is PolicyGitSource):
        raise StateError('Exact initial-image or policy producer must match the current phase')
    source = proposal.describe()
    if original_destination_sha != destination_sha256:
        raise StateError('Writer destination differs from observed original base')
    budget = Budget(Limits(timeout=min(120, context.remaining_seconds())))
    deadline = budget.deadline
    inputs = []
    for path, pin in ((writer, writer_sha256), (destination, destination_sha256)):
        raw, identity = _read_input(path, 64000, deadline)
        if _sha(raw) != pin:
            raise StateError('Selected writer input differs from its pin')
        inputs.append((path, raw, identity))
    selected, destination_doc = delivery_writer_configuration(inputs[0][1]), destination_configuration(inputs[1][1])
    if (selected['run_scope'] != identities or selected['environment_id'] != journal.state['environment_id']
            or destination_doc['target_ref'] != selected['target_ref_prefix']+run_id+'/'+context.phase['id']
            or destination_doc['base_commit'] != scope['deployment_commit']):
        raise StateError('Writer run, branch or base selection differs')
    expires = datetime.fromisoformat(selected['expires_at'].replace('Z', '+00:00'))
    if expires > config_expiry:
        raise StateError('Writer window exceeds current lifecycle authority')
    _bootstrap(bootstrap_authority, journal, destination_doc)
    read_names = {v for key in ('api_read_credential','git_read_credential')
                  for name,v in destination_doc[key].items() if name.endswith('_env')}
    owned_reader = (destination_doc['api_read_credential']=={'kind':'owned-reader','transport':'api'}
        and destination_doc['git_read_credential']=={'kind':'owned-reader','transport':'git'})
    if not read_names and not owned_reader:
        raise StateError('Separate explicit reader credentials are required')
    # Concrete IDs and the exact secret come only from the original issuer.
    from .owned_writer_identity import require_owned_writer_origin, _writer_secret
    writer_binding = require_owned_writer_origin(writer_origin,journal=journal,
        bootstrap_authority=bootstrap_authority,policy_sha256=writer_sha256)
    selected = {**selected,'expected_user_id':writer_binding['user_id'],
        'expected_token_id':writer_binding['token_id']}
    token = _writer_secret(writer_origin,journal=journal,bootstrap_authority=bootstrap_authority,policy_sha256=writer_sha256)
    trust = destination_doc['trust']
    if trust['mode'] == 'bundle':
        ca, identity = _read_input(Path(trust['path']), 2_000_000, deadline)
        if _sha(ca) != trust['sha256']:
            raise StateError('Selected writer CA changed')
        inputs.append((Path(trust['path']), ca, identity))
        tls = ssl.create_default_context(cadata=ca.decode('ascii'))
    else:
        tls = ssl.create_default_context()
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    output = journal.run_dir/'operations'/operation/'owned-git-delivery'
    _private_directory(output, new=True)
    info = output.stat()
    retained = {}
    route_options = {} if service_route is None else dict(service_route=service_route,
        ca_sha256=trust.get('sha256') if trust.get('mode')=='bundle' else None)
    client = GitReceiveClient(origin=destination_doc['origin'], repository_path=destination_doc['repository_path'],
                              context=tls, token=token, budget=budget, **route_options)
    def audit():
        if _context(context, journal)[0] != operation or datetime.now(timezone.utc) >= expires:
            raise StateError('Writer operation or authority window changed')
        _bootstrap(bootstrap_authority, journal, destination_doc)
        actual, origins = _prepared_inputs(prepared,journal)
        if actual != (transport, observed, scope, original_destination_sha) or origins != (proposal,commit,publication):
            raise StateError('Original transport/base authority changed')
        if require_owned_writer_origin(writer_origin,journal=journal,bootstrap_authority=bootstrap_authority,
                policy_sha256=writer_sha256) != writer_binding:
            raise StateError('Original writer identity changed')
        for path, raw, identity in inputs:
            current, observed_identity = _read_input(path, max(64000, len(raw)), deadline)
            if current != raw or observed_identity != identity:
                raise StateError('Selected writer input changed during execution')
        _installed(identities['package_sha256'], plugin_id, plugin_version, deadline)
        current = output.stat()
        if (current.st_dev,current.st_ino) != (info.st_dev,info.st_ino):
            raise StateError('Writer output route changed')
        for path,pin in retained.items():
            if _sha(_read(path,16_000_000)) != pin:
                raise StateError('Retained writer evidence changed')
        budget.remaining()
        # Package and evidence audits can consume the remaining action window.
        # Check again at their end, immediately before the one dispatch.
        if _context(context, journal)[0] != operation or datetime.now(timezone.utc) >= expires:
            raise StateError('Writer operation or authority window expired during audit')
    def save(name, value):
        audit()
        raw = value if type(value) is bytes else _canonical(value)
        path = output/(name if type(value) is bytes else name+'.json')
        _exclusive(path,raw)
        retained[path] = _sha(raw)
        audit()
    try:
        audit()
        first = _admission(client, selected, destination_doc, store=lambda n,v:save('admit1-'+n,v))
        if first['token']['expires_at'] != writer_binding['token_expires_at'] or first['username'] != writer_binding['username']:
            raise StateError('Action-time writer identity differs from original issuance')
        client.set_username(first['username'])
        advertisement = client.advertisement()
        audit()
        request = git_request_claim.prepare_operation_request(journal, context.phase['id'], transport=transport,
            observed_base=observed, **scope, expected_destination_sha256=destination_sha256,
            advertisement=advertisement, timeout=min(120,budget.remaining()))
        # Re-observe permissions with the exact captured secret after native work.
        second = _admission(client, selected, destination_doc, store=lambda n,v:save('admit2-'+n,v))
        if first != second:
            raise StateError('Writer action-time identity or branch policy changed')
        claim = git_request_claim.claim_operation_request(journal, context.phase['id'], request)
        git_request_claim.require_local_git_request_claim(claim, journal=journal, phase_id=context.phase['id'])
        current = git_request_claim._audit_request(request,journal,context.phase['id'])
        from .git_base_possession import observed_base_artifact_refs
        request_artifacts=observed_base_artifact_refs(observed)
        request_artifacts.update({current['output']/name:deepcopy(ref) for name,ref in current['artifacts'].items()})
        claim_record=git_request_claim._claim_record(claim)
        request_artifacts.update({journal._artifact_path(ref['path']):{'sha256':ref['sha256'],'bytes':ref['bytes']}
                                  for ref in claim_record['refs']})
        body = current['body']
        audit()
        save('dispatch-intent', {'operation_id':operation,'target_ref':destination_doc['target_ref'],
            'new_commit':current['report']['new_commit'],'request_sha256':_sha(body),
            'writer_user_id':selected['expected_user_id'],'writer_token_id':selected['expected_token_id'],
            'remote_effect':'unknown-until-complete-status-and-readback'})
        git_request_claim.require_local_git_request_claim(claim, journal=journal, phase_id=context.phase['id'])
        audit()
        status = client.send_once(body)
        validate_status(status,target_ref=destination_doc['target_ref'],request_body=body)
        save('receive-status.bin',status)
        # Independent connections and a fresh reader object; same frozen secret
        # and aggregate budget, never a second write or inferred success on loss.
        reader = GitReceiveClient(origin=destination_doc['origin'], repository_path=destination_doc['repository_path'],
                                  context=tls,token=token,budget=budget, **route_options)
        project = '/api/v4/projects/'+str(destination_doc['project_id'])
        _,_,raw = reader.read_api(project)
        _projection('project',raw,selected,destination_doc,datetime.now(timezone.utc))
        save('readback-project',{'response_sha256':_sha(raw)})
        branch = quote(destination_doc['target_ref'].removeprefix('refs/heads/'),safe='')
        _,_,raw = reader.read_api(project+'/repository/branches/'+branch)
        branch_doc = api_json(raw)
        commit_bound = commit.describe()['prepared']
        if (branch_doc.get('name') != destination_doc['target_ref'].removeprefix('refs/heads/')
                or branch_doc.get('commit',{}).get('id') != commit_bound['commit']):
            raise StateError('Independent branch readback differs')
        save('readback-branch',{'response_sha256':_sha(raw),'commit':commit_bound['commit']})
        _,_,raw = reader.read_api(project+'/repository/commits/'+commit_bound['commit'])
        remote = api_json(raw)
        if remote.get('id') != commit_bound['commit'] or remote.get('parent_ids') != [commit_bound['parent']]:
            raise StateError('Independent commit readback differs')
        save('readback-commit',{'response_sha256':_sha(raw),'id':remote['id'],'parent_ids':remote['parent_ids']})
        _,_,blob = reader.read_api(project+'/repository/blobs/'+commit_bound['after_blob']+'/raw',
                                  cap=2_000_000,media='application/octet-stream')
        if _sha(blob) != source['after']['sha256'] or len(blob) != source['after']['bytes']:
            raise StateError('Independent blob readback differs')
        save('readback-values.bin',blob)
        rendered_path = Path(source['patch']['path']).parent/'rendered-after.json'
        rendered = _read(rendered_path,16_000_000)
        if _sha(rendered) != source['rendered_after']['sha256'] or len(rendered) != source['rendered_after']['bytes']:
            raise StateError('Original rendered source changed')
        save('rendered-after.json',rendered)
        commit_source = commit.describe()
        transport_source = transport.describe()
        source_artifacts = deepcopy(request_artifacts)
        proposal_root = Path(source['patch']['path']).parent
        for name, ref in (('proposal.patch',source['patch']),('values-before.yaml',source['before']),
                ('values-after.yaml',source['after']),('rendered-before.json',source['rendered_before']),
                ('rendered-after.json',source['rendered_after'])):
            source_artifacts[proposal_root/name] = {'sha256':ref['sha256'],'bytes':ref['bytes']}
        for observed_source in (commit_source,transport_source):
            for name, ref in observed_source['artifacts'].items():
                source_artifacts[Path(observed_source['output_path'])/name] = deepcopy(ref)
        for path, ref in source_artifacts.items():
            raw = _read(path,64_000_000)
            if _sha(raw) != ref['sha256'] or len(raw) != ref['bytes']:
                raise StateError('Original delivery source snapshot changed')
        save('historical-source',{'proposal':source,'commit':commit_source,'transport':transport_source})
        binding = {'run_id':run_id,'operation_id':operation,'phase':context.phase['id'],'attempt':'replay' if context.phase['id']=='reset' else context.phase.get('attempt','primary'),
            'source_commit':scope['source_commit'],'deployment_commit':commit_bound['commit'],
            'parent':commit_bound['parent'],'tree':commit_bound['tree'],
            'repository_url':destination_doc['origin']+destination_doc['repository_path'],
            'local_repository':source['deployment_repository'],'rendered_path':str(output/'rendered-after.json'),
            'rendered_sha256':source['rendered_after']['sha256'],
            'image_refs':[urlsplit(source['origin']).netloc+'/'+source['repository']+'@'+source['manifest_digest']],
            'origin':destination_doc['origin'],'project_id':destination_doc['project_id'],
            'path_with_namespace':destination_doc['path_with_namespace'],'target_ref':destination_doc['target_ref'],
            'base_ref':destination_doc['base_ref'],'delivery_stage':'review-branch','watched_revision':None}
        if source.get('source_kind')=='qualified-policy-change':
            binding.update(source_kind=source['source_kind'],source_provenance=deepcopy(source['source_provenance']),
                selected_tree=deepcopy(source['selected_tree']),content_already_baseline=source['allow_empty_reset'])
        binding['observed_at']=datetime.now(timezone.utc).isoformat()
        save('delivery',binding)
        value = object.__new__(ObservedGitDelivery)
        _DELIVERIES[value] = dict(journal=journal,pid=os.getpid(),thread=threading.get_ident(),
            binding=binding,identities=identities,prepared=prepared,retained=retained,
            source_artifacts=source_artifacts,source_origins=(proposal,commit,publication,transport,observed,request,claim),
            output=output,directory_identity=(info.st_dev,info.st_ino),bootstrap=bootstrap_authority)
        audit()
        require_delivery(value,journal=journal)
        return value
    except BaseException:
        # The dispatch intent and durable claim remain. Never infer no effect.
        issued = locals().get('value')
        if type(issued) is ObservedGitDelivery:
            _DELIVERIES.pop(issued,None)
        raise
    finally:
        client._token = None
        token = None


def merge_reviewed(context, *, journal, delivery, session, required_jobs,
                   poll_seconds=2, max_observations=30):
    """Continue only this original branch delivery into the fixed owned MR path.

    The MR workflow re-observes gates immediately before its one merge request.
    A missing/failed/unknown gate never becomes a merge grant. No API call occurs
    until the original owned project and current operation have been checked.
    """
    import time
    from .gitlab_api import GitLabSession
    from .gitlab_mr import MRSelection, MergeRequestError, MergeRequestWorkflow
    operation, _, _, _ = _context(context, journal)
    binding = require_delivery(delivery,journal=journal)
    record = _record(delivery,journal)
    if (binding['operation_id'] != operation or binding['delivery_stage'] != 'review-branch'
            or 'mr_workflow' in record or type(session) is not GitLabSession
            or session.writes_blocked or type(poll_seconds) not in (int,float) or not 0 < poll_seconds <= 30
            or type(max_observations) is not int or not 1 <= max_observations <= 120):
        raise StateError('Original unconsumed delivery and bounded owned MR session required')
    from .canonical_bootstrap import require_bootstrap_authority, runner_origin_for
    owned = require_bootstrap_authority(record['bootstrap'],journal)
    if (session.origin != binding['origin'] or session.expected_user_id != owned['owner_user_id']
            or owned['projects']['deployment']['id'] != binding['project_id']):
        raise StateError('MR session differs from the original owned project')
    # A new bounded transport retains the original credential, trust and
    # uncertain-write latch. It cannot renew a spent or poisoned write grant.
    session = session.renew_for_operation()
    _context(context, journal)
    selected = MRSelection(project_id=binding['project_id'],path_with_namespace=binding['path_with_namespace'],
        namespace_id=owned['namespace_id'],source_branch=binding['target_ref'].removeprefix('refs/heads/'),
        target_branch=binding['base_ref'].removeprefix('refs/heads/'),source_sha=binding['deployment_commit'],
        target_sha=binding['parent'],required_jobs=required_jobs,
        run_id=_sha(_canonical({'run_id':binding['run_id'],'operation_id':operation,'identities':journal.identities})))
    record['mr_workflow'] = None  # Burn before any exclusive attempt directory.
    workflow = MergeRequestWorkflow(session,selected,journal_root=record['output'],
        runner_origin=runner_origin_for(record['bootstrap'],journal,context))
    record['mr_workflow'] = workflow
    try:
        workflow.create()
        for index in range(max_observations):
            _context(context,journal)
            require_delivery(delivery,journal=journal)
            require_bootstrap_authority(record['bootstrap'],journal)
            try:
                workflow.observe_gates()
                break
            except MergeRequestError as error:
                if error.code not in ('mr-gates-not-ready','pipeline-binding-or-status','required-job-gate','mr-pipeline-missing'):
                    raise
                if index+1 == max_observations or context.remaining_seconds() <= poll_seconds:
                    raise StateError('MR gates did not qualify within the current operation') from None
                time.sleep(min(poll_seconds,context.remaining_seconds()))
        _context(context,journal)
        require_delivery(delivery,journal=journal)
        require_bootstrap_authority(record['bootstrap'],journal)
        merged = workflow.merge()
        _context(context,journal)
        require_delivery(delivery,journal=journal)
        if (merged['source_sha'] != binding['deployment_commit']
                or merged['target_observed_sha'] != binding['deployment_commit']
                or merged['target_baseline_sha'] != binding['parent']):
            raise StateError('Exact MR merge readback differs from original delivery')
        workflow._audit()
        for name,ref in workflow._events.items():
            path=workflow.path/(name+'.json')
            raw=_read(path,65536)
            if _sha(raw)!=ref['sha256'] or len(raw)!=ref['bytes']:
                raise StateError('Original MR event changed')
            record['retained'][path]=ref['sha256']
        merged_binding = {**binding,'delivery_stage':'merged-watched-branch',
            'watched_revision':binding['deployment_commit'],'merge_request_iid':merged['iid'],
            'pipeline_id':merged['pipeline_id'],'required_jobs':merged['required_jobs'],'mr_scope_sha256':selected.run_id,
            'target_sha_atomic_precondition':False,'pipeline_id_atomic_precondition':False,
            'observed_at':datetime.now(timezone.utc).isoformat()}
        path = record['output']/'merged-delivery.json'
        _exclusive(path,_canonical(merged_binding))
        record['retained'][path] = _sha(_read(path,64000))
        record['binding'] = merged_binding
        require_delivery(delivery,journal=journal)
        return delivery
    except BaseException:
        # The original branch observation remains an observation. A spent MR
        # workflow is retained and cannot be replaced or retried by this helper.
        record['revoked']=True
        session.stop_writes()
        raise
    finally:
        workflow.close()
