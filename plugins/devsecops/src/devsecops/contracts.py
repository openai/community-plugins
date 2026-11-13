"""Versioned external report contract, independent of display formatting."""
from copy import deepcopy
from jsonschema import Draft202012Validator


REPORT_SCHEMA_V1 = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    '$id': 'urn:devsecops:report:1', 'type': 'object',
    'required': ['schema_version', 'plugin_version', 'status', 'decision', 'scope', 'targets', 'summary', 'gaps', 'ci'],
    'properties': {
        'schema_version': {'const': 1},
        'plugin_version': {'type': 'string'},
        'run_id': {'type': 'string'},
        'started_at': {'type': 'string'}, 'finished_at': {'type': 'string'},
        'status': {'enum': ['complete', 'partial', 'error']},
        'decision': {'enum': ['pass', 'fail', 'unknown']},
        'static_decision': {'enum': ['pass', 'fail', 'unknown']},
        'scope': {'type': 'object', 'required': ['repo'], 'properties': {'repo': {'type': 'string'}}},
        'summary': {'type': 'object', 'properties': {name: {'type': 'integer', 'minimum': 0}
            for name in ['findings', 'high_findings', 'required_gaps']}},
        'ci': {'type': ['object', 'null']},
        'gaps': {'type': 'array', 'items': {'$ref': '#/$defs/gap'}},
        'targets': {'type': 'array', 'items': {
            'type': 'object', 'required': ['name', 'type', 'status', 'decision', 'findings', 'changes', 'gaps', 'inventory', 'receipts'],
            'properties': {
                'name': {'type': 'string'}, 'type': {'enum': ['helm', 'manifests']},
                'status': {'enum': ['complete', 'partial', 'error']},
                'decision': {'enum': ['pass', 'fail', 'unknown']},
                'findings': {'type': 'array', 'items': {'$ref': '#/$defs/finding'}},
                'changes': {'type': 'array'}, 'gaps': {'type': 'array', 'items': {'$ref': '#/$defs/gap'}},
                'inventory': {'type': 'object', 'required': ['base_count', 'head_count'],
                    'properties': {'base_count': {'type': 'integer', 'minimum': 0}, 'head_count': {'type': 'integer', 'minimum': 0}}},
                'receipts': {'type': 'array'},
            },
        }},
    },
    '$defs': {
        'gap': {'type': 'object', 'required': ['code', 'message'], 'properties': {
            'code': {'type': 'string'}, 'message': {'type': 'string'}, 'required': {'type': 'boolean'},
        }},
        'finding': {'type': 'object', 'required': ['rule_id', 'severity', 'resource', 'field', 'message', 'evidence', 'change', 'remediation', 'fingerprint'],
            'properties': {
                'rule_id': {'type': 'string'}, 'severity': {'enum': ['low', 'medium', 'high', 'critical']},
                'resource': {'type': 'string'}, 'field': {'type': 'string'}, 'message': {'type': 'string'},
                'evidence': {'type': 'object'}, 'change': {'enum': ['introduced', 'worsened', 'preexisting', 'resolved']},
                'remediation': {'type': 'string'}, 'fingerprint': {'type': 'string'},
            }},
    },
}

REPORT_SCHEMA_V2 = deepcopy(REPORT_SCHEMA_V1)
REPORT_SCHEMA_V2['$id'] = 'urn:devsecops:report:2'
REPORT_SCHEMA_V2['properties']['schema_version'] = {'const': 2}
REPORT_SCHEMA_V2['properties']['targets']['items']['properties']['type']['enum'].append('kustomize')
REPORT_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    '$id': 'urn:devsecops:report',
    'oneOf': [REPORT_SCHEMA_V1, REPORT_SCHEMA_V2],
}


def validate_report(report):
    if not Draft202012Validator(REPORT_SCHEMA).is_valid(report):
        raise ValueError('Generated report does not satisfy the versioned report schema')
    if report['decision'] != 'unknown' and report['status'] != 'complete':
        raise ValueError('An incomplete report cannot have a passing or failing gate decision')
