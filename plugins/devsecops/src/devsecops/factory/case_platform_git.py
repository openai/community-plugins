"""D3-only Git transitions for one originally owned platform extension file.

This is not a general push, merge, patch or command interface. It changes only
the selected extension's dependsOn field and restores the original exact bytes.
Uncertain writes retain their original intent and poison the direct API session.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import os
from pathlib import Path
import re
from weakref import WeakKeyDictionary

from devsecops.resources import strict_load_yaml
from .gitlab_bootstrap import BootstrapScope, _branch, _file, _project_identity
from .gitlab_api import GitLabAPIError
from .lifecycle import PhaseContext
from .state import RunJournal, StateError, _canonical, _exclusive, _read, _sha

_TRANSITIONS = WeakKeyDictionary()


class PlatformGitTransition:
    __slots__ = ('__weakref__',)
    def __new__(cls):
        raise StateError('Platform transitions require actual current GitLab writes and readbacks')
    def __reduce__(self):
        raise StateError('Platform Git authority cannot be restored from JSON')


def require_platform_transition(value, *, journal, context):
    record = _TRANSITIONS.get(value) if type(value) is PlatformGitTransition else None
    if record is None or record['pid'] != os.getpid() or record['owner'].journal is not journal:
        raise StateError('Original same-process platform Git transition required')
    record['owner']._admit(context)
    if record['owner']._last is not value:
        raise StateError('Only the current platform transition can select a Flux revision')
    for path, digest in record['files'].items():
        if _sha(_read(path, 1_000_000)) != digest:
            raise StateError('Original platform Git evidence changed')
    return deepcopy(record['binding'])


def extension_fault(baseline, selection):
    documents = strict_load_yaml(baseline.decode('utf-8'))
    if len(documents) != 1 or type(documents[0]) is not dict:
        raise StateError('D3 requires one exact optional-extension manifest')
    document = documents[0]
    metadata, spec = document.get('metadata', {}), document.get('spec', {})
    dependency = {'name': selection['dependency_name']}
    if (set(document) != {'apiVersion', 'kind', 'metadata', 'spec'}
            or document['apiVersion'] != 'kustomize.toolkit.fluxcd.io/v1'
            or document['kind'] != 'Kustomization' or metadata.get('name') != selection['extension_name']
            or metadata.get('namespace') != selection['namespace'] or spec.get('dependsOn') != [dependency]
            or spec.get('sourceRef') != {'kind': 'GitRepository', 'name': selection['source_name']}
            or spec.get('suspend', False) is not False or spec.get('deletionPolicy', 'MirrorPrune') not in ('MirrorPrune', 'Delete')):
        raise StateError('D3 baseline is not the selected enabled extension and exact dependency')
    missing = selection['dependency_name'] + '-unavailable'
    if len(missing) > 63:
        raise StateError('D3 bounded missing-dependency name is too long')
    changed = deepcopy(document)
    changed['spec']['dependsOn'] = [{'name': missing}]
    return _canonical(changed)


def _tree(session, project_id, revision):
    rows = session.get_collection(f'/api/v4/projects/{project_id}/repository/tree?ref={revision}&recursive=true&per_page=100',
                                  max_pages=1, max_items=64)
    if type(rows) is not list or not rows or len(rows) >= 64:
        raise StateError('D3 source tree is outside the bounded owned project')
    result = {}
    for row in rows:
        path, kind = row.get('path'), row.get('type')
        if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,7}', path)
                or any(part in ('.', '..', '.git') for part in path.split('/')) or path in result
                or kind not in ('tree', 'blob') or row.get('mode') != ('040000' if kind == 'tree' else '100644')
                or not re.fullmatch(r'[a-f0-9]{40}', row.get('id', ''))):
            raise StateError('D3 source tree is unsupported or ambiguous')
        result[path] = {'type': kind, 'id': row['id'], 'mode': row['mode']}
    return result


def unchanged_other_blobs(before, after, path):
    if set(before) != set(after) or path not in before or before[path]['type'] != 'blob':
        raise StateError('D3 commit changed the source tree membership')
    for name, value in before.items():
        if name != path and value['type'] == 'blob' and value != after[name]:
            raise StateError('D3 commit changed an unrelated file')


def case_branch_head(session, project_id, branch, *, absent=False):
    if not re.fullmatch(r'codex-d3-[a-f0-9]{32}', branch):
        raise StateError('Only the fixed per-run D3 branch is supported')
    route = f'/api/v4/projects/{project_id}/repository/branches/{branch}'
    receipt_count = len(session.receipts)
    try:
        value = session.request('GET', route, expected=(200,))
    except GitLabAPIError as error:
        receipts = session.receipts
        if (absent and error.code == 'http-status' and error.status == 404 and error.outcome == 'none'
                and len(receipts) == receipt_count + 1 and receipts[-1].get('method') == 'GET'
                and receipts[-1].get('request_sha256') == _sha(route.encode()) and receipts[-1].get('status') == 404):
            return None
        raise
    status = session.receipts[-1].get('status')
    if absent:
        raise StateError('D3 branch already exists; no branch may be adopted or overwritten')
    if (status != 200 or type(value) is not dict or value.get('name') != branch
            or not re.fullmatch(r'[a-f0-9]{40}', value.get('commit', {}).get('id', ''))):
        raise StateError('D3 branch identity is absent or changed')
    return value['commit']['id']


class OwnedPlatformGit:
    def __init__(self, journal, *, operations, selection, output):
        from .owned_operations import OwnedOperationCoordinator
        if (type(journal) is not RunJournal or type(operations) is not OwnedOperationCoordinator
                or operations.journal is not journal):
            raise StateError('D3 writer requires the direct same-run operation coordinator')
        self.journal, self.operations = journal, operations
        self.selection, self.output = deepcopy(selection), Path(output)
        self._pid, self._last, self._dispatched = os.getpid(), None, set()
        self._files, self._initial, self._fault = {}, None, None

    def _admit(self, context):
        if (self._pid != os.getpid() or type(context) is not PhaseContext
                or context.phase['id'] != 'platform-dependency' or context.phase['operation'] != 'case'
                or context.execution_mode != 'operator' or context.inputs['config']['profile'] != 'bigbang-live'
                or not {'inject', 'deliver', 'probe'} <= set(context.inputs['config']['allowed_mutations'])):
            raise StateError('D3 writer requires the exact live case and inject/deliver/probe grants')
        self.operations._admit(context)
        return self.journal.current_mutation('platform-dependency', 'platform-dependency')

    def _write(self, name, value):
        raw = value if type(value) is bytes else _canonical(value)
        path = self.output / name
        _exclusive(path, raw)
        self._files[path] = _sha(raw)
        return path

    def transition(self, context, stage):
        operation = self._admit(context)
        if stage not in ('fault', 'repair') or stage in self._dispatched or (stage == 'repair') != (self._last is not None):
            raise StateError('D3 transitions are one fault then one repair; uncertain effects require inspection')
        from .canonical_bootstrap import require_bootstrap_authority
        bootstrap = self.operations.bootstrap
        authority, session = bootstrap.gitlab(context)
        observed = require_bootstrap_authority(authority, self.journal)
        scope = BootstrapScope(**dict(observed['scope']))
        project = observed['projects']['deployment']
        project_id = project['id']
        source_url = scope.origin + '/' + project['path_with_namespace'] + '.git'
        selected = self.selection
        branch = 'codex-d3-' + context.run_id.replace('-', '')
        resources = bootstrap.kubernetes(context)
        descriptor = {'apiVersion': 'source.toolkit.fluxcd.io/v1', 'kind': 'GitRepository',
                      'metadata': {'name': selected['source_name'], 'namespace': selected['namespace']}}
        source = resources.read_owned(context, descriptor)
        extension_descriptor = {'apiVersion': 'kustomize.toolkit.fluxcd.io/v1', 'kind': 'Kustomization',
                                'metadata': {'namespace': selected['namespace'], 'name': selected['extension_name']}}
        extension = (resources.read_owned(context, extension_descriptor) if stage == 'fault' else
                     resources.read_platform_extension(context, transition=self._last, descriptor=extension_descriptor))
        source_commit = source.get('spec', {}).get('ref', {}).get('commit')
        if (source.get('spec', {}).get('url') != source_url or not isinstance(source_commit, str)
                or not re.fullmatch(r'[a-f0-9]{40}', source_commit)
                or set(source['spec']['ref']) != {'commit'}):
            raise StateError('D3 source is not the original owned project at an immutable commit')
        if stage == 'fault':
            missing_path = ('/apis/kustomize.toolkit.fluxcd.io/v1/namespaces/' + selected['namespace']
                            + '/kustomizations/' + selected['dependency_name'] + '-unavailable')
            if resources.session.request('GET', missing_path, deadline=context.deadline) is not None:
                raise StateError('D3 missing-dependency control already exists; no fault may be staged')
            self.output.mkdir(mode=0o700)
            baseline = _read(Path(selected['baseline_file']['path']), 1_000_000)
            if _sha(baseline) != selected['baseline_file']['sha256']:
                raise StateError('D3 extension baseline differs from its explicit selected bytes')
            self._initial, self._fault = baseline, extension_fault(baseline, selected)
            self._write('baseline.yaml', baseline)
            self._write('fault.yaml', self._fault)
        else:
            prior = require_platform_transition(self._last, journal=self.journal, context=context)
            if source_commit != prior['commit'] or source['metadata']['uid'] != prior['uid']:
                raise StateError('D3 repair requires the observed original fault source selection')
        _project_identity(session.request('GET', f'/api/v4/projects/{project_id}'), scope, 'deployment', project_id)
        main = _branch(session, project_id)
        parent = source_commit if stage == 'fault' else case_branch_head(session, project_id, branch)
        if stage == 'fault':
            case_branch_head(session, project_id, branch, absent=True)
        if stage == 'repair' and parent != prior['commit']:
            raise StateError('D3 repair base diverged from the exact fault commit')
        expected = self._initial if stage == 'fault' else self._fault
        replacement = self._fault if stage == 'fault' else self._initial
        file_path = selected['repository_path']
        original = _file(session, project_id, file_path, parent, expected_content=expected)
        before = _tree(session, project_id, parent)
        if before.get(file_path, {}).get('id') != original['blob_id']:
            raise StateError('D3 selected file is absent from the actual owned source tree')
        payload = {'branch': branch, 'commit_message': 'Owned D3 extension ' + stage,
                   'force': False, 'allow_empty': False, 'actions': [{'action': 'update', 'file_path': file_path,
                    'encoding': 'base64', 'content': base64.b64encode(replacement).decode('ascii'),
                    'last_commit_id': original['last_commit_id']}]}
        self._admit(context)
        if _branch(session, project_id) != main or stage == 'repair' and case_branch_head(session, project_id, branch) != parent:
            raise StateError('D3 branch changed before the authorized write')
        if stage == 'fault':
            case_branch_head(session, project_id, branch, absent=True)
        intent = {'run_id': context.run_id, 'identities': dict(context.identities), 'operation_id': operation,
                  'stage': stage, 'project_id': project_id, 'branch': branch, 'main_preserved': main,
                  'parent_commit': parent, 'repository_path': file_path,
                  'replacement_sha256': _sha(replacement), 'payload_sha256': _sha(_canonical(payload))}
        self._write(stage + '-intent.json', intent)
        self._dispatched.add(stage)
        try:
            if stage == 'fault':
                self._write('branch-create-intent.json', {'branch': branch, 'source_commit': source_commit,
                    'project_id': project_id, 'operation_id': operation, 'adoption': 'forbidden'})
                created = session.request('POST', f'/api/v4/projects/{project_id}/repository/branches',
                    payload={'branch': branch, 'ref': source_commit}, expected=(201,))
                if (created.get('name') != branch or created.get('commit', {}).get('id') != source_commit
                        or case_branch_head(session, project_id, branch) != source_commit):
                    raise StateError('D3 create-only branch differs from original selected source')
                self._write('branch-created.json', {'branch': branch, 'source_commit': source_commit,
                    'project_id': project_id, 'transport_receipts': session.receipts})
            if case_branch_head(session, project_id, branch) != parent or _branch(session, project_id) != main:
                raise StateError('D3 branch changed before exact commit creation')
            commit = session.request('POST', f'/api/v4/projects/{project_id}/repository/commits', payload=payload, expected=(201,))
            commit_id = commit.get('id')
            if (not isinstance(commit_id, str) or not re.fullmatch(r'[a-f0-9]{40}', commit_id)
                    or commit.get('parent_ids') != [parent]):
                raise StateError('D3 commit parent or identity differs')
            if case_branch_head(session, project_id, branch) != commit_id or _branch(session, project_id) != main:
                raise StateError('D3 branch readback differs from the created commit')
            changed = _file(session, project_id, file_path, commit_id, expected_content=replacement)
            after = _tree(session, project_id, commit_id)
            unchanged_other_blobs(before, after, file_path)
            if after[file_path]['id'] != changed['blob_id']:
                raise StateError('D3 committed blob differs from its exact content readback')
            _project_identity(session.request('GET', f'/api/v4/projects/{project_id}'), scope, 'deployment', project_id)
            self._admit(context)
            if case_branch_head(session, project_id, branch) != commit_id or _branch(session, project_id) != main:
                raise StateError('D3 branch changed after readback')
            binding = {'run_id': context.run_id, 'identities': dict(context.identities), 'operation_id': operation,
                       'stage': stage, 'namespace': selected['namespace'], 'name': selected['source_name'],
                       'uid': source['metadata']['uid'], 'previous_commit': source_commit, 'commit': commit_id,
                       'project_id': project_id, 'source_url': source_url, 'repository_path': file_path,
                       'branch': branch, 'main_preserved': main,
                       'file_sha256': _sha(replacement),
                       'extension': {'namespace': selected['namespace'], 'name': selected['extension_name'],
                                     'uid': extension['metadata']['uid']},
                       'expected_depends_on': strict_load_yaml(replacement.decode('utf-8'))[0]['spec']['dependsOn'],
                       'previous_depends_on': strict_load_yaml(expected.decode('utf-8'))[0]['spec']['dependsOn']}
            self._write(stage + '-observed.json', {'kind': 'owned-platform-git-transition', 'binding': binding,
                'parent_commit': parent, 'other_tree_blobs_preserved': True, 'transport_receipts': session.receipts})
        except BaseException:
            session.stop_writes()
            raise
        result = object.__new__(PlatformGitTransition)
        _TRANSITIONS[result] = {'pid': self._pid, 'owner': self, 'binding': binding, 'files': dict(self._files)}
        self._last = result
        require_platform_transition(result, journal=self.journal, context=context)
        return result
