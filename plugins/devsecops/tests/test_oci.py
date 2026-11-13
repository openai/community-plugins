"""Real localhost TLS registry exchanges; no Registry1 qualification claim."""
import base64
from copy import deepcopy
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from devsecops.adapters.http import APIError, HTTPSReadClient
from devsecops.adapters.oci import (
    CONFIG, DOCKER_CONFIG, DOCKER_INDEX, DOCKER_MANIFEST, INDEX, MANIFEST, collect_oci,
)
import test_argocd as _tls

# Pytest discovers these fixtures locally; reuse the actual TLS server without
# duplicating its transport implementation.
ca, server = _tls.ca, _tls.server

REPOSITORY = 'ironbank/opensource/nginx/nginx'
SECRET = 'DO-NOT-EXPORT-REGISTRY-SECRET'
TOKEN = 'DO-NOT-EXPORT-ISSUED-TOKEN'
LAYER = 'sha256:'+'c'*64
DIFF_ID = 'sha256:'+'d'*64


def encode(document):
    return json.dumps(document, sort_keys=True, separators=(',', ':')).encode()


def digest(raw):
    return 'sha256:'+hashlib.sha256(raw).hexdigest()


def chain(*, config_change=None, manifest_change=None, index_change=None, docker=False):
    config = {'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': [DIFF_ID]},
              'config': {'Env': ['PASSWORD='+SECRET], 'Labels': {'private': SECRET}},
              'history': [{'created_by': SECRET}], 'author': SECRET}
    if config_change:
        config_change(config)
    config_raw = encode(config)
    config_type, manifest_type, index_type = (DOCKER_CONFIG, DOCKER_MANIFEST, DOCKER_INDEX) if docker else (CONFIG, MANIFEST, INDEX)
    manifest = {'schemaVersion': 2, 'mediaType': manifest_type,
        'config': {'mediaType': config_type, 'digest': digest(config_raw), 'size': len(config_raw)},
        'layers': [{'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': LAYER, 'size': 42}],
        'annotations': {'private': SECRET}}
    if manifest_change:
        manifest_change(manifest)
    manifest_raw = encode(manifest)
    index = {'schemaVersion': 2, 'mediaType': index_type, 'manifests': [
        {'mediaType': manifest_type, 'digest': 'sha256:'+'b'*64, 'size': 100,
         'platform': {'os': 'linux', 'architecture': 'arm64'}},
        {'mediaType': manifest_type, 'digest': digest(manifest_raw), 'size': len(manifest_raw),
         'platform': {'os': 'linux', 'architecture': 'amd64'}}]}
    if index_change:
        index_change(index)
    return {'index': encode(index), 'manifest': manifest_raw, 'config': config_raw,
            'index_type': index_type, 'manifest_type': manifest_type, 'config_type': config_type}


def response(raw, media):
    return {'raw': raw, 'content_type': media, 'headers': {'Docker-Content-Digest': digest(raw)}}


def enqueue(server, documents, *, direct=False):
    server['responses'] = ([] if direct else [response(documents['index'], documents['index_type'])]) + [
        response(documents['manifest'], documents['manifest_type']),
        response(documents['config'], 'application/octet-stream')]


def collect(server, documents, *, direct=False, **kwargs):
    return collect_oci(server['origin'], repository=REPOSITORY, allowed_repositories=[REPOSITORY],
        requested_digest=digest(documents['manifest' if direct else 'index']), architecture='amd64',
        ca_bundle=server['ca'], **kwargs)


def codes(result):
    return {gap['code'] for gap in result['gaps']}


@pytest.mark.parametrize('docker', [False, True])
@pytest.mark.parametrize('direct', [False, True])
def test_real_digest_chain_preserves_identity_levels_and_no_layer_reads(server, docker, direct):
    documents = chain(docker=docker)
    enqueue(server, documents, direct=direct)
    result = collect(server, documents, direct=direct)
    observed = result['observations']
    assert result['decision'] == 'unknown' and result['execution_status'] == 'partial'
    assert observed['content_identity']['decision'] == 'pass'
    assert observed['index_digest'] == (None if direct else digest(documents['index']))
    assert observed['platform_manifest_digest'] == digest(documents['manifest'])
    assert observed['config_digest'] == digest(documents['config'])
    assert len({digest(documents[key]) for key in ('index', 'manifest', 'config')}) == 3
    assert observed['platform'] == {'os': 'linux', 'architecture': 'amd64', 'config_variant': None,
                                     'index_variant': None, 'layer_count': 1}
    assert all(gap['required'] for gap in result['gaps'])
    assert codes(result) == {'requested-digest-origin-unverified', 'publisher-signature-unverified', 'required-attestation-unverified'}
    expected = ([] if direct else ['/v2/'+REPOSITORY+'/manifests/'+digest(documents['index'])]) + [
        '/v2/'+REPOSITORY+'/manifests/'+digest(documents['manifest']), '/v2/'+REPOSITORY+'/blobs/'+digest(documents['config'])]
    assert [request['path'] for request in server['requests']] == expected
    assert SECRET not in json.dumps(result) and LAYER not in json.dumps(result) and DIFF_ID not in json.dumps(result)


@pytest.mark.parametrize(('position', 'kind', 'code'), [
    (0, 'body', 'oci-digest-mismatch'), (1, 'body', 'oci-digest-mismatch'), (2, 'body', 'oci-digest-mismatch'),
    (0, 'header', 'oci-header-digest-mismatch'), (2, 'header', 'oci-header-digest-mismatch'),
    (0, 'type', 'oci-manifest-media-mismatch'), (2, 'type', 'oci-config-media-mismatch'),
])
def test_raw_body_header_and_media_mismatches_are_not_accepted(server, position, kind, code):
    documents = chain()
    enqueue(server, documents)
    item = server['responses'][position]
    if kind == 'body':
        item['raw'] += b' '
    elif kind == 'header':
        item['headers']['Docker-Content-Digest'] = 'sha256:'+'0'*64
    else:
        item['content_type'] = DOCKER_CONFIG if position == 2 else MANIFEST
    result = collect(server, documents)
    assert code in codes(result)
    assert result['observations']['content_identity']['decision'] != 'pass'
    assert len(server['requests']) == position+1
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize(('change', 'code'), [
    (lambda index: index['manifests'].append(deepcopy(index['manifests'][1])), 'oci-platform-ambiguous'),
    (lambda index: index['manifests'][1]['platform'].update(architecture='s390x'), 'oci-platform-unavailable'),
    (lambda index: index['manifests'][1]['platform'].update(os='windows'), 'oci-platform-unavailable'),
    (lambda index: index['manifests'][1].update(mediaType=INDEX), 'oci-unsupported-index-entry'),
    (lambda index: index['manifests'][1].pop('platform'), 'oci-unresolved-index-platform'),
    (lambda index: index['manifests'][1]['platform'].update({'os.features': ['special']}), 'oci-unsupported-platform-constraints'),
    (lambda index: index['manifests'][1].update(size=2_000_001), 'oci-object-limit'),
    (lambda index: index['manifests'][1].update(size=1), 'oci-descriptor-size-mismatch'),
])
def test_index_selection_and_descriptor_boundaries(server, change, code):
    documents = chain(index_change=change)
    enqueue(server, documents)
    result = collect(server, documents)
    assert code in codes(result)
    assert result['observations']['content_identity']['decision'] != 'pass'
    assert len(server['requests']) <= 2


@pytest.mark.parametrize(('change', 'code'), [
    (lambda config: config.update(architecture='arm64'), 'oci-platform-mismatch'),
    (lambda config: config.update(os='windows'), 'oci-platform-mismatch'),
    (lambda config: config.update(variant='v8'), 'oci-platform-mismatch'),
    (lambda config: config['rootfs'].update(diff_ids=[]), 'oci-invalid-rootfs'),
    (lambda config: config['rootfs'].update(type='unknown'), 'oci-invalid-rootfs'),
])
def test_authenticated_config_must_match_selected_platform_and_layer_count(server, change, code):
    documents = chain(config_change=change)
    enqueue(server, documents)
    result = collect(server, documents)
    assert code in codes(result)
    assert result['observations']['content_identity']['decision'] != 'pass'
    assert len(server['requests']) == 3


def test_config_descriptor_size_is_verified(server):
    documents = chain(manifest_change=lambda manifest: manifest['config'].update(size=1))
    enqueue(server, documents)
    result = collect(server, documents)
    assert 'oci-descriptor-size-mismatch' in codes(result)


@pytest.mark.parametrize(('change', 'code'), [
    (lambda manifest: manifest['config'].update(mediaType='application/vnd.unknown+json'), 'oci-unsupported-config-media'),
    (lambda manifest: manifest['layers'][0].update(mediaType='invalid type'), 'oci-invalid-descriptor'),
    (lambda manifest: manifest['config'].update(size=True), 'oci-invalid-descriptor'),
])
def test_unsupported_or_invalid_config_and_layer_descriptors_are_gaps(server, change, code):
    documents = chain(manifest_change=change)
    enqueue(server, documents)
    result = collect(server, documents)
    assert code in codes(result) and len(server['requests']) == 2


def challenge(server, **override):
    fields = {'realm': server['origin']+'/service/token', 'service': 'harbor-registry', 'scope': 'repository:'+REPOSITORY+':pull'}
    fields.update(override)
    return 'Bearer '+','.join(key+'="'+value+'"' for key, value in fields.items())


def credentials(monkeypatch):
    monkeypatch.setenv('OCI_TEST_USER', 'robot$demo')
    monkeypatch.setenv('OCI_TEST_PASSWORD', SECRET)
    return {'basic_username_env': 'OCI_TEST_USER', 'basic_password_env': 'OCI_TEST_PASSWORD'}


def test_basic_env_then_exact_same_origin_pull_token_exchange(server, monkeypatch):
    auth = credentials(monkeypatch)
    documents = chain()
    enqueue(server, documents)
    server['responses'][:0] = [{'status': 401, 'headers': {'WWW-Authenticate': challenge(server)}, 'json': {'secret': SECRET}},
                              {'json': {'token': TOKEN, 'access_token': TOKEN, 'expires_in': 60}}]
    result = collect(server, documents, **auth, token_path='/service/token', token_service='harbor-registry')
    assert result['observations']['content_identity']['decision'] == 'pass'
    assert result['observations']['authentication']['token_exchange'] == 'completed'
    basic = 'Basic '+base64.b64encode(('robot$demo:'+SECRET).encode()).decode()
    assert [item['authorization'] for item in server['requests']] == [basic, basic, 'Bearer '+TOKEN, 'Bearer '+TOKEN, 'Bearer '+TOKEN]
    token_request = urlsplit(server['requests'][1]['path'])
    assert token_request.path == '/service/token'
    assert parse_qs(token_request.query) == {'service': ['harbor-registry'], 'scope': ['repository:'+REPOSITORY+':pull']}
    assert all(value not in json.dumps(result) for value in (SECRET, TOKEN, basic))


@pytest.mark.parametrize(('change', 'code'), [
    ({'realm': 'https://unapproved.example/service/token'}, 'oci-auth-realm-mismatch'),
    ({'realm': 'http://127.0.0.1/service/token'}, 'oci-auth-realm-mismatch'),
    ({'realm': 'https://user:password@127.0.0.1/service/token'}, 'oci-auth-realm-mismatch'),
    ({'scope': 'repository:'+REPOSITORY+':pull,push'}, 'oci-auth-scope-mismatch'),
    ({'scope': 'repository:other/repository:pull'}, 'oci-auth-scope-mismatch'),
    ({'scope': 'repository:'+REPOSITORY+':pull repository:other:pull'}, 'oci-auth-scope-mismatch'),
    ({'service': 'unapproved-service'}, 'oci-auth-service-mismatch'),
])
def test_challenge_cannot_expand_origin_scope_or_audience(server, monkeypatch, change, code):
    documents = chain()
    server['responses'] = [{'status': 401, 'headers': {'WWW-Authenticate': challenge(server, **change)}}]
    result = collect(server, documents, **credentials(monkeypatch), token_path='/service/token', token_service='harbor-registry')
    assert code in codes(result) and len(server['requests']) == 1
    assert TOKEN not in json.dumps(result) and SECRET not in json.dumps(result)


@pytest.mark.parametrize('suffix', ['?scope=repository:other:pull', '#fragment', '/other', '/../other'])
def test_same_origin_challenge_cannot_change_configured_token_path(server, monkeypatch, suffix):
    documents = chain()
    server['responses'] = [{'status': 401, 'headers': {'WWW-Authenticate': challenge(server, realm=server['origin']+'/service/token'+suffix)}}]
    result = collect(server, documents, **credentials(monkeypatch), token_path='/service/token', token_service='harbor-registry')
    assert 'oci-auth-realm-mismatch' in codes(result) and len(server['requests']) == 1


@pytest.mark.parametrize('token_response', [
    {'token': TOKEN, 'access_token': 'different'}, {'token': 'line\nbreak'}, {'token': ''},
    {'access_token': TOKEN, 'expires_in': 0}, {'token': TOKEN, 'expires_in': True}, {},
])
def test_invalid_token_response_never_reaches_manifest_retry(server, monkeypatch, token_response):
    documents = chain()
    server['responses'] = [{'status': 401, 'headers': {'WWW-Authenticate': challenge(server)}}, {'json': token_response}]
    result = collect(server, documents, **credentials(monkeypatch), token_path='/service/token', token_service='harbor-registry')
    assert 'oci-invalid-auth-token' in codes(result) and len(server['requests']) == 2
    assert TOKEN not in json.dumps(result) and SECRET not in json.dumps(result)


def test_bearer_env_and_missing_auth_never_trigger_token_discovery(server, monkeypatch):
    documents = chain()
    monkeypatch.setenv('OCI_TEST_TOKEN', TOKEN)
    enqueue(server, documents, direct=True)
    result = collect(server, documents, direct=True, token_env='OCI_TEST_TOKEN')
    assert result['observations']['content_identity']['decision'] == 'pass'
    assert all(item['authorization'] == 'Bearer '+TOKEN for item in server['requests'])
    server['requests'].clear()
    server['responses'] = [{'status': 401, 'headers': {'WWW-Authenticate': challenge(server)}}]
    result = collect(server, documents)
    assert 'oci-authentication-required' in codes(result) and len(server['requests']) == 1


def test_repeated_challenge_is_not_retried(server, monkeypatch):
    documents = chain()
    denied = {'status': 401, 'headers': {'WWW-Authenticate': challenge(server)}}
    server['responses'] = [denied, {'json': {'token': TOKEN}}, denied]
    result = collect(server, documents, **credentials(monkeypatch), token_path='/service/token', token_service='harbor-registry')
    assert 'oci-denied' in codes(result) and len(server['requests']) == 3


@pytest.mark.parametrize('position', [0, 1, 2])
def test_registry_and_config_redirects_never_forward_credentials(server, monkeypatch, position):
    documents = chain()
    enqueue(server, documents)
    server['responses'][position] = {'status': 302, 'headers': {'Location': 'https://unapproved.example/'+SECRET}}
    result = collect(server, documents, **credentials(monkeypatch))
    assert 'oci-redirect' in codes(result) and len(server['requests']) == position+1
    assert SECRET not in json.dumps(result)


@pytest.mark.parametrize('change', [
    {'repository': '../other'}, {'repository': 'other/repository'}, {'requested_digest': 'latest'},
    {'requested_digest': 'sha256:'+'A'*64}, {'allowed_repositories': ['*']}, {'architecture': 'unknown'},
    {'token_path': '/service/token'}, {'token_path': '//other/token', 'token_service': 'harbor-registry'},
])
def test_invalid_or_unapproved_selection_is_rejected_before_network(server, change):
    options = {'repository': REPOSITORY, 'allowed_repositories': [REPOSITORY], 'requested_digest': 'sha256:'+'a'*64,
               'architecture': 'amd64', 'ca_bundle': server['ca']}
    options.update(change)
    with pytest.raises(APIError):
        collect_oci(server['origin'], **options)
    assert server['requests'] == []


def test_basic_credentials_are_references_and_fail_closed_before_network(server, monkeypatch):
    with pytest.raises(APIError):
        HTTPSReadClient(server['origin'], basic_username_env='OCI_TEST_USER')
    with pytest.raises(APIError):
        HTTPSReadClient(server['origin'], token_env='OCI_TEST_TOKEN', **credentials(monkeypatch))
    monkeypatch.setenv('OCI_TEST_USER', 'invalid:username')
    client = HTTPSReadClient(server['origin'], ca_bundle=server['ca'], basic_username_env='OCI_TEST_USER', basic_password_env='OCI_TEST_PASSWORD')
    with pytest.raises(APIError, match='credential-unavailable'):
        client.get_bytes('/v2/')
    assert server['requests'] == []


def test_raw_transport_hides_challenge_repr_and_preserves_bounds(server):
    server['responses'] = [{'status': 401, 'headers': {'WWW-Authenticate': 'Bearer '+SECRET}, 'raw': SECRET.encode()}]
    client = HTTPSReadClient(server['origin'], ca_bundle=server['ca'], max_requests=1)
    result = client.get_bytes('/v2/', allow_unauthorized=True)
    assert result.status == 401 and result.body == b''
    assert SECRET not in repr(result) and SECRET not in json.dumps(client.receipts)
    with pytest.raises(APIError, match='request-limit'):
        client.get_bytes('/v2/')


@pytest.mark.parametrize(('response_override', 'code'), [
    ({'length': 2_000_001}, 'oci-body-limit'),
    ({'headers': {'Content-Encoding': 'gzip'}}, 'oci-unsupported-encoding'),
    ({'headers': {'Content-Type': INDEX}}, 'oci-invalid-response'),
    ({'headers': {'Docker-Content-Digest': 'sha256:'+'a'*64, 'docker-content-digest': 'sha256:'+'a'*64}}, 'oci-invalid-response'),
])
def test_raw_content_transport_keeps_limits_and_unambiguous_headers(server, response_override, code):
    documents = chain()
    enqueue(server, documents)
    server['responses'][0].update(response_override)
    result = collect(server, documents)
    assert code in codes(result) and len(server['requests']) == 1


def test_token_redirect_is_not_followed_and_token_exchange_is_not_discovered(server, monkeypatch):
    documents = chain()
    auth = credentials(monkeypatch)
    denied = {'status': 401, 'headers': {'WWW-Authenticate': challenge(server)}}
    server['responses'] = [denied]
    result = collect(server, documents, **auth)
    assert 'oci-auth-challenge-unconfigured' in codes(result) and len(server['requests']) == 1
    server['requests'].clear()
    server['responses'] = [denied, {'status': 302, 'headers': {'Location': 'https://unapproved.example/token'}}]
    result = collect(server, documents, **auth, token_path='/service/token', token_service='harbor-registry')
    assert 'oci-redirect' in codes(result) and len(server['requests']) == 2


@pytest.mark.parametrize('modify', [
    lambda value: value+',', lambda value: value+',scope="repository:other:pull"',
    lambda value: value.replace('Bearer ', 'Basic '), lambda value: value+',offline_token="true"',
])
def test_challenge_grammar_rejects_ambiguity_and_permission_extensions(server, monkeypatch, modify):
    documents = chain()
    server['responses'] = [{'status': 401, 'headers': {'WWW-Authenticate': modify(challenge(server))}}]
    result = collect(server, documents, **credentials(monkeypatch), token_path='/service/token', token_service='harbor-registry')
    assert result['observations']['content_identity']['decision'] == 'unknown'
    assert len(server['requests']) == 1


def test_digest_valid_duplicate_json_is_still_rejected(server):
    documents = chain()
    documents['index'] = documents['index'].replace(b'"schemaVersion":2', b'"schemaVersion":2,"schemaVersion":2')
    enqueue(server, documents)
    result = collect(server, documents)
    assert 'oci-invalid-json' in codes(result) and len(server['requests']) == 1


def test_alternate_descriptor_urls_are_never_followed_or_exported(server):
    documents = chain(manifest_change=lambda manifest: manifest['config'].update(urls=['https://unapproved.example/'+SECRET]))
    enqueue(server, documents)
    result = collect(server, documents)
    assert result['observations']['content_identity']['decision'] == 'pass'
    assert len(server['requests']) == 3 and SECRET not in json.dumps(result)


def test_explicit_variant_requires_config_agreement(server):
    documents = chain(config_change=lambda config: config.update(variant='v3'),
                      index_change=lambda index: index['manifests'][1]['platform'].update(variant='v3'))
    enqueue(server, documents)
    result = collect(server, documents, variant='v3')
    assert result['observations']['content_identity']['decision'] == 'pass'
    assert result['observations']['platform']['config_variant'] == 'v3'


def test_raw_transport_does_not_accept_header_or_url_injection(server):
    client = HTTPSReadClient(server['origin'], ca_bundle=server['ca'])
    for path in ('https://other.example/v2/', '//other.example/v2/', '/v2/../private', '/v2/%2e%2e/private'):
        with pytest.raises(APIError):
            client.get_bytes(path)
    with pytest.raises(APIError):
        client.get_bytes('/v2/', accept=['application/json\r\nAuthorization: injected'])
    assert server['requests'] == []
