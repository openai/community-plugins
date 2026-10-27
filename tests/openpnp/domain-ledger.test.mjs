import test from 'node:test';
import assert from 'node:assert/strict';
import { prepareJob, recordInspection, verifyInspection, assessInspectionCoverage, createPlacementLedger, applyPlacementEvent, createMaterialLedger, applyMaterialEvent, DomainError } from '../../plugins/openpnp/mcp/domain/index.mjs';

const job = prepareJob({ format: 'reference-csv', content: 'Ref,Value,Package,X,Y,Rotation,Side,Height\nR1,10k,R_0603,7,4,135,top,0.5\n', units: 'mm', widthMm: 40, heightMm: 30 });
const scope = { jobRevision: job.revision, configurationRevision: 'a'.repeat(64), boardLoadId: 'physical-board-001' };
const placementKey = '["load-1","R1"]';
const inspectionInput = {
  ...scope, placementKey, actor: 'operator:alice', method: 'operator-metrology', observedAt: '2026-09-10T10:00:00Z', presence: 'present', polarity: 'not-applicable',
  measurements: { dxMm: 0.03, dyMm: 0.04, rotationErrorDeg: 1, uncertaintyMm: 0.01, uncertaintyDeg: 0.1 },
  tolerances: { xyMm: 0.1, rotationDeg: 2 }, evidence: [{ id: 'measurement-1', kind: 'measurement-file', sha256: 'b'.repeat(64) }],
};
const code = expected => e => e instanceof DomainError && e.code === expected;

test('inspection uses independent radial XY error and conservative uncertainty bounds', () => {
  const record = recordInspection(inspectionInput);
  assert.equal(record.status, 'pass'); assert.equal(record.error.xyMm, 0.05);
  assert.deepEqual(verifyInspection(record), record);
  const uncertain = recordInspection({ ...inspectionInput, measurements: { ...inspectionInput.measurements, dxMm: 0.09, dyMm: 0, uncertaintyMm: 0.02 } });
  assert.equal(uncertain.status, 'uncertain');
  assert.equal(recordInspection({ ...inspectionInput, presence: 'missing' }).status, 'fail');
  assert.equal(recordInspection({ ...inspectionInput, polarity: 'incorrect' }).status, 'fail');
  assert.equal(recordInspection({ ...inspectionInput, presence: 'unknown' }).status, 'uncertain');
  assert.equal(recordInspection({ ...inspectionInput, measurements: { ...inspectionInput.measurements, dxMm: 0.2 } }).status, 'fail');
  assert.throws(() => recordInspection({ ...inspectionInput, method: 'agent-says-done' }), code('invalid_inspection'));
  assert.throws(() => recordInspection({ ...inspectionInput, observedAt: '2026-02-30T10:00:00Z' }), code('invalid_timestamp'));
  assert.throws(() => recordInspection({ ...inspectionInput, evidence: [] }), code('missing_evidence'));
});

test('inspection receipts detect altered status, errors and scope; coverage never grants machine authority', () => {
  const record = recordInspection(inspectionInput);
  assert.throws(() => verifyInspection({ ...record, status: 'fail' }), code('inspection_tampered'));
  assert.throws(() => verifyInspection({ ...record, error: { xyMm: 0, rotationDeg: 0 } }), code('inspection_tampered'));
  const input = { job, configurationRevision: scope.configurationRevision, boardLoadId: scope.boardLoadId };
  assert.deepEqual(assessInspectionCoverage({ ...input, inspections: [] }).issues, [{ code: 'inspection_missing', placementKey, inspectionId: null }]);
  const coverage = assessInspectionCoverage({ ...input, inspections: [record] });
  assert.equal(coverage.inspectionComplete, true); assert.equal(coverage.productionQualified, false);
  assert.throws(() => assessInspectionCoverage({ ...input, boardLoadId: 'other-board', inspections: [record] }), code('inspection_scope_mismatch'));
  assert.throws(() => assessInspectionCoverage({ ...input, job: { ...job, id: 'changed' }, inspections: [record] }), code('revision_mismatch'));
  const failure = recordInspection({ ...inspectionInput, observedAt: '2026-09-10T10:01:00Z', presence: 'missing' });
  assert.equal(assessInspectionCoverage({ ...input, inspections: [failure, record] }).inspectionComplete, false);
  assert.throws(() => assessInspectionCoverage({ ...input, inspections: [record, recordInspection({ ...inspectionInput, presence: 'missing' })] }), code('ambiguous_inspection'));
});

function placementEvent(ledger, status, extras = {}) {
  return { id: `event-${ledger.sequence + 1}`, expectedSequence: ledger.sequence, type: 'observe', ...scope, placementKey, status, evidenceId: `native-event-${ledger.sequence + 1}`, ...extras };
}
test('placement state separates placed and inspected; event replays do not apply twice', () => {
  const initial = createPlacementLedger({ job, ...scope });
  const pick = placementEvent(initial, 'picked');
  let ledger = applyPlacementEvent(initial, pick);
  assert.equal(initial.sequence, 0); assert.equal(initial.entries[0].status, 'pending');
  assert.deepEqual(applyPlacementEvent(ledger, pick), ledger);
  assert.throws(() => applyPlacementEvent(ledger, { ...pick, status: 'unknown' }), code('idempotency_conflict'));
  ledger = applyPlacementEvent(ledger, placementEvent(ledger, 'aligned'));
  ledger = applyPlacementEvent(ledger, placementEvent(ledger, 'placed'));
  assert.equal(ledger.entries[0].status, 'placed');
  assert.throws(() => applyPlacementEvent(ledger, placementEvent(ledger, 'verified')), code('invalid_inspection'));
  ledger = applyPlacementEvent(ledger, placementEvent(ledger, 'verified', { inspection: recordInspection(inspectionInput) }));
  assert.equal(ledger.entries[0].status, 'verified'); assert.equal(ledger.sequence, 4);
  assert.throws(() => applyPlacementEvent(ledger, placementEvent(ledger, 'picked')), code('invalid_transition'));
  const newLoad = createPlacementLedger({ job, configurationRevision: scope.configurationRevision, boardLoadId: 'physical-board-002' });
  assert.equal(newLoad.entries[0].status, 'pending');
  assert.throws(() => applyPlacementEvent(newLoad, placementEvent(newLoad, 'picked')), code('event_scope_mismatch'));
});

test('unknown placement outcomes require explicit scoped reconciliation; stale writes fail', () => {
  let ledger = createPlacementLedger({ job, ...scope });
  ledger = applyPlacementEvent(ledger, placementEvent(ledger, 'unknown'));
  assert.throws(() => applyPlacementEvent(ledger, placementEvent(ledger, 'picked')), code('invalid_transition'));
  assert.throws(() => applyPlacementEvent(ledger, placementEvent(ledger, 'pending', { type: 'reconcile' })), code('invalid_identifier'));
  const reconcile = placementEvent(ledger, 'placed', { type: 'reconcile', actor: 'operator:alice', reason: 'Inspected occupied pad after interrupted release.' });
  ledger = applyPlacementEvent(ledger, reconcile);
  assert.equal(ledger.entries[0].status, 'placed');
  assert.throws(() => applyPlacementEvent(ledger, { ...placementEvent(ledger, 'unknown'), expectedSequence: 0 }), code('sequence_conflict'));
});

function materialEvent(ledger, type, extras = {}) { return { id: `event-${ledger.sequence + 1}`, expectedSequence: ledger.sequence, type, feederId: 'f1', lotId: 'lot-1', operationId: 'feed-1', evidenceId: `native-event-${ledger.sequence + 1}`, ...extras }; }
const materials = () => createMaterialLedger({ feeders: [{ id: 'f1', partId: 'RES-10K', lotId: 'lot-1', remaining: 10 }] });

test('material feed intent, actual consumption and duplicate operations are distinct', () => {
  const initial = materials(); const reserve = materialEvent(initial, 'reserve', { quantity: 1 });
  let ledger = applyMaterialEvent(initial, reserve);
  assert.equal(initial.entries[0].status, 'known');
  assert.equal(ledger.entries[0].status, 'pending'); assert.equal(ledger.entries[0].remaining, 10);
  assert.deepEqual(applyMaterialEvent(ledger, reserve), ledger);
  const consume = materialEvent(ledger, 'consume'); ledger = applyMaterialEvent(ledger, consume);
  assert.equal(ledger.entries[0].remaining, 9); assert.equal(ledger.entries[0].consumed, 1);
  assert.deepEqual(applyMaterialEvent(ledger, consume), ledger);
  assert.throws(() => applyMaterialEvent(ledger, materialEvent(ledger, 'reserve', { quantity: 1 })), code('duplicate_operation'));
  assert.throws(() => applyMaterialEvent(ledger, materialEvent(ledger, 'reserve', { quantity: 11, operationId: 'feed-2' })), code('insufficient_material'));
  assert.throws(() => applyMaterialEvent(ledger, materialEvent(ledger, 'reserve', { quantity: 1, operationId: 'feed-2', lotId: 'new-lot' })), code('event_scope_mismatch'));
});

test('lost feed acknowledgment blocks reserve and consumption until bounded recount reconciliation', () => {
  let ledger = materials(); ledger = applyMaterialEvent(ledger, materialEvent(ledger, 'reserve', { quantity: 2 }));
  ledger = applyMaterialEvent(ledger, materialEvent(ledger, 'unknown'));
  assert.throws(() => applyMaterialEvent(ledger, materialEvent(ledger, 'reserve', { quantity: 1, operationId: 'feed-2' })), code('material_unresolved'));
  assert.throws(() => applyMaterialEvent(ledger, materialEvent(ledger, 'consume')), code('material_unresolved'));
  assert.throws(() => applyMaterialEvent(ledger, materialEvent(ledger, 'reconcile', { actor: 'alice', reason: 'Recount', remaining: 11 })), code('invalid_count'));
  ledger = applyMaterialEvent(ledger, materialEvent(ledger, 'reconcile', { actor: 'alice', reason: 'One pocket advanced; one untouched.', remaining: 9 }));
  assert.equal(ledger.entries[0].status, 'known'); assert.equal(ledger.entries[0].remaining, 9); assert.equal(ledger.entries[0].consumed, 1);
  ledger = applyMaterialEvent(ledger, materialEvent(ledger, 'reserve', { quantity: 1, operationId: 'feed-2' }));
  ledger = applyMaterialEvent(ledger, materialEvent(ledger, 'discard', { operationId: 'feed-2' }));
  assert.equal(ledger.entries[0].remaining, 8); assert.equal(ledger.entries[0].consumed, 2); assert.equal(ledger.entries[0].discarded, 1);
});
