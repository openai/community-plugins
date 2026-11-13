"""Pure operator service declarations; no observations or execution authority."""
from __future__ import annotations

from jsonschema import Draft202012Validator

from .contracts import NAME, SERVICE_NAMES, array, document, object_schema
from .state import StateError


ENVIRONMENT_SCHEMA = document('retained-foundation-environment', {
    'schema_version': {'const': 1}, 'environment_id': NAME,
    'services': array(object_schema({
        'id': {'enum': SERVICE_NAMES},
        'disposition': {'enum': ['external-shared', 'planned-owned', 'retained-foundation']},
    }), minimum=1, maximum=16),
})


def classify_foundation_services(environment, config):
    """Admit the complete declaration before any caller chooses service reads.

    Configuration is already validated by the lifecycle. These projected rows
    describe the operator's plan, never observed readiness or resource ownership.
    """
    if not Draft202012Validator(ENVIRONMENT_SCHEMA).is_valid(environment):
        raise StateError('Foundation requires a closed environment service inventory')
    rows = environment['services']
    by_id = {row['id']: row for row in rows}
    configured = {row['id']: row for row in config['services']}
    foundation_kind = config['runtime'].get('foundation', {}).get('kind')
    cluster_disposition = 'planned-owned' if foundation_kind == 'fresh-owned-tcg-v1' else 'retained-foundation'
    if (environment['environment_id'] != config['environment']
            or len(rows) != len(by_id) or len(config['services']) != len(configured)
            or set(by_id) != set(configured)
            or by_id.get('kubernetes', {}).get('disposition') != cluster_disposition
            or by_id.get('registry1', {}).get('disposition') != 'external-shared'
            or any(row['disposition'] == 'retained-foundation' and row['id'] != 'kubernetes' for row in rows)):
        raise StateError('Declared services must exactly cover configuration and the selected cluster scope')
    return [{'id': name, 'origin': configured[name]['origin'], 'disposition': by_id[name]['disposition'],
             'declaration_authority': 'operator-selected', 'ownership': 'unverified',
             'bootstrap': 'not-performed', 'cleanup': 'not-performed'} for name in sorted(by_id)]
