"""Bounded GET-only Git destination observation; no remote write authority."""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import ipaddress
import math
import os
from pathlib import Path
import re
import ssl
import stat
import time
from urllib.parse import urlsplit

from devsecops.adapters.artifacts import _open_directory
from devsecops.adapters.http import APIError, HTTPSReadClient, _bearer, _json
from devsecops.adapters.vulnerability_policy import checkpoint
from devsecops.factory.git_transport import (
    open_transport_consumer_output, require_prepared_git_transport,
)
from devsecops.runtime.context import canonical_bytes, seal

MAX_PROJECT_BYTES = 64 * 1024
MAX_ADVERTISEMENT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * (MAX_PROJECT_BYTES + MAX_ADVERTISEMENT_BYTES)
MAX_REFS = 10_000
MAX_CA_BYTES = 2_000_000
_OID = re.compile(r'[a-f0-9]{40}')
_SHA = re.compile(r'[a-f0-9]{64}')
_ENV = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}')
_MEDIA = 'application/x-git-upload-pack-advertisement'
_FLAGS = frozenset(('multi_ack', 'multi_ack_detailed', 'thin-pack', 'side-band', 'side-band-64k',
    'ofs-delta', 'shallow', 'deepen-since', 'deepen-not', 'deepen-relative', 'no-progress',
    'include-tag', 'allow-tip-sha1-in-want', 'allow-reachable-sha1-in-want',
    'allow-any-sha1-in-want', 'filter', 'no-done'))


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _origin(value):
    """Canonical literal HTTPS origin, matching the existing client's grammar."""
    try:
        if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
            raise ValueError
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (parsed.scheme != 'https' or not host or parsed.username is not None
                or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
                or '%' in host or '\\' in value):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
            canonical = str(address)
            authority = '[' + canonical + ']' if address.version == 6 else canonical
        except ValueError:
            if (len(host) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                    for part in host.split('.')) or all(part.isdigit() for part in host.split('.'))):
                raise ValueError
            authority = host
        if port is not None and (type(port) is not int or not 1 <= port <= 65535 or port == 443):
            raise ValueError
        if value != 'https://' + authority + (':' + str(port) if port is not None else ''):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('One canonical HTTPS Git origin is required') from None
    return value


def _ref(value, *, head=False, peeled=False):
    if head and value == 'HEAD':
        return value
    ordinary = value
    if peeled and isinstance(value, str) and value.endswith('^{}'):
        ordinary = value[:-3]
        if not ordinary.startswith('refs/tags/'):
            raise APIError('git-advertisement-ref')
    if (not isinstance(ordinary, str) or not 1 <= len(ordinary) <= 1024
            or not ordinary.startswith('refs/') or len(ordinary.split('/')) < 3
            or '..' in ordinary or '@{' in ordinary or ordinary.endswith('.')
            or any(not part or part.startswith('.') or part.endswith('.lock')
                or not re.fullmatch(r'[A-Za-z0-9_+@=.-]+', part) for part in ordinary.split('/'))):
        raise APIError('git-advertisement-ref')
    return value


def _credential_reference(value):
    if not isinstance(value, dict):
        raise ValueError('Explicit read credential references are required')
    if value.get('kind') == 'bearer' and set(value) == {'kind', 'token_env'}:
        names = [value['token_env']]
    elif value.get('kind') == 'basic' and set(value) == {'kind', 'username_env', 'password_env'}:
        names = [value['username_env'], value['password_env']]
    else:
        raise ValueError('Unsupported read credential reference')
    if any(not isinstance(name, str) or not _ENV.fullmatch(name) for name in names) or len(set(names)) != len(names):
        raise ValueError('Bounded distinct environment references are required')
    return set(names)


def destination_configuration(raw):
    """Closed non-secret selection. No remote URL selects a request destination."""
    value = _json(raw)
    fields = {'schema_version', 'kind', 'origin', 'project_id', 'repository_path',
        'base_ref', 'base_commit', 'target_ref', 'path_with_namespace',
        'api_read_credential', 'git_read_credential', 'trust'}
    if (set(value) != fields or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['kind'] not in ('mission-git-destination-observation-configuration',
                                    'owned-git-destination-observation-configuration')
            or type(value['project_id']) is not int or not 1 <= value['project_id'] < 2**63
            or not isinstance(value['base_commit'], str) or not _OID.fullmatch(value['base_commit'])
            or value['base_commit'] == '0' * 40):
        raise ValueError('Exact Git destination observation configuration is required')
    _origin(value['origin'])
    path = value['repository_path']
    if (not isinstance(path, str) or not 1 <= len(path) <= 1900 or not path.startswith('/')
            or not path.endswith('.git') or len(path.split('/')) < 3
            or any(not re.fullmatch(r'[A-Za-z0-9_~.-]+', part) or part in ('.', '..')
                for part in path[1:].split('/'))):
        raise ValueError('One literal repository HTTP path is required')
    namespace = value['path_with_namespace']
    if namespace is not None and (not isinstance(namespace, str) or '/' + namespace + '.git' != path):
        raise ValueError('Selected project namespace must match the repository path')
    for name in ('base_ref', 'target_ref'):
        if (not isinstance(value[name], str) or not value[name].startswith('refs/heads/')
                or _ref(value[name]) != value[name]):
            raise ValueError('Exact branch refs are required')
    if value['base_ref'] == value['target_ref']:
        raise ValueError('Base and prospective target refs must be distinct')
    if value['kind'] == 'owned-git-destination-observation-configuration':
        if any(value[kind+'_read_credential'] != {'kind':'owned-reader','transport':kind} for kind in ('api','git')):
            raise ValueError('Exact owned reader role selectors required')
    else:
        api = _credential_reference(value['api_read_credential'])
        git = _credential_reference(value['git_read_credential'])
        if value['api_read_credential']['kind'] != 'bearer' or api & git:
            raise ValueError('Separate API and Git read credential references are required')
    trust = value['trust']
    if not isinstance(trust, dict):
        raise ValueError('Explicit TLS trust selection is required')
    if trust.get('mode') == 'system' and set(trust) == {'mode'}:
        pass
    elif trust.get('mode') == 'bundle' and set(trust) == {'mode', 'path', 'sha256'}:
        if (not isinstance(trust['path'], str) or not Path(trust['path']).is_absolute()
                or '..' in Path(trust['path']).parts or not isinstance(trust['sha256'], str)
                or not _SHA.fullmatch(trust['sha256'])):
            raise ValueError('Pinned absolute CA bundle required')
    else:
        raise ValueError('Unsupported TLS trust selection')
    return value


def _read_input(path, maximum, deadline):
    checkpoint(deadline)
    if not isinstance(path, Path) or not path.is_absolute() or '..' in path.parts:
        raise ValueError('Absolute stable input path required')
    parent = _open_directory(path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
            raise ValueError('Input size or type unsupported')
        raw = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode', 'st_nlink')
    identity = tuple(getattr(before, key) for key in fields)
    if len(raw) != before.st_size or identity != tuple(getattr(after, key) for key in fields):
        raise ValueError('Input changed during observation')
    checkpoint(deadline)
    return raw, identity


def _authorization(reference):
    if reference.get('kind') == 'owned-reader':
        raise ValueError('Owned credentials require the original direct reader origin')
    if reference['kind'] == 'bearer':
        return _bearer(os.environ.get(reference['token_env'], ''))
    username = os.environ.get(reference['username_env'], '')
    password = os.environ.get(reference['password_env'], '')
    if (not 1 <= len(username) <= 255 or ':' in username or not 1 <= len(password) <= 8192
            or any(ord(char) < 33 or ord(char) > 126 for char in username + password)):
        raise APIError('credential-unavailable')
    return 'Basic ' + base64.b64encode((username + ':' + password).encode('ascii')).decode('ascii')


def _capabilities(raw):
    if not raw or len(raw) > 4096:
        raise APIError('git-advertisement-capability')
    try:
        values = raw.decode('ascii').split(' ')
    except UnicodeError:
        raise APIError('git-advertisement-capability') from None
    if len(values) > 64:
        raise APIError('git-advertisement-capability')
    result = {}
    for token in values:
        name, equals, value = token.partition('=')
        if not token or len(token) > 1024 or name in result:
            raise APIError('git-advertisement-capability')
        if name in _FLAGS and not equals:
            result[name] = True
        elif name == 'object-format' and equals and value == 'sha1':
            result[name] = value
        elif name == 'agent' and equals and re.fullmatch(r'[A-Za-z0-9._/+()-]{1,128}', value):
            result[name] = value
        elif name == 'symref' and equals and value.startswith('HEAD:refs/heads/'):
            result[name] = _ref(value[5:])
        else:
            raise APIError('git-advertisement-capability')
    return result


def _advertisement(raw, selected, deadline):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_ADVERTISEMENT_BYTES:
        raise APIError('git-advertisement-limit')
    offset = 0

    def packet():
        nonlocal offset
        checkpoint(deadline)
        if offset + 4 > len(raw) or not re.fullmatch(b'[0-9a-f]{4}', raw[offset:offset + 4]):
            raise APIError('git-advertisement-framing')
        size = int(raw[offset:offset + 4], 16)
        offset += 4
        if size == 0:
            return None
        if not 4 < size <= 65520 or offset + size - 4 > len(raw):
            raise APIError('git-advertisement-framing')
        value = raw[offset:offset + size - 4]
        offset += size - 4
        return value

    if packet() != b'# service=git-upload-pack\n' or packet() is not None:
        raise APIError('git-advertisement-protocol')
    refs, capabilities = {}, None
    while True:
        row = packet()
        if row is None:
            break
        if len(refs) >= MAX_REFS or not row.endswith(b'\n') or len(row) > 8192:
            raise APIError('git-advertisement-limit')
        row = row[:-1]
        if not refs:
            if row.count(b'\0') != 1:
                raise APIError('git-advertisement-capability')
            row, encoded = row.split(b'\0')
            capabilities = _capabilities(encoded)
        elif b'\0' in row:
            raise APIError('git-advertisement-capability')
        try:
            oid, name = row.decode('ascii').split(' ')
        except (UnicodeError, ValueError):
            raise APIError('git-advertisement-ref') from None
        if not _OID.fullmatch(oid) or oid == '0' * 40 or name in refs:
            raise APIError('git-advertisement-ref')
        refs[_ref(name, head=True, peeled=True)] = oid
    if offset != len(raw) or not refs or capabilities is None:
        raise APIError('git-advertisement-framing')
    for name in refs:
        if name.endswith('^{}') and name[:-3] not in refs:
            raise APIError('git-advertisement-ref')
    if 'symref' in capabilities:
        target = capabilities['symref']
        if 'HEAD' not in refs or target not in refs or refs['HEAD'] != refs[target]:
            raise APIError('git-advertisement-symref')
    if refs.get(selected['base_ref']) != selected['base_commit']:
        raise APIError('git-destination-base-mismatch')
    checkpoint(deadline)
    return {'refs': refs, 'capabilities': capabilities,
        'base_ref': selected['base_ref'], 'base_commit': selected['base_commit'],
        'target_ref': selected['target_ref'], 'target_observed_oid': refs.get(selected['target_ref'])}


def _project(raw, selected):
    value = _json(raw)
    if (type(value.get('id')) is not int or value['id'] != selected['project_id']
            or value.get('http_url_to_repo') != selected['origin'] + selected['repository_path']
            or (selected['path_with_namespace'] is not None
                and value.get('path_with_namespace') != selected['path_with_namespace'])):
        raise APIError('git-destination-project-mismatch')
    return {'id': value['id'], 'http_url_to_repo': value['http_url_to_repo'],
        'path_with_namespace': selected['path_with_namespace']}


def observe_git_destination(*, transport, run_scope, source_commit, deployment_commit,
        proposal_configuration_sha256, configuration_sha256, config, expected_config_sha256,
        output, timeout=60):
    """Observe one selected destination without granting write or object-omission authority."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 60
            or not isinstance(config, Path) or not config.is_absolute() or '..' in config.parts
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts
            or not isinstance(expected_config_sha256, str) or not _SHA.fullmatch(expected_config_sha256)):
        raise ValueError('Pinned selection, new absolute output and bounded timeout are required')
    deadline = time.monotonic() + timeout
    scope = deepcopy(dict(run_scope=run_scope, source_commit=source_commit, deployment_commit=deployment_commit,
        proposal_configuration_sha256=proposal_configuration_sha256, configuration_sha256=configuration_sha256))
    binding = require_prepared_git_transport(transport, **scope, deadline=deadline)
    raw, identity = _read_input(config, 64_000, deadline)
    if _sha(raw) != expected_config_sha256:
        raise ValueError('Selected Git destination configuration changed')
    selected = destination_configuration(raw)
    if binding['base_commit'] != selected['base_commit'] or deployment_commit != selected['base_commit']:
        raise ValueError('Git destination base differs from the direct local plan')
    inputs = [(config, 64_000, _sha(raw), identity)]
    clients, authorizations = {}, None
    report = {'schema_version': 1, 'kind': 'mission-git-destination-observation',
        'decision': 'unknown', 'execution_status': 'partial', 'factory_decision': 'unknown',
        'remote_possession': 'unverified', 'omission_authorized': False,
        'delivery_authority': 'not-issued', 'scope': 'authenticated-destination-observation-only',
        'configuration_sha256': expected_config_sha256, 'transport_scope': deepcopy(scope),
        'image_publication': deepcopy(binding['image_publication']),
        'new_commit': binding['new_commit'],
        'selection': {key: deepcopy(selected[key]) for key in
            ('origin', 'project_id', 'repository_path', 'base_ref', 'base_commit', 'target_ref', 'path_with_namespace')},
        'snapshots': [], 'responses': [], 'transport_receipts': {}, 'gaps': [], 'started_at': _now()}
    with open_transport_consumer_output(transport, **scope, output=output, deadline=deadline) as admitted:
        retained = {}

        def audit():
            checkpoint(deadline)
            admitted.audit()
            if require_prepared_git_transport(transport, **scope, deadline=deadline) != binding:
                raise ValueError('Direct Git transport binding changed')
            for path, maximum, digest, original in inputs:
                observed, current = _read_input(path, maximum, deadline)
                if original != current or _sha(observed) != digest:
                    raise ValueError('Selected Git observation input changed')
            checkpoint(deadline)

        def store(name, contents):
            audit()
            ref = {'sha256': _sha(contents), 'bytes': len(contents)}
            if name not in retained:
                if admitted.write_bytes(name, contents) != ref:
                    raise ValueError('Retained Git observation differs from its bytes')
                retained[name] = ref
            elif retained[name] != ref:
                raise ValueError('Retained Git observation name collision')
            audit()
            return {'file': name, **ref}

        total = 0

        def request(kind):
            nonlocal total
            audit()
            if len(report['responses']) >= 4:
                raise APIError('request-limit')
            path = (f'/api/v4/projects/{selected["project_id"]}' if kind == 'api'
                else selected['repository_path'] + '/info/refs')
            params = None if kind == 'api' else {'service': 'git-upload-pack'}
            media, limit = ('application/json', MAX_PROJECT_BYTES) if kind == 'api' else (_MEDIA, MAX_ADVERTISEMENT_BYTES)
            response = clients[kind].get_bytes(path, params=params, accept=(media,))
            audit()
            if (response.status != 200 or response.media_type != media or response.pagination
                    or not isinstance(response.body, bytes) or not 0 < len(response.body) <= limit):
                raise APIError('git-destination-response')
            total += len(response.body)
            if total > MAX_TOTAL_BYTES:
                raise APIError('body-limit')
            ref = store('sha256-' + _sha(response.body) + '.blob', response.body)
            report['responses'].append({'index': len(report['responses']) + 1, 'kind': kind,
                'observed_at': _now(), 'request_sha256': _sha((path + ('?service=git-upload-pack' if kind == 'git' else '')).encode()),
                'media_type': media, **ref})
            return response.body

        try:
            store('git-destination-config.json', raw)
            trust_context = None
            if selected['trust']['mode'] == 'bundle':
                ca_path = Path(selected['trust']['path'])
                ca, ca_identity = _read_input(ca_path, MAX_CA_BYTES, deadline)
                if _sha(ca) != selected['trust']['sha256']:
                    raise ValueError('Selected Git CA bundle changed')
                inputs.append((ca_path, MAX_CA_BYTES, _sha(ca), ca_identity))
                store('ca.pem', ca)
                try:
                    trust_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    trust_context.minimum_version = ssl.TLSVersion.TLSv1_2
                    trust_context.load_verify_locations(cadata=ca.decode('ascii'))
                except (UnicodeError, ValueError, ssl.SSLError):
                    raise APIError('invalid-ca') from None
            for kind, limit in (('api', MAX_PROJECT_BYTES), ('git', MAX_ADVERTISEMENT_BYTES)):
                client = HTTPSReadClient(selected['origin'], timeout=min(30, timeout), total_timeout=timeout,
                    max_requests=2, max_body_bytes=limit, max_total_bytes=2 * limit)
                if client.origin != selected['origin']:
                    raise ValueError('Git origin was normalized')
                client._deadline = deadline
                if trust_context is not None:
                    client._context = trust_context
                clients[kind] = client
            audit()
            authorizations = {kind: _authorization(selected[kind + '_read_credential']) for kind in ('api', 'git')}
            for kind, client in clients.items():
                client._authorization = lambda chosen=authorizations[kind]: chosen
            for _ in range(2):
                project = _project(request('api'), selected)
                advertisement = _advertisement(request('git'), selected, deadline)
                snapshot = {'project': project, 'advertisement': advertisement}
                if report['snapshots'] and snapshot != report['snapshots'][0]:
                    raise APIError('git-destination-changed')
                report['snapshots'].append(snapshot)
            report['decision'], report['execution_status'] = 'pass', 'complete'
            report['observed_body_bytes'] = total
            report['atomic_snapshot'] = False
            report['target_absence_meaning'] = 'only absent from these authenticated advertisements'
            audit()
        except APIError as error:
            report['gaps'].append(error.code)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            report['gaps'].append('git-destination-observation-incomplete')
        finally:
            report['transport_receipts'] = {kind: client.receipts for kind, client in clients.items()}
            for client in clients.values():
                client._authorization = lambda: None
            authorizations = None
        if report['gaps']:
            report['decision'], report['execution_status'] = 'unknown', 'partial'
        report['retained'] = deepcopy(retained)
        report['finished_at'] = _now()
        report = seal(report, 'observation_sha256')
        audit()
        admitted.write_bytes('git-destination.json', canonical_bytes(report), terminal=True)
        audit()
        return report
