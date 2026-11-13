"""Byte consistency for terminal-bound CI evidence bundles, never CI authority."""
import json
from pathlib import Path
import re

from .state import _pairs, _read, _sha


def verify_ci_bundles(result_path, result, manifest):
    from .qualification import QualificationError, file_reference
    root = Path(result_path).parent
    known = {row['path']: row for row in manifest['files']}
    registered = {row['path']: row for phase in result['phase_records'] for row in phase['artifacts']}
    if len(registered) > 4096 or sum(row['bytes'] for row in registered.values()) > 256_000_000:
        raise QualificationError('Terminal phase evidence exceeds its fixed qualification bound')
    summaries = []
    for name, reference in registered.items():
        if reference['bytes'] > 16_000_000:
            raise QualificationError('Terminal phase artifact exceeds its canonical per-file bound')
        if known.get(name) != reference:
            raise QualificationError('CI evidence phase membership differs from terminal manifest')
        path = file_reference(root, reference)
        raw = _read(path, 16_000_000)
        if len(raw) != reference['bytes'] or _sha(raw) != reference['sha256']:
            raise QualificationError('CI evidence bytes changed during qualification')
        if not raw.lstrip().startswith(b'{'):
            continue
        try:
            value = json.loads(raw, object_pairs_hook=_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            continue  # Other registered artifacts can be text, traces or binary.
        if type(value) is not dict or value.get('kind') != 'original-ci-evidence-bundle':
            continue
        if (set(value) != {'schema_version', 'kind', 'run_id', 'operation_id', 'authority', 'documents'}
                or value['schema_version'] != 1 or value['run_id'] != result['run_id']
                or not isinstance(value['operation_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['operation_id'])
                or value['authority'] != 'historical-evidence-only'
                or type(value['documents']) is not list or not 1 <= len(value['documents']) <= 170):
            raise QualificationError('CI bundle has unsupported scope, authority or document membership')
        labels, paths, total = set(), set(), 0
        for document in value['documents']:
            if (type(document) is not dict or set(document) != {'label', 'path', 'sha256', 'bytes', 'utf8'}
                    or not isinstance(document['label'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}\.json', document['label'])
                    or not isinstance(document['path'], str) or not Path(document['path']).is_absolute()
                    or '..' in Path(document['path']).parts or Path(document['path']).name != document['label']
                    or document['label'] in labels or document['path'] in paths
                    or not isinstance(document['utf8'], str) or type(document['bytes']) is not int
                    or not 0 < document['bytes'] <= 8_000_000):
                raise QualificationError('CI bundle contains invalid or duplicate retained labels')
            data = document['utf8'].encode('utf-8')
            total += len(data)
            if total > 8_000_000 or len(data) != document['bytes'] or _sha(data) != document['sha256']:
                raise QualificationError('CI bundle retained document bytes differ from their original hashes')
            try:
                parsed = json.loads(data, object_pairs_hook=_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeError, RecursionError) as error:
                raise QualificationError('CI bundle member is not bounded retained JSON') from error
            if type(parsed) is not dict:
                raise QualificationError('CI bundle member must be a retained JSON document')
            labels.add(document['label'])
            paths.add(document['path'])
        summaries.append({'artifact': name, 'sha256': reference['sha256'], 'documents': len(labels),
            'retained_bytes': total, 'authority': 'historical-evidence-only',
            'basis': 'terminal-bound-embedded-byte-consistency'})
    return summaries
