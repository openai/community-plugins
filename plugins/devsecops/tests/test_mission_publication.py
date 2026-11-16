"""Actual Cosign + local TLS OCI artifact publication on controlled evidence."""
from copy import deepcopy
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pickle
import socket
import ssl
import tarfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from devsecops.adapters import mission_evidence as evidence
from devsecops.adapters.oci import INDEX, MANIFEST
from devsecops.factory import mission_publication as publication
from devsecops.factory import mission_signing as signing
from devsecops.runtime.context import verify_seal
from test_argocd import ca
from test_mission_signing import case as signing_case, scanned, encode, sha, update
from test_signatures import lab

__all__ = ['ca', 'scanned', 'lab']
REPOSITORY = 'demo/evidence'
TOKEN = 'CONTROLLED-PUBLICATION-TOKEN'


@pytest.fixture
def registry(ca, monkeypatch):
    monkeypatch.setenv('PUBLICATION_TEST_TOKEN', TOKEN)
    monkeypatch.setenv('PUBLICATION_TEST_USER', 'fixture')
    monkeypatch.setenv('PUBLICATION_TEST_PASSWORD', 'fixture-password')
    state = {'requests': [], 'blobs': {}, 'manifests': {}, 'fault': None, 'count': 0, 'referrers': [], 'hook': None}
    prefix = '/v2/' + REPOSITORY
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def respond(self, status, raw=b'', media='application/octet-stream', **headers):
            self.send_response(status)
            self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(raw)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(raw)
                self.wfile.flush()

        def authorized(self):
            state['requests'].append({'method': self.command, 'path': self.path, 'authorization': self.headers.get('Authorization')})
            if self.headers.get('Authorization') == 'Bearer ' + TOKEN:
                return True
            realm = state['origin'] + '/service/token'
            if state['fault'] == 'auth-outside':
                realm = 'https://outside.invalid/service/token'
            self.respond(401, **{'WWW-Authenticate': f'Bearer realm="{realm}",service="fixture",scope="repository:{REPOSITORY}:pull"'})
            return False

        def do_HEAD(self):
            if self.authorized():
                self.respond(404)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == '/service/token':
                state['requests'].append({'method': 'GET', 'path': self.path, 'authorization': self.headers.get('Authorization')})
                self.respond(200, encode({'token': TOKEN, 'expires_in': 300}), 'application/json')
                return
            if not self.authorized():
                return
            if state['hook']:
                state['hook'](self.path)
            if parsed.path.startswith(prefix + '/referrers/'):
                assert parse_qs(parsed.query) == {'artifactType': [evidence.ARTIFACT_TYPE]}
                if state['fault'] == 'no-referrers':
                    self.respond(404)
                    return
                if state['fault'] == 'referrers-redirect':
                    self.respond(307, Location='https://outside.invalid/discovery')
                    return
                found = deepcopy(state['referrers'])
                if state['fault'] == 'missing-discovery':
                    found = []
                elif state['fault'] == 'conflicting-discovery' and found:
                    found[0]['size'] += 1
                elif state['fault'] == 'duplicate-discovery' and found:
                    found.append(found[0])
                self.respond(200, encode({'schemaVersion': 2, 'mediaType': INDEX, 'manifests': found}), INDEX,
                    Link='<https://outside.invalid/next>; rel="next"')
                return
            if parsed.path.startswith(prefix + '/manifests/'):
                key = parsed.path.removeprefix(prefix + '/manifests/')
                raw, media = state['manifests'].get(key), MANIFEST
            else:
                key = parsed.path.removeprefix(prefix + '/blobs/')
                raw, media = state['blobs'].get(key), 'application/octet-stream'
                if raw is not None and state['fault'] == 'corrupt-blob':
                    raw = bytes([raw[0] ^ 1]) + raw[1:]
                if state['fault'] == 'blob-redirect':
                    self.respond(307, Location='https://outside.invalid/blob')
                    return
            self.respond(404 if raw is None else 200, raw or b'', media, **{'Docker-Content-Digest': key})

        def do_POST(self):
            if not self.authorized():
                return
            assert self.path == prefix + '/blobs/uploads/'
            state['count'] += 1
            location = prefix + '/blobs/uploads/session-' + str(state['count'])
            if state['fault'] == 'external-location':
                location = 'https://outside.invalid' + location
            self.respond(202, Location=location)

        def do_PUT(self):
            if not self.authorized():
                return
            raw = self.rfile.read(int(self.headers['Content-Length']))
            parsed = urlsplit(self.path)
            if parsed.path.startswith(prefix + '/blobs/uploads/'):
                key = parse_qs(parsed.query)['digest'][0]
                assert key == 'sha256:' + sha(raw)
                state['blobs'][key] = raw
                location = prefix + '/blobs/' + key
            else:
                key = parsed.path.removeprefix(prefix + '/manifests/')
                assert key == 'sha256:' + sha(raw)
                state['manifests'][key] = raw
                doc = json.loads(raw)
                state['referrers'].append({**evidence.descriptor(raw, MANIFEST), 'artifactType': doc['artifactType']})
                location = prefix + '/manifests/' + key
            if state['fault'] == 'put-disconnect':
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                self.close_connection = True
                return
            self.respond(201, Location=location, **{'Docker-Content-Digest': key})

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, *_):
            pass
    httpd = Server(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(ca[0]), str(ca[1]))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    state.update(origin='https://127.0.0.1:' + str(httpd.server_port), ca=ca[0])
    yield state
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


@pytest.fixture
def case(scanned, lab, tmp_path, monkeypatch, registry):
    selected = signing_case.__wrapped__(scanned, lab, tmp_path, monkeypatch)
    for obj in (selected['args'], selected['binding'], selected['trust']):
        obj.update(origin=registry['origin'], repository=REPOSITORY)
    update(selected, trust=True)
    signed_report, cap = signing.sign_assessed_mission(**selected['args'])
    assert cap is None and signed_report['checks']['verify'] == 'pass'
    output = selected['args']['output']
    retained = signed_report['artifacts']
    source = {**deepcopy(selected['binding']), 'archive_path': str(output / 'image.oci.tar'),
        'sbom': {'path': str(output / 'sbom.spdx.json'), **retained['sbom.spdx.json']},
        'vulnerabilities': {'path': str(output / 'grype.json'), **retained['grype.json']},
        'trust_policy_sha256': retained['signing-trust.json']['sha256'], 'signer': signed_report['signer'],
        'signature_payload': {'path': str(output / 'payload.json'), **retained['payload.json']},
        'signature_bundle': {'path': str(output / 'bundle.json'), **retained['bundle.json']},
        'signature_verification_sha256': retained['signature-verification.json']['sha256'],
        'signing_tool': signed_report['tool'], 'scope': 'local-signed-artifacts-only',
        'expires_at': json.loads((output / 'payload.json').read_bytes())['optional']['devsecops']['expires_at'],
        'execution_mode': 'controlled-test'}
    authority = object.__new__(signing.LocallySignedMission)
    signing._SIGNED[authority] = {'pid': os.getpid(), 'expires_monotonic': time.monotonic()+600, 'binding': source}
    with tarfile.open(output / 'image.oci.tar') as archive:
        registry['manifests'][source['manifest_digest']] = archive.extractfile('blobs/sha256/' + source['manifest_digest'][7:]).read()
    config = {'schema_version': 1, 'kind': 'mission-evidence-publication-configuration',
        'origin': registry['origin'], 'repository': REPOSITORY, 'credentials': {'token_env': 'PUBLICATION_TEST_TOKEN'},
        'ca_bundle': str(registry['ca']), 'trust_policy_path': selected['config']['trust_policy_path'],
        'trust_policy_sha256': source['trust_policy_sha256'], 'public_key_path': selected['config']['public_key_path'],
        'public_key_sha256': selected['trust']['public_key_sha256'], 'cosign_path': selected['config']['cosign_path']}
    config_path = tmp_path / 'publication.json'
    config_path.write_bytes(encode(config))
    args = {'signed': authority, **{key: source[key] for key in publication._SCOPE_FIELDS},
        'config': config_path, 'expected_config_sha256': sha(config_path.read_bytes()), 'output': tmp_path / 'published'}
    return {'args': args, 'binding': source, 'config': config, 'trust': selected['trust'], 'registry': registry}


def update_config(case):
    case['args']['config'].write_bytes(encode(case['config']))
    case['args']['expected_config_sha256'] = sha(case['args']['config'].read_bytes())


def test_real_tls_immutable_artifact_readback_and_cosign_verification(case):
    report, cap = publication.publish_signed_mission(**case['args'])
    assert cap is None and report['decision'] == 'unknown' and report['factory_decision'] == 'unknown'
    assert report['gaps'] == ['controlled-evidence-cannot-issue-publication-authority']
    assert report['checks'] == {**dict.fromkeys(report['checks'], 'pass'), 'trust': 'unknown'}
    verify_seal(report, 'publication_sha256')
    registry, output = case['registry'], case['args']['output']
    manifest = json.loads(registry['manifests'][report['artifact']['digest']])
    assert manifest['subject']['digest'] == case['binding']['manifest_digest']
    assert manifest['artifactType'] == evidence.ARTIFACT_TYPE
    assert manifest['config'] == evidence.descriptor(b'{}', evidence.EMPTY_TYPE)
    assert len(manifest['layers']) == 7 and len(registry['blobs']) == 8
    for name, _, _ in evidence.FILES:
        assert (output / name).read_bytes() == (output / ('readback-' + name)).read_bytes()
    assert all(item['state'] == 'content-confirmed-by-immutable-readback' for item in report['mutations'])
    requests = registry['requests']
    assert next(i for i, item in enumerate(requests) if '/referrers/' in item['path']) < next(i for i, item in enumerate(requests) if item['method'] == 'POST')
    assert not any('outside.invalid' in item['path'] or '/tags/' in item['path'] or item['method'] == 'DELETE' for item in requests)
    assert 'password' not in '\n'.join(path.name for path in output.iterdir())


def test_independent_pull_auth_sessions_and_explicit_push_scope(case):
    case['config']['credentials'] = {'basic_username_env': 'PUBLICATION_TEST_USER',
        'basic_password_env': 'PUBLICATION_TEST_PASSWORD', 'token_path': '/service/token', 'token_service': 'fixture'}
    update_config(case)
    report, cap = publication.publish_signed_mission(**case['args'])
    assert cap is None and report['checks']['discovery'] == 'pass'
    exchanges = [parse_qs(urlsplit(item['path']).query)['scope'][0] for item in case['registry']['requests'] if '/service/token?' in item['path']]
    assert exchanges == ['repository:' + REPOSITORY + ':pull', 'repository:' + REPOSITORY + ':pull,push', 'repository:' + REPOSITORY + ':pull']


@pytest.mark.parametrize('fault', ['no-referrers', 'referrers-redirect', 'subject-corrupt', 'trust-pin', 'key-pin', 'source-payload', 'source-bundle', 'source-sbom', 'source-receipt', 'destination'])
def test_preflight_mismatch_prevents_all_writes(case, fault):
    if fault == 'subject-corrupt':
        case['registry']['manifests'][case['binding']['manifest_digest']] = b'{}'
    elif fault in ('no-referrers', 'referrers-redirect'):
        case['registry']['fault'] = fault
    elif fault in ('trust-pin', 'key-pin'):
        case['config']['trust_policy_sha256' if fault == 'trust-pin' else 'public_key_sha256'] = '0'*64
        update_config(case)
    elif fault == 'destination':
        case['config']['repository'] = 'other/evidence'
        update_config(case)
    else:
        name = {'source-payload': 'payload.json', 'source-bundle': 'bundle.json', 'source-sbom': 'sbom.spdx.json', 'source-receipt': 'scanner-receipt.json'}[fault]
        path = Path(case['binding']['signature_payload']['path']).parent / name
        path.chmod(0o600)
        path.write_bytes(b'changed')
    report, cap = publication.publish_signed_mission(**case['args'])
    assert report['decision'] == 'unknown' and cap is None
    assert not report['mutations'] and not any(item['method'] in ('POST', 'PUT') for item in case['registry']['requests'])


@pytest.mark.parametrize('fault', ['put-disconnect', 'external-location', 'corrupt-blob', 'blob-redirect', 'missing-discovery', 'conflicting-discovery', 'duplicate-discovery'])
def test_ambiguous_mutation_or_discovery_retains_exact_digests_without_retry(case, fault):
    case['registry']['fault'] = fault
    report, cap = publication.publish_signed_mission(**case['args'])
    assert report['decision'] == 'unknown' and cap is None and report['artifact']['digest'].startswith('sha256:')
    assert report['mutations']
    assert report['gaps'] == ['publication-incomplete-retain-exact-digests-and-mutation-intents']
    requests = case['registry']['requests']
    puts = [item['path'] for item in requests if item['method'] == 'PUT']
    assert len(puts) == len(set(puts))
    if fault == 'put-disconnect':
        assert len(puts) == 1 and len(case['registry']['blobs']) == 1
    if 'discovery' in fault:
        assert report['checks']['signature_verification'] == 'pass'
        assert report['artifact']['digest'] in case['registry']['manifests']
    assert not any(item['method'] == 'DELETE' for item in requests)


@pytest.mark.parametrize('fault', ['json', 'expired', 'foreign', 'scope'])
def test_closed_capability_before_output_or_network(case, fault):
    if fault == 'json':
        case['args']['signed'] = case['binding']
    elif fault == 'expired':
        signing._SIGNED[case['args']['signed']]['expires_monotonic'] = 0
    elif fault == 'foreign':
        signing._SIGNED[case['args']['signed']]['pid'] += 1
    else:
        case['args']['source_commit'] = 'f'*40
    with pytest.raises(ValueError):
        publication.publish_signed_mission(**case['args'])
    assert not case['args']['output'].exists() and not case['registry']['requests']


@pytest.mark.parametrize('fault', ['deadline', 'authority-expiry', 'evidence', 'route'])
def test_terminal_publication_reaudits_and_withdraws_report(case, monkeypatch, fault):
    real_write = publication._write
    original = case['args']['output']
    def write(directory, name, value):
        ref = real_write(directory, name, value)
        if name == 'mission-publication.json':
            if fault == 'deadline':
                actual = time.monotonic
                monkeypatch.setattr(publication.time, 'monotonic', lambda: actual() + 1000)
            elif fault == 'authority-expiry':
                signing._SIGNED[case['args']['signed']]['expires_monotonic'] = 0
            elif fault == 'evidence':
                path = original / 'readback-payload.json'
                path.chmod(0o600)
                path.write_bytes(b'changed')
            else:
                original.rename(original.with_name('moved'))
                original.mkdir(mode=0o700)
        return ref
    monkeypatch.setattr(publication, '_write', write)
    with pytest.raises(ValueError):
        publication.publish_signed_mission(**case['args'])
    retained = original.with_name('moved') if fault == 'route' else original
    assert not (retained / 'mission-publication.json').exists()


def test_downloaded_bundle_is_cryptographically_verified_not_only_hashed(case):
    # A deliberately controlled capability lets invalid cryptographic material
    # reach the final verifier. It cannot issue real publication authority.
    path = Path(case['binding']['signature_bundle']['path'])
    bundle = json.loads(path.read_bytes())
    signature = bytearray(base64.b64decode(bundle['messageSignature']['signature']))
    signature[-1] ^= 1
    bundle['messageSignature']['signature'] = base64.b64encode(signature).decode()
    raw = encode(bundle)
    path.chmod(0o600)
    path.write_bytes(raw)
    case['binding']['signature_bundle'].update(evidence.reference(raw))
    report, cap = publication.publish_signed_mission(**case['args'])
    assert cap is None and report['decision'] == 'unknown'
    assert report['checks']['upload'] == report['checks']['readback'] == 'pass'
    assert report['checks']['signature_verification'] == 'unknown'
    observed = json.loads((case['args']['output'] / 'readback-signature-verification.json').read_bytes())['observations']
    assert observed['verification']['decision'] != 'pass'


def test_expiring_at_native_preflight_prevents_any_mutation(case):
    def expire(path):
        if '/referrers/' in path:
            signing._SIGNED[case['args']['signed']]['expires_monotonic'] = 0
    case['registry']['hook'] = expire
    report, cap = publication.publish_signed_mission(**case['args'])
    assert cap is None and report['decision'] == 'unknown'
    assert not report['mutations'] and not any(item['method'] in ('POST', 'PUT') for item in case['registry']['requests'])


def test_changed_fsynced_intent_prevents_mutation(case, monkeypatch):
    real_write = publication._write
    def write(directory, name, value):
        ref = real_write(directory, name, value)
        if name == 'mutation-001.json':
            (case['args']['output'] / name).write_bytes(b'changed')
        return ref
    monkeypatch.setattr(publication, '_write', write)
    report, cap = publication.publish_signed_mission(**case['args'])
    assert cap is None and report['decision'] == 'unknown'
    assert len(report['mutations']) == 1 and not any(item['method'] in ('POST', 'PUT') for item in case['registry']['requests'])


def test_independent_auth_realm_cannot_forward_credentials(case):
    case['config']['credentials'] = {'basic_username_env': 'PUBLICATION_TEST_USER',
        'basic_password_env': 'PUBLICATION_TEST_PASSWORD', 'token_path': '/service/token', 'token_service': 'fixture'}
    case['registry']['fault'] = 'auth-outside'
    update_config(case)
    report, cap = publication.publish_signed_mission(**case['args'])
    assert cap is None and not report['mutations']
    assert len(case['registry']['requests']) == 1
    # Basic authentication is explicitly selected for this trusted origin. The
    # hostile challenge cannot trigger a token exchange or forward it elsewhere.
    request = case['registry']['requests'][0]
    assert request['path'] == '/v2/' + REPOSITORY + '/manifests/' + case['binding']['manifest_digest']
    assert request['authorization'].startswith('Basic ')


def test_publication_type_cannot_be_constructed_or_serialized():
    with pytest.raises(TypeError):
        publication.PublishedMission()
    with pytest.raises(TypeError):
        pickle.dumps(object.__new__(publication.PublishedMission))
