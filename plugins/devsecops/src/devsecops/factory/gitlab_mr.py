"""One-shot MR creation, CI gate observation and source-pinned fast-forward merge.

Unregistered component: no restored artifact grants action-time authority, no
branch writer, approval impersonation, CI trigger, controller sync or promotion.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, urlencode

from devsecops.adapters.artifacts import _open_directory
from devsecops.runtime.cli import _verify_output_path, _write
from .git_destination import _ref
from .gitlab_api import GitLabSession, VERSION


class MergeRequestError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('Merge request workflow stopped (' + code + ')')


@dataclass(frozen=True)
class MRSelection:
    project_id: int
    path_with_namespace: str
    namespace_id: int
    source_branch: str
    target_branch: str
    source_sha: str
    target_sha: str
    required_jobs: tuple[str, ...]
    run_id: str

    def __post_init__(self):
        if (any(type(value) is not int or not 0 < value < 2**63
                for value in (self.project_id, self.namespace_id))
                or not isinstance(self.path_with_namespace, str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.path_with_namespace)
                or any(not isinstance(value, str) or not re.fullmatch('[a-f0-9]{40}', value)
                       or value == '0' * 40 for value in (self.source_sha, self.target_sha))
                or self.source_sha == self.target_sha
                or self.source_branch == self.target_branch
                or not isinstance(self.run_id, str) or not re.fullmatch('[a-f0-9]{64}', self.run_id)
                or type(self.required_jobs) is not tuple or not 1 <= len(self.required_jobs) <= 32
                or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', name)
                       for name in self.required_jobs)
                or len(set(self.required_jobs)) != len(self.required_jobs)):
            raise MergeRequestError('invalid-selection')
        for branch in (self.source_branch, self.target_branch):
            if not isinstance(branch, str) or len(branch) > 255:
                raise MergeRequestError('invalid-selection')
            _ref('refs/heads/' + branch)


def _fingerprint(session, selection):
    return hashlib.sha256(json.dumps({'origin': session.origin,
        'principal_id': session.expected_user_id, 'selection': asdict(selection)},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _guard_key(session, selection):
    # Uncertainty concerns the external branch pair, not the caller's current
    # token, gate order, run label or SHA. None of those may reset the guard.
    return hashlib.sha256(json.dumps([session.origin, selection.project_id,
        selection.source_branch, selection.target_branch], separators=(',', ':')).encode()).hexdigest()


def _finished(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


class MergeRequestWorkflow:
    """A direct process-local workflow. Existing journal directories never resume.

    Keep one stable journal_root for the selected run. Its deterministic scope
    directory is exclusively created before reads or writes; any prior attempt,
    including a crash after intent, requires read-only reconciliation. Deleting
    or changing that ledger is an explicit owner recovery decision, not retry.
    """
    def __init__(self, session, selection, *, journal_root, runner_origin=None):
        if type(session) is not GitLabSession or type(selection) is not MRSelection:
            raise MergeRequestError('direct-session-and-selection-required')
        if not isinstance(journal_root, Path) or not journal_root.is_absolute() or '..' in journal_root.parts:
            raise MergeRequestError('absolute-journal-root-required')
        self.session, self.selection = session, selection
        self._runner_origin = runner_origin
        self._runner_facts(current=True)
        self._key = _fingerprint(session, selection)
        self.path = journal_root / ('mr-' + _guard_key(session, selection))
        root = _open_directory(journal_root)
        self._fd = None
        try:
            info = os.fstat(root)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise MergeRequestError('journal-root-ownership')
            try:
                os.mkdir(self.path.name, mode=0o700, dir_fd=root)
            except FileExistsError:
                raise MergeRequestError('existing-attempt-reconcile-only') from None
            os.fsync(root)
            self._fd = os.open(self.path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        finally:
            os.close(root)
        self._iid, self._phase, self._pid, self._events = None, 'new', os.getpid(), {}
        try:
            self._event('selection', {'kind': 'gitlab-mr-selection', 'origin': session.origin,
                'principal_id': session.expected_user_id, 'version': VERSION, 'edition': 'CE',
                'selection': asdict(selection), 'authority': 'not-issued'})
            if self._runner_origin is not None:
                self._event('runner-origin', self._runner_facts(current=True))
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __reduce__(self):
        raise TypeError('MR workflows cannot be serialized')

    def _runner_facts(self, *, current=False):
        if self._runner_origin is None:
            return None
        from .owned_runner_service import runner_execution_binding
        facts = runner_execution_binding(self._runner_origin, current=current)
        if (facts['origin'] != self.session.origin or self.selection.project_id not in facts['project_ids']):
            raise MergeRequestError('original-guest-runner-project-binding')
        return facts

    def _audit(self):
        if self._fd is None or self._pid != os.getpid():
            raise MergeRequestError('closed-or-foreign-workflow')
        self._runner_facts()
        _verify_output_path(self.path, self._fd)
        if _fingerprint(self.session, self.selection) != self._key:
            raise MergeRequestError('selection-changed')
        # Durable write guards must remain present and byte-identical. Reports
        # are evidence only, but removing an intent must never enable a retry.
        for name, expected in self._events.items():
            fd = os.open(name + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._fd)
            with os.fdopen(fd, 'rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != expected['sha256']:
                raise MergeRequestError('journal-changed')

    def _event(self, name, value):
        self._audit()
        ref = _write(self._fd, name + '.json', value)
        os.fsync(self._fd)
        self._events[name] = ref

    @property
    def _project(self):
        return '/api/v4/projects/' + str(self.selection.project_id)

    def _branch(self, name):
        value = self.session.request('GET', self._project + '/repository/branches/' + quote(name, safe=''))
        if type(value) is not dict or value.get('name') != name or type(value.get('commit')) is not dict:
            raise MergeRequestError('branch-identity')
        return value

    def _project_policy(self):
        user = self.session.verify_identity()['principal']
        value = self.session.request('GET', self._project)
        selected = self.selection
        expected = {'id': selected.project_id, 'path_with_namespace': selected.path_with_namespace,
            'visibility': 'private', 'archived': False, 'default_branch': selected.target_branch,
            'merge_method': 'ff', 'squash_option': 'never',
            'only_allow_merge_if_pipeline_succeeds': True, 'allow_merge_on_skipped_pipeline': False,
            'only_allow_merge_if_all_discussions_are_resolved': True,
            'remove_source_branch_after_merge': False}
        if (type(value) is not dict or any(type(value.get(key)) is not type(item) or value.get(key) != item
                                          for key, item in expected.items())
                or type(value.get('owner')) is not dict or type(value['owner'].get('id')) is not int
                or value['owner'].get('id') != user['id']
                or type(value.get('namespace')) is not dict
                or type(value['namespace'].get('id')) is not int
                or value['namespace'].get('id') != selected.namespace_id
                or value['namespace'].get('kind') != 'user'
                or value['namespace'].get('full_path') != user['username']
                or selected.path_with_namespace.split('/')[0] != user['username']):
            raise MergeRequestError('project-owner-or-policy')
        permissions = value.get('permissions')
        access = permissions.get('project_access') if type(permissions) is dict else None
        if type(access) is not dict or type(access.get('access_level')) is not int or access['access_level'] < 40:
            raise MergeRequestError('project-permission')

    def _branches(self, *, merged=False):
        source = self._branch(self.selection.source_branch)
        target = self._branch(self.selection.target_branch)
        target_sha = self.selection.source_sha if merged else self.selection.target_sha
        if (source['commit'].get('id') != self.selection.source_sha
                or target['commit'].get('id') != target_sha or target.get('protected') is not True):
            raise MergeRequestError('branch-sha-or-protection')

    def _mr_identity(self, value, *, merged=False):
        selected = self.selection
        expected = {'project_id': selected.project_id, 'source_project_id': selected.project_id,
            'target_project_id': selected.project_id, 'source_branch': selected.source_branch,
            'target_branch': selected.target_branch, 'sha': selected.source_sha,
            'state': 'merged' if merged else 'opened', 'draft': False}
        if (type(value) is not dict or any(type(value.get(key)) is not type(item) or value.get(key) != item
                                          for key, item in expected.items())
                or type(value.get('iid')) is not int or not 0 < value['iid'] < 2**63
                or (self._iid is not None and value['iid'] != self._iid)
                or type(value.get('author')) is not dict
                or type(value['author'].get('id')) is not int
                or value['author'].get('id') != self.session.expected_user_id
                or value.get('description') != self._description):
            raise MergeRequestError('mr-identity')
        return value

    @property
    def _description(self):
        return 'Owned demo run ' + self.selection.run_id + '\nScope ' + self._key

    def _fetch_mr(self, *, merged=False):
        value = self.session.request('GET', self._project + '/merge_requests/' + str(self._iid))
        return self._mr_identity(value, merged=merged)

    def _mutate(self, action, method, path, payload, expected):
        self._audit()
        self._event(action + '-intent', {'action': action, 'method': method, 'path': path,
            'payload': payload, 'scope_sha256': self._key, 'outcome': 'unknown-until-readback'})
        self._phase = action + '-attempted'
        try:
            result = self.session.request(method, path, payload, expected=expected)
            self._event(action + '-response', {'status': 'response-observed',
                'response_sha256': hashlib.sha256(json.dumps(result, sort_keys=True,
                    separators=(',', ':')).encode()).hexdigest()})
            return result
        except BaseException:
            self.session.stop_writes()
            self._phase = 'reconcile-only'
            # The fsynced pre-dispatch intent remains authoritative evidence of
            # uncertainty even if writing this diagnostic is itself interrupted.
            raise

    def create(self):
        self._runner_facts(current=True)
        self._audit()
        if self._phase != 'new' or self.session.writes_blocked:
            raise MergeRequestError('reconcile-only')
        self._project_policy()
        self._branches()
        query = urlencode({'scope': 'all', 'state': 'all', 'source_branch': self.selection.source_branch,
                           'target_branch': self.selection.target_branch})
        existing = self.session.get_collection(self._project + '/merge_requests?' + query)
        if existing:
            raise MergeRequestError('existing-mr-reconcile-only')
        payload = {'source_branch': self.selection.source_branch, 'target_branch': self.selection.target_branch,
            'title': 'Reviewed demo change ' + self.selection.run_id[:12], 'description': self._description,
            'remove_source_branch': False, 'allow_collaboration': False, 'squash': False}
        try:
            result = self._mutate('create', 'POST', self._project + '/merge_requests', payload, (201,))
            self._iid = self._mr_identity(result)['iid']
            self._fetch_mr()
            self._branches()
            self._event('created', {'iid': self._iid, 'source_sha': self.selection.source_sha})
            self._phase = 'created'
            return {'iid': self._iid, 'state': 'opened', 'source_sha': self.selection.source_sha}
        except BaseException:
            if self._phase != 'new':
                self.session.stop_writes()
                self._phase = 'reconcile-only'
            raise

    def observe_gates(self):
        runner = self._runner_facts(current=True)
        self._audit()
        if self._phase != 'created':
            raise MergeRequestError('current-created-mr-required')
        self._project_policy()
        self._branches()
        mr = self._fetch_mr()
        if (mr.get('detailed_merge_status') != 'mergeable' or mr.get('has_conflicts') is not False
                or mr.get('blocking_discussions_resolved') is not True or mr.get('squash') is not False):
            raise MergeRequestError('mr-gates-not-ready')
        diff = mr.get('diff_refs')
        if type(diff) is not dict or diff != {'base_sha': self.selection.target_sha,
                'start_sha': self.selection.target_sha, 'head_sha': self.selection.source_sha}:
            raise MergeRequestError('mr-diff-binding')
        head = mr.get('head_pipeline')
        if type(head) is not dict or type(head.get('id')) is not int or not 0 < head['id'] < 2**63:
            raise MergeRequestError('mr-pipeline-missing')
        pipeline_id = head['id']
        pipeline = self.session.request('GET', self._project + '/pipelines/' + str(pipeline_id))
        expected = {'id': pipeline_id, 'project_id': self.selection.project_id,
            'sha': self.selection.source_sha, 'ref': f'refs/merge-requests/{self._iid}/head',
            'status': 'success', 'source': 'merge_request_event'}
        for value in (head, pipeline):
            if type(value) is not dict or any(type(value.get(key)) is not type(item) or value.get(key) != item
                                             for key, item in expected.items()):
                raise MergeRequestError('pipeline-binding-or-status')
        if pipeline.get('tag') is not False or pipeline.get('yaml_errors') is not None:
            raise MergeRequestError('pipeline-shape')
        jobs = self.session.get_collection(self._project + '/pipelines/' + str(pipeline_id)
                                          + '/jobs?include_retried=false')
        if self.session.get_collection(self._project + '/pipelines/' + str(pipeline_id) + '/trigger_jobs'):
            raise MergeRequestError('child-pipeline-unsupported')
        names, ids, gates = set(), set(), {}
        for job in jobs:
            if type(job) is not dict or type(job.get('name')) is not str or type(job.get('id')) is not int:
                raise MergeRequestError('job-binding')
            name, jid = job['name'], job['id']
            if name in names or jid in ids or jid <= 0:
                raise MergeRequestError('duplicate-job')
            names.add(name)
            ids.add(jid)
            if name not in self.selection.required_jobs:
                continue
            jp, commit = job.get('pipeline'), job.get('commit')
            if (job.get('status') != 'success' or job.get('allow_failure') is not False
                    or job.get('erased_at') is not None or not _finished(job.get('finished_at'))
                    or type(jp) is not dict or any(type(jp.get(key)) is not type(item) or jp.get(key) != item
                        for key, item in expected.items() if key in ('id', 'project_id', 'sha', 'ref', 'status'))
                    or type(commit) is not dict or commit.get('id') != self.selection.source_sha):
                raise MergeRequestError('required-job-gate')
            if runner is not None and (
                    type(job.get('runner')) is not dict or type(job['runner'].get('id')) is not int
                    or job['runner'].get('id') != runner['runner_id']
                    or type(job.get('runner_manager')) is not dict
                    or job['runner_manager'].get('system_id') != runner['runner_manager_system_identity']
                    or job['runner_manager'].get('platform') != 'linux'
                    or job['runner_manager'].get('architecture') != 'amd64'):
                raise MergeRequestError('required-job-original-runner')
            gates[name] = jid
        if set(gates) != set(self.selection.required_jobs):
            raise MergeRequestError('required-job-missing')
        # Re-observe head and branches after the paginated gate collection. These
        # are snapshot consistency checks, not an atomic target/pipeline CAS.
        if self._fetch_mr().get('head_pipeline') != head:
            raise MergeRequestError('pipeline-changed')
        self._branches()
        return {'iid': self._iid, 'source_sha': self.selection.source_sha,
                'target_baseline_sha': self.selection.target_sha,
                'pipeline_id': pipeline_id, 'required_jobs': gates}

    def merge(self):
        gates = self.observe_gates()  # Never trust a saved gate result supplied by the caller.
        payload = {'sha': self.selection.source_sha, 'auto_merge': False,
                   'squash': False, 'should_remove_source_branch': False}
        try:
            result = self._mutate('merge', 'PUT', self._project + '/merge_requests/' + str(self._iid)
                                  + '/merge', payload, (200,))
            self._mr_identity(result, merged=True)
            self._project_policy()
            final = self._fetch_mr(merged=True)
            head = final.get('head_pipeline')
            expected = {'id': gates['pipeline_id'], 'project_id': self.selection.project_id,
                'sha': self.selection.source_sha, 'ref': f'refs/merge-requests/{self._iid}/head',
                'status': 'success', 'source': 'merge_request_event'}
            if (type(head) is not dict or any(type(head.get(key)) is not type(item) or head.get(key) != item
                                            for key, item in expected.items())
                    or final.get('merge_commit_sha') not in (None, self.selection.source_sha)
                    or final.get('squash_commit_sha') is not None):
                raise MergeRequestError('merged-pipeline-changed')
            self._branches(merged=True)
            receipt = {'kind': 'gitlab-mr-observed-merge', **gates,
                'target_observed_sha': self.selection.source_sha,
                'source_sha_server_precondition': True, 'target_sha_atomic_precondition': False,
                'pipeline_id_atomic_precondition': False, 'merge_method': 'ff',
                'controller_sync': 'not-established', 'delivery_authority': 'not-issued'}
            self._event('merged', receipt)
            self._phase = 'merged'
            return receipt
        except BaseException:
            self.session.stop_writes()
            self._phase = 'reconcile-only'
            raise

    def reconcile(self):
        """Read-only current-state evidence after an uncertain create or merge.

        Absence is inconclusive. A matching MR is an observation, never restored
        write authority, ownership of a new resource, or a declaration of merge.
        """
        self._audit()
        if self._phase not in ('reconcile-only', 'create-attempted', 'merge-attempted'):
            raise MergeRequestError('uncertain-attempt-required')
        return reconcile_selection(self.session, self.selection)


def reconcile_selection(session, selection):
    """Independent read-only reconciliation; deliberately no journal resume API."""
    if type(session) is not GitLabSession or type(selection) is not MRSelection:
        raise MergeRequestError('direct-session-and-selection-required')
    session.verify_identity()
    project = '/api/v4/projects/' + str(selection.project_id)
    query = urlencode({'scope': 'all', 'state': 'all', 'source_branch': selection.source_branch,
                       'target_branch': selection.target_branch})
    items = session.get_collection(project + '/merge_requests?' + query)
    marker = 'Owned demo run ' + selection.run_id + '\nScope ' + _fingerprint(session, selection)
    matches = []
    for item in items:
        if (type(item) is dict and type(item.get('iid')) is int and 0 < item['iid'] < 2**63
                and all(type(item.get(key)) is int and item[key] == selection.project_id
                        for key in ('project_id', 'source_project_id', 'target_project_id'))
                and item.get('source_branch') == selection.source_branch
                and item.get('target_branch') == selection.target_branch
                and item.get('sha') == selection.source_sha and item.get('description') == marker
                and type(item.get('author')) is dict
                and type(item['author'].get('id')) is int
                and item['author'].get('id') == session.expected_user_id
                and item.get('state') in ('opened', 'closed', 'merged')):
            matches.append({'iid': item['iid'], 'state': item['state']})
    branch = session.request('GET', project + '/repository/branches/' + quote(selection.target_branch, safe=''))
    commit = branch.get('commit') if type(branch) is dict and branch.get('name') == selection.target_branch else None
    target = commit.get('id') if type(commit) is dict else None
    if not isinstance(target, str) or not re.fullmatch('[a-f0-9]{40}', target):
        target = None
    return {'kind': 'gitlab-mr-reconciliation-observation', 'scope_sha256': _fingerprint(session, selection),
        'matching_mrs': matches, 'observed_candidate_count': len(items), 'target_observed_sha': target,
        'outcome': 'matching-state-observed' if len(matches) == 1 else 'unresolved',
        'absence_proves_nonexecution': False, 'write_authority': 'not-issued', 'retry_allowed': False}
