"""Fixed resident composition for the canonical owned Linux phase graph.

JSON selects pinned data only. Concrete instances retain their direct origins
from provision through native calls, delivery, recovery and observed cleanup.
"""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from time import monotonic

from .lifecycle import _deadline
from .state import StateError, _json, _read

REQUIRED_SELECTIONS = frozenset({'owned_bootstrap_recipe', 'owned_operations_recipe', 'native_selection', 'owned_cases_recipe'})


class OwnedProductionCoordinator:
    def __init__(self, driver):
        from .owned_linux import OwnedLinuxDriver, PACKAGE_ROOT
        from .owned_cleanup import OwnedCleanupCoordinator
        from .canonical_bootstrap import OwnedBootstrapCoordinator
        from .runtime_observations import RuntimeObservationSession
        from .owned_operations import OwnedOperationCoordinator
        from .native_phase import NativePhaseCoordinator
        from .owned_cases import OwnedCanonicalCases
        from .replay_acceptance import OwnedReplayAcceptance
        if type(driver) is not OwnedLinuxDriver:
            raise StateError('Production composition requires its concrete owned driver')
        names = set(driver.auxiliary_sources or {})
        if not REQUIRED_SELECTIONS <= names:
            raise StateError('Production composition requires all explicit pinned selections')
        driver._auxiliary_artifacts()
        self.driver, self.journal, self.pid = driver, driver.journal, os.getpid()
        self.run_id, self.identities = self.journal.state['run_id'], dict(self.journal.identities)
        self.cleanup = OwnedCleanupCoordinator(driver)
        self.bootstrap = OwnedBootstrapCoordinator(driver, cleanup=self.cleanup)
        from .owned_fresh_foundation import OwnedFreshRKE2Foundation
        if type(getattr(self.bootstrap, 'foundation', None)) is OwnedFreshRKE2Foundation:
            self.bootstrap.foundation.enable_resident_transport()
        if self.bootstrap.cleanup_inventory_contract() != 'owned-runner-vm-v1':
            raise StateError('Canonical production requires the explicit dedicated runner VM profile before effects')
        self.runtime = RuntimeObservationSession(driver, bootstrap=self.bootstrap)
        self.operations = OwnedOperationCoordinator(self.journal, bootstrap=self.bootstrap,
            runtime=self.runtime, plugin_id=driver.plugin_id, plugin_version=driver.plugin_version)
        self.cases = OwnedCanonicalCases(self.journal, operations=self.operations, runtime=self.runtime, driver=driver)
        selected = self.journal.state['lifecycle']['auxiliary_inputs']['native_selection']
        doc = _json(_read(self.journal._artifact_path(selected['path']), 64000))
        self.native = NativePhaseCoordinator(self.journal, installed_root=PACKAGE_ROOT,
                                            engine_python=Path(doc['engine_python']))
        self.replay_acceptance = OwnedReplayAcceptance(self)

    def _current(self):
        if (self.pid != os.getpid() or self.driver.journal is not self.journal
                or self.journal.state['run_id'] != self.run_id or self.journal.identities != self.identities):
            raise StateError('Production coordinator belongs to another resident run')
        self.driver._auxiliary_artifacts()

    def execute(self, context):
        self._current()
        operation = context.phase['operation']
        if operation in ('preflight', 'prepare', 'up'):
            return self.bootstrap.execute(context)
        if operation in ('baseline', 'confirm-fault', 'verify'):
            return self.runtime.dispatch(context)
        if operation == 'case':
            return self.cases.execute(context)
        if operation == 'reset':
            self.replay_acceptance.begin_reset(context)
            return self.replay_acceptance.retain_reset(context, self.operations.dispatch(context))
        if operation in ('build-promote', 'inject', 'deliver', 'probe'):
            return self.operations.dispatch(context)
        if operation == 'down':
            return self.cleanup.execute(context)
        if operation == 'finalize':
            state = self.journal.state
            if (state['execution_status'] != 'released' or state['environment_reserved']
                    or state['active_mutation'] is not None or state['active_native'] is not None
                    or state['cleanup_validation'] is None):
                raise StateError('Finalization requires durable observed cleanup')
            from .owned_fresh_foundation import OwnedFreshRKE2Foundation
            transport = None
            if type(getattr(self.bootstrap, 'foundation', None)) is OwnedFreshRKE2Foundation:
                transport = self.bootstrap.foundation.close_resident_transport(context.deadline)
                if transport is not None:
                    self.bootstrap._retain_outcome(transport)
            outcome = self.replay_acceptance.finalize(context)
            return outcome if transport is None else replace(
                outcome, artifacts=tuple(dict.fromkeys((*outcome.artifacts, *transport.artifacts))))
        raise StateError('Canonical production operation has no fixed route')

    def _selection(self, step_id):
        self._current()
        parts = step_id.split('-')
        if len(parts) != 2 or parts[0] not in ('primary', 'replay'):
            raise StateError('Canonical native step identity is invalid')
        attempt, kind = parts
        if kind in ('review', 'correction'):
            return self.operations.source_selection(attempt)
        if kind == 'diagnose':
            return self.runtime.diagnosis_inputs(attempt)
        if kind == 'recovery':
            return self.runtime.recovery_inputs(attempt)
        raise StateError('Canonical native workflow has no fixed input producer')

    def native_inputs(self, context):
        with _deadline(context.deadline):
            selection = self._selection(context.phase['id'])
        return self.native.prepare(context, selection=selection)

    def native_scope(self, *, step_id):
        # scope owns its lifecycle lock; selectors are freshly checked first.
        deadline = monotonic() + 30
        with _deadline(deadline):
            selection = self._selection(step_id)
        return self.native.scope(step_id=step_id, selection=selection, deadline=deadline)

    def validate_native(self, request, result, artifacts):
        self._current()
        return self.native.validate_native(request, result, artifacts)

    def validate_cleanup(self, receipts):
        self._current()
        return self.cleanup.validate_cleanup(receipts)

    def reconcile_cleanup(self, context, operation_id):
        self._current()
        return self.cleanup.reconcile(context, operation_id)

    def validate_partial_cleanup(self, receipts):
        self._current()
        return self.cleanup.validate_partial_cleanup(receipts)

    def prepare_cleanup_continuation(self, context, prior_operation_id, *, cleanup_mode="normal"):
        self._current()
        return self.cleanup.prepare_continuation(context, prior_operation_id, cleanup_mode=cleanup_mode)

    def continue_cleanup(self, context, prior_operation_id, *, cleanup_mode="normal"):
        self._current()
        return self.cleanup.continue_cleanup(context, prior_operation_id, cleanup_mode=cleanup_mode)
