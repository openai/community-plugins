"""Strict data-only profiles. Candidate repository content cannot select policy."""
from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path, PurePosixPath
from jsonschema import Draft202012Validator


PROFILE_SCHEMA_V1 = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "id", "revision", "targets", "policy"],
    "properties": {
        "schema_version": {"const": 1},
        "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,63}$"},
        "revision": {"type": "string", "minLength": 1, "maxLength": 80},
        "description": {"type": "string", "maxLength": 1000},
        "configured": {"type": "boolean"},
        "targets": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["name", "type", "path", "namespace", "kubernetes_version"],
            "properties": {
                "name": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,62}$"},
                "type": {"enum": ["helm", "manifests"]},
                "path": {"type": "string", "minLength": 1, "maxLength": 300},
                "namespace": {"type": "string", "pattern": "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", "maxLength": 63},
                "release": {"type": "string", "pattern": "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$", "maxLength": 53},
                "mode": {"enum": ["install", "upgrade"]},
                "values": {"type": "array", "maxItems": 16, "items": {"type": "string", "maxLength": 300}},
                "api_versions": {"type": "array", "maxItems": 100,
                    "items": {"type": "string", "pattern": "^[A-Za-z0-9./-]{1,120}$"}},
                "kubernetes_version": {"type": "string", "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$"},
            }}},
        "policy": {"type": "object", "additionalProperties": False, "properties": {
            "require_image_digest": {"type": "boolean"},
            "allowed_registries": {"type": "array", "maxItems": 100,
                "items": {"type": "string", "pattern": "^[a-zA-Z0-9.:-]{1,253}$"}},
            "required_labels": {"type": "array", "maxItems": 100,
                "items": {"type": "string", "pattern": "^[A-Za-z0-9._/-]{1,253}$"}},
            "gate": {"type": "object", "additionalProperties": False, "properties": {
                "minimum_severity": {"enum": ["low", "medium", "high", "critical"]},
                "changes": {"type": "array", "minItems": 1, "uniqueItems": True,
                    "items": {"enum": ["introduced", "worsened", "preexisting"]}},
            }},
            "rule_severities": {"type": "object", "maxProperties": 100,
                "additionalProperties": {"enum": ["low", "medium", "high", "critical"]}},
            "exceptions": {"type": "array", "maxItems": 100, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "rule_id", "target", "resource", "reason", "owner", "expires"],
                "properties": {k: {"type": "string", "minLength": 1, "maxLength": 1000}
                    for k in ("id", "rule_id", "target", "resource", "reason", "owner", "expires")},
            }},
        }},
    },
}

# Existing profiles retain their exact v1 meaning. Kustomize is an explicit
# additive version rather than silently widening a published v1 target enum.
PROFILE_SCHEMA_V2 = deepcopy(PROFILE_SCHEMA_V1)
PROFILE_SCHEMA_V2['properties']['schema_version'] = {'const': 2}
_target_v2 = PROFILE_SCHEMA_V2['properties']['targets']['items']
_target_v2['properties']['type']['enum'].append('kustomize')
_target_v2['properties']['source_root'] = {'type': 'string', 'minLength': 1, 'maxLength': 300}
_target_v2['allOf'] = [{
    'if': {'properties': {'type': {'const': 'kustomize'}}},
    'then': {'required': ['source_root'], 'not': {'anyOf': [
        {'required': [key]} for key in ('release', 'mode', 'values', 'api_versions')]}},
    'else': {'not': {'required': ['source_root']}},
}]
PROFILE_SCHEMA = {
    '$schema': 'https://json-schema.org/draft/2020-12/schema',
    '$id': 'urn:devsecops:profile',
    'oneOf': [PROFILE_SCHEMA_V1, PROFILE_SCHEMA_V2],
}


def relative_path(value: str) -> str:
    p = PurePosixPath(value)
    if (not value or value.startswith(('/', '-')) or '\\' in value or '\x00' in value
            or any(part in ('', '.', '..', '.git') for part in value.split('/'))):
        raise ValueError("Input paths must be normalized relative paths without traversal or .git")
    if p.is_absolute():
        raise ValueError("Input paths must be relative")
    return p.as_posix()


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("Duplicate JSON key in profile")
        result[k] = v
    return result


def load_profile(path: Path, repo: Path, selected: list[str] | None = None, base_sha: str | None = None) -> tuple[dict, str]:
    lexical = Path(os.path.abspath(path))
    resolved = path.resolve()
    baseline_path = lexical if lexical.is_relative_to(repo.resolve()) else resolved
    if baseline_path.is_relative_to(repo.resolve()):
        if not base_sha:
            raise ValueError("Repository profiles require an immutable baseline revision; candidate policy is not trusted")
        from .snapshot import git
        relative = relative_path(str(baseline_path.relative_to(repo.resolve())))
        tree = git(repo, 'ls-tree', base_sha, '--', relative)
        if not tree.startswith((b'100644 blob ', b'100755 blob ')):
            raise ValueError('Baseline profile must be a regular tracked file')
        raw = git(repo, 'show', base_sha+':'+relative, limit=128_000)
    else:
        if resolved.stat().st_size > 128_000:
            raise ValueError("Profile size limit exceeded")
        raw = resolved.read_bytes()
    try:
        profile = json.loads(raw, object_pairs_hook=_unique)
    except (json.JSONDecodeError, UnicodeError):
        raise ValueError("Invalid JSON profile") from None
    errors = list(Draft202012Validator(PROFILE_SCHEMA).iter_errors(profile))
    if errors:
        raise ValueError("Profile does not match the data-only profile schema")
    if profile.get("configured") is False:
        raise ValueError("Example profile is unconfigured; copy and explicitly configure it before use")
    from .policy import validate_policy
    validate_policy(profile['policy'])
    names = [t['name'] for t in profile['targets']]
    if len(names) != len(set(names)):
        raise ValueError("Profile target names must be unique")
    for target in profile['targets']:
        relative_path(target['path'])
        for value in target.get('values', []):
            relative_path(value)
        if target['type'] == 'helm' and not target.get('release'):
            raise ValueError("Helm targets require an explicit release name")
        if target['type'] == 'manifests' and target.get('values'):
            raise ValueError("Manifest targets do not accept Helm values")
        if target['type'] == 'kustomize':
            root = PurePosixPath(relative_path(target['source_root']))
            if not PurePosixPath(target['path']).is_relative_to(root):
                raise ValueError('Kustomize path must be within its declared source_root')
    if selected:
        if set(selected) - set(names):
            raise ValueError("Selected target is absent from the trusted profile")
        profile['targets'] = [t for t in profile['targets'] if t['name'] in selected]
    return profile, hashlib.sha256(raw).hexdigest()
