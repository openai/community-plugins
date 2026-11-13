"""Fixed native input preparation on the canonical owned backend.

Selections describe data, never runtime or delivery authority. The fixed host
coordinator must obtain them afresh from its direct operation/collector origins.
Only this process retains the prepared scopes and their input bindings.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
from time import monotonic
from types import MappingProxyType, SimpleNamespace

from . import native_handoff, native_reports
from .lifecycle import PhaseContext, _deadline
from .state import MAX_ARTIFACT_BYTES, MAX_DOCUMENT_BYTES, RunJournal, StateError, _json, _private_directory, _read, _sha

_HASH = re.compile(r'[a-f0-9]{64}\Z')
_COMMIT = re.compile(r'[a-f0-9]{40}|[a-f0-9]{64}\Z')
_WORKFLOWS = {'review': 'review-kubernetes-change', 'diagnose': 'diagnose-gitops-deployment',
              'correction': 'prepare-gitops-correction', 'recovery': 'verify-gitops-recovery'}


def _path(value):
    if not isinstance(value, Path) or not value.is_absolute() or '..' in value.parts:
        raise StateError('Native source paths must be explicit and absolute')
    return value


def _pin(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise StateError('Native source identity must be an explicit SHA256')
    return value


def _mapping(value):
    if type(value) is not dict:
        raise StateError('Native source mapping must be explicit')
    frozen = {}
    for name, pair in value.items():
        if not isinstance(name, str) or type(pair) is not tuple or len(pair) != 2:
            raise StateError('Native source mapping requires exact path/hash pairs')
        frozen[name] = (_path(pair[0]), _pin(pair[1]))
    return MappingProxyType(frozen)


@dataclass(frozen=True)
class NativeSourceSelection:
    repository: Path
    base_commit: str
    fault_commit: str
    profile: Path
    profile_sha256: str
    correction_contract: Path
    correction_contract_sha256: str
    tools: dict
    schemas: Path
    schemas_sha256: str

    def __post_init__(self):
        for name in ('repository', 'profile', 'correction_contract', 'schemas'):
            _path(getattr(self, name))
        for name in ('profile_sha256', 'correction_contract_sha256', 'schemas_sha256'):
            _pin(getattr(self, name))
        if any(not isinstance(v, str) or not _COMMIT.fullmatch(v) for v in (self.base_commit, self.fault_commit)):
            raise StateError('Native review requires exact source revisions')
        object.__setattr__(self, 'tools', _mapping(self.tools))


@dataclass(frozen=True)
class NativeDiagnosisSelection:
    baseline: Path
    baseline_sha256: str
    fault: Path
    fault_sha256: str

    def __post_init__(self):
        for name in ('baseline', 'fault'):
            _path(getattr(self, name))
            _pin(getattr(self, name + '_sha256'))


@dataclass(frozen=True)
class NativeRecoverySelection(NativeDiagnosisSelection):
    recovered: Path
    recovered_sha256: str
    contract: Path
    contract_sha256: str
    evidence: dict

    def __post_init__(self):
        super().__post_init__()
        for name in ('recovered', 'contract'):
            _path(getattr(self, name))
            _pin(getattr(self, name + '_sha256'))
        object.__setattr__(self, 'evidence', _mapping(self.evidence))


def _step(step_id):
    if not isinstance(step_id, str) or '-' not in step_id:
        raise StateError('Canonical native step is required')
    attempt, kind = step_id.split('-', 1)
    if attempt not in ('primary', 'replay') or kind not in _WORKFLOWS:
        raise StateError('Only canonical primary/replay native steps are supported')
    return attempt, kind


def _verify_inputs(selection):
    if type(selection) is NativeSourceSelection:
        pairs = [(selection.profile, selection.profile_sha256),
                 (selection.correction_contract, selection.correction_contract_sha256)]
    elif type(selection) in (NativeDiagnosisSelection, NativeRecoverySelection):
        pairs = [(getattr(selection, name), getattr(selection, name + '_sha256')) for name in ('baseline', 'fault')]
        if type(selection) is NativeRecoverySelection:
            pairs += [(selection.recovered, selection.recovered_sha256), (selection.contract, selection.contract_sha256)]
            pairs += list(selection.evidence.values())
    else:
        raise StateError('A fixed native data selection is required')
    for path, pin in pairs:
        if _sha(_read(path, MAX_ARTIFACT_BYTES)) != pin:
            raise StateError('Selected native source bytes changed')


class NativePhaseCoordinator:
    """Prepare, retain and validate canonical native scopes; no origin reload."""
    def __init__(self, journal, *, installed_root, engine_python):
        if type(journal) is not RunJournal:
            raise StateError('A concrete current run journal is required')
        self.journal, self.installed_root, self.engine_python = journal, _path(installed_root), _path(engine_python)
        self.pid, self.run_id = os.getpid(), journal.state['run_id']
        self.identities = dict(journal.identities)
        self._prepared = {}

    def __reduce__(self):
        raise StateError('Native scopes cannot be restored from saved documents')

    def _current(self, step_id):
        state = self.journal.state
        active = state['lifecycle']['active_phase']
        if (self.pid != os.getpid() or state['run_id'] != self.run_id
                or self.journal.identities != self.identities or not self.journal._lifecycle_locked
                or not state['environment_reserved'] or state['active_mutation'] is not None
                or state['lifecycle']['abort'] is not None or active is None
                or active['phase_id'] != step_id
                or (state['lifecycle']['driver_id'], state['lifecycle']['execution_mode']) not in
                   (('owned-linux', 'operator'), ('controlled-test', 'controlled-test'))):
            raise StateError('Native inputs require the exact current reserved phase')
        _step(step_id)
        return state

    def prepare(self, context, *, selection):
        if type(context) is not PhaseContext:
            raise StateError('A current native phase context is required')
        deadline = min(context.deadline, monotonic() + 120)
        with _deadline(deadline):
            step_id = context.phase['id']
            _, kind = _step(step_id)
            state = self._current(step_id)
            if (context.run_id != self.run_id or context.run_dir != self.journal.run_dir
                    or dict(context.identities) != self.identities or context.phase['operation'] != 'native'
                    or context.phase['workflow'] != _WORKFLOWS[kind]
                    or state['active_native'] is not None or step_id in self._prepared):
                raise StateError('Native input preparation is single-use and phase-bound')
            expected = NativeSourceSelection if kind in ('review', 'correction') else (
                NativeDiagnosisSelection if kind == 'diagnose' else NativeRecoverySelection)
            if type(selection) is not expected:
                raise StateError('Native phase received the wrong fixed input selection')
            if deadline - monotonic() < 1:
                raise StateError('Native input preparation deadline expired')
            parent = self.journal.run_dir / 'native-inputs'
            _private_directory(parent)
            output = parent / step_id
            if output.exists() or output.is_symlink():
                raise StateError('Native scope was already claimed; inspect rather than retry')
            _verify_inputs(selection)
        if kind == 'correction':
            # This existing fixed function owns the one POSIX deadline context.
            scope = native_handoff.prepare_correction_scope(repository=selection.repository,
                base_commit=selection.fault_commit, profile=selection.profile, profile_sha256=selection.profile_sha256,
                contract=selection.correction_contract, contract_sha256=selection.correction_contract_sha256,
                tools=dict(selection.tools), schemas=selection.schemas, schemas_sha256=selection.schemas_sha256,
                output=output, timeout=min(120, deadline - monotonic()))
        else:
            with _deadline(deadline):
                if kind == 'review':
                    scope = native_reports.prepare_review_scope(repository=selection.repository,
                        base_commit=selection.base_commit, head_commit=selection.fault_commit,
                        profile=selection.profile, profile_sha256=selection.profile_sha256,
                        tools=dict(selection.tools), schemas=selection.schemas, schemas_sha256=selection.schemas_sha256,
                        output=output, deadline=deadline)
                elif kind == 'diagnose':
                    scope = native_reports.prepare_diagnosis_scope(baseline=selection.baseline, fault=selection.fault,
                                                                  output=output, deadline=deadline)
                else:
                    scope = native_reports.prepare_recovery_scope(baseline=selection.baseline, fault=selection.fault,
                        recovered=selection.recovered, contract=selection.contract, contract_sha256=selection.contract_sha256,
                        evidence={name: pair[0] for name, pair in selection.evidence.items()}, output=output, deadline=deadline)
        with _deadline(deadline):
            _verify_inputs(selection)
            self._current(step_id)
            self._prepared[step_id] = (selection, scope)
            return scope.input_artifacts

    def _retained(self, step_id, selection=None):
        self._current(step_id)
        if step_id not in self._prepared:
            raise StateError('Original native scope is unavailable; fresh host preparation is required')
        original, scope = self._prepared[step_id]
        if selection is not None and (type(selection) is not type(original) or selection != original):
            raise StateError('Current producer selection differs from prepared native inputs')
        _verify_inputs(original)
        return original, scope

    def scope(self, *, step_id, selection, deadline=None):
        deadline = min(deadline, monotonic() + 30) if deadline is not None else monotonic() + 30
        with _deadline(deadline), self.journal.lifecycle_lock():
            _, scope = self._retained(step_id, selection)
            active = self.journal.state['active_native']
            if active is None or active['step_id'] != step_id:
                raise StateError('Exact pending native request is required')
            request = _json(_read(self.journal.run_dir / active['request']['path'], MAX_DOCUMENT_BYTES))
            self._audit_scope(scope, deadline)
            self._request_inputs(request, scope)
            return scope

    def _request_inputs(self, request, scope):
        expected = {name: _read(path, MAX_ARTIFACT_BYTES) for name, path in scope.input_artifacts.items()}
        actual = {row['name']: _read(self.journal.run_dir / row['path'], MAX_ARTIFACT_BYTES)
                  for row in request['input_artifacts']}
        if expected != actual or request['run_id'] != self.run_id or request['identities'] != self.identities:
            raise StateError('Native request differs from its exact prepared inputs')

    @staticmethod
    def _audit_scope(scope, deadline):
        if type(scope) is native_handoff.CorrectionScope:
            native_handoff._audit(native_handoff._scope(scope), deadline)
        elif type(scope) is native_reports.NativeReportScope:
            native_reports._audit(native_reports._scope(scope), deadline)
        else:
            raise StateError('Only directly prepared native scopes are supported')

    def validate_native(self, request, result, artifacts):
        """Fixed artifact validation only; no model or runtime origin is issued.

        The caller's lifecycle validator deadline bounds this function. A fresh
        process cannot reconstruct scopes from these request/result documents.
        """
        step_id = request['step_id']
        _, scope = self._retained(step_id)
        active = self.journal.state['active_native']
        raw = _read(self.journal.run_dir / active['request']['path'], MAX_DOCUMENT_BYTES) if active else b''
        if (active is None or active['step_id'] != step_id or _sha(raw) != active['request']['sha256']
                or _json(raw) != request or result['status'] != 'completed'):
            raise StateError('Native validation requires the exact current completed result')
        self._request_inputs(request, scope)
        expires = datetime.fromisoformat(request['expires_at'].replace('Z', '+00:00'))
        remaining = (expires - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            raise StateError('Native request expired before validation')
        deadline = monotonic() + min(120, remaining)
        self._audit_scope(scope, deadline)
        directory = self.journal.run_dir / ('native-host-validation-' + active['request']['sha256'])
        _private_directory(directory, new=True)
        if type(scope) is native_handoff.CorrectionScope:
            validator = native_handoff._CorrectionValidator(native_handoff._scope(scope), request, directory, deadline)
            checks = validator(request, result, artifacts)
        else:
            selected = native_reports._scope(scope)
            report, _ = native_reports._document_artifacts(request['workflow'], result, artifacts)
            expected = native_reports._replay(selected, SimpleNamespace(engine_python=self.engine_python,
                installed_root=self.installed_root), directory, deadline, None)
            if native_reports._semantics(request['workflow'], report) != native_reports._semantics(request['workflow'], expected):
                raise StateError('Native result differs from fixed independent replay')
            native_reports._fresh(selected, report)
            checks = ['fixed-engine-replay-matched', 'domain-verdict-preserved']
        self._audit_scope(scope, deadline)
        self._request_inputs(request, scope)
        return [*checks, 'native-artifact-validation-only', 'native-runtime-origin-not-issued']
