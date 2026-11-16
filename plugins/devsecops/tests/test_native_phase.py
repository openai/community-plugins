"""Canonical input preparation with synthetic runtime data and controlled phases.

Uses real scopes, frozen bytes and lifecycle locks; does not create live origin.
"""
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
import pickle

import pytest

from test_native_factory_session import pending, correction_inputs, correction_case, installed, recovery_scope
from test_factory_lifecycle import FixtureDriver, finish_native
from devsecops.engine import _schema_receipt
from devsecops.factory import lifecycle, native_reports
from devsecops.factory.native_phase import (NativePhaseCoordinator, NativeSourceSelection,
    NativeDiagnosisSelection, NativeRecoverySelection)
from devsecops.factory.state import RunJournal, StateError, _sha

__all__ = ['pending', 'correction_inputs', 'correction_case', 'installed', 'recovery_scope']


def test_canonical_input_caller_prepares_all_four_types_and_refuses_lost_scope(pending, installed, tmp_path, monkeypatch):
    _, template, _, scopes, args = pending
    source = NativeSourceSelection(repository=args.repo, base_commit=args.base, fault_commit=args.base,
        profile=args.profile, profile_sha256=_sha(args.profile.read_bytes()), correction_contract=args.contract,
        correction_contract_sha256=_sha(args.contract.read_bytes()),
        tools={'kubeconform': (Path(args.kubeconform), _sha(Path(args.kubeconform).read_bytes()))},
        schemas=args.schema_location, schemas_sha256=_schema_receipt(args.schema_location)['sha256'])
    diagnosis = native_reports._scope(scopes['diagnose-gitops-deployment'])['paths']
    recovery = native_reports._scope(scopes['verify-gitops-recovery'])['paths']
    diagnosed = NativeDiagnosisSelection(diagnosis['baseline'], _sha(diagnosis['baseline'].read_bytes()),
                                        diagnosis['fault'], _sha(diagnosis['fault'].read_bytes()))
    recovered = NativeRecoverySelection(recovery['baseline'], _sha(recovery['baseline'].read_bytes()),
        recovery['fault'], _sha(recovery['fault'].read_bytes()), recovery['recovered'],
        _sha(recovery['recovered'].read_bytes()), recovery['contract'], _sha(recovery['contract'].read_bytes()),
        {name: (recovery['evidence-'+str(index)], _sha(recovery['evidence-'+str(index)].read_bytes()))
         for index, name in enumerate(native_reports._scope(scopes['verify-gitops-recovery'])['options']['evidence_names'])})
    choices = {'review-kubernetes-change': source, 'prepare-gitops-correction': source,
               'diagnose-gitops-deployment': diagnosed, 'verify-gitops-recovery': recovered}
    journal = RunJournal.create(tmp_path/'prepared-journal', identities=template.journal.identities,
                                environment_id=template.journal.state['environment_id'])
    preparer = NativePhaseCoordinator(journal, installed_root=installed.installed_root,
                                     engine_python=installed.engine_python)
    class Driver(FixtureDriver):
        def native_inputs(self, context):
            self.calls.append(context.phase['id'])
            return preparer.prepare(context, selection=choices[context.phase['workflow']])
    driver = Driver()
    engine = lifecycle.LifecycleEngine(journal, inputs=template.input_sources,
                                       shared_lock_dir=tmp_path/'prepared-locks', _test_driver=driver)
    # The fixed owned backend invokes the preparer's single deadline directly.
    # This explicitly controlled driver omits only the outer fixture wrapper;
    # the real preparers still use their original deadline implementations.
    monkeypatch.setattr(lifecycle, '_deadline', lambda _: nullcontext())
    result = engine.advance()
    for index, suffix in enumerate(('review', 'diagnose', 'correction', 'recovery')):
        assert result['exit_code'] == 3, (suffix, result)
        active = journal.state['active_native']
        assert active['step_id'] == 'primary-'+suffix
        choice = choices[active['workflow']]
        scope = preparer.scope(step_id=active['step_id'], selection=choice)
        assert scope.input_artifacts
        fresh = NativePhaseCoordinator(journal, installed_root=installed.installed_root,
                                       engine_python=installed.engine_python)
        with pytest.raises(StateError, match='Original native scope is unavailable'):
            fresh.scope(step_id=active['step_id'], selection=choice)
        with journal.lifecycle_lock():
            context = engine._context(next(p for p in engine.docs['scenario']['phases'] if p['id'] == active['step_id']))
            with pytest.raises(StateError, match='single-use'):
                preparer.prepare(context, selection=choice)
        if index < 3:
            result = finish_native(engine, result)
    before = list(driver.calls)
    recovery['fault'].write_bytes(recovery['fault'].read_bytes()+b'\n')
    with pytest.raises(StateError, match='source bytes changed'):
        preparer.scope(step_id='primary-recovery', selection=recovered)
    assert driver.calls == before and 'primary-deliver' in driver.calls
    with pytest.raises(StateError, match='cannot be restored'):
        pickle.dumps(preparer)


def test_native_selections_are_data_only_and_pin_every_runtime_input(tmp_path):
    p = tmp_path/'data'
    selected = NativeDiagnosisSelection(p, '0'*64, p, '1'*64)
    with pytest.raises(StateError, match='absolute'):
        replace(selected, baseline=Path('relative'))
    with pytest.raises(StateError, match='SHA256'):
        replace(selected, fault_sha256='unbound')
    evidence = {'source': (p, '2'*64)}
    recovered = NativeRecoverySelection(p, '0'*64, p, '1'*64, p, '2'*64, p, '3'*64, evidence)
    evidence['source'] = (p, '4'*64)
    assert recovered.evidence['source'][1] == '2'*64
    with pytest.raises(TypeError):
        recovered.evidence['source'] = (p, '5'*64)
