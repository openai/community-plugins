"""Orchestrate one bounded review. Network access is confined to GitLab collection."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from uuid import uuid4
from . import __version__
from .checks import compare_and_check
from .gitlab import GitLabClient
from .profiles import load_profile
from .policy import evaluate_findings
from .render import collect, gap, local_schema_only, tool_identity, tool_path, validate
from .snapshot import freeze_commit, freeze_worktree, resolve_ref, git


def aggregate(decisions: list[str]) -> str:
    return 'unknown' if 'unknown' in decisions else 'fail' if 'fail' in decisions else 'pass'


def _schema_receipt(path: Path, destination: Path | None = None) -> dict:
    if not path.is_dir():
        raise ValueError('Local schema directory is unavailable; run bootstrap or provide trusted schemas')
    digest = hashlib.sha256()
    entries = sorted(path.rglob('*'))
    if any(p.is_symlink() for p in entries):
        raise ValueError('Schema directories must not contain symlinks')
    files = [f for f in entries if f.is_file() and f.suffix == '.json']
    if len(files) > 10000:
        raise ValueError('Schema file limit exceeded')
    total = 0
    for f in files:
        if f.is_symlink() or f.stat().st_size > 16_000_000:
            raise ValueError('Schema file is unsafe or exceeds limits')
        raw = f.read_bytes()
        total += len(raw)
        if total > 64_000_000:
            raise ValueError('Schema byte limit exceeded')
        if len(raw) > 16_000_000:
            raise ValueError('Schema file size limit exceeded')
        # External references must not cause implicit schema-network access.
        parsed = json.loads(raw)
        local_schema_only(parsed)
        digest.update(str(f.relative_to(path)).encode() + b'\0' + hashlib.sha256(raw).digest())
        if destination is not None:
            frozen = destination/f.relative_to(path)
            frozen.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            frozen.write_bytes(raw)
            frozen.chmod(0o600)
    return {'path': str(path), 'sha256': digest.hexdigest(), 'file_count': len(files)}


def review(args, ci_client=None, *, resource_validator=None) -> dict:
    report = {'schema_version': 1, 'plugin_version': __version__, 'status': 'partial', 'decision': 'unknown',
              'scope': {}, 'targets': [], 'ci': None, 'gaps': [], 'summary': {}, 'toolchain': {}}
    report.update({'run_id': str(uuid4()), 'rule_version': '1', 'started_at': datetime.now(timezone.utc).isoformat()})
    repo = Path(args.repo).resolve(strict=True)
    actual_root = Path(git(repo, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if actual_root != repo:
        raise ValueError('Repository path must be its Git worktree root')
    report['scope'] = {'repo': str(repo)}
    base, head = args.base, args.head
    wants_ci = any((args.gitlab_url, args.gitlab_project, args.gitlab_mr, args.artifact))
    if wants_ci:
        if not all((args.gitlab_url, args.gitlab_project, args.gitlab_mr)):
            raise ValueError('GitLab URL, project, and merge-request IID must be supplied together')
        if ci_client is None:
            token = os.environ.get(args.gitlab_token_env, '')
            if not token:
                raise ValueError('The configured GitLab token environment variable is empty')
            ci_client = GitLabClient(args.gitlab_url, token)
        artifacts = []
        for item in args.artifact or []:
            job, separator, path = item.partition(':')
            if not separator or not job.isdigit():
                raise ValueError('Artifacts must be specified as JOB_ID:relative/path')
            artifacts.append({'job_id': int(job), 'path': path})
        ci = ci_client.collect(args.gitlab_project, args.gitlab_mr, artifact_requests=artifacts)
        report['ci'] = ci
        refs = ci.get('refs') or {}
        base = base or refs.get('base_sha')
        head = head or refs.get('head_sha')
        if not base or not head:
            report['gaps'].append(gap('gitlab-revisions', 'GitLab revisions are unavailable; provide explicit local base/head to retain static review'))
            return finish(report)
    if not base:
        raise ValueError('Select an explicit base revision (or a GitLab merge request)')
    head = head or 'WORKTREE'
    base_sha = resolve_ref(repo, base)
    head_sha = head if head == 'WORKTREE' else resolve_ref(repo, head)
    profile, profile_hash = load_profile(Path(args.profile), repo, args.target, base_sha=base_sha)
    if any(t['type'] == 'kustomize' for t in profile['targets']):
        report['schema_version'] = 2
    profile_is_baseline = Path(os.path.abspath(args.profile)).is_relative_to(repo) or Path(args.profile).resolve().is_relative_to(repo)
    report['scope'].update({'profile_id': profile['id'], 'profile_revision': profile['revision'],
                           'profile_sha256': profile_hash, 'targets': profile['targets'],
                           'policy': profile['policy'], 'profile_origin': 'baseline' if profile_is_baseline else 'external'})
    if profile_is_baseline:
        report['gaps'].append({'code': 'baseline-profile', 'required': False,
            'message': 'Repository profile is read from the baseline commit; candidate policy changes do not change this review'})
    report['scope'].update({'base': base_sha, 'head': head_sha})
    if report['ci'] is not None:
        refs = report['ci'].get('refs') or {}
        if refs.get('base_sha') != base_sha or refs.get('head_sha') != head_sha:
            report['ci']['decision'] = 'unknown'
            report['ci']['status'] = 'partial'
            report['ci']['gaps'].append(gap('ci-revision-mismatch', 'CI evidence does not match the exact local base/head; a working tree cannot inherit commit CI success'))
    def available(value):
        try:
            return tool_path(value)
        except ValueError:
            return None
    helm = available(args.helm) if any(t['type'] == 'helm' for t in profile['targets']) else None
    kustomize = available(getattr(args, 'kustomize', 'kustomize')) if any(t['type'] == 'kustomize' for t in profile['targets']) else None
    if kustomize and kustomize.is_relative_to(repo):
        raise ValueError('Select a trusted Kustomize executable outside the candidate checkout')
    kubeconform = available(args.kubeconform)
    schemas = Path(args.schema_location).resolve()
    # Schemas, like policies and executables, are trusted inputs outside the candidate checkout.
    if schemas.is_relative_to(repo):
        raise ValueError('Select trusted schemas outside the candidate checkout')
    with tempfile.TemporaryDirectory(prefix='devsecops-private-') as tmp:
        scratch = Path(tmp)
        scratch.chmod(0o700)
        if helm:
            report['toolchain']['helm'] = tool_identity(helm, ['version', '--short'], scratch)
        if kustomize:
            report['toolchain']['kustomize'] = tool_identity(kustomize, ['version'], scratch)
        if kubeconform:
            report['toolchain']['kubeconform'] = tool_identity(kubeconform, ['-v'], scratch)
        frozen_schemas = scratch/'trusted-schemas'
        if schemas.name.endswith('-standalone-strict'):
            frozen_schemas = frozen_schemas/schemas.name
        frozen_schemas.mkdir(mode=0o700, parents=True)
        if schemas.is_dir():
            schema_receipt = _schema_receipt(schemas, destination=frozen_schemas)
            report['toolchain']['schemas'] = schema_receipt
        identities = {'base': {}, 'head': {}}
        for target in profile['targets']:
            item = {'name': target['name'], 'type': target['type'], 'decision': 'unknown', 'status': 'partial',
                    'findings': [], 'changes': [], 'gaps': [], 'validation': [], 'receipts': [],
                    'inventory': {'base_count': 0, 'head_count': 0}, 'inputs': {}}
            report['targets'].append(item)
            try:
                roots = ([target['source_root']] if target['type'] == 'kustomize'
                         else sorted(set([target['path'], *target.get('values', [])])))
                sides = {}
                for side, sha in [('base', base_sha), ('head', head_sha)]:
                    target_scratch = scratch/target['name']/side
                    snapshot = target_scratch/'input'
                    workspace = target_scratch/'tool'
                    workspace.mkdir(parents=True, mode=0o700)
                    snapshot.mkdir(mode=0o700)
                    frozen = freeze_worktree(repo, roots, snapshot) if sha == 'WORKTREE' else freeze_commit(repo, sha, roots, snapshot)
                    item['inputs'][side] = frozen
                    collected = collect(snapshot, target, helm, workspace, kustomize=kustomize)
                    if target['type'] == 'kustomize':
                        item.setdefault('renders', {})[side] = {
                            k: collected[k] for k in ('render_sha256', 'semantic_sha256',
                                'repeated_semantic_sha256', 'input_closure') if k in collected}
                    sides[side] = collected
                    for record in collected['records']:
                        identities[side].setdefault(record['id'], set()).add(target['name'])
                    item['inventory'][side+'_count'] = len(collected['records'])
                    item['gaps'] += [{**g, 'side': side} for g in collected['gaps']]
                    item['receipts'] += [{**r, 'side': side} for r in collected['receipts']]
                    if kubeconform is None or not schemas.is_dir():
                        validation = {'decision': 'unknown', 'gaps': [gap('schema-prerequisite',
                            'Local schema validator or schema directory is unavailable; static findings are retained')],
                            'receipts': [], 'counts': {}, 'results': []}
                    else:
                        validation = validate(collected['records'], target, kubeconform, frozen_schemas, workspace)
                    item['validation'].append({'side': side, **{k: v for k, v in validation.items() if k != 'receipts'}})
                    item['gaps'] += [{**g, 'side': side} for g in validation['gaps']]
                    item['receipts'] += [{**r, 'side': side} for r in validation['receipts']]
                if not any(v['present'] for v in sides.values()):
                    item['gaps'].append(gap('target-missing', 'Selected target is absent in both revisions'))
                if any(g.get('required', True) for side in sides.values() for g in side['gaps']):
                    item['gaps'].append(gap('comparison-incomplete', 'Incomplete rendering or inventory prevents reliable added/removed resource claims'))
                    compared = {'changes': [], 'findings': [], 'gaps': []}
                else:
                    compared = compare_and_check(sides['base']['records'], sides['head']['records'], profile['policy'])
                    if resource_validator is not None:
                        item['validation'].append(resource_validator(target, sides['base']['records'], sides['head']['records']))
                item['changes'], item['findings'] = compared['changes'], compared['findings']
                item['gaps'] += compared['gaps']
                evaluated = evaluate_findings(item['findings'], profile['policy'], target['name'])
                item['findings'] = evaluated['findings']
                item['gaps'] += evaluated['gaps']
                item['applied_exceptions'] = evaluated['applied_exceptions']
                item['policy_evaluated_at'] = evaluated['evaluated_at']
                failed = any(v['decision'] == 'fail' for v in item['validation']) or evaluated['policy_failure']
                item['confirmed_failure'] = failed
                item['decision'] = 'unknown' if any(g.get('required', True) for g in item['gaps']) else 'fail' if failed else 'pass'
                item['status'] = 'partial' if item['decision'] == 'unknown' else 'complete'
            except (ValueError, OSError, UnicodeError) as error:
                # No raw parser or OS diagnostic can disclose a values/Secret payload.
                detail = str(error) if type(error) is ValueError else 'Check selected paths, strict YAML, tool receipts, and dependency availability'
                item['gaps'].append(gap('target-incomplete', 'Target processing could not complete: '+detail))
        for side, index in identities.items():
            for identity, names in index.items():
                if len(names) > 1:
                    for item in report['targets']:
                        if item['name'] in names:
                            item['gaps'].append(gap('cross-target-identity', 'Multiple selected targets produce the same resource identity', resource=identity, side=side))
                            item['decision'], item['status'] = 'unknown', 'partial'
    return finish(report)


def finish(report: dict) -> dict:
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    decisions = [item['decision'] for item in report['targets']]
    if report['ci'] is not None:
        decisions.append(report['ci']['decision'])
    if any(g.get('required', True) for g in report['gaps']) or not report['targets']:
        decisions.append('unknown')
    report['static_decision'] = aggregate([t['decision'] for t in report['targets']]) if report['targets'] else 'unknown'
    report['decision'] = aggregate(decisions)
    report['status'] = 'partial' if report['decision'] == 'unknown' else 'complete'
    findings = [f for t in report['targets'] for f in t['findings'] if f['change'] != 'resolved']
    gaps = report['gaps'] + [g for t in report['targets'] for g in t['gaps']] + (report['ci'].get('gaps', []) if report['ci'] else [])
    report['summary'] = {'findings': len(findings),
                         'high_findings': sum(f['severity'] in ('high', 'critical') for f in findings),
                         'required_gaps': sum(g.get('required', True) for g in gaps)}
    report['limitations'] = [
        'Static rendering and schemas do not prove admission, deployment, runtime safety, or cluster enforcement.',
        'Specialized semantic checks are limited to the documented v0.1 kind/version allowlist.',
        'No live cluster queries, deployments, pipeline execution, comments, or repository modifications were performed.',
        'Helm comparison uses declared render inputs, not deployed release history or Helm three-way merge state.',
    ]
    if any(t['type'] == 'kustomize' for t in report['targets']):
        report['limitations'].append('Kustomize uses bounded local inputs; repeated local renders do not establish Argo CD or Flux controller parity.')
    return report
