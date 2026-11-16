"""Derive a correction base from original delivered fault identity.

The pinned template selects the complete permitted edit scope. Only base_commit
is replaced, once per attempt, using the retained watched delivery and checkout.
No report, selected path or derived document is an origin or mutation grant.
"""
from copy import deepcopy
import os
from pathlib import Path
import threading
from weakref import WeakKeyDictionary

from jsonschema import Draft202012Validator

from devsecops.correction import CONTRACT_SCHEMA, ScopeValidator
from devsecops.profiles import load_profile, relative_path
from devsecops.resources import strict_load_yaml
from devsecops.runtime.cli import _new_directory, _write
from .state import StateError, _json, _read, _sha

_CONTRACTS = WeakKeyDictionary()


class DerivedCorrectionContract:
    __slots__ = ('__weakref__',)

    def __new__(cls):
        raise StateError('Correction derivation requires original delivered fault origins')

    def __reduce__(self):
        raise StateError('Correction derivation cannot be restored from documents')


def _scalar(value, pointer):
    import re
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise StateError('Exact qualified policy pointer required')
    for part in pointer.split('/')[1:]:
        if not part or re.search(r'~(?![01])', part):
            raise StateError('Invalid qualified policy pointer')
        key = part.replace('~1', '/').replace('~0', '~')
        if type(value) is dict and key in value:
            value = value[key]
        elif type(value) is list and re.fullmatch(r'0|[1-9][0-9]*', key) and int(key) < len(value):
            value = value[int(key)]
        else:
            raise StateError('Qualified policy scalar is absent')
    if type(value) is not str:
        raise StateError('Qualified policy must select one string scalar')
    return value


def _originals(operations, attempt):
    from .owned_operations import OwnedOperationCoordinator
    from .network_policy_qualification import require_qualified_network_policy_path
    if (type(operations) is not OwnedOperationCoordinator or operations.pid != os.getpid()
            or operations._thread != threading.get_ident() or attempt not in ('primary', 'replay')):
        raise StateError('Original same-process operation coordinator required')
    delivered = operations.delivery_binding(attempt, 'fault')
    checkout = operations.source_checkout(attempt, 'fault')
    baseline = operations.source_checkout('primary', 'baseline')
    qualification = dict(require_qualified_network_policy_path(operations.runtime._network_policy_path,
        runtime=operations.runtime, run_id=operations.journal.state['run_id'], identities=operations.journal.identities))
    if (delivered['run_id'] != operations.journal.state['run_id']
            or delivered['attempt'] != attempt or delivered['phase'] != attempt+'-inject'
            or delivered.get('source_kind') != 'qualified-policy-change'
            or delivered['watched_revision'] != checkout['revision']
            or delivered['deployment_commit'] != checkout['revision']
            or baseline['revision'] != qualification['source_commit']):
        raise StateError('Correction source differs from original qualified fault delivery')
    return delivered, checkout, baseline, qualification


def derive_correction_contract(operations, context, *, attempt):
    operations._admit(context, 'inject')
    if context.phase['attempt'] != attempt or context.phase['id'] != attempt+'-inject':
        raise StateError('Correction derivation belongs to its exact fault injection')
    if attempt in operations._correction_contracts:
        raise StateError('Original correction derivation cannot be replaced')
    delivered, checkout, baseline, qualification = _originals(operations, attempt)
    selected = operations._recipe()['native']
    policy = operations.runtime.qualified_policy_plan(context)
    plan = policy['plan']
    source_path = relative_path(policy['source_path'])
    if (policy['qualification'] != qualification or source_path != qualification['source_path']
            or policy['render_resource_id'] != qualification['resource_id']
            or plan.resource_id != qualification['resource_id'] or plan.pointer != qualification['pointer']
            or plan.before != qualification['before'] or plan.after != qualification['after']):
        raise StateError('Correction scope differs from the original independent policy qualification')
    provenance = delivered['source_provenance']
    if (provenance['source_path'] != source_path or provenance['source_pointer'] != policy['source_pointer']
            or provenance['qualification_sha256'] != qualification['qualification_sha256']
            or provenance['previous_commit'] != delivered['parent']
            or provenance['phase'] != attempt+'-inject'):
        raise StateError('Actual fault source provenance differs from its qualified scope')
    repository = Path(checkout['repository'])
    template = Path(selected['correction_contract']['path'])
    profile = Path(selected['profile']['path'])
    if template.is_relative_to(repository) or profile.is_relative_to(repository):
        raise StateError('Correction template and render profile must be external trusted selections')
    template_raw, profile_raw = _read(template, 128_000), _read(profile, 128_000)
    if (_sha(template_raw) != selected['correction_contract']['sha256']
            or _sha(profile_raw) != selected['profile']['sha256']):
        raise StateError('Pinned correction template or render profile changed')
    approved = _json(template_raw)
    expected = [{'resource': qualification['resource_id'], 'pointers': [qualification['pointer']]}]
    if (not Draft202012Validator(CONTRACT_SCHEMA).is_valid(approved)
            or approved['profile_sha256'] != selected['profile']['sha256']
            or approved['allowed_files'] != [source_path] or approved['allowed_rendered_changes'] != expected):
        raise StateError('Approved correction template must select exactly the qualified source and rendered scalar')
    ScopeValidator(approved['allowed_rendered_changes'])
    loaded, profile_sha = load_profile(profile, repository, None, base_sha=checkout['revision'])
    if profile_sha != selected['profile']['sha256'] or any(t['type'] != 'helm' for t in loaded['targets']):
        raise StateError('Correction derivation requires the original qualified Helm render profile')
    roots = [Path(p) for t in loaded['targets'] for p in [t['path'], *t.get('values', [])]]
    if not any(Path(source_path) == root or Path(source_path).is_relative_to(root) for root in roots):
        raise StateError('Qualified source file is outside the selected render profile')
    fault_path, baseline_path = repository/source_path, Path(baseline['repository'])/source_path
    fault_raw, baseline_raw = _read(fault_path, 1_000_000), _read(baseline_path, 1_000_000)
    files = [row for row in delivered['selected_tree']['files'] if row['path'] == source_path]
    if (len(files) != 1 or files[0]['sha256'] != _sha(fault_raw) or files[0]['bytes'] != len(fault_raw)):
        raise StateError('Fault worktree differs from the original delivered source snapshot')
    for raw, value in ((fault_raw, qualification['after']), (baseline_raw, qualification['before'])):
        docs = strict_load_yaml(raw.decode('utf-8'))
        if len(docs) != 1 or _scalar(docs[0], policy['source_pointer']) != value:
            raise StateError('Actual policy source is not the qualified baseline/fault scalar')
    rendered_path = Path(delivered['rendered_path'])
    rendered_raw = _read(rendered_path, 16_000_000)
    rendered = _json(rendered_raw)
    resources = [r['body'] for r in rendered['resources'] if r['id'] == qualification['resource_id']]
    if (_sha(rendered_raw) != delivered['rendered_sha256'] or len(resources) != 1
            or _scalar(resources[0], qualification['pointer']) != qualification['after']
            or resources[0]['spec'] != _json(plan.fault)['spec']):
        raise StateError('Actual delivered rendered policy differs from the qualified fault')
    derived = deepcopy(approved)
    derived['base_commit'] = checkout['revision']
    output = operations.journal.run_dir/'owned-operations'/('correction-contract-'+attempt)
    paths = {}
    with _new_directory(output) as fd:
        for name, value in {'template.json': template_raw.decode(), 'profile.json': profile_raw.decode(),
                'baseline-source.yaml': baseline_raw.decode(), 'fault-source.yaml': fault_raw.decode(),
                'fault-rendered.json': rendered_raw.decode(), 'contract.json': derived}.items():
            paths[output/name] = _write(fd, name, value)
        path = output/'contract.json'
        derivation = {'schema_version': 1, 'kind': 'owned-correction-contract-derivation',
            'run_id': context.run_id, 'identities': dict(context.identities), 'attempt': attempt,
            'fault_operation_id': delivered['operation_id'], 'fault_commit': checkout['revision'],
            'parent_commit': delivered['parent'], 'tree': delivered['tree'],
            'template_sha256': selected['correction_contract']['sha256'],
            'profile_sha256': selected['profile']['sha256'], 'contract_sha256': paths[path]['sha256'],
            'changed_fields': ['base_commit'], 'source_path': source_path,
            'source_pointer': policy['source_pointer'], 'qualified_policy': qualification,
            'authority': 'original-resident-origins-only'}
        paths[output/'derivation.json'] = _write(fd, 'derivation.json', derivation)
        os.fsync(fd)
    # Reaudit after local publication; never issue from a partial or changed input.
    if _originals(operations, attempt) != (delivered, checkout, baseline, qualification):
        raise StateError('Original correction inputs changed during derivation')
    value = object.__new__(DerivedCorrectionContract)
    _CONTRACTS[value] = dict(owner=operations, attempt=attempt, originals=(delivered, checkout, baseline, qualification),
        origins=(operations._deliveries[(attempt,'fault')], operations._source_checkouts[(attempt,'fault')],
                 operations._source_checkouts[('primary','baseline')], operations.runtime._network_policy_path),
        selected=deepcopy(selected), paths=paths, path=path,
        inputs={template:template_raw, profile:profile_raw, fault_path:fault_raw,
                baseline_path:baseline_raw, rendered_path:rendered_raw})
    operations._correction_contracts[attempt] = value
    operations._derived_evidence.update(paths)
    return require_correction_contract(value, operations=operations, attempt=attempt)


def require_correction_contract(value, *, operations, attempt):
    record = _CONTRACTS.get(value) if type(value) is DerivedCorrectionContract else None
    if (record is None or record['owner'] is not operations or record['attempt'] != attempt
            or operations._correction_contracts.get(attempt) is not value):
        raise StateError('Original resident correction derivation required')
    current = (operations._deliveries.get((attempt,'fault')), operations._source_checkouts.get((attempt,'fault')),
               operations._source_checkouts.get(('primary','baseline')), operations.runtime._network_policy_path)
    if any(a is not b for a,b in zip(current, record['origins'])):
        raise StateError('Original correction source origins were replaced')
    if (_originals(operations, attempt) != record['originals']
            or operations._recipe()['native'] != record['selected']):
        raise StateError('Original correction derivation inputs changed')
    for path, raw in record['inputs'].items():
        if _read(path, len(raw)+1) != raw:
            raise StateError('Original correction input bytes changed')
    for path, ref in record['paths'].items():
        raw = _read(path, ref['bytes']+1)
        if len(raw) != ref['bytes'] or _sha(raw) != ref['sha256']:
            raise StateError('Derived correction evidence bytes changed')
    return record['path'], record['paths'][record['path']]['sha256']
