"""Local Git transport plans; private fixture authority never represents a live service."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import os
import pickle
import stat
import time

import pytest

from devsecops.factory import deployment_commit as commits
from devsecops.factory import git_transport as transport
from test_deployment_commit import case, git, update


__all__ = ['case']
SCOPE = ('run_scope', 'source_commit', 'deployment_commit',
         'proposal_configuration_sha256', 'configuration_sha256')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            value = ('symlink', os.readlink(path))
        elif stat.S_ISREG(mode):
            value = ('file', stat.S_IMODE(mode), sha(path.read_bytes()))
        else:
            value = ('directory', stat.S_IMODE(mode))
        result[str(path.relative_to(root))] = value
    return result


def select_new_base(case):
    base = git(case['repo'], 'rev-parse', 'HEAD').decode().strip()
    case['args']['deployment_commit'] = base
    case['binding']['deployment_commit'] = base
    case['config']['deployment_commit'] = base
    update(case)
    return base


def prepared_case(case, *, controlled=False):
    # Existing test fixture injects only process-local proposal authority. The
    # conspicuous non-live label below exercises ordinary issuer/consumer code;
    # it does not add a production override or a caller-selectable trust flag.
    if not controlled:
        case['binding']['published_evidence']['signer']['authority'] = 'test-fixture-only-not-live'
    report, capability = commits.prepare_deployment_commit(**case['args'])
    assert set(report['checks'].values()) == {'pass'}, report
    if controlled:
        assert capability is None and report['decision'] == 'unknown'
        source = case['binding']
        binding = {name: deepcopy(source[name]) for name in (
            'run_scope', 'source_commit', 'origin', 'repository', 'manifest_digest',
            'deployment_repository', 'deployment_commit', 'values_path', 'before', 'after',
            'published_evidence', 'expires_at')}
        binding.update(proposal_configuration_sha256=case['args']['proposal_configuration_sha256'],
            configuration_sha256=case['args']['expected_config_sha256'],
            patch_sha256=source['patch']['sha256'], scope='local-prepared-commit-only',
            prepared=deepcopy(report['prepared']), git=deepcopy(report['git']),
            output_path=str(case['args']['output']), artifacts=deepcopy(report['artifacts']))
        capability = object.__new__(commits.PreparedDeploymentCommit)
        commits._PREPARED[capability] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic()+300,
                                       'binding': binding}
    else:
        assert type(capability) is commits.PreparedDeploymentCommit
        binding = capability.describe()
    args = {'prepared': capability, **{name: binding[name] for name in SCOPE},
            'output': case['args']['output'].parent / 'transport'}
    return args, binding, report


def assert_local_only(report):
    assert report['checks']['local_complete'] is True, report
    assert report['transmitted_pack'] is None
    assert report['remote_possession'] == 'unverified'
    assert report['omission_authorized'] is False


def test_real_closure_and_prepared_pack_preserve_source_and_exact_change(case, tmp_path):
    # A prior commit is locally available but must not enter the selected closure.
    ancestor = case['binding']['deployment_commit']
    (case['repo']/'unchanged.txt').write_bytes(b'Current sibling with prior history\n')
    git(case['repo'], 'add', 'unchanged.txt')
    git(case['repo'], 'commit', '--quiet', '-m', 'Selected baseline')
    base = select_new_base(case)
    args, binding, _ = prepared_case(case)
    before = inventory(case['repo'])
    prepared_before = inventory(case['args']['output'])
    report, plan = transport.prepare_git_transport(**args)
    assert report['decision'] == 'pass' and type(plan) is transport.PreparedGitTransport
    assert_local_only(report)
    admitted = transport.require_prepared_git_transport(plan, **{key: args[key] for key in SCOPE})
    assert admitted['image_publication'] == {key: binding[key] for key in ('origin', 'repository', 'manifest_digest')}
    assert admitted['git_destination'] is None
    assert admitted['base_commit'] == base
    assert admitted['new_commit'] == binding['prepared']['commit'] != base
    assert ancestor in report['closure']['base_parents_not_imported']
    assert ancestor not in report['closure']['objects']
    expected = {base, git(case['repo'], 'rev-parse', base+'^{tree}').decode().strip()}
    for row in git(case['repo'], 'ls-tree', '-rz', '-t', base).split(b'\0'):
        if row:
            expected.add(row.split(b'\t', 1)[0].split()[2].decode())
    assert set(report['closure']['objects']) == expected
    assert inventory(case['repo']) == before
    assert inventory(case['args']['output']) == prepared_before
    receiver = tmp_path/'independent-receiver'
    receiver.mkdir()
    git(receiver, 'init', '--bare', '--quiet', '--template=', '--object-format=sha1')
    for name in ('base.pack', 'prepared.pack'):
        git(receiver, 'index-pack', '--stdin', input=(args['output']/name).read_bytes())
    present = set(git(receiver, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)').decode().splitlines())
    assert present == expected | set(binding['prepared']['objects'])
    new = binding['prepared']['commit']
    assert git(receiver, 'rev-parse', new+'^').decode().strip() == base
    assert git(receiver, 'diff-tree', '--no-commit-id', '--name-only', '-r', base, new) == b'environments/lab.yaml\n'
    assert git(receiver, 'show', new+':environments/lab.yaml') == case['after']
    for path in ('unchanged.txt', 'environments/link', 'environments/run.sh'):
        assert git(receiver, 'ls-tree', new, path) == git(receiver, 'ls-tree', base, path)


def test_controlled_preparation_cannot_issue_transport_plan(case):
    args, _, _ = prepared_case(case, controlled=True)
    report, plan = transport.prepare_git_transport(**args)
    assert_local_only(report)
    assert report['decision'] == 'unknown' and plan is None


@pytest.mark.parametrize('mutation', ['dictionary', 'unissued', 'pid', 'monotonic-expiry', 'wall-expiry', 'source', 'base', 'configuration'])
def test_direct_admission_rejects_before_output_or_native_call(case, monkeypatch, mutation):
    args, binding, _ = prepared_case(case)
    record = commits._PREPARED[args['prepared']]
    if mutation == 'dictionary':
        args['prepared'] = deepcopy(binding)
    elif mutation == 'unissued':
        args['prepared'] = object.__new__(commits.PreparedDeploymentCommit)
    elif mutation == 'pid':
        record['pid'] = -1
    elif mutation == 'monotonic-expiry':
        record['expires_monotonic'] = 0
    elif mutation == 'wall-expiry':
        record['binding']['expires_at'] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    else:
        key = {'source': 'source_commit', 'base': 'deployment_commit', 'configuration': 'configuration_sha256'}[mutation]
        args[key] = '0' * len(args[key])
    monkeypatch.setattr(transport, 'run', lambda *a, **k: pytest.fail('Invalid capability invoked native Git'))
    with pytest.raises(ValueError):
        transport.prepare_git_transport(**args)
    assert not args['output'].exists()


@pytest.mark.parametrize('target', ['source', 'source-git', 'prepared-evidence', 'source-alias', 'evidence-alias'])
def test_output_must_not_modify_source_or_prepared_evidence(case, tmp_path, target):
    args, _, _ = prepared_case(case)
    source_before, evidence_before = inventory(case['repo']), inventory(case['args']['output'])
    destination = {'source': case['repo'], 'source-git': case['repo']/'.git',
                   'prepared-evidence': case['args']['output']}.get(target)
    if destination is None:
        destination = tmp_path/'alias'
        destination.symlink_to(case['repo'] if target == 'source-alias' else case['args']['output'], target_is_directory=True)
    args['output'] = destination/'new-transport'
    with pytest.raises((OSError, ValueError)):
        transport.prepare_git_transport(**args)
    assert not args['output'].exists()
    assert inventory(case['repo']) == source_before
    assert inventory(case['args']['output']) == evidence_before


@pytest.mark.parametrize('name', ['objects.pack', 'objects.idx', 'commit.txt', 'commit-config.json'])
def test_changed_retained_preparation_is_rejected(case, name):
    args, _, _ = prepared_case(case)
    path = case['args']['output']/name
    path.chmod(0o600)
    path.write_bytes(path.read_bytes()+b'CHANGED')
    with pytest.raises((OSError, ValueError)):
        transport.prepare_git_transport(**args)
    assert not (args['output']/'git-transport.json').exists()


def test_missing_unmodified_sibling_object_rejects_complete_closure(case):
    args, _, _ = prepared_case(case)
    oid = git(case['repo'], 'rev-parse', 'HEAD:unchanged.txt').decode().strip()
    (case['repo']/'.git/objects'/oid[:2]/oid[2:]).unlink()
    with pytest.raises((OSError, ValueError)):
        transport.prepare_git_transport(**args)
    assert not (args['output']/'git-transport.json').exists()


@pytest.mark.parametrize('limit', ['MAX_CLOSURE_OBJECTS', 'MAX_CLOSURE_RAW', 'MAX_OBJECT_BYTES', 'MAX_DEPTH', 'MAX_TREE_ENTRIES'])
def test_closure_rejects_each_resource_bound(case, monkeypatch, limit):
    args, _, _ = prepared_case(case)
    monkeypatch.setattr(transport, limit, 1 if limit != 'MAX_DEPTH' else 0)
    with pytest.raises((OSError, ValueError)):
        transport.prepare_git_transport(**args)
    assert not (args['output']/'git-transport.json').exists()


def test_gitlink_is_rejected_without_traversal(case):
    base = case['binding']['deployment_commit']
    git(case['repo'], 'update-index', '--add', '--cacheinfo', '160000,'+base+',submodule')
    git(case['repo'], 'commit', '--quiet', '-m', 'Unsupported gitlink')
    select_new_base(case)
    args, _, _ = prepared_case(case)
    before = inventory(case['repo'])
    with pytest.raises((OSError, ValueError)):
        transport.prepare_git_transport(**args)
    assert inventory(case['repo']) == before
    assert not (args['output']/'git-transport.json').exists()


def test_reused_object_is_verified_and_never_authorizes_omission(case, tmp_path):
    (case['repo']/'already-present.yaml').write_bytes(case['after'])
    git(case['repo'], 'add', 'already-present.yaml')
    git(case['repo'], 'commit', '--quiet', '-m', 'Exact reused candidate blob')
    select_new_base(case)
    args, binding, _ = prepared_case(case)
    report, plan = transport.prepare_git_transport(**args)
    assert type(plan) is transport.PreparedGitTransport
    assert_local_only(report)
    reused = binding['prepared']['after_blob']
    assert reused in report['closure']['objects']
    assert report['closure']['objects'][reused] == binding['prepared']['objects'][reused]
    receiver = tmp_path/'prepared-only'
    receiver.mkdir()
    git(receiver, 'init', '--bare', '--quiet', '--template=')
    git(receiver, 'index-pack', '--stdin', input=(args['output']/'prepared.pack').read_bytes())
    actual = set(git(receiver, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)').decode().splitlines())
    assert actual == set(binding['prepared']['objects']) and reused in actual


def test_shared_object_record_mismatch_is_rejected(case):
    (case['repo']/'already-present.yaml').write_bytes(case['after'])
    git(case['repo'], 'add', 'already-present.yaml')
    git(case['repo'], 'commit', '--quiet', '-m', 'Reused candidate')
    select_new_base(case)
    args, binding, _ = prepared_case(case)
    commits._PREPARED[args['prepared']]['binding']['prepared']['objects'][binding['prepared']['after_blob']]['sha256'] = '0'*64
    with pytest.raises((OSError, ValueError)):
        transport.prepare_git_transport(**args)
    assert not (args['output']/'git-transport.json').exists()


def test_native_calls_have_no_ambient_access_and_use_private_scratch(case, monkeypatch):
    args, _, _ = prepared_case(case)
    for key in ('GIT_CONFIG_PARAMETERS', 'GIT_ASKPASS', 'GIT_SSH_COMMAND', 'HTTP_PROXY', 'HTTPS_PROXY'):
        monkeypatch.setenv(key, 'UNSELECTED-ACCESS-CANARY')
    actual = transport.run
    calls = []
    def checked(argv, **kwargs):
        assert 'protocol.allow=never' in argv and 'core.hooksPath=/dev/null' in argv
        assert kwargs['env']['GIT_NO_LAZY_FETCH'] == '1'
        assert 'UNSELECTED-ACCESS-CANARY' not in kwargs['env'].values()
        scratch = args['output']/'scratch'
        scratch_info = os.fstat(kwargs['scratch_fd'])
        expected_scratch = scratch.stat()
        assert stat.S_ISDIR(scratch_info.st_mode)
        assert (scratch_info.st_dev, scratch_info.st_ino) == (expected_scratch.st_dev, expected_scratch.st_ino)
        cwd_info = os.fstat(kwargs['cwd_fd'])
        assert stat.S_ISDIR(cwd_info.st_mode)
        allowed = [case['repo'], scratch, *(path for path in scratch.rglob('*') if path.is_dir() and not path.is_symlink())]
        assert (cwd_info.st_dev, cwd_info.st_ino) in {(path.stat().st_dev, path.stat().st_ino) for path in allowed}
        executable_info = os.fstat(kwargs['executable_fd'])
        expected_executable = (scratch/'git').stat()
        assert stat.S_ISREG(executable_info.st_mode)
        assert (executable_info.st_dev, executable_info.st_ino) == (expected_executable.st_dev, expected_executable.st_ino)
        assert kwargs['executable_sha256'] == case['config']['git_sha256']
        assert 'temp_directory' not in kwargs and 'cwd' not in kwargs
        assert not any(item in argv for item in ('clone', 'fetch', 'push', 'send-pack'))
        calls.append(argv)
        return actual(argv, **kwargs)
    monkeypatch.setattr(transport, 'run', checked)
    report, _ = transport.prepare_git_transport(**args)
    assert_local_only(report)
    assert calls


def test_capability_expiring_after_receipt_write_withdraws_terminal_claim(case, monkeypatch):
    args, _, _ = prepared_case(case)
    actual = transport._write
    def expire(directory, name, value):
        result = actual(directory, name, value)
        if name == 'git-transport.json':
            commits._PREPARED[args['prepared']]['expires_monotonic'] = 0
        return result
    monkeypatch.setattr(transport, '_write', expire)
    with pytest.raises(ValueError):
        transport.prepare_git_transport(**args)
    assert not (args['output']/'git-transport.json').exists()


def test_terminal_output_route_swap_preserves_external_directory(case, tmp_path, monkeypatch):
    args, _, _ = prepared_case(case)
    external = tmp_path/'external-do-not-write'
    external.mkdir()
    (external/'canary').write_bytes(b'PRESERVED-EXTERNAL-DIRECTORY')
    moved = tmp_path/'moved-original-output'
    actual = transport._write
    def replace(directory, name, value):
        result = actual(directory, name, value)
        if name == 'git-transport.json':
            args['output'].rename(moved)
            args['output'].symlink_to(external, target_is_directory=True)
        return result
    monkeypatch.setattr(transport, '_write', replace)
    with pytest.raises((OSError, ValueError)):
        transport.prepare_git_transport(**args)
    assert (external/'canary').read_bytes() == b'PRESERVED-EXTERNAL-DIRECTORY'
    assert sorted(p.name for p in external.iterdir()) == ['canary']
    assert not (moved/'git-transport.json').exists()


@pytest.mark.parametrize('target', ['output', 'scratch'])
def test_precopy_route_swap_keeps_snapshot_writes_on_retained_directory(case, tmp_path, monkeypatch, target):
    args, _, _ = prepared_case(case)
    external = tmp_path/'external-before-copy'
    external.mkdir()
    (external/'canary').write_bytes(b'PRESERVED-BEFORE-COPY')
    moved = tmp_path/'moved-before-copy'
    original = transport._snapshot_to
    swapped = False

    def replace(source, directory, name, limit, deadline):
        nonlocal swapped
        selected = 'commit-config.json' if target == 'output' else 'git'
        if name == selected and not swapped:
            route = args['output'] if target == 'output' else args['output']/'scratch'
            route.rename(moved)
            route.symlink_to(external, target_is_directory=True)
            swapped = True
        return original(source, directory, name, limit, deadline)

    monkeypatch.setattr(transport, '_snapshot_to', replace)
    with pytest.raises((OSError, ValueError)):
        transport.prepare_git_transport(**args)
    assert swapped, 'The race must run after admission and immediately before copying'
    assert (external/'canary').read_bytes() == b'PRESERVED-BEFORE-COPY'
    assert sorted(path.name for path in external.iterdir()) == ['canary']
    assert not (args['output']/'git-transport.json').exists()
    assert not (moved/'git-transport.json').exists()


def test_mutable_caller_scope_cannot_rewrite_issued_plan_binding(case):
    original_run_scope = {'run_id': 'controlled-dictionary-scope', 'attempt': {'id': 'first'}}
    case['binding']['run_scope'] = deepcopy(original_run_scope)
    case['args']['run_scope'] = deepcopy(original_run_scope)
    args, _, _ = prepared_case(case)
    original_scope = deepcopy({key: args[key] for key in SCOPE})
    report, plan = transport.prepare_git_transport(**args)
    assert_local_only(report)
    args['run_scope']['attempt']['id'] = 'caller-mutated-after-issuance'
    args['run_scope']['extra'] = 'caller-added-after-issuance'
    binding = transport.require_prepared_git_transport(plan, **original_scope)
    assert binding['run_scope'] == original_run_scope
    with pytest.raises(ValueError):
        transport.require_prepared_git_transport(plan, **{key: args[key] for key in SCOPE})


def test_plan_is_nonserializable_and_rechecks_source_and_retained_pack(case):
    args, _, _ = prepared_case(case)
    report, plan = transport.prepare_git_transport(**args)
    assert_local_only(report)
    with pytest.raises(TypeError):
        transport.PreparedGitTransport()
    with pytest.raises(TypeError):
        pickle.dumps(plan)
    scope = {key: args[key] for key in SCOPE}
    with pytest.raises(ValueError):
        transport.require_prepared_git_transport(deepcopy(report), **scope)
    path = args['output']/'prepared.pack'
    path.chmod(0o600)
    path.write_bytes(path.read_bytes()+b'SUBSTITUTED')
    with pytest.raises(ValueError):
        transport.require_prepared_git_transport(plan, **scope)


def test_plan_consumer_rejects_expired_upstream_authority(case):
    args, _, _ = prepared_case(case)
    _, plan = transport.prepare_git_transport(**args)
    commits._PREPARED[args['prepared']]['expires_monotonic'] = 0
    with pytest.raises(ValueError):
        transport.require_prepared_git_transport(plan, **{key: args[key] for key in SCOPE})
