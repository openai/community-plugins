"""Direct local signature -> immutable OCI evidence -> verified registry readback."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import math
import os
from pathlib import Path
import re
import time
from weakref import WeakKeyDictionary

from devsecops.adapters.http import _json
from devsecops.adapters.mission_evidence import (
    ARTIFACT_TYPE, EMPTY_TYPE, FILES, EvidenceReader, artifact_manifest, bind_payload, descriptor, reference,
)
from devsecops.adapters.oci import MANIFEST
from devsecops.adapters.signatures import _public_key, _snapshot, verify_image_signature
from devsecops.adapters.staging_registry import _StagingRegistry
from devsecops.adapters.vulnerability_policy import checkpoint, timestamp
from devsecops.factory.mission_security import _read_retained
from devsecops.factory.mission_signing import _VERIFIER_GAPS, require_locally_signed_mission, trust_document, trust_guard
from devsecops.factory.mission_staging import _configuration as staging_configuration
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import canonical_bytes, seal

_PUBLISHED = WeakKeyDictionary()
_SCOPE_FIELDS = ('run_scope', 'source_commit', 'architecture', 'origin', 'repository', 'policy_sha256', 'trust_policy_sha256')


class PublishedMission:
    """Current exact artifact publication; not CI execution or promotion authority."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('Publication requires direct signing and immutable verified readback')

    def __reduce__(self):
        raise TypeError('Publication authority cannot be serialized')

    def describe(self):
        record = _PUBLISHED.get(self)
        if record is None:
            raise ValueError('Unissued publication authority')
        return deepcopy(record['binding'])


def require_published_mission(value, *, run_scope, source_commit, architecture, origin, repository,
                              policy_sha256, trust_policy_sha256, publication_config_sha256):
    record = _PUBLISHED.get(value) if type(value) is PublishedMission else None
    if record is None or record['pid'] != os.getpid() or time.monotonic() >= record['expires_monotonic']:
        raise ValueError('Unissued, foreign-process or expired publication authority')
    selected = dict(run_scope=run_scope, source_commit=source_commit, architecture=architecture,
        origin=origin, repository=repository, policy_sha256=policy_sha256, trust_policy_sha256=trust_policy_sha256,
        publication_config_sha256=publication_config_sha256)
    binding = record['binding']
    if (any(binding[key] != value for key, value in selected.items())
            or datetime.now(timezone.utc) >= timestamp(binding['expires_at'])):
        raise ValueError('Publication authority differs from selected scope')
    return deepcopy(binding)


def publication_configuration(raw):
    selected = _json(raw)
    if (set(selected) != {'schema_version', 'kind', 'origin', 'repository', 'credentials', 'ca_bundle',
            'trust_policy_path', 'trust_policy_sha256', 'public_key_path', 'public_key_sha256', 'cosign_path'}
            or selected['kind'] != 'mission-evidence-publication-configuration'
            or any(not isinstance(selected[name], str) or not re.fullmatch(r'[a-f0-9]{64}', selected[name])
                   for name in ('trust_policy_sha256', 'public_key_sha256'))):
        raise ValueError('Explicit independently pinned evidence publication configuration required')
    staging_configuration(canonical_bytes({**{name: selected[name] for name in (
        'schema_version', 'origin', 'repository', 'credentials', 'ca_bundle')}, 'kind': 'mission-staging-configuration'}))
    for name in ('trust_policy_path', 'public_key_path', 'cosign_path'):
        if (not isinstance(selected[name], str) or len(selected[name]) > 4096
                or not Path(selected[name]).is_absolute() or '..' in Path(selected[name]).parts):
            raise ValueError('Publication trust and verifier paths must be explicit local files')
    return selected


def publish_signed_mission(*, signed, run_scope, source_commit, architecture, origin, repository,
                           policy_sha256, trust_policy_sha256, config, expected_config_sha256, output, timeout=300):
    """Write fixed digest-only objects once; ambiguous mutations remain unknown."""
    if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 2 <= timeout <= 600
            or not isinstance(expected_config_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_config_sha256)
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Pinned publication configuration, new output and bounded deadline required')
    deadline = time.monotonic() + timeout
    scope = deepcopy(dict(run_scope=run_scope, source_commit=source_commit, architecture=architecture,
        origin=origin, repository=repository, policy_sha256=policy_sha256, trust_policy_sha256=trust_policy_sha256))
    def admission():
        checkpoint(deadline)
        return require_locally_signed_mission(signed, **scope)
    source = admission()  # Reject JSON, stale or foreign authority before any I/O.
    report = {'schema_version': 1, 'kind': 'mission-evidence-publication', 'decision': 'unknown',
        'factory_decision': 'unknown', 'publication_authority': 'not-issued', 'scope': 'registry-evidence-publication-only',
        'destination': {'origin': origin, 'repository': repository}, 'run_scope': scope['run_scope'],
        'source_commit': source_commit, 'architecture': architecture, 'configuration_sha256': expected_config_sha256,
        'subject_digest': source['manifest_digest'], 'started_at': datetime.now(timezone.utc).isoformat(),
        'checks': {'local_signing': 'pass', 'trust': 'unknown', 'artifacts': 'unknown', 'subject': 'unknown',
            'native_referrers': 'unknown', 'upload': 'not-started', 'readback': 'not-started',
            'signature_verification': 'not-started', 'discovery': 'not-started'},
        'downstream': {'ci_execution': 'not-established', 'promote': 'not-started'}, 'mutations': [], 'gaps': []}
    binding, writer, reader, preflight = None, None, None, None
    with _new_directory(output) as directory:
        retained = {}
        def freeze(path, name, limit, expected):
            _verify_output_path(output, directory)
            ref = _snapshot(path, output / name, limit, deadline=deadline)
            if ref['sha256'] != expected:
                raise ValueError('Publication input differs from independently pinned evidence')
            retained[name] = ref
            return ref
        def read(name):
            return _read_retained(directory, name, retained[name], deadline, materialize=True)
        def audit():
            admission()
            trust_guard(trust, deadline=deadline)
            _verify_output_path(output, directory)
            for name, ref in retained.items():
                _read_retained(directory, name, ref, deadline)
            _verify_output_path(output, directory)
        def intent(kind, digest):
            audit()
            item = {'kind': kind, 'digest': digest, 'state': 'may-have-written',
                'destination': report['destination'], 'configuration_sha256': expected_config_sha256,
                'run_scope': scope['run_scope'], 'source_commit': source_commit}
            name = 'mutation-' + str(len(report['mutations']) + 1).zfill(3) + '.json'
            retained[name] = _write(directory, name, item)
            os.fsync(directory)
            report['mutations'].append({'intent': name, **retained[name], 'state': 'unknown', 'digest': digest})
            audit()
        class Body(io.BytesIO):
            def read(self, count=-1):
                admission()
                trust_guard(trust, deadline=deadline)
                return super().read(count)
        try:
            freeze(config, 'publication-config.json', 64000, expected_config_sha256)
            selected = publication_configuration(read('publication-config.json'))
            if ((selected['origin'], selected['repository']) != (origin, repository)
                    or selected['trust_policy_sha256'] != trust_policy_sha256
                    or selected['public_key_sha256'] != source['signer']['public_key_sha256']):
                raise ValueError('Publication destination or independent signer pins differ')
            freeze(Path(selected['trust_policy_path']), 'signing-trust.json', 64000, trust_policy_sha256)
            trust = trust_document(read('signing-trust.json'), deadline=deadline)
            expires = min(timestamp(source['expires_at']), trust_guard(trust, deadline=deadline),
                datetime.now(timezone.utc) + timedelta(minutes=15))
            signer = {name: trust[name] for name in ('issuer', 'authority', 'id', 'version', 'trust_reference', 'public_key_sha256')}
            signer.update(trust_policy_sha256=trust_policy_sha256, lifecycle='operator-selected-active-policy',
                publisher_authority='not-claimed', transparency_log='not-used')
            if ((trust['origin'], trust['repository']) != (origin, repository)
                    or signer != source['signer'] or trust['public_key_sha256'] != selected['public_key_sha256']):
                raise ValueError('Independent operator trust differs from actual signer')
            freeze(Path(selected['public_key_path']), 'public-key.pem', 16384, selected['public_key_sha256'])
            _public_key(read('public-key.pem'))
            report['signer'] = signer
            report['checks']['trust'] = 'unknown' if trust['authority'] == 'controlled-test' else 'pass'
            for field, name, limit in [('signature_payload', 'payload.json', 1_000_000),
                    ('signature_bundle', 'bundle.json', 1_000_000), ('sbom', 'sbom.spdx.json', 16_000_000),
                    ('vulnerabilities', 'grype.json', 16_000_000)]:
                ref = freeze(Path(source[field]['path']), name, limit, source[field]['sha256'])
                if ref['bytes'] != source[field]['bytes']:
                    raise ValueError('Signed artifact size differs')
            freeze(Path(source['signature_payload']['path']).parent / 'scanner-receipt.json',
                'scanner-receipt.json', 16_000_000, source['scanner_receipt_sha256'])
            bind_payload(read('payload.json'), source, signer, deadline=deadline)
            refs = {name: retained[name] for name, _, _ in FILES}
            report['checks']['artifacts'] = 'pass'
            ca = None
            if selected['ca_bundle'] is not None:
                ca = output / 'tls-ca.pem'
                retained['tls-ca.pem'] = _snapshot(Path(selected['ca_bundle']), ca, 2_000_000, deadline=deadline)
            audit()
            preflight = EvidenceReader(selected, ca_bundle=ca, deadline=deadline)
            subject_raw = preflight.manifest(source['manifest_digest'])
            subject_document = _json(subject_raw)
            if (type(subject_document.get('schemaVersion')) is not int or subject_document['schemaVersion'] != 2
                    or subject_document.get('mediaType') != MANIFEST or subject_document.get('artifactType')
                    or subject_document.get('subject') or subject_document.get('config', {}).get('digest') != source['config_digest']):
                raise ValueError('Registry image subject differs from signed mission')
            retained['subject-manifest.json'] = _write(directory, 'subject-manifest.json', subject_raw.decode('utf-8'))
            subject = descriptor(subject_raw, MANIFEST)
            report['subject'], report['checks']['subject'] = subject, 'pass'
            report['referrers_preflight'] = preflight.referrers(source['manifest_digest'])
            report['checks']['native_referrers'] = 'pass'
            raw, manifest = artifact_manifest(subject, refs)
            retained['artifact-manifest.json'] = _write(directory, 'artifact-manifest.json', raw.decode('utf-8'))
            retained['empty-config.json'] = _write(directory, 'empty-config.json', '{}')
            report['artifact'] = {**manifest, 'artifactType': ARTIFACT_TYPE}
            audit()
            writer = _StagingRegistry(origin, repository=repository, deadline=deadline, ca_bundle=ca, **selected['credentials'])
            writer.authenticate(manifest['digest'])
            body_files = [('empty-config.json', descriptor(b'{}', EMPTY_TYPE))]
            body_files.extend((name, item) for (name, _, _), item in zip(FILES, _json(raw)['layers'], strict=True))
            report['checks']['upload'] = 'unknown'
            for name, item in body_files:
                intent('begin-blob', item['digest'])
                location = writer.begin_blob()
                report['mutations'][-1]['state'] = 'upload-session-acknowledged'
                intent('complete-blob', item['digest'])
                writer.finish_blob(location, Body(read(name)), item)
                report['mutations'][-1]['state'] = 'acknowledged-awaiting-readback'
            intent('put-manifest', manifest['digest'])
            writer.put_manifest(Body(read('artifact-manifest.json')), manifest)
            report['mutations'][-1]['state'] = 'acknowledged-awaiting-readback'
            report['checks']['upload'] = 'pass'
            audit()
            report['checks']['readback'] = 'unknown'
            reader = EvidenceReader(selected, ca_bundle=ca, deadline=deadline)  # Independent pull session, no writer token.
            readback_raw = reader.manifest(manifest['digest'])
            if readback_raw != read('artifact-manifest.json'):
                raise ValueError('Artifact manifest readback differs')
            retained['readback-artifact-manifest.json'] = _write(directory, 'readback-artifact-manifest.json', readback_raw.decode('utf-8'))
            for name, item in body_files:
                audit()
                filename = 'readback-' + name
                fd = os.open(filename, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                try:
                    os.fchmod(fd, 0o600)
                    actual = reader.blob(item, fd=fd)
                    os.fchmod(fd, 0o400)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if actual != retained[name]:
                    raise ValueError('Artifact blob readback differs')
                retained[filename] = actual
            bind_payload(read('readback-payload.json'), source, signer, deadline=deadline)
            if (trust_document(read('readback-signing-trust.json'), deadline=deadline) != trust
                    or reference(read('readback-public-key.pem')) != retained['public-key.pem']):
                raise ValueError('Readback signer trust differs from independent selection')
            report['checks']['readback'] = 'pass'
            audit()
            remaining = deadline - time.monotonic()
            if remaining < 1:
                raise ValueError('Publication verification deadline exceeded')
            report['checks']['signature_verification'] = 'unknown'
            verification = verify_image_signature(expected_repository=origin.removeprefix('https://') + '/' + repository,
                expected_digest=source['manifest_digest'], public_key=output / 'readback-public-key.pem',
                payload=output / 'readback-payload.json', bundle=output / 'readback-bundle.json',
                cosign=Path(selected['cosign_path']), timeout=min(60, remaining))
            retained['readback-signature-verification.json'] = _write(directory, 'readback-signature-verification.json', verification)
            observed = verification['observations']
            if (any(observed[name]['decision'] != 'pass' for name in ('crypto', 'subject_binding', 'verification'))
                    or {item['code'] for item in verification['gaps']} != _VERIFIER_GAPS
                    or observed['tool'] != source['signing_tool'] or observed.get('bundle_payload_digest_matches') is not True
                    or observed['inputs'] != {name: retained['readback-' + name] for name in ('public-key.pem', 'payload.json', 'bundle.json')}):
                raise ValueError('Readback cryptographic verification is incomplete or differs')
            report['checks']['signature_verification'] = 'pass'
            report['checks']['discovery'] = 'unknown'
            report['referrers_readback'] = reader.referrers(source['manifest_digest'], manifest)
            report['checks']['discovery'] = 'pass'
            # Also retain the exact image subject under the independent final session.
            if reader.manifest(source['manifest_digest']) != read('subject-manifest.json'):
                raise ValueError('Readback image subject changed')
            for mutation in report['mutations']:
                mutation['state'] = 'content-confirmed-by-immutable-readback'
            audit()
            if trust['authority'] == 'controlled-test' or source.get('execution_mode') == 'controlled-test':
                report['gaps'].append('controlled-evidence-cannot-issue-publication-authority')
            else:
                binding = {**deepcopy(source), 'publication_config_sha256': expected_config_sha256,
                    'artifact_manifest': report['artifact'], 'publication_readback': deepcopy(retained),
                    'publication_path': str(output), 'scope': 'registry-evidence-publication-only', 'expires_at': expires.isoformat()}
                report['decision'], report['publication_authority'], report['binding'] = 'pass', 'issued', deepcopy(binding)
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError):
            binding = None
            report['decision'], report['publication_authority'] = 'unknown', 'not-issued'
            report.pop('binding', None)
            report['gaps'].append('publication-incomplete-retain-exact-digests-and-mutation-intents')
        finally:
            for name, client in [('preflight', preflight), ('upload', writer), ('readback', reader)]:
                if client is not None:
                    report[name + '_receipts'] = client.receipts
                    client.close()
        report['artifacts'] = deepcopy(retained)
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        report = seal(report, 'publication_sha256')
        _verify_output_path(output, directory)
        try:
            _write(directory, 'mission-publication.json', report)
            os.fsync(directory)
            _verify_output_path(output, directory)
            if report['checks']['discovery'] == 'pass':
                audit()
                if datetime.now(timezone.utc) >= expires:
                    raise ValueError('Publication expired during terminal write')
        except BaseException:
            try:
                os.unlink('mission-publication.json', dir_fd=directory)
            except FileNotFoundError:
                pass
            os.fsync(directory)
            raise
        if binding is None:
            return report, None
        capability = object.__new__(PublishedMission)
        _PUBLISHED[capability] = {'pid': os.getpid(), 'binding': deepcopy(binding),
            'expires_monotonic': time.monotonic() + (expires-datetime.now(timezone.utc)).total_seconds()}
        return report, capability
