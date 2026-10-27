import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { OpenPnpRuntime } from '../../src/openpnp/node/runtime.mjs';
import { ArtifactStore } from '../../src/openpnp/node/artifacts.mjs';
import * as domain from '../../plugins/openpnp/mcp/domain/index.mjs';

test('inspection tools retain provenance limits, immutable scope, missing coverage, and uncertainty', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'openpnp-inspection-')); t.after(() => rm(root, { recursive: true, force: true }));
  const artifacts = new ArtifactStore(root); const runtime = new OpenPnpRuntime({ artifacts, domain });
  const imported = await runtime.call('openpnp_import_job', { format: 'reference-csv', content: 'Ref,Val,Package,PosX,PosY,Rot,Side\nR1,10k,R0805,10,10,0,top\nR2,10k,R0805,20,10,0,top\n', units: 'mm', widthMm: 40, heightMm: 30 });
  const job = await artifacts.get(imported.artifact_id); const placements = domain.flattenJob(job);
  const scope = { job_artifact_id: imported.artifact_id, configuration_revision: 'a'.repeat(64), board_load_id: 'load-1' };
  const measurement = { ...scope, placement_key: placements[0].placementKey, method: 'external-metrology', actor: 'test-inspector', observed_at: '2026-09-10T12:00:00Z',
    presence: 'present', polarity: 'correct', measurements: { dxMm: 0.01, dyMm: 0.01, rotationErrorDeg: 0.1, uncertaintyMm: 0.005, uncertaintyDeg: 0.01 },
    tolerances: { xyMm: 0.05, rotationDeg: 0.5 }, evidence: [{ id: 'measured-fixture-1', sha256: 'b'.repeat(64), kind: 'measurement-file' }] };
  const first = await runtime.call('openpnp_record_inspection', measurement);
  assert.equal(first.inspection.status, 'pass'); assert.equal(first.production_qualified, false); assert.equal(first.provenance_validation, 'supplied-references-only');
  assert.equal((await runtime.call('openpnp_record_inspection', measurement)).artifact_id, first.artifact_id);
  assert.equal((await artifacts.get(first.artifact_id)).production_qualified, false);
  const incomplete = await runtime.call('openpnp_assess_inspection_coverage', { ...scope, inspection_artifact_ids: [first.artifact_id] });
  assert.equal(incomplete.inspectionComplete, false); assert.equal(incomplete.required, 2); assert.equal(incomplete.inspected, 1);
  const second = await runtime.call('openpnp_record_inspection', { ...measurement, placement_key: placements[1].placementKey });
  const complete = await runtime.call('openpnp_assess_inspection_coverage', { ...scope, inspection_artifact_ids: [first.artifact_id, second.artifact_id] });
  assert.equal(complete.inspectionComplete, true); assert.equal(complete.productionQualified, false);
  const uncertain = await runtime.call('openpnp_record_inspection', { ...measurement, placement_key: placements[1].placementKey, observed_at: '2026-09-10T12:01:00Z', measurements: { ...measurement.measurements, dxMm: 0.049 } });
  const replaced = await runtime.call('openpnp_assess_inspection_coverage', { ...scope, inspection_artifact_ids: [first.artifact_id, second.artifact_id, uncertain.artifact_id] });
  assert.equal(replaced.inspectionComplete, false); assert.equal(replaced.issues[0].code, 'inspection_uncertain');
  await assert.rejects(runtime.call('openpnp_assess_inspection_coverage', { ...scope, board_load_id: 'another-load', inspection_artifact_ids: [first.artifact_id] }), { code: 'inspection_scope_mismatch' });
  await assert.rejects(runtime.call('openpnp_record_inspection', { ...measurement, placement_key: 'invented-placement' }), { code: 'unknown_placement' });
});
