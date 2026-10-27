// Native-shaped projection fixtures only; real OpenPnP coverage is separate.
import test from 'node:test';
import assert from 'node:assert/strict';
import { projectProgress } from '../../src/openpnp/node/progress.mjs';
import { INLINE_JSON_BYTES } from '../../src/openpnp/node/responses.mjs';

const row = i => ({ nozzle_id: `N${i}`, state: i === 18 ? 'retained' : 'observed_empty', recorded_state: i === 18 ? 'retained' : 'observed_empty',
  sticky_fault: i === 18, reason: i === 18 ? 'part_off_false' : null, observation_count: i, returned_count: i, failed_count: 0,
  last_observation_id: `observation-${i}`, last_bridge_instance_id: 'original-instance', historical: false,
  empty_observation_binding: { source: { declared: 'full-read-only' } }, last_record: { arbitrary: 'x'.repeat(30000) } });
const journal = count => ({ profile: 'native-vacuum-observation-v1', observations: 32, observation_limit: 200000, nozzle_limit: 64,
  pending: [{ observation_id: 'unfinished', nozzle_id: 'N1' }], nozzles: Array.from({ length: count }, (_, i) => row(i)),
  recovered_history: true, execution_authority_restored: false, physical_occupancy_verified: false, hardware_qualified: false, reconciliation_supported: false });

test('all three progress views preserve sensing faults, pending observations and explicit missing authority', () => {
  const j = journal(20), op = { state: 'outcome_unknown', operation_id: 'original', vacuum_sensing_journal: j };
  for (const [tool, value, nested] of [
    ['openpnp_get_status', { vacuum_sensing_journal: j }, false],
    ['openpnp_get_operation', op, false],
    ['openpnp_get_request_status', { found: true, operation: op }, true],
  ]) {
    const p = projectProgress(tool, value), v = (nested ? p.operation : p).vacuum_sensing_journal;
    assert.equal(v.pending_count, 1); assert.equal(v.sticky_fault_count, 1, 'fault outside visible prefix remains explicit');
    assert.equal(v.nozzles_count, 20); assert.equal(v.nozzles.length, 16); assert.equal(v.nozzles_complete, false);
    for (const key of ['execution_authority_restored', 'physical_occupancy_verified', 'hardware_qualified', 'reconciliation_supported']) assert.equal(v[key], false);
    assert.equal(v.recovered_history, true); assert.equal(v.nozzles[0].reason, null);
    assert.equal(v.nozzles[0].last_record, undefined); assert.equal(v.nozzles[0].empty_observation_binding, undefined);
    assert.ok(Buffer.byteLength(JSON.stringify(p)) < INLINE_JSON_BYTES);
    assert.equal(p.response_view.retained_snapshot, false);
  }
});

test('sensing projection bounds rows and bytes without manufacturing an all-clear or fresh evidence', () => {
  for (const count of [0, 1, 16, 17, 32]) {
    const j = journal(count); j.pending = [];
    const v = projectProgress('openpnp_get_status', { vacuum_sensing_journal: j }).vacuum_sensing_journal;
    assert.equal(v.nozzles_count, count); assert.equal(v.nozzles_complete, count <= 16);
    assert.equal(v.nozzles.length, Math.min(count, 16)); assert.equal(v.pending_count, 0);
  }
  const j = journal(16); j.nozzles.forEach(n => { n.reason = 'r'.repeat(1000); n.nozzle_id = 'n'.repeat(1000); });
  const v = projectProgress('openpnp_get_operation', { vacuum_sensing_journal: j }).vacuum_sensing_journal;
  assert.equal(v.nozzles_complete, false); assert.ok(v.nozzles.length < 16); assert.ok(Buffer.byteLength(JSON.stringify(v.nozzles)) <= 8192);
  assert.equal(v.observed_at, undefined); assert.equal(v.nozzles[0].historical, false);
  assert.equal(projectProgress('openpnp_get_status', {}).vacuum_sensing_journal, undefined);
  assert.equal(projectProgress('openpnp_get_status', { vacuum_sensing_journal: null }).vacuum_sensing_journal, null);
  const absent = projectProgress('openpnp_get_status', { vacuum_sensing_journal: {} }).vacuum_sensing_journal;
  assert.equal(absent.sticky_fault_count, undefined); assert.equal(absent.pending_count, undefined);
});

test('malformed sensing journal or fault observations are rejected rather than silently dropped', () => {
  for (const j of [[], true, { pending: {} }, { nozzles: {} }, { nozzles: [null] }, { nozzles: [{}] },
    { nozzles: [{ sticky_fault: 'false' }] }, { nozzles: [{ sticky_fault: false, state: [] }] }, journal(65), { pending: Array(257).fill({}) }]) {
    assert.throws(() => projectProgress('openpnp_get_status', { vacuum_sensing_journal: j }), { code: 'INVALID_PROGRESS_RESPONSE' });
  }
});

test('negative native verdict and passive sample facts remain exact in operation progress', () => {
  const result = { profile: 'native-vacuum-sensing-v1', nozzle_id: 'N1', nozzle_tip_id: 'NT1', sensor_id: 'A1',
    check_kind: 'part_off', native_verdict: false, reading_units: 'native-actuator-units', occupancy_authority: 'requires-separate-journal-disposition' };
  const p = projectProgress('openpnp_get_operation', { state: 'succeeded', result });
  assert.equal(p.state, 'succeeded'); assert.deepEqual(p.result, result);
  const measured = projectProgress('openpnp_get_operation', { result: { sample_count: 3, samples: [70, 70, 70], part_state_inferred: false } });
  assert.deepEqual(measured.result, { sample_count: 3, part_state_inferred: false });
});

test('machine lifecycle fault survives polling even when no trustworthy nozzle row exists', () => {
  const j = { ...journal(0), pending: [], lifecycle_fault: true };
  const p = projectProgress('openpnp_get_status', { vacuum_sensing_journal: j }).vacuum_sensing_journal;
  assert.equal(p.lifecycle_fault, true); assert.equal(p.sticky_fault_count, 0);
  assert.equal(p.nozzles_count, 0); assert.equal(p.execution_authority_restored, false);
  for (const invalid of [null, 'false', 0]) assert.throws(() => projectProgress('openpnp_get_status', { vacuum_sensing_journal: { ...j, lifecycle_fault: invalid } }), { code: 'INVALID_PROGRESS_RESPONSE' });
});
