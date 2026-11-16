"""Same-process, fresh image origins from actual publisher/signature acquisition.

This producer is available before baseline collection. Its receipts map locked
content; they do not attest runtime health or authorize deployment.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from weakref import WeakKeyDictionary

from .cases_recipe import bind_cases_recipe
from .case_supply_chain import _freeze, selected_read_policy, verify_locked_image
from .state import StateError, _canonical, _exclusive, _read, _sha, _time

_ORIGINS = WeakKeyDictionary()


class VerifiedLockedImages:
    __slots__ = ('__weakref__',)
    def __new__(cls):
        raise StateError('Locked image origins require actual current acquisition')
    def __reduce__(self):
        raise StateError('Locked image origins cannot be restored from JSON')


def require_locked_images(origin, *, journal, context):
    record = _ORIGINS.get(origin) if type(origin) is VerifiedLockedImages else None
    if record is None or record['pid'] != os.getpid() or record['operations'].journal is not journal:
        raise StateError('Original same-process locked image verification required')
    record['operations']._admit(context)
    binding = record['binding']
    if binding['run_id'] != context.run_id or binding['identities'] != dict(context.identities):
        raise StateError('Locked image verification belongs to another run')
    for path, (digest, length) in record['files'].items():
        raw = _read(path, 16_000_000)
        if _sha(raw) != digest or len(raw) != length:
            raise StateError('Original acquired locked-image evidence changed')
    now = datetime.now(timezone.utc)
    for name in ('reference', 'mission_base'):
        image = binding[name]
        if not _time(image['observed_at']) <= now < _time(image['expires_at']):
            raise StateError('Locked image verification expired; acquire a fresh explicit read-only observation')
    return deepcopy(binding)


def qualify_locked_images(context, *, operations, output):
    """Execute selected positive controls under the current read-only budget.

    Requalification always creates a new original origin and new evidence. No
    stale receipt is silently refreshed or relabeled.
    """
    from .owned_operations import OwnedOperationCoordinator
    if type(operations) is not OwnedOperationCoordinator:
        raise StateError('Exact original operations caller required')
    operations._admit(context)
    if context.execution_mode != 'operator':
        raise StateError('Controlled adapters cannot issue locked-image runtime origins')
    path, pin, raw = operations.selected_component('owned_cases_recipe')
    if _sha(raw) != pin or _read(path, 64000) != raw:
        raise StateError('Locked image selection changed')
    recipe = bind_cases_recipe(raw, identities=context.identities,
        environment_id=operations.journal.state['environment_id'], plugin_id=operations.plugin_id,
        plugin_version=operations.plugin_version)['supply_chain']
    tools = [row for row in context.inputs['lock']['tools'] if row['name'] == 'cosign']
    if (len(tools) != 1 or tools[0]['sha256'] != recipe['cosign']['sha256']
            or context.inputs['config']['tool_paths'].get('cosign') != recipe['cosign']['path']):
        raise StateError('Cryptographic client differs from the selected lock')
    output = Path(output)
    if not output.is_absolute() or '..' in output.parts:
        raise StateError('Image verification requires an exact new output directory')
    output.mkdir(mode=0o700)
    cosign = _freeze(recipe['cosign'], output, 'cosign.selected', context.deadline, maximum=256_000_000)
    binding = {'schema_version': 1, 'kind': 'verified-locked-images', 'run_id': context.run_id,
               'identities': dict(context.identities), 'environment_id': operations.journal.state['environment_id'],
               'authority': 'image-content-mapping-only'}
    for name, destination in (('reference_web', 'reference'), ('mission_base', 'mission_base')):
        selected_read_policy(recipe[name], context.inputs['config'])
        locked = context.inputs['lock']['images'][name]
        result = verify_locked_image(name, recipe[name], locked,
            architecture=context.inputs['lock']['platform']['architecture'], cosign=cosign,
            output=output / name, deadline=context.deadline)
        expires = min(_time(result['expires_at']), _time(context.inputs['config']['expires_at']),
                      _time(result['observed_at']) + timedelta(seconds=recipe['max_verification_age_seconds']))
        mapping = {'schema_version': 1, 'kind': 'runtime-image-mapping', 'repository': locked['repository'],
            'requested_digest': 'sha256:' + locked['manifest_sha256'],
            'platform_manifest_digest': 'sha256:' + locked['platform_manifest_sha256'],
            'config_digest': 'sha256:' + locked['config_sha256'], 'architecture': locked['architecture'],
            'required_predicates': sorted(locked['required_predicates']),
            'trust_identity_basis': 'publisher-policy-sha256-v1', 'trust_policy_sha256': locked['trust_sha256']}
        mapping_path = output / name / 'runtime-image-mapping.json'
        mapping_raw = _canonical(mapping)
        _exclusive(mapping_path, mapping_raw)
        binding[destination] = {'repository': locked['repository'],
            'requested_digest': 'sha256:' + locked['manifest_sha256'],
            'platform_manifest_digest': 'sha256:' + locked['platform_manifest_sha256'],
            'config_digest': 'sha256:' + locked['config_sha256'], 'architecture': locked['architecture'],
            'publisher_trust_sha256': result['publisher_receipt_sha256'],
            'verification_sha256': result['image_receipt_sha256'],
            'required_predicates': list(locked['required_predicates']),
            'mapping_receipt_path': str(mapping_path), 'mapping_receipt_sha256': _sha(mapping_raw),
            'acquisition_receipt_path': str(result['mapping_path']), 'acquisition_receipt_sha256': result['mapping_sha256'],
            'observed_at': result['observed_at'], 'expires_at': expires.isoformat()}
    files = {}
    total = 0
    _exclusive(output / 'locked-images.json', _canonical(binding))
    for item in sorted(output.rglob('*')):
        if item.name == 'cosign.selected' or not item.is_file():
            continue
        data = _read(item, 16_000_000)
        total += len(data)
        if total > 64_000_000:
            raise StateError('Acquired image evidence exceeded its bounded retained budget')
        files[item] = (_sha(data), len(data))
    result = object.__new__(VerifiedLockedImages)
    _ORIGINS[result] = {'pid': os.getpid(), 'operations': operations, 'binding': binding, 'files': files}
    require_locked_images(result, journal=operations.journal, context=context)
    return result


def locked_image_artifacts(origin, *, journal, context):
    require_locked_images(origin, journal=journal, context=context)
    return tuple(_ORIGINS[origin]['files'])


def locked_image_evidence_artifacts(origin, *, journal):
    """Export historical original bytes without renewing image trust authority."""
    record = _ORIGINS.get(origin) if type(origin) is VerifiedLockedImages else None
    if record is None or record['pid'] != os.getpid() or record['operations'].journal is not journal:
        raise StateError('Original same-process locked image verification required')
    binding, state = record['binding'], journal.state
    if (binding['run_id'] != state['run_id'] or binding['identities'] != state['identities']
            or binding['environment_id'] != state['environment_id']):
        raise StateError('Historical locked image evidence belongs to another run')
    if not 1 <= len(record['files']) <= 255:
        raise StateError('Historical locked image evidence exceeds its phase file bound')
    total = 0
    for path, (digest, length) in record['files'].items():
        raw = _read(path, 16_000_000)
        total += len(raw)
        if _sha(raw) != digest or len(raw) != length or total > 64_000_000:
            raise StateError('Original acquired locked-image evidence changed or exceeded its bound')
    return tuple(record['files'])
