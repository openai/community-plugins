"""Pinned descriptor-specific attestation bounds; no network or trust authority.

Only selected immutable envelope identities receive the larger fixed bounds.
Transport selections are process-local objects; serialized data cannot mint one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import stat
import threading
import time
import weakref

from .http import (APIError, _json, DEFAULT_JSON_VALUES as DEFAULT_PAYLOAD_JSON_VALUES,
                   MAX_SELECTED_JSON_VALUES as MAX_PAYLOAD_JSON_VALUES,
                   MAX_LARGE_ATTESTATION_JSON_VALUES as V3_MAX_PAYLOAD_JSON_VALUES)
from .registry_download_policy import REPOSITORY, RedirectPolicyError, _origin

DEFAULT_ENVELOPE = 4_000_000
DEFAULT_PAYLOAD = 2_500_000
MAX_ENVELOPE = 8 * 1024**2
MAX_PAYLOAD = 6 * 1024**2
V3_MAX_ENVELOPE = 16 * 1024**2
V3_MAX_PAYLOAD = 12 * 1024**2
MAX_DESCRIPTORS = 16
MAX_TOTAL = 64_000_000
_ISSUER = object()
_ISSUED = weakref.WeakKeyDictionary()
_ISSUED_LOCK = threading.Lock()
_POLICIES = weakref.WeakKeyDictionary()


class AttestationPolicyError(APIError):
    """Only fixed categories escape; no local path or untrusted values."""


def _require(condition, code):
    if not condition:
        raise AttestationPolicyError(code)


def _checkpoint(deadline):
    if deadline is not None and time.monotonic() >= deadline:
        raise AttestationPolicyError('attestation-policy-deadline')


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r'sha256:[a-f0-9]{64}', value)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value)


@dataclass(frozen=True, eq=False)
class SelectedAttestationBlob:
    origin: str
    repository: str
    subject_digest: str
    digest: str
    size_bytes: int
    policy_sha256: str
    max_envelope_bytes: int = MAX_ENVELOPE
    _issuer: object = field(default=None, init=False, repr=False)


def _selection_identity(selection):
    return (selection.origin, selection.repository, selection.subject_digest,
            selection.digest, selection.size_bytes, selection.policy_sha256, selection.max_envelope_bytes)


def validate_attestation_selection(selection, *, origin, repository, digest, size_bytes):
    """Admit only an unchanged selection issued by a validated local policy."""
    _require(type(selection) is SelectedAttestationBlob
             and selection._issuer is _ISSUER, 'attestation-selection-unissued')
    with _ISSUED_LOCK:
        issued = _ISSUED.get(selection)
    _require(issued is not None and issued == _selection_identity(selection),
             'attestation-selection-changed')
    _require((origin, repository, digest, size_bytes)
             == (selection.origin, selection.repository, selection.digest, selection.size_bytes)
             and type(size_bytes) is int, 'attestation-selection-scope-mismatch')
    return selection.max_envelope_bytes


@dataclass(frozen=True, eq=False)
class AttestationSizePolicy:
    origin: str
    repository: str
    subject_digest: str
    descriptors: tuple[tuple[str, int], ...]
    policy_sha256: str
    schema_version: int = 1
    payload_json_values: tuple[int, ...] = ()

    def __post_init__(self):
        try:
            _origin(self.origin)
            _require(isinstance(self.repository, str) and len(self.repository) <= 255
                and re.fullmatch(REPOSITORY, self.repository) and _digest(self.subject_digest)
                and _sha(self.policy_sha256), 'attestation-policy-scope-invalid')
            _require(type(self.descriptors) is tuple
                and 1 <= len(self.descriptors) <= MAX_DESCRIPTORS,
                'attestation-policy-descriptors-invalid')
            _require(type(self.schema_version) is int and self.schema_version in (1, 2, 3),
                     'attestation-policy-json-allowance-invalid')
            envelope_limit = V3_MAX_ENVELOPE if self.schema_version == 3 else MAX_ENVELOPE
            seen, total = set(), 0
            for row in self.descriptors:
                _require(type(row) is tuple and len(row) == 2 and _digest(row[0])
                    and type(row[1]) is int and 1 <= row[1] <= envelope_limit
                    and row[0] not in seen, 'attestation-policy-descriptors-invalid')
                seen.add(row[0])
                total += row[1]
            _require(total <= MAX_TOTAL, 'attestation-policy-total-limit')
            _require(type(self.payload_json_values) is tuple,
                'attestation-policy-json-allowance-invalid')
            if self.schema_version == 1:
                _require(not self.payload_json_values, 'attestation-policy-json-allowance-invalid')
            else:
                json_maximum = (V3_MAX_PAYLOAD_JSON_VALUES if self.schema_version == 3
                                else MAX_PAYLOAD_JSON_VALUES)
                _require(len(self.payload_json_values) == len(self.descriptors)
                    and all(type(limit) is int and DEFAULT_PAYLOAD_JSON_VALUES <= limit
                            <= json_maximum for limit in self.payload_json_values),
                    'attestation-policy-json-allowance-invalid')
        except AttestationPolicyError:
            raise
        except (RedirectPolicyError, ValueError, TypeError, AttributeError):
            raise AttestationPolicyError('attestation-policy-invalid') from None

    def require_scope(self, *, origin, repository, subject_digest):
        self._validated()
        _require((origin, repository, subject_digest)
            == (self.origin, self.repository, self.subject_digest), 'attestation-policy-scope-mismatch')
        return self

    def limits_for(self, digest, size_bytes):
        self._validated()
        _require(_digest(digest) and type(size_bytes) is int and size_bytes > 0,
                 'attestation-descriptor-invalid')
        selected = dict(self.descriptors).get(digest)
        if selected is not None:
            _require(size_bytes == selected, 'attestation-descriptor-size-mismatch')
            return ((V3_MAX_ENVELOPE, V3_MAX_PAYLOAD) if self.schema_version == 3
                    else (MAX_ENVELOPE, MAX_PAYLOAD))
        _require(size_bytes <= DEFAULT_ENVELOPE, 'attestation-descriptor-outside-policy')
        return DEFAULT_ENVELOPE, DEFAULT_PAYLOAD

    def select_blob(self, digest, size_bytes):
        self.limits_for(digest, size_bytes)
        if digest not in dict(self.descriptors):
            return None
        value = SelectedAttestationBlob(self.origin, self.repository, self.subject_digest,
                                        digest, size_bytes, self.policy_sha256,
                                        self.limits_for(digest, size_bytes)[0])
        object.__setattr__(value, '_issuer', _ISSUER)
        with _ISSUED_LOCK:
            _ISSUED[value] = _selection_identity(value)
        return value

    def payload_json_values_for(self, digest, size_bytes):
        # Revalidate the issued policy and exact descriptor size before granting
        # an allowance. Unselected envelopes and every v1 policy keep defaults.
        self.limits_for(digest, size_bytes)
        if self.schema_version in (2, 3):
            for (selected_digest, _), limit in zip(self.descriptors, self.payload_json_values):
                if digest == selected_digest:
                    return limit
        return DEFAULT_PAYLOAD_JSON_VALUES

    def identity_record(self):
        self._validated()
        descriptors = [{'digest': digest, 'size_bytes': size} for digest, size in self.descriptors]
        if self.schema_version in (2, 3):
            for row, limit in zip(descriptors, self.payload_json_values):
                row['max_payload_json_values'] = limit
        record = {'schema_version': self.schema_version, 'kind': 'registry-attestation-size-policy',
            'policy_sha256': self.policy_sha256, 'origin': self.origin,
            'repository': self.repository, 'subject_digest': self.subject_digest,
            'descriptors': descriptors,
            'selected_envelope_bytes': V3_MAX_ENVELOPE if self.schema_version == 3 else MAX_ENVELOPE,
            'selected_payload_bytes': V3_MAX_PAYLOAD if self.schema_version == 3 else MAX_PAYLOAD,
            'default_envelope_bytes': DEFAULT_ENVELOPE, 'default_payload_bytes': DEFAULT_PAYLOAD}
        if self.schema_version in (2, 3):
            record['default_payload_json_values'] = DEFAULT_PAYLOAD_JSON_VALUES
            record['maximum_payload_json_values'] = (V3_MAX_PAYLOAD_JSON_VALUES
                if self.schema_version == 3 else MAX_PAYLOAD_JSON_VALUES)
        return record

    def _identity(self):
        return (self.origin, self.repository, self.subject_digest, self.descriptors,
                self.policy_sha256, self.schema_version, self.payload_json_values)

    def _validated(self):
        with _ISSUED_LOCK:
            issued = _POLICIES.get(self)
        _require(type(self) is AttestationSizePolicy and issued is not None
            and issued == self._identity(), 'attestation-policy-unissued-or-changed')


def limits_for(policy, digest, size_bytes):
    if policy is None:
        return DEFAULT_ENVELOPE, DEFAULT_PAYLOAD
    _require(type(policy) is AttestationSizePolicy, 'attestation-policy-invalid')
    return policy.limits_for(digest, size_bytes)


def payload_json_values_for(policy, digest, size_bytes):
    if policy is None:
        return DEFAULT_PAYLOAD_JSON_VALUES
    _require(type(policy) is AttestationSizePolicy, 'attestation-policy-invalid')
    return policy.payload_json_values_for(digest, size_bytes)


def parse_attestation_size_policy(raw, expected_sha256=None, *, origin=None, repository=None,
                                  subject_digest=None, deadline=None):
    """Validate frozen bytes, optionally matching a selected scope and hash."""
    try:
        _checkpoint(deadline)
        _require(type(raw) is bytes and 0 < len(raw) <= 64000, 'attestation-policy-document-invalid')
        actual = hashlib.sha256(raw).hexdigest()
        _require(expected_sha256 is None or _sha(expected_sha256) and actual == expected_sha256,
                 'attestation-policy-identity-mismatch')
        value = _json(raw)
        _require(set(value) == {'schema_version', 'kind', 'origin', 'repository',
                               'subject_digest', 'descriptors'}
            and type(value['schema_version']) is int and value['schema_version'] in (1, 2, 3)
            and value['kind'] == 'registry-attestation-size-policy'
            and type(value['descriptors']) is list, 'attestation-policy-document-invalid')
        descriptors, json_allowances = [], []
        version = value['schema_version']
        for row in value['descriptors']:
            _checkpoint(deadline)
            fields = {'digest', 'size_bytes'} | ({'max_payload_json_values'} if version in (2, 3) else set())
            _require(type(row) is dict and set(row) == fields,
                     'attestation-policy-descriptors-invalid')
            descriptors.append((row['digest'], row['size_bytes']))
            if version in (2, 3):
                json_allowances.append(row['max_payload_json_values'])
        selected = AttestationSizePolicy(value['origin'], value['repository'], value['subject_digest'],
                                         tuple(descriptors), actual, version, tuple(json_allowances))
        with _ISSUED_LOCK:
            _POLICIES[selected] = selected._identity()
        scope = (origin, repository, subject_digest)
        if any(item is not None for item in scope):
            _require(all(item is not None for item in scope), 'attestation-policy-scope-mismatch')
            selected.require_scope(origin=origin, repository=repository, subject_digest=subject_digest)
        _checkpoint(deadline)
        return selected
    except AttestationPolicyError:
        raise
    except (APIError, ValueError, TypeError, KeyError):
        raise AttestationPolicyError('attestation-policy-document-invalid') from None


def load_attestation_size_policy(path, expected_sha256, *, origin, repository, subject_digest, deadline=None):
    """Freeze one exact regular file, rejecting symlinks and changing reads."""
    if path is None and expected_sha256 is None:
        return None
    directory = None
    try:
        _checkpoint(deadline)
        _require(isinstance(path, Path) and path.is_absolute() and '..' not in path.parts
            and _sha(expected_sha256), 'attestation-policy-selection-invalid')
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in path.parts[1:-1]:
            _checkpoint(deadline)
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            _require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= 64000,
                     'attestation-policy-file-invalid')
            raw = stream.read(64001)
            after = os.fstat(stream.fileno())
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        _require(all(getattr(before, key) == getattr(after, key) for key in keys)
            and len(raw) == before.st_size, 'attestation-policy-file-changed')
        return parse_attestation_size_policy(raw, expected_sha256, origin=origin,
            repository=repository, subject_digest=subject_digest, deadline=deadline)
    except AttestationPolicyError:
        raise
    except (OSError, ValueError, TypeError):
        raise AttestationPolicyError('attestation-policy-unavailable') from None
    finally:
        if directory is not None:
            os.close(directory)
