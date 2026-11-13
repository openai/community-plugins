"""Fixed canonical cleanup composition retaining the original child objects.

Saved observations are evidence only. This coordinator must survive from the
first creation intent through cleanup; loss of that process requires explicit
reconciliation and cannot manufacture a new cleanup grant from report JSON.
"""
from copy import deepcopy
import os
from pathlib import Path
import time
from uuid import uuid4

from devsecops.runtime.cli import _new_directory, _write
from .cleanup_observation import ComponentObservations
from .lifecycle import PhaseContext, PhaseOutcome, _load_inputs
from .state import StateError, _read, _sha
from .owned_linux import _plain


class OwnedCleanupCoordinator:
    def __init__(self, driver):
        from .owned_linux import OwnedLinuxDriver
        if type(driver) is not OwnedLinuxDriver:
            raise StateError('Cleanup needs the resident canonical driver')
        self.driver, self.journal, self.pid = driver, driver.journal, os.getpid()
        self.components, self.attempts, self.gitlab = {}, {}, None
        self._cleanup_operation, self._terminal = None, None
        self._inventory_contract = "owned-container-runner-v1"
        self._disposal_basis = None
        self._disposal_operation = None
        self._disposal_request = None
        self._continuation_of, self._history = None, []
        self._observations = ComponentObservations()
        self._events = []
        self._deferred, self._sealed, self._terminal_context = set(), None, None
        birth = self.journal.state
        self._no_effects_eligible = not birth.get('operations') and birth.get('active_mutation') is None

    def _admit(self, context, action, operation_id=None):
        if (self.pid != os.getpid() or type(context) is not PhaseContext
                or time.monotonic() >= context.deadline or action not in ('up', 'down')):
            raise StateError('Cleanup needs the original bounded lifecycle context')
        if operation_id is None:
            self.driver._admit(context, context.deadline)
        else:
            # The normal driver correctly rejects uncertain operations. This
            # read-only admission performs the same input/run/phase checks and
            # uses the journal's explicit reconciliation gate instead.
            state = self.journal.state
            life = state.get('lifecycle', {})
            if (not self.journal._lifecycle_locked or context.run_id != state['run_id']
                    or context.run_dir != self.journal.run_dir or _plain(context.identities) != self.journal.identities
                    or _plain(context.input_artifacts) != life.get('inputs')
                    or context.execution_mode != life.get('execution_mode') or life.get('driver_id') != 'owned-linux'
                    or (life.get('active_phase') or {}).get('phase_id') != context.phase['id']
                    or _plain(context.phase) not in life.get('phases', []) or life.get('final_result') is not None):
                raise StateError('Cleanup reconciliation differs from current journal')
            paths = {name: self.journal._artifact_path(ref['path']) for name, ref in life['inputs'].items()}
            _, docs = _load_inputs(paths, self.journal.identities, state['environment_id'])
            if docs != _plain(context.inputs):
                raise StateError('Cleanup reconciliation inputs changed')
            self.driver._auxiliary_artifacts()
        if context.phase['operation'] != action or 'cleanup' not in context.inputs['config']['allowed_mutations']:
            raise StateError('Cleanup disposition is not admitted')
        current = self.journal.current_mutation(context.phase['id'], action, reconciliation=operation_id is not None)
        if operation_id is not None and current != operation_id:
            raise StateError('Cleanup reconciliation operation changed')
        self.driver._package(self.journal.identities['package_sha256'], context.deadline)
        from . import owned_linux, owned_gitlab_cleanup, cleanup_observation, gitlab_api, gitlab_bootstrap
        root = owned_linux.PACKAGE_ROOT
        members = dict(line.rsplit('  ', 1)[::-1] for line in _read(root/'SHA256SUMS', 4_000_000).decode().splitlines())
        for module_path in (Path(__file__), *[Path(module.__file__) for module in
                (owned_gitlab_cleanup, cleanup_observation, gitlab_api, gitlab_bootstrap)]):
            target = root/'src/devsecops/factory'/module_path.name
            if module_path.resolve() != target or members.get(str(target.relative_to(root))) != _sha(_read(target, 2_000_000)):
                raise StateError('Cleanup implementation is outside the selected package')
        return current

    def _register(self, kind, component, expected):
        if self.pid != os.getpid() or type(component) is not expected or component.journal is not self.journal:
            raise StateError('Cleanup requires the exact journal-bound component')
        if (kind in self.components or self._cleanup_operation is not None
                or self._sealed is not None and kind not in self._deferred):
            raise StateError('Cleanup component registration cannot be replaced')
        self.components[kind] = component

    def register_foundation(self, component):
        from .owned_foundation import OwnedRKE2Foundation
        from .owned_fresh_foundation import OwnedFreshRKE2Foundation
        expected = type(component)
        if expected not in (OwnedRKE2Foundation, OwnedFreshRKE2Foundation):
            raise StateError('Cleanup requires an exact journal-bound foundation producer')
        self._register('foundation', component, expected)

    def register_registry(self, component):
        from .owned_registry import OwnedOCIRegistry
        self._register('registry', component, OwnedOCIRegistry)

    def register_services(self, component):
        from .owned_services import OwnedServiceStack
        self._register('services', component, OwnedServiceStack)

    def register_service_links(self, component):
        from .owned_service_links import OwnedServiceLinks
        self._register('service-links', component, OwnedServiceLinks)

    def register_runner_vm(self, component):
        from .owned_runner_vm import OwnedRunnerVM
        self._register('runner-vm', component, OwnedRunnerVM)

    def register_runner_routes(self, component):
        from .runner_vm_routes import OwnedRunnerVMRoutes
        self._register('runner-routes', component, OwnedRunnerVMRoutes)

    def register_runner_service(self, component):
        from .owned_runner_service import OwnedRunnerService
        self._register('runner-service', component, OwnedRunnerService)

    def _scopes(self):
        legacy = ('foundation', 'registry', 'services', 'service-links', 'kubernetes', 'writer', 'reader', 'gitlab')
        return legacy + (('runner-vm', 'runner-routes', 'runner-service')
                         if self._inventory_contract == 'owned-runner-vm-v1' else ())

    def _down_order(self):
        return ('kubernetes', 'writer', 'reader', 'gitlab', 'runner-service', 'runner-routes',
                'runner-vm', 'service-links', 'services', 'registry', 'foundation')

    def register_writer(self, component):
        from .owned_writer_identity import OwnedDeliveryWriter
        self._register('writer', component, OwnedDeliveryWriter)

    def register_reader(self, component):
        from .owned_reader_identity import OwnedGitLabReader
        self._register('reader', component, OwnedGitLabReader)

    def register_kubernetes(self, component):
        from .kubernetes_operations import OwnedKubernetesResources
        self._register('kubernetes', component, OwnedKubernetesResources)
        self._no_effects_eligible = False
        self.attempts['kubernetes'] = {'up_operation': None, 'up_outcome': None,
            'down_attempted': False, 'outcome': None, 'observation': None}

    def declare_deferred_scope(self, context, kind):
        self._admit(context, 'up')
        if kind not in ('kubernetes', 'gitlab', 'writer', 'reader') or self._sealed is not None or kind in self._deferred:
            raise StateError('Only fixed unattempted scopes can be declared before inventory closure')
        self._deferred.add(kind)

    def seal_inventory(self, context, *, inventory_contract="owned-container-runner-v1"):
        if inventory_contract not in ('owned-container-runner-v1', 'owned-runner-vm-v1'):
            raise StateError('Unknown original cleanup inventory contract')
        if self._sealed is not None:
            raise StateError('Cleanup inventory cannot be replaced')
        self._inventory_contract = inventory_contract
        operation = self._admit(context, 'up')
        if (self._sealed is not None or self.attempts
                or set(self.components) | self._deferred != set(self._scopes())):
            raise StateError('All fixed cleanup scopes must be declared before the first effect')
        self._event(context, 'inventory-sealed', {'operation_id': operation,
            'components': sorted(self.components), 'deferred': sorted(self._deferred),
            'inventory_contract': self._inventory_contract})
        self._sealed = operation

    def _event(self, context, kind, value):
        path = self.journal.run_dir / ('cleanup-event-'+uuid4().hex)
        with _new_directory(path) as directory:
            _write(directory, 'event.json', {'run_id': context.run_id, 'identities': dict(context.identities),
                'kind': kind, 'value': value})
            os.fsync(directory)
        self._events.append(path/'event.json')

    def _up(self, kind, context):
        operation = self._admit(context, 'up')
        if (kind not in self.components or kind in self.attempts or self._cleanup_operation is not None
                or self._sealed != operation):
            raise StateError('Component provisioning is missing, repeated or cleanup already began')
        self._no_effects_eligible = False
        self.attempts[kind] = {'up_operation': operation, 'up_outcome': None, 'down_attempted': False,
                               'outcome': None, 'observation': None}
        self._event(context, 'component-up-intent', {'component': kind, 'operation_id': operation})
        outcome = self.components[kind].up(context)
        self.attempts[kind]['up_outcome'] = outcome
        self.components[kind].cleanup_observation(outcome)
        return outcome

    def foundation_up(self, context):
        return self._up('foundation', context)

    def registry_up(self, context):
        return self._up('registry', context)

    def services_up(self, context):
        return self._up('services', context)

    def service_links_up(self, context):
        return self._up('service-links', context)

    def runner_vm_up(self, context):
        return self._up('runner-vm', context)

    def runner_routes_up(self, context):
        return self._up('runner-routes', context)

    def runner_service_up(self, context):
        return self._up('runner-service', context)

    def writer_up(self, context):
        return self._up('writer', context)

    def reader_up(self, context):
        return self._up('reader', context)

    def kubernetes_up(self, context):
        return self._up('kubernetes', context)

    def bind_gitlab(self, context, session, scope):
        from .owned_gitlab_cleanup import OwnedGitLabResources
        self._admit(context, 'up')
        if (self.gitlab is not None or self._cleanup_operation is not None
                or self._sealed is None or 'gitlab' not in self._deferred):
            raise StateError('GitLab cleanup ownership cannot be replaced')
        self._no_effects_eligible = False
        self.gitlab = OwnedGitLabResources(self, context, session, scope)
        return self.gitlab

    @staticmethod
    def _resolved(kind, observation):
        if kind == 'foundation':
            rows = observation.get('resources', [])
            if observation.get('kind') == 'owned-fresh-rke2-foundation':
                return (observation.get('complete_cleanup') is True
                    and observation.get('mutation_effect') == 'applied'
                    and observation.get('unresolved_operations') == []
                    and bool(rows)
                    and all(row.get('disposition') in ('remove', 'preserve') for row in rows)
                    and all(row.get('state') == ('absent' if row.get('disposition') == 'remove' else 'preserved')
                            for row in rows))
            return (observation.get('runtime_cleanup') == 'observed-absent'
                and all(row.get('state') == 'absent' for row in rows if row.get('disposition') == 'remove'))
        if kind == 'service-links':
            rows = observation.get('resources', [])
            return (observation.get('kind') == 'owned-service-links'
                and observation.get('complete_cleanup') is True
                and observation.get('mutation_effect') == 'applied'
                and observation.get('unresolved_operations') == [] and bool(rows)
                and all(row.get('disposition') in ('remove', 'preserve') for row in rows)
                and all(row.get('state') == ('absent' if row.get('disposition') == 'remove' else 'preserved')
                        for row in rows))
        if kind in ('runner-service', 'runner-routes', 'runner-vm'):
            rows = observation.get('resources', [])
            if kind == 'runner-vm' and observation.get('never_dispatched') is True:
                return (observation.get('kind') == 'owned-runner-vm'
                    and observation.get('complete_cleanup') is True
                    and observation.get('mutation_effect') == 'not_applied'
                    and observation.get('unresolved_operations') == [] and len(rows) == 1
                    and rows[0].get('kind') == 'original-runner-vm-inventory'
                    and rows[0].get('basis') == 'original-resident-no-dispatch'
                    and rows[0].get('disposition') == 'remove' and rows[0].get('state') == 'absent')
            return (observation.get('complete_cleanup') is True
                and observation.get('mutation_effect') == 'applied'
                and observation.get('unresolved_operations') == [] and bool(rows)
                and all(row.get('disposition') in ('remove', 'preserve') for row in rows)
                and all(row.get('state') == ('absent' if row['disposition'] == 'remove' else 'preserved')
                        for row in rows))
        if kind == 'registry':
            actual = observation.get('observation', {})
            return (actual.get('inflight') is None and actual.get('cleanup') in
                    ('owned-runtime-absent-private-material-retained', 'owned-runtime-and-private-material-absent'))
        return (observation.get('complete_cleanup') is True and observation.get('mutation_effect') == 'applied'
            and observation.get('unresolved_operations') == []
            and all(row.get('state') in ('absent', 'preserved') for row in observation.get('resources', [])))

    def _identity(self, context, kind, observation, operation):
        if (observation.get('run_id') != context.run_id or observation.get('operation_id') != operation
                or observation.get('identities') != dict(context.identities)
                or observation.get('action') not in ('down', 'reconcile')):
            raise StateError('Cleanup child observation differs from current operation')

    def execute(self, context):
        operation = self._admit(context, 'down')
        if self._cleanup_operation is not None:
            raise StateError('Cleanup was already attempted; reconcile without repeating actions')
        self._cleanup_operation = operation
        return self._execute_remaining(context, operation)

    def _audit_children(self):
        for kind, attempt in self.attempts.items():
            if attempt['outcome'] is not None:
                observed = _plain(self.components[kind].cleanup_observation(attempt['outcome']))
                if observed != attempt['observation']:
                    raise StateError('Original child cleanup observation changed')

    def _continuable_child(self, kind, observation, operation):
        # Only these concrete producers implement terminal partial continuation.
        # Unknown effects and arbitrary callable hooks are refused.
        from .owned_service_links import OwnedServiceLinks
        from .kubernetes_operations import OwnedKubernetesResources
        expected = {'service-links': OwnedServiceLinks, 'kubernetes': OwnedKubernetesResources}
        return (kind in expected and type(self.components.get(kind)) is expected[kind]
            and observation is not None and observation.get('action') == 'reconcile'
            and observation.get('operation_id') == operation
            and observation.get('cleanup_eligible') is True
            and observation.get('unresolved_operations') == []
            and observation.get('mutation_effect') in ('applied', 'not_applied'))

    def _terminal_partial(self, operation):
        if self._sealed is None:
            return False
        dispatched = False
        for kind, attempt in self.attempts.items():
            if not attempt['down_attempted']:
                continue
            dispatched = True
            value = attempt['observation']
            if value is None or not (self._resolved(kind, value)
                    or self._continuable_child(kind, value, operation)):
                return False
        if self.gitlab is not None and self.gitlab._down_operation is not None:
            dispatched = True
            if not self._resolved('gitlab', self.gitlab.observation(self.gitlab._down_operation)):
                return False
        # A service PAT created before API handoff is not a terminal known scope.
        if 'services' in self.attempts and (self.gitlab is None or self.gitlab.token is None
                or self.gitlab.token['state'] != 'absent'):
            return False
        return dispatched

    def _continuation_basis(self, prior_operation_id, *, cleanup_mode="normal"):
        if self.pid != os.getpid() or self._terminal is None or self._cleanup_operation != prior_operation_id:
            raise StateError('Cleanup continuation requires its original resident predecessor')
        report = self._observations.observe(self._terminal)
        self._audit_children()
        if (report['operation_id'] != prior_operation_id or report['reconciliation'] is not True
                or report['complete_cleanup'] or report.get('continuation_eligible') is not True
                or (not self._contained_partial(prior_operation_id) if cleanup_mode == 'dispose-owned-runner-guest'
                    else not self._terminal_partial(prior_operation_id))
                or report.get('cleanup_continuation_modes', ['normal']) != [cleanup_mode]):
            raise StateError('Original cleanup effects have not reached terminal partial reconciliation')
        return report

    def prepare_continuation(self, context, prior_operation_id, *, cleanup_mode="normal"):
        self.driver._admit(context, context.deadline)
        if context.phase['operation'] != 'down':
            raise StateError('Cleanup continuation requires the current cleanup phase')
        self._continuation_basis(prior_operation_id, cleanup_mode=cleanup_mode)
        state = self.journal.state
        prior = next((o for o in reversed(state['operations']) if o['action'] == 'down'), None)
        if (state['active_mutation'] is not None or prior is None
                or prior['operation_id'] != prior_operation_id or prior['status'] != 'partial'):
            raise StateError('Cleanup continuation requires the durably reconciled partial predecessor')
        return self._terminal.artifacts

    def continue_cleanup(self, context, prior_operation_id, *, cleanup_mode="normal"):
        operation = self._admit(context, 'down')
        self._continuation_basis(prior_operation_id, cleanup_mode=cleanup_mode)
        active = self.journal.state['active_mutation']
        if (operation == prior_operation_id or active is None
                or active.get('continues_operation') != prior_operation_id
                or active.get('cleanup_mode', 'normal') != cleanup_mode):
            raise StateError('Cleanup continuation requires a new explicitly chained down operation')
        self._history.append(self._terminal)
        self._continuation_of = prior_operation_id
        self._cleanup_operation = operation
        self._event(context, 'cleanup-continuation-intent', {'operation_id': operation,
            'prior_operation_id': prior_operation_id})
        if cleanup_mode == 'dispose-owned-runner-guest':
            return self._dispose_runner_guest(context, operation, prior_operation_id)
        return self._execute_remaining(context, operation)

    def _execute_remaining(self, context, operation):
        for kind in self._down_order():
            if kind == 'gitlab':
                if self.gitlab is None:
                    continue
                if self.gitlab._down_operation is not None:
                    if self._resolved(kind, self.gitlab.observation(self.gitlab._down_operation)):
                        continue
                    return self._receipt(context, operation, 'gitlab-operation-unresolved')
                self._event(context, 'gitlab-down-intent', {'operation_id': operation})
                try:
                    observation = self.gitlab.down(context)
                except Exception:
                    return self._receipt(context, operation, 'gitlab-operation-unresolved')
                self._identity(context, kind, observation, operation)
                if not self._resolved(kind, observation):
                    return self._receipt(context, operation, 'gitlab-operation-unresolved')
                continue
            if kind not in self.attempts:
                continue
            attempt = self.attempts[kind]
            if attempt['observation'] is not None and self._resolved(kind, attempt['observation']):
                continue
            self._admit(context, 'down')
            if kind == 'services' and (self.gitlab is None or self.gitlab.token is None
                    or self.gitlab.token['state'] != 'absent'):
                return self._receipt(context, operation, 'service-token-ownership-unreconciled')
            previous = attempt.get('down_operation')
            if attempt['down_attempted'] and not self._continuable_child(kind, attempt['observation'], previous):
                return self._receipt(context, operation, kind+'-operation-unresolved')
            attempt['down_attempted'] = True
            attempt['down_operation'] = operation
            self._event(context, 'component-down-intent', {'component': kind, 'operation_id': operation,
                'prior_operation_id': previous})
            attempt['outcome'] = attempt['observation'] = None
            try:
                outcome = (self.components[kind].continue_cleanup(context, previous) if previous is not None
                           else self.components[kind].down(context))
                attempt['outcome'] = outcome
                observation = _plain(self.components[kind].cleanup_observation(outcome))
                self._identity(context, kind, observation, operation)
                attempt['observation'] = observation
            except Exception:
                # The latest intent supersedes any old completed observation;
                # it cannot be used to erase an uncertain new effect.
                attempt['outcome'] = attempt['observation'] = None
                return self._receipt(context, operation, kind+'-operation-unresolved')
            if not self._resolved(kind, observation):
                return self._receipt(context, operation, kind+'-operation-unresolved')
        return self._receipt(context, operation)

    def reconcile(self, context, operation_id):
        self._admit(context, 'down', operation_id=operation_id)
        if operation_id != self._cleanup_operation:
            raise StateError('Cleanup reconciliation needs this resident operation')
        disposal = self._disposal_operation == operation_id
        for kind in self._down_order():
            if disposal and kind in ('runner-service', 'runner-routes'):
                # The original guest may already be gone. Only the retained
                # parent-disposal observer can close these dependent resources.
                continue
            if disposal and kind == 'runner-vm':
                self._reconcile_runner_disposal(context, operation_id)
                continue
            if kind == 'gitlab':
                if self.gitlab is not None and self.gitlab._down_operation == operation_id:
                    self.gitlab.reconcile(context, operation_id)
                continue
            attempt = self.attempts.get(kind)
            if not attempt or not attempt['down_attempted'] or attempt.get('down_operation') != operation_id:
                continue
            try:
                outcome = self.components[kind].reconcile(context, operation_id)
            except Exception:
                if kind not in ('runner-service', 'runner-routes'):
                    raise
                # A guest read failure is retained as unresolved. Only a
                # separate original parent/host observer can bound its scope.
                attempt['outcome'] = attempt['observation'] = None
                continue
            observation = _plain(self.components[kind].cleanup_observation(outcome))
            self._identity(context, kind, observation, operation_id)
            attempt.update(outcome=outcome, observation=observation)
        self._terminal_context = context
        self._prepare_contained_partial(context, operation_id)
        return self._receipt(context, operation_id, reconciliation=True)

    def _prepare_contained_partial(self, context, operation):
        if self._inventory_contract != 'owned-runner-vm-v1' or self.gitlab is None:
            return
        guest = [self.attempts.get(kind) for kind in ('runner-service', 'runner-routes')]
        if not any(row and row['down_attempted'] and row.get('down_operation') == operation
                   and (row['observation'] is None or not self._resolved(kind, row['observation']))
                   for kind, row in zip(('runner-service', 'runner-routes'), guest)):
            return
        # Every non-guest effect already dispatched must be terminal; this is
        # not a general permission to bypass API/host uncertainty.
        if not self._resolved('gitlab', self.gitlab.observation(self.gitlab._down_operation)):
            return
        for kind, row in self.attempts.items():
            if kind in ('runner-service', 'runner-routes', 'runner-vm') or not row['down_attempted']:
                continue
            if row['observation'] is None or not self._resolved(kind, row['observation']):
                return
        service, routes, vm = (self.components[k] for k in ('runner-service','runner-routes','runner-vm'))
        # Concrete registered classes and original child relationship required.
        from .owned_runner_service import OwnedRunnerService
        from .runner_vm_routes import OwnedRunnerVMRoutes
        from .owned_runner_vm import OwnedRunnerVM
        if (type(service) is not OwnedRunnerService or type(routes) is not OwnedRunnerVMRoutes
                or type(vm) is not OwnedRunnerVM or service.vm is not vm or routes.vm is not vm):
            raise StateError('Contained residual requires original fixed runner components')
        try:
            revoked = service.revocation_proof(context, reconciliation_operation=operation)
            route_outcome = routes.prepare_disposal(context, operation)
            route_report = _plain(routes.cleanup_observation(route_outcome))
            if route_report.get('host_disposal_eligible') is not True:
                return
            basis = vm.prepare_disposal(context, prior_operation_id=operation, revocation=revoked)
            self._disposal_basis = {'operation':operation,'vm':basis,'routes':route_outcome,
                                    'revocation':revoked}
            self._contained_partial(operation)
        except Exception:
            self._disposal_basis = None

    def _contained_partial(self, operation):
        selected = self._disposal_basis
        if selected is None or selected['operation'] != operation:
            return False
        routes, vm = self.components['runner-routes'], self.components['runner-vm']
        report = _plain(routes.cleanup_observation(selected['routes']))
        parent = vm.disposal_observation(selected['vm'], self._terminal_context)
        return (report.get('host_disposal_eligible') is True
                and parent.get('parent_disposal_eligible') is True
                and parent.get('runner_vm') == report.get('runner_vm'))

    def _dispose_runner_guest(self, context, operation, prior):
        selected = self._disposal_basis
        if selected is None or selected['operation'] != prior:
            raise StateError('Original bounded guest residual proof required')
        routes, vm, service = (self.components[k] for k in ('runner-routes','runner-vm','runner-service'))
        self._event(context,'explicit-runner-parent-disposal-intent',
            {'operation_id':operation,'prior_operation_id':prior,'cleanup_mode':'dispose-owned-runner-guest'})
        # All attempted new effects are journaled before invocation. Their
        # original unknown outcomes remain in history, never overwritten as success.
        self.attempts['runner-routes'].update(down_attempted=True,down_operation=operation,
                                             outcome=None,observation=None)
        host = routes.prepare_host_disposal(context, prior)
        self.attempts['runner-routes'].update(outcome=host,observation=_plain(routes.cleanup_observation(host)))
        if not routes.host_cleanup_ready(context):
            return self._receipt(context,operation,'runner-host-relay-removal-unverified')
        self.attempts['runner-vm'].update(down_attempted=True,down_operation=operation,
                                        outcome=None,observation=None)
        self._disposal_operation = operation
        self._disposal_request = {'operation': operation, 'prior': prior,
            'basis': selected['vm'], 'revocation': selected['revocation']}
        outcome, proof = vm.dispose_guest(context,revocation=selected['revocation'],basis=selected['vm'])
        report = _plain(vm.cleanup_observation(outcome))
        self._identity(context,'runner-vm',report,operation)
        self.attempts['runner-vm'].update(outcome=outcome,observation=report)
        if not self._resolved('runner-vm',report):
            return self._receipt(context,operation,'runner-parent-disposal-unverified')
        self._accept_runner_cessation(context, operation, proof)
        return self._execute_remaining(context,operation)

    def _accept_runner_cessation(self, context, operation, proof, *, reconciliation=False):
        for kind in ('runner-service', 'runner-routes'):
            component = self.components[kind]
            kwargs = {'reconciliation_operation': operation} if reconciliation else {}
            outcome = component.accept_guest_cessation(proof, context, **kwargs)
            report = _plain(component.cleanup_observation(outcome))
            self._identity(context, kind, report, operation)
            self.attempts[kind].update(down_attempted=True, down_operation=operation,
                                       outcome=outcome, observation=report)
            if not self._resolved(kind, report):
                raise StateError('Original parent cessation did not close '+kind)

    def _reconcile_runner_disposal(self, context, operation):
        selected = self._disposal_request
        attempt = self.attempts.get('runner-vm')
        if (selected is None or self._disposal_operation != operation
                or selected['operation'] != operation or selected['prior'] != self._continuation_of
                or not attempt or not attempt['down_attempted']
                or attempt.get('down_operation') != operation):
            raise StateError('Original attempted parent disposal is required for reconciliation')
        vm = self.components['runner-vm']
        if not vm.disposal_was_dispatched(context, operation_id=operation,
                revocation=selected['revocation'], basis=selected['basis']):
            # This is an affirmative original resident ledger result, never
            # inferred from a missing receipt. The separate host-only observer
            # must issue a successor restricted basis before any new operation.
            return
        outcome, proof = vm.reconcile_disposal(context, operation_id=operation,
            revocation=selected['revocation'], basis=selected['basis'])
        report = _plain(vm.cleanup_observation(outcome))
        self._identity(context, 'runner-vm', report, operation)
        attempt.update(outcome=outcome, observation=report)
        if not self._resolved('runner-vm', report):
            raise StateError('Original parent disposal remains unverified')
        self._accept_runner_cessation(context, operation, proof, reconciliation=True)

    def validate_partial_cleanup(self, receipts):
        contained = self._contained_partial(self._cleanup_operation)
        report = self._continuation_basis(self._cleanup_operation,
            cleanup_mode='dispose-owned-runner-guest' if contained else 'normal')
        self._admit(self._terminal_context, 'down', operation_id=self._cleanup_operation)
        expected = tuple(_read(path, 2_000_000) for path in self._terminal.artifacts)
        if type(receipts) is not tuple or receipts != expected or report['identities'] != self.journal.identities:
            raise StateError('Partial cleanup requires exact original terminal observation bytes')
        if contained:
            return ['owned-guest-contained-residual-parent-disposal-only',
                    'original-runner-parent-ownership-observed', 'original-runner-registration-revoked-observed']
        return ['cleanup-dispatched-effects-terminal', 'cleanup-remaining-scopes-retained',
                'cleanup-continuation-requires-new-operation']

    def _receipt(self, context, operation, blocker=None, reconciliation=False):
        observations = {kind: deepcopy(row['observation']) for kind, row in self.attempts.items()}
        if self.gitlab is not None:
            observations['gitlab'] = self.gitlab.observation(self.gitlab._down_operation or operation)
        gaps = [blocker] if blocker else []
        no_effects = self._no_effects_eligible and not self.attempts and self.gitlab is None
        if self._sealed is None and not no_effects:
            gaps.append('complete-inventory-never-sealed')
        unattempted = []
        for kind in self._scopes():
            value = observations.get(kind)
            attempted = kind in self.attempts if kind != 'gitlab' else self.gitlab is not None
            if not attempted and (self._sealed is not None or no_effects):
                unattempted.append(kind)
            elif value is None or not self._resolved(kind, value):
                gaps.append(kind+'-cleanup-unverified')
        registry = (observations.get('registry') or {}).get('observation', {})
        if 'registry' in self.attempts and registry.get('cleanup') != 'owned-runtime-and-private-material-absent':
            gaps.append('registry-private-material-removal-unverified')
        # Both paths preserve selected source inputs. Fresh guest disks are
        # disposable; retained guest disks belong to the preserved baseline.
        foundation = observations.get('foundation') or {}
        protected = [row for row in foundation.get('resources', []) if row.get('disposition') == 'preserve']
        if 'foundation' in self.attempts and (not protected or any(row.get('state') != 'preserved' for row in protected)):
            gaps.append('foundation-source-protection-unverified' if foundation.get('kind') == 'owned-fresh-rke2-foundation'
                        else 'retained-foundation-protection-unverified')
        complete = not gaps
        contained = reconciliation and not complete and self._contained_partial(operation)
        report = {'schema_version': 1, 'kind': 'canonical-owned-cleanup', 'run_id': context.run_id,
            'operation_id': operation, 'identities': dict(context.identities), 'action': 'down',
            'complete_cleanup': complete, 'mutation_effect': 'applied' if complete else 'unknown',
            'components': observations, 'never_dispatched_scopes': unattempted,
            'resident_no_effects_closure': no_effects,
            'gaps': sorted(set(gaps)), 'reconciliation': reconciliation,
            'continuation_of': self._continuation_of,
            'continuation_eligible': reconciliation and not complete and (self._terminal_partial(operation) or contained),
            'cleanup_continuation_modes': ['dispose-owned-runner-guest'] if contained else ['normal'],
            'contained_guest_residuals': sorted(k for k in ('runner-service','runner-routes')
                if contained and (observations.get(k) is None or not self._resolved(k,observations[k]))),
            'inventory_contract': self._inventory_contract,
            'coverage': 'original-process-fixed-component-observations'}
        self._admit(context, 'down', operation_id=operation if reconciliation else None)
        output = self.journal.run_dir/('owned-cleanup-'+uuid4().hex)
        with _new_directory(output) as directory:
            _write(directory, 'cleanup.json', report)
            os.fsync(directory)
        paths = [output/'cleanup.json', *self._events]
        for previous in self._history:
            paths.extend(previous.artifacts)
        for attempt in self.attempts.values():
            if attempt['outcome'] is not None:
                paths.extend(attempt['outcome'].artifacts)
        if self.gitlab is not None:
            paths.extend(self.gitlab.events)
        if self._disposal_basis is not None:
            paths.extend(self._disposal_basis['routes'].artifacts)
            paths.extend(self.components['runner-vm'].disposal_artifacts(self._disposal_basis['vm']))
        paths = tuple(dict.fromkeys(paths))
        if len(paths) > 255 or sum(path.stat().st_size for path in paths) > 64_000_000:
            raise StateError('Complete original cleanup evidence exceeds its fixed publication bound')
        outcome = PhaseOutcome('completed' if complete else 'incomplete',
            'owned-cleanup-complete' if complete else 'owned-cleanup-unresolved',
            paths, mutation_effect=report['mutation_effect'])
        self._terminal = self._observations.retain(outcome, report)
        self._terminal_context = context
        return outcome

    def validate_cleanup(self, receipts):
        if self._terminal is None or self.pid != os.getpid():
            raise StateError('No resident cleanup observation is available')
        report = self._observations.observe(self._terminal)
        self._admit(self._terminal_context, 'down',
                    operation_id=self._cleanup_operation if report['reconciliation'] else None)
        for kind, attempt in self.attempts.items():
            if attempt['outcome'] is not None:
                observed = _plain(self.components[kind].cleanup_observation(attempt['outcome']))
                if observed != attempt['observation']:
                    raise StateError('Original child cleanup observation changed')
        expected = tuple(_read(path, 2_000_000) for path in self._terminal.artifacts)
        if (type(receipts) is not tuple or receipts != expected or report['complete_cleanup'] is not True
                or report['gaps'] or report['operation_id'] != self._cleanup_operation
                or report['identities'] != self.journal.identities):
            raise StateError('Complete original cleanup observation is required before release')
        return ['owned-resource-inventory-closed', 'owned-resource-absence-observed',
                'retained-resources-protected', 'cleanup-current-operation-bound']
