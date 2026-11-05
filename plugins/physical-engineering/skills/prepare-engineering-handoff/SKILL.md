---
name: prepare-engineering-handoff
description: Regenerate and check the selected drone-module source, STEP, print files, drawing, BOM and notes, then execute a clean reproduction with independent CAD state.
---

# Prepare an engineering handoff

Use the installed plugin's `scripts/engineering.py` and the selected study.
Read [the reusable session guide](../../references/session-guide.md).

1. Resume the study and confirm its active requirements and selected run.
2. Verify the selected geometry. Required failed/unknown checks block a passing
   handoff; retain their evidence and repair the design in a new run.
3. Run `handoff STUDY`. Explicitly regenerate assembly STEP, individual print
   files, dimensioned PDF, BOM and assembly notes from the same selection.
4. Re-read delivered geometry and inspect the PDF. Trace the changed requirement
   to its measurement and exact output file. A successful build does not update
   a previously exported file.
5. Include editable source, frozen requirements, checker/version, dependencies,
   run manifest and reproduction command. Report the solid-material mass
   estimate separately from purchased parts.
6. Execute the delivered command on the selected clean destination with
   independent CAD state. The CLI also accepts `reproduce HANDOFF DEST`.
   For cold-runtime qualification, prepare a separate runtime as well.
   Preserve original studies and do not stop unrelated CAD servers.
7. Compare geometric measurements within their recorded tolerances; STEP bytes
   may differ because of metadata. Preserve the new reproduction result.

State what actually ran. Missing clean reproduction, installed-Codex evidence,
independent facilitator acceptance or recording remains pending; do not replace
it with an earlier receipt.

Before a public evidence/media handoff, inspect for private input, credentials,
personal paths and token-bearing CAD viewer URLs. Do not publish or send files
unless the user authorized that action. Release approval is separate from
producing a local fabrication package.
