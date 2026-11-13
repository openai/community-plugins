"""Resident composition for all canonical native handoffs.

The existing report and correction coordinators perform the actual invocation
and independent validation. This session retains their direct objects while
the lifecycle advances; saved reports cannot recreate those objects. Host
scope preparation and operational backend handlers remain separate concerns.
"""
from __future__ import annotations

from .native_reports import NativeReportCoordinator
from .native_session import NativeCorrectionSession, NativeCodexSelection
from .state import MAX_ARTIFACT_BYTES, MAX_ARTIFACT_TOTAL_BYTES, MAX_DOCUMENT_BYTES, StateError, _read


_REPORT_STEPS = {
    f'{attempt}-{suffix}': (workflow, kind)
    for attempt in ('primary', 'replay')
    for suffix, workflow, kind in (
        ('review', 'review-kubernetes-change', 'review-report'),
        ('diagnose', 'diagnose-gitops-deployment', 'diagnosis'),
        ('recovery', 'verify-gitops-recovery', 'recovery-report'),
    )
}


class NativeFactorySession(NativeCorrectionSession):
    """Service one pending native step and retain every accepted direct origin.

    ``execute_pending`` never advances delivery or another lifecycle phase.
    Call ``advance`` explicitly after acceptance. Report verdicts remain domain
    observations, not deployment authority or scenario assertions. Failure and
    cancellation stop continuation; explicit ``interrupt`` retains the existing
    cleanup path without pretending that native validation proves cleanup.
    """

    def __init__(self, engine, *, selection):
        if type(selection) is not NativeCodexSelection or selection.max_trace_bytes > MAX_ARTIFACT_BYTES:
            raise StateError('Canonical native trace exceeds the journal artifact byte bound')
        super().__init__(engine, selection=selection)
        self._report_coordinator = NativeReportCoordinator(engine, selection)

    @property
    def cancelled(self):
        """Read-only cancellation state for the fixed operation dispatcher."""
        self._bound()
        return self._cancel.is_set()

    def selection_for_readonly_case(self, context):
        """Expose immutable selection only for the current independent D5 read.

        This neither creates a native request nor advances the main lifecycle;
        a case executor must retain its own evidence with no runtime authority.
        """
        from .lifecycle import PhaseContext
        from .owned_linux import _plain
        state = self._available()
        self._origins(state)
        life = state['lifecycle']
        if (type(context) is not PhaseContext or not self._journal._lifecycle_locked
                or not state['environment_reserved'] or state['active_mutation'] is not None
                or state['active_native'] is not None or life['abort'] is not None
                or (life['active_phase'] or {}).get('phase_id') != 'evidence-gaps'
                or context.phase.get('id') != 'evidence-gaps' or context.phase.get('operation') != 'case'
                or _plain(context.phase) not in life['phases']
                or context.run_id != self._run_id or context.run_dir != self._journal.run_dir
                or dict(context.identities) != self._identities
                or context.execution_mode != self._mode or context.remaining_seconds() <= 0
                or _plain(context.inputs) != self._engine.docs
                or _plain(context.input_artifacts) != life['inputs']):
            raise StateError('Native selection requires the exact current read-only evidence-gap case')
        return self._selection

    def selection_for_interrupted_review(self, context):
        """Original selection for the separate D6 read during the current reset.

        Reset's existing mutation remains owned by its caller. This grants no
        Git effect, main native progress, runtime origin or restored authority.
        """
        from .lifecycle import PhaseContext
        from .owned_linux import _plain
        state = self._available()
        self._origins(state)
        life = state['lifecycle']
        if (type(context) is not PhaseContext or not self._journal._lifecycle_locked
                or not state['environment_reserved'] or state['active_native'] is not None
                or life['abort'] is not None
                or (life['active_phase'] or {}).get('phase_id') != 'reset'
                or context.phase.get('id') != 'reset' or context.phase.get('operation') != 'reset'
                or _plain(context.phase) not in life['phases']
                or context.run_id != self._run_id or context.run_dir != self._journal.run_dir
                or dict(context.identities) != self._identities
                or context.execution_mode != self._mode or context.remaining_seconds() <= 0
                or _plain(context.inputs) != self._engine.docs
                or _plain(context.input_artifacts) != life['inputs']):
            raise StateError('Interrupted review requires the original current reset context')
        self._journal.current_mutation('reset', 'reset')
        return self._selection

    def completed_step_artifacts(self, step_id):
        """Bound original evidence for one of the eight accepted main steps."""
        from .native_reports import native_report_artifacts
        from .native_provenance import native_execution_artifacts
        state = self._bound()
        origins = self._origins(state)
        accepted = [row for row in state['native_results'] if row['step_id'] == step_id]
        if len(accepted) != 1 or accepted[0]['status'] != 'completed':
            raise StateError('Exactly one completed canonical native step is required')
        if step_id in _REPORT_STEPS:
            paths = native_report_artifacts(self._reports(state)[step_id])
        elif step_id in ('primary-correction', 'replay-correction'):
            paths = native_execution_artifacts(origins[step_id.removesuffix('-correction')])
        else:
            raise StateError('Only eight canonical primary/replay native steps are supported')
        if any(not path.is_relative_to(self._journal.run_dir) for path in paths):
            raise StateError('Native evidence belongs outside the current run')
        if sum(len(_read(path, MAX_ARTIFACT_BYTES)) for path in paths) > MAX_ARTIFACT_TOTAL_BYTES:
            raise StateError('Canonical native evidence exceeds the phase artifact byte bound')
        return paths

    def terminal_native_identity(self):
        """Publish selected identity only alongside retained native receipts.

        An absent provider-observed model is unknown, never inferred from the
        requested model. This does not restore or issue execution authority.
        """
        state = self._bound()
        self._origins(state)
        if not state['native_results']:
            raise StateError('No accepted native receipt is available for terminal identity')
        selected = self._selection
        return {'client_path': str(selected.executable),
                'client_sha256': selected.expected_client_sha256,
                'client_version': selected.expected_client_version, 'model': selected.model,
                'reasoning_effort': selected.reasoning_effort, 'provider_observed_model': None,
                'identity_basis': 'explicit-selection-and-native-receipts'}

    def _reports(self, state):
        coordinator = self._report_coordinator
        if coordinator.engine is not self._engine or coordinator.selection is not self._selection:
            raise StateError('Resident report coordinator identity changed')
        reports = {}
        for accepted in state['native_results']:
            step = accepted['step_id']
            if step not in _REPORT_STEPS:
                continue
            request = next((row for row in state['native_requests']
                            if row['request_id'] == accepted['request_id'] and row['step_id'] == step), None)
            if request is None or accepted['status'] != 'completed':
                raise StateError('A completed resident report is required')
            value = coordinator.report(step_id=step, request_sha256=request['request']['sha256'])
            document = value.document
            workflow, kind = _REPORT_STEPS[step]
            artifacts = [row for row in accepted['artifacts'] if row['kind'] == kind]
            if (document['run_id'] != self._run_id or document['step_id'] != step
                    or document['workflow'] != workflow or document['identities'] != self._identities
                    or document['execution_mode'] != ('controlled-test' if self._mode == 'controlled-test' else 'direct')
                    or document['runtime_authority'] != 'not-issued' or len(artifacts) != 1):
                raise StateError('Resident report differs from the accepted canonical step')
            bindings = {'request_sha256': self._reference(request['request'], MAX_DOCUMENT_BYTES),
                        'result_sha256': self._reference(accepted['result'], MAX_DOCUMENT_BYTES),
                        'validation_sha256': self._reference(accepted['validation'], MAX_DOCUMENT_BYTES),
                        'report_sha256': artifacts[0]['sha256']}
            if any(document[name] != digest for name, digest in bindings.items()):
                raise StateError('Resident report does not bind the accepted evidence bytes')
            # coordinator.report() audits every retained report/companion and
            # independent replay artifact against its original bytes.
            reports[step] = value
        return reports

    def _origins(self, state):
        origins = super()._origins(state)
        self._reports(state)
        return origins

    def report(self, *, step_id):
        """Return the original accepted report in this process, never a reload."""
        if step_id not in _REPORT_STEPS:
            raise StateError('Only canonical primary/replay reports are supported')
        state = self._available()
        self._origins(state)
        reports = self._reports(state)
        if step_id not in reports:
            raise StateError('Direct report is unavailable; saved evidence cannot restore it')
        return reports[step_id]

    def execute_pending(self, scope):
        """Dispatch the exact current report or correction using its fixed path."""
        state = self._available()
        self._origins(state)
        active = state['active_native']
        if active is None:
            raise StateError('A pending canonical native step is required')
        if active['step_id'] in ('primary-correction', 'replay-correction'):
            origin = super().execute_pending(scope)
            # The fixed owned backend retains this direct scope/origin for its
            # following local validation phase. No generic callback is selected.
            from .owned_linux import OwnedLinuxDriver
            if type(self._driver) is OwnedLinuxDriver:
                with self._operation():
                    self._origins(self._available())
                    self._driver.bind_correction_scope(scope,
                        request_sha256=active['request']['sha256'], origin=origin)
                    self._origins(self._available())
            return origin
        if (active['step_id'] not in _REPORT_STEPS
                or active['workflow'] != _REPORT_STEPS[active['step_id']][0]):
            raise StateError('Only canonical report and correction steps are supported')
        with self._operation():
            value = self._report_coordinator.execute(scope, cancel=self._cancel)
            state = self._available()
            self._origins(state)
            if self._reports(state).get(active['step_id']) is not value:
                raise StateError('Resident report origin is unavailable')
            return value
