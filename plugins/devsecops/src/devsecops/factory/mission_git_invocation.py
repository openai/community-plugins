"""Closed operator selections for publication and read-only Git preparation.

This envelope is input, not authenticated CI identity or lifecycle authority.
"""
from __future__ import annotations

from pathlib import Path
import re

from jsonschema import Draft202012Validator

from .contracts import ABS_PATH, DIGEST, PINNED_FILE, document, object_schema
from .state import IDENTITY_KEYS, _json, _read, _sha

NAMES = ('build', 'staging', 'security', 'signing', 'publication', 'proposal', 'commit', 'destination')

INVOCATION_SCHEMA = document('mission-git-invocation', {
    'schema_version': {'type': 'integer', 'const': 1},
    'kind': {'const': 'mission-git-preparation-invocation'},
    'configurations': object_schema({name: PINNED_FILE for name in NAMES}),
    'run_scope': object_schema({name: DIGEST for name in sorted(IDENTITY_KEYS)}),
    'plugin_id': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9.@_-]{0,127}$'},
    'plugin_version': {'type': 'string', 'pattern': '^[0-9]+\\.[0-9]+\\.[0-9]+(?:-[a-zA-Z0-9.-]+)?$', 'maxLength': 128},
    'source': object_schema({'repository': ABS_PATH,
        'revision': {'type': 'string', 'pattern': '^(?:[a-f0-9]{40}|[a-f0-9]{64})$'}}),
    'output': ABS_PATH, 'runtime_root': ABS_PATH,
    'timeout': {'type': 'number', 'minimum': 249, 'maximum': 3960},
})


def invocation_configuration(raw: bytes) -> dict:
    """Parse bounded exact schema; do not read paths, invoke tools or issue handles."""
    if not isinstance(raw, bytes) or len(raw) > 64000:
        raise ValueError('Invocation must be bounded JSON bytes')
    value = _json(raw)
    if not Draft202012Validator(INVOCATION_SCHEMA).is_valid(value):
        raise ValueError('Invalid mission Git invocation')
    paths = [value['source']['repository'], value['output'], value['runtime_root'],
             *[entry['path'] for entry in value['configurations'].values()]]
    if any('..' in Path(path).parts for path in paths):
        raise ValueError('Invocation paths cannot traverse parents')
    return value


def load_invocation(path: Path, expected_sha256: str) -> dict:
    """Read one explicitly selected file; its hash conveys no execution authority."""
    if (not isinstance(path, Path) or not path.is_absolute() or '..' in path.parts
            or not isinstance(expected_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_sha256)):
        raise ValueError('An absolute pinned invocation is required')
    raw = _read(path, 64000)
    if _sha(raw) != expected_sha256:
        raise ValueError('Invocation differs from its selected hash')
    return invocation_configuration(raw)


def operation_arguments(value: dict, *, expected_source_repository: Path,
                        expected_source_revision: str) -> dict:
    """Bind a parsed selection to the caller's independently supplied source."""
    if (not isinstance(expected_source_repository, Path) or not expected_source_repository.is_absolute()
            or '..' in expected_source_repository.parts
            or str(expected_source_repository) != value['source']['repository']
            or expected_source_revision != value['source']['revision']):
        raise ValueError('Independent source selection differs from invocation')
    return dict(configurations={name: (Path(entry['path']), entry['sha256'])
                                 for name, entry in value['configurations'].items()},
        expected_run_scope=dict(value['run_scope']), expected_source_repository=expected_source_repository,
        expected_source_revision=expected_source_revision, plugin_id=value['plugin_id'],
        plugin_version=value['plugin_version'], output=Path(value['output']),
        runtime_root=Path(value['runtime_root']), timeout=value['timeout'])
