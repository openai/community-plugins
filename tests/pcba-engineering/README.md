# PCBA qualification

From a checkout, use Node 22.19 or newer and Python 3.12 or newer. No npm dependencies need to be installed for this plugin's suite:

```sh
npm run test:pcba-engineering
```

This is the single root master command. It validates the self-contained package, runs every unit, integration and security test, then checks pinned dataset provenance. `npm run test:marketplace` discovers this same command from the catalog. The `PCBA Engineering` workflow runs it sequentially on Node 22.19.0, 24 and 26 in one Ubuntu job with Python 3.12. Other operating systems and actual installed-Codex behavior require their own recorded qualification; a portable implementation or a passing Ubuntu job does not establish those results.

- Unit: explicit schema/CSV mapping, population and geometry checks, inert source strings, report rendering, and optional native adapter protocol behavior.
- Integration: public sensor, training-board and BMS fixtures, exact one- and two-reference corrections, CSV source-line trace separation, immutable revisions, fresh-process resume, stale-plan rejection, pointer-write recovery and independently parsed handoff files.
- Security: changed/rehashed plans, CSV and visual/Markdown substitutions, manifest omissions, path traversal, project/delivery symlinks and active locks.
- Cold package: copy the complete installable directory to an arbitrary path, deny repository reads, child tools and network access, then run the sensor and independent training-board workflows. This proves standalone packaging on the test host; it does not prove Codex discovery.
- Dataset: verify pinned source bytes and license files, regenerate all three boards' canonical geometry deterministically, and compare Opulo positions with its independent published export. BMS preparation runs in a temporary copy and byte-compares all eight derived fixture files, including the preparation receipt, with the committed versions. Missing native KiCad exports are reported as explicit skips.

## Actual installed Codex

Authenticated host qualification is separate from the automated master. Install the candidate plugin through its marketplace, then use a fresh Codex chat and a project directory outside the source checkout. Record the installed version and package hashes before qualifying these cases:

1. Discover all three skills and run the installed `doctor` command.
2. Import supplied engineering inputs, inspect actual findings, preview and apply the authorized bounded correction, recheck, export, and separately verify the delivered packet.
3. In another fresh chat, resume the project from disk and independently verify the existing handoff without reimporting.
4. Reject a stale plan while retaining the current revision; confirm a missing optional KiCad executable leaves the supplied-export workflow usable.
5. Complete the same workflow on an independent board with a different population and package size.

Retain prompts, actual tool results, package identity and project/packet evidence. Inspect the offline HTML guide and trace a changed reference through the CSVs and change record. A successful chat or subprocess exit alone is not acceptance. Preserve unsuccessful runs and label rendered event capture as replay. Do not commit account state, personal paths or raw private chat logs.

## Native and media evidence

The optional KiCad tests exercise an explicit fake executable protocol and are labeled accordingly. Real KiCad qualification requires actual exports, receipts and `python3 tests/pcba-engineering/support/dataset-verify.py --require-cli`; a protocol pass is never a native-host pass. The supplied-export workflow does not need KiCad.

Media production and playback review are separate from the automated plugin tests. A decoder pass, screenshot or subtitle presence does not establish a well-designed movie or intelligible narration. Record actual visual/audio review, provenance and known limitations in the demo delivery report.

The current BMS scenario is documented in `docs/pcba-engineering-bms-demo.md`.
Its source files are hash-checked by `integration/bms.test.mjs`, which also
imports the real mapped CSVs, checks the exact two-part diff, preserves unknown
source information, and verifies the independent handoff. The master dataset
check additionally regenerates the BMS inputs from the pinned source revisions;
this is source-reproduction evidence, not an independent native KiCad export.
