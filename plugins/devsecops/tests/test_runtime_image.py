"""Controlled CI/registry/crypto; real current policy evaluation, no live E2E."""

from datetime import datetime, timedelta, timezone
import pickle

import pytest

from devsecops.adapters import vulnerability_policy as policy_module
from devsecops.adapters.mission_evidence import ARTIFACT_TYPE, artifact_manifest, reference
from devsecops.factory import ci_publication as module, owned_ci
from devsecops.runtime.context import canonical_bytes
from test_ci_publication import case as case, pin
from test_owned_ci import jobs, pipe, project, wire as wire, SHA
from test_gitlab_api import Response, identity, page
from test_vulnerability_policy import documents


def history_responses(wire, raw, *, changed_job=None):  # noqa: F811 - pytest injects the registered fixture
    identity(wire)
    rows = jobs(wire)
    if changed_job:
        rows[0].update(changed_job)
    wire.queue.extend(
        [
            Response(project()),
            Response(pipe()),
            page(rows),
            page([]),
            Response(raw=raw),
            page(rows),
            Response(pipe()),
        ]
    )
    identity(wire)
    wire.queue.append(Response(project()))


@pytest.fixture
def runtime_case(case, tmp_path, wire):  # noqa: F811 - pytest injects the registered fixture
    docs = documents()
    docs["selected"]["database"]["authority"] = policy_module.AUTHORITY
    selected, listing = docs["selected"], docs["listing"]
    selected["database"]["listing_sha256"] = pin(canonical_bytes(listing))
    pp, lp = tmp_path / "current-policy.json", tmp_path / "current-listing.json"
    pp.write_bytes(canonical_bytes(selected))
    lp.write_bytes(canonical_bytes(listing))
    a = case["payload"]["optional"]["devsecops"]["assessment"]
    a["policy_sha256"] = pin(pp.read_bytes())
    a.update(policy_id=selected["id"], policy_version=selected["version"])
    a["database"] = {
        "authority": policy_module.AUTHORITY,
        "archive": {"sha256": selected["database"]["archive_sha256"], "bytes": 100},
        "listing": reference(lp.read_bytes()),
        "built_at": listing["built"],
        "schema_version": listing["schemaVersion"],
    }
    a["database"].update(
        {key: selected["database"][key] for key in ("trust_reference", "source_url", "acquired_at")}
    )
    docs["sbom"]["packages"][-1]["versionInfo"] = a["archive"]["sha256"]
    docs["sbom"]["packages"][-1]["checksums"][0]["checksumValue"] = a["manifest_digest"][7:]
    docs["result"]["source"]["target"].update(
        manifestDigest=a["manifest_digest"],
        userInput="frozen-artifact:" + a["archive"]["sha256"],
        imageID=a["config_digest"],
    )
    obs = docs["observation"]
    obs.update(
        source=a["archive"],
        image={
            "manifest_digest": a["manifest_digest"],
            "config_digest": a["config_digest"],
            "platform": {"os": "linux", "architecture": "amd64"},
        },
        coverage="one-platform-image",
        tools=a["tools"],
    )
    from copy import deepcopy
    from devsecops.adapters.scanner_catalog import NORMALIZATION
    from devsecops.factory.mission_security import _BASELINE_GAPS

    obs.update(
        catalog=deepcopy(obs["image"]), normalization={**deepcopy(obs["image"]), "method": NORMALIZATION}
    )
    obs["database"].update(sha256=selected["database"]["archive_sha256"], bytes=100, decision="pass")
    obs["sbom"]["decision"] = "pass"
    obs["vulnerabilities"]["decision"] = "pass"
    blobs = case["state"]["blobs"]
    blobs["sbom.spdx.json"] = canonical_bytes(docs["sbom"])
    blobs["grype.json"] = canonical_bytes(docs["result"])
    obs["artifacts"] = {
        key: {"path": name, **reference(blobs[name])}
        for key, name in (("sbom", "sbom.spdx.json"), ("vulnerabilities", "grype.json"))
    }
    blobs["scanner-receipt.json"] = canonical_bytes(
        {"observations": obs, "gaps": [{"code": code} for code in sorted(_BASELINE_GAPS)]}
    )
    a.update(
        sbom=reference(blobs["sbom.spdx.json"]),
        vulnerabilities=reference(blobs["grype.json"]),
        scanner_receipt_sha256=pin(blobs["scanner-receipt.json"]),
    )
    # Original signature occurs during the authenticated successful job.
    evidence = case["payload"]["optional"]["devsecops"]
    evidence["signed_at"] = (
        datetime.fromisoformat(jobs(wire)[0]["finished_at"]) - timedelta(minutes=1)
    ).isoformat()
    blobs["payload.json"] = canonical_bytes(case["payload"])
    artifact_raw, artifact = artifact_manifest(
        case["subject"], {name: reference(raw) for name, raw in blobs.items()}
    )
    case["state"]["artifact_raw"] = artifact_raw
    case["selector"].update(
        artifact_manifest={**artifact, "artifactType": ARTIFACT_TYPE}, policy_sha256=a["policy_sha256"]
    )
    case["args"].update(policy_sha256=a["policy_sha256"], selector_raw=case["acquire"]())
    pub = module.verify_ci_publication(**case["args"])
    return {
        "base": case,
        "publication": pub,
        "policy": pp,
        "listing": lp,
        "docs": docs,
        "args": {
            "publication": pub,
            "policy": pp,
            "listing": lp,
            "expected_policy_sha256": pin(pp.read_bytes()),
            "output": tmp_path / "runtime",
        },
    }


def test_expired_publication_is_not_revived_by_runtime_proof(runtime_case, wire, monkeypatch):  # noqa: F811 - pytest injects the registered fixture
    c = runtime_case
    original = c["publication"].describe()
    future = datetime.now(timezone.utc) + timedelta(minutes=6)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(module, "datetime", Later)
    monkeypatch.setattr(owned_ci, "datetime", Later)
    module._VERIFIED[c["publication"]]["deadline"] = 0
    c["base"]["flow"].deadline = 0
    with pytest.raises(ValueError):
        c["publication"].describe()
    history_responses(wire, c["base"]["args"]["selector_raw"])
    runtime = module.refresh_runtime_image(**c["args"])
    binding = module.require_runtime_image(runtime, run_scope=original["run_scope"], source_commit=SHA)
    assert binding["scope"] == "runtime-image-identity-only"
    assert binding["mapping_receipt_sha256"] == original["mapping_receipt_sha256"]
    assert binding["ci"]["jobs"] == original["ci"]["jobs"]
    assert binding["current_policy"]["decision"] == "pass"
    from pathlib import Path
    import json

    history_path = Path(binding["history_readback"]["path"])
    receipt = json.loads(history_path.read_bytes())
    assert pin(history_path.read_bytes()) == binding["history_readback"]["sha256"]
    assert receipt["jobs"] == original["ci"]["jobs"] and receipt["transport"]
    assert all(row["method"] == "GET" for row in receipt["transport"])
    assert binding["historical_expires_at"] < future.isoformat()
    original_paths = module.publication_evidence_artifacts(c["publication"], run_scope=original["run_scope"])
    runtime_paths = module.runtime_image_artifacts(runtime, run_scope=original["run_scope"])
    assert any(path.name == "ci-selector.json" for path in original_paths)
    assert history_path in runtime_paths
    bundle = c["base"]["flow"].evidence_artifacts(run_id=c["base"]["flow"].selection.run_id)[0]
    assert history_path.name in {item["label"] for item in json.loads(bundle.read_bytes())["documents"]}
    with pytest.raises(ValueError):
        module.require_verified_ci_publication(runtime, **module._VERIFIED[c["publication"]]["scope"])
    with pytest.raises(TypeError):
        pickle.dumps(runtime)
    assert len([row for row in wire.calls if row["method"] == "POST"]) == 1
    module._RUNTIME[runtime]["deadline"] = 0
    assert module.runtime_image_artifacts(runtime, run_scope=original["run_scope"]) == runtime_paths
    with pytest.raises(ValueError):
        runtime.describe()
    history_path.chmod(0o600)
    history_path.write_bytes(b"{}")
    with pytest.raises(ValueError):
        module.runtime_image_artifacts(runtime, run_scope=original["run_scope"])
    with pytest.raises(ValueError):
        runtime.describe()


@pytest.mark.parametrize("change", ["jobs", "artifact", "policy", "listing", "trust", "signature", "subject"])
def test_runtime_refresh_rejects_changed_original_or_current_inputs(runtime_case, wire, change):  # noqa: F811 - pytest injects the registered fixture
    c = runtime_case
    raw = c["base"]["args"]["selector_raw"] + (b" " if change == "artifact" else b"")
    history_responses(wire, raw, changed_job={"id": 902} if change == "jobs" else None)
    if change in ("policy", "listing"):
        c[change].write_bytes(c[change].read_bytes() + b" ")
    elif change == "trust":
        from pathlib import Path

        p = Path(c["base"]["config"]["trust_policy_path"])
        p.write_bytes(p.read_bytes() + b" ")
    elif change == "signature":
        c["base"]["state"]["fault"] = "signature"
    elif change == "subject":
        c["base"]["state"]["subject_raw"] += b" "
    with pytest.raises(ValueError):
        module.refresh_runtime_image(**c["args"])


def test_runtime_origin_requires_original_process_handle(runtime_case):
    c = runtime_case
    c["args"]["publication"] = c["publication"].describe()
    with pytest.raises(ValueError):
        module.refresh_runtime_image(**c["args"])
    with pytest.raises(TypeError):
        module.RuntimeImageProvenance()


def test_runtime_admission_rehashes_mapping_bytes(runtime_case, wire):  # noqa: F811 - pytest injects the registered fixture
    c = runtime_case
    history_responses(wire, c["base"]["args"]["selector_raw"])
    runtime = module.refresh_runtime_image(**c["args"])
    from pathlib import Path

    path = Path(runtime.describe()["mapping_receipt_path"])
    path.chmod(0o600)
    path.write_bytes(b"{}")
    with pytest.raises(ValueError):
        runtime.describe()


@pytest.mark.parametrize("days", [6, 8])
def test_current_database_or_policy_expiry_cannot_use_old_signed_pass(runtime_case, wire, monkeypatch, days):  # noqa: F811 - pytest injects the registered fixture
    c = runtime_case
    history_responses(wire, c["base"]["args"]["selector_raw"])
    future = datetime.now(timezone.utc) + timedelta(days=days)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return future

    monkeypatch.setattr(policy_module, "datetime", Later)
    with pytest.raises(ValueError, match="stale"):
        module.refresh_runtime_image(**c["args"])
