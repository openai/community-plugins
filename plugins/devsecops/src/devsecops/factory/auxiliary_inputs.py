"""Read-only operator selections; content binding never grants component effects."""
from __future__ import annotations

from pathlib import Path
import re

from .state import AUXILIARY_MISSION_NAMES, AUXILIARY_NAMES, StateError, _json, _read, _sha, _time

MAX_BYTES = 64000


def load_selection(selection, *, identities, environment_id, plugin_id, plugin_version):
    """Read explicit current paths only; never resolve selections from saved state.

    The eight component configurations are byte-bound here. Their component
    schemas, path contents, credentials and effects are not qualified by this
    read-only materialization step.
    """
    selection = {} if selection is None else selection
    if (type(selection) is not dict or not set(selection) <= AUXILIARY_NAMES
            or (set(selection) & AUXILIARY_MISSION_NAMES
                and not AUXILIARY_MISSION_NAMES <= set(selection))):
        raise StateError('Unsupported or partial auxiliary selection')
    raw, pairs = {}, {}
    for name, pair in selection.items():
        if (type(pair) is not tuple or len(pair) != 2 or not isinstance(pair[0], Path)
                or not pair[0].is_absolute() or '..' in pair[0].parts
                or not isinstance(pair[1], str) or not re.fullmatch('[a-f0-9]{64}', pair[1])):
            raise StateError('Auxiliary selection requires explicit absolute pinned paths')
        raw[name] = _read(pair[0], MAX_BYTES)
        if _sha(raw[name]) != pair[1]:
            raise StateError('Auxiliary input differs from its explicit pin')
        pairs[name] = {'path': str(pair[0]), 'sha256': pair[1]}
    if AUXILIARY_MISSION_NAMES <= set(raw):
        from .mission_git_invocation import invocation_configuration
        try:
            invocation = invocation_configuration(raw['mission_git_invocation'])
        except ValueError as error:
            raise StateError('Invalid auxiliary mission invocation') from error
        selected = {name.removeprefix('mission_git_'): pair for name, pair in pairs.items()
                    if name in AUXILIARY_MISSION_NAMES and name != 'mission_git_invocation'}
        if (invocation['configurations'] != selected or invocation['run_scope'] != dict(identities)
                or invocation['plugin_id'] != plugin_id or invocation['plugin_version'] != plugin_version):
            raise StateError('Auxiliary invocation differs from the current explicit run selection')
    if 'owned_registry_recipe' in raw:
        from .service_recipe import validate
        try:
            recipe = validate(_json(raw['owned_registry_recipe']))
        except ValueError as error:
            raise StateError('Invalid auxiliary registry recipe') from error
        if recipe['environment'] != environment_id:
            raise StateError('Auxiliary recipe environment differs from the run')
    if 'native_selection' in raw:
        from .native_driver import validate_selection
        validate_selection(raw['native_selection'], identities=identities, environment_id=environment_id,
                           plugin_id=plugin_id, plugin_version=plugin_version)
    if 'owned_operations_recipe' in raw:
        from .operations_recipe import bind_operations_recipe
        operations = bind_operations_recipe(raw['owned_operations_recipe'], identities=identities,
                               environment_id=environment_id, plugin_id=plugin_id, plugin_version=plugin_version)
        writer = operations['writer']
        path = Path(writer['path'])
        if not path.is_absolute() or '..' in path.parts:
            raise StateError('Writer policy needs an explicit pinned absolute path')
        writer_raw = _read(path, MAX_BYTES)
        if _sha(writer_raw) != writer['sha256']:
            raise StateError('Writer policy differs from the operation selection')
        if 'owned_git_writer' in raw and raw['owned_git_writer'] != writer_raw:
            raise StateError('Writer policy selections conflict')
        raw['owned_git_writer'] = writer_raw
        runtime = operations['runtime']
        runtime_path = Path(runtime['path'])
        if not runtime_path.is_absolute() or '..' in runtime_path.parts:
            raise StateError('Runtime recipe needs an explicit pinned absolute path')
        runtime_raw = _read(runtime_path, MAX_BYTES)
        if _sha(runtime_raw) != runtime['sha256']:
            raise StateError('Runtime recipe differs from the operation selection')
        if 'owned_runtime_recipe' in raw and raw['owned_runtime_recipe'] != runtime_raw:
            raise StateError('Runtime recipe selections conflict')
        raw['owned_runtime_recipe'] = runtime_raw
    if 'owned_git_writer' in raw:
        if 'owned_operations_recipe' not in raw:
            raise StateError('Writer policy requires the original operation selection')
        from .owned_git_delivery import delivery_writer_configuration
        writer = delivery_writer_configuration(raw['owned_git_writer'])
        if writer['run_scope'] != dict(identities) or writer['environment_id'] != environment_id:
            raise StateError('Writer policy differs from the explicit run scope')
    if 'owned_bootstrap_recipe' in raw:
        from .bootstrap_recipe import validate as validate_bootstrap
        recipe = validate_bootstrap(_json(raw['owned_bootstrap_recipe']))
        if recipe['environment'] != environment_id:
            raise StateError('Bootstrap recipe environment differs from the run')
        nested = recipe['registry']
        path = Path(nested['path'])
        if not path.is_absolute() or '..' in path.parts:
            raise StateError('Bootstrap registry recipe requires an explicit absolute path')
        registry_raw = _read(path, MAX_BYTES)
        if _sha(registry_raw) != nested['sha256']:
            raise StateError('Bootstrap registry recipe differs from its selected pin')
        if 'owned_registry_recipe' in raw and raw['owned_registry_recipe'] != registry_raw:
            raise StateError('Explicit and bootstrap registry selections conflict')
        from .service_recipe import validate as validate_registry
        registry = validate_registry(_json(registry_raw))
        if registry['environment'] != environment_id:
            raise StateError('Bootstrap registry environment differs from the run')
        raw['owned_registry_recipe'] = registry_raw
        writer_pin = recipe['writer_policy']
        if 'owned_git_writer' not in raw or _sha(raw['owned_git_writer']) != writer_pin['sha256']:
            raise StateError('Bootstrap writer policy must match the frozen operation writer')
        writer_path = Path(writer_pin['path'])
        if not writer_path.is_absolute() or '..' in writer_path.parts or _read(writer_path, MAX_BYTES) != raw['owned_git_writer']:
            raise StateError('Bootstrap writer policy path differs from the selected operation bytes')
    if 'owned_runtime_recipe' in raw:
        if 'owned_operations_recipe' not in raw:
            raise StateError('Runtime recipe requires the original operation selection')
        from .owned_runtime_inputs import bind_runtime_recipe
        bind_runtime_recipe(raw['owned_runtime_recipe'], identities=identities,
                            environment_id=environment_id)
    if 'owned_cases_recipe' in raw:
        from .cases_recipe import bind_cases_recipe
        bind_cases_recipe(raw['owned_cases_recipe'], identities=identities,
                          environment_id=environment_id, plugin_id=plugin_id, plugin_version=plugin_version)
    return raw


def validate_temporal_scope(raw, config):
    if 'owned_git_writer' in raw:
        writer = _json(raw['owned_git_writer'])
        if _time(writer['expires_at']) > _time(config['expires_at']):
            raise StateError('Writer policy cannot outlive the explicit operator configuration')


def selected_artifacts(journal, raw):
    """Materialize exact frozen selected bytes, not a capability or permission.

    This function neither begins a mutation nor reserves an environment. The
    fixed driver calls it before journaling intent; effectful components must
    still require their current owned operation and independent authorization.
    """
    retained = journal.state['lifecycle'].get('auxiliary_inputs', {})
    if set(retained) != set(raw):
        raise StateError('Explicit auxiliary selection is missing or changed')
    paths = []
    for name in sorted(retained):
        ref = retained[name]
        path = journal._artifact_path(ref['path'])
        value = _read(path, MAX_BYTES)
        if value != raw[name] or _sha(value) != ref['sha256'] or len(value) != ref['bytes']:
            raise StateError('Frozen auxiliary selection changed')
        paths.append(path)
    return tuple(paths)
