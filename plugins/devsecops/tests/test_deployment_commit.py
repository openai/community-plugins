"""Actual native Git preparation from controlled process-local proposal records."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from devsecops.factory import deployment_commit as prepared
from devsecops.factory import deployment_proposal as proposal
from devsecops.runtime.context import verify_seal


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git(repo, *args, input=None):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgSign=false',
        '-c', 'user.name=Controlled Test', '-c', 'user.email=test@example.invalid', *args], cwd=repo,
        env={'PATH': os.environ['PATH'], 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}, input=input, timeout=10)


@pytest.fixture
def case(tmp_path):
    root = tmp_path.resolve()
    repo = root/'deployment'
    repo.mkdir()
    values = repo/'environments/lab.yaml'
    values.parent.mkdir()
    before = b'# Preserve comments and bytes\r\nimage:\r\n  repository: old.example/mission\r\n  digest: sha256:' + b'b'*64 + b'\r\n'
    after = before.replace(b'old.example/mission', b'new.example/mission').replace(b'b'*64, b'a'*64)
    values.write_bytes(before)
    (repo/'unchanged.txt').write_bytes(b'Unchanged sibling\n')
    executable = repo/'environments/run.sh'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o700)
    (repo/'environments/link').symlink_to('run.sh')
    git(repo, 'init', '--quiet', '--template=')
    git(repo, 'add', '--all')
    git(repo, 'commit', '--quiet', '-m', 'Controlled baseline')
    base = git(repo, 'rev-parse', 'HEAD').decode().strip()
    evidence = root/'proposal'
    evidence.mkdir(mode=0o700)
    refs = {}
    for name, raw in {'values-before.yaml': before, 'values-after.yaml': after,
        'proposal.patch': proposal._patch(before, after, 'environments/lab.yaml'),
        'rendered-before.json': b'{"controlled": "before"}', 'rendered-after.json': b'{"controlled": "after"}'}.items():
        (evidence/name).write_bytes(raw)
        (evidence/name).chmod(0o600)
        refs[name] = {'sha256': sha(raw), 'bytes': len(raw)}
    binding = {'run_scope': 'controlled-local-commit', 'source_commit': 'c'*40, 'origin': 'https://new.example',
        'repository': 'mission', 'manifest_digest': 'sha256:'+'a'*64, 'deployment_repository': str(repo),
        'deployment_commit': base, 'configuration_sha256': 'd'*64, 'values_path': 'environments/lab.yaml',
        'patch': {'path': str(evidence/'proposal.patch'), **refs['proposal.patch']},
        'before': refs['values-before.yaml'], 'after': refs['values-after.yaml'],
        'rendered_before': refs['rendered-before.json'], 'rendered_after': refs['rendered-after.json'],
        'published_evidence': {'signer': {'authority': 'controlled-test'}, 'artifact_manifest': {'digest': 'sha256:'+'e'*64}},
        'change_required': True, 'expires_at': (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()}
    authority = object.__new__(proposal.DeploymentProposal)
    proposal._PROPOSALS[authority] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic()+300, 'binding': binding}
    binary = Path(shutil.which('git')).resolve()
    config = {'schema_version': 1, 'kind': 'mission-local-deployment-commit-configuration',
        'deployment_repository': str(repo), 'deployment_commit': base, 'values_path': binding['values_path'],
        'proposal_configuration_sha256': binding['configuration_sha256'], 'git_path': str(binary),
        'git_sha256': sha(binary.read_bytes()), 'author_name': 'Controlled Operator', 'author_email': 'test@example.invalid'}
    config_path = root/'commit-config.json'
    config_path.write_text(json.dumps(config))
    args = {'proposal': authority, **{name: binding[name] for name in ('run_scope', 'source_commit', 'origin', 'repository',
        'manifest_digest', 'deployment_repository', 'deployment_commit')},
        'proposal_configuration_sha256': binding['configuration_sha256'], 'config': config_path,
        'expected_config_sha256': sha(config_path.read_bytes()), 'output': root/'prepared'}
    return {'args': args, 'binding': binding, 'config': config, 'repo': repo, 'values': values,
        'before': before, 'after': after, 'evidence': evidence}


def update(case):
    case['args']['config'].write_text(json.dumps(case['config']))
    case['args']['expected_config_sha256'] = sha(case['args']['config'].read_bytes())


@pytest.mark.parametrize('destination', ['working-tree', 'nested-parents', 'git-directory', 'working-tree-alias', 'git-directory-alias', 'case-alias'])
def test_output_inside_deployment_repository_is_rejected_before_writes(case, tmp_path, destination):
    if destination == 'case-alias':
        alias = case['repo'].with_name(case['repo'].name.swapcase())
        if not alias.exists() or not alias.samefile(case['repo']):
            pytest.skip('Case-sensitive filesystem has no case alias')
        output = alias / '.GIT/new-parent/prepared'
    elif destination.endswith('-alias'):
        target = case['repo'] / '.git' if destination == 'git-directory-alias' else case['repo']
        alias = tmp_path / 'source-alias'
        alias.symlink_to(target, target_is_directory=True)
        output = alias / 'prepared'
    else:
        relative = {'working-tree': 'prepared', 'nested-parents': 'new-parent/deeper/prepared',
                    'git-directory': '.git/new-parent/prepared'}[destination]
        output = case['repo'] / relative

    def inventory():
        return {str(path.relative_to(case['repo'])):
                ('symlink', os.readlink(path)) if path.is_symlink() else
                ('directory', path.stat().st_mode) if path.is_dir() else
                ('file', path.stat().st_mode, sha(path.read_bytes()))
                for path in case['repo'].rglob('*')}

    before = inventory()
    case['args']['output'] = output
    try:
        with pytest.raises(ValueError, match='outside the deployment repository'):
            prepared.prepare_deployment_commit(**case['args'])
    finally:
        assert inventory() == before
        assert not output.exists()


@pytest.mark.parametrize('layout', ['separate', 'linked', 'git-symlink', 'commondir', 'commondir-symlink',
    'objects-symlink', 'loose-directory-symlink', 'pack-symlink', 'alternates', 'http-alternates',
    'commondir-case-alias', 'alternates-case-alias'])
def test_external_metadata_is_rejected_before_any_output_or_scratch(case, tmp_path, monkeypatch, layout):
    metadata = case['repo'] / '.git'
    external = tmp_path / 'external-metadata'
    if layout.endswith('case-alias'):
        if not (case['repo'] / '.GIT').exists() or not (case['repo'] / '.GIT').samefile(metadata):
            pytest.skip('Case-sensitive filesystem has no case alias')
        if layout == 'commondir-case-alias':
            (metadata / 'COMMONDIR').write_text(str(external))
        else:
            (metadata / 'objects').rename(metadata / 'OBJECTS')
            (metadata / 'OBJECTS/info').rename(metadata / 'OBJECTS/INFO')
            (metadata / 'OBJECTS/INFO/ALTERNATES').write_text(str(external))
    elif layout in ('separate', 'git-symlink'):
        metadata.rename(external)
        if layout == 'separate':
            metadata.write_text(f'gitdir: {external}\n')
        else:
            metadata.symlink_to(external, target_is_directory=True)
    elif layout == 'linked':
        linked = tmp_path / 'linked'
        git(case['repo'], 'worktree', 'add', '--detach', str(linked), 'HEAD')
        case['args']['deployment_repository'] = str(linked)
        case['binding']['deployment_repository'] = str(linked)
        case['config']['deployment_repository'] = str(linked)
        update(case)
        external = metadata
    elif layout in ('commondir', 'commondir-symlink'):
        if layout == 'commondir':
            (metadata / 'commondir').write_text(str(external))
        else:
            (metadata / 'commondir').symlink_to(external)
    elif layout in ('objects-symlink', 'loose-directory-symlink'):
        selected = metadata / 'objects'
        if layout == 'loose-directory-symlink':
            selected = next(path for path in selected.iterdir() if len(path.name) == 2 and path.is_dir())
        selected.rename(external)
        selected.symlink_to(external, target_is_directory=True)
    elif layout == 'pack-symlink':
        external.mkdir()
        (external / 'canary').write_bytes(b'PRIVATE-SOURCE-CANARY')
        (metadata / 'objects/pack/pack-canary.pack').symlink_to(external / 'canary')
    else:
        (metadata / 'objects/info' / layout).write_text(str(external))
    case['args']['output'] = external / 'prepared'

    def inventory():
        return {str(path.relative_to(tmp_path)):
                ('symlink', os.readlink(path)) if path.is_symlink() else
                ('directory', path.stat().st_mode) if path.is_dir() else
                ('file', path.stat().st_mode, sha(path.read_bytes()))
                for path in tmp_path.rglob('*')}

    before = inventory()
    def forbidden(*args, **kwargs):
        pytest.fail('Unsupported Git layout reached output, scratch or process creation')
    monkeypatch.setattr(prepared, '_new_directory', forbidden)
    monkeypatch.setattr(prepared, '_private_scratch', forbidden)
    monkeypatch.setattr(prepared, 'run', forbidden)
    with pytest.raises(ValueError, match='Git metadata|ordinary .git'):
        prepared.prepare_deployment_commit(**case['args'])
    assert inventory() == before


@pytest.mark.parametrize('bound', ['MAX_METADATA_ENTRIES', 'MAX_METADATA_DEPTH'])
def test_metadata_inventory_bounds_reject_before_output(case, monkeypatch, bound):
    monkeypatch.setattr(prepared, bound, 0)
    with pytest.raises(ValueError, match='metadata inventory exceeds'):
        prepared.prepare_deployment_commit(**case['args'])
    assert not case['args']['output'].exists()


@pytest.mark.parametrize('temporary_root', ['working-tree', 'metadata'])
def test_all_temporary_writes_ignore_ambient_source_directory(case, monkeypatch, temporary_root):
    from devsecops import process

    ambient = case['repo'] if temporary_root == 'working-tree' else case['repo'] / '.git'
    actual = process.tempfile.TemporaryFile
    calls = []
    def private_file(*args, **kwargs):
        selected = kwargs.get('dir')
        assert selected == case['args']['output'] / 'scratch'
        assert selected.is_dir() and selected.stat().st_mode & 0o077 == 0
        assert not selected.is_relative_to(case['repo'])
        calls.append(selected)
        return actual(*args, **kwargs)
    monkeypatch.setattr(process.tempfile, 'tempdir', str(ambient))
    monkeypatch.setattr(process.tempfile, 'TemporaryFile', private_file)
    before = {str(path.relative_to(case['repo'])) for path in case['repo'].rglob('*')}
    report, capability = prepared.prepare_deployment_commit(**case['args'])
    assert set(report['checks'].values()) == {'pass'}, report
    assert capability is None and len(calls) > 3
    assert not (case['args']['output'] / 'scratch').exists()
    assert {str(path.relative_to(case['repo'])) for path in case['repo'].rglob('*')} == before


def test_scratch_cleanup_failure_withdraws_terminal_receipt(case, monkeypatch):
    def failed_cleanup(path, *, dir_fd):
        assert path == 'scratch' and isinstance(dir_fd, int)
        raise OSError('controlled cleanup failure')
    monkeypatch.setattr(prepared.shutil, 'rmtree', failed_cleanup)
    with pytest.raises(OSError, match='controlled cleanup failure'):
        prepared.prepare_deployment_commit(**case['args'])
    assert not (case['args']['output'] / 'deployment-commit.json').exists()


def test_scratch_symlink_replacement_preserves_external_target(case, tmp_path, monkeypatch):
    target = tmp_path / 'unrelated'
    target.mkdir()
    (target / 'canary').write_bytes(b'PRESERVE-EXTERNAL-TARGET')
    actual = prepared._write
    def replace(fd, name, value):
        result = actual(fd, name, value)
        if name == 'deployment-commit.json':
            scratch = case['args']['output'] / 'scratch'
            scratch.rename(scratch.with_name('moved-scratch'))
            scratch.symlink_to(target, target_is_directory=True)
        return result
    monkeypatch.setattr(prepared, '_write', replace)
    with pytest.raises((OSError, ValueError)):
        prepared.prepare_deployment_commit(**case['args'])
    assert (target / 'canary').read_bytes() == b'PRESERVE-EXTERNAL-TARGET'
    assert [path.name for path in target.iterdir()] == ['canary']
    assert not (case['args']['output'] / 'deployment-commit.json').exists()


@pytest.mark.parametrize('cancel', [False, True])
def test_failed_or_cancelled_tool_removes_private_scratch(case, monkeypatch, cancel):
    def failed_tool(*args, **kwargs):
        raise KeyboardInterrupt if cancel else ValueError('controlled tool failure')
    monkeypatch.setattr(prepared, 'run', failed_tool)
    if cancel:
        with pytest.raises(KeyboardInterrupt):
            prepared.prepare_deployment_commit(**case['args'])
        assert not (case['args']['output'] / 'deployment-commit.json').exists()
    else:
        report, capability = prepared.prepare_deployment_commit(**case['args'])
        assert report['decision'] == 'unknown' and capability is None
    assert not (case['args']['output'] / 'scratch').exists()


def test_actual_native_git_pack_has_exact_parent_one_change_and_preserved_siblings(case, tmp_path):
    def inventory():
        return {str(path.relative_to(case['repo'])): sha(path.read_bytes()) for path in case['repo'].rglob('*')
            if path.is_file() and not path.is_symlink()}
    before = inventory()
    report, cap = prepared.prepare_deployment_commit(**case['args'])
    assert cap is None and report['decision'] == 'unknown', report
    assert set(report['checks'].values()) == {'pass'}, report
    assert report['gaps'] == ['controlled-proposal-cannot-issue-prepared-commit-authority']
    assert report['scope'] == 'local-prepared-commit-only'
    assert set(report['downstream'].values()) == {'not-performed'}
    verify_seal(report, 'preparation_sha256')
    assert inventory() == before
    # Independently import only the resulting pack into an owned repository copy.
    receiver = tmp_path/'receiver'
    shutil.copytree(case['repo'], receiver, symlinks=True)
    git(receiver, 'index-pack', '--stdin', input=(case['args']['output']/'objects.pack').read_bytes())
    selected = report['prepared']
    commit = selected['commit']
    assert git(receiver, 'rev-parse', commit+'^').decode().strip() == case['binding']['deployment_commit']
    assert git(receiver, 'diff-tree', '--no-commit-id', '--name-only', '-r', commit) == b'environments/lab.yaml\n'
    assert git(receiver, 'show', commit+':environments/lab.yaml') == case['after']
    assert git(receiver, 'ls-tree', commit, 'environments/link') == git(receiver, 'ls-tree', commit+'^', 'environments/link')
    assert git(receiver, 'ls-tree', commit, 'environments/run.sh') == git(receiver, 'ls-tree', commit+'^', 'environments/run.sh')
    assert len(selected['objects']) == 4


@pytest.mark.parametrize('mutation', ['dict', 'expired', 'foreign', 'scope'])
def test_direct_authority_admission_precedes_io(case, mutation):
    if mutation == 'dict':
        case['args']['proposal'] = deepcopy(case['binding'])
    elif mutation == 'scope':
        case['args']['source_commit'] = '0'*40
    else:
        proposal._PROPOSALS[case['args']['proposal']]['expires_monotonic' if mutation == 'expired' else 'pid'] = 0
    with pytest.raises(ValueError):
        prepared.prepare_deployment_commit(**case['args'])
    assert not case['args']['output'].exists()


@pytest.mark.parametrize('mutation', ['pin', 'git-pin', 'base', 'values', 'proposal-pin', 'author', 'sha256', 'extra'])
def test_invalid_pins_and_configuration_stop_before_native_preparation(case, mutation, monkeypatch):
    if mutation == 'pin':
        case['args']['expected_config_sha256'] = '0'*64
    else:
        key, value = {'git-pin': ('git_sha256', '0'*64), 'base': ('deployment_commit', '0'*40),
            'values': ('values_path', '../outside'), 'proposal-pin': ('proposal_configuration_sha256', '0'*64),
            'author': ('author_name', 'Unsafe\nName'), 'sha256': ('deployment_commit', 'a'*64),
            'extra': ('remote', 'https://unselected.invalid')}[mutation]
        case['config'][key] = value
        update(case)
    monkeypatch.setattr(prepared, 'run', lambda *args, **kwargs: pytest.fail('Invalid preflight invoked Git'))
    report, cap = prepared.prepare_deployment_commit(**case['args'])
    assert cap is None and report['checks']['objects'] == 'not-started'
    assert 'local-commit-preparation-incomplete-or-changed' in report['gaps']


def test_every_native_invocation_disables_ambient_access_and_uses_private_stdin(case, monkeypatch):
    actual = prepared.run
    calls = []
    for name in ('GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'HTTP_PROXY', 'HTTPS_PROXY', 'GIT_SSH_COMMAND', 'GIT_ASKPASS'):
        monkeypatch.setenv(name, 'UNSELECTED-CREDENTIAL-CANARY')
    def checked(argv, **kwargs):
        assert 'protocol.allow=never' in argv and 'core.hooksPath=/dev/null' in argv
        assert kwargs['env']['GIT_NO_LAZY_FETCH'] == '1'
        assert 'UNSELECTED-CREDENTIAL-CANARY' not in kwargs['env'].values()
        assert argv[0] != case['config']['git_path']
        calls.append(argv)
        return actual(argv, **kwargs)
    monkeypatch.setattr(prepared, 'run', checked)
    report, cap = prepared.prepare_deployment_commit(**case['args'])
    assert cap is None and report['checks']['pack'] == 'pass'
    assert calls and all(not any(value in ('fetch', 'clone', 'push', 'send-pack') for value in argv) for argv in calls)
    assert any('stdin_sha256' in command for command in report['commands'])


@pytest.mark.parametrize('change', ['expiry', 'proposal-bytes', 'pack-bytes', 'deadline', 'route', 'metadata', 'repository'])
def test_terminal_audit_withdraws_receipt_without_claiming_delivery(case, monkeypatch, change):
    actual = prepared._write
    moved = case['args']['output'].with_name('moved-output')
    def changed(fd, name, value):
        ref = actual(fd, name, value)
        if name == 'deployment-commit.json':
            if change == 'expiry':
                proposal._PROPOSALS[case['args']['proposal']]['expires_monotonic'] = 0
            elif change == 'proposal-bytes':
                (case['evidence']/'values-after.yaml').write_text('changed')
            elif change == 'pack-bytes':
                (case['args']['output']/'objects.pack').chmod(0o600)
                (case['args']['output']/'objects.pack').write_bytes(b'changed')
            elif change == 'deadline':
                monkeypatch.setattr(prepared.time, 'monotonic', lambda: 10**20)
            elif change == 'metadata':
                metadata = case['repo'] / '.git'
                metadata.rename(case['repo'] / 'original-metadata')
                metadata.mkdir()
            elif change == 'repository':
                original = case['repo'].with_name('original-repository')
                case['repo'].rename(original)
                case['repo'].mkdir()
                (original / '.git').rename(case['repo'] / '.git')
            else:
                case['args']['output'].rename(moved)
                case['args']['output'].mkdir()
        return ref
    monkeypatch.setattr(prepared, '_write', changed)
    with pytest.raises(ValueError):
        prepared.prepare_deployment_commit(**case['args'])
    assert not (case['args']['output']/'deployment-commit.json').exists()
    assert not (moved/'deployment-commit.json').exists()


def test_mismatched_base_blob_never_builds_pack(case):
    raw = b'not the source blob'
    (case['evidence']/'values-before.yaml').write_bytes(raw)
    case['binding']['before'] = {'sha256': sha(raw), 'bytes': len(raw)}
    report, cap = prepared.prepare_deployment_commit(**case['args'])
    assert cap is None and report['checks']['pack'] == 'not-started'


def test_noop_does_not_create_commit_or_invoke_git(case, monkeypatch):
    (case['evidence']/'values-after.yaml').write_bytes(case['before'])
    case['binding']['after'] = deepcopy(case['binding']['before'])
    case['binding']['change_required'] = False
    monkeypatch.setattr(prepared, 'run', lambda *args, **kwargs: pytest.fail('No-op invoked Git'))
    report, cap = prepared.prepare_deployment_commit(**case['args'])
    assert cap is None and report['gaps'] == ['unchanged-values-no-commit-required']
    assert 'prepared' not in report and not (case['args']['output']/'objects.pack').exists()


def rebase_fixture(case):
    git(case['repo'], 'add', '--all')
    git(case['repo'], 'commit', '--quiet', '-m', 'Controlled alternate baseline')
    base = git(case['repo'], 'rev-parse', 'HEAD').decode().strip()
    case['binding']['deployment_commit'] = case['args']['deployment_commit'] = case['config']['deployment_commit'] = base
    update(case)


def test_executable_values_mode_and_non_ascii_sibling_names_are_preserved(case):
    case['values'].chmod(0o700)
    (case['repo']/'environments/odd\nname-\u2603').write_text('unchanged sibling')
    rebase_fixture(case)
    report, cap = prepared.prepare_deployment_commit(**case['args'])
    assert cap is None and report['checks']['pack'] == 'pass', report
    assert report['prepared']['mode'] == '100755'


def test_selected_symlink_cannot_be_replaced_with_regular_values(case):
    case['values'].unlink()
    case['values'].symlink_to('run.sh')
    rebase_fixture(case)
    report, cap = prepared.prepare_deployment_commit(**case['args'])
    assert cap is None and report['checks']['objects'] != 'pass'


@pytest.mark.parametrize('bound', ['tree', 'aggregate', 'depth'])
def test_local_object_budgets_fail_closed(case, monkeypatch, bound):
    if bound == 'tree':
        monkeypatch.setattr(prepared, 'MAX_TREE', 20)
    elif bound == 'aggregate':
        monkeypatch.setattr(prepared, 'MAX_OBJECT_BYTES', 20)
    else:
        case['config']['values_path'] = '/'.join(['nested']*17)
        update(case)
    report, cap = prepared.prepare_deployment_commit(**case['args'])
    assert cap is None and report['checks']['pack'] != 'pass'


@pytest.mark.parametrize('mutation', ['extra-pack-object', 'missing-parent', 'changed-mode'])
def test_independent_native_verification_rejects_wrong_generated_objects(case, monkeypatch, mutation):
    actual = prepared.run
    def changed(argv, **kwargs):
        if mutation == 'extra-pack-object' and 'pack-objects' in argv:
            kwargs['stdin_bytes'] += (case['binding']['deployment_commit']+'\n').encode()
        elif mutation == 'missing-parent' and 'commit-tree' in argv:
            argv = list(argv)
            position = argv.index('-p')
            del argv[position:position+2]
        elif mutation == 'changed-mode' and 'mktree' in argv and b'lab.yaml\0' in kwargs['stdin_bytes']:
            kwargs['stdin_bytes'] = kwargs['stdin_bytes'].replace(b'100644 blob ', b'100755 blob ')
        return actual(argv, **kwargs)
    monkeypatch.setattr(prepared, 'run', changed)
    report, cap = prepared.prepare_deployment_commit(**case['args'])
    assert cap is None and report['checks']['pack'] != 'pass'
    assert not (case['args']['output']/'objects.pack').exists()


def test_partial_clone_missing_object_cannot_invoke_configured_remote_helper(case, tmp_path):
    marker = tmp_path/'remote-invoked'
    helper = tmp_path/'helper.sh'
    helper.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 1\n')
    helper.chmod(0o700)
    git(case['repo'], 'config', 'remote.evidence.url', 'ext::'+str(helper))
    git(case['repo'], 'config', 'remote.evidence.promisor', 'true')
    git(case['repo'], 'config', 'extensions.partialClone', 'evidence')
    git(case['repo'], 'config', 'protocol.ext.allow', 'always')
    base = case['binding']['deployment_commit']
    (case['repo']/'.git/objects'/base[:2]/base[2:]).unlink()
    report, cap = prepared.prepare_deployment_commit(**case['args'])
    assert cap is None and report['checks']['objects'] != 'pass'
    assert not marker.exists()


@pytest.mark.parametrize('relative', ['new-output/nested', '.git/new-output/nested', ''])
def test_output_inside_source_rejected_before_directory_creation(case, relative):
    repo = case['repo']
    def inventory():
        return {str(path.relative_to(repo)): (path.lstat().st_mode,
            os.readlink(path) if path.is_symlink() else sha(path.read_bytes()) if path.is_file() else None)
            for path in repo.rglob('*')}
    before = inventory()
    case['args']['output'] = repo/relative
    with pytest.raises(ValueError, match='outside the deployment repository'):
        prepared.prepare_deployment_commit(**case['args'])
    assert inventory() == before
    if relative:
        assert not (repo/relative.split('/nested')[0]).exists()


def test_output_case_alias_inside_source_rejected_before_directory_creation(case):
    alias = case['repo'].with_name(case['repo'].name.upper())
    if not alias.exists() or not alias.samefile(case['repo']):
        pytest.skip('Filesystem does not expose a case alias')
    case['args']['output'] = alias/'new-output/nested'
    with pytest.raises(ValueError, match='outside the deployment repository'):
        prepared.prepare_deployment_commit(**case['args'])
    assert not (case['repo']/'new-output').exists()
