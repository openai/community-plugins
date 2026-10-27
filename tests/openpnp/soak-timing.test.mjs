import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, symlink } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { SoakTimingGuard, TIMING_POLICY, inspectTimingFiles } from '../../scripts/openpnp-soak-timing.mjs';

const DURATION = 8 * 3600000, START = 10000, WALL = Date.parse('2026-09-11T00:00:00.000Z');
const ID = '11111111-2222-4333-8444-555555555555';
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
function sample(t, overrides = {}) {
  return { type: 'sample', elapsed_ms: START + t + 10, observed_at: new Date(WALL + START + t + 10).toISOString(),
    bridge_instance_id: ID, native_metrics: { uptime_ms: 500000 + t }, native_process: { pid: 12345, started_at_ps: 'Fri Sep 11 00:00:00 2026', executable: 'java' },
    native_read_started_elapsed_ms: START + t, native_read_completed_elapsed_ms: START + t + 5, ...overrides };
}
function history(mutate = x => x, end = DURATION) {
  const rows = []; for (let t = 0; t <= end; t += 10000) rows.push(mutate(sample(t), t)); return rows;
}
function assess(rows, options = {}) {
  const g = new SoakTimingGuard({ workloadStartElapsedMs: START }); rows.forEach(row => g.add(row));
  return g.finish({ closed: true, rawSimulatorSoakComplete: true, workloadEndElapsedMs: rows.at(-1).elapsed_ms, ...options });
}

test('same-instance bracketed eight-hour observations pass the independent gate only', () => {
  const r = assess(history()); assert.equal(r.continuous_simulator_soak_qualified, true);
  assert.equal(r.observed_native_timer_span_ms, DURATION); assert.equal(r.full_A11_qualified, false); assert.equal(r.hardware_qualified, false);
  assert.equal(r.root_cause_determined, false); assert.equal(r.policy.maximum_observation_gap_ms, 60000);
});
test('wall/harness eight hours with seven hours fifteen native minutes remains unqualified', () => {
  const r = assess(history((row, t) => ({ ...row, native_metrics: { uptime_ms: 500000 + Math.floor(t * 435 / 480) } })));
  assert.equal(r.checks.harness_timer_eight_hours, true); assert.equal(r.checks.native_timer_eight_hours, false);
  assert.equal(r.observed_native_timer_span_ms, 435 * 60000); assert.ok(r.reason_codes.includes('CLOCK_DIVERGENCE'));
  assert.equal(r.continuous_simulator_soak_qualified, false);
});
test('matching long clock gap prevents continuous qualification even after eight native hours', () => {
  const r = assess(history((row, t) => t < 100000 ? row : sample(t + 95000)));
  assert.ok(r.reason_codes.includes('OBSERVATION_GAP')); assert.equal(r.checks.native_timer_eight_hours, true);
  assert.equal(r.continuous_simulator_soak_qualified, false);
});
test('clock divergence is retained even when later counters catch up', () => {
  const r = assess(history((row, t) => t < 100000 ? row : { ...sample(t + 2700000), native_metrics: { uptime_ms: 500000 + t + (t < 200000 ? 0 : 2700000) } }));
  assert.ok(r.reason_codes.includes('CLOCK_DIVERGENCE')); assert.equal(r.continuous_simulator_soak_qualified, false);
  assert.ok(r.anomalies.length <= TIMING_POLICY.maximum_recorded_anomalies);
});
test('uptime differences cannot count earlier JVM startup or exclude uncovered workload edges', () => {
  assert.equal(assess(history(x => x, DURATION - 10000)).checks.native_timer_eight_hours, false);
  const rows = history().map(row => ({ ...row, elapsed_ms: row.elapsed_ms + 100000,
    observed_at: new Date(Date.parse(row.observed_at) + 100000).toISOString(),
    native_read_started_elapsed_ms: row.native_read_started_elapsed_ms + 100000, native_read_completed_elapsed_ms: row.native_read_completed_elapsed_ms + 100000 }));
  assert.equal(assess(rows).checks.workload_endpoint_coverage, false);
  assert.equal(assess(history(), { workloadEndElapsedMs: START + DURATION + 100000 }).checks.workload_endpoint_coverage, false);
});
test('historical unbracketed samples remain diagnostic despite a complete native span', () => {
  const r = assess(history(row => { const { native_read_started_elapsed_ms, native_read_completed_elapsed_ms, ...old } = row; return old; }));
  assert.equal(r.observed_native_timer_span_ms, DURATION); assert.equal(r.precise_native_boundary_qualification_available, false);
  assert.equal(r.continuous_simulator_soak_qualified, false);
});
test('partial or failed raw results cannot be promoted by a passing native clock', () => {
  for (const flags of [{ closed: false }, { reportStatus: 'failed' }, { rawSimulatorSoakComplete: false }, { rawSimulatorSoakComplete: 'true' }])
    assert.equal(assess(history(), flags).continuous_simulator_soak_qualified, false);
});
test('identity changes and clock regressions cannot recover later within the run', () => {
  for (const [key, mutate] of [
    ['IDENTITY_CHANGED', row => ({ ...row, bridge_instance_id: '22222222-2222-4333-8444-555555555555' })],
    ['IDENTITY_CHANGED', row => ({ ...row, native_process: { ...row.native_process, started_at_ps: 'restarted' } })],
    ['CLOCK_REGRESSION', row => ({ ...row, native_metrics: { uptime_ms: 1 } })],
    ['CLOCK_REGRESSION', row => ({ ...row, observed_at: new Date(WALL).toISOString() })],
  ]) { const rows = history(); rows[100] = mutate(rows[100]); const r = assess(rows); assert.ok(r.reason_codes.includes(key)); assert.equal(r.continuous_simulator_soak_qualified, false); }
});
test('missing, coerced, nonfinite and oversized inputs fail closed with bounded state', () => {
  for (const invalid of [{ ...sample(0), elapsed_ms: NaN }, { ...sample(0), elapsed_ms: '10010' },
    { ...sample(0), native_metrics: {} }, { ...sample(0), native_metrics: { uptime_ms: Infinity } },
    { ...sample(0), bridge_instance_id: '-'.repeat(36) }]) { const g = new SoakTimingGuard({ workloadStartElapsedMs: START }); g.add(invalid); assert.ok(g.finish().reason_codes.includes('INVALID_TIMING_SAMPLE')); }
  const g = new SoakTimingGuard({ workloadStartElapsedMs: START }); for (let i = 0; i <= TIMING_POLICY.maximum_samples; i++) g.add(sample(i));
  const r = g.finish(); assert.ok(r.reason_codes.includes('SAMPLE_CAPACITY')); assert.ok(JSON.stringify(r).length < 12000);
});
test('native-read interval straddling startup cannot establish a qualified first endpoint', () => {
  const rows = history(); rows[0].native_read_started_elapsed_ms = START - 1;
  const r = assess(rows); assert.equal(r.ignored_outside_workload, 1); assert.equal(r.checks.native_timer_eight_hours, false);
});

async function fixture(status = 'completed', rows = history()) {
  const dir = await mkdtemp(path.join(process.env.OPENPNP_TIMING_TEST_OUTPUT || os.tmpdir(), 'soak-timing-'));
  const report = path.join(dir, 'report.json'), progress = path.join(dir, 'progress.jsonl');
  await writeFile(report, JSON.stringify({ status, started_at: new Date(WALL).toISOString(), bridge: { bridge_instance_id: ID }, native_process_rss: { supplied_pid: 12345, identity: 'Fri Sep 11 00:00:00 2026|java' }, simulator_soak_complete: true, elapsed_ms: START + DURATION + 10,
    workload_elapsed_ms: DURATION + 10, timing_window: { workload_started_elapsed_ms: START } }) + '\n');
  await writeFile(progress, [{ type: 'run_started', started_at: new Date(WALL).toISOString() }, ...rows, { type: 'run_finished', status, elapsed_ms: START + DURATION + 10, simulator_soak_complete: true }].map((row, index) => JSON.stringify({ sequence: index + 1, ...row })).join('\n') + '\n');
  return { dir, report, progress };
}
test('actual read-only CLI writes an independent result and refuses output reuse', async () => {
  const f = await fixture(); const before = { report: sha(await readFile(f.report)), progress: sha(await readFile(f.progress)) };
  const script = fileURLToPath(new URL('../../scripts/openpnp-soak-timing.mjs', import.meta.url)), output = path.join(f.dir, 'gate.json');
  const args = [script, '--report', f.report, '--progress', f.progress, '--output', output];
  const child = spawnSync(process.execPath, args, { encoding: 'utf8', timeout: 15000 }); assert.equal(child.status, 0, child.stderr);
  assert.equal(JSON.parse(await readFile(output, 'utf8')).continuous_simulator_soak_qualified, true);
  assert.equal(spawnSync(process.execPath, args, { timeout: 15000 }).status, 1);
  assert.equal(sha(await readFile(f.report)), before.report); assert.equal(sha(await readFile(f.progress)), before.progress);
});
test('actual file assessment preserves live/partial evidence and rejects malformed or symlink inputs', async () => {
  const f = await fixture('running'); assert.equal((await inspectTimingFiles(f.report, f.progress)).classification, 'in-progress-unqualified');
  await writeFile(f.progress, '{"sequence":1,"type":"sample"'); const partial = await inspectTimingFiles(f.report, f.progress);
  assert.equal(partial.inputs.partial_tail_observed, true); assert.equal(partial.continuous_simulator_soak_qualified, false);
  await writeFile(f.progress, '{"sequence":2}\n'); await assert.rejects(inspectTimingFiles(f.report, f.progress), /sequence/);
  const link = path.join(f.dir, 'report-link.json'); await symlink(f.report, link); await assert.rejects(inspectTimingFiles(link, f.progress), /regular/);
});


test('missing or malformed native process identity and PID changes cannot qualify', () => {
  for (const native_process of [null, {}, { pid: 12345, executable: 'java', started_at_ps: 1 }, { ...sample(0).native_process, pid: '12345' }])
    assert.equal(assess(history(row => ({ ...row, native_process }))).checks.native_process_identity_available, false);
  const rows = history(); rows[100].native_process.pid = 23456;
  assert.ok(assess(rows).reason_codes.includes('IDENTITY_CHANGED'));
});
test('closed report requires matching complete progress and native identity', async () => {
  const f = await fixture(); const progress = await readFile(f.progress, 'utf8');
  await writeFile(f.progress, progress.split('\n').slice(0, -2).join('\n') + '\n');
  assert.ok((await inspectTimingFiles(f.report, f.progress)).reason_codes.includes('CLOSED_RECORD_MISMATCH'));
  await writeFile(f.progress, progress);
  const report = JSON.parse(await readFile(f.report, 'utf8')); report.bridge.bridge_instance_id = '22222222-2222-4333-8444-555555555555';
  await writeFile(f.report, JSON.stringify(report));
  assert.ok((await inspectTimingFiles(f.report, f.progress)).reason_codes.includes('REPORT_IDENTITY_MISMATCH'));
});


test('bounded large native terminal receipts are allowed while oversized sample/record data is refused', async () => {
  const f = await fixture(); const rows = (await readFile(f.progress, 'utf8')).trim().split('\n').map(JSON.parse);
  rows.splice(1, 0, { type: 'operation_terminal', result: { fixture_padding: 'x'.repeat(1800000) } });
  const save = async () => writeFile(f.progress, rows.map((r, i) => JSON.stringify({ ...r, sequence: i + 1 })).join('\n') + '\n');
  await save(); assert.equal((await inspectTimingFiles(f.report, f.progress)).continuous_simulator_soak_qualified, true);
  rows[1] = { ...sample(0), fixture_padding: 'x'.repeat(TIMING_POLICY.maximum_sample_bytes) }; await save();
  await assert.rejects(inspectTimingFiles(f.report, f.progress), /sample record too large/);
  rows[1] = { type: 'operation_terminal', fixture_padding: 'x'.repeat(TIMING_POLICY.maximum_record_bytes) }; await save();
  await assert.rejects(inspectTimingFiles(f.report, f.progress), /record too large/);
});


test('actual disconnected harness persists the independent false gate and guard hash', async () => {
  const { runSoak, parseOptions } = await import('../../scripts/openpnp-soak.mjs');
  const dir = await mkdtemp(path.join(process.env.OPENPNP_TIMING_TEST_OUTPUT || os.tmpdir(), 'soak-timing-disconnected-'));
  const options = parseOptions(['--connection-file', path.join(dir, 'absent.json'), '--evidence-dir', path.join(dir, 'evidence'), '--duration-hours', '0.001', '--placements', '1']);
  const result = await runSoak(options);
  assert.equal(result.failure.code, 'INVALID_CONNECTION'); assert.equal(result.simulator_soak_complete, false);
  assert.equal(result.continuous_simulator_soak_qualified, false);
  assert.equal(result.independent_timing_gate.checks.closed_successful_raw_report, false);
  assert.equal(result.timing_guard_sha256, sha(await readFile(fileURLToPath(new URL('../../scripts/openpnp-soak-timing.mjs', import.meta.url)))));
  const sidecar = await inspectTimingFiles(path.join(options.evidenceDir, 'report.json'), path.join(options.evidenceDir, 'progress.jsonl'));
  assert.equal(sidecar.continuous_simulator_soak_qualified, false); assert.equal(sidecar.raw_report_status, 'failed');
});

test('file assessment rejects interior samples moved outside either workload edge before filtering', async () => {
  for (const mutate of [
    row => ({ ...row, elapsed_ms: 0 }),
    row => ({ ...row, elapsed_ms: START + DURATION + 1010 }),
    row => ({ ...row, elapsed_ms: 0, bridge_instance_id: '22222222-2222-4333-8444-555555555555', native_process: { ...row.native_process, pid: 23456 } }),
  ]) {
    const rows = history(); rows[100] = mutate(rows[100]); const f = await fixture('completed', rows);
    const result = await inspectTimingFiles(f.report, f.progress);
    assert.equal(result.continuous_simulator_soak_qualified, false);
    assert.ok(result.reason_codes.includes('CLOCK_REGRESSION'));
    assert.ok(result.reason_codes.includes('WORKLOAD_WINDOW_REENTRY'));
  }
});
test('ordered prefix and suffix samples cannot count toward span but remain identity checked', () => {
  const end = START + DURATION + 10;
  const rows = [sample(-10000), ...history(), sample(DURATION + 10000)];
  const run = input => { const g = new SoakTimingGuard({ workloadStartElapsedMs: START, workloadEndElapsedMs: end }); input.forEach(row => g.add(row)); return g.finish({ closed: true, rawSimulatorSoakComplete: true }); };
  const control = run(rows); assert.equal(control.continuous_simulator_soak_qualified, true);
  assert.equal(control.ignored_outside_workload, 2); assert.equal(control.observed_native_timer_span_ms, DURATION);
  for (const index of [0, rows.length - 1]) {
    const bad = structuredClone(rows); bad[index].native_process.pid = 23456;
    assert.ok(run(bad).reason_codes.includes('IDENTITY_CHANGED')); assert.equal(run(bad).continuous_simulator_soak_qualified, false);
  }
});
test('invalid read bounds cannot hide identity/order faults or permit workload reentry', () => {
  const rows = history(); rows[100].native_read_started_elapsed_ms = -1;
  rows[100].bridge_instance_id = '22222222-2222-4333-8444-555555555555';
  const r = assess(rows); assert.equal(r.continuous_simulator_soak_qualified, false);
  assert.ok(r.reason_codes.includes('INVALID_READ_BRACKET')); assert.ok(r.reason_codes.includes('IDENTITY_CHANGED'));
  const reordered = history(); reordered[100].native_read_started_elapsed_ms = reordered[99].native_read_completed_elapsed_ms - 1;
  assert.ok(assess(reordered).reason_codes.includes('READ_ORDER_INVALID'));
});


test('closed file report cannot hide a sample or native-read bracket after its total duration', async () => {
  for (const suffix of [sample(DURATION + 3600000), { ...sample(DURATION), native_read_completed_elapsed_ms: START + DURATION + 1000 }]) {
    const rows = [...history(), suffix], f = await fixture('completed', rows);
    const result = await inspectTimingFiles(f.report, f.progress);
    assert.equal(result.continuous_simulator_soak_qualified, false);
    assert.ok(result.reason_codes.includes('REPORT_END_EXCEEDED'));
  }
});
