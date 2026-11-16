from devsecops.correction_presentation import render_correction_html, render_correction_markdown


def test_static_pass_cannot_present_a_failed_correction_as_passed():
    value = {'decision': 'fail', 'execution_status': 'complete', 'static_review_decision': 'pass',
             'has_rendered_change': False, 'delivery_status': 'not_performed', 'runtime_recovery': 'not_evaluated'}
    page = render_correction_html(value)
    assert '<h1>Patch needs revision</h1>' in page
    assert 'The patch does not change rendered behavior.' in page
    assert 'Patch passes local validation' not in page
    assert 'not performed' in page and 'not evaluated' in page
    assert 'Patch needs revision' in render_correction_markdown(value)


def test_malformed_or_missing_decision_does_not_default_to_pass():
    assert '<h1>Patch validation needs more evidence</h1>' in render_correction_html({})
    assert '<h1>Patch validation needs more evidence</h1>' in render_correction_html({'decision': 'success'})


def test_report_escapes_evidence_and_loads_no_external_scripts_or_assets():
    sentinel = '<script>alert("bad")</script>'
    value = {'decision': 'unknown', 'contract_id': sentinel, 'changed_files': [sentinel],
             'gaps': [{'message': sentinel}], 'source': {'base_commit': sentinel}}
    page = render_correction_html(value)
    assert sentinel not in page and '&lt;script&gt;' in page
    assert '<script' not in page and '<img' not in page
    assert 'default-src' in page and 'href="correction-manifest.json"' in page
