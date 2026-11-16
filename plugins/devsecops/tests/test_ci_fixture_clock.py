"""Exercise real publication admission after synthetic collection delay; no live I/O."""

from datetime import datetime, timedelta, timezone

import pytest

from devsecops.adapters.mission_evidence import ARTIFACT_TYPE, artifact_manifest, reference
from devsecops.factory import ci_publication as publication
from devsecops.runtime.context import canonical_bytes
import test_owned_ci as ci_fixtures
from test_ci_publication import case as case
from test_owned_ci import wire as wire
from test_runtime_image import history_responses, jobs as imported_jobs, runtime_case as runtime_case


@pytest.mark.parametrize("collection_age", [timedelta(minutes=45), timedelta(hours=2)])
def test_delayed_collection_uses_one_execution_clock(request, monkeypatch, collection_age):
    execution_started = datetime.now(timezone.utc)
    # Reproduce the original import-time state without sleeping or changing the
    # production clock. Imported jobs/history helpers must use the current wire.
    monkeypatch.setattr(ci_fixtures, "NOW", execution_started - collection_age, raising=False)
    current = request.getfixturevalue("runtime_case")
    stream = request.getfixturevalue("wire")
    evidence = current["base"]["payload"]["optional"]["devsecops"]
    job = imported_jobs(stream)[0]
    assert execution_started <= stream.ci_finished_at <= datetime.now(timezone.utc)
    signed, expires = (datetime.fromisoformat(evidence[key]) for key in ("signed_at", "expires_at"))
    assert datetime.fromisoformat(job["started_at"]) <= signed <= datetime.fromisoformat(job["finished_at"])
    assert signed == stream.ci_finished_at - timedelta(minutes=1)
    assert expires == stream.ci_finished_at + timedelta(minutes=5)
    assert timedelta(0) < expires - signed <= timedelta(minutes=15)
    assert datetime.fromisoformat(evidence["assessment"]["expires_at"]) == (
        stream.ci_finished_at + timedelta(minutes=10)
    )
    assert current["publication"].describe()["ci"]["jobs"][job["name"]]["finished_at"] == job["finished_at"]
    history_responses(stream, current["base"]["args"]["selector_raw"])
    fresh = publication.refresh_runtime_image(**current["args"])
    assert fresh.describe()["ci"]["jobs"][job["name"]]["finished_at"] == job["finished_at"]
    assert not stream.queue


@pytest.mark.parametrize(
    "signed_offset,expiry_offset,reason",
    [
        (-16, 5, "bounded interval"),
        (-6, -1, "stale or future"),
        (1, 5, "stale or future"),
    ],
)
def test_fixture_correction_keeps_production_timing_rejections(case, wire, signed_offset, expiry_offset, reason):  # noqa: F811 - pytest fixture injection
    evidence = case["payload"]["optional"]["devsecops"]
    evidence["signed_at"] = (wire.ci_finished_at + timedelta(minutes=signed_offset)).isoformat()
    evidence["expires_at"] = (wire.ci_finished_at + timedelta(minutes=expiry_offset)).isoformat()
    blobs = case["state"]["blobs"]
    blobs["payload.json"] = canonical_bytes(case["payload"])
    raw, descriptor = artifact_manifest(case["subject"], {name: reference(value) for name, value in blobs.items()})
    case["state"]["artifact_raw"] = raw
    case["selector"]["artifact_manifest"] = {**descriptor, "artifactType": ARTIFACT_TYPE}
    case["args"]["selector_raw"] = case["acquire"]()
    with pytest.raises(ValueError, match=reason):
        publication.verify_ci_publication(**case["args"])
