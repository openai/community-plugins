"""One fixed OCI evidence artifact and bounded native-referrers readback.

No registry mutations, URL discovery or generic artifact interpretation live here.
"""
from __future__ import annotations

import hashlib
import time

from devsecops.adapters.http import APIError, _json
from devsecops.adapters.image_layout import _RegistryBlobClient
from devsecops.adapters.oci import INDEX, MANIFEST, _RegistrySession, _descriptor, _digest
from devsecops.adapters.vulnerability_policy import checkpoint, timestamp
from devsecops.runtime.context import canonical_bytes

ARTIFACT_TYPE = 'application/vnd.devsecops.mission-evidence.v1'
EMPTY_TYPE = 'application/vnd.oci.empty.v1+json'
MAX_EVIDENCE = 16_000_000
MAX_TOTAL = 64_000_000
FILES = (
    ('payload.json', 'application/vnd.devsecops.mission-signature-payload.v1+json', 1_000_000),
    ('bundle.json', 'application/vnd.dev.sigstore.bundle.v0.3+json', 1_000_000),
    ('public-key.pem', 'application/x-pem-file', 16384),
    ('signing-trust.json', 'application/vnd.devsecops.mission-signing-trust.v1+json', 64000),
    ('sbom.spdx.json', 'application/spdx+json', MAX_EVIDENCE),
    ('grype.json', 'application/vnd.devsecops.grype-report.v1+json', MAX_EVIDENCE),
    ('scanner-receipt.json', 'application/vnd.devsecops.scanner-receipt.v1+json', MAX_EVIDENCE),
)


def reference(raw):
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def descriptor(raw, media_type):
    return {'mediaType': media_type, 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'size': len(raw)}


def artifact_manifest(subject, references):
    """A non-runnable OCI 1.1 artifact with exactly seven named evidence layers."""
    _descriptor(subject)
    if set(subject) != {'mediaType', 'digest', 'size'} or subject['mediaType'] != MANIFEST:
        raise ValueError('Exact OCI image subject descriptor required')
    if set(references) != {name for name, _, _ in FILES}:
        raise ValueError('Fixed mission evidence files required')
    layers = []
    total = 2
    for name, media, maximum in FILES:
        selected = references[name]
        item = {'mediaType': media, 'digest': 'sha256:' + selected['sha256'], 'size': selected['bytes'],
                'annotations': {'org.opencontainers.image.title': name}}
        _descriptor(item)
        if not 1 <= item['size'] <= maximum:
            raise ValueError('Mission evidence file exceeds its bound')
        total += item['size']
        layers.append(item)
    if total > MAX_TOTAL:
        raise ValueError('Mission evidence aggregate bound exceeded')
    manifest = {'schemaVersion': 2, 'mediaType': MANIFEST, 'artifactType': ARTIFACT_TYPE,
        'config': descriptor(b'{}', EMPTY_TYPE), 'layers': layers, 'subject': subject}
    raw = canonical_bytes(manifest)
    return raw, descriptor(raw, MANIFEST)


def bind_payload(raw, source, signer, *, deadline):
    """Compare signed claims with the live capability, never promote imported JSON."""
    checkpoint(deadline)
    if reference(raw) != {key: source['signature_payload'][key] for key in ('sha256', 'bytes')}:
        raise ValueError('Signature payload differs from directly signed bytes')
    payload = _json(raw)
    expected_critical = {'identity': {'docker-reference': source['origin'].removeprefix('https://') + '/' + source['repository']},
        'image': {'docker-manifest-digest': source['manifest_digest']}, 'type': 'cosign container image signature'}
    if payload.get('critical') != expected_critical or set(payload) != {'critical', 'optional'}:
        raise ValueError('Signature subject differs from selected image')
    optional = payload['optional']
    if not isinstance(optional, dict) or set(optional) != {'devsecops'}:
        raise ValueError('Unsupported signed mission payload')
    evidence = optional['devsecops']
    if (not isinstance(evidence, dict) or set(evidence) != {'schema_version', 'kind', 'issuer', 'assessment', 'signed_at', 'expires_at'}
            or type(evidence['schema_version']) is not int or evidence['schema_version'] != 1
            or evidence['kind'] != 'devsecops-mission-security-evidence'
            or evidence['issuer'] != signer or evidence['issuer'] != source['signer']
            or evidence['expires_at'] != source['expires_at']):
        raise ValueError('Signed mission issuer or validity differs')
    assessed = evidence['assessment']
    fields = ('run_scope', 'source_commit', 'architecture', 'context_sha256', 'base', 'manifest_digest',
        'config_digest', 'build_report_sha256', 'policy_sha256', 'policy_id', 'policy_version', 'database',
        'tools', 'scanner_receipt_sha256', 'applied_exceptions')
    if not isinstance(assessed, dict) or set(assessed) != set(fields) | {
            'expires_at', 'staging', 'archive', 'sbom', 'vulnerabilities', 'assessment_binding_sha256'}:
        raise ValueError('Signed assessment shape differs')
    for name in fields:
        checkpoint(deadline)
        if canonical_bytes(assessed[name]) != canonical_bytes(source[name]):
            raise ValueError('Signed assessment identity differs')
    if (assessed['staging'] != {key: source[key] for key in ('origin', 'repository', 'configuration_sha256', 'readback_sha256')}
            or assessed['archive'] != {'sha256': source['archive_sha256'], 'bytes': source['archive_bytes']}
            or any(assessed[name] != {key: source[name][key] for key in ('sha256', 'bytes')} for name in ('sbom', 'vulnerabilities'))
            or not _digest('sha256:' + str(assessed['assessment_binding_sha256']))
            or not timestamp(evidence['signed_at']) < timestamp(evidence['expires_at']) <= timestamp(assessed['expires_at'])):
        raise ValueError('Signed artifact, staging or validity binding differs')
    checkpoint(deadline)
    return evidence


class EvidenceReader:
    """Fresh pull-only session with fixed subject/referrer/digest routes."""
    def __init__(self, selected, *, ca_bundle, deadline, service_route=None):
        remaining = deadline - time.monotonic()
        checkpoint(deadline)
        credentials = selected['credentials']
        self.client = _RegistryBlobClient(selected['origin'], ca_bundle=ca_bundle,
            timeout=min(30, remaining), total_timeout=min(600, remaining), max_layer_bytes=MAX_EVIDENCE,
            max_total_bytes=MAX_TOTAL + 8_000_000, max_requests=32,
            **{name: value for name, value in credentials.items() if name.endswith('_env')},
            **({} if service_route is None else {'service_route': service_route}))
        self.client._deadline = deadline
        self.session = _RegistrySession(self.client, repository=selected['repository'],
            token_path=credentials.get('token_path'), token_service=credentials.get('token_service'))
        self.repository, self.deadline = selected['repository'], deadline

    @property
    def receipts(self):
        return self.client.receipts

    def manifest(self, digest):
        checkpoint(self.deadline)
        if not _digest(digest):
            raise ValueError('Exact manifest digest required')
        response = self.session.request('/v2/' + self.repository + '/manifests/' + digest, (MANIFEST,))
        if (response.status != 200 or response.media_type != MANIFEST
                or 'sha256:' + hashlib.sha256(response.body).hexdigest() != digest
                or response.content_digest not in (None, digest)):
            raise ValueError('Exact manifest readback differs')
        checkpoint(self.deadline)
        return response.body

    def referrers(self, subject, expected=None):
        """One bounded response proves API support or positive exact inclusion only.

        We do not follow pagination or infer absence. A valid page without the
        expected artifact remains unknown even if another page may contain it.
        """
        checkpoint(self.deadline)
        if not _digest(subject):
            raise ValueError('Exact referrers subject required')
        response = self.session._authenticated(lambda first: self.client.get_bytes(
            '/v2/' + self.repository + '/referrers/' + subject, params={'artifactType': ARTIFACT_TYPE},
            accept=(INDEX,), allow_unauthorized=first))
        if response.status != 200 or response.media_type != INDEX:
            raise ValueError('Native OCI referrers API is required before publication')
        index = _json(response.body)
        if (type(index.get('schemaVersion')) is not int or index['schemaVersion'] != 2
                or index.get('mediaType') != INDEX or not isinstance(index.get('manifests'), list)
                or len(index['manifests']) > 1024):
            raise ValueError('Unsupported native referrers index')
        found = 0
        for item in index['manifests']:
            checkpoint(self.deadline)
            _descriptor(item)
            if expected is not None and item['digest'] == expected['digest']:
                if (any(item.get(key) != value for key, value in expected.items())
                        or item.get('artifactType') != ARTIFACT_TYPE or set(item) - {
                            'mediaType', 'digest', 'size', 'artifactType', 'annotations'}):
                    raise ValueError('Referrer descriptor conflicts with uploaded artifact')
                found += 1
        if expected is not None and found != 1:
            raise ValueError('Exact artifact discovery remains unknown')
        return {'sha256': hashlib.sha256(response.body).hexdigest(), 'bytes': len(response.body),
            'scope': 'exact-artifact-inclusion-not-exhaustive-enumeration', 'included': found == 1}

    def blob(self, descriptor, *, fd):
        checkpoint(self.deadline)
        result = self.session.blob(descriptor['digest'], fd=fd, expected_size=descriptor['size'],
            accept=(descriptor['mediaType'], 'application/octet-stream'))
        if result.status != 200 or result.sha256 != descriptor['digest'][7:] or result.size_bytes != descriptor['size']:
            raise APIError('mission-evidence-blob-differs')
        checkpoint(self.deadline)
        return {'sha256': result.sha256, 'bytes': result.size_bytes}

    def close(self):
        self.client.session_token = None
