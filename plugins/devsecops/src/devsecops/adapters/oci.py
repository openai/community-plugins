"""Bounded OCI content identity resolution; no publisher or runtime trust claim."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
import threading

from .http import APIError, HTTPSReadClient, _attestation_body_allowance, _bearer, _json
from .registry_download_policy import RegistryBlobPolicy, RedirectPolicyError, load_blob_download_policy

INDEX = 'application/vnd.oci.image.index.v1+json'
MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
CONFIG = 'application/vnd.oci.image.config.v1+json'
DOCKER_INDEX = 'application/vnd.docker.distribution.manifest.list.v2+json'
DOCKER_MANIFEST = 'application/vnd.docker.distribution.manifest.v2+json'
DOCKER_CONFIG = 'application/vnd.docker.container.image.v1+json'
INDEX_TYPES = (INDEX, DOCKER_INDEX)
MANIFEST_TYPES = (MANIFEST, DOCKER_MANIFEST)
CONFIG_TYPES = (CONFIG, DOCKER_CONFIG)
MANIFEST_ACCEPT = (*INDEX_TYPES, *MANIFEST_TYPES)
REPOSITORY = r'[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*(?:/[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*)*'
DIGEST = r'sha256:[0-9a-f]{64}'
ARCHITECTURES = {'386', 'amd64', 'arm', 'arm64', 'loong64', 'mips', 'mipsle', 'mips64',
                 'mips64le', 'ppc64', 'ppc64le', 'riscv64', 's390x'}
MAX_OBJECT = 2_000_000


def _repository(value):
    return isinstance(value, str) and len(value) <= 255 and re.fullmatch(REPOSITORY, value)


def _digest(value):
    return isinstance(value, str) and re.fullmatch(DIGEST, value)


def _descriptor(value):
    if (not isinstance(value, dict) or not _digest(value.get('digest'))
            or type(value.get('size')) is not int or not 0 <= value['size'] <= 2**63-1
            or not isinstance(value.get('mediaType'), str) or len(value['mediaType']) > 128
            or not re.fullmatch(r'[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+', value['mediaType'])):
        raise APIError('invalid-descriptor')
    return value


class _RegistryClient(HTTPSReadClient):
    """Transient bearer credentials are confined to this exact-origin client."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_token = None

    def _authorization(self):
        return _bearer(self.session_token) if self.session_token is not None else super()._authorization()


def _challenge(value, *, origin, path, service, repository):
    # A deliberately small grammar: one Bearer challenge, exactly three quoted
    # fields, no escapes, duplicate attributes, extra audiences or permissions.
    if not isinstance(value, str) or not value[:7].lower() == 'bearer ' or value.rstrip().endswith(','):
        raise APIError('unsupported-auth-challenge')
    remaining, fields = value[7:].strip(), {}
    while remaining:
        match = re.match(r'([A-Za-z][A-Za-z0-9_-]*)="([^"\\\r\n]*)"(?:\s*,\s*|$)', remaining)
        if not match or match[1].lower() in fields:
            raise APIError('invalid-auth-challenge')
        fields[match[1].lower()] = match[2]
        remaining = remaining[match.end():]
    if set(fields) != {'realm', 'service', 'scope'}:
        raise APIError('invalid-auth-challenge')
    # String equality is stricter than URL normalization: no userinfo, alternate
    # origin, query parameters, percent-encoded path, or fragment can be added.
    if fields['realm'] != origin+path:
        raise APIError('auth-realm-mismatch')
    if fields['service'] != service:
        raise APIError('auth-service-mismatch')
    if fields['scope'] != 'repository:'+repository+':pull':
        raise APIError('auth-scope-mismatch')
    return {'service': service, 'scope': 'repository:'+repository+':pull'}


class _RegistrySession:
    """One repository and exact-origin auth exchange under one transport budget."""
    def __init__(self, client, *, repository, token_path=None, token_service=None, blob_download_policy=None):
        if (not _repository(repository) or (token_path is None) != (token_service is None)
                or (token_path is not None and (
                    not isinstance(token_path, str) or not re.fullmatch(r'/[A-Za-z0-9_./~-]{1,255}', token_path)
                    or token_path.startswith('//') or any(part in ('.', '..') for part in token_path.split('/'))
                    or not isinstance(token_service, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', token_service)))):
            raise APIError('invalid-configuration')
        self.client, self.repository = client, repository
        if blob_download_policy is not None and (not isinstance(blob_download_policy, RegistryBlobPolicy)
                or blob_download_policy.registry_origin != client.origin
                or repository not in blob_download_policy.repositories):
            raise APIError('blob-policy-scope-mismatch')
        self.blob_download_policy = blob_download_policy
        self._operation_lock = threading.Lock()
        self.token_path, self.token_service, self.exchanged = token_path, token_service, False
        self.mode = ('bearer-env' if client.token_env is not None else
                     'basic-env' if client._basic_username_env is not None else 'none')
        self.authentication = {'mode': self.mode, 'token_exchange': 'not-attempted'}

    def request(self, path, accept, *, expected_size=None, attestation_selection=None):
        prefix = '/v2/' + self.repository
        if not isinstance(path, str):
            raise APIError('registry-route-outside-scope')
        suffix = path.rsplit('/', 1)[-1]
        if path not in (prefix + '/manifests/' + suffix, prefix + '/blobs/' + suffix):
            raise APIError('registry-route-outside-scope')
        if not _digest(suffix) and not (path.startswith(prefix + '/manifests/')
                and re.fullmatch(r'sha256-[0-9a-f]{64}\.(?:sig|att)', suffix)):
            raise APIError('registry-route-outside-scope')
        is_blob = path.startswith(prefix + '/blobs/')
        allowance = None
        maximum = self.client._max_body
        if attestation_selection is not None:
            if not is_blob or type(expected_size) is not int:
                raise APIError('invalid-attestation-allowance')
            allowance = _attestation_body_allowance(attestation_selection, origin=self.client.origin,
                repository=self.repository, digest=suffix, size_bytes=expected_size)
            maximum = allowance.size_bytes
        if expected_size is not None and (not is_blob or type(expected_size) is not int
                or not 0 <= expected_size <= maximum):
            raise APIError('object-limit')
        if not self._operation_lock.acquire(blocking=False):
            raise APIError('concurrent-request')
        try:
            options = {'_allow_blob_redirect': True} if is_blob and self.blob_download_policy is not None else {}
            if allowance is not None:
                options['_attestation_allowance'] = allowance
            response = self._authenticated(lambda first: self.client.get_bytes(path, accept=accept,
                allow_unauthorized=first, **options))
            if response.status == 307:
                if expected_size is None:
                    raise APIError('blob-descriptor-required')
                response = self._download(response, path, suffix, accept=accept, expected_size=expected_size,
                                          attestation_selection=attestation_selection)
            if is_blob and expected_size is not None:
                self._verify_buffered_blob(response, suffix, expected_size)
            return response
        finally:
            self._operation_lock.release()

    def blob(self, digest, *, fd, expected_size, accept):
        """Large file streams are restricted to this repository's exact digest."""
        if not _digest(digest):
            raise APIError('registry-route-outside-scope')
        path = '/v2/' + self.repository + '/blobs/' + digest
        if not self._operation_lock.acquire(blocking=False):
            raise APIError('concurrent-request')
        try:
            options = {'_allow_blob_redirect': True} if self.blob_download_policy is not None else {}
            response = self._authenticated(lambda first: self.client.get_blob(path, fd=fd,
                expected_size=expected_size, expected_digest=digest, accept=accept, allow_unauthorized=first,
                **options))
            if response.status == 307:
                response = self._download(response, path, digest, accept=accept,
                    expected_size=expected_size, fd=fd)
            return response
        finally:
            self._operation_lock.release()

    @staticmethod
    def _verify_buffered_blob(response, digest, expected_size):
        actual = 'sha256:' + hashlib.sha256(response.body).hexdigest()
        if actual != digest:
            raise APIError('digest-mismatch')
        if response.content_digest is not None and response.content_digest != actual:
            raise APIError('header-digest-mismatch')
        if len(response.body) != expected_size:
            raise APIError('descriptor-size-mismatch')

    def _download(self, response, source_path, digest, *, accept, expected_size, fd=None,
                  attestation_selection=None):
        # Only a directly observed source response reaches this private method.
        # Storage failures are exceptions, never a source authentication challenge.
        if self.blob_download_policy is None:
            raise APIError('redirect', response.status)
        try:
            target = self.blob_download_policy.select(registry_origin=self.client.origin,
                repository=self.repository, request_path=source_path, digest=digest,
                status=response.status, location_headers=[response.blob_location])
        except RedirectPolicyError as error:
            raise APIError('blob-' + error.code, response.status) from None
        from .owned_route import audit_service_route
        audit_service_route(self.client)
        from .http_blob import HTTPSBlobReadClient
        cls = HTTPSBlobReadClient if fd is not None else HTTPSReadClient
        # No source auth, cookies, mTLS, owned route, private trust or proxy is
        # passed to this independent client. Pin OS default roots explicitly so
        # SSL_CERT_FILE/SSL_CERT_DIR cannot import the registry's private CA.
        storage = cls(target.origin, _system_roots_only=True)
        storage._share_transport_budget(self.client)
        storage._registry_download = True
        if fd is not None:
            storage._max_layer = self.client._max_layer
        path, params = target.request_parts()
        try:
            if fd is None:
                options = {}
                if attestation_selection is not None:
                    options['_attestation_allowance'] = _attestation_body_allowance(attestation_selection,
                        origin=self.client.origin, repository=self.repository, digest=digest,
                        size_bytes=expected_size, target=target)
                result = storage.get_bytes(path, params, accept=accept, **options)
            else:
                result = storage.get_blob(path, _params=params, fd=fd, expected_size=expected_size,
                    expected_digest=digest, accept=accept)
        except APIError as error:
            raise APIError('blob-download-' + error.code, error.status) from None
        audit_service_route(self.client)
        return result

    def _authenticated(self, get):
        response = get(True)
        if response.status != 401:
            return response
        if self.exchanged or self.mode != 'basic-env':
            raise APIError('authentication-required', 401)
        if self.token_path is None:
            raise APIError('auth-challenge-unconfigured', 401)
        params = _challenge(response.auth_challenge, origin=self.client.origin, path=self.token_path,
                            service=self.token_service, repository=self.repository)
        self.exchanged = True
        self.authentication['token_exchange'] = 'attempted'
        issued = self.client.get_json(self.token_path, params)
        token = issued.get('token', issued.get('access_token'))
        if ('token' in issued and 'access_token' in issued and issued['token'] != issued['access_token']):
            raise APIError('invalid-auth-token')
        try:
            _bearer(token)
        except APIError:
            raise APIError('invalid-auth-token') from None
        if 'expires_in' in issued and (type(issued['expires_in']) is not int or issued['expires_in'] <= 0):
            raise APIError('invalid-auth-token')
        self.client.session_token = token
        self.authentication['token_exchange'] = 'completed'
        # Exactly one retry; a second challenge cannot expand permission or
        # mint refresh credentials. The transient token dies with this call.
        return get(False)


def _collect_image_chain(session, *, requested_digest, architecture, variant, observations, object_sink=None):
    """Shared content checks; sink receives only directly fetched, checked bytes."""
    repository = session.repository
    def read_object(digest, role, descriptor=None, *, blob=False):
        if descriptor is not None and descriptor['size'] > MAX_OBJECT:
            raise APIError('object-limit')
        accept = (*CONFIG_TYPES, 'application/octet-stream', 'application/json') if blob else MANIFEST_ACCEPT
        response = session.request('/v2/'+repository+('/blobs/' if blob else '/manifests/')+digest, accept,
                                   **({'expected_size': descriptor['size']} if blob and descriptor is not None else {}))
        if len(response.body) > MAX_OBJECT:
            raise APIError('object-limit')
        actual = 'sha256:'+hashlib.sha256(response.body).hexdigest()
        if actual != digest:
            raise APIError('digest-mismatch')
        if response.content_digest is not None and response.content_digest != actual:
            raise APIError('header-digest-mismatch')
        if descriptor is not None and len(response.body) != descriptor['size']:
            raise APIError('descriptor-size-mismatch')
        document = _json(response.body)
        media = descriptor['mediaType'] if blob else response.media_type
        if not blob:
            if (type(document.get('schemaVersion')) is not int or document['schemaVersion'] != 2
                    or ('mediaType' in document and document['mediaType'] != response.media_type)):
                raise APIError('manifest-media-mismatch')
            if descriptor is not None and response.media_type != descriptor['mediaType']:
                raise APIError('descriptor-media-mismatch')
        elif response.media_type not in (media, 'application/json', 'application/octet-stream'):
            raise APIError('config-media-mismatch')
        object_receipt = {'role': role, 'digest': actual, 'media_type': media, 'size_bytes': len(response.body)}
        observations['objects'].append(object_receipt)
        if object_sink is not None:
            object_sink(response.body, object_receipt)
        return document, media

    selected, media = read_object(requested_digest, 'requested')
    platform_descriptor = None
    manifest_digest = requested_digest
    if media in INDEX_TYPES:
        observations['index_digest'] = requested_digest
        descriptors = selected.get('manifests')
        if not isinstance(descriptors, list) or not 1 <= len(descriptors) <= 256:
            raise APIError('invalid-index')
        matches = []
        for descriptor in descriptors:
            _descriptor(descriptor)
            platform = descriptor.get('platform')
            if not isinstance(platform, dict):
                raise APIError('unresolved-index-platform')
            if platform.get('os') != 'linux' or platform.get('architecture') != architecture:
                continue
            if variant is not None and platform.get('variant') != variant:
                continue
            if descriptor['mediaType'] not in MANIFEST_TYPES:
                raise APIError('unsupported-index-entry')
            if set(platform)-{'os', 'architecture', 'variant'}:
                raise APIError('unsupported-platform-constraints')
            if platform.get('variant') is not None and (not isinstance(platform['variant'], str)
                    or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,31}', platform['variant'])):
                raise APIError('invalid-platform')
            matches.append(descriptor)
        if len(matches) != 1:
            raise APIError('platform-unavailable' if not matches else 'platform-ambiguous')
        descriptor = matches[0]
        platform_descriptor = descriptor['platform']
        manifest_digest = descriptor['digest']
        selected, media = read_object(manifest_digest, 'platform-manifest', descriptor)
    if media not in MANIFEST_TYPES or selected.get('artifactType') or selected.get('subject'):
        raise APIError('unsupported-image-manifest')
    observations['platform_manifest_digest'] = manifest_digest
    config_descriptor = _descriptor(selected.get('config'))
    if config_descriptor['mediaType'] not in CONFIG_TYPES:
        raise APIError('unsupported-config-media')
    layers = selected.get('layers')
    if not isinstance(layers, list) or len(layers) > 256:
        raise APIError('invalid-layers')
    for layer in layers:
        _descriptor(layer)
    config, _ = read_object(config_descriptor['digest'], 'config', config_descriptor, blob=True)
    if config.get('os') != 'linux' or config.get('architecture') != architecture:
        raise APIError('platform-mismatch')
    observed_variant = config.get('variant')
    if observed_variant is not None and (not isinstance(observed_variant, str)
            or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,31}', observed_variant)):
        raise APIError('invalid-platform')
    if ((variant is not None and observed_variant != variant)
            or (platform_descriptor is not None and observed_variant is not None
                and observed_variant != platform_descriptor.get('variant'))):
        raise APIError('platform-mismatch')
    rootfs = config.get('rootfs')
    if (not isinstance(rootfs, dict) or rootfs.get('type') != 'layers'
            or not isinstance(rootfs.get('diff_ids'), list) or len(rootfs['diff_ids']) != len(layers)
            or any(not _digest(value) for value in rootfs['diff_ids'])):
        raise APIError('invalid-rootfs')
    observations['config_digest'] = config_descriptor['digest']
    observations['platform'] = {'os': 'linux', 'architecture': architecture, 'config_variant': observed_variant,
        'index_variant': platform_descriptor.get('variant') if platform_descriptor else None,
        'layer_count': len(layers)}
    observations['content_identity']['decision'] = 'pass'

def collect_oci(origin, *, repository, requested_digest, architecture, allowed_repositories,
                variant=None, token_env=None, basic_username_env=None, basic_password_env=None,
                token_path=None, token_service=None, ca_bundle=None, timeout=5.0,
                total_timeout=30.0, now=None, service_route=None,
                blob_download_policy=None, blob_download_policy_sha256=None):
    """Verify one requested sha256 image chain through scoped HTTPS GETs.

    Content equality is distinct from trusting how the requested digest was
    acquired, publisher signatures, attestations, or the contents of layers.
    Therefore this evidence envelope always retains required trust gaps.
    """
    if (not _repository(repository) or not _digest(requested_digest)
            or not isinstance(allowed_repositories, (list, tuple)) or not 1 <= len(allowed_repositories) <= 32
            or any(not _repository(item) for item in allowed_repositories)
            or len(set(allowed_repositories)) != len(allowed_repositories)
            or repository not in allowed_repositories
            or not isinstance(architecture, str) or architecture not in ARCHITECTURES
            or (variant is not None and (not isinstance(variant, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,31}', variant)))
            or (now is not None and (not isinstance(now, datetime) or now.tzinfo is None))):
        raise APIError('invalid-configuration')
    if ((token_path is None) != (token_service is None) or (token_path is not None and (
            not isinstance(token_path, str) or not re.fullmatch(r'/[A-Za-z0-9_./~-]{1,255}', token_path)
            or token_path.startswith('//') or any(part in ('.', '..') for part in token_path.split('/'))
            or not isinstance(token_service, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', token_service)))):
        raise APIError('invalid-configuration')
    client = _RegistryClient(origin, token_env=token_env, basic_username_env=basic_username_env,
        basic_password_env=basic_password_env, ca_bundle=ca_bundle, timeout=timeout,
        total_timeout=total_timeout, max_requests=6, max_body_bytes=MAX_OBJECT, max_total_bytes=8_000_000,
        **({} if service_route is None else {'service_route': service_route}))
    try:
        download_policy = load_blob_download_policy(blob_download_policy, blob_download_policy_sha256,
            registry_origin=client.origin, allowed_repositories=allowed_repositories)
    except RedirectPolicyError as error:
        raise APIError('blob-' + error.code) from None
    def clock():
        return now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)

    started = clock().isoformat()
    mode = 'bearer-env' if token_env is not None else 'basic-env' if basic_username_env is not None else 'none'
    observations = {'api_contract': {'distribution': '1.1.1', 'image': '1.1.1', 'docker': 'schema2'},
        'content_identity': {'decision': 'unknown'}, 'authentication': {'mode': mode, 'token_exchange': 'not-attempted'},
        'requested_digest': requested_digest, 'index_digest': None, 'platform_manifest_digest': None,
        'config_digest': None, 'objects': [], 'platform': None,
        'trust': {'requested_digest_origin': 'not-verified', 'signature': 'not-verified',
                  'attestation': 'not-verified', 'layer_content': 'not-read'}}
    gaps = [
        {'code': 'requested-digest-origin-unverified', 'message': 'The supplied digest has no authenticated acquisition receipt.', 'required': True},
        {'code': 'publisher-signature-unverified', 'message': 'Publisher signature trust has not been verified.', 'required': True},
        {'code': 'required-attestation-unverified', 'message': 'Required attestation subjects and trust have not been verified.', 'required': True},
    ]
    result = {'schema_version': 1, 'provider': 'oci', 'execution_status': 'partial', 'decision': 'unknown',
        'confirmed_failure': False, 'scope': {'origin': client.origin, 'repository': repository,
            'requested_digest': requested_digest, 'os': 'linux', 'architecture': architecture, 'variant': variant},
        'observed_at': started, 'observations': observations, 'gaps': gaps}
    session = _RegistrySession(client, repository=repository, token_path=token_path, token_service=token_service,
        blob_download_policy=download_policy)
    if download_policy is not None:
        observations['blob_download_policy'] = download_policy.identity_record()
    observations['authentication'] = session.authentication
    try:
        _collect_image_chain(session, requested_digest=requested_digest, architecture=architecture,
                             variant=variant, observations=observations)
    except APIError as error:
        observations['content_identity']['decision'] = 'fail' if error.code in {
            'digest-mismatch', 'header-digest-mismatch', 'descriptor-size-mismatch', 'platform-mismatch'} else 'unknown'
        gaps.append({'code': 'oci-'+error.code, 'message': 'Scoped OCI content resolution did not complete; no remote diagnostic is exported.',
                     'required': True})
    finally:
        client.session_token = None
        result['observed_at'] = clock().isoformat()
        observations['collection'] = {'started_at': started, 'finished_at': result['observed_at']}
        observations['receipts'] = client.receipts
    return result
