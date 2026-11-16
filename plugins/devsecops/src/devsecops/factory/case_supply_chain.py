"""D4 actual trust controls and rejection of mutations of acquired evidence.

No scanner/database acquisition occurs here. A direct same-process authenticated
    CI runtime provenance is mandatory for the derived application's real evidence. The
negative controls alter copies, never registry subjects or finalized receipts.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import time

from devsecops.adapters import attestations, image_policy, publisher_trust
from devsecops.adapters.http import _json
from devsecops.adapters.scanner_json import parse_scanner_document
from devsecops.adapters.registry_attestation_policy import (load_attestation_size_policy,
    parse_attestation_size_policy, limits_for as attestation_limits_for)
from devsecops.adapters.vulnerability_policy import _package_index
from devsecops.adapters.signatures import _snapshot
from devsecops.runtime.context import verify_seal
from .lifecycle import AssertionResult, PhaseOutcome
from .state import StateError, _canonical, _exclusive, _read, _sha, _time

ASSERTIONS = ('valid-unchanged-reference-and-mission-base-trust-controls',
              'mismatched-sbom-and-scan-rejected', 'missing-tampered-untrusted-attestations-rejected',
              'valid-derived-app-subject-chain')


def _remaining(deadline, minimum=1, maximum=120):
    seconds = deadline - time.monotonic()
    if seconds < minimum:
        raise StateError('D4 verification deadline expired')
    return min(seconds, maximum)


def _write(root, name, value):
    path = root / name
    _exclusive(path, value if type(value) is bytes else _canonical(value))
    return path


def _freeze(reference, root, name, deadline, *, maximum=64000):
    path = root / name
    observed = _snapshot(Path(reference['path']), path, maximum, deadline=deadline)
    if observed['sha256'] != reference['sha256']:
        raise StateError('D4 input differs from the independent pinned selection')
    return path


def _subject(locked, policy, selected, architecture):
    subject = policy['subject']
    if (subject['origin'].removeprefix('https://') + '/' + subject['repository'] != locked['repository']
            or subject['digest'] != 'sha256:' + locked['manifest_sha256']
            or subject['architecture'] != architecture or locked['architecture'] != architecture
            or set(locked['required_predicates']) != {item['type'] for item in policy['predicates']}
            or selected['trust_identity_basis'] != 'publisher-policy-sha256-v1'
            or locked['trust_sha256'] != selected['publisher_policy']['sha256']
            or policy['signer']['key_sha256'] != selected['public_key']['sha256']):
        raise StateError('D4 immutable image, predicate or versioned trust selection differs from release lock')
    return subject


def _attestation_policy_matches(selected, policy, service):
    """Resolve one explicitly pinned immutable subject, never a union of limits."""
    refs = service.get('attestation_size_policies')
    if refs is None:
        return selected.get('attestation_size_policy') == service.get('attestation_size_policy')
    chosen = selected.get('attestation_size_policy')
    if ('attestation_size_policy' in service or chosen is None or chosen not in refs
            or not 1 <= len(refs) <= 16):
        return False
    subjects, paths, matches = set(), set(), []
    expected = policy['subject']
    for ref in refs:
        if ref['path'] in paths:
            return False
        paths.add(ref['path'])
        raw = _read(Path(ref['path']), 64000)
        sizes = parse_attestation_size_policy(raw, ref['sha256'])
        identity = (sizes.origin, sizes.repository, sizes.subject_digest)
        if (identity in subjects or sizes.origin != service['origin']
                or sizes.repository not in service.get('scope', {}).get('repositories', [])):
            return False
        subjects.add(identity)
        if identity == (expected['origin'], expected['repository'], expected['digest']):
            matches.append(ref)
    return matches == [chosen]


def _bind_registry_read_scope(selected, policy, config):
    matches = [item for item in config['services'] if item['id'] in ('registry', 'registry1')
               and item['origin'] == policy['subject']['origin']]
    if len(matches) != 1:
        raise StateError('D4 registry destination lacks a unique configured read scope')
    service = matches[0]
    if policy['subject']['repository'] not in service.get('scope', {}).get('repositories', []):
        raise StateError('D4 image repository is outside the configured read scope')
    if 'blob_download_policy' in selected:
        ref = selected['blob_download_policy']
        download = image_policy.load_blob_download_policy(Path(ref['path']), ref['sha256'],
            registry_origin=service['origin'], allowed_repositories=service['scope'].get('repositories', []))
        if policy['subject']['repository'] not in download.repositories:
            raise StateError('D4 blob policy does not permit the selected repository')
    if 'attestation_size_policy' in selected:
        ref = selected['attestation_size_policy']
        subject = policy['subject']
        load_attestation_size_policy(Path(ref['path']), ref['sha256'], origin=subject['origin'],
            repository=subject['repository'], subject_digest=subject['digest'])
    read = service['read_auth']
    expected = ({} if read['mode'] == 'none' else {'token_env': read['token_env']} if read['mode'] == 'bearer'
                else {'basic_username_env': read['username_env'], 'basic_password_env': read['password_env']})
    selected_auth = selected['registry_auth']
    if ({key: value for key, value in selected_auth.items() if key.endswith('_env')} != expected
            or selected_auth.get('ca_bundle') != service.get('ca_file')
            or selected.get('blob_download_policy') != service.get('blob_download_policy')
            or not _attestation_policy_matches(selected, policy, service)):
        raise StateError('D4 registry credential or CA reference differs from the configured read identity')


def selected_read_policy(selected, config):
    raw = _read(Path(selected['image_policy']['path']), 64000)
    if _sha(raw) != selected['image_policy']['sha256']:
        raise StateError('Selected registry policy changed before credential admission')
    policy = image_policy._policy(raw)
    _bind_registry_read_scope(selected, policy, config)
    return policy


def _bind_derived_base(binding, locked):
    expected = {'repository': locked['repository'], 'requested_digest': 'sha256:' + locked['manifest_sha256'],
                'platform_manifest_digest': 'sha256:' + locked['platform_manifest_sha256'],
                'config_digest': 'sha256:' + locked['config_sha256']}
    if any(binding['base'].get(key) != value for key, value in expected.items()):
        raise StateError('D4 derived application was not built from the actual locked mission base')


def _verified_blob(directory, name, reference, *, maximum=4_000_000):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
        raise StateError('D4 artifact route is unsupported')
    raw = _read(directory / name, maximum)
    if len(raw) != reference['bytes'] or _sha(raw) != reference['sha256']:
        raise StateError('D4 acquired evidence changed')
    return raw


def authority_expiry(publisher_policy, image_selection, actual_trust):
    limits = [publisher_trust._instant(publisher_policy['validity']['not_after']),
              image_policy._instant(image_selection['signer']['valid_until'])]
    if publisher_policy['source']['kind'] == 'operator-trusted-package':
        source = actual_trust['source']
        if source.get('max_age_seconds') != publisher_policy['source']['max_age_seconds']:
            raise StateError('Actual publisher package age policy differs')
        limits.append(_time(source['reviewed_at']) + timedelta(seconds=source['max_age_seconds']))
    return min(limits).isoformat()


def require_current_controls(controls, selected):
    now = datetime.now(timezone.utc)
    if any(not _time(control['observed_at']) <= now < _time(control['expires_at'])
           or (now - _time(control['observed_at'])).total_seconds() >= selected['max_verification_age_seconds']
           for control in controls):
        raise StateError('D4 current publisher or signer verification expired during controls')


def verify_locked_image(name, selected, locked, *, architecture, cosign, output, deadline):
    """Actual current publisher authority, signed image policy and exact chain."""
    output.mkdir(mode=0o700)
    publisher = _freeze(selected['publisher_policy'], output, 'publisher-policy.json', deadline)
    key = _freeze(selected['public_key'], output, 'public-key.pem', deadline, maximum=16384)
    policy_path = _freeze(selected['image_policy'], output, 'image-policy.json', deadline)
    package = (None if selected['publisher_package'] is None else
               _freeze(selected['publisher_package'], output, 'publisher-package.json', deadline))
    policy = image_policy._policy(_read(policy_path, 64000))
    subject = _subject(locked, policy, selected, architecture)
    download_args = {}
    selected_sizes = None
    if 'blob_download_policy' in selected:
        download = _freeze(selected['blob_download_policy'], output, 'blob-download-policy.json', deadline)
        image_policy.load_blob_download_policy(download, selected['blob_download_policy']['sha256'],
            registry_origin=subject['origin'], allowed_repositories=[subject['repository']])
        download_args = {'blob_download_policy': download,
                         'blob_download_policy_sha256': selected['blob_download_policy']['sha256']}
    if 'attestation_size_policy' in selected:
        sizes = _freeze(selected['attestation_size_policy'], output, 'attestation-size-policy.json', deadline)
        selected_sizes = load_attestation_size_policy(sizes, selected['attestation_size_policy']['sha256'],
            origin=subject['origin'], repository=subject['repository'], subject_digest=subject['digest'], deadline=deadline)
        download_args.update(attestation_size_policy=sizes,
                             attestation_size_policy_sha256=selected['attestation_size_policy']['sha256'])
    trust = publisher_trust.assess_publisher_trust(policy_path=publisher,
        expected_policy_sha256=selected['publisher_policy']['sha256'], public_key=key,
        registry_origin=subject['origin'], repository=subject['repository'], output=output / 'publisher',
        package_path=package, total_timeout=_remaining(deadline))
    if trust['publisher_trust'] != 'pass' or trust['execution_mode'] == 'controlled-test':
        raise StateError('D4 actual publisher authority is incomplete')
    auth = dict(selected['registry_auth'])
    if 'ca_bundle' in auth:
        auth['ca_bundle'] = Path(auth['ca_bundle'])
    assessed = image_policy.verify_registry_image_policy(policy_path=policy_path,
        expected_policy_sha256=selected['image_policy']['sha256'], public_key=key, cosign=cosign,
        output=output / 'image', total_timeout=_remaining(deadline, 10, 300), **auth, **download_args)
    if assessed['policy_satisfaction'] != 'pass' or assessed['subject'] != subject:
        raise StateError('D4 actual signed image policy did not pass')
    verification_root = output / 'image/verification'
    verification = _json(_read(verification_root / 'image-verification.json', 1_000_000))
    verify_seal(verification, 'verification_sha256')
    if verification['verification_sha256'] != assessed['verification_sha256']:
        raise StateError('D4 verification identity differs from its direct assessment')
    acquired = _json(_verified_blob(verification_root, 'registry-acquisition.json',
                                    verification['evidence']['registry-acquisition.json']))
    chain = acquired['observations']['image']
    if (chain['requested_digest'] != 'sha256:' + locked['manifest_sha256']
            or chain['platform_manifest_digest'] != 'sha256:' + locked['platform_manifest_sha256']
            or chain['config_digest'] != 'sha256:' + locked['config_sha256']):
        raise StateError('D4 acquired index/platform/config chain differs from the release lock')
    envelopes, envelope_descriptors = {}, {}
    for row in acquired['observations']['attestations']:
        kind = row['predicate_type']
        if kind not in locked['required_predicates']:
            continue
        if kind in envelopes:
            raise StateError('D4 predicate acquisition is ambiguous')
        artifact = row['envelope_artifact']
        reference = acquired['artifacts'][artifact]
        descriptor = {'digest': 'sha256:' + reference['sha256'], 'size_bytes': reference['bytes']}
        if selected_sizes is not None and row.get('descriptor') != descriptor:
            raise StateError('D4 attestation descriptor differs from acquired content')
        maximum, _ = attestation_limits_for(selected_sizes, descriptor['digest'], descriptor['size_bytes'])
        raw = _verified_blob(verification_root, artifact, reference, maximum=maximum)
        envelopes[kind] = _write(output, 'verified-envelope-' + str(len(envelopes)) + '.json', raw)
        envelope_descriptors[kind] = descriptor
    if set(envelopes) != set(locked['required_predicates']):
        raise StateError('D4 required acquired predicates are incomplete')
    publisher_policy = _json(_read(publisher, 64000))
    return {'name': name, 'subject': subject, 'key': key, 'envelopes': envelopes,
            'publisher_receipt_sha256': trust['trust_sha256'], 'image_receipt_sha256': assessed['verification_sha256'],
            'observed_at': trust['finished_at'],
            'expires_at': authority_expiry(publisher_policy, policy, trust),
            'attestation_size_policy': selected_sizes, 'envelope_descriptors': envelope_descriptors,
            'mapping_path': verification_root / 'registry-acquisition.json',
            'mapping_sha256': verification['evidence']['registry-acquisition.json']['sha256']}


def verify_negative_attestations(control, *, untrusted_key, cosign, output, deadline):
    """Run the actual offline signature verifier on exact acquired copies."""
    output.mkdir(mode=0o700)
    subject = control['subject']
    repository = subject['origin'].removeprefix('https://') + '/' + subject['repository']
    predicates = sorted(control['envelopes'])
    if _sha(_read(control['key'], 16384)) == _sha(_read(untrusted_key, 16384)):
        raise StateError('D4 untrusted-key control must differ from the independently trusted key')
    results = {}
    selected_sizes = control.get('attestation_size_policy')
    selected_arguments = ({} if selected_sizes is None else {
        'attestation_size_policy': selected_sizes, 'envelope_descriptors': control['envelope_descriptors']})
    for case in ('valid', 'missing', 'tampered', 'untrusted'):
        paths = dict(control['envelopes'])
        key = control['key']
        if case == 'tampered' and selected_sizes is not None:
            # Only the verifier creates this negative copy, after fresh strict
            # validation of the original bytes and their real signatures. The
            # derived object never becomes approved acquisition input.
            report = attestations.verify_negative_attestation_control(
                expected_repository=repository, expected_digest=subject['digest'],
                required_predicates=predicates, public_key=key, envelopes=paths, cosign=cosign,
                output=output / 'tampered-control', timeout=_remaining(deadline), **selected_arguments)
            _write(output, case + '-verification.json', report)
            results[case] = report
            continue
        if case == 'missing':
            paths[predicates[0]] = None
        elif case == 'tampered':
            envelope = _json(_read(paths[predicates[0]], 1_000_000))
            # Modify the exact DSSE payload while retaining its real signature;
            # no forged signature or imported verifier decision is supplied.
            statement, _, _, _ = attestations._decode(_canonical(envelope))
            statement = deepcopy(statement)
            statement['predicate']['devsecops_negative_control'] = 'tampered-after-acquisition'
            envelope['payload'] = base64.b64encode(_canonical(statement)).decode('ascii')
            paths[predicates[0]] = _write(output, 'tampered-envelope.json', envelope)
        elif case == 'untrusted':
            key = untrusted_key
        report = attestations.verify_attestations(expected_repository=repository, expected_digest=subject['digest'],
            required_predicates=predicates, public_key=key, envelopes=paths, cosign=cosign,
            timeout=_remaining(deadline), **selected_arguments)
        _write(output, case + '-verification.json', report)
        results[case] = report
    def observation(case):
        return results[case]['observations']['verification']['decision']
    missing_gaps = {item['code'] for item in results['missing']['gaps']}
    if (observation('valid') != 'pass' or observation('missing') != 'unknown'
            or 'required-attestation-missing' not in missing_gaps
            or observation('tampered') != 'fail' or observation('untrusted') != 'fail'):
        raise StateError('D4 required positive or rejection control did not establish its expected result')
    return {name: {'decision': observation(name), 'report_sha256': _sha(_canonical(report))}
            for name, report in results.items()}


def verify_derived_subjects(binding, *, output, deadline):
    """Check actual authenticated CI readback subjects, then changed copies.

    Caller must admit the direct VerifiedCIPublication immediately before and
    after this call. This helper never issues a publication/runtime capability.
    """
    output.mkdir(mode=0o700)
    root, refs = Path(binding['publication_path']), binding['publication_readback']
    raw = {name: _verified_blob(root, 'readback-' + name, refs['readback-' + name], maximum=16_000_000)
           for name in ('sbom.spdx.json', 'grype.json', 'scanner-receipt.json')}
    for name, value in raw.items():
        _write(output, 'acquired-' + name, value)
    source = {'manifest_digest': binding['manifest_digest'], 'config_digest': binding['config_digest'],
              'archive_sha256': binding['archive']['sha256'], 'architecture': binding['architecture']}
    sbom = parse_scanner_document(raw['sbom.spdx.json'])
    scan = parse_scanner_document(raw['grype.json'])
    receipt = _json(raw['scanner-receipt.json'])
    observed = receipt['observations']
    def subjects(sbom_doc, scan_doc):
        _remaining(deadline)
        _package_index(sbom_doc, source, deadline)
        target = scan_doc.get('source', {})
        if (target.get('type') != 'image' or target.get('target', {}).get('manifestDigest') != source['manifest_digest']
                or target['target'].get('imageID') != source['config_digest']
                or target['target'].get('architecture') != source['architecture']
                or target['target'].get('userInput') != 'frozen-artifact:' + source['archive_sha256']
                or target['target'].get('os') != 'linux'):
            raise StateError('D4 scanner subject differs from the actual CI image')
    subjects(sbom, scan)
    if (observed['image']['manifest_digest'] != source['manifest_digest']
            or observed['image']['config_digest'] != source['config_digest']
            or observed['source']['sha256'] != source['archive_sha256']
            or binding['sbom'] != {key: refs['readback-sbom.spdx.json'][key] for key in ('sha256', 'bytes')}
            or binding['vulnerabilities'] != {key: refs['readback-grype.json'][key] for key in ('sha256', 'bytes')}
            or binding['scanner_receipt_sha256'] != refs['readback-scanner-receipt.json']['sha256']):
        raise StateError('D4 signed CI assessment and actual scanner subject chain differ')
    other = '0' * 64 if source['manifest_digest'] != 'sha256:' + '0' * 64 else '1' * 64
    rejected = []
    for case in ('sbom', 'scan'):
        changed_sbom, changed_scan = deepcopy(sbom), deepcopy(scan)
        if case == 'sbom':
            container = next(row for row in changed_sbom['packages'] if row.get('primaryPackagePurpose') == 'CONTAINER')
            container['checksums'] = [{'algorithm': 'SHA256', 'checksumValue': other}]
        else:
            changed_scan['source']['target']['manifestDigest'] = 'sha256:' + other
        _write(output, 'mismatched-' + case + '.json', changed_sbom if case == 'sbom' else changed_scan)
        try:
            subjects(changed_sbom, changed_scan)
        except (StateError, ValueError):
            rejected.append(case)
    if rejected != ['sbom', 'scan']:
        raise StateError('D4 mismatched SBOM/scan was not rejected')
    return {'actual_source_commit': binding['source_commit'], 'actual_manifest_digest': source['manifest_digest'],
            'actual_config_digest': source['config_digest'], 'mismatched_copies_rejected': rejected,
            'scanner_execution': 'authenticated-ci-evidence-no-new-scanner-or-database-acquisition'}


def execute_supply_chain(context, *, operations, selection, output):
    """Fixed ordinary D4 dispatch: actual producers or explicit incomplete."""
    if context.phase['id'] != 'supply-chain-controls' or context.phase['operation'] != 'case':
        raise StateError('D4 requires its exact canonical case phase')
    from .owned_operations import OwnedOperationCoordinator
    if type(operations) is not OwnedOperationCoordinator:
        raise StateError('D4 requires the exact current operation coordinator')
    operations._admit(context)
    from .runtime_image import RuntimeImageProvenance
    publication = operations.runtime_image(context)
    if type(publication) is not RuntimeImageProvenance:
        raise StateError('D4 requires the direct freshly verified runtime image provenance')
    binding = publication.describe()
    if binding['run_scope'] != dict(context.identities):
        raise StateError('D4 actual publication belongs to another run')
    output.mkdir(mode=0o700)
    report = {'schema_version': 1, 'kind': 'canonical-supply-chain-controls', 'case_id': 'D4',
              'run_id': context.run_id, 'identities': dict(context.identities), 'execution_mode': context.execution_mode,
              'decision': 'unknown', 'checks': {}, 'started_at': datetime.now(timezone.utc).isoformat(),
              'authority': 'not-issued', 'scanner_acquisition': 'not-performed'}
    try:
        selected = selection['supply_chain']
        tools = [tool for tool in context.inputs['lock']['tools'] if tool['name'] == 'cosign']
        if (len(tools) != 1 or tools[0]['sha256'] != selected['cosign']['sha256']
                or context.inputs['config']['tool_paths'].get('cosign') != selected['cosign']['path']):
            raise StateError('D4 cryptographic tool differs from the selected release lock')
        cosign = _freeze(selected['cosign'], output, 'cosign.selected', context.deadline, maximum=256_000_000)
        # The fixed verifier verifies its own shipped binary pin before execution.
        untrusted = _freeze(selected['untrusted_public_key'], output, 'untrusted-public-key.pem', context.deadline, maximum=16384)
        controls = []
        for name in ('reference_web', 'mission_base'):
            selected_read_policy(selected[name], context.inputs['config'])
            control = verify_locked_image(name, selected[name], context.inputs['lock']['images'][name],
                architecture=context.inputs['lock']['platform']['architecture'], cosign=cosign,
                output=output / name, deadline=context.deadline)
            controls.append(control)
            report['checks'][name] = {'subject': control['subject'],
                'publisher_receipt_sha256': control['publisher_receipt_sha256'],
                'image_receipt_sha256': control['image_receipt_sha256']}
            report['checks'][name + '-negative-controls'] = verify_negative_attestations(control,
                untrusted_key=untrusted, cosign=cosign, output=output / (name + '-negatives'), deadline=context.deadline)
        publication.describe()
        _bind_derived_base(binding, context.inputs['lock']['images']['mission_base'])
        report['checks']['derived-app'] = verify_derived_subjects(binding, output=output / 'derived', deadline=context.deadline)
        operations._admit(context)
        publication.describe()
        _remaining(context.deadline)
        require_current_controls(controls, selected)
        if context.execution_mode == 'operator':
            report['decision'] = 'pass'
        else:
            report['gap'] = 'controlled-inputs-do-not-establish-live-D4'
    except (ValueError, OSError, KeyError, TypeError):
        report['gap'] = 'actual-trust-or-rejection-control-incomplete'
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    _write(output, 'case-result.json', report)
    artifacts = tuple(sorted(path for path in output.rglob('*') if path.is_file() and path.name != 'cosign.selected'))
    success = report['decision'] == 'pass'
    return PhaseOutcome('completed' if success else 'incomplete',
        'actual-supply-chain-controls-verified' if success else 'actual-supply-chain-controls-incomplete', artifacts,
        tuple(AssertionResult('D4', name, 'pass') for name in ASSERTIONS) if success else ())
