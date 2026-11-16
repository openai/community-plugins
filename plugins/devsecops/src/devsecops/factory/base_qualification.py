"""Concrete local Linux F0 composition; serialized reports never grant authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4
from weakref import WeakKeyDictionary

from devsecops.adapters.http import _json
from devsecops.adapters.registry_download_policy import load_blob_download_policy
from devsecops.adapters.registry_attestation_policy import load_attestation_size_policy
from devsecops.adapters.image_layout import acquire_image_layout
from devsecops.adapters.image_policy import _policy as read_image_policy, verify_registry_image_policy
from devsecops.adapters.publisher_trust import _policy as read_publisher_policy, assess_publisher_trust
from devsecops.adapters.signatures import _snapshot
from devsecops.factory import qualify_builder
from devsecops.factory.build import _base
from devsecops.factory.state import IDENTITY_KEYS, _read
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal, verify_seal

_ISSUED = WeakKeyDictionary()


class VerifiedBase:
    """Same-process, expiring issuer result. There is no JSON constructor."""
    __slots__ = ('__weakref__',)

    def __new__(cls, *args, **kwargs):
        raise TypeError('VerifiedBase can only be issued by direct F0 qualification')

    def __reduce__(self):
        raise TypeError('VerifiedBase authority cannot be serialized')

    def describe(self):
        record = _ISSUED.get(self)
        if record is None:
            raise ValueError('Unissued base object')
        return deepcopy(record['binding'])


def require_verified_base(value, *, run_scope, repository, requested_digest, architecture):
    """Consumer admission for the same process/run; returns copied bindings only."""
    record = _ISSUED.get(value) if type(value) is VerifiedBase else None
    if record is None or record['pid'] != os.getpid() or record['expires_monotonic'] <= time.monotonic():
        raise ValueError('Unissued, foreign-process or expired base authority')
    binding = record['binding']
    if (binding['run_scope'] != run_scope or binding['repository'] != repository
            or binding['requested_digest'] != requested_digest or binding['architecture'] != architecture
            or datetime.now(timezone.utc) >= datetime.fromisoformat(binding['expires_at'])):
        raise ValueError('Base authority differs from the selected run or image')
    return deepcopy(binding)


def _runtime(*, output, tools, expected_uid, source, layout, wheel, subject, timeout):
    recipe = {'kind': 'mission-python-runtime-v1',
        'repository': subject['origin'].removeprefix('https://') + '/' + subject['repository'],
        'requested_digest': subject['digest'], 'manifest_digest': layout['manifest_digest'],
        'architecture': subject['architecture'], 'nonce': uuid4().hex}
    return qualify_builder.qualify(output=output, tools=tools, expected_uid=expected_uid, timeout=timeout,
        _python_probe={'recipe': recipe, 'acquired': (source, layout), 'wheel': wheel})


def qualify_mission_base(*, publisher_policy, publisher_policy_sha256, image_policy,
                         image_policy_sha256, public_key, cosign, wheel, tools,
                         output, runtime_output, expected_uid, run_scope,
                         publisher_package=None, token_env=None, basic_username_env=None,
                         basic_password_env=None, token_path=None, token_service=None,
                         ca_bundle=None, total_timeout=900, blob_download_policy=None,
                         blob_download_policy_sha256=None, attestation_size_policy=None,
                         attestation_size_policy_sha256=None):
    """Acquire, verify and execute the exact base before issuing local authority."""
    return _qualify(publisher_policy=publisher_policy, publisher_policy_sha256=publisher_policy_sha256,
        image_policy=image_policy, image_policy_sha256=image_policy_sha256, public_key=public_key,
        cosign=cosign, wheel=wheel, tools=tools, output=output, runtime_output=runtime_output,
        expected_uid=expected_uid, run_scope=run_scope, publisher_package=publisher_package,
        token_env=token_env, basic_username_env=basic_username_env, basic_password_env=basic_password_env,
        token_path=token_path, token_service=token_service, ca_bundle=ca_bundle, total_timeout=total_timeout,
        blob_download_policy=blob_download_policy, blob_download_policy_sha256=blob_download_policy_sha256,
        attestation_size_policy=attestation_size_policy,
        attestation_size_policy_sha256=attestation_size_policy_sha256)


def _qualify(*, publisher_policy, publisher_policy_sha256, image_policy, image_policy_sha256,
             public_key, cosign, wheel, tools, output, runtime_output, expected_uid, run_scope,
             publisher_package=None, token_env=None, basic_username_env=None, basic_password_env=None,
             token_path=None, token_service=None, ca_bundle=None, total_timeout=900, _controlled=False,
             blob_download_policy=None, blob_download_policy_sha256=None,
             attestation_size_policy=None, attestation_size_policy_sha256=None):
    if (type(total_timeout) not in (int, float) or not math.isfinite(total_timeout) or not 30 <= total_timeout <= 1800
            or not isinstance(run_scope, dict) or set(run_scope) != IDENTITY_KEYS
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value) for value in run_scope.values())
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts
            or not isinstance(runtime_output, Path) or not runtime_output.is_absolute() or '..' in runtime_output.parts
            or not isinstance(tools, Path) or not tools.is_absolute() or type(expected_uid) is not int or expected_uid <= 0
            or any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
                   for value in (publisher_policy_sha256, image_policy_sha256))):
        raise ValueError('Explicit frozen F0 policy, run identities, Linux owner and bounded outputs are required')
    run_scope = deepcopy(run_scope)
    deadline = time.monotonic() + total_timeout
    def remaining(maximum, minimum=1):
        value = deadline-time.monotonic()
        if value < minimum:
            raise ValueError('F0 qualification exceeded its remaining time budget')
        return min(maximum, value)
    with tempfile.TemporaryDirectory(prefix='devsecops-f0-inputs-') as temporary:
        frozen = Path(temporary).resolve()
        for label, source, expected in (('publisher.json', publisher_policy, publisher_policy_sha256),
                                        ('image.json', image_policy, image_policy_sha256)):
            ref = _snapshot(source, frozen / label, 64_000, deadline=deadline)
            if ref['sha256'] != expected:
                raise ValueError('F0 policy differs from the separately selected identity')
        publisher = read_publisher_policy((frozen / 'publisher.json').read_bytes())
        image = read_image_policy((frozen / 'image.json').read_bytes())
        key_ref = _snapshot(public_key, frozen / 'publisher.pem', 16_384, deadline=deadline)
        if key_ref['sha256'] != publisher['key']['pem_sha256'] or key_ref['sha256'] != image['signer']['key_sha256']:
            raise ValueError('Publisher and image policy do not bind the same exact key')
        subject = image['subject']
        download = load_blob_download_policy(blob_download_policy, blob_download_policy_sha256,
            registry_origin=subject['origin'], allowed_repositories=[subject['repository']])
        frozen_download = None
        if download is not None:
            frozen_download = frozen / 'blob-download-policy.json'
            download_ref = _snapshot(blob_download_policy, frozen_download, 64_000, deadline=deadline)
            if download_ref['sha256'] != blob_download_policy_sha256:
                raise ValueError('Blob download policy changed before F0 snapshot')
        sizes = load_attestation_size_policy(attestation_size_policy, attestation_size_policy_sha256,
            origin=subject['origin'], repository=subject['repository'], subject_digest=subject['digest'],
            deadline=deadline)
        frozen_sizes = None
        if sizes is not None:
            frozen_sizes = frozen / 'attestation-size-policy.json'
            sizes_ref = _snapshot(attestation_size_policy, frozen_sizes, 64_000, deadline=deadline)
            if sizes_ref['sha256'] != attestation_size_policy_sha256:
                raise ValueError('Attestation size policy changed before F0 snapshot')
        repository = subject['origin'].removeprefix('https://') + '/' + subject['repository']
        if not _controlled:
            if publisher['publisher'] != 'platform-one-iron-bank' or subject['origin'] != 'https://registry1.dso.mil':
                raise ValueError('Canonical F0 requires the official Iron Bank publisher and registry')
            _base(repository, subject['digest'], subject['architecture'])
        authority_expiry = min(datetime.fromisoformat(value.replace('Z', '+00:00')) for value in
            (publisher['validity']['not_after'], image['signer']['valid_until']))
        frozen_package = None
        if publisher_package is not None:
            frozen_package = frozen / 'publisher-package.json'
            _snapshot(publisher_package, frozen_package, 64_000, deadline=deadline)
        result = {'schema_version': 1, 'kind': 'mission-base-qualification',
            'decision': 'unknown', 'base_authority': 'not-issued',
            'execution_mode': 'controlled-test' if _controlled else 'direct-local-linux',
            'run_scope': deepcopy(run_scope), 'subject': deepcopy(subject),
            'publisher_policy_sha256': publisher_policy_sha256, 'image_policy_sha256': image_policy_sha256,
            'key_sha256': key_ref['sha256'], 'started_at': datetime.now(timezone.utc).isoformat(),
            'checks': {'publisher': 'unknown', 'signed_policy': 'unknown', 'layers': 'unknown', 'runtime': 'unknown'},
            'evidence': {}, 'gaps': []}
        auth = dict(token_env=token_env, basic_username_env=basic_username_env,
            basic_password_env=basic_password_env, token_path=token_path, token_service=token_service,
            ca_bundle=ca_bundle)
        if download is not None:
            result['blob_download_policy'] = download.identity_record()
            auth.update(blob_download_policy=frozen_download,
                        blob_download_policy_sha256=blob_download_policy_sha256)
        verification_auth = dict(auth)
        if sizes is not None:
            result['attestation_size_policy'] = sizes.identity_record()
            verification_auth.update(attestation_size_policy=frozen_sizes,
                                     attestation_size_policy_sha256=attestation_size_policy_sha256)
        binding = None
        with _new_directory(output) as directory:
            try:
                trust = assess_publisher_trust(policy_path=frozen / 'publisher.json',
                    expected_policy_sha256=publisher_policy_sha256, public_key=frozen / 'publisher.pem',
                    registry_origin=subject['origin'], repository=subject['repository'], output=output / 'publisher',
                    package_path=frozen_package, total_timeout=remaining(120))
                result['checks']['publisher'] = trust['publisher_trust']
                result['evidence']['publisher_trust_sha256'] = trust['trust_sha256']
                if trust['publisher_trust'] != 'pass' or trust['execution_mode'] == 'controlled-test':
                    raise ValueError('Actual publisher authority is incomplete')
                if publisher['source']['kind'] == 'operator-trusted-package':
                    reviewed = datetime.fromisoformat(trust['source']['reviewed_at'].replace('Z', '+00:00'))
                    authority_expiry = min(authority_expiry,
                        reviewed + timedelta(seconds=publisher['source']['max_age_seconds']))
                verified = verify_registry_image_policy(policy_path=frozen / 'image.json',
                    expected_policy_sha256=image_policy_sha256, public_key=frozen / 'publisher.pem',
                    cosign=cosign, output=output / 'signed-policy', total_timeout=remaining(240, 10), **verification_auth)
                result['checks']['signed_policy'] = verified['policy_satisfaction']
                result['evidence']['verification_sha256'] = verified['verification_sha256']
                if verified['policy_satisfaction'] != 'pass' or verified['subject'] != subject:
                    raise ValueError('Actual signed image predicate policy is incomplete')
                # Reopen exact crypto-bound evidence only to compare its selected
                # image chain with the separately acquired full layer layout.
                crypto = _json(_read(output / 'signed-policy/verification/image-verification.json', 4_000_000))
                verify_seal(crypto, 'verification_sha256')
                if crypto['verification_sha256'] != verified['verification_sha256']:
                    raise ValueError('Cryptographic evidence identity changed')
                raw = _read(output / 'signed-policy/verification/registry-acquisition.json', 4_000_000)
                ref = crypto['evidence']['registry-acquisition.json']
                if hashlib.sha256(raw).hexdigest() != ref['sha256'] or len(raw) != ref['bytes']:
                    raise ValueError('Signed acquisition evidence changed')
                signed_chain = _json(raw)['observations']['image']
                layers = acquire_image_layout(subject['origin'], repository=subject['repository'],
                    requested_digest=subject['digest'], architecture=subject['architecture'],
                    allowed_repositories=[subject['repository']], output=output / 'image',
                    total_timeout=remaining(600), **auth)
                result['checks']['layers'] = layers['observations']['acquisition']['decision']
                result['evidence']['layout_manifest_sha256'] = layers['manifest_sha256']
                if result['checks']['layers'] != 'pass':
                    raise ValueError('Actual image layer acquisition is incomplete')
                chain = layers['observations']['image']
                if (signed_chain['requested_digest'] != subject['digest']
                        or layers['scope']['requested_digest'] != subject['digest']
                        or layers['scope']['origin'] != subject['origin']
                        or layers['scope']['repository'] != subject['repository']
                        or layers['scope']['architecture'] != subject['architecture']):
                    raise ValueError('Signed and acquired requested image identities differ')
                for key in ('index_digest', 'platform_manifest_digest', 'config_digest', 'platform'):
                    if chain[key] != signed_chain[key]:
                        raise ValueError('Signed and acquired image identities differ')
                layout = layers['observations']['layout']
                runtime = _runtime(output=runtime_output, tools=tools, expected_uid=expected_uid,
                    source=output / 'image', layout=layout, wheel=wheel, subject=subject, timeout=remaining(600, 30))
                result['checks']['runtime'] = runtime['decision']
                result['evidence']['runtime'] = _write(directory, 'runtime-observation.json', runtime)
                if runtime['decision'] != 'pass' or runtime['scope'] != 'mission-python-runtime-probe':
                    raise ValueError('Actual base runtime compatibility is incomplete')
                if (runtime['base']['repository'] != repository or runtime['base']['requested_digest'] != subject['digest']
                        or runtime['base']['manifest_digest'] != chain['platform_manifest_digest']
                        or runtime['base']['architecture'] != subject['architecture']
                        or runtime['runtime_inputs']['layout'] != layout['sha256']):
                    raise ValueError('Runtime proof differs from verified image content')
                remaining(1)
                now = datetime.now(timezone.utc)
                expires = min(now + timedelta(minutes=15), authority_expiry)
                if expires <= now:
                    raise ValueError('Publisher or image policy expired during qualification')
                binding = {'repository': repository, 'requested_digest': subject['digest'],
                    'platform_manifest_digest': chain['platform_manifest_digest'], 'config_digest': chain['config_digest'],
                    'architecture': subject['architecture'], 'layout_sha256': layout['sha256'],
                    'layout_path': str(output / 'image/layout'), 'layout_inventory': layout,
                    'key_sha256': key_ref['sha256'], 'publisher_trust_sha256': trust['trust_sha256'],
                    'verification_sha256': verified['verification_sha256'], 'run_scope': deepcopy(run_scope),
                    'expires_at': expires.isoformat(), 'runtime_run_id': runtime['run_id'],
                    'runtime_proof': runtime['runtime_proof'], 'nonce': uuid4().hex}
                if _controlled or runtime.get('execution_mode') == 'controlled-test':
                    binding = None
                    result['gaps'].append('controlled-execution-cannot-issue-base-authority')
                else:
                    result['decision'], result['base_authority'] = 'pass', 'issued'
            except (OSError, ValueError, TypeError, KeyError, IndexError):
                binding = None
                result['gaps'].append('base-qualification-incomplete')
                if 'fail' in result['checks'].values():
                    result['decision'] = 'fail'
            result['finished_at'] = datetime.now(timezone.utc).isoformat()
            if binding is not None:
                result['binding'] = deepcopy(binding)
            result = seal(result, 'qualification_sha256')
            _verify_output_path(output, directory)
            try:
                _write(directory, 'base-qualification.json', result)
                os.fsync(directory)
                _verify_output_path(output, directory)
                if binding is not None:
                    remaining(1)
                    if datetime.now(timezone.utc) >= datetime.fromisoformat(binding['expires_at']):
                        raise ValueError('Base authority expired during publication')
            except BaseException:
                try:
                    os.unlink('base-qualification.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
            if binding is None:
                return result, None
            capability = object.__new__(VerifiedBase)
            _ISSUED[capability] = {'pid': os.getpid(), 'binding': deepcopy(binding),
                'expires_monotonic': time.monotonic() + (datetime.fromisoformat(binding['expires_at']) - datetime.now(timezone.utc)).total_seconds()}
            return result, capability
