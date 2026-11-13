"""D6 selection access through actual controlled lifecycle; no extra model call."""

from dataclasses import replace
import pytest
from test_native_factory_session import (
    pending,
    correction_inputs,
    correction_case,
    installed,
    recovery_scope,
    controlled_native,
    controlled_dispatch,
    execute_report,
)
from test_factory_lifecycle import FixtureDriver
from devsecops.factory.lifecycle import PhaseOutcome
from devsecops.factory.state import StateError

__all__ = [
    "pending",
    "correction_inputs",
    "correction_case",
    "installed",
    "recovery_scope",
    "controlled_native",
    "controlled_dispatch",
]


def test_original_selection_only_during_exact_owned_reset(
    pending, controlled_native, controlled_dispatch, installed
):
    session, engine, driver, scopes, args = pending
    called = []

    def earlier(context):
        with pytest.raises(StateError):
            session.selection_for_interrupted_review(context)
        return FixtureDriver().execute(context)

    def observe(context):
        before = engine.journal.state
        assert session.selection_for_interrupted_review(context) is installed
        assert engine.journal.current_mutation("reset", "reset")
        for changed in (
            replace(context, run_id="other"),
            replace(context, phase={**context.phase, "operation": "deliver"}),
            replace(context, identities={**context.identities, "config_sha256": "0" * 64}),
            replace(context, deadline=0),
        ):
            with pytest.raises(StateError):
                session.selection_for_interrupted_review(changed)
        assert engine.journal.state == before
        called.append(context)
        return PhaseOutcome("incomplete", "controlled-reset-stop", mutation_effect="not_applied")

    driver.behaviors["evidence-gaps"] = earlier
    driver.behaviors["reset"] = observe
    controlled_dispatch["patch"] = args.patch.read_bytes()
    for suffix in ("review", "diagnose", "correction", "recovery"):
        active = engine.journal.state["active_native"]
        assert active["step_id"] == "primary-" + suffix
        if suffix == "correction":
            session.execute_pending(scopes[active["workflow"]])
        else:
            execute_report(pending, controlled_native, installed)
        result = session.advance()
    assert len(called) == 1 and result["exit_code"] == 2, engine.journal.state["lifecycle"]["abort"]
    assert len(engine.journal.state["native_results"]) == 4
    with pytest.raises(StateError):
        session.selection_for_interrupted_review(called[0])
