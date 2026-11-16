"""Local Kustomize proof, dependency boundaries, and v1 contract compatibility."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import threading

from jsonschema import Draft202012Validator
import pytest
import yaml

from devsecops.cli import build_parser, main
from devsecops.contracts import REPORT_SCHEMA_V1, REPORT_SCHEMA_V2, validate_report
from devsecops.engine import review
from devsecops.profiles import PROFILE_SCHEMA_V1, PROFILE_SCHEMA_V2, load_profile
from devsecops import render

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT/'tests/fixtures/kustomize'
PINNED = Path.home()/'.cache/devsecops/kustomize-v5.8.3/bin/kustomize'
DEFAULT_CACHE = Path.home()/'.cache/devsecops/v0.1'
TARGET = {'name': 'mission', 'type': 'kustomize', 'path': 'deploy/overlays/demo',
          'source_root': 'deploy', 'namespace': 'demo', 'kubernetes_version': '1.34.0'}
CANARY = 'SYNTHETIC_KUSTOMIZE_SECRET_CANARY_721'


def git(repo, *args):
    environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                   'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                   'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null',
        '-c', 'core.fsmonitor=false', '-c', 'user.name=Synthetic Fixture',
        '-c', 'user.email=fixture@example.invalid', *args], cwd=repo, env=environment).decode().strip()


def profile(path, target=None, version=2):
    value = {'schema_version': version, 'id': 'kustomize-fixture', 'revision': '1',
             'policy': {'require_image_digest': True}, 'targets': [deepcopy(target or TARGET)]}
    path.write_text(json.dumps(value))
    return value


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path/'repository with spaces'
    shutil.copytree(FIXTURE, repo)
    git(repo, 'init', '-q')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'synthetic baseline')
    return repo


@pytest.fixture
def kustomize_bin():
    path = Path(os.environ.get('DEVSECOPS_KUSTOMIZE', PINNED))
    if not path.is_file():
        pytest.skip('Real Kustomize required; run scripts/bootstrap_kustomize.py')
    return path


@pytest.fixture
def tools_ready(kustomize_bin):
    tools = {'kustomize': kustomize_bin,
             'helm': Path(os.environ.get('DEVSECOPS_HELM', DEFAULT_CACHE/'bin/helm')),
             'kubeconform': Path(os.environ.get('DEVSECOPS_KUBECONFORM', DEFAULT_CACHE/'bin/kubeconform')),
             'schema-location': Path(os.environ.get('DEVSECOPS_SCHEMAS', DEFAULT_CACHE/'schemas'))}
    if not all(path.exists() for path in tools.values()):
        pytest.skip('Real Helm, Kustomize, kubeconform, and local schemas are required')
    return tools


def arguments(repo, path, output, tools, head='WORKTREE'):
    return build_parser().parse_args(['review', '--repo', str(repo), '--base', 'HEAD',
        '--head', head, '--profile', str(path), '--output', str(output),
        *[part for key, value in tools.items() for part in ('--'+key, str(value))]])


def set_config(repo, **fields):
    file = repo/TARGET['path']/'kustomization.yaml'
    config = yaml.safe_load(file.read_text())
    config.update(fields)
    file.write_text(yaml.safe_dump(config))


def collect(repo, tmp_path, binary):
    scratch = tmp_path/'render scratch'
    scratch.mkdir(exist_ok=True)
    return render.collect(repo, TARGET, None, scratch, kustomize=binary)


def test_real_local_bases_component_patch_and_receipts(repository, tmp_path, tools_ready):
    path = tmp_path/'profile.json'
    profile(path)
    before_index = (repository/'.git/index').read_bytes()
    report = review(arguments(repository, path, tmp_path/'output', tools_ready, 'HEAD'))
    validate_report(report)
    assert report['schema_version'] == 2
    assert report['decision'] == 'pass' and report['status'] == 'complete'
    target = report['targets'][0]
    assert target['inventory'] == {'base_count': 2, 'head_count': 2}
    assert not target['changes'] and not target['findings'] and not target['gaps']
    assert len([r for r in target['receipts'] if r['operation'] == 'kustomize-build']) == 4
    for side in ('base', 'head'):
        receipt = target['renders'][side]
        assert receipt['semantic_sha256'] == receipt['repeated_semantic_sha256']
        closure = receipt['input_closure']
        assert closure['kustomization_count'] == 3 and closure['file_count'] == 6
        assert 'deploy/base/deployment.yaml' in closure['files']
        assert 'deploy/overlays/demo/privilege.yaml' in closure['files']
    identity = report['toolchain']['kustomize']
    assert identity['version'] == 'v5.8.3'
    assert identity['sha256'] == hashlib.sha256(tools_ready['kustomize'].read_bytes()).hexdigest()
    assert (repository/'.git/index').read_bytes() == before_index
    assert 'controller parity' in ' '.join(report['limitations'])
    downgraded = deepcopy(report)
    downgraded['schema_version'] = 1
    assert not Draft202012Validator(REPORT_SCHEMA_V1).is_valid(downgraded)
    assert Draft202012Validator(REPORT_SCHEMA_V2).is_valid(report)


def test_real_candidate_privilege_change_and_atomic_cli_report(repository, tmp_path, tools_ready, capsys):
    path = tmp_path/'profile.json'
    profile(path)
    patch = repository/TARGET['path']/'privilege.yaml'
    patch.write_text(patch.read_text().replace('privileged: false', 'privileged: true'))
    before = {str(p.relative_to(repository)): p.read_bytes() for p in repository.rglob('*') if p.is_file()}
    output = tmp_path/'report'
    cli = ['review', '--repo', str(repository), '--base', 'HEAD', '--profile', str(path),
           '--output', str(output), *[part for key, value in tools_ready.items() for part in ('--'+key, str(value))]]
    assert main(cli) == 1
    report = json.loads((output/'report.json').read_text())
    findings = report['targets'][0]['findings']
    assert any(f['rule_id'] == 'K8S-PRIVILEGED' and f['change'] == 'introduced' and f['severity'] == 'high' for f in findings)
    assert any('rendered' in f['evidence']['source'] for f in findings)
    manifest = json.loads((output/'run-manifest.json').read_text())
    assert manifest['finished'] and manifest['decision'] == 'fail'
    assert all((output/name).is_file() for name in ('report.json', 'report.md', 'report.html'))
    assert before == {str(p.relative_to(repository)): p.read_bytes() for p in repository.rglob('*') if p.is_file()}
    assert json.loads(capsys.readouterr().out)['decision'] == 'fail'


def test_built_in_generators_local_files_and_literal_urls(repository, tmp_path, kustomize_bin):
    overlay = repository/TARGET['path']
    (overlay/'configuration.txt').write_text('setting=true\n')
    (overlay/'explicit.env').write_text('ENDPOINT=https://example.invalid/api\n')
    (overlay/'secret.txt').write_text(CANARY)
    set_config(repository,
        configMapGenerator=[{'name': 'settings', 'files': ['configuration.txt'], 'envs': ['explicit.env'],
                             'literals': ['url=https://example.invalid/data']}],
        secretGenerator=[{'name': 'credentials', 'files': ['password=secret.txt']}])
    result = collect(repository, tmp_path, kustomize_bin)
    assert len(result['records']) == 4
    assert not any(g.get('required', True) for g in result['gaps'])
    assert result['semantic_sha256'] == result['repeated_semantic_sha256']
    assert CANARY not in json.dumps({k: v for k, v in result.items() if k != 'records'})
    configmap = next(r['body'] for r in result['records'] if r['kind'] == 'ConfigMap')
    assert configmap['data']['ENDPOINT'] == 'https://example.invalid/api'


def test_secret_input_never_appears_in_exported_review(repository, tmp_path, tools_ready):
    overlay = repository/TARGET['path']
    (overlay/'secret.txt').write_text(CANARY)
    set_config(repository, secretGenerator=[{'name': 'credentials', 'files': ['password=secret.txt']}])
    git(repository, 'add', '.')
    git(repository, 'commit', '-qm', 'synthetic secret generator')
    path = tmp_path/'profile.json'
    profile(path)
    result = review(arguments(repository, path, tmp_path/'output', tools_ready))
    assert result['decision'] == 'pass'
    assert CANARY not in json.dumps(result)
    import base64
    assert base64.b64encode(CANARY.encode()).decode() not in json.dumps(result)


@pytest.mark.parametrize('fields', [
    {'resources': ['https://example.invalid/secret.yaml']},
    {'resources': ['git::https://example.invalid/repo//base?ref=main']},
    {'resources': ['github.com/example/repo/base?ref=v1']},
    {'resources': ['../../../../outside.yaml']},
    {'resources': ['/etc/passwd']},
    {'resources': ['missing.yaml']},
    {'resources': ['.']},
    {'generators': ['generator.yaml']},
    {'transformers': ['exec.yaml']},
    {'helmCharts': [{'name': 'app', 'repo': 'https://example.invalid/charts'}]},
    {'helmGlobals': {'chartHome': 'charts'}},
    {'helmChartInflationGenerator': [{'chartName': 'app'}]},
    {'openapi': {'path': 'https://example.invalid/schema'}},
    {'crds': ['schema.json']},
    {'unrecognizedFutureLoader': 'https://example.invalid/future'},
    {'kind': {'secret': CANARY}},
    {'resources': 'not-a-list'},
    {'patches': [{'path': 'a', 'patch': 'b'}]},
    {'patches': [{'patch': {'secret': CANARY}}]},
    {'patches': [{'patch': 'metadata:\n  annotations:\n    config.kubernetes.io/function: '+CANARY+'\n'}]},
    {'replacements': [{'path': 'https://example.invalid/replacement.yaml'}]},
    {'configurations': ['https://example.invalid/configuration.yaml']},
    {'configMapGenerator': [{'name': 'x', 'files': ['key=https://example.invalid/data']}]},
    {'secretGenerator': [{'name': 'x', 'envs': ['../../../outside.env']}]},
])
def test_unsupported_inputs_are_rejected_before_any_renderer(repository, tmp_path, monkeypatch, fields):
    set_config(repository, **fields)
    def forbidden(*args, **kwargs):
        raise AssertionError('No renderer should execute for unsupported dependencies')
    monkeypatch.setattr(render, 'run', forbidden)
    result = collect(repository, tmp_path, Path('/not-needed/kustomize'))
    assert result['gaps'][0]['code'] == 'kustomize-inputs'
    assert result['records'] == [] and result['receipts'] == []
    assert CANARY not in json.dumps(result)


def test_nested_remote_dependency_makes_zero_http_requests(repository, tmp_path, kustomize_bin):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(500)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = repository/'deploy/base/kustomization.yaml'
        base.write_text(yaml.safe_dump({'resources': [f'http://127.0.0.1:{server.server_port}/resource.yaml']}))
        result = collect(repository, tmp_path, kustomize_bin)
        assert result['records'] == [] and result['receipts'] == []
        assert result['gaps'][0]['code'] == 'kustomize-inputs'
        assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_ambient_environment_fallback_is_rejected(repository, tmp_path, kustomize_bin):
    (repository/TARGET['path']/'ambient.env').write_text('HOME\n')
    set_config(repository, configMapGenerator=[{'name': 'x', 'envs': ['ambient.env']}])
    result = collect(repository, tmp_path, kustomize_bin)
    assert result['receipts'] == [] and result['gaps'][0]['code'] == 'kustomize-inputs'


@pytest.mark.parametrize('document', [
    'resources: []\nresources: []\n',
    'resources: &r []\nbases: *r\n',
    'resources: !External []\n',
    'resources: []\n---\nresources: []\n',
    'metadata:\n  annotations:\n    config.kubernetes.io/function: '+CANARY+'\n',
])
def test_strict_kustomization_yaml_precedes_render(repository, tmp_path, document, kustomize_bin):
    (repository/TARGET['path']/'kustomization.yaml').write_text(document)
    result = collect(repository, tmp_path, kustomize_bin)
    assert result['receipts'] == [] and result['gaps'][0]['code'] == 'kustomize-inputs'
    assert CANARY not in json.dumps(result)


def test_referenced_resource_alias_and_symlink_rejected(repository, tmp_path, kustomize_bin):
    path = repository/'deploy/base/deployment.yaml'
    path.write_text('apiVersion: v1\nkind: Pod\nmetadata: &m {name: app}\nspec: *m\n')
    assert collect(repository, tmp_path, kustomize_bin)['gaps'][0]['code'] == 'kustomize-inputs'
    path.unlink()
    path.symlink_to(tmp_path/'secret-outside.yaml')
    assert collect(repository, tmp_path, kustomize_bin)['gaps'][0]['code'] == 'kustomize-inputs'


def test_dependency_depth_is_bounded_before_render(repository, tmp_path, kustomize_bin):
    root = repository/'deploy/depth'
    root.mkdir()
    for i in range(26):
        directory = root/str(i)
        directory.mkdir()
        (directory/'kustomization.yaml').write_text(yaml.safe_dump(
            {'resources': [f'../{i+1}']} if i < 25 else {'resources': []}))
    set_config(repository, resources=['../../depth/0'])
    result = collect(repository, tmp_path, kustomize_bin)
    assert result['receipts'] == []
    assert 'traversal limit' in result['gaps'][0]['message']


def test_ambiguous_kustomization_filename_rejected(repository, tmp_path, kustomize_bin):
    (repository/TARGET['path']/'Kustomization').write_text('resources: []\n')
    result = collect(repository, tmp_path, kustomize_bin)
    assert result['receipts'] == []
    assert 'exactly one' in result['gaps'][0]['message']


def test_large_dependency_rejected_before_read_or_render(repository, tmp_path, kustomize_bin):
    path = repository/'deploy/base/deployment.yaml'
    with path.open('wb') as handle:
        handle.truncate(8_000_001)
    result = collect(repository, tmp_path, kustomize_bin)
    assert result['receipts'] == []
    assert 'bounded regular file' in result['gaps'][0]['message']


def test_actual_renderer_failure_is_unknown_and_sanitized(repository, tmp_path, tools_ready):
    path = tmp_path/'profile.json'
    profile(path)
    set_config(repository, patches=[{'target': {'kind': 'Deployment', 'name': 'mission'},
        'patch': '- op: replace\n  path: /unavailable/path\n  value: '+CANARY+'\n'}])
    report = review(arguments(repository, path, tmp_path/'output', tools_ready))
    assert report['decision'] == 'unknown'
    assert any(g['code'] == 'kustomize-render-failed' for g in report['targets'][0]['gaps'])
    assert any(r['operation'] == 'kustomize-build' and r['exit_code'] != 0 for r in report['targets'][0]['receipts'])
    assert not report['targets'][0]['changes']
    assert CANARY not in json.dumps(report)


def test_ignored_dependency_is_missing_not_silently_rendered(repository, tmp_path, tools_ready):
    (repository/'.gitignore').write_text('deploy/overlays/demo/ignored.yaml\n')
    ignored = repository/TARGET['path']/'ignored.yaml'
    ignored.write_text('apiVersion: v1\nkind: Namespace\nmetadata: {name: ignored}\n')
    set_config(repository, resources=['../../base', 'ignored.yaml'])
    path = tmp_path/'profile.json'
    profile(path)
    report = review(arguments(repository, path, tmp_path/'output', tools_ready))
    assert report['decision'] == 'unknown'
    assert not report['targets'][0]['changes']
    assert any(g['code'] == 'kustomize-inputs' for g in report['targets'][0]['gaps'])


def test_missing_binary_preserves_independent_manifest_review(repository, tmp_path, tools_ready):
    (repository/'plain').mkdir()
    (repository/'plain/pod.yaml').write_text('apiVersion: v1\nkind: Pod\nmetadata: {name: other}\nspec:\n  containers:\n    - name: app\n      image: example.invalid/app:latest\n      securityContext: {privileged: true}\n')
    path = tmp_path/'profile.json'
    value = profile(path)
    value['targets'].append({'name': 'plain', 'type': 'manifests', 'path': 'plain', 'namespace': 'demo', 'kubernetes_version': '1.34.0'})
    path.write_text(json.dumps(value))
    args = arguments(repository, path, tmp_path/'output', tools_ready)
    args.kustomize = '/definitely-missing/kustomize'
    report = review(args)
    assert report['decision'] == 'unknown'
    assert any(g['code'] == 'kustomize-prerequisite' for g in report['targets'][0]['gaps'])
    assert report['targets'][1]['confirmed_failure'] is True


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(schema_version=1),
    lambda p: p.update(schema_version=3),
    lambda p: p['targets'][0].pop('source_root'),
    lambda p: p['targets'][0].update(source_root='other'),
    lambda p: p['targets'][0].update(source_root='../escape'),
    lambda p: p['targets'][0].update(source_root='.'),
    lambda p: p['targets'][0].update(values=[]),
    lambda p: p['targets'][0].update(command='something'),
])
def test_kustomize_profile_is_explicit_versioned_and_data_only(repository, tmp_path, mutation):
    path = tmp_path/'profile.json'
    value = profile(path)
    mutation(value)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load_profile(path, repository)


@pytest.mark.parametrize('version', [1, 2])
def test_existing_target_profiles_keep_v1_report_semantics(repository, tmp_path, tools_ready, version):
    target = {'name': 'plain', 'type': 'manifests', 'path': 'deploy/base',
              'namespace': 'demo', 'kubernetes_version': '1.34.0'}
    # A raw manifest target must not include a kustomization descriptor.
    target['path'] = 'deploy/base/deployment.yaml'
    path = tmp_path/'profile.json'
    value = profile(path, target, version)
    assert Draft202012Validator(PROFILE_SCHEMA_V1 if version == 1 else PROFILE_SCHEMA_V2).is_valid(value)
    args = arguments(repository, path, tmp_path/'output', tools_ready, 'HEAD')
    args.kustomize = '/does-not-exist'
    report = review(args)
    assert report['schema_version'] == 1 and report['decision'] == 'pass'
    assert 'kustomize' not in report['toolchain']
    assert Draft202012Validator(REPORT_SCHEMA_V1).is_valid(report)


def test_baseline_profile_cannot_be_redirected_by_candidate(repository, tmp_path, tools_ready):
    path = repository/'profile.json'
    value = profile(path)
    git(repository, 'add', 'profile.json')
    git(repository, 'commit', '-qm', 'trusted baseline profile')
    baseline_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    value['targets'][0]['source_root'] = 'other'
    path.write_text(json.dumps(value))
    report = review(arguments(repository, path, tmp_path/'output', tools_ready))
    assert report['decision'] == 'pass'
    assert report['scope']['profile_sha256'] == baseline_digest
    assert report['scope']['targets'][0]['source_root'] == 'deploy'


def test_candidate_binary_cannot_run(repository, tmp_path, tools_ready):
    binary = repository/'unsafe-tool'
    binary.write_text('#!/bin/sh\nprintf "v5.8.3"\n')
    binary.chmod(0o700)
    path = tmp_path/'profile.json'
    profile(path)
    args = arguments(repository, path, tmp_path/'output', tools_ready)
    args.kustomize = str(binary)
    with pytest.raises(ValueError, match='outside the candidate checkout'):
        review(args)


def test_semantic_repeat_comparison_ignores_document_and_key_order():
    first = b'apiVersion: v1\nkind: Namespace\nmetadata: {name: a}\n---\napiVersion: v1\nkind: Namespace\nmetadata: {name: b}\n'
    second = b'# non-semantic\nmetadata: {name: b}\nkind: Namespace\napiVersion: v1\n---\nmetadata: {name: a}\nkind: Namespace\napiVersion: v1\n'
    assert render._semantic_digest(first) == render._semantic_digest(second)
    assert render._semantic_digest(first) != render._semantic_digest(second.replace(b'name: b', b'name: c'))


def test_disagreeing_renders_are_incomplete(repository, tmp_path, monkeypatch):
    calls = []
    def unstable(*args, **kwargs):
        calls.append(1)
        raw = ('apiVersion: v1\nkind: Namespace\nmetadata: {name: n'+str(len(calls))+'}\n').encode()
        return 0, raw, b'', {'exit_code': 0}
    monkeypatch.setattr(render, 'run', unstable)
    result = collect(repository, tmp_path, Path('/controlled-renderer'))
    assert len(calls) == 2
    assert any(g['code'] == 'kustomize-nondeterministic' for g in result['gaps'])


def test_cli_help_doctor_and_relocated_wrapper(repository, tmp_path, tools_ready, capsys):
    assert '--kustomize' in subprocess.check_output([sys.executable, str(ROOT/'scripts/devsecops_review.py'), 'review', '--help'], text=True)
    flags = [part for key, value in tools_ready.items() for part in ('--'+key, str(value))]
    assert main(['doctor', '--with-kustomize', *flags]) == 0
    assert json.loads(capsys.readouterr().out)['toolchain']['kustomize']['version'] == 'v5.8.3'
    relocated = tmp_path/'relocated package with spaces'
    shutil.copytree(ROOT/'src', relocated/'src', ignore=shutil.ignore_patterns('__pycache__'))
    (relocated/'scripts').mkdir()
    shutil.copyfile(ROOT/'scripts/devsecops_review.py', relocated/'scripts/devsecops_review.py')
    path = tmp_path/'profile.json'
    profile(path)
    result = subprocess.run([sys.executable, '-I', str(relocated/'scripts/devsecops_review.py'),
        'review', '--repo', str(repository), '--base', 'HEAD', '--head', 'HEAD',
        '--profile', str(path), '--output', str(tmp_path/'relocated report'), *flags],
        cwd=tmp_path, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path/'relocated report/report.json').read_text())['schema_version'] == 2


def bootstrap_module():
    spec = importlib.util.spec_from_file_location('kustomize_bootstrap_test', ROOT/'scripts/bootstrap_kustomize.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_offline_missing_archive_fails_closed(tmp_path, capsys):
    assert bootstrap_module().main(['--cache', str(tmp_path/'missing cache'), '--offline']) == 2
    assert not (tmp_path/'missing cache/bin/kustomize').exists()
    assert json.loads(capsys.readouterr().err)['status'] == 'error'


@pytest.mark.parametrize('name,kind', [('kustomize', tarfile.SYMTYPE), ('../kustomize', tarfile.REGTYPE)])
def test_bootstrap_rejects_link_or_traversal_archive(name, kind):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w:gz') as handle:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = 'outside'
        handle.addfile(info)
    with pytest.raises(ValueError):
        bootstrap_module().executable(raw.getvalue())
