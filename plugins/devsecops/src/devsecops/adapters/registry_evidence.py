"""Direct scoped registry acquisition; saved JSON never authorizes acquisition.

Legacy Cosign storage is a candidate convention, not qualified Registry1 support.
Cryptographic verification and publisher/predicate authority remain separate.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time

from .attestations import MAX_ENVELOPE, JSON_LIMIT_CODES, _decode, _predicate_type
from .http import APIError, _json
from .oci import (ARCHITECTURES, CONFIG_TYPES, MANIFEST_ACCEPT, MANIFEST_TYPES, MAX_OBJECT,
                  _RegistryClient, _RegistrySession, _collect_image_chain,
                  _descriptor, _digest, _repository)
from .signatures import MAX_PAYLOAD as MAX_SIGNATURE_PAYLOAD, _Invalid, _base64, _payload, _snapshot
from .registry_download_policy import load_blob_download_policy
from .registry_attestation_policy import (load_attestation_size_policy, limits_for as attestation_limits_for,
    payload_json_values_for as attestation_json_values_for)
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import canonical_bytes, seal

SIGNATURE_MEDIA = 'application/vnd.dev.cosign.simplesigning.v1+json'
ATTESTATION_MEDIA = 'application/vnd.dsse.envelope.v1+json'
SIGNATURE_ANNOTATION = 'dev.cosignproject.cosign/signature'
STOP_ERRORS = {'deadline', 'request-limit', 'body-limit', 'denied', 'redirect', 'authentication-required',
               'auth-challenge-unconfigured', 'auth-realm-mismatch', 'auth-service-mismatch',
               'auth-scope-mismatch', 'invalid-auth-challenge', 'unsupported-auth-challenge',
               'credential-unavailable', 'invalid-auth-token'}
CONTENT_FAILURES = {'digest-mismatch', 'header-digest-mismatch', 'descriptor-size-mismatch', 'platform-mismatch'}


def collect_registry_evidence(origin, *, repository, requested_digest, architecture,
                              allowed_repositories, required_predicates, output,
                              variant=None, token_env=None, basic_username_env=None,
                              basic_password_env=None, token_path=None, token_service=None,
                              ca_bundle=None, timeout=5.0, total_timeout=120.0,
                              max_requests=64, max_total_bytes=64_000_000, now=None, service_route=None,
                              blob_download_policy=None, blob_download_policy_sha256=None,
                              attestation_size_policy=None, attestation_size_policy_sha256=None):
    """Acquire checked image metadata and every supported legacy Cosign layer.

    Credentials are explicit environment-reference names. A configured read
    credential is required, even for a public registry. Output must be a new
    private absolute directory. No import, report replay or injected client API
    exists. Returned predicate labels are unverified selection hints only.
    """
    if (not _repository(repository) or not _digest(requested_digest)
            or not isinstance(allowed_repositories, (list, tuple)) or not 1 <= len(allowed_repositories) <= 32
            or any(not _repository(p) for p in allowed_repositories)
            or len(set(allowed_repositories)) != len(allowed_repositories) or repository not in allowed_repositories
            or not isinstance(architecture, str) or architecture not in ARCHITECTURES
            or (variant is not None and (not isinstance(variant, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,31}', variant)))
            or not isinstance(required_predicates, (list, tuple)) or not 1 <= len(required_predicates) <= 16
            or any(not _predicate_type(p) for p in required_predicates)
            or len(set(required_predicates)) != len(required_predicates)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts
            or (token_env is None and basic_username_env is None)
            or (now is not None and (not isinstance(now, datetime) or now.tzinfo is None))):
        raise APIError('invalid-configuration')
    selected_download = load_blob_download_policy(blob_download_policy, blob_download_policy_sha256,
        registry_origin=origin, allowed_repositories=allowed_repositories)
    selected_sizes = load_attestation_size_policy(attestation_size_policy, attestation_size_policy_sha256,
        origin=origin, repository=repository, subject_digest=requested_digest)
    started_monotonic = time.monotonic()
    # Validate the bounded transport configuration before opening an output or
    # reading a supplied CA path. Its SSL context uses a frozen private CA below.
    probe = _RegistryClient(origin, token_env=token_env, basic_username_env=basic_username_env,
        basic_password_env=basic_password_env, timeout=timeout, total_timeout=total_timeout,
        max_requests=max_requests, max_body_bytes=MAX_ENVELOPE, max_total_bytes=max_total_bytes)
    _RegistrySession(probe, repository=repository, token_path=token_path, token_service=token_service,
        blob_download_policy=selected_download)
    probe._authorization()  # Missing/invalid credentials fail before network or output.
    if service_route is not None:
        from .owned_route import bind_service_route
        bind_service_route(probe, service_route, origin=probe.origin, ca_bundle=ca_bundle)
    deadline = started_monotonic + total_timeout
    def clock():
        return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    def check_time():
        if time.monotonic() >= deadline:
            raise APIError('deadline')
    result = {'schema_version': 1, 'provider': 'registry-evidence', 'execution_mode': 'controlled-test' if now else 'direct',
        'execution_status': 'partial', 'decision': 'unknown', 'confirmed_failure': False,
        'scope': {'origin': probe.origin, 'repository': repository, 'requested_digest': requested_digest,
                  'os': 'linux', 'architecture': architecture, 'variant': variant,
                  'required_predicates': list(required_predicates)},
        'observed_at': clock(), 'artifacts': {}, 'gaps': [
            {'code': code, 'required': True} for code in ('legacy-storage-convention-unqualified',
                'publisher-signature-unverified', 'required-attestation-cryptography-unverified',
                'publisher-authority-unverified', 'predicate-semantics-unassessed', 'image-layer-content-not-read')],
        'observations': {'acquisition': {'decision': 'unknown', 'source': 'direct-authenticated-https'},
            'storage_convention': 'legacy-cosign-sha256-tag-v1-candidate',
            'authentication': {'mode': 'unavailable', 'token_exchange': 'not-attempted'},
            'tls': {'trust': 'system-ca' if ca_bundle is None else 'explicit-frozen-ca', 'ca_sha256': None},
            'image': {'content_identity': {'decision': 'unknown'}, 'requested_digest': requested_digest,
                'index_digest': None, 'platform_manifest_digest': None, 'config_digest': None,
                'objects': [], 'platform': None},
            'attachments': [], 'signatures': [], 'attestations': [],
            'required_predicates': [{'predicate_type': p, 'candidate_count': 0, 'selection': 'unavailable'} for p in required_predicates],
            'collection': {'started_at': clock()}, 'receipts': []}}
    if selected_download is not None:
        result['scope']['blob_download_policy'] = selected_download.identity_record()
    if selected_sizes is not None:
        result['scope']['attestation_size_policy'] = selected_sizes.identity_record()
    obs = result['observations']
    acquisition_gaps = []
    def gap(code, role=None):
        item = {'code': 'registry-' + code, 'required': True, **({'role': role} if role else {})}
        acquisition_gaps.append(item)
        result['gaps'].append(item)
    with _new_directory(output) as directory:
        def store(raw, media_type, role):
            check_time()
            if not isinstance(raw, bytes):
                raise APIError('object-limit')
            value = hashlib.sha256(raw).hexdigest()
            maximum = (attestation_limits_for(selected_sizes, 'sha256:' + value, len(raw))[0]
                       if media_type == ATTESTATION_MEDIA and role.startswith('att-layer-') else MAX_ENVELOPE)
            if not 0 < len(raw) <= maximum:
                raise APIError('object-limit')
            name = 'sha256-' + value + '.blob'
            if name not in result['artifacts']:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                result['artifacts'][name] = {'sha256': value, 'bytes': len(raw), 'media_types': [], 'roles': []}
            entry = result['artifacts'][name]
            if media_type not in entry['media_types']:
                entry['media_types'].append(media_type)
            if role not in entry['roles']:
                entry['roles'].append(role)
            check_time()
            return name
        client = None
        try:
            with tempfile.TemporaryDirectory(prefix='devsecops-registry-ca-') as temporary:
                frozen_ca = None
                if ca_bundle is not None:
                    frozen_ca = Path(temporary).resolve() / 'ca.pem'
                    ca_ref = _snapshot(ca_bundle, frozen_ca, 2_000_000, deadline=deadline)
                    obs['tls']['ca_sha256'] = ca_ref['sha256']
                client = _RegistryClient(origin, token_env=token_env, basic_username_env=basic_username_env,
                    basic_password_env=basic_password_env, ca_bundle=frozen_ca, timeout=timeout,
                    total_timeout=total_timeout, max_requests=max_requests,
                    max_body_bytes=MAX_ENVELOPE, max_total_bytes=max_total_bytes,
                    **({} if service_route is None else {'service_route': service_route}))
                client._deadline = deadline
                session = _RegistrySession(client, repository=repository, token_path=token_path, token_service=token_service,
                    blob_download_policy=selected_download)
                obs['authentication'] = session.authentication
                def image_sink(raw, receipt):
                    receipt['artifact'] = store(raw, receipt['media_type'], 'image-' + receipt['role'])
                _collect_image_chain(session, requested_digest=requested_digest, architecture=architecture,
                    variant=variant, observations=obs['image'], object_sink=image_sink)
                def read(reference, *, blob=False, descriptor=None, accept=MANIFEST_ACCEPT, attestation=False):
                    check_time()
                    maximum, selected_blob = MAX_ENVELOPE, None
                    if attestation and descriptor is not None:
                        maximum, _ = attestation_limits_for(selected_sizes, descriptor['digest'], descriptor['size'])
                        if selected_sizes is not None:
                            selected_blob = selected_sizes.select_blob(descriptor['digest'], descriptor['size'])
                    if descriptor is not None and descriptor['size'] > maximum:
                        raise APIError('object-limit')
                    route = '/v2/' + repository + ('/blobs/' if blob else '/manifests/') + reference
                    response = session.request(route, accept, **({'expected_size': descriptor['size']}
                        if blob and descriptor is not None else {}),
                        **({'attestation_selection': selected_blob} if selected_blob is not None else {}))
                    if len(response.body) > maximum:
                        raise APIError('object-limit')
                    actual = 'sha256:' + hashlib.sha256(response.body).hexdigest()
                    if _digest(reference) and actual != reference:
                        raise APIError('digest-mismatch')
                    if response.content_digest is not None and response.content_digest != actual:
                        raise APIError('header-digest-mismatch')
                    if descriptor is not None and len(response.body) != descriptor['size']:
                        raise APIError('descriptor-size-mismatch')
                    if descriptor is not None and response.media_type not in (descriptor['mediaType'], 'application/json', 'application/octet-stream'):
                        raise APIError('descriptor-media-mismatch')
                    check_time()
                    return response, actual
                for suffix, layer_type, maximum in (('sig', SIGNATURE_MEDIA, 16), ('att', ATTESTATION_MEDIA, 32)):
                    tag = 'sha256-' + requested_digest[7:] + '.' + suffix
                    attachment = {'kind': suffix, 'tag': tag, 'manifest_digest': None, 'state': 'incomplete',
                                  'expected_layer_count': None, 'collected_layer_count': 0}
                    obs['attachments'].append(attachment)
                    try:
                        discovery, pinned = read(tag)
                        # Resolve the convention's mutable tag, then read the same
                        # content by digest. No tag changes can alter this run.
                        raw, actual = read(pinned)
                        if raw.body != discovery.body or raw.media_type != discovery.media_type:
                            raise APIError('digest-mismatch')
                        attachment['manifest_digest'] = actual
                        attachment['manifest_artifact'] = store(raw.body, raw.media_type, suffix + '-manifest')
                        manifest = _json(raw.body)
                        if (raw.media_type not in MANIFEST_TYPES or manifest.get('mediaType', raw.media_type) != raw.media_type
                                or type(manifest.get('schemaVersion')) is not int or manifest['schemaVersion'] != 2
                                or manifest.get('artifactType') or manifest.get('subject')):
                            raise APIError('attachment-manifest-unsupported')
                        config = _descriptor(manifest.get('config'))
                        if config['mediaType'] not in CONFIG_TYPES or config['size'] > MAX_OBJECT:
                            raise APIError('attachment-config-unsupported')
                        config_raw, _ = read(config['digest'], blob=True, descriptor=config,
                            accept=(*CONFIG_TYPES, 'application/json', 'application/octet-stream'))
                        _json(config_raw.body)
                        attachment['config_artifact'] = store(config_raw.body, config['mediaType'], suffix + '-config')
                        layers = manifest.get('layers')
                        if not isinstance(layers, list) or not 1 <= len(layers) <= maximum:
                            raise APIError('attachment-layer-count-unsupported')
                        attachment['expected_layer_count'] = len(layers)
                        for index, descriptor in enumerate(layers):
                            _descriptor(descriptor)
                            if descriptor['mediaType'] != layer_type:
                                raise APIError('attachment-layer-media-unsupported')
                            if suffix == 'sig' and descriptor['size'] > MAX_SIGNATURE_PAYLOAD:
                                raise APIError('signature-payload-limit')
                            response, _ = read(descriptor['digest'], blob=True, descriptor=descriptor,
                                accept=(layer_type, 'application/json', 'application/octet-stream'), attestation=suffix == 'att')
                            filename = store(response.body, layer_type, suffix + '-layer-' + str(index))
                            attachment['collected_layer_count'] += 1
                            common = {'source_manifest_digest': actual, 'layer_index': index, 'parsed_state': 'unsupported'}
                            if suffix == 'sig':
                                entry = {**common, 'payload_artifact': filename, 'signature_artifact': None}
                                obs['signatures'].append(entry)
                                try:
                                    annotation = descriptor.get('annotations', {}).get(SIGNATURE_ANNOTATION)
                                    _base64(annotation, minimum=64, maximum=128)
                                    entry['signature_artifact'] = store(annotation.encode('ascii'), 'text/plain', 'detached-signature-' + str(index))
                                    _payload(response.body)
                                    entry['parsed_state'] = 'recognized-unverified'
                                except (APIError, _Invalid, AttributeError, TypeError, UnicodeError):
                                    gap('signature-storage-unsupported', suffix)
                            else:
                                entry = {**common, 'envelope_artifact': filename, 'predicate_type': None,
                                    'descriptor': {'digest': descriptor['digest'], 'size_bytes': descriptor['size']}}
                                obs['attestations'].append(entry)
                                try:
                                    _, payload_limit = attestation_limits_for(selected_sizes, descriptor['digest'], descriptor['size'])
                                    json_limit = attestation_json_values_for(selected_sizes, descriptor['digest'], descriptor['size'])
                                    statement, _, _, _ = _decode(response.body, max_payload=payload_limit,
                                        max_payload_json_values=json_limit)
                                    entry.update(predicate_type=statement['predicateType'], parsed_state='recognized-unverified')
                                except (APIError, _Invalid, ValueError, TypeError, KeyError) as error:
                                    code = getattr(error, 'code', None)
                                    entry['parse_failure_code'] = code if code in JSON_LIMIT_CODES else 'attestation-storage-unsupported'
                                    gap(entry['parse_failure_code'], suffix)
                        attachment['state'] = 'complete'
                    except APIError as error:
                        gap(error.code, suffix)
                        if error.code in STOP_ERRORS:
                            break
                check_time()
        except APIError as error:
            gap(error.code, 'image' if obs['image']['content_identity']['decision'] != 'pass' else 'collection')
        except _Invalid as error:
            gap(error.code, 'ca')
        except (OSError, ValueError, TypeError):
            gap('input-or-output-unavailable', 'collection')
        finally:
            if client is not None:
                client.session_token = None
                obs['receipts'] = client.receipts
        for requirement in obs['required_predicates']:
            count = sum(item['predicate_type'] == requirement['predicate_type'] for item in obs['attestations'])
            absent = ('unresolved' if any('parse_failure_code' in item for item in obs['attestations']) else 'missing')
            requirement.update(candidate_count=count, selection='unique-unverified' if count == 1 else absent if count == 0 else 'ambiguous')
            if count != 1:
                gap('required-predicate-' + absent if count == 0 else 'required-predicate-ambiguous')
        if not obs['signatures']:
            gap('image-signature-missing')
        elif len(obs['signatures']) != 1:
            gap('image-signature-candidates-ambiguous')
        if any(g['code'].removeprefix('registry-') in CONTENT_FAILURES for g in acquisition_gaps):
            obs['acquisition']['decision'] = 'fail'
        elif not acquisition_gaps:
            obs['acquisition']['decision'] = 'pass'
        result['observed_at'] = clock()
        obs['collection']['finished_at'] = result['observed_at']
        if now is not None:
            result['gaps'].append({'code': 'controlled-test-clock', 'required': True})
        result = seal(result, 'receipt_sha256')
        raw = canonical_bytes(result)
        receipt_name = 'sha256-' + hashlib.sha256(raw).hexdigest() + '.receipt.json'
        receipt_ref = _write(directory, receipt_name, raw.decode())
        manifest = {'schema_version': 1, 'kind': 'registry-acquisition-manifest', 'finished': True,
            'decision': 'unknown', 'acquisition': obs['acquisition']['decision'],
            'receipt': {'path': receipt_name, **receipt_ref}, 'artifacts': result['artifacts']}
        _verify_output_path(output, directory)
        _write(directory, 'registry-manifest.json', manifest)
        os.fsync(directory)
        try:
            _verify_output_path(output, directory)
        except (OSError, ValueError):
            os.unlink('registry-manifest.json', dir_fd=directory)
            os.fsync(directory)
            raise
        return result
