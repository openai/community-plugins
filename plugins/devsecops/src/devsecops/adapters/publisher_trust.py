"""Assess an explicitly selected publisher public-key policy.

Online authority comes from a direct official HTTPS publication plus the
operator's key pin. Offline authority comes from an explicitly trusted package
pin and its configured freshness window. Neither is an image/build capability.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from .http import APIError, HTTPSReadClient, _json
from .oci import _repository
from .signatures import _Invalid, _public_key, _snapshot
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal

P1_ORIGIN = 'https://p1docs.dso.mil'
P1_PATH = '/iron-bank/tutorials/cosign'
P1_URL = P1_ORIGIN + P1_PATH
P1_SECTION = 'iron-bank-signing-certs-and-keys'
MAX_SOURCE = 1_000_000
MAX_WINDOW = 90 * 86400


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value)


def _instant(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', value):
        raise ValueError('Trust timestamps require UTC seconds')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _now():
    return datetime.now(timezone.utc)


def _checkpoint(deadline):
    if time.monotonic() >= deadline:
        raise APIError('publisher-trust-deadline')


def _key(raw):
    _public_key(raw)
    lines = raw.decode('ascii').strip().splitlines()
    der = base64.b64decode(''.join(lines[1:-1]), validate=True)
    encoded = base64.b64encode(der).decode()
    pem = ('-----BEGIN PUBLIC KEY-----\n' + '\n'.join(encoded[i:i+64] for i in range(0, len(encoded), 64))
           + '\n-----END PUBLIC KEY-----\n').encode()
    return {'pem_sha256': _sha(raw), 'spki_sha256': _sha(der), 'algorithm': 'ecdsa-p256'}, pem


def _policy(raw):
    value = _json(raw)
    if (set(value) != {'schema_version', 'kind', 'publisher', 'scope', 'key', 'validity', 'source', 'revoked_spki_sha256'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['kind'] != 'publisher-public-key-policy'
            or not isinstance(value['publisher'], str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', value['publisher'])):
        raise ValueError('Unsupported publisher public-key policy')
    scope, key, validity, source, revoked = (value[k] for k in ('scope', 'key', 'validity', 'source', 'revoked_spki_sha256'))
    if (not isinstance(scope, dict) or set(scope) != {'registry_origin', 'repository_prefix'}
            or HTTPSReadClient(scope['registry_origin']).origin != scope['registry_origin']
            or not isinstance(scope['repository_prefix'], str) or not scope['repository_prefix'].endswith('/')
            or not _repository(scope['repository_prefix'][:-1])):
        raise ValueError('Exact registry origin and namespace prefix are required')
    if (not isinstance(key, dict) or set(key) != {'pem_sha256', 'spki_sha256'}
            or not all(_hash(item) for item in key.values())):
        raise ValueError('Exact PEM and SPKI key identities are required')
    if not isinstance(validity, dict) or set(validity) != {'not_before', 'not_after'}:
        raise ValueError('Explicit current trust policy validity is required')
    first, last = (_instant(validity[k]) for k in ('not_before', 'not_after'))
    if not 0 < (last-first).total_seconds() <= MAX_WINDOW:
        raise ValueError('Trust policy validity must be positive and at most 90 days')
    if (not isinstance(revoked, list) or len(revoked) > 128
            or any(not _hash(item) for item in revoked) or len(set(revoked)) != len(revoked)):
        raise ValueError('Unsupported operator key denylist')
    if not isinstance(source, dict):
        raise ValueError('Explicit publisher authority source is required')
    if source == {'kind': 'p1-current-document'}:
        if (value['publisher'] != 'platform-one-iron-bank'
                or scope != {'registry_origin': 'https://registry1.dso.mil', 'repository_prefix': 'ironbank/'}):
            raise ValueError('P1 publication is restricted to the Iron Bank signing scope')
    elif (set(source) == {'kind', 'package_sha256', 'max_age_seconds'}
            and source['kind'] == 'operator-trusted-package' and _hash(source['package_sha256'])
            and type(source['max_age_seconds']) is int and 1 <= source['max_age_seconds'] <= MAX_WINDOW):
        pass
    else:
        raise ValueError('Unsupported authority mode; certificate policies are not implicitly downgraded')
    return value


def _current(policy):
    return _instant(policy['validity']['not_before']) <= _now() < _instant(policy['validity']['not_after'])


def _verify_artifacts(directory, inventory, deadline):
    for name, reference in inventory.items():
        _checkpoint(deadline)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_size != reference['bytes']
                    or before.st_uid != os.getuid() or before.st_nlink != 1 or before.st_mode & 0o077):
                raise APIError('publisher-output-changed')
            checksum, size = hashlib.sha256(), 0
            while chunk := stream.read(65536):
                _checkpoint(deadline)
                size += len(chunk)
                if size > reference['bytes']:
                    raise APIError('publisher-output-changed')
                checksum.update(chunk)
            after = os.fstat(fd)
            if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or checksum.hexdigest() != reference['sha256']):
                raise APIError('publisher-output-changed')




def _current_document_key(raw, deadline):
    """Extract only the unique current-section key, never the legacy section."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE:
        raise APIError('publisher-document-size')
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.active, self.closed, self.sections, self.pre, self.blocks, self.parts = False, False, 0, False, [], []
            self.characters = 0

        def handle_starttag(self, tag, attrs):
            _checkpoint(deadline)
            if tag == 'h2':
                if self.active:
                    if self.pre:
                        raise APIError('publisher-document-structure')
                    self.active, self.closed = False, True
                ids = [value for key, value in attrs if key == 'id']
                if P1_SECTION in ids:
                    self.sections += 1
                    if len(ids) != 1 or self.sections != 1:
                        raise APIError('publisher-document-structure')
                    self.active = True
            if self.active and tag == 'pre':
                if self.pre or len(self.blocks) >= 8:
                    raise APIError('publisher-document-structure')
                self.pre, self.parts, self.characters = True, [], 0
            if self.active and self.pre and tag in {'script', 'style', 'iframe'}:
                raise APIError('publisher-document-structure')

        def handle_endtag(self, tag):
            _checkpoint(deadline)
            if self.active and tag == 'pre':
                if not self.pre:
                    raise APIError('publisher-document-structure')
                self.blocks.append(''.join(self.parts))
                self.pre, self.parts = False, []

        def handle_data(self, data):
            _checkpoint(deadline)
            if self.active and self.pre:
                self.characters += len(data)
                if self.characters > 32_000:
                    raise APIError('publisher-document-structure')
                self.parts.append(data)
    try:
        text = raw.decode('utf-8')
        parser = Parser()
        for offset in range(0, len(text), 65536):
            _checkpoint(deadline)
            parser.feed(text[offset:offset+65536])
        parser.close()
        if parser.sections != 1 or not parser.closed or parser.pre:
            raise APIError('publisher-document-structure')
        candidates = [block.strip().encode('ascii') for block in parser.blocks if '-----BEGIN PUBLIC KEY-----' in block]
        if len(candidates) != 1:
            raise APIError('publisher-current-key-ambiguous')
        identity, pem = _key(candidates[0])
        _checkpoint(deadline)
        return identity, pem
    except (UnicodeError, _Invalid):
        raise APIError('publisher-current-key-unsupported') from None


def _package(raw, policy):
    document = _json(raw)
    if (set(document) != {'schema_version', 'kind', 'publisher', 'scope', 'source_url', 'reviewed_at', 'public_key_pem'}
            or type(document['schema_version']) is not int or document['schema_version'] != 1
            or document['kind'] != 'operator-trusted-publisher-key-package'
            or document['publisher'] != policy['publisher'] or document['scope'] != policy['scope']
            or not isinstance(document['source_url'], str) or len(document['source_url']) > 2048
            or not re.fullmatch(r'https://[A-Za-z0-9./_-]+', document['source_url'])
            or not isinstance(document['public_key_pem'], str) or len(document['public_key_pem']) > 16_384):
        raise APIError('publisher-package-unsupported')
    if policy['publisher'] == 'platform-one-iron-bank' and document['source_url'] != P1_URL:
        raise APIError('publisher-package-provenance-mismatch')
    reviewed = _instant(document['reviewed_at'])
    age = (_now()-reviewed).total_seconds()
    if not 0 <= age <= policy['source']['max_age_seconds']:
        raise APIError('publisher-package-stale')
    identity, pem = _key(document['public_key_pem'].encode('ascii'))
    return identity, pem, document


def assess_publisher_trust(*, policy_path, expected_policy_sha256, public_key,
                           registry_origin, repository, output, package_path=None,
                           total_timeout=30):
    """Assess the selected key policy by direct source acquisition or package pin.

    No image metadata, imported verifier verdict, environment credentials, caller
    clock, configurable URL or evaluator callback can supply publisher authority.
    The policy digest and package trust are separately chosen operator inputs.
    """
    if (not _hash(expected_policy_sha256) or not _repository(repository)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts
            or type(total_timeout) not in (int, float) or not math.isfinite(total_timeout)
            or not 1 <= total_timeout <= 120):
        raise ValueError('Explicit pinned policy, scope, new output and bounded deadline are required')
    deadline = time.monotonic() + total_timeout
    with tempfile.TemporaryDirectory(prefix='devsecops-publisher-') as temporary:
        scratch = Path(temporary).resolve()
        ref = _snapshot(policy_path, scratch / 'policy.json', 64_000, deadline=deadline)
        if ref['sha256'] != expected_policy_sha256:
            raise ValueError('Publisher policy differs from the independently selected identity')
        policy = _policy((scratch / 'policy.json').read_bytes())
        if registry_origin != policy['scope']['registry_origin'] or not repository.startswith(policy['scope']['repository_prefix']):
            raise ValueError('Publisher trust policy does not cover the selected registry repository')
        if not _current(policy):
            raise ValueError('Publisher policy is outside its validity window')
        offline = policy['source']['kind'] == 'operator-trusted-package'
        if offline != (package_path is not None):
            raise ValueError('Offline package presence must match the selected authority mode')
        _snapshot(public_key, scratch / 'selected-key.pem', 16_384, deadline=deadline)
        key_raw = (scratch / 'selected-key.pem').read_bytes()
        key_identity, _ = _key(key_raw)
        if any(key_identity[k] != policy['key'][k] for k in ('pem_sha256', 'spki_sha256')):
            raise ValueError('Selected public key differs from the trust policy identity')
        result = {'schema_version': 1, 'kind': 'publisher-key-trust-assessment',
            'decision': 'unknown', 'publisher_trust': 'unknown', 'build_authority': 'not-issued',
            'execution_mode': 'operator-offline' if offline else 'direct',
            'publisher': policy['publisher'], 'scope': {'registry_origin': registry_origin, 'repository': repository},
            'policy_sha256': ref['sha256'], 'key': key_identity, 'started_at': _now().isoformat(),
            'checks': {'source_binding': 'unknown', 'policy_current': 'pass', 'key_status': 'unknown'},
            'source': {'mode': policy['source']['kind'], 'authority': 'operator-selected-package' if offline else 'official-https-publication'},
            'revocation': {'mode': 'operator-policy-denylist', 'certificate_crl_ocsp': 'not-required-by-selected-public-key-policy'},
            'artifacts': {}, 'receipts': [], 'gaps': []}
        with _new_directory(output) as directory:
            def retain(name, raw):
                _checkpoint(deadline)
                result['artifacts'][name] = _write(directory, name, raw.decode('utf-8'))
            try:
                retain('selected-policy.json', (scratch / 'policy.json').read_bytes())
                retain('selected-key.pem', key_raw)
                if key_identity['spki_sha256'] in policy['revoked_spki_sha256']:
                    result['checks']['key_status'] = 'fail'
                    raise APIError('publisher-key-denylisted')
                result['checks']['key_status'] = 'pass'
                if offline:
                    package_ref = _snapshot(package_path, scratch / 'package.json', 64_000, deadline=deadline)
                    if package_ref['sha256'] != policy['source']['package_sha256']:
                        raise APIError('publisher-package-identity-mismatch')
                    source_raw = (scratch / 'package.json').read_bytes()
                    source_key, source_pem, package = _package(source_raw, policy)
                    retain('trusted-key-package.json', source_raw)
                    result['source'].update(package_sha256=package_ref['sha256'],
                        provenance_url=package['source_url'], provenance='operator-asserted-and-explicitly-pinned',
                        reviewed_at=package['reviewed_at'], max_age_seconds=policy['source']['max_age_seconds'])
                else:
                    remaining = deadline-time.monotonic()
                    _checkpoint(deadline)
                    client = HTTPSReadClient(P1_ORIGIN, timeout=min(15, remaining), total_timeout=remaining,
                        max_body_bytes=MAX_SOURCE, max_requests=1, max_total_bytes=MAX_SOURCE)
                    client._deadline = deadline
                    # Tests may exercise the actual TLS path at a controlled
                    # origin. Such a source cannot satisfy P1 authority.
                    if client.origin != 'https://p1docs.dso.mil':
                        result['execution_mode'] = 'controlled-test'
                        result['source']['authority'] = 'controlled-test-endpoint'
                    try:
                        response = client.get_bytes(P1_PATH, accept=('text/html',))
                    finally:
                        result['receipts'] = client.receipts
                    source_raw = response.body
                    retain('official-source.html', source_raw)
                    source_key, source_pem = _current_document_key(source_raw, deadline)
                    result['source'].update(url=client.origin+P1_PATH, section=P1_SECTION,
                        document_sha256=_sha(source_raw), fetched_at=_now().isoformat(),
                        provenance='direct-https-current-section')
                retain('source-key.pem', source_pem)
                if source_key['spki_sha256'] != key_identity['spki_sha256']:
                    result['checks']['source_binding'] = 'fail'
                    raise APIError('publisher-key-source-mismatch')
                result['checks']['source_binding'] = 'pass'
            except (APIError, _Invalid) as error:
                result['gaps'].append(error.code)
            except (OSError, ValueError, TypeError, KeyError, UnicodeError):
                result['gaps'].append('publisher-trust-input-or-output-unavailable')
            result['checks']['policy_current'] = 'pass' if _current(policy) else 'unknown'
            values = result['checks'].values()
            result['publisher_trust'] = ('fail' if 'fail' in values else
                'pass' if all(value == 'pass' for value in values) and not result['gaps'] else 'unknown')
            if result['execution_mode'] == 'controlled-test':
                result['publisher_trust'] = 'unknown'
                result['gaps'].append('controlled-source-is-not-publisher-authority')
            # This component can satisfy its explicit key-trust policy, while
            # complete image/base authority remains the caller's separate task.
            result['decision'] = result['publisher_trust']
            result['finished_at'] = _now().isoformat()
            _verify_output_path(output, directory)
            result = seal(result, 'trust_sha256')
            try:
                _write(directory, 'publisher-trust.json', result)
                os.fsync(directory)
                _verify_output_path(output, directory)
                if result['publisher_trust'] == 'pass':
                    _checkpoint(deadline)
                    _verify_artifacts(directory, result['artifacts'], deadline)
                    if not _current(policy):
                        raise APIError('publisher-policy-expired-during-publication')
                    if offline:
                        _package(source_raw, policy)  # Freshness also holds at completion.
                    _verify_output_path(output, directory)
                    _checkpoint(deadline)
            except BaseException:
                try:
                    os.unlink('publisher-trust.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            return result
