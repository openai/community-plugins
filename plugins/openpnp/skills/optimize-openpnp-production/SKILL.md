---
name: optimize-openpnp-production
description: Compare measured OpenPnP throughput and placement quality and evaluate one bounded planner or parameter change.
---

# Optimize OpenPnP Production

Read [the runtime/control contract](../../references/control-contract.md) and, for this workflow, [optimize openpnp production guidance](../../references/performance-experiments.md). Use only tool operations and recipes reported as supported by the current runtime. Tool presence alone does not qualify physical hardware.

## Workflow

1. Inspect capabilities and collect the baseline `openpnp_export_run_report`, job/configuration revisions, part/package mix, inspection coverage, retries, material loss, and operator interventions.
2. Identify one measurable bottleneck from evidence, such as travel, tip changes, settling, retries, or vision time. Select a supported parameter/planner change within qualified limits.
3. Prepare `openpnp_plan_configuration` and review shared impact plus invalidated calibration/qualification. Preserve a supported backup/baseline; apply only the scoped experimental change.
4. Use `openpnp_prepare_job` and `openpnp_validate_job` as needed for a comparable test, then supported job execution under its own scope. Compare equal workloads and independently recorded inspection, not just native completion.
5. Retain the change only if its acceptance criteria pass; otherwise restore the supported baseline and revalidate affected dependencies. Report raw denominators and measurement limitations.

## Completion and recovery

Return baseline/candidate revisions, measured cycle-time and quality/material results, and the evidence-backed retain/revert decision.

Stop expanding speed, retries, or vision tolerances beyond the profile; a faster run with worse yield or missing inspection is not a demonstrated improvement. If a dependent tool or capability is unavailable, complete the supported inspection or preparation and name the exact remaining gap; do not imitate execution or use raw commands as a fallback.
