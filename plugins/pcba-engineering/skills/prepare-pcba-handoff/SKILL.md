---
name: prepare-pcba-handoff
description: Export a consistent PCBA project into an immutable offline assembly packet and independently re-read its BOM, placements, provenance and artifact hashes before delivery.
---

# Prepare a verified assembly handoff

Resolve the installed plugin root from this skill location. Use the bundled `node <installed-plugin-root>/scripts/pcba.mjs`; do not report completion from a prior chat summary or generator pass flag.

1. Run `status --project <directory>` and `check --project <directory>`. If document consistency fails, retain the review artifacts and identify the blocking references. Use `$revise-pcba-assembly` for an authorized correction.
2. Run `export --project <directory> --output <new-delivery-directory>`. Export writes a candidate packet, independently verifies it, and only then publishes that directory. Existing handoffs are never overwritten. Use a distinct delivery directory for a new revision.
3. Separately run `verify-handoff --directory <delivery-directory>` against the delivered files. The verifier checks the complete manifest, hashes, independently parsed CSV rows, source snapshot, fresh findings, brief and revision history.
4. Open the delivered `assembly-guide.html` locally and inspect a meaningful reference against `population.csv`, `placements.csv` and `change-record.json`. Confirm the guide works without a server or network. Link the guide, manifest and handoff directory.
5. Report the actual revision and verification result, remaining source findings, and separate material/electrical/manufacturing evidence states. Use “document consistency verified” for a passing result. A digital package is not a production build, electrical approval, stock check or manufacturing authorization.

The packet includes HTML/SVG, CSV, JSON and Markdown. PDF and native CAD generation are not prerequisites. When handing off a third-party board, preserve the source attribution and hardware license obligations supplied in its source manifest.
