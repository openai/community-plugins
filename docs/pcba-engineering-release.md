# PCBA Engineering release candidate

PCBA Engineering 0.1.3 is a standalone Codex plugin for reviewing, revising and
verifying supplied assembly data. Its three skills use a bundled local Node
CLI; the installable directory contains no demonstration board, OpenPnP,
machine controller, npm dependencies or media tooling.

[Watch the 2:27 Codex demo](media/pcba-engineering/README.md) ·
[Replay the BMS scenario](pcba-engineering-bms-demo.md) ·
[Plugin usage and input contract](../plugins/pcba-engineering/README.md) ·
[Test coverage](../tests/pcba-engineering/README.md)

## Reuse contract

Use canonical JSON or explicitly mapped BOM and placement CSV from any exporter.
Declare units, origin, rotation, bottom-side convention, revision, variant and
quantity. The same checks handle SMT, THT, mechanical and fiducial categories,
DNP population and unknown part identities. Complete authoritative geometry is
optional for review and required for source-backed coordinate restoration.
Sensor, training-board and BMS fixtures exercise independent board layouts and
population patterns outside the installed plugin.

The workflow is import → check → preview → apply → recheck → export → verify.
Revisions and plans are immutable. A preview binds the exact base and candidate;
stale or altered plans fail. Exported CSV is parsed independently and checked
against the saved revision. Offline HTML/SVG, readiness, source and change
records travel with the packet. Hashes prove internal consistency, not origin
or engineering approval. Handoffs pin their verifier version; preserve that
version when archiving a packet.

## Install and review

Review the candidate from a checkout using the supported marketplace workflow
in the [plugin guide](../plugins/pcba-engineering/README.md). After this PR is
approved and merged, the community marketplace can install it with:

```sh
codex plugin marketplace add openai/community-plugins --ref main
codex plugin add pcba-engineering@community-plugins
```

An existing marketplace registration should be reused or explicitly refreshed,
not silently replaced. Node 22.19 or newer is required. Inputs and outputs stay
local. The optional KiCad adapter only invokes an explicitly installed local
CLI; no KiCad install is required for supplied exports.

From a checkout with Node and Python 3.12 or newer:

```sh
npm run test:pcba-engineering
npm run validate
npm run test:marketplace
```

The canonical PCBA master covers package structure, domain/CSV handling,
immutable revisions, independent handoff readback, adversarial input and cold
copied-package execution with checkout/network access denied. It also
regenerates all three datasets from pinned public source bytes. One workflow
runs the same master sequentially on Node 22.19, 24 and 26. The repository's
marketplace dispatcher invokes that same command.

## Installed candidate verification

The [0.1.3 qualification receipt](pcba-engineering-qualification.json) binds all
22 installed files to the source package. A fresh Codex run executed 17 real
CLI calls outside the checkout: BMS import, check, two-reference preview/apply,
recheck, export, independent verification and resume, followed by a separate
training-board handoff. Parent-side readback independently reverified both
11-artifact packets, all 145 unchanged placements and unchanged non-placement
input. This is separate from the earlier movie recording.

## Demonstration and qualification boundaries

The movie records an actual installed **0.1.2** run: a Libre Solar BMS v0.2 BOM
with v0.1.1 placements, a two-reference D11/D12 correction, and 11 independently
verified artifacts. The packet and its original verifier accompany the film.
The public edit masks personal paths and unrelated app chrome; it does not
recreate chat or tool output. The native SVG preview failed during capture,
so the film pairs real chat with separately labeled saved board visuals.
The exported interactive HTML guide passed a separate in-app-browser check.

Version **0.1.3** additionally rejects comma/semicolon inside a single canonical
reference before import; grouped CSV BOM references remain supported. It also
uses the canonical community repository URL. The earlier film is evidence for
its recorded version, not a new recording of this patch.

Document consistency is distinct from material availability, electrical,
thermal, manufacturing and flight qualification. The BMS retains its unresolved
R59 population and unknown part identities. Native KiCad host qualification and
human listening/presentation approval are not claimed. Core tests run locally;
hosted checks and maintainer approval are tracked in the PR. Publishing to the
official Plugins Directory is a separate process.

Code is Apache-2.0. Public hardware fixtures retain their upstream attribution
and license terms; the BMS derivative data and visuals are CC BY-SA 4.0. See
[media attribution](media/pcba-engineering/ATTRIBUTION.md) and the fixture manifests.
