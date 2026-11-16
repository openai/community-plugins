"""Real local Helm, Kustomize, and kubeconform adapters over frozen inputs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import io
import tarfile
import time

from .process import run
from .profiles import relative_path
from .resources import inventory, strict_load_yaml


def gap(code, message, **extra):
    return {'code': code, 'message': message, 'required': True, **extra}


def tool_path(value: str) -> Path:
    result = shutil.which(value)
    if not result:
        raise ValueError('Required tool is unavailable; run the explicit bootstrap step')
    return Path(result).resolve(strict=True)


def _remaining(deadline):
    remaining = 45 if deadline is None else min(45, deadline - time.monotonic())
    if remaining <= 0:
        raise ValueError('Local rendering or validation exceeded its deadline')
    return remaining


def tool_identity(path: Path, args: list[str], cwd: Path, *, deadline=None):
    code, out, _, receipt = run([str(path), *args], cwd=cwd, limit=32_000, timeout=_remaining(deadline))
    _remaining(deadline)
    if code:
        raise ValueError('Could not establish tool version')
    text = out.decode('utf-8', errors='replace').strip()
    # Version output from a trusted executable only; bounded and no diagnostics.
    version = re.search(r'v?\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.]+)?', text)
    if not version:
        raise ValueError('Unsupported tool version output')
    result = {'path': str(path), 'version': version.group(0),
              'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'receipt': receipt}
    _remaining(deadline)
    return result


def _safe_yaml_files(root: Path):
    return sorted([root] if root.is_file() else [p for p in root.rglob('*')
        if p.is_file() and p.suffix.lower() in ('.yaml', '.yml', '.json')])


SCHEMA_DIALECTS = {
    scheme + '://json-schema.org/' + suffix
    for scheme in ('http', 'https')
    for suffix in ('schema', 'schema#', 'draft-04/schema', 'draft-04/schema#',
                   'draft-06/schema', 'draft-06/schema#', 'draft-07/schema', 'draft-07/schema#')
}


def local_schema_only(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ('$ref', '$dynamicRef', '$recursiveRef') and (not isinstance(v, str) or not v.startswith('#')):
                raise ValueError('Schemas must be self-contained; external references are not allowed')
            if k == '$schema' and (not isinstance(v, str) or v not in SCHEMA_DIALECTS):
                raise ValueError('External or unsupported JSON schema dialect is not allowed')
            if k in ('$id', 'id') and isinstance(v, str) and not v.startswith('#'):
                raise ValueError('External JSON schema resolution scopes are not allowed')
            local_schema_only(v)
    elif isinstance(node, list):
        for v in node:
            local_schema_only(v)


def inspect_chart(path: Path) -> bool:
    """Prevent remote schema resolution and boundedly inspect vendored archives.

    Helm itself unpacks chart archives and resolves JSON-schema references. Those
    capabilities need input validation before invoking a supposedly local render.
    """
    total, members, has_lookup = 0, 0, False
    def inspect(name: str, raw: bytes, depth=0):
        nonlocal total, members, has_lookup
        members += 1
        total += len(raw)
        if depth > 4 or total > 64_000_000 or members > 5000:
            raise ValueError('Chart dependency expansion limit exceeded')
        basename = Path(name).name
        # Template filenames are arbitrary, including values.yaml and JSON.
        # Resolve chart-relative locations: a dependency may itself be named
        # "templates", and an archive's first component is its chart wrapper.
        parts = Path(name).parts[1:] if depth else Path(name).parts
        while len(parts) > 2 and parts[0] == 'charts':
            parts = parts[2:]
        if len(parts) > 1 and parts[0] == 'templates':
            has_lookup |= bool(re.search(r'\blookup\b', raw.decode('utf-8')))
        elif name.endswith('.tgz'):
            try:
                with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
                    for entry in archive:
                        if entry.isdir():
                            continue
                        if (not entry.isfile() or entry.size > 64_000_000-total or entry.name.startswith('/')
                                or '..' in Path(entry.name).parts):
                            raise ValueError('Unsafe or oversized vendored chart archive')
                        handle = archive.extractfile(entry)
                        if handle is None:
                            raise ValueError('Unreadable vendored chart archive')
                        inspect(entry.name, handle.read(), depth+1)
            except tarfile.TarError:
                raise ValueError('Vendored chart archive is invalid') from None
        elif basename == 'values.schema.json':
            schema = json.loads(raw)
            local_schema_only(schema)
        elif basename in ('Chart.yaml', 'Chart.lock', 'values.yaml'):
            strict_load_yaml(raw.decode('utf-8'), source='chart-metadata')
    for f in sorted(path.rglob('*')):
        if f.is_file():
            inspect(str(f.relative_to(path)), f.read_bytes())
    return has_lookup


KUSTOMIZATION_FILES = ('kustomization.yaml', 'kustomization.yml', 'Kustomization')
KUSTOMIZATION_FIELDS = {
    'apiVersion', 'kind', 'metadata', 'resources', 'bases', 'components', 'namespace',
    'namePrefix', 'nameSuffix', 'commonLabels', 'labels', 'commonAnnotations',
    'images', 'replicas', 'patches', 'patchesStrategicMerge', 'patchesJson6902',
    'replacements', 'configMapGenerator', 'secretGenerator', 'generatorOptions',
    'configurations', 'vars', 'buildMetadata', 'sortOptions',
}


def inspect_kustomize(snapshot: Path, target: dict) -> dict:
    """Resolve a bounded, local dependency closure before starting Kustomize.

    Only known data-only fields are supported. In particular, resources can be
    remote Git URLs even without a network-enabling CLI flag, so disabling
    plugins alone is insufficient. References are checked in their own context;
    URLs in image names, annotations, or literal ConfigMap values remain data.
    """
    snapshot = snapshot.resolve()
    root = snapshot / relative_path(target['source_root'])
    entrypoint = snapshot / relative_path(target['path'])
    if any(path.is_symlink() for path in (root, *root.parents) if path.is_relative_to(snapshot)):
        raise ValueError('Kustomize source_root must not contain symlinks')
    if not root.is_dir() or not entrypoint.is_dir():
        raise ValueError('Kustomize source_root and target path must be directories')
    root = root.resolve()
    if not entrypoint.resolve().is_relative_to(root):
        raise ValueError('Kustomize target escapes its declared source_root')
    total = 0
    for count, path in enumerate(root.rglob('*'), 1):
        if count > 10000 or path.is_symlink():
            raise ValueError('Kustomize input tree is oversized or contains symlinks')
        if path.is_file():
            total += path.stat().st_size
            if total > 64_000_000:
                raise ValueError('Kustomize input tree exceeds the byte limit')
    files, visited, active = {}, set(), set()

    def reference(base, value):
        if (not isinstance(value, str) or not value or value.startswith(('/', '-'))
                or any(c in value for c in (':', '?', '#', '\\', '\x00'))
                or any(ord(c) < 32 for c in value)):
            raise ValueError('Kustomize references must be local relative paths without URI syntax')
        path = (base/value).resolve()
        if not path.is_relative_to(root) or '.git' in path.relative_to(root).parts:
            raise ValueError('Kustomize reference escapes its declared source_root')
        if not path.exists():
            raise ValueError('Kustomize dependency is missing from the frozen source_root')
        return path

    def yaml_documents(text):
        docs = strict_load_yaml(text)
        def functions(node):
            if isinstance(node, dict):
                if 'config.kubernetes.io/function' in node:
                    raise ValueError('Kustomize function annotations require separate qualification')
                for value in node.values():
                    functions(value)
            elif isinstance(node, list):
                for value in node:
                    functions(value)
        functions(docs)
        return docs

    def read(path, yaml=True):
        if not path.is_file() or path.stat().st_size > 8_000_000:
            raise ValueError('Kustomize dependency is not a bounded regular file')
        data = path.read_bytes()
        if len(data) > 8_000_000:
            raise ValueError('Kustomize dependency exceeds the byte limit')
        files[str(path.relative_to(snapshot))] = {
            'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        return yaml_documents(data.decode('utf-8')) if yaml else None

    def sequence(config, key):
        value = config.get(key, [])
        if not isinstance(value, list):
            raise ValueError('Kustomize input uses an unsupported field shape')
        return value

    def visit(directory, depth=0):
        directory = directory.resolve()
        if directory in active:
            raise ValueError('Kustomize dependency graph contains a cycle')
        if directory in visited:
            return
        if depth > 24 or len(visited) >= 128:
            raise ValueError('Kustomize dependency graph exceeds the traversal limit')
        names = [directory/name for name in KUSTOMIZATION_FILES if (directory/name).is_file()]
        if len(names) != 1:
            raise ValueError('Each Kustomize directory requires exactly one kustomization file')
        docs = read(names[0])
        if len(docs) != 1 or not isinstance(docs[0], dict):
            raise ValueError('Kustomization must contain exactly one mapping document')
        config = docs[0]
        if set(config) - KUSTOMIZATION_FIELDS:
            raise ValueError('Kustomization contains an unsupported field; remote, plugin, exec, Helm, and custom OpenAPI inputs are not qualified')
        kind = config.get('kind', 'Kustomization')
        if not isinstance(kind, str):
            raise ValueError('Unsupported Kustomization kind or API version')
        expected_api = {'Kustomization': 'kustomize.config.k8s.io/v1beta1',
                        'Component': 'kustomize.config.k8s.io/v1alpha1'}.get(kind)
        if expected_api is None or config.get('apiVersion', expected_api) != expected_api:
            raise ValueError('Unsupported Kustomization kind or API version')
        active.add(directory)
        visited.add(directory)
        for key in ('resources', 'bases', 'components'):
            for value in sequence(config, key):
                path = reference(directory, value)
                if path.is_dir():
                    visit(path, depth+1)
                elif key == 'components':
                    raise ValueError('Kustomize components must be local directories')
                else:
                    read(path)
        for value in sequence(config, 'patchesStrategicMerge'):
            if isinstance(value, str) and ('\n' in value or value.lstrip().startswith('{')):
                yaml_documents(value)
            else:
                read(reference(directory, value))
        for key in ('patches', 'patchesJson6902'):
            for value in sequence(config, key):
                if not isinstance(value, dict) or ('path' in value) == ('patch' in value):
                    raise ValueError('Kustomize patches require exactly one local path or inline patch')
                if 'path' in value:
                    read(reference(directory, value['path']))
                elif not isinstance(value['patch'], str):
                    raise ValueError('Inline Kustomize patches must be strings')
                else:
                    yaml_documents(value['patch'])
        for value in sequence(config, 'replacements'):
            if not isinstance(value, dict):
                raise ValueError('Kustomize replacements must be mappings')
            if 'path' in value:
                read(reference(directory, value['path']))
        for value in sequence(config, 'configurations'):
            read(reference(directory, value))
        for key in ('configMapGenerator', 'secretGenerator'):
            for generator in sequence(config, key):
                if not isinstance(generator, dict):
                    raise ValueError('Built-in Kustomize generators must be mappings')
                for value in sequence(generator, 'files'):
                    if not isinstance(value, str):
                        raise ValueError('Kustomize generator file references must be strings')
                    read(reference(directory, value.split('=', 1)[-1]), yaml=False)
                values = sequence(generator, 'envs')
                if 'env' in generator:
                    values = [*values, generator['env']]
                for value in values:
                    path = reference(directory, value)
                    read(path, yaml=False)
                    # Bare keys can fall back to process environment in some
                    # Kustomize versions. A declared file must carry the value.
                    for line in path.read_text(encoding='utf-8').splitlines():
                        line = line.strip()
                        if line and not line.startswith('#') and '=' not in line:
                            raise ValueError('Kustomize generator environment files must contain explicit values')
        active.remove(directory)

    visit(entrypoint)
    encoded = json.dumps(files, sort_keys=True, separators=(',', ':')).encode()
    return {'source_root': target['source_root'], 'entrypoint': target['path'],
            'kustomization_count': len(visited), 'file_count': len(files),
            'sha256': hashlib.sha256(encoded).hexdigest(), 'files': files}


def _semantic_digest(raw):
    documents = strict_load_yaml(raw.decode('utf-8'))
    canonical = sorted(json.dumps(doc, sort_keys=True, separators=(',', ':'))
                       for doc in documents if doc is not None)
    return hashlib.sha256(json.dumps(canonical, separators=(',', ':')).encode()).hexdigest()


def _collect_kustomize(snapshot, target, kustomize, scratch, result):
    # Inspect before even checking/invoking the binary, including unsafe base
    # dependencies that a renderer would otherwise fetch while accumulating.
    try:
        result['input_closure'] = inspect_kustomize(snapshot, target)
    except ValueError as error:
        result['gaps'].append(gap('kustomize-inputs', str(error)))
        return result
    if kustomize is None:
        result['gaps'].append(gap('kustomize-prerequisite', 'Kustomize is unavailable; explicitly provision or select a trusted local executable'))
        return result
    env = {'HOME': str(scratch), 'XDG_CONFIG_HOME': str(scratch/'config'),
           'KUBECONFIG': os.devnull, 'KUSTOMIZE_PLUGIN_HOME': str(scratch/'no-plugins')}
    args = [str(kustomize), 'build', str(snapshot/target['path']),
            '--load-restrictor', 'LoadRestrictionsRootOnly']
    outputs = []
    for attempt in (1, 2):
        code, out, _, receipt = run(args, cwd=scratch, env=env)
        receipt.update({'operation': 'kustomize-build', 'attempt': attempt, 'target': target['name']})
        result['receipts'].append(receipt)
        if code:
            result['gaps'].append(gap('kustomize-render-failed', 'Kustomize rendering failed; raw diagnostics are withheld because they may contain secrets'))
            return result
        outputs.append(out)
    result['render_sha256'] = hashlib.sha256(outputs[0]).hexdigest()
    result['semantic_sha256'], result['repeated_semantic_sha256'] = map(_semantic_digest, outputs)
    if result['semantic_sha256'] != result['repeated_semantic_sha256']:
        result['gaps'].append(gap('kustomize-nondeterministic', 'Repeated Kustomize renders differ; resource comparison is incomplete'))
    result['records'], gaps = inventory(outputs[0].decode('utf-8'), namespace=target['namespace'],
                                        source=target['path']+' (rendered)')
    for record in result['records']:
        record['source_type'] = 'rendered-kustomize'
    result['gaps'].extend(gaps)
    if not result['records'] and not gaps:
        result['gaps'].append(gap('empty-render', 'Kustomize rendered no resources for a selected target'))
    return result


def collect(snapshot: Path, target: dict, helm: Path | None, scratch: Path,
            *, kustomize: Path | None = None, deadline=None) -> dict:
    _remaining(deadline)
    if deadline is not None and target['type'] != 'helm':
        raise ValueError('Bounded collection currently supports the Helm path only')
    result = {'records': [], 'gaps': [], 'receipts': [], 'present': False}
    path = snapshot / target['path']
    if not path.exists():
        return result
    result['present'] = True
    if target['type'] == 'kustomize':
        return _collect_kustomize(snapshot, target, kustomize, scratch, result)
    if target['type'] == 'manifests':
        chunks = []
        sources = []
        for f in _safe_yaml_files(path):
            # Validate each source independently before combining identity/scope across files.
            text = f.read_text(encoding='utf-8')
            documents = strict_load_yaml(text, source=str(f.relative_to(snapshot)))
            sources.extend([(str(f.relative_to(snapshot)), i) for i in range(len(documents))])
            chunks.append(text)
        if not chunks:
            result['gaps'].append(gap('empty-target', 'Selected manifest target contains no YAML or JSON files'))
        result['records'], gaps = inventory('\n---\n'.join(chunks), namespace=target['namespace'],
                                               source=target['path'])
        result['gaps'].extend(gaps)
        for record in result['records']:
            if record['document_index'] < len(sources):
                record['source'], record['document_index'] = sources[record['document_index']]
        if not result['records'] and not result['gaps']:
            result['gaps'].append(gap('empty-target', 'Selected manifest target contains no Kubernetes objects'))
        return result
    if helm is None:
        raise ValueError('Helm is required for Helm targets')
    if not (path / 'Chart.yaml').is_file():
        raise ValueError('Helm target does not contain Chart.yaml')
    # Check all values files and Chart metadata without reserializing or changing Helm coercion.
    inputs = [path/'Chart.yaml', path/'values.yaml', *[snapshot/v for v in target.get('values', [])]]
    if (path/'Chart.lock').exists():
        inputs.append(path/'Chart.lock')
    for f in inputs:
        if f.name == 'values.yaml' and not f.exists():
            continue
        if not f.is_file():
            raise ValueError('A selected Helm values or metadata file is missing')
        strict_load_yaml(f.read_text(encoding='utf-8'), source=str(f.relative_to(snapshot)))
    # lookup results cannot be inferred from a client-only render. Repeat rendering checks
    # randomness, while this conservative check exposes a known empty-client-lookup gap.
    if inspect_chart(path):
        result['gaps'].append(gap('helm-lookup', 'Chart contains lookup; offline rendering cannot establish cluster-dependent output'))
    env = {'HOME': str(scratch), 'KUBECONFIG': os.devnull, 'HELM_CACHE_HOME': str(scratch/'cache'),
           'HELM_CONFIG_HOME': str(scratch/'config'), 'HELM_DATA_HOME': str(scratch/'data'),
           'HELM_PLUGINS': str(scratch/'no-plugins'), 'HELM_NO_PLUGINS': '1'}
    values_args = [a for v in target.get('values', []) for a in ('--values', str(snapshot/v))]
    common = ['--namespace', target['namespace'], '--kube-version', target['kubernetes_version']]
    code, _, _, receipt = run([str(helm), 'lint', str(path), '--strict', *common, *values_args],
                              cwd=scratch, env=env, timeout=_remaining(deadline))
    _remaining(deadline)
    receipt.update({'operation': 'helm-lint', 'target': target['name']})
    result['receipts'].append(receipt)
    if code:
        result['gaps'].append(gap('helm-lint-failed', 'Helm lint failed; raw diagnostics are withheld because they may contain secrets'))
    args = [str(helm), 'template', target['release'], str(path), '--include-crds', *common, *values_args]
    if target.get('mode', 'upgrade') == 'upgrade':
        args.append('--is-upgrade')
    for version in target.get('api_versions', []):
        args += ['--api-versions', version]
    outputs = []
    for attempt in (1, 2):
        code, out, _, receipt = run(args, cwd=scratch, env=env, timeout=_remaining(deadline))
        _remaining(deadline)
        receipt.update({'operation': 'helm-template', 'attempt': attempt, 'target': target['name']})
        result['receipts'].append(receipt)
        if code:
            result['gaps'].append(gap('helm-render-failed', 'Helm rendering failed; fix the chart or missing vendored dependencies and rerun'))
            return result
        outputs.append(out)
    first = strict_load_yaml(outputs[0].decode('utf-8'))
    second = strict_load_yaml(outputs[1].decode('utf-8'))
    def canonical(documents):
        return sorted(json.dumps(doc, sort_keys=True, separators=(',', ':')) for doc in documents if doc is not None)
    if canonical(first) != canonical(second):
        result['gaps'].append(gap('helm-nondeterministic', 'Repeated Helm renders differ; resource comparison is incomplete'))
    result['render_sha256'] = hashlib.sha256(outputs[0]).hexdigest()
    result['records'], gaps = inventory(outputs[0].decode('utf-8'), namespace=target['namespace'],
                                        source=target['path']+' (rendered)')
    # Helm emits a source comment on each rendered document. It identifies a
    # template, not the original values-file line; retain that honest distinction.
    import yaml
    lines = outputs[0].decode('utf-8').splitlines()
    nodes = list(yaml.compose_all(outputs[0].decode('utf-8'), Loader=yaml.SafeLoader))
    previous = 0
    template_sources = {}
    for i, node in enumerate(nodes):
        start = node.start_mark.line if node is not None else previous
        comments = lines[previous:start]
        matches = [line.removeprefix('# Source: ').strip() for line in comments if line.startswith('# Source: ')]
        if matches and re.fullmatch(r'[A-Za-z0-9_. /-]{1,300}', matches[-1]) and '..' not in matches[-1].split('/'):
            template_sources[i] = matches[-1]+' (rendered)'
        previous = node.end_mark.line if node is not None else start
    for record in result['records']:
        record['source_type'] = 'rendered-template'
        record['source'] = template_sources.get(record['document_index'], record['source'])
    result['gaps'].extend(gaps)
    if not result['records'] and not gaps:
        result['gaps'].append(gap('empty-render', 'Helm rendered no resources for a selected target'))
    _remaining(deadline)
    return result


def validate(records: list[dict], target: dict, kubeconform: Path, schemas: Path, scratch: Path, *, deadline=None) -> dict:
    _remaining(deadline)
    result = {'decision': 'pass', 'gaps': [], 'receipts': [], 'counts': {'valid': 0, 'invalid': 0, 'errors': 0, 'skipped': 0}, 'results': []}
    if not records:
        return result
    directory = scratch/'schema-input'
    directory.mkdir(mode=0o700)
    for i, rec in enumerate(records):
        _remaining(deadline)
        p = directory/f'{i:06d}.json'
        p.write_text(json.dumps(rec['body']), encoding='utf-8')
        p.chmod(0o600)
    schema_template = str(schemas/'{{.NormalizedKubernetesVersion}}-standalone-strict'/'{{.ResourceKind}}{{.KindSuffix}}.json')
    if schemas.name.endswith('-standalone-strict'):
        schema_template = str(schemas/'{{.ResourceKind}}{{.KindSuffix}}.json')
    args = [str(kubeconform), '-strict', '-verbose', '-summary', '-output', 'json',
            '-kubernetes-version', target['kubernetes_version'], '-schema-location', schema_template, str(directory)]
    code, out, _, receipt = run(args, cwd=scratch, limit=8_000_000, timeout=_remaining(deadline))
    _remaining(deadline)
    receipt['operation'] = 'kubeconform'
    result['receipts'].append(receipt)
    try:
        payload = json.loads(out)
        counts = payload['summary']
        if not isinstance(counts, dict) or any(type(counts.get(k)) is not int or counts[k] < 0 for k in result['counts']):
            raise ValueError
        if sum(counts[k] for k in result['counts']) != len(records):
            raise ValueError
        result['counts'] = {k: counts[k] for k in result['counts']}
        for row in payload.get('resources', []):
            _remaining(deadline)
            index = int(Path(row['filename']).stem)
            if index < 0 or index >= len(records):
                raise ValueError
            state = row['status']
            if state not in ('statusValid', 'statusInvalid', 'statusError', 'statusSkipped'):
                raise ValueError
            result['results'].append({'resource': records[index]['id'], 'status': state,
                'message': {'statusValid': 'Schema valid', 'statusInvalid': 'Schema invalid',
                            'statusError': 'Schema unavailable or validation could not complete',
                            'statusSkipped': 'Schema validation skipped'}[state]})
    except (KeyError, ValueError, TypeError):
        result['decision'] = 'unknown'
        result['gaps'].append(gap('schema-output', 'Schema validator returned incomplete or unrecognized output'))
        return result
    if counts['invalid']:
        result['decision'] = 'fail'
    if counts['errors'] or counts['skipped'] or (code != 0 and not counts['invalid']):
        result['decision'] = 'unknown'
        result['gaps'].append(gap('schema-unavailable', 'One or more local schemas are missing or validation could not complete'))
    _remaining(deadline)
    return result
