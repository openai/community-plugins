"""Direct registry acquisition followed by frozen signature/attestation checks.

This entry point never consumes a previously asserted acquisition report. The
caller supplies a separately reviewed public-key identity. Publisher authority,
runtime compatibility and promotion policy remain explicit separate gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import re
import tempfile
import time

from .attestations import verify_attestations
from .http import APIError
from .registry_evidence import collect_registry_evidence
from .registry_download_policy import load_blob_download_policy
from .registry_attestation_policy import load_attestation_size_policy, limits_for as attestation_limits_for
from .signatures import _Invalid, _snapshot, verify_image_signature
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import seal
from devsecops.factory.state import _read, _safe_path


def verify_registry_image(origin, *, repository, requested_digest, architecture,
                          allowed_repositories, required_predicates, public_key, expected_key_sha256,
                          cosign, output, variant=None, token_env=None, basic_username_env=None,
                          basic_password_env=None, token_path=None, token_service=None, ca_bundle=None,
                          timeout=5.0, total_timeout=180.0,
                          blob_download_policy=None, blob_download_policy_sha256=None,
                          attestation_size_policy=None, attestation_size_policy_sha256=None):
    """Acquire actual bytes and verify them; there is no imported-report mode.

    The public-key hash is an explicit caller-supplied identity, not proof of
    publisher ownership. An image build capability is never issued here.
    """
    if (not isinstance(expected_key_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_key_sha256)
            or type(total_timeout) not in (int, float) or not math.isfinite(total_timeout)
            or not 5 <= total_timeout <= 300
            or not isinstance(output, Path) or not output.is_absolute() or '..' in output.parts):
        raise ValueError('Explicit key identity, new absolute output and bounded deadline are required')
    selected_download = load_blob_download_policy(blob_download_policy, blob_download_policy_sha256,
        registry_origin=origin, allowed_repositories=allowed_repositories)
    selected_sizes = load_attestation_size_policy(attestation_size_policy, attestation_size_policy_sha256,
        origin=origin, repository=repository, subject_digest=requested_digest)
    output = _safe_path(output)
    if output.exists() or any(parent.exists() and not parent.is_dir() for parent in output.parents):
        raise ValueError('Image verification requires a new output directory')
    deadline = time.monotonic() + total_timeout
    started = datetime.now(timezone.utc).isoformat()
    result = {'schema_version': 1, 'kind': 'registry-image-verification',
        'execution_status': 'partial', 'decision': 'unknown', 'build_authority': 'not-issued',
        'scope': {'origin': origin, 'repository': repository, 'digest': requested_digest},
        'started_at': started, 'key_sha256': expected_key_sha256,
        'checks': {'acquisition': 'unknown', 'image_signature': 'unknown', 'required_attestations': 'unknown'},
        'verification': 'unknown', 'gaps': [], 'evidence': {}}
    if selected_download is not None:
        result['scope']['blob_download_policy'] = selected_download.identity_record()
    if selected_sizes is not None:
        result['scope']['attestation_size_policy'] = selected_sizes.identity_record()
    def remaining(maximum):
        seconds = deadline - time.monotonic()
        if seconds < 1:
            raise _Invalid('image-verification-deadline')
        return min(maximum, seconds)
    def finished():
        if time.monotonic() >= deadline:
            raise _Invalid('image-verification-deadline')
    def add_gap(code):
        if code not in result['gaps']:
            result['gaps'].append(code)
    def retain_required(report, reassessed=()):
        gaps = report.get('gaps')
        if not isinstance(gaps, list):
            add_gap('verification-evidence-gap-metadata-invalid')
            return
        for gap in gaps:
            if (not isinstance(gap, dict) or type(gap.get('required')) is not bool
                    or not isinstance(gap.get('code'), str) or not re.fullmatch('[a-z0-9-]{1,128}', gap['code'])):
                add_gap('verification-evidence-gap-metadata-invalid')
            elif gap['required'] and gap['code'] not in reassessed:
                add_gap(gap['code'])
    with tempfile.TemporaryDirectory(prefix='devsecops-image-verification-') as temporary:
        scratch = Path(temporary).resolve()
        key = scratch / 'publisher-key.pem'
        key_ref = _snapshot(public_key, key, 16_384, deadline=deadline)
        if key_ref['sha256'] != expected_key_sha256:
            # Do not send credentials or create output when the separately
            # selected key material has changed.
            raise ValueError('Public key does not match the explicit reviewed identity')
        acquisition_dir = scratch / 'acquisition'
        acquisition = collect_registry_evidence(origin, repository=repository,
            requested_digest=requested_digest, architecture=architecture, allowed_repositories=allowed_repositories,
            required_predicates=required_predicates, output=acquisition_dir, variant=variant, token_env=token_env,
            basic_username_env=basic_username_env, basic_password_env=basic_password_env,
            token_path=token_path, token_service=token_service, ca_bundle=ca_bundle,
            timeout=timeout, total_timeout=remaining(120),
            **({} if selected_download is None else {'blob_download_policy': blob_download_policy,
                'blob_download_policy_sha256': blob_download_policy_sha256}),
            **({} if selected_sizes is None else {'attestation_size_policy': attestation_size_policy,
                'attestation_size_policy_sha256': attestation_size_policy_sha256}))
        result['checks']['acquisition'] = acquisition['observations']['acquisition']['decision']
        # Freeze and recheck every returned object before interpreting any
        # selection hint. The collector is called here, not loaded from JSON.
        frozen = scratch / 'frozen'
        frozen.mkdir(mode=0o700)
        artifacts = {}
        total = 0
        if not isinstance(acquisition.get('artifacts'), dict) or len(acquisition['artifacts']) > 128:
            raise ValueError('Acquisition artifact inventory is invalid')
        for name, ref in acquisition['artifacts'].items():
            if (not isinstance(name, str) or not re.fullmatch(r'sha256-[a-f0-9]{64}\.blob', name)
                    or not isinstance(ref, dict) or ref.get('sha256') != name[7:-5]
                    or type(ref.get('bytes')) is not int or ref['bytes'] < 1):
                raise ValueError('Acquisition artifact identity is invalid')
            maximum = 4_000_000
            if 'application/vnd.dsse.envelope.v1+json' in ref.get('media_types', []):
                maximum, _ = attestation_limits_for(selected_sizes, 'sha256:' + ref['sha256'], ref['bytes'])
            if ref['bytes'] > maximum:
                raise ValueError('Acquisition artifact exceeds its selected bound')
            item = _snapshot(acquisition_dir / name, frozen / name, maximum, deadline=deadline)
            total += item['bytes']
            if item != {key: ref[key] for key in ('sha256', 'bytes')} or total > 64_000_000:
                raise ValueError('Acquisition artifact content changed or exceeds its bound')
            artifacts[name] = item
        def selected_artifact(name):
            if not isinstance(name, str) or name not in artifacts:
                raise _Invalid('verification-artifact-reference-invalid')
            return frozen / name
        verification_reports = {}
        try:
            if result['checks']['acquisition'] == 'pass':
                candidates = acquisition['observations']['signatures']
                requirements = acquisition['observations']['required_predicates']
                if len(candidates) != 1 or any(item['selection'] != 'unique-unverified' for item in requirements):
                    raise _Invalid('verification-artifacts-ambiguous')
                selected = candidates[0]
                payload = selected_artifact(selected['payload_artifact'])
                detached_signature = selected_artifact(selected['signature_artifact'])
                envelopes, envelope_descriptors = {}, {}
                for item in acquisition['observations']['attestations']:
                    predicate = item['predicate_type']
                    if predicate not in required_predicates:
                        continue
                    if predicate in envelopes:
                        raise _Invalid('verification-artifacts-ambiguous')
                    envelopes[predicate] = selected_artifact(item['envelope_artifact'])
                    envelope_ref = artifacts[item['envelope_artifact']]
                    envelope_descriptors[predicate] = {'digest': 'sha256:' + envelope_ref['sha256'],
                                                       'size_bytes': envelope_ref['bytes']}
                    if selected_sizes is not None and item.get('descriptor') != envelope_descriptors[predicate]:
                        raise _Invalid('verification-attestation-descriptor-mismatch')
                if set(envelopes) != set(required_predicates):
                    raise _Invalid('verification-artifacts-incomplete')
                signed_repository = acquisition['scope']['origin'].removeprefix('https://') + '/' + repository
                signature = verify_image_signature(
                    expected_repository=signed_repository,
                    expected_digest=requested_digest, public_key=key, cosign=cosign,
                    payload=payload, signature=detached_signature,
                    timeout=remaining(60))
                finished()
                verification_reports['image-signature.json'] = signature
                result['checks']['image_signature'] = signature['observations']['verification']['decision']
                attestation = verify_attestations(
                    expected_repository=signed_repository,
                    expected_digest=requested_digest, required_predicates=required_predicates,
                    public_key=key, envelopes=envelopes, cosign=cosign, timeout=remaining(120),
                    **({} if selected_sizes is None else {'attestation_size_policy': selected_sizes,
                        'envelope_descriptors': envelope_descriptors}))
                finished()
                verification_reports['required-attestations.json'] = attestation
                result['checks']['required_attestations'] = attestation['observations']['verification']['decision']
            else:
                add_gap('registry-acquisition-incomplete')
        except (_Invalid, APIError) as error:
            add_gap(error.code)
        except (OSError, ValueError, TypeError, KeyError):
            add_gap('image-verification-unavailable')
        # Reassess only the specific checks performed here. Preserve every
        # other required acquisition/crypto gap, including newly added ones.
        reassessed = set()
        if result['checks']['image_signature'] == 'pass':
            reassessed.add('publisher-signature-unverified')
        if result['checks']['required_attestations'] == 'pass':
            reassessed.update(('required-attestation-cryptography-unverified', 'required-attestations-unverified'))
        retain_required(acquisition, reassessed)
        for report in verification_reports.values():
            retain_required(report, reassessed)
        # A narrow pass means only that all actual acquired objects matched
        # this explicit key and subject. It never grants runtime/build authority.
        values = list(result['checks'].values())
        result['verification'] = 'fail' if 'fail' in values else 'pass' if all(v == 'pass' for v in values) else 'unknown'
        if 'verification-evidence-gap-metadata-invalid' in result['gaps']:
            result['verification'] = 'unknown'
        for gap in ('publisher-key-authority-unverified', 'key-lifecycle-unverified',
                    'predicate-semantics-unassessed', 'runtime-compatibility-unqualified',
                    'promotion-policy-unassessed'):
            add_gap(gap)
        if time.monotonic() >= deadline:
            result['verification'] = 'unknown'
            add_gap('image-verification-deadline')
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        with _new_directory(output) as directory:
            for name, ref in artifacts.items():
                finished()
                maximum, _ = attestation_limits_for(selected_sizes, 'sha256:' + ref['sha256'], ref['bytes'])
                raw = _read(frozen / name, maximum)
                if hashlib.sha256(raw).hexdigest() != ref['sha256'] or len(raw) != ref['bytes']:
                    raise ValueError('Frozen verification evidence changed')
                descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            for name, evidence in {'registry-acquisition.json': acquisition, **verification_reports}.items():
                result['evidence'][name] = _write(directory, name, evidence)
            finished()
            result = seal(result, 'verification_sha256')
            reference = _write(directory, 'image-verification.json', result)
            manifest = {'schema_version': 1, 'kind': 'registry-image-verification-manifest',
                        'finished': True, 'decision': 'unknown', 'verification': result['verification'],
                        'artifacts': {**artifacts, **result['evidence'], 'image-verification.json': reference}}
            _verify_output_path(output, directory)
            _write(directory, 'image-manifest.json', manifest)
            os.fsync(directory)
            try:
                _verify_output_path(output, directory)
                finished()
            except (OSError, ValueError):
                os.unlink('image-manifest.json', dir_fd=directory)
                os.fsync(directory)
                raise
        return result
