"""Offline, synthetic URL policy cases; these do not qualify a live transport."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from urllib.parse import urlencode

import pytest


from devsecops.adapters import registry_download_policy as module

REGISTRY = 'https://registry1.dso.mil'
STORAGE = 'https://ib-prod-harbor-storage.s3.us-gov-west-1.amazonaws.com'
REPOSITORY = 'ironbank/opensource/nginx/nginx'
DIGEST = 'sha256:' + 'a' * 64
PATH = '/docker/registry/v2/blobs/sha256/aa/' + 'a' * 64 + '/data'
ACCESS = 'EXAMPLEACCESSKEY00000'
SIGNATURE = 'c' * 64
TOKEN = 'SYNTHETIC-TOKEN-NOT-A-CREDENTIAL'
BASE_QUERY = {
    'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
    'X-Amz-Credential': ACCESS + '/20261103/us-gov-west-1/s3/aws4_request',
    'X-Amz-Date': '20261103T030000Z', 'X-Amz-Expires': '300',
    'X-Amz-Signature': SIGNATURE, 'X-Amz-SignedHeaders': 'host',
}


def url(*, origin=STORAGE, path=PATH, changes=None):
    query = dict(BASE_QUERY)
    query.update(changes or {})
    return origin + path + '?' + urlencode(query)


def select(location=None, **changes):
    args = {'registry_origin': REGISTRY, 'repository': REPOSITORY,
        'request_path': '/v2/' + REPOSITORY + '/blobs/' + DIGEST,
        'digest': DIGEST, 'status': 307, 'location_headers': [location or url()]}
    args.update(changes)
    return module.RegistryBlobPolicy(REGISTRY, (REPOSITORY,), (STORAGE,)).select(**args)


@pytest.mark.parametrize('temporary', [False, True])
def test_scoped_selection_keeps_capability_values_out_of_public_record(temporary):
    location = url(changes={'X-Amz-Security-Token': TOKEN} if temporary else {})
    target = select(location)
    assert target.origin == STORAGE
    path, query = target.request_parts()
    assert path == PATH
    assert query['X-Amz-Signature'] == SIGNATURE
    assert STORAGE + path + '?' + urlencode(query) == location
    record = target.policy_record()
    public = repr(target) + json.dumps(record)
    assert all(secret not in public for secret in (ACCESS, SIGNATURE, TOKEN, PATH, location))
    assert record['decision'] == 'selected-download-target'
    assert record['transport_requirements']['omit_headers'] == ['Authorization', 'Cookie']
    query['X-Amz-Signature'] = 'mutated'
    assert target.request_parts()[1]['X-Amz-Signature'] == SIGNATURE
    with pytest.raises(FrozenInstanceError):
        target.origin = 'https://attacker.example'


@pytest.mark.parametrize('changes', [
    {'status': 301}, {'status': 302}, {'status': 308}, {'status': 200},
    {'previous_hops': 1}, {'previous_hops': True},
    {'registry_origin': 'https://other-registry.example'},
    {'repository': 'ironbank/another/repository'},
    {'request_path': '/service/token'},
    {'request_path': '/v2/' + REPOSITORY + '/manifests/' + DIGEST},
    {'request_path': '/v2/' + REPOSITORY + '/blobs/' + 'sha256:' + 'b' * 64},
    {'digest': 'sha256:truncated'},
    {'location_headers': []}, {'location_headers': [url(), url()]},
    {'location_headers': url()},
])
def test_request_identity_and_single_hop_scope(changes):
    with pytest.raises(module.RedirectPolicyError):
        select(**changes)


@pytest.mark.parametrize('location', [
    url(origin=STORAGE.replace('https:', 'http:')),
    url(origin=STORAGE + '.attacker.example'),
    url(origin=STORAGE + ':8443'),
    url(origin=STORAGE.replace('https://', 'https://user:password@')),
    url(origin='https://127.0.0.1'),
    url(origin='https://169.254.169.254'),
    url(origin='https://[::1]'),
    url(origin='https://attacker.example'),
    url(origin=STORAGE.upper()),
    url() + '#fragment', url() + '\r\nX-Injected:yes',
    ' ' + url(), url() + '\\',
    url(path='//elsewhere'), url(path='/a//data'), url(path='/a/../data'), url(path='/a/./data'),
    url(path='/a/%2e%2e/data'), url(path='/a/%2f/data'),
    url(path='/' + 'a' * 2048), 'https://' + 'x' * 8193,
    url() + '&X-Amz-Signature=' + SIGNATURE,
    url() + '&unexpected=anything',
    url().replace('%2F', '%2f'),
    url(changes={'X-Amz-Algorithm': 'unsupported'}),
    url(changes={'X-Amz-SignedHeaders': 'authorization;host'}),
    url(changes={'X-Amz-Signature': 'not-a-signature'}),
    url(changes={'X-Amz-Credential': ACCESS + '/20261103/us-gov-west-1/ec2/aws4_request'}),
    url(changes={'X-Amz-Date': '20261104T030000Z'}),
    url(changes={'X-Amz-Date': '20261103T250000Z'}),
    url(changes={'X-Amz-Date': '20261103T03\u066000Z'}),
    url(changes={'X-Amz-Expires': '0'}),
    url(changes={'X-Amz-Expires': '604801'}),
    url(changes={'X-Amz-Expires': '000300'}),
    url(changes={'X-Amz-Security-Token': 'bad\nvalue'}),
])
def test_rejects_capability_expansion_and_ambiguous_urls(location):
    with pytest.raises(module.RedirectPolicyError) as failure:
        select(location)
    assert all(value not in str(failure.value) for value in (location, ACCESS, SIGNATURE, TOKEN))


@pytest.mark.parametrize('storage_origins', [(), (STORAGE, STORAGE), (REGISTRY,),
    ('https://*.amazonaws.com',), ('https://allowed.example/path',),
    ('https://allowed.example?query',), ('https://allowed.example#fragment',),
    ('https://allowed.example:bad',), ('https://allowed.example:0443',),
    ('https://allowed.example.',), ('https://user@allowed.example',),
    ['https://allowed.example'],
])
def test_policy_requires_explicit_immutable_origins(storage_origins):
    with pytest.raises(module.RedirectPolicyError):
        module.RegistryBlobPolicy(REGISTRY, (REPOSITORY,), storage_origins)


def test_exact_custom_storage_origin_is_supported_without_global_aws_allowlist():
    custom = 'https://object-store.customer.example:8443'
    policy = module.RegistryBlobPolicy(REGISTRY, (REPOSITORY,), (custom,))
    target = policy.select(registry_origin=REGISTRY, repository=REPOSITORY,
        request_path='/v2/' + REPOSITORY + '/blobs/' + DIGEST, digest=DIGEST,
        status=307, location_headers=(url(origin=custom),))
    assert target.origin == custom
