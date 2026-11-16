"""Human-readable, self-contained projections of the review report.

The structured report is authoritative. Presentation functions do not run tools,
infer a passing result, load remote resources, or interpret HTML from evidence.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any


def _text(value: Any) -> str:
    if value is None:
        return "Not recorded"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    return str(value)


def _h(value: Any) -> str:
    return html.escape(_text(value), quote=True)


def _md(value: Any) -> str:
    text = html.escape(_text(value), quote=False).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~])", r"\\\1", text)


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _decision(value: Any) -> tuple[str, str]:
    return {
        "pass": ("pass", "Checks passed"),
        "fail": ("fail", "Findings require attention"),
        "unknown": ("unknown", "Review needs more evidence"),
    }.get(str(value), ("unknown", "Review needs more evidence"))


def _finding_title(finding: dict[str, Any]) -> str:
    return _text(finding.get("message") or finding.get("rule_id") or "Review finding")


def _gaps(report: dict[str, Any]) -> list[Any]:
    return _items(report.get("gaps"))


def _policy_summary(report: dict[str, Any]) -> str:
    findings = [
        _mapping(finding)
        for target in _items(report.get("targets"))
        for finding in _items(_mapping(target).get("findings"))
    ]
    suppressed = sum(finding.get("policy_suppressed") is True for finding in findings)
    overridden = sum(
        finding.get("original_severity", finding.get("severity"))
        != finding.get("effective_severity", finding.get("severity"))
        for finding in findings
    )
    treatments = []
    if suppressed:
        treatments.append(f"{suppressed} finding{'s' if suppressed != 1 else ''} excluded from the rule gate by active exception")
    if overridden:
        treatments.append(f"{overridden} finding{'s' if overridden != 1 else ''} with a profile severity override")
    return "Policy treatment: " + "; ".join(treatments) + ". Details remain visible below." if treatments else ""


def _exception_markdown(target: dict[str, Any]) -> list[str]:
    exceptions = _items(target.get("applied_exceptions"))
    if not exceptions:
        return []
    lines = [
        "### Applied policy exceptions",
        "",
        f"Evaluated on {_md(target.get('policy_evaluated_at'))} (UTC). "
        "These exceptions exclude matched findings from the configured rule gate; "
        "they do not remove findings or excuse missing validation.",
        "",
    ]
    for raw_exception in exceptions:
        exception = _mapping(raw_exception)
        lines.extend([
            f"**{_md(exception.get('id'))}**",
            "",
            f"- Owner: {_md(exception.get('owner'))}",
            f"- Reason: {_md(exception.get('reason'))}",
            f"- Valid through: {_md(exception.get('expires'))} (UTC)",
            f"- Target / rule: {_md(exception.get('target'))} / {_md(exception.get('rule_id'))}",
            f"- Resource: {_md(exception.get('resource'))}",
            f"- Matched findings: {_md(exception.get('matched_findings'))}",
            "",
        ])
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    """Render a review without allowing evidence to introduce Markdown markup."""
    scope = _mapping(report.get("scope"))
    summary = _mapping(report.get("summary"))
    _, label = _decision(report.get("decision"))
    lines = [
        "# DevSecOps change review",
        "",
        f"**{label}** · Review status: {_md(report.get('status', 'unknown'))}",
        "",
        "This report covers the declared local checks. It is not deployment approval, "
        "cluster admission proof, or compliance certification.",
        "",
        "| Review scope | Value |",
        "| --- | --- |",
        f"| Repository | {_md(scope.get('repo'))} |",
        f"| Baseline | {_md(scope.get('base'))} |",
        f"| Candidate | {_md(scope.get('head'))} |",
        f"| Profile | {_md(scope.get('profile_id'))} |",
        f"| Profile SHA-256 | {_md(scope.get('profile_sha256'))} |",
        "",
        f"Findings: **{_md(summary.get('findings', 0))}** · "
        f"High severity: **{_md(summary.get('high_findings', 0))}** · "
        f"Required gaps: **{_md(summary.get('required_gaps', 0))}**",
        "",
    ]
    if _policy_summary(report):
        lines.extend([_md(_policy_summary(report)), ""])
    if _gaps(report):
        lines.extend(["## Review coverage gaps", ""])
        lines.extend(f"- {_md(gap)}" for gap in _gaps(report))
        lines.append("")
    for raw_target in _items(report.get("targets")):
        target = _mapping(raw_target)
        _, target_label = _decision(target.get("decision"))
        lines.extend([
            f"## {_md(target.get('name', 'Unnamed target'))}",
            "",
            f"{target_label} · {_md(target.get('type'))} · {_md(target.get('status'))}",
            "",
        ])
        inventory = _mapping(target.get("inventory"))
        lines.extend([
            f"Resources: {_md(inventory.get('base_count', 'unknown'))} baseline → "
            f"{_md(inventory.get('head_count', 'unknown'))} candidate.",
            "",
        ])
        lines.extend(_exception_markdown(target))
        findings = _items(target.get("findings"))
        if findings:
            for raw_finding in findings:
                finding = _mapping(raw_finding)
                lines.extend([
                    f"### {_md(finding.get('severity', 'unknown')).upper()} · "
                    f"{_md(_finding_title(finding))}",
                    "",
                    f"- Resource: {_md(finding.get('resource'))}",
                    f"- Rule: {_md(finding.get('rule_id'))}",
                    f"- Field: {_md(finding.get('field'))}",
                    f"- Change: {_md(finding.get('change'))}",
                    f"- Original severity: {_md(finding.get('original_severity', finding.get('severity')))}; "
                    f"effective severity: {_md(finding.get('effective_severity', finding.get('severity')))}",
                    (
                        "- Gate treatment: **Excluded by active exception** · "
                        + _md(", ".join(_text(value) for value in _items(finding.get('policy_exception_ids'))))
                        if finding.get("policy_suppressed") is True
                        else "- Gate treatment: No exception suppression; the configured severity and change-class gate applies."
                    ),
                    f"- Evidence: {_md(finding.get('evidence'))}",
                    f"- Remediation: {_md(finding.get('remediation'))}",
                    "",
                ])
        else:
            lines.extend(["No findings were produced by the checks that completed.", ""])
        for key, title in [
            ("gaps", "Coverage gaps"),
            ("validation", "Validation"),
            ("changes", "Resource changes"),
            ("receipts", "Tool receipts"),
        ]:
            values = _items(target.get(key))
            if values:
                lines.extend([f"### {title}", ""])
                lines.extend(f"- {_md(item)}" for item in values)
                lines.append("")
    ci = report.get("ci")
    if ci is not None:
        lines.extend(["## GitLab evidence", "", _md(ci), ""])
    lines.extend([
        "---",
        "",
        f"DevSecOps {_md(report.get('plugin_version', 'unknown'))} · "
        f"Report schema {_md(report.get('schema_version', 'unknown'))}",
        "",
        "The structured report and run manifest preserve the review evidence and artifact hashes.",
        "",
    ])
    return "\n".join(lines)


_CSS = """
:root{color-scheme:light dark;--bg:#f4f7fa;--surface:#fff;--ink:#102a3d;
--muted:#536879;--line:#d9e3eb;--brand:#164e63;--soft:#eaf4f7;--pass:#126749;
--pass-bg:#e7f6ed;--fail:#a12531;--fail-bg:#fff0f1;--unknown:#8a5a07;
--unknown-bg:#fff7df;--shadow:0 12px 40px #102a3d0b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
font-size:15px;line-height:1.55}main{width:min(1120px,calc(100% - 48px));margin:40px auto 64px}
.brand{display:flex;align-items:center;gap:10px;font-weight:750;letter-spacing:-.02em;
font-size:19px}.mark{display:grid;place-items:center;width:32px;height:36px;
color:white;background:var(--brand);clip-path:polygon(50% 0,100% 16%,95% 72%,50% 100%,5% 72%,0 16%)}
.edition{margin-left:auto;font-size:12px;font-weight:500;color:var(--muted);letter-spacing:0}
.hero{margin:30px 0 22px}.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;
font-weight:750;color:var(--muted)}h1{font-size:clamp(27px,5vw,43px);line-height:1.16;
letter-spacing:-.04em;max-width:840px;margin:10px 0 15px}h2{font-size:21px;letter-spacing:-.025em;
line-height:1.3;margin:0}h3{font-size:15px;line-height:1.4;margin:0}p{margin:8px 0}
.subtitle{color:var(--muted);max-width:760px;font-size:15px}.badge{display:inline-block;
border-radius:999px;padding:4px 10px;font-size:11px;font-weight:750;letter-spacing:.015em;
background:var(--soft);color:var(--brand);vertical-align:middle;overflow-wrap:anywhere}
.pass{color:var(--pass);background:var(--pass-bg)}.fail,.high,.critical{color:var(--fail);background:var(--fail-bg)}
.unknown,.medium,.warning{color:var(--unknown);background:var(--unknown-bg)}
.low,.info{color:var(--brand);background:var(--soft)}.statusline{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}
.stat,.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}
.stat{padding:17px 20px}.stat strong{display:block;font-size:29px;line-height:1.2;font-weight:700;
letter-spacing:-.04em}.stat span{display:block;color:var(--muted);font-size:12px;margin-top:4px}
.card{padding:24px;margin-top:18px;overflow:hidden}.section-head{display:flex;gap:16px;
justify-content:space-between;align-items:flex-start;margin-bottom:17px}.context{display:grid;
grid-template-columns:1fr 1fr;gap:15px 28px;margin:0}.context div{min-width:0}.context dt,.field-label{
font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;
margin-bottom:4px}.context dd{margin:0;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;
font-size:12px;overflow-wrap:anywhere}.context .wide{grid-column:1/-1}.target{scroll-margin-top:20px}
.target-meta{margin-top:5px;font-size:12px;color:var(--muted)}.findings{display:grid;gap:12px;margin-top:18px}
.finding{border:1px solid var(--line);border-radius:12px;overflow:hidden}.finding-title{padding:17px 18px;
background:var(--surface)}.finding-title h3{margin-top:8px}.finding-meta{display:flex;gap:8px;
align-items:center;flex-wrap:wrap;color:var(--muted);font-size:11px}.finding-body{padding:0 18px 18px}
.finding-body p{font-size:13px}.resource{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;
font-size:11px;color:var(--muted);overflow-wrap:anywhere;margin:8px 0}
.severity-trace{font-size:11px;color:var(--muted);margin:8px 0 0}.exceptions{border:1px solid var(--line);
border-radius:12px;padding:16px;margin:18px 0;background:var(--soft)}.exceptions>p{font-size:12px;
color:var(--muted)}.exception{border-top:1px solid var(--line);padding-top:13px;margin-top:13px}
.exception .context{gap:10px 20px}.exception .context dd{font-family:inherit;font-size:12px}
.exception-id{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;font-weight:700;
overflow-wrap:anywhere;margin-bottom:10px}
.empty{padding:18px;border:1px dashed var(--line);border-radius:12px;color:var(--muted);
font-size:13px;margin-top:18px}.notice{border-left:3px solid var(--unknown);background:var(--unknown-bg);
color:var(--ink);padding:14px 16px;border-radius:0 10px 10px 0;margin:16px 0;font-size:13px}
details{border-top:1px solid var(--line);margin-top:15px;padding-top:13px}summary{cursor:pointer;
font-size:13px;font-weight:650;list-style-position:outside;margin-left:16px}summary:hover{color:var(--brand)}
.detail-list{margin:12px 0 0;padding:0;list-style:none;display:grid;gap:10px}.detail-list li{
border:1px solid var(--line);padding:12px 14px;border-radius:8px;font-size:12px;overflow-wrap:anywhere}
pre{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px;line-height:1.6;
white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);
border-radius:8px;padding:12px 14px;margin:10px 0 0;max-height:420px;overflow:auto}
.finding-body details{margin-top:12px}.finding-body pre{max-height:280px}.rule{font-family:
ui-monospace,SFMono-Regular,Consolas,monospace;font-size:10px;color:var(--muted)}
.foot{margin-top:25px;color:var(--muted);font-size:11px;display:flex;gap:15px;justify-content:space-between}
.small{font-size:12px;color:var(--muted)}a{color:var(--brand)}
@media screen and (prefers-color-scheme:dark){:root{--bg:#0c1720;--surface:#132330;--ink:#e4edf3;
--muted:#a0b3c1;--line:#2a3d4d;--brand:#75d5e8;--soft:#193c4c;--pass:#93e6b6;
--pass-bg:#193e30;--fail:#ffadb5;--fail-bg:#472630;--unknown:#f1d08a;--unknown-bg:#3d3320;
--shadow:none}.mark{background:#164e63}}
@media(max-width:680px){main{width:calc(100% - 28px);margin-top:22px}.stats{grid-template-columns:1fr 1fr;
gap:8px}.stat{padding:14px 16px}.card{padding:18px}.context{grid-template-columns:1fr}.context .wide{
grid-column:auto}.edition{font-size:10px}.section-head{flex-direction:column;gap:8px}.foot{flex-direction:column;gap:4px}}
@media print{:root{color-scheme:light;--bg:#fff;--surface:#fff;--line:#ccc;--shadow:none}
body{background:white;color:black}main{width:100%;margin:0}.card,.finding{break-inside:auto;overflow:visible}
.stat,.finding,.finding-title,.detail-list li{break-inside:avoid-page}
.finding-title{padding:12px 16px}.finding-body{padding:0 16px 14px}pre{line-height:1.35}
.detail-list li{border:0;padding:0}.detail-list pre{margin-top:0}
.edition,.foot{color:#555}.findings,.detail-list{display:block}
.finding+.finding{margin-top:12px}.detail-list li+li{margin-top:10px}
details{display:block}details>*{display:block}details::details-content{display:block;content-visibility:visible}
summary{list-style:none;margin-left:0;cursor:default}summary::-webkit-details-marker{display:none}
pre,.finding-body pre{max-height:none;overflow:visible}.section-head,.finding-title,summary{break-after:avoid}
.hero{margin-top:20px}h1{font-size:29px}}
"""


def _details(title: str, values: Any, *, opened: bool = False) -> str:
    items = _items(values)
    if not items:
        return ""
    content = "".join(f"<li><pre>{_h(item)}</pre></li>" for item in items)
    open_attr = " open" if opened else ""
    return (
        f"<details{open_attr}><summary>{_h(title)} <span class=\"small\">({len(items)})</span>"
        f"</summary><ul class=\"detail-list\">{content}</ul></details>"
    )


def _finding_html(finding: dict[str, Any]) -> str:
    severity = str(finding.get("effective_severity", finding.get("severity", "unknown"))).lower()
    original = str(finding.get("original_severity", finding.get("severity", "unknown"))).lower()
    style = severity if severity in {"critical", "high", "medium", "low", "info", "warning"} else "unknown"
    suppression = ""
    if finding.get("policy_suppressed") is True:
        identifiers = ", ".join(_text(value) for value in _items(finding.get("policy_exception_ids")))
        suppression = (
            '<div class="notice"><strong>Excluded from the configured finding gate by active exception.</strong> '
            f'Exception IDs: {_h(identifiers or "Not recorded")}. '
            'The finding remains visible; validation failures and coverage requirements still apply.</div>'
        )
    return (
        '<article class="finding">'
        '<div class="finding-title"><div class="finding-meta">'
        f'<span class="badge {style}">{_h(severity.upper())}</span>'
        f'<span>{_h(finding.get("change", "Change classification unavailable"))}</span>'
        f'<span class="rule">{_h(finding.get("rule_id", ""))}</span></div>'
        f'<div class="severity-trace">Original severity: {_h(original.upper())} '
        f'→ Effective severity: {_h(severity.upper())}</div>'
        f'<h3>{_h(_finding_title(finding))}</h3>'
        f'<div class="resource">{_h(finding.get("resource"))}</div></div>'
        '<div class="finding-body">' + suppression + '<div class="field-label">Recommended next step</div>'
        f'<p>{_h(finding.get("remediation", "Review the cited evidence with the platform owner."))}</p>'
        '<details><summary>Evidence and affected field</summary>'
        f'<p class="resource">{_h(finding.get("field"))}</p>'
        f'<pre>{_h(finding.get("evidence"))}</pre></details></div></article>'
    )


def _exceptions_html(target: dict[str, Any]) -> str:
    exceptions = _items(target.get("applied_exceptions"))
    if not exceptions:
        return ""
    cards = []
    for raw_exception in exceptions:
        exception = _mapping(raw_exception)
        fields = [
            ("Owner", exception.get("owner"), ""),
            ("Valid through (UTC)", exception.get("expires"), ""),
            ("Reason", exception.get("reason"), "wide"),
            ("Target / rule", f"{_text(exception.get('target'))} / {_text(exception.get('rule_id'))}", "wide"),
            ("Resource", exception.get("resource"), "wide"),
            ("Matched findings", exception.get("matched_findings"), ""),
            ("Evaluated on (UTC)", target.get("policy_evaluated_at"), ""),
        ]
        context = "".join(
            f'<div class="{wide}"><dt>{_h(title)}</dt><dd>{_h(value)}</dd></div>'
            for title, value, wide in fields
        )
        cards.append(
            f'<article class="exception"><div class="exception-id">{_h(exception.get("id"))}</div>'
            f'<dl class="context">{context}</dl></article>'
        )
    return (
        '<section class="exceptions"><h3>Applied policy exceptions</h3>'
        '<p>Matched findings remain visible but are excluded from the configured rule gate. '
        'Exceptions do not excuse validation failures or missing required evidence.</p>'
        + "".join(cards) + "</section>"
    )


def render_html(report: dict[str, Any]) -> str:
    """Render a responsive standalone report; all report content is escaped."""
    scope = _mapping(report.get("scope"))
    summary = _mapping(report.get("summary"))
    style, label = _decision(report.get("decision"))
    targets = _items(report.get("targets"))
    stats = [
        (len(targets), "Targets reviewed"),
        (summary.get("findings", 0), "Findings"),
        (summary.get("high_findings", 0), "High severity"),
        (summary.get("required_gaps", 0), "Required coverage gaps"),
    ]
    stats_html = "".join(
        f'<div class="stat"><strong>{_h(value)}</strong><span>{_h(title)}</span></div>'
        for value, title in stats
    )
    context_html = "".join(
        f'<div class="{wide}"><dt>{_h(title)}</dt><dd>{_h(value)}</dd></div>'
        for title, value, wide in [
            ("Repository", scope.get("repo"), "wide"),
            ("Baseline", scope.get("base"), ""),
            ("Candidate", scope.get("head"), ""),
            ("Profile", scope.get("profile_id"), ""),
            ("Profile SHA-256", scope.get("profile_sha256"), ""),
        ]
    )
    global_gaps = _gaps(report)
    coverage_html = ""
    if global_gaps:
        coverage_html = (
            '<section class="card"><h2>Coverage to resolve</h2>'
            '<p class="small">These gaps limit the conclusion. An unavailable check is not a pass.</p>'
            + _details("Review gaps", global_gaps, opened=True)
            + "</section>"
        )
    target_parts = []
    for index, raw_target in enumerate(targets):
        target = _mapping(raw_target)
        target_style, target_label = _decision(target.get("decision"))
        inventory = _mapping(target.get("inventory"))
        findings = _items(target.get("findings"))
        finding_html = (
            '<div class="findings">'
            + "".join(_finding_html(_mapping(finding)) for finding in findings)
            + "</div>"
            if findings
            else '<div class="empty">No findings were produced by the checks that completed. '
            "Review validation and coverage before drawing a broader conclusion.</div>"
        )
        target_parts.append(
            f'<section class="card target" id="target-{index + 1}">'
            '<div class="section-head"><div>'
            f'<h2>{_h(target.get("name", "Unnamed target"))}</h2>'
            f'<div class="target-meta">{_h(target.get("type"))} · '
            f'{_h(target.get("status"))} · '
            f'{_h(inventory.get("base_count", "?"))} → {_h(inventory.get("head_count", "?"))} resources</div>'
            f'</div><span class="badge {target_style}">{_h(target_label)}</span></div>'
            + _exceptions_html(target)
            + finding_html
            + _details("Coverage gaps", target.get("gaps"), opened=True)
            + _details("Validation", target.get("validation"))
            + _details("Resource changes", target.get("changes"))
            + _details("Tool receipts", target.get("receipts"))
            + "</section>"
        )
    ci_html = ""
    ci = report.get("ci")
    if ci is not None:
        ci_mapping = _mapping(ci)
        ci_style, ci_label = _decision(ci_mapping.get("decision"))
        ci_html = (
            '<section class="card"><div class="section-head"><div><h2>GitLab evidence</h2>'
            '<p class="small">Read-only context. Pipeline status does not prove deployment safety.</p>'
            f'</div><span class="badge {ci_style}">{_h(ci_label)}</span></div>'
            f'<details><summary>Evidence and coverage</summary><pre>{_h(ci)}</pre></details></section>'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        '<meta name="color-scheme" content="light dark">'
        f'<title>DevSecOps · {_h(label)}</title><style>{_CSS}</style></head><body><main>'
        '<header class="brand"><span class="mark" aria-hidden="true">✓</span>DevSecOps'
        f'<span class="edition">Kubernetes + Helm review · v{_h(report.get("plugin_version", "unknown"))}</span></header>'
        '<section class="hero"><div class="eyebrow">Change review</div>'
        f'<h1>{_h(label)}</h1><div class="statusline"><span class="badge {style}">'
        f'{_h(str(report.get("decision", "unknown")).upper())}</span>'
        f'<span class="badge">{_h(report.get("status", "unknown"))}</span></div>'
        '<p class="subtitle">A review of the declared candidate, profile, and local validation. '
        'Use the findings and coverage below to decide the next engineering step.</p></section>'
        f'<div class="stats">{stats_html}</div>'
        + (f'<div class="notice">{_h(_policy_summary(report))}</div>' if _policy_summary(report) else "")
        + '<section class="card"><div class="section-head">'
        '<h2>What was reviewed</h2><span class="badge">Local evidence</span></div>'
        f'<dl class="context">{context_html}</dl></section>{coverage_html}'
        + "".join(target_parts)
        + ci_html
        + '<div class="notice">This report is not deployment approval, cluster admission proof, '
        'runtime verification, or compliance certification. Missing evidence remains a gap.</div>'
        '<footer class="foot"><span>Generated locally · No external assets or scripts</span>'
        f'<span>Structured report schema {_h(report.get("schema_version", "unknown"))} · '
        'Artifact hashes are recorded in the run manifest.</span></footer></main></body></html>'
    )
