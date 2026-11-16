"""Pinned native image catalog admission and safe Grype source projection.

Native catalog/config bytes are transient input, never public SBOM evidence.
The original native process-output hashes remain in the scanner receipt.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import time

from .scanner_json import MAX_DOCUMENT_BYTES, parse_scanner_document
from .signatures import _Invalid

FORMAT_VERSION = '16.1.11'
NORMALIZATION = 'devsecops-grype-image-projection-v1'
MAX_OUTPUT = MAX_DOCUMENT_BYTES
_METADATA = {'userInput', 'imageID', 'manifestDigest', 'mediaType', 'tags', 'imageSize',
             'layers', 'manifest', 'config', 'repoDigests', 'architecture', 'os'}


def _checkpoint(deadline):
    if time.monotonic() >= deadline:
        raise _Invalid('scan-deadline-exceeded')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _document(raw, deadline):
    _checkpoint(deadline)
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_OUTPUT:
        raise _Invalid('scanner-catalog-bound-exceeded')
    value = parse_scanner_document(raw)
    _checkpoint(deadline)
    if not isinstance(value, dict):
        raise _Invalid('scanner-catalog-unsupported')
    return value


def _metadata(value, image, deadline):
    """Check both named identity and the embedded exact OCI document bytes."""
    if (not isinstance(value, dict) or not _METADATA <= set(value) <= _METADATA | {'labels'}
            or 'labels' in value and not isinstance(value['labels'], dict)
            or value['userInput'] != 'source.tar'
            or value['imageID'] != image['config_digest']
            or value['manifestDigest'] != image['manifest_digest']
            or value['mediaType'] != 'application/vnd.oci.image.manifest.v1+json'
            or value['architecture'] != image['platform']['architecture'] or value['os'] != 'linux'):
        raise _Invalid('scanner-image-identity-mismatch')
    for name, digest in (('manifest', image['manifest_digest']), ('config', image['config_digest'])):
        _checkpoint(deadline)
        encoded = value[name]
        if not isinstance(encoded, str) or not 1 <= len(encoded) <= 2_666_668:
            raise _Invalid('scanner-image-document-mismatch')
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise _Invalid('scanner-image-document-mismatch') from None
        if len(decoded) > 2_000_000 or 'sha256:' + _hash(decoded) != digest:
            raise _Invalid('scanner-image-document-mismatch')
        _checkpoint(deadline)


def inspect_image_catalog(raw, *, image, archive_sha256, syft_version, deadline):
    value = _document(raw, deadline)
    source, descriptor, schema = value.get('source', {}), value.get('descriptor', {}), value.get('schema', {})
    if (not isinstance(source, dict) or source.get('type') != 'image'
            or source.get('name') != 'frozen-artifact' or source.get('version') != archive_sha256
            or not isinstance(descriptor, dict) or descriptor.get('name') != 'syft'
            or descriptor.get('version') != syft_version or not isinstance(schema, dict)
            or schema.get('version') != FORMAT_VERSION or not isinstance(value.get('artifacts'), list)
            or len(value['artifacts']) > 10000):
        raise _Invalid('scanner-catalog-unsupported')
    _metadata(source.get('metadata'), image, deadline)
    result = {'format': 'syft-json', 'schema_version': FORMAT_VERSION, 'sha256': _hash(raw), 'bytes': len(raw),
            'packages': len(value['artifacts']), 'retention': 'private-transient-input-only',
            'manifest_digest': image['manifest_digest'], 'config_digest': image['config_digest'],
            'platform': image['platform']}
    _checkpoint(deadline)
    return result


def normalize_image_result(raw, *, image, archive_sha256, grype_version, deadline):
    """Retain matcher/distro evidence; remove configuration-bearing envelopes.

The source target and entire descriptor configuration are projected together.
Unknown envelope fields fail closed instead of silently publishing a new copy.
"""
    value = _document(raw, deadline)
    if not {'matches', 'source', 'distro', 'descriptor'} <= set(value) <= {
            'matches', 'ignoredMatches', 'source', 'distro', 'descriptor'}:
        raise _Invalid('scanner-result-envelope-unsupported')
    source, descriptor = value['source'], value['descriptor']
    if (not isinstance(source, dict) or set(source) != {'type', 'target'} or source['type'] != 'image'
            or not isinstance(descriptor, dict) or set(descriptor) != {'name', 'version', 'configuration', 'db', 'timestamp'}
            or descriptor['name'] != 'grype' or descriptor['version'] != grype_version
            or not isinstance(descriptor['configuration'], dict)):
        raise _Invalid('scanner-result-envelope-unsupported')
    _metadata(source['target'], image, deadline)
    database = descriptor['db']
    if (not isinstance(database, dict) or set(database) != {'status', 'providers'}
            or not isinstance(database['status'], dict)
            or not {'schemaVersion', 'built', 'valid'} <= set(database['status']) <= {
                'schemaVersion', 'built', 'valid', 'from', 'path', 'error'}
            or not isinstance(database['providers'], dict) or len(database['providers']) > 64):
        raise _Invalid('scanner-database-envelope-unsupported')
    # Provider identities/timestamps are database evidence. No arbitrary extra
    # descriptor envelope can carry a second image/registry configuration copy.
    for name, provider in database['providers'].items():
        _checkpoint(deadline)
        if (not isinstance(name, str) or not 1 <= len(name) <= 128 or not isinstance(provider, dict)
                or set(provider) != {'captured', 'input'}
                or any(not isinstance(item, str) or len(item) > 256 for item in provider.values())):
            raise _Invalid('scanner-database-envelope-unsupported')
    value['source'] = {'type': 'image', 'target': {
        'userInput': 'frozen-artifact:' + archive_sha256, 'imageID': image['config_digest'],
        'manifestDigest': image['manifest_digest'], 'architecture': image['platform']['architecture'], 'os': 'linux'}}
    value['descriptor'] = {'name': descriptor['name'], 'version': descriptor['version'],
        'db': {'status': {key: database['status'][key] for key in ('schemaVersion', 'built', 'valid')},
               'providers': database['providers']}}
    normalized = _canonical(value)
    if len(normalized) > MAX_OUTPUT:
        raise _Invalid('scanner-catalog-bound-exceeded')
    receipt = {'method': NORMALIZATION, 'original_stdout_sha256': _hash(raw),
        'original_stdout_bytes': len(raw), 'original_retention': 'private-transient-output-only',
        'descriptor_configuration_sha256': _hash(_canonical(descriptor['configuration'])),
        'manifest_digest': image['manifest_digest'], 'config_digest': image['config_digest'],
        'platform': image['platform']}
    _checkpoint(deadline)
    return value, normalized, receipt
