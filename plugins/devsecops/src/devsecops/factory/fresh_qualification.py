"""Read-only consistency of canonical fresh-foundation receipts.

This consumes terminal-replayed evidence; it does not recreate a live origin,
authenticate arbitrary JSON or assert that controlled fixtures created a guest.
"""
from pathlib import Path
import re
from uuid import UUID

from .state import _read, _sha, _time

MODULES = {'owned_fresh_foundation', 'fresh_foundation_recipe', 'fresh_foundation_remote',
           'registry_remote', 'tcg', 'cleanup_observation'}


def _identity(value):
    return (type(value) is dict and set(value) == {'device', 'inode'}
            and all(type(v) is int and v >= 0 for v in value.values()))


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value)


def _fingerprint(value):
    if type(value) is not dict or set(value) != {'namespaces', 'nodes', 'ca_sha256'}:
        return False
    if (type(value['namespaces']) is not dict or set(value['namespaces']) != {'default', 'kube-system'}
            or type(value['nodes']) is not dict or len(value['nodes']) != 1 or not _hash(value['ca_sha256'])):
        return False
    try:
        for uid in [*value['namespaces'].values(), *value['nodes'].values()]:
            if str(UUID(uid)) != uid:
                return False
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _preservation(rows):
    if not isinstance(rows, list) or len(rows) > 64 or len({row.get('kind') for row in rows}) != len(rows):
        return False
    required = {'network', 'seed', 'runtime', 'disposable-disk-seed-pki', 'supplied-assets', 'supplied-tool-image',
                'local-api-tunnel', 'local-issued-credential-copies'}
    if not required <= {row.get('kind') for row in rows}:
        return False
    return all(row.get('state') == {'remove': 'absent', 'preserve': 'preserved'}.get(row.get('disposition'))
               for row in rows)


def _renewals(recipe, result, up, down, documents, files):
    """Validate the ordered accepted renewal chain from registered byte copies."""
    from .qualification import QualificationError
    initial = recipe['transport']['certificate']['sha256']
    previous = initial
    prior_grant = None
    summaries = []
    seen = set()
    phases = result['phase_records']
    references = {row['path'] for phase in phases for row in phase['artifacts']}
    def resolve(pin):
        candidates = [(name, value) for name, value in documents.items()
            if name in references and files[name]['sha256'] == pin]
        if not candidates:
            raise QualificationError('Fresh renewal chain member is absent from selected terminal phase artifacts')
        # Repeated immutable copies may appear in more than one phase. Their
        # exact content hash is the identity; no report path is ever opened.
        return candidates
    initial_chain = up.get('transport_generations')
    chain = down.get('transport_generations')
    if initial_chain is None and chain is None:
        return []  # Original producer before renewal support; recipe stays fixed.
    fields = {'initial_certificate_sha256', 'current_certificate_sha256', 'recipe_identity_changed', 'renewals'}
    if any(type(value) is not dict or set(value) != fields or value['initial_certificate_sha256'] != initial
           or value['recipe_identity_changed'] is not False or type(value['renewals']) is not list
           or len(value['renewals']) > 128 for value in (initial_chain, chain)):
        raise QualificationError('Fresh transport generation evidence differs from the original recipe')
    if initial_chain['renewals'] != chain['renewals'][:len(initial_chain['renewals'])]:
        raise QualificationError('Fresh creation renewal history is not a prefix of terminal cleanup history')
    for entry in chain['renewals']:
        keys = {'receipt_name', 'intent_name', 'receipt_sha256', 'intent_sha256', 'operation_id',
                'previous_certificate_sha256', 'certificate_sha256'}
        if (type(entry) is not dict or set(entry) != keys
                or any(not _hash(entry[key]) for key in ('receipt_sha256', 'intent_sha256', 'certificate_sha256'))
                or entry['receipt_sha256'] in seen or entry['certificate_sha256'] == previous
                or entry['previous_certificate_sha256'] != previous
                or any(not isinstance(entry[key], str) or not re.fullmatch(r'fresh-transport-renewal-[A-Za-z0-9_.-]+\.json', entry[key])
                       for key in ('receipt_name', 'intent_name'))):
            raise QualificationError('Fresh renewal chain is duplicated, reordered or unsupported')
        receipt_copies = resolve(entry['receipt_sha256'])
        intent_copies = resolve(entry['intent_sha256'])
        receipt_name, receipt = receipt_copies[0]
        intent_name, intent = intent_copies[0]
        seen.add(entry['receipt_sha256'])
        expected_intent = {key: value for key, value in receipt.items() if key not in ('finished_at', 'transport')}
        expected_intent['status'] = 'unobserved'
        grant = receipt.get('certificate_grant', {})
        transport = receipt.get('transport', {})
        phase_matches = [phase for phase in phases
            if any(name in {row['path'] for row in phase['artifacts']} for name, _ in receipt_copies)
            and any(name in {row['path'] for row in phase['artifacts']} for name, _ in intent_copies)
            and _time(phase['started_at']) <= _time(receipt['started_at'])
            <= _time(receipt['finished_at']) <= _time(phase['finished_at'])]
        if (receipt.get('schema_version') != 1 or receipt.get('kind') != 'fresh-foundation-certificate-renewal'
                or receipt.get('status') != 'authenticated' or receipt.get('execution_mode') != 'operator'
                or receipt.get('run_id') != result['run_id'] or receipt.get('identities') != result['identities']
                or receipt.get('up_operation_id') != up['operation_id']
                or receipt.get('binding_sha256') != up['binding_sha256'] or receipt.get('package') != up['package']
                or receipt.get('modules') != documents[next(name for name, value in documents.items()
                    if value.get('kind') == 'fresh-foundation-intent' and value.get('operation_id') == up['operation_id'])]['modules']
                or any(receipt.get(key) != entry[key] for key in ('operation_id', 'previous_certificate_sha256', 'certificate_sha256'))
                or receipt.get('initial_certificate_sha256') != initial
                or receipt.get('recipe_identity_changed') is not False or receipt.get('owner_binding_changed') is not False
                or receipt.get('remote_command') != 'true' or receipt.get('certificate_only_probe') is not True
                or intent != expected_intent or not phase_matches
                or _time(receipt['finished_at']) > _time(down['finished_at'])
                or set(grant) != {'grant_sha256', 'identity_sha256', 'serial', 'valid_after', 'valid_before', 'algorithm'}
                or any(not _hash(grant.get(key)) for key in ('grant_sha256', 'identity_sha256'))
                or receipt.get('original_grant_sha256') != grant['grant_sha256']
                or receipt.get('original_identity_sha256') != grant['identity_sha256']
                or type(grant['serial']) is not int or not 0 <= grant['serial'] < 2 ** 64
                or type(grant['valid_after']) is not int or type(grant['valid_before']) is not int
                or grant['algorithm'] not in {name + '-cert-v01@openssh.com' for name in
                    ('ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521')}
                or type(receipt.get('minimum_validity_seconds')) is not int
                or not 60 <= receipt['minimum_validity_seconds'] <= 86400
                or not grant['valid_after'] <= _time(receipt['started_at']).timestamp()
                or not _time(receipt['finished_at']).timestamp() + receipt['minimum_validity_seconds'] <= grant['valid_before']
                or type(transport) is not dict or set(transport) != set(recipe['transport'])
                or any(transport[key] != value for key, value in recipe['transport'].items() if key not in ('config', 'certificate'))
                or any(type(transport[key]) is not dict or set(transport[key]) != {'path', 'sha256'}
                    or not _hash(transport[key]['sha256']) or not Path(transport[key]['path']).is_absolute()
                    or '..' in Path(transport[key]['path']).parts for key in ('config', 'certificate'))
                or transport['certificate']['sha256'] != entry['certificate_sha256']):
            raise QualificationError('Fresh renewal lacks unchanged authority, authenticated probe or bounded phase evidence')
        grant_identity = {key: grant[key] for key in ('grant_sha256', 'identity_sha256', 'algorithm')}
        if prior_grant is not None and prior_grant != grant_identity:
            raise QualificationError('Fresh accepted renewals changed the original issuer, key or grant')
        prior_grant, previous = grant_identity, entry['certificate_sha256']
        summaries.append({'receipt': receipt_name, 'intent': intent_name, 'certificate_sha256': previous})
    prefix_current = initial if not initial_chain['renewals'] else initial_chain['renewals'][-1]['certificate_sha256']
    if initial_chain['current_certificate_sha256'] != prefix_current or chain['current_certificate_sha256'] != previous:
        raise QualificationError('Fresh current certificate does not match the accepted renewal history')
    return summaries


def verify_fresh_foundation(result_path, result, manifest, evidence_paths):
    """Verify exact receipt/intent/package/phase bindings after verify_terminal.

    Missing or retained origins return incomplete. Supplied inconsistent fresh
    evidence is rejected; no label, operator attestation or raw hash substitutes
    for the producer's recorded creation, independent API and cleanup receipts.
    """
    from .qualification import QualificationError, _document, file_reference
    root = Path(result_path).parent
    files = {item['path']: item for item in manifest['files']}
    selected = set(evidence_paths)
    def document(relative):
        if relative not in files:
            raise QualificationError('Fresh receipt is outside the finalized evidence manifest')
        return _document(root, files[relative])[1]
    reports, documents = [], {}
    for relative in selected:
        value = document(relative)
        documents[relative] = value
        if value.get('kind') == 'owned-fresh-rke2-foundation':
            reports.append((relative, value))
    if not reports:
        return {'status': 'incomplete', 'gap': 'actual-fresh-foundation-up-and-cleanup-receipts-missing'}
    ups = [(path, row) for path, row in reports if row.get('action') == 'up' and row.get('foundation_readiness') == 'fresh-rke2-ready']
    downs = [(path, row) for path, row in reports if row.get('action') in ('down', 'reconcile') and row.get('complete_cleanup') is True]
    if len(ups) != 1 or len(downs) != 1:
        return {'status': 'incomplete', 'gap': 'one-complete-fresh-creation-and-terminal-cleanup-required'}
    state = document('state.json')
    inputs = state['lifecycle']['inputs']
    config = document(inputs['config']['path'])
    lock = document(inputs['lock']['path'])
    package_path = file_reference(root, files[inputs['package']['path']], document=True)
    package_raw = _read(package_path, 1_000_000)
    if _sha(package_raw) != result['identities']['package_sha256']:
        raise QualificationError('Fresh producer package differs from the actual finalized run')
    members = {}
    for line in package_raw.decode('utf-8').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  ([^\s]+)', line)
        if match is None or match[2] in members:
            raise QualificationError('Fresh package manifest is malformed')
        members[match[2]] = match[1]
    from .fresh_foundation_recipe import binding, validate
    recipe = validate(config['runtime']['foundation'])
    phases = {row['phase_id']: row for row in result['phase_records']}
    retained = []
    for relative, report in (ups[0], downs[0]):
        phase = phases.get(report.get('phase_id'))
        phase_paths = set() if phase is None else {ref['path'] for ref in phase['artifacts']}
        intents = [(name, row) for name, row in documents.items() if name in phase_paths
                   and row.get('kind') == 'fresh-foundation-intent' and row.get('operation_id') == report['operation_id']]
        remotes = [(name, row) for name, row in documents.items() if name in phase_paths
                   and row.get('operation_id') == report['operation_id'] and row.get('generation') == result['run_id']
                   and 'root_disk_creation' in row and 'source_assets_preserved' in row]
        if len(intents) != 1 or len(remotes) != 1:
            raise QualificationError('Fresh evidence selection omits or duplicates its original phase intent/remote observation')
        (intent_name, intent), (remote_name, remote) = intents[0], remotes[0]
        expected_phase = ('provision', 'up') if report['action'] == 'up' else ('cleanup', 'down')
        if (phase is None or phase['outcome'] != 'completed'
                or (phase['phase_id'], phase['operation']) != expected_phase
                or not {relative, intent_name, remote_name} <= {ref['path'] for ref in phase['artifacts']}
                or report.get('schema_version') != 1 or report.get('execution_mode') != 'operator'
                or report.get('run_id') != result['run_id'] or report.get('identities') != result['identities']
                or report.get('package') != {'sha256': result['identities']['package_sha256'], 'files': len(members)}
                or report.get('mutation_effect') != 'applied' or report.get('unresolved_operations') != []
                or report.get('coverage') != 'fresh-rke2-foundation-only'
                or intent.get('kind') != 'fresh-foundation-intent'
                or any(intent.get(key) != report.get(key) for key in ('run_id', 'operation_id', 'action', 'identities'))
                or set(intent.get('modules', {})) != MODULES
                or any(members.get('src/devsecops/factory/' + name + '.py') != pin for name, pin in intent['modules'].items())
                or not _time(phase['started_at']) <= _time(report['finished_at']) <= _time(phase['finished_at'])):
            raise QualificationError('Fresh producer differs from its canonical phase, package or immutable intent')
        expected = binding(recipe, run_id=result['run_id'], environment_id=result['environment_id'],
                           identities=result['identities'], modules=intent['modules'])
        if (intent.get('binding') != expected or report.get('binding_sha256') != expected
                or remote.get('binding') != expected or remote.get('operation_id') != report['operation_id']
                or remote.get('run_id') != result['run_id'] or remote.get('generation') != result['run_id']
                or remote.get('unresolved_operations') != []):
            raise QualificationError('Fresh creation or cleanup has a different ownership generation')
        retained.append((report, remote))
    (up, created), (down, cleaned) = retained
    origin = up.get('origin')
    if (type(origin) is not dict or origin.get('kind') != 'fresh-created'
            or origin.get('generation') != result['run_id'] or origin.get('binding') != up['binding_sha256']
            or origin.get('up_operation_id') != up['operation_id']
            or not _identity(origin.get('root_identity')) or not _fingerprint(origin.get('cluster_fingerprint'))
            or down.get('origin') != origin or up.get('cluster_fingerprint') != origin['cluster_fingerprint']
            or down.get('cluster_fingerprint') != origin['cluster_fingerprint']
            or created.get('state') != 'ready' or created.get('up_operation_id') != up['operation_id']
            or cleaned.get('up_operation_id') != up['operation_id']
            or created.get('root_identity') != origin['root_identity'] or cleaned.get('root_identity') != origin['root_identity']
            or origin.get('api_ca_basis') != 'issued-by-observed-new-guest'
            or origin.get('ssh_pki_basis') != 'operator-supplied-pins-with-run-created-copies'):
        raise QualificationError('Fresh origin lacks original new-guest identity and preserved cleanup binding')
    disk = origin.get('root_disk_creation', {})
    guest = origin.get('guest_freshness', {})
    if (disk.get('exclusive_generation') != origin['root_identity'] or not _identity(disk.get('disk_identity'))
            or disk.get('helper_exit_code') != 0 or not _hash(disk.get('helper_id'))
            or disk.get('virtual_bytes') != 171798691840 or disk.get('initial_source_sha256') != recipe['boot_image']['sha256']
            or guest.get('before_install') != 'rke2-config-and-data-absent' or not _hash(guest.get('seed_sha256'))
            or any(remote.get('root_disk_creation') != disk or remote.get('guest_freshness') != guest
                   for remote in (created, cleaned))):
        raise QualificationError('Fresh disk or pristine pre-install observation is missing')
    assets = {'boot': recipe['boot_image'], **recipe['rke2_assets'], **recipe['ssh_pki']}
    observed_assets = origin.get('source_assets', {})
    if set(observed_assets) != set(assets) or any(
            {key: observed_assets[name].get(key) for key in ('path', 'sha256', 'bytes')} != pin
            or not _identity({key: observed_assets[name].get(key) for key in ('device', 'inode')})
            for name, pin in assets.items()):
        raise QualificationError('Fresh supplied assets differ from the frozen operation selection')
    if any(remote.get('source_assets') != observed_assets for remote in (created, cleaned)):
        raise QualificationError('Fresh source asset identity changed between creation and cleanup')
    observed = created.get('api_observations', [])
    if len(observed) != 3:
        raise QualificationError('Fresh API identity needs three actual independent observations')
    for row in observed:
        routes = ('/readyz', '/version', '/api/v1/nodes', '/api/v1/namespaces/default',
                  '/api/v1/namespaces/kube-system', '/api')
        transport = row.get('transport', [])
        if (row.get('ready') is not True or row.get('fingerprint') != origin['cluster_fingerprint']
                or row.get('tls_hostname_and_ca_verified') is not True or row.get('authenticated_api_read') != '/api'
                or row.get('server_version') != lock['platform']['distribution_version']
                or len(transport) != 6 or any(item.get('status') != 200 or item.get('method') != 'GET'
                    or item.get('request_index') != index or item.get('request_sha256') != _sha(route.encode())
                    or item.get('outcome') != 'complete' or not _hash(item.get('body_sha256'))
                    or type(item.get('body_bytes')) is not int or item['body_bytes'] <= 0
                    for index, (item, route) in enumerate(zip(transport, routes, strict=True), 1))
                or not _time(phases[up['phase_id']]['started_at']) <= _time(row['observed_at']) <= _time(up['finished_at'])):
            raise QualificationError('Fresh independent authenticated API readiness evidence differs')
    cleanup_rows = {row['kind']: row for row in down.get('resources', [])}
    remote_rows = cleaned.get('resources', [])
    if (cleaned.get('complete_cleanup') is not True or cleaned.get('source_assets_preserved') is not True
            or cleaned.get('tool_image_preserved') is not True or not _preservation(down.get('resources'))
            or any(cleanup_rows.get(row['kind']) != row for row in remote_rows)
            or cleanup_rows.get('disposable-disk-seed-pki', {}).get('identity') != disk['disk_identity']
            or cleanup_rows.get('supplied-assets', {}).get('identity') != observed_assets
            or cleanup_rows.get('supplied-tool-image', {}).get('identity') != recipe['tool_image']
            or not _time(up['finished_at']) < _time(down['finished_at']) <= _time(result['finalized_at'])
            or result['cleanup'].get('environment_released') is not True):
        raise QualificationError('Fresh environment cleanup or supplied input preservation is incomplete')
    renewals = _renewals(recipe, result, up, down, documents, files)
    return {'status': 'pass', 'basis': 'terminal-bound-producer-and-independent-api-receipts',
            'transport_renewals': renewals,
            'run_id': result['run_id'], 'generation': origin['generation'],
            'root_identity': origin['root_identity'], 'disk_identity': disk['disk_identity'],
            'cluster_uid': origin['cluster_fingerprint']['namespaces']['kube-system'],
            'cluster_fingerprint': origin['cluster_fingerprint'],
            'creation_receipt': ups[0][0], 'cleanup_receipt': downs[0][0]}


def distinct_fresh_runs(results):
    if len(results) != 2 or any(row.get('status') != 'pass' for row in results):
        return False
    first, second = results
    if any(row['cluster_uid'] != row['cluster_fingerprint']['namespaces']['kube-system'] for row in results):
        return False
    return (first['generation'] != second['generation'] and first['root_identity'] != second['root_identity']
            and first['disk_identity'] != second['disk_identity']
            and first['cluster_uid'] != second['cluster_uid']
            and first['cluster_fingerprint']['ca_sha256'] != second['cluster_fingerprint']['ca_sha256']
            and set(first['cluster_fingerprint']['nodes'].values()).isdisjoint(second['cluster_fingerprint']['nodes'].values()))
