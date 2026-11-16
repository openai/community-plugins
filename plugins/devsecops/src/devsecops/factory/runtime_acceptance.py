"""Canonical D1/D2 acceptance from resident production proof, never receipts alone.

The recovery report remains a domain report. These eight additional assertions
bind independent CI/image, pre-incident F0 and installed native histories to the
current primary/replay observation. They do not issue runtime/write authority.
"""

from copy import deepcopy

from devsecops.runtime.context import canonical_bytes, digest, parse_time
from .lifecycle import AssertionResult, PhaseContext
from .state import (
    StateError,
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACT_TOTAL_BYTES,
    MAX_RESULT_ARTIFACTS,
    MAX_DOCUMENT_BYTES,
    _json,
    _read,
    _sha,
)

CHECKS = (
    ("D1", "reference-web-signature-and-required-attestations"),
    ("D1", "reference-web-unchanged-origin-to-runtime-identity"),
    ("D1", "mission-real-ci-build-to-digest"),
    ("D1", "mission-sbom-scan-signature-subjects"),
    ("D2", "exact-qualified-policy-fault"),
    ("D2", "scrape-failure-causally-isolated"),
    ("D2", "actual-installed-review-and-diagnosis"),
    ("D2", "native-patch-digest-before-validation"),
)


class _Unknown(ValueError):
    pass


class _Conflict(ValueError):
    pass


def _condition(report, name, *, phase=None, target=None):
    rows = [
        row
        for row in report["conditions"]
        if row["id"] == name
        and (phase is None or row["phase"] == phase)
        and (target is None or row.get("target_id") == target)
    ]
    if len(rows) != 1 or rows[0].get("authenticated") is not True or rows[0]["observed_state"] == "unknown":
        raise _Unknown("required-independent-condition-unavailable")
    if rows[0]["observed_state"] != "recovered":
        raise _Conflict("independent-condition-failed")


def _current(session, context, report):
    from .runtime_observations import RuntimeObservationSession, _ORIGINS, verify_runtime_observation_origin

    if type(session) is not RuntimeObservationSession or type(context) is not PhaseContext:
        raise _Unknown("original-runtime-session-required")
    session._admit(context)
    attempt = context.phase["attempt"]
    if (
        context.execution_mode != "operator"
        or context.phase["id"] != attempt + "-verify"
        or context.phase["operation"] != "verify"
        or attempt not in ("primary", "replay")
    ):
        raise _Unknown("current-operator-verification-required")
    binding = session._recovery[attempt]
    issued = _ORIGINS[binding["origin"]]
    retained = issued.get("verified_report")
    if (
        retained is None
        or retained[0] is not report
        or retained[1] != canonical_bytes(report)
        or retained[2] is not context
        or issued.get("phase_context") is not context
        or issued["session"] is not session
        or issued["attempt"] != attempt
        or report.get("execution_mode") != "direct"
        or report.get("identities") != dict(context.identities)
    ):
        raise _Unknown("same-call-direct-verification-required")
    proof = verify_runtime_observation_origin(binding["origin"], artifacts=issued["artifacts"])
    if (
        set(proof.assurances) != set(report["provenance"]["assurances"])
        or report["provenance"]["verified"] is not True
    ):
        raise _Unknown("original-direct-runtime-assurances-unavailable")
    contract = _json(issued["artifacts"]["contract"])
    if (
        contract["attempt_id"] != attempt
        or contract["run_id"] != context.run_id
        or contract["identities"] != dict(context.identities)
    ):
        raise _Unknown("current-attempt-contract-required")
    return issued, contract, set(proof.assurances)


def _assurance(assurances, *names):
    if not set(names) <= assurances:
        raise _Unknown("required-original-assurance-unavailable")


def _reference(session, context, contract):
    from .locked_images import require_locked_images, locked_image_artifacts

    binding = require_locked_images(session._locked_images, journal=session._journal, context=context)
    image, locked = binding["reference"], contract["factory_lock"]["images"]["reference_web"]
    wanted = {
        "repository": locked["repository"],
        "requested_digest": "sha256:" + locked["manifest_sha256"],
        "platform_manifest_digest": "sha256:" + locked["platform_manifest_sha256"],
        "config_digest": "sha256:" + locked["config_sha256"],
        "architecture": locked["architecture"],
    }
    if any(image[key] != value for key, value in wanted.items()) or set(image["required_predicates"]) != set(
        locked["required_predicates"]
    ):
        raise _Conflict("locked-reference-content-or-predicates-differ")
    return image, locked_image_artifacts(session._locked_images, journal=session._journal, context=context)


def _mission(session, context):
    from .ci_publication import require_runtime_image, runtime_image_artifacts

    image = require_runtime_image(
        session._runtime_image,
        run_scope=context.identities,
        source_commit=session._mission_subject["source_commit"],
    )
    if image["scope"] != "runtime-image-identity-only" or image["ci"]["run_id"] != context.run_id:
        raise _Unknown("original-current-ci-runtime-history-required")
    if image["ci"]["source_sha"] != image["source_commit"]:
        raise _Conflict("ci-source-and-image-build-differ")
    if (
        type(image["ci"]["pipeline_id"]) is not int
        or image["ci"]["pipeline_id"] <= 0
        or not image["ci"]["jobs"]
        or any(job["status"] != "success" for job in image["ci"]["jobs"].values())
    ):
        raise _Unknown("successful-original-ci-jobs-required")
    subject = {key: deepcopy(image[key]) for key in session._mission_subject}
    subject["ci"].pop("observed_at", None)
    if subject != session._mission_subject:
        raise _Conflict("selected-build-subject-changed")
    return image, runtime_image_artifacts(session._runtime_image, run_scope=context.identities)


def _policy(session, context, contract, report, assurances):
    """Match every observed incident policy to the original F0 selector plan."""
    from devsecops.adapters.kubernetes import _policy as normalize_policy

    _assurance(assurances, "network-policy-path", "collection-origin", "operator-intent")
    qualified = session.qualified_policy_plan(context)
    plan, binding = qualified["plan"], qualified["qualification"]
    if binding["run_id"] != context.run_id or binding["identities"] != dict(context.identities):
        raise _Unknown("original-same-run-f0-required")
    mission = contract["mission_target"]
    target = next(t for t in contract["intents"]["baseline"]["targets"] if t["id"] == mission)
    namespace = target["kubernetes"]["namespace"]
    selected_id = binding["resource_id"]
    namespace_id, name = selected_id.split("/")[2:]
    if namespace_id != namespace:
        raise _Conflict("qualified-policy-target-differs")
    baseline = _json(plan.baseline)
    fault = _json(plan.fault)
    if (
        digest(baseline["spec"]) != binding["baseline_spec_sha256"]
        or digest(fault["spec"]) != binding["fault_spec_sha256"]
    ):
        raise _Conflict("qualified-policy-bytes-differ")
    fields = ("pod_selector", "policy_types", "ingress", "egress")
    original = session._retained("f0-network-policy", "recovery")
    if original["mode"] != "operator" or original["bundle"]["execution_mode"] != "direct":
        raise _Unknown("original-independent-f0-observation-required")
    original_rows = original["bundle"]["targets"][mission]["after"]["kubernetes"]["observations"][
        "network_policies"
    ]
    original_selected = [p for p in original_rows if p["namespace"] == namespace and p["name"] == name]
    if len(original_selected) != 1:
        raise _Unknown("original-independent-f0-policy-identity-required")
    initial = (
        original_selected[0]["uid"],
        sorted(
            [
                {k: v for k, v in p.items() if k not in ("resource_version", "generation")}
                for p in original_rows
                if p is not original_selected[0]
            ],
            key=lambda p: (p["namespace"], p["name"]),
        ),
    )
    others = {}
    for phase in ("baseline", "fault", "recovery"):
        record = session._retained(context.phase["attempt"], phase)
        if record["mode"] != "operator" or record["bundle"]["execution_mode"] != "direct":
            raise _Unknown("actual-incident-observations-required")
        for side in ("before", "after"):
            rows = record["bundle"]["targets"][mission][side]["kubernetes"]["observations"][
                "network_policies"
            ]
            matches = [p for p in rows if p["namespace"] == namespace and p["name"] == name]
            if len(matches) != 1:
                raise _Unknown("exact-incident-policy-observation-required")
            actual = matches[0]
            template = deepcopy(fault if phase == "fault" else baseline)
            template["metadata"] = {
                "name": name,
                "namespace": namespace,
                "uid": actual["uid"],
                "resourceVersion": actual["resource_version"],
                "generation": actual["generation"],
            }
            expected = normalize_policy(template, namespace)
            if any(actual[field] != expected[field] for field in fields):
                raise _Conflict("incident-policy-differs-from-qualified-state")
            unchanged = [
                {k: v for k, v in p.items() if k not in ("resource_version", "generation")}
                for p in rows
                if p is not actual
            ]
            stable = (actual["uid"], sorted(unchanged, key=lambda p: (p["namespace"], p["name"])))
            if initial is not None and stable != initial:
                raise _Conflict("incident-policy-identity-or-unrelated-policy-changed")
            for target_id, target_data in record["bundle"]["targets"].items():
                if target_id == mission:
                    continue
                policies = target_data[side]["kubernetes"]["observations"]["network_policies"]
                selected_policies = sorted(
                    [
                        {k: v for k, v in p.items() if k not in ("resource_version", "generation")}
                        for p in policies
                    ],
                    key=lambda p: (p["namespace"], p["name"]),
                )
                if target_id in others and others[target_id] != selected_policies:
                    raise _Conflict("unrelated-target-policy-changed")
                others[target_id] = selected_policies
    _condition(report, "rendered-change-scope")
    _condition(report, "ordered-measured-windows")
    return binding


def _causal(session, context, contract, report, assurances):
    binding = _policy(session, context, contract, report, assurances)
    _assurance(assurances, "probe-origin-and-source", "image-content-mapping", "cluster-destination")
    for target in (contract["mission_target"], contract["reference_target"]):
        _condition(report, "runtime-images-and-ownership", phase="all", target=target)
        for phase in ("baseline", "fault", "recovery"):
            _condition(report, "workload-readiness", phase=phase, target=target)
            kind = "mission-health" if target == contract["mission_target"] else "reference-health"
            _condition(report, kind, phase=phase, target=target)
    for phase in ("baseline", "recovery"):
        _condition(report, "functional-telemetry", phase=phase, target=contract["mission_target"])
        _condition(report, "metrics-policy-selector-controls", phase=phase, target=contract["mission_target"])
    _condition(report, "telemetry-outage", phase="fault", target=contract["mission_target"])
    from devsecops.runtime.recovery import _fault_scrape_evidence

    fault = session._retained(context.phase["attempt"], "fault")["bundle"]
    target = next(t for t in contract["intents"]["fault"]["targets"] if t["id"] == contract["mission_target"])
    if not _fault_scrape_evidence(fault["targets"][target["id"]]["prometheus"], target, fault["window"]):
        raise _Unknown("fresh-exact-target-up-zero-required-for-causal-scrape-failure")
    # The recovery report alone deliberately says causality unproven. Only
    # this separate original pre-incident controlled experiment, matched to
    # the exact incident policy and preserved health, closes this assertion.
    return binding


def _native_reports(session, context):
    from .native_factory_session import NativeFactorySession
    from .native_reports import ValidatedNativeReport, native_report_artifacts

    native = getattr(session.driver, "_native_session", None)
    if type(native) is not NativeFactorySession:
        raise _Unknown("original-resident-native-session-required")
    evidence, documents = [], {}
    for suffix, workflow in (
        ("review", "review-kubernetes-change"),
        ("diagnose", "diagnose-gitops-deployment"),
    ):
        step = context.phase["attempt"] + "-" + suffix
        origin = native.report(step_id=step)
        if type(origin) is not ValidatedNativeReport:
            raise _Unknown("original-installed-native-report-required")
        doc = origin.document
        if (
            doc["step_id"] != step
            or doc["workflow"] != workflow
            or doc["run_id"] != context.run_id
            or doc["identities"] != dict(context.identities)
            or doc["execution_mode"] != "direct"
            or doc["runtime_authority"] != "not-issued"
        ):
            raise _Unknown("native-report-attempt-or-installed-package-differs")
        evidence.extend(native_report_artifacts(origin))
        documents[suffix] = doc
    return documents, evidence


def _patch(session, context, issued, report, assurances):
    from .correction_phase import require_validated_correction
    from .native_factory_session import NativeFactorySession
    from .native_provenance import verify_native_execution_origin

    _assurance(assurances, "native-execution", "correction-execution")
    correction = require_validated_correction(session.driver, context, attempt=context.phase["attempt"])
    native = getattr(session.driver, "_native_session", None)
    if (
        type(native) is not NativeFactorySession
        or correction["execution_mode"] != "operator"
        or native.origin(attempt=context.phase["attempt"]) is not correction["origin"]
    ):
        raise _Unknown("original-successful-current-attempt-correction-required")
    proof = verify_native_execution_origin(correction["origin"], artifacts=issued["artifacts"])
    if not proof["verified"] or proof["execution_mode"] != "direct":
        raise _Unknown("direct-native-patch-origin-required")
    execution = _json(issued["artifacts"]["native_execution"])
    validation = _json(issued["artifacts"]["native_validation"])
    digest_value = _sha(issued["artifacts"]["patch"])
    if (
        execution["attempt_id"] != context.phase["attempt"]
        or execution["patch_sha256"] != digest_value
        or execution["validation_receipt_sha256"] != _sha(issued["artifacts"]["native_validation"])
        or not parse_time(execution["finished_at"]) <= parse_time(validation["validated_at"])
    ):
        raise _Conflict("native-patch-digest-or-validation-order-differs")
    _condition(report, "native-patch-delivery-chain")
    return {
        "patch_sha256": digest_value,
        "native_execution_sha256": _sha(issued["artifacts"]["native_execution"]),
    }


def assertions_for_verification(session, context, report):
    """Return eight explicitly checked outcomes and original evidence paths.

    Missing authority stays unknown. An authenticated conflicting observation
    is fail. No overall report verdict is converted wholesale into assertions.
    """
    result = {
        "schema_version": 1,
        "kind": "canonical-runtime-acceptance",
        "run_id": context.run_id,
        "attempt_id": context.phase["attempt"],
        "identities": dict(context.identities),
        "execution_mode": context.execution_mode,
        "recovery_sha256": _sha(canonical_bytes(report)),
        "checks": [],
    }
    paths, outputs = set(), []
    try:
        issued, contract, assurances = _current(session, context, report)
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        issued = contract = assurances = None
    for case, name in CHECKS:
        status, code, proof = "unknown", "original-current-verification-unavailable", {}
        try:
            if issued is None:
                raise _Unknown(code)
            if name == CHECKS[0][1]:
                image, evidence = _reference(session, context, contract)
                paths.update(evidence)
                proof = {
                    key: image[key]
                    for key in ("verification_sha256", "publisher_trust_sha256", "mapping_receipt_sha256")
                }
            elif name == CHECKS[1][1]:
                image, evidence = _reference(session, context, contract)
                paths.update(evidence)
                _assurance(assurances, "image-content-mapping", "collection-origin")
                _condition(
                    report, "runtime-images-and-ownership", phase="all", target=contract["reference_target"]
                )
                proof = {"mapping_receipt_sha256": image["mapping_receipt_sha256"]}
            elif name in (CHECKS[2][1], CHECKS[3][1]):
                image, evidence = _mission(session, context)
                paths.update(evidence)
                proof = {
                    "source_commit": image["source_commit"],
                    "manifest_digest": image["manifest_digest"],
                    "pipeline_id": image["ci"]["pipeline_id"],
                    "history_readback": image["history_readback"]["sha256"],
                }
                if name == CHECKS[3][1]:
                    if image["current_policy"]["decision"] != "pass" or image["current_policy"]["gaps"]:
                        raise _Unknown("current-image-policy-unavailable")
                    proof.update(artifact_manifest=image["artifact_manifest"], signer=image["signer"])
            elif name == CHECKS[4][1]:
                proof = dict(_policy(session, context, contract, report, assurances))
            elif name == CHECKS[5][1]:
                proof = {
                    "qualification_sha256": _causal(session, context, contract, report, assurances)[
                        "qualification_sha256"
                    ]
                }
            elif name == CHECKS[6][1]:
                documents, evidence = _native_reports(session, context)
                paths.update(evidence)
                proof = {
                    key: {
                        name: doc[name] for name in ("request_sha256", "report_sha256", "validation_sha256")
                    }
                    for key, doc in documents.items()
                }
            else:
                proof = _patch(session, context, issued, report, assurances)
            status, code = "pass", "original-proofs-bound-to-current-attempt"
        except _Conflict as error:
            status, code = "fail", str(error)
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            code = "required-direct-proof-unavailable-or-incomplete"
        outputs.append(AssertionResult(case, name, status))
        result["checks"].append(
            {"case_id": case, "assertion_id": name, "status": status, "code": code, "proof": proof}
        )
    # Only additional current verifier artifacts are carried here. Previous F0,
    # native and correction histories remain in their original accepted phases.
    ordered = tuple(sorted(paths))
    if len(ordered) > MAX_RESULT_ARTIFACTS - 3:
        raise StateError("Additional verification evidence exceeds the phase path bound")
    own_bytes = len(canonical_bytes(report)) + len(canonical_bytes(result)) + MAX_DOCUMENT_BYTES
    if sum(len(_read(path, MAX_ARTIFACT_BYTES)) for path in ordered) + own_bytes > MAX_ARTIFACT_TOTAL_BYTES:
        raise StateError("Additional verification evidence exceeds the phase byte bound")
    return tuple(outputs), result, ordered
