import { LIMITS, fail, identifier, digest } from './common.mjs';
import { flattenJob } from './geometry.mjs';
import { verifyInspection } from './evidence.mjs';

const PLACEMENT_TRANSITIONS = Object.freeze({
  pending: ['picked', 'unknown'], picked: ['aligned', 'discarded', 'unknown'], aligned: ['placed', 'discarded', 'unknown'],
  placed: ['verified', 'unknown'], verified: [], discarded: [], unknown: [],
});
const count = (n, name) => { if (!Number.isSafeInteger(n) || n < 0) fail('invalid_count', `${name} must be a nonnegative safe integer.`); return n; };

function checkedJob(job) {
  if (!job || typeof job !== 'object') fail('invalid_job', 'A canonical job is required.');
  const { revision, ...body } = job;
  if (revision !== digest(body)) fail('revision_mismatch', 'Job content changed since its revision was issued.');
  return flattenJob(job).filter(p => p.enabled && p.type === 'placement');
}

/** New physical board load; prior completion is never copied from the reusable job definition. */
export function createPlacementLedger({ job, configurationRevision, boardLoadId }) {
  if (typeof configurationRevision !== 'string' || !/^[a-f0-9]{64}$/u.test(configurationRevision)) fail('invalid_revision', 'configurationRevision must be SHA-256.');
  identifier(boardLoadId, 'boardLoadId'); const placements = checkedJob(job);
  return { schemaVersion: 1, kind: 'placement-ledger', jobRevision: job.revision, configurationRevision, boardLoadId, sequence: 0,
    entries: placements.map(p => ({ placementKey: p.placementKey, partId: p.partId, status: 'pending', evidence: [] })), events: [] };
}

function eventBase(ledger, event, kind) {
  if (!ledger || ledger.schemaVersion !== 1 || ledger.kind !== kind || !Array.isArray(ledger.events) || !Array.isArray(ledger.entries)) fail('invalid_ledger', `Expected ${kind} schema 1.`);
  if (!event || typeof event !== 'object') fail('invalid_event', 'Event must be an object.');
  identifier(event.id, 'event.id'); const payloadHash = digest(event), previous = ledger.events.find(e => e.id === event.id);
  if (previous) {
    if (previous.payloadHash !== payloadHash) fail('idempotency_conflict', 'Event identity was already used with different content.');
    return { replay: true };
  }
  if (ledger.events.length >= LIMITS.placements) fail('input_limit', 'Ledger event limit reached. Rotate after reconciliation.');
  if (!Number.isSafeInteger(event.expectedSequence) || event.expectedSequence !== ledger.sequence) fail('sequence_conflict', 'Ledger changed since this event was prepared.');
  identifier(event.evidenceId, 'event.evidenceId');
  return { replay: false, receipt: { id: event.id, payloadHash, evidenceId: event.evidenceId, sequence: ledger.sequence + 1 } };
}

/** Observed outcomes only. This does not dispatch/replay native pick or place operations. */
export function applyPlacementEvent(ledger, event) {
  const check = eventBase(ledger, event, 'placement-ledger'); if (check.replay) return structuredClone(ledger);
  if (event.boardLoadId !== ledger.boardLoadId || event.jobRevision !== ledger.jobRevision || event.configurationRevision !== ledger.configurationRevision)
    fail('event_scope_mismatch', 'Placement event belongs to a different job/configuration/physical load.');
  const entry = ledger.entries.find(p => p.placementKey === event.placementKey);
  if (!entry) fail('unknown_placement', 'Placement key does not exist in this load.');
  let inspectionId = null;
  if (event.type === 'reconcile') {
    if (entry.status !== 'unknown') fail('invalid_transition', 'Only unknown placement outcomes require reconciliation.');
    if (!['pending', 'picked', 'aligned', 'placed', 'discarded'].includes(event.status)) fail('invalid_transition', 'Invalid reconciliation outcome.');
    identifier(event.actor, 'event.actor'); identifier(event.reason, 'event.reason');
  } else if (event.type !== 'observe' || !PLACEMENT_TRANSITIONS[entry.status]?.includes(event.status))
    fail('invalid_transition', `Cannot transition ${entry.status} to ${event.status}; unknown outcomes require evidence-backed reconciliation.`);
  if (event.status === 'verified') {
    const inspection = verifyInspection(event.inspection);
    if (inspection.status !== 'pass' || inspection.jobRevision !== ledger.jobRevision || inspection.configurationRevision !== ledger.configurationRevision || inspection.boardLoadId !== ledger.boardLoadId || inspection.placementKey !== entry.placementKey)
      fail('inspection_scope_mismatch', 'Verification requires a passing inspection of this exact placement, configuration and physical load.');
    inspectionId = inspection.id;
  }
  return { ...structuredClone(ledger), sequence: ledger.sequence + 1,
    entries: ledger.entries.map(p => p === entry ? { ...p, status: event.status, evidence: [...p.evidence, event.evidenceId], ...(inspectionId ? { inspectionId } : {}) } : structuredClone(p)),
    events: [...structuredClone(ledger.events), { ...check.receipt, placementKey: entry.placementKey, type: event.type, status: event.status }] };
}

export function createMaterialLedger({ feeders }) {
  if (!Array.isArray(feeders) || feeders.length > LIMITS.placements) fail('invalid_ledger', 'Feeders must be a bounded array.');
  const seen = new Set();
  const entries = feeders.map(f => {
    identifier(f.id, 'feeder.id'); identifier(f.partId, 'feeder.partId'); identifier(f.lotId, 'feeder.lotId');
    if (seen.has(f.id)) fail('duplicate_id', 'Feeder identity must be unique.'); seen.add(f.id);
    count(f.remaining, 'remaining');
    return { id: f.id, partId: f.partId, lotId: f.lotId, remaining: f.remaining, consumed: 0, discarded: 0, status: 'known', pendingOperation: null };
  });
  return { schemaVersion: 1, kind: 'material-ledger', sequence: 0, entries, events: [] };
}

/** Reserve before a feed; lost acknowledgment leaves an unresolved outcome until reconciliation. */
export function applyMaterialEvent(ledger, event) {
  const check = eventBase(ledger, event, 'material-ledger'); if (check.replay) return structuredClone(ledger);
  const old = ledger.entries.find(f => f.id === event.feederId);
  if (!old) fail('unknown_feeder', 'Unknown feeder.');
  if (event.lotId !== old.lotId) fail('event_scope_mismatch', 'Event belongs to another physical material lot.');
  const entry = structuredClone(old);
  if (event.type === 'reserve') {
    if (entry.status !== 'known') fail('material_unresolved', 'Resolve the previous feed outcome before advancing material.');
    identifier(event.operationId, 'event.operationId'); count(event.quantity, 'quantity');
    if (ledger.events.some(e => e.type === 'reserve' && e.operationId === event.operationId)) fail('duplicate_operation', 'A feed operation cannot reserve material twice.');
    if (!event.quantity || event.quantity > entry.remaining) fail('insufficient_material', 'Reservation must be positive and available.');
    entry.status = 'pending'; entry.pendingOperation = { id: event.operationId, quantity: event.quantity };
  } else if (event.type === 'consume' || event.type === 'discard') {
    if (entry.status !== 'pending' || entry.pendingOperation?.id !== event.operationId) fail('material_unresolved', 'A matching pending feed reservation is required.');
    const quantity = entry.pendingOperation.quantity;
    entry.remaining -= quantity; entry.consumed += quantity;
    if (event.type === 'discard') entry.discarded += quantity;
    entry.status = 'known'; entry.pendingOperation = null;
  } else if (event.type === 'unknown') {
    if (entry.status !== 'pending' || entry.pendingOperation?.id !== event.operationId) fail('invalid_transition', 'Only a pending feed can become unknown.');
    entry.status = 'unknown';
  } else if (event.type === 'reconcile') {
    if (entry.status !== 'unknown') fail('invalid_transition', 'Only unknown feed outcomes require reconciliation.');
    if (entry.pendingOperation?.id !== event.operationId) fail('event_scope_mismatch', 'Reconciliation must identify the unresolved feed operation.');
    identifier(event.actor, 'event.actor'); identifier(event.reason, 'event.reason'); count(event.remaining, 'remaining');
    const minimum = entry.remaining - entry.pendingOperation.quantity;
    if (event.remaining < minimum || event.remaining > entry.remaining) fail('invalid_count', 'Recount must resolve only the reserved quantity; reloading a lot creates a new ledger.');
    const consumed = entry.remaining - event.remaining;
    entry.remaining = event.remaining; entry.consumed += consumed; entry.status = 'known'; entry.pendingOperation = null;
  } else fail('invalid_event', 'Unknown material event type.');
  return { ...structuredClone(ledger), sequence: ledger.sequence + 1, entries: ledger.entries.map(f => f === old ? entry : structuredClone(f)),
    events: [...structuredClone(ledger.events), { ...check.receipt, feederId: entry.id, type: event.type, operationId: event.operationId }] };
}
