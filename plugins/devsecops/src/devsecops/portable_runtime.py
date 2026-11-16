"""Separate, read-only portable GitOps observations; never Iron Bank qualification."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from html import escape
import hashlib
import json
import os
from pathlib import Path
import sys

from jsonschema import Draft202012Validator, FormatChecker

from .adapters.argocd import API_CONTRACT, collect_argocd
from .adapters.http import APIError, HTTPSReadClient
from .adapters.kubernetes import KubernetesCollector
from .adapters.prometheus import PrometheusCollector
from .factory.contracts import read_json
from .runtime.cli import _new_directory, _verify_output_path, _write
from .runtime.context import canonical_bytes

PROFILE = "portable-gitops-v1"
CONDITION_NAMES = ("controller_sync", "controller_health", "workload_ready", "scrape_fresh", "functional_progress")
DECISION = {"enum": ["pass", "fail", "unknown"]}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
REVISION = {"type": "string", "pattern": "^(?:[a-f0-9]{40}|[a-f0-9]{64})$"}
NAME = {"type": "string", "pattern": "^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"}
TIME = {"type": "string", "format": "date-time"}


def _object(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


CONNECTION = _object({
    "origin": {"type": "string", "maxLength": 2048, "pattern": "^https://"},
    "token_env": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{0,127}$"},
    "ca_bundle": {"type": "string", "pattern": "^/[^\\u0000\\r\\n]+$", "maxLength": 4096},
}, ["origin"])
LIMITS = {
    "window_seconds": {"type": "integer", "minimum": 30, "maximum": 600},
    "step_seconds": {"type": "integer", "minimum": 5, "maximum": 60},
    "max_sample_age_seconds": {"type": "integer", "minimum": 5, "maximum": 120},
    "minimum_samples": {"type": "integer", "minimum": 3, "maximum": 121},
}
CONFIG_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:devsecops:portable-runtime-config:1",
    **_object({
        "schema_version": {"const": 1}, "profile": {"const": PROFILE},
        "argocd": _object({"connection": CONNECTION, "project": NAME, "application_namespace": NAME,
                           "application": NAME, "expected_revision": REVISION}),
        "kubernetes": _object({"connection": CONNECTION, "namespace": NAME, "deployment": NAME, "service": NAME}),
        "prometheus": _object({"connection": CONNECTION,
            "job": {"type": "string", "pattern": "^[A-Za-z0-9_.:/-]{1,128}$"},
            "instance": {"type": "string", "pattern": "^[A-Za-z0-9_.:\\[\\]-]{1,253}$"}, **LIMITS},
            ["connection", "job", "instance"]),
    }),
}
GAP = _object({"code": {"type": "string", "maxLength": 128},
               "message": {"type": "string", "maxLength": 512}, "required": {"type": "boolean"}})
SCOPE = _object({
    "argocd": _object({"origin": {"type": "string"}, "project": NAME, "application_namespace": NAME, "application": NAME}),
    "kubernetes": _object({"origin": {"type": "string"}, "namespace": NAME, "deployment": NAME, "service": NAME}),
    "prometheus": _object({"origin": {"type": "string"}, "job": {"type": "string"},
                            "instance": {"type": "string"}, **LIMITS}),
})
REPORT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:devsecops:portable-runtime-observation:1",
    **_object({
        "schema_version": {"const": 1}, "kind": {"const": "portable-gitops-observation"},
        "profile": {"const": PROFILE}, "phase": {"enum": ["baseline", "fault", "recovered", "observation"]},
        "decision": {"const": "unknown"}, "execution_status": {"const": "partial"},
        "transport_mode": {"enum": ["verified-https", "controlled-test"]},
        "started_at": TIME, "finished_at": TIME, "declared_delivery_at": {"anyOf": [TIME, {"type": "null"}]},
        "config_sha256": HASH, "scope_sha256": HASH, "scope": SCOPE, "expected_revision": REVISION,
        "conditions": _object({name: DECISION for name in CONDITION_NAMES}),
        "providers": _object({name: {"type": "object"} for name in ("argocd", "kubernetes", "prometheus")}),
        "gaps": {"type": "array", "items": GAP, "minItems": 1, "maxItems": 128},
    }),
    "allOf": [{"if": {"properties": {"phase": {"const": "recovered"}}},
               "then": {"properties": {"declared_delivery_at": TIME}}}],
}
COMPARISON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:devsecops:portable-runtime-comparison:1",
    **_object({
        "schema_version": {"const": 1}, "kind": {"const": "portable-gitops-comparison"},
        "profile": {"const": PROFILE}, "decision": {"const": "unknown"}, "execution_status": {"const": "partial"},
        "scope_sha256": HASH,
        "input_sha256": _object({phase: HASH for phase in ("baseline", "fault", "recovered")}, ["baseline", "fault"]),
        "conditions": {"type": "array", "minItems": 5, "maxItems": 5, "items": _object({
            "condition": {"enum": list(CONDITION_NAMES)}, "baseline": DECISION, "fault": DECISION,
            "recovered": DECISION, "fault_observed": {"type": "boolean"}, "recovery_observed": {"type": "boolean"}},
            ["condition", "baseline", "fault", "fault_observed", "recovery_observed"])},
        "phases": _object({phase: _object({
            "expected_revision": REVISION, "transport_mode": {"enum": ["verified-https", "controlled-test"]},
            "started_at": TIME, "finished_at": TIME, "declared_delivery_at": {"anyOf": [TIME, {"type": "null"}]},
        }) for phase in ("baseline", "fault", "recovered")}, ["baseline", "fault"]),
        "gaps": {"type": "array", "items": GAP, "minItems": 1, "maxItems": 128},
        "controlled_test_inputs": {"type": "boolean"},
    }),
}


def _validate(value, schema, label):
    if not Draft202012Validator(schema, format_checker=FormatChecker()).is_valid(value):
        raise ValueError(f"Invalid {label}; use its packaged schema")


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, AttributeError):
        raise ValueError("An explicit timezone-aware timestamp is required") from None


def _digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _gap(code, message):
    return {"code": code, "message": message, "required": True}


def _limits(config):
    defaults = {"window_seconds": 60, "step_seconds": 15, "max_sample_age_seconds": 45, "minimum_samples": 3}
    return {key: config.get(key, value) for key, value in defaults.items()}


def _scope(config, clients):
    result = {}
    for provider in ("argocd", "kubernetes", "prometheus"):
        result[provider] = {key: value for key, value in config[provider].items()
                            if key not in ("connection", "expected_revision")}
        result[provider]["origin"] = clients[provider].origin
    result["prometheus"].update(_limits(config["prometheus"]))
    return result


def _conditions(providers, finished_at=None):
    result = dict.fromkeys(CONDITION_NAMES, "unknown")
    argo, kube, prom = (providers[name] for name in ("argocd", "kubernetes", "prometheus"))
    if not any(gap["required"] for gap in argo["gaps"]):
        for name in ("sync", "health"):
            result["controller_" + name] = argo["observations"].get("decisions", {}).get(name, "unknown")
    ready = kube["observations"].get("workload_ready")
    if not kube["gaps"] and type(ready) is bool:
        result["workload_ready"] = "pass" if ready else "fail"
    minimum = prom["observations"]["window"]["minimum_fresh_samples"]
    # These are independent source-sample observations, not image/delivery verification.
    # The adapter publishes each series only after validating exact labels, source
    # timestamps, freshness and values. Keep cross-series inconsistency unknown.
    if not any(gap["code"] == "prometheus-source-window-mismatch" for gap in prom["gaps"]):
        for name, field, condition in (("scrape", "all_up", "scrape_fresh"),
                                       ("functional", "progressing", "functional_progress")):
            observed = prom["observations"].get(name, {})
            if observed.get("fresh_sample_count", 0) >= minimum and type(observed.get(field)) is bool:
                result[condition] = "pass" if observed[field] else "fail"
                if finished_at is not None and (finished_at.timestamp() - observed["samples"][-1]["source_timestamp"]
                                                > prom["observations"]["window"]["max_sample_age_seconds"]):
                    result[condition] = "unknown"
                    prom["gaps"].append(_gap("prometheus-completion-stale-" + name,
                                            "The last source sample aged out before the complete observation finished."))
                    prom["decision"], prom["execution_status"] = "unknown", "partial"
    reconciled = argo["observations"].get("application", {}).get("reconciled_at")
    if finished_at is not None and reconciled is not None and (finished_at - _timestamp(reconciled)).total_seconds() > 300:
        result["controller_sync"] = result["controller_health"] = "unknown"
        argo["gaps"].append(_gap("completion-stale", "Controller reconciliation aged out before the complete observation finished."))
        argo["decision"], argo["execution_status"] = "unknown", "partial"
    return result


def observe(config, *, phase="observation", delivered_at=None, _clients=None, _now=None):
    """Read the selected services only. Injection hooks always label controlled tests."""
    _validate(config, CONFIG_SCHEMA, "portable configuration")
    if phase not in ("baseline", "fault", "recovered", "observation"):
        raise ValueError("Unknown observation phase")
    if phase == "recovered" and delivered_at is None:
        raise ValueError("Recovered observations require the operator's explicit delivery timestamp")
    controlled = _clients is not None or _now is not None
    clock = (lambda: _now) if _now is not None else (lambda: datetime.now(UTC))
    start = clock()
    if not isinstance(start, datetime) or start.tzinfo is None:
        raise ValueError("An aware collection time is required")
    delivery = _timestamp(delivered_at) if delivered_at is not None else None
    if delivery is not None and delivery > start:
        raise ValueError("Delivery time cannot be in the future")
    limits = _limits(config["prometheus"])
    PrometheusCollector(None, **limits)  # Validate every bound before any network request.
    clients = _clients if _clients is not None else {
        name: HTTPSReadClient(**config[name]["connection"], max_requests=32 if name == "kubernetes" else 8,
                              total_timeout=45 if name == "kubernetes" else 20)
        for name in ("argocd", "kubernetes", "prometheus")
    }
    scope = _scope(config, clients)
    argo, kube, prom = (config[name] for name in ("argocd", "kubernetes", "prometheus"))
    reports = {
        "argocd": collect_argocd(clients["argocd"], project=argo["project"],
            application_namespace=argo["application_namespace"], application=argo["application"],
            selected_revisions={1: argo["expected_revision"]}, now=_now),
        "kubernetes": KubernetesCollector(clients["kubernetes"]).collect(
            kube["namespace"], kube["deployment"], kube["service"], now=_now),
        "prometheus": PrometheusCollector(clients["prometheus"], **limits).collect(
            kube["namespace"], kube["service"], prom["job"], prom["instance"], delivered_at=delivery, now=_now),
    }
    finished = clock()
    if finished < start:
        raise ValueError("Collection clock moved backward")
    for provider, report in reports.items():
        receipts = getattr(clients[provider], "receipts", [])
        if not isinstance(receipts, list):
            raise ValueError("Invalid transport receipts")
        report["observations"]["receipts"] = receipts
    conditions = _conditions(reports, finished)
    gaps = [_gap("portable-provenance-unverified", "These observations do not authenticate build, image publisher, delivery, environment or recovery provenance."),
            _gap("portable-image-window-unverified", "A single Kubernetes snapshot does not establish stable runtime images throughout the Prometheus window.")]
    for provider, report in reports.items():
        for gap in report["gaps"]:
            normalized = {**gap, "code": provider + ":" + gap["code"]}
            if normalized not in gaps:
                gaps.append(normalized)
    if delivery is not None:
        reconciled = reports["argocd"]["observations"].get("application", {}).get("reconciled_at")
        if reconciled is None or _timestamp(reconciled) <= delivery:
            conditions["controller_sync"] = conditions["controller_health"] = "unknown"
            gaps.append(_gap("controller-pre-delivery", "Controller reconciliation is not demonstrably after the declared delivery."))
    if controlled:
        gaps.append(_gap("controlled-test-input", "Injected clients or clock make this a controlled test, not live service qualification."))
    result = {"schema_version": 1, "kind": "portable-gitops-observation", "profile": PROFILE,
              "phase": phase, "decision": "unknown", "execution_status": "partial",
              "transport_mode": "controlled-test" if controlled else "verified-https",
              "started_at": start.isoformat(), "finished_at": finished.isoformat(),
              "declared_delivery_at": delivery.isoformat() if delivery else None,
              "config_sha256": _digest(config), "scope_sha256": _digest(scope), "scope": scope,
              "expected_revision": argo["expected_revision"], "conditions": conditions,
              "providers": reports, "gaps": gaps}
    _validate(result, REPORT_SCHEMA, "portable observation")
    return result


def _load_observation(path):
    report, digest = read_json(path, limit=16_000_000)
    _validate(report, REPORT_SCHEMA, "portable observation")
    manifest, _ = read_json(path.parent / "portable-manifest.json")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get("portable-observation.json"), dict):
        raise ValueError("Observation terminal manifest has invalid artifact metadata")
    expected = artifacts["portable-observation.json"]
    if (path.name != "portable-observation.json" or manifest.get("kind") != "portable-command-manifest"
            or manifest.get("profile") != PROFILE or manifest.get("command") != "collect"
            or manifest.get("complete") is not True or expected.get("sha256") != digest
            or report["scope_sha256"] != _digest(report["scope"])):
        raise ValueError("Observation integrity or terminal manifest is invalid")
    return report, digest


def compare(paths):
    """Offline, scope-bound comparison of explicit terminal observation bundles."""
    if set(paths) not in ({"baseline", "fault"}, {"baseline", "fault", "recovered"}):
        raise ValueError("Comparison requires baseline and fault; recovered is optional")
    phases = {}
    inputs = {}
    for phase in ("baseline", "fault", "recovered"):
        if phase in paths:
            phases[phase], inputs[phase] = _load_observation(Path(paths[phase]))
            if phases[phase]["phase"] != phase:
                raise ValueError("Observation phase does not match the selected input")
    if len({report["scope_sha256"] for report in phases.values()}) != 1:
        raise ValueError("Cannot compare observations with different service identities or selectors")
    ordered = list(phases.values())
    for report in ordered:
        if _timestamp(report["started_at"]) > _timestamp(report["finished_at"]):
            raise ValueError("Observation collection timestamps are reversed")
        if report["declared_delivery_at"] is not None and (_timestamp(report["declared_delivery_at"])
                                                          > _timestamp(report["started_at"])):
            raise ValueError("Declared delivery cannot follow its observation start")
    for before, after in zip(ordered, ordered[1:]):
        if _timestamp(before["finished_at"]) > _timestamp(after["started_at"]):
            raise ValueError("Observation phases overlap or are out of chronological order")
    if "recovered" in phases and (_timestamp(phases["recovered"]["declared_delivery_at"])
                                   <= _timestamp(phases["fault"]["finished_at"])):
        raise ValueError("Recovered delivery must follow the completed fault observation")
    rows = []
    for condition in CONDITION_NAMES:
        values = {phase: report["conditions"][condition] for phase, report in phases.items()}
        rows.append({"condition": condition, **values,
                     "fault_observed": values["baseline"] == "pass" and values["fault"] == "fail",
                     "recovery_observed": "recovered" in values and values["fault"] == "fail" and values["recovered"] == "pass"})
    result = {"schema_version": 1, "kind": "portable-gitops-comparison", "profile": PROFILE,
            "decision": "unknown", "execution_status": "partial", "scope_sha256": ordered[0]["scope_sha256"],
            "input_sha256": inputs, "conditions": rows,
            "phases": {phase: {"expected_revision": value["expected_revision"], "transport_mode": value["transport_mode"],
                       "started_at": value["started_at"], "finished_at": value["finished_at"],
                       "declared_delivery_at": value["declared_delivery_at"]} for phase, value in phases.items()},
            "gaps": [_gap("portable-provenance-unverified", "Hashes establish local integrity, not source authentication or qualified recovery."),
                     _gap("portable-causality-unproven", "Observed changes do not prove policy, application or infrastructure causality.")],
            "controlled_test_inputs": any(report["transport_mode"] == "controlled-test" for report in ordered)}
    _validate(result, COMPARISON_SCHEMA, "portable comparison")
    return result


def _render(report):
    observation = report["kind"] == "portable-gitops-observation"
    title = "GitOps observation" if observation else "GitOps comparison"
    headings = ["Condition", "Observed"] if observation else ["Condition", "Baseline", "Fault", "Recovered"]
    rows = ([[name.replace("_", " "), value] for name, value in report["conditions"].items()] if observation else
            [[row["condition"].replace("_", " "), row["baseline"], row["fault"], row.get("recovered", "not supplied")]
             for row in report["conditions"]])
    table = "".join("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row) + "</tr>" for row in rows)
    gaps = "".join(f"<li><code>{escape(gap['code'])}</code> — {escape(gap['message'])}</li>" for gap in report["gaps"])
    name = "portable-observation.json" if observation else "portable-comparison.json"
    html = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · DevSecOps</title><style>body{{margin:0;background:#eef3f7;color:#152635;font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1000px;margin:50px auto;padding:36px;background:white;border:1px solid #ccd7df;border-radius:16px}}
small{{color:#456073;letter-spacing:.1em}}h1{{font-size:36px;line-height:1.15}}.badge{{display:inline-block;background:#fff0cb;color:#754600;border-radius:8px;padding:7px 14px;font-weight:700}}
table{{width:100%;border-collapse:collapse;margin:28px 0}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #dce5eb}}th{{background:#f0f5f8}}code{{font-size:13px}}a{{color:#006b92}}li{{margin:10px 0}}@media(max-width:650px){{main{{margin:12px;padding:18px}}h1{{font-size:28px}}table{{font-size:13px}}}}</style>
<main><small>DEVSECOPS · {PROFILE}</small><h1>{title}</h1><p class="badge">Aggregate decision: unknown</p>
<p>Each condition uses its scoped provider result. Verified HTTPS binds reads to the configured service endpoints; it does not establish authenticated build, image, delivery or recovery provenance. Controlled inputs are identified in the JSON.</p>
<table><thead><tr>{''.join('<th>'+escape(label)+'</th>' for label in headings)}</tr></thead><tbody>{table}</tbody></table>
<p><a href="{name}">Read the evidence JSON</a> · <a href="portable-manifest.json">Artifact integrity</a></p><h2>Evidence boundaries</h2><ul>{gaps}</ul></main></html>'''
    md = f"# {title}\n\nProfile: {PROFILE}. Aggregate decision: **unknown**.\n\n"
    md += "| " + " | ".join(headings) + " |\n| " + " | ".join("---" for _ in headings) + " |\n"
    md += "".join("| " + " | ".join(map(str, row)) + " |\n" for row in rows)
    md += "\n" + "\n".join(f"- {gap['code']}: {gap['message']}" for gap in report["gaps"]) + "\n"
    return md, html


def _save(report, output, command, inputs):
    name = "portable-observation.json" if command == "collect" else "portable-comparison.json"
    markdown, html = _render(report)
    with _new_directory(output) as directory:
        artifacts = {name: _write(directory, name, report), "report.md": _write(directory, "report.md", markdown),
                     "report.html": _write(directory, "report.html", html)}
        _verify_output_path(output, directory)
        try:
            _write(directory, "portable-manifest.json", {"schema_version": 1, "kind": "portable-command-manifest",
                "profile": PROFILE, "command": command, "complete": True, "input_sha256": inputs, "artifacts": artifacts})
            _verify_output_path(output, directory)
        except BaseException:
            try:
                os.unlink("portable-manifest.json", dir_fd=directory)
            except FileNotFoundError:
                pass
            raise
    return output / name


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only portable GitOps evidence; separate from Iron Bank qualification")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect", help=f"Read named Argo CD ({API_CONTRACT}), Kubernetes and Prometheus APIs")
    collect.add_argument("--config", required=True, type=Path, help="Explicit trusted operator config, never inferred from evidence")
    collect.add_argument("--phase", choices=["baseline", "fault", "recovered", "observation"], default="observation")
    collect.add_argument("--not-before", help="Explicit delivery timestamp; required for recovered, used to reject earlier source samples")
    comparison = commands.add_parser("compare", help="Compare explicit observation bundles offline")
    comparison.add_argument("--baseline", required=True, type=Path)
    comparison.add_argument("--fault", required=True, type=Path)
    comparison.add_argument("--recovered", type=Path)
    for command in (collect, comparison):
        command.add_argument("--output", required=True, type=Path, help="New private output directory")
    args = parser.parse_args(argv)
    try:
        if args.command == "collect":
            config, digest = read_json(args.config)
            report = observe(config, phase=args.phase, delivered_at=args.not_before)
            inputs = {"config": digest}
        else:
            report = compare({phase: getattr(args, phase) for phase in ("baseline", "fault", "recovered") if getattr(args, phase)})
            inputs = report["input_sha256"]
        path = _save(report, args.output.absolute(), args.command, inputs)
    except (ValueError, OSError, APIError):
        print("Portable runtime command did not complete; check the config/schema, access, input integrity and new output path.", file=sys.stderr)
        return 3
    print(json.dumps({"decision": "unknown", "profile": PROFILE, "report": str(path),
                      "manifest": str(path.parent / "portable-manifest.json")}))
    return 2  # Measured condition results never close this profile's provenance gaps.


if __name__ == "__main__":
    raise SystemExit(main())
