"""Explicit S3 blob URL selection; no network operations.

The registry session must supply the independently selected policy, preserve
its shared transport budgets, and create a fresh download client without the
registry's credentials, cookies, client certificate or trust context.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import stat
from urllib.parse import parse_qsl, urlencode, urlsplit


REPOSITORY = r'[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*(?:/[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*)*'
REQUIRED_QUERY = frozenset({'X-Amz-Algorithm', 'X-Amz-Credential', 'X-Amz-Date',
    'X-Amz-Expires', 'X-Amz-Signature', 'X-Amz-SignedHeaders'})


class RedirectPolicyError(ValueError):
    """Only fixed categories escape; URL and credential values are excluded."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _require(condition, code):
    if not condition:
        raise RedirectPolicyError(code)


def _origin(value):
    _require(isinstance(value, str) and 0 < len(value) <= 2048, 'invalid-storage-origin')
    parsed = urlsplit(value)
    host, port = parsed.hostname, parsed.port
    _require(parsed.scheme == 'https' and host is not None and parsed.username is None
        and parsed.password is None and not parsed.query and not parsed.fragment
        and parsed.path == '' and '\\' not in value and '%' not in value,
        'invalid-storage-origin')
    _require(len(host) <= 253 and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p)
        for p in host.split('.')) and not all(p.isdigit() for p in host.split('.')),
        'invalid-storage-origin')
    _require(port is None or 1 <= port <= 65535, 'invalid-storage-origin')
    canonical = 'https://' + host + (':' + str(port) if port is not None else '')
    _require(value == canonical, 'noncanonical-storage-origin')
    return canonical


@dataclass(frozen=True)
class BlobDownloadTarget:
    origin: str
    expected_digest: str
    _path: str = field(repr=False)
    _query: tuple[tuple[str, str], ...] = field(repr=False)
    _url_sha256: str = field(repr=False)

    def request_parts(self):
        """Transport use only. Do not log or serialize the signed query values."""
        return self._path, dict(self._query)

    def policy_record(self):
        """A selection record, not evidence that any download was performed."""
        return {'origin': self.origin, 'expected_digest': self.expected_digest,
            'path_sha256': hashlib.sha256(self._path.encode()).hexdigest(),
            'url_sha256': self._url_sha256,
            'query_parameter_names': sorted(key for key, _ in self._query),
            'decision': 'selected-download-target', 'source_response_status': 307,
            'max_followup_hops': 1, 'transport_requirements': {
                'omit_headers': ['Authorization', 'Cookie'], 'client_certificate': 'none'}}


@dataclass(frozen=True)
class RegistryBlobPolicy:
    registry_origin: str
    repositories: tuple[str, ...]
    storage_origins: tuple[str, ...]
    policy_sha256: str | None = None

    def __post_init__(self):
        try:
            _origin(self.registry_origin)
            _require(type(self.repositories) is tuple and 1 <= len(self.repositories) <= 32
                and len(set(self.repositories)) == len(self.repositories)
                and all(isinstance(p, str) and len(p) <= 255 and re.fullmatch(REPOSITORY, p)
                    for p in self.repositories), 'invalid-registry-scope')
            _require(type(self.storage_origins) is tuple and 1 <= len(self.storage_origins) <= 8
                and len(set(self.storage_origins)) == len(self.storage_origins), 'invalid-storage-scope')
            for origin in self.storage_origins:
                _origin(origin)
                _require(origin != self.registry_origin, 'registry-origin-is-not-a-download-origin')
            _require(self.policy_sha256 is None or isinstance(self.policy_sha256, str)
                and re.fullmatch(r'[a-f0-9]{64}', self.policy_sha256), 'invalid-policy-identity')
        except RedirectPolicyError:
            raise
        except (ValueError, TypeError, AttributeError):
            raise RedirectPolicyError('invalid-blob-policy') from None

    def identity_record(self):
        return {'schema_version': 1, 'kind': 'registry-blob-download-policy',
            'policy_sha256': self.policy_sha256, 'registry_origin': self.registry_origin,
            'repositories': list(self.repositories), 'storage_origins': list(self.storage_origins)}

    def select(self, *, registry_origin, repository, request_path, digest,
               status, location_headers, previous_hops=0):
        """Select one scoped 307 without treating its URL as publisher authority."""
        try:
            _require(registry_origin == self.registry_origin and repository in self.repositories,
                'registry-scope-mismatch')
            _require(isinstance(digest, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', digest),
                'invalid-blob-digest')
            _require(request_path == '/v2/' + repository + '/blobs/' + digest,
                'redirect-route-is-not-selected-blob')
            _require(type(status) is int and status == 307, 'unsupported-redirect-status')
            _require(type(previous_hops) is int and previous_hops == 0, 'redirect-hop-limit')
            _require(type(location_headers) in (tuple, list) and len(location_headers) == 1,
                'ambiguous-redirect-location')
            location = location_headers[0]
            _require(isinstance(location, str) and 0 < len(location) <= 8192
                and all(33 <= ord(c) <= 126 for c in location) and '\\' not in location,
                'invalid-redirect-location')
            target = urlsplit(location)
            _require(target.username is None and target.password is None and not target.fragment,
                'invalid-redirect-location')
            origin = _origin(target.scheme + '://' + target.netloc)
            _require(origin in self.storage_origins, 'storage-origin-not-selected')
            _require(re.fullmatch(r'/[A-Za-z0-9_./~:-]{1,2047}', target.path)
                and '//' not in target.path
                and not any(p in ('.', '..') for p in target.path.split('/')),
                'unsupported-download-path')
            pairs = parse_qsl(target.query, keep_blank_values=True, strict_parsing=True,
                encoding='utf-8', errors='strict', max_num_fields=7)
            values = dict(pairs)
            _require(len(pairs) == len(values) and REQUIRED_QUERY <= set(values)
                and not set(values) - REQUIRED_QUERY - {'X-Amz-Security-Token'},
                'unsupported-signed-query')
            _require(urlencode(pairs) == target.query, 'noncanonical-signed-query')
            _require(values['X-Amz-Algorithm'] == 'AWS4-HMAC-SHA256'
                and values['X-Amz-SignedHeaders'] == 'host'
                and re.fullmatch(r'[a-f0-9]{64}', values['X-Amz-Signature']),
                'unsupported-signed-query')
            credential = re.fullmatch(r'[A-Z0-9]{16,128}/([0-9]{8})/[a-z0-9-]{1,64}/s3/aws4_request',
                values['X-Amz-Credential'])
            _require(credential is not None and re.fullmatch(r'[0-9]{8}T[0-9]{6}Z', values['X-Amz-Date'])
                and credential[1] == values['X-Amz-Date'][:8], 'unsupported-signed-query')
            datetime.strptime(values['X-Amz-Date'], '%Y%m%dT%H%M%SZ')
            _require(re.fullmatch(r'[1-9][0-9]{0,5}', values['X-Amz-Expires'])
                and int(values['X-Amz-Expires']) <= 604800, 'unsupported-signed-query')
            if 'X-Amz-Security-Token' in values:
                _require(0 < len(values['X-Amz-Security-Token']) <= 8192
                    and all(33 <= ord(c) <= 126 for c in values['X-Amz-Security-Token']),
                    'unsupported-signed-query')
            return BlobDownloadTarget(origin, digest, target.path, tuple(pairs),
                hashlib.sha256(location.encode()).hexdigest())
        except RedirectPolicyError:
            raise
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise RedirectPolicyError('invalid-redirect-location') from None


def parse_blob_download_policy(raw):
    """Parse a closed, immutable policy document; this grants no network action."""
    from .http import APIError, _json
    try:
        _require(isinstance(raw, bytes) and 0 < len(raw) <= 64000, 'invalid-policy-document')
        value = _json(raw)
        _require(set(value) == {'schema_version', 'kind', 'registry_origin', 'repositories', 'storage_origins'}
            and type(value['schema_version']) is int and value['schema_version'] == 1
            and value['kind'] == 'registry-blob-download-policy'
            and type(value['repositories']) is list and type(value['storage_origins']) is list,
            'invalid-policy-document')
        return RegistryBlobPolicy(value['registry_origin'], tuple(value['repositories']),
            tuple(value['storage_origins']), hashlib.sha256(raw).hexdigest())
    except RedirectPolicyError:
        raise
    except (APIError, ValueError, TypeError, KeyError):
        raise RedirectPolicyError('invalid-policy-document') from None


def load_blob_download_policy(path, expected_sha256, *, registry_origin, allowed_repositories):
    """Read one pinned regular file without following links; intersect service scope."""
    if path is None and expected_sha256 is None:
        return None
    directory = None
    try:
        _require(isinstance(path, Path) and path.is_absolute() and '..' not in path.parts
            and isinstance(expected_sha256, str) and re.fullmatch(r'[a-f0-9]{64}', expected_sha256),
            'invalid-policy-selection')
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in path.parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            _require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= 64000,
                'invalid-policy-file')
            raw = stream.read(64001)
            after = os.fstat(stream.fileno())
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        _require(all(getattr(before, k) == getattr(after, k) for k in keys)
            and len(raw) == before.st_size and hashlib.sha256(raw).hexdigest() == expected_sha256,
            'policy-identity-mismatch')
        policy = parse_blob_download_policy(raw)
        _require(policy.registry_origin == registry_origin
            and isinstance(allowed_repositories, (list, tuple)) and 1 <= len(allowed_repositories) <= 32
            and all(isinstance(p, str) and len(p) <= 255 and re.fullmatch(REPOSITORY, p)
                    for p in allowed_repositories)
            and len(set(allowed_repositories)) == len(allowed_repositories), 'policy-service-scope-mismatch')
        effective = tuple(p for p in policy.repositories if p in allowed_repositories)
        _require(bool(effective), 'policy-service-scope-mismatch')
        # Keep the original file identity while narrowing the in-memory scope.
        return replace(policy, repositories=effective)
    except RedirectPolicyError:
        raise
    except (OSError, ValueError, TypeError):
        raise RedirectPolicyError('policy-unavailable') from None
    finally:
        if directory is not None:
            os.close(directory)
