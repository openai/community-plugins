"""Controlled CI/registry/crypto boundaries; no real build or E2E claim."""

from datetime import timedelta
import hashlib
import os
import pickle

import pytest

from devsecops.adapters.mission_evidence import ARTIFACT_TYPE, FILES, artifact_manifest, descriptor, reference
from devsecops.adapters.oci import MANIFEST
from devsecops.factory import ci_publication as module
from devsecops.factory.mission_signing import _VERIFIER_GAPS
from devsecops.factory.state import IDENTITY_KEYS
from devsecops.runtime.context import canonical_bytes
from test_owned_ci import SHA, started, observed_responses, wire as wire
from test_gitlab_api import Response


def pin(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def case(wire, tmp_path, monkeypatch):  # noqa: F811 - pytest injects the registered fixture
    flow = started(wire, tmp_path)
    observed_responses(wire)
    ci = flow.observe()["origin"]
    now = wire.ci_finished_at
    scope = {name: "b" * 64 for name in IDENTITY_KEYS}
    public = b"CONTROLLED PUBLIC KEY\n"
    trust = {
        "schema_version": 1,
        "kind": "mission-application-signing-trust",
        "id": "owned-key",
        "version": "1",
        "issuer": "owned-ci",
        "authority": "owned-lab-application-key",
        "public_key_sha256": pin(public),
        "origin": "https://registry.example.test",
        "repository": "demo/mission",
        "status": "active",
        "valid_from": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "trust_reference": "controlled-model-only",
    }
    trust_raw = canonical_bytes(trust)
    trust_path, public_path = tmp_path / "trust.json", tmp_path / "public.pem"
    trust_path.write_bytes(trust_raw)
    public_path.write_bytes(public)
    signer = {
        name: trust[name]
        for name in ("issuer", "authority", "id", "version", "trust_reference", "public_key_sha256")
    }
    signer.update(
        trust_policy_sha256=pin(trust_raw),
        lifecycle="operator-selected-active-policy",
        publisher_authority="not-claimed",
        transparency_log="not-used",
    )
    subject_raw = canonical_bytes(
        {"schemaVersion": 2, "mediaType": MANIFEST, "config": {"digest": "sha256:" + "c" * 64}, "layers": []}
    )
    subject = descriptor(subject_raw, MANIFEST)
    blobs = {name: canonical_bytes({"controlled": name}) for name, _, _ in FILES}
    blobs.update({"public-key.pem": public, "signing-trust.json": trust_raw})
    assessment = {
        "run_scope": scope,
        "source_commit": SHA,
        "architecture": "amd64",
        "context_sha256": "d" * 64,
        "base": {"repository": "controlled/base"},
        "manifest_digest": subject["digest"],
        "config_digest": "sha256:" + "c" * 64,
        "build_report_sha256": "e" * 64,
        "policy_sha256": "f" * 64,
        "policy_id": "policy",
        "policy_version": "1",
        "database": {"controlled": True},
        "tools": {"controlled": True},
        "scanner_receipt_sha256": pin(blobs["scanner-receipt.json"]),
        "applied_exceptions": [],
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "staging": {
            "origin": trust["origin"],
            "repository": trust["repository"],
            "configuration_sha256": "0" * 64,
            "readback_sha256": "1" * 64,
        },
        "archive": {"sha256": "2" * 64, "bytes": 100},
        "sbom": reference(blobs["sbom.spdx.json"]),
        "vulnerabilities": reference(blobs["grype.json"]),
        "assessment_binding_sha256": "3" * 64,
    }
    payload = {
        "critical": {
            "identity": {"docker-reference": "registry.example.test/demo/mission"},
            "image": {"docker-manifest-digest": subject["digest"]},
            "type": "cosign container image signature",
        },
        "optional": {
            "devsecops": {
                "schema_version": 1,
                "kind": "devsecops-mission-security-evidence",
                "issuer": signer,
                "assessment": assessment,
                "signed_at": (now - timedelta(seconds=10)).isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
            }
        },
    }
    blobs["payload.json"] = canonical_bytes(payload)
    artifact_raw, artifact = artifact_manifest(subject, {name: reference(raw) for name, raw in blobs.items()})
    config = {
        "schema_version": 1,
        "kind": "mission-evidence-publication-configuration",
        "origin": trust["origin"],
        "repository": trust["repository"],
        "credentials": {"token_env": "NO_LIVE_TOKEN"},
        "ca_bundle": None,
        "trust_policy_path": str(trust_path),
        "trust_policy_sha256": pin(trust_raw),
        "public_key_path": str(public_path),
        "public_key_sha256": pin(public),
        "cosign_path": "/controlled/cosign",
    }
    config_path = tmp_path / "publication.json"
    config_path.write_bytes(canonical_bytes(config))
    selector = {
        "schema_version": 1,
        "kind": "owned-ci-publication-selector",
        "run_scope": scope,
        "source_commit": SHA,
        "origin": trust["origin"],
        "repository": trust["repository"],
        "manifest_digest": subject["digest"],
        "architecture": "amd64",
        "artifact_manifest": {**artifact, "artifactType": ARTIFACT_TYPE},
        "policy_sha256": "f" * 64,
        "trust_policy_sha256": pin(trust_raw),
        "publication_config_sha256": pin(config_path.read_bytes()),
    }
    state = {
        "subject_raw": subject_raw,
        "artifact_raw": artifact_raw,
        "blobs": blobs,
        "closed": False,
        "reads": [],
        "fault": None,
    }

    class Reader:
        def __init__(self, selected, *, ca_bundle, deadline):
            assert selected == config

        def manifest(self, digest):
            state["reads"].append(("manifest", digest))
            if digest == subject["digest"]:
                return state["subject_raw"]
            assert digest == "sha256:" + pin(state["artifact_raw"])
            return state["artifact_raw"]

        def blob(self, layer, *, fd):
            name = layer["annotations"]["org.opencontainers.image.title"]
            raw = state["blobs"][name]
            assert layer["digest"] == "sha256:" + pin(raw)
            os.write(fd, raw)
            return reference(raw)

        def referrers(self, digest, expected):
            if state["fault"] == "discovery":
                raise ValueError("Missing actual referrer")
            assert digest == subject["digest"] and expected["digest"] == "sha256:" + pin(
                state["artifact_raw"]
            )

        def close(self):
            state["closed"] = True

    def crypto(**kwargs):
        assert kwargs["expected_digest"] == subject["digest"]
        decision = "fail" if state["fault"] == "signature" else "pass"
        return {
            "observations": {
                **{name: {"decision": decision} for name in ("crypto", "subject_binding", "verification")},
                "bundle_payload_digest_matches": True,
                "inputs": {
                    name: reference(kwargs[key].read_bytes())
                    for name, key in [
                        ("public-key.pem", "public_key"),
                        ("payload.json", "payload"),
                        ("bundle.json", "bundle"),
                    ]
                },
            },
            "gaps": [{"code": code} for code in _VERIFIER_GAPS],
        }

    monkeypatch.setattr(module, "EvidenceReader", Reader)
    monkeypatch.setattr(module, "verify_image_signature", crypto)

    def acquire():
        raw = canonical_bytes(selector)
        wire.queue.append(Response(raw=raw))
        observed_responses(wire)
        return flow.publication_report(ci)

    args = {
        "ci_origin": ci,
        "selector_raw": acquire(),
        "run_scope": scope,
        "source_commit": SHA,
        "architecture": "amd64",
        "policy_sha256": "f" * 64,
        "config": config_path,
        "expected_config_sha256": pin(config_path.read_bytes()),
        "output": tmp_path / "verified",
    }
    yield {
        "args": args,
        "flow": flow,
        "state": state,
        "selector": selector,
        "payload": payload,
        "acquire": acquire,
        "subject": subject,
        "config": config,
        "trust": trust,
    }
    flow.close()


def test_fresh_authenticated_artifact_registry_and_signature_composition(case):
    cap = module.verify_ci_publication(**case["args"])
    assert type(cap) is module.VerifiedCIPublication
    binding = cap.describe()
    assert binding["source_commit"] == SHA
    assert binding["ci"]["pipeline_id"] == 701
    assert binding["scope"] == "fresh-ci-publication-readback-only"
    assert case["state"]["closed"]
    with pytest.raises(TypeError):
        pickle.dumps(cap)
    changed = case["args"]["output"] / "readback-grype.json"
    changed.chmod(0o600)
    changed.write_text("{}")
    with pytest.raises(ValueError):
        cap.describe()


@pytest.mark.parametrize(
    "field,value",
    [("source_commit", "9" * 40), ("policy_sha256", "0" * 64), ("architecture", "arm64"), ("run_scope", {})],
)
def test_selection_must_match_independent_source_and_policy(case, field, value):
    args = dict(case["args"])
    args[field] = value
    with pytest.raises(ValueError):
        module.verify_ci_publication(**args)
    assert not case["state"]["reads"]


def test_same_ci_origin_cannot_admit_unacquired_selector_bytes(case):
    case["args"]["selector_raw"] += b" "
    with pytest.raises(ValueError, match="artifact"):
        module.verify_ci_publication(**case["args"])
    assert not case["state"]["reads"]


@pytest.mark.parametrize("fault", ["signature", "discovery"])
def test_registry_or_crypto_gap_cannot_issue_capability(case, fault):
    case["state"]["fault"] = fault
    with pytest.raises(ValueError):
        module.verify_ci_publication(**case["args"])
    assert case["state"]["closed"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_commit", "9" * 40),
        ("policy_sha256", "0" * 64),
        ("scanner_receipt_sha256", "0" * 64),
        ("manifest_digest", "sha256:" + "0" * 64),
    ],
)
def test_valid_signature_does_not_substitute_wrong_signed_assessment(case, field, value):
    case["payload"]["optional"]["devsecops"]["assessment"][field] = value
    case["state"]["blobs"]["payload.json"] = canonical_bytes(case["payload"])
    raw, artifact = artifact_manifest(
        case["subject"], {name: reference(raw) for name, raw in case["state"]["blobs"].items()}
    )
    case["state"]["artifact_raw"] = raw
    case["selector"]["artifact_manifest"] = {**artifact, "artifactType": ARTIFACT_TYPE}
    case["args"]["selector_raw"] = case["acquire"]()
    with pytest.raises(ValueError):
        module.verify_ci_publication(**case["args"])


def test_saved_publication_verdict_is_not_a_capability():
    with pytest.raises(ValueError):
        module.require_verified_ci_publication({"decision": "pass"})
    with pytest.raises(TypeError):
        module.VerifiedCIPublication()
