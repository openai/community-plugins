"""Fixed production composition, diagnostics and evidence for the owned backend.

Explicit recipes select concrete resident components. Without that composition,
diagnostic reads establish only response facts, never identity, grants, ownership,
image trust, cleanup or permission to mutate services.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import sys
import time
from types import MappingProxyType
from uuid import uuid4

from devsecops.adapters.http import APIError, HTTPSReadClient
from devsecops.adapters.signatures import _snapshot
from devsecops.adapters.vulnerability_policy import checkpoint
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from devsecops.runtime.context import MAX_BUNDLE, canonical_bytes
from . import auxiliary_inputs, evidence_export, service_inventory
from .doctor import inspect, REQUIRED_SERVICES
from .lifecycle import PhaseContext, PhaseOutcome, _load_inputs, _deadline
from .native_codex import _package
from .state import MAX_ARTIFACT_BYTES, RunJournal, StateError, _read, _sha


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DRIVER_ID = 'owned-linux'
MAX_TIMEOUT = 120
MAX_SCOPE_ITEMS = 8
_VERSION = re.compile(r'v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]{1,64})?')
_MISSING = (
    'operator-identity-and-grants-unverified', 'registry-origin-and-image-trust-unverified',
    'runtime-capacity-and-cluster-ownership-unverified', 'runner-and-builder-unqualified',
    'bigbang-flux-and-platform-controls-unqualified', 'bootstrap-handler-unimplemented',
    'ci-build-and-deployment-delivery-unintegrated', 'native-runtime-recovery-unintegrated',
    'fault-reset-and-complete-cleanup-unimplemented',
)


def _withdraw_terminal(directory, name, identity, original_error):
    """Withdraw only our created inode; keep the original failure authoritative."""
    try:
        try:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (current.st_dev, current.st_ino) == identity:
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
    except BaseException as cleanup_error:
        original_error.add_note('Diagnostic withdrawal failed: '+type(cleanup_error).__name__)


def _write_terminal(directory, name, value):
    raw = canonical_bytes(value)
    if len(raw) > MAX_BUNDLE:
        raise ValueError('Output artifact exceeds its byte bound')
    fd, identity = None, None
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError('Diagnostic write did not advance')
            offset += written
        os.fsync(fd)
        closing, fd = fd, None
        os.close(closing)
        return identity
    except BaseException as error:
        if fd is not None:
            closing, fd = fd, None
            try:
                os.close(closing)
            except BaseException as close_error:
                error.add_note('Diagnostic close failed: '+type(close_error).__name__)
        if identity is not None:
            _withdraw_terminal(directory, name, identity, error)
        raise


def _plain(value, depth=0):
    if depth > 32:
        raise StateError('Phase context exceeds its supported depth')
    if type(value) in (dict, MappingProxyType):
        return {key: _plain(child, depth+1) for key, child in value.items()}
    if type(value) in (tuple, list):
        return [_plain(child, depth+1) for child in value]
    if value is None or type(value) in (str, int, bool):
        return value
    raise StateError('Unsupported phase context value')


def _version(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise APIError('unsupported-version')
    return value


def _matches_version(observed, expected):
    return observed.removeprefix('v') == expected.removeprefix('v')


def _window_checkpoint(deadline, expires_at):
    checkpoint(deadline)
    if datetime.now(timezone.utc) >= datetime.fromisoformat(expires_at.replace('Z', '+00:00')):
        raise StateError('Operator diagnostic window has expired')


class OwnedLinuxDriver:
    """Fixed dispatch over a current journal; no callbacks or saved capabilities.

    The caller holds the journal lifecycle lock (and any mutation lock required
    by the engine). The package is the actual versioned Codex installation
    containing this module. Registration in LifecycleEngine is a separate step.
    """

    def __init__(self, journal, *, plugin_id, plugin_version, auxiliary_inputs=None):
        if (type(journal) is not RunJournal or not isinstance(plugin_id, str)
                or not re.fullmatch(r'[a-z][a-z0-9-]{0,62}@[a-z][a-z0-9-]{0,62}', plugin_id)
                or not isinstance(plugin_version, str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+-]{0,127}', plugin_version)):
            raise StateError('An explicit journal and installed plugin identity are required')
        self.journal, self.plugin_id, self.plugin_version = journal, plugin_id, plugin_version
        self.auxiliary_sources = deepcopy(auxiliary_inputs)
        self._pid = os.getpid()
        self._correction_bindings = {}
        self._native_session = None
        self._production_instance = None

    def _production(self):
        from .owned_production import OwnedProductionCoordinator, REQUIRED_SELECTIONS
        selected = set(self.auxiliary_sources or {})
        if not selected & REQUIRED_SELECTIONS:
            return None
        if not REQUIRED_SELECTIONS <= selected:
            raise StateError('Production operation selections are incomplete')
        if self._production_instance is None:
            self._production_instance = OwnedProductionCoordinator(self)
        if (type(self._production_instance) is not OwnedProductionCoordinator
                or self._production_instance.driver is not self):
            raise StateError('Production coordinator identity changed')
        return self._production_instance

    @property
    def native_cancelled(self):
        return self._native_session is not None and self._native_session.cancelled

    def bind_native_session(self, session):
        from .native_factory_session import NativeFactorySession
        if (type(session) is not NativeFactorySession or self._native_session is not None
                or session._engine.driver is not self or session._engine.journal is not self.journal
                or self._pid != os.getpid()):
            raise StateError('Native session must be the original resident canonical caller')
        self._native_session = session

    def completed_native_artifacts(self, step_id):
        if self._native_session is None:
            raise StateError('Completed production native evidence requires its resident session')
        return self._native_session.completed_step_artifacts(step_id)

    def terminal_native_identity(self):
        if self._native_session is None or not self.journal.state['native_results']:
            return None
        return self._native_session.terminal_native_identity()

    def _admit(self, context, deadline):
        checkpoint(deadline)
        if (self._pid != os.getpid() or type(context) is not PhaseContext
                or not self.journal._lifecycle_locked or self.journal.needs_reconciliation):
            raise StateError('A current locked lifecycle context is required')
        state = self.journal.state
        lifecycle = state.get('lifecycle', {})
        phase = _plain(context.phase)
        if (context.run_id != state['run_id'] or context.run_dir != self.journal.run_dir
                or _plain(context.identities) != self.journal.identities
                or _plain(context.input_artifacts) != lifecycle.get('inputs')
                or context.execution_mode != lifecycle.get('execution_mode')
                or lifecycle.get('driver_id') != DRIVER_ID
                or (lifecycle.get('active_phase') or {}).get('phase_id') != phase.get('id')
                or phase not in lifecycle.get('phases', []) or lifecycle.get('final_result') is not None):
            raise StateError('Owned Linux phase differs from the current journal')
        paths = {name: self.journal._artifact_path(ref['path']) for name, ref in lifecycle['inputs'].items()}
        raw, docs = _load_inputs(paths, self.journal.identities, state['environment_id'])
        if docs != _plain(context.inputs):
            raise StateError('Phase inputs differ from the retained operator inputs')
        self._auxiliary_artifacts()
        if phase['operation'] == 'preflight':
            _window_checkpoint(deadline, docs['config']['expires_at'])
        checkpoint(deadline)
        return raw, docs

    def _auxiliary_artifacts(self):
        from .auxiliary_inputs import load_selection, selected_artifacts
        raw = load_selection(self.auxiliary_sources, identities=self.journal.identities,
                             environment_id=self.journal.state['environment_id'],
                             plugin_id=self.plugin_id, plugin_version=self.plugin_version)
        return selected_artifacts(self.journal, raw)

    def mutation_intent_artifacts(self, context):
        """Read-only fixed pre-dispatch binding; effects require a later owned intent."""
        self._admit(context, context.deadline)
        self._package(self.journal.identities['package_sha256'], context.deadline)
        return self._auxiliary_artifacts()

    def _package(self, expected, deadline):
        if PACKAGE_ROOT != Path(__file__).resolve().parents[3]:
            raise StateError('Backend must load from its selected installed package')
        raw, paths = _package(PACKAGE_ROOT, expected, self.plugin_id, self.plugin_version, deadline)
        for module, file in (('owned_linux', Path(__file__).resolve()),
                             ('auxiliary_inputs', Path(auxiliary_inputs.__file__).resolve()),
                             ('service_inventory', Path(service_inventory.__file__).resolve()),
                             ('evidence_export', Path(evidence_export.__file__).resolve())):
            name = 'src/devsecops/factory/' + module + '.py'
            if file != PACKAGE_ROOT/name or paths.get(name) != _sha(_read(file, MAX_ARTIFACT_BYTES)):
                raise StateError('Backend source is not included in the selected package')
        if self.auxiliary_sources:
            from . import mission_git_invocation, service_recipe
            for module in (mission_git_invocation, service_recipe):
                file = Path(module.__file__).resolve()
                name = 'src/devsecops/factory/' + file.name
                if file != PACKAGE_ROOT/name or paths.get(name) != _sha(_read(file, MAX_ARTIFACT_BYTES)):
                    raise StateError('Auxiliary parser is not included in the selected package')
        if set(self.auxiliary_sources or {}) & {'owned_bootstrap_recipe', 'owned_operations_recipe', 'native_selection'}:
            # All loaded package modules must come from this installation. The
            # manifest verifier above audits every selected file's bytes.
            for name, module in tuple(sys.modules.items()):
                if name == 'devsecops' or name.startswith('devsecops.'):
                    location = getattr(module, '__file__', None)
                    if location is None:
                        raise StateError('Loaded package module has no fixed source origin')
                    file = Path(location).resolve()
                    if not file.is_relative_to(PACKAGE_ROOT):
                        raise StateError('Production module loaded outside the installed package')
                    relative = file.relative_to(PACKAGE_ROOT).as_posix()
                    if paths.get(relative) != _sha(_read(file, MAX_ARTIFACT_BYTES)):
                        raise StateError('Production module differs from installed package membership')
        return {'sha256': _sha(raw), 'files': len(paths), 'content_verified': True,
                'release_qualification': 'unverified'}

    def _service(self, service, lock, output, deadline, expires_at, *, disposition=None):
        name, auth = service['id'], service['read_auth']
        row = {'service': name, 'origin': service['origin'], 'observation_status': 'incomplete',
               'tls_destination_verified': False, 'credential_sent': 'not-confirmed',
               'authentication': 'unverified', 'read_grants': 'unverified',
               'operator_grants': 'not-probed', 'observations': {}, 'receipts': [], 'gaps': []}
        if disposition is not None:
            if disposition not in ('external-shared', 'planned-owned', 'retained-foundation'):
                raise StateError('Service disposition must come from the admitted inventory')
            row.update(disposition=disposition, declaration_authority='operator-selected',
                       ownership='unverified', bootstrap='not-performed', cleanup='not-performed')
            if disposition != 'external-shared':
                row['observation_status'] = 'deferred'
                row['credential_sent'] = 'not-sent'
                row['gaps'].append('planned-owned-service-bootstrap-unimplemented' if disposition == 'planned-owned'
                                   else 'retained-foundation-readiness-unobserved')
                return row
        if name not in {'gitlab', 'kubernetes', 'argocd', 'prometheus'}:
            row['gaps'].append('service-diagnostic-unimplemented')
            return row
        client, ca = None, None
        try:
            _window_checkpoint(deadline, expires_at)
            operator = service.get('operator_auth', {})
            read_refs = {value for key, value in auth.items() if key.endswith('_env') and key != 'username_env'}
            operator_refs = {value for key, value in operator.items() if key.endswith('_env') and key != 'username_env'}
            if read_refs & operator_refs:
                raise APIError('read-operator-identity-overlap')
            if auth['mode'] not in {'none', 'bearer'}:
                raise APIError('read-auth-unsupported')
            ids = service['scope'].get('project_ids' if name == 'gitlab' else 'namespaces', [])
            if name in {'gitlab', 'kubernetes'} and not 1 <= len(ids) <= MAX_SCOPE_ITEMS:
                raise APIError('exact-scope-missing-or-oversized')
            if service.get('ca_file'):
                ca = output / (name + '-ca.pem')
                row['ca'] = _snapshot(Path(service['ca_file']), ca, 1_000_000, deadline=deadline)
            remaining = deadline - time.monotonic()
            client = HTTPSReadClient(service['origin'], token_env=auth.get('token_env'), ca_bundle=ca,
                timeout=min(5, remaining), total_timeout=min(20, remaining), max_requests=MAX_SCOPE_ITEMS+2,
                max_body_bytes=128_000, max_total_bytes=1_280_000)
            observations = row['observations']
            _window_checkpoint(deadline, expires_at)
            if name == 'gitlab':
                observations['projects'] = []
                for identifier in ids:
                    _window_checkpoint(deadline, expires_at)
                    project = client.get_json(f'/api/v4/projects/{identifier}')
                    if type(project.get('id')) is not int or project['id'] != identifier:
                        raise APIError('project-scope-mismatch')
                    observations['projects'].append({'id': identifier, 'read_observed': True})
            elif name == 'kubernetes':
                version = _version(client.get_json('/version').get('gitVersion'))
                observations['server_version'] = version
                if not _matches_version(version, lock['platform']['kubernetes_version']):
                    row['gaps'].append('locked-version-mismatch')
                _window_checkpoint(deadline, expires_at)
                ready = client.get_bytes('/readyz', accept=('text/plain',))
                observations['readyz_ok'] = ready.body.strip() == b'ok'
                if not observations['readyz_ok']:
                    row['gaps'].append('api-readiness-not-confirmed')
                observations['namespaces'] = []
                for namespace in ids:
                    _window_checkpoint(deadline, expires_at)
                    value = client.get_json('/api/v1/namespaces/' + namespace)
                    metadata = value.get('metadata', {})
                    uid = metadata.get('uid') if isinstance(metadata, dict) else None
                    if (value.get('kind') != 'Namespace' or value.get('apiVersion') != 'v1'
                            or metadata.get('name') != namespace or not isinstance(uid, str)
                            or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', uid)):
                        raise APIError('namespace-scope-mismatch')
                    observations['namespaces'].append({'name': namespace, 'uid': uid,
                                                       'ownership': 'unverified'})
            elif name == 'argocd':
                version = _version(client.get_json('/api/version').get('Version'))
                observations['server_version'] = version
                if not _matches_version(version, lock['platform']['argo_version']):
                    row['gaps'].append('locked-version-mismatch')
                row['gaps'].append('application-access-and-revision-unverified')
            else:
                value = client.get_json('/api/v1/status/buildinfo')
                if value.get('status') != 'success' or not isinstance(value.get('data'), dict):
                    raise APIError('invalid-build-information')
                observations['server_version'] = _version(value['data'].get('version'))
                row['gaps'].append('target-and-fresh-samples-unverified')
            _window_checkpoint(deadline, expires_at)
            row['observation_status'] = 'complete'
        except APIError as error:
            row['gaps'].append('service-' + error.code)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            row['gaps'].append('service-input-or-observation-invalid')
        if client is not None:
            row['receipts'] = client.receipts
            # A received status implies the verified HTTPS connection reached
            # the selected destination. It does not authenticate the caller.
            responded = any(type(item.get('status')) is int for item in row['receipts'])
            row['tls_destination_verified'] = responded
            row['credential_sent'] = ('none-selected' if auth['mode'] == 'none'
                                      else 'yes' if responded else 'not-confirmed')
        if ca is not None and ca.exists() and _sha(_read(ca, 1_000_000)) != row.get('ca', {}).get('sha256'):
            raise StateError('Frozen service CA changed during diagnostics')
        return row

    def execute(self, context):
        if (type(context) is not PhaseContext or type(context.deadline) not in (int, float)
                or not math.isfinite(context.deadline)):
            raise StateError('A bounded current phase is required')
        deadline = min(context.deadline, time.monotonic() + MAX_TIMEOUT)
        raw, docs = self._admit(context, deadline)
        phase = _plain(context.phase)
        if phase['operation'] == 'validate-correction':
            from . import correction_phase
            return correction_phase.execute(self, context, deadline)
        if phase['operation'] == 'export':
            self._package(self.journal.identities['package_sha256'], deadline)
            with evidence_export.export_checkpoint(self.journal, context, deadline=deadline) as artifacts:
                self._admit(context, deadline)
                self._package(self.journal.identities['package_sha256'], deadline)
                self._admit(context, deadline)
            return PhaseOutcome('completed', 'owned-linux-evidence-exported', artifacts,
                                assertions=(), mutation_effect='not_applied')
        production = self._production()
        if production is not None:
            self._package(self.journal.identities['package_sha256'], deadline)
            return production.execute(context)
        output = self.journal.run_dir / ('owned-linux-' + uuid4().hex)
        report = {'schema_version': 1, 'kind': 'owned-linux-phase-diagnostic', 'driver_id': DRIVER_ID,
            'run_id': context.run_id, 'phase_id': phase['id'], 'identities': dict(self.journal.identities),
            'execution_mode': context.execution_mode, 'execution_status': 'incomplete',
            'readiness': 'unknown', 'canonical_success': False, 'external_mutations_performed': False,
            'resource_ownership': 'unverified', 'created_resources': [], 'cleanup': 'not-performed',
            'started_at': datetime.now(timezone.utc).isoformat(), 'services': [], 'gaps': []}
        with _new_directory(output) as directory:
            package_checked = False
            if phase['operation'] != 'preflight':
                report['gaps'].append('phase-handler-unimplemented')
            else:
                try:
                    report['preflight_stage'] = 'package-validation'
                    report['package'] = self._package(self.journal.identities['package_sha256'], deadline)
                    package_checked = True
                    report['preflight_stage'] = 'profile-and-window'
                    config, lock = docs['config'], docs['lock']
                    if config['profile'] != 'bigbang-live':
                        raise StateError('Only the owned Big Bang live reference profile is supported')
                    remaining = (datetime.fromisoformat(config['expires_at'].replace('Z', '+00:00'))
                                 - datetime.now(timezone.utc)).total_seconds()
                    if remaining <= 0:
                        raise StateError('Operator diagnostic window has expired')
                    deadline = min(deadline, time.monotonic()+remaining)
                    dispositions = {}
                    if 'foundation' in config['runtime']:
                        report['preflight_stage'] = 'foundation-service-inventory'
                        report['service_inventory'] = service_inventory.classify_foundation_services(
                            docs['environment'], config)
                        dispositions = {row['id']: row['disposition'] for row in report['service_inventory']}
                    report['preflight_stage'] = 'local-input-checks'
                    for name in ('config', 'lock'):
                        _write(directory, name+'.json', raw[name].decode('utf-8'))
                    report['local_checks'] = inspect(output/'config.json', profile=config['profile'],
                        lock_path=output/'lock.json', deadline=deadline,
                        scenario_root=PACKAGE_ROOT/'demo/software-factory/scenarios/ironbank-mission-recovery-v1')
                    checkpoint(deadline)
                    services = {row['id']: row for row in config['services']}
                    report['preflight_stage'] = 'service-observations'
                    for name in sorted(REQUIRED_SERVICES['bigbang-live']):
                        _window_checkpoint(deadline, config['expires_at'])
                        if name not in services:
                            report['gaps'].append('required-service-not-configured:' + name)
                        else:
                            report['services'].append(self._service(services[name], lock, output, deadline,
                                config['expires_at'], disposition=dispositions.get(name)))
                    report['gaps'].extend(_MISSING)
                except (OSError, ValueError, TypeError, KeyError, AttributeError):
                    report['gaps'].append('preflight-input-package-or-budget-unavailable')
            self._admit(context, deadline)
            if package_checked:
                self._package(self.journal.identities['package_sha256'], deadline)
            _verify_output_path(output, directory)
            report['finished_at'] = datetime.now(timezone.utc).isoformat()
            terminal_identity = None
            try:
                self._admit(context, deadline)
                terminal_identity = _write_terminal(directory, 'diagnostic.json', report)
                os.fsync(directory)
                _verify_output_path(output, directory)
                self._admit(context, deadline)
                if package_checked:
                    self._package(self.journal.identities['package_sha256'], deadline)
                self._admit(context, deadline)
                checkpoint(deadline)
            except BaseException as error:
                if terminal_identity is not None:
                    _withdraw_terminal(directory, 'diagnostic.json', terminal_identity, error)
                raise
        return PhaseOutcome('incomplete', 'owned-linux-preflight-incomplete' if phase['operation'] == 'preflight'
                            else 'owned-linux-phase-unimplemented', (output/'diagnostic.json',), mutation_effect='not_applied')

    def native_inputs(self, context):
        if type(context) is not PhaseContext:
            raise StateError('Native preparation requires a concrete phase context')
        with _deadline(context.deadline):
            self._admit(context, context.deadline)
            self._package(self.journal.identities['package_sha256'], context.deadline)
            production = self._production()
            if production is None:
                raise StateError('Owned Linux native phase requires explicit production selections')
            selection = production._selection(context.phase['id'])
        return production.native.prepare(context, selection=selection)

    def native_scope(self, *, step_id):
        production = self._production()
        if production is None:
            raise StateError('Native scope requires the retained production coordinator')
        return production.native_scope(step_id=step_id)

    def bind_correction_scope(self, scope, *, request_sha256, origin):
        from . import correction_phase
        return correction_phase.bind(self, scope, request_sha256=request_sha256, origin=origin)

    def validate_native(self, request, result, artifacts):
        production = self._production()
        if production is None:
            raise StateError('Native validation requires the retained production coordinator')
        return production.validate_native(request, result, artifacts)

    def _resident_cleanup(self):
        from .owned_production import OwnedProductionCoordinator
        production = self._production_instance
        if type(production) is not OwnedProductionCoordinator or production.driver is not self:
            raise StateError('Cleanup reconciliation and continuation require the original resident producer')
        return production

    def reconcile_cleanup(self, context, operation_id):
        return self._resident_cleanup().reconcile_cleanup(context, operation_id)

    def validate_partial_cleanup(self, receipts):
        return self._resident_cleanup().validate_partial_cleanup(receipts)

    def prepare_cleanup_continuation(self, context, prior_operation_id, *, cleanup_mode="normal"):
        return self._resident_cleanup().prepare_cleanup_continuation(context, prior_operation_id, cleanup_mode=cleanup_mode)

    def continue_cleanup(self, context, prior_operation_id, *, cleanup_mode="normal"):
        return self._resident_cleanup().continue_cleanup(context, prior_operation_id, cleanup_mode=cleanup_mode)

    def validate_cleanup(self, receipts):
        production = self._production()
        if production is None:
            raise StateError('Cleanup requires the retained production coordinator')
        return production.validate_cleanup(receipts)
