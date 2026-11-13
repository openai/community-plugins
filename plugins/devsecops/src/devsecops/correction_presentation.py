"""A correction-specific view; a static review pass is not the correction gate."""
from __future__ import annotations

from .presentation import _CSS, _h, _md


def _label(result):
    return {'pass': 'Patch passes local validation', 'fail': 'Patch needs revision'}.get(
        result.get('decision'), 'Patch validation needs more evidence')


def render_correction_html(result: dict) -> str:
    decision = result.get('decision') if result.get('decision') in {'pass', 'fail'} else 'unknown'
    source = result.get('source', {})
    rows = ''.join(
        '<tr>' + ''.join(f'<td>{_h(value)}</td>' for value in (
            receipt['target'], change['resource'], change['pointer'],
            'Within contract' if change['permitted'] is True else 'Outside contract')) + '</tr>'
        for receipt in result.get('scope_receipts', []) for change in receipt.get('changes', []))
    gaps = ''.join(f'<li>{_h(gap.get("message", gap.get("code")))}</li>' for gap in result.get('gaps', []))
    violations = ''.join(f'<li>{_h(v)}</li>' for receipt in result.get('scope_receipts', [])
                         for v in receipt.get('violations', []))
    if result.get('has_rendered_change') is False:
        violations += '<li>The patch does not change rendered behavior.</li>'
    identities = [
        ('Contract', result.get('contract_id')), ('Original base commit', source.get('base_commit')),
        ('Patch SHA-256', result.get('patch_sha256')), ('Patched tree SHA-256', result.get('patched_tree', {}).get('sha256')),
        ('Profile SHA-256', source.get('profile_sha256')), ('Contract SHA-256', result.get('contract_sha256')),
    ]
    context = ''.join(f'<div><dt>{_h(name)}</dt><dd>{_h(value)}</dd></div>' for name, value in identities)
    states = [(result.get('static_review_decision', 'unknown'), 'Static review'),
              (result.get('delivery_status', 'not_performed'), 'Delivery'),
              (result.get('runtime_recovery', 'not_evaluated'), 'Runtime recovery')]
    stats = ''.join(f'<div class="stat"><strong>{_h(value.replace("_", " "))}</strong><span>{_h(name)}</span></div>'
                    for value, name in states)
    changes = ('<div class="table-scroll"><table><thead><tr><th>Target</th><th>Resource</th><th>Field</th><th>Scope</th>'
               '</tr></thead><tbody>' + rows + '</tbody></table></div>') if rows else '<p>No rendered field changes were recorded.</p>'
    files = ''.join(f'<li><code>{_h(path)}</code></li>' for path in result.get('changed_files', []))
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        '<meta name="color-scheme" content="light dark">'
        f'<title>DevSecOps · {_h(_label(result))}</title><style>{_CSS}'
        '.stats{grid-template-columns:repeat(3,minmax(0,1fr))}.stat strong{font-size:1.15rem}'
        '.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left}'
        'td,th{padding:12px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}td{font-size:.88rem}'
        'a{color:var(--brand);text-underline-offset:3px}.artifacts{display:flex;flex-wrap:wrap;gap:18px}'
        '.card>h2{margin-bottom:18px}.card>h3{margin-top:20px}'
        '@media(max-width:620px){.stats{grid-template-columns:1fr}.table-scroll td{min-width:130px}}'
        '</style></head><body><main><header class="brand"><span class="mark" aria-hidden="true">✓</span>'
        'DevSecOps<span class="edition">GitOps correction</span></header>'
        f'<section class="hero"><div class="eyebrow">Exact patch · Frozen inputs</div><h1>{_h(_label(result))}</h1>'
        f'<div class="statusline"><span class="badge {decision}">{decision.upper()}</span>'
        f'<span class="badge">{_h(result.get("execution_status", "partial"))}</span></div>'
        '<p class="subtitle">The correction outcome includes the exact patch, permitted files and rendered fields. '
        'Delivery and functional recovery are separate stages.</p></section>'
        f'<div class="stats">{stats}</div>'
        + (f'<section class="card"><h2>What needs attention</h2><ul>{violations}{gaps}</ul></section>' if violations or gaps else '')
        + f'<section class="card"><h2>Rendered changes</h2>{changes}<h3>Changed files</h3><ul>{files}</ul></section>'
        + f'<section class="card"><h2>Frozen identities</h2><dl class="context">{context}</dl></section>'
        '<section class="card"><h2>Review artifacts</h2><nav class="artifacts" aria-label="Review artifacts">'
        '<a href="proposal.patch" download>Exact proposed patch</a><a href="report.html">Underlying static review</a>'
        '<a href="correction.json">Structured correction</a><a href="correction-manifest.json">Artifact manifest</a></nav></section>'
        '<footer class="foot"><span>Generated locally · No external assets or scripts</span>'
        '<span>The terminal correction manifest binds this report and the original patch.</span></footer></main></body></html>'
    )


def render_correction_markdown(result: dict) -> str:
    return '\n'.join([
        '# GitOps correction', '', f'**{_label(result)}** · {_md(result.get("decision", "unknown"))}', '',
        f'Static review: {_md(result.get("static_review_decision"))}. '
        f'Delivery: {_md(result.get("delivery_status"))}. Runtime recovery: {_md(result.get("runtime_recovery"))}.', '',
        f'Original base: {_md(result.get("source", {}).get("base_commit"))}', '',
        f'Patch SHA-256: {_md(result.get("patch_sha256"))}', '',
        f'Patched tree SHA-256: {_md(result.get("patched_tree", {}).get("sha256"))}', '',
        '[Correction report](correction.html) · [Exact proposed patch](proposal.patch) · '
        '[Static review](report.html) · [Terminal manifest](correction-manifest.json)', '',
    ])
