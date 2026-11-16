"""Read-only local readiness checks; never substitute them for live qualification."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import time

from .contracts import SCENARIO_ID, load_document


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
SCENARIO_ROOT = PACKAGE_ROOT / "demo/software-factory/scenarios" / SCENARIO_ID
REQUIRED_SERVICES = {
    "bigbang-live": {"gitlab", "argocd", "registry", "registry1", "prometheus", "kubernetes"},
    "bigbang-disconnected": {"gitlab", "argocd", "registry", "prometheus", "kubernetes"},
    "partybus-hosted": {"gitlab", "argocd", "registry", "registry1"},
    "openshift-app": {"gitlab", "argocd", "registry", "kubernetes"},
    "adapter-qualification": {"jenkins", "registry"},
    "replay": set(),
}


class _InspectionDeadline(TimeoutError):
    pass


def _checkpoint(deadline):
    if deadline is not None:
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ValueError('An absolute finite inspection deadline is required')
        if time.monotonic() >= deadline:
            raise _InspectionDeadline('Local readiness deadline exceeded')


def _file_hash(path: Path, *, limit: int = 512_000_000, deadline=None) -> str:
    """Hash explicitly configured tools without executing them."""
    _checkpoint(deadline)
    try:
        if not path.is_file() or path.stat().st_size > limit:
            raise ValueError("File is unavailable or exceeds the size limit")
        _checkpoint(deadline)
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as source:
            while True:
                _checkpoint(deadline)
                chunk = source.read(1024 * 1024)
                _checkpoint(deadline)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ValueError("File exceeds the size limit")
                digest.update(chunk)
        _checkpoint(deadline)
        return digest.hexdigest()
    except _InspectionDeadline:
        raise
    except OSError:
        raise ValueError("File is unavailable") from None


def inspect(config_path: Path, *, profile: str, lock_path: Path | None = None,
            scenario_root: Path = SCENARIO_ROOT, environ=None, now=None, deadline=None) -> dict:
    _checkpoint(deadline)
    env = os.environ if environ is None else environ
    current = datetime.now(timezone.utc) if now is None else now
    config, config_hash = load_document(config_path, "factory-config")
    _checkpoint(deadline)
    if config["profile"] != profile:
        raise ValueError("Requested profile differs from operator configuration")
    scenario, scenario_hash = load_document(scenario_root / "scenario.json", "factory-scenario")
    _checkpoint(deadline)
    _, assertions_hash = load_document(scenario_root / "assertions.json", "scenario-assertions")
    _checkpoint(deadline)
    report = {
        "schema_version": 1, "operation": "doctor", "execution_status": "complete",
        "readiness": "unknown", "live_qualification": "not_performed", "mutations_performed": False,
        "profile": profile, "environment": config["environment"], "scenario_id": scenario["scenario_id"],
        "observed_at": current.isoformat(), "config_sha256": config_hash,
        "scenario_sha256": scenario_hash, "assertions_sha256": assertions_hash, "checks": [], "gaps": [],
    }

    def gap(code, message, action):
        report["gaps"].append({"code": code, "message": message, "next_action": action, "required": True})

    expiry = datetime.fromisoformat(config["expires_at"].replace("Z", "+00:00"))
    if expiry <= current:
        gap("environment-expired", "The authorized lab window has expired.",
            "Have the owner renew the lab scope before starting or resuming mutations.")
    report["checks"].append({"id": "config-schema", "status": "pass"})
    report["checks"].append({"id": "scenario-graph", "status": "pass", "phases": len(scenario["phases"])})
    present = {service["id"] for service in config["services"]}
    for missing in sorted(REQUIRED_SERVICES[profile] - present):
        gap(f"service-missing:{missing}", f"Required service {missing} is not configured.",
            "Add the approved service origin, scope, and credential references.")
    for service in config["services"]:
        _checkpoint(deadline)
        for role in ("read_auth", "operator_auth"):
            auth = service.get(role)
            if auth is None:
                continue
            refs = [value for key, value in auth.items() if key.endswith("_env")]
            absent = sum(not bool(env.get(reference)) for reference in refs)
            report["checks"].append({"id": f"credential-presence:{service['id']}:{role}",
                                     "status": "unknown" if absent else "pass",
                                     "required_references": len(refs), "missing_references": absent,
                                     "authentication_verified": False})
            if absent:
                gap(f"credential-missing:{service['id']}:{role}",
                    "One or more configured credential references are unset.",
                    "Authenticate using the documented operator flow and set the configured environment references; do not put tokens in JSON.")
        if service["id"] in {"gitlab", "argocd", "registry1", "kubernetes"} and service["read_auth"]["mode"] == "none":
            gap(f"read-auth-missing:{service['id']}", "Required read identity is not configured.",
                "Configure a scoped read credential independently from the lab operator identity.")
        read_refs = {v for k, v in service["read_auth"].items() if k.endswith("_env") and k != "username_env"}
        operator_refs = {v for k, v in service.get("operator_auth", {}).items() if k.endswith("_env") and k != "username_env"}
        if read_refs & operator_refs:
            gap(f"identity-overlap:{service['id']}", "Read and operator roles share a credential reference.",
                "Use separate read-only and scoped lab operator credentials.")
        if service.get("ca_file"):
            try:
                ca_hash = _file_hash(Path(service["ca_file"]), limit=1_000_000, deadline=deadline)
                report["checks"].append({"id": f"ca-presence:{service['id']}", "status": "pass", "sha256": ca_hash,
                                         "certificate_validation_performed": False})
            except ValueError:
                gap(f"ca-unavailable:{service['id']}", "Configured private CA is unavailable.",
                    "Provide the approved CA file before service authentication.")

    if lock_path is None:
        gap("lock-missing", "No resolved release lock was supplied.",
            "Resolve and authenticate the Iron Bank images and exact platform/tool tuple, then provide the tested lock. No replacement public image is used.")
    else:
        _checkpoint(deadline)
        lock, lock_hash = load_document(lock_path, "factory-lock")
        _checkpoint(deadline)
        report["lock_sha256"] = lock_hash
        if lock["profile"] != profile:
            raise ValueError("Lock profile differs from operator configuration")
        if lock["scenario_sha256"] != scenario_hash:
            gap("scenario-lock-mismatch", "The locked scenario differs from the packaged graph.", "Review and qualify the changed scenario before replacing the lock.")
        if lock["assertions_sha256"] != assertions_hash:
            gap("assertions-lock-mismatch", "The locked assertions differ from the packaged assertions.", "Review and qualify the changed assertions before replacing the lock.")
        if lock["platform"]["architecture"] != config["runtime"]["architecture"]:
            gap("architecture-mismatch", "Configured runtime and locked images target different architectures.", "Select and qualify the same architecture throughout acquisition, build and runtime.")
        for tool in lock["tools"]:
            _checkpoint(deadline)
            path = config["tool_paths"].get(tool["name"])
            try:
                actual = _file_hash(Path(path), deadline=deadline) if path else None
            except ValueError:
                actual = None
            if actual != tool["sha256"]:
                gap(f"tool-unverified:{tool['name']}", "A locked tool is absent or its file digest differs.",
                    "Acquire the pinned approved binary and configure its path before executing it.")
            else:
                report["checks"].append({"id": f"tool-integrity:{tool['name']}", "status": "pass", "sha256": actual,
                                         "execution_performed": False})
    gap("live-preflight-pending", "Service authentication, actual grants, image trust, runtime capacity and controller compatibility have not been verified.",
        "Complete the live F0 access and compatibility checks; local schema or hash checks cannot qualify the factory.")
    gap("lifecycle-implementation-pending", "This engineering slice implements local preflight; live lifecycle execution is not available yet.",
        "Continue the implementation ledger through the canonical deployment, fault, native correction, recovery and cleanup milestones.")
    _checkpoint(deadline)
    return report
