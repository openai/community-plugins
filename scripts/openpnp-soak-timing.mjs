#!/usr/bin/env node
// Independent timing gate. No connection, machine action, timer extension or report rewrite.
import { createHash } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, open } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const TIMING_POLICY = Object.freeze({
  version: 1, required_native_span_ms: 8 * 3600000,
  maximum_observation_gap_ms: 60000, maximum_clock_divergence_ms: 30000,
  maximum_sample_read_ms: 30000, maximum_samples: 40000,
  maximum_recorded_anomalies: 16, maximum_progress_bytes: 32 * 1024 ** 2,
  maximum_record_bytes: 8 * 1024 ** 2, maximum_sample_bytes: 256 * 1024, maximum_records: 100000,
});
const finite = x => typeof x === 'number' && Number.isFinite(x) && x >= 0;
const record = x => x !== null && typeof x === 'object' && !Array.isArray(x);
const hash = b => createHash('sha256').update(b).digest('hex');
const invariant = (ok, message) => { if (!ok) throw new Error(message); };
const processIdentity = p => record(p) && Number.isSafeInteger(p.pid) && p.pid > 0 && p.executable === 'java' &&
  typeof p.started_at_ps === 'string' && /^[A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4}$/.test(p.started_at_ps)
  ? `${p.pid}|${p.started_at_ps}|${p.executable}` : null;
const iso = value => typeof value === 'string' && Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value;

export class SoakTimingGuard {
  constructor({ workloadStartElapsedMs = null, workloadEndElapsedMs = null } = {}) {
    invariant(workloadStartElapsedMs === null || finite(workloadStartElapsedMs), 'Invalid workload start');
    invariant(workloadEndElapsedMs === null || finite(workloadEndElapsedMs) && workloadEndElapsedMs >= workloadStartElapsedMs, 'Invalid workload end');
    this.start = workloadStartElapsedMs; this.end = workloadEndElapsedMs;
    this.count = 0; this.ignored = 0; this.first = null; this.last = null;
    this.allFirst = null; this.allLast = null; this.windowPhase = 0;
    this.flags = new Set(); this.anomalies = []; this.bracketed = true;
    this.maxGap = 0; this.maxDivergence = 0; this.violationSamples = 0;
  }
  note(code, sample = null) {
    this.flags.add(code); this.violationSamples++;
    if (this.anomalies.length < TIMING_POLICY.maximum_recorded_anomalies)
      this.anomalies.push({ code, sample_index: this.count, elapsed_ms: sample?.elapsed_ms ?? null });
  }
  add(raw) {
    if (++this.count > TIMING_POLICY.maximum_samples) { this.note('SAMPLE_CAPACITY'); return; }
    const uptime = raw?.native_metrics?.uptime_ms;
    if (!record(raw) || !finite(raw.elapsed_ms) || !Number.isSafeInteger(uptime) || uptime < 0 ||
        !iso(raw.observed_at) || typeof raw.bridge_instance_id !== 'string' || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(raw.bridge_instance_id)) {
      this.note('INVALID_TIMING_SAMPLE'); return;
    }
    const begin = raw.native_read_started_elapsed_ms, end = raw.native_read_completed_elapsed_ms;
    const bounded = finite(begin) && finite(end) && begin <= end && end <= raw.elapsed_ms &&
      end - begin <= TIMING_POLICY.maximum_sample_read_ms && raw.elapsed_ms - begin <= TIMING_POLICY.maximum_sample_read_ms;
    if (!bounded) { this.bracketed = false; this.note('INVALID_READ_BRACKET', raw); }
    const sample = { elapsed_ms: raw.elapsed_ms, wall_ms: Date.parse(raw.observed_at), observed_at: raw.observed_at,
      native_uptime_ms: uptime, bridge_instance_id: raw.bridge_instance_id,
      native_read_started_elapsed_ms: bounded ? begin : null, native_read_completed_elapsed_ms: bounded ? end : null,
      native_process_identity: processIdentity(raw.native_process) };
    // Check every observation before choosing workload endpoints. Out-of-window timestamps
    // cannot hide an interior identity change or clock regression from the final gate.
    if (!sample.native_process_identity) this.note('INVALID_NATIVE_IDENTITY', sample);
    if (this.allLast) {
      if (sample.bridge_instance_id !== this.allFirst.bridge_instance_id || sample.native_process_identity !== this.allFirst.native_process_identity) this.note('IDENTITY_CHANGED', sample);
      if (sample.elapsed_ms < this.allLast.elapsed_ms || sample.wall_ms < this.allLast.wall_ms || uptime < this.allLast.native_uptime_ms) this.note('CLOCK_REGRESSION', sample);
      if (bounded && this.allLast.native_read_completed_elapsed_ms !== null && begin < this.allLast.native_read_completed_elapsed_ms) this.note('READ_ORDER_INVALID', sample);
    } else this.allFirst = sample;
    this.allLast = sample;
    const phase = this.start !== null && (sample.elapsed_ms < this.start || bounded && begin < this.start) ? 0
      : this.end !== null && (sample.elapsed_ms > this.end || bounded && end > this.end) ? 2 : 1;
    if (phase < this.windowPhase) this.note('WORKLOAD_WINDOW_REENTRY', sample);
    this.windowPhase = Math.max(this.windowPhase, phase);
    if (phase !== 1) { this.ignored++; return; }
    if (this.last) {
      const deltas = [sample.elapsed_ms - this.last.elapsed_ms, sample.wall_ms - this.last.wall_ms, uptime - this.last.native_uptime_ms];
      if (deltas.some(v => v < 0)) this.note('CLOCK_REGRESSION', sample);
      this.maxGap = Math.max(this.maxGap, ...deltas);
      if (deltas.some(v => v > TIMING_POLICY.maximum_observation_gap_ms)) this.note('OBSERVATION_GAP', sample);
      const spans = [sample.elapsed_ms - this.first.elapsed_ms, sample.wall_ms - this.first.wall_ms, uptime - this.first.native_uptime_ms];
      const divergence = Math.max(...spans) - Math.min(...spans);
      this.maxDivergence = Math.max(this.maxDivergence, divergence);
      if (divergence > TIMING_POLICY.maximum_clock_divergence_ms) this.note('CLOCK_DIVERGENCE', sample);
    } else this.first = sample;
    this.last = sample;
  }
  finish({ closed = false, reportStatus = closed ? 'completed' : 'running', rawSimulatorSoakComplete = false, workloadEndElapsedMs = this.end } = {}) {
    const window = this.start !== null && finite(workloadEndElapsedMs) && workloadEndElapsedMs >= this.start;
    const endpoints = this.first && this.last && this.first !== this.last;
    const nativeSpan = endpoints ? this.last.native_uptime_ms - this.first.native_uptime_ms : null;
    const harnessSpan = endpoints ? this.last.elapsed_ms - this.first.elapsed_ms : null;
    const wallSpan = endpoints ? this.last.wall_ms - this.first.wall_ms : null;
    const inside = Boolean(window && this.bracketed && endpoints && this.first.native_read_started_elapsed_ms >= this.start &&
      this.last.native_read_completed_elapsed_ms <= workloadEndElapsedMs && this.last.elapsed_ms <= workloadEndElapsedMs);
    const checks = {
      closed_successful_raw_report: closed === true && reportStatus === 'completed' && rawSimulatorSoakComplete === true,
      workload_window_available: window, bracketed_workload_endpoints: inside,
      workload_endpoint_coverage: Boolean(inside && this.first.native_read_started_elapsed_ms - this.start <= TIMING_POLICY.maximum_observation_gap_ms &&
        workloadEndElapsedMs - this.last.elapsed_ms <= TIMING_POLICY.maximum_observation_gap_ms),
      native_timer_eight_hours: nativeSpan !== null && nativeSpan >= TIMING_POLICY.required_native_span_ms,
      harness_timer_eight_hours: harnessSpan !== null && harnessSpan >= TIMING_POLICY.required_native_span_ms,
      wall_observations_eight_hours: wallSpan !== null && wallSpan >= TIMING_POLICY.required_native_span_ms,
      native_process_identity_available: Boolean(endpoints && this.first.native_process_identity && this.last.native_process_identity),
      valid_ordered_identity_and_observations: this.flags.size === 0 && Boolean(endpoints),
    };
    const qualified = Object.values(checks).every(Boolean);
    return { schema_version: 1, policy: TIMING_POLICY, continuous_simulator_soak_qualified: qualified,
      raw_simulator_soak_complete: rawSimulatorSoakComplete === true,
      classification: !closed && reportStatus === 'running' ? 'in-progress-unqualified' : qualified ? 'timing-gate-passed' : 'timing-unqualified',
      checks, sample_count: Math.min(this.count, TIMING_POLICY.maximum_samples), ignored_outside_workload: this.ignored,
      workload_started_elapsed_ms: this.start, workload_ended_elapsed_ms: workloadEndElapsedMs,
      observed_native_timer_span_ms: nativeSpan, observed_harness_timer_span_ms: harnessSpan, observed_wall_span_ms: wallSpan,
      maximum_observation_gap_ms: this.maxGap, maximum_clock_divergence_ms: this.maxDivergence,
      reason_codes: [...this.flags], violation_sample_count: this.violationSamples, first_sample: this.first, last_sample: this.last,
      anomalies: this.anomalies, precise_native_boundary_qualification_available: inside,
      root_cause_determined: false, full_A11_qualified: false, hardware_qualified: false,
      interpretation: 'Native System.nanoTime differences are elapsed timer observations, not CPU time or proof of physical operation. Clock/observation discontinuities do not identify their cause. Unbracketed historical samples remain diagnostic only. No raw report field is rewritten.' };
  }
}

async function boundedRead(filename, maximum) {
  const before = await lstat(filename); invariant(before.isFile() && !before.isSymbolicLink() && before.size <= maximum, 'Expected bounded regular input');
  const file = await open(filename, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = await file.stat(); invariant(opened.ino === before.ino && opened.dev === before.dev && opened.size <= maximum, 'Input identity changed');
    const bytes = Buffer.alloc(opened.size); let offset = 0;
    while (offset < bytes.length) { const r = await file.read(bytes, offset, bytes.length - offset, offset); invariant(r.bytesRead > 0, 'Input truncated'); offset += r.bytesRead; }
    const after = await file.stat(), current = await lstat(filename);
    invariant(after.ino === opened.ino && after.size >= bytes.length && current.ino === opened.ino, 'Input replaced/truncated');
    // A growing live prefix may be diagnosed, never qualified as a completed immutable report.
    return { bytes, stable: after.size === opened.size && after.mtimeMs === opened.mtimeMs,
      provenance: { path: path.resolve(filename), prefix_bytes: bytes.length, prefix_sha256: hash(bytes), observed_size: after.size } };
  } finally { await file.close(); }
}

export async function inspectTimingFiles(reportFile, progressFile) {
  const reportRead = await boundedRead(reportFile, 8 * 1024 ** 2);
  invariant(reportRead.stable, 'Report changed during read');
  const decode = b => new TextDecoder('utf-8', { fatal: true }).decode(b);
  const report = JSON.parse(decode(reportRead.bytes)); invariant(record(report), 'Expected report object');
  const explicit = report.timing_window?.workload_started_elapsed_ms;
  const legacy = finite(report.elapsed_ms) && finite(report.workload_elapsed_ms) && report.workload_elapsed_ms <= report.elapsed_ms
    ? report.elapsed_ms - report.workload_elapsed_ms : null;
  const start = finite(explicit) ? explicit : legacy, end = finite(report.elapsed_ms) ? report.elapsed_ms : null;
  const guard = new SoakTimingGuard({ workloadStartElapsedMs: start, workloadEndElapsedMs: end });
  const progress = await boundedRead(progressFile, TIMING_POLICY.maximum_progress_bytes);
  const text = decode(progress.bytes); const partial = text.length > 0 && !text.endsWith('\n');
  const lines = text.split('\n'); lines.pop();
  invariant(lines.length <= TIMING_POLICY.maximum_records, 'Progress record capacity exceeded');
  let sequence = 0, firstRecord = null, finalRecord = null, finishRecords = 0;
  for (const line of lines) {
    invariant(Buffer.byteLength(line) <= TIMING_POLICY.maximum_record_bytes, 'Progress record too large');
    const row = JSON.parse(line); invariant(record(row) && row.sequence === ++sequence, 'Progress sequence invalid');
    firstRecord ??= row; finalRecord = row;
    if (row.type === 'run_finished') finishRecords++;
    if (row.type === 'sample') {
      invariant(Buffer.byteLength(line) <= TIMING_POLICY.maximum_sample_bytes, 'Timing sample record too large');
      // Here the window end is the entire report duration, not an optional subwindow.
      if (report.status !== 'running' && end !== null &&
          [row.elapsed_ms, row.native_read_started_elapsed_ms, row.native_read_completed_elapsed_ms].some(value => finite(value) && value > end))
        guard.note('REPORT_END_EXCEEDED', row);
      guard.add(row);
    }
  }
  const closedRecordMatches = firstRecord?.type === 'run_started' && firstRecord.started_at === report.started_at &&
    finishRecords === 1 && finalRecord?.type === 'run_finished' && finalRecord.status === report.status &&
    finalRecord.elapsed_ms === report.elapsed_ms && finalRecord.simulator_soak_complete === report.simulator_soak_complete;
  const reportIdentityMatches = guard.first?.bridge_instance_id === report.bridge?.bridge_instance_id &&
    guard.first?.native_process_identity === (Number.isSafeInteger(report.native_process_rss?.supplied_pid)
      ? `${report.native_process_rss.supplied_pid}|${report.native_process_rss.identity}` : null);
  if (report.status !== 'running' && !closedRecordMatches) guard.note('CLOSED_RECORD_MISMATCH');
  if (report.status !== 'running' && !reportIdentityMatches) guard.note('REPORT_IDENTITY_MISMATCH');
  if (partial) guard.note('PARTIAL_PROGRESS_TAIL');
  if (!progress.stable) guard.note('GROWING_PROGRESS_PREFIX');
  const result = guard.finish({ closed: ['completed', 'failed', 'incomplete'].includes(report.status) && progress.stable && !partial, reportStatus: report.status,
    rawSimulatorSoakComplete: report.simulator_soak_complete, workloadEndElapsedMs: end });
  return { ...result, raw_report_status: report.status, raw_qualification_preserved: true,
    inputs: { report: reportRead.provenance, progress: progress.provenance, progress_complete_records: sequence,
      partial_tail_observed: partial, progress_stable_during_read: progress.stable,
      closed_record_matches_report: closedRecordMatches, report_identity_matches_observations: reportIdentityMatches } };
}

async function main(args) {
  invariant(args.length === 6 && args[0] === '--report' && args[2] === '--progress' && args[4] === '--output',
    'Usage: openpnp-soak-timing.mjs --report REPORT --progress PROGRESS_JSONL --output NEW_JSON');
  const result = await inspectTimingFiles(args[1], args[3]);
  const destination = await open(args[5], 'wx', 0o600);
  try { await destination.writeFile(JSON.stringify(result, null, 2) + '\n'); await destination.sync(); } finally { await destination.close(); }
  process.stdout.write(JSON.stringify({ output: path.resolve(args[5]), classification: result.classification,
    continuous_simulator_soak_qualified: result.continuous_simulator_soak_qualified }) + '\n');
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).catch(error => { process.stderr.write(error.message + '\n'); process.exitCode = 1; });
}
