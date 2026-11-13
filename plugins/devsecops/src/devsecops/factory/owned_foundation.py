"""Journal-bound retained RKE2 operations, not complete factory provisioning.

No saved observation can authorize a mutation. Original screen receipts are
operator-selected provenance; current resource identity comes from the fixed
TCG driver. This component never completes a canonical phase or releases the
environment. The registered readiness handler does not dispatch it yet.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from devsecops.adapters.signatures import _snapshot
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from . import service_inventory, tcg
from .cleanup_observation import ComponentObservations
from .lifecycle import PhaseContext, PhaseOutcome, _load_inputs
from .native_codex import _package
from .owned_linux import DRIVER_ID, _plain
from .state import RunJournal, StateError, _json, _read, _safe_path, _sha
from .tcg import RetainedTCGDriver, TCGObservation, TCGSpec, VERSION


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
MAX_RECEIPT = 1_000_000
_GAPS = ['fresh-rke2-provisioning-not-performed', 'factory-service-bootstrap-unimplemented',
         'service-connectivity-unqualified', 'retained-dependencies-preserved',
         'complete-resource-cleanup-unimplemented']


def _checkpoint(deadline, config, action):
    if time.monotonic() >= deadline:
        raise StateError('Foundation operation deadline expired')
    if action == 'up' and datetime.now(timezone.utc) >= datetime.fromisoformat(
            config['expires_at'].replace('Z', '+00:00')):
        raise StateError('Foundation provisioning window expired')


def _original(raw, selected, pins_raw):
    """Validate actual selected screen bytes, without authenticating their author."""
    value, pins = _json(raw), _json(pins_raw)
    try:
        result, cleanup = value['rke2'], value['cleanup']
        observations = result['observations'][-3:]
        if (value['schema_version'] != 1 or value['kind'] != 'tcg-rke2-readiness-screen'
                or value['run_id'] != selected['provision_run_id']
                or value['private_lab_path'] != selected['remote_lab']
                or value['status'] != 'rke2_readiness_pass'
                or value['live_platform_qualification'] != 'rke2_api_and_node_only'
                or value['guest'] != {'vcpus': 8, 'memory_bytes': 32 * 1024**3,
                    'disk_virtual_bytes': 160 * 1024**3, 'accelerator': 'tcg', 'architecture': 'x86_64'}
                or value['toolchain']['image_id'] != selected['tool_image']
                or result['status'] != 'api_and_node_ready'
                or result['guest_local_artifacts_verified'] is not True
                or result['consecutive_ready_observations'] != 3 or len(observations) != 3
                or result['credentials']['api_ca_sha256'] != selected['original_ca_sha256']
                or result['credentials']['directory'] != selected['remote_lab'] + '/operator'
                or result['credentials']['status'] != 'issued_by_observed_guest'
                or result['api_read_smoke']['kind'] != 'APIVersions'
                or 'v1' not in result['api_read_smoke']['versions']
                or cleanup['containers_and_network_removed'] is not True
                or cleanup['remaining_runtime_resources'] != []
                or cleanup['preserved_private_lab_path'] != selected['remote_lab']
                or cleanup['preserved_tool_image_id'] != selected['tool_image']
                or not {'probe.qcow2', 'seed.iso', 'ssh/operator', 'ssh/host', 'operator'}.issubset(
                    cleanup['preserved_files'])
                or value['rke2_provenance']['pins'] != pins
                or value['rke2_provenance']['pins_sha256'] != _sha(pins_raw)
                or value['rke2_provenance']['all_asset_hashes_verified'] is not True
                or value['rke2_provenance']['release_checksums_verified'] is not True
                or pins['rke2_version'] != VERSION):
            raise ValueError
        for row in observations:
            if (row['readyz'] != 'ok' or row['server_version'] != VERSION or len(row['nodes']) != 1
                    or row['nodes'][0]['ready'] is not True or row['nodes'][0]['kubelet_version'] != VERSION):
                raise ValueError
    except (KeyError, TypeError, IndexError, ValueError):
        raise StateError('Original retained RKE2 receipt does not match the selected foundation') from None
    return {'sha256': _sha(raw), 'bytes': len(raw), 'provision_run_id': selected['provision_run_id'],
            'authority': 'operator-selected-historical-provenance', 'current_readiness': 'not-established'}


def _within(path, root):
    """Reject lexical containment and filesystem case aliases before any mkdir."""
    if path.is_relative_to(root):
        return True
    try:
        target = root.stat()
    except FileNotFoundError:
        return False
    for ancestor in (path, *path.parents):
        try:
            item = ancestor.stat()
        except FileNotFoundError:
            continue
        if (item.st_dev, item.st_ino) == (target.st_dev, target.st_ino):
            return True
    return False


class OwnedRKE2Foundation:
    """Fixed, directly callable component; the caller journals and resolves effects.

    Hold both journal locks, begin the canonical `provision/up` or `cleanup/down`
    mutation, then call the corresponding method. Returned PhaseOutcomes always
    remain incomplete. Reconciliation is read-only and never retries a mutation.
    """

    def __init__(self, journal, *, plugin_id, plugin_version):
        if (type(journal) is not RunJournal or not isinstance(plugin_id, str)
                or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}@[a-z][a-z0-9-]{0,62}', plugin_id)
                or not isinstance(plugin_version, str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+-]{0,127}', plugin_version)):
            raise StateError('Foundation requires a journal and explicit installed plugin identity')
        self.journal, self.plugin_id, self.plugin_version = journal, plugin_id, plugin_version
        self._pid = os.getpid()
        self._cleanup_observations = ComponentObservations()
        self._bootstrap_observations = ComponentObservations()

    def _admit(self, context, action, deadline, operation_id=None):
        if (type(context) is not PhaseContext or self._pid != os.getpid()
                or not self.journal._lifecycle_locked):
            raise StateError('Foundation requires the current locked lifecycle context')
        state, phase = self.journal.state, _plain(context.phase)
        lifecycle = state.get('lifecycle', {})
        expected_action = phase.get('operation') if action == 'reconcile' else action
        if (expected_action not in ('up', 'down') or phase.get('operation') != expected_action
                or context.run_id != state['run_id'] or context.run_dir != self.journal.run_dir
                or _plain(context.identities) != self.journal.identities
                or _plain(context.input_artifacts) != lifecycle.get('inputs')
                or context.execution_mode != lifecycle.get('execution_mode')
                or lifecycle.get('driver_id') != DRIVER_ID or phase not in lifecycle.get('phases', [])
                or lifecycle.get('final_result') is not None):
            raise StateError('Foundation operation differs from the current lifecycle')
        current = self.journal.current_mutation(phase['id'], expected_action, reconciliation=action == 'reconcile')
        if operation_id is not None and operation_id != current:
            raise StateError('Reconciliation operation does not match the journal')
        paths = {name: self.journal._artifact_path(ref['path']) for name, ref in lifecycle['inputs'].items()}
        _, docs = _load_inputs(paths, self.journal.identities, state['environment_id'])
        config = docs['config']
        if docs != _plain(context.inputs) or config['profile'] != 'bigbang-live':
            raise StateError('Foundation inputs differ from the approved reference profile')
        if 'cleanup' not in config['allowed_mutations'] or (action == 'up' and 'provision' not in config['allowed_mutations']):
            raise StateError('Foundation operation lacks its recorded mutation scope')
        _checkpoint(deadline, config, action)
        selected = config['runtime'].get('foundation')
        platform = docs['lock']['platform']
        if (selected is None or config['runtime']['architecture'] != 'amd64'
                or any(platform.get(k) != v for k, v in {'os': 'linux', 'architecture': 'amd64',
                    'distribution': 'rke2', 'distribution_version': VERSION,
                    'kubernetes_version': VERSION, 'cni': 'canal'}.items())):
            raise StateError('Foundation configuration or locked platform is unsupported')
        if PACKAGE_ROOT != Path(__file__).resolve().parents[3]:
            raise StateError('Foundation must load from its selected installed package')
        manifest, members = _package(PACKAGE_ROOT, self.journal.identities['package_sha256'],
                                    self.plugin_id, self.plugin_version, deadline)
        for name, file in (('owned_foundation', Path(__file__).resolve()),
                           ('tcg', Path(tcg.__file__).resolve()),
                           ('service_inventory', Path(service_inventory.__file__).resolve())):
            # Paths are fixed source modules, never supplied by configuration.
            relative = 'src/devsecops/factory/'+name+'.py'
            if file != PACKAGE_ROOT/relative or members.get(relative) != _sha(_read(file, MAX_RECEIPT)):
                raise StateError('Foundation implementation differs from the installed package')
        services = service_inventory.classify_foundation_services(docs['environment'], config)
        _checkpoint(deadline, config, action)
        spec = TCGSpec(context.run_id, state['environment_id'], dict(self.journal.identities),
            selected['host'], selected['remote_lab'], selected['tool_image'], selected['original_ca_sha256'],
            selected['provision_run_id'], selected['source_receipt']['sha256'], selected['transport']).validate()
        return current, config, selected, spec, services, {'sha256': _sha(manifest), 'files': len(members)}

    def _perform(self, context, action, operation_id=None):
        if (type(context) is not PhaseContext or type(context.deadline) not in (int, float)
                or not math.isfinite(context.deadline)):
            raise StateError('A bounded foundation context is required')
        deadline = min(context.deadline, time.monotonic() + {'up': 1230, 'down': 300, 'reconcile': 90}[action])
        admitted = self._admit(context, action, deadline, operation_id)
        operation, config, selected, spec, services, package = admitted
        if action == 'up':
            deadline = min(deadline, time.monotonic() + max(0, (datetime.fromisoformat(
                config['expires_at'].replace('Z', '+00:00')) - datetime.now(timezone.utc)).total_seconds()))
        output = self.journal.run_dir / ('foundation-' + uuid4().hex)
        private = _safe_path(Path(selected['private_state_directory']))
        # TCG state remains stable across calls and separate from retained input
        # material and ephemeral evidence output; no caller chooses an output path.
        if (not Path(selected['private_state_directory']).is_absolute()
                or any(_within(private, protected) or _within(protected, private)
                       for protected in (self.journal.run_dir, PACKAGE_ROOT))):
            raise StateError('Foundation private state must be separate from the run journal and installed package')
        for item in [selected['source_receipt'], *[selected['transport'][name] for name in
                ('config', 'known_hosts', 'certificate', 'identity', 'proxy')]]:
            if _within(_safe_path(Path(item['path'])), private):
                raise StateError('Foundation private state cannot contain selected input artifacts')
        with _new_directory(output) as directory:
            source = selected['source_receipt']
            ref = _snapshot(Path(source['path']), output/'original-receipt.json', MAX_RECEIPT, deadline=deadline)
            if ref['sha256'] != source['sha256']:
                raise StateError('Original receipt bytes differ from the operator pin')
            pins_path = PACKAGE_ROOT/'demo/software-factory/provision/tcg/rke2-release.json'
            pins_raw = _read(pins_path, MAX_RECEIPT)
            original = _original(_read(output/'original-receipt.json', MAX_RECEIPT), selected, pins_raw)
            # Every connection still uses the TCG transport's own exact hash and
            # certificate/host checks. Freeze copies only to audit the selected inputs.
            refs = {}
            for name in ('config', 'known_hosts', 'certificate', 'identity', 'proxy'):
                ref = _snapshot(Path(selected['transport'][name]['path']), output/('transport-'+name),
                                MAX_RECEIPT, deadline=deadline)
                if ref['sha256'] != selected['transport'][name]['sha256']:
                    raise StateError('Foundation transport artifact differs from its pin')
                refs[name] = ref
            self._admit(context, action, deadline, operation_id)
            report = {'schema_version': 1, 'kind': 'owned-rke2-foundation', 'action': action,
                'run_id': context.run_id, 'operation_id': operation, 'phase_id': context.phase['id'],
                'identities': dict(self.journal.identities), 'execution_mode': context.execution_mode,
                'package': package, 'original_provenance': original, 'transport_artifacts': refs,
                'binding_sha256': spec.digest, 'foundation_readiness': 'unknown',
                'canonical_success': False, 'canonical_phase_status': 'incomplete',
                'mutation_effect': 'unknown', 'complete_cleanup': False,
                'started_at': datetime.now(timezone.utc).isoformat(), 'services': services,
                'resources': [], 'gaps': list(_GAPS)}
            driver = RetainedTCGDriver(private, spec)
            # Fixed methods only, no callback or dynamically selected executable.
            if action == 'up':
                observation = driver.up(operation, deadline=deadline)
            elif action == 'down':
                observation = driver.down(operation, deadline=deadline)
            else:
                observation = driver.reconcile(operation, deadline=deadline)
            _checkpoint(deadline, config, action)
            if (type(observation) is not TCGObservation or observation.receipt.parent != private/'receipts'
                    or observation.mutation_effect not in ('applied', 'not_applied', 'unknown')):
                raise StateError('Direct foundation observation is unsupported')
            copied = _snapshot(observation.receipt, output/'tcg-observation.json', MAX_RECEIPT, deadline=deadline)
            if copied['sha256'] != observation.receipt_sha256:
                raise StateError('Direct foundation observation changed before retention')
            actual = _json(_read(output/'tcg-observation.json', MAX_RECEIPT))
            if (actual.get('kind') != 'retained-tcg-observation' or actual.get('action') != action
                    or actual.get('run_id') != context.run_id or actual.get('operation_id') != operation
                    or actual.get('identities') != dict(self.journal.identities)
                    or actual.get('binding_sha256') != spec.digest or actual.get('driver_sha256') != driver.source_sha256
                    or actual.get('state') != observation.state or actual.get('mutation_effect') != observation.mutation_effect
                    or actual.get('canonical_platform_qualified') is not False):
                raise StateError('Direct foundation observation does not match the current operation')
            report['observation'] = copied
            report['mutation_effect'] = observation.mutation_effect
            self._inventory(report, actual, selected, private, observation)
            # Audit retained inputs and actual implementation again before and
            # after publication. No late receipt survives a route/deadline change.
            def audit():
                self._admit(context, action, deadline, operation_id)
                _verify_output_path(output, directory)
                if (_sha(_read(output/'original-receipt.json', MAX_RECEIPT)) != original['sha256']
                        or _sha(_read(output/'tcg-observation.json', MAX_RECEIPT)) != copied['sha256']):
                    raise StateError('Retained foundation evidence changed')
                for name, reference in refs.items():
                    if _sha(_read(output/('transport-'+name), MAX_RECEIPT)) != reference['sha256']:
                        raise StateError('Retained foundation transport changed')
                _checkpoint(deadline, config, action)
            try:
                audit()
                report['finished_at'] = datetime.now(timezone.utc).isoformat()
                _write(directory, 'foundation.json', report)
                os.fsync(directory)
                audit()
            except BaseException:
                try:
                    os.unlink('foundation.json', dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
                raise
        outcome = PhaseOutcome('incomplete', 'owned-rke2-foundation-only',
            (output/'foundation.json', output/'tcg-observation.json'), mutation_effect=observation.mutation_effect)
        self._bootstrap_observations.retain(outcome, {'report': report, 'actual': actual,
                                                   'private_state_directory': str(private)})
        return self._cleanup_observations.retain(outcome, report)

    @staticmethod
    def _inventory(report, actual, selected, private, observation):
        remote = actual.get('remote', {})
        disk = remote.get('disk_identity')
        if disk is not None and (not isinstance(disk, dict) or set(disk) != {'device', 'inode'}
                or any(type(value) is not int or value < 0 for value in disk.values())):
            raise StateError('Observed retained disk identity is unsupported')
        for kind in ('container', 'network'):
            identifier = remote.get(kind+'_id')
            if identifier is not None and (not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{64}', identifier)):
                raise StateError('Observed runtime resource identity is unsupported')
            report['resources'].append({'kind': kind, 'identity': identifier,
                'ownership': 'driver-verified' if identifier else 'not-observed', 'disposition': 'remove',
                'state': 'present' if identifier else 'absent' if remote.get('state') == 'absent' else 'unknown'})
        report['resources'].extend([
            {'kind': 'retained-disk', 'path': selected['remote_lab']+'/probe.qcow2', 'identity': disk,
             'disposition': 'preserve', 'state': 'observed' if disk else 'unverified'},
            {'kind': 'retained-seed-and-guest-credentials', 'path': selected['remote_lab'],
             'provision_run_id': selected['provision_run_id'], 'disposition': 'preserve', 'state': 'not-removed'},
            {'kind': 'tool-image', 'identity': selected['tool_image'], 'disposition': 'preserve', 'state': 'not-removed'},
            {'kind': 'local-private-state-and-credentials', 'path': str(private), 'disposition': 'preserve', 'state': 'not-removed'},
        ])
        tunnel = actual.get('tunnel_identity')
        report['resources'].append({'kind': 'tunnel', 'identity': tunnel, 'disposition': 'remove',
            'state': 'absent' if actual.get('tunnel_cleanup', {}).get('owned_tunnel_absent') is True
            else 'observed' if tunnel else 'unknown'})
        if observation.state == 'running_ready' and observation.mutation_effect != 'unknown':
            rows = actual.get('api_observations', [])
            if actual['action'] == 'reconcile':
                rows = [actual.get('api_observation', {})]
            if (not disk or not rows or not all(row.get('ready') is True for row in rows[-3:])
                    or (actual['action'] == 'up' and len(rows) < 3)):
                raise StateError('Ready foundation lacks actual retained cluster observations')
            report['foundation_readiness'] = 'retained-rke2-ready'
            report['cluster_fingerprint'] = rows[-1]['fingerprint']
        protection = remote.get('protected_resources', {})
        if (protection.get('verified') is True and protection.get('disk_identity') == disk
                and protection.get('original_ca_sha256') == selected['original_ca_sha256']
                and protection.get('tool_image') == selected['tool_image']
                and protection.get('files')):
            for row in report['resources']:
                if row['disposition'] == 'preserve':
                    row['state'] = 'preserved'
            report['protected_resources'] = protection
        report['runtime_cleanup'] = ('observed-absent' if actual['action'] in ('down', 'reconcile')
            and observation.state == 'absent' and observation.mutation_effect == 'applied'
            and remote.get('remaining_owned_runtime') is False
            and actual.get('tunnel_cleanup', {}).get('owned_tunnel_absent') is True else 'incomplete')

    def up(self, context):
        return self._perform(context, 'up')

    def down(self, context):
        return self._perform(context, 'down')

    def reconcile(self, context, operation_id):
        return self._perform(context, 'reconcile', operation_id)

    def bootstrap_observation(self, outcome):
        return self._bootstrap_observations.observe(outcome)

    def cleanup_observation(self, outcome):
        return self._cleanup_observations.observe(outcome)

    def validate_cleanup(self, receipts):
        raise StateError('Retained foundation cleanup cannot release the complete factory environment')
