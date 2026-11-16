"""Direct creation-to-removal ownership for the factory's GitLab resources.

An ID copied from a report is never enough. Every removable ID is retained by
this exact object before and after its original creation, and read again before
removal. A timed out DELETE is reconciled only through reads, never retried.
"""
from copy import deepcopy
import os
from urllib.parse import quote

from devsecops.runtime.cli import _new_directory, _write
from .gitlab_api import GitLabAPIError, GitLabSession
from .gitlab_bootstrap import BootstrapScope, _identity, _project_identity
from .state import StateError, _read, _sha
from uuid import uuid4


class OwnedGitLabResources:
    def __init__(self, coordinator, context, session, scope):
        if type(session) is not GitLabSession or type(scope) is not BootstrapScope:
            raise StateError('GitLab ownership needs the direct bounded session and scope')
        scope.validate()
        self.owner, self.context, self.session, self.scope = coordinator, context, session, scope
        self.pid, self.projects, self.runners = os.getpid(), [], []
        self.events, self._down_operation, self.token, self._token_stack = [], None, None, None
        self._event_refs={}
        self.assert_binding(session, scope)

    def assert_binding(self, session, scope):
        if (os.getpid() != self.pid or session is not self.session or scope is not self.scope
                or session.origin != scope.origin or session.expected_user_id != scope.owner_user_id):
            raise StateError('GitLab cleanup ownership differs from original session')
        self.owner._admit(self.context, 'up')

    def _event(self, kind, row):
        output = self.owner.journal.run_dir / ('gitlab-resource-' + uuid4().hex)
        with _new_directory(output) as directory:
            ref=_write(directory, 'event.json', {'kind': kind, 'run_id': self.context.run_id,
                'identities': dict(self.context.identities), 'resource': deepcopy(row)})
            os.fsync(directory)
        self.events.append(output/'event.json')
        self._event_refs[output/'event.json']=ref

    def evidence_artifacts(self, context):
        """Historical public ownership events only; no cleanup or token grant."""
        if (os.getpid()!=self.pid or context.run_id!=self.context.run_id
                or dict(context.identities)!=dict(self.context.identities)):
            raise StateError('Original GitLab resource evidence requires the same run')
        self.owner.driver._admit(context,context.deadline)
        if len(self._event_refs)>128 or set(self.events)!=set(self._event_refs):
            raise StateError('Original GitLab event ledger changed')
        for path,ref in self._event_refs.items():
            raw=_read(path,ref['bytes']+1)
            if len(raw)!=ref['bytes'] or _sha(raw)!=ref['sha256']:
                raise StateError('Original GitLab event bytes changed')
        return tuple(self._event_refs)

    def _route_options(self, context, operation_id=None):
        route=getattr(self.session,'_service_route',None)
        if route is None:
            return {}
        from .owned_service_links import renew_service_route
        return {'service_route':renew_service_route(route,context=context,operation_id=operation_id)}

    def accept_service_token(self, stack):
        from .owned_services import OwnedServiceStack
        self.assert_binding(self.session, self.scope)
        if type(stack) is not OwnedServiceStack or self.owner.components.get('services') is not stack or self.token is not None:
            raise StateError('Token ownership requires the original registered service stack')
        observed = stack.principal(self.context)
        token_id = observed.get('token_id')
        if (type(token_id) is not int or token_id <= 0
                or observed.get('owner_user_id') != self.scope.owner_user_id
                or observed.get('namespace_id') != self.scope.namespace_id):
            raise StateError('Created service token identity differs from the bound owner')
        actual = self.session.request('GET', '/api/v4/personal_access_tokens/self')
        if (actual.get('id') != token_id or actual.get('user_id') != self.scope.owner_user_id
                or actual.get('revoked') is not False or actual.get('active') is not True):
            raise StateError('Current token is not the directly created service token')
        self._token_stack = stack
        self.token = {'kind': 'gitlab-personal-access-token', 'id': token_id,
            'owner_user_id': self.scope.owner_user_id, 'state': 'owned-present', 'disposition': 'remove'}
        self._event('owned-token-observed', self.token)

    def _token_absent(self, context):
        # Rejected authentication can mean expiry or policy changes. It never
        # substitutes for the original owned server's exact revoked-record read.
        observed = self._token_stack.token_status(context, token_id=self.token['id'],
                                                  owner_user_id=self.token['owner_user_id'])
        if (observed.get('token_id') != self.token['id']
                or observed.get('owner_user_id') != self.token['owner_user_id']
                or observed.get('token_name') != 'factory-owned' or observed.get('revoked') is not True):
            return False
        try:
            self.session.request('GET', '/api/v4/personal_access_tokens/self')
        except GitLabAPIError as error:
            if error.status == 401:
                return True
            raise
        return False

    def project_intent(self, role):
        self.assert_binding(self.session, self.scope)
        if role not in ('source', 'deployment') or any(row['role'] == role for _, row in self.projects):
            raise StateError('Project creation is already attempted or unsupported')
        ticket, row = object(), {'kind': 'gitlab-project', 'role': role, 'id': None,
            'path': self.scope.namespace_path+'/'+self.scope.project_path(role),
            'state': 'create-unknown', 'disposition': 'remove', 'delete_stage': None}
        self.projects.append((ticket, row))
        self._event('create-intent', row)
        return ticket

    def project_created(self, ticket, project_id):
        self.assert_binding(self.session, self.scope)
        row = next((row for handle, row in self.projects if handle is ticket), None)
        if row is None or row['state'] != 'create-unknown':
            raise StateError('Project creation handle is not current')
        if type(project_id) is not int or project_id <= 0:
            raise StateError('Observed project ID is unsupported')
        # Preserve the observed ID even if validation fails; never silently lose
        # a created resource and never grant deletion from this unverified ID.
        row['id'] = project_id
        self._event('create-id-observed', row)
        _identity(self.session, self.scope)
        _project_identity(self.session.request('GET', f'/api/v4/projects/{project_id}'),
                          self.scope, row['role'], project_id)
        if any(other['id'] == project_id for _, other in self.projects if other is not row):
            raise StateError('Project ID reused across creation intents')
        row['state'] = 'owned-present'
        self._event('owned-project-observed', row)

    def runner_intent(self, project_id, description):
        self.assert_binding(self.session, self.scope)
        if (description != self.scope.marker or self.runners
                or not any(row['id'] == project_id and row['state'] == 'owned-present'
                           for _, row in self.projects)):
            raise StateError('Runner creation needs the directly owned project')
        ticket, row = object(), {'kind': 'gitlab-runner', 'id': None, 'project_id': project_id,
            'project_ids': [project_id], 'assignment': None,
            'description': description, 'state': 'create-unknown', 'disposition': 'remove'}
        self.runners.append((ticket, row))
        self._event('runner-create-intent', row)
        return ticket

    def _runner_identity(self, value, row, *, projects=None):
        projects = row['project_ids'] if projects is None else projects
        if (type(value) is not dict or type(value.get('id')) is not int or value['id'] != row['id']
                or value.get('description') != self.scope.marker or value.get('runner_type') != 'project_type'
                or value.get('is_shared') is not False or type(value.get('projects')) is not list
                or any(type(item) is not dict or type(item.get('id')) is not int for item in value['projects'])
                or len(value['projects']) != len(projects)
                or {item['id'] for item in value['projects']} != set(projects)):
            raise StateError('Runner identity or project ownership changed')

    def runner_assignment_intent(self, ticket, project_id):
        self.assert_binding(self.session, self.scope)
        row = next((row for handle,row in self.runners if handle is ticket),None)
        source = next((p for _,p in self.projects if p['role']=='source'),None)
        target = next((p for _,p in self.projects if p['role']=='deployment'),None)
        if (row is None or row['state']!='owned-present' or row['assignment'] is not None
                or source is None or target is None or type(project_id) is not int
                or project_id != target['id'] or row['project_id'] != source['id']
                or any(p['state']!='owned-present' for p in (source,target))):
            raise StateError('Runner assignment requires the original exact two owned projects')
        _identity(self.session,self.scope)
        for project in (source,target):
            _project_identity(self.session.request('GET',f"/api/v4/projects/{project['id']}"),
                              self.scope,project['role'],project['id'])
        fresh=self.session.request('GET',f"/api/v4/runners/{row['id']}")
        self._runner_identity(fresh,row)
        if fresh.get('paused') is not True or fresh.get('locked') is not False:
            raise StateError('Runner assignment requires the original paused unassigned runner')
        row['assignment']={'project_id':project_id,'project_ids':sorted([source['id'],target['id']]),
            'operation_id':self.owner._admit(self.context,'up'),'state':'unknown'}
        row['state']='assignment-unknown'
        self._event('runner-assignment-intent',row)

    def _assignment_observation(self, row, session):
        _identity(session,self.scope)
        first=session.request('GET',f"/api/v4/runners/{row['id']}")
        projects=first.get('projects') if type(first) is dict else None
        if type(projects) is not list or any(type(p) is not dict or type(p.get('id')) is not int for p in projects):
            raise StateError('Runner assignment readback is incomplete')
        actual=sorted(p['id'] for p in projects)
        allowed=(sorted(row['project_ids']),row['assignment']['project_ids'])
        if actual not in allowed:
            raise StateError('Runner assignment escaped the original owned project set')
        self._runner_identity(first,row,projects=actual)
        for identifier in row['assignment']['project_ids']:
            project=next((p for _,p in self.projects if p['id']==identifier and p['state']=='owned-present'),None)
            if project is None:
                raise StateError('Runner project identity is no longer owned')
            _project_identity(session.request('GET',f'/api/v4/projects/{identifier}'),
                              self.scope,project['role'],identifier)
            rows=session.get_collection(f'/api/v4/projects/{identifier}/runners')
            found=[item for item in rows if item.get('id')==row['id']]
            if len(found) != (1 if identifier in actual else 0):
                raise StateError('Runner assignment disagrees with project inventory')
        final=session.request('GET',f"/api/v4/runners/{row['id']}")
        self._runner_identity(final,row,projects=actual)
        if first != final:
            raise StateError('Runner changed during assignment observation')
        _identity(session,self.scope)
        return actual

    def runner_assigned(self, ticket, project_id):
        self.assert_binding(self.session,self.scope)
        row=next((row for handle,row in self.runners if handle is ticket),None)
        if (row is None or row['state']!='assignment-unknown' or row['assignment'] is None
                or row['assignment']['project_id']!=project_id):
            raise StateError('Original pending runner assignment required')
        try:
            actual=self._assignment_observation(row,self.session)
            if actual != row['assignment']['project_ids']:
                raise StateError('Runner assignment remains unobserved')
        except BaseException:
            self.session.stop_writes()
            raise
        row['project_ids']=actual
        row['assignment']['state']='observed-assigned'
        row['state']='owned-present'
        self._event('runner-assignment-observed',row)

    def reconcile_runner_assignment(self, context, ticket, operation_id):
        self.owner._admit(context,'up',operation_id=operation_id)
        row=next((row for handle,row in self.runners if handle is ticket),None)
        if (os.getpid()!=self.pid or row is None or row['state']!='assignment-unknown'
                or row['assignment'] is None or row['assignment']['operation_id']!=operation_id):
            raise StateError('Original uncertain runner assignment required')
        # This never clears the original shared write poison or dispatches an
        # assignment. It records current ownership for a separately admitted
        # cleanup after the original uncertain operation has been reconciled.
        reader=self.session.bounded_read_session(**self._route_options(context,operation_id))
        actual=self._assignment_observation(row,reader)
        row['project_ids']=actual
        row['assignment']['state']='observed-assigned' if actual==row['assignment']['project_ids'] else 'observed-unassigned'
        row['state']='owned-present'
        self._event('runner-assignment-reconciled',row)
        return deepcopy(row)

    def runner_created(self, ticket, runner_id):
        self.assert_binding(self.session, self.scope)
        row = next((row for handle, row in self.runners if handle is ticket), None)
        if row is None or row['state'] != 'create-unknown' or type(runner_id) is not int or runner_id <= 0:
            raise StateError('Runner creation handle is not current')
        row['id'] = runner_id
        self._event('runner-id-observed', row)
        _identity(self.session, self.scope)
        self._runner_identity(self.session.request('GET', f'/api/v4/runners/{runner_id}'), row)
        row['state'] = 'owned-present'
        self._event('owned-runner-observed', row)

    def _get(self, path):
        try:
            return self.session.request('GET', path)
        except GitLabAPIError as error:
            if error.status == 404:
                return None
            raise

    def _project_absent(self, row):
        _identity(self.session, self.scope)
        if self._get(f"/api/v4/projects/{row['id']}") is not None:
            return False
        if self._get('/api/v4/projects/'+quote(row['path'], safe='')) is not None:
            raise StateError('Deleted project path was reused')
        projects = self.session.get_collection('/api/v4/projects?owned=true&simple=true')
        if any(item.get('id') == row['id'] or item.get('path_with_namespace') == row['path'] for item in projects):
            raise StateError('Project absence disagrees with owner inventory')
        _identity(self.session, self.scope)
        return True

    def _runner_absent(self, row):
        _identity(self.session, self.scope)
        if self._get(f"/api/v4/runners/{row['id']}") is not None:
            return False
        for project_id in row['project_ids']:
            values = self.session.get_collection(f"/api/v4/projects/{project_id}/runners")
            if any(item.get('id') == row['id'] for item in values):
                raise StateError('Runner absence disagrees with project inventory')
        _identity(self.session, self.scope)
        return True

    def down(self, context):
        operation = self.owner._admit(context, 'down')
        if self._down_operation is not None:
            raise StateError('GitLab deletion was already attempted; use read-only reconciliation')
        self._down_operation = operation
        self.session = self.session.cleanup_session(**self._route_options(context))
        for _, row in [*self.runners, *reversed(self.projects)]:
            self.owner._admit(context, 'down')
            if row['state'] != 'owned-present':
                return self.observation(operation)
            _identity(self.session, self.scope)
            if row['kind'] == 'gitlab-runner':
                self._runner_identity(self.session.request('GET', f"/api/v4/runners/{row['id']}"), row)
                row['state'] = 'delete-unknown'
                self._event('runner-delete-intent', row)
                self.session.request('DELETE', f"/api/v4/runners/{row['id']}", expected=(204,))
                absent = self._runner_absent(row)
            else:
                fresh = self.session.request('GET', f"/api/v4/projects/{row['id']}")
                _project_identity(fresh, self.scope, row['role'], row['id'])
                if fresh.get('marked_for_deletion_on') is not None:
                    raise StateError('Project deletion was changed outside this coordinator')
                row.update(state='delete-unknown', delete_stage='mark')
                self._event('project-mark-intent', row)
                self.session.request('DELETE', f"/api/v4/projects/{row['id']}", expected=(202,))
                if self._project_absent(row):
                    absent = True
                else:
                    fresh = self.session.request('GET', f"/api/v4/projects/{row['id']}")
                    _project_identity(fresh, self.scope, row['role'], row['id'])
                    if not isinstance(fresh.get('marked_for_deletion_on'), str):
                        return self.observation(operation)
                    # This is a distinct documented second-stage operation, not
                    # a retry of the original mark request.
                    row['delete_stage'] = 'permanent'
                    self.owner._admit(context, 'down')
                    self._event('project-permanent-delete-intent', row)
                    self.session.request('DELETE', f"/api/v4/projects/{row['id']}",
                        {'permanently_remove': True, 'full_path': row['path']}, expected=(202,))
                    absent = self._project_absent(row)
            if not absent:
                return self.observation(operation)
            row['state'] = 'absent'
            self._event('absence-observed', row)
        if self.token is not None:
            self.owner._admit(context, 'down')
            actual = self.session.request('GET', '/api/v4/personal_access_tokens/self')
            if actual.get('id') != self.token['id'] or actual.get('user_id') != self.scope.owner_user_id:
                raise StateError('Token identity changed before revocation')
            self.token['state'] = 'delete-unknown'
            self._event('token-revoke-intent', self.token)
            self.session.request('DELETE', f"/api/v4/personal_access_tokens/{self.token['id']}", expected=(204,))
            if self._token_absent(context):
                self.token['state'] = 'absent'
                self._event('token-invalidation-observed', self.token)
        return self.observation(operation)

    def reconcile(self, context, operation_id):
        self.owner._admit(context, context.phase['operation'], operation_id=operation_id)
        if operation_id != self._down_operation:
            raise StateError('GitLab reconciliation needs the original deletion operation')
        self.session = self.session.cleanup_session(read_only=True,**self._route_options(context,operation_id))
        token_terminal = self.token is not None and self.token['state'] in ('delete-unknown', 'absent')
        for _, row in [*self.runners, *reversed(self.projects)]:
            if token_terminal and row['state'] == 'absent':
                continue  # Verified before revocation; revoked credentials cannot re-read resources.
            if row['state'] not in ('delete-unknown', 'absent'):
                continue
            if row['kind'] == 'gitlab-runner':
                # A deleted project prevents runner-scope inventory. Preserve
                # the already observed runner absence; do not reinterpret 404.
                if row['state'] == 'absent' and any(p['id'] in row['project_ids'] and p['state'] == 'absent' for _, p in self.projects):
                    continue
                absent = self._runner_absent(row)
            else:
                absent = self._project_absent(row)
            if absent:
                row['state'] = 'absent'
                self._event('reconciled-absence', row)
        if self.token is not None and self.token['state'] in ('delete-unknown', 'absent'):
            if self._token_absent(context):
                self.token['state'] = 'absent'
                self._event('token-invalidation-reconciled', self.token)
        return self.observation(operation_id)

    def observation(self, operation_id):
        rows = deepcopy([row for _, row in [*self.runners, *self.projects]] + ([self.token] if self.token else []))
        complete = all(row['state'] == 'absent' for row in rows)
        return {'run_id': self.context.run_id, 'operation_id': operation_id,
            'identities': dict(self.context.identities), 'action': 'down', 'resources': rows,
            'complete_cleanup': complete, 'mutation_effect': 'applied' if complete else 'unknown',
            'unresolved_operations': [row['kind'] for row in rows if row['state'] != 'absent'],
            'preserved': ['operator-selected-token-input-bytes', 'operator-selected-trust']}

