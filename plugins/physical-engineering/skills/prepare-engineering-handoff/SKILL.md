---
name: prepare-engineering-handoff
description: Regenerate a study's checked CAD, source, measurements and BOM, then rebuild the delivered package in a separate destination and compare the results.
---

# Prepare an engineering handoff

Use the installed plugin's `scripts/engineering.py` and the selected study.
Read [compatibility](../../references/compatibility.md) for the profile's scope.

1. Resume and confirm active requirements and the selected run. Retain failed
   evidence; failed/unknown required checks block handoff.
2. Run `handoff STUDY`. It rebuilds source and rechecks actual STEP/STL exports
   before packaging requirements, measurements, BOM and rebuild instructions.
   The drone profile also generates and checks its dimensioned PDF and assembly
   notes. The generic profile does not promise an engineering drawing.
3. Inspect the delivered geometry and any PDF. Trace each changed requirement to
   its measurement and exact file. Report solid-density mass separately from
   purchased components and unresolved physical requirements.
4. Execute `sh HANDOFF/reproduce.sh NEW_DESTINATION` with `PE_RUNTIME` set, or
   use `reproduce HANDOFF DEST`. Use a separate runtime too when qualifying a
   cold installation. Inspect received executable source before running it;
   hashes detect changed files but do not establish trust in the author.
5. Compare the new measurements within recorded tolerances and retain the
   receipt. Report what ran and any differences. Preserve original studies;
   never stop another study's CAD server.

State missing validation honestly. An automated rebuild does not establish a
physical assembly or a successful installed-Codex session. Demo recording and
facilitator exercises are optional companion activities, not prerequisites for
using this plugin on another project.

Before sharing evidence, inspect for private inputs, personal paths, credentials
and token-bearing viewer URLs. Publish or send only within the user's authorized
scope. A local engineering handoff and a plugin release are separate actions.
