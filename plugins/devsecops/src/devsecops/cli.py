"""Small explicit CLI shared by the native skill, local usage, and CI."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone
from uuid import uuid4
from . import __version__
from .engine import review
from .contracts import validate_report
from .presentation import render_html, render_markdown
from .render import tool_identity, tool_path


def build_parser():
    parser = argparse.ArgumentParser(prog='devsecops-review', description='Read-only Kubernetes, Helm, and local Kustomize change review')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('review', help='Compare frozen local revisions, optionally with read-only GitLab CI evidence')
    p.add_argument('--repo', required=True, type=Path)
    p.add_argument('--base', help='Local Git base revision; defaults to MR diff base when GitLab is selected')
    p.add_argument('--head', help='Local Git candidate revision or WORKTREE; defaults to MR head or WORKTREE')
    p.add_argument('--profile', required=True, type=Path, help='Trusted external JSON profile, or repository profile loaded from the baseline')
    p.add_argument('--target', action='append', help='Selected target name; repeat for multiple targets, omit for all')
    p.add_argument('--output', required=True, type=Path, help='New output directory outside the reviewed checkout')
    correction = commands.add_parser('validate-correction', help='Validate a proposed patch in an isolated frozen tree; never modify or deliver to the source repository')
    correction.add_argument('--repo', required=True, type=Path)
    correction.add_argument('--base', required=True, help='Full frozen commit SHA')
    correction.add_argument('--profile', required=True, type=Path)
    correction.add_argument('--target', action='append', help='Optional explicit selection; must include every target in the trusted profile')
    correction.add_argument('--contract', required=True, type=Path, help='Trusted external correction scope bound to base/profile')
    correction.add_argument('--patch', required=True, type=Path, help='Actual model-proposed UTF-8 Git diff')
    correction.add_argument('--output', required=True, type=Path)
    cache = Path.home()/'.cache/devsecops/v0.1'
    kustomize_cache = Path.home()/'.cache/devsecops/kustomize-v5.8.3/bin/kustomize'
    doctor = commands.add_parser('doctor', help='Check local tool prerequisites without network access')
    doctor.add_argument('--with-kustomize', action='store_true', help='Also require Kustomize; default retains v1 prerequisites')
    for sub in (p, doctor, correction):
        sub.add_argument('--helm', default=str(cache/'bin/helm') if (cache/'bin/helm').exists() else 'helm')
        sub.add_argument('--kustomize', default=str(kustomize_cache) if kustomize_cache.exists() else 'kustomize',
                         help='Trusted Kustomize executable outside the reviewed checkout')
        sub.add_argument('--kubeconform', default=str(cache/'bin/kubeconform') if (cache/'bin/kubeconform').exists() else 'kubeconform')
        sub.add_argument('--schema-location', default=cache/'schemas', type=Path, help='Trusted local schema directory; never a URL')
    p.add_argument('--gitlab-url', help='Explicit HTTPS GitLab origin, e.g. https://gitlab.example.com')
    p.add_argument('--gitlab-project', help='Project numeric ID or namespace/project')
    p.add_argument('--gitlab-mr', help='Merge request IID')
    p.add_argument('--gitlab-token-env', default='GITLAB_TOKEN', help='Credential environment-variable name; never the token value')
    p.add_argument('--artifact', action='append', help='Opt-in JOB_ID:relative/artifact/path; metadata/digest only')
    from .runtime.cli import add_commands
    add_commands(commands)
    return parser


def check_output(path: Path, repo: Path):
    for p in (path, *path.parents):
        if p.is_symlink():
            raise ValueError('Output paths must not contain symlinks')
    resolved = path.resolve()
    if resolved.exists():
        raise ValueError('Output directory already exists; select a new run directory')
    if resolved.is_relative_to(repo.resolve()):
        raise ValueError('Output must be outside the reviewed checkout')


def write_report(report: dict, output: Path):
    validate_report(report)
    output = Path(output)
    check_output(output, Path(report.get('scope', {}).get('repo', '/__unused__')))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(mode=0o700)
    artifacts = {
        'report.json': json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True)+'\n',
        'report.md': render_markdown(report),
        'report.html': render_html(report),
    }
    manifest = {'schema_version': 1, 'run_id': report.get('run_id'), 'finished_at': report.get('finished_at'),
                'finished': True, 'status': report['status'],
                'decision': report['decision'], 'artifacts': {}}
    def commit(name, text):
        raw = text.encode('utf-8')
        temp = output/('.'+name+'.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, output/name)
        return hashlib.sha256(raw).hexdigest()
    for name, content in artifacts.items():
        manifest['artifacts'][name] = {'sha256': commit(name, content), 'bytes': len(content.encode('utf-8'))}
    # A killed process cannot leave a terminal success record from an earlier run.
    commit('run-manifest.json', json.dumps(manifest, indent=2, sort_keys=True)+'\n')


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command in {'collect-runtime', 'resume-runtime', 'diagnose-gitops', 'verify-gitops-recovery'}:
            from .runtime.cli import run
            return run(args)
        if args.command == 'doctor':
            identities = {'helm': tool_identity(tool_path(args.helm), ['version', '--short'], Path.cwd()),
                          'kubeconform': tool_identity(tool_path(args.kubeconform), ['-v'], Path.cwd())}
            if args.with_kustomize:
                identities['kustomize'] = tool_identity(tool_path(args.kustomize), ['version'], Path.cwd())
            from .engine import _schema_receipt
            identities['schemas'] = _schema_receipt(Path(args.schema_location))
            print(json.dumps({'ready': True, 'toolchain': identities}, indent=2))
            return 0
        check_output(args.output, args.repo)
        if args.command == 'validate-correction':
            from .correction import validate, write_outputs
            rendered_artifacts = {}
            result, static_review, patch = validate(args, rendered_artifacts=rendered_artifacts)
            write_outputs(result, static_review, patch, args.output, rendered_artifacts=rendered_artifacts)
            print(json.dumps({'decision': result['decision'], 'execution_status': result['execution_status'],
                              'patch_sha256': result['patch_sha256'], 'patched_tree_sha256': result['patched_tree']['sha256'],
                              'report': str((args.output / 'correction.html').resolve()),
                              'structured_report': str((args.output / 'correction.json').resolve()), 'delivery_status': 'not_performed'}))
            return 2 if result['decision'] == 'unknown' else 1 if result['decision'] == 'fail' else 0
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', args.gitlab_token_env):
            raise ValueError('Invalid token environment-variable name')
        result = review(args)
        write_report(result, args.output)
        print(json.dumps({'decision': result['decision'], 'status': result['status'],
                          'summary': result['summary'], 'report': str((args.output/'report.html').resolve())}))
        return 2 if result['decision'] == 'unknown' else 1 if result['decision'] == 'fail' else 0
    except ValueError as error:
        # Only our deliberately sanitized ValueErrors are expected at this boundary.
        # json/codec errors may include untrusted values and are handled conservatively.
        message = str(error) if type(error) is ValueError else 'Input or tool validation failed'
        print(json.dumps({'status': 'error', 'decision': 'unknown', 'message': message}), file=sys.stderr)
        failure_report(args, message)
        return 2
    except (OSError, UnicodeError, RecursionError):
        print(json.dumps({'status': 'error', 'decision': 'unknown', 'message': 'An input, tool, or output path is unavailable or invalid'}), file=sys.stderr)
        failure_report(args, 'An input, tool, or output path is unavailable or invalid')
        return 2
    except KeyboardInterrupt:
        print(json.dumps({'status': 'error', 'decision': 'unknown', 'message': 'Review canceled'}), file=sys.stderr)
        failure_report(args, 'Review canceled')
        return 2


def failure_report(args, message):
    if args.command != 'review':
        return
    report = {'schema_version': 1, 'plugin_version': __version__, 'run_id': str(uuid4()),
        'status': 'error', 'decision': 'unknown', 'static_decision': 'unknown',
        'finished_at': datetime.now(timezone.utc).isoformat(), 'scope': {'repo': str(args.repo.resolve())},
        'targets': [], 'ci': None, 'gaps': [{'code': 'review-error', 'message': message, 'required': True}],
        'summary': {'findings': 0, 'high_findings': 0, 'required_gaps': 1}, 'toolchain': {}}
    try:
        write_report(report, args.output)
    except (ValueError, OSError):
        pass  # Original output or an interrupted partial run must never be overwritten.


if __name__ == '__main__':
    raise SystemExit(main())
