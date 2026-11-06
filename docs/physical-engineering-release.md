# Physical Engineering release review

`0.2.0-preview.1` separates the reusable engineering workflow from its drone demo.
The installable package is `plugins/physical-engineering/`; media, presentation
dependencies and repository tests stay outside that directory.

## What is reusable

The same study lifecycle handles initialization, accepted requirement revisions,
source snapshots, independent export readback, retained failures, handoff and
source-based rebuild. `mechanical-part` accepts custom single-solid source and
validated measurement requirements. The sensor plate supplies a runnable starter;
CAD tests also use a separately authored spacer. The assembly-specific
`drone-module` profile preserves the existing demonstration.

The generic checker supports dimensions/position, occupied envelope, solid mass,
volume ranges, full-height Z-axis bores and box keepouts. It compares actual
STEP/STL exports. New assembly classes and unsupported engineering properties
need appropriate independent measurement methods; this is not a universal
structural, manufacturing or assembly verifier.

## Review paths

- [Plugin entry point](../plugins/physical-engineering/README.md)
- [Custom requirements and model contract](../plugins/physical-engineering/references/custom-part.md)
- [Single master test command and coverage](../tests/physical-engineering/README.md)
- [Video, captions, poster and source provenance](media/physical-engineering/README.md)
- [Optional drone session runbook](physical-engineering-demo-runbook.md)

The video is bound to the earlier drone implementation and presentation commits
listed in its manifest. It remains a worked example, not test evidence for the
new generic profile. No old recording has been relabeled as a newer run.

## Automated release checks

```sh
export PE_RUNTIME="/absolute/path/to/pinned-runtime"
export PE_PYTHON="$PE_RUNTIME/bin/python"
npm run validate:marketplace
npm run test:physical-engineering
npm run test:marketplace:runner
npm run validate
```

The marketplace dispatcher calls the same physical-engineering master. Its one
plugin CI job runs supported Node versions sequentially, then audits pinned CAD,
narration and presentation dependencies. Missing runtime or failed audit access
is an error; no external integration is claimed from a skip.

The core tests cover a copied-package custom-part lifecycle, revised requirements
against unchanged geometry, failed measurements, a corrected build, handoff and
delivered-source rebuild in separate CAD state. Security checks exercise stale
artifacts, path escapes, preserved requirement revisions, incomplete PASS reports
and interrupted handoff publication. Geometry checks include changed features
with equal summary measurements and obstructed bores.

A handoff is fully staged and hash-checked before promotion. Failed preparation
keeps the prior handoff; a failed rename attempts restoration. A process or host
crash during the rename interval can leave a complete prior handoff under its
`handoff-previous-*` name. Local hashes detect inconsistency; they are not
signatures or protection from arbitrary executable model source.

## Publication boundary

This PR prepares a community marketplace preview. Required CI, CLA and maintainer
approval still gate merge under the repository's [publication policy](review-and-publish.md).
The exact PR checks are the current validation record. Installed-Codex discovery,
viewer behavior on each supported host and physical qualification need their own
evidence; a direct CLI test does not establish those outcomes.

The package adds no MCP server, hosted service or account. It executes reviewed
local Python through separately installed Text-to-CAD and downloads pinned public
dependencies during setup. No demo media or neural narration runtime is required
for normal plugin use. The [data-flow and permission description](../plugins/physical-engineering/README.md#data-flow-and-scope)
and dependency notices cover this behavior.

Publishing this community marketplace entry is separate from official Plugins
Directory submission. The latter would also require its applicable publisher,
listing/policy URLs and portal review; this PR neither submits nor attests to it.
