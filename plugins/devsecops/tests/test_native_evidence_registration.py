"""Resident canonical evidence paths, with explicitly controlled native execution."""

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
from devsecops.factory.state import StateError
from test_native_provenance import correction as provenance_case, lab
from devsecops.factory import native_provenance

__all__ = [
    "pending",
    "correction_inputs",
    "correction_case",
    "installed",
    "recovery_scope",
    "controlled_native",
    "controlled_dispatch",
    "provenance_case",
    "lab",
]


def test_canonical_steps_expose_original_bounded_execution_files_without_scanning(
    pending, controlled_native, controlled_dispatch, installed
):
    session, engine, driver, scopes, args = pending
    with pytest.raises(StateError):
        session.completed_step_artifacts("primary-review")
    controlled_dispatch["patch"] = args.patch.read_bytes()
    for suffix in ("review", "diagnose", "correction", "recovery"):
        active = engine.journal.state["active_native"]
        step = "primary-" + suffix
        assert active["step_id"] == step
        if suffix == "correction":
            session.execute_pending(scopes[active["workflow"]])
            # This legacy controlled fixture replaces the entire issuer and
            # intentionally lacks original file provenance. Never synthesize it.
            with pytest.raises(StateError, match="file provenance is unavailable"):
                session.completed_step_artifacts(step)
            session.advance()
            continue
        else:
            execute_report(pending, controlled_native, installed)
        paths = session.completed_step_artifacts(step)
        names = {p.name for p in paths}
        assert {"execution.json", "invocation.json", "trace.jsonl", "manifest.json"} <= names
        assert all(p.is_file() and p.is_relative_to(engine.journal.run_dir) for p in paths)
        assert len(paths) <= 32
        stray = paths[0].parent / "unissued.json"
        stray.write_bytes(b"{}")
        assert stray not in session.completed_step_artifacts(step)
        chosen = next(p for p in paths if p.name == "invocation.json")
        original = chosen.read_bytes()
        chosen.write_bytes(original + b"changed")
        with pytest.raises(StateError):
            session.completed_step_artifacts(step)
        chosen.write_bytes(original)
        assert session.completed_step_artifacts(step) == paths
        session.advance()
    assert controlled_native["calls"] == 3 and controlled_dispatch["calls"] == 1


def test_actual_local_correction_issuer_retains_original_files_and_rejects_tampering(provenance_case):
    _, journal, args = provenance_case
    origin = native_provenance.execute_validated_correction(**args)
    paths = native_provenance.native_execution_artifacts(origin)
    assert {
        "execution.json",
        "invocation.json",
        "trace.jsonl",
        "manifest.json",
        "origin-manifest.json",
        "native_execution.json",
        "native_request.json",
        "native_result.json",
        "native_validation.json",
        "patch.patch",
    } <= {p.name for p in paths}
    path = next(p for p in paths if p.name == "trace.jsonl")
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(StateError, match="artifact bytes changed"):
        native_provenance.native_execution_artifacts(origin)
    with pytest.raises(StateError):
        native_provenance.native_execution_artifacts(origin.document)
