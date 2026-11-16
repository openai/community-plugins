"""Real authenticated local TLS registry; candidate storage, not P1 acceptance."""
import base64
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from test_argocd import ca
from test_oci import chain, digest, encode, REPOSITORY, SECRET
from devsecops.adapters import registry_evidence as adapter
from devsecops.adapters.http import APIError
from devsecops.adapters.oci import CONFIG, MANIFEST
from devsecops.runtime.context import verify_seal

__all__ = ['ca']
TOKEN = 'private-operator-read-token'
PREDICATES = ['https://example.test/sbom/v1', 'https://example.test/hardening/v1']


@pytest.fixture
def registry(ca, monkeypatch):
    state = {'requests': [], 'routes': {}, 'mode': 'bearer', 'challenge': None}
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *_):
            pass
        def do_GET(self):
            auth = self.headers.get('Authorization')
            state['requests'].append({'path': self.path, 'authorization': auth})
            if self.path.startswith('/service/token') and state['mode'] == 'basic-exchange':
                expected = 'Basic ' + base64.b64encode(('operator:' + SECRET).encode()).decode()
                if auth == expected:
                    response = {'raw': encode({'token': TOKEN, 'expires_in': 60}), 'media': 'application/json'}
                else:
                    response = {'status': 403}
            elif auth != 'Bearer ' + TOKEN:
                if state['mode'] == 'basic-exchange':
                    challenge = state['challenge'] or ('Bearer realm="' + state['origin'] + '/service/token",'
                        'service="registry",scope="repository:' + REPOSITORY + ':pull"')
                    response = {'status': 401, 'headers': {'WWW-Authenticate': challenge}}
                else:
                    response = {'status': 403}
            else:
                response = state['routes'].get(self.path, {'status': 404})
            try:
                if response.get('delay'):
                    time.sleep(response['delay'])
                raw = response.get('raw', b'{"private":"never-export-remote-diagnostics"}')
                self.send_response(response.get('status', 200))
                self.send_header('Content-Type', response.get('media', 'application/json'))
                self.send_header('Content-Length', str(response.get('size', len(raw))))
                for name, value in response.get('headers', {}).items():
                    self.send_header(name, value)
                self.end_headers()
                if response.get('trickle'):
                    for byte in raw:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(response['trickle'])
                else:
                    self.wfile.write(raw)
                    self.wfile.flush()
            except (OSError, ssl.SSLError):
                pass
    class QuietServer(ThreadingHTTPServer):
        daemon_threads = True
        def handle_error(self, *_):
            pass
    server = QuietServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(ca[0]), str(ca[1]))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    state.update(origin=f'https://127.0.0.1:{server.server_port}', ca=ca[0])
    monkeypatch.setenv('REGISTRY_TEST_TOKEN', TOKEN)
    monkeypatch.setenv('REGISTRY_TEST_USER', 'operator')
    monkeypatch.setenv('REGISTRY_TEST_PASSWORD', SECRET)
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def install(state, *, mutate_signature=None, mutate_attestation=None, predicates=None, signatures=1):
    image = chain()
    requested = digest(image['index'])
    repository = state['origin'].removeprefix('https://') + '/' + REPOSITORY
    routes = state['routes']
    def put(raw, media, *, blob=True, tag=None):
        item = {'raw': raw, 'media': media, 'headers': {'Docker-Content-Digest': digest(raw)}}
        path = '/v2/' + REPOSITORY + ('/blobs/' if blob else '/manifests/')
        routes[path + digest(raw)] = item
        if tag:
            routes[path + tag] = deepcopy(item)
        return {'mediaType': media, 'digest': digest(raw), 'size': len(raw)}
    for key, media in (('index', image['index_type']), ('manifest', image['manifest_type']), ('config', image['config_type'])):
        put(image[key], media, blob=key == 'config')
    config = put(b'{}', CONFIG)
    simple = {'critical': {'identity': {'docker-reference': repository},
        'image': {'docker-manifest-digest': requested}, 'type': 'cosign container image signature'},
        'optional': {'private': SECRET}}
    signature_layers = []
    for index in range(signatures):
        payload = encode(simple | {'optional': {'private': SECRET, 'index': index}})
        descriptor = put(payload, adapter.SIGNATURE_MEDIA)
        descriptor['annotations'] = {adapter.SIGNATURE_ANNOTATION: base64.b64encode(bytes([index]) * 64).decode(),
                                     'private': SECRET}
        signature_layers.append(descriptor)
    sig = {'schemaVersion': 2, 'mediaType': MANIFEST, 'config': config, 'layers': signature_layers}
    if mutate_signature:
        mutate_signature(sig)
    sig_ref = put(encode(sig), MANIFEST, blob=False, tag='sha256-' + requested[7:] + '.sig')
    att_layers = []
    for predicate in PREDICATES if predicates is None else predicates:
        statement = {'_type': 'https://in-toto.io/Statement/v1', 'subject': [{'name': repository,
            'digest': {'sha256': requested[7:]}}], 'predicateType': predicate, 'predicate': {'private': SECRET}}
        envelope = {'payloadType': 'application/vnd.in-toto+json',
                    'payload': base64.b64encode(encode(statement)).decode(),
                    'signatures': [{'keyid': '', 'sig': base64.b64encode(b'a' * 64).decode()}]}
        att_layers.append(put(encode(envelope), adapter.ATTESTATION_MEDIA))
    att = {'schemaVersion': 2, 'mediaType': MANIFEST, 'config': config, 'layers': att_layers}
    if mutate_attestation:
        mutate_attestation(att)
    att_ref = put(encode(att), MANIFEST, blob=False, tag='sha256-' + requested[7:] + '.att')
    return {'image': image, 'requested': requested, 'sig': sig, 'att': att,
            'sig_ref': sig_ref, 'att_ref': att_ref}


def collect(registry, fixture, output, **overrides):
    args = dict(repository=REPOSITORY, requested_digest=fixture['requested'], architecture='amd64',
        allowed_repositories=[REPOSITORY], required_predicates=PREDICATES, output=output.resolve(),
        token_env='REGISTRY_TEST_TOKEN', ca_bundle=registry['ca'])
    args.update(overrides)
    return adapter.collect_registry_evidence(registry['origin'], **args)


def codes(result):
    return {g['code'] for g in result['gaps']}


def test_direct_authenticated_tls_preserves_all_exact_objects_privately(registry, tmp_path):
    data = install(registry)
    output = tmp_path / 'private acquisition with spaces'
    result = collect(registry, data, output)
    obs = result['observations']
    assert obs['acquisition']['decision'] == 'pass'
    assert result['decision'] == 'unknown' and result['execution_status'] == 'partial'
    assert obs['image']['platform_manifest_digest'] == digest(data['image']['manifest'])
    assert obs['image']['config_digest'] == digest(data['image']['config'])
    assert len(obs['signatures']) == 1 and len(obs['attestations']) == 2
    assert all(r['selection'] == 'unique-unverified' for r in obs['required_predicates'])
    assert all(r['authorization'] == 'Bearer ' + TOKEN for r in registry['requests'])
    assert len(registry['requests']) == 12
    assert all('referrers' not in r['path'] and 'latest' not in r['path'] for r in registry['requests'])
    verify_seal(result, 'receipt_sha256')
    manifest = json.loads((output / 'registry-manifest.json').read_bytes())
    receipt = output / manifest['receipt']['path']
    assert hashlib.sha256(receipt.read_bytes()).hexdigest() == manifest['receipt']['sha256']
    assert json.loads(receipt.read_bytes()) == result
    for filename, item in result['artifacts'].items():
        raw = (output / filename).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item['sha256']
        assert len(raw) == item['bytes'] and (output / filename).stat().st_mode & 0o077 == 0
    assert output.stat().st_mode & 0o077 == 0
    serialized = json.dumps(result)
    assert SECRET not in serialized and TOKEN not in serialized and str(output) not in serialized
    assert any(SECRET.encode() in p.read_bytes() for p in output.glob('*.blob'))


def test_one_shared_scoped_basic_to_bearer_exchange(registry, tmp_path):
    data = install(registry)
    registry['mode'] = 'basic-exchange'
    result = collect(registry, data, tmp_path / 'out', token_env=None,
        basic_username_env='REGISTRY_TEST_USER', basic_password_env='REGISTRY_TEST_PASSWORD',
        token_path='/service/token', token_service='registry')
    assert result['observations']['acquisition']['decision'] == 'pass'
    assert result['observations']['authentication']['token_exchange'] == 'completed'
    assert len(registry['requests']) == 14
    assert sum(r['path'].startswith('/service/token') for r in registry['requests']) == 1
    params = parse_qs(urlsplit(registry['requests'][1]['path']).query)
    assert params == {'service': ['registry'], 'scope': ['repository:' + REPOSITORY + ':pull']}
    assert all(r['authorization'] == 'Bearer ' + TOKEN for r in registry['requests'][2:])


@pytest.mark.parametrize('damage', ['missing-signature', 'missing-predicate', 'duplicate-predicate', 'multiple-signatures'])
def test_missing_or_ambiguous_candidates_remain_explicit(registry, tmp_path, damage):
    data = install(registry, predicates=[PREDICATES[0]] if damage == 'missing-predicate' else
        [*PREDICATES, PREDICATES[0]] if damage == 'duplicate-predicate' else None,
        signatures=2 if damage == 'multiple-signatures' else 1)
    if damage == 'missing-signature':
        del registry['routes']['/v2/' + REPOSITORY + '/manifests/sha256-' + data['requested'][7:] + '.sig']
    result = collect(registry, data, tmp_path / 'out')
    assert result['observations']['acquisition']['decision'] == 'unknown'
    assert result['decision'] == 'unknown'
    expected = {'missing-signature': 'registry-image-signature-missing',
        'missing-predicate': 'registry-required-predicate-missing',
        'duplicate-predicate': 'registry-required-predicate-ambiguous',
        'multiple-signatures': 'registry-image-signature-candidates-ambiguous'}[damage]
    assert expected in codes(result)
    if damage == 'duplicate-predicate':
        assert len(result['observations']['attestations']) == 3
        assert result['observations']['required_predicates'][0]['candidate_count'] == 2


@pytest.mark.parametrize('damage', ['descriptor-size', 'descriptor-too-large', 'wrong-media', 'invalid-envelope'])
def test_attachment_descriptors_and_envelopes_are_checked(registry, tmp_path, damage):
    def mutate(att):
        if damage == 'descriptor-size':
            att['layers'][0]['size'] = 1
        elif damage == 'descriptor-too-large':
            att['layers'][0]['size'] = adapter.MAX_ENVELOPE + 1
        elif damage == 'wrong-media':
            att['layers'][0]['mediaType'] = 'application/vnd.unapproved+json'
    data = install(registry, mutate_attestation=mutate)
    if damage == 'invalid-envelope':
        descriptor = data['att']['layers'][0]
        route = registry['routes']['/v2/' + REPOSITORY + '/blobs/' + descriptor['digest']]
        route['raw'] += b' '
    result = collect(registry, data, tmp_path / 'out')
    expected = {'descriptor-size': 'registry-descriptor-size-mismatch', 'descriptor-too-large': 'registry-object-limit',
                'wrong-media': 'registry-attachment-layer-media-unsupported', 'invalid-envelope': 'registry-digest-mismatch'}[damage]
    assert expected in codes(result)
    assert result['observations']['acquisition']['decision'] != 'pass'


@pytest.mark.parametrize('damage', ['denied', 'redirect', 'oversized', 'malformed', 'digest-change'])
def test_discovery_errors_never_become_success_or_follow_redirects(registry, tmp_path, damage):
    data = install(registry)
    route = '/v2/' + REPOSITORY + '/manifests/sha256-' + data['requested'][7:] + '.sig'
    response = registry['routes'][route]
    if damage == 'denied':
        response['status'] = 403
    elif damage == 'redirect':
        response.update(status=302, headers={'Location': 'https://unapproved.example/' + SECRET})
    elif damage == 'oversized':
        response['size'] = adapter.MAX_ENVELOPE + 1
    elif damage == 'malformed':
        response['raw'] = b'{"schemaVersion":2,"schemaVersion":2}'
        response['headers']['Docker-Content-Digest'] = digest(response['raw'])
        registry['routes']['/v2/' + REPOSITORY + '/manifests/' + digest(response['raw'])] = deepcopy(response)
    else:
        registry['routes']['/v2/' + REPOSITORY + '/manifests/' + data['sig_ref']['digest']]['raw'] += b' '
    result = collect(registry, data, tmp_path / 'out')
    assert result['observations']['acquisition']['decision'] != 'pass'
    if damage in {'denied', 'redirect', 'oversized'}:
        assert len(registry['requests']) == 4
    assert all(SECRET not in g.get('message', '') for g in result['gaps'])


@pytest.mark.parametrize('mode', ['header', 'body'])
def test_total_deadline_includes_artifact_headers_and_bodies(registry, tmp_path, mode):
    data = install(registry)
    route = '/v2/' + REPOSITORY + '/manifests/sha256-' + data['requested'][7:] + '.sig'
    registry['routes'][route]['delay' if mode == 'header' else 'trickle'] = .5 if mode == 'header' else .03
    start = time.monotonic()
    result = collect(registry, data, tmp_path / 'out', timeout=.15, total_timeout=.3)
    assert time.monotonic() - start < 1
    assert 'registry-deadline' in codes(result) and len(registry['requests']) == 4


@pytest.mark.parametrize('budget', ['requests', 'bytes'])
def test_image_and_all_attachments_share_one_budget(registry, tmp_path, budget):
    data = install(registry)
    options = {'max_requests': 6} if budget == 'requests' else {'max_total_bytes': sum(len(data['image'][k]) for k in ('index', 'manifest', 'config')) + 5}
    result = collect(registry, data, tmp_path / 'out', **options)
    assert ('registry-request-limit' if budget == 'requests' else 'registry-body-limit') in codes(result)
    assert result['observations']['acquisition']['decision'] == 'unknown'
    assert len(registry['requests']) <= 6


@pytest.mark.parametrize('challenge', ['origin', 'scope', 'service'])
def test_token_challenge_cannot_expand_origin_repository_or_permission(registry, tmp_path, challenge):
    data = install(registry)
    registry['mode'] = 'basic-exchange'
    realm = 'https://unapproved.example/token' if challenge == 'origin' else registry['origin'] + '/service/token'
    scope = 'repository:' + REPOSITORY + (':pull,push' if challenge == 'scope' else ':pull')
    registry['challenge'] = f'Bearer realm="{realm}",service="' + ('other' if challenge == 'service' else 'registry') + f'",scope="{scope}"'
    result = collect(registry, data, tmp_path / 'out', token_env=None,
        basic_username_env='REGISTRY_TEST_USER', basic_password_env='REGISTRY_TEST_PASSWORD',
        token_path='/service/token', token_service='registry')
    assert result['observations']['acquisition']['decision'] == 'unknown'
    assert len(registry['requests']) == 1


def test_descriptor_metadata_urls_are_inert(registry, tmp_path):
    def mutate(manifest):
        manifest['config']['urls'] = ['https://unapproved.example/' + SECRET]
        manifest['layers'][0]['urls'] = ['https://unapproved.example/' + SECRET]
    data = install(registry, mutate_signature=mutate, mutate_attestation=mutate)
    result = collect(registry, data, tmp_path / 'out')
    assert result['observations']['acquisition']['decision'] == 'pass'
    assert len(registry['requests']) == 12 and SECRET not in json.dumps(result)


@pytest.mark.parametrize('damage', ['anonymous', 'empty-token', 'unapproved-repo', 'extra-predicate', 'existing-output'])
def test_invalid_authority_or_output_fails_before_network(registry, tmp_path, monkeypatch, damage):
    data = install(registry)
    output = tmp_path / 'out'
    overrides = {}
    if damage == 'anonymous':
        overrides['token_env'] = None
    elif damage == 'empty-token':
        monkeypatch.delenv('REGISTRY_TEST_TOKEN')
    elif damage == 'unapproved-repo':
        overrides['allowed_repositories'] = ['other/repo']
    elif damage == 'extra-predicate':
        overrides['required_predicates'] = [PREDICATES[0], PREDICATES[0]]
    else:
        output.mkdir()
    with pytest.raises((APIError, ValueError)):
        collect(registry, data, output, **overrides)
    assert registry['requests'] == []


def test_output_path_swap_never_redirects_private_registry_bodies(registry, tmp_path, monkeypatch):
    data = install(registry)
    output, retained, decoy = (tmp_path / n for n in ('out', 'retained', 'decoy'))
    decoy.mkdir()
    original = adapter._collect_image_chain
    def swap(session, **kwargs):
        output.rename(retained)
        output.symlink_to(decoy, target_is_directory=True)
        return original(session, **kwargs)
    monkeypatch.setattr(adapter, '_collect_image_chain', swap)
    with pytest.raises((OSError, ValueError)):
        collect(registry, data, output)
    assert not list(decoy.iterdir())
    assert list(retained.glob('*.blob'))
    assert not (retained / 'registry-manifest.json').exists()


@pytest.mark.parametrize('damage', ['missing', 'non-object', 'invalid-base64', 'oversized-payload'])
def test_signature_storage_incomplete_cannot_cover_read_artifact_bytes(registry, tmp_path, damage):
    def mutate(manifest):
        layer = manifest['layers'][0]
        if damage == 'missing':
            layer['annotations'].pop(adapter.SIGNATURE_ANNOTATION)
        elif damage == 'non-object':
            layer['annotations'] = []
        elif damage == 'invalid-base64':
            layer['annotations'][adapter.SIGNATURE_ANNOTATION] = 'bad encoding'
        else:
            layer['size'] = adapter.MAX_SIGNATURE_PAYLOAD + 1
    data = install(registry, mutate_signature=mutate)
    result = collect(registry, data, tmp_path / 'out')
    assert result['observations']['acquisition']['decision'] == 'unknown'
    assert ('registry-signature-payload-limit' if damage == 'oversized-payload'
            else 'registry-signature-storage-unsupported') in codes(result)
    assert len(result['observations']['attestations']) == 2


def test_wrong_read_credential_is_denied_without_anonymous_retry(registry, tmp_path, monkeypatch):
    data = install(registry)
    monkeypatch.setenv('REGISTRY_TEST_TOKEN', 'incorrect-private-token')
    result = collect(registry, data, tmp_path / 'out')
    assert 'registry-denied' in codes(result)
    assert len(registry['requests']) == 1
    assert 'incorrect-private-token' not in json.dumps(result)


def test_scoped_session_rejects_nested_or_foreign_registry_routes(registry):
    client = adapter._RegistryClient(registry['origin'], token_env='REGISTRY_TEST_TOKEN', ca_bundle=registry['ca'])
    session = adapter._RegistrySession(client, repository=REPOSITORY)
    for path in ('/v2/other/repo/manifests/' + 'sha256:' + 'a' * 64,
                 '/v2/' + REPOSITORY + '/manifests/other/' + 'sha256:' + 'a' * 64,
                 '/v2/' + REPOSITORY + '/blobs/sha256-' + 'a' * 64 + '.sig',
                 '/v2/' + REPOSITORY + '/manifests/latest'):
        with pytest.raises(APIError, match='registry-route-outside-scope'):
            session.request(path, adapter.MANIFEST_ACCEPT)
    assert registry['requests'] == []
