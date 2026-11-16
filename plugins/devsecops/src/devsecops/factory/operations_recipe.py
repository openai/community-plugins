"""Closed selections for the fixed owned GitLab/operation coordinator.

Selections cannot issue source, pipeline, delivery or native-review authority.
Project, runner, pipeline, job and Git identities are observed by producers.
"""
from __future__ import annotations

from pathlib import Path

from jsonschema import Draft202012Validator

from .contracts import ABS_PATH, DIGEST, PINNED_FILE, array, document, object_schema
from .state import IDENTITY_KEYS, _json

OPERATIONS_SCHEMA = document('owned-operations-recipe', {
    'schema_version': {'type': 'integer', 'const': 1},
    'kind': {'const': 'owned-operations-recipe'},
    'environment_id': {'type': 'string', 'pattern': '^[a-z][a-z0-9-]{0,31}$'},
    'run_scope': object_schema({name: DIGEST for name in sorted(IDENTITY_KEYS)}),
    'plugin_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
    'plugin_version': {'type': 'string', 'minLength': 1, 'maxLength': 128},
    'writer': PINNED_FILE,
    'runtime': PINNED_FILE,
    'ci': object_schema({
        'required_jobs': array({'type': 'string', 'pattern': '^[a-z][a-z0-9_]{0,63}$'}, minimum=1, maximum=16),
        'publication_job': {'const': 'publish_and_prepare_mission_git'},
        'delivery_required_jobs': array({'type': 'string', 'pattern': '^[a-z][a-z0-9_]{0,63}$'}, minimum=1, maximum=16),
        'poll_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 30},
        'max_observations': {'type': 'integer', 'minimum': 1, 'maximum': 120},
    }),
    'native': object_schema({
        'repository': ABS_PATH,
        'profile': PINNED_FILE,
        'correction_contract': PINNED_FILE,
        'tools': object_schema({'helm': PINNED_FILE, 'kubeconform': PINNED_FILE}),
        'schemas': PINNED_FILE,
    }),
})


def operations_configuration(raw: bytes) -> dict:
    if type(raw) is not bytes or len(raw) > 64000:
        raise ValueError('Bounded operations recipe bytes required')
    value = _json(raw)
    if not Draft202012Validator(OPERATIONS_SCHEMA).is_valid(value):
        raise ValueError('Invalid owned operations recipe')
    if value['ci']['delivery_required_jobs'] != ['validate_owned_deployment']:
        raise ValueError('Owned delivery requires its fixed installed deployment validation job')
    native = value['native']
    paths = [value['writer']['path'], value['runtime']['path'], native['repository'], native['profile']['path'], native['correction_contract']['path'],
             native['schemas']['path'], *[row['path'] for row in native['tools'].values()]]
    if any('..' in Path(path).parts for path in paths):
        raise ValueError('Operations paths cannot traverse parents')
    if value['ci']['publication_job'] not in value['ci']['required_jobs']:
        raise ValueError('Publication job must be mandatory')
    return value


def bind_operations_recipe(raw, *, identities, environment_id, plugin_id, plugin_version):
    value = operations_configuration(raw)
    if (value['run_scope'] != dict(identities) or value['environment_id'] != environment_id
            or value['plugin_id'] != plugin_id or value['plugin_version'] != plugin_version):
        raise ValueError('Operations recipe differs from the current run')
    return value
