"""Human-readable observations; provider health never implies recovery."""
from __future__ import annotations

from devsecops.presentation import _CSS, _h, _md


def _title(result):
    if result.get('kind') == 'runtime-recovery':
        return {'verified': 'Recovery independently verified', 'not-recovered': 'Recovery conditions failed'}.get(
            result.get('recovery'), 'Recovery conditions checked')
    if result.get('kind') == 'runtime-diagnosis':
        return {'aligned': 'Baseline and fault compared', 'incompatible': 'Evidence could not be aligned'}.get(
            result.get('comparison_status'), 'Incomplete GitOps evidence')
    return {'pending': 'Observation window started', 'measured': 'Runtime observations collected',
            'blocked': 'Runtime evidence needs attention'}.get(result.get('stage'), 'Runtime evidence needs attention')


def _rows(result):
    rows = []
    for name, target in result.get('targets', {}).items():
        latest = target.get('after') or target.get('before') or {}
        argo = (latest.get('argocd') or {}).get('observations', {})
        kube = (latest.get('kubernetes') or {}).get('observations', {})
        prom = (target.get('prometheus') or {}).get('observations', {})
        controller = ' / '.join(str(argo[key]) for key in ('sync_status', 'health_status') if argo.get(key)) or 'Missing'
        ready = {True: 'Ready', False: 'Not ready', None: 'Unknown'}.get(kube.get('workload_ready'), 'Unknown')
        scrape = prom.get('scrape') or {}
        functional = prom.get('functional') or {}
        count = scrape.get('fresh_sample_count')
        telemetry = f'{count} samples in recorded window' if count is not None else 'Not measured'
        freshness = prom.get('collection_freshness', {}).get('decision')
        if freshness:
            telemetry += ' · freshness at collection: ' + str(freshness)
        if functional.get('progressing') is True:
            telemetry += ' · counter progressing'
        elif functional.get('progressing') is False:
            telemetry += ' · counter not progressing'
        rows.append((name, controller, ready, telemetry))
    return rows


def _value(value):
    if value is None:
        return 'Not observed'
    return (', '.join(map(str, value)) or 'None recorded') if isinstance(value, list) else str(value)


def _failure_label(result):
    if not result.get('confirmed_failure'):
        return 'Not established'
    retained = result.get('retained_failures', [])
    if retained and all(item.get('freshness') == 'historical-window-only' for item in retained):
        return 'Historical window only'
    return 'Recorded; check timing'


def _gaps_html(gaps):
    labels = {
        'immutable-journal-authentication-unverified': 'Inputs are not authenticated by a trusted run journal',
        'direct-http-evidence-missing': 'Application HTTP health has not been measured',
        'controlled-test-evidence': 'Testing hooks were used',
        'effective-policy-enforcement-unverified': 'Actual network-policy enforcement is not verified',
        'independent-probe-controls-missing': 'Independent positive and negative probe controls are missing',
        'causality-not-proven': 'The cause has not been proven',
    }
    groups = {}
    for gap in gaps:
        code = gap.get('code', 'unknown')
        scope = ' / '.join(str(gap[key]) for key in ('input', 'target_id') if gap.get(key))
        groups.setdefault(code, set()).add(scope)
    items = []
    for code, scopes in groups.items():
        label = labels.get(code, str(code).replace('-', ' '))
        detail = ' · '.join(sorted(scope for scope in scopes if scope))
        items.append(f'<li><strong>{_h(label)}</strong>' + (f'<br><small>{_h(detail)}</small>' if detail else '')
                     + f'<br><code>{_h(code)}</code></li>')
    if not items:
        return ''
    visible = '<ul>' + ''.join(items[:6]) + '</ul>'
    if len(items) > 6:
        visible += f'<details><summary>{len(items)-6} more gap categories</summary><ul>' + ''.join(items[6:]) + '</ul></details>'
    return '<section class="card"><h2>Evidence gaps</h2>' + visible + '</section>'


def _diagnosis_html(result):
    if result.get('kind') != 'runtime-diagnosis':
        return ''
    def table(headers, rows):
        heading = ''.join(f'<th>{_h(item)}</th>' for item in headers)
        body = ''.join('<tr>' + ''.join(f'<td>{_h(item)}</td>' for item in row) + '</tr>' for row in rows)
        return f'<div class="table-scroll"><table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>'

    facts = result.get('observations', [])
    changed_targets = {fact['target_id'] for fact in facts if fact['comparison'] == 'changed'}
    primary, remaining = [], []
    for fact in facts:
        row = (fact['target_id'], fact['kind'].replace('-', ' '), _value(fact.get('baseline')),
               _value(fact.get('fault')), fact['comparison'])
        important = fact['comparison'] == 'changed' or (
            fact['target_id'] in changed_targets and fact['kind'] in {'kubernetes-readiness', 'argo-health'})
        (primary if important else remaining).append(row)
    sections = ''
    if facts:
        headers = ('Target', 'Observation', 'Baseline', 'Fault', 'Comparison')
        sections += '<section class="card"><h2>What changed</h2>'
        sections += table(headers, primary) if primary else '<p>No change was established by the available observations.</p>'
        if remaining:
            sections += f'<details><summary>{len(remaining)} other observations</summary>{table(headers, remaining)}</details>'
        sections += '</section>'
    alternatives = result.get('alternatives', [])
    if alternatives:
        supported, unsupported = [], []
        for alternative in alternatives:
            row = (alternative['target_id'], alternative.get('rank') or '—', alternative['id'].replace('-', ' '),
                   len(alternative.get('supporting_fact_ids', [])), alternative['evidence_status'].replace('-', ' '))
            (supported if alternative.get('supporting_fact_ids') else unsupported).append(row)
        headers = ('Target', 'Evidence order', 'Alternative', 'Supporting observations', 'Evidence status')
        sections += ('<section class="card"><h2>Alternatives to investigate</h2>'
            '<p>Ordered by the number of supporting observations. This order does not measure causal confidence. '
            'The structured report retains counterevidence and required independent controls.</p>')
        sections += table(headers, supported) if supported else '<p>No alternative has direct supporting observations.</p>'
        if unsupported:
            sections += f'<details><summary>{len(unsupported)} alternatives without direct support</summary>{table(headers, unsupported)}</details>'
        sections += '</section>'
    retained = ''.join('<tr>' + ''.join(f'<td>{_h(value)}</td>' for value in (
        failure['input'], failure['target_id'], failure['fact'].replace('-', ' '),
        failure.get('freshness', 'not-assessed').replace('-', ' '))) + '</tr>'
        for failure in result.get('retained_failures', []))
    if retained:
        sections += ('<section class="card"><h2>Retained failure evidence</h2>'
            '<p>A historical failure remains evidence of that recorded window, even when it is too old to describe current health.</p>'
            '<div class="table-scroll"><table><thead><tr><th>Input</th><th>Target</th><th>Observation</th><th>Freshness</th>'
            f'</tr></thead><tbody>{retained}</tbody></table></div></section>')
    states = ''.join(f'<li>{_h(name.title())}: {_h(value.get("state"))}</li>' for name, value in result.get('inputs', {}).items())
    sections += '<section class="card"><h2>Comparison coverage</h2><ul>' + states + '</ul>'
    sections += '<p>Input validity checks structure and hashes; it does not authenticate the source. '
    sections += 'Application HTTP health was not observed. Causality remains unproven.</p></section>'
    return sections


def _recovery_html(result):
    if result.get('kind') != 'runtime-recovery':
        return ''
    states = {'recovered': 'Observed match', 'not-recovered': 'Observed mismatch', 'unknown': 'Unknown'}
    conditions = result.get('conditions', [])
    def table(items):
        labels = ('Condition', 'Phase', 'Target', 'Observation', 'Source origin')
        rows = ''.join('<tr>' + ''.join('<td data-label="' + label + '">' + _h(value) + '</td>' for label, value in zip(labels, (
            item['id'].replace('-', ' '), item['phase'], item.get('target_id', 'Delivery chain'),
            states[item['observed_state']], 'Verified' if item['authenticated'] else 'Unverified'))) + '</tr>'
            for item in items)
        return ('<div class="table-scroll recovery-table"><table><thead><tr><th>Condition</th><th>Phase</th><th>Target</th>'
                '<th>Observation</th><th>Source origin</th></tr></thead><tbody>' + rows + '</tbody></table></div>')
    exceptions = [item for item in conditions if item['observed_state'] != 'recovered']
    phase_order = {'recovery': 0, 'chain': 1, 'all': 2, 'fault': 3, 'baseline': 4}
    condition_order = {name: index for index, name in enumerate((
        'mission-health', 'reference-health', 'functional-telemetry', 'post-delivery-sample-freshness',
        'workload-readiness', 'controller-convergence'))}
    matches = sorted((item for item in conditions if item['observed_state'] == 'recovered'),
                     key=lambda item: (phase_order[item['phase']], condition_order.get(item['id'], 99)))
    sections = ('<section class="card"><h2>Recovery conditions</h2><p>Each observation is checked against the '
                'approved contract. An observed match does not authenticate its source. '
                'A matching fault condition means the expected defect was observed.</p>')
    if exceptions:
        sections += f'<h3>{len(exceptions)} conditions need attention</h3>' + table(exceptions[:12])
        if len(exceptions) > 12:
            sections += f'<details><summary>{len(exceptions)-12} more conditions needing attention</summary>{table(exceptions[12:])}</details>'
    if matches:
        sections += f'<h3>{len(matches)} observed matches</h3>'
        visible = [] if exceptions else matches[:8]
        sections += table(visible) if visible else ''
        remaining = matches[len(visible):]
        if remaining:
            sections += f'<details><summary>{len(remaining)} more observed matches</summary>{table(remaining)}</details>'
    if not conditions:
        sections += '<p>No recovery condition could be evaluated.</p>'
    sections += '</section><section class="card"><h2>Source verification</h2><p>'
    sections += ('The configured provenance adapter authenticated the consumed input bytes.'
                 if result.get('provenance', {}).get('verified') else
                 'The origin of the supplied evidence has not been independently authenticated. '
                 'Content hashes alone cannot establish native execution, Git delivery or live recovery.')
    sections += '</p><p>Causality remains unproven. The structured evidence preserves exact input hashes, '
    sections += 'condition pointers and retained failure observations.</p></section>'
    retained = result.get('retained_failures', [])
    if retained:
        sections += ('<section class="card"><h2>Retained failure evidence</h2><p>These observations describe '
                     'the earlier fault window. Later recovery checks do not erase them.</p>'
                     f'<details><summary>{len(retained)} historical failure observations</summary>'
                     + table(retained) + '</details></section>')
    return sections


def render_html(result, structured_name):
    recovery = result.get('kind') == 'runtime-recovery'
    window = result.get('window') or {}
    intent = result.get('context_snapshot', {}).get('intent', {})
    identities = [('Environment', intent.get('environment')), ('Run', intent.get('run_id')),
                  ('Phase', intent.get('phase')), ('Observed at', result.get('observed_at')),
                  ('Next eligible collection', window.get('next_eligible_at')), ('Window expires', window.get('expires_at'))]
    context = ''.join(f'<div><dt>{_h(key)}</dt><dd>{_h(value)}</dd></div>' for key, value in identities if value is not None)
    rows = ''.join('<tr>' + ''.join(f'<td>{_h(value)}</td>' for value in row) + '</tr>' for row in _rows(result))
    gaps = result.get('gaps', [])
    next_step = ('Resume with the saved context and pending collection between the times below. '
                 'An early resume performs no reads; an expired window requires a new collection.'
                 if result.get('stage') == 'pending' else
                 'Review the observed changes and required gaps before proposing a correction or evaluating recovery.')
    if recovery:
        next_step = ('Compare the independent health, telemetry, image and delivery observations below. '
                     'Required evidence gaps keep the overall recovery result unknown.')
    recovery_state = result.get('recovery', 'unknown') if recovery else 'unknown'
    recovery_label = {'verified': 'VERIFIED', 'not-recovered': 'NOT VERIFIED', 'unknown': 'UNKNOWN'}[recovery_state]
    recovery_class = {'verified': 'pass', 'not-recovered': 'fail', 'unknown': 'unknown'}[recovery_state]
    failure_value = (str(sum(item['observed_state'] == 'recovered' for item in result.get('conditions', [])))
                     + ' / ' + str(len(result.get('conditions', [])))) if recovery else _failure_label(result)
    controlled = (result.get('execution_mode') == 'controlled-test'
                  or any(gap.get('code') == 'controlled-test-evidence' for gap in gaps))
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
        '<meta name="color-scheme" content="light dark">'
        f'<title>DevSecOps · {_h(_title(result))}</title><style>{_CSS}'
        '.stats{grid-template-columns:repeat(3,minmax(0,1fr))}.stat strong{font-size:1.1rem}'
        '.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left}'
        'td,th{padding:12px;border-bottom:1px solid var(--line)}td{overflow-wrap:anywhere}'
        'a{color:var(--brand);text-underline-offset:3px}.artifacts{display:flex;flex-wrap:wrap;gap:18px}'
        '.card h2{margin-bottom:18px}li{margin-bottom:8px}'
        '.evidence-note{padding:16px 24px;display:flex;flex-wrap:wrap;gap:8px 16px}'
        '@media(max-width:620px){.stats{grid-template-columns:1fr}.table-scroll td{min-width:120px}}'
        '@media(max-width:800px){'
        '.recovery-table table,.recovery-table tbody,.recovery-table tr{display:block}'
        '.recovery-table thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}'
        '.recovery-table tr{border:1px solid var(--line);border-radius:12px;margin:12px 0;padding:8px}'
        '.recovery-table td{display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px;min-width:0;padding:8px;border:0}'
        '.recovery-table td::before{content:attr(data-label);font-weight:600;color:var(--muted)}}'
        '</style></head><body><main><header class="brand"><span class="mark" aria-hidden="true">✓</span>'
        'DevSecOps<span class="edition">Runtime evidence</span></header>'
        f'<section class="hero"><div class="eyebrow">Scoped reads · Recorded evidence</div><h1>{_h(_title(result))}</h1>'
        f'<div class="statusline"><span class="badge {_h(recovery_class)}">RECOVERY {_h(recovery_label)}</span>'
        f'<span class="badge">{_h(result.get("execution_status", "partial"))}</span></div>'
        f'<p class="subtitle">{_h(next_step)}</p></section>'
        '<div class="stats"><div class="stat"><strong>'
        + _h(failure_value)
        + '</strong><span>' + ('Observed matches' if recovery else 'Failure evidence') + '</span></div><div class="stat"><strong>'
        + str(len(gaps)) + '</strong><span>Evidence gaps</span></div>'
        '<div class="stat"><strong>' + (_h(recovery_state.replace('-', ' ').title()) if recovery else 'Not evaluated')
        + '</strong><span>Functional recovery</span></div></div>'
        + ('<aside class="card evidence-note"><strong>Controlled test evidence</strong><span>A testing client or clock override was used. '
           'These observations are not live qualification.</span></aside>' if controlled else '')
        + (f'<section class="card"><h2>Observation context</h2><dl class="context">{context}</dl></section>' if context and not recovery else '')
        + ('<section class="card"><h2>Latest workload observations</h2><p>Pod readiness is separate from application HTTP health.</p>'
           '<div class="table-scroll"><table><thead><tr><th>Target</th><th>Argo status</th><th>Workload readiness</th><th>Telemetry</th>'
           f'</tr></thead><tbody>{rows}</tbody></table></div></section>' if rows else '')
        + _diagnosis_html(result)
        + _recovery_html(result)
        + (f'<section class="card"><h2>Observation context</h2><dl class="context">{context}</dl></section>' if context and recovery else '')
        + _gaps_html(gaps)
        + '<section class="card"><h2>Evidence artifacts</h2><nav class="artifacts" aria-label="Evidence artifacts">'
        f'<a href="{_h(structured_name)}">Structured evidence</a><a href="runtime-manifest.json">Artifact manifest</a>'
        '</nav><p>Hashes detect changed content. An independent trusted journal must authenticate the inputs.</p></section>'
        '<footer class="foot"><span>Generated locally · No external assets or scripts</span>'
        + ('<span>Recovery verification does not authorize a deployment.</span>' if recovery else
           '<span>Collection and diagnosis do not authorize delivery or establish recovery.</span>')
        + '</footer></main></body></html>'
    )


def render_markdown(result, structured_name):
    recovery = result.get('kind') == 'runtime-recovery'
    lines = ['# ' + _title(result), '', '**Recovery: ' + _md(result.get('recovery', 'unknown')) + '.** Execution: ' + _md(result.get('execution_status')), '',
             'Failure evidence: ' + _failure_label(result), '',
             'Pod readiness is separate from application HTTP health.', '']
    if recovery:
        lines += ['| Condition | Phase | Target | Observation | Source origin |', '| --- | --- | --- | --- | --- |']
        states = {'recovered': 'Observed match', 'not-recovered': 'Observed mismatch', 'unknown': 'Unknown'}
        for item in result.get('conditions', []):
            lines.append('| ' + ' | '.join(_md(value) for value in (
                item['id'].replace('-', ' '), item['phase'], item.get('target_id', 'Delivery chain'),
                states[item['observed_state']], 'Verified' if item['authenticated'] else 'Unverified')) + ' |')
        lines += ['', 'An observed match does not authenticate its source. A matching fault condition means the expected defect was observed.',
                  '', 'Causality remains unproven.', '']
    rows = _rows(result)
    if rows:
        lines += ['| Target | Argo status | Workload readiness | Telemetry |', '| --- | --- | --- | --- |']
        lines += ['| ' + ' | '.join(_md(value) for value in row) + ' |' for row in rows]
        lines.append('')
    if result.get('kind') == 'runtime-diagnosis':
        lines += ['Comparison: ' + _md(result.get('comparison_status')), '',
                  'Causality is unproven. Application HTTP health was not observed.', '']
        facts = result.get('observations', [])
        if facts:
            lines += ['| Target | Observation | Baseline | Fault | Comparison |', '| --- | --- | --- | --- | --- |']
            lines += ['| ' + ' | '.join(_md(value) for value in (
                fact['target_id'], fact['kind'].replace('-', ' '), _value(fact.get('baseline')),
                _value(fact.get('fault')), fact['comparison'])) + ' |' for fact in facts]
            lines.append('')
        lines += ['Alternatives are ordered by supporting observations, not causal confidence.', '']
        for alternative in result.get('alternatives', []):
            lines.append('- ' + _md(alternative['target_id']) + ': ' + _md(alternative['id']) +
                         ' · ' + str(len(alternative.get('supporting_fact_ids', []))) + ' supporting observations · ' +
                         _md(alternative['evidence_status']))
        lines.append('')
        for failure in result.get('retained_failures', []):
            lines.append('- Retained ' + _md(failure['input']) + ' / ' + _md(failure['target_id']) + ': ' +
                         _md(failure['fact']) + ' · ' + _md(failure.get('freshness', 'not-assessed')))
        lines.append('')
    lines += ['Required evidence gaps:', '']
    lines += ['- ' + _md(gap.get('code')) for gap in result.get('gaps', [])]
    lines += ['', f'[Structured evidence]({structured_name}) · [Artifact manifest](runtime-manifest.json)', '',
              'Input hashes are integrity evidence, not independent authentication.', '']
    return '\n'.join(lines)
