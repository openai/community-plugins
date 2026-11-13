"""Closed dispatch for the canonical D3/D4/D5 case phases."""
import os

from .cases_recipe import bind_cases_recipe
from .case_supply_chain import execute_supply_chain
from .lifecycle import PhaseContext, PhaseOutcome
from .state import RunJournal, StateError, _canonical, _exclusive


class OwnedCanonicalCases:
    def __init__(self, journal, *, operations, runtime, driver):
        from .owned_operations import OwnedOperationCoordinator
        from .owned_linux import OwnedLinuxDriver
        if (type(journal) is not RunJournal or type(operations) is not OwnedOperationCoordinator
                or operations.journal is not journal or type(driver) is not OwnedLinuxDriver
                or driver.journal is not journal):
            raise StateError('Canonical cases require the direct same-run operation coordinator')
        self.journal, self.operations = journal, operations
        self.runtime, self.driver = runtime, driver
        self._pid = os.getpid()

    def __reduce__(self):
        raise StateError('Canonical case callers cannot be restored from saved evidence')

    def _admit(self, context):
        if (self._pid != os.getpid() or type(context) is not PhaseContext
                or context.phase['id'] not in ('platform-dependency', 'supply-chain-controls', 'evidence-gaps')
                or context.phase['operation'] != 'case'):
            raise StateError('Current canonical case context required')
        self.operations._admit(context)
        path, pin, raw = self.operations.selected_component('owned_cases_recipe')
        from .state import _read, _sha
        if _read(path, 64000) != raw or _sha(raw) != pin:
            raise StateError('Selected case recipe changed')
        return bind_cases_recipe(raw, identities=context.identities,
            environment_id=self.journal.state['environment_id'], plugin_id=self.operations.plugin_id,
            plugin_version=self.operations.plugin_version), path

    def mutation_intent_artifacts(self, context):
        _, path = self._admit(context)
        return (path,)

    def _incomplete(self, context, code):
        parent = context.run_dir / 'canonical-cases'
        parent.mkdir(mode=0o700, exist_ok=True)
        path = parent / (context.phase['id'] + '-incomplete.json')
        _exclusive(path, _canonical({'schema_version': 1, 'kind': 'canonical-case-incomplete',
            'run_id': context.run_id, 'identities': dict(context.identities), 'phase_id': context.phase['id'],
            'execution_mode': context.execution_mode, 'decision': 'unknown', 'gap': code,
            'live_effect': 'not-performed', 'authority': 'not-issued'}))
        return PhaseOutcome('incomplete', code, (path,), mutation_effect='not_applied')

    def execute(self, context):
        selected, _ = self._admit(context)
        if context.phase['id'] == 'supply-chain-controls':
            parent = context.run_dir / 'canonical-cases'
            parent.mkdir(mode=0o700, exist_ok=True)
            return execute_supply_chain(context, operations=self.operations, selection=selected, output=parent / 'D4')
        if context.phase['id'] == 'platform-dependency':
            from .case_platform import OwnedPlatformCase
            parent = context.run_dir / 'canonical-cases'
            parent.mkdir(mode=0o700, exist_ok=True)
            return OwnedPlatformCase(self, selected['platform_dependency'], parent / 'D3').execute(context)
        from .case_evidence import execute_evidence_gaps
        parent = context.run_dir / 'canonical-cases'
        parent.mkdir(mode=0o700, exist_ok=True)
        return execute_evidence_gaps(context, cases=self, selected=selected['evidence_gaps'], output=parent / 'D5')
