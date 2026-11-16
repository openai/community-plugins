"""Fresh registry/signature verification of an authenticated CI job selection.

This issues a new narrow readback capability; it never reconstructs a local
build, scanner, signing, or publication capability from a saved JSON verdict.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import time
from weakref import WeakKeyDictionary

from devsecops.adapters.http import _json
from devsecops.adapters.mission_evidence import (
    ARTIFACT_TYPE,
    FILES,
    EvidenceReader,
    artifact_manifest,
    descriptor,
)
from devsecops.adapters.oci import MANIFEST
from devsecops.adapters.signatures import _snapshot, verify_image_signature
from devsecops.adapters.vulnerability_policy import (
    checkpoint,
    timestamp,
    policy_document,
    database_listing,
    evaluate_scan,
    temporal_guard,
    CONTROLLED,
)
from devsecops.runtime.cli import _new_directory, _verify_output_path, _write
from .mission_publication import publication_configuration
from .mission_security import _read_retained
from .mission_signing import _VERIFIER_GAPS, trust_document, trust_guard
from .owned_ci import require_ci_artifact, require_ci_origin, require_ci_history, refresh_ci_history
from .state import _sha

_VERIFIED = WeakKeyDictionary()
_RUNTIME = WeakKeyDictionary()
_SELECTOR_KEYS = {
    "schema_version",
    "kind",
    "run_scope",
    "source_commit",
    "origin",
    "repository",
    "manifest_digest",
    "architecture",
    "artifact_manifest",
    "policy_sha256",
    "trust_policy_sha256",
    "publication_config_sha256",
}


class VerifiedCIPublication:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("CI publication requires fresh authenticated acquisition and verification")

    def __reduce__(self):
        raise TypeError("Verified CI publication cannot be serialized")

    def describe(self):
        record = _VERIFIED.get(self)
        if record is None:
            raise ValueError("Unissued CI publication")
        return require_verified_ci_publication(self, **record["scope"])


class RuntimeImageProvenance:
    """Fresh runtime identity only; never publication or Git write authority."""

    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Runtime image provenance requires fresh historical and current checks")

    def __reduce__(self):
        raise TypeError("Runtime image provenance cannot be serialized")

    def describe(self):
        return require_runtime_image(self)


def require_runtime_image(value, *, run_scope=None, source_commit=None):
    record = _RUNTIME.get(value) if type(value) is RuntimeImageProvenance else None
    if (
        record is None
        or record["pid"] != os.getpid()
        or time.monotonic() >= record["deadline"]
        or datetime.now(timezone.utc) >= timestamp(record["binding"]["expires_at"])
        or run_scope is not None
        and record["binding"]["run_scope"] != dict(run_scope)
        or source_commit is not None
        and record["binding"]["source_commit"] != source_commit
    ):
        raise ValueError("Fresh runtime image provenance required")
    require_ci_history(record["ci"])
    from devsecops.adapters.artifacts import _open_directory

    fd = _open_directory(record["path"])
    try:
        for name, ref in record["refs"].items():
            _read_retained(fd, name, ref, record["deadline"])
    finally:
        os.close(fd)
    temporal_guard(
        record["policy"], record["listing"], deadline=record["deadline"], applied=record["applied"]
    )
    trust_guard(record["trust"], deadline=record["deadline"])
    return deepcopy(record["binding"])


def refresh_runtime_image(
    *,
    publication,
    policy,
    expected_policy_sha256,
    listing,
    output,
    timeout=180,
    service_route=None,
    gitlab_service_route=None,
):
    """Retain old attestation as history, freshly verify current use conditions.

    The original publication may have expired. Its original direct record and
    exact retained bytes are required; no saved JSON can recreate this path.
    """
    old = _VERIFIED.get(publication) if type(publication) is VerifiedCIPublication else None
    if (
        old is None
        or old["pid"] != os.getpid()
        or type(timeout) not in (int, float)
        or not 2 <= timeout <= 600
    ):
        raise ValueError("Original direct publication and bounded runtime readback required")
    deadline = time.monotonic() + timeout
    from devsecops.adapters.artifacts import _open_directory

    fd = _open_directory(old["path"])
    try:
        for name, ref in old["refs"].items():
            _read_retained(fd, name, ref, deadline)
    finally:
        os.close(fd)
    checkpoint(deadline)
    history, raw = refresh_ci_history(
        old["ci"],
        timeout=min(deadline - time.monotonic(), 180),
        **({} if gitlab_service_route is None else {"service_route": gitlab_service_route}),
    )
    binding = old["binding"]
    cap = _verify_publication(
        ci_origin=history,
        selector_raw=raw,
        run_scope=binding["run_scope"],
        source_commit=binding["source_commit"],
        architecture=binding["architecture"],
        policy_sha256=binding["policy_sha256"],
        config=old["config"],
        expected_config_sha256=binding["publication_config_sha256"],
        expected_ci_publication_config_sha256=old["ci_config_sha256"],
        output=output,
        timeout=deadline - time.monotonic(),
        _historical=(policy, expected_policy_sha256, listing),
        service_route=service_route,
    )
    fresh = require_runtime_image(cap)
    for key in (
        "run_scope",
        "source_commit",
        "architecture",
        "origin",
        "repository",
        "manifest_digest",
        "config_digest",
        "base",
        "artifact_manifest",
        "trust_policy_sha256",
    ):
        if fresh[key] != binding[key]:
            _RUNTIME.pop(cap, None)
            raise ValueError("Runtime image differs from original publication")
    return cap


def require_verified_ci_publication(value, **scope):
    record = _VERIFIED.get(value) if type(value) is VerifiedCIPublication else None
    if (
        record is None
        or record["pid"] != os.getpid()
        or record["scope"] != scope
        or time.monotonic() >= record["deadline"]
        or datetime.now(timezone.utc) >= timestamp(record["binding"]["expires_at"])
    ):
        raise ValueError("Current verified CI publication required")
    require_ci_origin(record["ci"], source_sha=scope["source_commit"])
    # Keep the source files byte-bound for each downstream admission.
    from devsecops.adapters.artifacts import _open_directory

    fd = _open_directory(record["path"])
    try:
        for name, ref in record["refs"].items():
            _read_retained(fd, name, ref, record["deadline"])
    finally:
        os.close(fd)
    return deepcopy(record["binding"])


def publication_evidence_artifacts(value, *, run_scope):
    """Original immutable build history only; no current mutation authority."""
    record = _VERIFIED.get(value) if type(value) is VerifiedCIPublication else None
    if record is None or record["pid"] != os.getpid() or record["binding"]["run_scope"] != dict(run_scope):
        raise ValueError("Original same-run publication evidence required")
    from devsecops.adapters.artifacts import _open_directory

    fd = _open_directory(record["path"])
    try:
        for name, ref in record["refs"].items():
            _read_retained(fd, name, ref, time.monotonic() + 30)
    finally:
        os.close(fd)
    return tuple(record["path"] / name for name in record["refs"])


def runtime_image_artifacts(value, *, run_scope):
    """Original verifier/history bytes only; never renew runtime validity."""
    record = _RUNTIME.get(value) if type(value) is RuntimeImageProvenance else None
    if record is None or record["pid"] != os.getpid() or record["binding"]["run_scope"] != dict(run_scope):
        raise ValueError("Original same-run runtime image evidence required")
    from devsecops.adapters.artifacts import _open_directory
    from .state import _read

    deadline = time.monotonic() + 30
    fd = _open_directory(record["path"])
    try:
        for name, ref in record["refs"].items():
            _read_retained(fd, name, ref, deadline)
    finally:
        os.close(fd)
    history = record["binding"]["history_readback"]
    history_path = Path(history["path"])
    raw = _read(history_path, history["bytes"] + 1)
    if len(raw) != history["bytes"] or _sha(raw) != history["sha256"]:
        raise ValueError("Original historical CI evidence changed")
    return (*tuple(record["path"] / name for name in record["refs"]), history_path)


def verify_ci_publication(
    *,
    ci_origin,
    selector_raw,
    run_scope,
    source_commit,
    architecture,
    policy_sha256,
    config,
    expected_config_sha256,
    output,
    timeout=180,
    expected_ci_publication_config_sha256=None,
    service_route=None,
):
    return _verify_publication(
        ci_origin=ci_origin,
        selector_raw=selector_raw,
        run_scope=run_scope,
        source_commit=source_commit,
        architecture=architecture,
        policy_sha256=policy_sha256,
        config=config,
        expected_config_sha256=expected_config_sha256,
        output=output,
        timeout=timeout,
        expected_ci_publication_config_sha256=expected_ci_publication_config_sha256,
        service_route=service_route,
    )


def _verify_publication(
    *,
    ci_origin,
    selector_raw,
    run_scope,
    source_commit,
    architecture,
    policy_sha256,
    config,
    expected_config_sha256,
    output,
    timeout=180,
    expected_ci_publication_config_sha256=None,
    _historical=None,
    service_route=None,
):
    """Authenticate exact subject/evidence bytes and independent active signer.

    The signed assessment is trusted only under the independently pinned active
    application signing policy. This does not claim official Iron Bank origin,
    reexecute scanners, or approve promotion, Git writes, or deployment.
    """

    def admit_ci():
        if _historical is None:
            result = require_ci_origin(ci_origin, source_sha=source_commit)
            require_ci_artifact(ci_origin, selector_raw)
        else:
            result = require_ci_history(ci_origin, raw=selector_raw)
            if result["source_sha"] != source_commit:
                raise ValueError("Historical source differs")
        return result

    ci = admit_ci()
    if (
        not isinstance(output, Path)
        or not output.is_absolute()
        or ".." in output.parts
        or type(timeout) not in (int, float)
        or not 2 <= timeout <= 600
        or type(selector_raw) is not bytes
        or len(selector_raw) > 64000
    ):
        raise ValueError("Bounded exact CI publication selections required")
    deadline = time.monotonic() + timeout
    selector = _json(selector_raw)
    if (
        type(selector) is not dict
        or set(selector) != _SELECTOR_KEYS
        or type(selector["schema_version"]) is not int
        or selector["schema_version"] != 1
        or selector["kind"] != "owned-ci-publication-selector"
        or selector["run_scope"] != dict(run_scope)
        or selector["source_commit"] != source_commit
        or selector["architecture"] != architecture
        or selector["policy_sha256"] != policy_sha256
        or selector["publication_config_sha256"]
        != (
            expected_config_sha256
            if expected_ci_publication_config_sha256 is None
            else expected_ci_publication_config_sha256
        )
    ):
        raise ValueError("Authenticated job selector differs from selected run or policy")
    refs, reader, binding = {}, None, None
    with _new_directory(output) as fd:

        def write(name, raw):
            refs[name] = _write(fd, name, raw.decode("utf-8") if type(raw) is bytes else raw)
            return refs[name]

        def read(name):
            return _read_retained(fd, name, refs[name], deadline, materialize=True)

        def freeze(path, name, maximum, expected):
            refs[name] = _snapshot(path, output / name, maximum, deadline=deadline)
            if refs[name]["sha256"] != expected:
                raise ValueError("Independent publication selection changed")

        def audit():
            checkpoint(deadline)
            admit_ci()
            _verify_output_path(output, fd)
            for name in refs:
                read(name)

        try:
            write("ci-selector.json", selector_raw)
            freeze(config, "publication-config.json", 64000, expected_config_sha256)
            selected = publication_configuration(read("publication-config.json"))
            if any(
                selector[name] != selected[name] for name in ("origin", "repository", "trust_policy_sha256")
            ):
                raise ValueError("CI publication destination or signer differs")
            freeze(
                Path(selected["trust_policy_path"]),
                "signing-trust.json",
                64000,
                selected["trust_policy_sha256"],
            )
            trust = trust_document(read("signing-trust.json"), deadline=deadline)
            if (
                trust["authority"] == "controlled-test"
                or (trust["origin"], trust["repository"]) != (selected["origin"], selected["repository"])
                or trust["public_key_sha256"] != selected["public_key_sha256"]
            ):
                raise ValueError("Independent active application signer required")
            freeze(Path(selected["public_key_path"]), "public-key.pem", 16384, selected["public_key_sha256"])
            ca = None
            if selected["ca_bundle"] is not None:
                ca = output / "tls-ca.pem"
                refs["tls-ca.pem"] = _snapshot(Path(selected["ca_bundle"]), ca, 2_000_000, deadline=deadline)
            reader = EvidenceReader(
                selected,
                ca_bundle=ca,
                deadline=deadline,
                **({} if service_route is None else {"service_route": service_route}),
            )
            subject_raw = reader.manifest(selector["manifest_digest"])
            subject = _json(subject_raw)
            if (
                subject.get("mediaType") != MANIFEST
                or type(subject.get("schemaVersion")) is not int
                or subject["schemaVersion"] != 2
                or subject.get("artifactType")
                or subject.get("subject")
            ):
                raise ValueError("Selected subject is not an OCI image")
            write("subject-manifest.json", subject_raw)
            artifact = selector["artifact_manifest"]
            if (
                type(artifact) is not dict
                or set(artifact) != {"mediaType", "digest", "size", "artifactType"}
                or artifact["artifactType"] != ARTIFACT_TYPE
            ):
                raise ValueError("Fixed mission evidence descriptor required")
            artifact_raw = reader.manifest(artifact["digest"])
            if {**descriptor(artifact_raw, MANIFEST), "artifactType": ARTIFACT_TYPE} != artifact:
                raise ValueError("Exact CI evidence manifest differs")
            manifest = _json(artifact_raw)
            layers = manifest.get("layers")
            if not isinstance(layers, list) or len(layers) != len(FILES):
                raise ValueError("Fixed evidence layers required")
            evidence_refs = {}
            for (name, media, limit), layer in zip(FILES, layers, strict=True):
                if (
                    type(layer) is not dict
                    or set(layer) != {"mediaType", "digest", "size", "annotations"}
                    or layer["mediaType"] != media
                    or layer["annotations"] != {"org.opencontainers.image.title": name}
                    or type(layer["size"]) is not int
                    or not 1 <= layer["size"] <= limit
                ):
                    raise ValueError("Evidence layer selection differs")
                filename = "readback-" + name
                blob = os.open(filename, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
                try:
                    refs[filename] = reader.blob(layer, fd=blob)
                    os.fchmod(blob, 0o400)
                    os.fsync(blob)
                finally:
                    os.close(blob)
                evidence_refs[name] = refs[filename]
            expected_raw, expected_descriptor = artifact_manifest(
                descriptor(subject_raw, MANIFEST), evidence_refs
            )
            if artifact_raw != expected_raw:
                raise ValueError("Evidence manifest, subject or config differs")
            write("artifact-manifest.json", artifact_raw)
            reader.referrers(selector["manifest_digest"], expected_descriptor)
            if read("readback-public-key.pem") != read("public-key.pem") or read(
                "readback-signing-trust.json"
            ) != read("signing-trust.json"):
                raise ValueError("Registry signer differs from independent trust")
            verification = verify_image_signature(
                expected_repository=selected["origin"].removeprefix("https://")
                + "/"
                + selected["repository"],
                expected_digest=selector["manifest_digest"],
                public_key=output / "readback-public-key.pem",
                payload=output / "readback-payload.json",
                bundle=output / "readback-bundle.json",
                cosign=Path(selected["cosign_path"]),
                timeout=min(60, deadline - time.monotonic()),
            )
            observed = verification["observations"]
            if (
                any(
                    observed[name]["decision"] != "pass"
                    for name in ("crypto", "subject_binding", "verification")
                )
                or {item["code"] for item in verification["gaps"]} != _VERIFIER_GAPS
                or observed.get("bundle_payload_digest_matches") is not True
                or observed["inputs"]
                != {
                    name: refs["readback-" + name]
                    for name in ("public-key.pem", "payload.json", "bundle.json")
                }
            ):
                raise ValueError("Fresh signature verification incomplete")
            payload = _json(read("readback-payload.json"))
            if (
                set(payload) != {"critical", "optional"}
                or set(payload["optional"]) != {"devsecops"}
                or payload["critical"]
                != {
                    "identity": {
                        "docker-reference": selected["origin"].removeprefix("https://")
                        + "/"
                        + selected["repository"]
                    },
                    "image": {"docker-manifest-digest": selector["manifest_digest"]},
                    "type": "cosign container image signature",
                }
            ):
                raise ValueError("Signed payload shape differs")
            evidence = payload["optional"]["devsecops"]
            signer = {
                name: trust[name]
                for name in ("issuer", "authority", "id", "version", "trust_reference", "public_key_sha256")
            }
            signer.update(
                trust_policy_sha256=selected["trust_policy_sha256"],
                lifecycle="operator-selected-active-policy",
                publisher_authority="not-claimed",
                transparency_log="not-used",
            )
            if (
                set(evidence) != {"schema_version", "kind", "issuer", "assessment", "signed_at", "expires_at"}
                or evidence["schema_version"] != 1
                or evidence["kind"] != "devsecops-mission-security-evidence"
                or evidence["issuer"] != signer
            ):
                raise ValueError("Signed issuer or evidence shape differs")
            assessment = evidence["assessment"]
            expected_fields = {
                "run_scope",
                "source_commit",
                "architecture",
                "context_sha256",
                "base",
                "manifest_digest",
                "config_digest",
                "build_report_sha256",
                "policy_sha256",
                "policy_id",
                "policy_version",
                "database",
                "tools",
                "scanner_receipt_sha256",
                "applied_exceptions",
                "expires_at",
                "staging",
                "archive",
                "sbom",
                "vulnerabilities",
                "assessment_binding_sha256",
            }
            if type(assessment) is not dict or set(assessment) != expected_fields:
                raise ValueError("Closed signed assessment required")
            for name, expected in (
                ("run_scope", dict(run_scope)),
                ("source_commit", source_commit),
                ("architecture", architecture),
                ("policy_sha256", policy_sha256),
                ("manifest_digest", selector["manifest_digest"]),
                ("config_digest", subject["config"]["digest"]),
            ):
                if assessment.get(name) != expected:
                    raise ValueError("Signed assessment differs from current selected CI source")
            if (
                assessment["sbom"] != refs["readback-sbom.spdx.json"]
                or assessment["vulnerabilities"] != refs["readback-grype.json"]
                or assessment["scanner_receipt_sha256"] != refs["readback-scanner-receipt.json"]["sha256"]
                or any(assessment["staging"][name] != selected[name] for name in ("origin", "repository"))
            ):
                raise ValueError("Signed scanner evidence differs from registry bytes")
            now = datetime.now(timezone.utc)
            if (
                not timedelta(0)
                < timestamp(evidence["expires_at"]) - timestamp(evidence["signed_at"])
                <= timedelta(minutes=15)
            ):
                raise ValueError("Signed issuance validity exceeds the original bounded interval")
            if _historical is None:
                if (
                    not timestamp(evidence["signed_at"])
                    <= now
                    < timestamp(evidence["expires_at"])
                    <= timestamp(assessment["expires_at"])
                ):
                    raise ValueError("Signed assessment is stale or future dated")
                expires = min(
                    timestamp(evidence["expires_at"]),
                    trust_guard(trust, deadline=deadline),
                    now + timedelta(minutes=15),
                )
            else:
                job = ci["jobs"][ci["publication_job"]]
                if not (
                    timestamp(job["started_at"])
                    <= timestamp(evidence["signed_at"])
                    < timestamp(evidence["expires_at"])
                    <= timestamp(assessment["expires_at"])
                    and timestamp(evidence["signed_at"])
                    <= timestamp(job["finished_at"])
                    <= timestamp(ci["historical_observed_at"])
                    <= now
                ):
                    raise ValueError("Authenticated historical issuance timing is unavailable")
                selected_policy, policy_pin, selected_listing = _historical
                if policy_pin != policy_sha256:
                    raise ValueError("Current policy differs from selected signed policy")
                freeze(selected_policy, "current-policy.json", 256000, policy_pin)
                current_policy = policy_document(read("current-policy.json"), deadline=deadline)
                if current_policy["database"]["authority"] == CONTROLLED:
                    raise ValueError("Current database trust is not independently selected")
                freeze(
                    selected_listing,
                    "current-database-listing.json",
                    64000,
                    current_policy["database"]["listing_sha256"],
                )
                current_listing = database_listing(
                    read("current-database-listing.json"), current_policy, deadline=deadline
                )
                db = assessment["database"]
                if (
                    db["archive"]["sha256"] != current_policy["database"]["archive_sha256"]
                    or db["listing"]["sha256"] != current_policy["database"]["listing_sha256"]
                    or db["built_at"] != current_listing["built"]
                    or db["schema_version"] != current_listing["schemaVersion"]
                    or any(
                        db[name] != current_policy["database"][name]
                        for name in ("authority", "trust_reference", "source_url", "acquired_at")
                    )
                    or assessment["policy_id"] != current_policy["id"]
                    or assessment["policy_version"] != current_policy["version"]
                ):
                    raise ValueError("Current selected database differs from signed scan")
                applied = assessment["applied_exceptions"]
                if (
                    type(applied) is not list
                    or len(set(applied)) != len(applied)
                    or not set(applied) <= {item["id"] for item in current_policy["exceptions"]}
                ):
                    raise ValueError("Historical applied exception identity differs")
                temporal_guard(current_policy, current_listing, deadline=deadline, applied=applied)
                receipt = _json(read("readback-scanner-receipt.json"))
                observed = receipt["observations"]
                from .mission_security import _BASELINE_GAPS
                from devsecops.adapters.scanner_catalog import NORMALIZATION

                if (
                    {item["code"] for item in receipt["gaps"]} != _BASELINE_GAPS
                    or observed["coverage"] != "one-platform-image"
                    or observed["source"] != assessment["archive"]
                    or observed["image"]["manifest_digest"] != assessment["manifest_digest"]
                    or observed["image"]["config_digest"] != assessment["config_digest"]
                    or observed["image"]["platform"] != {"os": "linux", "architecture": architecture}
                    or any(
                        observed[name][key] != observed["image"][key]
                        for name in ("catalog", "normalization")
                        for key in ("manifest_digest", "config_digest", "platform")
                    )
                    or observed["normalization"]["method"] != NORMALIZATION
                    or observed["tools"] != assessment["tools"]
                    or observed["database"]["decision"] != "pass"
                    or observed["database"]["sha256"] != db["archive"]["sha256"]
                    or observed["database"]["bytes"] != db["archive"]["bytes"]
                    or observed["sbom"]["decision"] != "pass"
                    or observed["vulnerabilities"]["decision"] not in ("pass", "fail")
                    or any(
                        observed["artifacts"][key] != {"path": name, **assessment[key]}
                        for key, name in (("sbom", "sbom.spdx.json"), ("vulnerabilities", "grype.json"))
                    )
                ):
                    raise ValueError("Historical scanner subject differs")
                source = {**assessment, "archive_sha256": assessment["archive"]["sha256"]}
                current_assessment = evaluate_scan(
                    read("readback-sbom.spdx.json"),
                    read("readback-grype.json"),
                    observed,
                    source,
                    current_policy,
                    current_listing,
                    deadline=deadline,
                )
                if current_assessment["decision"] != "pass" or current_assessment["gaps"]:
                    raise ValueError("Current vulnerability policy does not pass")
                expires = min(
                    timestamp(current_assessment["expires_at"]),
                    trust_guard(trust, deadline=deadline),
                    now + timedelta(minutes=15),
                )
                write("current-runtime-policy.json", current_assessment)
            if reader.manifest(selector["manifest_digest"]) != subject_raw:
                raise ValueError("Final registry subject changed")
            write("signature-verification.json", verification)
            binding = {
                **deepcopy(assessment),
                "origin": selected["origin"],
                "repository": selected["repository"],
                "signer": signer,
                "trust_policy_sha256": selected["trust_policy_sha256"],
                "publication_config_sha256": expected_config_sha256,
                "artifact_manifest": deepcopy(artifact),
                "publication_path": str(output),
                "publication_readback": deepcopy(refs),
                "ci": {
                    key: value
                    for key, value in ci.items()
                    if key not in ("historical_observed_at", "history_readback")
                },
                "scope": "fresh-ci-publication-readback-only",
                "expires_at": expires.isoformat(),
            }
            binding.update(
                mapping_receipt_path=str(output / "subject-manifest.json"),
                mapping_receipt_sha256=refs["subject-manifest.json"]["sha256"],
            )
            if _historical is not None:
                binding.update(
                    scope="runtime-image-identity-only",
                    historical_signed_at=evidence["signed_at"],
                    historical_expires_at=evidence["expires_at"],
                    historical_ci_observed_at=ci["historical_observed_at"],
                    history_readback=deepcopy(ci["history_readback"]),
                    current_policy=current_assessment,
                )
            audit()
            write(
                "ci-publication.json",
                {
                    "schema_version": 1,
                    "kind": "verified-ci-publication",
                    "binding": binding,
                    "promotion_authority": "not-issued",
                    "git_write": "not-performed",
                },
            )
            os.fsync(fd)
            audit()
        finally:
            if reader is not None:
                reader.close()
    scope = {
        name: binding[name]
        for name in (
            "run_scope",
            "source_commit",
            "architecture",
            "origin",
            "repository",
            "policy_sha256",
            "trust_policy_sha256",
            "publication_config_sha256",
        )
    }
    record = {
        "pid": os.getpid(),
        "deadline": time.monotonic() + (expires - datetime.now(timezone.utc)).total_seconds(),
        "scope": scope,
        "binding": binding,
        "refs": deepcopy(refs),
        "path": output,
        "ci": ci_origin,
    }
    if _historical is None:
        capability = object.__new__(VerifiedCIPublication)
        record.update(config=config, ci_config_sha256=expected_ci_publication_config_sha256)
        _VERIFIED[capability] = record
        require_verified_ci_publication(capability, **scope)
    else:
        capability = object.__new__(RuntimeImageProvenance)
        record.update(
            policy=current_policy,
            listing=current_listing,
            applied=current_assessment["applied_exceptions"],
            trust=trust,
        )
        _RUNTIME[capability] = record
        require_runtime_image(capability)
    return capability
