"""Original successful local validation, not a saved diagnostic, feeds delivery."""
from copy import deepcopy
import time

import pytest

from test_correction_phase import accepted, bind, begin_validation, execute  # noqa: F401
from test_native_handoff import prepared, correction_case  # noqa: F401
from devsecops.factory import correction_phase as module
from devsecops.factory.lifecycle import PhaseContext, _readonly
from devsecops.factory.state import StateError


def delivery_context(value, artifacts):
    journal = value['journal']
    with journal.lifecycle_lock():
        journal.finish_phase('primary-validate-correction', 'completed', 'controlled-local-validation',
                             receipt_artifacts=list(artifacts))
        journal.begin_phase('primary-deliver')
    phase = next(row for row in value['docs']['scenario']['phases'] if row['id'] == 'primary-deliver')
    return PhaseContext(journal.state['run_id'], _readonly(phase), _readonly(journal.identities),
        _readonly(value['docs']), _readonly(journal.state['lifecycle']['inputs']), journal.run_dir,
        time.monotonic()+60, 'controlled-test')


def test_actual_success_retains_only_original_validated_artifacts(accepted):  # noqa: F811
    bind(accepted)
    outcome = execute(accepted, begin_validation(accepted))
    context = delivery_context(accepted, outcome.artifacts)
    with accepted['journal'].lifecycle_lock():
        binding = module.require_validated_correction(accepted['driver'], context, attempt='primary')
        assert binding['origin'] is accepted['origin'] and binding['scope'] is accepted['scope']
        assert binding['execution_mode'] == 'controlled-test'
        assert set(binding['artifacts']) == {'patch', 'correction', 'correction_contract', 'rendered_before', 'rendered_after'}
        original = deepcopy(binding['base_tree'])
        binding['base_tree'].clear()
        again = module.require_validated_correction(accepted['driver'], context, attempt='primary')
        assert again['base_tree'] == original


@pytest.mark.parametrize('artifact', ['patch', 'correction', 'rendered_before', 'rendered_after', 'correction_contract'])
def test_changed_success_output_cannot_grant_correction_execution(accepted, artifact):  # noqa: F811
    bind(accepted)
    outcome = execute(accepted, begin_validation(accepted))
    context = delivery_context(accepted, outcome.artifacts)
    with accepted['journal'].lifecycle_lock():
        binding = module.require_validated_correction(accepted['driver'], context, attempt='primary')
        path = binding['artifacts'][artifact][0]
        path.write_bytes(path.read_bytes()+b' ')
        with pytest.raises((StateError, ValueError)):
            module.require_validated_correction(accepted['driver'], context, attempt='primary')


def test_failed_consumed_validation_never_retains_success(accepted):  # noqa: F811
    bind(accepted)
    accepted['scope'].input_artifacts['correction-contract'].write_bytes(b'{}')
    outcome = execute(accepted, begin_validation(accepted))
    assert outcome.status == 'incomplete'
    original = next(iter(accepted['driver']._correction_bindings.values()))
    assert original['consumed'] is True and 'validated_artifacts' not in original
