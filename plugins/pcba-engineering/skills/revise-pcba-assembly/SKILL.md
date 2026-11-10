---
name: revise-pcba-assembly
description: Preview and apply a bounded, revision-bound PCBA placement or population correction using explicit authoritative geometry, then regenerate and recheck the assembly package.
---

# Revise a PCBA assembly package

Resolve the installed plugin root from this skill location. Use `node <installed-plugin-root>/scripts/pcba.mjs` for the real workflow, even outside the plugin repository. Read `../../references/workflow.md` for the data contract.

1. Run `status --project <directory>` to load the current revision, variant and unresolved findings. Never rely on remembered chat state.
2. Establish the authorized scope and authoritative replacement source. Existing user authorization persists; do not ask again for a concrete correction the user already requested. If evidence or scope is missing, request that information before applying dependent changes.
3. Generate an exact preview using one form:
   - `plan-change --project <directory> --use-authoritative --reason <specific reason>` uses the imported authoritative placement snapshot.
   - `plan-change --project <directory> --placements <complete.csv-or-json> --units mm --bottom-view top-view --reason <specific reason>` replaces the placement export using supplied geometry. JSON accepts an array or an object with a placements array.
   - `plan-change --project <directory> --population <reference-to-boolean.json> --reason <specific reason>` changes only the specified population flags; newly populated parts require authoritative coordinates.
4. Present the exact reference-level diff and before/after findings. Verify it matches the requested correction. Do not silently change part identities, electrical design, quantity or variant to make checks pass.
5. Apply the returned plan path with `apply-change --project <directory> --plan <plan.json>`. The CLI rejects tampered or stale plans. A retry of the same applied plan is idempotent. If stale, inspect the current revision and prepare a fresh preview; never edit the plan manually.
6. Run `check --project <directory> --expect <new-revision>`. Read the regenerated guide and unchanged checking rules. Unresolved material and engineering evidence remain visible. Continue to `$prepare-pcba-handoff` when document consistency passes and a handoff is requested.

Project revisions and existing delivery directories are immutable. Do not edit their JSON or generated reports directly. View filters in the HTML are not population changes. Keep source data, scenario changes and actual physical outcomes distinct.
