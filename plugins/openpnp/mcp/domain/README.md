# Deterministic OpenPnP domain API

These ES modules use Node builtins and perform no I/O, machine commands or network calls. They return new JSON values and never modify inputs. They provide data validation and evidence accounting; the authenticated native bridge must enforce hardware permissions, durable journaling, actor provenance and physical qualification. A content hash is an integrity receipt, not a signature or proof that a physical observation occurred.

## Imports

```js
import { prepareJob, validateJob } from './index.mjs';
const job = prepareJob({
  format: 'reference-csv', content: placementCsv, bomContent: bomCsv,
  units: 'mm', widthMm: 40, heightMm: 30, variant: 'production',
});
const validation = validateJob(job, { mode: 'offline' });
```

- `prepareJob({format,content,bomContent?,mapping?,bomMapping?,units?,jobId?,boardId?,widthMm?,heightMm?,side?,variant?,kicadBottomConvention?})` supports `reference-csv`, `kicad-pos`, and `canonical-json`. Output is canonical schema 1, `mm`, `openpnp-top-view`, source hashes and an immutable content revision.
- `parseCsv(content)` returns `{row,cells}[]`, preserving physical source lines, quoting and commas/newlines in fields. UTF-8 strings only; comma delimiter, decimal point, max 4 MiB, 100,000 rows, 256 columns and 8,192 characters per field. Ambiguous encoding, malformed quoting and column counts fail.
- `parseBom(content,{mapping?})` returns `{records,source}`. Required columns are `Refs,PartId`; optional `Package,Value,Height (mm),DNP,Variants`. Grouped refs use spaces, commas or semicolons (quote commas in CSV). Variant membership uses `;` or `|`. Height must be positive; missing is `null`.
- Reference CSV requires `Ref,Value,Package,X,Y,Rotation,Side`. Common native aliases are supported (`RefDes`, `Designator`, `CompValue`, `Footprint`, `RefX`, `SymX`, `Rot`, `Rotate`, `Layer`, etc.). `mapping` selects exact headers for semantic keys: `ref,value,packageId,partId,x,y,rotation,side,heightMm,dnp,variants,type`. Unknown columns survive in `metadata`. This is a documented strict subset, not every vendor CSV dialect.
- Coordinates need explicit per-cell suffixes, `(mm)`, `(mil)` or `(in)` headers, or `units`. Angles use degrees, are normalized to `[-180,180)`, and represent OpenPnP's side-specific placement angle. No automatic datum offset, Y inversion, package rotation correction or polarity inference occurs. DNP rows remain visible with `enabled:false`.
- KiCad `.pos` records have seven whitespace-delimited fields `Ref Val Package PosX PosY Rot Side`, optionally quoted. The parser recognizes `## Unit = mm, Angle = deg.` and `# Units = mm`. A bottom record **requires** `kicadBottomConvention`: `negated-x` applies `x=-x, angle=180-angle`; `top-view` declares XY and angle already canonical and preserves them. It does not guess export settings from negative coordinates. This distinction follows the [pinned native importer](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/gui/importer/KicadPosImporter.java).
- BOM and placement references reconcile one-for-one, including DNP records. Missing, extra or conflicting BOM references reject the entire import; scope a mixed SMT/through-hole BOM explicitly before import. Variant-specific rows require a selected variant. Part IDs default deterministically from package/value when no explicit identity exists.
- `importCanonicalJob(content)` and `exportCanonicalJob(job)` validate the supported canonical structure and preserve unknown JSON fields. Import recomputes the content revision. Native `.board.xml`, `.panel.xml`, `.job.xml` imports return `unsupported_format` here; no generic regex XML parser exists. A separately qualified native adapter is required.

Failures throw `DomainError` with `{code,message,details}`. Atomic import failures have `details.diagnostics[]` including severity, code, original row and semantic column when available. Inputs return no partial job. `validateJob` instead aggregates findings into `{valid,mode,productionQualified:false,issues,placements,counts}`.

## Canonical definitions, instances and previews

Jobs contain `parts`, reusable `boards` (`widthMm,heightMm,placements`), reusable `panels` (`widthMm,heightMm,children`) and physical-side `instances`. A board or panel instance is `{id,kind:'board'|'panel',definitionId,x,y,z,rotation,side:'top'|'bottom',enabled}`. Placements have `{ref,partId,packageId,value,x,y,z,rotation,side,heightMm,enabled,type:'placement'|'fiducial'}`. Unknown board dimensions and part heights are `null`, not zero. Prepare defaults to one top instance named `load-1`; this design instance ID is distinct from a registered physical `boardLoadId`.

`flattenJob(job)` composes nested instance transforms and returns selected-side placements, keeping X-outs as `enabled:false`. `placementKey` is a JSON array string of the instance path followed by the reference, preventing ambiguous delimiter collisions. Duplicate sibling instances, duplicate board references, cycles and expansion beyond 100,000 instances/placements fail. Instance disablement never changes a shared definition.

`transformPlacement(placement,{x,y,z,rotation,side,widthMm})` returns a preview `{x,y,z,rotation,units:'mm'}`. Bottom XY translates by board width and reflects X. Stored native placement angle is added to the decomposed frame rotation. This follows [pinned Utils2D](https://github.com/openpnp/openpnp/blob/5bd404cfc70f34103a3ca0fbb6b50c2b465f407c/src/main/java/org/openpnp/util/Utils2D.java); it is not a fiducial-fit transform, nozzle-offset solution or safe motion plan.

`validateJob(job,{mode:'production',feeders,nozzleTips})` additionally requires known positive part heights/board dimensions, enabled feeder capacity per part, and enabled tip/package compatibility. Feeders have `{id,partId,enabled,remaining}`; tips have `{id,enabled,packageIds}`. This does not qualify hardware, fixture clearance, tape orientation, motion envelopes, vision or production. It always reports `productionQualified:false`.

## Inspection and evidence coverage

`recordInspection(input)` requires SHA-256 `jobRevision,configurationRevision`, physical `boardLoadId`, `placementKey`, actual `actor`, UTC `observedAt`, and a method: `operator-metrology`, `calibrated-vision`, or `external-metrology`. Input has:

```js
{
  presence: 'present', // present | missing | unknown
  polarity: 'not-applicable', // correct | incorrect | not-applicable | unknown
  measurements: { dxMm: 0.03, dyMm: 0.04, rotationErrorDeg: 1,
    uncertaintyMm: 0.01, uncertaintyDeg: 0.1 },
  tolerances: { xyMm: 0.1, rotationDeg: 2 },
  evidence: [{ id: 'artifact-id', kind: 'measurement-file', sha256: '...64 hex...' }],
}
```

The result has immutable `id`, radial XY/angle error, and calculated `status:'pass'|'fail'|'uncertain'`. A pass requires the error plus its stated uncertainty inside both tolerances and categorical checks to pass. `verifyInspection(record)` checks the immutable receipt and recomputes the result. Neither validates metrology calibration nor authenticates the supplied actor; the bridge must establish those independently. Categorical-only inspection and sampling qualification are deferred.

`assessInspectionCoverage({job,configurationRevision,boardLoadId,inspections})` requires every enabled placement in the selected physical load, uses the newest inspection per placement, rejects ambiguous same-time results or mismatched revisions, and reports `inspectionComplete,required,inspected,passed,issues`. It always reports `productionQualified:false`.

## Pure outcome ledgers

- `createPlacementLedger({job,configurationRevision,boardLoadId})` starts a new physical load with pending placements; it never copies a previous board's completion. `applyPlacementEvent(ledger,event)` accepts `type:'observe'`, scoped revisions/load/placement, new `id`, `expectedSequence`, `evidenceId`, and `status`. Allowed flow is pending → picked → aligned → placed → verified; a passing matching inspection is required for verified. Picked/aligned may become discarded. Pending/picked/aligned/placed may become unknown.
- Unknown outcomes permit only `type:'reconcile'` with `actor,reason,evidenceId` and a resolved status (`pending,picked,aligned,placed,discarded`). Reconciliation does not dispatch a new action. Rework, skipped/failed outcomes and preserving one physical board's history across a flip require additional native workflow integration.
- `createMaterialLedger({feeders:[{id,partId,lotId,remaining}]})` records known nonnegative material counts. `applyMaterialEvent(ledger,event)` requires `id,expectedSequence,evidenceId,feederId,lotId,operationId,type`. `reserve` adds positive `quantity` and records intent; `consume` or `discard` resolves the pending operation and reduces remaining count. `unknown` blocks another feed. `reconcile` requires actor/reason and a recount within the unresolved reservation's range. Refill/lot replacement starts a new explicitly identified ledger; it never invents a material refund.
- Repeated event IDs with identical payload are idempotent; reused IDs with changed content fail. Stale sequences, reused feed operation IDs, and physical scope mismatches fail. Ledger helpers require trusted current state from the bridge's durable store. They neither persist events nor implement machine retries.

Run: `node --test tests/openpnp/domain*.test.mjs`.
