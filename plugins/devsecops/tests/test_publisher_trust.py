"""Real TLS and frozen operator-package trust; controlled endpoints cannot qualify P1."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time

import pytest

from test_argocd import ca, server
from test_attestations import lab
from devsecops.adapters import publisher_trust as adapter
from devsecops.adapters.http import APIError, HTTPSReadClient
from devsecops.runtime.context import verify_seal
from scripts.assess_publisher_trust import main

__all__ = ['ca', 'server', 'lab']

# Public material from the official P1 signing guide, not a generated lab key.
P1_KEY = b'''-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE7CjMGH005DFFz6mffqTIGurBt6fL
UfTZxuEDFRBS8mFJx1xw8DEVvjMibLTtqmAoJxUmzmGFgzz+LV875syVEg==
-----END PUBLIC KEY-----
'''


def stamp(value):
    return value.strftime('%Y-%m-%dT%H:%M:%SZ')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(tmp_path, *, offline=True, key=P1_KEY):
    now = datetime.now(timezone.utc)
    scope = {'registry_origin': 'https://registry1.dso.mil', 'repository_prefix': 'ironbank/'}
    identity, _ = adapter._key(key)
    policy = {'schema_version': 1, 'kind': 'publisher-public-key-policy',
        'publisher': 'platform-one-iron-bank', 'scope': scope,
        'key': {k: identity[k] for k in ('pem_sha256', 'spki_sha256')},
        'validity': {'not_before': stamp(now-timedelta(minutes=1)), 'not_after': stamp(now+timedelta(hours=1))},
        'source': {'kind': 'p1-current-document'}, 'revoked_spki_sha256': []}
    package = {'schema_version': 1, 'kind': 'operator-trusted-publisher-key-package',
        'publisher': policy['publisher'], 'scope': scope, 'source_url': adapter.P1_URL,
        'reviewed_at': stamp(now-timedelta(seconds=10)), 'public_key_pem': key.decode()}
    package_path = tmp_path / 'operator-package.json'
    package_path.write_bytes(encode(package))
    if offline:
        policy['source'] = {'kind': 'operator-trusted-package', 'package_sha256': digest(encode(package)),
                            'max_age_seconds': 3600}
    path, public_key = tmp_path / 'operator-policy.json', tmp_path / 'publisher.pem'
    path.write_bytes(encode(policy))
    public_key.write_bytes(key)
    args = dict(policy_path=path, expected_policy_sha256=digest(encode(policy)), public_key=public_key,
                registry_origin=scope['registry_origin'], repository='ironbank/opensource/python/python',
                output=tmp_path / 'result', **({'package_path': package_path} if offline else {}))
    return policy, package, args


def rewrite(policy, args):
    raw = encode(policy)
    args['policy_path'].write_bytes(raw)
    args['expected_policy_sha256'] = digest(raw)


def html(current=P1_KEY, legacy=None):
    document = '<h2 id="iron-bank-signing-certs-and-keys">Current</h2><pre><code>'
    document += current.decode() + '</code></pre><h2 id="next-section">Next</h2>'
    if legacy is not None:
        document += '<h2 id="iron-bank-signing-certs-and-keys-before-04sep2025">Old</h2><pre>' + legacy.decode() + '</pre>'
    return document.encode()


def controlled_endpoint(server, monkeypatch):
    def client(origin, **kwargs):
        if origin == adapter.P1_ORIGIN:
            return HTTPSReadClient(server['origin'], ca_bundle=server['ca'], **kwargs)
        return HTTPSReadClient(origin, **kwargs)
    monkeypatch.setattr(adapter, 'HTTPSReadClient', client)


def test_operator_trusted_package_can_satisfy_its_explicit_policy(tmp_path, monkeypatch):
    _, _, args = fixture(tmp_path)
    monkeypatch.setenv('HTTP_PROXY', 'http://invalid.example:1')
    result = adapter.assess_publisher_trust(**args)
    verify_seal(result, 'trust_sha256')
    assert result['publisher_trust'] == result['decision'] == 'pass'
    assert result['build_authority'] == 'not-issued'
    assert result['execution_mode'] == 'operator-offline' and not result['receipts']
    assert result['source']['authority'] == 'operator-selected-package'
    assert result['source']['provenance'] == 'operator-asserted-and-explicitly-pinned'
    assert result['revocation']['certificate_crl_ocsp'] == 'not-required-by-selected-public-key-policy'
    for name, ref in result['artifacts'].items():
        path = args['output'] / name
        assert digest(path.read_bytes()) == ref['sha256']
        assert path.stat().st_mode & 0o777 == 0o600


def test_reusable_operator_scope_can_trust_a_different_publisher(tmp_path, lab):
    policy, package, args = fixture(tmp_path, key=(lab[0] / 'lab.pub').read_bytes())
    scope = {'registry_origin': 'https://registry.example.test', 'repository_prefix': 'approved/'}
    policy.update(publisher='controlled-test-publisher', scope=scope)
    package.update(publisher=policy['publisher'], scope=scope, source_url='https://publisher.example.test/signing')
    args['package_path'].write_bytes(encode(package))
    policy['source']['package_sha256'] = digest(encode(package))
    args.update(registry_origin=scope['registry_origin'], repository='approved/service')
    rewrite(policy, args)
    result = adapter.assess_publisher_trust(**args)
    assert result['publisher_trust'] == 'pass'
    assert result['publisher'] == 'controlled-test-publisher'
    assert result['scope'] != {'registry_origin': 'https://registry1.dso.mil', 'repository': args['repository']}


def test_real_controlled_tls_is_never_p1_authority(server, tmp_path, monkeypatch):
    _, _, args = fixture(tmp_path, offline=False)
    source = html()
    server['responses'] = [{'raw': source, 'content_type': 'text/html'}]
    controlled_endpoint(server, monkeypatch)
    result = adapter.assess_publisher_trust(**args)
    assert all(v == 'pass' for v in result['checks'].values())
    assert result['publisher_trust'] == 'unknown' and result['execution_mode'] == 'controlled-test'
    assert result['source']['document_sha256'] == digest(source)
    assert result['artifacts']['official-source.html']['sha256'] == digest(source)
    assert server['requests'] == [{'path': adapter.P1_PATH, 'authorization': None}]


def test_current_section_excludes_legacy_key(lab):
    legacy = (lab[0] / 'lab.pub').read_bytes()
    observed, key = adapter._current_document_key(html(legacy=legacy), time.monotonic()+2)
    assert observed['spki_sha256'] == adapter._key(P1_KEY)[0]['spki_sha256']
    assert key == P1_KEY


@pytest.mark.parametrize('source', [
    b'<h2 id="iron-bank-signing-certs-and-keys-before-04sep2025">Old</h2><pre>' + P1_KEY + b'</pre>',
    html().replace(b'Current', b'Current</h2><h2 id="iron-bank-signing-certs-and-keys">Duplicate'),
    html().replace(b'</code></pre>', b'</code></pre><pre>' + P1_KEY + b'</pre>'),
    html().replace(P1_KEY, b'not a key'),
    html().replace(b'</code></pre>', b'<script>bad</script></code></pre>'),
    html().split(b'<h2 id="next-section">')[0],
])
def test_malformed_or_ambiguous_publication_never_selects_a_key(source):
    with pytest.raises(APIError):
        adapter._current_document_key(source, time.monotonic()+2)


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(schema_version=True),
    lambda p: p.update(certificate_policy={'required': True}),
    lambda p: p['source'].update(url='https://untrusted.example/key'),
    lambda p: p['validity'].update(not_after='2000-01-01T00:00:00Z'),
    lambda p: p['key'].update(spki_sha256='x'*64),
    lambda p: p['source'].update(max_age_seconds=True),
])
def test_bad_policy_never_opens_output(tmp_path, mutation):
    policy, _, args = fixture(tmp_path)
    mutation(policy)
    rewrite(policy, args)
    with pytest.raises((ValueError, TypeError)):
        adapter.assess_publisher_trust(**args)
    assert not args['output'].exists()


@pytest.mark.parametrize('field,value', [('registry_origin', 'https://outside.example'),
    ('repository', 'ironbank-lookalike/python'), ('repository', 'different/python')])
def test_scope_is_exact_before_acquisition(tmp_path, field, value):
    _, _, args = fixture(tmp_path)
    args[field] = value
    with pytest.raises(ValueError, match='does not cover'):
        adapter.assess_publisher_trust(**args)
    assert not args['output'].exists()


def test_changed_policy_hash_is_rejected(tmp_path):
    _, _, args = fixture(tmp_path)
    args['policy_path'].write_text('{}')
    with pytest.raises(ValueError, match='policy differs'):
        adapter.assess_publisher_trust(**args)
    assert not args['output'].exists()


def test_changed_key_hash_is_rejected(tmp_path, lab):
    _, _, args = fixture(tmp_path)
    args['public_key'].write_bytes((lab[0] / 'lab.pub').read_bytes())
    with pytest.raises(ValueError, match='key differs'):
        adapter.assess_publisher_trust(**args)
    assert not args['output'].exists()


def test_denylisted_key_cannot_pass(tmp_path):
    policy, _, args = fixture(tmp_path)
    policy['revoked_spki_sha256'] = [policy['key']['spki_sha256']]
    rewrite(policy, args)
    result = adapter.assess_publisher_trust(**args)
    assert result['publisher_trust'] == 'fail'
    assert 'publisher-key-denylisted' in result['gaps']


@pytest.mark.parametrize('damage', ['hash', 'stale', 'future', 'scope', 'source'])
def test_unusable_offline_material_cannot_pass(tmp_path, damage):
    policy, package, args = fixture(tmp_path)
    if damage == 'hash':
        args['package_path'].write_text('{}')
    else:
        if damage == 'stale':
            package['reviewed_at'] = '2000-01-01T00:00:00Z'
        elif damage == 'future':
            package['reviewed_at'] = '2099-01-01T00:00:00Z'
        elif damage == 'scope':
            package['scope'] = dict(package['scope'], repository_prefix='untrusted/')
        elif damage == 'source':
            package['source_url'] = 'https://untrusted.example/key'
        args['package_path'].write_bytes(encode(package))
        policy['source']['package_sha256'] = digest(encode(package))
        rewrite(policy, args)
    result = adapter.assess_publisher_trust(**args)
    assert result['publisher_trust'] == 'unknown' and result['gaps']


@pytest.mark.parametrize('response,code', [
    ({'status': 302, 'headers': {'Location': 'https://other.example/key'}}, 'redirect'),
    ({'status': 403}, 'denied'),
    ({'length': 1_000_001}, 'body-limit'),
])
def test_direct_publication_transport_failure_is_bounded(server, tmp_path, monkeypatch, response, code):
    _, _, args = fixture(tmp_path, offline=False)
    server['responses'] = [dict(response, content_type='text/html')]
    controlled_endpoint(server, monkeypatch)
    result = adapter.assess_publisher_trust(**args)
    assert result['publisher_trust'] == 'unknown' and code in result['gaps']
    assert len(server['requests']) == 1


def test_online_source_key_mismatch_is_rejected(server, tmp_path, monkeypatch, lab):
    _, _, args = fixture(tmp_path, offline=False)
    server['responses'] = [{'raw': html(current=(lab[0] / 'lab.pub').read_bytes()), 'content_type': 'text/html'}]
    controlled_endpoint(server, monkeypatch)
    result = adapter.assess_publisher_trust(**args)
    assert result['checks']['source_binding'] == 'fail'
    assert 'publisher-key-source-mismatch' in result['gaps']


@pytest.mark.parametrize('damage', ['deadline', 'expiry', 'artifact'])
def test_terminal_publication_failure_withdraws_receipt(tmp_path, monkeypatch, damage):
    _, _, args = fixture(tmp_path)
    original = adapter._write
    def changed(directory, name, value):
        result = original(directory, name, value)
        if name == 'publisher-trust.json':
            if damage == 'deadline':
                monkeypatch.setattr(adapter.time, 'monotonic', lambda: 10**12)
            elif damage == 'expiry':
                monkeypatch.setattr(adapter, '_now', lambda: datetime(2099, 1, 1, tzinfo=timezone.utc))
            else:
                (args['output'] / 'selected-key.pem').write_bytes(b'changed')
        return result
    monkeypatch.setattr(adapter, '_write', changed)
    with pytest.raises(APIError):
        adapter.assess_publisher_trust(**args)
    assert not (args['output'] / 'publisher-trust.json').exists()


def test_cli_reports_narrow_pass_and_no_build_authority(tmp_path, capsys):
    _, _, args = fixture(tmp_path)
    argv = []
    for option, key in [('policy', 'policy_path'), ('policy-sha256', 'expected_policy_sha256'),
        ('public-key', 'public_key'), ('registry-origin', 'registry_origin'), ('repository', 'repository'),
        ('output', 'output'), ('package', 'package_path')]:
        argv.extend(['--' + option, str(args[key])])
    assert main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['publisher_trust'] == 'pass' and result['build_authority'] == 'not-issued'
