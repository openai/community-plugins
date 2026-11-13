"""Fixed stdlib-only storage exercise. Never mission or publisher authority.

This module is shipped with exactly three existing HTTP/OCI adapter modules in
a verified zip, then run in a dedicated owned container. No dynamic callback,
customer code, package install, signature claim or opaque capability is used.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

from devsecops.adapters.http import APIError, HTTPSReadClient, _json
from devsecops.adapters.oci import INDEX, MANIFEST, _descriptor, _repository
from devsecops.adapters.staging_registry import _StagingRegistry

ARTIFACT = 'application/vnd.devsecops.registry-storage-qualification.v1+json'
EMPTY = 'application/vnd.oci.empty.v1+json'


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()


def descriptor(raw, media):
    return {'digest': 'sha256:'+hashlib.sha256(raw).hexdigest(), 'size': len(raw), 'mediaType': media}


def objects(binding):
    if not isinstance(binding, str) or not re.fullmatch('[a-f0-9]{64}', binding):
        raise ValueError('Exact registry operation binding required')
    config = b'{}'
    payload = canonical({'kind': 'registry-storage-qualification-only', 'binding_sha256': binding})
    subject = canonical({'schemaVersion': 2, 'mediaType': MANIFEST, 'artifactType': ARTIFACT,
        'config': descriptor(config, EMPTY), 'layers': [descriptor(payload, ARTIFACT)]})
    referrer = canonical({'schemaVersion': 2, 'mediaType': MANIFEST, 'artifactType': ARTIFACT,
        'subject': descriptor(subject, MANIFEST), 'config': descriptor(config, EMPTY),
        'layers': [descriptor(payload, ARTIFACT)]})
    return config, payload, subject, referrer


def _withdraw(directory, name, identity):
    try:
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == identity:
        os.unlink(name, dir_fd=directory)
        os.fsync(directory)


def _write(directory, name, value):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    info = os.fstat(fd)
    identity = (info.st_dev, info.st_ino)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
    except BaseException:
        _withdraw(directory, name, identity)
        raise
    return identity


def qualify(*, origin, repository, ca_bundle, operator, reader, binding, output, timeout=90, expires_at=None, service_route=None):
    """Write bounded probe objects once, independently read and retain all effects.

    `operator` and `reader` contain existing env reference names, never secrets.
    Failure receipts retain ambiguous attempts; a caller must not retry them.
    """
    if (not _repository(repository) or type(timeout) not in (int, float) or not 1 <= timeout <= 90
            or any(set(item) != {'basic_username_env', 'basic_password_env'} for item in (operator, reader))):
        raise ValueError('Unsupported fixed registry qualification')
    config, payload, subject, referrer = objects(binding)
    expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00')) if expires_at is not None else None
    if expiry is not None and expiry.tzinfo is None:
        raise ValueError('Registry qualification window must have a timezone')
    deadline = time.monotonic()+timeout
    output = Path(output)
    directory = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.listdir(directory):
            raise ValueError('Registry qualification output must be empty; retain prior attempts without retry')
        # Concurrent callers cannot both acquire this attempt, even if both
        # observed an empty directory. Claim before any network request.
        _write(directory, 'attempt.json', {'binding_sha256': binding, 'origin': origin, 'repository': repository})
    except BaseException:
        os.close(directory)
        raise
    report = {'schema_version': 1, 'kind': 'registry-storage-qualification-only',
        'binding_sha256': binding, 'origin': origin, 'repository': repository,
        'subject': descriptor(subject, MANIFEST), 'referrer': descriptor(referrer, MANIFEST),
        'status': 'unknown', 'canonical_success': False, 'mutation_effect': 'not_applied',
        'anonymous_read': 'unknown', 'reader_write': 'unknown', 'effects': [], 'requests': []}
    clients = []

    def checkpoint():
        if time.monotonic() >= deadline:
            raise APIError('deadline')
        if expiry is not None and datetime.now(timezone.utc) >= expiry:
            raise APIError('operator-window-expired')
        current, held = os.stat(output, follow_symlinks=False), os.fstat(directory)
        if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            raise APIError('qualification-output-changed')

    def read_client(credentials):
        checkpoint()
        client = HTTPSReadClient(origin, ca_bundle=ca_bundle, timeout=min(15, deadline-time.monotonic()),
            total_timeout=min(90, deadline-time.monotonic()), max_body_bytes=65536,
            max_total_bytes=262144, max_requests=16, **credentials,
            **({} if service_route is None else {'service_route': service_route}))
        client._deadline = deadline
        clients.append(client)
        return client

    def writer(credentials):
        checkpoint()
        client = _StagingRegistry(origin, repository=repository, ca_bundle=ca_bundle,
            deadline=deadline, **credentials,
            **({} if service_route is None else {'service_route': service_route}))
        clients.append(client)
        return client

    def effect(kind, body, call):
        checkpoint()
        item = {'sequence': len(report['effects']), 'kind': kind, 'outcome': 'unknown',
                'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}
        _write(directory, 'intent-'+str(item['sequence'])+'.json', item)
        report['effects'].append(item)
        report['mutation_effect'] = 'unknown'
        result = call()
        checkpoint()
        item['outcome'] = 'response-acknowledged-awaiting-independent-readback'
        _write(directory, 'response-'+str(item['sequence'])+'.json', item)
        return result

    def referrers(client, expected=None):
        response = client.get_bytes('/v2/'+repository+'/referrers/'+descriptor(subject, MANIFEST)['digest'],
            params={'artifactType': ARTIFACT}, accept=(INDEX,))
        doc = _json(response.body)
        if (response.status != 200 or response.media_type != INDEX or doc.get('schemaVersion') != 2
                or doc.get('mediaType') != INDEX or not isinstance(doc.get('manifests'), list)
                or len(doc['manifests']) > 128):
            raise APIError('native-referrers-unavailable')
        found = 0
        for entry in doc['manifests']:
            _descriptor(entry)
            if expected is not None and entry['digest'] == expected['digest']:
                if any(entry.get(k) != v for k, v in expected.items()) or entry.get('artifactType') != ARTIFACT:
                    raise APIError('referrer-identity-mismatch')
                found += 1
        if expected is not None and found != 1:
            raise APIError('referrer-discovery-unknown')
        checkpoint()

    try:
        # API capability is checked before any write. Negative authorization is
        # tested against actual existing content after independent readback.
        readiness = read_client(reader)
        for attempt in range(5):
            try:
                referrers(readiness)
                break
            except APIError as error:
                if error.code != 'transport' or attempt == 4:
                    raise
                checkpoint()
                time.sleep(min(1, max(0, deadline-time.monotonic())))
        first = writer(operator)
        first.authenticate(descriptor(subject, MANIFEST)['digest'])
        for raw, media in ((config, EMPTY), (payload, ARTIFACT)):
            location = effect('upload-session', b'', first.begin_blob)
            effect('blob', raw, lambda raw=raw, media=media, location=location:
                   first.finish_blob(location, io.BytesIO(raw), descriptor(raw, media)))
        effect('subject-manifest', subject, lambda: first.put_manifest(io.BytesIO(subject), descriptor(subject, MANIFEST)))
        second = writer(operator)
        second.authenticate(descriptor(referrer, MANIFEST)['digest'])
        effect('referrer-manifest', referrer, lambda: second.put_manifest(io.BytesIO(referrer), descriptor(referrer, MANIFEST)))
        fresh = read_client(reader)
        for raw, media, route in ((config, EMPTY, 'blobs'), (payload, ARTIFACT, 'blobs'),
                                  (subject, MANIFEST, 'manifests'), (referrer, MANIFEST, 'manifests')):
            expected = descriptor(raw, media)
            response = fresh.get_bytes('/v2/'+repository+'/'+route+'/'+expected['digest'],
                                       accept=(media, 'application/octet-stream') if route == 'blobs' else (media,))
            if (response.status != 200 or response.body != raw
                    or response.content_digest not in (None, expected['digest'])):
                raise APIError('independent-readback-mismatch')
            checkpoint()
        referrers(fresh, descriptor(referrer, MANIFEST))
        anonymous = read_client({})
        denied = anonymous.get_bytes('/v2/'+repository+'/manifests/'+descriptor(subject, MANIFEST)['digest'],
                                     accept=(MANIFEST,), allow_unauthorized=True)
        if denied.status != 401:
            raise APIError('anonymous-read-not-denied')
        report['anonymous_read'] = 'denied-401'
        limited = writer(reader)
        limited.authenticate(descriptor(subject, MANIFEST)['digest'])
        # Record this attempt even though a rejection is expected. An unexpected
        # accepted session is a real storage effect, not a harmless assertion.
        try:
            effect('reader-upload-denial-probe', b'', limited.begin_blob)
        except APIError as error:
            if error.code != 'staging-upload-not-acknowledged' or error.status not in (401, 403):
                raise
            report['effects'][-1]['outcome'] = 'denied-'+str(error.status)
            report['reader_write'] = 'denied-'+str(error.status)
        else:
            raise APIError('reader-write-not-denied')
        checkpoint()
        report.update(status='pass', mutation_effect='applied', independent_readback='exact-bytes',
                      referrer_discovery='exact-inclusion')
    except (APIError, ValueError, OSError, KeyError, TypeError) as error:
        report['error'] = error.code if isinstance(error, APIError) else 'qualification-invalid-or-interrupted'
    finally:
        for client in clients:
            report['requests'].extend(client.receipts)
            if isinstance(client, _StagingRegistry):
                client.close()
        terminal_identity = None
        try:
            checkpoint()
            terminal_identity = _write(directory, 'qualification.json', report)
            checkpoint()
        except BaseException:
            if terminal_identity is not None:
                _withdraw(directory, 'qualification.json', terminal_identity)
            os.close(directory)
            raise
        os.close(directory)
    return report


def main():
    # No caller-selected path/module/command. The fixed container mount contract
    # supplies this private file; credentials never enter Docker argv/env/logs.
    if sys.version_info < (3, 11) or len(sys.argv) != 1:
        raise SystemExit(2)
    try:
        fd = os.open('/input/qualification.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16384 or info.st_nlink != 1:
                raise ValueError
            raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError
        settings = _json(raw)
        if set(settings) != {'origin', 'repository', 'binding', 'expires_at', 'operator', 'reader'}:
            raise ValueError
        for actor in ('operator', 'reader'):
            if set(settings[actor]) != {'username', 'password'}:
                raise ValueError
            for field in ('username', 'password'):
                value = settings[actor][field]
                if not isinstance(value, str) or not 1 <= len(value) <= 1024 or any(ord(c) < 33 or ord(c) > 126 for c in value):
                    raise ValueError
                os.environ['REGISTRY_'+actor.upper()+'_'+field.upper()] = value
        credentials = {actor: {'basic_username_env': 'REGISTRY_'+actor.upper()+'_USERNAME',
                              'basic_password_env': 'REGISTRY_'+actor.upper()+'_PASSWORD'} for actor in ('operator', 'reader')}
        report = qualify(origin=settings['origin'], repository=settings['repository'],
            ca_bundle='/input/ca.crt', binding=settings['binding'], output='/evidence',
            expires_at=settings['expires_at'], **credentials)
        print(canonical(report).decode(), end='')
    except (APIError, ValueError, OSError, KeyError, TypeError):
        print('{"status":"unknown","error":"qualification-interrupted"}')
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
