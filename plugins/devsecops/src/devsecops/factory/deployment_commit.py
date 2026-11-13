"""Current deployment proposal to a bounded native Git commit/pack, locally only."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time
from weakref import WeakKeyDictionary

from devsecops.adapters.artifacts import _open_directory
from devsecops.adapters.http import _json
from devsecops.adapters.signatures import _snapshot
from devsecops.adapters.vulnerability_policy import checkpoint, timestamp
from devsecops.factory.deployment_proposal import require_deployment_proposal
from devsecops.factory.mission_security import _read_retained
from devsecops.process import run
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import canonical_bytes, seal

MAX_TREE = 2 * 1024 * 1024
MAX_OBJECT_BYTES = 8 * 1024 * 1024
MAX_PACK = 9 * 1024 * 1024
MAX_DEPTH = 16
MAX_METADATA_ENTRIES = 20000
MAX_METADATA_DEPTH = 32
_PREPARED = WeakKeyDictionary()
_OID = re.compile(r'[a-f0-9]{40}')


class PreparedDeploymentCommit:
    """Local prepared objects only; cannot authorize a Git write or deployment."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('Prepared commits require a current direct deployment proposal')

    def __reduce__(self):
        raise TypeError('Prepared commit authority cannot be serialized')

    def describe(self):
        record = _PREPARED.get(self)
        if record is None:
            raise ValueError('Unissued prepared commit')
        return deepcopy(record['binding'])


def require_prepared_deployment_commit(value, *, run_scope, source_commit, deployment_commit,
                                      proposal_configuration_sha256, configuration_sha256):
    record = _PREPARED.get(value) if type(value) is PreparedDeploymentCommit else None
    if record is None or record['pid'] != os.getpid() or time.monotonic() >= record['expires_monotonic']:
        raise ValueError('Unissued, foreign-process or expired prepared commit')
    bound = record['binding']
    selected = dict(run_scope=run_scope, source_commit=source_commit, deployment_commit=deployment_commit,
        proposal_configuration_sha256=proposal_configuration_sha256, configuration_sha256=configuration_sha256)
    if any(bound[key] != item for key, item in selected.items()) or datetime.now(timezone.utc) >= timestamp(bound['expires_at']):
        raise ValueError('Prepared commit scope changed or expired')
    if 'policy_source' in record:
        from .policy_git_source import require_policy_source
        if require_policy_source(record['policy_source'], **record['policy_scope']) != record['policy_binding']:
            raise ValueError('Original policy source changed')
    return deepcopy(bound)


def commit_configuration(raw):
    selected = _json(raw)
    fields = {'schema_version', 'kind', 'deployment_repository', 'deployment_commit', 'values_path',
        'proposal_configuration_sha256', 'git_path', 'git_sha256', 'author_name', 'author_email'}
    if (set(selected) != fields or type(selected['schema_version']) is not int or selected['schema_version'] != 1
            or selected['kind'] != 'mission-local-deployment-commit-configuration'
            or not isinstance(selected['deployment_commit'], str) or not _OID.fullmatch(selected['deployment_commit'])):
        raise ValueError('Exact SHA-1 deployment commit configuration required')
    for name in ('git_path', 'deployment_repository'):
        value = selected[name]
        if not isinstance(value, str) or len(value) > 4096 or not Path(value).is_absolute() or '..' in Path(value).parts:
            raise ValueError('Explicit absolute repository and Git paths required')
    for name in ('git_sha256', 'proposal_configuration_sha256'):
        if not isinstance(selected[name], str) or not re.fullmatch(r'[a-f0-9]{64}', selected[name]):
            raise ValueError('Explicit independent configuration and tool pins required')
    path = selected['values_path']
    if (not isinstance(path, str) or not 1 <= len(path) <= 300 or len(path.split('/')) > MAX_DEPTH
            or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', part) or part in ('.', '..') for part in path.split('/'))):
        raise ValueError('Bounded normalized values path required')
    author, email = selected['author_name'], selected['author_email']
    if (not isinstance(author, str) or not 1 <= len(author) <= 128 or author.strip() != author
            or any(ord(char) < 32 or ord(char) > 126 or char in '<>' for char in author)
            or not isinstance(email, str) or len(email) > 254
            or not re.fullmatch(r'[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+', email)):
        raise ValueError('Bounded explicit operator commit identity required')
    if Path(selected['git_path']).is_relative_to(Path(selected['deployment_repository'])):
        raise ValueError('Approved Git must be outside the candidate repository')
    return selected


def _oid(raw):
    text = raw.decode('ascii').strip()
    if not _OID.fullmatch(text):
        raise ValueError('Native Git did not return one exact SHA-1 object ID')
    return text


def _object_id(kind, raw):
    return hashlib.sha1(kind.encode() + b' ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def _entries(raw, deadline):
    if not raw or not raw.endswith(b'\0') or len(raw) > MAX_TREE:
        raise ValueError('Incomplete or oversized native Git tree listing')
    entries, seen = [], set()
    for entry in raw[:-1].split(b'\0'):
        checkpoint(deadline)
        metadata, separator, name = entry.partition(b'\t')
        parts = metadata.split(b' ')
        if len(entries) >= 20000 or not separator or len(parts) != 3 or not name or b'/' in name or name in (b'.', b'..') or name in seen:
            raise ValueError('Invalid native Git tree inventory')
        mode, kind, oid = parts
        if ({b'040000': b'tree', b'100644': b'blob', b'100755': b'blob', b'120000': b'blob', b'160000': b'commit'}.get(mode) != kind
                or not re.fullmatch(b'[a-f0-9]{40}', oid)):
            raise ValueError('Unsupported native Git tree entry')
        entries.append((mode, kind, oid, name))
        seen.add(name)
    return entries


def _tree_input(entries):
    return b''.join(b' '.join((mode, kind, oid)) + b'\t' + name + b'\0' for mode, kind, oid, name in entries)


def _output_outside_repository(output, repository, deadline):
    """Reject existing source ancestors by identity before creating any output."""
    repository_fd = _open_directory(Path(repository))
    try:
        source = os.fstat(repository_fd)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current = os.open(output.anchor, flags)
        try:
            for component in (*output.parts[1:], None):
                checkpoint(deadline)
                ancestor = os.fstat(current)
                if (source.st_dev, source.st_ino) == (ancestor.st_dev, ancestor.st_ino):
                    raise ValueError('Output must be outside the deployment repository')
                if component is None:
                    break
                try:
                    child = os.open(component, flags, dir_fd=current)
                except FileNotFoundError:
                    break
                os.close(current)
                current = child
        finally:
            os.close(current)
        return (source.st_dev, source.st_ino)
    finally:
        os.close(repository_fd)

def _forbid_metadata_redirect(directory, parts):
    current = os.dup(directory)
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            except FileNotFoundError:
                return
            os.close(current)
            current = child
        try:
            os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise ValueError('Shared or external Git metadata is unsupported')
    finally:
        os.close(current)


def _require_ordinary_repository(repository, deadline, expected=None, source_identity=None):
    try:
        source = _open_directory(repository)
        try:
            source_stat = os.fstat(source)
            selected_source = (source_stat.st_dev, source_stat.st_ino)
            if source_identity is not None and selected_source != source_identity:
                raise ValueError('Deployment repository identity changed')
            directory = os.open('.git', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source)
        finally:
            os.close(source)
    except OSError:
        raise ValueError('Deployment repository requires an internal ordinary .git directory') from None
    try:
        metadata = os.fstat(directory)
        identity = (*selected_source, metadata.st_dev, metadata.st_ino)
        if expected is not None and identity != expected:
            raise ValueError('Deployment Git metadata identity changed')
        for parts in (('commondir',), ('objects', 'info', 'alternates'), ('objects', 'info', 'http-alternates')):
            try:
                _forbid_metadata_redirect(directory, parts)
            except OSError:
                raise ValueError('Symlinked or special Git metadata is unsupported') from None
        count = 0
        def visit(parent, parts=()):
            nonlocal count
            if len(parts) > MAX_METADATA_DEPTH:
                raise ValueError('Git metadata inventory exceeds its bound')
            with os.scandir(parent) as entries:
                for entry in entries:
                    checkpoint(deadline)
                    count += 1
                    if count > MAX_METADATA_ENTRIES:
                        raise ValueError('Git metadata inventory exceeds its bound')
                    path = (*parts, entry.name)
                    mode = entry.stat(follow_symlinks=False).st_mode
                    if stat.S_ISDIR(mode):
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        try:
                            visit(child, path)
                        finally:
                            os.close(child)
                    elif not stat.S_ISREG(mode):
                        raise ValueError('Symlinked or special Git metadata is unsupported')
        visit(directory)
        return identity
    finally:
        os.close(directory)


@contextmanager
def _private_scratch(output, directory):
    _verify_output_path(output, directory)
    os.mkdir('scratch', 0o700, dir_fd=directory)
    try:
        yield output / 'scratch'
    finally:
        try:
            shutil.rmtree('scratch', dir_fd=directory)
        except BaseException:
            try:
                os.unlink('deployment-commit.json', dir_fd=directory)
            except FileNotFoundError:
                pass
            os.fsync(directory)
            raise


def prepare_deployment_commit(*, proposal, run_scope, source_commit, origin, repository, manifest_digest,
                              deployment_repository, deployment_commit, proposal_configuration_sha256,
                              config, expected_config_sha256, output, timeout=120):
    """Use fixed native Git plumbing; never contacts or changes a remote."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 120
            or not isinstance(expected_config_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_config_sha256)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Pinned configuration, new absolute output and bounded deadline required')
    deadline = time.monotonic() + timeout
    scope = deepcopy(dict(run_scope=run_scope, source_commit=source_commit, origin=origin, repository=repository,
        manifest_digest=manifest_digest, deployment_repository=deployment_repository, deployment_commit=deployment_commit,
        configuration_sha256=proposal_configuration_sha256))
    def admission():
        checkpoint(deadline)
        from .policy_git_source import PolicyGitSource, require_policy_source
        if type(proposal) is PolicyGitSource:
            return require_policy_source(proposal, **scope)
        return require_deployment_proposal(proposal, **scope)
    source = admission()
    source_identity = _output_outside_repository(output.resolve(), deployment_repository, deadline)
    git_identity = _require_ordinary_repository(Path(deployment_repository), deadline, source_identity=source_identity)
    report = {'schema_version': 1, 'kind': 'mission-local-deployment-commit', 'decision': 'unknown',
        'scope': 'local-prepared-commit-only', 'preparation_authority': 'not-issued', 'factory_decision': 'unknown',
        'run_scope': run_scope, 'source_commit': source_commit, 'configuration_sha256': expected_config_sha256,
        'checks': {'proposal': 'pass', 'configuration': 'unknown', 'git': 'not-started', 'objects': 'not-started',
                   'single_file': 'not-started', 'pack': 'not-started'},
        'downstream': dict.fromkeys(('git_write', 'merge_request', 'merge', 'ci_execution', 'deployment', 'promotion'), 'not-performed'),
        'started_at': datetime.now(timezone.utc).isoformat(), 'gaps': [], 'commands': []}
    binding = None
    with _new_directory(output) as directory, _private_scratch(output, directory) as scratch:
        retained = {}
        proposal_path = Path(source['patch']['path']).parent
        proposal_fd = _open_directory(proposal_path)
        try:
            scratch_fd = _open_directory(scratch)
        except BaseException:
            os.close(proposal_fd)
            raise
        repo_fd, tool = None, None
        try:
            proposal_refs = {'proposal.patch': source['patch'], 'values-before.yaml': source['before'],
                'values-after.yaml': source['after'], 'rendered-before.json': source['rendered_before'],
                'rendered-after.json': source['rendered_after']}
            def audit():
                admission()
                _require_ordinary_repository(Path(deployment_repository), deadline, git_identity)
                _verify_output_path(output, directory)
                _verify_output_path(proposal_path, proposal_fd)
                for name, ref in proposal_refs.items():
                    _read_retained(proposal_fd, name, ref, deadline)
                for name, ref in retained.items():
                    _read_retained(directory, name, ref, deadline)
                if repo_fd is not None:
                    _verify_output_path(Path(deployment_repository), repo_fd)
                _verify_output_path(scratch, scratch_fd)
                if tool is not None:
                    _read_retained(scratch_fd, 'git', tool, deadline)
                checkpoint(deadline)
            def freeze(path, name, maximum, expected=None):
                _verify_output_path(output, directory)
                ref = _snapshot(path, output/name, maximum, deadline=deadline)
                if expected is not None and ref['sha256'] != expected:
                    raise ValueError('Selected local preparation input changed')
                retained[name] = ref
                return ref
            try:
                audit()
                freeze(config, 'commit-config.json', 64000, expected_config_sha256)
                selected = commit_configuration(_read_retained(directory, 'commit-config.json', retained['commit-config.json'], deadline, materialize=True))
                if any(selected[name] != expected for name, expected in (
                    ('deployment_repository', deployment_repository), ('deployment_commit', deployment_commit),
                    ('proposal_configuration_sha256', proposal_configuration_sha256), ('values_path', source['values_path']))):
                    raise ValueError('Local preparation configuration differs from proposal')
                report['checks']['configuration'] = 'pass'
                before = _read_retained(proposal_fd, 'values-before.yaml', source['before'], deadline, materialize=True)
                after = _read_retained(proposal_fd, 'values-after.yaml', source['after'], deadline, materialize=True)
                if not 1 <= len(before) <= 64000 or not 1 <= len(after) <= 64000 or source['change_required'] != (before != after):
                    raise ValueError('Proposal before/after or change status differs')
                report['change_required'] = source['change_required']
                empty_reset = source.get('source_kind') == 'qualified-policy-change' and source.get('allow_empty_reset') is True
                if not source['change_required'] and not empty_reset:
                    report['gaps'].append('unchanged-values-no-commit-required')
                else:
                    repo = Path(deployment_repository)
                    repo_fd = _open_directory(repo)
                    binary = scratch/'git'
                    tool = _snapshot(Path(selected['git_path']), binary, 200_000_000, deadline=deadline)
                    if tool['sha256'] != selected['git_sha256']:
                        raise ValueError('Approved Git binary pin differs')
                    binary.chmod(0o500)
                    env = {'GIT_NO_LAZY_FETCH': '1', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_CONFIG_NOSYSTEM': '1',
                        'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1'}
                    fixed = ['--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                        '-c', 'core.pager=cat', '-c', 'core.attributesFile=/dev/null', '-c', 'protocol.allow=never',
                        '-c', 'protocol.http.allow=never', '-c', 'protocol.https.allow=never', '-c', 'protocol.ssh.allow=never',
                        '-c', 'protocol.git.allow=never', '-c', 'protocol.file.allow=never', '-c', 'protocol.ext.allow=never',
                        '-c', 'credential.helper=', '-c', 'submodule.recurse=false', '-c', 'commit.gpgSign=false', '-c', 'gc.auto=0']
                    def native(*args, cwd=None, limit=MAX_TREE, stdin_bytes=None, identity=None):
                        checkpoint(deadline)
                        _require_ordinary_repository(repo, deadline, git_identity)
                        _read_retained(scratch_fd, 'git', tool, deadline)
                        _verify_output_path(scratch, scratch_fd)
                        _verify_output_path(repo, repo_fd)
                        checkpoint(deadline)
                        code, out, _, receipt = run([str(binary), *fixed, *args], cwd=cwd or scratch,
                            timeout=min(30, deadline-time.monotonic()), limit=limit,
                            env={**env, **(identity or {})}, stdin_bytes=stdin_bytes, temp_directory=scratch)
                        report['commands'].append({'operation': args[0], **receipt})
                        checkpoint(deadline)
                        _read_retained(scratch_fd, 'git', tool, deadline)
                        if code:
                            raise ValueError('Bounded native Git preparation failed')
                        return out
                    version = native('--version', limit=8192).decode('ascii').strip()
                    if not re.fullmatch(r'git version [A-Za-z0-9_.+-]{1,128}', version):
                        raise ValueError('Approved Git version unavailable')
                    if (native('rev-parse', '--show-toplevel', cwd=repo, limit=8192).decode().strip() != str(repo)
                            or native('rev-parse', '--show-object-format', cwd=repo, limit=8192) != b'sha1\n'):
                        raise ValueError('Selected Git repository or object format differs')
                    report['git'], report['checks']['git'] = {'sha256': tool['sha256'], 'version': version}, 'pass'
                    private = scratch/'objects.git'
                    native('init', '--bare', '--quiet', '--template=', '--object-format=sha1', str(private), limit=8192)
                    objects, new_ids = {}, set()
                    def remember(kind, raw, oid, *, new=False):
                        checkpoint(deadline)
                        if _object_id(kind, raw) != oid:
                            raise ValueError('Native object bytes differ from the selected Git ID')
                        row = {'kind': kind, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                        if oid in objects and objects[oid] != row:
                            raise ValueError('Git object identity collision')
                        objects[oid] = row
                        if len(objects) > 2*MAX_DEPTH+4 or sum(item['bytes'] for item in objects.values()) > MAX_OBJECT_BYTES:
                            raise ValueError('Local object inventory exceeds its bound')
                        if new:
                            new_ids.add(oid)
                    def copy_object(kind, oid, maximum):
                        raw = native('cat-file', kind, oid, cwd=repo, limit=maximum)
                        remember(kind, raw, oid)
                        if _oid(native('hash-object', '-w', '-t', kind, '--stdin', cwd=private, limit=8192, stdin_bytes=raw)) != oid:
                            raise ValueError('Copied native object identity differs')
                        return raw
                    base = copy_object('commit', deployment_commit, 64000)
                    first = base.split(b'\n', 1)[0]
                    if not re.fullmatch(b'tree [a-f0-9]{40}', first):
                        raise ValueError('Base commit has no exact root tree')
                    tree_id, levels = first[5:].decode(), []
                    parts = selected['values_path'].split('/')
                    for index, part in enumerate(parts):
                        copy_object('tree', tree_id, MAX_TREE)
                        entries = _entries(native('ls-tree', '-z', tree_id, cwd=repo), deadline)
                        if _oid(native('mktree', '-z', '--missing', cwd=private, limit=8192, stdin_bytes=_tree_input(entries))) != tree_id:
                            raise ValueError('Native tree reconstruction changed the baseline')
                        chosen = [item for item in entries if item[3] == part.encode()]
                        if len(chosen) != 1:
                            raise ValueError('Selected values path is missing or ambiguous')
                        mode, kind, oid, _ = chosen[0]
                        if index == len(parts)-1:
                            if mode not in (b'100644', b'100755') or kind != b'blob':
                                raise ValueError('Selected values must remain a regular Git blob')
                            old_blob, file_mode = oid.decode(), mode
                        elif mode != b'040000' or kind != b'tree':
                            raise ValueError('Selected path contains a symlink or non-tree ancestor')
                        levels.append((part.encode(), entries))
                        tree_id = oid.decode()
                    if copy_object('blob', old_blob, 64000) != before:
                        raise ValueError('Proposal before-values differ from the base Git object')
                    new_blob = _oid(native('hash-object', '-w', '--stdin', cwd=private, limit=8192, stdin_bytes=after))
                    remember('blob', after, new_blob, new=True)
                    child = new_blob
                    for part, entries in reversed(levels):
                        updated = [(mode, kind, child.encode() if name == part else oid, name) for mode, kind, oid, name in entries]
                        child = _oid(native('mktree', '-z', '--missing', cwd=private, limit=8192, stdin_bytes=_tree_input(updated)))
                        if _entries(native('ls-tree', '-z', child, cwd=private), deadline) != updated:
                            raise ValueError('Prepared tree changes a sibling or file mode')
                        remember('tree', native('cat-file', 'tree', child, cwd=private), child, new=True)
                    root_tree = child
                    epoch = str(int(datetime.now(timezone.utc).timestamp())) + ' +0000'
                    identity = {'GIT_AUTHOR_NAME': selected['author_name'], 'GIT_AUTHOR_EMAIL': selected['author_email'],
                        'GIT_COMMITTER_NAME': selected['author_name'], 'GIT_COMMITTER_EMAIL': selected['author_email'],
                        'GIT_AUTHOR_DATE': epoch, 'GIT_COMMITTER_DATE': epoch}
                    heading = 'Prepare qualified policy source' if source.get('source_kind') == 'qualified-policy-change' else 'Prepare verified mission image'
                    message = (heading+'\n\nSource: ' + source_commit + '\nImage: ' + manifest_digest
                        + '\nProposal-patch-sha256: ' + source['patch']['sha256'] + '\nPublished-evidence-sha256: '
                        + hashlib.sha256(canonical_bytes(source['published_evidence'])).hexdigest() + '\n').encode()
                    commit = _oid(native('commit-tree', root_tree, '-p', deployment_commit, cwd=private, limit=8192,
                        stdin_bytes=message, identity=identity))
                    commit_raw = native('cat-file', 'commit', commit, cwd=private, limit=64000)
                    remember('commit', commit_raw, commit, new=True)
                    headers = commit_raw.split(b'\n\n', 1)[0].split(b'\n')
                    if headers[:2] != [b'tree ' + root_tree.encode(), b'parent ' + deployment_commit.encode()] or len([row for row in headers if row.startswith(b'parent ')]) != 1:
                        raise ValueError('Prepared commit tree or parent differs')
                    diff = native('diff-tree', '--no-commit-id', '--raw', '-z', '-r', '--no-renames', deployment_commit, commit, cwd=private)
                    expected = b':' + file_mode + b' ' + file_mode + b' ' + old_blob.encode() + b' ' + new_blob.encode() + b' M\0' + selected['values_path'].encode() + b'\0'
                    if empty_reset:
                        expected = b''
                    if diff != expected or native('cat-file', 'blob', new_blob, cwd=private, limit=64000) != after:
                        raise ValueError('Native commit diff exceeds the exact approved file change')
                    report['checks']['objects'], report['checks']['single_file'] = 'pass', 'pass'
                    if len(new_ids) > MAX_DEPTH+2:
                        raise ValueError('Prepared pack object count exceeds its bound')
                    pack = native('pack-objects', '--stdout', '--window=0', '--depth=0', '--threads=1', '--no-reuse-delta', '--no-reuse-object',
                        cwd=private, limit=MAX_PACK, stdin_bytes=('\n'.join(sorted(new_ids))+'\n').encode())
                    pack_path, index_path = scratch/'objects.pack', scratch/'objects.idx'
                    pack_path.write_bytes(pack)
                    pack_path.chmod(0o600)
                    pack_id = _oid(native('index-pack', '-o', str(index_path), str(pack_path), cwd=private, limit=8192))
                    index_pin = _snapshot(index_path, scratch/'index-pin', 64000, deadline=deadline)
                    verified = native('verify-pack', '-v', str(index_path), cwd=private, limit=64000).decode('ascii').splitlines()
                    observed = {}
                    for row in verified:
                        checkpoint(deadline)
                        fields = row.split()
                        if fields and _OID.fullmatch(fields[0]):
                            if len(fields) != 5 or fields[0] in observed or fields[1] not in ('tree', 'commit', 'blob') or not fields[2].isdigit():
                                raise ValueError('Native pack verification is incomplete or contains deltas')
                            observed[fields[0]] = (fields[1], int(fields[2]))
                    if observed != {oid: (objects[oid]['kind'], objects[oid]['bytes']) for oid in new_ids}:
                        raise ValueError('Pack inventory differs from prepared objects')
                    freeze(pack_path, 'objects.pack', MAX_PACK, hashlib.sha256(pack).hexdigest())
                    freeze(index_path, 'objects.idx', 64000, index_pin['sha256'])
                    retained['commit.txt'] = _write(directory, 'commit.txt', commit_raw.decode('utf-8'))
                    report['checks']['pack'] = 'pass'
                    report['prepared'] = {'commit': commit, 'parent': deployment_commit, 'tree': root_tree,
                        'values_path': selected['values_path'], 'mode': file_mode.decode(), 'before_blob': old_blob,
                        'after_blob': new_blob, 'pack_git_checksum': pack_id, 'objects': {oid: objects[oid] for oid in sorted(new_ids)},
                        'author': {'name': selected['author_name'], 'email': selected['author_email'], 'date': epoch},
                        'object_format': 'sha1', 'source_objects': {oid: row for oid, row in objects.items() if oid not in new_ids}}
                    if source.get('source_kind') == 'qualified-policy-change':
                        report['prepared']['content_already_baseline'] = empty_reset
                    audit()
                    if source['published_evidence']['signer']['authority'] == 'controlled-test':
                        report['gaps'].append('controlled-proposal-cannot-issue-prepared-commit-authority')
                    else:
                        binding = {**{key: source[key] for key in ('run_scope', 'source_commit', 'origin', 'repository',
                            'manifest_digest', 'deployment_repository', 'deployment_commit', 'values_path', 'before', 'after',
                            'published_evidence', 'expires_at')}, 'proposal_configuration_sha256': proposal_configuration_sha256,
                            'configuration_sha256': expected_config_sha256, 'patch_sha256': source['patch']['sha256'],
                            'scope': 'local-prepared-commit-only', 'prepared': deepcopy(report['prepared']), 'git': report['git'],
                            'output_path': str(output), 'artifacts': deepcopy(retained)}
                        if source.get('source_kind') == 'qualified-policy-change':
                            binding.update(source_kind=source['source_kind'], source_provenance=deepcopy(source['source_provenance']))
                        report['decision'], report['preparation_authority'], report['binding'] = 'pass', 'issued', deepcopy(binding)
            except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
                binding = None
                report['gaps'].append('local-commit-preparation-incomplete-or-changed')
            report['artifacts'], report['finished_at'] = deepcopy(retained), datetime.now(timezone.utc).isoformat()
            report = seal(report, 'preparation_sha256')
            _verify_output_path(output, directory)
            try:
                _write(directory, 'deployment-commit.json', report)
                os.fsync(directory)
                _verify_output_path(output, directory)
                audit()
            except BaseException:
                try:
                    os.unlink('deployment-commit.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            if binding is None:
                return report, None
            capability = object.__new__(PreparedDeploymentCommit)
            _PREPARED[capability] = {'pid': os.getpid(), 'binding': deepcopy(binding),
                'expires_monotonic': time.monotonic() + (timestamp(binding['expires_at'])-datetime.now(timezone.utc)).total_seconds()}
            if source.get('source_kind') == 'qualified-policy-change':
                _PREPARED[capability].update(policy_source=proposal,policy_scope=deepcopy(scope),policy_binding=deepcopy(source))
            return report, capability
        finally:
            os.close(proposal_fd)
            os.close(scratch_fd)
            if repo_fd is not None:
                os.close(repo_fd)
